[CmdletBinding()]
param([string] $GatePath, [string] $WatchdogPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $GatePath) { $GatePath = Join-Path $PSScriptRoot '..\Assert-StratusPostMigrationSuccessorGate.ps1' }
if (-not $WatchdogPath) { $WatchdogPath = Join-Path $PSScriptRoot '..\Invoke-StratusParentStageWatchdog.ps1' }
# Cross-function DOMAIN interoperability only: actual five gate and six parent
# pure helpers, with exactly one Files.Utf8 accessor replaced by managed UTF8.
# The date-preserving JSON parser below is a fixture, NOT a native StrictJson or
# endpoint substitute/proof. No main bodies, runtime, pipes, process/Job, Docker,
# old fault experiment or filesystem fixture executes. Wire codec is separate.
function Assert-Interop { param([bool] $Condition, [string] $Message) if (-not $Condition) { throw $Message } }
function Read-InteropAst {
    param([string] $Path)
    $text = [IO.File]::ReadAllText([IO.Path]::GetFullPath($Path)); $tokens = $null; $errors = $null
    $tree = [Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)
    Assert-Interop (@($errors).Count -eq 0) 'Source failed AST parsing.'
    return $tree
}
function Get-InteropFunction {
    param([object] $Tree, [string] $Name)
    $found = @($Tree.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $Name }, $true))
    Assert-Interop ($found.Count -eq 1) ('Missing or ambiguous pure helper: ' + $Name)
    $parent = $found[0].Parent
    while ($null -ne $parent) {
        Assert-Interop ($parent -isnot [Management.Automation.Language.FunctionDefinitionAst]) 'Pure helper cannot be nested.'
        $parent = $parent.Parent
    }
    return $found[0]
}
$gateAst = Read-InteropAst $GatePath; $parentAst = Read-InteropAst $WatchdogPath
$gateNames = @('Assert-ParentStageControlFields','ConvertTo-ParentStageControlTime','Assert-ParentStageControlRequest',
    'New-ParentStageControlRequestBytes','Assert-ParentStageControlResponse')
$parentNames = @('Assert-ParentProperties','Get-ParentUtc','Test-ParentInt32','Get-ParentNonAuthorization',
    'Assert-ParentTransitionControlRequest','New-ParentTransitionControlResponse')
$definitions = [Collections.Generic.List[object]]::new()
foreach ($selection in @(@{tree=$gateAst;names=$gateNames}, @{tree=$parentAst;names=$parentNames})) {
    foreach ($name in $selection.names) {
        $fn = Get-InteropFunction $selection.tree $name
        foreach ($command in @($fn.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] }, $true))) {
            Assert-Interop ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
                $command.GetCommandName() -cin @('Assert-ParentStageControlFields','ConvertTo-ParentStageControlTime',
                    'Assert-ParentStageControlRequest','ConvertTo-Json','Assert-ParentProperties','Get-ParentUtc',
                    'Test-ParentInt32','Get-ParentNonAuthorization','Assert-ParentTransitionControlRequest',
                    'Microsoft.PowerShell.Utility\ConvertTo-Json')) ('Unsafe extracted command: ' + $command.Extent.Text)
        }
        foreach ($type in @($fn.FindAll({ param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or
            $n -is [Management.Automation.Language.TypeConstraintAst] }, $true))) {
            Assert-Interop ($type.TypeName.FullName -cin @('object','string','string[]','char','int','long','bool','void','ordered',
                'DateTimeOffset','Globalization.CultureInfo','TimeSpan','Text.UTF8Encoding','Array','StringComparer',
                'Stratus.ParentStage.Files')) ('Unsafe extracted type: ' + $type.TypeName.FullName)
        }
        foreach ($method in @($fn.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] }, $true))) {
            Assert-Interop ($method.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and
                $method.Member.Value -cin @('GetType','IndexOf','Contains','ParseExact','ToString','new','GetBytes','AddSeconds','Sort')) ('Unsafe extracted method: ' + $method.Extent.Text)
        }
        foreach ($variable in @($fn.FindAll({ param($n) $n -is [Management.Automation.Language.VariableExpressionAst] }, $true))) {
            Assert-Interop (-not $variable.VariablePath.UserPath.Contains(':')) 'External/scoped state in extracted helper.'
        }
        $definitions.Add($fn)
    }
}
Assert-Interop ($definitions.Count -eq 11) 'Exact pure helper set differs.'
foreach ($fn in $definitions) {
    $text = $fn.Extent.Text
    $accessors = @([regex]::Matches($text, '\[Stratus\.ParentStage\.Files\]'))
    if ($fn.Name -ceq 'New-ParentTransitionControlResponse') {
        Assert-Interop ($accessors.Count -eq 1 -and $text.Contains('[Stratus.ParentStage.Files]::Utf8.GetBytes(')) 'Unexpected Files dependency in pure producer.'
        $text = $text.Replace('[Stratus.ParentStage.Files]::Utf8', '[Text.UTF8Encoding]::new($false, $true)')
    } else { Assert-Interop ($accessors.Count -eq 0) 'Unreviewed Files dependency.' }
    . ([scriptblock]::Create($text))
}
$expectedCases = 31; $script:cases = 0; $script:passed = 0; $script:failures = [Collections.Generic.List[string]]::new()
function Case {
    param([string] $Name, [scriptblock] $Body)
    $script:cases++
    try { & $Body; $script:passed++; Write-Output ('PASS parent-control-interop: ' + $Name) }
    catch { $script:failures.Add($Name + ': ' + $_.Exception.GetBaseException().Message); Write-Output ('FAIL parent-control-interop: ' + $Name) }
}
function Reject {
    param([scriptblock] $Body)
    $rejected = $false; try { & $Body | Out-Null } catch { $rejected = $true }
    Assert-Interop $rejected 'Cross-function invalid input unexpectedly accepted.'
}
function Parse-FixtureJson {
    param([byte[]] $Bytes)
    $args = @{ InputObject = [Text.UTF8Encoding]::new($false, $true).GetString($Bytes) }
    if ((Get-Command Microsoft.PowerShell.Utility\ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $args.DateKind = 'String' }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @args
}
function New-Binding {
    return [pscustomobject][ordered]@{
        format = 'stratus-parent-transition-control-request-v1'; operation = 'observe-transition-live'
        parent_session_id = 'a' * 32; parent_lease_sha256 = 'b' * 64; transition_session_id = 'c' * 32
        launch_intent_sha256 = 'd' * 64; launch_ack_sha256 = 'e' * 64; transition_nonce = 'f' * 64
        transition_lease_sha256 = '1' * 64; transition_ready_sha256 = '2' * 64; prestart_sha256 = '3' * 64
        hard_deadline_utc = '2026-09-15T12:10:00.0000000+00:00'; boundary = 'early-transition-control-probe'
    }
}
$begin = [DateTimeOffset]::ParseExact('2026-09-15T12:01:00.0000000+00:00','o',[Globalization.CultureInfo]::InvariantCulture)
$observation = $begin.AddMilliseconds(100); $end = $begin.AddMilliseconds(200)
$birth = '2026-09-15T12:00:00.1234567+00:00'
function Roundtrip {
    param([object] $ProcessId = ([int]4321))
    $client = New-Binding; $expected = New-Binding
    $requestBytes = New-ParentStageControlRequestBytes $client
    $received = Parse-FixtureJson $requestBytes
    Assert-ParentTransitionControlRequest $received $expected $observation
    $responseBytes = New-ParentTransitionControlResponse $received $expected $ProcessId $birth $observation
    $response = Parse-FixtureJson $responseBytes
    Assert-ParentStageControlResponse $response $client ([int]4321) $birth $begin $end
    return [pscustomobject]@{request=$received;response=$response;request_bytes=$requestBytes;response_bytes=$responseBytes}
}
Case 'actual client request server response and client validation complete a pure JSON roundtrip' {
    $round = Roundtrip
    Assert-Interop ($round.request_bytes -is [byte[]] -and $round.response_bytes -is [byte[]] -and
        $round.request_bytes.Length -le 4096 -and $round.response_bytes.Length -le 4096) 'Actual producer bytes exceed common bounds.'
}
Case 'actual ordered schemas are exact13 and18 with unchanged twelve echoed bindings' {
    $round = Roundtrip
    $requestFields = 'format,operation,parent_session_id,parent_lease_sha256,transition_session_id,launch_intent_sha256,launch_ack_sha256,transition_nonce,transition_lease_sha256,transition_ready_sha256,prestart_sha256,hard_deadline_utc,boundary'
    Assert-Interop (($round.request.PSObject.Properties.Name -join ',') -ceq $requestFields -and
        ($round.response.PSObject.Properties.Name -join ',') -ceq ($requestFields + ',process_id,process_started_at,observed_at,status,authorization')) 'Actual cross schemas differ.'
    foreach ($p in $round.request.PSObject.Properties) {
        if ($p.Name -cne 'format') { Assert-Interop ($round.response.($p.Name) -ceq $p.Value) ('Echo changed: ' + $p.Name) }
    }
}
Case 'actual native int producer survives JSON as native int or long in both shell families' {
    $round = Roundtrip ([int]4321)
    Assert-Interop (($round.response.process_id -is [int] -or $round.response.process_id -is [long]) -and
        $round.response.process_id -eq 4321) 'JSON integer representation differs.'
}
Case 'actual native long producer survives JSON without string or float coercion' { [void](Roundtrip ([long]4321)) }
Case 'actual UTC process instant and observer instant retain seven-digit canonical precision' {
    $round = Roundtrip
    Assert-Interop ($round.response.process_started_at -ceq $birth -and $round.response.observed_at -ceq $observation.ToString('o') -and
        $round.response.process_started_at -is [string] -and $round.response.observed_at -is [string]) 'Date parser/serializer changed actual instants.'
}
Case 'actual response authorization is exact eight false native booleans' {
    $round = Roundtrip
    $names = 'postgres_start_permitted,api_start_permitted,worker_start_permitted,safety_control_start_permitted,live_transition_permitted,binance_kill_switch_removal_permitted,migration_container_permitted,container_remove_permitted'
    Assert-Interop (($round.response.authorization.PSObject.Properties.Name -join ',') -ceq $names) 'Authorization schema differs.'
    foreach ($p in $round.response.authorization.PSObject.Properties) { Assert-Interop ($p.Value -is [bool] -and -not $p.Value) 'Authorization was widened/coerced.' }
}
Case 'actual compact request and response contain no BOM CR or LF' {
    $round = Roundtrip
    foreach ($bytes in @($round.request_bytes,$round.response_bytes)) {
        $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
        Assert-Interop ($bytes[0] -eq 123 -and $bytes[-1] -eq 125 -and -not $text.Contains("`r") -and -not $text.Contains("`n")) 'JSON acquired extra framing.'
    }
}
Case 'actual roundtrip remains invariant under non-English thread culture' {
    $thread = [Threading.Thread]::CurrentThread; $saved = $thread.CurrentCulture
    try { $thread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('de-DE'); [void](Roundtrip) }
    finally { $thread.CurrentCulture = $saved }
}
foreach ($name in @('parent_session_id','parent_lease_sha256','transition_session_id','launch_intent_sha256','launch_ack_sha256',
    'transition_nonce','transition_lease_sha256','transition_ready_sha256','prestart_sha256','hard_deadline_utc','boundary','operation')) {
    $field = $name
    Case ('actual server rejects client bytes rebound at ' + $field) {
        $client = New-Binding; $received = Parse-FixtureJson (New-ParentStageControlRequestBytes $client)
        $expected = New-Binding; $expected.$field = 'different'
        Reject { New-ParentTransitionControlResponse $received $expected 4321 $birth $observation }
    }
}
Case 'actual server rejects an expired original client deadline' {
    $client = New-Binding; $received = Parse-FixtureJson (New-ParentStageControlRequestBytes $client)
    Reject { New-ParentTransitionControlResponse $received (New-Binding) 4321 $birth $begin.AddMinutes(10) }
}
Case 'actual server rejects coerced float string bool or oversized identity before response bytes' {
    foreach ($pidValue in @('4321',4321.0,$true,[long]2147483648)) { Reject { Roundtrip $pidValue } }
}
Case 'client rejects a real server response replayed against a different ACK' {
    $round = Roundtrip; $client = New-Binding; $client.launch_ack_sha256 = '4' * 64
    Reject { Assert-ParentStageControlResponse $round.response $client 4321 $birth $begin $end }
}
Case 'client rejects a real server response against a different process birth' {
    $round = Roundtrip
    Reject { Assert-ParentStageControlResponse $round.response (New-Binding) 4321 '2026-09-15T12:00:00.1234568+00:00' $begin $end }
}
Case 'client rejects a real server response against a different process PID' {
    $round = Roundtrip; Reject { Assert-ParentStageControlResponse $round.response (New-Binding) 4322 $birth $begin $end }
}
Case 'client rejects expired replay of actual response outside its current wall interval' {
    $round = Roundtrip
    Reject { Assert-ParentStageControlResponse $round.response (New-Binding) 4321 $birth $begin.AddSeconds(5) $end.AddSeconds(5) }
}
Case 'client rejects actual response altered to grant PostgreSQL authority' {
    $round = Roundtrip; $round.response.authorization.postgres_start_permitted = $true
    Reject { Assert-ParentStageControlResponse $round.response (New-Binding) 4321 $birth $begin $end }
}
Case 'client rejects actual response with typed timestamp substituted after JSON parse' {
    $round = Roundtrip; $round.response.observed_at = $observation.UtcDateTime
    Reject { Assert-ParentStageControlResponse $round.response (New-Binding) 4321 $birth $begin $end }
}
Case 'client rejects actual response with string PID substituted after JSON parse' {
    $round = Roundtrip; $round.response.process_id = '4321'
    Reject { Assert-ParentStageControlResponse $round.response (New-Binding) 4321 $birth $begin $end }
}
Case 'client rejects actual response changed to another stage even if status remains live' {
    $round = Roundtrip; $round.response.boundary = 'postgres-start'
    Reject { Assert-ParentStageControlResponse $round.response (New-Binding) 4321 $birth $begin $end }
}
Case 'the pure client acceptance emits no authority or native proof on actual server bytes' {
    $round = Roundtrip
    Assert-Interop (@(Assert-ParentStageControlResponse $round.response (New-Binding) 4321 $birth $begin $end).Count -eq 0) 'Pure acceptance emitted authority.'
}
if ($script:cases -ne $expectedCases) { throw ('Interop case count differs: expected ' + $expectedCases + ', actual ' + $script:cases) }
Write-Output ('Parent control interop tests: ' + $script:passed + '/' + $expectedCases + ' passed. Pure domain roundtrip only; native transport and freshness unqualified.')
if ($script:failures.Count -ne 0) { throw ($script:failures -join '; ') }
