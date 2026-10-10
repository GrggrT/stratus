param([string] $WatchdogPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrEmpty($WatchdogPath)) { $WatchdogPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'Invoke-StratusParentStageWatchdog.ps1' }
# AST checks and eight narrowly extracted managed functions only. The watchdog
# body, Start-ParentOwnedObserver, native runtime, observer, Docker and DB are
# NEVER loaded/executed. Stub StopAndProve/Update calls do not prove OS behavior.
$source = Get-Content -LiteralPath $WatchdogPath -Raw
function Read-ObserverTestAst {
    param([string] $Text)
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)
    if (@($errors).Count -ne 0) { throw 'Observer wiring source has AST parse errors.' }
    return $ast
}
function Get-ObserverTestFunction {
    param([object] $Ast, [string] $Name)
    $found = @($Ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $Name }, $true))
    if ($found.Count -ne 1) { throw "Required observer function differs: $Name" }
    return $found[0]
}
function Get-ObserverTestCommand {
    param([object] $Ast, [string] $Name)
    return @($Ast.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq $Name }, $true))
}
function Get-ObserverTestMember {
    param([object] $Ast, [string] $Name)
    return @($Ast.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
        $n.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and $n.Member.Value -ceq $Name }, $true))
}
function Assert-ObserverTestComparison {
    param([object] $Ast, [string] $Text, [string] $Label)
    $found = @($Ast.FindAll({ param($n) $n -is [Management.Automation.Language.BinaryExpressionAst] -and $n.Extent.Text -ceq $Text }, $true))
    if ($found.Count -ne 1) { throw "Required observer comparison differs: $Label" }
}
function Assert-ObserverWiringAst {
    param([string] $Text)
    $ast = Read-ObserverTestAst $Text
    $entry = @($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.ThrowStatementAst] })
    if ($entry.Count -ne 1 -or $entry[0].Extent.Text -notmatch 'parent-stage watchdog is WIP and non-authorizing') { throw 'Unconditional observer host entry barrier differs.' }
    $start = Get-ObserverTestFunction $ast 'Start-ParentOwnedObserver'
    if ($start.Body.ParamBlock.Parameters.Count -ne 1 -or $start.Body.ParamBlock.Parameters[0].Name.VariablePath.UserPath -cne 'Intent') { throw 'Observer launch signature accepts caller authority.' }
    foreach ($name in @('Assert-ParentHeartbeat', 'Assert-ParentTransitionEvidenceHeld', 'Test-ParentValidatedCompletionUnchanged',
        'Get-ParentContainer', 'Assert-ParentTopology', 'Assert-ParentRegularAncestry', 'Read-ParentHeldBytes', 'Assert-ParentBootstrapHeldFile', 'Get-ParentObserverArguments')) {
        if (@(Get-ObserverTestCommand $start $name).Count -lt 1) { throw "Observer launch prerequisite missing: $name" }
    }
    $create = @(Get-ObserverTestMember $start 'StartSuspended')
    $suspended = @(Get-ObserverTestMember $start 'AssertSuspendedOwned')
    $resume = @(Get-ObserverTestMember $start 'Resume')
    if ($create.Count -ne 1 -or $suspended.Count -ne 1 -or $resume.Count -ne 1 -or
        -not $create[0].Static -or $create[0].Expression -isnot [Management.Automation.Language.TypeExpressionAst] -or
        $create[0].Expression.TypeName.FullName -cne 'Stratus.ParentStage.NativeChild' -or $create[0].Arguments.Count -ne 6) {
        throw 'Observer native launch capability shape differs.'
    }
    $latches = @{}
    foreach ($name in @('script:observerLaunchAttempted', 'script:observerChildResumed')) {
        $items = @($start.FindAll({ param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and
            $n.Left -is [Management.Automation.Language.VariableExpressionAst] -and $n.Left.VariablePath.UserPath -ceq $name -and $n.Right.Extent.Text -ceq '$true' }, $true))
        if ($items.Count -ne 1) { throw "Observer attempt latch differs: $name" }
        $latches[$name] = $items[0].Extent.StartOffset
    }
    if ($latches['script:observerLaunchAttempted'] -ge $create[0].Extent.StartOffset -or
        $create[0].Extent.StartOffset -ge $suspended[0].Extent.StartOffset -or
        $suspended[0].Extent.StartOffset -ge $latches['script:observerChildResumed'] -or
        $latches['script:observerChildResumed'] -ge $resume[0].Extent.StartOffset) { throw 'Observer suspended/attempt/resume ordering differs.' }
    foreach ($name in @('GetProcessById', 'Kill')) {
        if (@(Get-ObserverTestMember $start $name).Count -ne 0) { throw 'Observer launch contains unowned process handling.' }
    }
    if (@(Get-ObserverTestMember $start 'Dispose' | Where-Object { $_.Expression.Extent.Text -cne '$random' }).Count -ne 0) { throw 'Observer launch disposes a native lifetime.' }
    $update = Get-ObserverTestFunction $ast 'Update-ParentOwnedObserver'
    foreach ($pair in @(@('PollFirstOutputLine', '450000, 8192'), @('PollResult', '450000'))) {
        $calls = @(Get-ObserverTestMember $update $pair[0])
        if ($calls.Count -ne 1 -or (($calls[0].Arguments | ForEach-Object { $_.Extent.Text }) -join ', ') -cne $pair[1]) { throw "Observer bounded capture call differs: $($pair[0])" }
    }
    $live = @(Get-ObserverTestMember $update 'AssertRunningOwned')
    if ($live.Count -ne 2) { throw 'Observer pre-ACK native liveness checks missing.' }
    $readyCheck = @(Get-ObserverTestCommand $update 'Assert-ParentObserverReadyFrame')
    $ack = @(Get-ObserverTestCommand $update 'Write-ParentAck')
    if ($readyCheck.Count -ne 1 -or $ack.Count -ne 1 -or $readyCheck[0].Extent.StartOffset -ge $ack[0].Extent.StartOffset) { throw 'Observer readiness validation must precede ACK.' }
    if ($live[1].Extent.EndOffset -ge $ack[0].Extent.StartOffset -or $live[1].Arguments[0].Extent.Text -cne '450000') { throw 'Observer immediate ACK liveness boundary differs.' }
    $clean = @(Get-ObserverTestMember $update 'AssertCleanCapture')
    $poll = @(Get-ObserverTestMember $update 'PollResult')
    if ($clean.Count -ne 2 -or $clean[0].Extent.EndOffset -ge $poll[0].Extent.StartOffset -or $poll[0].Extent.EndOffset -ge $clean[1].Extent.StartOffset -or
        $clean[0].Arguments[0].Extent.Text -cne '450000' -or $clean[1].Arguments[0].Extent.Text -cne '450000') { throw 'Observer pre/post result capture boundaries differ.' }
    if (@(Get-ObserverTestCommand $update 'Write-ParentRecoveryDocument').Count -ne 1) { throw 'Observer own completion publication missing.' }
    Assert-ObserverTestComparison $update '$script:observerCompleted.receipt_sha256 -cne $receiptHash' 'own completion receipt'
    foreach ($name in @('Assert-ParentHeartbeat', 'Assert-ParentTransitionEvidenceHeld', 'Read-ParentObserverEvidence', 'ConvertFrom-ParentJson')) {
        if (@(Get-ObserverTestCommand $update $name).Count -lt 1) { throw "Observer completion prerequisite missing: $name" }
    }
    foreach ($pair in @(
        @('$result.ExitCode -ne 0', 'exit0'), @('$result.StderrBytes -ne 0', 'empty stderr'),
        @('$terminal.output_sha256 -cne $output.sha256', 'captured output hash'), @('$terminal.output_bytes -ne $output.bytes', 'captured output bytes'),
        @('$result.Stdout -cne $expectedText', 'exact complete stdout'), @('$result.StdoutBytes -ne $expectedBytes.LongLength', 'stdout byte length'))) {
        Assert-ObserverTestComparison $update $pair[0] $pair[1]
    }
    foreach ($name in @('Dispose', 'Kill', 'StopAndProve')) {
        if (@(Get-ObserverTestMember $update $name).Count -ne 0) { throw 'Observer success polling mutates native lifetime.' }
    }
    $ready = Get-ObserverTestFunction $ast 'Assert-ParentObserverReadyFrame'
    foreach ($pair in @(@('$frameValue.nonce -cne $script:observerPlan.nonce', 'private nonce'),
        @('$frameValue.ready_sha256 -cne $Ready.sha256', 'captured ready digest'),
        @('$Frame.ProcessId -ne $script:observerChild.ProcessId', 'native frame PID'))) { Assert-ObserverTestComparison $ready $pair[0] $pair[1] }
    $reader = Get-ObserverTestFunction $ast 'Read-ParentObserverEvidence'
    foreach ($name in @('Assert-ParentRegularAncestry', 'Assert-ParentTransitionSingleLink', 'ConvertFrom-ParentObserverOutput')) {
        if (@(Get-ObserverTestCommand $reader $name).Count -lt 1) { throw "Observer held-reader prerequisite missing: $name" }
    }
    $parser = Get-ObserverTestFunction $ast 'ConvertFrom-ParentObserverOutput'
    if (@(Get-ObserverTestMember $parser 'ValidateObserverOutput').Count -ne 1) { throw 'Observer full-output bounded strict parser missing.' }
    $stop = Get-ObserverTestFunction $ast 'Stop-ParentOwnedObserver'
    if (@(Get-ObserverTestMember $stop 'StopAndProve').Count -ne 1 -or @(Get-ObserverTestMember $stop 'Dispose').Count -ne 0) { throw 'Observer containment lacks exact retained empty-tree proof.' }
    $containment = Get-ObserverTestFunction $ast 'Invoke-ParentContainment'
    $stopCall = @(Get-ObserverTestCommand $containment 'Stop-ParentOwnedObserver')
    $containers = @(Get-ObserverTestCommand $containment 'Invoke-ParentContainerContainmentRound')
    if ($stopCall.Count -ne 1 -or $containers.Count -ne 1 -or $stopCall[0].Extent.StartOffset -ge $containers[0].Extent.StartOffset) { throw 'Observer containment ordering differs.' }
    $handoff = Get-ObserverTestFunction $ast 'Assert-ParentHandoff'
    if (@(Get-ObserverTestCommand $handoff 'Assert-ParentObserverCompleted').Count -ne 1) { throw 'Handoff omits owned observer completion.' }
    Assert-ObserverTestComparison $handoff '$evidence.value.evidence.runtime_sha256 -cne $script:observerCompleted.output_sha256' 'handoff owned runtime hash'
    if (@(Get-ObserverTestCommand $handoff 'Read-ParentObserverEvidence').Count -ne 1) { throw 'Handoff bypasses owned bounded output reader.' }
    $ackWriter = Get-ObserverTestFunction $ast 'Write-ParentAck'
    $ackGuards = @(Get-ObserverTestCommand $ackWriter 'Assert-ParentObserverAckBoundary')
    $ackPublish = @(Get-ObserverTestCommand $ackWriter 'Write-ParentDocument')
    $ackHeartbeats = @(Get-ObserverTestCommand $ackWriter 'Assert-ParentHeartbeat')
    $ackReads = @(Get-ObserverTestCommand $ackWriter 'Read-ParentDocument')
    $ackCache = @($ackWriter.FindAll({ param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$observed[$Name]' }, $true))
    if ($ackGuards.Count -ne 3 -or $ackPublish.Count -ne 1 -or $ackGuards[1].Extent.EndOffset -ge $ackPublish[0].Extent.StartOffset -or
        $ackHeartbeats.Count -ne 2 -or $ackReads.Count -ne 2 -or $ackCache.Count -ne 1 -or
        $ackGuards[0].Extent.StartOffset -le $ackReads[0].Extent.EndOffset -or
        $ackGuards[2].Extent.StartOffset -le $ackReads[1].Extent.EndOffset -or
        $ackGuards[2].Extent.StartOffset -le $ackHeartbeats[1].Extent.EndOffset -or
        $ackGuards[2].Extent.EndOffset -ge $ackCache[0].Extent.StartOffset) { throw 'Observer generic ACK boundary guards differ.' }
    $ackBoundary = Get-ObserverTestFunction $ast 'Assert-ParentObserverAckBoundary'
    Assert-ObserverTestComparison $ackBoundary '$script:observerPlan.clock.ElapsedMilliseconds -ge 30000' 'original ACK readiness deadline'
    $transition = Get-ObserverTestFunction $ast 'Assert-ParentTransitionRegistration'
    if ($transition.Body.EndBlock.Statements[-1] -isnot [Management.Automation.Language.ThrowStatementAst]) { throw 'Transition unqualified native provenance barrier missing.' }
    $supervisor = Get-ObserverTestFunction $ast 'Assert-ParentSuccessorSupervisor'
    if ($supervisor.Body.EndBlock.Statements.Count -ne 1 -or $supervisor.Body.EndBlock.Statements[0] -isnot [Management.Automation.Language.ThrowStatementAst]) { throw 'Terminal supervisor barrier missing.' }
    if ($Text -notmatch 'External observer PID/Job registration is forbidden') { throw 'External observer registration rejection missing.' }
}

$results = [Collections.Generic.List[object]]::new()
function Invoke-ObserverTest {
    param([string] $CaseName, [scriptblock] $Body)
    try { & $Body } catch { throw "Observer contract case $CaseName failed: $($_.Exception.Message)" }
    $results.Add([pscustomobject]@{ name = $CaseName; passed = $true })
}
function Assert-ObserverTestThrows {
    param([scriptblock] $Body, [string] $Pattern)
    $failure = $null; try { & $Body } catch { $failure = $_.Exception.Message }
    if ($null -eq $failure -or $failure -notmatch $Pattern) { throw "Expected rejection $Pattern; actual $failure" }
}
Invoke-ObserverTest 'current AST launch/capture/containment barriers' { Assert-ObserverWiringAst $source }
$removals = @(
    @('Read-ParentHeldBytes $sourcePath $source.sha256', '$null', 'launch prerequisite missing: Read-ParentHeldBytes'),
    @('Assert-ParentBootstrapHeldFile $script:hostLock $contract.watchdog.host_path $contract.watchdog.host_sha256', '$null', 'launch prerequisite missing: Assert-ParentBootstrapHeldFile'),
    @('$script:observerLaunchAttempted = $true', '$script:observerLaunchAttempted = $false', 'attempt latch differs'),
    @('$script:observerChildResumed = $true', '$script:observerChildResumed = $false', 'attempt latch differs'),
    @('$script:observerChild.AssertSuspendedOwned()', '$null', 'native launch capability shape'),
    @('$script:observerChild.Resume()', '$null', 'native launch capability shape'),
    @('$script:observerChild.PollFirstOutputLine(450000, 8192)', '$null', 'bounded capture call differs: PollFirstOutputLine'),
    @('$script:observerChild.PollResult(450000)', '$null', 'bounded capture call differs: PollResult'),
    @('Assert-ParentObserverReadyFrame $frame $ready', '$null', 'readiness validation must precede ACK'),
    @('$result.ExitCode -ne 0', '$false', 'comparison differs: exit0'),
    @('$result.StderrBytes -ne 0', '$false', 'comparison differs: empty stderr'),
    @('$terminal.output_sha256 -cne $output.sha256', '$false', 'comparison differs: captured output hash'),
    @('$terminal.output_bytes -ne $output.bytes', '$false', 'comparison differs: captured output bytes'),
    @('$result.Stdout -cne $expectedText', '$false', 'comparison differs: exact complete stdout'),
    @('$result.StdoutBytes -ne $expectedBytes.LongLength', '$false', 'comparison differs: stdout byte length'),
    @('$frameValue.nonce -cne $script:observerPlan.nonce', '$false', 'comparison differs: private nonce'),
    @('$frameValue.ready_sha256 -cne $Ready.sha256', '$false', 'comparison differs: captured ready digest'),
    @('$Frame.ProcessId -ne $script:observerChild.ProcessId', '$false', 'comparison differs: native frame PID'),
    @('[Stratus.ParentStage.StrictJson]::ValidateObserverOutput($Bytes)', '$null', 'full-output bounded strict parser missing'),
    @('$script:observerChild.StopAndProve()', '$null', 'exact retained empty-tree proof'),
    @('$script:observerChild.AssertRunningOwned(450000)', '$null', 'pre-ACK native liveness checks missing'),
    @('$script:observerChild.AssertCleanCapture(450000)', '$null', 'pre/post result capture boundaries differ'),
    @('$script:observerCompleted.receipt_sha256 -cne $receiptHash', '$false', 'comparison differs: own completion receipt'),
    @('$evidence.value.evidence.runtime_sha256 -cne $script:observerCompleted.output_sha256', '$false', 'comparison differs: handoff owned runtime hash'),
    @("if (`$Name -ceq 'observer_ack') { Assert-ParentObserverAckBoundary }", '$null', 'generic ACK boundary guards differ'),
    @('$script:observerPlan.clock.ElapsedMilliseconds -ge 30000', '$false', 'comparison differs: original ACK readiness deadline'))
foreach ($row in $removals) {
    $before = $row[0]; $after = $row[1]; $pattern = $row[2]
    Invoke-ObserverTest ('AST removal rejected: ' + $before) {
        if (-not $source.Contains($before)) { throw 'Removal target does not exist.' }
        $changed = $source.Replace($before, $after)
        Assert-ObserverTestThrows { Assert-ObserverWiringAst $changed } $pattern
    }
}
foreach ($site in @(0, 1, 2)) {
    Invoke-ObserverTest ('individual generic ACK live guard removal rejected: ' + @('existing readback', 'prepublication', 'post-readback cache')[$site]) {
        $writer = Get-ObserverTestFunction (Read-ObserverTestAst $source) 'Write-ParentAck'
        $guard = @(Get-ObserverTestCommand $writer 'Assert-ParentObserverAckBoundary')[$site]
        $changed = $source.Substring(0, $guard.Extent.StartOffset) + '$null' + $source.Substring($guard.Extent.EndOffset)
        Assert-ObserverTestThrows { Assert-ObserverWiringAst $changed } 'generic ACK boundary guards differ'
    }
}
foreach ($site in @(1, 2)) {
    Invoke-ObserverTest ('generic ACK live guard misordering rejected: ' + @('unused', 'after publication', 'before readback and heartbeat')[$site]) {
        $writer = Get-ObserverTestFunction (Read-ObserverTestAst $source) 'Write-ParentAck'
        $guard = @(Get-ObserverTestCommand $writer 'Assert-ParentObserverAckBoundary')[$site]
        $changed = $source.Substring(0, $guard.Extent.StartOffset) + '$null' + $source.Substring($guard.Extent.EndOffset)
        $anchor = '$published = Read-ParentDocument $paths.$Name'
        if (-not $changed.Contains($anchor)) { throw 'ACK reorder anchor missing.' }
        $changed = $changed.Replace($anchor, ('Assert-ParentObserverAckBoundary; ' + $anchor))
        Assert-ObserverTestThrows { Assert-ObserverWiringAst $changed } 'generic ACK boundary guards differ'
    }
}
# Extract only these three functions after checking every command/member in
# them. No Start/Poll/reader/parent body can cross this managed-only loader.
$ast = Read-ObserverTestAst $source
foreach ($name in @('Get-ParentObserverArguments', 'Stop-ParentOwnedObserver', 'Assert-ParentObserverCompleted')) {
    $function = Get-ObserverTestFunction $ast $name
    foreach ($command in $function.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] }, $true)) {
        if ($command.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or $command.GetCommandName() -cne 'Update-ParentOwnedObserver') { throw 'Managed observer extractor found an unreviewed command.' }
    }
    foreach ($member in $function.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
        if ($member.Static -or $member.Member -isnot [Management.Automation.Language.StringConstantExpressionAst] -or $member.Member.Value -cnotin @('Substring', 'StopAndProve', 'ContainsKey')) { throw 'Managed observer extractor found an unreviewed member.' }
    }
    if (@($function.FindAll({ param($n) $n -is [Management.Automation.Language.TypeExpressionAst] }, $true)).Count -ne 0) { throw 'Managed observer extractor found an unreviewed static type.' }
    . ([scriptblock]::Create($function.Extent.Text))
}
function Update-ParentOwnedObserver { $script:updateCalls++; if ($script:updateFails) { throw 'managed update stub failure' } }
function New-ObserverStopStub {
    $value = [pscustomobject]@{ calls = 0; fail = $false }
    $value | Add-Member -MemberType ScriptMethod -Name StopAndProve -Value { $this.calls++; if ($this.fail) { throw 'managed stop stub failure' } }
    return $value
}
$contract = [pscustomobject]@{ root = 'C:\fixture'; session_id = 'a' * 32; revision = 'b' * 40; api = [pscustomobject]@{ image_id = 'sha256:' + ('c' * 64) } }
$paths = [pscustomobject]@{ observer_ready = 'C:\fixture\ready.json'; observer_output = 'C:\fixture\output.json' }
$script:observerPlan = [pscustomobject]@{ nonce = 'd' * 64; script_path = 'C:\fixture\scripts\Invoke-StratusRuntimeLoadObserver.ps1' }
Invoke-ObserverTest 'fixed private observer argv exact vector' {
    $actual = @(Get-ParentObserverArguments)
    $wanted = @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $script:observerPlan.script_path, '-Root', $contract.root,
        '-DurationSeconds', '390', '-DatabaseIntervalSeconds', '1', '-ResourceIntervalSeconds', '5', '-DrainSeconds', '5', '-ExpectedSchemaHead', '20260730_0047',
        '-MaxApiSessions', '4', '-LoadGateRunId', $contract.session_id, '-ExpectedApiImageId', $contract.api.image_id, '-ExpectedRevision', $contract.revision,
        '-ExpectedReleaseDigest', ('c' * 64), '-ReadyPath', $paths.observer_ready, '-OutputPath', $paths.observer_output, '-ParentStageNonce', ('d' * 64))
    if ($actual.Count -ne $wanted.Count -or ($actual -join "`0") -cne ($wanted -join "`0")) { throw 'Fixed argv differs.' }
}
Invoke-ObserverTest 'missing private plan rejected' { $old = $script:observerPlan; $script:observerPlan = $null; try { Assert-ObserverTestThrows { Get-ParentObserverArguments } 'private one-attempt' } finally { $script:observerPlan = $old } }
foreach ($bad in @(('D' * 64), ('d' * 63), (('d' * 64) + "`n"))) {
    Invoke-ObserverTest ('invalid private nonce length=' + $bad.Length) { $script:observerPlan.nonce = $bad; try { Assert-ObserverTestThrows { Get-ParentObserverArguments } 'private one-attempt' } finally { $script:observerPlan.nonce = 'd' * 64 } }
}
Invoke-ObserverTest 'never-created observer needs no stop' { $script:observerChild = $null; $script:observerLaunchAttempted = $false; Stop-ParentOwnedObserver }
Invoke-ObserverTest 'ambiguous failed creation cannot claim empty tree' { $script:observerChild = $null; $script:observerLaunchAttempted = $true; Assert-ObserverTestThrows { Stop-ParentOwnedObserver } 'creation remains ambiguous' }
Invoke-ObserverTest 'owned stop delegates only to retained capability' { $script:observerChild = New-ObserverStopStub; $held = $script:observerChild; Stop-ParentOwnedObserver; if ($held.calls -ne 1 -or -not [object]::ReferenceEquals($held, $script:observerChild)) { throw 'Owned capability not retained.' } }
Invoke-ObserverTest 'failed stop retains retry capability' { $script:observerChild = New-ObserverStopStub; $held = $script:observerChild; $held.fail = $true; Assert-ObserverTestThrows { Stop-ParentOwnedObserver } 'managed stop stub failure'; if (-not [object]::ReferenceEquals($held, $script:observerChild)) { throw 'Failure lost capability.' }; $held.fail = $false; Stop-ParentOwnedObserver; if ($held.calls -ne 2) { throw 'Owned stop was not retried.' } }
$script:observerChild = New-ObserverStopStub; $script:observerFrame = [pscustomobject]@{}; $script:observerCompleted = [pscustomobject]@{ receipt_sha256 = 'e' * 64 }
$observed = @{ observer_ack = [pscustomobject]@{} }; $script:updateCalls = 0; $script:updateFails = $false
Invoke-ObserverTest 'owned completion always rechecks through managed update stub' { Assert-ParentObserverCompleted; if ($script:updateCalls -ne 1) { throw 'Completion skipped refresh.' } }
foreach ($name in @('observerChild', 'observerFrame', 'observerCompleted')) {
    Invoke-ObserverTest ('completion missing ' + $name + ' rejected') { $old = Get-Variable -Name $name -Scope Script -ValueOnly; Set-Variable -Name $name -Scope Script -Value $null
        try { Assert-ObserverTestThrows { Assert-ParentObserverCompleted } 'owned observer completion' } finally { Set-Variable -Name $name -Scope Script -Value $old } }
}
Invoke-ObserverTest 'completion missing own ACK rejected' { $observed = @{}; try { Assert-ObserverTestThrows { Assert-ParentObserverCompleted } 'owned observer completion' } finally { $observed = @{ observer_ack = [pscustomobject]@{} } } }
Invoke-ObserverTest 'completion missing own publication rejected' { $script:observerCompleted.receipt_sha256 = $null; try { Assert-ObserverTestThrows { Assert-ParentObserverCompleted } 'owned observer completion' } finally { $script:observerCompleted.receipt_sha256 = 'e' * 64 } }
Invoke-ObserverTest 'completion update failure propagates' { $script:updateFails = $true; try { Assert-ObserverTestThrows { Assert-ParentObserverCompleted } 'managed update stub failure' } finally { $script:updateFails = $false } }
# Ready-schema instrumentation below compiles ONLY this explicit managed hash
# helper. It contains no file/process/native API. The actual production function
# is lexically remapped solely for its Files.Utf8/Hash calls; JSON conversion is
# a test stub, so these cases do NOT qualify the production strict parser or
# actual NativeOutputFrame provenance. Start/Update/readers remain AST-only.
Add-Type -TypeDefinition @'
namespace StratusObserverContractTests {
    public static class Bytes {
        public static readonly System.Text.UTF8Encoding Utf8 = new System.Text.UTF8Encoding(false, true);
        public static string Hash(byte[] value) {
            using (var sha = System.Security.Cryptography.SHA256.Create()) {
                return System.BitConverter.ToString(sha.ComputeHash(value)).Replace("-", "").ToLowerInvariant();
            }
        }
    }
}
'@
function ConvertFrom-ParentJson {
    param([byte[]] $Bytes)
    $argsForJson = @{ InputObject = [StratusObserverContractTests.Bytes]::Utf8.GetString($Bytes) }
    if ((Get-Command 'Microsoft.PowerShell.Utility\ConvertFrom-Json').Parameters.ContainsKey('DateKind')) { $argsForJson.DateKind = 'String' }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @argsForJson
}
foreach ($name in @('Assert-ParentProperties', 'Get-ParentUtc', 'Test-ParentInt32', 'Assert-ParentObserverReadyFrame')) {
    $function = Get-ObserverTestFunction $ast $name
    foreach ($command in $function.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] }, $true)) {
        if ($command.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or $command.GetCommandName() -cnotmatch '\A(?:Assert-ParentProperties|Get-ParentUtc|Test-ParentInt32|ConvertFrom-ParentJson|Microsoft\.PowerShell\.Utility\\ConvertTo-Json)\z') { throw 'Ready extractor found an unreviewed command.' }
    }
    foreach ($member in $function.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
        if ($member.Member -isnot [Management.Automation.Language.StringConstantExpressionAst] -or $member.Member.Value -cnotin @('GetType', 'Sort', 'ParseExact', 'ToString', 'GetBytes', 'Hash', 'Substring', 'AddSeconds', 'IsNaN', 'IsInfinity')) { throw 'Ready extractor found an unreviewed member.' }
    }
    foreach ($type in $function.FindAll({ param($n) $n -is [Management.Automation.Language.TypeExpressionAst] }, $true)) {
        if ($type.TypeName.FullName -cnotin @('Array', 'StringComparer', 'DateTimeOffset', 'Globalization.CultureInfo', 'TimeSpan', 'int', 'long', 'bool', 'string', 'decimal', 'double', 'Stratus.ParentStage.Files')) { throw 'Ready extractor found an unreviewed static type.' }
    }
    . ([scriptblock]::Create($function.Extent.Text.Replace('[Stratus.ParentStage.Files]', '[StratusObserverContractTests.Bytes]')))
}
function New-ObserverReadyFixture {
    $birth = [DateTimeOffset]::UtcNow.AddSeconds(-10).ToString('o')
    $at = [DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')
    $child = [pscustomobject]@{ ProcessId = 1234; StartedAt = $birth }
    $marker = [pscustomobject][ordered]@{
        schema_version = 1; gate = 'stratus_dashboard_runtime_observation_ready'; load_gate_run_id = $contract.session_id
        ready_at = $at; first_database_sample_at = $at; first_resource_sample_at = $at; first_dynamic_topology_sample_at = $at
        preflight_database_sample_count = 1; preflight_resource_sample_count = 1; preflight_topology_sample_count = 2
        api_image_id = $contract.api.image_id; revision = $contract.revision; release_digest = $contract.api.image_id.Substring(7)
        compose_project = 'weatherpoly'; preflight_safe = $true
        database_preflight = [pscustomobject][ordered]@{ api_sessions = 4; api_idle_in_tx = 0; global_idle_in_tx = 0; lock_waiters = 0; runtime_writer_sessions = 0 }
        resource_preflight = [pscustomobject][ordered]@{ api_memory_percent = 89.99; postgres_cpu_percent = 199.99 }
    }
    $frameValue = [pscustomobject][ordered]@{ format = 'stratus-parent-observer-ready-v1'; session_id = $contract.session_id; nonce = $script:observerPlan.nonce
        pid = $child.ProcessId; started_at = $birth; ready_path = $paths.observer_ready; ready_sha256 = 'e' * 64 }
    return [pscustomobject]@{ child = $child; marker = $marker; frame_value = $frameValue; ready_sha256 = 'e' * 64; frame = $null }
}
function Set-ObserverTestFrame {
    param([object] $Fixture, [string] $Text)
    if (-not $PSBoundParameters.ContainsKey('Text')) { $Text = (Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $Fixture.frame_value -Depth 4 -Compress) + "`n" }
    $bytes = [StratusObserverContractTests.Bytes]::Utf8.GetBytes($Text)
    $Fixture.frame = [pscustomobject]@{ Text = $Text; ByteCount = $bytes.LongLength; Sha256 = [StratusObserverContractTests.Bytes]::Hash($bytes)
        ProcessId = $Fixture.child.ProcessId; StartedAt = $Fixture.child.StartedAt }
}
function Invoke-ObserverReadyFixture {
    param([object] $Fixture)
    $script:observerChild = $Fixture.child
    Assert-ParentObserverReadyFrame $Fixture.frame ([pscustomobject]@{ sha256 = $Fixture.ready_sha256; value = $Fixture.marker })
}
Invoke-ObserverTest 'actual ready helper accepts synthetic fully bound native-type boundary marker' { $fixture = New-ObserverReadyFixture; Set-ObserverTestFrame $fixture; Invoke-ObserverReadyFixture $fixture }
Invoke-ObserverTest 'actual ready helper accepts native Int64 counters and decimal resource below boundaries' { $fixture = New-ObserverReadyFixture; $fixture.marker.schema_version = [long]1; $fixture.marker.preflight_topology_sample_count = [long]2; $fixture.marker.resource_preflight.api_memory_percent = [decimal]89.9; Set-ObserverTestFrame $fixture; Invoke-ObserverReadyFixture $fixture }
$readyNegatives = @(
    @('capture PID', { $fixture.frame.ProcessId++ }, 'native capture binding'),
    @('capture birth', { $fixture.frame.StartedAt = [DateTimeOffset]::UtcNow.ToString('o') }, 'native capture binding'),
    @('capture bytes', { $fixture.frame.ByteCount++ }, 'native capture binding'),
    @('capture hash', { $fixture.frame.Sha256 = 'f' * 64 }, 'native capture binding'),
    @('frame nonce', { $fixture.frame_value.nonce = 'f' * 64; Set-ObserverTestFrame $fixture }, 'bound to this launch'),
    @('frame session', { $fixture.frame_value.session_id = 'f' * 32; Set-ObserverTestFrame $fixture }, 'bound to this launch'),
    @('frame PID string', { $fixture.frame_value.pid = '1234'; Set-ObserverTestFrame $fixture }, 'bound to this launch'),
    @('frame birth', { $fixture.frame_value.started_at = [DateTimeOffset]::UtcNow.ToString('o'); Set-ObserverTestFrame $fixture }, 'bound to this launch'),
    @('frame format', { $fixture.frame_value.format = 'wrong'; Set-ObserverTestFrame $fixture }, 'bound to this launch'),
    @('frame path', { $fixture.frame_value.ready_path = 'C:\other.json'; Set-ObserverTestFrame $fixture }, 'bound to this launch'),
    @('held ready hash', { $fixture.ready_sha256 = 'f' * 64 }, 'bound to this launch'),
    @('frame nonce native bool', { $fixture.frame_value.nonce = $false; Set-ObserverTestFrame $fixture }, 'native strings'),
    @('frame extra field', { $fixture.frame_value | Add-Member unexpected 1; Set-ObserverTestFrame $fixture }, 'property set differs'),
    @('frame CRLF', { Set-ObserverTestFrame $fixture ($fixture.frame.Text.Replace("`n", "`r`n")) }, 'exact compact LF frame'),
    @('frame space', { Set-ObserverTestFrame $fixture (' ' + $fixture.frame.Text) }, 'exact compact LF frame'),
    @('marker extra field', { $fixture.marker | Add-Member unexpected 1 }, 'property set differs'),
    @('marker missing field', { $fixture.marker.PSObject.Properties.Remove('revision') }, 'property set differs'),
    @('marker schema type', { $fixture.marker.schema_version = '1' }, 'native integers'),
    @('marker schema value', { $fixture.marker.schema_version = 2 }, 'identity or preflight'),
    @('marker revision type', { $fixture.marker.revision = 1 }, 'native strings'),
    @('marker image', { $fixture.marker.api_image_id = 'sha256:' + ('f' * 64) }, 'identity or preflight'),
    @('marker session', { $fixture.marker.load_gate_run_id = 'f' * 32 }, 'identity or preflight'),
    @('marker revision', { $fixture.marker.revision = 'f' * 40 }, 'identity or preflight'),
    @('marker release', { $fixture.marker.release_digest = 'f' * 64 }, 'identity or preflight'),
    @('marker project', { $fixture.marker.compose_project = 'other' }, 'identity or preflight'),
    @('marker safe false', { $fixture.marker.preflight_safe = $false }, 'identity or preflight'),
    @('marker safe string', { $fixture.marker.preflight_safe = 'true' }, 'identity or preflight'),
    @('marker DB samples zero', { $fixture.marker.preflight_database_sample_count = 0 }, 'identity or preflight'),
    @('marker resource samples zero', { $fixture.marker.preflight_resource_sample_count = 0 }, 'identity or preflight'),
    @('marker topology samples one', { $fixture.marker.preflight_topology_sample_count = 1 }, 'identity or preflight'),
    @('marker sample fractional count', { $fixture.marker.preflight_topology_sample_count = 2.1 }, 'native integers'),
    @('marker sample overflow count', { $fixture.marker.preflight_topology_sample_count = [long]2147483648 }, 'native integers'),
    @('ready before birth', { $fixture.marker.ready_at = ([DateTimeOffset]::Parse($fixture.child.StartedAt)).AddSeconds(-1).ToString('o') }, 'time differs'),
    @('ready future', { $fixture.marker.ready_at = [DateTimeOffset]::UtcNow.AddSeconds(60).ToString('o') }, 'time differs'),
    @('ready noncanonical', { $fixture.marker.ready_at = '2026-09-14T00:00:00Z' }, 'String was not recognized|valid DateTime|DateTime|canonical'),
    @('sample before birth', { $fixture.marker.first_database_sample_at = ([DateTimeOffset]::Parse($fixture.child.StartedAt)).AddSeconds(-1).ToString('o') }, 'outside its captured readiness interval'),
    @('sample after ready', { $fixture.marker.first_resource_sample_at = [DateTimeOffset]::UtcNow.AddSeconds(60).ToString('o') }, 'outside its captured readiness interval'),
    @('topology sample before birth', { $fixture.marker.first_dynamic_topology_sample_at = ([DateTimeOffset]::Parse($fixture.child.StartedAt)).AddSeconds(-1).ToString('o') }, 'outside its captured readiness interval'),
    @('database extra field', { $fixture.marker.database_preflight | Add-Member unexpected 0 }, 'property set differs'),
    @('database missing field', { $fixture.marker.database_preflight.PSObject.Properties.Remove('lock_waiters') }, 'property set differs'),
    @('database sessions threshold', { $fixture.marker.database_preflight.api_sessions = 5 }, 'writer/contention-free'),
    @('database sessions negative', { $fixture.marker.database_preflight.api_sessions = -1 }, 'writer/contention-free'),
    @('database sessions string', { $fixture.marker.database_preflight.api_sessions = '4' }, 'writer/contention-free'),
    @('database idle API', { $fixture.marker.database_preflight.api_idle_in_tx = 1 }, 'writer/contention-free'),
    @('database idle global', { $fixture.marker.database_preflight.global_idle_in_tx = 1 }, 'writer/contention-free'),
    @('database waiter', { $fixture.marker.database_preflight.lock_waiters = 1 }, 'writer/contention-free'),
    @('database writer', { $fixture.marker.database_preflight.runtime_writer_sessions = 1 }, 'writer/contention-free'),
    @('resource extra field', { $fixture.marker.resource_preflight | Add-Member unexpected 0 }, 'property set differs'),
    @('resource missing field', { $fixture.marker.resource_preflight.PSObject.Properties.Remove('postgres_cpu_percent') }, 'property set differs'),
    @('resource memory boundary', { $fixture.marker.resource_preflight.api_memory_percent = 90 }, 'fixed boundary'),
    @('resource CPU boundary', { $fixture.marker.resource_preflight.postgres_cpu_percent = 200 }, 'fixed boundary'),
    @('resource negative', { $fixture.marker.resource_preflight.postgres_cpu_percent = -0.1 }, 'fixed boundary'),
    @('resource string', { $fixture.marker.resource_preflight.api_memory_percent = '1' }, 'native JSON numbers'),
    @('resource bool', { $fixture.marker.resource_preflight.api_memory_percent = $true }, 'native JSON numbers'),
    @('resource NaN', { $fixture.marker.resource_preflight.api_memory_percent = [double]::NaN }, 'fixed boundary'),
    @('resource infinity', { $fixture.marker.resource_preflight.postgres_cpu_percent = [double]::PositiveInfinity }, 'fixed boundary'))
foreach ($row in $readyNegatives) {
    Invoke-ObserverTest ('ready rejection: ' + $row[0]) { $fixture = New-ObserverReadyFixture; Set-ObserverTestFrame $fixture; & $row[1]; Assert-ObserverTestThrows { Invoke-ObserverReadyFixture $fixture } $row[2] }
}
# Actual ACK-boundary function with a managed method stub. This does not run
# Write-ParentAck or any publication/I/O; its three use sites are checked above.
$function = Get-ObserverTestFunction $ast 'Assert-ParentObserverAckBoundary'
if (@($function.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] }, $true)).Count -ne 0 -or
    @(Get-ObserverTestMember $function 'AssertRunningOwned').Count -ne 1 -or
    @($function.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] }, $true)).Count -ne 1 -or
    @($function.FindAll({ param($n) $n -is [Management.Automation.Language.TypeExpressionAst] }, $true)).Count -ne 0) { throw 'ACK helper extraction exceeds managed-only scope.' }
. ([scriptblock]::Create($function.Extent.Text))
function Reset-ObserverAckStub {
    $script:observerPlan = [pscustomobject]@{ clock = [pscustomobject]@{ ElapsedMilliseconds = 29999 } }
    $script:observerFrame = [pscustomobject]@{}; $script:observerChildResumed = $true
    $script:observerChild = [pscustomobject]@{ calls = 0; fail = $false; delayed = $false }
    $script:observerChild | Add-Member -MemberType ScriptMethod -Name AssertRunningOwned -Value {
        param([int] $Limit)
        $this.calls++; if ($Limit -ne 450000) { throw 'Wrong owned lifetime.' }
        if ($this.fail) { throw 'managed live capability failure' }
        if ($this.delayed) { $script:observerPlan.clock.ElapsedMilliseconds = 30000 }
    }
}
Invoke-ObserverTest 'actual ACK boundary uses retained live capability and original deadline' { Reset-ObserverAckStub; Assert-ParentObserverAckBoundary; if ($script:observerChild.calls -ne 1) { throw 'ACK bypassed retained child.' } }
foreach ($field in @('observerChild', 'observerPlan', 'observerFrame')) {
    Invoke-ObserverTest ('ACK missing ' + $field + ' rejected') { Reset-ObserverAckStub; Set-Variable -Name $field -Scope Script -Value $null; Assert-ObserverTestThrows { Assert-ParentObserverAckBoundary } 'live readiness capability' }
}
Invoke-ObserverTest 'ACK requires own resume' { Reset-ObserverAckStub; $script:observerChildResumed = $false; Assert-ObserverTestThrows { Assert-ParentObserverAckBoundary } 'live readiness capability' }
foreach ($elapsed in @(30000, 30001)) {
    Invoke-ObserverTest ('ACK original deadline rejects elapsed=' + $elapsed) { Reset-ObserverAckStub; $script:observerPlan.clock.ElapsedMilliseconds = $elapsed; Assert-ObserverTestThrows { Assert-ParentObserverAckBoundary } 'original readiness deadline' }
}
Invoke-ObserverTest 'ACK native capability failure propagates' { Reset-ObserverAckStub; $script:observerChild.fail = $true; Assert-ObserverTestThrows { Assert-ParentObserverAckBoundary } 'managed live capability failure' }
Invoke-ObserverTest 'ACK deadline rechecked after simulated live query delay' { Reset-ObserverAckStub; $script:observerChild.delayed = $true; Assert-ObserverTestThrows { Assert-ParentObserverAckBoundary } 'original readiness deadline' }
$expectedCases = 115
if ($results.Count -ne $expectedCases) { throw "Fixed observer contract case count differs: $($results.Count)" }
[pscustomobject]@{ suite = 'parent-owned-observer-ast-managed'; expected = $expectedCases; executed = $results.Count; passed = $results.Count; failed = 0
    watchdog_sha256 = (Get-FileHash -LiteralPath $WatchdogPath -Algorithm SHA256).Hash.ToLowerInvariant()
    native_executed = $false; watchdog_body_executed = $false; observer_body_executed = $false; docker_executed = $false
    ready_helper_hash_type_remapped = $true; ready_json_parser_stubbed = $true; strict_parser_qualified = $false
    full_protocol_accepted = $false; results = @($results.ToArray()) } | ConvertTo-Json -Depth 5 -Compress
