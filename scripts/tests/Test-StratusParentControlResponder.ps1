[CmdletBinding()]
param([string] $WatchdogPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $WatchdogPath) { $WatchdogPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'Invoke-StratusParentStageWatchdog.ps1' }
# Only six actual pure helpers are extracted. A tiny explicit managed UTF8
# substitution replaces Files; no runtime, private pipe, endpoint, main loop,
# reader, launch, Docker, native process or on-disk fixture is executed.
# AST controls inspect actual wiring but do not qualify native freshness.
$source = Get-Content -LiteralPath $WatchdogPath -Raw
$sourceHash = (Get-FileHash -LiteralPath $WatchdogPath -Algorithm SHA256).Hash.ToLowerInvariant()
function Read-ControlTestAst {
    param([string] $Text)
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
    if (@($errors).Count -ne 0) { throw 'Control responder AST parse failed.' }
    return $ast
}
function Get-ControlTestFunction {
    param([object] $Ast,[string] $Name)
    $found=@($Ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $Name},$true))
    if ($found.Count -ne 1) { throw ('Exact responder helper differs: '+$Name) }
    return $found[0]
}
function Assert-ControlTestCount {
    param([string] $Text,[string] $Needle,[int] $Expected)
    if ([regex]::Matches($Text,[regex]::Escape($Needle)).Count -ne $Expected) { throw ('Responder invariant differs: '+$Needle) }
}
$ast=Read-ControlTestAst $source
Add-Type -TypeDefinition @'
namespace StratusControlResponderTest {
    public static class Bytes {
        public static readonly System.Text.UTF8Encoding Utf8 = new System.Text.UTF8Encoding(false, true);
    }
}
'@
$pureNames=@('Assert-ParentProperties','Get-ParentUtc','Test-ParentInt32','Get-ParentNonAuthorization',
    'Assert-ParentTransitionControlRequest','New-ParentTransitionControlResponse')
foreach ($name in $pureNames) {
    $fn=Get-ControlTestFunction $ast $name
    foreach ($command in $fn.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)) {
        if ($command.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or
            $command.GetCommandName() -cnotmatch '\A(?:Assert-ParentProperties|Get-ParentUtc|Test-ParentInt32|Get-ParentNonAuthorization|Assert-ParentTransitionControlRequest|Microsoft\.PowerShell\.Utility\\ConvertTo-Json)\z') {
            throw ('Unreviewed pure responder command: '+$command.Extent.Text)
        }
    }
    foreach ($member in $fn.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)) {
        if ($member.Member -isnot [Management.Automation.Language.StringConstantExpressionAst] -or
            $member.Member.Value -cnotin @('GetType','Sort','ParseExact','ToString','GetBytes')) {
            throw ('Unreviewed pure responder member: '+$member.Extent.Text)
        }
    }
    foreach ($type in $fn.FindAll({param($n) $n -is [Management.Automation.Language.TypeExpressionAst]},$true)) {
        if ($type.TypeName.FullName -cnotin @('Array','StringComparer','DateTimeOffset','Globalization.CultureInfo',
            'TimeSpan','int','long','string','bool','Stratus.ParentStage.Files')) { throw ('Unreviewed pure responder type: '+$type.TypeName.FullName) }
    }
    . ([scriptblock]::Create($fn.Extent.Text.Replace('[Stratus.ParentStage.Files]','[StratusControlResponderTest.Bytes]')))
}
$results=[Collections.Generic.List[object]]::new()
function Invoke-ControlTest {
    param([string] $Name,[scriptblock] $Body)
    try { & $Body } catch { throw ('Responder case '+$Name+' failed: '+$_.Exception.Message) }
    $results.Add([pscustomobject]@{name=$Name;passed=$true})
}
function Assert-ControlTestRejected {
    param([scriptblock] $Body)
    $failed=$false
    try { & $Body | Out-Null } catch { $failed=$true }
    if (-not $failed) { throw 'Required control rejection was absent.' }
}
function New-ControlTestBinding {
    return [pscustomobject][ordered]@{
        format='stratus-parent-transition-control-request-v1'; operation='observe-transition-live'
        parent_session_id=('a'*32);parent_lease_sha256=('b'*64);transition_session_id=('c'*32)
        launch_intent_sha256=('d'*64);launch_ack_sha256=('e'*64);transition_nonce=('f'*64)
        transition_lease_sha256=('1'*64);transition_ready_sha256=('2'*64);prestart_sha256=('3'*64)
        hard_deadline_utc='2026-09-15T01:05:00.0000000+00:00';boundary='early-transition-control-probe'
    }
}
$now=[DateTimeOffset]::ParseExact('2026-09-15T01:01:00.0000000+00:00','o',[Globalization.CultureInfo]::InvariantCulture)
$birth='2026-09-15T01:00:10.0000000+00:00'
$fields=@('format','operation','parent_session_id','parent_lease_sha256','transition_session_id',
    'launch_intent_sha256','launch_ack_sha256','transition_nonce','transition_lease_sha256',
    'transition_ready_sha256','prestart_sha256','hard_deadline_utc','boundary')
Invoke-ControlTest 'exact thirteen-field selected domain binding' {
    Assert-ParentTransitionControlRequest (New-ControlTestBinding) (New-ControlTestBinding) $now
}
Invoke-ControlTest 'actual response bytes have eighteen ordered fields and all-false authority' {
    $bytes=New-ParentTransitionControlResponse (New-ControlTestBinding) (New-ControlTestBinding) 4321 $birth $now
    if ($bytes -isnot [byte[]] -or $bytes.Length -gt 4096 -or $bytes.Length -lt 1) { throw 'Response byte bound/type differs.' }
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
    if ($text.Contains([string][char]13) -or $text.Contains([string][char]10)) { throw 'Response carries an extra line.' }
    $parameters=@{InputObject=$text}
    if ((Get-Command Microsoft.PowerShell.Utility\ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $parameters.DateKind='String' }
    $value=Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
    $expectedNames=@($fields)+@('process_id','process_started_at','observed_at','status','authorization')
    if (($value.PSObject.Properties.Name -join '|') -cne ($expectedNames -join '|') -or
        $value.format -cne 'stratus-parent-transition-control-response-v1' -or $value.status -cne 'live' -or
        $value.process_id -ne 4321 -or $value.process_started_at -cne $birth -or $value.observed_at -cne $now.ToString('o')) { throw 'Response contract differs.' }
    $authNames=@('postgres_start_permitted','api_start_permitted','worker_start_permitted','safety_control_start_permitted',
        'live_transition_permitted','binance_kill_switch_removal_permitted','migration_container_permitted','container_remove_permitted')
    if (($value.authorization.PSObject.Properties.Name -join '|') -cne ($authNames -join '|')) { throw 'Authority field set differs.' }
    foreach ($p in $value.authorization.PSObject.Properties) { if ($p.Value -isnot [bool] -or $p.Value) { throw 'Authority was granted.' } }
}
foreach ($field in $fields) {
    Invoke-ControlTest ('exact binding rejection '+$field) {
        $value=New-ControlTestBinding; $expected=New-ControlTestBinding; $value.$field='different'
        Assert-ControlTestRejected { Assert-ParentTransitionControlRequest $value $expected $now }
    }
}
foreach ($field in $fields) {
    Invoke-ControlTest ('native string rejection '+$field) {
        $value=New-ControlTestBinding; $expected=New-ControlTestBinding; $value.$field=7
        Assert-ControlTestRejected { Assert-ParentTransitionControlRequest $value $expected $now }
    }
}
Invoke-ControlTest 'missing request field rejected' {
    $v=New-ControlTestBinding; $v.PSObject.Properties.Remove('launch_ack_sha256')
    Assert-ControlTestRejected { Assert-ParentTransitionControlRequest $v (New-ControlTestBinding) $now }
}
Invoke-ControlTest 'extra request field rejected' {
    $v=New-ControlTestBinding; $v | Add-Member unexpected $false
    Assert-ControlTestRejected { Assert-ParentTransitionControlRequest $v (New-ControlTestBinding) $now }
}
foreach ($field in @('parent_session_id','transition_session_id','parent_lease_sha256','launch_intent_sha256',
    'launch_ack_sha256','transition_nonce','transition_lease_sha256','transition_ready_sha256','prestart_sha256')) {
    Invoke-ControlTest ('matched but malformed domain field rejected '+$field) {
        $v=New-ControlTestBinding; $e=New-ControlTestBinding; $v.$field='invalid'+[char]10; $e.$field=$v.$field
        Assert-ControlTestRejected { Assert-ParentTransitionControlRequest $v $e $now }
    }
}
foreach ($field in @('format','operation','boundary')) {
    Invoke-ControlTest ('matched but unimplemented operation rejected '+$field) {
        $v=New-ControlTestBinding; $e=New-ControlTestBinding; $v.$field='another'; $e.$field=$v.$field
        Assert-ControlTestRejected { Assert-ParentTransitionControlRequest $v $e $now }
    }
}
Invoke-ControlTest 'original deadline reached rejected' {
    Assert-ControlTestRejected { Assert-ParentTransitionControlRequest (New-ControlTestBinding) (New-ControlTestBinding) $now.AddMinutes(4) }
}
foreach ($time in @('2026-09-15T01:05:00Z','2026-09-15T01:05:00.0000000+01:00','bad')) {
    Invoke-ControlTest ('matched malformed deadline rejected '+$time) {
        $v=New-ControlTestBinding; $e=New-ControlTestBinding; $v.hard_deadline_utc=$time; $e.hard_deadline_utc=$time
        Assert-ControlTestRejected { Assert-ParentTransitionControlRequest $v $e $now }
    }
}
foreach ($pidValue in @('4321',0,-1)) {
    Invoke-ControlTest ('native positive PID required '+[string]$pidValue) {
        Assert-ControlTestRejected { New-ParentTransitionControlResponse (New-ControlTestBinding) (New-ControlTestBinding) $pidValue $birth $now }
    }
}
foreach ($birthValue in @($now.AddSeconds(1).ToString('o'),'2026-09-15T01:00:10Z',7)) {
    Invoke-ControlTest ('original canonical process birth required '+[string]$birthValue) {
        Assert-ControlTestRejected { New-ParentTransitionControlResponse (New-ControlTestBinding) (New-ControlTestBinding) 4321 $birthValue $now }
    }
}
Invoke-ControlTest 'non-UTC observed clock rejected' {
    Assert-ControlTestRejected { New-ParentTransitionControlResponse (New-ControlTestBinding) (New-ControlTestBinding) 4321 $birth $now.ToOffset([TimeSpan]::FromHours(1)) }
}
Invoke-ControlTest 'native in-range Int64 process identity remains native' {
    $bytes=New-ParentTransitionControlResponse (New-ControlTestBinding) (New-ControlTestBinding) ([long]4321) $birth $now
    if ($bytes -isnot [byte[]]) { throw 'Native Int64 response failed.' }
}
function Assert-ControlTestIntegration {
    param([string] $Text)
    $a=Read-ControlTestAst $Text
    $service=(Get-ControlTestFunction $a 'Service-ParentTransitionControl').Extent.Text
    $context=(Get-ControlTestFunction $a 'Get-ParentTransitionControlContext').Extent.Text
    foreach ($item in @(
        @('$script:controlEndpoint.PollRequest()',1),@('$script:controlEndpoint.Respond($request, $bytes)',1),
        @('$script:transitionControlAttempted = $true',1),@('if ($script:transitionControlAttempted)',1),
        @('[Stratus.ParentStage.PrivateControlRequest]',1),@('Assert-ParentHeartbeat',2),
        @('Assert-ParentTransitionLaunchReady',2),@('Get-ParentTransitionControlContext',2),
        @('ConvertFrom-ParentJson $payload',1),@('$payload.Length -gt 4096',1),
        @('$script:controlEndpoint.AssertHealthy()',2))) {
        Assert-ControlTestCount $service $item[0] $item[1]
    }
    if ($service.IndexOf('$script:transitionControlAttempted = $true') -gt $service.IndexOf('ConvertFrom-ParentJson $payload') -or
        $service.LastIndexOf('Assert-ParentTransitionLaunchReady') -gt $service.IndexOf('$script:controlEndpoint.Respond(') -or
        $service.LastIndexOf('Assert-ParentHeartbeat') -lt $service.LastIndexOf('Get-ParentTransitionControlContext') -or
        $service.LastIndexOf('Assert-ParentTransitionLaunchReady') -lt $service.LastIndexOf('Assert-ParentHeartbeat')) { throw 'Responder actual sample/publication order differs.' }
    Assert-ControlTestCount $context '$ack.published' 1
    Assert-ControlTestCount $context '[Stratus.ParentStage.Files]::Hash($ack.bytes)' 1
    Assert-ControlTestCount $context '(Read-ParentDocument $ack.path 65536).sha256' 1
    Assert-ControlTestCount $context '(Read-ParentDocument $intent.path 65536).sha256' 1
    Assert-ControlTestCount $context '$null -ne $script:transitionLaunchTerminal' 1
    Assert-ControlTestCount $Text '$script:controlEndpoint = [Stratus.ParentStage.PrivateControlEndpoint]::Open(' 1
    Assert-ControlTestCount $Text "ContainsKey('ControlInputHandle')" 1
    Assert-ControlTestCount $Text "ContainsKey('ControlOutputHandle')" 1
    $open=$Text.IndexOf('$script:controlEndpoint = [Stratus.ParentStage.PrivateControlEndpoint]::Open(')
    $r1=$Text.IndexOf('$script:r1Proof = Assert-ParentR1Anchor')
    if ($open -lt $Text.IndexOf('$locks.Add($script:hostLock)') -or $open -gt $r1) { throw 'Endpoint inheritance is not cleared before external work.' }
    $loops=@($a.FindAll({param($n) $n -is [Management.Automation.Language.WhileStatementAst]},$true) |
        Where-Object {$_.Body.Statements.Count -gt 0 -and $_.Body.Statements[0].Extent.Text -ceq 'Service-ParentTransitionControl'})
    if ($loops.Count -ne 1) { throw 'Main loop does not service private control first.' }
    $throws=@($a.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.ThrowStatementAst]})
    if ($throws.Count -ne 1 -or $throws[0].Extent.Text -notlike '*unconditional entry blocker*') { throw 'Entry WIP barrier changed.' }
    if (@($service | Select-String -Pattern 'Write-Parent|Invoke-ParentDocker|validatedCompletions|BeginInvoke|Start-ThreadJob|Start-Job').Count -ne 0) {
        throw 'Responder unexpectedly writes evidence or delegates status authority.'
    }
}
Invoke-ControlTest 'actual owner-thread control wiring and no mutation route' { Assert-ControlTestIntegration $source }
$mutations=@(
    @('native request poll','$script:controlEndpoint.PollRequest()','$null'),
    @('actual response queue','$script:controlEndpoint.Respond($request, $bytes)','$null'),
    @('one shot latch','$script:transitionControlAttempted = $true','$script:transitionControlAttempted = $false'),
    @('replay rejection','if ($script:transitionControlAttempted)','if ($false)'),
    @('native request identity','[Stratus.ParentStage.PrivateControlRequest]','[object]'),
    @('fresh heartbeat','    Assert-ParentHeartbeat','    # removed heartbeat'),
    @('actual native readiness','    Assert-ParentTransitionLaunchReady','    # removed native readiness'),
    @('strict request JSON','ConvertFrom-ParentJson $payload','[pscustomobject]@{}'),
    @('payload bound','$payload.Length -gt 4096','$payload.Length -gt 99999'),
    @('owned ACK published','$ack.published','$true'),
    @('owned ACK raw hash','[Stratus.ParentStage.Files]::Hash($ack.bytes)','$ack.sha256'),
    @('ACK held readback','(Read-ParentDocument $ack.path 65536).sha256','$ack.sha256'),
    @('intent held readback','(Read-ParentDocument $intent.path 65536).sha256','$intent.sha256'),
    @('terminal live blocker','$null -ne $script:transitionLaunchTerminal','$false'),
    @('required input endpoint',"ContainsKey('ControlInputHandle')","ContainsKey('OtherInput')"),
    @('required output endpoint',"ContainsKey('ControlOutputHandle')","ContainsKey('OtherOutput')")
)
foreach ($mutation in $mutations) {
    Invoke-ControlTest ('AST removal rejected '+$mutation[0]) {
        if (-not $source.Contains($mutation[1])) { throw 'Removal target absent.' }
        Assert-ControlTestRejected { Assert-ControlTestIntegration $source.Replace($mutation[1],$mutation[2]) }
    }
}
$expected=71
if ($results.Count -ne $expected -or @($results | Where-Object {-not $_.passed}).Count -ne 0) { throw ('Fixed control responder case count differs: '+$results.Count) }
[ordered]@{suite='parent-private-control-responder-ast-managed';expected=$expected;executed=$results.Count;passed=$results.Count;failed=0
    watchdog_sha256=$sourceHash;native_executed=$false;watchdog_body_executed=$false;private_pipe_executed=$false
    docker_executed=$false;filesystem_fixtures_written=$false;helper_utf8_type_remapped=$true
    freshness_qualified=$false;full_protocol_accepted=$false;results=@($results)} | ConvertTo-Json -Depth 6 -Compress
