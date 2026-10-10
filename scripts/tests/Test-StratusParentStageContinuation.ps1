[CmdletBinding()]
param([string] $GatePath, [string] $RuntimePath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('GatePath')) {
    $GatePath = Join-Path $PSScriptRoot '..\Assert-StratusPostMigrationSuccessorGate.ps1'
}
if (-not $PSBoundParameters.ContainsKey('RuntimePath')) {
    $RuntimePath = Join-Path $PSScriptRoot '..\Stratus.ParentStage.Runtime.cs'
}

# Read/AST/source-only wiring regressions. No gate/helper body is executed, no
# runtime is compiled, and no native process, Docker command, network request or
# filesystem fixture is created. Negative controls change source text in memory.
# PASS is not behavioral, full-protocol, native lifetime or release acceptance.
$expectedCases = 33
$script:caseCount = 0
$script:passed = 0
$script:failures = [Collections.Generic.List[string]]::new()
$GatePath = [IO.Path]::GetFullPath($GatePath)
$RuntimePath = [IO.Path]::GetFullPath($RuntimePath)
$gateSource = [IO.File]::ReadAllText($GatePath)
$runtimeSource = [IO.File]::ReadAllText($RuntimePath)
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($gateSource, [ref]$tokens, [ref]$parseErrors)

function Assert-Static {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}
function Invoke-StaticCase {
    param([string] $Name, [scriptblock] $Action)
    $script:caseCount++
    try { & $Action; $script:passed++; Write-Output ('PASS parent-continuation static: ' + $Name) }
    catch {
        $script:failures.Add($Name + ': ' + $_.Exception.GetBaseException().Message)
        Write-Output ('FAIL parent-continuation static: ' + $Name)
    }
}
function Get-FunctionAst {
    param([string] $Name)
    $found = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name
    }, $true))
    Assert-Static ($found.Count -eq 1) ('Missing or ambiguous helper: ' + $Name)
    $parent = $found[0].Parent
    while ($null -ne $parent) {
        Assert-Static ($parent -isnot [Management.Automation.Language.FunctionDefinitionAst]) ('Nested helper: ' + $Name)
        $parent = $parent.Parent
    }
    return $found[0]
}
function Get-Commands {
    param([Management.Automation.Language.Ast] $Within, [string] $Name)
    return @($Within.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq $Name
    }, $true))
}
function Get-Methods {
    param([Management.Automation.Language.Ast] $Within, [string] $Name)
    return @($Within.FindAll({ param($node)
        $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
            $node.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and
            $node.Member.Value -ceq $Name
    }, $true))
}
function Get-Assignments {
    param([Management.Automation.Language.Ast] $Within, [string] $Left)
    return @($Within.FindAll({ param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -ceq $Left
    }, $true))
}
function Get-MainTry {
    $calls = @(Get-Commands $ast 'Invoke-ParentStageContinuation')
    Assert-Static ($calls.Count -eq 1) 'Expected one same-invocation continuation call.'
    $parent = $calls[0].Parent
    while ($null -ne $parent -and $parent -isnot [Management.Automation.Language.TryStatementAst]) {
        Assert-Static ($parent -isnot [Management.Automation.Language.FunctionDefinitionAst]) 'Continuation call moved out of the actual main invocation.'
        $parent = $parent.Parent
    }
    Assert-Static ($null -ne $parent -and $parent.Parent -eq $ast.EndBlock) 'Continuation must belong to the direct main Transition try.'
    return $parent
}
function Assert-Precedes {
    param([Management.Automation.Language.Ast] $Before, [Management.Automation.Language.Ast] $After, [string] $Message)
    Assert-Static ($Before.Extent.EndOffset -le $After.Extent.StartOffset) $Message
}
function Assert-DirectCommand {
    param([Management.Automation.Language.CommandAst] $Command, [Management.Automation.Language.StatementBlockAst] $Block)
    Assert-Static ($Command.Parent -is [Management.Automation.Language.PipelineAst] -and
        $Command.Parent.Parent -eq $Block -and $Command.Parent.PipelineElements.Count -eq 1 -and
        $Command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown) 'Required command is conditional, detached or dynamically invoked.'
}
function Assert-ExpressionPresent {
    param([Management.Automation.Language.Ast] $Within, [string] $Expression)
    $matches = @($Within.FindAll({ param($node)
        $node -is [Management.Automation.Language.BinaryExpressionAst] -and $node.Extent.Text -ceq $Expression
    }, $true))
    Assert-Static ($matches.Count -eq 1) ('Required rejecting expression missing: ' + $Expression)
}
function Get-NativeWatchdogSource {
    # This suite qualifies source predicates only for the original bootstrap
    # watchdog class. A similarly named method on the independent Transition
    # capture or NativeChild must neither satisfy nor make that lookup ambiguous.
    # The exact class declaration and its indentation boundary are deliberately
    # strict; a changed or duplicated class requires renewed source review.
    $found = [regex]::Matches($runtimeSource,
        '(?ms)^    public sealed class IndependentWatchdogLaunch : IDisposable \{.*?^    \}')
    Assert-Static ($found.Count -eq 1) 'Missing or ambiguous original IndependentWatchdogLaunch class.'
    return $found[0].Value
}
function Get-NativeMethod {
    param([string] $Name)
    # Source-only extraction inside one exact original class: the method's next
    # declaration is outside its body. No C# loader/compiler is used. Comments
    # are removed before source assertions, not before defining class scope.
    $classSource = Get-NativeWatchdogSource
    $pattern = '(?ms)^        (?:public|private|internal) (?:static )?[^\r\n{]+\b' + [regex]::Escape($Name) + '\([^\r\n]*\) \{.*?(?=^        (?:public|private|internal) |^    \})'
    $found = [regex]::Matches($classSource, $pattern)
    Assert-Static ($found.Count -eq 1) ('Missing or ambiguous native method: ' + $Name)
    return [regex]::Replace($found[0].Value, '(?m)//[^\r\n]*', '')
}
function Invoke-GateMutation {
    param([Management.Automation.Language.Ast] $Node, [string] $Replacement, [scriptblock] $Check, [string] $ExpectedFailure)
    $originalAst = $script:ast; $originalErrors = $script:parseErrors
    try {
        $mutated = $gateSource.Remove($Node.Extent.StartOffset, $Node.Extent.EndOffset - $Node.Extent.StartOffset).Insert($Node.Extent.StartOffset, $Replacement)
        $mutationTokens = $null; $mutationErrors = $null
        $script:ast = [Management.Automation.Language.Parser]::ParseInput($mutated, [ref]$mutationTokens, [ref]$mutationErrors)
        $script:parseErrors = $mutationErrors
        Assert-Static (@($mutationErrors).Count -eq 0) 'Negative control must remain valid PowerShell.'
        $failure = $null
        try { & $Check } catch { $failure = $_.Exception.GetBaseException().Message }
        Assert-Static ($null -ne $failure -and $failure -like $ExpectedFailure) ('Negative control missed intended rejection: ' + $failure)
    }
    finally { $script:ast = $originalAst; $script:parseErrors = $originalErrors }
}
function Invoke-NativeMutation {
    param([string] $Find, [string] $Replacement, [scriptblock] $Check, [string] $ExpectedFailure)
    $originalSource = $script:runtimeSource
    try {
        Assert-Static ($runtimeSource.Contains($Find)) 'Native negative-control anchor is missing.'
        $script:runtimeSource = $runtimeSource.Replace($Find, $Replacement)
        $failure = $null
        try { & $Check } catch { $failure = $_.Exception.GetBaseException().Message }
        Assert-Static ($null -ne $failure -and $failure -like $ExpectedFailure) ('Native negative control missed intended rejection: ' + $failure)
    }
    finally { $script:runtimeSource = $originalSource }
}

Invoke-StaticCase 'source parses and all earlier entry blockers remain' {
    Assert-Static (@($parseErrors).Count -eq 0) 'Gate source does not parse.'
    Assert-Static ($ast.EndBlock.Statements[2] -is [Management.Automation.Language.ThrowStatementAst] -and
        $ast.EndBlock.Statements[2].Extent.Text.Contains('WIP and non-authorizing')) 'Gate top-level blocker is missing.'
    $open = Get-FunctionAst 'Open-ParentStageBoundary'
    Assert-Static ($open.Body.EndBlock.Statements[0] -is [Management.Automation.Language.ThrowStatementAst]) 'Bootstrap entry blocker is missing.'
}

$noOutputCheck = {
    foreach ($node in @($ast.FindAll({ param($item)
        $item -is [Management.Automation.Language.VariableExpressionAst] -and
            $item.VariablePath.UserPath -ceq 'parentAuthorizationJson'
    }, $true))) {
        $parent = $node.Parent
        $accepted = ($parent -is [Management.Automation.Language.AssignmentStatementAst] -and $parent.Left -eq $node) -or
            ($parent -is [Management.Automation.Language.CommandAst] -and $parent.GetCommandName() -ceq 'Invoke-ParentStageContinuation')
        Assert-Static $accepted 'Intermediate authorization JSON may only be assigned or passed to the owned continuation; it must never be emitted.'
    }
}
Invoke-StaticCase 'intermediate authorization JSON has no success stream emission' $noOutputCheck

$invocationCheck = {
    $main = Get-MainTry
    $call = @(Get-Commands $main.Body 'Invoke-ParentStageContinuation')[0]
    Assert-DirectCommand $call $main.Body
    Assert-Static ($call.Extent.Text -match '-AuthorizationJson \$parentAuthorizationJson' -and
        $call.Extent.Text -match '-PostgresStartedAt \(\[string\]\$authorizationPostgres.started_at\)') 'Continuation did not receive actual same-invocation authorization/start evidence.'
    $publish = @(Get-Commands $main.Body 'Publish-ParentAuthorization')
    Assert-Static ($publish.Count -eq 1) 'Expected one actual authorization publication.'
    Assert-Precedes $publish[0] $call 'Continuation precedes its actual authorization publication.'
    Assert-Precedes $call $main.Finally 'Continuation escaped the main ownership lifetime.'
}
Invoke-StaticCase 'main Transition calls its continuation before finally in the same invocation' $invocationCheck

Invoke-StaticCase 'continuation enforces initialized single entry before registration' {
    $fn = Get-FunctionAst 'Invoke-ParentStageContinuation'
    $parameters = @($fn.Body.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    Assert-Static (($parameters -join ',') -ceq 'AuthorizationJson,PostgresStartedAt') 'Continuation accepts a callback or foreign capability.'
    $guard = $fn.Body.EndBlock.Statements[1]
    Assert-Static ($guard -is [Management.Automation.Language.IfStatementAst] -and
        $guard.Clauses[0].Item1.Extent.Text.Contains('$state.continuation_entered') -and
        $guard.Clauses[0].Item2.Statements[0] -is [Management.Automation.Language.ThrowStatementAst]) 'Continuation lacks direct rejecting one-way entry guard.'
    $latch = @(Get-Assignments $fn '$state.continuation_entered')
    Assert-Static ($latch.Count -eq 1 -and $latch[0].Right.Extent.Text -ceq '$true') 'Continuation entry latch is not one-way true.'
    $registration = @(Get-Commands $fn 'Invoke-ParentStageTransitionRegistration')
    Assert-Static ($registration.Count -eq 1) 'Continuation must call one fixed registration helper.'
    Assert-Precedes $guard $latch[0] 'Entry latch precedes its guard.'
    Assert-Precedes $latch[0] $registration[0] 'Registration runs before entry latching.'
}

$wipCheck = {
    $fn = Get-FunctionAst 'Invoke-ParentStageContinuation'
    $statements = @($fn.Body.EndBlock.Statements)
    Assert-Static ($statements[-1] -is [Management.Automation.Language.ThrowStatementAst] -and
        $statements[-1].Extent.Text.Contains('downstream provision/API/load continuation is WIP')) 'Downstream WIP throw must be the unconditional final continuation statement.'
    $registration = @(Get-Commands $fn 'Invoke-ParentStageTransitionRegistration')
    Assert-Static ($registration.Count -eq 1 -and $registration[0].Parent.Parent -eq $fn.Body.EndBlock) 'Registration must be direct before the downstream blocker.'
    Assert-Precedes $registration[0] $statements[-1] 'Downstream WIP blocker is misplaced.'
}
Invoke-StaticCase 'downstream remains explicitly blocked after owned registration' $wipCheck

Invoke-StaticCase 'registration is exact native owner lease schema with only create-new publication' {
    $fn = Get-FunctionAst 'Invoke-ParentStageTransitionRegistration'
    $assignment = @(Get-Assignments $fn '$registration')
    Assert-Static ($assignment.Count -eq 1) 'Expected one fixed registration document.'
    $tables = @($assignment[0].FindAll({ param($node) $node -is [Management.Automation.Language.HashtableAst] }, $true))
    Assert-Static ($tables.Count -eq 1) 'Registration must be one exact hashtable.'
    $keys = @($tables[0].KeyValuePairs | ForEach-Object { $_.Item1.Value })
    Assert-Static (($keys -join ',') -ceq 'format,session_id,lease_sha256,owner_pid,owner_start_time_utc,published_at,parent_authorization_sha256,transition_terminal_sha256,postgres_started_at') 'Registration schema differs.'
    Assert-Static ($assignment[0].Extent.Text.Contains('owner_pid = $PID') -and
        $assignment[0].Extent.Text.Contains("format = 'stratus-parent-transition-registration-v1'")) 'Registration does not bind the current owner and exact format.'
    $publication = @(Get-Methods $fn 'PublishNew')
    Assert-Static ($publication.Count -eq 1 -and
        $publication[0].Extent.Text -ceq '$files::PublishNew($paths.transition_registration, $bytes)') 'Registration must use the owned exact create-new publisher.'
}

Invoke-StaticCase 'registration retains actual authorization and terminal evidence before publication' {
    $fn = Get-FunctionAst 'Invoke-ParentStageTransitionRegistration'
    $locks = @(Get-Commands $fn 'Open-ParentStageEvidenceLock')
    $publication = @(Get-Methods $fn 'PublishNew')[0]
    foreach ($text in @('Open-ParentStageEvidenceLock $paths.parent_authorization $authorizationHash',
        'Open-ParentStageEvidenceLock $paths.transition_terminal $terminalHash')) {
        $found = @($locks | Where-Object { $_.Extent.Text -ceq $text })
        Assert-Static ($found.Count -eq 1) ('Missing held original evidence: ' + $text)
        Assert-Precedes $found[0] $publication 'Original evidence is retained only after publication.'
    }
    $terminalHash = @(Get-Assignments $fn '$terminalHash')
    Assert-Static ($terminalHash.Count -eq 1 -and $terminalHash[0].Right.Extent.Text -ceq '[string]$watchdogTerminalReceipt.sha256') 'Terminal digest must come from the retained validated receipt, not a fresh replacement hash.'
    Assert-Static (@(Get-Commands $fn 'Get-FileSha256').Count -eq 0) 'Registration must not adopt a freshly read terminal hash as authority.'
    foreach ($expression in @('$authorization.session_id -cne $transitionSessionId',
        '$authorization.parent_stage.session_id -cne $state.contract.session_id',
        '$authorization.parent_stage.ready_sha256 -cne $state.ready_sha256',
        '$terminal.session_id -cne $transitionSessionId')) {
        Assert-ExpressionPresent $fn $expression
    }
}

$publicationCheck = {
    $fn = Get-FunctionAst 'Invoke-ParentStageTransitionRegistration'
    $publication = @(Get-Methods $fn 'PublishNew')
    Assert-Static ($publication.Count -eq 1) 'Missing fixed create-new registration publisher.'
    $boundaries = @(Get-Commands $fn 'Assert-ParentStageBoundary')
    Assert-Static ($boundaries.Count -eq 4) 'Registration lost one of its pre-read, held-evidence publication, wait or post-readback boundaries.'
    Assert-Precedes $boundaries[0] $publication[0] 'Mutation publication precedes its owned parent boundary.'
    Assert-Precedes $boundaries[1] $publication[0] 'Mutation publication precedes its held-evidence parent recheck.'
    $retained = @(Get-Commands $fn 'Open-ParentStageEvidenceLock' | Where-Object { $_.Extent.Text -ceq 'Open-ParentStageEvidenceLock $paths.transition_registration $state.transition_registration_sha256' })
    Assert-Static ($retained.Count -eq 1) 'Published registration is not retained by its exact hash.'
    Assert-Precedes $publication[0] $retained[0] 'Registration retention precedes create-new publication.'
}
Invoke-StaticCase 'published registration stays held across live acknowledgement wait' $publicationCheck

$waitCheck = {
    $fn = Get-FunctionAst 'Invoke-ParentStageTransitionRegistration'
    $loops = @($fn.FindAll({ param($node) $node -is [Management.Automation.Language.WhileStatementAst] }, $true))
    Assert-Static ($loops.Count -eq 1) 'Expected one bounded registration wait.'
    $loop = $loops[0]
    $bounds = @($loop.FindAll({ param($node)
        $node -is [Management.Automation.Language.IfStatementAst] -and
            $node.Clauses[0].Item1.Extent.Text -ceq '$wait.ElapsedMilliseconds -ge 15000' -and
            $node.Clauses[0].Item2.Statements[0] -is [Management.Automation.Language.ThrowStatementAst]
    }, $true))
    Assert-Static ($bounds.Count -eq 2) 'Acknowledgement needs both fixed 15000ms pre-read and post-read deadline rejections.'
    $guard = @(Get-Commands $loop 'Assert-ParentStageBoundary')
    Assert-Static ($guard.Count -eq 2 -and $guard[0].Parent -eq $loop.Body.Statements[0]) 'Every acknowledgement poll must start with the live parent boundary.'
    $check = @(Get-Commands $loop 'Assert-ParentStageTransitionAck')[0]
    $held = @(Get-Commands $loop 'Open-ParentStageEvidenceLock')[0]
    $accepted = @(Get-Assignments $loop '$state.transition_ack_sha256')[0]
    Assert-Precedes $bounds[0] $check 'Acknowledgement parsing precedes its deadline.'
    Assert-Precedes $check $held 'Acknowledgement was held before strict validation.'
    Assert-Precedes $held $guard[1] 'No live-boundary recheck after acknowledgement retention.'
    Assert-Precedes $bounds[1] $accepted 'Acknowledgement accepted before post-read deadline.'
}
Invoke-StaticCase 'ack wait is continuously supervised and bounded before and after readback' $waitCheck

Invoke-StaticCase 'acknowledgement rejects unknown schema and coercible native types' {
    $fn = Get-FunctionAst 'Assert-ParentStageTransitionAck'
    $propertyChecks = @(Get-Commands $fn 'Assert-ParentStageProperties')
    Assert-Static ($propertyChecks.Count -eq 2) 'Ack and boundary need exact property checks.'
    $keys = @($propertyChecks[0].CommandElements[2].SafeGetValue())
    Assert-Static (($keys -join ',') -ceq 'format,session_id,lease_sha256,acknowledged_at,watchdog_pid,watchdog_start_time_utc,source_path,source_sha256,boundary,authorization') 'Ack exact schema differs.'
    Assert-Static (@(Get-Commands $fn 'Assert-NativeJsonInteger').Count -eq 1 -and
        @(Get-Commands $fn 'Assert-NativeJsonString').Count -eq 2 -and
        @(Get-Commands $fn 'Assert-ParentStageNoAuthorization').Count -eq 1) 'Ack native types/non-authorization checks are missing.'
}

$ackCheck = {
    $fn = Get-FunctionAst 'Assert-ParentStageTransitionAck'
    foreach ($expression in @('$ack.session_id -cne $state.contract.session_id',
        '$ack.lease_sha256 -cne $state.lease_sha256', '$ack.watchdog_pid -ne $state.launch.ProcessId',
        '$ack.watchdog_start_time_utc -cne $state.launch.StartedAt',
        '$ack.source_path -cne $state.contract.artifacts.transition_registration',
        '$ack.source_sha256 -cne $RegistrationSha256', '$ack.boundary.postgres_started_at -cne $PostgresStartedAt',
        '$ackTime -lt $PublishedAt', '$ackTime -ge $state.deadline',
        '$ackTime -gt [DateTimeOffset]::UtcNow.AddSeconds(2)')) {
        Assert-ExpressionPresent $fn $expression
    }
}
Invoke-StaticCase 'ack binds exact owned launch registration PostgreSQL start and fresh time' $ackCheck

$cleanupCheck = {
    $main = Get-MainTry
    $first = $main.Finally.Statements[0]
    if ($first -is [Management.Automation.Language.TryStatementAst]) {
        $close = @(Get-Commands $first.Body 'Close-ParentStageBoundary')
        Assert-Static ($close.Count -eq 1 -and $close[0].Parent -eq $first.Body.Statements[0]) 'Fatal parent cleanup must be the first release boundary.'
        Assert-Static (@($first.CatchClauses).Count -eq 1) 'Parent cleanup may only have one failure-preserving catch.'
        $catchStatements = @($first.CatchClauses[0].Body.Statements)
        Assert-Static ($catchStatements.Count -gt 0 -and $catchStatements[-1] -is [Management.Automation.Language.ThrowStatementAst]) 'Parent cleanup rejection must be rethrown, never suppressed.'
        Assert-Static ($catchStatements.Count -eq 2 -and $catchStatements[0] -is [Management.Automation.Language.IfStatementAst] -and
            $catchStatements[0].Clauses.Count -eq 1 -and $null -eq $catchStatements[0].ElseClause -and
            $catchStatements[0].Clauses[0].Item1.Extent.Text -ceq '$null -ne $transitionFailure' -and
            $catchStatements[0].Clauses[0].Item2.Statements.Count -eq 1 -and
            $catchStatements[0].Clauses[0].Item2.Statements[0] -is [Management.Automation.Language.ThrowStatementAst] -and
            $catchStatements[0].Clauses[0].Item2.Statements[0].Extent.Text.Contains('[AggregateException]::new')) 'Parent cleanup catch must preserve the primary failure and throw on every branch.'
        Assert-Static ($null -eq $first.Finally) 'Parent cleanup must not release resources in an inner finally.'
    }
    else {
        $close = @(Get-Commands $first 'Close-ParentStageBoundary')
        Assert-Static ($close.Count -eq 1 -and $first -is [Management.Automation.Language.PipelineAst]) 'Fatal parent cleanup must be the first release boundary.'
    }
    Assert-Static (@(Get-Commands $main.Finally 'Close-ParentStageBoundary').Count -eq 1) 'Parent cleanup can be bypassed or duplicated.'
    Assert-Static (@(Get-Commands $main.Finally 'Write-Warning').Count -eq 0) 'Cleanup may not emit a warning-only authorized return.'
    $retention = Get-FunctionAst 'Close-ParentStageBoundary'
    $guard = $retention.Body.EndBlock.Statements[1]
    Assert-Static ($guard -is [Management.Automation.Language.IfStatementAst] -and
        $guard.Clauses[0].Item1.Extent.Text -ceq '$null -ne $state -and $state.resume_attempted' -and
        $guard.Clauses[0].Item2.Statements[0] -is [Management.Automation.Language.ThrowStatementAst]) 'Resumed parent capabilities must remain retained before all disposal.'
}
Invoke-StaticCase 'fatal parent cleanup precedes every other local release and cannot be warning-only' $cleanupCheck

$aggregatePayloadCheck = {
    $main = Get-MainTry
    $first = $main.Finally.Statements[0]
    Assert-Static ($first -is [Management.Automation.Language.TryStatementAst] -and
        $first.CatchClauses.Count -eq 1) 'Cleanup must aggregate the primary and retained-lifetime failures in its fatal catch.'
    $constructors = @(Get-Methods $first.CatchClauses[0].Body 'new' | Where-Object {
        $_.Expression -is [Management.Automation.Language.TypeExpressionAst] -and
            $_.Expression.TypeName.FullName -ceq 'AggregateException'
    })
    Assert-Static ($constructors.Count -eq 1 -and $constructors[0].Arguments.Count -eq 2) 'Cleanup must have one explicit two-argument aggregate constructor.'
    $payload = $constructors[0].Arguments[1]
    Assert-Static ($payload -is [Management.Automation.Language.ConvertExpressionAst] -and
        $payload.Type.TypeName.FullName -ceq 'Exception[]' -and
        ($payload.Extent.Text -replace '\s', '') -ceq '[Exception[]]@($transitionFailure.Exception,$_.Exception)') 'Cleanup aggregate must preserve both the primary Transition exception and the current cleanup exception.'
}
Invoke-StaticCase 'fatal cleanup aggregate preserves both exact exception objects' $aggregatePayloadCheck

Invoke-StaticCase 'new helpers have no callbacks external lifecycle commands or caller native capabilities' {
    foreach ($name in @('Assert-ParentStageTransitionAck', 'Invoke-ParentStageTransitionRegistration', 'Invoke-ParentStageContinuation')) {
        $fn = Get-FunctionAst $name
        foreach ($command in @($fn.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true))) {
            Assert-Static ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
                $command.GetCommandName() -cnotin @('Start-Process', 'Invoke-Expression', 'Invoke-Command', 'Start-Job', 'Start-ThreadJob', 'Stop-Process', 'docker', 'wsl', 'cmd')) 'Continuation can delegate its owned lifetime to an uncontrolled command.'
        }
    }
}

$terminalOneWayCheck = {
    $begin = Get-NativeMethod 'BeginTerminalObservation'
    Assert-Static (-not ($runtimeSource -match 'terminalObservationStarted\s*=\s*false')) 'Terminal observation latch must never reset to false.'
    Assert-Static ($begin -match 'if\(terminalObservationStarted\)\s+throw' -and $begin -match 'if\(readiness==null\)\s+throw') 'Terminal observation needs one-way/live-readiness preconditions.'
    Assert-Static ($begin.Contains('PollLiveReady();') -and $begin.Contains('AssertCollectedFrame(acceptedFrameBytes);') -and
        $begin.Contains('terminalObservationStarted=true;') -and $begin.Contains('AssertTerminalDeadline();') -and
        $begin.IndexOf('PollLiveReady();') -lt $begin.IndexOf('terminalObservationStarted=true;') -and
        $begin.IndexOf('AssertCollectedFrame(acceptedFrameBytes);') -lt $begin.IndexOf('terminalObservationStarted=true;') -and
        $begin.Contains('Stopwatch observationClock=Stopwatch.StartNew();') -and
        $begin.IndexOf('Stopwatch observationClock=Stopwatch.StartNew();') -lt $begin.IndexOf('PollLiveReady();') -and
        $begin.Contains('terminalClock=observationClock;') -and
        $begin.IndexOf('terminalClock=observationClock;') -lt $begin.IndexOf('AssertTerminalDeadline();') -and
        $begin.IndexOf('AssertTerminalDeadline();') -lt $begin.IndexOf('terminalObservationStarted=true;')) 'Terminal observation latches before its exact live frame proof and original-entry deadline.'
    $deadline = Get-NativeMethod 'AssertTerminalDeadline'
    Assert-Static ($deadline.Contains('terminalClock.ElapsedMilliseconds>=30000') -and $deadline.Contains('throw new TimeoutException')) 'Terminal observation lost its fixed 30000ms deadline.'
}
Invoke-StaticCase 'native terminal observation is irreversible and starts from accepted live readiness' $terminalOneWayCheck

$terminalReadOnlyCheck = {
    foreach ($name in @('BeginTerminalObservation', 'PollTerminalExit', 'AssertTerminalFrame', 'AssertTerminalDeadline')) {
        $method = Get-NativeMethod $name
        Assert-Static ($method -notmatch '\b(?:TerminateProcess|TerminateJobObject|SuspendThread|ResumeThread|CloseHandle|CreateProcess|Dispose|AbortBeforeResume|PublishNew|ReplacePublished|WriteAllText|WriteAllBytes)\s*\(') ('Native observation must not mutate lifecycle or release handles: ' + $name)
    }
}
Invoke-StaticCase 'native terminal observation path contains no kill resume or handle-release calls' $terminalReadOnlyCheck

$terminalFrameBodyCheck = {
    $frame = Get-NativeMethod 'AssertTerminalFrame'
    Assert-Static ($frame -match 'if\(faults.Count!=0\)\s+throw new AggregateException\(faults.ToArray\(\)\);' -and
        $frame -match 'if\(readiness==null \|\| acceptedFrameBytes<1 \|\| errorCount!=0 \|\|\s*outputCount!=acceptedFrameBytes\+1 \|\| outputBytes.Length!=acceptedFrameBytes\+1\)\s*throw new InvalidDataException\(') 'Terminal frame body must reject collector faults, missing readiness, stderr and changed output counts or lengths.'
    Assert-Static ($frame.Contains('byte[] bytes=outputBytes.ToArray();') -and
        $frame -match 'if\(bytes\[acceptedFrameBytes\]!=10\)\s*throw new InvalidDataException\(') 'Terminal frame body must copy the captured output and reject a changed exact LF terminator.'
    Assert-Static ($frame.Contains('byte[] frame=new byte[acceptedFrameBytes]; Array.Copy(bytes,frame,acceptedFrameBytes);') -and
        $frame -match 'if\(Files.Hash\(frame\)!=readiness.FrameSha256\)\s*throw new InvalidDataException\(') 'Terminal frame body must reject bytes whose digest differs from the retained readiness frame.'
    Assert-Static ($frame.IndexOf('byte[] bytes=outputBytes.ToArray();') -lt $frame.IndexOf('if(bytes[acceptedFrameBytes]!=10)') -and
        $frame.IndexOf('if(bytes[acceptedFrameBytes]!=10)') -lt $frame.IndexOf('Array.Copy(bytes,frame,acceptedFrameBytes);') -and
        $frame.IndexOf('Array.Copy(bytes,frame,acceptedFrameBytes);') -lt $frame.IndexOf('if(Files.Hash(frame)!=readiness.FrameSha256)')) 'Terminal frame validation must hash the captured frame only after exact length and newline validation.'
}
Invoke-StaticCase 'native terminal frame helper itself rejects altered captured bytes and framing' $terminalFrameBodyCheck

$terminalProofCheck = {
    $poll = Get-NativeMethod 'PollTerminalExit'
    Assert-Static ($poll -match 'if\(!terminalObservationStarted\)\s+throw' -and
        [regex]::Matches($poll, 'AssertIdentity\(\); AssertNoJob\(\); ThrowFaults\(\);').Count -eq 2 -and
        [regex]::Matches($poll, 'AssertTerminalFrame\(\); AssertTerminalDeadline\(\);').Count -eq 2) 'Native terminal poll must recheck held identity frame and deadline before proof.'
    Assert-Static ($poll.Contains('if(exitCode!=0) throw') -and $poll.Contains('GetExitCodeProcess(process,out exitCode)') -and
        $poll.Contains('GetProcessTimes(process,out created,out exited,out kernel,out user)')) 'Native terminal poll lacks exact zero exit/time proof.'
    Assert-Static ($poll.Contains('if(!outputEof || !errorEof || !controlDrained) return null;') -and
        $poll.IndexOf('if(!outputEof || !errorEof || !controlDrained) return null;') -lt $poll.IndexOf('terminalProof=TerminalExitProof.FromOwnedObservation(this);')) 'Native terminal proof must wait for both exact captured EOFs.'
    Assert-Static ($runtimeSource.Contains('private TerminalExitProof(IndependentWatchdogLaunch launch)') -and
        $runtimeSource.Contains('!launch.terminalProofReady') -and $runtimeSource.Contains('!launch.terminalObservationStarted')) 'Terminal exit proof must not be publicly constructible from supplied receipt data.'
}
Invoke-StaticCase 'native exit proof requires source identity unchanged ready frame zero exit and both EOFs' $terminalProofCheck

Invoke-StaticCase 'terminal observation cannot fall back to permissive live readiness' {
    foreach ($name in @('PollReady', 'PollLiveReady')) {
        $method = Get-NativeMethod $name
        Assert-Static ($method -match 'if\(terminalObservationStarted\)\s+throw') 'Live readiness must reject after terminal observation starts.'
    }
    $frame = Get-NativeMethod 'AssertCollectedFrame'
    Assert-Static ($frame.Contains('outputEof || errorEof') -and $frame.Contains('errorCount!=0')) 'Live readiness weakened its strict EOF/stderr rejection.'
}

Invoke-StaticCase 'negative control premature authorization output is rejected' {
    $call = @(Get-Commands $ast 'Invoke-ParentStageContinuation')[0]
    Invoke-GateMutation $call '$parentAuthorizationJson' $noOutputCheck '*must never be emitted*'
}
Invoke-StaticCase 'negative control missing same-invocation continuation is rejected' {
    $call = @(Get-Commands $ast 'Invoke-ParentStageContinuation')[0]
    Invoke-GateMutation $call 'Write-Output $null' $invocationCheck '*one same-invocation continuation call*'
}
Invoke-StaticCase 'negative control removed downstream blocker is rejected' {
    $fn = Get-FunctionAst 'Invoke-ParentStageContinuation'
    Invoke-GateMutation $fn.Body.EndBlock.Statements[-1] 'return' $wipCheck '*unconditional final continuation statement*'
}
Invoke-StaticCase 'negative control acknowledgement registration-hash bypass is rejected' {
    $fn = Get-FunctionAst 'Assert-ParentStageTransitionAck'
    $node = @($fn.FindAll({ param($item) $item -is [Management.Automation.Language.BinaryExpressionAst] -and $item.Extent.Text -ceq '$ack.source_sha256 -cne $RegistrationSha256' }, $true))[0]
    Invoke-GateMutation $node '$false' $ackCheck '*Required rejecting expression missing: $ack.source_sha256*'
}
Invoke-StaticCase 'negative control suppressed parent cleanup is rejected before other releases' {
    $main = Get-MainTry
    Invoke-GateMutation $main.Finally.Statements[0] 'try { Close-ParentStageBoundary } catch { Write-Warning $_ }' $cleanupCheck '*must be rethrown*'
}
Invoke-StaticCase 'negative control cleanup aggregate losing the primary exception is rejected' {
    $main = Get-MainTry
    $constructor = @(Get-Methods $main.Finally.Statements[0].CatchClauses[0].Body 'new' | Where-Object {
        $_.Expression -is [Management.Automation.Language.TypeExpressionAst] -and
            $_.Expression.TypeName.FullName -ceq 'AggregateException'
    })[0]
    Invoke-GateMutation $constructor.Arguments[1] '[Exception[]]@($_.Exception)' $aggregatePayloadCheck '*must preserve both the primary Transition exception*'
}
Invoke-StaticCase 'negative control native terminal kill call is rejected' {
    Invoke-NativeMutation 'public TerminalExitProof PollTerminalExit() {' 'public TerminalExitProof PollTerminalExit() { TerminateProcess(process,200);' $terminalReadOnlyCheck '*must not mutate lifecycle*'
}
Invoke-StaticCase 'negative control missing captured EOF requirement is rejected' {
    Invoke-NativeMutation 'if(!outputEof || !errorEof || !controlDrained) return null;' 'if(false) return null;' $terminalProofCheck '*must wait for both exact captured EOFs*'
}
Invoke-StaticCase 'negative control empty terminal frame helper is rejected' {
    $method = [regex]::Matches($runtimeSource, '(?ms)^        private void AssertTerminalFrame\(\) \{.*?(?=^        public TerminalExitProof PollTerminalExit\()')
    Assert-Static ($method.Count -eq 1) 'Expected one exact terminal-frame helper for the negative control.'
    Invoke-NativeMutation $method[0].Value "        private void AssertTerminalFrame() { }`n" $terminalFrameBodyCheck '*Terminal frame body must reject collector faults*'
}
Invoke-StaticCase 'negative control resettable native terminal latch is rejected' {
    Invoke-NativeMutation 'terminalObservationStarted=true;' 'terminalObservationStarted=false;' $terminalOneWayCheck '*latch must never reset to false*'
}

Invoke-StaticCase 'native terminal method selector stays within the original watchdog class' {
    $classSource = Get-NativeWatchdogSource
    Assert-Static ($classSource.StartsWith('    public sealed class IndependentWatchdogLaunch : IDisposable {') -and
        -not $classSource.Contains('class IndependentTransitionLaunch') -and
        -not $classSource.Contains('class NativeChild')) 'Original native class extraction crossed its class boundary.'
    $poll = Get-NativeMethod 'PollTerminalExit'
    Assert-Static ($poll.Contains('if(!terminalObservationStarted)') -and
        -not $poll.Contains('if(!resumeSucceeded || readyProof==null)')) 'Terminal selector adopted a different native class method.'
}
Invoke-StaticCase 'negative control another class cannot supply a missing original terminal method' {
    $classSource = Get-NativeWatchdogSource
    $mutatedClass = $classSource.Replace('public TerminalExitProof PollTerminalExit() {',
        'public TerminalExitProof RemovedOriginalTerminalExit() {')
    Assert-Static ($mutatedClass -cne $classSource -and
        $runtimeSource.Remove($runtimeSource.IndexOf($classSource), $classSource.Length).Contains('public TerminalExitProof PollTerminalExit() {')) 'Wrong-class negative control lacks its independent same-name decoy.'
    Invoke-NativeMutation $classSource $mutatedClass { [void](Get-NativeMethod 'PollTerminalExit') } '*Missing or ambiguous native method: PollTerminalExit*'
}
Invoke-StaticCase 'negative control duplicate original terminal methods remain ambiguous' {
    $classSource = Get-NativeWatchdogSource
    $mutatedClass = $classSource.Replace('public TerminalExitProof PollTerminalExit() {',
        "public TerminalExitProof PollTerminalExit() { }`n        public TerminalExitProof PollTerminalExit() {")
    Invoke-NativeMutation $classSource $mutatedClass { [void](Get-NativeMethod 'PollTerminalExit') } '*Missing or ambiguous native method: PollTerminalExit*'
}
Invoke-StaticCase 'negative control duplicate original watchdog classes remain ambiguous' {
    $classSource = Get-NativeWatchdogSource
    Invoke-NativeMutation $classSource ($classSource + "`n" + $classSource) { [void](Get-NativeMethod 'PollTerminalExit') } '*Missing or ambiguous original IndependentWatchdogLaunch class*'
}

if ($script:caseCount -ne $expectedCases -or $script:passed -ne $expectedCases -or $script:failures.Count -ne 0) {
    $global:LASTEXITCODE = 1
    throw ('Parent-continuation static wiring failed: ' + ($script:failures -join '; ') + '; cases=' + $script:caseCount)
}
$global:LASTEXITCODE = 0
[pscustomobject]@{
    suite = 'stratus-parent-continuation-static-wiring-v1'
    passed = $script:passed; expected = $expectedCases
    gate_path = $GatePath; runtime_path = $RuntimePath
    static_wiring_only = $true; native_children_executed = $false
    full_parent_protocol_accepted = $false; release_accepted = $false; production_authorized = $false
}
