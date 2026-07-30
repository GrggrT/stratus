[CmdletBinding()]
param(
    [Uri] $BaseUri = 'http://127.0.0.1:8000/',

    [Security.SecureString] $ApiKey,

    [ValidateRange(300, 3600)]
    [int] $SteadyStateSeconds = 300,

    [ValidateRange(1, 3)]
    [int] $TimeoutSeconds = 3,

    [ValidateRange(1, 3)]
    [int] $MaxConcurrentGet = 3,

    [ValidatePattern('^[0-9a-fA-F]{32}$')]
    [string] $LoadGateRunId,

    [string] $OutputPath,

    [switch] $ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

$CoreEndpoints = @(
    [pscustomobject]@{ Tier = 'core'; Name = 'healthz'; Path = 'healthz' }
    [pscustomobject]@{ Tier = 'core'; Name = 'readyz'; Path = 'readyz' }
    [pscustomobject]@{
        Tier = 'core'
        Name = 'alerts_badge'
        Path = 'api/v1/alerts?state=unacknowledged&limit=1'
    }
    [pscustomobject]@{ Tier = 'core'; Name = 'overview'; Path = 'api/v1/overview' }
    [pscustomobject]@{
        Tier = 'core'
        Name = 'signals'
        Path = 'api/v1/signals?limit=100'
    }
    [pscustomobject]@{
        Tier = 'core'
        Name = 'positions'
        Path = 'api/v1/positions?limit=100'
    }
    [pscustomobject]@{
        Tier = 'core'
        Name = 'paper_equity'
        Path = 'api/v1/equity?mode=paper'
    }
    [pscustomobject]@{
        Tier = 'core'
        Name = 'commands'
        Path = 'api/v1/commands?limit=100'
    }
    [pscustomobject]@{
        Tier = 'core'
        Name = 'jobs'
        Path = 'api/v1/jobs?limit=100'
    }
)

$EvidenceEndpoints = @(
    [pscustomobject]@{
        Tier = 'evidence_execution'
        Name = 'edge_report'
        Path = 'api/v1/edge-report?mode=paper'
    }
    [pscustomobject]@{
        Tier = 'evidence_execution'
        Name = 'validation_ledger'
        Path = 'api/v1/validation-ledger?mode=paper'
    }
    [pscustomobject]@{
        Tier = 'evidence_execution'
        Name = 'selection_policy'
        Path = 'api/v1/selection-policy-report?mode=paper'
    }
    [pscustomobject]@{
        Tier = 'evidence_execution'
        Name = 'resolution_reconciliation'
        Path = 'api/v1/resolution-reconciliation?limit=40'
    }
    [pscustomobject]@{
        Tier = 'evidence_execution'
        Name = 'settlement_cases'
        Path = 'api/v1/settlement-cases?limit=40'
    }
    [pscustomobject]@{
        Tier = 'evidence_execution'
        Name = 'storage_capacity'
        Path = 'api/v1/storage-capacity'
    }
    [pscustomobject]@{
        Tier = 'evidence_execution'
        Name = 'execution_cost'
        Path = 'api/v1/execution-cost-report?mode=paper'
    }
    [pscustomobject]@{
        Tier = 'evidence_execution'
        Name = 'evidence_continuity'
        Path = 'api/v1/evidence-continuity'
    }
)

$DiagnosticEndpoints = @(
    [pscustomobject]@{
        Tier = 'accuracy_provenance'
        Name = 'brier_report'
        Path = 'api/v1/brier-report?mode=paper'
    }
    [pscustomobject]@{
        Tier = 'accuracy_provenance'
        Name = 'clv_report'
        Path = 'api/v1/clv-report?mode=paper'
    }
    [pscustomobject]@{
        Tier = 'accuracy_provenance'
        Name = 'root_cause_actions'
        Path = 'api/v1/root-cause-actions?mode=paper'
    }
    [pscustomobject]@{
        Tier = 'accuracy_provenance'
        Name = 'accuracy_overview'
        Path = 'api/v1/accuracy-overview'
    }
    [pscustomobject]@{
        Tier = 'accuracy_provenance'
        Name = 'forecast_provenance'
        Path = 'api/v1/forecast-provenance'
    }
    [pscustomobject]@{
        Tier = 'accuracy_provenance'
        Name = 'official_resolution_sources'
        Path = 'api/v1/official-resolution-sources'
    }
)

function Assert-BaseUri {
    param(
        [Parameter(Mandatory = $true)]
        [Uri] $Uri
    )

    if (-not $Uri.IsAbsoluteUri) {
        throw 'BaseUri must be absolute.'
    }
    if ($Uri.Scheme -notin @('http', 'https')) {
        throw 'BaseUri must use HTTP or HTTPS.'
    }
    if (-not $Uri.IsLoopback) {
        throw 'BaseUri must be loopback; this harness is restricted to a locally staged API.'
    }
    if (
        -not [string]::IsNullOrEmpty($Uri.Query) -or
        -not [string]::IsNullOrEmpty($Uri.Fragment) -or
        -not [string]::IsNullOrEmpty($Uri.UserInfo) -or
        $Uri.AbsolutePath -ne '/'
    ) {
        throw 'BaseUri must contain only the loopback scheme, authority, port, and trailing slash.'
    }
}

function ConvertFrom-Secret {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString] $Secret
    )

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Test-ReadyFailClosed {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Body
    )

    try {
        $payload = $Body | ConvertFrom-Json
    }
    catch {
        return $false
    }

    $reasons = @($payload.reasons)
    $allowedReasons = @(
        'kill_switch',
        'project_halted',
        'health_audit_stale',
        'health_audit_missing'
    )
    return (
        [string]$payload.status -eq 'not_ready' -and
        $reasons -contains 'kill_switch' -and
        @($reasons | Where-Object { $_ -notin $allowedReasons }).Count -eq 0
    )
}

function Test-ReadyHttpResponse {
    param(
        [Parameter(Mandatory = $true)]
        [int] $StatusCode,

        [Parameter(Mandatory = $true)]
        [string] $Body
    )

    return (
        $StatusCode -eq 503 -and
        (Test-ReadyFailClosed -Body $Body)
    )
}


function Test-JsonResponseBody {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Body
    )

    if ([string]::IsNullOrWhiteSpace($Body)) {
        return $false
    }
    try {
        return $null -ne ($Body | ConvertFrom-Json)
    }
    catch {
        return $false
    }
}


function Get-HttpTelemetrySnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [Net.Http.HttpClient] $Client,

        [Parameter(Mandatory = $true)]
        [string] $RunId,

        [Parameter(Mandatory = $true)]
        [int] $RequestTimeoutSeconds
    )

    $cancellation = [Threading.CancellationTokenSource]::new()
    $response = $null
    try {
        $cancellation.CancelAfter([TimeSpan]::FromSeconds($RequestTimeoutSeconds))
        $response = $Client.GetAsync(
            'api/v1/runtime-http-counters',
            $cancellation.Token
        ).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "Runtime HTTP counters returned status $([int]$response.StatusCode)."
        }
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $snapshot = $body | ConvertFrom-Json
        if ([string]$snapshot.run_id -ne $RunId) {
            throw 'Runtime HTTP counters returned a different load-gate run ID.'
        }
        return $snapshot
    }
    finally {
        if ($null -ne $response) {
            $response.Dispose()
        }
        $cancellation.Dispose()
    }
}


function New-RequestResult {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Endpoint,

        [Parameter(Mandatory = $true)]
        [string] $Phase,

        [Parameter(Mandatory = $true)]
        [int] $Iteration,

        [Parameter(Mandatory = $true)]
        [double] $ScheduledOffsetSeconds,

        [Parameter(Mandatory = $true)]
        [DateTimeOffset] $StartedAt,

        [Parameter(Mandatory = $true)]
        [double] $ElapsedMilliseconds,

        [Nullable[int]] $StatusCode,

        [Parameter(Mandatory = $true)]
        [bool] $Accepted,

        [Parameter(Mandatory = $true)]
        [bool] $TimedOut,

        [Parameter(Mandatory = $true)]
        [bool] $ReadyFailClosed,

        [string] $ErrorMessage
    )

    [pscustomobject]@{
        phase = $Phase
        tier = $Endpoint.Tier
        iteration = $Iteration
        scheduled_offset_seconds = [Math]::Round($ScheduledOffsetSeconds, 3)
        endpoint = $Endpoint.Name
        path = $Endpoint.Path
        started_at = $StartedAt.ToUniversalTime().ToString('O')
        elapsed_ms = [Math]::Round($ElapsedMilliseconds, 3)
        status_code = $StatusCode
        accepted = $Accepted
        timeout = $TimedOut
        readyz_fail_closed = $ReadyFailClosed
        error = $ErrorMessage
    }
}

function Start-GetRequest {
    param(
        [Parameter(Mandatory = $true)]
        [Net.Http.HttpClient] $Client,

        [Parameter(Mandatory = $true)]
        [object] $Endpoint,

        [Parameter(Mandatory = $true)]
        [string] $Phase,

        [Parameter(Mandatory = $true)]
        [int] $Iteration,

        [Parameter(Mandatory = $true)]
        [double] $ScheduledOffsetSeconds,

        [Parameter(Mandatory = $true)]
        [int] $RequestTimeoutSeconds
    )

    $startedAt = [DateTimeOffset]::UtcNow
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $cancellation = [Threading.CancellationTokenSource]::new()
    # The timeout belongs to this request and starts only when its GET starts,
    # never while the endpoint is waiting for a concurrency slot.
    $cancellation.CancelAfter([TimeSpan]::FromSeconds($RequestTimeoutSeconds))
    try {
        $uri = [Uri]::new($Client.BaseAddress, [string]$Endpoint.Path)
        $task = $Client.GetAsync($uri, $cancellation.Token)
        return [pscustomobject]@{
            WorkItem = [pscustomobject]@{
                Endpoint = $Endpoint
                StartedAt = $startedAt
                Stopwatch = $stopwatch
                Cancellation = $cancellation
                Task = $task
            }
            ImmediateResult = $null
        }
    }
    catch {
        $stopwatch.Stop()
        $cancellation.Dispose()
        return [pscustomobject]@{
            WorkItem = $null
            ImmediateResult = (New-RequestResult `
                -Endpoint $Endpoint `
                -Phase $Phase `
                -Iteration $Iteration `
                -ScheduledOffsetSeconds $ScheduledOffsetSeconds `
                -StartedAt $startedAt `
                -ElapsedMilliseconds $stopwatch.Elapsed.TotalMilliseconds `
                -StatusCode $null `
                -Accepted $false `
                -TimedOut $false `
                -ReadyFailClosed $false `
                -ErrorMessage $_.Exception.GetBaseException().Message)
        }
    }
}

function Complete-GetRequest {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Item,

        [Parameter(Mandatory = $true)]
        [string] $Phase,

        [Parameter(Mandatory = $true)]
        [int] $Iteration,

        [Parameter(Mandatory = $true)]
        [double] $ScheduledOffsetSeconds,

        [Parameter(Mandatory = $true)]
        [int] $RequestTimeoutSeconds
    )

    $statusCode = $null
    $accepted = $false
    $timedOut = $false
    $readyFailClosed = $false
    $errorMessage = $null
    $response = $null
    try {
        $response = $Item.Task.GetAwaiter().GetResult()
        $statusCode = [int]$response.StatusCode
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($Item.Endpoint.Name -eq 'readyz') {
            if (Test-ReadyHttpResponse -StatusCode $statusCode -Body $body) {
                $accepted = $true
                $readyFailClosed = $true
            }
            else {
                $errorMessage = if ($statusCode -ge 200 -and $statusCode -lt 300) {
                    'readyz returned success; staged API is not in the required kill-switch fail-closed state'
                }
                elseif ($statusCode -eq 503) {
                    'readyz 503 did not contain an expected staged kill-switch fail-closed state'
                }
                else {
                    "unexpected HTTP status $statusCode"
                }
            }
        }
        elseif ($Item.Endpoint.Name -eq 'healthz') {
            if (
                $statusCode -ge 200 -and
                $statusCode -lt 300 -and
                (Test-JsonResponseBody -Body $body) -and
                [string](($body | ConvertFrom-Json).status) -eq 'ok'
            ) {
                $accepted = $true
            }
            else {
                $errorMessage = 'healthz did not return the expected JSON liveness contract'
            }
        }
        elseif ($statusCode -ge 200 -and $statusCode -lt 300) {
            if (Test-JsonResponseBody -Body $body) {
                $accepted = $true
            }
            else {
                $errorMessage = 'successful response body is not valid JSON'
            }
        }
        else {
            $errorMessage = "unexpected HTTP status $statusCode"
        }
    }
    catch {
        $timedOut = $Item.Cancellation.IsCancellationRequested
        $errorMessage = if ($timedOut) {
            "timeout after $RequestTimeoutSeconds second(s)"
        }
        else {
            $_.Exception.GetBaseException().Message
        }
    }
    finally {
        if ($null -ne $response) {
            $response.Dispose()
        }
        $Item.Stopwatch.Stop()
        $Item.Cancellation.Dispose()
    }

    $latencyBudgetMilliseconds = $RequestTimeoutSeconds * 1000
    if ($accepted -and $Item.Stopwatch.Elapsed.TotalMilliseconds -ge $latencyBudgetMilliseconds) {
        $accepted = $false
        $errorMessage = "latency exceeded the ${RequestTimeoutSeconds}s Cockpit budget"
    }

    return (New-RequestResult `
        -Endpoint $Item.Endpoint `
        -Phase $Phase `
        -Iteration $Iteration `
        -ScheduledOffsetSeconds $ScheduledOffsetSeconds `
        -StartedAt $Item.StartedAt `
        -ElapsedMilliseconds $Item.Stopwatch.Elapsed.TotalMilliseconds `
        -StatusCode $statusCode `
        -Accepted $accepted `
        -TimedOut $timedOut `
        -ReadyFailClosed $readyFailClosed `
        -ErrorMessage $errorMessage)
}

function Invoke-EndpointTier {
    param(
        [Parameter(Mandatory = $true)]
        [Net.Http.HttpClient] $Client,

        [Parameter(Mandatory = $true)]
        [object[]] $Endpoints,

        [Parameter(Mandatory = $true)]
        [string] $Phase,

        [Parameter(Mandatory = $true)]
        [int] $Iteration,

        [Parameter(Mandatory = $true)]
        [double] $ScheduledOffsetSeconds,

        [Parameter(Mandatory = $true)]
        [int] $Concurrency,

        [Parameter(Mandatory = $true)]
        [int] $RequestTimeoutSeconds,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[object]] $Results
    )

    # Work-conserving queue: keep up to Concurrency GETs active and refill a
    # slot immediately after any request completes. There are no batch
    # barriers, so one slow request cannot leave otherwise available slots idle.
    $pending = [Collections.Generic.List[object]]::new()
    $nextEndpoint = 0
    while ($nextEndpoint -lt $Endpoints.Count -or $pending.Count -gt 0) {
        while (
            $nextEndpoint -lt $Endpoints.Count -and
            $pending.Count -lt $Concurrency
        ) {
            $started = Start-GetRequest `
                -Client $Client `
                -Endpoint $Endpoints[$nextEndpoint] `
                -Phase $Phase `
                -Iteration $Iteration `
                -ScheduledOffsetSeconds $ScheduledOffsetSeconds `
                -RequestTimeoutSeconds $RequestTimeoutSeconds
            $nextEndpoint += 1
            if ($null -ne $started.WorkItem) {
                $pending.Add($started.WorkItem)
            }
            else {
                $Results.Add($started.ImmediateResult)
            }
        }

        if ($pending.Count -eq 0) {
            continue
        }

        $tasks = [Threading.Tasks.Task[]]@(
            $pending | ForEach-Object { $_.Task }
        )
        $completedIndex = [Threading.Tasks.Task]::WaitAny($tasks)
        $completed = $pending[$completedIndex]
        $pending.RemoveAt($completedIndex)
        $Results.Add((Complete-GetRequest `
            -Item $completed `
            -Phase $Phase `
            -Iteration $Iteration `
            -ScheduledOffsetSeconds $ScheduledOffsetSeconds `
            -RequestTimeoutSeconds $RequestTimeoutSeconds))
    }
}

function Get-EndpointSummary {
    param(
        [Parameter(Mandatory = $true)]
        [object[]] $Results
    )

    return @(
        $Results |
            Group-Object endpoint |
            Sort-Object Name |
            ForEach-Object {
                $rows = @($_.Group)
                $latencies = @($rows | ForEach-Object { [double]$_.elapsed_ms } | Sort-Object)
                $p95Index = [Math]::Max(
                    0,
                    [Math]::Ceiling($latencies.Count * 0.95) - 1
                )
                $statusCounts = @(
                    $rows |
                        Where-Object { $null -ne $_.status_code } |
                        Group-Object status_code |
                        Sort-Object Name |
                        ForEach-Object {
                            [pscustomobject]@{
                                status_code = [int]$_.Name
                                count = $_.Count
                            }
                        }
                )
                [pscustomobject]@{
                    endpoint = $_.Name
                    tier = $rows[0].tier
                    count = $rows.Count
                    accepted = @($rows | Where-Object accepted).Count
                    errors = @($rows | Where-Object { -not $_.accepted }).Count
                    timeouts = @($rows | Where-Object timeout).Count
                    readyz_fail_closed = @($rows | Where-Object readyz_fail_closed).Count
                    latency_ms = [pscustomobject]@{
                        minimum = [Math]::Round($latencies[0], 3)
                        average = [Math]::Round(
                            ($latencies | Measure-Object -Average).Average,
                            3
                        )
                        p95 = [Math]::Round($latencies[$p95Index], 3)
                        maximum = [Math]::Round($latencies[-1], 3)
                    }
                    statuses = $statusCounts
                }
            }
    )
}

function Write-ResultJson {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Result,

        [string] $Path
    )

    $json = $Result | ConvertTo-Json -Depth 8
    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        $resolvedPath = [IO.Path]::GetFullPath($Path)
        $parent = Split-Path -Parent $resolvedPath
        if (-not [string]::IsNullOrWhiteSpace($parent)) {
            [void](New-Item -ItemType Directory -Path $parent -Force)
        }
        $temporaryPath = "$resolvedPath.tmp"
        [IO.File]::WriteAllText(
            $temporaryPath,
            $json,
            [Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporaryPath -Destination $resolvedPath -Force
    }
    $json
}

$validation = [pscustomobject]@{
    schema_version = 3
    gate = 'stratus_dashboard_http_load'
    validated = $true
    http_subgate_only = $true
    http_passed = $null
    requires_external_runtime_observation = $true
    production_gate_passed = $null
    request_method = 'GET'
    mutation_requests = 0
    contamination_policy = 'fail on any foreign or in-flight /api/v1 request or telemetry mismatch'
    telemetry_endpoint = 'api/v1/runtime-http-counters'
    accepts_external_run_id = $true
    base_uri_restricted_to_loopback = $true
    cold_refresh = [pscustomobject]@{
        core_requests = $CoreEndpoints.Count
        evidence_execution_requests = $EvidenceEndpoints.Count
        accuracy_provenance_requests = $DiagnosticEndpoints.Count
        total_requests = $CoreEndpoints.Count + $EvidenceEndpoints.Count + $DiagnosticEndpoints.Count
    }
    cadence_seconds = [pscustomobject]@{
        core = 10
        evidence_execution = 60
        accuracy_provenance = 300
    }
    max_concurrent_get = $MaxConcurrentGet
    scheduling_policy = 'work_conserving'
    timeout_seconds = $TimeoutSeconds
    readyz_policy = 'require HTTP 503 and status=not_ready with kill_switch; reject HTTP 2xx'
    endpoints = @($CoreEndpoints + $EvidenceEndpoints + $DiagnosticEndpoints)
}

if ($ValidateOnly) {
    $validation | ConvertTo-Json -Depth 6 -Compress
    return
}

$startedAt = [DateTimeOffset]::UtcNow
$runId = if ([string]::IsNullOrWhiteSpace($LoadGateRunId)) {
    [guid]::NewGuid().ToString('N')
}
else {
    $LoadGateRunId.ToLowerInvariant()
}
$allResults = [Collections.Generic.List[object]]::new()
$httpClient = $null
$handler = $null
$exitCode = 2
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $staleOutput = [IO.Path]::GetFullPath($OutputPath)
    Remove-Item -LiteralPath $staleOutput -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "$staleOutput.tmp" -Force -ErrorAction SilentlyContinue
}
try {
    Assert-BaseUri -Uri $BaseUri
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseProxy = $false
    $handler.MaxConnectionsPerServer = $MaxConcurrentGet
    $httpClient = [Net.Http.HttpClient]::new($handler, $true)
    $httpClient.BaseAddress = $BaseUri
    $httpClient.Timeout = [Threading.Timeout]::InfiniteTimeSpan
    [void]$httpClient.DefaultRequestHeaders.TryAddWithoutValidation(
        'User-Agent',
        'STRATUS-Dashboard-Load-Gate/1'
    )
    [void]$httpClient.DefaultRequestHeaders.TryAddWithoutValidation(
        'X-Stratus-Load-Gate-Run',
        $runId
    )

    $plainApiKey = if ($null -ne $ApiKey) {
        ConvertFrom-Secret -Secret $ApiKey
    }
    else {
        [Environment]::GetEnvironmentVariable('STRATUS_OPERATOR_API_KEY', 'Process')
    }
    if (-not [string]::IsNullOrWhiteSpace($plainApiKey)) {
        [void]$httpClient.DefaultRequestHeaders.TryAddWithoutValidation(
            'X-API-Key',
            $plainApiKey
        )
    }
    $plainApiKey = $null
    $telemetryBefore = Get-HttpTelemetrySnapshot `
        -Client $httpClient `
        -RunId $runId `
        -RequestTimeoutSeconds $TimeoutSeconds

    $iteration = 1
    Invoke-EndpointTier `
        -Client $httpClient `
        -Endpoints $CoreEndpoints `
        -Phase 'cold_full' `
        -Iteration $iteration `
        -ScheduledOffsetSeconds 0 `
        -Concurrency $MaxConcurrentGet `
        -RequestTimeoutSeconds $TimeoutSeconds `
        -Results $allResults
    Invoke-EndpointTier `
        -Client $httpClient `
        -Endpoints $EvidenceEndpoints `
        -Phase 'cold_full' `
        -Iteration $iteration `
        -ScheduledOffsetSeconds 0 `
        -Concurrency $MaxConcurrentGet `
        -RequestTimeoutSeconds $TimeoutSeconds `
        -Results $allResults
    Invoke-EndpointTier `
        -Client $httpClient `
        -Endpoints $DiagnosticEndpoints `
        -Phase 'cold_full' `
        -Iteration $iteration `
        -ScheduledOffsetSeconds 0 `
        -Concurrency $MaxConcurrentGet `
        -RequestTimeoutSeconds $TimeoutSeconds `
        -Results $allResults

    $steadyClock = [Diagnostics.Stopwatch]::StartNew()
    $nextCore = 10.0
    $nextEvidence = 60.0
    $nextDiagnostics = 300.0
    while ($true) {
        $nextDue = [Math]::Min($nextCore, [Math]::Min($nextEvidence, $nextDiagnostics))
        if ($nextDue -gt $SteadyStateSeconds) {
            break
        }

        while ($steadyClock.Elapsed.TotalSeconds -lt $nextDue) {
            $remainingMilliseconds = [Math]::Ceiling(
                ($nextDue - $steadyClock.Elapsed.TotalSeconds) * 1000
            )
            Start-Sleep -Milliseconds ([Math]::Min(500, $remainingMilliseconds))
        }

        $iteration += 1
        $observedOffset = $steadyClock.Elapsed.TotalSeconds
        $coreDue = $nextCore -le $observedOffset
        $evidenceDue = $nextEvidence -le $observedOffset
        $diagnosticsDue = $nextDiagnostics -le $observedOffset

        if ($coreDue) {
            Invoke-EndpointTier `
                -Client $httpClient `
                -Endpoints $CoreEndpoints `
                -Phase 'steady_state' `
                -Iteration $iteration `
                -ScheduledOffsetSeconds $nextCore `
                -Concurrency $MaxConcurrentGet `
                -RequestTimeoutSeconds $TimeoutSeconds `
                -Results $allResults
        }
        if ($evidenceDue) {
            Invoke-EndpointTier `
                -Client $httpClient `
                -Endpoints $EvidenceEndpoints `
                -Phase 'steady_state' `
                -Iteration $iteration `
                -ScheduledOffsetSeconds $nextEvidence `
                -Concurrency $MaxConcurrentGet `
                -RequestTimeoutSeconds $TimeoutSeconds `
                -Results $allResults
        }
        if ($diagnosticsDue) {
            Invoke-EndpointTier `
                -Client $httpClient `
                -Endpoints $DiagnosticEndpoints `
                -Phase 'steady_state' `
                -Iteration $iteration `
                -ScheduledOffsetSeconds $nextDiagnostics `
                -Concurrency $MaxConcurrentGet `
                -RequestTimeoutSeconds $TimeoutSeconds `
                -Results $allResults
        }

        # Match the Cockpit: timer ticks that elapsed while a refresh was active
        # are dropped, never replayed in an immediate catch-up loop.
        $afterRefresh = $steadyClock.Elapsed.TotalSeconds
        while ($nextCore -le $afterRefresh) {
            $nextCore += 10
        }
        while ($nextEvidence -le $afterRefresh) {
            $nextEvidence += 60
        }
        while ($nextDiagnostics -le $afterRefresh) {
            $nextDiagnostics += 300
        }
    }
    $steadyClock.Stop()

    $telemetryAfter = Get-HttpTelemetrySnapshot `
        -Client $httpClient `
        -RunId $runId `
        -RequestTimeoutSeconds $TimeoutSeconds
    $rows = @($allResults)
    $failures = @($rows | Where-Object { -not $_.accepted })
    $runStarted = (
        [int]$telemetryAfter.run_started -
        [int]$telemetryBefore.run_started
    )
    $runCompleted = (
        [int]$telemetryAfter.run_completed -
        [int]$telemetryBefore.run_completed
    )
    $runAborted = (
        [int]$telemetryAfter.run_aborted -
        [int]$telemetryBefore.run_aborted
    )
    $run5xx = (
        [int]$telemetryAfter.run_5xx -
        [int]$telemetryBefore.run_5xx
    )
    $totalStarted = (
        [int]$telemetryAfter.total_started -
        [int]$telemetryBefore.total_started
    )
    $totalCompleted = (
        [int]$telemetryAfter.total_completed -
        [int]$telemetryBefore.total_completed
    )
    $totalAborted = (
        [int]$telemetryAfter.total_aborted -
        [int]$telemetryBefore.total_aborted
    )
    $total5xx = (
        [int]$telemetryAfter.total_5xx -
        [int]$telemetryBefore.total_5xx
    )
    $foreignStarted = $totalStarted - $runStarted
    $foreignCompleted = $totalCompleted - $runCompleted
    $foreign5xx = $total5xx - $run5xx
    $untaggedStarted = (
        [int]$telemetryAfter.untagged_api_started -
        [int]$telemetryBefore.untagged_api_started
    )
    $untaggedCompleted = (
        [int]$telemetryAfter.untagged_api_completed -
        [int]$telemetryBefore.untagged_api_completed
    )
    # Runtime telemetry intentionally covers only /api/v1. Health liveness and
    # fail-closed readiness are validated by their response contracts above,
    # but they must not be included in the API-counter reconciliation.
    # The baseline telemetry response is recorded after its response body was
    # captured. The closing telemetry response is recorded after this snapshot.
    $apiRows = @(
        $rows | Where-Object { [string]$_.path -like 'api/v1/*' }
    )
    $expectedTaggedRequests = $apiRows.Count + 1
    $expectedTagged5xx = @(
        $apiRows | Where-Object {
            $null -ne $_.status_code -and [int]$_.status_code -ge 500
        }
    ).Count
    $telemetryMismatch = (
        $runStarted -ne $expectedTaggedRequests -or
        $runCompleted -ne $expectedTaggedRequests -or
        $runAborted -ne 0 -or
        $run5xx -ne $expectedTagged5xx -or
        $totalStarted -ne $runStarted -or
        $totalCompleted -ne $runCompleted -or
        $totalAborted -ne 0 -or
        $total5xx -ne $run5xx -or
        [int]$telemetryBefore.active_requests -ne 1 -or
        [int]$telemetryAfter.active_requests -ne 1
    )
    $contaminated = (
        $foreignStarted -ne 0 -or
        $foreignCompleted -ne 0 -or
        $foreign5xx -ne 0 -or
        $untaggedStarted -ne 0 -or
        $untaggedCompleted -ne 0 -or
        $telemetryMismatch
    )
    $exitCode = if ($failures.Count -eq 0 -and -not $contaminated) { 0 } else { 1 }
    $result = [pscustomobject]@{
        schema_version = 3
        gate = 'stratus_dashboard_http_load'
        http_passed = $exitCode -eq 0
        contaminated = $contaminated
        requires_external_runtime_observation = $true
        production_gate_passed = $null
        exit_code = $exitCode
        started_at = $startedAt.ToString('O')
        completed_at = [DateTimeOffset]::UtcNow.ToString('O')
        base_uri = $BaseUri.GetLeftPart([UriPartial]::Authority)
        request_method = 'GET'
        mutation_requests = 0
        load_gate_run_id = $runId
        telemetry = [pscustomobject]@{
            foreign_started = $foreignStarted
            foreign_completed = $foreignCompleted
            foreign_5xx = $foreign5xx
            untagged_started = $untaggedStarted
            untagged_completed = $untaggedCompleted
            run_started = $runStarted
            run_completed = $runCompleted
            run_aborted = $runAborted
            expected_run_requests = $expectedTaggedRequests
            run_5xx = $run5xx
            expected_run_5xx = $expectedTagged5xx
            total_started = $totalStarted
            total_completed = $totalCompleted
            total_aborted = $totalAborted
            total_5xx = $total5xx
            counter_mismatch = $telemetryMismatch
            before = $telemetryBefore
            after = $telemetryAfter
        }
        configuration = [pscustomobject]@{
            cold_full_refreshes = 1
            steady_state_seconds = $SteadyStateSeconds
            core_interval_seconds = 10
            evidence_execution_interval_seconds = 60
            accuracy_provenance_interval_seconds = 300
            max_concurrent_get = $MaxConcurrentGet
            scheduling_policy = 'work_conserving'
            timeout_seconds = $TimeoutSeconds
        }
        totals = [pscustomobject]@{
            requests = $rows.Count
            accepted = @($rows | Where-Object accepted).Count
            errors = $failures.Count
            timeouts = @($rows | Where-Object timeout).Count
            readyz_expected_fail_closed = @(
                $rows | Where-Object readyz_fail_closed
            ).Count
        }
        endpoint_summary = @(Get-EndpointSummary -Results $rows)
        requests = $rows
    }
    Write-ResultJson -Result $result -Path $OutputPath
}
catch {
    $fatalResult = [pscustomobject]@{
        schema_version = 3
        gate = 'stratus_dashboard_http_load'
        http_passed = $false
        contaminated = $null
        requires_external_runtime_observation = $true
        production_gate_passed = $null
        exit_code = 2
        started_at = $startedAt.ToString('O')
        completed_at = [DateTimeOffset]::UtcNow.ToString('O')
        base_uri = if ($BaseUri.IsAbsoluteUri) {
            $BaseUri.GetLeftPart([UriPartial]::Authority)
        }
        else {
            $null
        }
        request_method = 'GET'
        mutation_requests = 0
        load_gate_run_id = $runId
        fatal_error = $_.Exception.GetBaseException().Message
        requests = @($allResults)
    }
    Write-ResultJson -Result $fatalResult -Path $OutputPath
    $exitCode = 2
}
finally {
    if ($null -ne $httpClient) {
        $httpClient.Dispose()
    }
    elseif ($null -ne $handler) {
        $handler.Dispose()
    }
}

exit $exitCode
