[CmdletBinding()]
param([string] $GatePath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('GatePath')) {
    $GatePath = Join-Path $PSScriptRoot '..\Assert-StratusPostMigrationSuccessorGate.ps1'
}

# AST-only wiring regressions. This script reads the real gate source, but never
# invokes its entry or extracted bodies, compiles its runtime, creates a child,
# creates a filesystem fixture, or contacts Docker. PASS is NOT native lifetime,
# complete parent protocol, R1/release, or production authorization evidence.
$expectedCases = 18
$script:bootstrapCaseCount = 0
$script:bootstrapPassed = 0
$script:bootstrapFailures = [Collections.Generic.List[string]]::new()
$GatePath = [IO.Path]::GetFullPath($GatePath)
$gateSource = [IO.File]::ReadAllText($GatePath)
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $GatePath, [ref]$tokens, [ref]$parseErrors
)

function Assert-BootstrapStatic {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-BootstrapStaticCase {
    param([string] $Name, [scriptblock] $Action)
    $script:bootstrapCaseCount++
    try {
        & $Action
        $script:bootstrapPassed++
        Write-Output ('PASS parent-bootstrap static: ' + $Name)
    }
    catch {
        $script:bootstrapFailures.Add($Name + ': ' + $_.Exception.GetBaseException().Message)
        Write-Output ('FAIL parent-bootstrap static: ' + $Name)
    }
}

function Get-BootstrapFunction {
    param([string] $Name)
    $definitions = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name
    }, $true))
    Assert-BootstrapStatic ($definitions.Count -eq 1) ('Missing or ambiguous gate helper: ' + $Name)
    $parent = $definitions[0].Parent
    while ($null -ne $parent) {
        Assert-BootstrapStatic ($parent -isnot [Management.Automation.Language.FunctionDefinitionAst]) `
            ('Nested helper cannot substitute for gate wiring: ' + $Name)
        $parent = $parent.Parent
    }
    return $definitions[0]
}

function Get-BootstrapCommands {
    param([Management.Automation.Language.Ast] $Within, [string] $Name)
    return @($Within.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq $Name
    }, $true))
}

function Get-BootstrapMethods {
    param([Management.Automation.Language.Ast] $Within, [string] $Name)
    return @($Within.FindAll({ param($node)
        $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
            $node.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and
            $node.Member.Value -ceq $Name
    }, $true))
}

function Get-BootstrapAssignment {
    param([Management.Automation.Language.Ast] $Call, [string] $Left)
    $parent = $Call.Parent
    while ($null -ne $parent -and $parent -isnot [Management.Automation.Language.AssignmentStatementAst]) {
        Assert-BootstrapStatic ($parent -isnot [Management.Automation.Language.FunctionDefinitionAst]) `
            ('Call has no retained assignment: ' + $Call.Extent.Text)
        $parent = $parent.Parent
    }
    Assert-BootstrapStatic ($null -ne $parent -and $parent.Left.Extent.Text.Trim() -ceq $Left) `
        ('Factory result is not retained directly by ' + $Left)
    return $parent
}

function Assert-BootstrapPrecedes {
    param([Management.Automation.Language.Ast] $Before, [Management.Automation.Language.Ast] $After,
        [string] $Message)
    Assert-BootstrapStatic ($Before.Extent.EndOffset -le $After.Extent.StartOffset) $Message
}

function Get-BootstrapTopAssignment {
    param([Management.Automation.Language.FunctionDefinitionAst] $Function, [string] $Left)
    $assignments = @($Function.Body.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
            $_.Left.Extent.Text.Trim() -ceq $Left
    })
    Assert-BootstrapStatic ($assignments.Count -eq 1) ('Missing or ambiguous direct assignment: ' + $Left)
    return $assignments[0]
}

function Assert-BootstrapDirectStatement {
    param([Management.Automation.Language.Ast] $Node,
        [Management.Automation.Language.FunctionDefinitionAst] $Function)
    $parent = $Node
    while ($null -ne $parent.Parent -and $parent.Parent -ne $Function.Body.EndBlock) {
        Assert-BootstrapStatic ($parent.Parent -isnot [Management.Automation.Language.StatementBlockAst] -and
            $parent.Parent -isnot [Management.Automation.Language.FunctionDefinitionAst]) `
            ('Required wiring is nested in conditional/dead code: ' + $Node.Extent.Text)
        $parent = $parent.Parent
    }
    Assert-BootstrapStatic ($parent.Parent -eq $Function.Body.EndBlock) 'Required wiring is not a direct function statement.'
}

function Get-BootstrapThrowGuard {
    param([Management.Automation.Language.Ast] $Within, [string] $Message)
    $guards = @($Within.FindAll({ param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and
            @($node.Clauses).Count -eq 1 -and $null -eq $node.ElseClause -and
            @($node.Clauses[0].Item2.Statements).Count -eq 1 -and
            $node.Clauses[0].Item2.Statements[0] -is [Management.Automation.Language.ThrowStatementAst] -and
            $node.Clauses[0].Item2.Statements[0].Extent.Text.Contains($Message)
    }, $true))
    Assert-BootstrapStatic ($guards.Count -eq 1) ('Missing or ambiguous rejecting guard: ' + $Message)
    return $guards[0]
}

$entryCheck = {
    Assert-BootstrapStatic (@($parseErrors).Count -eq 0) 'Gate source does not parse.'
    $open = Get-BootstrapFunction 'Open-ParentStageBoundary'
    $statements = @($open.Body.EndBlock.Statements)
    Assert-BootstrapStatic ($statements.Count -eq 5 -and
        $statements[0] -is [Management.Automation.Language.ThrowStatementAst] -and
        $statements[0].Extent.Text -match 'integration is WIP') `
        'Unconditional Open boundary blocker must precede every bootstrap action.'
    $order = @('Initialize-ParentStageRuntime', 'Assert-ParentStageLaunchContract',
        'Start-ParentStageBootstrap', 'Complete-ParentStageBoundary')
    for ($index = 0; $index -lt $order.Count; $index++) {
        Assert-BootstrapStatic ($statements[$index + 1] -is [Management.Automation.Language.PipelineAst] -and
            $statements[$index + 1].PipelineElements.Count -eq 1 -and
            $statements[$index + 1].PipelineElements[0].GetCommandName() -ceq $order[$index]) `
            'Blocked Open boundary wiring order differs.'
    }
    $launches = @(Get-BootstrapCommands $ast 'Start-ParentStageBootstrap')
    Assert-BootstrapStatic ($launches.Count -eq 1 -and $launches[0].Extent.StartOffset -gt $statements[0].Extent.EndOffset -and
        $launches[0].Extent.EndOffset -lt $open.Extent.EndOffset) 'Bootstrap has an alternate unblocked entry call.'
}
Invoke-BootstrapStaticCase 'unconditional Open boundary blocker precedes its only bootstrap entry' $entryCheck

Invoke-BootstrapStaticCase 'top-level blocker and same-scope reentry rejection precede every state reset' {
    $statements = @($ast.EndBlock.Statements)
    Assert-BootstrapStatic ($statements.Count -gt 4 -and
        $statements[0] -is [Management.Automation.Language.PipelineAst] -and
        $statements[0].PipelineElements.Count -eq 1 -and
        $statements[0].PipelineElements[0].GetCommandName() -ceq 'Set-StrictMode' -and
        $statements[1] -is [Management.Automation.Language.AssignmentStatementAst] -and
        $statements[1].Left.Extent.Text -ceq '$ErrorActionPreference' -and
        $statements[2] -is [Management.Automation.Language.ThrowStatementAst] -and
        $statements[2].Extent.Text.Contains('WIP and non-authorizing')) `
        'Top-level unconditional WIP blocker must precede any session or resource action.'
    $guard = Get-BootstrapThrowGuard $ast 'Parent-stage script scope already exists;'
    Assert-BootstrapStatic ($statements[3] -eq $guard) 'Reentry rejection must immediately follow the top-level blocker.'
    $checks = @(Get-BootstrapCommands $guard 'Get-Variable')
    Assert-BootstrapStatic ($checks.Count -eq 2) 'Reentry must inspect both retained script-scope references.'
    foreach ($name in @('parentStage', 'parentStageOwnedHandles')) {
        Assert-BootstrapStatic (@($checks | Where-Object {
            $_.Extent.Text -ceq ('Get-Variable -Name ' + $name + ' -Scope Script -ErrorAction SilentlyContinue')
        }).Count -eq 1) ('Missing same-scope retained reference check: ' + $name)
    }
    foreach ($assignment in @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and
            $node.Left.Extent.Text -cin @('$script:parentStage', '$script:parentStageOwnedHandles')
    }, $true))) {
        Assert-BootstrapPrecedes $guard $assignment 'A retained script-scope reference can be reset before reentry rejection.'
    }
}

Invoke-BootstrapStaticCase 'external callers cannot supply fence lease ready heartbeat or process capability' {
    $names = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    Assert-BootstrapStatic (@($names | Where-Object { $_ -like 'Parent*' }).Count -eq 2 -and
        'ParentStageContractPath' -cin $names -and 'ParentStageContractSha256' -cin $names) `
        'Only contract path and hash may be supplied as parent inputs.'
    foreach ($name in @('ParentMutationFence', 'ParentStageLeasePath', 'ParentStageLeaseSha256',
        'ParentStageReadyPath', 'ParentStageReadySha256', 'ParentStageHeartbeatPath',
        'ParentStageLaunch', 'ParentStageWatchdogProcess', 'ParentStageCapability')) {
        Assert-BootstrapStatic ($name -cnotin $names) ('External parent authority parameter remains: ' + $name)
    }
    foreach ($parameter in $ast.ParamBlock.Parameters) {
        foreach ($attribute in $parameter.Attributes) {
            Assert-BootstrapStatic ($attribute.TypeName.FullName -cnotin @('IO.FileStream',
                'Stratus.ParentStage.ExactFence', 'Stratus.ParentStage.IndependentWatchdogLaunch')) `
                'A caller-provided native/fence capability remains part of gate entry.'
        }
    }
}

Invoke-BootstrapStaticCase 'real boundary initializes its private runtime before invoking its bootstrap' {
    $open = Get-BootstrapFunction 'Open-ParentStageBoundary'
    $initialize = @(Get-BootstrapCommands $open 'Initialize-ParentStageRuntime')
    $start = @(Get-BootstrapCommands $open 'Start-ParentStageBootstrap')
    Assert-BootstrapStatic ($initialize.Count -eq 1 -and $start.Count -eq 1) `
        'Open-ParentStageBoundary must call each trusted initializer exactly once.'
    Assert-BootstrapPrecedes $initialize[0] $start[0] 'Bootstrap precedes trusted runtime initialization.'
    $runtime = Get-BootstrapFunction 'Initialize-ParentStageRuntime'
    $compilers = @(Get-BootstrapCommands $runtime 'Add-Type')
    $namespaces = @(Get-BootstrapMethods $runtime 'NewGuid')
    $replacements = @(Get-BootstrapMethods $runtime 'Replace')
    Assert-BootstrapStatic ($compilers.Count -eq 1 -and $namespaces.Count -eq 1 -and
        $replacements.Count -ge 1 -and $runtime.Extent.Text -match 'approvedParentStageRuntimeSha256' -and
        $runtime.Extent.Text -match 'Stratus\.ParentStageGate') `
        'Runtime must remain hash-pinned and freshly namespaced for this invocation.'
    Assert-BootstrapPrecedes $namespaces[0] $compilers[0] 'Native type namespace is not generated before compilation.'
    foreach ($entry in @(@('$launchType', '.IndependentWatchdogLaunch', 'launch_type'),
        @('$fenceType', '.ExactFence', 'fence_type'), @('$heartbeatType', '.Heartbeat', 'heartbeat_type'))) {
        $assignment = Get-BootstrapTopAssignment $runtime $entry[0]
        Assert-BootstrapStatic ($assignment.Right.Extent.Text.Contains('$compiled') -and
            $assignment.Right.Extent.Text.Contains('$namespace') -and
            $assignment.Right.Extent.Text.Contains($entry[1]) -and
            $runtime.Extent.Text.Contains(($entry[2] + ' = ' + $entry[0] + '[0]'))) `
            ('Native capability type is not selected from this compilation: ' + $entry[1])
        Assert-BootstrapPrecedes $compilers[0] $assignment 'Capability type is selected before trusted compilation.'
    }
}

$resumeCheck = {
    $bootstrap = Get-BootstrapFunction 'Start-ParentStageBootstrap'
    $factories = @(Get-BootstrapMethods $bootstrap 'StartSuspendedWithControl')
    $resume = @(Get-BootstrapMethods $bootstrap 'Resume')
    Assert-BootstrapStatic ($factories.Count -eq 1 -and $factories[0].Static -and $resume.Count -eq 1 -and
        @(Get-BootstrapMethods $bootstrap 'StartSuspended').Count -eq 0) `
        'Bootstrap must create exactly one control-enabled suspended child and resume it once.'
    $retained = Get-BootstrapAssignment $factories[0] '$state.launch'
    Assert-BootstrapDirectStatement $retained $bootstrap
    Assert-BootstrapDirectStatement $resume[0] $bootstrap
    Assert-BootstrapStatic ($factories[0].Expression.Extent.Text -ceq '$launchType' -and
        (Get-BootstrapTopAssignment $bootstrap '$launchType').Right.Extent.Text -ceq '$state.launch_type') `
        'Suspended child factory is not from the private runtime.'
    Assert-BootstrapStatic ($resume[0].Expression.Extent.Text.Trim() -ceq '$state.launch') `
        'Resume does not use the gate-retained launch capability.'
    Assert-BootstrapPrecedes $retained $resume[0] 'Launch session is not retained before Resume.'
    $latch = Get-BootstrapTopAssignment $bootstrap '$state.resume_attempted'
    Assert-BootstrapStatic ($latch.Right.Extent.Text.Trim() -ceq '$true') 'Resume-attempt latch must become true before Resume.'
    Assert-BootstrapPrecedes $retained $latch 'Resume latch precedes retained native creation.'
    Assert-BootstrapPrecedes $latch $resume[0] 'Ambiguous Resume can occur before the retention latch.'
    $openedProcesses = @(Get-BootstrapMethods $bootstrap 'Open' | Where-Object {
        $_.Expression.Extent.Text -match '(?i)process|lease'
    })
    Assert-BootstrapStatic ($openedProcesses.Count -eq 0) `
        'Bootstrap adopts a caller-described watchdog process instead of its own suspended child.'
}
Invoke-BootstrapStaticCase 'bootstrap privately retains and latches its native launch before Resume' $resumeCheck

$fenceCheck = {
    $bootstrap = Get-BootstrapFunction 'Start-ParentStageBootstrap'
    $resume = @(Get-BootstrapMethods $bootstrap 'Resume')
    $fences = @(Get-BootstrapMethods $bootstrap 'AcquireExisting')
    $lease = @(Get-BootstrapCommands $bootstrap 'Assert-ParentStagePreparedLease')
    Assert-BootstrapStatic ($resume.Count -eq 1 -and $fences.Count -eq 1 -and
        $fences[0].Static -and $lease.Count -eq 1) 'Required owned bootstrap/lease boundaries are missing or ambiguous.'
    $retained = Get-BootstrapAssignment $fences[0] '$state.fence'
    Assert-BootstrapDirectStatement $retained $bootstrap
    Assert-BootstrapDirectStatement $lease[0] $bootstrap
    Assert-BootstrapPrecedes $resume[0] $fences[0] 'Fence acquisition precedes child publication.'
    Assert-BootstrapPrecedes $lease[0] $retained 'Fence is acquired before prepared lease validation.'
    Assert-BootstrapStatic ($fences[0].Expression.Extent.Text -ceq '$fenceType' -and
        (Get-BootstrapTopAssignment $bootstrap '$fenceType').Right.Extent.Text -ceq '$state.fence_type' -and
        $fences[0].Arguments.Count -eq 2 -and
        $fences[0].Arguments[0].Extent.Text -ceq '$paths.mutation_fence' -and
        $fences[0].Arguments[1].Extent.Text -ceq '$state.lease.mutation_fence_sha256') `
        'Exact fence factory/type/path/hash is not privately bound to the prepared lease.'
    $prepared = Get-BootstrapFunction 'Assert-ParentStagePreparedLease'
    $guard = Get-BootstrapThrowGuard $prepared 'Prepared lease is not bound'
    foreach ($identity in @('$lease.watchdog.pid -ne $state.launch.ProcessId',
        '$lease.watchdog.start_time_utc -cne $state.launch.StartedAt',
        '$lease.source_manifest_sha256 -cne $state.source_manifest_sha256')) {
        Assert-BootstrapStatic ($guard.Clauses[0].Item1.Extent.Text.Contains($identity)) 'Prepared lease lacks owned launch/R1 rejection.'
    }
    Assert-BootstrapStatic (@(Get-BootstrapCommands $prepared 'Assert-ParentStageNoAuthorization').Count -eq 1) `
        'Prepared lease does not reject authorization flags.'
}
Invoke-BootstrapStaticCase 'prepared launch-bound lease precedes private ExactFence acquisition' $fenceCheck

$armingCheck = {
    $bootstrap = Get-BootstrapFunction 'Start-ParentStageBootstrap'
    $fence = Get-BootstrapTopAssignment $bootstrap '$state.fence'
    $heartbeat = Get-BootstrapTopAssignment $bootstrap '$state.heartbeat'
    $owned = @(Get-BootstrapCommands $bootstrap 'Assert-ParentStageOwnedFence')
    $healthy = @(Get-BootstrapMethods $bootstrap 'AssertHealthy')
    $heartbeatReadback = @(Get-BootstrapCommands $bootstrap 'Assert-ParentStageOwnedHeartbeat')
    $publish = @(Get-BootstrapMethods $bootstrap 'PublishNew')
    Assert-BootstrapStatic ($owned.Count -eq 1 -and $healthy.Count -eq 1 -and $heartbeatReadback.Count -eq 1 -and $publish.Count -eq 1 -and
        $publish[0].Static -and $publish[0].Expression.Extent.Text -ceq '$files' -and
        $publish[0].Arguments.Count -eq 2 -and $publish[0].Arguments[0].Extent.Text -ceq '$paths.parent_armed' -and
        $publish[0].Arguments[1].Extent.Text -ceq '$armedBytes') 'Owned fence/healthy heartbeat/parent_armed publication is missing.'
    Assert-BootstrapStatic ($heartbeat.Right.Extent.Text -ceq
        '$heartbeatType::new($paths.heartbeat, $contract.session_id, $state.lease_sha256, 500)' -and
        (Get-BootstrapTopAssignment $bootstrap '$heartbeatType').Right.Extent.Text -ceq '$state.heartbeat_type') `
        'Heartbeat is not created privately with the prepared session/lease.'
    foreach ($node in @($owned[0], $healthy[0], $heartbeatReadback[0], $publish[0])) { Assert-BootstrapDirectStatement $node $bootstrap }
    Assert-BootstrapPrecedes $fence $owned[0] 'Owned fence validation must follow acquisition.'
    Assert-BootstrapPrecedes $owned[0] $heartbeat 'Heartbeat precedes owned fence validation.'
    Assert-BootstrapPrecedes $heartbeat $healthy[0] 'Heartbeat health check precedes its construction.'
    Assert-BootstrapPrecedes $healthy[0] $heartbeatReadback[0] 'Heartbeat readback precedes publication health.'
    Assert-BootstrapPrecedes $heartbeatReadback[0] $publish[0] 'Parent armed publication precedes exact heartbeat readback.'
    $capture = @(Get-BootstrapMethods $bootstrap 'PollReady' | Where-Object { $_.Extent.StartOffset -gt $publish[0].Extent.EndOffset })
    Assert-BootstrapStatic ($capture.Count -eq 1) 'Captured ready must follow parent armed publication exactly once.'
    [void](Get-BootstrapAssignment $capture[0] '$captured')
    Assert-BootstrapStatic ($capture[0].Expression.Extent.Text -ceq '$state.launch') 'Ready capture is not from the retained child.'
    $readyIdentity = Get-BootstrapThrowGuard $bootstrap 'Captured ready differs from owned prepared lease.'
    foreach ($binding in @('$captured.ProcessId -ne $state.launch.ProcessId',
        '$captured.StartedAt -cne $state.launch.StartedAt', '$captured.SessionId -cne $contract.session_id',
        '$captured.Nonce -cne $state.launch.Nonce', '$captured.LeaseSha256 -cne $state.lease_sha256')) {
        Assert-BootstrapStatic ($readyIdentity.Clauses[0].Item1.Extent.Text.Contains($binding)) 'Captured readiness identity rejection is incomplete.'
    }
    $retained = Get-BootstrapTopAssignment $bootstrap '$state.readiness'
    $complete = Get-BootstrapTopAssignment $bootstrap '$state.bootstrap_complete'
    Assert-BootstrapPrecedes $capture[0] $readyIdentity 'Captured readiness is validated before capture.'
    Assert-BootstrapPrecedes $readyIdentity $retained 'Readiness is retained before identity validation.'
    Assert-BootstrapPrecedes $retained $complete 'Bootstrap completion precedes retained readiness.'
    Assert-BootstrapStatic ($retained.Right.Extent.Text -ceq '$captured' -and $complete.Right.Extent.Text -ceq '$true') `
        'Private readiness/completion assignment differs.'
}
Invoke-BootstrapStaticCase 'owned fence heartbeat parent_armed captured ready form a retained handshake' $armingCheck

$earlyReadyCheck = {
    $bootstrap = Get-BootstrapFunction 'Start-ParentStageBootstrap'
    $publish = @(Get-BootstrapMethods $bootstrap 'PublishNew')
    Assert-BootstrapStatic ($publish.Count -eq 1) 'Parent armed publication is ambiguous.'
    $early = @(Get-BootstrapMethods $bootstrap 'PollReady' | Where-Object { $_.Extent.EndOffset -lt $publish[0].Extent.StartOffset })
    Assert-BootstrapStatic ($early.Count -eq 2) 'Both pre-arming unexpected-ready guards are required.'
    foreach ($call in $early) {
        $guard = $call.Parent
        while ($null -ne $guard -and $guard -isnot [Management.Automation.Language.IfStatementAst]) { $guard = $guard.Parent }
        Assert-BootstrapStatic ($null -ne $guard -and @($guard.Clauses).Count -eq 1 -and
            $null -eq $guard.ElseClause -and $guard.Clauses[0].Item1.Extent.Text.Trim() -ceq '$null -ne $state.launch.PollReady()' -and
            @($guard.Clauses[0].Item2.Statements).Count -eq 1 -and
            $guard.Clauses[0].Item2.Statements[0] -is [Management.Automation.Language.ThrowStatementAst] -and
            $guard.Clauses[0].Item2.Statements[0].Extent.Text.Contains('Parent ready arrived before owner arming.')) `
            'Early PollReady may only reject unexpected readiness, never admit it.'
    }
    $loops = @($bootstrap.Body.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.WhileStatementAst] -or $_ -is [Management.Automation.Language.DoWhileStatementAst]
    })
    Assert-BootstrapStatic ($loops.Count -eq 2) 'Bootstrap requires the bounded prepared-lease and captured-ready loops.'
    foreach ($loop in $loops) {
        Assert-BootstrapStatic ($loop.Body.Statements[0] -is [Management.Automation.Language.PipelineAst] -and
            $loop.Body.Statements[0].PipelineElements[0].GetCommandName() -ceq 'Assert-ParentStageBootstrapWait') `
            'Bootstrap loop does not begin by checking retained guards and deadline.'
    }
}
Invoke-BootstrapStaticCase 'pre-arming ready is rejection only and both waits begin with supervision checks' $earlyReadyCheck

Invoke-BootstrapStaticCase 'every accepted boundary rechecks launch fence and heartbeat ownership' {
    $boundary = Get-BootstrapFunction 'Assert-ParentStageBoundary'
    foreach ($entry in @(@('PollReady', '$state.launch'), @('AssertHealthy', '$state.heartbeat'))) {
        $calls = @(Get-BootstrapMethods $boundary $entry[0] | Where-Object {
            $_.Expression.Extent.Text.Trim() -ceq $entry[1]
        })
        Assert-BootstrapStatic ($calls.Count -eq 1) ('Missing exact retained recheck: ' + ($entry -join '.'))
    }
    Assert-BootstrapStatic (@(Get-BootstrapCommands $boundary 'Assert-ParentStageOwnedFence').Count -eq 1) 'Boundary omits owned fence validation.'
    Assert-BootstrapStatic (@(Get-BootstrapCommands $boundary 'Assert-ParentStageOwnedHeartbeat').Count -eq 1) 'Boundary omits owned heartbeat readback.'
    $fence = Get-BootstrapFunction 'Assert-ParentStageOwnedFence'
    Assert-BootstrapStatic (@(Get-BootstrapMethods $fence 'AssertHeld').Count -eq 1 -and
        $fence.Extent.Text.Contains('$state.fence.GetType() -ne $state.fence_type') -and
        @(Get-BootstrapMethods $fence 'ReadBytes').Count -eq 1) 'Owned fence type/retained-byte check is missing.'
    Assert-BootstrapStatic ($boundary.Extent.Text -notmatch '\$ParentMutationFence\b' -and
        $boundary.Extent.Text -notmatch '\$state\.watchdog_process\b') `
        'Boundary still accepts external fence/process authority.'
}

$cleanupCheck = {
    $close = Get-BootstrapFunction 'Close-ParentStageBoundary'
    $calls = @($ast.FindAll({ param($node)
        if ($node -isnot [Management.Automation.Language.CommandAst] -or
            $node.GetCommandName() -cne 'Close-ParentStageBoundary') { return $false }
        $parent = $node.Parent
        while ($null -ne $parent) {
            if ($parent -is [Management.Automation.Language.FunctionDefinitionAst]) { return $false }
            $parent = $parent.Parent
        }
        return $true
    }, $true))
    Assert-BootstrapStatic ($calls.Count -eq 1) 'Top-level cleanup must invoke the parent lifetime owner exactly once.'
    $parent = $calls[0].Parent; $inFinally = $false
    while ($null -ne $parent) {
        if ($parent -is [Management.Automation.Language.TryStatementAst] -and $null -ne $parent.Finally -and
            $calls[0].Extent.StartOffset -gt $parent.Finally.Extent.StartOffset -and
            $calls[0].Extent.EndOffset -lt $parent.Finally.Extent.EndOffset) { $inFinally = $true }
        $parent = $parent.Parent
    }
    Assert-BootstrapStatic $inFinally 'Parent lifetime owner is not invoked from top-level finally.'
    $externalClears = @($ast.FindAll({ param($node)
        if ($node -isnot [Management.Automation.Language.InvokeMemberExpressionAst] -or
            $node.Member.Extent.Text -cnotin @('Clear', 'Dispose') -or
            $node.Expression.Extent.Text -notmatch 'parentStageOwnedHandles') { return $false }
        $parent = $node.Parent
        while ($null -ne $parent) {
            if ($parent -is [Management.Automation.Language.FunctionDefinitionAst]) {
                return $parent.Name -cne 'Close-ParentStageBoundary'
            }
            $parent = $parent.Parent
        }
        return $true
    }, $true))
    Assert-BootstrapStatic ($externalClears.Count -eq 0) `
        'Generic cleanup releases or forgets parent resources outside its lifetime owner.'
    $retention = Get-BootstrapThrowGuard $close 'Resumed parent session retained;'
    Assert-BootstrapStatic ($retention.Clauses[0].Item1.Extent.Text.Trim() -ceq '$null -ne $state -and $state.resume_attempted' -and
        $close.Body.EndBlock.Statements[1] -eq $retention) 'Resumed session rejection must dominate every cleanup effect.'
    foreach ($method in @((Get-BootstrapMethods $close 'Dispose')) + @((Get-BootstrapMethods $close 'Clear'))) {
        Assert-BootstrapPrecedes $retention $method 'Cleanup releases ownership before checking uncertain Resume.'
    }
    Assert-BootstrapStatic (@(Get-BootstrapCommands $close 'Start-Sleep').Count -eq 0 -and
        @(Get-BootstrapMethods $close 'Kill').Count -eq 0 -and
        @($close.FindAll({ param($node) $node -is [Management.Automation.Language.TryStatementAst] }, $true)).Count -eq 0) `
        'Cleanup must not swallow live-session retention or kill the independent watchdog.'
}
Invoke-BootstrapStaticCase 'finally delegates cleanup and uncertain Resume dominates every release' $cleanupCheck

$receiptCheck = {
    foreach ($name in @('Complete-ParentStageBoundary', 'Assert-ParentStageBoundary')) {
        $definition = Get-BootstrapFunction $name
        $polls = @(Get-BootstrapMethods $definition 'PollReady')
        $references = @(Get-BootstrapMethods $definition 'ReferenceEquals')
        Assert-BootstrapStatic ($polls.Count -eq 1 -and $references.Count -eq 1 -and
            $polls[0].Expression.Extent.Text -ceq '$state.launch' -and
            $references[0].Arguments.Count -eq 2 -and
            $references[0].Arguments[0].Extent.Text -ceq '$captured' -and
            $references[0].Arguments[1].Extent.Text -ceq '$state.readiness') `
            'Receipt acceptance must retain exact captured readiness object identity.'
        Assert-BootstrapPrecedes $polls[0] $references[0] 'Receipt identity comparison precedes live recapture.'
    }
    $complete = Get-BootstrapFunction 'Complete-ParentStageBoundary'
    $initialized = Get-BootstrapTopAssignment $complete '$state.initialized'
    foreach ($helper in @('Assert-ParentStageR1Anchor', 'Assert-ParentStageOwnedFence')) {
        $calls = @(Get-BootstrapCommands $complete $helper)
        Assert-BootstrapStatic ($calls.Count -eq 1) ('Final receipt lacks ' + $helper)
        Assert-BootstrapPrecedes $calls[0] $initialized 'Parent boundary initializes before final receipt/R1 validation.'
    }
    $armed = Get-BootstrapThrowGuard $complete 'Parent-stage ready is not an acknowledgement'
    Assert-BootstrapStatic ($armed.Clauses[0].Item1.Extent.Text.Contains('$ready.source_sha256 -cne $state.parent_armed_sha256')) `
        'Ready receipt is not bound to this invocation parent_armed publication.'
}
Invoke-BootstrapStaticCase 'complete and later boundaries require live private readiness plus full receipt linkage' $receiptCheck

Invoke-BootstrapStaticCase 'bootstrap helpers expose no external command or preloaded native type shortcuts' {
    foreach ($name in @('Open-ParentStageBoundary', 'Initialize-ParentStageRuntime',
        'Start-ParentStageBootstrap', 'Close-ParentStageBoundary')) {
        $definition = Get-BootstrapFunction $name
        $commands = @($definition.FindAll({ param($node)
            $node -is [Management.Automation.Language.CommandAst]
        }, $true))
        foreach ($command in $commands) {
            Assert-BootstrapStatic ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
                $command.GetCommandName() -cnotin @('Start-Process', 'Invoke-Expression', 'Invoke-Command',
                    'Start-Job', 'Start-ThreadJob', 'Stop-Process', 'docker', 'wsl', 'cmd')) `
                ('Unexpected external/dynamic bootstrap command: ' + $command.Extent.Text)
        }
        $types = @($definition.FindAll({ param($node)
            $node -is [Management.Automation.Language.TypeExpressionAst]
        }, $true))
        foreach ($type in $types) {
            Assert-BootstrapStatic ($type.TypeName.FullName -notmatch '^Stratus\.ParentStage\.') `
                'Bootstrap uses a preloaded native type name instead of its freshly compiled private types.'
        }
    }
}

# Negative controls mutate only source TEXT/AST held in memory. They never create
# executable copies or invoke any production/helper body. Each must fail at its
# intended wiring assertion, so a missing unrelated function cannot count as PASS.
function Invoke-BootstrapMutationControl {
    param([string] $Name, [Management.Automation.Language.Ast] $Node, [string] $Replacement,
        [scriptblock] $Check, [string] $ExpectedFailure)
    $originalAst = $script:ast; $originalErrors = $script:parseErrors
    try {
        $mutated = $gateSource.Remove($Node.Extent.StartOffset, $Node.Extent.EndOffset - $Node.Extent.StartOffset).
            Insert($Node.Extent.StartOffset, $Replacement)
        $mutationTokens = $null; $mutationErrors = $null
        $script:ast = [Management.Automation.Language.Parser]::ParseInput(
            $mutated, [ref]$mutationTokens, [ref]$mutationErrors)
        $script:parseErrors = $mutationErrors
        Assert-BootstrapStatic (@($mutationErrors).Count -eq 0) ('Negative control has invalid syntax: ' + $Name)
        $failure = $null
        try { & $Check } catch { $failure = $_.Exception.GetBaseException().Message }
        Assert-BootstrapStatic ($null -ne $failure -and $failure -like $ExpectedFailure) `
            ('Negative control missed its intended rejection: ' + $Name + ': ' + $failure)
    }
    finally { $script:ast = $originalAst; $script:parseErrors = $originalErrors }
}

Invoke-BootstrapStaticCase 'negative control missing entry blocker is rejected' {
    $open = Get-BootstrapFunction 'Open-ParentStageBoundary'
    Invoke-BootstrapMutationControl 'missing blocker' $open.Body.EndBlock.Statements[0] '' $entryCheck '*Unconditional Open boundary blocker*'
}
Invoke-BootstrapStaticCase 'negative control false Resume latch is rejected' {
    $bootstrap = Get-BootstrapFunction 'Start-ParentStageBootstrap'
    Invoke-BootstrapMutationControl 'false Resume latch' (Get-BootstrapTopAssignment $bootstrap '$state.resume_attempted') `
        '$state.resume_attempted = $false' $resumeCheck '*Resume-attempt latch must become true*'
}
Invoke-BootstrapStaticCase 'negative control omitted prepared lease validation is rejected' {
    $bootstrap = Get-BootstrapFunction 'Start-ParentStageBootstrap'
    $lease = @(Get-BootstrapCommands $bootstrap 'Assert-ParentStagePreparedLease')
    Invoke-BootstrapMutationControl 'omitted lease validation' $lease[0] 'Write-Output $null' $fenceCheck '*owned bootstrap/lease boundaries*'
}
Invoke-BootstrapStaticCase 'negative control omitted heartbeat health is rejected' {
    $bootstrap = Get-BootstrapFunction 'Start-ParentStageBootstrap'
    $healthy = @(Get-BootstrapMethods $bootstrap 'AssertHealthy')
    Invoke-BootstrapMutationControl 'omitted heartbeat health' $healthy[0] '$null' $armingCheck '*healthy heartbeat/parent_armed publication*'
}
Invoke-BootstrapStaticCase 'negative control omitted live-session retention is rejected' {
    $close = Get-BootstrapFunction 'Close-ParentStageBoundary'
    $retention = Get-BootstrapThrowGuard $close 'Resumed parent session retained;'
    Invoke-BootstrapMutationControl 'omitted retention' $retention '' $cleanupCheck '*Missing or ambiguous rejecting guard*'
}
Invoke-BootstrapStaticCase 'negative control old factory without private control is rejected' {
    $bootstrap = Get-BootstrapFunction 'Start-ParentStageBootstrap'
    $factory = @(Get-BootstrapMethods $bootstrap 'StartSuspendedWithControl')[0]
    Invoke-BootstrapMutationControl 'old no-control factory' $factory.Member 'StartSuspended' $resumeCheck '*control-enabled suspended child*'
}

if ($script:bootstrapCaseCount -ne $expectedCases -or $script:bootstrapPassed -ne $expectedCases -or
    $script:bootstrapFailures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Parent-bootstrap static wiring failed: ' + ($script:bootstrapFailures -join '; '))
}
$global:LASTEXITCODE = 0
[pscustomobject]@{
    suite = 'stratus-parent-bootstrap-static-wiring-v1'
    passed = $script:bootstrapPassed
    expected = $expectedCases
    source_path = $GatePath
    static_wiring_only = $true
    native_children_executed = $false
    full_parent_protocol_accepted = $false
    release_accepted = $false
    production_authorized = $false
}
