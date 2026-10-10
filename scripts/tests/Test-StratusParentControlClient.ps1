[CmdletBinding()]
param([string] $GatePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PSBoundParameters.ContainsKey('GatePath')) { $GatePath = Join-Path $PSScriptRoot '..\Assert-StratusPostMigrationSuccessorGate.ps1' }
$GatePath = [IO.Path]::GetFullPath($GatePath)
$source = [IO.File]::ReadAllText($GatePath)
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
# Only AST/source and the five explicitly reviewed scalar/managed-byte helpers
# execute. The gate body, native client, pipes, clocks, process/Job APIs, Docker,
# existing files and historical fault experiments are NEVER executed by this suite.
# PASS is not a native 1000ms qualification or full-protocol/release acceptance.
$expectedCases = 44; $script:executed = 0; $script:passed = 0
$script:failures = [Collections.Generic.List[string]]::new()
function Assert-Test { param([bool] $Condition, [string] $Message) if (-not $Condition) { throw $Message } }
function Invoke-Case {
    param([string] $Name, [scriptblock] $Action)
    $script:executed++
    try { & $Action; $script:passed++; Write-Output ('PASS parent-control-client: ' + $Name) }
    catch { $script:failures.Add($Name + ': ' + $_.Exception.GetBaseException().Message); Write-Output ('FAIL parent-control-client: ' + $Name) }
}
function Assert-Rejected {
    param([scriptblock] $Action)
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    Assert-Test $rejected 'Invalid pure control data unexpectedly succeeded.'
}
function Get-FunctionAst {
    param([string] $Name)
    $found = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name
    }, $true))
    Assert-Test ($found.Count -eq 1) ('Missing or ambiguous helper: ' + $Name)
    $parent = $found[0].Parent
    while ($null -ne $parent) {
        Assert-Test ($parent -isnot [Management.Automation.Language.FunctionDefinitionAst]) ('Nested helper: ' + $Name)
        $parent = $parent.Parent
    }
    return $found[0]
}
function Test-ClientNativeProof {
    param([string] $Text)
    foreach ($required in @('$launch = $state.launch',
        '$responseType = $state.launch_type.Assembly.GetType($state.launch_type.Namespace + ''.PrivateControlResponse'', $true)',
        '[object]::ReferenceEquals($state.launch, $launch)', '$response.GetType() -ne $responseType',
        '$response.RequestPayloadSha256 -cne $payloadSha256', '$response.RequestSha256 -cnotmatch ''\A[0-9a-f]{64}\z''',
        '$response.Challenge -cnotmatch ''\A[0-9a-f]{64}\z''', '$response.Sequence -ne 1',
        '$response.ObservedRoundtripMilliseconds -lt 0', '$response.ObservedRoundtripMilliseconds -ge 1000',
        'throw ''Parent CONTROL response is not the exact original native private capability.''')) {
        if (-not $Text.Contains($required)) { return $false }
    }
    return $true
}
function Test-ClientConsumeOrder {
    param([string] $Text)
    $validate = $Text.IndexOf('Assert-ParentStageControlResponse -Value $value')
    $boundary = $Text.IndexOf('Assert-ParentStageBoundary -Boundary ''private Transition CONTROL before consumption''')
    if ($boundary -lt 0) { return $false }
    $health = $Text.IndexOf('$launch.AssertControlHealthy()', $boundary)
    $consume = $Text.IndexOf('$response.Consume()')
    if ($consume -lt 0) { return $false }
    $post = $Text.IndexOf('$launch.AssertControlHealthy()', $consume)
    $latch = $Text.IndexOf('$state.transition_control_consumed = $true')
    return ($validate -ge 0 -and $boundary -gt $validate -and $health -gt $boundary -and
        $consume -gt $health -and $post -gt $consume -and $latch -gt $post -and
        @([regex]::Matches($Text, '\$response\.Consume\(\)')).Count -eq 1)
}
function Test-ClientStrictParse {
    param([string] $Text)
    $bound = $Text.IndexOf('$responseBytes.Length -gt 4096')
    $strict = $Text.IndexOf('$strictType::Validate($responseBytes)')
    $parse = $Text.IndexOf('$value = ConvertFrom-StrictJsonObjectText -Json $response.PayloadText')
    return ($bound -gt 0 -and $strict -gt $bound -and $parse -gt $strict -and
        $Text.Contains('$response.PayloadText.Contains("`r")') -and $Text.Contains('$response.PayloadText.Contains("`n")') -and
        $Text.Contains('$strictType = $state.strict_type'))
}
function Test-ClientPilot {
    param([string] $Text)
    $held = $Text.IndexOf('Assert-ParentStageBoundary -Boundary ''early Transition ACK held readback''')
    $retain = $Text.IndexOf('$state.transition_launch_ack = $ack')
    $probe = $Text.IndexOf('Invoke-ParentStageTransitionLiveQuery')
    $wip = $Text.IndexOf('throw ''Parent-owned Transition fresh-live/terminal/containment integration is WIP; no PostgreSQL start is authorized.''')
    return ($held -ge 0 -and $retain -gt $held -and $probe -gt $retain -and $wip -gt $probe -and
        @([regex]::Matches($Text, 'Invoke-ParentStageTransitionLiveQuery')).Count -eq 1)
}
function Test-ClientOneShot {
    param([string] $Text)
    $latch = $Text.IndexOf('$state.transition_control_attempted = $true')
    $begin = $Text.IndexOf('$launch.BeginControlRequest($bytes)')
    return ($Text.Contains('$state.transition_control_attempted -or $state.transition_control_consumed') -and
        $Text.Contains('$null -eq $state.transition_launch_ack') -and $latch -ge 0 -and $begin -gt $latch -and
        @([regex]::Matches($Text, '\.BeginControlRequest\(')).Count -eq 1)
}
function Test-ClientRequestBindings {
    param([string] $Text)
    foreach ($binding in @('parent_session_id = $state.contract.session_id', 'parent_lease_sha256 = $state.lease_sha256',
        'transition_session_id = $ack.boundary.transition_session_id', 'launch_intent_sha256 = $state.transition_launch_intent_sha256',
        'launch_ack_sha256 = $state.transition_launch_ack_sha256', 'transition_nonce = $ack.boundary.nonce',
        'transition_lease_sha256 = $ack.boundary.lease_sha256', 'transition_ready_sha256 = $ack.boundary.ready_sha256',
        'prestart_sha256 = $ack.boundary.prestart_sha256', 'hard_deadline_utc = $ack.boundary.hard_deadline_utc',
        '$bytes = New-ParentStageControlRequestBytes $request', '$payloadSha256 = Get-ByteArraySha256 -Bytes $bytes')) {
        if (-not $Text.Contains($binding)) { return $false }
    }
    return $true
}
$pureNames = @('Assert-ParentStageControlFields','ConvertTo-ParentStageControlTime','Assert-ParentStageControlRequest',
    'New-ParentStageControlRequestBytes','Assert-ParentStageControlResponse')
Invoke-Case 'gate source parses and only exact top-level pure helpers are selected' {
    Assert-Test (@($parseErrors).Count -eq 0) 'Gate syntax differs.'
    foreach ($name in $pureNames) { [void](Get-FunctionAst $name) }
}
Invoke-Case 'closed pure extraction excludes IO native pipes processes and dynamic commands' {
    foreach ($name in $pureNames) {
        $fn = Get-FunctionAst $name
        foreach ($command in @($fn.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] }, $true))) {
            Assert-Test ($command.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Unknown -and
                $command.GetCommandName() -cin @('Assert-ParentStageControlFields','ConvertTo-ParentStageControlTime',
                    'Assert-ParentStageControlRequest','ConvertTo-Json')) ('Unreviewed command: ' + $command.Extent.Text)
        }
        foreach ($type in @($fn.FindAll({ param($n) $n -is [Management.Automation.Language.TypeExpressionAst] -or
            $n -is [Management.Automation.Language.TypeConstraintAst] }, $true))) {
            Assert-Test ($type.TypeName.FullName -cin @('object','string','string[]','char','int','long','bool','void','DateTimeOffset',
                'Globalization.CultureInfo','TimeSpan','Text.UTF8Encoding')) ('Unreviewed type: ' + $type.TypeName.FullName)
        }
        foreach ($method in @($fn.FindAll({ param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst] }, $true))) {
            Assert-Test ($method.Member -is [Management.Automation.Language.StringConstantExpressionAst] -and
                $method.Member.Value -cin @('GetType','IndexOf','Contains','ParseExact','ToString','new','GetBytes','AddSeconds')) ('Unreviewed method: ' + $method.Extent.Text)
        }
        foreach ($variable in @($fn.FindAll({ param($n) $n -is [Management.Automation.Language.VariableExpressionAst] }, $true))) {
            Assert-Test (-not $variable.VariablePath.UserPath.Contains(':')) 'Scoped/external variable entered pure extraction.'
        }
    }
}
$query = (Get-FunctionAst 'Invoke-ParentStageTransitionLiveQuery').Extent.Text
$launchRequest = (Get-FunctionAst 'Request-ParentStageTransitionLaunch').Extent.Text
Invoke-Case 'native response exact original launch namespace assembly and request proof are mandatory' { Assert-Test (Test-ClientNativeProof $query) 'Original private capability proof differs.' }
Invoke-Case 'one-shot attempt latches before beginning any native request' { Assert-Test (Test-ClientOneShot $query) 'Uncertain private requests may retry.' }
Invoke-Case 'request fields derive from held parent and captured launch ACK context' { Assert-Test (Test-ClientRequestBindings $query) 'Request source bindings differ.' }
Invoke-Case 'bounded duplicate-key strict validation precedes ordinary JSON parsing' { Assert-Test (Test-ClientStrictParse $query) 'Strict parse boundary differs.' }
Invoke-Case 'consumption follows semantic check and ownership recheck and precedes success latch' { Assert-Test (Test-ClientConsumeOrder $query) 'Native consumption order differs.' }
Invoke-Case 'single pilot follows held ACK and remains before unconditional WIP throw' {
    Assert-Test (Test-ClientPilot $launchRequest) 'Pilot bypasses held ACK or WIP barrier.'
    $calls = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq 'Invoke-ParentStageTransitionLiveQuery' }, $true))
    Assert-Test ($calls.Count -eq 1) 'Pilot is invoked elsewhere.'
}
Invoke-Case 'wait stays supervised and query has no catch mutation disposal or returned authority' {
    foreach ($anchor in @('Assert-ParentStageBoundary -Boundary ''private Transition CONTROL before request''',
        'Assert-ParentStageBoundary -Boundary ''private Transition CONTROL response wait''', '$launch.PollControlResponse()', 'Start-Sleep -Milliseconds 10')) {
        Assert-Test ($query.Contains($anchor)) ('Missing supervised wait: ' + $anchor)
    }
    $fn = Get-FunctionAst 'Invoke-ParentStageTransitionLiveQuery'
    Assert-Test (@($fn.FindAll({ param($n) $n -is [Management.Automation.Language.CatchClauseAst] -or
        $n -is [Management.Automation.Language.ReturnStatementAst] }, $true)).Count -eq 0) 'Pilot suppresses failures or emits return authority.'
    Assert-Test (-not [regex]::IsMatch($query, '\.(Dispose|Kill|Resume|PublishNew|StartSuspended)\(')) 'Pilot changes resource lifetime.'
}
Invoke-Case 'negative control rejects a substituted native response type' { Assert-Test (-not (Test-ClientNativeProof ($query.Replace('$response.GetType() -ne $responseType', '$false')))) 'Type negative control failed.' }
Invoke-Case 'negative control rejects missing exact original launch reference' { Assert-Test (-not (Test-ClientNativeProof ($query.Replace('[object]::ReferenceEquals($state.launch, $launch)', '$true')))) 'Launch negative control failed.' }
Invoke-Case 'negative control rejects missing request payload digest binding' { Assert-Test (-not (Test-ClientNativeProof ($query.Replace('$response.RequestPayloadSha256 -cne $payloadSha256', '$false')))) 'Digest negative control failed.' }
Invoke-Case 'negative control rejects widened roundtrip budget' { Assert-Test (-not (Test-ClientNativeProof ($query.Replace('-ge 1000', '-ge 2000')))) 'Deadline negative control failed.' }
Invoke-Case 'negative control rejects parser without pinned duplicate-key validation' { Assert-Test (-not (Test-ClientStrictParse ($query.Replace('$strictType::Validate($responseBytes)', '$null')))) 'Parser negative control failed.' }
Invoke-Case 'negative control rejects missing single-use native consumption' { Assert-Test (-not (Test-ClientConsumeOrder ($query.Replace('$response.Consume()', '$null')))) 'Consume negative control failed.' }
Invoke-Case 'negative control rejects missing post-consumption health check' {
    $consume = $query.IndexOf('$response.Consume()'); $post = $query.IndexOf('$launch.AssertControlHealthy()', $consume)
    Assert-Test (-not (Test-ClientConsumeOrder ($query.Remove($post, '$launch.AssertControlHealthy()'.Length)))) 'Post-health negative control failed.'
}
Invoke-Case 'negative control rejects pilot before retained ACK and missing WIP barrier' {
    Assert-Test (-not (Test-ClientPilot ($launchRequest.Replace('$state.transition_launch_ack = $ack', '$null')))) 'ACK negative control failed.'
    Assert-Test (-not (Test-ClientPilot ($launchRequest.Replace('throw ''Parent-owned Transition fresh-live/terminal/containment integration is WIP; no PostgreSQL start is authorized.''', '$null')))) 'WIP negative control failed.'
}
Invoke-Case 'negative control rejects lost one-shot attempt latch' { Assert-Test (-not (Test-ClientOneShot ($query.Replace('$state.transition_control_attempted = $true', '$null')))) 'Latch negative control failed.' }
Invoke-Case 'negative control rejects unbound launch ACK digest in request' { Assert-Test (-not (Test-ClientRequestBindings ($query.Replace('launch_ack_sha256 = $state.transition_launch_ack_sha256', 'launch_ack_sha256 = ''unbound''')))) 'ACK digest negative control failed.' }
if ($script:failures.Count -ne 0) { throw ('Safe extraction rejected: ' + ($script:failures -join '; ')) }
foreach ($name in $pureNames) { . ([scriptblock]::Create((Get-FunctionAst $name).Extent.Text)) }
$request = [pscustomobject][ordered]@{
    format = 'stratus-parent-transition-control-request-v1'; operation = 'observe-transition-live'
    parent_session_id = 'a' * 32; parent_lease_sha256 = 'b' * 64; transition_session_id = 'c' * 32
    launch_intent_sha256 = 'd' * 64; launch_ack_sha256 = 'e' * 64; transition_nonce = 'f' * 64
    transition_lease_sha256 = '1' * 64; transition_ready_sha256 = '2' * 64; prestart_sha256 = '3' * 64
    hard_deadline_utc = '2026-09-15T12:10:00.0000000+00:00'; boundary = 'early-transition-control-probe'
}
$responseFields = [ordered]@{}
foreach ($property in $request.PSObject.Properties) { $responseFields[$property.Name] = $property.Value }
$responseFields.format = 'stratus-parent-transition-control-response-v1'
$responseFields.process_id = [int]2345; $responseFields.process_started_at = '2026-09-15T12:00:00.0000000+00:00'
$responseFields.observed_at = '2026-09-15T12:01:00.1000000+00:00'; $responseFields.status = 'live'
$responseFields.authorization = [pscustomobject][ordered]@{
    postgres_start_permitted = $false; api_start_permitted = $false; worker_start_permitted = $false
    safety_control_start_permitted = $false; live_transition_permitted = $false; binance_kill_switch_removal_permitted = $false
    migration_container_permitted = $false; container_remove_permitted = $false
}
$response = [pscustomobject]$responseFields
$birth = $response.process_started_at
$requestAt = [DateTimeOffset]::Parse('2026-09-15T12:01:00.0000000+00:00', [Globalization.CultureInfo]::InvariantCulture)
$responseAt = $requestAt.AddMilliseconds(200)
function Assert-Response {
    Assert-ParentStageControlResponse -Value $response -Request $request -ProcessId ([int]2345) -ProcessStartedAt $birth `
        -RequestStartedAt $requestAt -ResponseReceivedAt $responseAt
}
function Assert-BadField {
    param([object] $Object, [string] $Name, [object[]] $Values, [scriptblock] $Check)
    $original = $Object.$Name
    try { foreach ($value in $Values) { $Object.$Name = $value; Assert-Rejected $Check } }
    finally { $Object.$Name = $original }
}
Invoke-Case 'request has exact thirteen ordered fields and strict compact UTF8 bytes' {
    $bytes = New-ParentStageControlRequestBytes $request
    $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    Assert-Test (($request.PSObject.Properties.Name -join ',') -ceq 'format,operation,parent_session_id,parent_lease_sha256,transition_session_id,launch_intent_sha256,launch_ack_sha256,transition_nonce,transition_lease_sha256,transition_ready_sha256,prestart_sha256,hard_deadline_utc,boundary') 'Request fieldset differs.'
    Assert-Test ($bytes -is [byte[]] -and $bytes.Length -le 4096 -and $bytes[0] -eq 123 -and $bytes[-1] -eq 125 -and
        -not $text.Contains("`r") -and -not $text.Contains("`n")) 'Request bytes differ.'
}
Invoke-Case 'valid eighteen-field response succeeds without emitting a capability' {
    Assert-Test (@($response.PSObject.Properties).Count -eq 18 -and @(Assert-Response).Count -eq 0) 'Valid response emitted authority or fieldset differs.'
}
Invoke-Case 'request root must be a native JSON object not a map or string' { Assert-Rejected { Assert-ParentStageControlRequest @{} }; Assert-Rejected { Assert-ParentStageControlRequest '{}' } }
Invoke-Case 'request format operation and boundary cannot select another protocol or stage' {
    foreach ($name in @('format','operation','boundary')) { Assert-BadField $request $name @('other','',123) { Assert-ParentStageControlRequest $request } }
}
Invoke-Case 'request session IDs reject uppercase trailing newline wrong length and coercion' {
    foreach ($name in @('parent_session_id','transition_session_id')) { Assert-BadField $request $name @(('A' * 32), ('a' * 31), (('a' * 32) + "`n"), 12) { Assert-ParentStageControlRequest $request } }
}
Invoke-Case 'every request digest and nonce requires exact lowercase sixty-four hex characters' {
    foreach ($name in @('parent_lease_sha256','launch_intent_sha256','launch_ack_sha256','transition_nonce','transition_lease_sha256','transition_ready_sha256','prestart_sha256')) {
        Assert-BadField $request $name @(('A' * 64), ('a' * 63), (('a' * 64) + "`n"), 123, $null) { Assert-ParentStageControlRequest $request }
    }
}
Invoke-Case 'request timestamp rejects typed local culture offset and noncanonical forms' {
    Assert-BadField $request 'hard_deadline_utc' @([DateTime]::UtcNow, '15/09/2026 12:10:00', '2026-09-15T12:10:00.0000000+01:00', '2026-09-15T12:10:00Z') { Assert-ParentStageControlRequest $request }
}
Invoke-Case 'request unknown missing and case-aliased properties are rejected' {
    $copy = [ordered]@{}; foreach ($p in $request.PSObject.Properties) { $copy[$p.Name] = $p.Value }
    $copy.extra = 'x'; Assert-Rejected { Assert-ParentStageControlRequest ([pscustomobject]$copy) }; $copy.Remove('extra')
    $copy.Remove('boundary'); Assert-Rejected { Assert-ParentStageControlRequest ([pscustomobject]$copy) }
    $copy.Boundary = $request.boundary; Assert-Rejected { Assert-ParentStageControlRequest ([pscustomobject]$copy) }
}
Invoke-Case 'response root and unknown property cannot masquerade as native strict JSON' {
    Assert-Rejected { Assert-ParentStageControlResponse @{} $request 2345 $birth $requestAt $responseAt }
    $response | Add-Member -NotePropertyName 'extra' -NotePropertyValue 'x'
    try { Assert-Rejected { Assert-Response } } finally { $response.PSObject.Properties.Remove('extra') }
}
Invoke-Case 'all twelve echoed request values reject another retained context' {
    foreach ($p in $request.PSObject.Properties) {
        if ($p.Name -cne 'format') { Assert-BadField $response $p.Name @('different') { Assert-Response } }
    }
}
Invoke-Case 'response format and status cannot imply terminal or another protocol' {
    Assert-BadField $response 'format' @($request.format,'other') { Assert-Response }
    Assert-BadField $response 'status' @('terminal','Live',$true,'') { Assert-Response }
}
Invoke-Case 'response PID rejects coercion fractions zero oversized and another exact process' {
    Assert-BadField $response 'process_id' @('2345',2345.0,0,[long]2147483648,2346,$true) { Assert-Response }
}
Invoke-Case 'expected PID cannot be coerced and native long representation remains supported' {
    Assert-Rejected { Assert-ParentStageControlResponse $response $request '2345' $birth $requestAt $responseAt }
    Assert-ParentStageControlResponse $response $request ([long]2345) $birth $requestAt $responseAt
}
Invoke-Case 'response process birth must match exact captured canonical instant' {
    Assert-BadField $response 'process_started_at' @('2026-09-15T12:00:00.0000001+00:00', '2026-09-15T12:00:00Z', [DateTime]::UtcNow) { Assert-Response }
}
Invoke-Case 'observation timestamps reject typed dates cultures offsets and missing precision' {
    Assert-BadField $response 'observed_at' @([DateTime]::UtcNow, '15.09.2026 12:01:00', '2026-09-15T13:01:00.1000000+01:00', '2026-09-15T12:01:00Z') { Assert-Response }
}
Invoke-Case 'observation before birth at hard deadline or outside current wall interval is rejected' {
    Assert-BadField $response 'observed_at' @('2026-09-15T11:59:59.0000000+00:00', $request.hard_deadline_utc,
        '2026-09-15T12:00:57.9999999+00:00', '2026-09-15T12:01:02.2000001+00:00') { Assert-Response }
}
Invoke-Case 'backward local roundtrip clock rejects rather than claiming freshness' { Assert-Rejected { Assert-ParentStageControlResponse $response $request 2345 $birth $responseAt $requestAt } }
Invoke-Case 'every authorization flag must be false not a granted or coerced boolean' {
    foreach ($p in $response.authorization.PSObject.Properties) { Assert-BadField $response.authorization $p.Name @($true,'false',0,$null) { Assert-Response } }
}
Invoke-Case 'authorization root and unknown fields reject permissive projections' {
    Assert-BadField $response 'authorization' @(@{},'{}',$null) { Assert-Response }
    $response.authorization | Add-Member -NotePropertyName extra -NotePropertyValue $false
    try { Assert-Rejected { Assert-Response } } finally { $response.authorization.PSObject.Properties.Remove('extra') }
}
Invoke-Case 'schema string guard rejects actual CR LF NUL and oversized strings' {
    foreach ($value in @("bad`r", "bad`n", ("bad" + [char]0), ('x' * 4097))) {
        Assert-Rejected { Assert-ParentStageControlFields -Value ([pscustomobject]@{ field = $value }) -Strings @('field') }
    }
}
Invoke-Case 'canonical timestamp parser is invariant under a non-English thread culture' {
    $thread = [Threading.Thread]::CurrentThread; $original = $thread.CurrentCulture
    try {
        $thread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('de-DE')
        Assert-Response; Assert-ParentStageControlRequest $request
    } finally { $thread.CurrentCulture = $original }
}
Invoke-Case 'request serialization rejects invalid data before building bounded bytes' {
    Assert-BadField $request 'boundary' @(('x' * 5000), "bad`n") { New-ParentStageControlRequestBytes $request }
}
Invoke-Case 'false-only payload validation emits nothing and cannot manufacture native response objects' {
    Assert-Test (@(Assert-Response).Count -eq 0) 'Pure response emitted a capability.'
    $fn = Get-FunctionAst 'Assert-ParentStageControlResponse'
    Assert-Test (-not $fn.Extent.Text.Contains('PrivateControlResponse') -and -not $fn.Extent.Text.Contains('Consume(')) 'Pure validator creates or consumes native authority.'
}
Invoke-Case 'negative control empty request validator loses required malformed-input rejection' {
    $empty = [scriptblock]::Create('param([object] $Value)')
    Assert-Test (@(& $empty ([pscustomobject]@{ format = 'wrong' })).Count -eq 0) 'Empty validator control setup differs.'
    Assert-Rejected { Assert-ParentStageControlRequest ([pscustomobject]@{ format = 'wrong' }) }
}
Invoke-Case 'original bootstrap uses exact control-enabled factory while old method stays uncalled' {
    $bootstrap = (Get-FunctionAst 'Start-ParentStageBootstrap').Extent.Text
    Assert-Test ($bootstrap.Contains('$launchType::StartSuspendedWithControl(') -and -not $bootstrap.Contains('$launchType::StartSuspended(')) 'Bootstrap does not provision the private channel.'
}
if ($script:executed -ne $expectedCases) { throw ('Case count differs: expected ' + $expectedCases + ', actual ' + $script:executed) }
Write-Output ('Parent control client tests: ' + $script:passed + '/' + $expectedCases + ' passed. Scope: pure managed and AST only; no native/control/body execution.')
if ($script:failures.Count -ne 0) { throw ($script:failures -join '; ') }
