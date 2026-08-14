[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\Assert-StratusLoadGateArtifacts.ps1')
)
$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw 'Composite load-gate assertion has PowerShell syntax errors.'
}
$source = Get-Content -LiteralPath $scriptPath -Raw
foreach ($requiredSourcePattern in @(
    '[IO.File]::ReadAllBytes($absoluteHttp)',
    '[IO.File]::ReadAllBytes($absoluteRuntime)',
    'Get-BytesSha256 -Bytes $httpBytes',
    'Get-BytesSha256 -Bytes $runtimeBytes'
)) {
    if (-not $source.Contains($requiredSourcePattern)) {
        throw "Composite load-gate lacks exact-byte evidence binding: $requiredSourcePattern"
    }
}
if ($source -match '(?im)\bGet-FileHash\b') {
    throw 'Composite load-gate re-reads an artifact while hashing it.'
}

$hostExecutable = (Get-Process -Id $PID).Path
$validationOutput = & $hostExecutable `
    -NoLogo -NoProfile -NonInteractive `
    -File $scriptPath `
    -HttpArtifactPath unused-http.json `
    -RuntimeArtifactPath unused-runtime.json `
    -ExpectedRunId ('a' * 32) `
    -ExpectedApiImageId ('sha256:' + ('b' * 64)) `
    -ExpectedRevision ('c' * 40) `
    -ExpectedReleaseDigest ('d' * 64) `
    -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw 'Composite load-gate validation mode failed.'
}
$validation = ($validationOutput -join [Environment]::NewLine) | ConvertFrom-Json
if (
    $validation.schema_version -ne 1 -or
    $validation.gate -ne 'stratus_candidate_production_load' -or
    -not $validation.validated -or
    -not $validation.requires_matching_run_id -or
    -not $validation.requires_full_runtime_overlap -or
    -not $validation.requires_exact_api_identity -or
    -not $validation.requires_exact_loopback_endpoint -or
    -not $validation.requires_raw_sample_coverage -or
    -not $validation.requires_exact_http_configuration -or
    -not $validation.requires_raw_http_reconciliation -or
    -not $validation.requires_raw_runtime_reconciliation -or
    -not $validation.requires_raw_topology_reconciliation -or
    -not $validation.requires_topology_hash_binding
) {
    throw 'Composite load-gate validation contract is incomplete.'
}

$expectedEndpoints = @(
    [pscustomobject]@{ tier = 'core'; endpoint = 'healthz'; path = 'healthz' }
    [pscustomobject]@{ tier = 'core'; endpoint = 'readyz'; path = 'readyz' }
    [pscustomobject]@{ tier = 'core'; endpoint = 'alerts_badge'; path = 'api/v1/alerts?state=unacknowledged&limit=1' }
    [pscustomobject]@{ tier = 'core'; endpoint = 'overview'; path = 'api/v1/overview' }
    [pscustomobject]@{ tier = 'core'; endpoint = 'signals'; path = 'api/v1/signals?limit=100' }
    [pscustomobject]@{ tier = 'core'; endpoint = 'positions'; path = 'api/v1/positions?limit=100' }
    [pscustomobject]@{ tier = 'core'; endpoint = 'paper_equity'; path = 'api/v1/equity?mode=paper' }
    [pscustomobject]@{ tier = 'core'; endpoint = 'commands'; path = 'api/v1/commands?limit=100' }
    [pscustomobject]@{ tier = 'core'; endpoint = 'jobs'; path = 'api/v1/jobs?limit=100' }
    [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'edge_report'; path = 'api/v1/edge-report?mode=paper' }
    [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'validation_ledger'; path = 'api/v1/validation-ledger?mode=paper' }
    [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'selection_policy'; path = 'api/v1/selection-policy-report?mode=paper' }
    [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'resolution_reconciliation'; path = 'api/v1/resolution-reconciliation?limit=40' }
    [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'settlement_cases'; path = 'api/v1/settlement-cases?limit=40' }
    [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'storage_capacity'; path = 'api/v1/storage-capacity' }
    [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'execution_cost'; path = 'api/v1/execution-cost-report?mode=paper' }
    [pscustomobject]@{ tier = 'evidence_execution'; endpoint = 'evidence_continuity'; path = 'api/v1/evidence-continuity' }
    [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'brier_report'; path = 'api/v1/brier-report?mode=paper' }
    [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'clv_report'; path = 'api/v1/clv-report?mode=paper' }
    [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'root_cause_actions'; path = 'api/v1/root-cause-actions?mode=paper' }
    [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'accuracy_overview'; path = 'api/v1/accuracy-overview' }
    [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'forecast_provenance'; path = 'api/v1/forecast-provenance' }
    [pscustomobject]@{ tier = 'accuracy_provenance'; endpoint = 'official_resolution_sources'; path = 'api/v1/official-resolution-sources' }
)

function Write-TestJson {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][object] $Value
    )

    [IO.File]::WriteAllText(
        $Path,
        ($Value | ConvertTo-Json -Depth 12),
        [Text.UTF8Encoding]::new($false)
    )
}

function Get-TestEndpointSummary {
    param([Parameter(Mandatory = $true)][object[]] $Requests)

    return @(
        $Requests |
            Group-Object endpoint |
            Sort-Object Name |
            ForEach-Object {
                $rows = @($_.Group)
                $latencies = @(
                    $rows | ForEach-Object { [double]$_.elapsed_ms } | Sort-Object
                )
                $p95Index = [Math]::Max(
                    0,
                    [Math]::Ceiling($latencies.Count * 0.95) - 1
                )
                $statuses = @(
                    $rows |
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
                    tier = [string]$rows[0].tier
                    count = $rows.Count
                    accepted = $rows.Count
                    errors = 0
                    timeouts = 0
                    readyz_fail_closed = @(
                        $rows | Where-Object readyz_fail_closed
                    ).Count
                    latency_ms = [pscustomobject]@{
                        minimum = [Math]::Round($latencies[0], 3)
                        average = [Math]::Round(
                            ($latencies | Measure-Object -Average).Average,
                            3
                        )
                        p95 = [Math]::Round($latencies[$p95Index], 3)
                        maximum = [Math]::Round($latencies[-1], 3)
                    }
                    statuses = $statuses
                }
            }
    )
}

function Invoke-TestGate {
    param(
        [Parameter(Mandatory = $true)][object] $Http,
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][string] $HttpPath,
        [Parameter(Mandatory = $true)][string] $RuntimePath,
        [Parameter(Mandatory = $true)][string] $AcceptedPath,
        [Parameter(Mandatory = $true)][string] $RunId,
        [Parameter(Mandatory = $true)][string] $ImageId,
        [Parameter(Mandatory = $true)][string] $Revision,
        [Parameter(Mandatory = $true)][string] $Digest
    )

    Write-TestJson -Path $HttpPath -Value $Http
    Write-TestJson -Path $RuntimePath -Value $Runtime
    & $hostExecutable `
        -NoLogo -NoProfile -NonInteractive `
        -File $scriptPath `
        -HttpArtifactPath $HttpPath `
        -RuntimeArtifactPath $RuntimePath `
        -ExpectedRunId $RunId `
        -ExpectedApiImageId $ImageId `
        -ExpectedRevision $Revision `
        -ExpectedReleaseDigest $Digest `
        -OutputPath $AcceptedPath *> $null
    return $LASTEXITCODE
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'stratus-load-gate-test-' + [guid]::NewGuid().ToString('N')
)
[void][IO.Directory]::CreateDirectory($tempRoot)
try {
    $runId = 'a' * 32
    $imageId = 'sha256:' + ('b' * 64)
    $revision = 'c' * 40
    $digest = 'd' * 64
    $httpPath = Join-Path $tempRoot 'http.json'
    $runtimePath = Join-Path $tempRoot 'runtime.json'
    $acceptedPath = Join-Path $tempRoot 'accepted.json'
    $httpStartedAt = [DateTimeOffset]::Parse('2026-07-29T10:00:00Z')
    $requestRows = @(
        foreach ($endpoint in $expectedEndpoints) {
            $ready = [string]$endpoint.endpoint -eq 'readyz'
            [pscustomobject]@{
                phase = 'cold_full'
                tier = [string]$endpoint.tier
                iteration = 1
                scheduled_offset_seconds = 0.0
                endpoint = [string]$endpoint.endpoint
                path = [string]$endpoint.path
                started_at = $httpStartedAt.ToString('O')
                elapsed_ms = 25.0
                status_code = if ($ready) { 503 } else { 200 }
                accepted = $true
                timeout = $false
                readyz_fail_closed = $ready
                error = $null
            }
            $interval = switch ([string]$endpoint.tier) {
                'core' { 10 }
                'evidence_execution' { 60 }
                'accuracy_provenance' { 300 }
                default { throw "Unexpected endpoint tier in fixture." }
            }
            for ($offset = $interval; $offset -le 300; $offset += $interval) {
                [pscustomobject]@{
                    phase = 'steady_state'
                    tier = [string]$endpoint.tier
                    iteration = [int]($offset / 10) + 1
                    scheduled_offset_seconds = [double]$offset
                    endpoint = [string]$endpoint.endpoint
                    path = [string]$endpoint.path
                    started_at = $httpStartedAt.AddSeconds($offset).ToString('O')
                    elapsed_ms = 25.0
                    status_code = if ($ready) { 503 } else { 200 }
                    accepted = $true
                    timeout = $false
                    readyz_fail_closed = $ready
                    error = $null
                }
            }
        }
    )
    $apiRequestCount = @(
        $requestRows | Where-Object { [string]$_.path -like 'api/v1/*' }
    ).Count
    $expectedTelemetryRequests = $apiRequestCount + 1
    $http = [pscustomobject]@{
        schema_version = 3
        gate = 'stratus_dashboard_http_load'
        http_passed = $true
        contaminated = $false
        exit_code = 0
        base_uri = 'http://127.0.0.1:8000'
        request_method = 'GET'
        mutation_requests = 0
        load_gate_run_id = $runId
        started_at = '2026-07-29T10:00:00Z'
        completed_at = '2026-07-29T10:05:01Z'
        telemetry = [pscustomobject]@{
            foreign_started = 0
            foreign_completed = 0
            foreign_5xx = 0
            untagged_started = 0
            untagged_completed = 0
            run_started = $expectedTelemetryRequests
            run_completed = $expectedTelemetryRequests
            run_aborted = 0
            expected_run_requests = $expectedTelemetryRequests
            run_5xx = 0
            expected_run_5xx = 0
            total_started = $expectedTelemetryRequests
            total_completed = $expectedTelemetryRequests
            total_aborted = 0
            total_5xx = 0
            counter_mismatch = $false
            before = [pscustomobject]@{ active_requests = 1 }
            after = [pscustomobject]@{ active_requests = 1 }
        }
        configuration = [pscustomobject]@{
            cold_full_refreshes = 1
            steady_state_seconds = 300
            core_interval_seconds = 10
            evidence_execution_interval_seconds = 60
            accuracy_provenance_interval_seconds = 300
            max_concurrent_get = 3
            scheduling_policy = 'work_conserving'
            timeout_seconds = 3
        }
        totals = [pscustomobject]@{
            requests = $requestRows.Count
            accepted = $requestRows.Count
            errors = 0
            timeouts = 0
            readyz_expected_fail_closed = @(
                $requestRows | Where-Object readyz_fail_closed
            ).Count
        }
        endpoint_summary = @(Get-TestEndpointSummary -Requests $requestRows)
        requests = $requestRows
    }
    $firstSample = [DateTimeOffset]::Parse('2026-07-29T09:59:55Z')
    $databaseSamples = @(
        0..350 | ForEach-Object {
            [pscustomobject]@{
                at = $firstSample.AddSeconds($_).ToString('O')
                api_sessions = 3
                api_active = 2
                api_idle_in_tx = 0
                runtime_writer_sessions = 0
                global_idle_in_tx = 0
                max_api_idle_tx_seconds = 0.0
                lock_waiters = 0
                oldest_api_query_seconds = 0.25
                schema_head = '20260730_0047'
                controls_ok = $true
                pending_commands = 0
                live_orders = 0
                live_positions = 0
                live_intents = 0
                live_fill_legs = 0
                live_enrollments = 0
            }
        }
    )
    $resourceSamples = @(
        0..69 | ForEach-Object {
            [pscustomobject]@{
                at = $firstSample.AddSeconds($_ * 5).ToString('O')
                api_cpu_percent = 12.0
                api_memory_percent = 20.0
                api_memory_limit_bytes = 1073741824L
                api_operator_key_entry_count = 1
                postgres_cpu_percent = 30.0
                postgres_memory_percent = 35.0
            }
        }
    )
    $topologySamples = @(
        0..78 | ForEach-Object {
            $topologyAt = if ($_ -eq 78) {
                [DateTimeOffset]::Parse('2026-07-29T10:06:30Z')
            }
            else {
                $firstSample.AddSeconds($_ * 5)
            }
            $projectRows = @(
                [pscustomobject]@{
                    container_id = 'api-container'
                    project = 'weatherpoly'
                    service = 'api'
                    oneoff = 'False'
                    container_name = 'weatherpoly-api-1'
                    networks = @('weatherpoly_default')
                },
                [pscustomobject]@{
                    container_id = 'postgres-container'
                    project = 'weatherpoly'
                    service = 'postgres'
                    oneoff = 'False'
                    container_name = 'weatherpoly-postgres-1'
                    networks = @('weatherpoly_default','weatherpoly_safety-plane')
                }
            )
            [pscustomobject]@{
                at = $topologyAt.ToString('O')
                project = 'weatherpoly'
                allowed_services = @('api','postgres')
                protected_networks = @('weatherpoly_default','weatherpoly_safety-plane')
                running_services = @('api','postgres')
                all_running_container_count = 2
                all_running_containers = @($projectRows)
                running_containers = @($projectRows)
                unexpected_containers = @()
                missing_container_ids = @()
                safe = $true
            }
        }
    )
    $drain = [pscustomobject]@{
        at = '2026-07-29T10:06:30Z'
        api_sessions = 1
        api_active = 0
        api_idle_in_tx = 0
        runtime_writer_sessions = 0
        global_idle_in_tx = 0
        max_api_idle_tx_seconds = 0.0
        lock_waiters = 0
        oldest_api_query_seconds = 0.0
        schema_head = '20260730_0047'
        controls_ok = $true
        pending_commands = 0
        live_orders = 0
        live_positions = 0
        live_intents = 0
        live_fill_legs = 0
        live_enrollments = 0
    }
    $runtime = [pscustomobject]@{
        schema_version = 2
        gate = 'stratus_dashboard_runtime_observation'
        load_gate_run_id = $runId
        started_at = '2026-07-29T09:59:54Z'
        observation_started_at = '2026-07-29T09:59:55Z'
        completed_at = '2026-07-29T10:06:31Z'
        duration_seconds = 390
        database_interval_seconds = 1
        resource_interval_seconds = 5
        drain_seconds = 5
        expected_schema_head = '20260730_0047'
        maximum_api_sessions = 4
        expected_api_image_id = $imageId
        expected_revision = $revision
        expected_release_digest = $digest
        baseline = [pscustomobject]@{
            api_container_id = 'api-container'
            api_image_id = $imageId
            api_restart_count = 0
            postgres_container_id = 'postgres-container'
            postgres_image_id = 'sha256:' + ('e' * 64)
            postgres_restart_count = 0
            compose_project = 'weatherpoly'
        }
        summary = [pscustomobject]@{
            database_sample_count = 351
            resource_sample_count = 70
            topology_sample_count = 79
            minimum_database_samples = 351
            minimum_resource_samples = 70
            sample_errors = 0
            max_api_sessions = 3
            max_api_active = 2
            max_runtime_writer_sessions = 0
            max_api_idle_in_tx = 0
            max_global_idle_in_tx = 0
            max_api_idle_tx_seconds = 0.0
            max_lock_waiters = 0
            max_oldest_api_query_seconds = 0.25
            max_api_cpu_percent = 12.0
            max_api_memory_percent = 20.0
            max_postgres_cpu_percent = 30.0
            rolling_30s_postgres_cpu_max = 30.0
            consecutive_postgres_cpu_150 = $false
            identity_changed = $false
            restart_changed = $false
            oom_observed = $false
            containers_healthy = $true
            candidate_identity_held = $true
            application_writers_stopped = $true
            exact_project_topology_held = $true
            invariants_changed = $false
            drain_passed = $true
            runtime_passed = $true
        }
        violations = @()
        drain = $drain
        sample_errors = @()
        database_samples = $databaseSamples
        resource_samples = $resourceSamples
        topology_samples = $topologySamples
    }
    $validHttpJson = $http | ConvertTo-Json -Depth 12
    $validRuntimeJson = $runtime | ConvertTo-Json -Depth 12

    $exitCode = Invoke-TestGate `
        -Http $http `
        -Runtime $runtime `
        -HttpPath $httpPath `
        -RuntimePath $runtimePath `
        -AcceptedPath $acceptedPath `
        -RunId $runId `
        -ImageId $imageId `
        -Revision $revision `
        -Digest $digest
    if ($exitCode -ne 0) {
        $diagnostic = Get-Content -LiteralPath $acceptedPath -Raw
        throw "Valid composite load-gate fixtures were rejected: $diagnostic"
    }
    $accepted = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (
        -not $accepted.production_gate_passed -or
        $accepted.run_id -ne $runId -or
        -not $accepted.overlap.runtime_covers_http_and_drain -or
        [string]$accepted.evidence.http_sha256 -cne (
            Get-FileHash -LiteralPath $httpPath -Algorithm SHA256
        ).Hash.ToLowerInvariant() -or
        [string]$accepted.evidence.runtime_sha256 -cne (
            Get-FileHash -LiteralPath $runtimePath -Algorithm SHA256
        ).Hash.ToLowerInvariant() -or
        [int]$accepted.evidence.topology_sample_count -ne 79 -or
        [string]$accepted.evidence.topology_sha256 -notmatch '^[0-9a-f]{64}$'
    ) {
        throw 'Composite load-gate did not bind valid evidence correctly.'
    }

    $lightweightHttp = $validHttpJson | ConvertFrom-Json
    $validRuntime = $validRuntimeJson | ConvertFrom-Json
    $lightweightHttp.configuration.max_concurrent_get = 1
    $exitCode = Invoke-TestGate `
        -Http $lightweightHttp `
        -Runtime $validRuntime `
        -HttpPath $httpPath `
        -RuntimePath $runtimePath `
        -AcceptedPath $acceptedPath `
        -RunId $runId `
        -ImageId $imageId `
        -Revision $revision `
        -Digest $digest
    if ($exitCode -ne 1) {
        $diagnostic = Get-Content -LiteralPath $acceptedPath -Raw
        throw "Lightweight HTTP concurrency evidence returned $exitCode`: $diagnostic"
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (
        $rejected.production_gate_passed -or
        @($rejected.violations) -notcontains 'http_configuration'
    ) {
        throw 'Lightweight concurrency rejection did not preserve its violation.'
    }

    $missingCadenceHttp = $validHttpJson | ConvertFrom-Json
    $validRuntime = $validRuntimeJson | ConvertFrom-Json
    $missingCadenceHttp.requests = @(
        $missingCadenceHttp.requests | Where-Object {
            -not (
                [string]$_.endpoint -eq 'healthz' -and
                [string]$_.phase -eq 'steady_state' -and
                [double]$_.scheduled_offset_seconds -eq 10
            )
        }
    )
    $missingCadenceHttp.totals.requests = $missingCadenceHttp.requests.Count
    $missingCadenceHttp.totals.accepted = $missingCadenceHttp.requests.Count
    $missingCadenceHttp.endpoint_summary = @(
        Get-TestEndpointSummary -Requests $missingCadenceHttp.requests
    )
    $exitCode = Invoke-TestGate `
        -Http $missingCadenceHttp `
        -Runtime $validRuntime `
        -HttpPath $httpPath `
        -RuntimePath $runtimePath `
        -AcceptedPath $acceptedPath `
        -RunId $runId `
        -ImageId $imageId `
        -Revision $revision `
        -Digest $digest
    if ($exitCode -ne 1) {
        throw 'Missing raw cadence evidence did not fail the composite load gate.'
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (@($rejected.violations) -notcontains 'http_endpoint_inventory') {
        throw 'Missing cadence rejection did not preserve its violation.'
    }

    $malformedHttp = $validHttpJson | ConvertFrom-Json
    $validRuntime = $validRuntimeJson | ConvertFrom-Json
    $malformedHttp.requests[0].PSObject.Properties.Remove('elapsed_ms')
    $exitCode = Invoke-TestGate `
        -Http $malformedHttp `
        -Runtime $validRuntime `
        -HttpPath $httpPath `
        -RuntimePath $runtimePath `
        -AcceptedPath $acceptedPath `
        -RunId $runId `
        -ImageId $imageId `
        -Revision $revision `
        -Digest $digest
    if ($exitCode -ne 1) {
        throw 'Malformed raw HTTP evidence did not fail the composite load gate.'
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (@($rejected.violations) -notcontains 'http_raw_requests') {
        throw 'Malformed raw HTTP rejection did not preserve its violation.'
    }

    $validHttp = $validHttpJson | ConvertFrom-Json
    $malformedRuntime = $validRuntimeJson | ConvertFrom-Json
    $malformedRuntime.database_samples[0].PSObject.Properties.Remove('controls_ok')
    $exitCode = Invoke-TestGate `
        -Http $validHttp `
        -Runtime $malformedRuntime `
        -HttpPath $httpPath `
        -RuntimePath $runtimePath `
        -AcceptedPath $acceptedPath `
        -RunId $runId `
        -ImageId $imageId `
        -Revision $revision `
        -Digest $digest
    if ($exitCode -ne 1) {
        throw 'Malformed raw runtime evidence did not fail the composite load gate.'
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (@($rejected.violations) -notcontains 'runtime_database_evidence') {
        throw 'Malformed raw runtime rejection did not preserve its violation.'
    }

    $wrongMemoryRuntime = $validRuntimeJson | ConvertFrom-Json
    $wrongMemoryRuntime.resource_samples[10].api_memory_limit_bytes = 2147483648L
    $exitCode = Invoke-TestGate `
        -Http $validHttp -Runtime $wrongMemoryRuntime `
        -HttpPath $httpPath -RuntimePath $runtimePath -AcceptedPath $acceptedPath `
        -RunId $runId -ImageId $imageId -Revision $revision -Digest $digest
    if ($exitCode -ne 1) {
        throw 'Transient API memory-cap inflation did not fail the composite load gate.'
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (@($rejected.violations) -notcontains 'runtime_resource_evidence') {
        throw 'API memory-cap rejection did not preserve its violation.'
    }

    $missingOperatorRuntime = $validRuntimeJson | ConvertFrom-Json
    $missingOperatorRuntime.resource_samples[10].api_operator_key_entry_count = 0
    $exitCode = Invoke-TestGate `
        -Http $validHttp -Runtime $missingOperatorRuntime `
        -HttpPath $httpPath -RuntimePath $runtimePath -AcceptedPath $acceptedPath `
        -RunId $runId -ImageId $imageId -Revision $revision -Digest $digest
    if ($exitCode -ne 1) {
        throw 'Transient API operator-credential removal did not fail the load gate.'
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (@($rejected.violations) -notcontains 'runtime_resource_evidence') {
        throw 'API operator-credential rejection did not preserve its violation.'
    }

    $validHttp = $validHttpJson | ConvertFrom-Json
    $unsafeTopologyRuntime = $validRuntimeJson | ConvertFrom-Json
    $unsafeTopologyRuntime.topology_samples[10].all_running_containers += @(
        [pscustomobject]@{
            container_id = 'worker-container'
            project = 'foreign-project'
            service = 'worker'
            oneoff = 'False'
            container_name = 'foreign-worker'
            networks = @('weatherpoly_default')
        }
    )
    $unsafeTopologyRuntime.topology_samples[10].all_running_container_count = 3
    $exitCode = Invoke-TestGate `
        -Http $validHttp -Runtime $unsafeTopologyRuntime `
        -HttpPath $httpPath -RuntimePath $runtimePath -AcceptedPath $acceptedPath `
        -RunId $runId -ImageId $imageId -Revision $revision -Digest $digest
    if ($exitCode -ne 1) {
        throw 'Forged safe topology hiding a protected-network container did not fail.'
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (@($rejected.violations) -notcontains 'runtime_topology_evidence') {
        throw 'Unexpected-container topology rejection lost its violation.'
    }

    $shortTopologyRuntime = $validRuntimeJson | ConvertFrom-Json
    $shortTopologyRuntime.topology_samples = @(
        $shortTopologyRuntime.topology_samples | Select-Object -First 69
    )
    $shortTopologyRuntime.summary.topology_sample_count = 69
    $exitCode = Invoke-TestGate `
        -Http $validHttp -Runtime $shortTopologyRuntime `
        -HttpPath $httpPath -RuntimePath $runtimePath -AcceptedPath $acceptedPath `
        -RunId $runId -ImageId $imageId -Revision $revision -Digest $digest
    if ($exitCode -ne 1) {
        throw 'Insufficient raw topology coverage did not fail.'
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (@($rejected.violations) -notcontains 'runtime_sample_coverage') {
        throw 'Topology coverage rejection lost its violation.'
    }

    $forgedSummaryRuntime = $validRuntimeJson | ConvertFrom-Json
    $forgedSummaryRuntime.summary.topology_sample_count = 80
    $forgedSummaryRuntime.summary.exact_project_topology_held = $false
    $exitCode = Invoke-TestGate `
        -Http $validHttp -Runtime $forgedSummaryRuntime `
        -HttpPath $httpPath -RuntimePath $runtimePath -AcceptedPath $acceptedPath `
        -RunId $runId -ImageId $imageId -Revision $revision -Digest $digest
    if ($exitCode -ne 1) {
        throw 'Forged topology summary did not fail reconciliation.'
    }
    $rejected = Get-Content -LiteralPath $acceptedPath -Raw | ConvertFrom-Json
    if (@($rejected.violations) -notcontains 'runtime_summary_reconciliation') {
        throw 'Topology summary rejection lost its violation.'
    }
}
finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$global:LASTEXITCODE = 0
[pscustomobject]@{
    script = $scriptPath
    syntax_valid = $true
    matching_run_required = $true
    full_overlap_required = $true
    exact_identity_required = $true
    validated = $true
} | ConvertTo-Json -Compress
