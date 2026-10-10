[CmdletBinding()]
param([string] $WatchdogPath, [string] $GatePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $WatchdogPath) { $WatchdogPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'Invoke-StratusParentStageWatchdog.ps1' }
if (-not $GatePath) { $GatePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'Assert-StratusPostMigrationSuccessorGate.ps1' }
# Only AST inspection and five scoped pure helpers. The Files UTF8/hash type is
# explicitly remapped to the managed test helper below. No script body, launch,
# reader, guard, Docker, native lifecycle, historical harness or filesystem
# fixture is executed. These cases cannot qualify native provenance/containment.
$source = Get-Content -LiteralPath $WatchdogPath -Raw
$gateSource = Get-Content -LiteralPath $GatePath -Raw
function Read-LaunchTestAst {
    param([string] $Text)
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)
    if (@($errors).Count -ne 0) { throw 'Launch test source AST parse failed.' }
    return $ast
}
function Get-LaunchTestFunction {
    param([object] $Ast, [string] $FunctionName)
    $found = @($Ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $FunctionName }, $true))
    if ($found.Count -ne 1) { throw ('Exact launch function missing: ' + $FunctionName) }
    return $found[0]
}
function Get-LaunchTestCommands {
    param([object] $Ast, [string] $CommandName)
    return @($Ast.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq $CommandName }, $true))
}
function Get-LaunchTestMembers {
    param([object] $Ast, [string] $MemberName)
    return @($Ast.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
        $n.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and $n.Member.Value -ceq $MemberName }, $true))
}
$results = [Collections.Generic.List[object]]::new()
function Invoke-LaunchTest {
    param([string] $CaseName, [scriptblock] $Body)
    try { & $Body } catch { throw "Launch case $CaseName failed: $($_.Exception.Message)" }
    $results.Add([pscustomobject]@{name=$CaseName; passed=$true})
}
function Assert-LaunchTestRejected {
    param([scriptblock] $Body, [string] $Pattern)
    $failure = $null; try { & $Body | Out-Null } catch { $failure = $_.Exception.Message }
    if ($null -eq $failure -or $failure -notmatch $Pattern) { throw "Expected rejection $Pattern; observed $failure" }
}
$ast = Read-LaunchTestAst $source
$gateAst = Read-LaunchTestAst $gateSource
$artifacts = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$artifactNames' }, $true))
if ($artifacts.Count -ne 1 -or @(Get-LaunchTestCommands $artifacts[0] '').Count -ne 0) { throw 'Artifact inventory AST differs.' }
$artifactNames = @($artifacts[0].Right.FindAll({ param($n) $n -is [Management.Automation.Language.StringConstantExpressionAst] }, $true) | ForEach-Object { $_.Value })
if ($artifactNames.Count -ne 44 -or @($artifactNames | Select-Object -Unique).Count -ne 44) { throw 'Exact 44-artifact inventory differs.' }
Add-Type -TypeDefinition @'
namespace StratusTransitionLaunchTest {
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
foreach ($functionName in @('Assert-ParentProperties','Get-ParentUtc','Test-ParentInt32','Get-ParentTransitionLaunchFamily','Get-ParentTransitionLaunchPlan')) {
    $fn = Get-LaunchTestFunction $ast $functionName
    foreach ($command in $fn.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] }, $true)) {
        if ($command.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or
            $command.GetCommandName() -cnotmatch '\A(?:Assert-ParentProperties|Get-ParentUtc|Test-ParentInt32|Get-ParentTransitionLaunchFamily|Join-Path|ForEach-Object|Microsoft\.PowerShell\.Utility\\ConvertTo-Json)\z') {
            throw ('Unreviewed launch helper command: ' + $command.Extent.Text)
        }
    }
    foreach ($member in $fn.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
        if ($member.Member -isnot [Management.Automation.Language.StringConstantExpressionAst] -or
            $member.Member.Value -cnotin @('new','GetType','Sort','ParseExact','ToString','EndsWith','Substring','Add','GetFullPath','StartsWith','GetBytes','Hash','AddSeconds','ContainsKey','Replace')) {
            throw ('Unreviewed launch helper member: ' + $member.Extent.Text)
        }
    }
    foreach ($type in $fn.FindAll({ param($n) $n -is [Management.Automation.Language.TypeExpressionAst] }, $true)) {
        if ($type.TypeName.FullName -cnotin @('string','bool','int','long','Array','StringComparison','StringComparer','IO.Path',
            'Collections.Generic.HashSet[string]','Collections.Generic.Dictionary[string,string]','Globalization.CultureInfo','DateTimeOffset','TimeSpan','Stratus.ParentStage.Files')) {
            throw ('Unreviewed launch helper type: ' + $type.TypeName.FullName)
        }
    }
    . ([scriptblock]::Create($fn.Extent.Text.Replace('[Stratus.ParentStage.Files]', '[StratusTransitionLaunchTest.Bytes]')))
}
function Reset-LaunchTestContext {
    $script:now = [DateTimeOffset]::ParseExact('2026-09-15T00:00:00.0000000+00:00','o',[Globalization.CultureInfo]::InvariantCulture)
    $script:leaseTime = $script:now.AddSeconds(-60); $script:deadline = $script:now.AddSeconds(900)
    $script:ContractPath = 'C:\stratus\contract.json'
    $script:approvedPostgresId = '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
    $script:contract = [pscustomobject]@{ root='C:\stratus'; session_id=('a'*32); revision=('b'*40); load_acceptance_path='C:\stratus\load.json'
        owner=[pscustomobject]@{pid=1234;start_time_utc=$script:now.AddSeconds(-120).ToString('o')}
        watchdog=[pscustomobject]@{host_path='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe';host_sha256=('c'*64)}
        api=[pscustomobject]@{image_id=('sha256:'+('d'*64));config_image='stratus:candidate-fixture'} }
    $pathMap = [ordered]@{}; foreach ($key in $artifactNames) { $pathMap[$key]='C:\stratus\protocol\'+$key+'.json' }
    $prefix='C:\stratus\stage\prestart.json'
    $pathMap.transition_terminal=$prefix+'.watchdog-terminal.json'
    $pathMap.transition_start_fence=$prefix+'.watchdog-start-fence.lock'
    $pathMap.transition_start_revoke=$prefix+'.watchdog-start-revoked.json'
    $pathMap.transition_start_attempt=$prefix+'.watchdog-start-attempt.json'
    $pathMap.transition_start_completion=$prefix+'.watchdog-start-complete.json'
    $pathMap.parent_authorization=$prefix+'.parent-authorization.json'
    $script:paths=[pscustomobject]$pathMap
    $script:r1Proof=[pscustomobject]@{source=@{'scripts/Invoke-StratusSuccessorTransitionWatchdog.ps1'=[pscustomobject]@{sha256=('e'*64);bytes=10000}}}
}
function New-LaunchTestFixture {
    Reset-LaunchTestContext
    $family=Get-ParentTransitionLaunchFamily
    $accepted=$script:now.AddSeconds(-10); $hard=$accepted.AddSeconds(300)
    $handoff=[ordered]@{format='stratus-successor-transition-watchdog-handoff-v3';session_id=('f'*32);owner_pid=1234
        owner_start_time_utc=$contract.owner.start_time_utc;hard_deadline_utc=$hard.ToString('o');heartbeat_timeout_seconds=15
        lease_path=$family.lease;receipt_path=$family.terminal;heartbeat_path=$family.heartbeat;commit_path=$family.commit
        live_path=$family.live;prestart_path=$family.prestart;start_fence_path=$family.fence;start_fence_sha256=('1'*64)
        start_revoke_path=$family.revoke;start_attempt_path=$family.attempt;start_completion_path=$family.completion}
    $program=[pscustomobject]@{script_path='C:\stratus\scripts\Invoke-StratusSuccessorTransitionWatchdog.ps1';script_sha256=('e'*64);script_bytes=10000
        host_path=$contract.watchdog.host_path;host_sha256=$contract.watchdog.host_sha256;host_bytes=20000
        owner_pid=1234;owner_start_time_utc=$contract.owner.start_time_utc;read_locks_held=$true}
    # Selected launch fields only, not full causal PreStart acceptance. Missing
    # historical/repair/topology semantics deliberately remain outside this fixture.
    $payload=[pscustomobject]@{accepted_at=$accepted.ToString('o')
        validity=[pscustomobject]@{max_age_seconds=600;postgres_start_window_seconds=300;expires_at=$accepted.AddSeconds(600).ToString('o');postgres_start_deadline=$hard.ToString('o')}
        successor=[pscustomobject]@{revision=$contract.revision;image=$contract.api.config_image;image_id=$contract.api.image_id}
        transition_session=[pscustomobject]@{mode='single-process';session_id=('f'*32);watchdog_handoff=[pscustomobject]$handoff;watchdog_program=$program}}
    $prestart=[pscustomobject]@{path=$family.prestart;sha256=('2'*64);value=[pscustomobject]@{format='stratus-post-migration-successor-transition-prestart-v2';accepted=$true;payload=$payload;payload_sha256=$null}}
    $fence=[pscustomobject]@{path=$family.fence;sha256=('1'*64);value=[pscustomobject]@{format='stratus-successor-transition-start-fence-v1';session_id=('f'*32);owner_pid=1234;owner_start_time_utc=$contract.owner.start_time_utc;created_at=$accepted.AddSeconds(-1).ToString('o')}}
    $intent=[pscustomobject]@{path=$paths.transition_launch_intent;sha256=('3'*64);value=[pscustomobject]@{transition_session_id=('f'*32);prestart_sha256=('2'*64);prestart_payload_sha256=$null;start_fence_sha256=('1'*64);hard_deadline_utc=$hard.ToString('o')}}
    $fixture=[pscustomobject]@{prestart=$prestart;fence=$fence;intent=$intent;now=$script:now}
    Set-LaunchTestPayloadHash $fixture
    return $fixture
}
function Set-LaunchTestPayloadHash {
    param([object] $Fixture)
    $text=Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $Fixture.prestart.value.payload -Depth 20 -Compress
    $hash=[StratusTransitionLaunchTest.Bytes]::Hash([StratusTransitionLaunchTest.Bytes]::Utf8.GetBytes($text))
    $Fixture.prestart.value.payload_sha256=$hash; $Fixture.intent.value.prestart_payload_sha256=$hash
}
function Invoke-LaunchTestFixture { param([object] $Fixture) return Get-ParentTransitionLaunchPlan $Fixture.intent $Fixture.prestart $Fixture.fence $Fixture.now }
Invoke-LaunchTest 'scoped exact family and nineteen-key private plan projection' {
    $fixture=New-LaunchTestFixture; $plan=Invoke-LaunchTestFixture $fixture
    $wanted=@('Root','SessionId','OwnerPid','OwnerStartTimeUtc','DeadlineUtc','HeartbeatTimeoutSeconds','LeasePath','ReceiptPath','HeartbeatPath','CommitPath','LiveAcceptancePath','PreStartAttestationPath','ExpectedPreStartSha256','StartFencePath','ExpectedStartFenceSha256','StartRevokePath','StartAttemptPath','StartCompletionPath','PostgresContainer')
    if ($plan.arguments.Count -ne 19 -or @($wanted | Where-Object {-not $plan.arguments.ContainsKey($_)}).Count -ne 0 -or
        $plan.arguments['OwnerPid'] -cne '1234' -or $plan.arguments['HeartbeatTimeoutSeconds'] -cne '15' -or
        $plan.arguments['SessionId'] -cne ('f'*32) -or $plan.arguments['LiveAcceptancePath'] -cne $paths.transition_live -or
        $plan.arguments['PostgresContainer'] -cne $approvedPostgresId -or $plan.arguments.ContainsKey('ParentStageNonce')) { throw 'Actual private plan differs.' }
}
$negativeCases=@(
    @('terminal suffix',{ $paths.transition_terminal='C:\stratus\wrong.json' },'fixed artifact family'),
    @('fence family',{ $paths.transition_start_fence='C:\stratus\wrong.lock' },'family differs'),
    @('protocol alias',{ $paths.transition_launch_ack=$paths.transition_capture_complete },'aliased'),
    @('Live outside root',{ $paths.transition_live='C:\other\live.json' },'outside root'),
    @('derived lease alias',{ $paths.transition_launch_ack=$fixture.prestart.path+'.watchdog-lease.json' },'Derived early Transition artifact aliases'),
    @('intent session malformed',{ $fixture.intent.value.transition_session_id='F'*32 },'fixed exact source documents'),
    @('intent digest malformed',{ $fixture.intent.value.prestart_sha256='2'*63 },'digest is malformed'),
    @('intent native type',{ $fixture.intent.value.prestart_sha256=$true },'native strings'),
    @('PreStart path',{ $fixture.prestart.path='C:\stratus\other.json' },'fixed exact source documents'),
    @('PreStart raw hash',{ $fixture.prestart.sha256='4'*64 },'fixed exact source documents'),
    @('fence raw hash',{ $fixture.fence.sha256='4'*64 },'fixed exact source documents'),
    @('envelope extra field',{ $fixture.prestart.value | Add-Member unexpected 1 },'property set differs'),
    @('envelope format',{ $fixture.prestart.value.format='wrong' },'envelope differs'),
    @('envelope accepted false',{ $fixture.prestart.value.accepted=$false },'envelope differs'),
    @('envelope accepted string',{ $fixture.prestart.value.accepted='true' },'envelope differs'),
    @('payload hash',{ $fixture.prestart.value.payload_sha256='4'*64; $fixture.intent.value.prestart_payload_sha256='4'*64 },'payload digest differs'),
    @('handoff extra field',{ $fixture.prestart.value.payload.transition_session.watchdog_handoff | Add-Member unexpected 1; Set-LaunchTestPayloadHash $fixture },'property set differs'),
    @('handoff integer string',{ $fixture.prestart.value.payload.transition_session.watchdog_handoff.owner_pid='1234'; Set-LaunchTestPayloadHash $fixture },'handoff integer differs'),
    @('handoff wrong owner',{ $fixture.prestart.value.payload.transition_session.watchdog_handoff.owner_pid=5678; Set-LaunchTestPayloadHash $fixture },'session, owner or original bounded deadline'),
    @('handoff wrong birth',{ $fixture.prestart.value.payload.transition_session.watchdog_handoff.owner_start_time_utc=$script:now.ToString('o'); Set-LaunchTestPayloadHash $fixture },'session, owner or original bounded deadline'),
    @('handoff timeout',{ $fixture.prestart.value.payload.transition_session.watchdog_handoff.heartbeat_timeout_seconds=16; Set-LaunchTestPayloadHash $fixture },'session, owner or original bounded deadline'),
    @('handoff Live path',{ $fixture.prestart.value.payload.transition_session.watchdog_handoff.live_path='C:\stratus\other.json'; Set-LaunchTestPayloadHash $fixture },'handoff path differs'),
    @('handoff session',{ $fixture.prestart.value.payload.transition_session.watchdog_handoff.session_id='4'*32; Set-LaunchTestPayloadHash $fixture },'session, owner or original bounded deadline'),
    @('mode',{ $fixture.prestart.value.payload.transition_session.mode='detached'; Set-LaunchTestPayloadHash $fixture },'session, owner or original bounded deadline'),
    @('future accepted',{ $fixture.prestart.value.payload.accepted_at=$script:now.AddSeconds(3).ToString('o'); $fixture.prestart.value.payload.validity.expires_at=$script:now.AddSeconds(603).ToString('o'); Set-LaunchTestPayloadHash $fixture },'session, owner or original bounded deadline'),
    @('old accepted',{ $fixture.prestart.value.payload.accepted_at=$script:leaseTime.AddSeconds(-1).ToString('o'); $fixture.prestart.value.payload.validity.expires_at=$script:leaseTime.AddSeconds(599).ToString('o'); Set-LaunchTestPayloadHash $fixture },'session, owner or original bounded deadline'),
    @('remaining lifetime',{ $fixture.now=$script:now.AddSeconds(260) },'session, owner or original bounded deadline'),
    @('fence created after acceptance',{ $fixture.fence.value.created_at=$script:now.ToString('o') },'fence marker identity differs'),
    @('fence wrong owner',{ $fixture.fence.value.owner_pid=5678 },'fence marker identity differs'),
    @('program source hash',{ $fixture.prestart.value.payload.transition_session.watchdog_program.script_sha256='4'*64; Set-LaunchTestPayloadHash $fixture },'source, host, owner or candidate differs'),
    @('program source path',{ $fixture.prestart.value.payload.transition_session.watchdog_program.script_path='C:\stratus\scripts\other.ps1'; Set-LaunchTestPayloadHash $fixture },'source, host, owner or candidate differs'),
    @('program source bytes',{ $fixture.prestart.value.payload.transition_session.watchdog_program.script_bytes=10001; Set-LaunchTestPayloadHash $fixture },'source, host, owner or candidate differs'),
    @('program host hash',{ $fixture.prestart.value.payload.transition_session.watchdog_program.host_sha256='4'*64; Set-LaunchTestPayloadHash $fixture },'source, host, owner or candidate differs'),
    @('program claimed read locks',{ $fixture.prestart.value.payload.transition_session.watchdog_program.read_locks_held=$false; Set-LaunchTestPayloadHash $fixture },'source, host, owner or candidate differs'),
    @('candidate image ID',{ $fixture.prestart.value.payload.successor.image_id='sha256:'+('4'*64); Set-LaunchTestPayloadHash $fixture },'source, host, owner or candidate differs'),
    @('candidate revision',{ $fixture.prestart.value.payload.successor.revision='4'*40; Set-LaunchTestPayloadHash $fixture },'source, host, owner or candidate differs'),
    @('missing independent R1 source',{ $script:r1Proof.source.Clear() },'R1 omits Transition source'),
    @('program byte count string',{ $fixture.prestart.value.payload.transition_session.watchdog_program.script_bytes='10000'; Set-LaunchTestPayloadHash $fixture },'program byte count is invalid'),
    @('host byte count string',{ $fixture.prestart.value.payload.transition_session.watchdog_program.host_bytes='20000'; Set-LaunchTestPayloadHash $fixture },'program byte count is invalid'),
    @('host byte count zero',{ $fixture.prestart.value.payload.transition_session.watchdog_program.host_bytes=0; Set-LaunchTestPayloadHash $fixture },'program byte count is invalid'),
    @('validity max age string',{ $fixture.prestart.value.payload.validity.max_age_seconds='600'; Set-LaunchTestPayloadHash $fixture },'validity types or expiry differ'),
    @('validity start window string',{ $fixture.prestart.value.payload.validity.postgres_start_window_seconds='300'; Set-LaunchTestPayloadHash $fixture },'validity types or expiry differ'),
    @('validity wrong expiry',{ $fixture.prestart.value.payload.validity.expires_at=$script:now.AddSeconds(591).ToString('o'); Set-LaunchTestPayloadHash $fixture },'validity types or expiry differ'))
foreach ($row in $negativeCases) {
    Invoke-LaunchTest ('scoped launch rejection: '+$row[0]) { $fixture=New-LaunchTestFixture; & $row[1]; Assert-LaunchTestRejected { Invoke-LaunchTestFixture $fixture } $row[2] }
}
function Assert-LaunchTestComparison {
    param([object] $Node,[string] $Expression,[string] $Label)
    if (@($Node.FindAll({param($n) $n -is [Management.Automation.Language.BinaryExpressionAst] -and $n.Extent.Text -ceq $Expression},$true)).Count -ne 1) { throw ('Required launch comparison differs: '+$Label) }
}
function Assert-LaunchTestWiring {
    param([string] $WatchdogText,[string] $GateText)
    $wa=Read-LaunchTestAst $WatchdogText; $ga=Read-LaunchTestAst $GateText
    foreach ($tree in @($wa,$ga)) {
        if (@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.ThrowStatementAst]}).Count -ne 1) { throw 'Unconditional entry barrier differs.' }
    }
    $start=Get-LaunchTestFunction $wa 'Start-ParentTransitionLaunch'
    $prepare=@(Get-LaunchTestMembers $start 'Prepare'); $create=@(Get-LaunchTestMembers $start 'StartSuspended')
    $resume=@(Get-LaunchTestMembers $start 'Resume'); $suspend=@(Get-LaunchTestMembers $start 'AssertSuspended')
    if ($prepare.Count -ne 1 -or $create.Count -ne 1 -or $resume.Count -ne 1 -or $suspend.Count -ne 1 -or -not $prepare[0].Static -or
        $prepare[0].Expression.Extent.Text -cne '[Stratus.ParentStage.IndependentTransitionLaunch]' -or $prepare[0].Arguments.Count -ne 10 -or
        $create[0].Static -or $create[0].Expression.Extent.Text -cne '$script:transitionLaunch' -or
        ($null -ne $create[0].Arguments -and $create[0].Arguments.Count -ne 0)) { throw 'Independent launch capability shape differs.' }
    $retention=@($start.Body.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
        $_.Left.Extent.Text -ceq '$script:transitionLaunch' -and @(Get-LaunchTestMembers $_ 'Prepare').Count -eq 1 })
    if ($retention.Count -ne 1 -or $retention[0].Extent.EndOffset -ge $create[0].Extent.StartOffset) { throw 'Independent launch capability shape lost retained-before-native ownership.' }
    foreach ($name in @('script:transitionLaunchAttempted','script:transitionLaunchResumed')) {
        $latch=@($start.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and
            $n.Left -is [Management.Automation.Language.VariableExpressionAst] -and $n.Left.VariablePath.UserPath -ceq $name -and $n.Right.Extent.Text -ceq '$true'},$true))
        if ($latch.Count -ne 1) { throw 'One-shot attempt latch differs.' }
        $later=if ($name -ceq 'script:transitionLaunchAttempted') {$create[0]} else {$resume[0]}
        if ($latch[0].Extent.StartOffset -ge $later.Extent.StartOffset) { throw 'Attempt latch follows native operation.' }
    }
    if ($create[0].Extent.StartOffset -ge $suspend[0].Extent.StartOffset -or $suspend[0].Extent.StartOffset -ge $resume[0].Extent.StartOffset) { throw 'Suspended-before-resume order differs.' }
    foreach ($name in @('Assert-ParentHeartbeat','Read-ParentHeldBytes','Get-ParentTransitionLaunchPlan','Assert-ParentBootstrapHeldFile','Assert-ParentEngine','Get-ParentContainer','Assert-ParentTopology')) {
        if (@(Get-LaunchTestCommands $start $name).Count -lt 1) { throw ('Launch prerequisite missing: '+$name) }
    }
    $fence=@(Get-LaunchTestCommands $start 'Read-ParentDocument')
    if ($fence.Count -ne 1 -or $fence[0].Extent.Text -cne 'Read-ParentDocument $family.fence 8192') { throw 'Early fence transient bounded read differs.' }
    foreach ($name in @('Initialize-ParentTransitionEvidenceFence','Open-ParentFenceOnce','Open-ExclusivePostgresStartFence')) {
        if (@(Get-LaunchTestCommands $start $name).Count -ne 0) { throw 'Early launch acquires a terminal/start fence.' }
    }
    foreach ($name in @('GetProcessById','Kill','Dispose','WaitForExit')) {
        if (@(Get-LaunchTestMembers $start $name).Count -ne 0) { throw 'Early launch uses a substituted process lifetime.' }
    }
    $request=Get-LaunchTestFunction $ga 'Request-ParentStageTransitionLaunch'
    if ($request.Body.EndBlock.Statements[-1] -isnot [Management.Automation.Language.ThrowStatementAst] -or
        $request.Body.EndBlock.Statements[-1].Extent.Text -notmatch 'fresh-live/terminal/containment integration is WIP') { throw 'Gate legacy ABI cut barrier differs.' }
    if (@(Get-LaunchTestCommands $ga 'Start-TransitionWatchdogProcess').Count -ne 0 -or
        @(Get-LaunchTestCommands $ga 'Request-ParentStageTransitionLaunch').Count -ne 1) { throw 'Gate legacy launch fallback or own request differs.' }
    $call=@(Get-LaunchTestCommands $ga 'Request-ParentStageTransitionLaunch')[0]
    $wait=@(Get-LaunchTestCommands $ga 'Wait-TransitionWatchdogLease')
    if ($wait.Count -ne 1 -or $call.Extent.EndOffset -ge $wait[0].Extent.StartOffset) { throw 'Early request no longer cuts before legacy lease path.' }
    if (@($request.FindAll({param($n) $n -is [Management.Automation.Language.BinaryExpressionAst] -and $n.Extent.Text -ceq '$wait.ElapsedMilliseconds -ge 30000'},$true)).Count -ne 2) { throw 'Gate original ACK wait/readback deadline checks differ.' }
    $publication=@(Get-LaunchTestMembers $request 'PublishNew')
    $requestLatch=@($request.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and
        $n.Left.Extent.Text -ceq '$state.transition_launch_requested' -and $n.Right.Extent.Text -ceq '$true'},$true))
    if ($publication.Count -ne 1 -or $requestLatch.Count -ne 1 -or $requestLatch[0].Extent.EndOffset -ge $publication[0].Extent.StartOffset) { throw 'Gate intent publication attempt is not one-shot.' }
    $update=Get-LaunchTestFunction $wa 'Update-ParentTransitionLaunch'
    if (@(Get-LaunchTestMembers $update 'PollReady').Count -ne 1 -or @(Get-LaunchTestMembers $update 'PollTerminalExit').Count -ne 1) { throw 'Actual owned capture polling differs.' }
    foreach ($pair in @(@('$terminal.ExitCode -ne 0','exit0'),@('$terminal.StderrBytes -ne 0','empty stderr'),
        @('$receiptBytes.LongLength -ne $terminal.ReceiptBytes','captured receipt length'),
        @("`$receipt.format -cne 'stratus-successor-transition-watchdog-terminal-v1'",'actual producer terminal v1'))) {
        Assert-LaunchTestComparison $update $pair[0] $pair[1]
    }
    $reads=@(Get-LaunchTestCommands $update 'Read-ParentHeldBytes')
    if ($reads.Count -ne 2 -or $reads[0].Extent.Text -cne 'Read-ParentHeldBytes $ready.LeasePath $ready.LeaseSha256 262144' -or
        $reads[1].Extent.Text -cne 'Read-ParentHeldBytes $terminal.ReceiptPath $terminal.ReceiptSha256 262144') { throw 'Actual held ready/terminal capture digest joins differ.' }
    $pending=@($update.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -ceq '$null -eq $terminal'},$true))
    if ($pending.Count -ne 1 -or $pending[0].Clauses[0].Item2.Statements.Count -ne 1 -or
        $pending[0].Clauses[0].Item2.Statements[0] -isnot [Management.Automation.Language.ReturnStatementAst]) { throw 'Pending terminal tail is incorrectly treated as sole-READY liveness.' }
    if ($update.Extent.Text.Contains('validatedCompletions') -or @(Get-LaunchTestMembers $update 'Dispose').Count -ne 0 -or
        @(Get-LaunchTestMembers $update 'Kill').Count -ne 0) { throw 'Capture projection grants cache/lifetime authority.' }
    $ack=Get-LaunchTestFunction $wa 'Write-ParentTransitionLaunchAck'
    $live=@(Get-LaunchTestCommands $ack 'Assert-ParentTransitionLaunchReady'); $publish=@(Get-LaunchTestCommands $ack 'Write-ParentRecoveryDocument')
    $read=@(Get-LaunchTestCommands $ack 'Read-ParentDocument'); $heartbeats=@(Get-LaunchTestCommands $ack 'Assert-ParentHeartbeat')
    $cache=@($ack.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$observed.transition_launch_ack'},$true))
    if ($live.Count -ne 2 -or $publish.Count -ne 1 -or $read.Count -ne 1 -or $heartbeats.Count -ne 2 -or $cache.Count -ne 1 -or
        $live[0].Extent.EndOffset -ge $publish[0].Extent.StartOffset -or $live[1].Extent.StartOffset -le $read[0].Extent.EndOffset -or
        $live[1].Extent.StartOffset -le $heartbeats[1].Extent.EndOffset -or $live[1].Extent.EndOffset -ge $cache[0].Extent.StartOffset) { throw 'Early ACK live/publication/readback boundary order differs.' }
    $contained=Get-LaunchTestFunction $wa 'Assert-ParentTransitionCaptureContained'
    if ($contained.Body.EndBlock.Statements[-1] -isnot [Management.Automation.Language.ThrowStatementAst] -or
        $contained.Body.EndBlock.Statements[-1].Extent.Text -notmatch 'takeover is unqualified') { throw 'Independent resumed containment barrier differs.' }
    Assert-LaunchTestComparison $contained '$script:transitionLaunchAttempted -and -not $script:transitionLaunchAbortProven' 'ambiguous creation containment'
    $abort=@(Get-LaunchTestMembers $contained 'AbortBeforeResume');$dispose=@(Get-LaunchTestMembers $contained 'Dispose')
    $abortLatch=@($contained.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$script:transitionLaunchAbortProven' -and $n.Right.Extent.Text -ceq '$true'},$true))
    $clear=@($contained.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$script:transitionLaunch' -and $n.Right.Extent.Text -ceq '$null'},$true))
    if ($abort.Count -ne 1 -or $dispose.Count -ne 1 -or $abortLatch.Count -ne 1 -or $clear.Count -ne 1 -or
        $abort[0].Extent.EndOffset -ge $dispose[0].Extent.StartOffset -or $dispose[0].Extent.EndOffset -ge $abortLatch[0].Extent.StartOffset -or
        $abortLatch[0].Extent.EndOffset -ge $clear[0].Extent.StartOffset) { throw 'Pre-resume abort proof must latch after completed cleanup and before clear.' }
    $references=@(Get-LaunchTestMembers $contained 'ReferenceEquals')
    if ($references.Count -ne 2 -or $abort[0].Expression.Extent.Text -cne '$launch' -or $dispose[0].Expression.Extent.Text -cne '$launch' -or
        $references[0].Extent.StartOffset -le $abort[0].Extent.EndOffset -or $references[0].Extent.EndOffset -ge $dispose[0].Extent.StartOffset -or
        $references[1].Extent.StartOffset -le $dispose[0].Extent.EndOffset -or $references[1].Extent.EndOffset -ge $abortLatch[0].Extent.StartOffset) { throw 'Pre-resume cleanup lost original-reference checks.' }
    $registration=Get-LaunchTestFunction $wa 'Assert-ParentTransitionRegistration'
    if ($registration.Body.EndBlock.Statements[-1] -isnot [Management.Automation.Language.ThrowStatementAst]) { throw 'Full native closure admission barrier differs.' }
}
Invoke-LaunchTest 'actual gate cut and parent native capture AST integration' { Assert-LaunchTestWiring $source $gateSource }
$astNegatives=@(
    @('parent attempt latch','watchdog','$script:transitionLaunchAttempted = $true','$script:transitionLaunchAttempted = $false','One-shot attempt latch'),
    @('parent resume latch','watchdog','$script:transitionLaunchResumed = $true','$script:transitionLaunchResumed = $false','One-shot attempt latch'),
    @('suspended check','watchdog','$script:transitionLaunch.AssertSuspended()','$null','launch capability shape'),
    @('transient fence bound','watchdog','Read-ParentDocument $family.fence 8192','Read-ParentDocument $family.fence 1048576','transient bounded read'),
    @('actual READY poll','watchdog','$script:transitionLaunch.PollReady()','$null','owned capture polling'),
    @('actual terminal poll','watchdog','$script:transitionLaunch.PollTerminalExit()','$null','owned capture polling'),
    @('terminal exit0','watchdog','$terminal.ExitCode -ne 0','$false','comparison differs: exit0'),
    @('terminal empty stderr','watchdog','$terminal.StderrBytes -ne 0','$false','comparison differs: empty stderr'),
    @('actual receipt bound','watchdog','$receiptBytes.LongLength -ne $terminal.ReceiptBytes','$false','comparison differs: captured receipt length'),
    @('producer terminal version','watchdog',"`$receipt.format -cne 'stratus-successor-transition-watchdog-terminal-v1'",'$false','comparison differs: actual producer terminal v1'),
    @('receipt hash join','watchdog','Read-ParentHeldBytes $terminal.ReceiptPath $terminal.ReceiptSha256 262144','Read-ParentHeldBytes $terminal.ReceiptPath $null 262144','captured digest joins|capture digest joins'),
    @('pending terminal tail','watchdog','if ($null -eq $terminal) { return }','if ($null -eq $terminal) { Assert-ParentTransitionLaunchReady; return }','Pending terminal tail'),
    @('ACK live pre/post','watchdog','Assert-ParentTransitionLaunchReady','No-ParentTransitionLaunchReady','ACK live/publication'),
    @('uncertain missing child','watchdog','$script:transitionLaunchAttempted -and -not $script:transitionLaunchAbortProven','$false','comparison differs: ambiguous creation'),
    @('gate request attempt','gate','$state.transition_launch_requested = $true','$state.transition_launch_requested = $false','publication attempt is not one-shot'),
    @('pre-resume abort latch','watchdog','$script:transitionLaunchAbortProven = $true','$script:transitionLaunchAbortProven = $false','abort proof must latch'))
foreach ($row in $astNegatives) {
    Invoke-LaunchTest ('AST integration removal rejected: '+$row[0]) {
        $w=$source; $g=$gateSource
        if ($row[1] -ceq 'watchdog') { if (-not $w.Contains($row[2])) {throw 'Missing AST mutation target.'};$w=$w.Replace($row[2],$row[3]) }
        else { if (-not $g.Contains($row[2])) {throw 'Missing gate AST mutation target.'};$g=$g.Replace($row[2],$row[3]) }
        Assert-LaunchTestRejected { Assert-LaunchTestWiring $w $g } $row[4]
    }
}
# This actual cleanup decision helper is safe ONLY with explicit managed method
# stubs. It cannot certify an aborted native child or a real terminal protocol.
$contained=Get-LaunchTestFunction $ast 'Assert-ParentTransitionCaptureContained'
if (@($contained.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)).Count -ne 0) { throw 'Containment extractor exceeds managed stub scope.' }
foreach ($type in $contained.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst]},$true)) {
    if ($type.TypeName.FullName -cne 'object') { throw 'Containment extractor exceeds managed reference-check type scope.' }
}
foreach ($member in $contained.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)) {
    if ($member.Static) {
        if ($member.Extent.Text -cne '[object]::ReferenceEquals($script:transitionLaunch, $launch)') { throw 'Containment extractor found unreviewed static method.' }
    }
    elseif ($member.Expression.Extent.Text -cne '$launch' -or $member.Member.Value -cnotin @('AbortBeforeResume','Dispose') -or
        ($null -ne $member.Arguments -and $member.Arguments.Count -ne 0)) { throw 'Containment extractor found unreviewed method.' }
}
. ([scriptblock]::Create($contained.Extent.Text))
function Reset-LaunchContainmentStub {
    $script:transitionLaunchAttempted=$true; $script:transitionLaunchResumed=$false; $script:transitionLaunchAbortProven=$false
    $script:transitionLaunch=[pscustomobject]@{calls=[Collections.Generic.List[string]]::new();fail_abort=$false;fail_dispose=$false}
    $script:transitionLaunch | Add-Member -MemberType ScriptMethod -Name AbortBeforeResume -Value {$this.calls.Add('abort');if($this.fail_abort){throw 'managed abort failure'}}
    $script:transitionLaunch | Add-Member -MemberType ScriptMethod -Name Dispose -Value {$this.calls.Add('dispose');if($this.fail_dispose){throw 'managed dispose failure'}}
}
Invoke-LaunchTest 'never attempted independent launch needs no containment action' { Reset-LaunchContainmentStub;$script:transitionLaunch=$null;$script:transitionLaunchAttempted=$false;Assert-ParentTransitionCaptureContained }
Invoke-LaunchTest 'missing capability after attempted creation cannot claim containment' { Reset-LaunchContainmentStub;$script:transitionLaunch=$null;Assert-LaunchTestRejected {Assert-ParentTransitionCaptureContained} 'creation outcome is ambiguous' }
Invoke-LaunchTest 'pre-resume managed abort then disposal latches proof and permits idempotent rounds' { Reset-LaunchContainmentStub;$held=$script:transitionLaunch;Assert-ParentTransitionCaptureContained;Assert-ParentTransitionCaptureContained;if(($held.calls -join ',') -cne 'abort,dispose' -or -not $script:transitionLaunchAbortProven -or $null -ne $script:transitionLaunch){throw 'Pre-resume completion not idempotently latched.'} }
Invoke-LaunchTest 'failed managed abort retains child and cannot latch proof' { Reset-LaunchContainmentStub;$held=$script:transitionLaunch;$held.fail_abort=$true;Assert-LaunchTestRejected {Assert-ParentTransitionCaptureContained} 'managed abort failure';if(-not [object]::ReferenceEquals($held,$script:transitionLaunch) -or $script:transitionLaunchAbortProven -or ($held.calls -join ',') -cne 'abort'){throw 'Abort failure lost retained lifetime.'} }
Invoke-LaunchTest 'failed managed disposal retains child and permits cleanup retry' { Reset-LaunchContainmentStub;$held=$script:transitionLaunch;$held.fail_dispose=$true;Assert-LaunchTestRejected {Assert-ParentTransitionCaptureContained} 'managed dispose failure';if(-not [object]::ReferenceEquals($held,$script:transitionLaunch) -or $script:transitionLaunchAbortProven){throw 'Dispose failure falsely latched cleanup.'};$held.fail_dispose=$false;Assert-ParentTransitionCaptureContained;if(-not $script:transitionLaunchAbortProven){throw 'Cleanup retry not latched.'} }
Invoke-LaunchTest 'resumed independent guard is not killed or disposed by containment' { Reset-LaunchContainmentStub;$held=$script:transitionLaunch;$script:transitionLaunchResumed=$true;Assert-LaunchTestRejected {Assert-ParentTransitionCaptureContained} 'takeover is unqualified';if($held.calls.Count -ne 0 -or -not [object]::ReferenceEquals($held,$script:transitionLaunch)){throw 'Resumed guard was retired.'} }
$expectedCases=67
if ($results.Count -ne $expectedCases) { throw ('Fixed launch case count differs: '+$results.Count) }
[pscustomobject]@{suite='parent-transition-launch-ast-managed';expected=$expectedCases;executed=$results.Count;passed=$results.Count;failed=0
    watchdog_sha256=(Get-FileHash -LiteralPath $WatchdogPath -Algorithm SHA256).Hash.ToLowerInvariant()
    gate_sha256=(Get-FileHash -LiteralPath $GatePath -Algorithm SHA256).Hash.ToLowerInvariant()
    native_executed=$false;watchdog_body_executed=$false;gate_body_executed=$false;docker_executed=$false;filesystem_fixtures_written=$false
    helper_hash_type_remapped=$true;full_prestart_causal_accepted=$false;full_protocol_accepted=$false;results=@($results.ToArray())} | ConvertTo-Json -Depth 5 -Compress
