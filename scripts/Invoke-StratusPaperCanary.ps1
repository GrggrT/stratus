[CmdletBinding()]
param(
    [string] $Root = '.',

    [Uri] $BaseUri = 'http://127.0.0.1:8000/',

    [Uri] $SafetyBaseUri = 'http://127.0.0.1:8001/',

    [Security.SecureString] $ApiKey,

    [Security.SecureString] $SafetyApiKey,

    [Parameter(Mandatory = $false)]
    [ValidatePattern('^[A-Za-z0-9._:-]{8,64}$')]
    [string] $ExpectedResumeRequestId,

    [Parameter(Mandatory = $false)]
    [ValidateLength(1, 64)]
    [string] $ExpectedResumeActor = 'codex-release-owner',

    [Parameter(Mandatory = $false)]
    [ValidateLength(1, 500)]
    [string] $ExpectedResumeReason = (
        'Successor epoch and cold scheduler canary accepted; ' +
        'resume Polymarket PAPER only.'
    ),

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, [long]::MaxValue)]
    [long] $ExpectedEpochId,

    [Parameter(Mandatory = $false)]
    [ValidatePattern('^[a-z0-9][a-z0-9._+-]{1,191}$')]
    [string] $ExpectedCohort,

    [Parameter(Mandatory = $false)]
    [ValidatePattern('^sha256:[0-9a-f]{64}$')]
    [string] $ExpectedImageId,

    [Parameter(Mandatory = $false)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedRevision,

    [Parameter(Mandatory = $false)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedReleaseDigest,

    [ValidateSet('20260730_0047')]
    [string] $ExpectedSchemaHead = '20260730_0047',

    [ValidateCount(1, 12)]
    [int[]] $MinuteOffsets = @(0, 5, 15, 30, 45, 60),

    [ValidateRange(5, 10)]
    [int] $GuardIntervalSeconds = 5,

    [ValidateRange(1, 3)]
    [int] $HttpTimeoutSeconds = 3,

    [ValidateRange(30, 900)]
    [int] $ResumeWaitSeconds = 300,

    [ValidateRange(15, 180)]
    [int] $StopConfirmationSeconds = 60,

    [ValidateRange(65536, 2097152)]
    [int] $MaxResponseBytes = 1048576,

    [ValidateRange(3, 20)]
    [int] $NativeTimeoutSeconds = 12,

    [ValidatePattern('^[0-9a-fA-F]{32}$')]
    [string] $CanaryRunId,

    [string] $OutputRoot = '.stratus-cache\paper-canary',

    [switch] $StopPolymarketOnDivergence,

    [switch] $ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

$ExpectedContainerMemoryLimits = [ordered]@{
    api = 1073741824L
    safety_control = 268435456L
    worker = 3221225472L
}

$EndpointInventory = @(
    [pscustomobject]@{ Name = 'healthz'; Path = 'healthz' }
    [pscustomobject]@{ Name = 'readyz'; Path = 'readyz' }
    [pscustomobject]@{ Name = 'overview'; Path = 'api/v1/overview' }
    [pscustomobject]@{ Name = 'venues'; Path = 'api/v1/venues' }
    [pscustomobject]@{ Name = 'commands'; Path = 'api/v1/commands?limit=200' }
    [pscustomobject]@{ Name = 'jobs'; Path = 'api/v1/jobs?limit=200' }
    [pscustomobject]@{
        Name = 'continuity'
        Path = 'api/v1/evidence-continuity'
    }
    [pscustomobject]@{
        Name = 'execution_cost'
        Path = 'api/v1/execution-cost-report?mode=paper'
    }
    [pscustomobject]@{
        Name = 'validation_ledger'
        Path = 'api/v1/validation-ledger?mode=paper'
    }
)

function Assert-CanaryArguments {
    foreach ($uriItem in @(
        [pscustomobject]@{ Name = 'BaseUri'; Value = $BaseUri; Port = 8000 },
        [pscustomobject]@{ Name = 'SafetyBaseUri'; Value = $SafetyBaseUri; Port = 8001 }
    )) {
        $uri = [Uri]$uriItem.Value
        if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'http') {
            throw "$($uriItem.Name) must be an absolute HTTP URI."
        }
        if (
            -not $uri.IsLoopback -or
            $uri.Host -ne '127.0.0.1' -or
            -not [string]::IsNullOrEmpty($uri.Query) -or
            -not [string]::IsNullOrEmpty($uri.Fragment) -or
            -not [string]::IsNullOrEmpty($uri.UserInfo) -or
            $uri.AbsolutePath -ne '/' -or
            $uri.Port -ne [int]$uriItem.Port
        ) {
            throw (
                "$($uriItem.Name) must be the exact loopback port " +
                "$($uriItem.Port) with a trailing slash."
            )
        }
    }
    $offsets = @($MinuteOffsets)
    if ($offsets[0] -ne 0) {
        throw 'MinuteOffsets must start at zero.'
    }
    if ($offsets[-1] -gt 180) {
        throw 'MinuteOffsets may cover at most 180 minutes.'
    }
    if (@($offsets | Sort-Object -Unique).Count -ne $offsets.Count) {
        throw 'MinuteOffsets must be unique.'
    }
    for ($index = 0; $index -lt $offsets.Count; $index++) {
        if ($offsets[$index] -lt 0) {
            throw 'MinuteOffsets may not be negative.'
        }
        if ($index -gt 0 -and $offsets[$index] -le $offsets[$index - 1]) {
            throw 'MinuteOffsets must be strictly increasing.'
        }
    }
    if (($offsets -join ',') -ne '0,5,15,30,45,60') {
        throw 'Release PAPER canary offsets must be exactly 0,5,15,30,45,60.'
    }
    if (
        $GuardIntervalSeconds -ne 5 -or
        $HttpTimeoutSeconds -ne 3 -or
        $ResumeWaitSeconds -ne 300 -or
        $StopConfirmationSeconds -ne 60 -or
        $MaxResponseBytes -ne 1048576 -or
        $NativeTimeoutSeconds -ne 12
    ) {
        throw 'Release PAPER canary safety budgets must use their exact production values.'
    }
    if (-not $StopPolymarketOnDivergence) {
        throw 'Release PAPER canary requires the Polymarket-only emergency stop to be armed.'
    }
}

Assert-CanaryArguments

$validation = [ordered]@{
    schema_version = 1
    collector = 'stratus_polymarket_paper_canary'
    validated = $true
    validation_only_has_no_io = $true
    starts_before_resume = $true
    anchor = 'exact_resume_request_executed_at_plus_first_authoritative_observation'
    minute_offsets = @($MinuteOffsets)
    guard_interval_seconds = $GuardIntervalSeconds
    max_snapshots = 6
    max_duration_minutes = 60
    max_response_bytes = $MaxResponseBytes
    native_timeout_seconds = $NativeTimeoutSeconds
    loopback_only = $true
    proxy_allowed = $false
    redirects_allowed = $false
    immutable_create_new_artifacts = $true
    hash_chained_snapshots = $true
    endpoint_inventory = @($EndpointInventory)
    direct_sql = [ordered]@{
        isolation = 'REPEATABLE READ READ ONLY'
        database_clock = 'clock_timestamp()'
        complete_job_window = $true
        live_tables = @(
            'orders',
            'positions',
            'execution_order_intents',
            'execution_fill_legs',
            'prospective_validation_enrollments',
            'prospective_validation_outcomes'
        )
    }
    expected_controls = [ordered]@{
        global = 'paper, kill=false'
        polymarket = 'enabled=true, paper, kill=false'
        binance_spot = 'enabled=true, backtest, kill=true'
    }
    mutation_default = 'none'
    emergency_mutation_requires_switch = 'StopPolymarketOnDivergence'
    emergency_stop_must_be_armed = $true
    emergency_stop_after_accepted_preflight_failure = $true
    emergency_max_post_attempts = 2
    distinct_control_plane_credentials = $true
    scheduler_lock_contention_identity_detection = $true
    exhaustive_running_project_topology = $true
    continuous_network_topology_guard = $true
    exact_database_session_identity = $true
    safety_lease = [ordered]@{
        database_clock = $true
        single_active = $true
        ttl_seconds_max = 30
        heartbeat_interval_seconds = 5
        heartbeat_checkpoints_between_multicall_io = $true
        independent_watchdog = $true
        resume_request_bound = $true
        nonce_not_persisted = $true
        divergence_stop_revokes_atomically = $true
        success_release_stops_atomically = $true
    }
    emergency_command = [ordered]@{
        command = 'stop'
        payload = [ordered]@{ venue = 'polymarket' }
        idempotent_request_id = $true
        exact_response_identity_required = $true
        exact_sql_poll_required = $true
        post_stop_controls_required = $true
        first_completed_post_must_create = $true
        ambiguous_post_database_recovery = $true
        bounded_same_id_retry = $true
        cleanup_state_sql_required = $true
        same_id_cleanup_replay = $true
        cleanup_retry_bounded_by_confirmation_window = $true
    }
    forbidden_actions = @(
        'resume',
        'set_mode',
        'live',
        'venue=all',
        'venue=binance_spot',
        'container lifecycle mutation',
        'Docker daemon restart',
        'direct database write'
    )
}
if ($ValidateOnly) {
    $validation | ConvertTo-Json -Depth 8 -Compress
    return
}

foreach ($required in @(
    'ExpectedResumeRequestId',
    'ExpectedResumeActor',
    'ExpectedResumeReason',
    'ExpectedCohort',
    'ExpectedImageId',
    'ExpectedRevision',
    'ExpectedReleaseDigest'
)) {
    if ([string]::IsNullOrWhiteSpace([string](Get-Variable -Name $required -ValueOnly))) {
        throw "$required is required outside validation mode."
    }
}
if ($ExpectedEpochId -lt 1) {
    throw 'ExpectedEpochId is required outside validation mode.'
}

function ConvertFrom-SecureStringValue {
    param([Parameter(Mandatory = $true)][Security.SecureString] $Secret)

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function New-AuthenticatedLoopbackClient {
    param(
        [Parameter(Mandatory = $true)][Uri] $Uri,
        [Parameter(Mandatory = $true)][Security.SecureString] $Secret
    )

    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseProxy = $false
    $httpClient = [Net.Http.HttpClient]::new($handler, $true)
    $httpClient.BaseAddress = $Uri
    $httpClient.Timeout = [Threading.Timeout]::InfiniteTimeSpan
    $plainKey = ConvertFrom-SecureStringValue -Secret $Secret
    try {
        if (
            -not $httpClient.DefaultRequestHeaders.TryAddWithoutValidation(
                'X-API-Key',
                $plainKey
            )
        ) {
            throw 'Could not configure the in-memory API authentication header.'
        }
    }
    catch {
        $httpClient.Dispose()
        throw
    }
    finally {
        $plainKey = $null
    }
    return $httpClient
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][byte[]] $Bytes)

    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return (
            [BitConverter]::ToString($hasher.ComputeHash($Bytes))
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}

function New-SecureCanaryNonce {
    $bytes = [byte[]]::new(32)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace(
            '+',
            '-'
        ).Replace('/', '_')
    }
    finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
        $generator.Dispose()
    }
}

function Write-ImmutableJson {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][object] $Payload
    )

    $payloadJson = $Payload | ConvertTo-Json -Depth 30 -Compress
    $payloadBytes = [Text.UTF8Encoding]::new($false).GetBytes($payloadJson)
    $envelope = [ordered]@{
        schema_version = 1
        payload_sha256 = Get-Sha256 -Bytes $payloadBytes
        payload = $Payload
    }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(
        ($envelope | ConvertTo-Json -Depth 32)
    )
    $stream = [IO.FileStream]::new(
        $Path,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::Read
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    return [pscustomobject]@{
        path = $Path
        bytes = $bytes.Length
        sha256 = Get-Sha256 -Bytes $bytes
    }
}

function Read-BoundedBody {
    param(
        [Parameter(Mandatory = $true)][Net.Http.HttpResponseMessage] $Response,
        [Parameter(Mandatory = $true)][Threading.CancellationToken] $Token
    )

    $declared = $Response.Content.Headers.ContentLength
    if ($null -ne $declared -and $declared -gt $MaxResponseBytes) {
        throw "HTTP response exceeds the $MaxResponseBytes-byte limit."
    }
    $inputStream = $Response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $outputStream = [IO.MemoryStream]::new()
    $buffer = [byte[]]::new(8192)
    try {
        while ($true) {
            $read = $inputStream.ReadAsync(
                $buffer,
                0,
                $buffer.Length,
                $Token
            ).GetAwaiter().GetResult()
            if ($read -eq 0) {
                break
            }
            if ($outputStream.Length + $read -gt $MaxResponseBytes) {
                throw "HTTP response exceeds the $MaxResponseBytes-byte limit."
            }
            $outputStream.Write($buffer, 0, $read)
        }
        return $outputStream.ToArray()
    }
    finally {
        $outputStream.Dispose()
        $inputStream.Dispose()
    }
}

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory = $true)][Net.Http.HttpClient] $Client,
        [Parameter(Mandatory = $true)][ValidateSet('GET', 'POST')][string] $Method,
        [Parameter(Mandatory = $true)][string] $Path,
        [string] $JsonBody
    )

    $httpMethod = if ($Method -eq 'GET') {
        [Net.Http.HttpMethod]::Get
    }
    else {
        [Net.Http.HttpMethod]::Post
    }
    $request = [Net.Http.HttpRequestMessage]::new($httpMethod, $Path)
    if ($Method -eq 'POST') {
        $request.Content = [Net.Http.StringContent]::new(
            $JsonBody,
            [Text.Encoding]::UTF8,
            'application/json'
        )
    }
    $cancellation = [Threading.CancellationTokenSource]::new()
    $response = $null
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $cancellation.CancelAfter([TimeSpan]::FromSeconds($HttpTimeoutSeconds))
        $response = $Client.SendAsync(
            $request,
            [Net.Http.HttpCompletionOption]::ResponseHeadersRead,
            $cancellation.Token
        ).GetAwaiter().GetResult()
        $bytes = Read-BoundedBody -Response $response -Token $cancellation.Token
        $text = [Text.Encoding]::UTF8.GetString($bytes)
        if ([string]::IsNullOrWhiteSpace($text)) {
            throw "HTTP $Method $Path returned an empty JSON response."
        }
        if ($text.TrimStart()[0] -ne '{') {
            throw "HTTP $Method $Path response root must be a JSON object."
        }
        $json = $text | ConvertFrom-Json
        if ($json -isnot [pscustomobject]) {
            throw "HTTP $Method $Path response root must be a JSON object."
        }
        return [pscustomobject]@{
            status_code = [int]$response.StatusCode
            elapsed_ms = [Math]::Round($watch.Elapsed.TotalMilliseconds, 3)
            bytes = $bytes.Length
            content_sha256 = Get-Sha256 -Bytes $bytes
            json = $json
        }
    }
    finally {
        $watch.Stop()
        if ($null -ne $response) {
            $response.Dispose()
        }
        $request.Dispose()
        $cancellation.Dispose()
    }
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string] $Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($slashes * 2) + 1)))
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) {
            [void]$builder.Append(('\' * $slashes))
            $slashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-BoundedNative {
    param(
        [Parameter(Mandatory = $true)][string] $FileName,
        [Parameter(Mandatory = $true)][string[]] $Arguments,
        [string] $StandardInput,
        [ValidateRange(1, 30)][int] $TimeoutSeconds = $NativeTimeoutSeconds,
        [ValidateRange(65536, 4194304)][int] $MaxOutputBytes = 2097152
    )

    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FileName
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardInput = $null -ne $StandardInput
    $argumentListProperty = $start.PSObject.Properties['ArgumentList']
    if ($null -ne $argumentListProperty) {
        foreach ($argument in $Arguments) {
            [void]$start.ArgumentList.Add($argument)
        }
    }
    else {
        $start.Arguments = (
            @($Arguments | ForEach-Object { ConvertTo-NativeArgument -Value $_ }) -join ' '
        )
    }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $cancellation = [Threading.CancellationTokenSource]::new()
    $stdoutBytes = [IO.MemoryStream]::new()
    $stderrBytes = [IO.MemoryStream]::new()
    $stdoutBuffer = [byte[]]::new(8192)
    $stderrBuffer = [byte[]]::new(8192)
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $started = $false
    $completed = $false
    try {
        if (-not $process.Start()) {
            throw "Could not start bounded native process: $FileName."
        }
        $started = $true
        $stdoutTask = $process.StandardOutput.BaseStream.ReadAsync(
            $stdoutBuffer,
            0,
            $stdoutBuffer.Length,
            $cancellation.Token
        )
        $stderrTask = $process.StandardError.BaseStream.ReadAsync(
            $stderrBuffer,
            0,
            $stderrBuffer.Length,
            $cancellation.Token
        )
        $stdoutDone = $false
        $stderrDone = $false
        $stdinDone = $null -eq $StandardInput
        $stdinTask = $null
        if ($null -ne $StandardInput) {
            $stdinBytes = [Text.UTF8Encoding]::new($false).GetBytes($StandardInput)
            $stdinTask = $process.StandardInput.BaseStream.WriteAsync(
                $stdinBytes,
                0,
                $stdinBytes.Length,
                $cancellation.Token
            )
        }

        while ($true) {
            if ($watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                throw "Native command exceeded ${TimeoutSeconds}s: $FileName."
            }

            if (-not $stdinDone -and $null -ne $stdinTask -and $stdinTask.IsCompleted) {
                [void]$stdinTask.GetAwaiter().GetResult()
                $process.StandardInput.Close()
                $stdinDone = $true
            }

            if (-not $stdoutDone -and $stdoutTask.IsCompleted) {
                $read = $stdoutTask.GetAwaiter().GetResult()
                if ($read -eq 0) {
                    $stdoutDone = $true
                }
                else {
                    if ($stdoutBytes.Length + $stderrBytes.Length + $read -gt $MaxOutputBytes) {
                        throw "Native command output exceeded $MaxOutputBytes bytes: $FileName."
                    }
                    $stdoutBytes.Write($stdoutBuffer, 0, $read)
                    $stdoutTask = $process.StandardOutput.BaseStream.ReadAsync(
                        $stdoutBuffer,
                        0,
                        $stdoutBuffer.Length,
                        $cancellation.Token
                    )
                }
            }

            if (-not $stderrDone -and $stderrTask.IsCompleted) {
                $read = $stderrTask.GetAwaiter().GetResult()
                if ($read -eq 0) {
                    $stderrDone = $true
                }
                else {
                    if ($stdoutBytes.Length + $stderrBytes.Length + $read -gt $MaxOutputBytes) {
                        throw "Native command output exceeded $MaxOutputBytes bytes: $FileName."
                    }
                    $stderrBytes.Write($stderrBuffer, 0, $read)
                    $stderrTask = $process.StandardError.BaseStream.ReadAsync(
                        $stderrBuffer,
                        0,
                        $stderrBuffer.Length,
                        $cancellation.Token
                    )
                }
            }

            if ($process.HasExited -and $stdinDone -and $stdoutDone -and $stderrDone) {
                break
            }
            Start-Sleep -Milliseconds 10
        }

        $stdout = [Text.Encoding]::UTF8.GetString($stdoutBytes.ToArray())
        $stderr = [Text.Encoding]::UTF8.GetString($stderrBytes.ToArray())
        if ($process.ExitCode -ne 0) {
            $boundedError = if ($stderr.Length -gt 2000) {
                $stderr.Substring(0, 2000)
            }
            else {
                $stderr
            }
            throw "Native command failed ($($process.ExitCode)): $FileName. $boundedError"
        }
        $result = [pscustomobject]@{
            exit_code = $process.ExitCode
            stdout = $stdout
            stderr = $stderr
        }
        $completed = $true
        return $result
    }
    finally {
        $watch.Stop()
        if (-not $completed -and $started) {
            try {
                $cancellation.Cancel()
            }
            catch {
                # Best effort; process termination below remains authoritative.
            }
            try {
                $process.StandardInput.Close()
            }
            catch {
                # The process may already have closed its stdin.
            }
            try {
                if (-not $process.HasExited) {
                    $process.Kill()
                    [void]$process.WaitForExit(2000)
                }
            }
            catch {
                # Preserve the original bounded-process failure.
            }
        }
        $cancellation.Dispose()
        $stdoutBytes.Dispose()
        $stderrBytes.Dispose()
        $process.Dispose()
    }
}

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]] $Arguments)

    $nativeArguments = @(
        'compose',
        '--project-directory',
        $absoluteRoot,
        '-f',
        $composePath,
        '--env-file',
        $composeEnvPath,
        '--project-name',
        'weatherpoly'
    ) + @($Arguments)
    $result = Invoke-BoundedNative -FileName 'docker' -Arguments $nativeArguments
    return @(
        $result.stdout -split '\r?\n' |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
}

function Get-ServiceContainerId {
    param([Parameter(Mandatory = $true)][string] $Service)

    $ids = @(
        Invoke-Compose -Arguments @('ps', '--all', '-q', $Service) |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($ids.Count -ne 1) {
        throw "Expected exactly one $Service container; observed $($ids.Count)."
    }
    return [string]$ids[0]
}

function Get-OptionalProperty {
    param(
        [AllowNull()][object] $InputObject,
        [Parameter(Mandatory = $true)][string] $Name
    )

    if ($null -eq $InputObject) {
        return $null
    }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Assert-NativeJsonObject {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Context
    )

    if ($null -eq $Value -or $Value -isnot [pscustomobject]) {
        throw "$Context must be a native JSON object."
    }
}

function Assert-RequiredJsonObjectProperty {
    param(
        [Parameter(Mandatory = $true)][object] $InputObject,
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Context
    )

    Assert-NativeJsonObject -Value $InputObject -Context $Context
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property -or $property.Value -isnot [pscustomobject]) {
        throw "$Context.$Name must be a native JSON object."
    }
}

function Assert-NativeJsonBooleanProperty {
    param(
        [Parameter(Mandatory = $true)][object] $InputObject,
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Context,
        [switch] $Optional
    )

    Assert-NativeJsonObject -Value $InputObject -Context $Context
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        if ($Optional) {
            return
        }
        throw "$Context.$Name is required."
    }
    if ($property.Value -isnot [bool]) {
        throw "$Context.$Name must be a native JSON boolean."
    }
}

function Assert-NativeJsonIntegerProperty {
    param(
        [Parameter(Mandatory = $true)][object] $InputObject,
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Context
    )

    Assert-NativeJsonObject -Value $InputObject -Context $Context
    $property = $InputObject.PSObject.Properties[$Name]
    if (
        $null -eq $property -or
        ($property.Value -isnot [int] -and $property.Value -isnot [long])
    ) {
        throw "$Context.$Name must be a native JSON integer."
    }
}

function Assert-NativeJsonArrayProperty {
    param(
        [Parameter(Mandatory = $true)][object] $InputObject,
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Context
    )

    Assert-NativeJsonObject -Value $InputObject -Context $Context
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property -or $property.Value -isnot [array]) {
        throw "$Context.$Name must be a native JSON array."
    }
}

function Assert-ApiJsonResponseShape {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [AllowNull()][object] $Json
    )

    $context = "API $Name response"
    Assert-NativeJsonObject -Value $Json -Context $context
    switch ($Name) {
        'healthz' {
            return
        }
        'readyz' {
            Assert-NativeJsonArrayProperty -InputObject $Json -Name 'reasons' `
                -Context $context
            return
        }
        'overview' {
            Assert-RequiredJsonObjectProperty -InputObject $Json -Name 'runtime' `
                -Context $context
            Assert-NativeJsonBooleanProperty -InputObject $Json.runtime `
                -Name 'kill_switch' -Context "$context.runtime"
            foreach ($propertyName in @(
                'active_markets',
                'blocked_markets',
                'open_positions',
                'pending_signals'
            )) {
                Assert-NativeJsonIntegerProperty -InputObject $Json `
                    -Name $propertyName -Context $context
            }
            return
        }
        'venues' {
            Assert-NativeJsonArrayProperty -InputObject $Json -Name 'items' `
                -Context $context
            foreach ($item in @($Json.items)) {
                Assert-NativeJsonObject -Value $item -Context "$context.items[]"
                Assert-NativeJsonBooleanProperty -InputObject $item `
                    -Name 'enabled' -Context "$context.items[]"
                Assert-NativeJsonBooleanProperty -InputObject $item `
                    -Name 'kill_switch' -Context "$context.items[]"
            }
            return
        }
        { $_ -in @('commands', 'jobs') } {
            Assert-NativeJsonArrayProperty -InputObject $Json -Name 'items' `
                -Context $context
            foreach ($item in @($Json.items)) {
                Assert-NativeJsonObject -Value $item -Context "$context.items[]"
                Assert-NativeJsonIntegerProperty -InputObject $item -Name 'id' `
                    -Context "$context.items[]"
            }
            return
        }
        'continuity' {
            Assert-RequiredJsonObjectProperty -InputObject $Json `
                -Name 'active_epoch' -Context $context
            Assert-RequiredJsonObjectProperty -InputObject $Json `
                -Name 'collection_window' -Context $context
            Assert-NativeJsonIntegerProperty -InputObject $Json.active_epoch `
                -Name 'id' -Context "$context.active_epoch"
            foreach ($propertyName in @(
                'cohort_match',
                'image_match',
                'execution_gate_allowed'
            )) {
                Assert-NativeJsonBooleanProperty -InputObject $Json `
                    -Name $propertyName -Context $context
            }
            foreach ($propertyName in @(
                'frozen_lanes',
                'lane_identity_mismatches',
                'continuity_lanes'
            )) {
                Assert-NativeJsonArrayProperty -InputObject $Json `
                    -Name $propertyName -Context $context
            }
            Assert-NativeJsonArrayProperty -InputObject $Json.collection_window `
                -Name 'missing_scheduler_jobs' `
                -Context "$context.collection_window"
            Assert-NativeJsonIntegerProperty -InputObject $Json.collection_window `
                -Name 'stalled_days' -Context "$context.collection_window"
            foreach ($lane in @($Json.continuity_lanes)) {
                Assert-NativeJsonObject -Value $lane `
                    -Context "$context.continuity_lanes[]"
                Assert-NativeJsonIntegerProperty -InputObject $lane `
                    -Name 'epoch_id' -Context "$context.continuity_lanes[]"
            }
            return
        }
        'execution_cost' {
            foreach ($propertyName in @(
                'attempts',
                'filled_attempts',
                'no_fill_attempts',
                'pending_attempts',
                'sealed_attempts',
                'maker_attempts',
                'maker_fills'
            )) {
                Assert-NativeJsonIntegerProperty -InputObject $Json `
                    -Name $propertyName -Context $context
            }
            return
        }
        'validation_ledger' {
            Assert-NativeJsonBooleanProperty -InputObject $Json `
                -Name 'selected_is_current' -Context $context
            foreach ($propertyName in @('enrolled_orders', 'pending_outcomes')) {
                Assert-NativeJsonIntegerProperty -InputObject $Json `
                    -Name $propertyName -Context $context
            }
            return
        }
        default {
            throw "Unknown API JSON response shape: $Name."
        }
    }
}

function Assert-SafetyHealthJsonResponseShape {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [AllowNull()][object] $Json
    )

    $context = "safety-control $Name response"
    Assert-NativeJsonObject -Value $Json -Context $context
    if ($Name -eq 'readyz') {
        Assert-NativeJsonArrayProperty -InputObject $Json -Name 'reasons' `
            -Context $context
    }
    elseif ($Name -ne 'healthz') {
        throw "Unknown safety-control JSON response shape: $Name."
    }
}

function Assert-SafetyMutationJsonResponseShape {
    param(
        [AllowNull()][object] $Json,
        [Parameter(Mandatory = $true)][string] $Context,
        [Parameter(Mandatory = $true)]
        [ValidateSet('lease-success', 'stop-success', 'error')]
        [string] $Schema
    )

    Assert-NativeJsonObject -Value $Json -Context $Context
    $actualProperties = @($Json.PSObject.Properties.Name | Sort-Object)
    if ($Schema -eq 'error') {
        $actualInventory = $actualProperties -join ','
        if ($actualInventory -notin @('detail', 'detail,retryable')) {
            throw "$Context has an unexpected error response property inventory."
        }
        Assert-NativeJsonBooleanProperty -InputObject $Json -Name 'retryable' `
            -Context $Context -Optional
        return
    }

    $expectedProperties = if ($Schema -eq 'lease-success') {
        @('created', 'idempotent', 'item')
    }
    else {
        @(
            'cancel_requested_live_orders',
            'created',
            'expired_signals',
            'idempotent',
            'item',
            'rejected_pending_resume'
        )
    }
    if (($actualProperties -join ',') -ne (($expectedProperties | Sort-Object) -join ',')) {
        throw "$Context has an unexpected success response property inventory."
    }
    Assert-NativeJsonBooleanProperty -InputObject $Json -Name 'created' `
        -Context $Context
    Assert-NativeJsonBooleanProperty -InputObject $Json -Name 'idempotent' `
        -Context $Context
    Assert-RequiredJsonObjectProperty -InputObject $Json -Name 'item' `
        -Context $Context
    if ($Json.created -eq $Json.idempotent) {
        throw "$Context created/idempotent flags are not complementary."
    }
    if ($Schema -eq 'stop-success') {
        foreach ($propertyName in @(
            'expired_signals',
            'cancel_requested_live_orders',
            'rejected_pending_resume'
        )) {
            Assert-NativeJsonIntegerProperty -InputObject $Json `
                -Name $propertyName -Context $Context
        }
    }
}

function Get-ContainerSnapshot {
    param(
        [Parameter(Mandatory = $true)][string] $Service,
        [Parameter(Mandatory = $true)][string] $ContainerId
    )

    $result = Invoke-BoundedNative -FileName 'docker' -Arguments @(
        'inspect',
        $ContainerId
    )
    $rows = @($result.stdout | ConvertFrom-Json)
    if ($rows.Count -ne 1) {
        throw "docker inspect returned an invalid container count for $Service."
    }
    $container = $rows[0]
    $state = Get-OptionalProperty -InputObject $container -Name 'State'
    $config = Get-OptionalProperty -InputObject $container -Name 'Config'
    $hostConfig = Get-OptionalProperty -InputObject $container -Name 'HostConfig'
    if ($null -eq $state -or $null -eq $config -or $null -eq $hostConfig) {
        throw "docker inspect omitted required state/config/host config for $Service."
    }
    $labels = Get-OptionalProperty -InputObject $config -Name 'Labels'
    $environment = @(Get-OptionalProperty -InputObject $config -Name 'Env')
    $releaseDigest = @(
        $environment |
            Where-Object { [string]$_ -like 'RELEASE_IMAGE_DIGEST=*' }
    )
    $publishedPorts = [Collections.Generic.List[object]]::new()
    $networkSettings = Get-OptionalProperty -InputObject $container -Name 'NetworkSettings'
    $ports = Get-OptionalProperty -InputObject $networkSettings -Name 'Ports'
    $networkMap = Get-OptionalProperty -InputObject $networkSettings -Name 'Networks'
    $clientAddresses = [Collections.Generic.List[string]]::new()
    $networkNames = if ($null -eq $networkMap) {
        @()
    }
    else {
        @($networkMap.PSObject.Properties | ForEach-Object Name | Sort-Object)
    }
    if ($null -ne $networkMap) {
        foreach ($networkProperty in @($networkMap.PSObject.Properties)) {
            foreach ($addressProperty in @('IPAddress', 'GlobalIPv6Address')) {
                $rawAddress = [string](
                    Get-OptionalProperty `
                        -InputObject $networkProperty.Value -Name $addressProperty
                )
                if ([string]::IsNullOrWhiteSpace($rawAddress)) {
                    continue
                }
                $parsedAddress = $null
                if (-not [Net.IPAddress]::TryParse($rawAddress, [ref]$parsedAddress)) {
                    throw "docker inspect returned an invalid $Service client address."
                }
                $normalizedAddress = $parsedAddress.ToString()
                if (-not $clientAddresses.Contains($normalizedAddress)) {
                    $clientAddresses.Add($normalizedAddress)
                }
            }
        }
    }
    if ($null -ne $ports) {
        foreach ($portProperty in @($ports.PSObject.Properties)) {
            foreach ($binding in @($portProperty.Value)) {
                if ($null -eq $binding) {
                    continue
                }
                $publishedPorts.Add([pscustomobject]@{
                    container_port = [string]$portProperty.Name
                    host_ip = [string](
                        Get-OptionalProperty -InputObject $binding -Name 'HostIp'
                    )
                    host_port = [string](
                        Get-OptionalProperty -InputObject $binding -Name 'HostPort'
                    )
                })
            }
        }
    }
    $healthState = Get-OptionalProperty -InputObject $state -Name 'Health'
    $healthStatus = Get-OptionalProperty -InputObject $healthState -Name 'Status'
    $revision = Get-OptionalProperty `
        -InputObject $labels -Name 'org.opencontainers.image.revision'
    $composeProject = Get-OptionalProperty `
        -InputObject $labels -Name 'com.docker.compose.project'
    $composeService = Get-OptionalProperty `
        -InputObject $labels -Name 'com.docker.compose.service'
    $composeOneoff = Get-OptionalProperty `
        -InputObject $labels -Name 'com.docker.compose.oneoff'
    return [ordered]@{
        service = $Service
        container_id = $ContainerId
        container_name = ([string]$container.Name).TrimStart('/')
        status = [string]$state.Status
        running = [bool]$state.Running
        oom_killed = [bool]$state.OOMKilled
        health = if ($null -ne $healthStatus) { [string]$healthStatus } else { $null }
        started_at = [string]$state.StartedAt
        image_id = [string]$container.Image
        revision = if ($null -ne $revision) { [string]$revision } else { $null }
        release_digest = if ($releaseDigest.Count -eq 1) {
            ([string]$releaseDigest[0]).Substring('RELEASE_IMAGE_DIGEST='.Length)
        }
        else {
            $null
        }
        restart_count = [int]$container.RestartCount
        memory_limit_bytes = [int64](
            Get-OptionalProperty -InputObject $hostConfig -Name 'Memory'
        )
        operator_api_key_entry_count = @(
            $environment | Where-Object {
                [string]$_ -imatch '^OPERATOR_API_KEY='
            }
        ).Count
        published_ports = @($publishedPorts)
        compose_project = if ($null -ne $composeProject) {
            [string]$composeProject
        }
        else { $null }
        compose_service = if ($null -ne $composeService) {
            [string]$composeService
        }
        else { $null }
        compose_oneoff = if ($null -ne $composeOneoff) {
            [string]$composeOneoff
        }
        else { $null }
        networks = @($networkNames)
        client_addresses = @($clientAddresses | Sort-Object)
    }
}

function Get-RunningProjectTopology {
    param([Parameter(Mandatory = $true)][Collections.IDictionary] $Containers)

    $expectedProject = [string]$Containers.api.compose_project
    if ([string]::IsNullOrWhiteSpace($expectedProject)) {
        throw 'Candidate API has no Compose project identity.'
    }
    $allowedById = @{
        ([string]$Containers.api.container_id) = 'api'
        ([string]$Containers.safety_control.container_id) = 'safety-control'
        ([string]$Containers.worker.container_id) = 'worker'
        ([string]$Containers.postgres.container_id) = 'postgres'
    }
    if ($allowedById.Count -ne 4) {
        throw 'Candidate service container identities are not unique.'
    }
    $expectedNetworks = @(
        @('api','safety_control','worker','postgres') |
            ForEach-Object { @($Containers[$_].networks) } |
            Sort-Object -Unique
    )
    if ($expectedNetworks.Count -ne 2) {
        throw 'Candidate services do not expose the exact two-network topology.'
    }

    $format = (
        '{{.ID}}|{{.Label "com.docker.compose.project"}}|' +
        '{{.Label "com.docker.compose.service"}}|' +
        '{{.Label "com.docker.compose.oneoff"}}|{{.Names}}|{{.Networks}}'
    )
    $result = Invoke-BoundedNative -FileName 'docker' -Arguments @(
        'ps',
        '--no-trunc',
        '--format',
        $format
    )
    $allRows = [Collections.Generic.List[object]]::new()
    $rows = [Collections.Generic.List[object]]::new()
    $unexpected = [Collections.Generic.List[string]]::new()
    foreach ($line in @($result.stdout -split '\r?\n')) {
        if ([string]::IsNullOrWhiteSpace([string]$line)) {
            continue
        }
        $parts = @([string]$line -split '\|', 6)
        if ($parts.Count -ne 6) {
            throw 'Docker running-project inventory returned a malformed row.'
        }
        $row = [pscustomobject]@{
            container_id = [string]$parts[0]
            project = [string]$parts[1]
            service = [string]$parts[2]
            oneoff = [string]$parts[3]
            container_name = [string]$parts[4]
            networks = @([string]$parts[5] -split ',' | Where-Object { $_ -ne '' })
        }
        $allRows.Add($row)
        $isRelevant = (
            $allowedById.ContainsKey($row.container_id) -or
            $row.project -eq $expectedProject -or
            @($row.networks | Where-Object { $_ -in $expectedNetworks }).Count -ne 0
        )
        if (-not $isRelevant) {
            continue
        }
        $rows.Add($row)
        if (
            -not $allowedById.ContainsKey($row.container_id) -or
            [string]$allowedById[$row.container_id] -ne $row.service -or
            $row.project -ne $expectedProject -or
            $row.oneoff -ne 'False'
        ) {
            $unexpected.Add(
                "$($row.container_name):$($row.service):$($row.container_id)"
            )
        }
    }
    $runningIds = @($rows | ForEach-Object container_id)
    $missingIds = @($allowedById.Keys | Where-Object { $_ -notin $runningIds })
    return [ordered]@{
        captured_at = [DateTimeOffset]::UtcNow.ToString('O')
        project = $expectedProject
        allowed_services = @('api', 'safety-control', 'worker', 'postgres')
        protected_networks = @($expectedNetworks)
        all_running_container_count = $allRows.Count
        all_running_containers = @($allRows)
        running_containers = @($rows)
        unexpected_containers = @($unexpected)
        missing_container_ids = @($missingIds)
        safe = (
            $rows.Count -eq 4 -and
            $unexpected.Count -eq 0 -and
            $missingIds.Count -eq 0
        )
    }
}

function Convert-Percent {
    param([Parameter(Mandatory = $true)][string] $Value)

    return [double]::Parse(
        $Value.TrimEnd('%'),
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function Get-ResourceSnapshot {
    param([Parameter(Mandatory = $true)][Collections.IDictionary] $Containers)

    $names = @(
        $Containers.api.container_name,
        $Containers.safety_control.container_name,
        $Containers.worker.container_name,
        $Containers.postgres.container_name
    )
    $arguments = @('stats', '--no-stream', '--format', '{{json .}}') + $names
    $result = Invoke-BoundedNative -FileName 'docker' -Arguments $arguments
    $stats = @{}
    foreach ($line in @($result.stdout -split '\r?\n')) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $item = $line | ConvertFrom-Json
        $stats[[string]$item.Name] = $item
    }
    foreach ($name in $names) {
        if (-not $stats.ContainsKey($name)) {
            throw "Docker resource sample omitted $name."
        }
    }
    return [ordered]@{
        captured_at = [DateTimeOffset]::UtcNow.ToString('O')
        api_cpu_percent = Convert-Percent $stats[$names[0]].CPUPerc
        api_memory_percent = Convert-Percent $stats[$names[0]].MemPerc
        safety_cpu_percent = Convert-Percent $stats[$names[1]].CPUPerc
        safety_memory_percent = Convert-Percent $stats[$names[1]].MemPerc
        worker_cpu_percent = Convert-Percent $stats[$names[2]].CPUPerc
        worker_memory_percent = Convert-Percent $stats[$names[2]].MemPerc
        postgres_cpu_percent = Convert-Percent $stats[$names[3]].CPUPerc
        postgres_memory_percent = Convert-Percent $stats[$names[3]].MemPerc
    }
}

function Get-ResourceGuardViolations {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]] $Samples
    )

    $violations = [Collections.Generic.List[string]]::new()
    if ($Samples.Count -eq 0) {
        return @('resource_sample_missing')
    }
    $latest = $Samples[-1]
    if (
        [double]$latest.api_memory_percent -ge 90 -or
        [double]$latest.safety_memory_percent -ge 90 -or
        [double]$latest.worker_memory_percent -ge 90 -or
        [double]$latest.postgres_memory_percent -ge 90
    ) {
        $violations.Add('container_memory_budget')
    }
    if ([double]$latest.postgres_cpu_percent -ge 200) {
        $violations.Add('postgres_cpu_single_sample')
    }
    if (
        $Samples.Count -ge 2 -and
        [double]$Samples[-1].postgres_cpu_percent -ge 150 -and
        [double]$Samples[-2].postgres_cpu_percent -ge 150
    ) {
        $violations.Add('postgres_cpu_two_consecutive_samples')
    }
    if ($Samples.Count -ge 7) {
        $window = @($Samples | Select-Object -Last 7)
        $average = (
            $window |
                Measure-Object -Property postgres_cpu_percent -Average
        ).Average
        if ([double]$average -ge 140) {
            $violations.Add('postgres_cpu_rolling_30s_average')
        }
    }
    return @($violations | Select-Object -Unique)
}

function Add-ResourceEvidenceSample {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[object]] $Window,
        [Parameter(Mandatory = $true)]
        [Collections.IDictionary] $Sample,
        [Parameter(Mandatory = $true)][double] $ScheduledElapsedSeconds,
        [Parameter(Mandatory = $true)][double] $ActualElapsedSeconds
    )

    $Sample['scheduled_elapsed_seconds'] = [Math]::Round(
        $ScheduledElapsedSeconds,
        3
    )
    $Sample['actual_elapsed_seconds'] = [Math]::Round($ActualElapsedSeconds, 3)
    $Sample['schedule_lag_seconds'] = [Math]::Round(
        [Math]::Max(0, $ActualElapsedSeconds - $ScheduledElapsedSeconds),
        3
    )
    $Window.Add([pscustomobject]$Sample)
    while ($Window.Count -gt 7) {
        $Window.RemoveAt(0)
    }
    $violations = [Collections.Generic.List[string]]::new()
    foreach ($item in @(Get-ResourceGuardViolations -Samples @($Window))) {
        $violations.Add($item)
    }
    if ([double]$Sample.schedule_lag_seconds -gt 2.5) {
        $violations.Add('resource_sampler_schedule_lag')
    }
    return @($violations | Select-Object -Unique)
}

function Get-ContainerGuardViolations {
    param([Parameter(Mandatory = $true)][Collections.IDictionary] $Containers)

    $violations = [Collections.Generic.List[string]]::new()
    foreach ($service in @('api','safety_control','worker','postgres')) {
        $container = $Containers[$service]
        if (
            -not [bool]$container.running -or
            [bool]$container.oom_killed -or
            [int]$container.restart_count -ne 0
        ) {
            $violations.Add("container_$service")
        }
    }
    $project = [string]$Containers.api.compose_project
    $expectedServices = @{
        api = 'api'
        safety_control = 'safety-control'
        worker = 'worker'
        postgres = 'postgres'
    }
    foreach ($service in @('api','safety_control','worker','postgres')) {
        $container = $Containers[$service]
        if (
            [string]::IsNullOrWhiteSpace($project) -or
            [string]$container.compose_project -ne $project -or
            [string]$container.compose_service -ne $expectedServices[$service] -or
            [string]$container.compose_oneoff -ne 'False'
        ) {
            $violations.Add("compose_identity_$service")
        }
    }
    if (-not $Containers.Contains('topology') -or -not [bool]$Containers.topology.safe) {
        $violations.Add('running_project_topology')
    }
    $defaultNetwork = "${project}_default"
    $safetyNetwork = "${project}_safety-plane"
    $expectedNetworks = @{
        api = @($defaultNetwork)
        worker = @($defaultNetwork)
        safety_control = @($safetyNetwork)
        postgres = @($defaultNetwork, $safetyNetwork)
    }
    foreach ($service in @('api','safety_control','worker','postgres')) {
        $actual = @($Containers[$service].networks | Sort-Object)
        $expected = @($expectedNetworks[$service] | Sort-Object)
        if (($actual -join '|') -cne ($expected -join '|')) {
            $violations.Add("network_topology_$service")
        }
    }
    if (
        [string]$Containers.api.health -ne 'healthy' -or
        [string]$Containers.safety_control.health -ne 'healthy' -or
        [string]$Containers.postgres.health -ne 'healthy' -or
        (
            $null -ne $Containers.worker.health -and
            [string]$Containers.worker.health -ne 'healthy'
        )
    ) {
        $violations.Add('container_health')
    }
    foreach ($service in @('api','safety_control','worker')) {
        if (
            [string]$Containers[$service].image_id -ne $ExpectedImageId -or
            [string]$Containers[$service].revision -ne $ExpectedRevision -or
            [string]$Containers[$service].release_digest -ne $ExpectedReleaseDigest
        ) {
            $violations.Add("image_identity_$service")
        }
    }
    foreach ($service in @('api','safety_control','worker','postgres')) {
        $expectedCount = if ($service -eq 'api') { 1 } else { 0 }
        if ([int]$Containers[$service].operator_api_key_entry_count -ne $expectedCount) {
            $violations.Add("operator_credential_$service")
        }
    }
    foreach ($service in @('api','safety_control','worker')) {
        if (
            [int64]$Containers[$service].memory_limit_bytes -ne
                [int64]$ExpectedContainerMemoryLimits[$service]
        ) {
            $violations.Add("memory_limit_$service")
        }
    }
    foreach ($binding in @(
        [pscustomobject]@{
            Service = 'api'
            ContainerPort = '8000/tcp'
            HostPort = [string]$BaseUri.Port
        },
        [pscustomobject]@{
            Service = 'safety_control'
            ContainerPort = '8001/tcp'
            HostPort = [string]$SafetyBaseUri.Port
        },
        [pscustomobject]@{
            Service = 'postgres'
            ContainerPort = '5432/tcp'
            HostPort = '5432'
        }
    )) {
        $container = $Containers[$binding.Service]
        $published = @($container.published_ports)
        $exact = @(
            $published |
                Where-Object {
                    [string]$_.container_port -eq $binding.ContainerPort -and
                    [string]$_.host_ip -eq '127.0.0.1' -and
                    [string]$_.host_port -eq $binding.HostPort
                }
        )
        if ($published.Count -ne 1 -or $exact.Count -ne 1) {
            $violations.Add("loopback_binding_$($binding.Service)")
        }
    }
    return @($violations | Select-Object -Unique)
}

function Get-ContainerSet {
    param(
        [Parameter(Mandatory = $true)][string] $ApiContainer,
        [Parameter(Mandatory = $true)][string] $SafetyContainer,
        [Parameter(Mandatory = $true)][string] $WorkerContainer,
        [Parameter(Mandatory = $true)][string] $PostgresContainer,
        [Parameter(Mandatory = $false)][scriptblock] $HeartbeatAction
    )

    $snapshots = [ordered]@{}
    foreach ($descriptor in @(
        [pscustomobject]@{ Key = 'api'; Service = 'api'; Id = $ApiContainer },
        [pscustomobject]@{
            Key = 'safety_control'
            Service = 'safety-control'
            Id = $SafetyContainer
        },
        [pscustomobject]@{ Key = 'worker'; Service = 'worker'; Id = $WorkerContainer },
        [pscustomobject]@{
            Key = 'postgres'
            Service = 'postgres'
            Id = $PostgresContainer
        }
    )) {
        $snapshots[$descriptor.Key] = Get-ContainerSnapshot `
            -Service $descriptor.Service -ContainerId $descriptor.Id
        if ($null -ne $HeartbeatAction) {
            & $HeartbeatAction
        }
    }
    $snapshots['topology'] = Get-RunningProjectTopology -Containers $snapshots
    if ($null -ne $HeartbeatAction) {
        & $HeartbeatAction
    }
    return $snapshots
}

function New-DatabaseSessionBoundary {
    param([Parameter(Mandatory = $true)][Collections.IDictionary] $Containers)

    $boundary = [ordered]@{}
    foreach ($service in @('api', 'worker', 'safety_control')) {
        $container = $Containers[$service]
        if ($null -eq $container) {
            throw "Database session boundary is missing the $service container."
        }
        $addresses = @(
            @(Get-OptionalProperty -InputObject $container -Name 'client_addresses') |
                Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } |
                Sort-Object -Unique
        )
        if ($addresses.Count -eq 0) {
            throw "Database session boundary has no $service client address."
        }
        $normalized = [Collections.Generic.List[string]]::new()
        foreach ($address in $addresses) {
            $parsedAddress = $null
            if (-not [Net.IPAddress]::TryParse([string]$address, [ref]$parsedAddress)) {
                throw "Database session boundary has an invalid $service client address."
            }
            $normalizedAddress = $parsedAddress.ToString()
            if (-not $normalized.Contains($normalizedAddress)) {
                $normalized.Add($normalizedAddress)
            }
        }
        $boundary["${service}_addresses"] = @($normalized | Sort-Object)
    }
    return $boundary
}

function ConvertTo-DatabaseAddressArraySql {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]] $Addresses,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $normalized = [Collections.Generic.List[string]]::new()
    foreach ($address in @($Addresses)) {
        $parsedAddress = $null
        if (-not [Net.IPAddress]::TryParse([string]$address, [ref]$parsedAddress)) {
            throw "Database session SQL has an invalid $Label client address."
        }
        $normalizedAddress = $parsedAddress.ToString()
        if (-not $normalized.Contains($normalizedAddress)) {
            $normalized.Add($normalizedAddress)
        }
    }
    if ($normalized.Count -eq 0) {
        throw "Database session SQL has no $Label client address."
    }
    $literals = @(
        $normalized |
            Sort-Object |
            ForEach-Object { "'$($_)'" }
    )
    return "ARRAY[$($literals -join ',')]::text[]"
}

function Get-DatabaseSessionSql {
    param(
        [Parameter(Mandatory = $true)]
        [Collections.IDictionary] $Boundary
    )

    $apiAddresses = ConvertTo-DatabaseAddressArraySql `
        -Addresses @($Boundary.api_addresses) -Label 'API'
    $workerAddresses = ConvertTo-DatabaseAddressArraySql `
        -Addresses @($Boundary.worker_addresses) -Label 'worker'
    $safetyAddresses = ConvertTo-DatabaseAddressArraySql `
        -Addresses @($Boundary.safety_control_addresses) -Label 'safety-control'

    return @'
(
  SELECT jsonb_build_object(
    'database_name', current_database(),
    'collector', (
      SELECT jsonb_build_object(
        'pid', activity.pid,
        'backend_type', activity.backend_type,
        'usename', activity.usename::text,
        'current_user', current_user::text,
        'application_name', activity.application_name,
        'client_addr', CASE
          WHEN activity.client_addr IS NULL THEN NULL
          ELSE host(activity.client_addr)
        END
      )
      FROM pg_stat_activity AS activity
      WHERE activity.pid=pg_backend_pid()
    ),
    'collector_exact', (
      SELECT
        activity.backend_type='client backend'
        AND activity.usename='stratus'
        AND session_user='stratus'::name
        AND current_user='stratus'::name
        AND activity.application_name='stratus-paper-canary-collector'
        AND activity.client_addr IS NULL
      FROM pg_stat_activity AS activity
      WHERE activity.pid=pg_backend_pid()
    ),
    'expected_identities', jsonb_build_object(
      'api', jsonb_build_object(
        'backend_type', 'client backend',
        'usename', 'stratus_app_' || md5(current_database()),
        'application_name', 'stratus-api',
        'client_addresses', to_jsonb(__API_ADDRESS_ARRAY__),
        'max_sessions', 4
      ),
      'worker', jsonb_build_object(
        'backend_type', 'client backend',
        'usename', 'stratus_app_' || md5(current_database()),
        'application_name', 'stratus-runtime',
        'client_addresses', to_jsonb(__WORKER_ADDRESS_ARRAY__),
        'max_sessions', 15
      ),
      'safety_control', jsonb_build_object(
        'backend_type', 'client backend',
        'usename', 'stratus_safety_app_' || md5(current_database()),
        'application_name', 'stratus-safety-control',
        'client_addresses', to_jsonb(__SAFETY_ADDRESS_ARRAY__),
        'max_sessions', 2
      )
    ),
    'client_backends', coalesce(
      jsonb_agg(
        jsonb_build_object(
          'pid', session_row.pid,
          'backend_type', session_row.backend_type,
          'usename', session_row.usename,
          'application_name', session_row.application_name,
          'client_addr', session_row.client_addr,
          'identity', session_row.identity
        ) ORDER BY session_row.pid
      ),
      '[]'::jsonb
    ),
    'client_backend_sessions', count(*),
    'api_sessions', count(*) FILTER (WHERE session_row.identity='api'),
    'worker_sessions', count(*) FILTER (WHERE session_row.identity='worker'),
    'safety_control_sessions', count(*) FILTER (
      WHERE session_row.identity='safety_control'
    ),
    'unexpected_sessions', count(*) FILTER (
      WHERE session_row.identity='unexpected'
    ),
    'idle_in_transaction', count(*) FILTER (
      WHERE session_row.state LIKE 'idle in transaction%'
    ),
    'lock_waiters', count(*) FILTER (
      WHERE session_row.wait_event_type='Lock'
    )
  )
  FROM (
    SELECT
      activity.pid,
      activity.backend_type,
      activity.usename::text AS usename,
      activity.application_name,
      CASE
        WHEN activity.client_addr IS NULL THEN NULL
        ELSE host(activity.client_addr)
      END AS client_addr,
      activity.state,
      activity.wait_event_type,
      CASE
        WHEN activity.usename=(
               'stratus_app_' || md5(current_database())
             )
         AND activity.application_name='stratus-api'
         AND host(activity.client_addr)=ANY(__API_ADDRESS_ARRAY__)
          THEN 'api'
        WHEN activity.usename=(
               'stratus_app_' || md5(current_database())
             )
         AND activity.application_name='stratus-runtime'
         AND host(activity.client_addr)=ANY(__WORKER_ADDRESS_ARRAY__)
          THEN 'worker'
        WHEN activity.usename=(
               'stratus_safety_app_' || md5(current_database())
             )
         AND activity.application_name='stratus-safety-control'
         AND host(activity.client_addr)=ANY(__SAFETY_ADDRESS_ARRAY__)
          THEN 'safety_control'
        ELSE 'unexpected'
      END AS identity
    FROM pg_stat_activity AS activity
    WHERE activity.datname=current_database()
      AND activity.backend_type='client backend'
      AND activity.pid<>pg_backend_pid()
  ) AS session_row
)
'@.Replace(
        '__API_ADDRESS_ARRAY__',
        $apiAddresses
    ).Replace(
        '__WORKER_ADDRESS_ARRAY__',
        $workerAddresses
    ).Replace(
        '__SAFETY_ADDRESS_ARRAY__',
        $safetyAddresses
    )
}

function ConvertFrom-ClaimIdentityToken {
    param(
        [Parameter(Mandatory = $true)][string] $Token,
        [Parameter(Mandatory = $true)][ValidateRange(0, [int]::MaxValue)]
        [int] $ExpectedCount
    )

    if ($ExpectedCount -eq 0) {
        return [pscustomobject]@{
            valid = $Token -eq '-'
            ids = @()
        }
    }
    if ($Token -notmatch '^[1-9][0-9]*(,[1-9][0-9]*)*$') {
        return [pscustomobject]@{
            valid = $false
            ids = @()
        }
    }
    $ids = @(
        $Token.Split(',') |
            ForEach-Object { [long]$_ }
    )
    return [pscustomobject]@{
        valid = (
            $ids.Count -eq $ExpectedCount -and
            @($ids | Sort-Object -Unique).Count -eq $ids.Count
        )
        ids = @($ids)
    }
}

function Get-WorkerMaintenanceLogSnapshot {
    param(
        [Parameter(Mandatory = $true)][string] $WorkerContainer,
        [Parameter(Mandatory = $true)][DateTimeOffset] $Since,
        [ValidateRange(100, 5000)][int] $Tail = 5000
    )

    $result = Invoke-BoundedNative -FileName 'docker' -Arguments @(
        'logs',
        '--timestamps',
        '--since',
        $Since.ToUniversalTime().ToString('O'),
        '--tail',
        [string]$Tail,
        $WorkerContainer
    )
    $combined = $result.stdout + [Environment]::NewLine + $result.stderr
    if ([Text.Encoding]::UTF8.GetByteCount($combined) -gt 2097152) {
        throw 'Worker log evidence exceeded the 2 MiB canary bound.'
    }
    $rows = [Collections.Generic.List[object]]::new()
    $dangerousLines = [Collections.Generic.List[string]]::new()
    $nonEmptyLineCount = 0
    $timestampParseFailures = 0
    $claimIdentityMismatches = 0
    $latestDockerTimestamp = $null
    $pattern = (
        'execution cost maintenance: at=(?<at>\S+) ' +
        'mark_claimed=(?<mark>\d+) observed=(?<observed>\d+) ' +
        'missing=(?<missing>\d+) deferred=(?<mark_deferred>\d+) ' +
        'mark_ids=(?<mark_ids>\S+) ' +
        'finalize_claimed=(?<finalize>\d+) sealed=(?<sealed>\d+) ' +
        'deferred=(?<finalize_deferred>\d+) ' +
        'finalize_ids=(?<finalize_ids>\S+)'
    )
    foreach ($line in @($combined -split '\r?\n')) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $nonEmptyLineCount += 1
        $timestampToken = @($line -split '\s+', 2)[0]
        $dockerTimestamp = $null
        try {
            $dockerTimestamp = [DateTimeOffset]$timestampToken
            if (
                $null -eq $latestDockerTimestamp -or
                $dockerTimestamp -gt $latestDockerTimestamp
            ) {
                $latestDockerTimestamp = $dockerTimestamp
            }
        }
        catch {
            # A line without Docker's requested timestamp makes the scan
            # incomplete and therefore fail-closed below.
            $timestampParseFailures += 1
        }
        $match = [regex]::Match($line, $pattern)
        if ($match.Success) {
            $markClaimed = [int]$match.Groups['mark'].Value
            $finalizeClaimed = [int]$match.Groups['finalize'].Value
            $markIdentity = ConvertFrom-ClaimIdentityToken `
                -Token $match.Groups['mark_ids'].Value `
                -ExpectedCount $markClaimed
            $finalizeIdentity = ConvertFrom-ClaimIdentityToken `
                -Token $match.Groups['finalize_ids'].Value `
                -ExpectedCount $finalizeClaimed
            $identityValid = (
                [bool]$markIdentity.valid -and
                [bool]$finalizeIdentity.valid
            )
            if (-not $identityValid) {
                $claimIdentityMismatches += 1
            }
            $rows.Add([pscustomobject]@{
                docker_timestamp = if ($null -ne $dockerTimestamp) {
                    $dockerTimestamp.ToUniversalTime().ToString('O')
                }
                else {
                    $null
                }
                observed_at = $match.Groups['at'].Value
                mark_claimed = $markClaimed
                marks_observed = [int]$match.Groups['observed'].Value
                marks_missing = [int]$match.Groups['missing'].Value
                mark_deferred = [int]$match.Groups['mark_deferred'].Value
                mark_ids = @($markIdentity.ids)
                finalize_claimed = $finalizeClaimed
                finalized_sealed = [int]$match.Groups['sealed'].Value
                finalize_deferred = [int]$match.Groups['finalize_deferred'].Value
                finalize_ids = @($finalizeIdentity.ids)
                claim_identity_valid = $identityValid
            })
        }
        elseif ($line -match 'execution cost maintenance:') {
            # The candidate log schema is part of the release evidence
            # contract. A legacy or partially emitted row must not disappear
            # from the scheduler-contention detector.
            $claimIdentityMismatches += 1
        }
        if (
            $line -match (
                '(?i)(traceback|max_instances|maximum number of running instances|' +
                'scheduler overlap|(?:^|\s)(?:ERROR|CRITICAL)(?:\s|:))'
            )
        ) {
            $dangerousLines.Add(
                $(if ($line.Length -gt 500) { $line.Substring(0, 500) } else { $line })
            )
        }
    }
    $phaseABRuns = @(
        $rows |
            Where-Object { $_.mark_claimed -gt 0 -and $_.finalize_claimed -gt 0 }
    )
    $withinGraceRuns = @(
        $rows |
            Where-Object { $_.mark_deferred -gt 0 }
    )
    $zeroLegSealedRuns = @(
        $rows |
            Where-Object {
                $_.mark_claimed -eq 0 -and
                $_.finalize_claimed -gt 0 -and
                $_.finalized_sealed -eq $_.finalize_claimed -and
                $_.finalize_deferred -eq 0
            }
    )
    $zeroMarkFinalizeEvents = [Collections.Generic.List[object]]::new()
    $eventKeys = [Collections.Generic.HashSet[string]]::new()
    foreach ($row in @(
        $rows |
            Where-Object {
                $_.claim_identity_valid -and
                $_.mark_claimed -eq 0 -and
                $_.finalize_claimed -gt 0
            }
    )) {
        foreach ($obligationId in @($row.finalize_ids)) {
            $eventKey = (
                [string]$row.docker_timestamp + '|' + [string]$obligationId
            )
            if ($eventKeys.Add($eventKey)) {
                $zeroMarkFinalizeEvents.Add([pscustomobject]@{
                    obligation_id = [long]$obligationId
                    docker_timestamp = [string]$row.docker_timestamp
                    event_key = $eventKey
                })
            }
        }
    }
    $repeatedZeroMarkFinalizeIds = @(
        $zeroMarkFinalizeEvents |
            Group-Object -Property obligation_id |
            Where-Object { $_.Count -gt 1 } |
            ForEach-Object { [long]$_.Name } |
            Sort-Object -Unique
    )
    return [ordered]@{
        since = $Since.ToUniversalTime().ToString('O')
        tail_limit = $Tail
        nonempty_lines = $nonEmptyLineCount
        timestamp_parse_failures = $timestampParseFailures
        possibly_truncated = $nonEmptyLineCount -ge $Tail
        latest_docker_timestamp = if ($null -ne $latestDockerTimestamp) {
            $latestDockerTimestamp.ToUniversalTime().ToString('O')
        }
        else {
            $null
        }
        maintenance_rows = $rows.Count
        rows = @($rows | Select-Object -Last 250)
        claim_identity_mismatches = $claimIdentityMismatches
        phase_a_b_runs = $phaseABRuns.Count
        within_grace_deferred_runs = $withinGraceRuns.Count
        zero_leg_sealed_runs = $zeroLegSealedRuns.Count
        zero_mark_finalize_events = @($zeroMarkFinalizeEvents)
        repeated_zero_mark_finalize_ids = @($repeatedZeroMarkFinalizeIds)
        dangerous_log_lines = @($dangerousLines | Select-Object -Last 50)
    }
}

function Invoke-ReadOnlySql {
    param(
        [Parameter(Mandatory = $true)][string] $PostgresContainer,
        [Parameter(Mandatory = $true)][string] $Sql
    )

    $result = Invoke-BoundedNative -FileName 'docker' -Arguments @(
        'exec',
        '-i',
        $PostgresContainer,
        'sh',
        '-lc',
        'PGPASSWORD="$POSTGRES_PASSWORD" PGAPPNAME="stratus-paper-canary-collector" exec psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    ) -StandardInput $Sql
    $text = $result.stdout.Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw 'Read-only PostgreSQL snapshot returned no JSON.'
    }
    return $text | ConvertFrom-Json
}

function Get-DatabaseClock {
    param([Parameter(Mandatory = $true)][string] $PostgresContainer)

    $state = Invoke-ReadOnlySql -PostgresContainer $PostgresContainer -Sql @'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '3s';
SELECT jsonb_build_object('database_now', clock_timestamp())::text;
COMMIT;
'@
    if ([string]::IsNullOrWhiteSpace([string]$state.database_now)) {
        throw 'PostgreSQL clock boundary could not be sealed.'
    }
    return [string]$state.database_now
}

function Get-GuardSql {
    param(
        [Parameter(Mandatory = $true)][string] $CommandBoundaryTime,
        [Parameter(Mandatory = $true)]
        [Collections.IDictionary] $DatabaseSessionBoundary
    )

    $databaseSessionSql = Get-DatabaseSessionSql -Boundary $DatabaseSessionBoundary
    return @'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '5s';
WITH global_required_marks AS (
  SELECT
    leg.id AS fill_leg_id,
    intent.id AS intent_id,
    horizon.seconds AS horizon_seconds,
    leg.fill_effective_at
      + (horizon.seconds + runtime.mark_grace_seconds) * interval '1 second'
      AS grace_at,
    mark.id AS mark_id
  FROM execution_fill_legs leg
  JOIN execution_order_intents intent ON intent.id=leg.intent_id
  CROSS JOIN execution_cost_runtime runtime
  CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) horizon(seconds)
  LEFT JOIN execution_postfill_marks mark
    ON mark.fill_leg_id=leg.id AND mark.horizon_seconds=horizon.seconds
  WHERE intent.mode='paper'::execution_mode
    AND leg.fill_effective_at IS NOT NULL
)
SELECT jsonb_build_object(
  'database_now', clock_timestamp(),
  'database_boundaries', jsonb_build_object(
    'runtime_exact', public.assert_stratus_runtime_boundary(false),
    'safety_exact', public.assert_stratus_safety_boundary(false)
  ),
  'resume', (
    SELECT jsonb_build_object(
      'request_id', request_id, 'command', command, 'payload', payload,
      'actor', actor, 'reason', reason, 'status', status,
      'created_at', created_at, 'executed_at', executed_at
    ) FROM operator_commands WHERE request_id = '__RESUME_REQUEST_ID__'
  ),
  'stop', (
    SELECT jsonb_build_object(
      'request_id', request_id, 'command', command, 'payload', payload,
      'actor', actor, 'reason', reason, 'status', status,
      'created_at', created_at, 'executed_at', executed_at
    ) FROM operator_commands WHERE request_id = '__STOP_REQUEST_ID__'
  ),
  'release_stop', (
    SELECT jsonb_build_object(
      'request_id', request_id, 'command', command, 'payload', payload,
      'actor', actor, 'reason', reason, 'status', status,
      'created_at', created_at, 'executed_at', executed_at
    ) FROM operator_commands WHERE request_id = '__RELEASE_REQUEST_ID__'
  ),
  'canary_lease', (
    SELECT jsonb_build_object(
      'id', id, 'lease_id', lease_id, 'run_id', run_id,
      'acquire_request_id', acquire_request_id,
      'resume_request_id', resume_request_id,
      'resume_actor', resume_actor, 'resume_reason', resume_reason,
      'watchdog_stop_request_id', watchdog_stop_request_id,
      'direct_stop_request_id', direct_stop_request_id,
      'release_request_id', release_request_id,
      'nonce_sha256', nonce_sha256, 'state', state,
      'acquired_at', acquired_at, 'heartbeat_at', heartbeat_at,
      'watchdog_at', watchdog_at, 'expires_at', expires_at,
      'released_at', released_at, 'stop_command_id', stop_command_id
    ) FROM paper_canary_safety_leases WHERE lease_id='__LEASE_ID__'
  ),
  'active_canary_leases', (
    SELECT count(*) FROM paper_canary_safety_leases WHERE state='active'
  ),
  'global_control', (
    SELECT jsonb_build_object(
      'mode', mode::text, 'kill_switch', kill_switch,
      'halted_reason', halted_reason,
      'live_confirmation_until', live_confirmation_until
    ) FROM runtime_control WHERE id=1
  ),
  'venues', (
    SELECT coalesce(jsonb_object_agg(
      venue::text,
      jsonb_build_object(
        'enabled', enabled, 'mode', mode::text, 'kill_switch', kill_switch,
        'halted_reason', halted_reason,
        'live_confirmation_until', live_confirmation_until
      )
    ), '{}'::jsonb) FROM venue_runtime_control
  ),
  'pending_commands', (
    SELECT count(*) FROM operator_commands WHERE status='pending'
  ),
  'pending_signals', (
    SELECT count(*) FROM signals WHERE status='pending'::signal_status
  ),
  'unexpected_pending_commands', (
    SELECT count(*) FROM operator_commands
    WHERE status='pending' AND request_id <> '__RESUME_REQUEST_ID__'
  ),
  'unexpected_commands_since_boundary', (
    SELECT count(*) FROM operator_commands
    WHERE created_at >= '__COMMAND_BOUNDARY_TIME__'::timestamptz
      AND request_id NOT IN ('__RESUME_REQUEST_ID__','__STOP_REQUEST_ID__')
  ),
  'runtime_database', __DATABASE_SESSION_SQL__,
  'scheduler', jsonb_build_object(
    'latest_maintenance_age_seconds', (
      SELECT extract(epoch FROM clock_timestamp()-max(finished_at))
      FROM job_runs
      WHERE job_id='execution-cost-maintenance' AND status='success'
    ),
    'recent_maintenance_failures', (
      SELECT count(*) FROM job_runs
      WHERE job_id='execution-cost-maintenance' AND status<>'success'
        AND scheduled_at >= clock_timestamp()-interval '2 minutes'
    ),
    'recent_legacy_runs', (
      SELECT count(*) FROM job_runs
      WHERE job_id IN ('execution-markouts','execution-cost-finalization')
        AND scheduled_at >= clock_timestamp()-interval '2 minutes'
    )
  ),
  'execution_guard', jsonb_build_object(
    'waiting_obligations', (
      SELECT count(*)
      FROM execution_cost_obligations obligation
      JOIN execution_order_intents intent ON intent.id=obligation.intent_id
      WHERE intent.mode='paper'::execution_mode
        AND obligation.state IN ('pending','waiting_for_marks')
    ),
    'post_grace_missing_marks', (
      SELECT count(*) FROM global_required_marks
      WHERE mark_id IS NULL AND grace_at < clock_timestamp()
    ),
    'finalizable_unsealed', (
      SELECT count(*)
      FROM execution_cost_obligations obligation
      JOIN execution_order_intents intent ON intent.id=obligation.intent_id
      WHERE intent.mode='paper'::execution_mode
        AND obligation.state IN ('pending','waiting_for_marks')
        AND obligation.next_due_at <= clock_timestamp()
        AND NOT EXISTS (
          SELECT 1
          FROM execution_fill_legs leg
          CROSS JOIN execution_cost_runtime runtime
          CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) horizon(seconds)
          LEFT JOIN execution_postfill_marks mark
            ON mark.fill_leg_id=leg.id AND mark.horizon_seconds=horizon.seconds
          WHERE leg.intent_id=obligation.intent_id
            AND leg.fill_effective_at IS NOT NULL
            AND mark.id IS NULL
        )
        AND NOT EXISTS (
          SELECT 1 FROM execution_cost_seals seal
          WHERE seal.obligation_id=obligation.id
        )
        AND coalesce(
          (
            SELECT max(mark.sealed_at)
            FROM execution_fill_legs leg
            JOIN execution_postfill_marks mark ON mark.fill_leg_id=leg.id
            WHERE leg.intent_id=obligation.intent_id
          ),
          obligation.next_due_at
        ) <= clock_timestamp()-interval '20 seconds'
    ),
    'finalizable_ids', (
      SELECT coalesce(jsonb_agg(candidate.id ORDER BY candidate.id), '[]'::jsonb)
      FROM (
        SELECT obligation.id
        FROM execution_cost_obligations obligation
        JOIN execution_order_intents intent ON intent.id=obligation.intent_id
        WHERE intent.mode='paper'::execution_mode
          AND obligation.state IN ('pending','waiting_for_marks')
          AND obligation.next_due_at <= clock_timestamp()-interval '20 seconds'
          AND NOT EXISTS (
            SELECT 1
            FROM execution_fill_legs leg
            CROSS JOIN execution_cost_runtime runtime
            CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) horizon(seconds)
            LEFT JOIN execution_postfill_marks mark
              ON mark.fill_leg_id=leg.id AND mark.horizon_seconds=horizon.seconds
            WHERE leg.intent_id=obligation.intent_id
              AND leg.fill_effective_at IS NOT NULL
              AND mark.id IS NULL
          )
          AND NOT EXISTS (
            SELECT 1 FROM execution_cost_seals seal
            WHERE seal.obligation_id=obligation.id
          )
          AND coalesce(
            (
              SELECT max(mark.sealed_at)
              FROM execution_fill_legs leg
              JOIN execution_postfill_marks mark ON mark.fill_leg_id=leg.id
              WHERE leg.intent_id=obligation.intent_id
            ),
            obligation.next_due_at
          ) <= clock_timestamp()-interval '20 seconds'
        ORDER BY obligation.id
        LIMIT 50
      ) candidate
    )
  ),
  'live', jsonb_build_object(
    'orders', (SELECT count(*) FROM orders WHERE mode='live'::execution_mode),
    'positions', (SELECT count(*) FROM positions WHERE mode='live'::execution_mode),
    'intents', (
      SELECT count(*) FROM execution_order_intents WHERE mode='live'::execution_mode
    ),
    'fill_legs', (
      SELECT count(*) FROM execution_fill_legs WHERE mode='live'::execution_mode
    ),
    'enrollments', (
      SELECT count(*) FROM prospective_validation_enrollments
      WHERE mode='live'::execution_mode
    ),
    'outcomes', (
      SELECT count(*)
      FROM prospective_validation_outcomes outcome
      JOIN prospective_validation_enrollments enrollment
        ON enrollment.id=outcome.enrollment_id
      WHERE enrollment.mode='live'::execution_mode
    )
  )
)::text;
COMMIT;
'@.Replace(
    '__RESUME_REQUEST_ID__',
    $ExpectedResumeRequestId.Replace("'", "''")
).Replace(
    '__STOP_REQUEST_ID__',
    $stopRequestId.Replace("'", "''")
).Replace(
    '__COMMAND_BOUNDARY_TIME__',
    $CommandBoundaryTime.Replace("'", "''")
).Replace(
    '__LEASE_ID__',
    $leaseId.Replace("'", "''")
).Replace(
    '__RELEASE_REQUEST_ID__',
    $leaseReleaseRequestId.Replace("'", "''")
).Replace(
    '__DATABASE_SESSION_SQL__',
    $databaseSessionSql
)
}

function Get-FullSql {
    param(
        [Parameter(Mandatory = $true)][string] $AnchorDatabaseTime,
        [Parameter(Mandatory = $true)][string] $CommandBoundaryTime,
        [Parameter(Mandatory = $true)]
        [Collections.IDictionary] $DatabaseSessionBoundary
    )

    $databaseSessionSql = Get-DatabaseSessionSql -Boundary $DatabaseSessionBoundary
    $template = @'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '8s';
WITH active_epoch AS (
  SELECT id, opened_at, cohort, image_digest
  FROM probability_pipeline_release_epochs
  ORDER BY id DESC LIMIT 1
),
required_marks AS (
  SELECT
    leg.id AS fill_leg_id,
    intent.id AS intent_id,
    horizon.seconds AS horizon_seconds,
    leg.fill_effective_at + horizon.seconds * interval '1 second' AS target_at,
    leg.fill_effective_at
      + (horizon.seconds + runtime.mark_grace_seconds) * interval '1 second'
      AS grace_at,
    mark.id AS mark_id,
    mark.status AS mark_status
  FROM execution_fill_legs leg
  JOIN execution_order_intents intent ON intent.id=leg.intent_id
  JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
  CROSS JOIN execution_cost_runtime runtime
  CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) horizon(seconds)
  LEFT JOIN execution_postfill_marks mark
    ON mark.fill_leg_id=leg.id AND mark.horizon_seconds=horizon.seconds
  WHERE intent.mode='paper'::execution_mode
    AND leg.fill_effective_at IS NOT NULL
),
global_required_marks AS (
  SELECT
    leg.id AS fill_leg_id,
    intent.id AS intent_id,
    horizon.seconds AS horizon_seconds,
    leg.fill_effective_at + horizon.seconds * interval '1 second' AS target_at,
    leg.fill_effective_at
      + (horizon.seconds + runtime.mark_grace_seconds) * interval '1 second'
      AS grace_at,
    mark.id AS mark_id,
    mark.status AS mark_status
  FROM execution_fill_legs leg
  JOIN execution_order_intents intent ON intent.id=leg.intent_id
  CROSS JOIN execution_cost_runtime runtime
  CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) horizon(seconds)
  LEFT JOIN execution_postfill_marks mark
    ON mark.fill_leg_id=leg.id AND mark.horizon_seconds=horizon.seconds
  WHERE intent.mode='paper'::execution_mode
    AND leg.fill_effective_at IS NOT NULL
),
maintenance_source AS (
  SELECT scheduled_at, finished_at, status
  FROM job_runs
  WHERE job_id='execution-cost-maintenance'
    AND scheduled_at >= '__ANCHOR_DATABASE_TIME__'::timestamptz - interval '30 seconds'
),
maintenance AS (
  SELECT scheduled_at, finished_at, status,
    extract(epoch FROM scheduled_at-lag(scheduled_at) OVER (ORDER BY scheduled_at)) AS gap
  FROM maintenance_source
),
mark_payloads AS MATERIALIZED (
  SELECT
    mark.record_hash AS stored_hash,
    jsonb_build_object(
      'record_kind', 'mark',
      'clob_snapshot_hash', mark.clob_snapshot_hash,
      'clob_snapshot_id', mark.clob_snapshot_id,
      'definition_hash', mark.definition_hash,
      'definition_version', mark.definition_version,
      'fill_leg_id', mark.fill_leg_id,
      'horizon_seconds', mark.horizon_seconds,
      'missing_reason', mark.missing_reason,
      'observed_at', mark.observed_at,
      'offset_seconds', mark.offset_seconds,
      'sealed_at', mark.sealed_at,
      'side_best_ask', mark.side_best_ask,
      'side_best_bid', mark.side_best_bid,
      'side_midpoint', mark.side_midpoint,
      'status', mark.status,
      'target_at', mark.target_at
    ) AS payload
  FROM execution_postfill_marks mark
  JOIN execution_fill_legs leg ON leg.id=mark.fill_leg_id
  JOIN execution_order_intents intent ON intent.id=leg.intent_id
  JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
),
seal_payloads AS MATERIALIZED (
  SELECT
    seal.record_hash AS stored_hash,
    jsonb_build_object(
      'record_kind', 'seal',
      'adverse_selection_1800s', seal.adverse_selection_1800s,
      'adverse_selection_300s', seal.adverse_selection_300s,
      'aggregate_role', seal.aggregate_role,
      'definition_hash', seal.definition_hash,
      'definition_version', seal.definition_version,
      'eligibility_reason', seal.eligibility_reason,
      'eligible', seal.eligible,
      'expected_slippage_coverage', seal.expected_slippage_coverage,
      'expected_slippage_per_share', seal.expected_slippage_per_share,
      'fee_drag', seal.fee_drag,
      'fee_per_share', seal.fee_per_share,
      'filled_notional_usd', seal.filled_notional_usd,
      'gross_model_edge', seal.gross_model_edge,
      'intent_id', seal.intent_id,
      'intent_midpoint', seal.intent_midpoint,
      'latency_movement', seal.latency_movement,
      'leg_count', seal.leg_count,
      'maker_improvement', seal.maker_improvement,
      'markout_1800s_coverage', seal.markout_1800s_coverage,
      'markout_300s_coverage', seal.markout_300s_coverage,
      'obligation_id', seal.obligation_id,
      'order_id', seal.order_id,
      'realized_net_edge', seal.realized_net_edge,
      'reason_detail', seal.reason_detail,
      'sealed_at', seal.sealed_at,
      'side_model_probability', seal.side_model_probability,
      'taker_slippage', seal.taker_slippage,
      'taker_spread', seal.taker_spread,
      'total_fee_usd', seal.total_fee_usd,
      'total_shares', seal.total_shares,
      'vwap_fill_price', seal.vwap_fill_price,
      'weighted_fill_midpoint', seal.weighted_fill_midpoint
    ) AS payload
  FROM execution_cost_seals seal
  JOIN execution_order_intents intent ON intent.id=seal.intent_id
  JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
),
cost_hashes AS MATERIALIZED (
  SELECT stored_hash, payload FROM mark_payloads
  UNION ALL
  SELECT stored_hash, payload FROM seal_payloads
),
latest_audit AS (
  SELECT ts, status, issues FROM health_audits ORDER BY ts DESC LIMIT 1
)
SELECT jsonb_build_object(
  'database_now', clock_timestamp(),
  'schema_head', (SELECT version_num FROM alembic_version),
  'database_boundaries', jsonb_build_object(
    'runtime_exact', public.assert_stratus_runtime_boundary(false),
    'safety_exact', public.assert_stratus_safety_boundary(false)
  ),
  'resume', (
    SELECT jsonb_build_object(
      'request_id', request_id, 'command', command, 'payload', payload,
      'actor', actor, 'reason', reason, 'status', status,
      'created_at', created_at, 'executed_at', executed_at
    ) FROM operator_commands WHERE request_id='__RESUME_REQUEST_ID__'
  ),
  'stop', (
    SELECT jsonb_build_object(
      'request_id', request_id, 'command', command, 'payload', payload,
      'actor', actor, 'reason', reason, 'status', status,
      'created_at', created_at, 'executed_at', executed_at
    ) FROM operator_commands WHERE request_id='__STOP_REQUEST_ID__'
  ),
  'release_stop', (
    SELECT jsonb_build_object(
      'request_id', request_id, 'command', command, 'payload', payload,
      'actor', actor, 'reason', reason, 'status', status,
      'created_at', created_at, 'executed_at', executed_at
    ) FROM operator_commands WHERE request_id='__RELEASE_REQUEST_ID__'
  ),
  'canary_lease', (
    SELECT jsonb_build_object(
      'id', id, 'lease_id', lease_id, 'run_id', run_id,
      'acquire_request_id', acquire_request_id,
      'resume_request_id', resume_request_id,
      'resume_actor', resume_actor, 'resume_reason', resume_reason,
      'watchdog_stop_request_id', watchdog_stop_request_id,
      'direct_stop_request_id', direct_stop_request_id,
      'release_request_id', release_request_id,
      'nonce_sha256', nonce_sha256, 'state', state,
      'acquired_at', acquired_at, 'heartbeat_at', heartbeat_at,
      'watchdog_at', watchdog_at, 'expires_at', expires_at,
      'released_at', released_at, 'stop_command_id', stop_command_id
    ) FROM paper_canary_safety_leases WHERE lease_id='__LEASE_ID__'
  ),
  'active_canary_leases', (
    SELECT count(*) FROM paper_canary_safety_leases WHERE state='active'
  ),
  'active_epoch', (
    SELECT jsonb_build_object(
      'id', id, 'opened_at', opened_at, 'cohort', cohort, 'image_digest', image_digest
    ) FROM active_epoch
  ),
  'latest_health_audit', (
    SELECT jsonb_build_object('ts', ts, 'status', status, 'issues', issues)
    FROM latest_audit
  ),
  'global_control', (
    SELECT jsonb_build_object(
      'mode', mode::text, 'kill_switch', kill_switch,
      'halted_reason', halted_reason,
      'live_confirmation_until', live_confirmation_until
    ) FROM runtime_control WHERE id=1
  ),
  'venues', (
    SELECT coalesce(jsonb_object_agg(
      venue::text,
      jsonb_build_object(
        'enabled', enabled, 'mode', mode::text, 'kill_switch', kill_switch,
        'halted_reason', halted_reason,
        'live_confirmation_until', live_confirmation_until
      )
    ), '{}'::jsonb) FROM venue_runtime_control
  ),
  'pending_commands', (
    SELECT count(*) FROM operator_commands WHERE status='pending'
  ),
  'pending_signals', (
    SELECT count(*) FROM signals WHERE status='pending'::signal_status
  ),
  'unexpected_pending_commands', (
    SELECT count(*) FROM operator_commands
    WHERE status='pending' AND request_id <> '__RESUME_REQUEST_ID__'
  ),
  'unexpected_commands_since_boundary', (
    SELECT count(*) FROM operator_commands
    WHERE created_at >= '__COMMAND_BOUNDARY_TIME__'::timestamptz
      AND request_id NOT IN ('__RESUME_REQUEST_ID__','__STOP_REQUEST_ID__')
  ),
  'runtime_database', __DATABASE_SESSION_SQL__,
  'scheduler', jsonb_build_object(
    'latest_maintenance_age_seconds', (
      SELECT extract(epoch FROM clock_timestamp()-max(finished_at))
      FROM job_runs
      WHERE job_id='execution-cost-maintenance' AND status='success'
    ),
    'recent_maintenance_failures', (
      SELECT count(*) FROM job_runs
      WHERE job_id='execution-cost-maintenance' AND status<>'success'
        AND scheduled_at >= clock_timestamp()-interval '2 minutes'
    ),
    'recent_legacy_runs', (
      SELECT count(*) FROM job_runs
      WHERE job_id IN ('execution-markouts','execution-cost-finalization')
        AND scheduled_at >= clock_timestamp()-interval '2 minutes'
    )
  ),
  'live', jsonb_build_object(
    'orders', (SELECT count(*) FROM orders WHERE mode='live'::execution_mode),
    'positions', (SELECT count(*) FROM positions WHERE mode='live'::execution_mode),
    'intents', (
      SELECT count(*) FROM execution_order_intents WHERE mode='live'::execution_mode
    ),
    'fill_legs', (
      SELECT count(*) FROM execution_fill_legs WHERE mode='live'::execution_mode
    ),
    'enrollments', (
      SELECT count(*) FROM prospective_validation_enrollments
      WHERE mode='live'::execution_mode
    ),
    'outcomes', (
      SELECT count(*)
      FROM prospective_validation_outcomes outcome
      JOIN prospective_validation_enrollments enrollment
        ON enrollment.id=outcome.enrollment_id
      WHERE enrollment.mode='live'::execution_mode
    )
  ),
  'api_expected', jsonb_build_object(
    'overview', jsonb_build_object(
      'mode', (SELECT mode::text FROM runtime_control WHERE id=1),
      'kill_switch', (SELECT kill_switch FROM runtime_control WHERE id=1),
      'active_markets', (
        SELECT count(*) FROM markets WHERE status::text='active'
      ),
      'blocked_markets', (
        SELECT count(*) FROM markets WHERE status::text='blocked'
      ),
      'open_positions', (
        SELECT count(*) FROM positions
        WHERE status::text='open'
          AND mode=(SELECT mode FROM runtime_control WHERE id=1)
      ),
      'pending_signals', (
        SELECT count(*) FROM signals WHERE status::text='pending'
      ),
      'bankroll_usd', (
        SELECT balance_usd FROM bankroll_log
        WHERE mode=(SELECT mode FROM runtime_control WHERE id=1)
        ORDER BY ts DESC LIMIT 1
      ),
      'open_exposure_usd', (
        SELECT coalesce(sum(size_usd),0) FROM positions
        WHERE status::text='open'
          AND mode=(SELECT mode FROM runtime_control WHERE id=1)
      )
    ),
    'venue_count', (SELECT count(*) FROM venue_runtime_control),
    'command_ids', (
      SELECT coalesce(jsonb_agg(id ORDER BY id DESC), '[]'::jsonb)
      FROM (
        SELECT id FROM operator_commands ORDER BY id DESC LIMIT 200
      ) recent_commands
    ),
    'job_ids', (
      SELECT coalesce(jsonb_agg(id ORDER BY id DESC), '[]'::jsonb)
      FROM (
        SELECT id FROM job_runs ORDER BY finished_at DESC LIMIT 200
      ) recent_jobs
    ),
    'execution_cost', (
      SELECT jsonb_build_object(
        'opened_at', runtime.opened_at,
        'definition_version', runtime.definition_version,
        'definition_hash', runtime.definition_hash,
        'attempts', count(DISTINCT intent.id),
        'filled_attempts', count(DISTINCT intent.id) FILTER (
          WHERE EXISTS (
            SELECT 1 FROM execution_fill_legs leg WHERE leg.intent_id=intent.id
          )
        ),
        'no_fill_attempts', count(DISTINCT seal.id) FILTER (
          WHERE seal.eligibility_reason='no_fill'
        ),
        'sealed_attempts', count(DISTINCT seal.id),
        'pending_attempts', greatest(
          0,
          count(DISTINCT intent.id)-count(DISTINCT seal.id)
        ),
        'maker_attempts', count(DISTINCT intent.id) FILTER (
          WHERE intent.order_style='maker'
        ),
        'maker_fills', count(DISTINCT intent.id) FILTER (
          WHERE intent.order_style='maker'
            AND EXISTS (
              SELECT 1 FROM execution_fill_legs leg WHERE leg.intent_id=intent.id
            )
        )
      )
      FROM execution_cost_runtime runtime
      LEFT JOIN execution_order_intents intent
        ON intent.mode='paper'::execution_mode
      LEFT JOIN execution_cost_seals seal ON seal.intent_id=intent.id
      WHERE runtime.id=1
      GROUP BY runtime.opened_at, runtime.definition_version, runtime.definition_hash
    ),
    'validation_ledger', jsonb_build_object(
      'selected_pipeline_cohort', (SELECT cohort FROM active_epoch),
      'enrolled_orders', (
        SELECT count(*) FROM prospective_validation_enrollments enrollment
        JOIN execution_order_intents intent ON intent.order_id=enrollment.order_id
        JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
        WHERE enrollment.mode='paper'::execution_mode
      ),
      'pending_outcomes', (
        SELECT count(*) FROM prospective_validation_enrollments enrollment
        JOIN execution_order_intents intent ON intent.order_id=enrollment.order_id
        JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
        LEFT JOIN prospective_validation_outcomes outcome
          ON outcome.enrollment_id=enrollment.id
        WHERE enrollment.mode='paper'::execution_mode AND outcome.id IS NULL
      )
    )
  ),
  'jobs', jsonb_build_object(
    'maintenance_runs', (
      SELECT count(*) FROM maintenance
      WHERE scheduled_at >= '__ANCHOR_DATABASE_TIME__'::timestamptz
    ),
    'maintenance_success', (
      SELECT count(*) FROM maintenance
      WHERE status='success'
        AND scheduled_at >= '__ANCHOR_DATABASE_TIME__'::timestamptz
    ),
    'maintenance_failures', (
      SELECT count(*) FROM maintenance
      WHERE status<>'success'
        AND scheduled_at >= '__ANCHOR_DATABASE_TIME__'::timestamptz
    ),
    'max_scheduled_gap_seconds', (
      SELECT max(gap) FROM maintenance
      WHERE status='success'
        AND scheduled_at >= '__ANCHOR_DATABASE_TIME__'::timestamptz
    ),
    'first_success_delay_seconds', (
      SELECT extract(
        epoch FROM min(scheduled_at)-'__ANCHOR_DATABASE_TIME__'::timestamptz
      )
      FROM maintenance
      WHERE status='success'
        AND scheduled_at >= '__ANCHOR_DATABASE_TIME__'::timestamptz
    ),
    'latest_maintenance_at', (
      SELECT max(finished_at) FROM maintenance WHERE status='success'
    ),
    'latest_maintenance_age_seconds', (
      SELECT extract(epoch FROM clock_timestamp()-max(finished_at))
      FROM maintenance WHERE status='success'
    ),
    'legacy_runs', (
      SELECT count(*) FROM job_runs
      WHERE job_id IN ('execution-markouts','execution-cost-finalization')
        AND scheduled_at >= '__ANCHOR_DATABASE_TIME__'::timestamptz
    )
  ),
  'execution', jsonb_build_object(
    'maker_intents', (
      SELECT count(*) FROM execution_order_intents intent
      JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
      WHERE intent.mode='paper'::execution_mode AND intent.order_style='maker'
    ),
    'resting_orders', (
      SELECT count(*) FROM orders ord
      JOIN execution_order_intents intent ON intent.order_id=ord.id
      JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
      WHERE ord.mode='paper'::execution_mode AND ord.status='paper_resting'
    ),
    'fill_legs', (
      SELECT count(*) FROM execution_fill_legs leg
      JOIN execution_order_intents intent ON intent.id=leg.intent_id
      JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
      WHERE leg.mode='paper'::execution_mode
    ),
    'cost_seals', (
      SELECT count(*) FROM execution_cost_seals seal
      JOIN execution_order_intents intent ON intent.id=seal.intent_id
      JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
    ),
    'mark_5m_observed', (
      SELECT count(*) FROM required_marks
      WHERE horizon_seconds=300 AND mark_status='observed'
    ),
    'mark_5m_missing', (
      SELECT count(*) FROM required_marks
      WHERE horizon_seconds=300 AND mark_status='missing'
    ),
    'mark_5m_unresolved', (
      SELECT count(*) FROM required_marks
      WHERE horizon_seconds=300 AND mark_id IS NULL
    ),
    'mark_30m_observed', (
      SELECT count(*) FROM required_marks
      WHERE horizon_seconds=1800 AND mark_status='observed'
    ),
    'mark_30m_missing', (
      SELECT count(*) FROM required_marks
      WHERE horizon_seconds=1800 AND mark_status='missing'
    ),
    'mark_30m_unresolved', (
      SELECT count(*) FROM required_marks
      WHERE horizon_seconds=1800 AND mark_id IS NULL
    ),
    'waiting_for_marks', (
      SELECT count(*) FROM required_marks WHERE mark_id IS NULL
    ),
    'cohort_waiting_obligations', (
      SELECT count(*)
      FROM execution_cost_obligations obligation
      JOIN execution_order_intents intent ON intent.id=obligation.intent_id
      JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
      WHERE obligation.state IN ('pending','waiting_for_marks')
    ),
    'global_waiting_obligations', (
      SELECT count(*)
      FROM execution_cost_obligations obligation
      JOIN execution_order_intents intent ON intent.id=obligation.intent_id
      WHERE intent.mode='paper'::execution_mode
        AND obligation.state IN ('pending','waiting_for_marks')
    ),
    'cohort_post_grace_missing_marks', (
      SELECT count(*) FROM required_marks
      WHERE mark_id IS NULL AND grace_at < clock_timestamp()
    ),
    'global_post_grace_missing_marks', (
      SELECT count(*) FROM global_required_marks
      WHERE mark_id IS NULL AND grace_at < clock_timestamp()
    ),
    'oldest_due_age_seconds', (
      SELECT greatest(0, extract(epoch FROM clock_timestamp()-min(target_at)))
      FROM required_marks WHERE mark_id IS NULL
    ),
    'duplicate_marks', (
      SELECT coalesce(sum(extra),0) FROM (
        SELECT count(*)-1 AS extra FROM execution_postfill_marks mark
        JOIN execution_fill_legs leg ON leg.id=mark.fill_leg_id
        JOIN execution_order_intents intent ON intent.id=leg.intent_id
        JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
        GROUP BY mark.fill_leg_id, mark.horizon_seconds HAVING count(*)>1
      ) duplicates
    ),
    'duplicate_seals', (
      SELECT coalesce(sum(extra),0) FROM (
        SELECT count(*)-1 AS extra FROM execution_cost_seals seal
        JOIN execution_order_intents intent ON intent.id=seal.intent_id
        JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
        GROUP BY seal.obligation_id HAVING count(*)>1
      ) duplicates
    ),
    'finalizable_unsealed', (
      SELECT count(*)
      FROM execution_cost_obligations obligation
      JOIN execution_order_intents intent ON intent.id=obligation.intent_id
      WHERE intent.mode='paper'::execution_mode
        AND obligation.state IN ('pending','waiting_for_marks')
        AND obligation.next_due_at <= clock_timestamp()
        AND NOT EXISTS (
          SELECT 1
          FROM execution_fill_legs leg
          CROSS JOIN execution_cost_runtime runtime
          CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) horizon(seconds)
          LEFT JOIN execution_postfill_marks mark
            ON mark.fill_leg_id=leg.id AND mark.horizon_seconds=horizon.seconds
          WHERE leg.intent_id=obligation.intent_id
            AND leg.fill_effective_at IS NOT NULL
            AND mark.id IS NULL
        )
        AND NOT EXISTS (
          SELECT 1 FROM execution_cost_seals seal
          WHERE seal.obligation_id=obligation.id
        )
        AND coalesce(
          (
            SELECT max(mark.sealed_at)
            FROM execution_fill_legs leg
            JOIN execution_postfill_marks mark ON mark.fill_leg_id=leg.id
            WHERE leg.intent_id=obligation.intent_id
          ),
          obligation.next_due_at
        ) <= clock_timestamp()-interval '20 seconds'
    ),
    'finalizable_unsealed_ids', (
      SELECT coalesce(jsonb_agg(candidate.id ORDER BY candidate.id), '[]'::jsonb)
      FROM (
        SELECT obligation.id
        FROM execution_cost_obligations obligation
        JOIN execution_order_intents intent ON intent.id=obligation.intent_id
        WHERE intent.mode='paper'::execution_mode
          AND obligation.state IN ('pending','waiting_for_marks')
          AND obligation.next_due_at <= clock_timestamp()
          AND NOT EXISTS (
            SELECT 1
            FROM execution_fill_legs leg
            CROSS JOIN execution_cost_runtime runtime
            CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) horizon(seconds)
            LEFT JOIN execution_postfill_marks mark
              ON mark.fill_leg_id=leg.id AND mark.horizon_seconds=horizon.seconds
            WHERE leg.intent_id=obligation.intent_id
              AND leg.fill_effective_at IS NOT NULL
              AND mark.id IS NULL
          )
          AND NOT EXISTS (
            SELECT 1 FROM execution_cost_seals seal
            WHERE seal.obligation_id=obligation.id
          )
          AND coalesce(
            (
              SELECT max(mark.sealed_at)
              FROM execution_fill_legs leg
              JOIN execution_postfill_marks mark ON mark.fill_leg_id=leg.id
              WHERE leg.intent_id=obligation.intent_id
            ),
            obligation.next_due_at
          ) <= clock_timestamp()-interval '20 seconds'
        ORDER BY obligation.id
        LIMIT 100
      ) candidate
    ),
    'seals_before_required_marks', (
      SELECT count(*)
      FROM execution_cost_seals seal
      JOIN execution_order_intents intent ON intent.id=seal.intent_id
      JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
      JOIN execution_fill_legs leg ON leg.intent_id=intent.id
      CROSS JOIN execution_cost_runtime runtime
      CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) horizon(seconds)
      LEFT JOIN execution_postfill_marks mark
        ON mark.fill_leg_id=leg.id AND mark.horizon_seconds=horizon.seconds
      WHERE leg.fill_effective_at IS NOT NULL
        AND (mark.id IS NULL OR mark.sealed_at > seal.sealed_at)
    ),
    'invalid_record_hashes', (
      SELECT count(*) FROM cost_hashes
      WHERE stored_hash IS DISTINCT FROM execution_cost_hash(payload)
    ),
    'hash_rows_checked', (SELECT count(*) FROM cost_hashes),
    'enrollments', (
      SELECT count(*) FROM prospective_validation_enrollments enrollment
      JOIN execution_order_intents intent ON intent.order_id=enrollment.order_id
      JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
      WHERE enrollment.mode='paper'::execution_mode
    ),
    'outcomes', (
      SELECT count(*) FROM prospective_validation_outcomes outcome
      JOIN prospective_validation_enrollments enrollment
        ON enrollment.id=outcome.enrollment_id
      JOIN execution_order_intents intent ON intent.order_id=enrollment.order_id
      JOIN active_epoch epoch ON epoch.id=intent.release_epoch_id
      WHERE enrollment.mode='paper'::execution_mode
    )
  )
)::text;
COMMIT;
'@
    return $template.Replace(
        '__ANCHOR_DATABASE_TIME__',
        $AnchorDatabaseTime.Replace("'", "''")
    ).Replace(
        '__RESUME_REQUEST_ID__',
        $ExpectedResumeRequestId.Replace("'", "''")
    ).Replace(
        '__STOP_REQUEST_ID__',
        $stopRequestId.Replace("'", "''")
    ).Replace(
        '__COMMAND_BOUNDARY_TIME__',
        $CommandBoundaryTime.Replace("'", "''")
    ).Replace(
        '__LEASE_ID__',
        $leaseId.Replace("'", "''")
    ).Replace(
        '__RELEASE_REQUEST_ID__',
        $leaseReleaseRequestId.Replace("'", "''")
    ).Replace(
        '__DATABASE_SESSION_SQL__',
        $databaseSessionSql
    )
}

function Get-DatabaseSessionGuardViolations {
    param([Parameter(Mandatory = $true)][object] $State)

    $violations = [Collections.Generic.List[string]]::new()
    $runtimeDatabase = Get-OptionalProperty `
        -InputObject $State -Name 'runtime_database'
    if ($null -eq $runtimeDatabase) {
        return @('database_session_inventory_missing')
    }
    $requiredRootProperties = @(
        'database_name',
        'collector',
        'collector_exact',
        'expected_identities',
        'client_backends',
        'client_backend_sessions',
        'api_sessions',
        'worker_sessions',
        'safety_control_sessions',
        'unexpected_sessions',
        'idle_in_transaction',
        'lock_waiters'
    )
    if (@(
        $requiredRootProperties |
            Where-Object {
                $null -eq $runtimeDatabase.PSObject.Properties[[string]$_]
            }
    ).Count -ne 0) {
        return @('database_session_inventory_missing')
    }
    if ([string]$runtimeDatabase.database_name -ne 'stratus') {
        $violations.Add('database_session_identity_boundary')
    }

    $collector = $runtimeDatabase.collector
    if (
        $null -eq $collector -or
        $null -eq $collector.PSObject.Properties['pid'] -or
        $null -eq $collector.PSObject.Properties['backend_type'] -or
        $null -eq $collector.PSObject.Properties['usename'] -or
        $null -eq $collector.PSObject.Properties['current_user'] -or
        $null -eq $collector.PSObject.Properties['application_name'] -or
        $null -eq $collector.PSObject.Properties['client_addr'] -or
        -not [bool]$runtimeDatabase.collector_exact -or
        [long]$collector.pid -le 0 -or
        [string]$collector.backend_type -ne 'client backend' -or
        [string]$collector.usename -ne 'stratus' -or
        [string]$collector.current_user -ne 'stratus' -or
        [string]$collector.application_name -ne
            'stratus-paper-canary-collector' -or
        $null -ne $collector.client_addr
    ) {
        $violations.Add('database_collector_identity')
    }

    $profileContracts = [ordered]@{
        api = [pscustomobject]@{
            application_name = 'stratus-api'
            max_sessions = 4
        }
        worker = [pscustomobject]@{
            application_name = 'stratus-runtime'
            max_sessions = 15
        }
        safety_control = [pscustomobject]@{
            application_name = 'stratus-safety-control'
            max_sessions = 2
        }
    }
    $profiles = @{}
    $runtimeRole = $null
    $runtimeRoleSuffix = $null
    foreach ($identity in $profileContracts.Keys) {
        $profile = Get-OptionalProperty `
            -InputObject $runtimeDatabase.expected_identities -Name $identity
        if ($null -eq $profile) {
            $violations.Add('database_session_identity_boundary')
            continue
        }
        $addresses = @(
            @(Get-OptionalProperty -InputObject $profile -Name 'client_addresses') |
                Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } |
                Sort-Object -Unique
        )
        $addressesValid = $addresses.Count -gt 0
        foreach ($address in $addresses) {
            $parsedAddress = $null
            if (-not [Net.IPAddress]::TryParse([string]$address, [ref]$parsedAddress)) {
                $addressesValid = $false
            }
        }
        if (
            [string](Get-OptionalProperty -InputObject $profile -Name 'backend_type') -ne
                'client backend' -or
            [string](Get-OptionalProperty -InputObject $profile -Name 'application_name') -ne
                [string]$profileContracts[$identity].application_name -or
            [int](Get-OptionalProperty -InputObject $profile -Name 'max_sessions') -ne
                [int]$profileContracts[$identity].max_sessions -or
            -not $addressesValid
        ) {
            $violations.Add('database_session_identity_boundary')
        }
        $profiles[$identity] = [pscustomobject]@{
            backend_type = [string](
                Get-OptionalProperty -InputObject $profile -Name 'backend_type'
            )
            usename = [string](
                Get-OptionalProperty -InputObject $profile -Name 'usename'
            )
            application_name = [string](
                Get-OptionalProperty -InputObject $profile -Name 'application_name'
            )
            client_addresses = @($addresses)
            max_sessions = [int](
                Get-OptionalProperty -InputObject $profile -Name 'max_sessions'
            )
        }
        if ($identity -eq 'api') {
            $runtimeRole = [string]$profiles[$identity].usename
            if ($runtimeRole -match '^stratus_app_([0-9a-f]{32})$') {
                $runtimeRoleSuffix = [string]$Matches[1]
            }
        }
    }
    if (
        [string]::IsNullOrWhiteSpace($runtimeRoleSuffix) -or
        -not $profiles.ContainsKey('worker') -or
        [string]$profiles.worker.usename -ne $runtimeRole -or
        -not $profiles.ContainsKey('safety_control') -or
        [string]$profiles.safety_control.usename -ne
            "stratus_safety_app_$runtimeRoleSuffix"
    ) {
        $violations.Add('database_session_identity_boundary')
    }

    $rows = @($runtimeDatabase.client_backends)
    $observed = @{
        api = 0
        worker = 0
        safety_control = 0
        unexpected = 0
    }
    $pids = [Collections.Generic.HashSet[long]]::new()
    foreach ($row in $rows) {
        if (
            $null -eq $row -or
            $null -eq $row.PSObject.Properties['pid'] -or
            $null -eq $row.PSObject.Properties['backend_type'] -or
            $null -eq $row.PSObject.Properties['usename'] -or
            $null -eq $row.PSObject.Properties['application_name'] -or
            $null -eq $row.PSObject.Properties['client_addr'] -or
            $null -eq $row.PSObject.Properties['identity']
        ) {
            $violations.Add('database_session_inventory_inconsistent')
            continue
        }
        $identity = [string]$row.identity
        if (-not $observed.ContainsKey($identity)) {
            $identity = 'unexpected'
        }
        $observed[$identity] += 1
        $sessionPid = [long]$row.pid
        if (
            $sessionPid -le 0 -or
            -not $pids.Add($sessionPid) -or
            ($null -ne $collector -and $sessionPid -eq [long]$collector.pid)
        ) {
            $violations.Add('database_session_inventory_inconsistent')
        }
        if ($identity -eq 'unexpected' -or -not $profiles.ContainsKey($identity)) {
            $violations.Add('unexpected_database_session')
            continue
        }
        $profile = $profiles[$identity]
        if (
            [string]$row.backend_type -ne [string]$profile.backend_type -or
            [string]$row.usename -ne [string]$profile.usename -or
            [string]$row.application_name -ne [string]$profile.application_name -or
            [string]$row.client_addr -notin @($profile.client_addresses)
        ) {
            $violations.Add('unexpected_database_session')
        }
    }
    if (
        [int]$runtimeDatabase.client_backend_sessions -ne $rows.Count -or
        [int]$runtimeDatabase.api_sessions -ne [int]$observed.api -or
        [int]$runtimeDatabase.worker_sessions -ne [int]$observed.worker -or
        [int]$runtimeDatabase.safety_control_sessions -ne
            [int]$observed.safety_control -or
        [int]$runtimeDatabase.unexpected_sessions -ne [int]$observed.unexpected
    ) {
        $violations.Add('database_session_inventory_inconsistent')
    }
    if ([int]$runtimeDatabase.unexpected_sessions -ne 0) {
        $violations.Add('unexpected_database_session')
    }
    foreach ($identity in $profileContracts.Keys) {
        if (
            $profiles.ContainsKey($identity) -and
            [int]$observed[$identity] -gt [int]$profiles[$identity].max_sessions
        ) {
            $violations.Add('database_session_pool_budget')
        }
    }
    if ([int]$runtimeDatabase.idle_in_transaction -ne 0) {
        $violations.Add('database_idle_in_transaction')
    }
    if ([int]$runtimeDatabase.lock_waiters -ne 0) {
        $violations.Add('database_lock_waiter')
    }
    return @($violations | Select-Object -Unique)
}

function Test-GuardState {
    param([Parameter(Mandatory = $true)][object] $State)

    $violations = [Collections.Generic.List[string]]::new()
    if (
        $null -eq $State.PSObject.Properties['database_boundaries'] -or
        -not [bool]$State.database_boundaries.runtime_exact -or
        -not [bool]$State.database_boundaries.safety_exact
    ) {
        $violations.Add('database_authority_boundary_drifted')
    }
    try {
        Assert-ActiveCanaryLease -Lease $State.canary_lease `
            -DatabaseNow ([DateTimeOffset]$State.database_now) `
            -ActiveLeaseCount ([int]$State.active_canary_leases)
    }
    catch {
        $violations.Add('paper_canary_safety_lease_diverged')
    }
    if (
        $null -eq $State.global_control -or
        [string]$State.global_control.mode -ne 'paper' -or
        [bool]$State.global_control.kill_switch -or
        $null -ne $State.global_control.halted_reason -or
        $null -ne $State.global_control.live_confirmation_until
    ) {
        $violations.Add('global_control_diverged')
    }
    $polymarket = $State.venues.polymarket
    $binance = $State.venues.binance_spot
    if (
        $null -eq $polymarket -or -not [bool]$polymarket.enabled -or
        [string]$polymarket.mode -ne 'paper' -or [bool]$polymarket.kill_switch -or
        $null -ne $polymarket.halted_reason -or
        $null -ne $polymarket.live_confirmation_until
    ) {
        $violations.Add('polymarket_control_diverged')
    }
    if (
        $null -eq $binance -or -not [bool]$binance.enabled -or
        [string]$binance.mode -ne 'backtest' -or -not [bool]$binance.kill_switch -or
        $null -ne $binance.live_confirmation_until
    ) {
        $violations.Add('binance_control_diverged')
    }
    if ([int]$State.pending_commands -ne 0) {
        $violations.Add('pending_operator_commands')
    }
    if (
        $null -eq $State.PSObject.Properties['pending_signals'] -or
        [int]$State.pending_signals -ne 0
    ) {
        $violations.Add('pending_signals_present')
    }
    if ([int]$State.unexpected_pending_commands -ne 0) {
        $violations.Add('unexpected_pending_operator_commands')
    }
    if ([int]$State.unexpected_commands_since_boundary -ne 0) {
        $violations.Add('unexpected_operator_command_since_preflight')
    }
    if ($null -ne $State.stop) {
        $violations.Add('emergency_stop_request_id_collision')
    }
    if ($null -ne $State.release_stop) {
        $violations.Add('canary_release_request_id_collision')
    }
    foreach ($property in @('orders','positions','intents','fill_legs','enrollments','outcomes')) {
        if ([int]$State.live.PSObject.Properties[$property].Value -ne 0) {
            $violations.Add("unexpected_live_$property")
        }
    }
    foreach ($item in @(Get-DatabaseSessionGuardViolations -State $State)) {
        $violations.Add($item)
    }
    if ($null -ne $State.PSObject.Properties['scheduler']) {
        if (
            $null -eq $State.scheduler.latest_maintenance_age_seconds -or
            [double]$State.scheduler.latest_maintenance_age_seconds -gt 20 -or
            [int]$State.scheduler.recent_maintenance_failures -ne 0 -or
            [int]$State.scheduler.recent_legacy_runs -ne 0
        ) {
            $violations.Add('scheduler_maintenance_not_fresh')
        }
    }
    $obligationState = if ($null -ne $State.PSObject.Properties['execution_guard']) {
        $State.execution_guard
    }
    elseif ($null -ne $State.PSObject.Properties['execution']) {
        [pscustomobject]@{
            post_grace_missing_marks = $State.execution.global_post_grace_missing_marks
            finalizable_unsealed = $State.execution.finalizable_unsealed
        }
    }
    else {
        $null
    }
    if (
        $null -eq $obligationState -or
        [int]$obligationState.post_grace_missing_marks -ne 0 -or
        [int]$obligationState.finalizable_unsealed -ne 0
    ) {
        $violations.Add('global_execution_obligation_stalled')
    }
    return @($violations)
}

function Test-FailClosedGuardState {
    param([Parameter(Mandatory = $true)][object] $State)

    $violations = [Collections.Generic.List[string]]::new()
    if (
        $null -eq $State.PSObject.Properties['database_boundaries'] -or
        -not [bool]$State.database_boundaries.runtime_exact -or
        -not [bool]$State.database_boundaries.safety_exact
    ) {
        $violations.Add('database_authority_boundary_drifted')
    }
    if (
        $null -eq $State.global_control -or
        [string]$State.global_control.mode -ne 'paper' -or
        -not [bool]$State.global_control.kill_switch -or
        $null -ne $State.global_control.live_confirmation_until
    ) {
        $violations.Add('global_not_fail_closed')
    }
    $polymarket = $State.venues.polymarket
    $binance = $State.venues.binance_spot
    if (
        $null -eq $polymarket -or -not [bool]$polymarket.enabled -or
        [string]$polymarket.mode -ne 'paper' -or
        -not [bool]$polymarket.kill_switch -or
        $null -ne $polymarket.halted_reason -or
        $null -ne $polymarket.live_confirmation_until
    ) {
        $violations.Add('polymarket_not_fail_closed')
    }
    if (
        $null -eq $binance -or -not [bool]$binance.enabled -or
        [string]$binance.mode -ne 'backtest' -or
        -not [bool]$binance.kill_switch -or
        $null -ne $binance.live_confirmation_until
    ) {
        $violations.Add('binance_not_fail_closed')
    }
    if ([int]$State.unexpected_pending_commands -ne 0) {
        $violations.Add('unexpected_pending_operator_commands')
    }
    if (
        $null -eq $State.PSObject.Properties['pending_signals'] -or
        [int]$State.pending_signals -ne 0
    ) {
        $violations.Add('pending_signals_present')
    }
    if ([int]$State.unexpected_commands_since_boundary -ne 0) {
        $violations.Add('unexpected_operator_command_since_preflight')
    }
    if ($null -ne $State.stop) {
        $violations.Add('emergency_stop_request_id_collision')
    }
    if ($null -ne $State.release_stop) {
        $violations.Add('canary_release_request_id_collision')
    }
    foreach ($property in @('orders','positions','intents','fill_legs','enrollments','outcomes')) {
        if ([int]$State.live.PSObject.Properties[$property].Value -ne 0) {
            $violations.Add("unexpected_live_$property")
        }
    }
    foreach ($item in @(Get-DatabaseSessionGuardViolations -State $State)) {
        $violations.Add($item)
    }
    if (
        $null -eq $State.scheduler.latest_maintenance_age_seconds -or
        [double]$State.scheduler.latest_maintenance_age_seconds -gt 20 -or
        [int]$State.scheduler.recent_maintenance_failures -ne 0 -or
        [int]$State.scheduler.recent_legacy_runs -ne 0
    ) {
        $violations.Add('scheduler_maintenance_not_fresh')
    }
    $obligationState = if ($null -ne $State.PSObject.Properties['execution_guard']) {
        $State.execution_guard
    }
    elseif ($null -ne $State.PSObject.Properties['execution']) {
        [pscustomobject]@{
            post_grace_missing_marks = $State.execution.global_post_grace_missing_marks
            finalizable_unsealed = $State.execution.finalizable_unsealed
        }
    }
    else {
        $null
    }
    if (
        $null -eq $obligationState -or
        [int]$obligationState.post_grace_missing_marks -ne 0 -or
        [int]$obligationState.finalizable_unsealed -ne 0
    ) {
        $violations.Add('global_execution_obligation_stalled')
    }
    return @($violations)
}

function Assert-ExactResume {
    param(
        [Parameter(Mandatory = $true)][object] $Resume,
        [Parameter(Mandatory = $true)][DateTimeOffset] $DatabaseNow
    )

    if (
        $null -eq $Resume -or
        [string]$Resume.request_id -ne $ExpectedResumeRequestId -or
        [string]$Resume.command -ne 'resume' -or
        [string]$Resume.actor -ne $ExpectedResumeActor -or
        [string]$Resume.reason -ne $ExpectedResumeReason -or
        @($Resume.payload.PSObject.Properties).Count -ne 1 -or
        [string]$Resume.payload.venue -ne 'polymarket'
    ) {
        throw 'Expected resume command identity or payload does not match.'
    }
    if (
        $null -ne $script:preflightDatabaseTime -and
        [DateTimeOffset]$Resume.created_at -lt [DateTimeOffset]$script:preflightDatabaseTime
    ) {
        throw 'Expected resume command predates the sealed collector preflight.'
    }
    $createdAt = [DateTimeOffset]$Resume.created_at
    $status = [string]$Resume.status
    if ($status -eq 'pending') {
        if ($null -ne $Resume.executed_at) {
            throw 'Pending resume command unexpectedly has an execution timestamp.'
        }
        return
    }
    if ($status -ne 'succeeded' -or $null -eq $Resume.executed_at) {
        throw "Expected resume command has unsafe terminal status: $status."
    }
    $executedAt = [DateTimeOffset]$Resume.executed_at
    $clockTolerance = [TimeSpan]::FromSeconds(5)
    if (
        $executedAt -lt $createdAt.Subtract($clockTolerance) -or
        $executedAt -gt $DatabaseNow.Add($clockTolerance)
    ) {
        throw 'Expected resume execution timestamp is outside the bounded clock ordering.'
    }
}

function New-StopBody {
    param([Parameter(Mandatory = $true)][string] $RunId)

    return [ordered]@{
        request_id = "paper-canary-stop-$RunId"
        command = 'stop'
        reason = 'STRATUS PAPER canary terminal fail-closed stop.'
        actor = 'codex-paper-canary'
        payload = [ordered]@{ venue = 'polymarket' }
    }
}

function Assert-ExactStopItem {
    param(
        [Parameter(Mandatory = $true)][object] $Item,
        [Parameter(Mandatory = $true)][Collections.IDictionary] $Expected
    )

    Assert-NativeJsonObject -Value $Item -Context 'safety-control stop item'
    Assert-RequiredJsonObjectProperty -InputObject $Item -Name 'payload' `
        -Context 'safety-control stop item'
    if (
        [string]$Item.request_id -ne [string]$Expected.request_id -or
        [string]$Item.command -ne 'stop' -or
        [string]$Item.actor -ne [string]$Expected.actor -or
        [string]$Item.reason -ne [string]$Expected.reason -or
        @($Item.payload.PSObject.Properties).Count -ne 1 -or
        [string]$Item.payload.venue -ne 'polymarket'
    ) {
        throw 'Idempotent stop request collided with a different command payload.'
    }
}

function Assert-ExactCanaryLeaseItem {
    param(
        [Parameter(Mandatory = $true)][object] $Item,
        [Parameter(Mandatory = $true)][string] $ExpectedState
    )

    Assert-NativeJsonObject -Value $Item -Context 'safety-control canary lease item'
    if (
        [string]$Item.lease_id -ne $leaseId -or
        [string]$Item.run_id -ne $runId -or
        [string]$Item.acquire_request_id -ne $leaseAcquireRequestId -or
        [string]$Item.resume_request_id -ne $ExpectedResumeRequestId -or
        [string]$Item.release_request_id -ne $leaseReleaseRequestId -or
        [string]$Item.resume_actor -ne $ExpectedResumeActor -or
        [string]$Item.resume_reason -ne $ExpectedResumeReason -or
        [string]$Item.nonce_sha256 -ne $leaseNonceSha256 -or
        [string]$Item.state -ne $ExpectedState
    ) {
        throw 'PAPER canary lease identity, nonce hash, or state does not match.'
    }
}

function Get-CanaryLeaseState {
    param([Parameter(Mandatory = $true)][string] $PostgresContainer)

    $escapedLeaseId = $leaseId.Replace("'", "''")
    $state = Invoke-ReadOnlySql -PostgresContainer $PostgresContainer -Sql @"
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '3s';
SELECT jsonb_build_object(
  'database_now', clock_timestamp(),
  'lease', (
    SELECT jsonb_build_object(
      'id', id, 'lease_id', lease_id, 'run_id', run_id,
      'acquire_request_id', acquire_request_id,
      'resume_request_id', resume_request_id,
      'resume_actor', resume_actor, 'resume_reason', resume_reason,
      'watchdog_stop_request_id', watchdog_stop_request_id,
      'direct_stop_request_id', direct_stop_request_id,
      'release_request_id', release_request_id,
      'nonce_sha256', nonce_sha256, 'state', state,
      'acquired_at', acquired_at, 'heartbeat_at', heartbeat_at,
      'watchdog_at', watchdog_at, 'expires_at', expires_at,
      'released_at', released_at, 'stop_command_id', stop_command_id
    ) FROM paper_canary_safety_leases WHERE lease_id='$escapedLeaseId'
  ),
  'stop_command', (
    SELECT jsonb_build_object(
      'id', command.id, 'request_id', command.request_id,
      'command', command.command, 'payload', command.payload,
      'actor', command.actor, 'reason', command.reason,
      'status', command.status, 'detail', command.detail,
      'created_at', command.created_at, 'executed_at', command.executed_at
    )
    FROM paper_canary_safety_leases lease
    JOIN operator_commands command ON command.id=lease.stop_command_id
    WHERE lease.lease_id='$escapedLeaseId'
  ),
  'active_leases', (
    SELECT count(*) FROM paper_canary_safety_leases WHERE state='active'
  ),
  'global_control', (
    SELECT jsonb_build_object(
      'mode', mode::text, 'kill_switch', kill_switch,
      'halted_reason', halted_reason,
      'live_confirmation_until', live_confirmation_until
    ) FROM runtime_control WHERE id=1
  ),
  'polymarket', (
    SELECT jsonb_build_object(
      'enabled', enabled, 'mode', mode::text, 'kill_switch', kill_switch,
      'halted_reason', halted_reason,
      'live_confirmation_until', live_confirmation_until
    ) FROM venue_runtime_control WHERE venue='polymarket'::trading_venue
  ),
  'binance', (
    SELECT jsonb_build_object(
      'enabled', enabled, 'mode', mode::text, 'kill_switch', kill_switch,
      'halted_reason', halted_reason,
      'live_confirmation_until', live_confirmation_until
    ) FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue
  ),
  'pending_commands', (
    SELECT count(*) FROM operator_commands WHERE status='pending'
  ),
  'pending_signals', (
    SELECT count(*) FROM signals WHERE status='pending'::signal_status
  ),
  'unexpected_commands_since_boundary', (
    SELECT count(*) FROM operator_commands
    WHERE created_at >= '$($commandBoundaryTime.Replace("'", "''"))'::timestamptz
      AND request_id NOT IN (
        '$($ExpectedResumeRequestId.Replace("'", "''"))',
        '$($stopRequestId.Replace("'", "''"))',
        '$($leaseReleaseRequestId.Replace("'", "''"))'
      )
  ),
  'live_rows', (
    (SELECT count(*) FROM orders WHERE mode='live'::execution_mode)
    +(SELECT count(*) FROM positions WHERE mode='live'::execution_mode)
    +(SELECT count(*) FROM execution_order_intents WHERE mode='live'::execution_mode)
    +(SELECT count(*) FROM execution_fill_legs WHERE mode='live'::execution_mode)
    +(SELECT count(*) FROM prospective_validation_enrollments
      WHERE mode='live'::execution_mode)
    +(SELECT count(*)
      FROM prospective_validation_outcomes outcome
      JOIN prospective_validation_enrollments enrollment
        ON enrollment.id=outcome.enrollment_id
      WHERE enrollment.mode='live'::execution_mode)
  )
)::text;
COMMIT;
"@
    return $state
}

function Assert-ActiveCanaryLease {
    param(
        [Parameter(Mandatory = $true)][object] $Lease,
        [Parameter(Mandatory = $true)][DateTimeOffset] $DatabaseNow,
        [Parameter(Mandatory = $true)][int] $ActiveLeaseCount
    )

    Assert-ExactCanaryLeaseItem -Item $Lease -ExpectedState 'active'
    if (
        $ActiveLeaseCount -ne 1 -or
        [string]$Lease.direct_stop_request_id -ne
            $leaseDirectStopRequestId -or
        [DateTimeOffset]$Lease.expires_at -le $DatabaseNow -or
        [DateTimeOffset]$Lease.heartbeat_at -lt [DateTimeOffset]$Lease.acquired_at -or
        [DateTimeOffset]$Lease.watchdog_at -le [DateTimeOffset]$Lease.acquired_at -or
        ($DatabaseNow - [DateTimeOffset]$Lease.heartbeat_at).TotalSeconds -gt 10 -or
        ($DatabaseNow - [DateTimeOffset]$Lease.watchdog_at).TotalSeconds -gt 7
    ) {
        throw 'PAPER canary lease is not singular, fresh, or unexpired.'
    }
}

function Invoke-CanaryLeaseAcquire {
    param(
        [Parameter(Mandatory = $true)][Net.Http.HttpClient] $SafetyClient,
        [Parameter(Mandatory = $true)][string] $PostgresContainer
    )

    $body = [ordered]@{
        lease_id = $leaseId
        run_id = $runId
        acquire_request_id = $leaseAcquireRequestId
        resume_request_id = $ExpectedResumeRequestId
        direct_stop_request_id = $leaseDirectStopRequestId
        release_request_id = $leaseReleaseRequestId
        resume_actor = $ExpectedResumeActor
        resume_reason = $ExpectedResumeReason
        nonce = $leaseNonce
    }
    $postAttempts = 0
    $ambiguousFirstPost = $false
    while ($postAttempts -lt 2) {
        $postAttempts += 1
        $responseReceived = $false
        $retryableResponse = $false
        try {
            $response = Invoke-JsonRequest -Client $SafetyClient -Method POST `
                -Path 'api/v1/canary-leases/acquire' `
                -JsonBody ($body | ConvertTo-Json -Depth 4 -Compress)
            $responseReceived = $true
            $responseSchema = if ($response.status_code -eq 200) {
                'lease-success'
            }
            else {
                'error'
            }
            Assert-SafetyMutationJsonResponseShape -Json $response.json `
                -Context 'safety-control canary lease acquire response' `
                -Schema $responseSchema
            if ($response.status_code -ne 200 -or $null -eq $response.json.item) {
                $retryableValue = Get-OptionalProperty `
                    -InputObject $response.json -Name 'retryable'
                $retryableResponse = (
                    $response.status_code -eq 503 -and
                    $null -ne $retryableValue -and
                    $retryableValue
                )
                throw (
                    "PAPER canary lease was not acquired " +
                    "(HTTP $($response.status_code))."
                )
            }
            Assert-ExactCanaryLeaseItem `
                -Item $response.json.item -ExpectedState 'active'
            if ($postAttempts -eq 1 -and -not $response.json.created) {
                throw 'PAPER canary lease acquire request was not newly created.'
            }
            return [ordered]@{
                created = $response.json.created
                post_attempts = $postAttempts
                ambiguous_first_post = $ambiguousFirstPost
                item = $response.json.item
            }
        }
        catch {
            if ($responseReceived -and -not $retryableResponse) {
                throw
            }
            $observed = $null
            try {
                $observed = Get-CanaryLeaseState `
                    -PostgresContainer $PostgresContainer
                if ($null -ne $observed.lease) {
                    Assert-ExactCanaryLeaseItem `
                        -Item $observed.lease -ExpectedState 'active'
                    return [ordered]@{
                        created = $null
                        post_attempts = $postAttempts
                        ambiguous_first_post = $true
                        item = $observed.lease
                    }
                }
            }
            catch {
                if (
                    $_.Exception.GetBaseException().Message -match
                        'identity|nonce hash|state does not match'
                ) {
                    throw
                }
            }
            if ($postAttempts -eq 1) {
                $ambiguousFirstPost = $true
                continue
            }
            throw 'PAPER canary lease acquisition remains unconfirmed.'
        }
    }
    throw 'PAPER canary lease acquisition exceeded its two-attempt bound.'
}

function Invoke-CanaryLeaseHeartbeat {
    param([Parameter(Mandatory = $true)][Net.Http.HttpClient] $SafetyClient)

    $body = [ordered]@{ run_id = $runId; nonce = $leaseNonce }
    $response = Invoke-JsonRequest -Client $SafetyClient -Method POST `
        -Path "api/v1/canary-leases/$leaseId/heartbeat" `
        -JsonBody ($body | ConvertTo-Json -Depth 3 -Compress)
    $responseSchema = if ($response.status_code -eq 200) {
        'lease-success'
    }
    else {
        'error'
    }
    Assert-SafetyMutationJsonResponseShape -Json $response.json `
        -Context 'safety-control canary lease heartbeat response' `
        -Schema $responseSchema
    if ($response.status_code -ne 200 -or $null -eq $response.json.item) {
        throw (
            "PAPER canary lease heartbeat failed " +
            "(HTTP $($response.status_code))."
        )
    }
    Assert-ExactCanaryLeaseItem -Item $response.json.item -ExpectedState 'active'
    if ($response.json.created) {
        throw 'PAPER canary heartbeat unexpectedly created a lease.'
    }
    return $response.json.item
}

function Assert-ReleasedCanaryLeaseState {
    param(
        [Parameter(Mandatory = $true)][object] $State,
        [Parameter(Mandatory = $true)][object] $BinanceBaseline
    )

    Assert-ExactCanaryLeaseItem -Item $State.lease -ExpectedState 'released'
    if (
        [string]$State.lease.direct_stop_request_id -ne
            $leaseDirectStopRequestId -or
        [string]$State.lease.release_request_id -ne $leaseReleaseRequestId -or
        $null -eq $State.lease.released_at -or
        $null -eq $State.lease.stop_command_id -or
        [int]$State.active_leases -ne 0 -or
        $null -eq $State.stop_command -or
        [long]$State.stop_command.id -ne [long]$State.lease.stop_command_id -or
        [string]$State.stop_command.request_id -ne $leaseReleaseRequestId -or
        [string]$State.stop_command.command -ne 'stop' -or
        [string]$State.stop_command.actor -ne 'stratus-safety-release' -or
        [string]$State.stop_command.reason -ne
            "Successful PAPER canary $leaseId completed." -or
        @($State.stop_command.payload.PSObject.Properties).Count -ne 1 -or
        [string]$State.stop_command.payload.venue -ne 'polymarket' -or
        [string]$State.stop_command.status -ne 'succeeded' -or
        $null -eq $State.stop_command.executed_at -or
        [string]$State.global_control.mode -ne 'paper' -or
        -not [bool]$State.global_control.kill_switch -or
        $null -ne $State.global_control.live_confirmation_until -or
        -not [bool]$State.polymarket.enabled -or
        [string]$State.polymarket.mode -ne 'paper' -or
        -not [bool]$State.polymarket.kill_switch -or
        $null -ne $State.polymarket.live_confirmation_until -or
        [int]$State.pending_commands -ne 0 -or
        [int]$State.unexpected_commands_since_boundary -ne 0 -or
        [int]$State.live_rows -ne 0
    ) {
        throw 'Released PAPER canary lease is not atomically linked fail-closed.'
    }
    if (
        [bool]$State.binance.enabled -ne [bool]$BinanceBaseline.enabled -or
        [string]$State.binance.mode -ne [string]$BinanceBaseline.mode -or
        [bool]$State.binance.kill_switch -ne [bool]$BinanceBaseline.kill_switch -or
        [string]$State.binance.halted_reason -ne
            [string]$BinanceBaseline.halted_reason -or
        [string]$State.binance.live_confirmation_until -ne
            [string]$BinanceBaseline.live_confirmation_until
    ) {
        throw 'PAPER canary release changed the sealed Binance baseline.'
    }
}

function Invoke-CanaryLeaseRelease {
    param(
        [Parameter(Mandatory = $true)][Net.Http.HttpClient] $SafetyClient,
        [Parameter(Mandatory = $true)][string] $PostgresContainer,
        [Parameter(Mandatory = $true)][object] $BinanceBaseline
    )

    $body = [ordered]@{
        run_id = $runId
        nonce = $leaseNonce
        release_request_id = $leaseReleaseRequestId
        successful = $true
    }
    $postAttempts = 0
    $ambiguousFirstPost = $false
    $responseCreated = $null
    while ($postAttempts -lt 2) {
        $postAttempts += 1
        $responseReceived = $false
        $retryableResponse = $false
        try {
            $response = Invoke-JsonRequest -Client $SafetyClient -Method POST `
                -Path "api/v1/canary-leases/$leaseId/release" `
                -JsonBody ($body | ConvertTo-Json -Depth 4 -Compress)
            $responseReceived = $true
            $responseSchema = if ($response.status_code -eq 200) {
                'lease-success'
            }
            else {
                'error'
            }
            Assert-SafetyMutationJsonResponseShape -Json $response.json `
                -Context 'safety-control canary lease release response' `
                -Schema $responseSchema
            if ($response.status_code -ne 200 -or $null -eq $response.json.item) {
                $retryableValue = Get-OptionalProperty `
                    -InputObject $response.json -Name 'retryable'
                $retryableResponse = (
                    $response.status_code -eq 503 -and
                    $null -ne $retryableValue -and
                    $retryableValue
                )
                throw (
                    "PAPER canary lease release failed " +
                    "(HTTP $($response.status_code))."
                )
            }
            Assert-ExactCanaryLeaseItem `
                -Item $response.json.item -ExpectedState 'released'
            if (
                [string]$response.json.item.release_request_id -ne
                    $leaseReleaseRequestId
            ) {
                throw 'PAPER canary release response identity does not match.'
            }
            if ($postAttempts -eq 1 -and -not $response.json.created) {
                throw 'PAPER canary release request was not newly created.'
            }
            $responseCreated = $response.json.created
        }
        catch {
            if ($responseReceived -and -not $retryableResponse) {
                throw
            }
            $observed = $null
            try {
                $observed = Get-CanaryLeaseState `
                    -PostgresContainer $PostgresContainer
                if (
                    $null -ne $observed.lease -and
                    [string]$observed.lease.state -eq 'released'
                ) {
                    Assert-ReleasedCanaryLeaseState -State $observed `
                        -BinanceBaseline $BinanceBaseline
                    return [ordered]@{
                        release_request_id = $leaseReleaseRequestId
                        created = $null
                        post_attempts = $postAttempts
                        ambiguous_first_post = $true
                        status = 'released'
                        controls_confirmed = $true
                        confirmed_at = [string]$observed.database_now
                        lease = $observed.lease
                        stop_command = $observed.stop_command
                    }
                }
                if (
                    $null -ne $observed.lease -and
                    [string]$observed.lease.state -ne 'active'
                ) {
                    throw (
                        "PAPER canary lease entered unexpected state " +
                        "$([string]$observed.lease.state)."
                    )
                }
            }
            catch {
                if (
                    $_.Exception.GetBaseException().Message -match
                        'identity|nonce|unexpected state|atomically linked|Binance'
                ) {
                    throw
                }
            }
            if ($postAttempts -eq 1) {
                $ambiguousFirstPost = $true
                continue
            }
            throw 'PAPER canary lease release remains unconfirmed.'
        }

        $confirmed = Get-CanaryLeaseState -PostgresContainer $PostgresContainer
        Assert-ReleasedCanaryLeaseState -State $confirmed `
            -BinanceBaseline $BinanceBaseline
        return [ordered]@{
            release_request_id = $leaseReleaseRequestId
            created = $responseCreated
            post_attempts = $postAttempts
            ambiguous_first_post = $ambiguousFirstPost
            status = 'released'
            controls_confirmed = $true
            confirmed_at = [string]$confirmed.database_now
            lease = $confirmed.lease
            stop_command = $confirmed.stop_command
        }
    }
    throw 'PAPER canary lease release exceeded its two-attempt bound.'
}

function Get-EmergencyStopState {
    param(
        [Parameter(Mandatory = $true)][string] $PostgresContainer,
        [Parameter(Mandatory = $true)][string] $RequestId
    )

    $escapedRequestId = $RequestId.Replace("'", "''")
    $sql = @"
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '3s';
SELECT jsonb_build_object(
  'database_now', clock_timestamp(),
  'command', (
    SELECT jsonb_build_object(
      'id', id, 'request_id', request_id, 'command', command, 'payload', payload,
      'actor', actor, 'reason', reason, 'status', status,
      'detail', detail, 'created_at', created_at, 'executed_at', executed_at
    ) FROM operator_commands WHERE request_id='$escapedRequestId'
  ),
  'canary_lease', (
    SELECT jsonb_build_object(
      'id', id, 'lease_id', lease_id, 'run_id', run_id,
      'acquire_request_id', acquire_request_id,
      'resume_request_id', resume_request_id,
      'resume_actor', resume_actor, 'resume_reason', resume_reason,
      'release_request_id', release_request_id,
      'watchdog_stop_request_id', watchdog_stop_request_id,
      'direct_stop_request_id', direct_stop_request_id,
      'nonce_sha256', nonce_sha256, 'state', state,
      'acquired_at', acquired_at, 'heartbeat_at', heartbeat_at,
      'watchdog_at', watchdog_at, 'expires_at', expires_at,
      'released_at', released_at, 'stop_command_id', stop_command_id
    ) FROM paper_canary_safety_leases
    WHERE lease_id='$($leaseId.Replace("'", "''"))'
  ),
  'lease_stop_command', (
    SELECT jsonb_build_object(
      'id', command.id, 'request_id', command.request_id,
      'command', command.command, 'payload', command.payload,
      'actor', command.actor, 'reason', command.reason,
      'status', command.status, 'created_at', command.created_at,
      'executed_at', command.executed_at
    )
    FROM paper_canary_safety_leases lease
    JOIN operator_commands command ON command.id=lease.stop_command_id
    WHERE lease.lease_id='$($leaseId.Replace("'", "''"))'
  ),
  'active_canary_leases', (
    SELECT count(*) FROM paper_canary_safety_leases WHERE state='active'
  ),
  'global', (
    SELECT jsonb_build_object(
      'mode', mode::text, 'kill_switch', kill_switch,
      'halted_reason', halted_reason,
      'live_confirmation_until', live_confirmation_until
    ) FROM runtime_control WHERE id=1
  ),
  'polymarket', (
    SELECT jsonb_build_object(
      'enabled', enabled, 'mode', mode::text, 'kill_switch', kill_switch,
      'halted_reason', halted_reason,
      'live_confirmation_until', live_confirmation_until
    ) FROM venue_runtime_control WHERE venue='polymarket'::trading_venue
  ),
  'binance', (
    SELECT jsonb_build_object(
      'enabled', enabled, 'mode', mode::text, 'kill_switch', kill_switch,
      'halted_reason', halted_reason,
      'live_confirmation_until', live_confirmation_until
    ) FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue
  ),
  'pending_commands', (
    SELECT count(*) FROM operator_commands WHERE status='pending'
  ),
  'pending_signals', (
    SELECT count(*) FROM signals WHERE status='pending'::signal_status
  ),
  'nonterminal_live_orders', (
    SELECT count(*) FROM orders
    WHERE mode='live'::execution_mode AND status IN ('open','pending')
  ),
  'live_rows', (
    (SELECT count(*) FROM orders WHERE mode='live'::execution_mode)
    +(SELECT count(*) FROM positions WHERE mode='live'::execution_mode)
    +(SELECT count(*) FROM execution_order_intents WHERE mode='live'::execution_mode)
    +(SELECT count(*) FROM execution_fill_legs WHERE mode='live'::execution_mode)
    +(SELECT count(*) FROM prospective_validation_enrollments
      WHERE mode='live'::execution_mode)
    +(SELECT count(*)
      FROM prospective_validation_outcomes outcome
      JOIN prospective_validation_enrollments enrollment
        ON enrollment.id=outcome.enrollment_id
      WHERE enrollment.mode='live'::execution_mode)
  )
)::text;
COMMIT;
"@
    return Invoke-ReadOnlySql -PostgresContainer $PostgresContainer -Sql $sql
}

function Assert-EmergencyStopControls {
    param(
        [Parameter(Mandatory = $true)][object] $State,
        [Parameter(Mandatory = $true)][Collections.IDictionary] $Body,
        [Parameter(Mandatory = $true)][object] $BinanceBaseline
    )

    Assert-ExactStopItem -Item $State.command -Expected $Body
    $status = [string]$State.command.status
    if ($status -in @('rejected','failed')) {
        throw "Polymarket stop command ended as $status."
    }
    if ($status -ne 'succeeded') {
        return $false
    }
    if ($null -eq $State.command.executed_at) {
        throw 'Succeeded Polymarket stop has no execution timestamp.'
    }
    if (
        [string]$State.global.mode -ne 'paper' -or
        -not [bool]$State.global.kill_switch -or
        $null -ne $State.global.live_confirmation_until -or
        -not [bool]$State.polymarket.enabled -or
        [string]$State.polymarket.mode -ne 'paper' -or
        -not [bool]$State.polymarket.kill_switch -or
        $null -ne $State.polymarket.live_confirmation_until -or
        [int]$State.pending_commands -ne 0
    ) {
        throw 'Stop succeeded but post-stop controls are unsafe.'
    }
    if (
        [int]$State.active_canary_leases -ne 0 -or
        (
            $null -ne $State.canary_lease -and
            [string]$State.canary_lease.state -eq 'active'
        )
    ) {
        throw 'Emergency stop did not terminalize the active canary lease.'
    }
    if ($null -ne $State.canary_lease) {
        $leaseState = [string]$State.canary_lease.state
        if ($leaseState -eq 'expired_safe') {
            if (
                $null -ne $State.canary_lease.stop_command_id -or
                $null -ne $State.lease_stop_command
            ) {
                throw 'Safe-expired canary lease unexpectedly has a stop command.'
            }
        }
        elseif ($leaseState -in @(
            'released',
            'expired_stopped',
            'revoked_stopped'
        )) {
            if (
                $null -eq $State.canary_lease.stop_command_id -or
                $null -eq $State.lease_stop_command -or
                [long]$State.canary_lease.stop_command_id -ne
                    [long]$State.lease_stop_command.id -or
                [string]$State.lease_stop_command.command -ne 'stop' -or
                @($State.lease_stop_command.payload.PSObject.Properties).Count -ne 1 -or
                [string]$State.lease_stop_command.payload.venue -ne 'polymarket' -or
                [string]$State.lease_stop_command.status -ne 'succeeded' -or
                $null -eq $State.lease_stop_command.executed_at
            ) {
                throw (
                    'Terminal canary lease is not linked to a succeeded ' +
                    'Polymarket stop command.'
                )
            }
            if ($leaseState -eq 'released') {
                $expectedLeaseStopRequestId = [string](
                    $State.canary_lease.release_request_id
                )
                if (
                    [string]$State.lease_stop_command.request_id -ne
                        $expectedLeaseStopRequestId
                ) {
                    throw (
                        'Released canary lease stop command identity does ' +
                        'not match its reserved request.'
                    )
                }
            }
            elseif ($leaseState -eq 'expired_stopped') {
                $expectedLeaseStopRequestId = [string](
                    $State.canary_lease.watchdog_stop_request_id
                )
                if (
                    [string]$State.lease_stop_command.request_id -ne
                        $expectedLeaseStopRequestId
                ) {
                    throw (
                        'Expired canary lease stop command identity does ' +
                        'not match its reserved request.'
                    )
                }
            }
            else {
                $leaseStopRequestId = [string](
                    $State.lease_stop_command.request_id
                )
                $directStopRequestId = [string](
                    $State.canary_lease.direct_stop_request_id
                )
                if ($leaseStopRequestId -eq $directStopRequestId) {
                    if (
                        $directStopRequestId -ne [string]$Body.request_id -or
                        [long]$State.lease_stop_command.id -ne
                            [long]$State.command.id
                    ) {
                        throw (
                            'Direct emergency stop lease revocation is not ' +
                            'linked to its reserved audit command.'
                        )
                    }
                }
                elseif (
                    [long]$State.lease_stop_command.id -eq
                        [long]$State.command.id -or
                    [DateTimeOffset]$State.lease_stop_command.executed_at -gt
                        [DateTimeOffset]$State.command.executed_at
                ) {
                    throw (
                        'Prior runtime stop did not terminalize the lease ' +
                        'before the submitted direct emergency stop.'
                    )
                }
                else {
                    if (
                        $null -eq $State.canary_lease.acquired_at -or
                        $null -eq $State.lease_stop_command.created_at -or
                        $null -eq $State.lease_stop_command.executed_at -or
                        [DateTimeOffset]$State.lease_stop_command.created_at -lt
                            [DateTimeOffset]$State.canary_lease.acquired_at -or
                        [DateTimeOffset]$State.lease_stop_command.executed_at -lt
                            [DateTimeOffset]$State.canary_lease.acquired_at -or
                        [DateTimeOffset]$State.lease_stop_command.created_at -gt
                            [DateTimeOffset]$State.lease_stop_command.executed_at
                    ) {
                        throw (
                            'Prior runtime stop does not prove ' +
                            'post-acquisition ordering.'
                        )
                    }
                }
            }
        }
        else {
            throw "Emergency stop observed unexpected canary lease state $leaseState."
        }
    }
    if (
        [bool]$State.binance.enabled -ne [bool]$BinanceBaseline.enabled -or
        [string]$State.binance.mode -ne [string]$BinanceBaseline.mode -or
        [bool]$State.binance.kill_switch -ne [bool]$BinanceBaseline.kill_switch -or
        [string]$State.binance.halted_reason -ne
            [string]$BinanceBaseline.halted_reason -or
        [string]$State.binance.live_confirmation_until -ne
            [string]$BinanceBaseline.live_confirmation_until
    ) {
        throw 'Polymarket emergency stop changed the sealed Binance baseline.'
    }
    return $true
}

function Invoke-EmergencyStop {
    param(
        [Parameter(Mandatory = $true)][Net.Http.HttpClient] $SafetyClient,
        [Parameter(Mandatory = $true)][Collections.IDictionary] $Body,
        [Parameter(Mandatory = $true)][string] $PostgresContainer,
        [Parameter(Mandatory = $true)][object] $BinanceBaseline
    )

    if (
        @($Body.payload.Keys).Count -ne 1 -or
        [string]$Body.payload.venue -ne 'polymarket' -or
        [string]$Body.command -ne 'stop'
    ) {
        throw 'Refusing a non-Polymarket or malformed emergency stop.'
    }
    $requestId = [string]$Body.request_id
    $jsonBody = $Body | ConvertTo-Json -Depth 4 -Compress
    # Keep one allowlisted mutation primitive. Both ambiguity recovery and
    # cleanup convergence replay this exact body and request identity.
    $postStopRequest = {
        Invoke-JsonRequest -Client $SafetyClient -Method POST `
            -Path 'api/v1/emergency-stop' -JsonBody $jsonBody
    }
    $postAttempts = 0
    $ambiguousFirstPost = $false
    $accepted = $false
    $responseCreated = $null
    $firstPostError = $null
    $cleanupRetryAttempts = 0
    $lastCleanupExpiredSignals = $null
    $lastCleanupCancelRequestedLiveOrders = $null
    $lastCleanupError = $null
    $lastState = $null

    while (-not $accepted -and $postAttempts -lt 2) {
        $postAttempts += 1
        $responseWasReceived = $false
        $responseRetryable = $false
        try {
            $response = & $postStopRequest
            $responseWasReceived = $true
            $responseSchema = if ($response.status_code -eq 200) {
                'stop-success'
            }
            else {
                'error'
            }
            Assert-SafetyMutationJsonResponseShape -Json $response.json `
                -Context 'safety-control emergency stop response' `
                -Schema $responseSchema
            if ($response.status_code -ne 200 -or $null -eq $response.json.item) {
                $retryableValue = Get-OptionalProperty `
                    -InputObject $response.json -Name 'retryable'
                $responseRetryable = (
                    $response.status_code -eq 503 -and
                    $null -ne $retryableValue -and
                    $retryableValue
                )
                throw (
                    "Polymarket stop was not accepted by safety-control " +
                    "(HTTP $($response.status_code))."
                )
            }
            Assert-ExactStopItem -Item $response.json.item -Expected $Body
            if ($postAttempts -eq 1 -and -not $response.json.created) {
                throw 'Polymarket stop request ID was not newly created.'
            }
            $responseCreated = $response.json.created
            $accepted = $true
        }
        catch {
            $postError = $_.Exception.GetBaseException().Message
            if ($postAttempts -eq 1) {
                $firstPostError = $postError
            }
            # A complete non-retryable HTTP response is authoritative.
            if ($responseWasReceived -and -not $responseRetryable) {
                throw
            }
            $observed = $null
            $pollError = $null
            try {
                $observed = Get-EmergencyStopState `
                    -PostgresContainer $PostgresContainer -RequestId $requestId
                if ($null -ne $observed.command) {
                    Assert-ExactStopItem -Item $observed.command -Expected $Body
                    if ([string]$observed.command.status -eq 'succeeded') {
                        $accepted = $true
                        $ambiguousFirstPost = $postAttempts -eq 1
                        break
                    }
                    if (
                        [string]$observed.command.status -in @('rejected','failed')
                    ) {
                        throw (
                            "Polymarket stop command ended as " +
                            "$([string]$observed.command.status)."
                        )
                    }
                }
            }
            catch {
                $pollError = $_.Exception.GetBaseException().Message
                if (
                    $pollError -match 'identity|collided|ended as (rejected|failed)'
                ) {
                    throw
                }
            }
            if ($postAttempts -eq 1) {
                $ambiguousFirstPost = $true
                continue
            }
            throw (
                "Polymarket stop remained ambiguous after one idempotent retry: " +
                "$postError; database poll: $pollError"
            )
        }
    }
    if (-not $accepted) {
        throw 'Polymarket stop was not accepted within the two-attempt bound.'
    }

    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($watch.Elapsed.TotalSeconds -lt $StopConfirmationSeconds) {
            $state = Get-EmergencyStopState `
                -PostgresContainer $PostgresContainer -RequestId $requestId
            $lastState = $state
            if (
                $null -ne $state.command -and
                (Assert-EmergencyStopControls -State $state -Body $Body `
                    -BinanceBaseline $BinanceBaseline)
            ) {
                $pendingSignals = [int]$state.pending_signals
                $nonterminalLiveOrders = [int]$state.nonterminal_live_orders
                if (
                    $pendingSignals -eq 0 -and
                    $nonterminalLiveOrders -eq 0 -and
                    [int]$state.live_rows -eq 0
                ) {
                    return [ordered]@{
                        request_id = $requestId
                        created = $responseCreated
                        post_attempts = $postAttempts
                        ambiguous_first_post = $ambiguousFirstPost
                        first_post_error = $firstPostError
                        status = 'succeeded'
                        confirmed_at = [string]$state.database_now
                        controls_confirmed = $true
                        live_rows = [int]$state.live_rows
                        cleanup_retry_attempts = $cleanupRetryAttempts
                        pending_signals = 0
                        nonterminal_live_orders = 0
                        last_cleanup_expired_signals = (
                            $lastCleanupExpiredSignals
                        )
                        last_cleanup_cancel_requested_live_orders = (
                            $lastCleanupCancelRequestedLiveOrders
                        )
                        last_cleanup_error = $lastCleanupError
                    }
                }

                $cleanupRetryAttempts += 1
                $cleanupResponseReceived = $false
                $cleanupResponseRetryable = $false
                try {
                    $cleanupResponse = & $postStopRequest
                    $cleanupResponseReceived = $true
                    $cleanupResponseSchema = if (
                        $cleanupResponse.status_code -eq 200
                    ) {
                        'stop-success'
                    }
                    else {
                        'error'
                    }
                    Assert-SafetyMutationJsonResponseShape `
                        -Json $cleanupResponse.json `
                        -Context 'safety-control emergency stop cleanup response' `
                        -Schema $cleanupResponseSchema
                    if (
                        $cleanupResponse.status_code -ne 200 -or
                        $null -eq $cleanupResponse.json.item
                    ) {
                        $cleanupRetryableValue = Get-OptionalProperty `
                            -InputObject $cleanupResponse.json -Name 'retryable'
                        $cleanupResponseRetryable = (
                            $cleanupResponse.status_code -eq 503 -and
                            $null -ne $cleanupRetryableValue -and
                            $cleanupRetryableValue
                        )
                        throw (
                            'Exact Polymarket cleanup replay was not ' +
                            "accepted (HTTP $($cleanupResponse.status_code))."
                        )
                    }
                    Assert-ExactStopItem -Item $cleanupResponse.json.item `
                        -Expected $Body
                    if (
                        $cleanupResponse.json.created -or
                        -not $cleanupResponse.json.idempotent -or
                        [int]$cleanupResponse.json.expired_signals -lt 0 -or
                        [int]$cleanupResponse.json.cancel_requested_live_orders -lt 0
                    ) {
                        throw (
                            'Exact cleanup replay changed audit identity or ' +
                            'returned invalid claimed-row counts.'
                        )
                    }
                    $lastCleanupExpiredSignals = [int](
                        $cleanupResponse.json.expired_signals
                    )
                    $lastCleanupCancelRequestedLiveOrders = [int](
                        $cleanupResponse.json.cancel_requested_live_orders
                    )
                    $lastCleanupError = $null
                    Start-Sleep -Seconds 1
                    continue
                }
                catch {
                    $lastCleanupError = (
                        $_.Exception.GetBaseException().Message
                    )
                    if (
                        $cleanupResponseReceived -and
                        -not $cleanupResponseRetryable
                    ) {
                        throw
                    }
                }
            }
            Start-Sleep -Seconds 1
        }
    }
    finally {
        $watch.Stop()
    }
    $pendingSignalsAtTimeout = if ($null -eq $lastState) {
        'unknown'
    }
    else {
        [string]$lastState.pending_signals
    }
    $nonterminalOrdersAtTimeout = if ($null -eq $lastState) {
        'unknown'
    }
    else {
        [string]$lastState.nonterminal_live_orders
    }
    if ([string]::IsNullOrWhiteSpace([string]$lastCleanupError)) {
        $lastCleanupError = (
            'accepted cleanup replay did not converge before the ' +
            'confirmation deadline'
        )
    }
    throw (
        'Polymarket stop remains unconfirmed after the bounded poll window; ' +
        "pending_signals=$pendingSignalsAtTimeout; " +
        "nonterminal_live_orders=$nonterminalOrdersAtTimeout; " +
        "cleanup_retries=$cleanupRetryAttempts; " +
        "last_cleanup_expired=$lastCleanupExpiredSignals; " +
        'last_cleanup_cancel_requested=' +
        "$lastCleanupCancelRequestedLiveOrders; " +
        "last_cleanup_error=$lastCleanupError"
    )
}

function Invoke-FailClosedStopBarrier {
    param(
        [Parameter(Mandatory = $true)][bool] $StopRequired,
        [Parameter(Mandatory = $false)][object] $CurrentStopEvidence,
        [Parameter(Mandatory = $true)][string] $StopRequestId,
        [Parameter(Mandatory = $true)][scriptblock] $EmergencyStopAction
    )

    if (
        -not $StopRequired -or
        (
            $null -ne $CurrentStopEvidence -and
            [bool]$CurrentStopEvidence.controls_confirmed
        )
    ) {
        return $CurrentStopEvidence
    }
    try {
        $result = & $EmergencyStopAction
        if ($null -eq $result -or -not [bool]$result.controls_confirmed) {
            throw 'Emergency stop action returned without confirmed controls.'
        }
        return $result
    }
    catch {
        return [ordered]@{
            request_id = $StopRequestId
            status = 'unconfirmed'
            controls_confirmed = $false
            error = $_.Exception.GetBaseException().Message
        }
    }
}

function Get-HttpSnapshot {
    param(
        [Parameter(Mandatory = $true)][Net.Http.HttpClient] $Client,
        [Parameter(Mandatory = $false)][scriptblock] $HeartbeatAction
    )

    $result = [ordered]@{}
    foreach ($endpoint in $EndpointInventory) {
        $sample = Invoke-JsonRequest -Client $Client -Method GET -Path $endpoint.Path
        Assert-ApiJsonResponseShape -Name $endpoint.Name -Json $sample.json
        $result[$endpoint.Name] = [ordered]@{
            path = $endpoint.Path
            status_code = $sample.status_code
            elapsed_ms = $sample.elapsed_ms
            bytes = $sample.bytes
            content_sha256 = $sample.content_sha256
            body = $sample.json
        }
        if ($null -ne $HeartbeatAction) {
            & $HeartbeatAction
        }
    }
    return $result
}

function Get-SafetyHttpSnapshot {
    param(
        [Parameter(Mandatory = $true)][Net.Http.HttpClient] $Client,
        [Parameter(Mandatory = $false)][scriptblock] $HeartbeatAction
    )

    $result = [ordered]@{}
    foreach ($endpoint in @(
        [pscustomobject]@{ Name = 'healthz'; Path = 'healthz' },
        [pscustomobject]@{ Name = 'readyz'; Path = 'readyz' }
    )) {
        $sample = Invoke-JsonRequest -Client $Client -Method GET -Path $endpoint.Path
        Assert-SafetyHealthJsonResponseShape -Name $endpoint.Name -Json $sample.json
        $result[$endpoint.Name] = [ordered]@{
            path = $endpoint.Path
            status_code = $sample.status_code
            elapsed_ms = $sample.elapsed_ms
            bytes = $sample.bytes
            content_sha256 = $sample.content_sha256
            body = $sample.json
        }
        if ($null -ne $HeartbeatAction) {
            & $HeartbeatAction
        }
    }
    return $result
}

function Get-SafetyHttpViolations {
    param([Parameter(Mandatory = $true)][Collections.IDictionary] $SafetyHttp)

    $violations = [Collections.Generic.List[string]]::new()
    foreach ($name in @('healthz','readyz')) {
        $sample = $SafetyHttp[$name]
        if ([int]$sample.status_code -ne 200) {
            $violations.Add("safety_http_$name")
        }
        if ([double]$sample.elapsed_ms -ge 3000) {
            $violations.Add("safety_http_latency_$name")
        }
    }
    if (
        [string]$SafetyHttp.healthz.body.status -ne 'ok' -or
        [string]$SafetyHttp.readyz.body.status -ne 'ready' -or
        [string]$SafetyHttp.readyz.body.database_boundary -ne 'exact' -or
        @($SafetyHttp.readyz.body.reasons).Count -ne 0
    ) {
        $violations.Add('safety_control_not_ready')
    }
    return @($violations | Select-Object -Unique)
}

function Get-ApiDatabaseCoherenceViolations {
    param(
        [Parameter(Mandatory = $true)][Collections.IDictionary] $Http,
        [Parameter(Mandatory = $true)][object] $Database
    )

    $violations = [Collections.Generic.List[string]]::new()
    $expected = Get-OptionalProperty -InputObject $Database -Name 'api_expected'
    if ($null -eq $expected) {
        return @('api_database_expectations_missing')
    }

    $overview = Get-OptionalProperty -InputObject $Http.overview -Name 'body'
    $expectedOverview = Get-OptionalProperty -InputObject $expected -Name 'overview'
    if ($null -eq $overview -or $null -eq $expectedOverview) {
        $violations.Add('api_overview_body_missing')
    }
    else {
        $overviewMatches = (
            [string]$overview.runtime.mode -eq [string]$expectedOverview.mode -and
            [bool]$overview.runtime.kill_switch -eq [bool]$expectedOverview.kill_switch -and
            [int]$overview.active_markets -eq [int]$expectedOverview.active_markets -and
            [int]$overview.blocked_markets -eq [int]$expectedOverview.blocked_markets -and
            [int]$overview.open_positions -eq [int]$expectedOverview.open_positions -and
            [int]$overview.pending_signals -eq [int]$expectedOverview.pending_signals -and
            [Math]::Abs(
                [double]$overview.open_exposure_usd -
                [double]$expectedOverview.open_exposure_usd
            ) -le 0.01
        )
        $expectedBankroll = Get-OptionalProperty `
            -InputObject $expectedOverview -Name 'bankroll_usd'
        $actualBankroll = Get-OptionalProperty -InputObject $overview -Name 'bankroll_usd'
        if ($null -eq $expectedBankroll) {
            $overviewMatches = $overviewMatches -and $null -eq $actualBankroll
        }
        else {
            $overviewMatches = $overviewMatches -and
                $null -ne $actualBankroll -and
                [Math]::Abs([double]$actualBankroll - [double]$expectedBankroll) -le 0.01
        }
        if (-not $overviewMatches) {
            $violations.Add('api_overview_database_mismatch')
        }
    }

    $venueItems = @(Get-OptionalProperty -InputObject $Http.venues.body -Name 'items')
    if ($venueItems.Count -ne [int]$expected.venue_count) {
        $violations.Add('api_venues_count_mismatch')
    }
    else {
        foreach ($venueName in @('polymarket','binance_spot')) {
            $apiVenue = @($venueItems | Where-Object { [string]$_.venue -eq $venueName })
            $dbVenue = Get-OptionalProperty -InputObject $Database.venues -Name $venueName
            if (
                $apiVenue.Count -ne 1 -or
                $null -eq $dbVenue -or
                [bool]$apiVenue[0].enabled -ne [bool]$dbVenue.enabled -or
                [string]$apiVenue[0].mode -ne [string]$dbVenue.mode -or
                [bool]$apiVenue[0].kill_switch -ne [bool]$dbVenue.kill_switch -or
                [string]$apiVenue[0].halted_reason -ne [string]$dbVenue.halted_reason -or
                [string]$apiVenue[0].live_confirmation_until -ne
                    [string]$dbVenue.live_confirmation_until
            ) {
                $violations.Add("api_venue_${venueName}_database_mismatch")
            }
        }
    }

    $expectedCommandIds = @($expected.command_ids | ForEach-Object { [long]$_ })
    $apiCommandItems = @(Get-OptionalProperty -InputObject $Http.commands.body -Name 'items')
    $apiCommandIds = @($apiCommandItems | ForEach-Object { [long]$_.id })
    if (
        $null -eq (Get-OptionalProperty -InputObject $Http.commands -Name 'body') -or
        ($apiCommandIds -join ',') -ne ($expectedCommandIds -join ',')
    ) {
        $violations.Add('api_commands_database_mismatch')
    }

    $expectedJobIds = @($expected.job_ids | ForEach-Object { [long]$_ })
    $apiJobItems = @(Get-OptionalProperty -InputObject $Http.jobs.body -Name 'items')
    $apiJobIds = @($apiJobItems | ForEach-Object { [long]$_.id })
    if (
        $null -eq (Get-OptionalProperty -InputObject $Http.jobs -Name 'body') -or
        $apiJobIds.Count -eq 0 -or
        ($apiJobIds -join ',') -ne ($expectedJobIds -join ',')
    ) {
        $violations.Add('api_jobs_database_mismatch')
    }

    $cost = Get-OptionalProperty -InputObject $Http.execution_cost -Name 'body'
    $expectedCost = Get-OptionalProperty -InputObject $expected -Name 'execution_cost'
    if (
        $null -eq $cost -or $null -eq $expectedCost -or
        [string]$cost.mode -ne 'paper' -or
        [DateTimeOffset]$cost.opened_at -ne [DateTimeOffset]$expectedCost.opened_at -or
        [string]$cost.definition_version -ne [string]$expectedCost.definition_version -or
        [string]$cost.definition_hash -ne [string]$expectedCost.definition_hash -or
        [int]$cost.attempts -ne [int]$expectedCost.attempts -or
        [int]$cost.filled_attempts -ne [int]$expectedCost.filled_attempts -or
        [int]$cost.no_fill_attempts -ne [int]$expectedCost.no_fill_attempts -or
        [int]$cost.pending_attempts -ne [int]$expectedCost.pending_attempts -or
        [int]$cost.sealed_attempts -ne [int]$expectedCost.sealed_attempts -or
        [int]$cost.maker_attempts -ne [int]$expectedCost.maker_attempts -or
        [int]$cost.maker_fills -ne [int]$expectedCost.maker_fills
    ) {
        $violations.Add('api_execution_cost_database_mismatch')
    }

    $ledger = Get-OptionalProperty -InputObject $Http.validation_ledger -Name 'body'
    $expectedLedger = Get-OptionalProperty -InputObject $expected -Name 'validation_ledger'
    if (
        $null -eq $ledger -or $null -eq $expectedLedger -or
        [string]$ledger.mode -ne 'paper' -or
        -not [bool]$ledger.selected_is_current -or
        [string]$ledger.selected_pipeline_cohort -ne
            [string]$expectedLedger.selected_pipeline_cohort -or
        [int]$ledger.enrolled_orders -ne [int]$expectedLedger.enrolled_orders -or
        [int]$ledger.pending_outcomes -ne [int]$expectedLedger.pending_outcomes
    ) {
        $violations.Add('api_validation_ledger_database_mismatch')
    }
    return @($violations | Select-Object -Unique)
}

function Get-CoherentHttpDatabaseSnapshot {
    param(
        [Parameter(Mandatory = $true)][Net.Http.HttpClient] $Client,
        [Parameter(Mandatory = $true)][string] $PostgresContainer,
        [Parameter(Mandatory = $true)][string] $AnchorDatabaseTime,
        [Parameter(Mandatory = $true)][string] $CommandBoundaryTime,
        [Parameter(Mandatory = $true)]
        [Collections.IDictionary] $DatabaseSessionBoundary,
        [Parameter(Mandatory = $false)][scriptblock] $HeartbeatAction
    )

    $lastViolations = @('coherence_not_attempted')
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $before = Invoke-ReadOnlySql -PostgresContainer $PostgresContainer `
            -Sql (Get-FullSql -AnchorDatabaseTime $AnchorDatabaseTime `
                -CommandBoundaryTime $CommandBoundaryTime `
                -DatabaseSessionBoundary $DatabaseSessionBoundary)
        if ($null -ne $HeartbeatAction) {
            & $HeartbeatAction
        }
        $http = Get-HttpSnapshot -Client $Client `
            -HeartbeatAction $HeartbeatAction
        $after = Invoke-ReadOnlySql -PostgresContainer $PostgresContainer `
            -Sql (Get-FullSql -AnchorDatabaseTime $AnchorDatabaseTime `
                -CommandBoundaryTime $CommandBoundaryTime `
                -DatabaseSessionBoundary $DatabaseSessionBoundary)
        if ($null -ne $HeartbeatAction) {
            & $HeartbeatAction
        }
        $beforeExpected = $before.api_expected | ConvertTo-Json -Depth 12 -Compress
        $afterExpected = $after.api_expected | ConvertTo-Json -Depth 12 -Compress
        $lastViolations = @(
            Get-ApiDatabaseCoherenceViolations -Http $http -Database $after
        )
        if (
            $beforeExpected -eq $afterExpected -and
            $lastViolations.Count -eq 0
        ) {
            return [ordered]@{
                attempts = $attempt
                stable_api_expected = $true
                http = $http
                database = $after
                api_expected_sha256 = Get-Sha256 -Bytes (
                    [Text.UTF8Encoding]::new($false).GetBytes($afterExpected)
                )
            }
        }
    }
    throw (
        "API/database snapshot did not stabilize within two attempts: " +
        "$($lastViolations -join ',')."
    )
}

function Get-FullViolations {
    param(
        [Parameter(Mandatory = $true)][object] $Http,
        [Parameter(Mandatory = $true)][Collections.IDictionary] $SafetyHttp,
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][object] $Docker,
        [Parameter(Mandatory = $true)][object] $Resources,
        [Parameter(Mandatory = $true)][object] $WorkerLogs,
        [Parameter(Mandatory = $true)][int] $BaselineStalledDays,
        [Parameter(Mandatory = $true)][DateTimeOffset] $WorkerStartedAt,
        [Parameter(Mandatory = $true)][int] $OffsetMinutes
    )

    $violations = [Collections.Generic.List[string]]::new()
    foreach ($name in $EndpointInventory.Name) {
        if ([int]$Http[$name].status_code -ne 200) {
            $violations.Add("http_$name")
        }
        if ([double]$Http[$name].elapsed_ms -ge 3000) {
            $violations.Add("http_latency_$name")
        }
    }
    if (
        [string]$Http.healthz.body.status -ne 'ok' -or
        [string]$Http.readyz.body.status -ne 'ready' -or
        @($Http.readyz.body.reasons).Count -ne 0
    ) {
        $violations.Add('api_not_ready')
    }
    foreach ($item in @(Get-SafetyHttpViolations -SafetyHttp $SafetyHttp)) {
        $violations.Add($item)
    }
    foreach ($guardViolation in @(Test-GuardState -State $Database)) {
        $violations.Add($guardViolation)
    }
    foreach ($coherenceViolation in @(
        Get-ApiDatabaseCoherenceViolations -Http $Http -Database $Database
    )) {
        $violations.Add($coherenceViolation)
    }
    if ([string]$Database.schema_head -ne $ExpectedSchemaHead) {
        $violations.Add('schema_head_changed')
    }
    if (
        [long]$Database.active_epoch.id -ne $ExpectedEpochId -or
        [string]$Database.active_epoch.cohort -ne $ExpectedCohort -or
        [string]$Database.active_epoch.image_digest -ne $ExpectedReleaseDigest
    ) {
        $violations.Add('database_epoch_identity_changed')
    }
    if (
        [string]$Http.continuity.body.active_epoch.id -ne [string]$ExpectedEpochId -or
        [string]$Http.continuity.body.active_epoch.cohort -ne $ExpectedCohort -or
        [string]$Http.continuity.body.active_epoch.image_digest -ne $ExpectedReleaseDigest -or
        -not [bool]$Http.continuity.body.cohort_match -or
        -not [bool]$Http.continuity.body.image_match -or
        -not [bool]$Http.continuity.body.execution_gate_allowed -or
        [string]$Http.continuity.body.execution_gate_reason -ne
            'evidence_continuity_match' -or
        @($Http.continuity.body.frozen_lanes).Count -ne 0 -or
        @($Http.continuity.body.lane_identity_mismatches).Count -ne 0 -or
        @($Http.continuity.body.collection_window.missing_scheduler_jobs).Count -ne 0 -or
        @($Http.continuity.body.continuity_lanes).Count -ne 6 -or
        @(
            $Http.continuity.body.continuity_lanes |
                Where-Object { [long]$_.epoch_id -ne $ExpectedEpochId }
        ).Count -ne 0
    ) {
        $violations.Add('continuity_diverged')
    }
    if ([int]$Http.continuity.body.collection_window.stalled_days -gt $BaselineStalledDays) {
        $violations.Add('stalled_days_increased')
    }
    if (
        $null -eq $Database.latest_health_audit -or
        [DateTimeOffset]$Database.latest_health_audit.ts -le $WorkerStartedAt -or
        (
            [DateTimeOffset]$Database.database_now -
                [DateTimeOffset]$Database.latest_health_audit.ts
        ).TotalMinutes -gt 20 -or
        [string]$Database.latest_health_audit.status -in @('critical','blocking') -or
        @(
            $Database.latest_health_audit.issues |
                Where-Object { [string]$_.severity -in @('critical','blocking') }
        ).Count -ne 0
    ) {
        $violations.Add('health_audit_blocking')
    }
    if (
        [int]$Database.jobs.maintenance_failures -ne 0 -or
        [int]$Database.jobs.legacy_runs -ne 0 -or
        $null -eq $Database.jobs.latest_maintenance_age_seconds -or
        [double]$Database.jobs.latest_maintenance_age_seconds -gt 20 -or
        (
            $null -ne $Database.jobs.max_scheduled_gap_seconds -and
            [double]$Database.jobs.max_scheduled_gap_seconds -gt 20
        )
    ) {
        $violations.Add('scheduler_contention_or_gap')
    }
    if ($OffsetMinutes -gt 0) {
        $minimumRuns = [Math]::Max(1, [Math]::Floor(($OffsetMinutes * 60) / 15) - 2)
        if (
            [int]$Database.jobs.maintenance_success -lt $minimumRuns -or
            [int]$WorkerLogs.maintenance_rows -lt $minimumRuns -or
            $null -eq $Database.jobs.first_success_delay_seconds -or
            [double]$Database.jobs.first_success_delay_seconds -gt 20
        ) {
            $violations.Add('scheduler_maintenance_runs_missing')
        }
    }
    if (
        [bool]$WorkerLogs.possibly_truncated -or
        [int]$WorkerLogs.timestamp_parse_failures -ne 0 -or
        [int]$WorkerLogs.claim_identity_mismatches -ne 0 -or
        @($WorkerLogs.dangerous_log_lines).Count -ne 0
    ) {
        $violations.Add('worker_log_error')
    }
    if (@($WorkerLogs.repeated_zero_mark_finalize_ids).Count -ne 0) {
        $violations.Add('scheduler_lock_contention')
    }
    if (
        [int]$Database.execution.global_post_grace_missing_marks -ne 0 -or
        [int]$Database.execution.duplicate_marks -ne 0 -or
        [int]$Database.execution.duplicate_seals -ne 0 -or
        [int]$Database.execution.finalizable_unsealed -ne 0 -or
        [int]$Database.execution.seals_before_required_marks -ne 0 -or
        [int]$Database.execution.invalid_record_hashes -ne 0
    ) {
        $violations.Add('execution_evidence_integrity')
    }
    foreach ($item in @(Get-ContainerGuardViolations -Containers $Docker)) {
        $violations.Add($item)
    }
    if (
        [double]$Resources.api_memory_percent -ge 90 -or
        [double]$Resources.safety_memory_percent -ge 90 -or
        [double]$Resources.worker_memory_percent -ge 90 -or
        [double]$Resources.postgres_memory_percent -ge 90 -or
        [double]$Resources.postgres_cpu_percent -ge 200
    ) {
        $violations.Add('container_resource_budget')
    }
    return @($violations | Select-Object -Unique)
}

function Get-PreflightViolations {
    param(
        [Parameter(Mandatory = $true)][object] $Http,
        [Parameter(Mandatory = $true)][Collections.IDictionary] $SafetyHttp,
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][object] $Docker,
        [Parameter(Mandatory = $true)][object] $Resources,
        [Parameter(Mandatory = $true)][object] $WorkerLogs,
        [Parameter(Mandatory = $true)][DateTimeOffset] $WorkerStartedAt
    )

    $violations = [Collections.Generic.List[string]]::new()
    foreach ($name in $EndpointInventory.Name) {
        $sample = $Http[$name]
        if ($name -eq 'readyz') {
            if (
                [int]$sample.status_code -ne 503 -or
                [string]$sample.body.status -ne 'not_ready' -or
                (@($sample.body.reasons) -join ',') -ne 'kill_switch'
            ) {
                $violations.Add('readyz_not_fail_closed_only')
            }
        }
        elseif ([int]$sample.status_code -ne 200) {
            $violations.Add("http_$name")
        }
        if ([double]$sample.elapsed_ms -ge 3000) {
            $violations.Add("http_latency_$name")
        }
    }
    if ([string]$Http.healthz.body.status -ne 'ok') {
        $violations.Add('healthz_not_ok')
    }
    foreach ($item in @(Get-SafetyHttpViolations -SafetyHttp $SafetyHttp)) {
        $violations.Add($item)
    }
    foreach ($guardViolation in @(Test-FailClosedGuardState -State $Database)) {
        $violations.Add($guardViolation)
    }
    foreach ($coherenceViolation in @(
        Get-ApiDatabaseCoherenceViolations -Http $Http -Database $Database
    )) {
        $violations.Add($coherenceViolation)
    }
    if ([int]$Database.pending_commands -ne 0) {
        $violations.Add('pending_operator_commands')
    }
    if ($null -ne $Database.resume) {
        $violations.Add('resume_request_id_already_exists')
    }
    if (
        [int]$Database.active_canary_leases -ne 0 -or
        $null -ne $Database.canary_lease
    ) {
        $violations.Add('paper_canary_lease_collision')
    }
    if ([string]$Database.schema_head -ne $ExpectedSchemaHead) {
        $violations.Add('schema_head_changed')
    }
    if (
        [long]$Database.active_epoch.id -ne $ExpectedEpochId -or
        [string]$Database.active_epoch.cohort -ne $ExpectedCohort -or
        [string]$Database.active_epoch.image_digest -ne $ExpectedReleaseDigest
    ) {
        $violations.Add('database_epoch_identity_changed')
    }
    if (
        $null -eq $Database.latest_health_audit -or
        [DateTimeOffset]$Database.latest_health_audit.ts -le $WorkerStartedAt -or
        (
            [DateTimeOffset]$Database.database_now -
                [DateTimeOffset]$Database.latest_health_audit.ts
        ).TotalMinutes -gt 20 -or
        [string]$Database.latest_health_audit.status -in @('critical','blocking') -or
        @(
            $Database.latest_health_audit.issues |
                Where-Object { [string]$_.severity -in @('critical','blocking') }
        ).Count -ne 0
    ) {
        $violations.Add('health_audit_not_fresh_noncritical')
    }
    if (
        [int]$Database.jobs.maintenance_success -lt 4 -or
        [int]$WorkerLogs.maintenance_rows -lt 4 -or
        [int]$Database.jobs.maintenance_failures -ne 0 -or
        [int]$Database.jobs.legacy_runs -ne 0 -or
        $null -eq $Database.jobs.first_success_delay_seconds -or
        [double]$Database.jobs.first_success_delay_seconds -gt 20 -or
        $null -eq $Database.jobs.latest_maintenance_age_seconds -or
        [double]$Database.jobs.latest_maintenance_age_seconds -gt 20 -or
        (
            $null -ne $Database.jobs.max_scheduled_gap_seconds -and
            [double]$Database.jobs.max_scheduled_gap_seconds -gt 20
        )
    ) {
        $violations.Add('cold_scheduler_canary_incomplete')
    }
    if (
        [bool]$WorkerLogs.possibly_truncated -or
        [int]$WorkerLogs.timestamp_parse_failures -ne 0 -or
        [int]$WorkerLogs.claim_identity_mismatches -ne 0 -or
        @($WorkerLogs.dangerous_log_lines).Count -ne 0
    ) {
        $violations.Add('worker_log_error')
    }
    if (@($WorkerLogs.repeated_zero_mark_finalize_ids).Count -ne 0) {
        $violations.Add('scheduler_lock_contention')
    }
    if (
        [int]$Database.execution.global_post_grace_missing_marks -ne 0 -or
        [int]$Database.execution.duplicate_marks -ne 0 -or
        [int]$Database.execution.duplicate_seals -ne 0 -or
        [int]$Database.execution.finalizable_unsealed -ne 0 -or
        [int]$Database.execution.seals_before_required_marks -ne 0 -or
        [int]$Database.execution.invalid_record_hashes -ne 0
    ) {
        $violations.Add('execution_evidence_integrity')
    }
    if (
        [string]$Http.continuity.body.active_epoch.id -ne [string]$ExpectedEpochId -or
        [string]$Http.continuity.body.active_epoch.cohort -ne $ExpectedCohort -or
        [string]$Http.continuity.body.active_epoch.image_digest -ne $ExpectedReleaseDigest -or
        -not [bool]$Http.continuity.body.cohort_match -or
        -not [bool]$Http.continuity.body.image_match -or
        -not [bool]$Http.continuity.body.execution_gate_allowed -or
        [string]$Http.continuity.body.execution_gate_reason -ne
            'evidence_continuity_match' -or
        @($Http.continuity.body.frozen_lanes).Count -ne 0 -or
        @($Http.continuity.body.lane_identity_mismatches).Count -ne 0 -or
        @($Http.continuity.body.collection_window.missing_scheduler_jobs).Count -ne 0 -or
        @($Http.continuity.body.continuity_lanes).Count -ne 6 -or
        @(
            $Http.continuity.body.continuity_lanes |
                Where-Object { [long]$_.epoch_id -ne $ExpectedEpochId }
        ).Count -ne 0
    ) {
        $violations.Add('continuity_not_ready')
    }
    foreach ($item in @(Get-ContainerGuardViolations -Containers $Docker)) {
        $violations.Add($item)
    }
    if (
        [double]$Resources.api_memory_percent -ge 90 -or
        [double]$Resources.safety_memory_percent -ge 90 -or
        [double]$Resources.worker_memory_percent -ge 90 -or
        [double]$Resources.postgres_memory_percent -ge 90 -or
        [double]$Resources.postgres_cpu_percent -ge 200
    ) {
        $violations.Add('container_resource_budget')
    }
    return @($violations | Select-Object -Unique)
}

function Get-ExecutionEvidenceSummary {
    param(
        [Parameter(Mandatory = $true)][object] $Baseline,
        [Parameter(Mandatory = $true)][object] $Final,
        [Parameter(Mandatory = $true)][object] $WorkerLogs,
        [Parameter(Mandatory = $true)][bool] $EarliestSnapshotObserved
    )

    $deltas = [ordered]@{}
    foreach ($field in @(
        'maker_intents',
        'fill_legs',
        'cost_seals',
        'mark_5m_observed',
        'mark_30m_observed',
        'enrollments',
        'outcomes'
    )) {
        $deltas[$field] = (
            [int]$Final.PSObject.Properties[$field].Value -
            [int]$Baseline.PSObject.Properties[$field].Value
        )
    }
    $cases = [ordered]@{
        earliest_snapshot_observed = $EarliestSnapshotObserved
        phase_a_b_observed = [int]$WorkerLogs.phase_a_b_runs -gt 0
        within_grace_deferred_observed = (
            [int]$WorkerLogs.within_grace_deferred_runs -gt 0
        )
        natural_maker_intent_observed = [int]$deltas.maker_intents -gt 0
        natural_fill_observed = [int]$deltas.fill_legs -gt 0
        natural_mark_observed = (
            [int]$deltas.mark_5m_observed +
            [int]$deltas.mark_30m_observed
        ) -gt 0
        natural_seal_observed = [int]$deltas.cost_seals -gt 0
        natural_enrollment_observed = [int]$deltas.enrollments -gt 0
        natural_outcome_observed = [int]$deltas.outcomes -gt 0
    }
    $allObserved = @(
        $cases.GetEnumerator() |
            Where-Object { -not [bool]$_.Value }
    ).Count -eq 0
    return [ordered]@{
        runtime_gate = 'informational_only'
        all_cases_observed = $allObserved
        cases = $cases
        deltas = $deltas
    }
}

$absoluteRoot = [IO.Path]::GetFullPath($Root)
$composePath = Join-Path $absoluteRoot 'docker-compose.yml'
$composeEnvPath = Join-Path $absoluteRoot '.env'
foreach ($overrideName in @(
    'COMPOSE_FILE',
    'COMPOSE_PROFILES',
    'COMPOSE_PROJECT_NAME',
    'COMPOSE_PATH_SEPARATOR'
)) {
    if (Test-Path -LiteralPath "Env:$overrideName") {
        throw "Release Compose override environment variable is forbidden: $overrideName"
    }
}
foreach ($requiredFile in @($composePath, $composeEnvPath)) {
    if (
        -not (Test-Path -LiteralPath $requiredFile -PathType Leaf) -or
        ((Get-Item -LiteralPath $requiredFile -Force).Attributes -band
            [IO.FileAttributes]::ReparsePoint)
    ) {
        throw "Release Compose provenance file is missing or linked: $requiredFile"
    }
}
if (@(
    Get-Content -LiteralPath $composeEnvPath |
        Where-Object {
            $_ -match '^\s*(?:export\s+)?COMPOSE_(?:FILE|PROFILES|PROJECT_NAME|PATH_SEPARATOR)\s*='
        }
).Count -ne 0) {
    throw 'Release .env must not define a reserved Compose override variable.'
}
if (-not [IO.File]::Exists($composePath)) {
    throw "Missing docker-compose.yml under $absoluteRoot."
}
$runId = if ([string]::IsNullOrWhiteSpace($CanaryRunId)) {
    [guid]::NewGuid().ToString('N')
}
else {
    $CanaryRunId.ToLowerInvariant()
}
$leaseId = "paper-lease-$runId"
$leaseAcquireRequestId = "paper-lease-acquire-$runId"
$leaseDirectStopRequestId = "paper-canary-stop-$runId"
$leaseReleaseRequestId = "paper-lease-release-$runId"
$leaseNonce = New-SecureCanaryNonce
$leaseNonceSha256 = Get-Sha256 -Bytes (
    [Text.UTF8Encoding]::new($false).GetBytes($leaseNonce)
)
$absoluteOutputRoot = if ([IO.Path]::IsPathRooted($OutputRoot)) {
    [IO.Path]::GetFullPath($OutputRoot)
}
else {
    [IO.Path]::GetFullPath((Join-Path $absoluteRoot $OutputRoot))
}
$runDirectory = Join-Path $absoluteOutputRoot $runId
[void][IO.Directory]::CreateDirectory($absoluteOutputRoot)
if ([IO.Directory]::Exists($runDirectory)) {
    throw "Canary output directory already exists: $runDirectory"
}
[void][IO.Directory]::CreateDirectory($runDirectory)
$ownerPath = Join-Path $runDirectory '.owner.lock'
$ownerStream = [IO.FileStream]::new(
    $ownerPath,
    [IO.FileMode]::CreateNew,
    [IO.FileAccess]::Write,
    [IO.FileShare]::Read
)
$ownerBytes = [Text.UTF8Encoding]::new($false).GetBytes(
    "$runId`n$PID`n$([DateTimeOffset]::UtcNow.ToString('O'))"
)
$ownerStream.Write($ownerBytes, 0, $ownerBytes.Length)
$ownerStream.Flush($true)

$client = $null
$safetyClient = $null
$postgresContainer = $null
$apiContainer = $null
$safetyContainer = $null
$workerContainer = $null
$workerStartedAt = $null
$commandBoundaryTime = $null
$databaseSessionBoundary = $null
$binanceBaseline = $null
$preflightAccepted = $false
$resumeObserved = $false
$stopEvidence = $null
$divergenceEvidence = $null
$lastObservation = $null
$snapshotFiles = [Collections.Generic.List[object]]::new()
$previousSnapshotHash = $null
$baselineStalledDays = $null
$anchorDatabaseTime = $null
$anchorExecutedAt = $null
$anchorWatch = [Diagnostics.Stopwatch]::new()
$resourceWindow = [Collections.Generic.List[object]]::new()
$resourceSampleCount = 0
$nextGuardDueSeconds = 5.0
$workerLogCursor = $null
$finalizerEventKeys = [Collections.Generic.HashSet[string]]::new()
$finalizerFirstEventByObligation = @{}
$schedulerLockContentionIds = [Collections.Generic.HashSet[long]]::new()
$executionBaseline = $null
$finalExecution = $null
$finalWorkerLogs = $null
$script:preflightDatabaseTime = $null
$stopBody = New-StopBody -RunId $runId
$stopRequestId = [string]$stopBody.request_id
if ($stopRequestId -ne $leaseDirectStopRequestId) {
    throw 'Emergency stop request does not match the lease reservation.'
}

try {
    $effectiveApiKey = $ApiKey
    if ($null -eq $effectiveApiKey) {
        $environmentKey = [Environment]::GetEnvironmentVariable(
            'STRATUS_OPERATOR_API_KEY',
            [EnvironmentVariableTarget]::Process
        )
        if ([string]::IsNullOrWhiteSpace($environmentKey)) {
            throw 'ApiKey or process STRATUS_OPERATOR_API_KEY is required.'
        }
        $effectiveApiKey = ConvertTo-SecureString $environmentKey -AsPlainText -Force
        $environmentKey = $null
    }

    $effectiveSafetyApiKey = $SafetyApiKey
    if ($null -eq $effectiveSafetyApiKey) {
        $environmentSafetyKey = [Environment]::GetEnvironmentVariable(
            'STRATUS_SAFETY_API_KEY',
            [EnvironmentVariableTarget]::Process
        )
        if ([string]::IsNullOrWhiteSpace($environmentSafetyKey)) {
            throw (
                'SafetyApiKey or process STRATUS_SAFETY_API_KEY is required.'
            )
        }
        $effectiveSafetyApiKey = ConvertTo-SecureString `
            $environmentSafetyKey -AsPlainText -Force
        $environmentSafetyKey = $null
    }

    $plainOperatorKey = ConvertFrom-SecureStringValue -Secret $effectiveApiKey
    $plainSafetyKey = ConvertFrom-SecureStringValue -Secret $effectiveSafetyApiKey
    try {
        if (
            [string]::Equals(
                $plainOperatorKey,
                $plainSafetyKey,
                [StringComparison]::Ordinal
            )
        ) {
            throw 'Operator and safety API keys must be distinct.'
        }
    }
    finally {
        $plainOperatorKey = $null
        $plainSafetyKey = $null
    }

    $client = New-AuthenticatedLoopbackClient -Uri $BaseUri -Secret $effectiveApiKey
    $safetyClient = New-AuthenticatedLoopbackClient `
        -Uri $SafetyBaseUri -Secret $effectiveSafetyApiKey

    $apiContainer = Get-ServiceContainerId -Service 'api'
    $safetyContainer = Get-ServiceContainerId -Service 'safety-control'
    $workerContainer = Get-ServiceContainerId -Service 'worker'
    $postgresContainer = Get-ServiceContainerId -Service 'postgres'
    $preflightDocker = Get-ContainerSet -ApiContainer $apiContainer `
        -SafetyContainer $safetyContainer `
        -WorkerContainer $workerContainer -PostgresContainer $postgresContainer
    $databaseSessionBoundary = New-DatabaseSessionBoundary `
        -Containers $preflightDocker
    if ([string]::IsNullOrWhiteSpace([string]$preflightDocker.worker.started_at)) {
        throw 'Candidate worker has no authoritative container start time.'
    }
    $workerStartedAt = [DateTimeOffset]$preflightDocker.worker.started_at
    $commandBoundaryTime = Get-DatabaseClock -PostgresContainer $postgresContainer
    $preflightCoherent = Get-CoherentHttpDatabaseSnapshot -Client $client `
        -PostgresContainer $postgresContainer `
        -AnchorDatabaseTime $workerStartedAt.ToUniversalTime().ToString('O') `
        -CommandBoundaryTime $commandBoundaryTime `
        -DatabaseSessionBoundary $databaseSessionBoundary
    $preflightHttp = $preflightCoherent.http
    $preflightSafetyHttp = Get-SafetyHttpSnapshot -Client $safetyClient
    $preflightDatabase = $preflightCoherent.database
    $preflightResources = Get-ResourceSnapshot -Containers $preflightDocker
    $preflightLogs = Get-WorkerMaintenanceLogSnapshot `
        -WorkerContainer $preflightDocker.worker.container_name -Since $workerStartedAt
    $preflightViolations = @(
        Get-PreflightViolations -Http $preflightHttp `
            -SafetyHttp $preflightSafetyHttp -Database $preflightDatabase `
            -Docker $preflightDocker -Resources $preflightResources `
            -WorkerLogs $preflightLogs -WorkerStartedAt $workerStartedAt
    )
    $preflight = [ordered]@{
        schema_version = 1
        canary_run_id = $runId
        captured_at = [DateTimeOffset]::UtcNow.ToString('O')
        emergency_stop_armed = $true
        expected_resume = [ordered]@{
            request_id = $ExpectedResumeRequestId
            actor = $ExpectedResumeActor
            reason = $ExpectedResumeReason
            command = 'resume'
            payload = [ordered]@{ venue = 'polymarket' }
        }
        emergency_stop = $stopBody
        docker = $preflightDocker
        resources = $preflightResources
        worker_logs = $preflightLogs
        database = $preflightDatabase
        http = $preflightHttp
        api_database_coherence = [ordered]@{
            attempts = $preflightCoherent.attempts
            stable_api_expected = $preflightCoherent.stable_api_expected
            api_expected_sha256 = $preflightCoherent.api_expected_sha256
        }
        safety_http = $preflightSafetyHttp
        safety = [ordered]@{
            passed = $preflightViolations.Count -eq 0
            violations = @($preflightViolations)
        }
    }
    $preflightWritten = Write-ImmutableJson `
        -Path (Join-Path $runDirectory 'preflight.json') -Payload $preflight
    if ($preflightViolations.Count -ne 0) {
        throw "PAPER canary preflight rejected: $($preflightViolations -join ',')."
    }
    $script:preflightDatabaseTime = [string]$preflightDatabase.database_now
    $binanceBaseline = [ordered]@{
        enabled = [bool]$preflightDatabase.venues.binance_spot.enabled
        mode = [string]$preflightDatabase.venues.binance_spot.mode
        kill_switch = [bool]$preflightDatabase.venues.binance_spot.kill_switch
        halted_reason = $preflightDatabase.venues.binance_spot.halted_reason
        live_confirmation_until = (
            $preflightDatabase.venues.binance_spot.live_confirmation_until
        )
    }
    $preflightAccepted = $true
    $baselineStalledDays = [int](
        $preflightHttp.continuity.body.collection_window.stalled_days
    )
    $leaseAcquire = Invoke-CanaryLeaseAcquire -SafetyClient $safetyClient `
        -PostgresContainer $postgresContainer
    $leaseReadyWatch = [Diagnostics.Stopwatch]::StartNew()
    $leaseHeartbeat = $null
    try {
        while ($leaseReadyWatch.Elapsed.TotalSeconds -lt 10) {
            $leaseState = Get-CanaryLeaseState `
                -PostgresContainer $postgresContainer
            Assert-ExactCanaryLeaseItem `
                -Item $leaseState.lease -ExpectedState 'active'
            if (
                [DateTimeOffset]$leaseState.lease.watchdog_at -gt
                    [DateTimeOffset]$leaseState.lease.acquired_at
            ) {
                $leaseHeartbeat = Invoke-CanaryLeaseHeartbeat `
                    -SafetyClient $safetyClient
                break
            }
            Start-Sleep -Seconds 1
        }
    }
    finally {
        $leaseReadyWatch.Stop()
    }
    if ($null -eq $leaseHeartbeat) {
        throw 'PAPER canary watchdog did not observe the acquired lease in time.'
    }
    $leaseState = Get-CanaryLeaseState -PostgresContainer $postgresContainer
    Assert-ActiveCanaryLease -Lease $leaseState.lease `
        -DatabaseNow ([DateTimeOffset]$leaseState.database_now) `
        -ActiveLeaseCount ([int]$leaseState.active_leases)
    if ([int]$leaseState.pending_signals -ne 0) {
        throw 'PAPER canary collector admission requires zero pending Signals.'
    }
    $leaseArtifact = [ordered]@{
        schema_version = 1
        canary_run_id = $runId
        preflight_artifact_sha256 = $preflightWritten.sha256
        lease_id = $leaseId
        acquire_request_id = $leaseAcquireRequestId
        resume_request_id = $ExpectedResumeRequestId
        direct_stop_request_id = $leaseDirectStopRequestId
        nonce_sha256 = $leaseNonceSha256
        acquire = $leaseAcquire
        confirmed = $leaseState
    }
    $leaseWritten = Write-ImmutableJson `
        -Path (Join-Path $runDirectory 'lease-acquire.json') `
        -Payload $leaseArtifact
    $ready = [ordered]@{
        schema_version = 1
        canary_run_id = $runId
        ready_at = [DateTimeOffset]::UtcNow.ToString('O')
        preflight_database_at = $script:preflightDatabaseTime
        command_boundary_at = $commandBoundaryTime
        preflight_artifact_sha256 = $preflightWritten.sha256
        preflight_safe = $true
        expected_resume_request_id = $ExpectedResumeRequestId
        emergency_stop_request_id = $stopRequestId
        emergency_stop_payload = [ordered]@{ venue = 'polymarket' }
        emergency_stop_armed = $true
        lease = [ordered]@{
            lease_id = $leaseId
            acquire_request_id = $leaseAcquireRequestId
            direct_stop_request_id = $leaseDirectStopRequestId
            release_request_id = $leaseReleaseRequestId
            nonce_sha256 = $leaseNonceSha256
            artifact_sha256 = $leaseWritten.sha256
            state = 'active'
        }
        offsets = @($MinuteOffsets)
    }
    $readyWritten = Write-ImmutableJson `
        -Path (Join-Path $runDirectory 'ready.json') -Payload $ready
    $previousSnapshotHash = $readyWritten.sha256

    $wait = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($wait.Elapsed.TotalSeconds -lt $ResumeWaitSeconds) {
            [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
            $guard = Invoke-ReadOnlySql -PostgresContainer $postgresContainer `
                -Sql (Get-GuardSql -CommandBoundaryTime $commandBoundaryTime `
                    -DatabaseSessionBoundary $databaseSessionBoundary)
            Assert-ActiveCanaryLease -Lease $guard.canary_lease `
                -DatabaseNow ([DateTimeOffset]$guard.database_now) `
                -ActiveLeaseCount ([int]$guard.active_canary_leases)
            $waitViolations = [Collections.Generic.List[string]]::new()
            if ($null -eq $guard.resume) {
                foreach ($item in @(Test-FailClosedGuardState -State $guard)) {
                    $waitViolations.Add($item)
                }
                if ([int]$guard.pending_commands -ne 0) {
                    $waitViolations.Add('pending_operator_commands')
                }
            }
            else {
                Assert-ExactResume -Resume $guard.resume `
                    -DatabaseNow ([DateTimeOffset]$guard.database_now)
                $resumeStatus = [string]$guard.resume.status
                if ($resumeStatus -in @('rejected','failed')) {
                    throw "Expected resume command ended as $resumeStatus before canary start."
                }
                if ($resumeStatus -eq 'pending') {
                    foreach ($item in @(Test-FailClosedGuardState -State $guard)) {
                        $waitViolations.Add($item)
                    }
                }
                elseif ($resumeStatus -eq 'succeeded') {
                    $resumeObserved = $true
                    foreach ($item in @(Test-GuardState -State $guard)) {
                        $waitViolations.Add($item)
                    }
                }
                else {
                    $waitViolations.Add("unexpected_resume_status_$resumeStatus")
                }
            }
            if ($waitViolations.Count -ne 0) {
                throw "Pre-resume guard diverged: $($waitViolations -join ',')."
            }
            $waitDocker = Get-ContainerSet -ApiContainer $apiContainer `
                -SafetyContainer $safetyContainer `
                -WorkerContainer $workerContainer -PostgresContainer $postgresContainer `
                -HeartbeatAction {
                    [void](
                        Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient
                    )
                }
            foreach ($item in @(Get-ContainerGuardViolations -Containers $waitDocker)) {
                $waitViolations.Add($item)
            }
            if ($waitViolations.Count -ne 0) {
                throw "Pre-resume runtime diverged: $($waitViolations -join ',')."
            }
            if ($resumeObserved) {
                $anchorDatabaseTime = [string]$guard.database_now
                $anchorExecutedAt = [string]$guard.resume.executed_at
                $anchorWatch.Start()
                $workerLogCursor = [DateTimeOffset]$anchorDatabaseTime
                $resourceWindow.Clear()
                $resourceSampleCount = 0
                $finalizerEventKeys.Clear()
                $finalizerFirstEventByObligation.Clear()
                $schedulerLockContentionIds.Clear()
                $nextGuardDueSeconds = 5.0
                break
            }
            Start-Sleep -Seconds $GuardIntervalSeconds
        }
    }
    finally {
        $wait.Stop()
    }
    if (-not $resumeObserved) {
        throw 'Expected resume request was not observed as succeeded before timeout.'
    }

    for ($sequence = 0; $sequence -lt $MinuteOffsets.Count; $sequence++) {
        $offset = $MinuteOffsets[$sequence]
        $targetSeconds = $offset * 60
        while ($nextGuardDueSeconds -lt $targetSeconds) {
            $delayMilliseconds = [int][Math]::Max(
                0,
                [Math]::Floor(
                    ($nextGuardDueSeconds - $anchorWatch.Elapsed.TotalSeconds) * 1000
                )
            )
            if ($delayMilliseconds -gt 0) {
                Start-Sleep -Milliseconds $delayMilliseconds
            }
            [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
            $guard = Invoke-ReadOnlySql -PostgresContainer $postgresContainer `
                -Sql (Get-GuardSql -CommandBoundaryTime $commandBoundaryTime `
                    -DatabaseSessionBoundary $databaseSessionBoundary)
            Assert-ExactResume -Resume $guard.resume `
                -DatabaseNow ([DateTimeOffset]$guard.database_now)
            $guardViolations = [Collections.Generic.List[string]]::new()
            foreach ($item in @(Test-GuardState -State $guard)) {
                $guardViolations.Add($item)
            }
            if ($guardViolations.Count -ne 0) {
                throw (
                    "Inter-milestone database guard diverged: " +
                    "$($guardViolations -join ',')."
                )
            }
            $guardDocker = Get-ContainerSet -ApiContainer $apiContainer `
                -SafetyContainer $safetyContainer `
                -WorkerContainer $workerContainer -PostgresContainer $postgresContainer `
                -HeartbeatAction {
                    [void](
                        Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient
                    )
                }
            foreach ($item in @(Get-ContainerGuardViolations -Containers $guardDocker)) {
                $guardViolations.Add($item)
            }
            if ($guardViolations.Count -ne 0) {
                throw (
                    "Inter-milestone container guard diverged: " +
                    "$($guardViolations -join ',')."
                )
            }
            [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
            $guardResourceStarted = $anchorWatch.Elapsed.TotalSeconds
            $guardResources = Get-ResourceSnapshot -Containers $guardDocker
            $guardResources['completed_elapsed_seconds'] = [Math]::Round(
                $anchorWatch.Elapsed.TotalSeconds,
                3
            )
            $resourceSampleCount += 1
            foreach ($item in @(
                Add-ResourceEvidenceSample -Window $resourceWindow `
                    -Sample $guardResources `
                    -ScheduledElapsedSeconds $nextGuardDueSeconds `
                    -ActualElapsedSeconds $guardResourceStarted
            )) {
                $guardViolations.Add($item)
            }
            if ($guardViolations.Count -ne 0) {
                throw (
                    "Inter-milestone resource guard diverged: " +
                    "$($guardViolations -join ',')."
                )
            }
            [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
            $guardLogSince = $workerLogCursor.AddSeconds(-2)
            $guardLogs = Get-WorkerMaintenanceLogSnapshot `
                -WorkerContainer $guardDocker.worker.container_name `
                -Since $guardLogSince -Tail 500
            if (
                [bool]$guardLogs.possibly_truncated -or
                [int]$guardLogs.timestamp_parse_failures -ne 0 -or
                [int]$guardLogs.claim_identity_mismatches -ne 0 -or
                @($guardLogs.dangerous_log_lines).Count -ne 0
            ) {
                $guardViolations.Add('worker_log_error')
            }
            foreach ($event in @($guardLogs.zero_mark_finalize_events)) {
                $eventKey = [string]$event.event_key
                if ($finalizerEventKeys.Add($eventKey)) {
                    $obligationKey = [string]$event.obligation_id
                    if ($finalizerFirstEventByObligation.ContainsKey($obligationKey)) {
                        [void]$schedulerLockContentionIds.Add(
                            [long]$event.obligation_id
                        )
                    }
                    else {
                        $finalizerFirstEventByObligation[$obligationKey] = $eventKey
                    }
                }
            }
            foreach ($obligationId in @(
                $guardLogs.repeated_zero_mark_finalize_ids
            )) {
                [void]$schedulerLockContentionIds.Add([long]$obligationId)
            }
            if ($schedulerLockContentionIds.Count -ne 0) {
                $guardViolations.Add('scheduler_lock_contention')
            }
            if ($null -ne $guardLogs.latest_docker_timestamp) {
                $latestGuardLogAt = [DateTimeOffset]$guardLogs.latest_docker_timestamp
                if ($latestGuardLogAt -gt $workerLogCursor) {
                    $workerLogCursor = $latestGuardLogAt
                }
            }
            if ($guardViolations.Count -ne 0) {
                throw (
                    "Inter-milestone worker-log guard diverged: " +
                    "$($guardViolations -join ',')."
                )
            }
            [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
            $healthGuard = Invoke-JsonRequest -Client $client -Method GET -Path 'healthz'
            $readyGuard = Invoke-JsonRequest -Client $client -Method GET -Path 'readyz'
            Assert-ApiJsonResponseShape -Name 'healthz' -Json $healthGuard.json
            Assert-ApiJsonResponseShape -Name 'readyz' -Json $readyGuard.json
            if (
                $healthGuard.status_code -ne 200 -or
                [string]$healthGuard.json.status -ne 'ok' -or
                $readyGuard.status_code -ne 200 -or
                [string]$readyGuard.json.status -ne 'ready'
            ) {
                $guardViolations.Add('api_not_ready')
            }
            if ($guardViolations.Count -ne 0) {
                throw (
                    "Inter-milestone API guard diverged: " +
                    "$($guardViolations -join ',')."
                )
            }
            [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
            $continuityGuard = Invoke-JsonRequest `
                -Client $client -Method GET -Path 'api/v1/evidence-continuity'
            Assert-ApiJsonResponseShape -Name 'continuity' `
                -Json $continuityGuard.json
            if (
                $continuityGuard.status_code -ne 200 -or
                [string]$continuityGuard.json.active_epoch.id -ne
                    [string]$ExpectedEpochId -or
                [string]$continuityGuard.json.active_epoch.cohort -ne
                    $ExpectedCohort -or
                [string]$continuityGuard.json.active_epoch.image_digest -ne
                    $ExpectedReleaseDigest -or
                -not [bool]$continuityGuard.json.cohort_match -or
                -not [bool]$continuityGuard.json.image_match -or
                -not [bool]$continuityGuard.json.execution_gate_allowed -or
                [string]$continuityGuard.json.execution_gate_reason -ne
                    'evidence_continuity_match' -or
                @($continuityGuard.json.frozen_lanes).Count -ne 0 -or
                @($continuityGuard.json.lane_identity_mismatches).Count -ne 0 -or
                @(
                    $continuityGuard.json.collection_window.missing_scheduler_jobs
                ).Count -ne 0 -or
                [int]$continuityGuard.json.collection_window.stalled_days -gt
                    $baselineStalledDays
            ) {
                $guardViolations.Add('continuity_guard_diverged')
            }
            if ($guardViolations.Count -ne 0) {
                throw (
                    "Inter-milestone continuity guard diverged: " +
                    "$($guardViolations -join ',')."
                )
            }
            [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
            $safetyHealthGuard = Invoke-JsonRequest `
                -Client $safetyClient -Method GET -Path 'healthz'
            $safetyReadyGuard = Invoke-JsonRequest `
                -Client $safetyClient -Method GET -Path 'readyz'
            Assert-SafetyHealthJsonResponseShape -Name 'healthz' `
                -Json $safetyHealthGuard.json
            Assert-SafetyHealthJsonResponseShape -Name 'readyz' `
                -Json $safetyReadyGuard.json
            if (
                $safetyHealthGuard.status_code -ne 200 -or
                [string]$safetyHealthGuard.json.status -ne 'ok' -or
                $safetyReadyGuard.status_code -ne 200 -or
                [string]$safetyReadyGuard.json.status -ne 'ready' -or
                [string]$safetyReadyGuard.json.database_boundary -ne 'exact'
            ) {
                $guardViolations.Add('safety_control_not_ready')
            }
            if ($guardViolations.Count -ne 0) {
                throw "Inter-milestone PAPER guard diverged: $($guardViolations -join ',')."
            }
            $nextGuardDueSeconds += $GuardIntervalSeconds
        }

        $milestoneDelayMilliseconds = [int][Math]::Max(
            0,
            [Math]::Floor(
                ($targetSeconds - $anchorWatch.Elapsed.TotalSeconds) * 1000
            )
        )
        if ($milestoneDelayMilliseconds -gt 0) {
            Start-Sleep -Milliseconds $milestoneDelayMilliseconds
        }
        [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
        $milestoneGuard = Invoke-ReadOnlySql -PostgresContainer $postgresContainer `
            -Sql (Get-GuardSql -CommandBoundaryTime $commandBoundaryTime `
                -DatabaseSessionBoundary $databaseSessionBoundary)
        Assert-ExactResume -Resume $milestoneGuard.resume `
            -DatabaseNow ([DateTimeOffset]$milestoneGuard.database_now)
        $milestoneGuardViolations = @(Test-GuardState -State $milestoneGuard)
        if ($milestoneGuardViolations.Count -ne 0) {
            throw (
                "Milestone database guard diverged: " +
                "$($milestoneGuardViolations -join ',')."
            )
        }
        $dockerState = Get-ContainerSet -ApiContainer $apiContainer `
            -SafetyContainer $safetyContainer `
            -WorkerContainer $workerContainer -PostgresContainer $postgresContainer `
            -HeartbeatAction {
                [void](
                    Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient
                )
            }
        [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
        $milestoneResourceStarted = $anchorWatch.Elapsed.TotalSeconds
        $resources = Get-ResourceSnapshot -Containers $dockerState
        $resources['completed_elapsed_seconds'] = [Math]::Round(
            $anchorWatch.Elapsed.TotalSeconds,
            3
        )
        $resourceSampleCount += 1
        $milestoneResourceViolations = @(
            Add-ResourceEvidenceSample -Window $resourceWindow -Sample $resources `
                -ScheduledElapsedSeconds $targetSeconds `
                -ActualElapsedSeconds $milestoneResourceStarted
        )
        [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
        $nextGuardDueSeconds = $targetSeconds + $GuardIntervalSeconds
        $coherent = Get-CoherentHttpDatabaseSnapshot -Client $client `
            -PostgresContainer $postgresContainer `
            -AnchorDatabaseTime $anchorDatabaseTime `
            -CommandBoundaryTime $commandBoundaryTime `
            -DatabaseSessionBoundary $databaseSessionBoundary `
            -HeartbeatAction {
                [void](
                    Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient
                )
            }
        $http = $coherent.http
        $safetyHttp = Get-SafetyHttpSnapshot -Client $safetyClient `
            -HeartbeatAction {
                [void](
                    Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient
                )
            }
        $database = $coherent.database
        [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
        $workerLogs = Get-WorkerMaintenanceLogSnapshot `
            -WorkerContainer $dockerState.worker.container_name `
            -Since ([DateTimeOffset]$anchorDatabaseTime)
        [void](Invoke-CanaryLeaseHeartbeat -SafetyClient $safetyClient)
        $violations = @(
            Get-FullViolations -Http $http -SafetyHttp $safetyHttp `
                -Database $database -Docker $dockerState `
                -Resources $resources -WorkerLogs $workerLogs `
                -BaselineStalledDays $baselineStalledDays `
                -WorkerStartedAt $workerStartedAt -OffsetMinutes $offset
        )
        $violations = @(
            @($violations) + @($milestoneResourceViolations) |
                Select-Object -Unique
        )
        $snapshot = [ordered]@{
            schema_version = 1
            canary_run_id = $runId
            sequence = $sequence
            offset_minutes = $offset
            captured_at = [DateTimeOffset]::UtcNow.ToString('O')
            anchor = [ordered]@{
                resume_request_id = $ExpectedResumeRequestId
                resume_executed_at = $anchorExecutedAt
                first_observed_database_at = $anchorDatabaseTime
                monotonic_elapsed_seconds = [Math]::Round(
                    $anchorWatch.Elapsed.TotalSeconds,
                    3
                )
            }
            expected_identity = [ordered]@{
                epoch_id = $ExpectedEpochId
                cohort = $ExpectedCohort
                image_id = $ExpectedImageId
                revision = $ExpectedRevision
                release_digest = $ExpectedReleaseDigest
                schema_head = $ExpectedSchemaHead
            }
            previous_snapshot_sha256 = $previousSnapshotHash
            docker = $dockerState
            resources = $resources
            resource_sampler = [ordered]@{
                total_samples = $resourceSampleCount
                expected_interval_seconds = $GuardIntervalSeconds
                rolling_window = @($resourceWindow)
                violations = @($milestoneResourceViolations)
            }
            worker_logs = $workerLogs
            database = $database
            http = $http
            api_database_coherence = [ordered]@{
                attempts = $coherent.attempts
                stable_api_expected = $coherent.stable_api_expected
                api_expected_sha256 = $coherent.api_expected_sha256
            }
            safety_http = $safetyHttp
            safety = [ordered]@{
                passed = $violations.Count -eq 0
                violations = @($violations)
            }
        }
        $lastObservation = $snapshot
        if ($violations.Count -ne 0) {
            throw "PAPER canary safety divergence: $($violations -join ',')."
        }
        if ($null -eq $executionBaseline) {
            $executionBaseline = $database.execution
        }
        $finalExecution = $database.execution
        $finalWorkerLogs = $workerLogs
        $snapshotPath = Join-Path $runDirectory ('snapshot-{0:D3}m.json' -f $offset)
        $written = Write-ImmutableJson -Path $snapshotPath -Payload $snapshot
        $snapshotFiles.Add($written)
        $previousSnapshotHash = $written.sha256
    }

    if (
        $snapshotFiles.Count -ne $MinuteOffsets.Count -or
        [IO.File]::Exists((Join-Path $runDirectory 'failure.json')) -or
        [IO.File]::Exists((Join-Path $runDirectory 'divergence.json'))
    ) {
        throw 'Canary artifact set is incomplete or contains failure evidence.'
    }
    if (
        $null -eq $executionBaseline -or
        $null -eq $finalExecution -or
        $null -eq $finalWorkerLogs
    ) {
        throw 'Canary execution evidence baseline or final sample is missing.'
    }
    $executionEvidence = Get-ExecutionEvidenceSummary `
        -Baseline $executionBaseline -Final $finalExecution `
        -WorkerLogs $finalWorkerLogs `
        -EarliestSnapshotObserved (
            @($snapshotFiles | Where-Object {
                [string]$_.path -like '*snapshot-000m.json'
            }).Count -eq 1
        )
    $stopEvidence = Invoke-CanaryLeaseRelease -SafetyClient $safetyClient `
        -PostgresContainer $postgresContainer `
        -BinanceBaseline $binanceBaseline
    $manifest = [ordered]@{
        schema_version = 1
        canary_run_id = $runId
        passed = $true
        runtime_canary_passed = $true
        expected_resume_request_id = $ExpectedResumeRequestId
        anchor_database_time = $anchorDatabaseTime
        anchor_executed_at = $anchorExecutedAt
        completed_at = [DateTimeOffset]::UtcNow.ToString('O')
        offsets = @($MinuteOffsets)
        evidence_chain = [ordered]@{
            preflight = $preflightWritten
            lease_acquire = $leaseWritten
            ready = $readyWritten
        }
        snapshots = @($snapshotFiles)
        final_snapshot_sha256 = $previousSnapshotHash
        execution_evidence = $executionEvidence
        emergency_stop_submitted = $true
        emergency_stop = $stopEvidence
        safety_lease_released = $true
        safety_lease_id = $leaseId
        safety_lease_release_request_id = $leaseReleaseRequestId
        terminal_runtime = 'polymarket_paper_fail_closed'
    }
    [void](Write-ImmutableJson -Path (Join-Path $runDirectory 'manifest.json') -Payload $manifest)
    $manifest | ConvertTo-Json -Depth 8 -Compress
}
catch {
    $failureMessage = $_.Exception.GetBaseException().Message
    # The independent safety-control mutation is deliberately first. No
    # Docker, SQL, log, HTTP, or artifact capture may delay a fail-closed stop.
    $stopRequired = (
        $preflightAccepted -and
        $StopPolymarketOnDivergence -and
        $null -ne $postgresContainer -and
        $null -ne $safetyClient -and
        $null -ne $binanceBaseline
    )
    $stopEvidence = Invoke-FailClosedStopBarrier `
        -StopRequired $stopRequired `
        -CurrentStopEvidence $stopEvidence `
        -StopRequestId $stopRequestId `
        -EmergencyStopAction {
            Invoke-EmergencyStop -SafetyClient $safetyClient `
                -Body $stopBody -PostgresContainer $postgresContainer `
                -BinanceBaseline $binanceBaseline
        }
    if ($preflightAccepted -and $null -ne $postgresContainer) {
        $divergence = [ordered]@{
            schema_version = 1
            canary_run_id = $runId
            captured_at = [DateTimeOffset]::UtcNow.ToString('O')
            failure = $failureMessage
            guard = $null
            docker = $null
            resources = $null
            worker_logs = $null
            healthz = $null
            readyz = $null
            safety_healthz = $null
            safety_readyz = $null
            last_observation = $lastObservation
            emergency_stop = $stopEvidence
            capture_errors = @()
        }
        $captureErrors = [Collections.Generic.List[string]]::new()
        try {
            $divergence.guard = Invoke-ReadOnlySql -PostgresContainer $postgresContainer `
                -Sql (Get-GuardSql -CommandBoundaryTime $commandBoundaryTime `
                    -DatabaseSessionBoundary $databaseSessionBoundary)
        }
        catch {
            $captureErrors.Add("database: $($_.Exception.GetBaseException().Message)")
        }
        try {
            $divergence.docker = Get-ContainerSet -ApiContainer $apiContainer `
                -SafetyContainer $safetyContainer `
                -WorkerContainer $workerContainer -PostgresContainer $postgresContainer
            $divergence.resources = Get-ResourceSnapshot -Containers $divergence.docker
            $logEvidenceSince = if ($null -ne $anchorDatabaseTime) {
                [DateTimeOffset]$anchorDatabaseTime
            }
            else {
                $workerStartedAt
            }
            $divergence.worker_logs = Get-WorkerMaintenanceLogSnapshot `
                -WorkerContainer $divergence.docker.worker.container_name `
                -Since $logEvidenceSince
        }
        catch {
            $captureErrors.Add("docker: $($_.Exception.GetBaseException().Message)")
        }
        try {
            $divergence.healthz = Invoke-JsonRequest -Client $client -Method GET -Path 'healthz'
            $divergence.readyz = Invoke-JsonRequest -Client $client -Method GET -Path 'readyz'
            $divergence.safety_healthz = Invoke-JsonRequest `
                -Client $safetyClient -Method GET -Path 'healthz'
            $divergence.safety_readyz = Invoke-JsonRequest `
                -Client $safetyClient -Method GET -Path 'readyz'
        }
        catch {
            $captureErrors.Add("http: $($_.Exception.GetBaseException().Message)")
        }
        $divergence.capture_errors = @($captureErrors)
        try {
            $divergenceEvidence = Write-ImmutableJson `
                -Path (Join-Path $runDirectory 'divergence.json') -Payload $divergence
        }
        catch {
            $divergenceEvidence = [ordered]@{
                status = 'capture_failed'
                error = $_.Exception.GetBaseException().Message
            }
        }
    }
    $failure = [ordered]@{
        schema_version = 1
        canary_run_id = $runId
        passed = $false
        failed_at = [DateTimeOffset]::UtcNow.ToString('O')
        resume_observed_succeeded = $resumeObserved
        failure = $failureMessage
        normal_collection_stopped = $true
        divergence_evidence = $divergenceEvidence
        emergency_stop = $stopEvidence
        snapshots = @($snapshotFiles)
        final_snapshot_sha256 = $previousSnapshotHash
    }
    try {
        [void](
            Write-ImmutableJson -Path (Join-Path $runDirectory 'failure.json') `
                -Payload $failure
        )
    }
    catch {
        # The original failure remains authoritative. The held ownership file
        # prevents another collector from adding artifacts to this run.
    }
    throw $failureMessage
}
finally {
    if ($anchorWatch.IsRunning) {
        $anchorWatch.Stop()
    }
    if ($null -ne $client) {
        $client.Dispose()
    }
    if ($null -ne $safetyClient) {
        $safetyClient.Dispose()
    }
    $leaseNonce = $null
    $ownerStream.Dispose()
}
