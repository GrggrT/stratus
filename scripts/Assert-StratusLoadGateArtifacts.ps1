[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $HttpArtifactPath,

    [Parameter(Mandatory = $true)]
    [string] $RuntimeArtifactPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{32}$')]
    [string] $ExpectedRunId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha256:[0-9a-f]{64}$')]
    [string] $ExpectedApiImageId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedRevision,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedReleaseDigest,

    [string] $OutputPath = '.stratus-cache\load-gates\candidate-accepted.json',

    [switch] $ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$validation = [pscustomobject]@{
    schema_version = 1
    gate = 'stratus_candidate_production_load'
    validated = $true
    requires_matching_run_id = $true
    requires_http_pass = $true
    requires_runtime_pass = $true
    requires_full_runtime_overlap = $true
    requires_exact_api_identity = $true
    requires_exact_loopback_endpoint = $true
    requires_raw_sample_coverage = $true
    requires_exact_http_configuration = $true
    requires_raw_http_reconciliation = $true
    requires_raw_runtime_reconciliation = $true
    requires_raw_topology_reconciliation = $true
    requires_topology_hash_binding = $true
    production_gate_passed = $null
}
if ($ValidateOnly) {
    $validation | ConvertTo-Json -Compress
    return
}

function Write-GateArtifact {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][object] $Artifact
    )

    $directory = [IO.Path]::GetDirectoryName($Path)
    if (-not [string]::IsNullOrWhiteSpace($directory)) {
        [void][IO.Directory]::CreateDirectory($directory)
    }
    $temporary = "$Path.tmp"
    [IO.File]::WriteAllText(
        $temporary,
        ($Artifact | ConvertTo-Json -Depth 8),
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Test-HasProperties {
    param(
        [object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Names
    )

    if ($null -eq $Value) {
        return $false
    }
    $available = @($Value.PSObject.Properties.Name)
    foreach ($name in $Names) {
        if ($available -notcontains $name) {
            return $false
        }
    }
    return $true
}

function Test-FiniteNumber {
    param([object] $Value)

    if ($null -eq $Value -or $Value -is [string] -or $Value -is [bool]) {
        return $false
    }
    try {
        $number = [double]$Value
        return -not [double]::IsNaN($number) -and -not [double]::IsInfinity($number)
    }
    catch {
        return $false
    }
}

function Test-NonNegativeInteger {
    param([object] $Value)

    return (
        (Test-FiniteNumber $Value) -and
        [double]$Value -ge 0 -and
        [Math]::Floor([double]$Value) -eq [double]$Value
    )
}

function Test-NearlyEqual {
    param(
        [object] $Left,
        [object] $Right,
        [double] $Tolerance = 0.001
    )

    return (
        (Test-FiniteNumber $Left) -and
        (Test-FiniteNumber $Right) -and
        [Math]::Abs([double]$Left - [double]$Right) -le $Tolerance
    )
}

function Try-ParseTimestamp {
    param(
        [object] $Value,
        [ref] $Parsed
    )

    $timestamp = [DateTimeOffset]::MinValue
    $ok = (
        $null -ne $Value -and
        [DateTimeOffset]::TryParse(
            [string]$Value,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind,
            [ref]$timestamp
        )
    )
    $Parsed.Value = $timestamp
    return $ok
}

function Add-UniqueViolation {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[string]] $Violations,
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    if (-not $Violations.Contains($Name)) {
        $Violations.Add($Name)
    }
}

function Get-RawMaximum {
    param(
        [Parameter(Mandatory = $true)][object[]] $Rows,
        [Parameter(Mandatory = $true)][string] $Property
    )

    if ($Rows.Count -eq 0) {
        return $null
    }
    return ($Rows | Measure-Object -Property $Property -Maximum).Maximum
}

function Test-ExactStringSet {
    param(
        [AllowNull()][object[]] $Actual,
        [Parameter(Mandatory = $true)][string[]] $Expected
    )

    $actualStrings = @($Actual | ForEach-Object { [string]$_ })
    $expectedStrings = @($Expected | ForEach-Object { [string]$_ })
    return (
        $actualStrings.Count -eq $expectedStrings.Count -and
        @($actualStrings | Sort-Object -Unique).Count -eq $actualStrings.Count -and
        (@($actualStrings | Sort-Object) -join "`n") -ceq
            (@($expectedStrings | Sort-Object) -join "`n")
    )
}

function Get-BytesSha256 {
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

function Get-TopologyRowKey {
    param([Parameter(Mandatory = $true)][object] $Row)

    $networks = @($Row.networks | ForEach-Object { [string]$_ } | Sort-Object)
    return @(
        [string]$Row.container_id,
        [string]$Row.project,
        [string]$Row.service,
        [string]$Row.oneoff,
        [string]$Row.container_name,
        ($networks -join ',')
    ) -join "`n"
}

$startedAt = [DateTimeOffset]::UtcNow
$runId = $ExpectedRunId.ToLowerInvariant()
$absoluteOutput = [IO.Path]::GetFullPath($OutputPath)
Remove-Item -LiteralPath $absoluteOutput -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$absoluteOutput.tmp" -Force -ErrorAction SilentlyContinue
$exitCode = 2
try {
    $absoluteHttp = [IO.Path]::GetFullPath($HttpArtifactPath)
    $absoluteRuntime = [IO.Path]::GetFullPath($RuntimeArtifactPath)
    if (-not [IO.File]::Exists($absoluteHttp)) {
        throw 'Dashboard HTTP artifact does not exist.'
    }
    if (-not [IO.File]::Exists($absoluteRuntime)) {
        throw 'Runtime observation artifact does not exist.'
    }

    $strictUtf8 = [Text.UTF8Encoding]::new($false, $true)
    $httpBytes = [IO.File]::ReadAllBytes($absoluteHttp)
    $runtimeBytes = [IO.File]::ReadAllBytes($absoluteRuntime)
    $httpSha256 = Get-BytesSha256 -Bytes $httpBytes
    $runtimeSha256 = Get-BytesSha256 -Bytes $runtimeBytes
    $http = $strictUtf8.GetString($httpBytes) | ConvertFrom-Json
    $runtime = $strictUtf8.GetString($runtimeBytes) | ConvertFrom-Json
    $violations = [Collections.Generic.List[string]]::new()

    $expectedEndpoints = @(
        [pscustomobject]@{ tier = 'core'; endpoint = 'healthz'; path = 'healthz'; interval = 10 }
        [pscustomobject]@{ tier = 'core'; endpoint = 'readyz'; path = 'readyz'; interval = 10 }
        [pscustomobject]@{ tier = 'core'; endpoint = 'alerts_badge'; path = 'api/v1/alerts?state=unacknowledged&limit=1'; interval = 10 }
        [pscustomobject]@{ tier = 'core'; endpoint = 'overview'; path = 'api/v1/overview'; interval = 10 }
        [pscustomobject]@{ tier = 'core'; endpoint = 'signals'; path = 'api/v1/signals?limit=100'; interval = 10 }
        [pscustomobject]@{ tier = 'core'; endpoint = 'positions'; path = 'api/v1/positions?limit=100'; interval = 10 }
        [pscustomobject]@{ tier = 'core'; endpoint = 'paper_equity'; path = 'api/v1/equity?mode=paper'; interval = 10 }
        [pscustomobject]@{ tier = 'core'; endpoint = 'commands'; path = 'api/v1/commands?limit=100'; interval = 10 }
        [pscustomobject]@{ tier = 'core'; endpoint = 'jobs'; path = 'api/v1/jobs?limit=100'; interval = 10 }
        [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'edge_report'; path = 'api/v1/edge-report?mode=paper'; interval = 60 }
        [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'validation_ledger'; path = 'api/v1/validation-ledger?mode=paper'; interval = 60 }
        [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'selection_policy'; path = 'api/v1/selection-policy-report?mode=paper'; interval = 60 }
        [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'resolution_reconciliation'; path = 'api/v1/resolution-reconciliation?limit=40'; interval = 60 }
        [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'settlement_cases'; path = 'api/v1/settlement-cases?limit=40'; interval = 60 }
        [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'storage_capacity'; path = 'api/v1/storage-capacity'; interval = 60 }
        [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'execution_cost'; path = 'api/v1/execution-cost-report?mode=paper'; interval = 60 }
        [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'evidence_continuity'; path = 'api/v1/evidence-continuity'; interval = 60 }
        [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'brier_report'; path = 'api/v1/brier-report?mode=paper'; interval = 300 }
        [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'clv_report'; path = 'api/v1/clv-report?mode=paper'; interval = 300 }
        [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'root_cause_actions'; path = 'api/v1/root-cause-actions?mode=paper'; interval = 300 }
        [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'accuracy_overview'; path = 'api/v1/accuracy-overview'; interval = 300 }
        [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'forecast_provenance'; path = 'api/v1/forecast-provenance'; interval = 300 }
        [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'official_resolution_sources'; path = 'api/v1/official-resolution-sources'; interval = 300 }
    )
    $expectedByName = @{}
    foreach ($endpoint in $expectedEndpoints) {
        $expectedByName[[string]$endpoint.endpoint] = $endpoint
    }

    $httpTopFields = @(
        'schema_version', 'gate', 'http_passed', 'contaminated', 'exit_code',
        'base_uri', 'request_method', 'mutation_requests', 'load_gate_run_id',
        'started_at', 'completed_at', 'telemetry', 'configuration', 'totals',
        'endpoint_summary', 'requests'
    )
    if (-not (Test-HasProperties $http $httpTopFields)) {
        Add-UniqueViolation $violations 'http_contract'
    }
    else {
        if (
            -not (Test-NonNegativeInteger $http.schema_version) -or
            [int]$http.schema_version -ne 3 -or
            [string]$http.gate -ne 'stratus_dashboard_http_load' -or
            $http.http_passed -isnot [bool] -or
            -not [bool]$http.http_passed -or
            $http.contaminated -isnot [bool] -or
            [bool]$http.contaminated -or
            -not (Test-NonNegativeInteger $http.exit_code) -or
            [int]$http.exit_code -ne 0 -or
            [string]$http.base_uri -ne 'http://127.0.0.1:8000' -or
            [string]$http.request_method -ne 'GET' -or
            -not (Test-NonNegativeInteger $http.mutation_requests) -or
            [int]$http.mutation_requests -ne 0
        ) {
            Add-UniqueViolation $violations 'http_gate'
        }
        $httpConfigurationFields = @(
            'cold_full_refreshes', 'steady_state_seconds',
            'core_interval_seconds', 'evidence_execution_interval_seconds',
            'accuracy_provenance_interval_seconds', 'max_concurrent_get',
            'scheduling_policy', 'timeout_seconds'
        )
        if (
            -not (Test-HasProperties $http.configuration $httpConfigurationFields) -or
            -not (Test-NonNegativeInteger $http.configuration.cold_full_refreshes) -or
            [int]$http.configuration.cold_full_refreshes -ne 1 -or
            -not (Test-NonNegativeInteger $http.configuration.steady_state_seconds) -or
            [int]$http.configuration.steady_state_seconds -ne 300 -or
            -not (Test-NonNegativeInteger $http.configuration.core_interval_seconds) -or
            [int]$http.configuration.core_interval_seconds -ne 10 -or
            -not (Test-NonNegativeInteger $http.configuration.evidence_execution_interval_seconds) -or
            [int]$http.configuration.evidence_execution_interval_seconds -ne 60 -or
            -not (Test-NonNegativeInteger $http.configuration.accuracy_provenance_interval_seconds) -or
            [int]$http.configuration.accuracy_provenance_interval_seconds -ne 300 -or
            -not (Test-NonNegativeInteger $http.configuration.max_concurrent_get) -or
            [int]$http.configuration.max_concurrent_get -ne 3 -or
            [string]$http.configuration.scheduling_policy -ne 'work_conserving' -or
            -not (Test-NonNegativeInteger $http.configuration.timeout_seconds) -or
            [int]$http.configuration.timeout_seconds -ne 3
        ) {
            Add-UniqueViolation $violations 'http_configuration'
        }
    }

    $httpStarted = [DateTimeOffset]::MinValue
    $httpCompleted = [DateTimeOffset]::MinValue
    if (
        -not (Test-HasProperties $http @('started_at', 'completed_at')) -or
        -not (Try-ParseTimestamp $http.started_at ([ref]$httpStarted)) -or
        -not (Try-ParseTimestamp $http.completed_at ([ref]$httpCompleted)) -or
        $httpCompleted -lt $httpStarted -or
        ($httpCompleted - $httpStarted).TotalSeconds -lt 300
    ) {
        Add-UniqueViolation $violations 'http_timestamps'
    }

    $httpRows = @(
        if (Test-HasProperties $http @('requests')) {
            $http.requests
        }
    )
    $httpRowFields = @(
        'phase', 'tier', 'iteration', 'scheduled_offset_seconds', 'endpoint',
        'path', 'started_at', 'elapsed_ms', 'status_code', 'accepted',
        'timeout', 'readyz_fail_closed', 'error'
    )
    if ($httpRows.Count -eq 0) {
        Add-UniqueViolation $violations 'http_raw_requests'
    }
    $rawHttpMalformed = $false
    $httpRowTimes = [Collections.Generic.List[DateTimeOffset]]::new()
    foreach ($row in $httpRows) {
        if (-not (Test-HasProperties $row $httpRowFields)) {
            $rawHttpMalformed = $true
            continue
        }
        $rowTimestamp = [DateTimeOffset]::MinValue
        $expectedEndpoint = $expectedByName[[string]$row.endpoint]
        if (
            $null -eq $expectedEndpoint -or
            [string]$row.tier -ne [string]$expectedEndpoint.tier -or
            [string]$row.path -ne [string]$expectedEndpoint.path -or
            [string]$row.phase -notin @('cold_full', 'steady_state') -or
            -not (Test-NonNegativeInteger $row.iteration) -or
            [int]$row.iteration -lt 1 -or
            -not (Test-FiniteNumber $row.scheduled_offset_seconds) -or
            -not (Test-FiniteNumber $row.elapsed_ms) -or
            [double]$row.elapsed_ms -lt 0 -or
            [double]$row.elapsed_ms -ge 3000 -or
            $row.accepted -isnot [bool] -or
            -not [bool]$row.accepted -or
            $row.timeout -isnot [bool] -or
            [bool]$row.timeout -or
            $row.readyz_fail_closed -isnot [bool] -or
            -not (Try-ParseTimestamp $row.started_at ([ref]$rowTimestamp)) -or
            $rowTimestamp -lt $httpStarted -or
            $rowTimestamp -gt $httpCompleted -or
            -not [string]::IsNullOrWhiteSpace([string]$row.error)
        ) {
            $rawHttpMalformed = $true
        }
        else {
            $httpRowTimes.Add($rowTimestamp)
        }
        if (
            [string]$row.endpoint -eq 'readyz' -and
            (
                -not (Test-NonNegativeInteger $row.status_code) -or
                [int]$row.status_code -ne 503 -or
                -not [bool]$row.readyz_fail_closed
            )
        ) {
            $rawHttpMalformed = $true
        }
        elseif (
            [string]$row.endpoint -ne 'readyz' -and
            (
                -not (Test-NonNegativeInteger $row.status_code) -or
                [int]$row.status_code -lt 200 -or
                [int]$row.status_code -ge 300 -or
                [bool]$row.readyz_fail_closed
            )
        ) {
            $rawHttpMalformed = $true
        }
        if ([string]$row.phase -eq 'cold_full') {
            if (
                -not (Test-NearlyEqual $row.scheduled_offset_seconds 0) -or
                [int]$row.iteration -ne 1
            ) {
                $rawHttpMalformed = $true
            }
        }
        elseif ($null -ne $expectedEndpoint) {
            $scheduled = [double]$row.scheduled_offset_seconds
            if (
                $scheduled -le 0 -or
                $scheduled -gt 300 -or
                [Math]::Abs(
                    $scheduled - (
                        [Math]::Round($scheduled / [double]$expectedEndpoint.interval) *
                        [double]$expectedEndpoint.interval
                    )
                ) -gt 0.001
            ) {
                $rawHttpMalformed = $true
            }
        }
    }
    if ($rawHttpMalformed) {
        Add-UniqueViolation $violations 'http_raw_requests'
    }

    foreach ($endpoint in $expectedEndpoints) {
        $coldRows = @(
            $httpRows | Where-Object {
                (Test-HasProperties $_ @('endpoint', 'phase')) -and
                [string]$_.endpoint -eq [string]$endpoint.endpoint -and
                [string]$_.phase -eq 'cold_full'
            }
        )
        $steadyRows = @(
            $httpRows | Where-Object {
                (Test-HasProperties $_ @('endpoint', 'phase', 'scheduled_offset_seconds')) -and
                [string]$_.endpoint -eq [string]$endpoint.endpoint -and
                [string]$_.phase -eq 'steady_state'
            }
        )
        $expectedSteadyOffsets = @(
            for (
                $offset = [int]$endpoint.interval;
                $offset -le 300;
                $offset += [int]$endpoint.interval
            ) {
                $offset
            }
        )
        if (
            $coldRows.Count -ne 1 -or
            $steadyRows.Count -ne $expectedSteadyOffsets.Count
        ) {
            Add-UniqueViolation $violations 'http_endpoint_inventory'
        }
        foreach ($expectedOffset in $expectedSteadyOffsets) {
            if (
                @(
                    $steadyRows | Where-Object {
                        Test-NearlyEqual $_.scheduled_offset_seconds $expectedOffset
                    }
                ).Count -ne 1
            ) {
                Add-UniqueViolation $violations 'http_endpoint_inventory'
            }
        }
        $duplicates = @(
            $httpRows |
                Where-Object {
                    (Test-HasProperties $_ @('endpoint')) -and
                    [string]$_.endpoint -eq [string]$endpoint.endpoint
                } |
                Group-Object phase, scheduled_offset_seconds |
                Where-Object Count -ne 1
        )
        if ($duplicates.Count -ne 0) {
            Add-UniqueViolation $violations 'http_endpoint_inventory'
        }
    }

    if (Test-HasProperties $http @('totals')) {
        $acceptedCount = @($httpRows | Where-Object {
            (Test-HasProperties $_ @('accepted')) -and [bool]$_.accepted
        }).Count
        $timeoutCount = @($httpRows | Where-Object {
            (Test-HasProperties $_ @('timeout')) -and [bool]$_.timeout
        }).Count
        $readyCount = @($httpRows | Where-Object {
            (Test-HasProperties $_ @('readyz_fail_closed')) -and
            [bool]$_.readyz_fail_closed
        }).Count
        if (
            -not (Test-HasProperties $http.totals @(
                'requests', 'accepted', 'errors', 'timeouts',
                'readyz_expected_fail_closed'
            )) -or
            -not (Test-NonNegativeInteger $http.totals.requests) -or
            -not (Test-NonNegativeInteger $http.totals.accepted) -or
            -not (Test-NonNegativeInteger $http.totals.errors) -or
            -not (Test-NonNegativeInteger $http.totals.timeouts) -or
            -not (Test-NonNegativeInteger $http.totals.readyz_expected_fail_closed) -or
            [int]$http.totals.requests -ne $httpRows.Count -or
            [int]$http.totals.accepted -ne $acceptedCount -or
            [int]$http.totals.errors -ne ($httpRows.Count - $acceptedCount) -or
            [int]$http.totals.timeouts -ne $timeoutCount -or
            [int]$http.totals.readyz_expected_fail_closed -ne $readyCount -or
            $acceptedCount -ne $httpRows.Count -or
            $timeoutCount -ne 0
        ) {
            Add-UniqueViolation $violations 'http_totals'
        }
    }
    else {
        Add-UniqueViolation $violations 'http_totals'
    }

    $endpointSummaries = @(
        if (Test-HasProperties $http @('endpoint_summary')) {
            $http.endpoint_summary
        }
    )
    if ($endpointSummaries.Count -ne $expectedEndpoints.Count) {
        Add-UniqueViolation $violations 'http_endpoint_summary'
    }
    foreach ($endpoint in $expectedEndpoints) {
        $rawForEndpoint = @(
            $httpRows | Where-Object {
                (Test-HasProperties $_ @('endpoint')) -and
                [string]$_.endpoint -eq [string]$endpoint.endpoint
            }
        )
        $summaryForEndpoint = @(
            $endpointSummaries | Where-Object {
                (Test-HasProperties $_ @('endpoint')) -and
                [string]$_.endpoint -eq [string]$endpoint.endpoint
            }
        )
        if ($summaryForEndpoint.Count -ne 1 -or $rawForEndpoint.Count -eq 0) {
            Add-UniqueViolation $violations 'http_endpoint_summary'
            continue
        }
        if (
            @(
                $rawForEndpoint | Where-Object {
                    -not (Test-HasProperties $_ $httpRowFields)
                }
            ).Count -ne 0
        ) {
            Add-UniqueViolation $violations 'http_endpoint_summary'
            continue
        }
        $endpointSummary = $summaryForEndpoint[0]
        $summaryFields = @(
            'endpoint', 'tier', 'count', 'accepted', 'errors', 'timeouts',
            'readyz_fail_closed', 'latency_ms', 'statuses'
        )
        if (
            -not (Test-HasProperties $endpointSummary $summaryFields) -or
            -not (Test-HasProperties $endpointSummary.latency_ms @(
                'minimum', 'average', 'p95', 'maximum'
            )) -or
            -not (Test-NonNegativeInteger $endpointSummary.count) -or
            -not (Test-NonNegativeInteger $endpointSummary.accepted) -or
            -not (Test-NonNegativeInteger $endpointSummary.errors) -or
            -not (Test-NonNegativeInteger $endpointSummary.timeouts) -or
            -not (Test-NonNegativeInteger $endpointSummary.readyz_fail_closed)
        ) {
            Add-UniqueViolation $violations 'http_endpoint_summary'
            continue
        }
        $latencies = @(
            $rawForEndpoint |
                ForEach-Object { [double]$_.elapsed_ms } |
                Sort-Object
        )
        $p95Index = [Math]::Max(
            0,
            [Math]::Ceiling($latencies.Count * 0.95) - 1
        )
        $rawAccepted = @($rawForEndpoint | Where-Object accepted).Count
        $rawTimeouts = @($rawForEndpoint | Where-Object timeout).Count
        $rawReady = @($rawForEndpoint | Where-Object readyz_fail_closed).Count
        if (
            [string]$endpointSummary.tier -ne [string]$endpoint.tier -or
            [int]$endpointSummary.count -ne $rawForEndpoint.Count -or
            [int]$endpointSummary.accepted -ne $rawAccepted -or
            [int]$endpointSummary.errors -ne ($rawForEndpoint.Count - $rawAccepted) -or
            [int]$endpointSummary.timeouts -ne $rawTimeouts -or
            [int]$endpointSummary.readyz_fail_closed -ne $rawReady -or
            -not (Test-NearlyEqual $endpointSummary.latency_ms.minimum (
                [Math]::Round($latencies[0], 3)
            )) -or
            -not (Test-NearlyEqual $endpointSummary.latency_ms.average (
                [Math]::Round(($latencies | Measure-Object -Average).Average, 3)
            )) -or
            -not (Test-NearlyEqual $endpointSummary.latency_ms.p95 (
                [Math]::Round($latencies[$p95Index], 3)
            )) -or
            -not (Test-NearlyEqual $endpointSummary.latency_ms.maximum (
                [Math]::Round($latencies[-1], 3)
            ))
        ) {
            Add-UniqueViolation $violations 'http_endpoint_summary'
        }
        $rawStatuses = @(
            $rawForEndpoint |
                Group-Object status_code |
                Sort-Object Name
        )
        $summaryStatuses = @($endpointSummary.statuses)
        if ($summaryStatuses.Count -ne $rawStatuses.Count) {
            Add-UniqueViolation $violations 'http_endpoint_summary'
        }
        else {
            for ($statusIndex = 0; $statusIndex -lt $rawStatuses.Count; $statusIndex += 1) {
                if (
                    -not (Test-HasProperties $summaryStatuses[$statusIndex] @(
                        'status_code', 'count'
                    )) -or
                    -not (Test-NonNegativeInteger $summaryStatuses[$statusIndex].status_code) -or
                    -not (Test-NonNegativeInteger $summaryStatuses[$statusIndex].count) -or
                    [int]$summaryStatuses[$statusIndex].status_code -ne
                        [int]$rawStatuses[$statusIndex].Name -or
                    [int]$summaryStatuses[$statusIndex].count -ne
                        [int]$rawStatuses[$statusIndex].Count
                ) {
                    Add-UniqueViolation $violations 'http_endpoint_summary'
                }
            }
        }
    }

    $apiRows = @(
        $httpRows | Where-Object {
            (Test-HasProperties $_ @('path')) -and
            [string]$_.path -like 'api/v1/*'
        }
    )
    $expectedApiRequests = $apiRows.Count + 1
    $expectedApi5xx = @(
        $apiRows | Where-Object {
            (Test-HasProperties $_ @('status_code')) -and
            (Test-NonNegativeInteger $_.status_code) -and
            [int]$_.status_code -ge 500
        }
    ).Count
    $telemetryFields = @(
        'foreign_started', 'foreign_completed', 'foreign_5xx',
        'untagged_started', 'untagged_completed', 'run_started',
        'run_completed', 'run_aborted', 'expected_run_requests', 'run_5xx',
        'expected_run_5xx', 'total_started', 'total_completed',
        'total_aborted', 'total_5xx', 'counter_mismatch', 'before', 'after'
    )
    $telemetryCountersValid = $true
    if (Test-HasProperties $http @('telemetry')) {
        foreach ($field in @(
            'foreign_started', 'foreign_completed', 'foreign_5xx',
            'untagged_started', 'untagged_completed', 'run_started',
            'run_completed', 'run_aborted', 'expected_run_requests',
            'run_5xx', 'expected_run_5xx', 'total_started',
            'total_completed', 'total_aborted', 'total_5xx'
        )) {
            if (
                -not (Test-HasProperties $http.telemetry @($field)) -or
                -not (Test-NonNegativeInteger $http.telemetry.$field)
            ) {
                $telemetryCountersValid = $false
            }
        }
    }
    if (
        -not (Test-HasProperties $http @('telemetry')) -or
        -not (Test-HasProperties $http.telemetry $telemetryFields) -or
        -not $telemetryCountersValid -or
        -not (Test-HasProperties $http.telemetry.before @('active_requests')) -or
        -not (Test-HasProperties $http.telemetry.after @('active_requests')) -or
        -not (Test-NonNegativeInteger $http.telemetry.before.active_requests) -or
        -not (Test-NonNegativeInteger $http.telemetry.after.active_requests) -or
        $http.telemetry.counter_mismatch -isnot [bool] -or
        [int]$http.telemetry.foreign_started -ne 0 -or
        [int]$http.telemetry.foreign_completed -ne 0 -or
        [int]$http.telemetry.foreign_5xx -ne 0 -or
        [int]$http.telemetry.untagged_started -ne 0 -or
        [int]$http.telemetry.untagged_completed -ne 0 -or
        [int]$http.telemetry.run_started -ne $expectedApiRequests -or
        [int]$http.telemetry.run_completed -ne $expectedApiRequests -or
        [int]$http.telemetry.expected_run_requests -ne $expectedApiRequests -or
        [int]$http.telemetry.run_aborted -ne 0 -or
        [int]$http.telemetry.run_5xx -ne $expectedApi5xx -or
        [int]$http.telemetry.expected_run_5xx -ne $expectedApi5xx -or
        [int]$http.telemetry.total_started -ne $expectedApiRequests -or
        [int]$http.telemetry.total_completed -ne $expectedApiRequests -or
        [int]$http.telemetry.total_aborted -ne 0 -or
        [int]$http.telemetry.total_5xx -ne $expectedApi5xx -or
        [int]$http.telemetry.before.active_requests -ne 1 -or
        [int]$http.telemetry.after.active_requests -ne 1 -or
        [bool]$http.telemetry.counter_mismatch
    ) {
        Add-UniqueViolation $violations 'http_telemetry_reconciliation'
    }

    $runtimeTopFields = @(
        'schema_version', 'gate', 'load_gate_run_id', 'started_at',
        'observation_started_at', 'completed_at', 'duration_seconds',
        'database_interval_seconds', 'resource_interval_seconds',
        'drain_seconds', 'expected_schema_head', 'maximum_api_sessions',
        'expected_api_image_id', 'expected_revision',
        'expected_release_digest', 'baseline', 'summary', 'violations',
        'drain', 'sample_errors', 'database_samples', 'resource_samples',
        'topology_samples'
    )
    if (-not (Test-HasProperties $runtime $runtimeTopFields)) {
        Add-UniqueViolation $violations 'runtime_contract'
    }
    else {
        if (
            -not (Test-NonNegativeInteger $runtime.schema_version) -or
            [int]$runtime.schema_version -ne 2 -or
            [string]$runtime.gate -ne 'stratus_dashboard_runtime_observation'
        ) {
            Add-UniqueViolation $violations 'runtime_contract'
        }
        if (
            -not (Test-NonNegativeInteger $runtime.duration_seconds) -or
            [int]$runtime.duration_seconds -ne 390 -or
            -not (Test-NonNegativeInteger $runtime.database_interval_seconds) -or
            [int]$runtime.database_interval_seconds -ne 1 -or
            -not (Test-NonNegativeInteger $runtime.resource_interval_seconds) -or
            [int]$runtime.resource_interval_seconds -ne 5 -or
            -not (Test-NonNegativeInteger $runtime.drain_seconds) -or
            [int]$runtime.drain_seconds -ne 5 -or
            -not (Test-NonNegativeInteger $runtime.maximum_api_sessions) -or
            [int]$runtime.maximum_api_sessions -ne 4 -or
            [string]$runtime.expected_schema_head -ne '20260730_0047'
        ) {
            Add-UniqueViolation $violations 'runtime_configuration'
        }
    }
    if (
        -not (Test-HasProperties $http @('load_gate_run_id')) -or
        -not (Test-HasProperties $runtime @('load_gate_run_id')) -or
        [string]$http.load_gate_run_id -ne $runId -or
        [string]$runtime.load_gate_run_id -ne $runId
    ) {
        Add-UniqueViolation $violations 'run_id_mismatch'
    }
    if (
        -not (Test-HasProperties $runtime @(
            'expected_api_image_id', 'expected_revision',
            'expected_release_digest', 'baseline'
        )) -or
        [string]$runtime.expected_api_image_id -ne $ExpectedApiImageId -or
        [string]$runtime.expected_revision -ne $ExpectedRevision -or
        [string]$runtime.expected_release_digest -ne $ExpectedReleaseDigest -or
        -not (Test-HasProperties $runtime.baseline @(
            'api_container_id', 'api_image_id', 'api_restart_count',
            'postgres_container_id', 'postgres_image_id',
            'postgres_restart_count', 'compose_project'
        )) -or
        [string]$runtime.baseline.api_image_id -ne $ExpectedApiImageId -or
        [string]::IsNullOrWhiteSpace([string]$runtime.baseline.api_container_id) -or
        [string]::IsNullOrWhiteSpace([string]$runtime.baseline.postgres_container_id) -or
        [string]::IsNullOrWhiteSpace([string]$runtime.baseline.postgres_image_id) -or
        [string]::IsNullOrWhiteSpace([string]$runtime.baseline.compose_project) -or
        -not (Test-NonNegativeInteger $runtime.baseline.api_restart_count) -or
        -not (Test-NonNegativeInteger $runtime.baseline.postgres_restart_count)
    ) {
        Add-UniqueViolation $violations 'candidate_identity'
    }

    $databaseSamples = @(
        if (Test-HasProperties $runtime @('database_samples')) {
            $runtime.database_samples
        }
    )
    $resourceSamples = @(
        if (Test-HasProperties $runtime @('resource_samples')) {
            $runtime.resource_samples
        }
    )
    $topologySamples = @(
        if (Test-HasProperties $runtime @('topology_samples')) {
            $runtime.topology_samples
        }
    )
    if (
        $databaseSamples.Count -lt 351 -or
        $resourceSamples.Count -lt 70 -or
        $topologySamples.Count -lt 70
    ) {
        Add-UniqueViolation $violations 'runtime_sample_coverage'
    }

    $databaseFields = @(
        'at', 'api_sessions', 'api_active', 'api_idle_in_tx',
        'runtime_writer_sessions', 'global_idle_in_tx',
        'max_api_idle_tx_seconds', 'lock_waiters',
        'oldest_api_query_seconds', 'schema_head', 'controls_ok',
        'pending_commands', 'live_orders', 'live_positions', 'live_intents',
        'live_fill_legs', 'live_enrollments'
    )
    $databaseTimes = [Collections.Generic.List[DateTimeOffset]]::new()
    $databaseMalformed = $false
    foreach ($sample in $databaseSamples) {
        if (-not (Test-HasProperties $sample $databaseFields)) {
            $databaseMalformed = $true
            continue
        }
        $sampleAt = [DateTimeOffset]::MinValue
        $countFields = @(
            'api_sessions', 'api_active', 'api_idle_in_tx',
            'runtime_writer_sessions', 'global_idle_in_tx',
            'lock_waiters', 'pending_commands', 'live_orders', 'live_positions',
            'live_intents', 'live_fill_legs', 'live_enrollments'
        )
        $numericValid = $true
        foreach ($field in $countFields) {
            if (-not (Test-NonNegativeInteger $sample.$field)) {
                $numericValid = $false
            }
        }
        foreach ($field in @('max_api_idle_tx_seconds', 'oldest_api_query_seconds')) {
            if (-not (Test-FiniteNumber $sample.$field) -or [double]$sample.$field -lt 0) {
                $numericValid = $false
            }
        }
        if (
            -not $numericValid -or
            -not (Try-ParseTimestamp $sample.at ([ref]$sampleAt)) -or
            $sample.controls_ok -isnot [bool] -or
            -not [bool]$sample.controls_ok -or
            [string]$sample.schema_head -ne '20260730_0047' -or
            [int]$sample.api_sessions -gt 4 -or
            [int]$sample.api_active -gt [int]$sample.api_sessions -or
            [int]$sample.api_idle_in_tx -ne 0 -or
            [int]$sample.runtime_writer_sessions -ne 0 -or
            [int]$sample.global_idle_in_tx -ne 0 -or
            [double]$sample.max_api_idle_tx_seconds -ne 0 -or
            [int]$sample.lock_waiters -ne 0 -or
            [int]$sample.pending_commands -ne 0 -or
            [int]$sample.live_orders -ne 0 -or
            [int]$sample.live_positions -ne 0 -or
            [int]$sample.live_intents -ne 0 -or
            [int]$sample.live_fill_legs -ne 0 -or
            [int]$sample.live_enrollments -ne 0
        ) {
            $databaseMalformed = $true
        }
        else {
            $databaseTimes.Add($sampleAt)
        }
    }
    if ($databaseMalformed -or $databaseTimes.Count -ne $databaseSamples.Count) {
        Add-UniqueViolation $violations 'runtime_database_evidence'
    }

    $resourceFields = @(
        'at', 'api_cpu_percent', 'api_memory_percent', 'api_memory_limit_bytes',
        'api_operator_key_entry_count',
        'postgres_cpu_percent', 'postgres_memory_percent'
    )
    $resourceTimes = [Collections.Generic.List[DateTimeOffset]]::new()
    $resourceMalformed = $false
    foreach ($sample in $resourceSamples) {
        if (-not (Test-HasProperties $sample $resourceFields)) {
            $resourceMalformed = $true
            continue
        }
        $sampleAt = [DateTimeOffset]::MinValue
        $numericValid = $true
        foreach ($field in @(
            'api_cpu_percent', 'api_memory_percent',
            'postgres_cpu_percent', 'postgres_memory_percent'
        )) {
            if (-not (Test-FiniteNumber $sample.$field) -or [double]$sample.$field -lt 0) {
                $numericValid = $false
            }
        }
        if (
            -not $numericValid -or
            -not (Test-NonNegativeInteger $sample.api_memory_limit_bytes) -or
            [int64]$sample.api_memory_limit_bytes -ne 1073741824L -or
            -not (Test-NonNegativeInteger $sample.api_operator_key_entry_count) -or
            [int]$sample.api_operator_key_entry_count -ne 1 -or
            -not (Try-ParseTimestamp $sample.at ([ref]$sampleAt)) -or
            [double]$sample.api_memory_percent -ge 90 -or
            [double]$sample.postgres_cpu_percent -ge 200
        ) {
            $resourceMalformed = $true
        }
        else {
            $resourceTimes.Add($sampleAt)
        }
    }
    if ($resourceMalformed -or $resourceTimes.Count -ne $resourceSamples.Count) {
        Add-UniqueViolation $violations 'runtime_resource_evidence'
    }

    $topologyTimes = [Collections.Generic.List[DateTimeOffset]]::new()
    $topologyMalformed = $false
    $expectedTopologyServices = @('api', 'postgres')
    $baselineProject = if (
        (Test-HasProperties $runtime @('baseline')) -and
        (Test-HasProperties $runtime.baseline @(
            'api_container_id', 'postgres_container_id', 'compose_project'
        ))
    ) {
        [string]$runtime.baseline.compose_project
    }
    else {
        ''
    }
    $expectedContainerIds = if ([string]::IsNullOrWhiteSpace($baselineProject)) {
        @()
    }
    else {
        @(
            [string]$runtime.baseline.api_container_id,
            [string]$runtime.baseline.postgres_container_id
        )
    }
    $expectedServiceById = @{}
    if (
        $expectedContainerIds.Count -eq 2 -and
        -not [string]::IsNullOrWhiteSpace($expectedContainerIds[0]) -and
        -not [string]::IsNullOrWhiteSpace($expectedContainerIds[1]) -and
        $expectedContainerIds[0] -cne $expectedContainerIds[1]
    ) {
        $expectedServiceById[$expectedContainerIds[0]] = 'api'
        $expectedServiceById[$expectedContainerIds[1]] = 'postgres'
    }
    else {
        $topologyMalformed = $true
    }
    foreach ($sample in $topologySamples) {
        $topologyAt = [DateTimeOffset]::MinValue
        $requiredFields = @(
            'at', 'project', 'allowed_services', 'protected_networks',
            'running_services', 'all_running_container_count',
            'all_running_containers', 'running_containers',
            'unexpected_containers', 'missing_container_ids', 'safe'
        )
        if (-not (Test-HasProperties $sample $requiredFields)) {
            $topologyMalformed = $true
            continue
        }
        $allRows = @($sample.all_running_containers)
        $runningRows = @($sample.running_containers)
        $protectedNetworks = @(
            $sample.protected_networks | ForEach-Object { [string]$_ }
        )
        $allRowKeys = [Collections.Generic.List[string]]::new()
        $runningRowKeys = [Collections.Generic.List[string]]::new()
        $recomputedRelevantKeys = [Collections.Generic.List[string]]::new()
        $recomputedRelevantRows = [Collections.Generic.List[object]]::new()
        $allIds = @(
            $allRows | ForEach-Object {
                if (Test-HasProperties $_ @(
                    'container_id', 'project', 'service', 'oneoff',
                    'container_name', 'networks'
                )) {
                    $rowNetworks = @($_.networks | ForEach-Object { [string]$_ })
                    if (
                        @($rowNetworks | Where-Object {
                            [string]::IsNullOrWhiteSpace($_)
                        }).Count -ne 0 -or
                        @($rowNetworks | Sort-Object -Unique).Count -ne
                            $rowNetworks.Count
                    ) {
                        $topologyMalformed = $true
                    }
                    $rowKey = Get-TopologyRowKey -Row $_
                    $allRowKeys.Add($rowKey)
                    if (
                        $expectedServiceById.ContainsKey([string]$_.container_id) -or
                        [string]$_.project -ceq $baselineProject -or
                        @($rowNetworks | Where-Object {
                            $_ -cin $protectedNetworks
                        }).Count -ne 0
                    ) {
                        $recomputedRelevantRows.Add($_)
                        $recomputedRelevantKeys.Add($rowKey)
                    }
                    [string]$_.container_id
                }
                else {
                    $topologyMalformed = $true
                }
            }
        )
        $runningIds = @(
            $runningRows | ForEach-Object {
                if (Test-HasProperties $_ @(
                    'container_id', 'project', 'service', 'oneoff',
                    'container_name', 'networks'
                )) {
                    $rowNetworks = @($_.networks | ForEach-Object { [string]$_ })
                    if (
                        @($rowNetworks | Where-Object {
                            [string]::IsNullOrWhiteSpace($_)
                        }).Count -ne 0 -or
                        @($rowNetworks | Sort-Object -Unique).Count -ne
                            $rowNetworks.Count
                    ) {
                        $topologyMalformed = $true
                    }
                    $runningRowKeys.Add((Get-TopologyRowKey -Row $_))
                    [string]$_.container_id
                }
                else {
                    $topologyMalformed = $true
                }
            }
        )
        $rowsSafe = $recomputedRelevantRows.Count -eq 2
        $recomputedNetworkUnion = @(
            $recomputedRelevantRows |
                ForEach-Object { @($_.networks) } |
                ForEach-Object { [string]$_ } |
                Sort-Object -Unique
        )
        foreach ($row in $recomputedRelevantRows) {
            if (
                -not (Test-HasProperties $row @(
                    'container_id', 'project', 'service', 'oneoff',
                    'container_name', 'networks'
                )) -or
                [string]$row.project -cne $baselineProject -or
                -not $expectedServiceById.ContainsKey([string]$row.container_id) -or
                [string]$row.service -cne
                    [string]$expectedServiceById[[string]$row.container_id] -or
                [string]$row.oneoff -cne 'False' -or
                [string]::IsNullOrWhiteSpace([string]$row.container_name)
            ) {
                $rowsSafe = $false
            }
        }
        if (
            -not (Try-ParseTimestamp $sample.at ([ref]$topologyAt)) -or
            [string]$sample.project -cne $baselineProject -or
            -not (Test-ExactStringSet $sample.allowed_services $expectedTopologyServices) -or
            -not (Test-ExactStringSet $sample.running_services $expectedTopologyServices) -or
            $protectedNetworks.Count -eq 0 -or
            @($protectedNetworks | Where-Object {
                [string]::IsNullOrWhiteSpace($_)
            }).Count -ne 0 -or
            @($protectedNetworks | Sort-Object -Unique).Count -ne
                $protectedNetworks.Count -or
            -not (Test-ExactStringSet $recomputedNetworkUnion $protectedNetworks) -or
            -not (Test-ExactStringSet $runningIds $expectedContainerIds) -or
            -not (Test-ExactStringSet $runningRowKeys $recomputedRelevantKeys) -or
            @($runningIds | Sort-Object -Unique).Count -ne $runningIds.Count -or
            @($allIds | Sort-Object -Unique).Count -ne $allIds.Count -or
            @($allRowKeys | Sort-Object -Unique).Count -ne $allRowKeys.Count -or
            -not (Test-NonNegativeInteger $sample.all_running_container_count) -or
            [int]$sample.all_running_container_count -ne $allRows.Count -or
            @($runningIds | Where-Object { $_ -notin $allIds }).Count -ne 0 -or
            @($sample.unexpected_containers).Count -ne 0 -or
            @($sample.missing_container_ids).Count -ne 0 -or
            $sample.safe -isnot [bool] -or
            -not [bool]$sample.safe -or
            -not $rowsSafe
        ) {
            $topologyMalformed = $true
        }
        else {
            $topologyTimes.Add($topologyAt)
        }
    }
    if ($topologyMalformed -or $topologyTimes.Count -ne $topologySamples.Count) {
        Add-UniqueViolation $violations 'runtime_topology_evidence'
    }

    $postgresCpu = @(
        $resourceSamples | ForEach-Object {
            if (Test-HasProperties $_ @('postgres_cpu_percent')) {
                [double]$_.postgres_cpu_percent
            }
        }
    )
    $twoConsecutiveAt150 = $false
    for ($index = 1; $index -lt $postgresCpu.Count; $index += 1) {
        if ($postgresCpu[$index - 1] -ge 150 -and $postgresCpu[$index] -ge 150) {
            $twoConsecutiveAt150 = $true
            break
        }
    }
    $rolling30Max = 0.0
    if ($postgresCpu.Count -ge 7) {
        for ($index = 6; $index -lt $postgresCpu.Count; $index += 1) {
            $window = @($postgresCpu[($index - 6)..$index])
            $average = [double](($window | Measure-Object -Average).Average)
            $rolling30Max = [Math]::Max($rolling30Max, $average)
        }
    }
    if ($twoConsecutiveAt150 -or $rolling30Max -ge 140) {
        Add-UniqueViolation $violations 'runtime_resource_threshold'
    }

    $runtimeStarted = [DateTimeOffset]::MinValue
    $runtimeProcessStarted = [DateTimeOffset]::MinValue
    $runtimeCompleted = [DateTimeOffset]::MinValue
    $drainAt = [DateTimeOffset]::MinValue
    $firstDatabaseSampleAt = [DateTimeOffset]::MinValue
    $firstResourceSampleAt = [DateTimeOffset]::MinValue
    $firstTopologySampleAt = [DateTimeOffset]::MinValue
    $lastDatabaseSampleAt = [DateTimeOffset]::MinValue
    $lastResourceSampleAt = [DateTimeOffset]::MinValue
    $lastTopologySampleAt = [DateTimeOffset]::MinValue
    $runtimeTimestampsValid = (
        (Test-HasProperties $runtime @(
            'started_at', 'observation_started_at', 'completed_at', 'drain'
        )) -and
        (Test-HasProperties $runtime.drain @('at')) -and
        (Try-ParseTimestamp $runtime.started_at ([ref]$runtimeProcessStarted)) -and
        (Try-ParseTimestamp $runtime.observation_started_at ([ref]$runtimeStarted)) -and
        (Try-ParseTimestamp $runtime.completed_at ([ref]$runtimeCompleted)) -and
        (Try-ParseTimestamp $runtime.drain.at ([ref]$drainAt)) -and
        $databaseTimes.Count -eq $databaseSamples.Count -and
        $resourceTimes.Count -eq $resourceSamples.Count -and
        $topologyTimes.Count -eq $topologySamples.Count -and
        $databaseTimes.Count -gt 0 -and
        $resourceTimes.Count -gt 0 -and
        $topologyTimes.Count -gt 0
    )
    if ($runtimeTimestampsValid) {
        $firstDatabaseSampleAt = $databaseTimes[0]
        $firstResourceSampleAt = $resourceTimes[0]
        $firstTopologySampleAt = $topologyTimes[0]
        $lastDatabaseSampleAt = $databaseTimes[-1]
        $lastResourceSampleAt = $resourceTimes[-1]
        $lastTopologySampleAt = $topologyTimes[-1]
        for ($index = 1; $index -lt $databaseTimes.Count; $index += 1) {
            if ($databaseTimes[$index] -le $databaseTimes[$index - 1]) {
                $runtimeTimestampsValid = $false
            }
        }
        for ($index = 1; $index -lt $resourceTimes.Count; $index += 1) {
            if ($resourceTimes[$index] -le $resourceTimes[$index - 1]) {
                $runtimeTimestampsValid = $false
            }
        }
        for ($index = 1; $index -lt $topologyTimes.Count; $index += 1) {
            if ($topologyTimes[$index] -le $topologyTimes[$index - 1]) {
                $runtimeTimestampsValid = $false
            }
        }
        if (
            $runtimeProcessStarted -gt $runtimeStarted -or
            $runtimeStarted -gt $firstDatabaseSampleAt -or
            $runtimeStarted -gt $firstResourceSampleAt -or
            $runtimeStarted -gt $firstTopologySampleAt -or
            $firstDatabaseSampleAt -gt $httpStarted -or
            $firstResourceSampleAt -gt $httpStarted -or
            $firstTopologySampleAt -gt $httpStarted -or
            $lastDatabaseSampleAt -gt $drainAt -or
            $lastResourceSampleAt -gt $drainAt -or
            $lastTopologySampleAt -lt $drainAt -or
            ($lastDatabaseSampleAt - $firstDatabaseSampleAt).TotalSeconds -lt 350 -or
            ($lastResourceSampleAt - $firstResourceSampleAt).TotalSeconds -lt 345 -or
            ($lastTopologySampleAt - $firstTopologySampleAt).TotalSeconds -lt 390 -or
            $drainAt -lt $httpCompleted.AddSeconds(5) -or
            ($drainAt - $firstDatabaseSampleAt).TotalSeconds -lt 390 -or
            $runtimeCompleted -lt $drainAt
        ) {
            $runtimeTimestampsValid = $false
        }
    }
    if (-not $runtimeTimestampsValid) {
        Add-UniqueViolation $violations 'runtime_timestamps'
    }
    $actualObservationSeconds = if (
        $firstDatabaseSampleAt -ne [DateTimeOffset]::MinValue -and
        $drainAt -ne [DateTimeOffset]::MinValue
    ) {
        ($drainAt - $firstDatabaseSampleAt).TotalSeconds
    }
    else {
        0.0
    }

    $drainCountsValid = $true
    if (
        -not (Test-HasProperties $runtime @('drain')) -or
        -not (Test-HasProperties $runtime.drain $databaseFields)
    ) {
        $drainCountsValid = $false
    }
    else {
        foreach ($field in @(
            'api_sessions', 'api_active', 'api_idle_in_tx',
            'runtime_writer_sessions', 'global_idle_in_tx', 'lock_waiters',
            'pending_commands', 'live_orders', 'live_positions',
            'live_intents', 'live_fill_legs', 'live_enrollments'
        )) {
            if (-not (Test-NonNegativeInteger $runtime.drain.$field)) {
                $drainCountsValid = $false
            }
        }
    }
    $drainValid = (
        (Test-HasProperties $runtime @('drain')) -and
        (Test-HasProperties $runtime.drain $databaseFields) -and
        $drainCountsValid -and
        [string]$runtime.drain.schema_head -eq '20260730_0047' -and
        $runtime.drain.controls_ok -is [bool] -and
        [bool]$runtime.drain.controls_ok -and
        (Test-NonNegativeInteger $runtime.drain.api_sessions) -and
        [int]$runtime.drain.api_sessions -le 4 -and
        (Test-NonNegativeInteger $runtime.drain.api_active) -and
        [int]$runtime.drain.api_active -eq 0 -and
        (Test-FiniteNumber $runtime.drain.max_api_idle_tx_seconds) -and
        [double]$runtime.drain.max_api_idle_tx_seconds -eq 0 -and
        (Test-FiniteNumber $runtime.drain.oldest_api_query_seconds) -and
        [double]$runtime.drain.oldest_api_query_seconds -ge 0 -and
        [int]$runtime.drain.api_idle_in_tx -eq 0 -and
        [int]$runtime.drain.runtime_writer_sessions -eq 0 -and
        [int]$runtime.drain.global_idle_in_tx -eq 0 -and
        [int]$runtime.drain.lock_waiters -eq 0 -and
        [int]$runtime.drain.pending_commands -eq 0 -and
        [int]$runtime.drain.live_orders -eq 0 -and
        [int]$runtime.drain.live_positions -eq 0 -and
        [int]$runtime.drain.live_intents -eq 0 -and
        [int]$runtime.drain.live_fill_legs -eq 0 -and
        [int]$runtime.drain.live_enrollments -eq 0
    )
    if (-not $drainValid) {
        Add-UniqueViolation $violations 'runtime_drain'
    }

    $summaryFields = @(
        'database_sample_count', 'resource_sample_count', 'topology_sample_count',
        'minimum_database_samples', 'minimum_resource_samples',
        'sample_errors', 'max_api_sessions', 'max_api_active',
        'max_runtime_writer_sessions', 'max_api_idle_in_tx',
        'max_global_idle_in_tx', 'max_api_idle_tx_seconds',
        'max_lock_waiters', 'max_oldest_api_query_seconds',
        'max_api_cpu_percent', 'max_api_memory_percent',
        'max_postgres_cpu_percent', 'rolling_30s_postgres_cpu_max',
        'consecutive_postgres_cpu_150', 'identity_changed',
        'restart_changed', 'oom_observed', 'containers_healthy',
        'candidate_identity_held', 'application_writers_stopped',
        'exact_project_topology_held',
        'invariants_changed', 'drain_passed', 'runtime_passed'
    )
    $sampleErrors = @(
        if (Test-HasProperties $runtime @('sample_errors')) {
            $runtime.sample_errors
        }
        else {
            'missing'
        }
    )
    $runtimeDeclaredViolations = @(
        if (Test-HasProperties $runtime @('violations')) {
            $runtime.violations
        }
        else {
            'missing'
        }
    )
    $summaryBooleansValid = $true
    $summaryIntegersValid = $true
    if (Test-HasProperties $runtime @('summary')) {
        foreach ($field in @(
            'consecutive_postgres_cpu_150', 'identity_changed',
            'restart_changed', 'oom_observed', 'containers_healthy',
            'candidate_identity_held', 'application_writers_stopped',
            'exact_project_topology_held',
            'invariants_changed', 'drain_passed', 'runtime_passed'
        )) {
            if (
                -not (Test-HasProperties $runtime.summary @($field)) -or
                $runtime.summary.$field -isnot [bool]
            ) {
                $summaryBooleansValid = $false
            }
        }
        foreach ($field in @(
            'database_sample_count', 'resource_sample_count', 'topology_sample_count',
            'minimum_database_samples', 'minimum_resource_samples',
            'sample_errors'
        )) {
            if (
                -not (Test-HasProperties $runtime.summary @($field)) -or
                -not (Test-NonNegativeInteger $runtime.summary.$field)
            ) {
                $summaryIntegersValid = $false
            }
        }
    }
    else {
        $summaryBooleansValid = $false
        $summaryIntegersValid = $false
    }
    if (
        -not (Test-HasProperties $runtime @('summary')) -or
        -not (Test-HasProperties $runtime.summary $summaryFields) -or
        -not $summaryBooleansValid -or
        -not $summaryIntegersValid -or
        $databaseMalformed -or
        $resourceMalformed -or
        $sampleErrors.Count -ne 0 -or
        $runtimeDeclaredViolations.Count -ne 0 -or
        [int]$runtime.summary.database_sample_count -ne $databaseSamples.Count -or
        [int]$runtime.summary.resource_sample_count -ne $resourceSamples.Count -or
        [int]$runtime.summary.topology_sample_count -ne $topologySamples.Count -or
        [int]$runtime.summary.minimum_database_samples -ne 351 -or
        [int]$runtime.summary.minimum_resource_samples -ne 70 -or
        [int]$runtime.summary.sample_errors -ne $sampleErrors.Count -or
        -not (Test-NearlyEqual $runtime.summary.max_api_sessions (
            Get-RawMaximum $databaseSamples 'api_sessions'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_api_active (
            Get-RawMaximum $databaseSamples 'api_active'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_runtime_writer_sessions (
            Get-RawMaximum $databaseSamples 'runtime_writer_sessions'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_api_idle_in_tx (
            Get-RawMaximum $databaseSamples 'api_idle_in_tx'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_global_idle_in_tx (
            Get-RawMaximum $databaseSamples 'global_idle_in_tx'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_api_idle_tx_seconds (
            Get-RawMaximum $databaseSamples 'max_api_idle_tx_seconds'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_lock_waiters (
            Get-RawMaximum $databaseSamples 'lock_waiters'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_oldest_api_query_seconds (
            Get-RawMaximum $databaseSamples 'oldest_api_query_seconds'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_api_cpu_percent (
            Get-RawMaximum $resourceSamples 'api_cpu_percent'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_api_memory_percent (
            Get-RawMaximum $resourceSamples 'api_memory_percent'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.max_postgres_cpu_percent (
            Get-RawMaximum $resourceSamples 'postgres_cpu_percent'
        )) -or
        -not (Test-NearlyEqual $runtime.summary.rolling_30s_postgres_cpu_max $rolling30Max) -or
        [bool]$runtime.summary.consecutive_postgres_cpu_150 -ne $twoConsecutiveAt150 -or
        [bool]$runtime.summary.identity_changed -or
        [bool]$runtime.summary.restart_changed -or
        [bool]$runtime.summary.oom_observed -or
        -not [bool]$runtime.summary.containers_healthy -or
        -not [bool]$runtime.summary.candidate_identity_held -or
        -not [bool]$runtime.summary.application_writers_stopped -or
        -not [bool]$runtime.summary.exact_project_topology_held -or
        $topologyMalformed -or
        [bool]$runtime.summary.invariants_changed -or
        [bool]$runtime.summary.drain_passed -ne $drainValid -or
        -not [bool]$runtime.summary.runtime_passed
    ) {
        Add-UniqueViolation $violations 'runtime_summary_reconciliation'
    }

    $passed = $violations.Count -eq 0
    $result = [pscustomobject]@{
        schema_version = 1
        gate = 'stratus_candidate_production_load'
        production_gate_passed = $passed
        exit_code = if ($passed) { 0 } else { 1 }
        run_id = $runId
        checked_at = [DateTimeOffset]::UtcNow.ToString('O')
        candidate = [pscustomobject]@{
            api_image_id = $ExpectedApiImageId
            revision = $ExpectedRevision
            release_digest = $ExpectedReleaseDigest
        }
        overlap = [pscustomobject]@{
            runtime_started_at = $runtimeStarted.ToString('O')
            first_database_sample_at = $firstDatabaseSampleAt.ToString('O')
            http_started_at = $httpStarted.ToString('O')
            http_completed_at = $httpCompleted.ToString('O')
            drain_at = $drainAt.ToString('O')
            runtime_completed_at = $runtimeCompleted.ToString('O')
            actual_observation_seconds = [Math]::Round($actualObservationSeconds, 3)
            runtime_covers_http_and_drain = -not ($violations -contains 'runtime_overlap')
        }
        evidence = [pscustomobject]@{
            http_artifact = $absoluteHttp
            http_sha256 = $httpSha256
            runtime_artifact = $absoluteRuntime
            runtime_sha256 = $runtimeSha256
            topology_sample_count = $topologySamples.Count
            topology_sha256 = Get-BytesSha256 -Bytes (
                [Text.UTF8Encoding]::new($false).GetBytes(
                    ($topologySamples | ConvertTo-Json -Depth 12 -Compress)
                )
            )
        }
        violations = @($violations)
    }
    Write-GateArtifact -Path $absoluteOutput -Artifact $result
    $result | ConvertTo-Json -Depth 5 -Compress
    $exitCode = if ($passed) { 0 } else { 1 }
}
catch {
    $fatal = [pscustomobject]@{
        schema_version = 1
        gate = 'stratus_candidate_production_load'
        production_gate_passed = $false
        exit_code = 2
        run_id = $runId
        checked_at = [DateTimeOffset]::UtcNow.ToString('O')
        fatal_error = $_.Exception.GetBaseException().Message
    }
    Write-GateArtifact -Path $absoluteOutput -Artifact $fatal
    $fatal | ConvertTo-Json -Compress
    $exitCode = 2
}

exit $exitCode
