[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\Assert-StratusPaperCanaryArtifacts.ps1')
)
$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw 'PAPER canary artifact validator has PowerShell syntax errors.'
}

$source = Get-Content -LiteralPath $scriptPath -Raw
foreach ($required in @(
    '[IO.File]::ReadAllBytes($absolutePath)',
    '$payloadJson = $envelope.payload | ConvertTo-Json -Depth 30 -Compress',
    'ConvertFrom-StrictArtifactJson',
    "-Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json'",
    "Parameters.ContainsKey('DateKind')",
    "`$convertParameters['DateKind'] = 'String'",
    'Assert-NoReparsePath',
    'Assert-ExactEvidenceRecord',
    'Assert-RawOperationalSafetyEvidence',
    'Assert-SnapshotTimingEvidence',
    'Assert-RuntimeDatabaseEvidence',
    'Assert-ApiDatabaseCoherenceEvidence',
    '$ExpectedContinuityLanes',
    "[ValidateSet('Ready', 'Terminal')]",
    "'failure.json', 'divergence.json'",
    "'snapshot-{0:D3}m.json' -f `$offset",
    "'stratus-safety-release'",
    "'polymarket_paper_fail_closed'"
)) {
    if (-not $source.Contains($required)) {
        throw "PAPER canary artifact validator lacks contract: $required"
    }
}
foreach ($forbidden in @(
    '(?im)&\s*docker\b',
    '(?im)\bInvoke-(WebRequest|RestMethod)\b',
    '(?im)\b(Start|Stop|Remove|Restart)-Process\b',
    '(?im)\b(Set-Content|Add-Content|Out-File|WriteAllBytes|WriteAllText)\b',
    '(?im)\b(psql|Npgsql|docker compose)\b'
)) {
    if ($source -match $forbidden) {
        throw "PAPER canary artifact validator contains forbidden I/O: $forbidden"
    }
}
$failureEvidenceChecks = [regex]::Matches(
    $source,
    'Assert-NoFailureEvidence\s+-RunDirectory\s+\$runDirectory'
).Count
if ($failureEvidenceChecks -lt 3) {
    throw 'PAPER canary artifact validator lacks pre/Ready/Terminal failure checks.'
}

function Get-TestSha256 {
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

function Read-TestArtifactJson {
    param([Parameter(Mandatory = $true)][string] $Path)

    $convertFromJson = Get-Command `
        -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
        -CommandType Cmdlet -ErrorAction Stop
    $convertParameters = @{
        InputObject = Get-Content -LiteralPath $Path -Raw
    }
    if ($convertFromJson.Parameters.ContainsKey('DateKind')) {
        $convertParameters['DateKind'] = 'String'
    }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @convertParameters
}

function Get-TestMd5 {
    param([Parameter(Mandatory = $true)][string] $Value)

    $hasher = [Security.Cryptography.MD5]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        return (
            [BitConverter]::ToString($hasher.ComputeHash($bytes))
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}

function Write-TestEnvelope {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][object] $Payload
    )

    $utf8 = [Text.UTF8Encoding]::new($false)
    $payloadBytes = $utf8.GetBytes(
        ($Payload | ConvertTo-Json -Depth 30 -Compress)
    )
    $envelope = [ordered]@{
        schema_version = 1
        payload_sha256 = Get-TestSha256 -Bytes $payloadBytes
        payload = $Payload
    }
    $envelopeJson = $envelope | ConvertTo-Json -Depth 32
    $bytes = $utf8.GetBytes($envelopeJson)
    [IO.File]::WriteAllBytes($Path, $bytes)
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($Path)
        bytes = $bytes.Length
        sha256 = Get-TestSha256 -Bytes $bytes
    }
}

function Get-TestFileRecord {
    param([Parameter(Mandatory = $true)][string] $Path)

    $bytes = [IO.File]::ReadAllBytes($Path)
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($Path)
        bytes = $bytes.Length
        sha256 = Get-TestSha256 -Bytes $bytes
    }
}

function New-TestLease {
    param(
        [Parameter(Mandatory = $true)][string] $RunId,
        [Parameter(Mandatory = $true)][string] $ResumeRequestId,
        [Parameter(Mandatory = $true)][string] $ResumeActor,
        [Parameter(Mandatory = $true)][string] $ResumeReason,
        [Parameter(Mandatory = $true)][string] $State,
        [Parameter(Mandatory = $true)][string] $NonceSha256
    )

    $released = $State -eq 'released'
    return [ordered]@{
        id = 77
        lease_id = "paper-lease-$RunId"
        run_id = $RunId
        acquire_request_id = "paper-lease-acquire-$RunId"
        resume_request_id = $ResumeRequestId
        resume_actor = $ResumeActor
        resume_reason = $ResumeReason
        watchdog_stop_request_id = "paper-canary-watchdog-$RunId"
        direct_stop_request_id = "paper-canary-stop-$RunId"
        release_request_id = "paper-lease-release-$RunId"
        nonce_sha256 = $NonceSha256
        state = $State
        acquired_at = '2026-08-13T23:59:40.0000000+00:00'
        heartbeat_at = if ($released) {
            '2026-08-14T01:00:04.0000000+00:00'
        }
        else {
            '2026-08-13T23:59:48.0000000+00:00'
        }
        watchdog_at = if ($released) {
            '2026-08-14T01:00:04.0000000+00:00'
        }
        else {
            '2026-08-13T23:59:49.0000000+00:00'
        }
        expires_at = if ($released) {
            '2026-08-14T01:00:24.0000000+00:00'
        }
        else {
            '2026-08-14T00:00:10.0000000+00:00'
        }
        released_at = if ($released) {
            '2026-08-14T01:00:05.0000000+00:00'
        }
        else {
            $null
        }
        stop_command_id = if ($released) { 99 } else { $null }
    }
}

function New-TestContainer {
    param(
        [Parameter(Mandatory = $true)][string] $Service,
        [Parameter(Mandatory = $true)][string] $ContainerId,
        [Parameter(Mandatory = $true)][string] $ContainerName,
        [Parameter(Mandatory = $true)][string] $ImageId,
        [AllowNull()][object] $Revision,
        [AllowNull()][object] $ReleaseDigest,
        [AllowNull()][object] $Health,
        [Parameter(Mandatory = $true)][string[]] $Networks,
        [AllowNull()][object[]] $PublishedPorts = @(),
        [AllowNull()][string[]] $ClientAddresses = @()
    )

    return [ordered]@{
        service = $Service
        container_id = $ContainerId
        container_name = $ContainerName
        status = 'running'
        running = $true
        oom_killed = $false
        health = $Health
        started_at = '2026-08-13T23:45:00.0000000+00:00'
        image_id = $ImageId
        revision = $Revision
        release_digest = $ReleaseDigest
        restart_count = 0
        memory_limit_bytes = switch ($Service) {
            'api' { 1073741824L; break }
            'safety-control' { 268435456L; break }
            'worker' { 3221225472L; break }
            default { 0L; break }
        }
        operator_api_key_entry_count = if ($Service -eq 'api') { 1 } else { 0 }
        published_ports = @($PublishedPorts)
        compose_project = 'stratus-candidate'
        compose_service = $Service
        compose_oneoff = 'False'
        networks = @($Networks)
        client_addresses = @($ClientAddresses)
    }
}

function New-TestDockerState {
    param(
        [Parameter(Mandatory = $true)][string] $ImageId,
        [Parameter(Mandatory = $true)][string] $Revision,
        [Parameter(Mandatory = $true)][string] $ReleaseDigest
    )

    $project = 'stratus-candidate'
    $defaultNetwork = "${project}_default"
    $safetyNetwork = "${project}_safety-plane"
    $api = New-TestContainer -Service 'api' -ContainerId ('1' * 64) `
        -ContainerName 'stratus-candidate-api-1' -ImageId $ImageId `
        -Revision $Revision -ReleaseDigest $ReleaseDigest -Health 'healthy' `
        -Networks @($defaultNetwork) `
        -PublishedPorts @([ordered]@{
            container_port = '8000/tcp'; host_ip = '127.0.0.1'; host_port = '8000'
        }) -ClientAddresses @('172.30.0.2')
    $safety = New-TestContainer -Service 'safety-control' `
        -ContainerId ('2' * 64) `
        -ContainerName 'stratus-candidate-safety-control-1' -ImageId $ImageId `
        -Revision $Revision -ReleaseDigest $ReleaseDigest -Health 'healthy' `
        -Networks @($safetyNetwork) `
        -PublishedPorts @([ordered]@{
            container_port = '8001/tcp'; host_ip = '127.0.0.1'; host_port = '8001'
        }) -ClientAddresses @('172.31.0.2')
    $worker = New-TestContainer -Service 'worker' -ContainerId ('3' * 64) `
        -ContainerName 'stratus-candidate-worker-1' -ImageId $ImageId `
        -Revision $Revision -ReleaseDigest $ReleaseDigest -Health $null `
        -Networks @($defaultNetwork) -ClientAddresses @('172.30.0.3')
    $postgres = New-TestContainer -Service 'postgres' -ContainerId ('4' * 64) `
        -ContainerName 'stratus-candidate-postgres-1' `
        -ImageId ('sha256:' + ('a' * 64)) -Revision $null `
        -ReleaseDigest $null -Health 'healthy' `
        -Networks @($defaultNetwork, $safetyNetwork) `
        -PublishedPorts @([ordered]@{
            container_port = '5432/tcp'; host_ip = '127.0.0.1'; host_port = '5432'
        }) -ClientAddresses @('172.30.0.4', '172.31.0.4')
    $rows = @(
        [ordered]@{
            container_id = $api.container_id; project = $project; service = 'api'
            oneoff = 'False'; container_name = $api.container_name
            networks = @($api.networks)
        },
        [ordered]@{
            container_id = $safety.container_id; project = $project
            service = 'safety-control'; oneoff = 'False'
            container_name = $safety.container_name; networks = @($safety.networks)
        },
        [ordered]@{
            container_id = $worker.container_id; project = $project; service = 'worker'
            oneoff = 'False'; container_name = $worker.container_name
            networks = @($worker.networks)
        },
        [ordered]@{
            container_id = $postgres.container_id; project = $project
            service = 'postgres'; oneoff = 'False'
            container_name = $postgres.container_name; networks = @($postgres.networks)
        }
    )
    return [ordered]@{
        api = $api
        safety_control = $safety
        worker = $worker
        postgres = $postgres
        topology = [ordered]@{
            captured_at = '2026-08-14T00:00:00.0000000+00:00'
            project = $project
            allowed_services = @('api', 'safety-control', 'worker', 'postgres')
            protected_networks = @($defaultNetwork, $safetyNetwork)
            all_running_container_count = 4
            all_running_containers = @($rows)
            running_containers = @($rows)
            unexpected_containers = @()
            missing_container_ids = @()
            safe = $true
        }
    }
}

function New-TestRuntimeDatabase {
    param([Parameter(Mandatory = $true)][object] $Docker)

    $roleSuffix = Get-TestMd5 -Value 'stratus'
    $runtimeRole = "stratus_app_$roleSuffix"
    $safetyRole = "stratus_safety_app_$roleSuffix"
    $rows = @(
        [ordered]@{
            pid = 1101; backend_type = 'client backend'; usename = $runtimeRole
            application_name = 'stratus-api'
            client_addr = [string]$Docker.api.client_addresses[0]
            identity = 'api'
        },
        [ordered]@{
            pid = 1102; backend_type = 'client backend'; usename = $runtimeRole
            application_name = 'stratus-api'
            client_addr = [string]$Docker.api.client_addresses[0]
            identity = 'api'
        },
        [ordered]@{
            pid = 1201; backend_type = 'client backend'; usename = $runtimeRole
            application_name = 'stratus-runtime'
            client_addr = [string]$Docker.worker.client_addresses[0]
            identity = 'worker'
        },
        [ordered]@{
            pid = 1202; backend_type = 'client backend'; usename = $runtimeRole
            application_name = 'stratus-runtime'
            client_addr = [string]$Docker.worker.client_addresses[0]
            identity = 'worker'
        },
        [ordered]@{
            pid = 1301; backend_type = 'client backend'; usename = $safetyRole
            application_name = 'stratus-safety-control'
            client_addr = [string]$Docker.safety_control.client_addresses[0]
            identity = 'safety_control'
        }
    )
    return [ordered]@{
        database_name = 'stratus'
        collector = [ordered]@{
            pid = 1001
            backend_type = 'client backend'
            usename = 'stratus'
            current_user = 'stratus'
            application_name = 'stratus-paper-canary-collector'
            client_addr = $null
        }
        collector_exact = $true
        expected_identities = [ordered]@{
            api = [ordered]@{
                backend_type = 'client backend'
                usename = $runtimeRole
                application_name = 'stratus-api'
                client_addresses = @($Docker.api.client_addresses)
                max_sessions = 4
            }
            worker = [ordered]@{
                backend_type = 'client backend'
                usename = $runtimeRole
                application_name = 'stratus-runtime'
                client_addresses = @($Docker.worker.client_addresses)
                max_sessions = 15
            }
            safety_control = [ordered]@{
                backend_type = 'client backend'
                usename = $safetyRole
                application_name = 'stratus-safety-control'
                client_addresses = @($Docker.safety_control.client_addresses)
                max_sessions = 2
            }
        }
        client_backends = @($rows)
        client_backend_sessions = 5
        api_sessions = 2
        worker_sessions = 2
        safety_control_sessions = 1
        unexpected_sessions = 0
        idle_in_transaction = 0
        lock_waiters = 0
    }
}

function New-TestContinuityLanes {
    param([Parameter(Mandatory = $true)][long] $EpochId)

    $names = @(
        'execution_evidence',
        'official_resolution_truth',
        'operational_legacy',
        'predictive_validation',
        'selection_policy',
        'station_lifecycle'
    )
    $lanes = [Collections.Generic.List[object]]::new()
    for ($index = 0; $index -lt $names.Count; $index++) {
        $lanes.Add([ordered]@{
            id = 100 + $index
            epoch_id = $EpochId
            lane_name = $names[$index]
            action = 'continue'
            identity_hash = ('{0:x}' -f ($index + 1)) * 64
            parent_lane_id = $null
            boundary_at = '2026-08-13T23:40:00.0000000+00:00'
            manifest = [ordered]@{ schema_version = 1; lane = $names[$index] }
            rationale = 'Immutable candidate lane accepted.'
            record_hash = ('{0:x}' -f ($index + 7)) * 64
        })
    }
    return @($lanes)
}

function New-TestHttpSample {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][object] $Body,
        [int] $StatusCode = 200
    )

    return [ordered]@{
        path = $Path
        status_code = $StatusCode
        elapsed_ms = 2.5
        bytes = 128
        content_sha256 = '9' * 64
        body = $Body
    }
}

function New-TestApiExpected {
    param(
        [Parameter(Mandatory = $true)][string] $Cohort,
        [Parameter(Mandatory = $true)][bool] $Active
    )

    return [ordered]@{
        overview = [ordered]@{
            mode = 'paper'
            kill_switch = -not $Active
            active_markets = 11
            blocked_markets = 2
            open_positions = 3
            pending_signals = 5
            bankroll_usd = 1000.25
            open_exposure_usd = 12.50
        }
        venue_count = 2
        command_ids = @(12, 11)
        # Job rows are ordered by finished_at, which need not follow ID order.
        job_ids = @(40, 41)
        execution_cost = [ordered]@{
            opened_at = '2026-08-13T23:40:00.0000000+00:00'
            definition_version = 'execution-cost-v1'
            definition_hash = '6' * 64
            attempts = 4
            filled_attempts = 1
            no_fill_attempts = 1
            sealed_attempts = 2
            pending_attempts = 2
            maker_attempts = 3
            maker_fills = 1
        }
        validation_ledger = [ordered]@{
            selected_pipeline_cohort = $Cohort
            enrolled_orders = 1
            pending_outcomes = 1
        }
    }
}

function New-TestHttpState {
    param(
        [Parameter(Mandatory = $true)][long] $EpochId,
        [Parameter(Mandatory = $true)][string] $Cohort,
        [Parameter(Mandatory = $true)][string] $ReleaseDigest,
        [Parameter(Mandatory = $true)][object[]] $ContinuityLanes,
        [Parameter(Mandatory = $true)][bool] $Active
    )

    $globalKill = -not $Active
    $readyStatus = if ($Active) { 'ready' } else { 'not_ready' }
    $readyCode = if ($Active) { 200 } else { 503 }
    $readyReasons = if ($Active) { @() } else { @('kill_switch') }
    return [ordered]@{
        healthz = New-TestHttpSample -Path 'healthz' `
            -Body ([ordered]@{ status = 'ok' })
        readyz = New-TestHttpSample -Path 'readyz' -StatusCode $readyCode `
            -Body ([ordered]@{ status = $readyStatus; reasons = @($readyReasons) })
        overview = New-TestHttpSample -Path 'api/v1/overview' -Body ([ordered]@{
            runtime = [ordered]@{ mode = 'paper'; kill_switch = $globalKill }
            active_markets = 11
            blocked_markets = 2
            open_positions = 3
            pending_signals = 5
            bankroll_usd = 1000.25
            open_exposure_usd = 12.50
        })
        venues = New-TestHttpSample -Path 'api/v1/venues' -Body ([ordered]@{
            items = @(
                [ordered]@{
                    venue = 'polymarket'; enabled = $true; mode = 'paper'
                    kill_switch = $globalKill; halted_reason = $null
                    live_confirmation_until = $null
                },
                [ordered]@{
                    venue = 'binance_spot'; enabled = $true; mode = 'backtest'
                    kill_switch = $true
                    halted_reason = 'binance kill remains sealed'
                    live_confirmation_until = $null
                }
            )
        })
        commands = New-TestHttpSample -Path 'api/v1/commands?limit=200' `
            -Body ([ordered]@{
                items = @([ordered]@{ id = 12 }, [ordered]@{ id = 11 })
            })
        jobs = New-TestHttpSample -Path 'api/v1/jobs?limit=200' `
            -Body ([ordered]@{
                items = @([ordered]@{ id = 40 }, [ordered]@{ id = 41 })
            })
        continuity = New-TestHttpSample -Path 'api/v1/evidence-continuity' `
            -Body ([ordered]@{
                active_epoch = [ordered]@{
                    id = $EpochId; cohort = $Cohort; image_digest = $ReleaseDigest
                }
                cohort_match = $true
                image_match = $true
                execution_gate_allowed = $true
                execution_gate_reason = 'evidence_continuity_match'
                frozen_lanes = @()
                lane_identity_mismatches = @()
                collection_window = [ordered]@{
                    stalled_days = 0; missing_scheduler_jobs = @()
                }
                continuity_lanes = @($ContinuityLanes)
            })
        execution_cost = New-TestHttpSample `
            -Path 'api/v1/execution-cost-report?mode=paper' `
            -Body ([ordered]@{
                mode = 'paper'
                opened_at = '2026-08-13T23:40:00.0000000+00:00'
                definition_version = 'execution-cost-v1'
                definition_hash = '6' * 64
                attempts = 4
                filled_attempts = 1
                no_fill_attempts = 1
                sealed_attempts = 2
                pending_attempts = 2
                maker_attempts = 3
                maker_fills = 1
            })
        validation_ledger = New-TestHttpSample `
            -Path 'api/v1/validation-ledger?mode=paper' `
            -Body ([ordered]@{
                mode = 'paper'
                selected_pipeline_cohort = $Cohort
                selected_is_current = $true
                enrolled_orders = 1
                pending_outcomes = 1
            })
    }
}

function New-TestSafetyHttpState {
    param([Parameter(Mandatory = $true)][bool] $Active)

    return [ordered]@{
        healthz = New-TestHttpSample -Path 'healthz' `
            -Body ([ordered]@{ status = 'ok' })
        readyz = New-TestHttpSample -Path 'readyz' -Body ([ordered]@{
            status = 'ready'; database_boundary = 'exact'; reasons = @()
        })
    }
}

function New-TestWorkerLogs {
    param(
        [Parameter(Mandatory = $true)][DateTimeOffset] $Since,
        [Parameter(Mandatory = $true)][int] $MaintenanceRows,
        [Parameter(Mandatory = $true)][DateTimeOffset] $LatestAt
    )

    return [ordered]@{
        since = $Since.ToString('O')
        tail_limit = 5000
        nonempty_lines = $MaintenanceRows
        timestamp_parse_failures = 0
        possibly_truncated = $false
        latest_docker_timestamp = $LatestAt.ToString('O')
        maintenance_rows = $MaintenanceRows
        rows = @()
        claim_identity_mismatches = 0
        phase_a_b_runs = 0
        within_grace_deferred_runs = 0
        zero_leg_sealed_runs = 0
        zero_mark_finalize_events = @()
        repeated_zero_mark_finalize_ids = @()
        dangerous_log_lines = @()
    }
}

function New-TestResourceSample {
    param(
        [Parameter(Mandatory = $true)][DateTimeOffset] $Anchor,
        [Parameter(Mandatory = $true)][double] $ScheduledSeconds
    )

    $actual = $ScheduledSeconds + 0.5
    return [ordered]@{
        captured_at = $Anchor.AddSeconds($ScheduledSeconds + 0.75).ToString('O')
        api_cpu_percent = 5.0
        api_memory_percent = 20.0
        safety_cpu_percent = 4.0
        safety_memory_percent = 15.0
        worker_cpu_percent = 7.0
        worker_memory_percent = 25.0
        postgres_cpu_percent = 10.0
        postgres_memory_percent = 30.0
        completed_elapsed_seconds = $ScheduledSeconds + 1.0
        scheduled_elapsed_seconds = $ScheduledSeconds
        actual_elapsed_seconds = $actual
        schedule_lag_seconds = 0.5
    }
}

function New-TestResourceEvidence {
    param(
        [Parameter(Mandatory = $true)][DateTimeOffset] $Anchor,
        [Parameter(Mandatory = $true)][int] $OffsetMinutes
    )

    $target = [double]($OffsetMinutes * 60)
    $totalSamples = [int]($target / 5) + 1
    $windowCount = [int][Math]::Min(7, $totalSamples)
    $firstScheduled = $target - (($windowCount - 1) * 5)
    $window = [Collections.Generic.List[object]]::new()
    for ($index = 0; $index -lt $windowCount; $index++) {
        $window.Add((New-TestResourceSample -Anchor $Anchor `
            -ScheduledSeconds ($firstScheduled + ($index * 5))))
    }
    return [pscustomobject]@{
        resources = $window[-1]
        sampler = [ordered]@{
            total_samples = $totalSamples
            expected_interval_seconds = 5
            rolling_window = @($window)
            violations = @()
        }
    }
}

function New-TestExecutionEvidence {
    return [ordered]@{
        maker_intents = 4
        resting_orders = 2
        fill_legs = 1
        cost_seals = 1
        mark_5m_observed = 1
        mark_5m_missing = 0
        mark_5m_unresolved = 0
        mark_30m_observed = 0
        mark_30m_missing = 0
        mark_30m_unresolved = 0
        waiting_for_marks = 0
        cohort_waiting_obligations = 0
        global_waiting_obligations = 0
        cohort_post_grace_missing_marks = 0
        global_post_grace_missing_marks = 0
        oldest_due_age_seconds = $null
        duplicate_marks = 0
        duplicate_seals = 0
        finalizable_unsealed = 0
        finalizable_unsealed_ids = @()
        seals_before_required_marks = 0
        invalid_record_hashes = 0
        hash_rows_checked = 4
        enrollments = 1
        outcomes = 0
    }
}

function New-TestJobEvidence {
    param(
        [Parameter(Mandatory = $true)][int] $SuccessCount,
        [Parameter(Mandatory = $true)][DateTimeOffset] $LatestAt
    )

    return [ordered]@{
        maintenance_runs = $SuccessCount
        maintenance_success = $SuccessCount
        maintenance_failures = 0
        max_scheduled_gap_seconds = 15.0
        first_success_delay_seconds = 5.0
        latest_maintenance_at = $LatestAt.ToString('O')
        latest_maintenance_age_seconds = 5.0
        legacy_runs = 0
    }
}

function New-TestSnapshotDatabase {
    param(
        [Parameter(Mandatory = $true)][string] $RunId,
        [Parameter(Mandatory = $true)][string] $ResumeRequestId,
        [Parameter(Mandatory = $true)][string] $ResumeActor,
        [Parameter(Mandatory = $true)][string] $ResumeReason,
        [Parameter(Mandatory = $true)][long] $EpochId,
        [Parameter(Mandatory = $true)][string] $Cohort,
        [Parameter(Mandatory = $true)][string] $ReleaseDigest,
        [Parameter(Mandatory = $true)][string] $NonceSha256,
        [Parameter(Mandatory = $true)][int] $OffsetMinutes,
        [Parameter(Mandatory = $true)][DateTimeOffset] $DatabaseNow,
        [Parameter(Mandatory = $true)][object] $Docker
    )

    return [ordered]@{
        database_now = $DatabaseNow.ToString('O')
        schema_head = '20260730_0047'
        database_boundaries = [ordered]@{
            runtime_exact = $true; safety_exact = $true
        }
        resume = [ordered]@{
            request_id = $ResumeRequestId
            command = 'resume'
            payload = [ordered]@{ venue = 'polymarket' }
            actor = $ResumeActor
            reason = $ResumeReason
            status = 'succeeded'
            created_at = '2026-08-13T23:59:55.0000000+00:00'
            executed_at = '2026-08-13T23:59:59.0000000+00:00'
        }
        stop = $null
        release_stop = $null
        canary_lease = [ordered]@{
            id = 77
            lease_id = "paper-lease-$RunId"
            run_id = $RunId
            acquire_request_id = "paper-lease-acquire-$RunId"
            resume_request_id = $ResumeRequestId
            resume_actor = $ResumeActor
            resume_reason = $ResumeReason
            watchdog_stop_request_id = "paper-canary-watchdog-$RunId"
            direct_stop_request_id = "paper-canary-stop-$RunId"
            release_request_id = "paper-lease-release-$RunId"
            nonce_sha256 = $NonceSha256
            state = 'active'
            acquired_at = '2026-08-13T23:59:40.0000000+00:00'
            heartbeat_at = $DatabaseNow.AddSeconds(-2).ToString('O')
            watchdog_at = $DatabaseNow.AddSeconds(-1).ToString('O')
            expires_at = $DatabaseNow.AddSeconds(20).ToString('O')
            released_at = $null
            stop_command_id = $null
        }
        active_canary_leases = 1
        active_epoch = [ordered]@{
            id = $EpochId
            opened_at = '2026-08-13T23:40:00.0000000+00:00'
            cohort = $Cohort
            image_digest = $ReleaseDigest
        }
        latest_health_audit = [ordered]@{
            ts = $DatabaseNow.AddSeconds(-5).ToString('O')
            status = 'healthy'
            issues = @()
        }
        global_control = [ordered]@{
            mode = 'paper'; kill_switch = $false; halted_reason = $null
            live_confirmation_until = $null
        }
        venues = [ordered]@{
            polymarket = [ordered]@{
                enabled = $true; mode = 'paper'; kill_switch = $false
                halted_reason = $null; live_confirmation_until = $null
            }
            binance_spot = [ordered]@{
                enabled = $true; mode = 'backtest'; kill_switch = $true
                halted_reason = 'binance kill remains sealed'
                live_confirmation_until = $null
            }
        }
        pending_commands = 0
        unexpected_pending_commands = 0
        unexpected_commands_since_boundary = 0
        runtime_database = New-TestRuntimeDatabase -Docker $Docker
        scheduler = [ordered]@{
            latest_maintenance_age_seconds = 5.0
            recent_maintenance_failures = 0
            recent_legacy_runs = 0
        }
        jobs = New-TestJobEvidence -SuccessCount 250 `
            -LatestAt $DatabaseNow.AddSeconds(-5)
        execution = New-TestExecutionEvidence
        api_expected = New-TestApiExpected -Cohort $Cohort -Active $true
        live = [ordered]@{
            orders = 0; positions = 0; intents = 0; fill_legs = 0
            enrollments = 0; outcomes = 0
        }
    }
}

function New-ValidFixture {
    param([Parameter(Mandatory = $true)][string] $RunId)

    $resumeRequestId = "resume-$($RunId.Substring(0, 16))"
    $resumeActor = 'codex-release-owner'
    $resumeReason = (
        'Successor epoch and cold scheduler canary accepted; ' +
        'resume Polymarket PAPER only.'
    )
    $epochId = 23L
    $cohort = 'paper-canary-release'
    $imageId = 'sha256:' + ('b' * 64)
    $revision = 'c' * 40
    $releaseDigest = 'd' * 64
    $nonceSha256 = 'e' * 64
    $root = Join-Path $script:temporaryRoot $RunId
    $runDirectory = Join-Path $root (
        Join-Path '.stratus-cache\paper-canary' $RunId
    )
    [void][IO.Directory]::CreateDirectory($runDirectory)
    $dockerState = New-TestDockerState -ImageId $imageId -Revision $revision `
        -ReleaseDigest $releaseDigest
    $continuityLanes = @(New-TestContinuityLanes -EpochId $epochId)
    $preflightHttp = New-TestHttpState -EpochId $epochId -Cohort $cohort `
        -ReleaseDigest $releaseDigest -ContinuityLanes $continuityLanes `
        -Active $false
    $preflightSafetyHttp = New-TestSafetyHttpState -Active $false
    $workerStartedAt = [DateTimeOffset]'2026-08-13T23:45:00.0000000+00:00'
    $preflightDatabaseNow = [DateTimeOffset]'2026-08-13T23:59:29.0000000+00:00'
    $preflightResources = [ordered]@{
        captured_at = '2026-08-13T23:59:30.0000000+00:00'
        api_cpu_percent = 5.0
        api_memory_percent = 20.0
        safety_cpu_percent = 4.0
        safety_memory_percent = 15.0
        worker_cpu_percent = 7.0
        worker_memory_percent = 25.0
        postgres_cpu_percent = 10.0
        postgres_memory_percent = 30.0
    }
    $preflightWorkerLogs = New-TestWorkerLogs -Since $workerStartedAt `
        -MaintenanceRows 4 -LatestAt $preflightDatabaseNow.AddSeconds(-5)
    $preflight = [ordered]@{
        schema_version = 1
        canary_run_id = $RunId
        captured_at = '2026-08-13T23:59:30.0000000+00:00'
        emergency_stop_armed = $true
        expected_resume = [ordered]@{
            request_id = $resumeRequestId
            actor = $resumeActor
            reason = $resumeReason
            command = 'resume'
            payload = [ordered]@{ venue = 'polymarket' }
        }
        emergency_stop = [ordered]@{
            request_id = "paper-canary-stop-$RunId"
            command = 'stop'
            reason = 'STRATUS PAPER canary terminal fail-closed stop.'
            actor = 'codex-paper-canary'
            payload = [ordered]@{ venue = 'polymarket' }
        }
        docker = $dockerState
        resources = $preflightResources
        worker_logs = $preflightWorkerLogs
        database = [ordered]@{
            database_now = $preflightDatabaseNow.ToString('O')
            schema_head = '20260730_0047'
            active_epoch = [ordered]@{
                id = $epochId
                opened_at = '2026-08-13T23:40:00.0000000+00:00'
                cohort = $cohort
                image_digest = $releaseDigest
            }
            latest_health_audit = [ordered]@{
                ts = $preflightDatabaseNow.AddSeconds(-5).ToString('O')
                status = 'healthy'
                issues = @()
            }
            database_boundaries = [ordered]@{
                runtime_exact = $true
                safety_exact = $true
            }
            global_control = [ordered]@{
                mode = 'paper'
                kill_switch = $true
                halted_reason = $null
                live_confirmation_until = $null
            }
            venues = [ordered]@{
                polymarket = [ordered]@{
                    enabled = $true
                    mode = 'paper'
                    kill_switch = $true
                    halted_reason = $null
                    live_confirmation_until = $null
                }
                binance_spot = [ordered]@{
                    enabled = $true
                    mode = 'backtest'
                    kill_switch = $true
                    halted_reason = 'binance kill remains sealed'
                    live_confirmation_until = $null
                }
            }
            pending_commands = 0
            unexpected_pending_commands = 0
            unexpected_commands_since_boundary = 0
            runtime_database = New-TestRuntimeDatabase -Docker $dockerState
            active_canary_leases = 0
            canary_lease = $null
            resume = $null
            stop = $null
            release_stop = $null
            scheduler = [ordered]@{
                latest_maintenance_age_seconds = 5.0
                recent_maintenance_failures = 0
                recent_legacy_runs = 0
            }
            jobs = New-TestJobEvidence -SuccessCount 4 `
                -LatestAt $preflightDatabaseNow.AddSeconds(-5)
            execution = New-TestExecutionEvidence
            api_expected = New-TestApiExpected -Cohort $cohort -Active $false
            live = [ordered]@{
                orders = 0
                positions = 0
                intents = 0
                fill_legs = 0
                enrollments = 0
                outcomes = 0
            }
        }
        http = $preflightHttp
        api_database_coherence = [ordered]@{
            attempts = 1
            stable_api_expected = $true
            api_expected_sha256 = '8' * 64
        }
        safety_http = $preflightSafetyHttp
        safety = [ordered]@{ passed = $true; violations = @() }
    }
    $preflightExpectedJson = $preflight.database.api_expected |
        ConvertTo-Json -Depth 12 -Compress
    $preflight.api_database_coherence.api_expected_sha256 = Get-TestSha256 `
        -Bytes ([Text.UTF8Encoding]::new($false).GetBytes($preflightExpectedJson))
    $preflightRecord = Write-TestEnvelope `
        -Path (Join-Path $runDirectory 'preflight.json') -Payload $preflight

    $activeLease = New-TestLease -RunId $RunId `
        -ResumeRequestId $resumeRequestId `
        -ResumeActor $resumeActor -ResumeReason $resumeReason `
        -State 'active' `
        -NonceSha256 $nonceSha256
    $leaseArtifact = [ordered]@{
        schema_version = 1
        canary_run_id = $RunId
        preflight_artifact_sha256 = $preflightRecord.sha256
        lease_id = "paper-lease-$RunId"
        acquire_request_id = "paper-lease-acquire-$RunId"
        resume_request_id = $resumeRequestId
        direct_stop_request_id = "paper-canary-stop-$RunId"
        nonce_sha256 = $nonceSha256
        acquire = [ordered]@{
            created = $true
            post_attempts = 1
            ambiguous_first_post = $false
            item = $activeLease
        }
        confirmed = [ordered]@{
            database_now = '2026-08-13T23:59:50.0000000+00:00'
            lease = $activeLease
            stop_command = $null
            active_leases = 1
            global_control = [ordered]@{
                mode = 'paper'
                kill_switch = $true
                halted_reason = $null
                live_confirmation_until = $null
            }
            polymarket = [ordered]@{
                enabled = $true
                mode = 'paper'
                kill_switch = $true
                halted_reason = $null
                live_confirmation_until = $null
            }
            binance = [ordered]@{
                enabled = $true
                mode = 'backtest'
                kill_switch = $true
                halted_reason = 'binance kill remains sealed'
                live_confirmation_until = $null
            }
            pending_commands = 0
            unexpected_commands_since_boundary = 0
            live_rows = 0
        }
    }
    $leaseRecord = Write-TestEnvelope `
        -Path (Join-Path $runDirectory 'lease-acquire.json') `
        -Payload $leaseArtifact

    $offsets = @(0, 5, 15, 30, 45, 60)
    $ready = [ordered]@{
        schema_version = 1
        canary_run_id = $RunId
        ready_at = '2026-08-13T23:59:50.0000000+00:00'
        preflight_database_at = '2026-08-13T23:59:29.0000000+00:00'
        command_boundary_at = '2026-08-13T23:59:28.0000000+00:00'
        preflight_artifact_sha256 = $preflightRecord.sha256
        preflight_safe = $true
        expected_resume_request_id = $resumeRequestId
        emergency_stop_request_id = "paper-canary-stop-$RunId"
        emergency_stop_payload = [ordered]@{ venue = 'polymarket' }
        emergency_stop_armed = $true
        lease = [ordered]@{
            lease_id = "paper-lease-$RunId"
            acquire_request_id = "paper-lease-acquire-$RunId"
            direct_stop_request_id = "paper-canary-stop-$RunId"
            release_request_id = "paper-lease-release-$RunId"
            nonce_sha256 = $nonceSha256
            artifact_sha256 = $leaseRecord.sha256
            state = 'active'
        }
        offsets = @($offsets)
    }
    $readyRecord = Write-TestEnvelope `
        -Path (Join-Path $runDirectory 'ready.json') -Payload $ready

    $snapshotRecords = [Collections.Generic.List[object]]::new()
    $previousHash = $readyRecord.sha256
    $anchorDatabase = [DateTimeOffset]'2026-08-14T00:00:00.0000000+00:00'
    $resumeExecuted = [DateTimeOffset]'2026-08-13T23:59:59.0000000+00:00'
    $activeHttp = New-TestHttpState -EpochId $epochId -Cohort $cohort `
        -ReleaseDigest $releaseDigest -ContinuityLanes $continuityLanes `
        -Active $true
    $activeSafetyHttp = New-TestSafetyHttpState -Active $true
    for ($sequence = 0; $sequence -lt $offsets.Count; $sequence++) {
        $offset = $offsets[$sequence]
        $databaseNow = $anchorDatabase.AddMinutes($offset).AddSeconds(2)
        $capturedAt = $databaseNow.AddSeconds(1)
        $snapshotDatabase = New-TestSnapshotDatabase -RunId $RunId `
            -ResumeRequestId $resumeRequestId -ResumeActor $resumeActor `
            -ResumeReason $resumeReason -EpochId $epochId -Cohort $cohort `
            -ReleaseDigest $releaseDigest -NonceSha256 $nonceSha256 `
            -OffsetMinutes $offset -DatabaseNow $databaseNow `
            -Docker $dockerState
        $resourceEvidence = New-TestResourceEvidence -Anchor $anchorDatabase `
            -OffsetMinutes $offset
        $workerLogs = New-TestWorkerLogs -Since $anchorDatabase `
            -MaintenanceRows 250 -LatestAt $databaseNow.AddSeconds(-1)
        $snapshot = [ordered]@{
            schema_version = 1
            canary_run_id = $RunId
            sequence = $sequence
            offset_minutes = $offset
            captured_at = $capturedAt.ToString('O')
            anchor = [ordered]@{
                resume_request_id = $resumeRequestId
                resume_executed_at = $resumeExecuted.ToString('O')
                first_observed_database_at = $anchorDatabase.ToString('O')
                monotonic_elapsed_seconds = ($offset * 60) + 1.0
            }
            expected_identity = [ordered]@{
                epoch_id = $epochId
                cohort = $cohort
                image_id = $imageId
                revision = $revision
                release_digest = $releaseDigest
                schema_head = '20260730_0047'
            }
            previous_snapshot_sha256 = $previousHash
            docker = $dockerState
            resources = $resourceEvidence.resources
            resource_sampler = $resourceEvidence.sampler
            worker_logs = $workerLogs
            database = $snapshotDatabase
            http = $activeHttp
            api_database_coherence = [ordered]@{
                attempts = 1
                stable_api_expected = $true
                api_expected_sha256 = '8' * 64
            }
            safety_http = $activeSafetyHttp
            safety = [ordered]@{ passed = $true; violations = @() }
        }
        $snapshotExpectedJson = $snapshot.database.api_expected |
            ConvertTo-Json -Depth 12 -Compress
        $snapshot.api_database_coherence.api_expected_sha256 = Get-TestSha256 `
            -Bytes ([Text.UTF8Encoding]::new($false).GetBytes($snapshotExpectedJson))
        $record = Write-TestEnvelope `
            -Path (Join-Path $runDirectory ('snapshot-{0:D3}m.json' -f $offset)) `
            -Payload $snapshot
        $snapshotRecords.Add($record)
        $previousHash = $record.sha256
    }

    $releasedLease = New-TestLease -RunId $RunId `
        -ResumeRequestId $resumeRequestId `
        -ResumeActor $resumeActor -ResumeReason $resumeReason `
        -State 'released' `
        -NonceSha256 $nonceSha256
    $manifest = [ordered]@{
        schema_version = 1
        canary_run_id = $RunId
        passed = $true
        runtime_canary_passed = $true
        expected_resume_request_id = $resumeRequestId
        anchor_database_time = $anchorDatabase.ToString('O')
        anchor_executed_at = $resumeExecuted.ToString('O')
        completed_at = '2026-08-14T01:00:07.0000000+00:00'
        offsets = @($offsets)
        evidence_chain = [ordered]@{
            preflight = $preflightRecord
            lease_acquire = $leaseRecord
            ready = $readyRecord
        }
        snapshots = @($snapshotRecords)
        final_snapshot_sha256 = $previousHash
        execution_evidence = [ordered]@{
            runtime_gate = 'informational_only'
            all_cases_observed = $false
            cases = [ordered]@{
                earliest_snapshot_observed = $true
                phase_a_b_observed = $false
                within_grace_deferred_observed = $false
                natural_maker_intent_observed = $false
                natural_fill_observed = $false
                natural_mark_observed = $false
                natural_seal_observed = $false
                natural_enrollment_observed = $false
                natural_outcome_observed = $false
            }
            deltas = [ordered]@{
                maker_intents = 0
                fill_legs = 0
                cost_seals = 0
                mark_5m_observed = 0
                mark_30m_observed = 0
                enrollments = 0
                outcomes = 0
            }
        }
        emergency_stop_submitted = $true
        emergency_stop = [ordered]@{
            release_request_id = "paper-lease-release-$RunId"
            created = $true
            post_attempts = 1
            ambiguous_first_post = $false
            status = 'released'
            controls_confirmed = $true
            confirmed_at = '2026-08-14T01:00:06.0000000+00:00'
            lease = $releasedLease
            stop_command = [ordered]@{
                id = 99
                request_id = "paper-lease-release-$RunId"
                command = 'stop'
                actor = 'stratus-safety-release'
                reason = "Successful PAPER canary paper-lease-$RunId completed."
                status = 'succeeded'
                payload = [ordered]@{ venue = 'polymarket' }
                detail = 'PAPER canary lease released fail-closed.'
                created_at = '2026-08-14T01:00:04.0000000+00:00'
                executed_at = '2026-08-14T01:00:05.0000000+00:00'
            }
        }
        safety_lease_released = $true
        safety_lease_id = "paper-lease-$RunId"
        safety_lease_release_request_id = "paper-lease-release-$RunId"
        terminal_runtime = 'polymarket_paper_fail_closed'
    }
    $manifestPath = Join-Path $runDirectory 'manifest.json'
    $manifestRecord = Write-TestEnvelope -Path $manifestPath -Payload $manifest

    return [pscustomobject]@{
        root = $root
        run_directory = $runDirectory
        run_id = $RunId
        resume_request_id = $resumeRequestId
        resume_actor = $resumeActor
        resume_reason = $resumeReason
        epoch_id = $epochId
        cohort = $cohort
        image_id = $imageId
        revision = $revision
        release_digest = $releaseDigest
        manifest_path = $manifestPath
        manifest_record = $manifestRecord
    }
}

function Update-TestSnapshotChain {
    param(
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][int] $TargetOffset,
        [Parameter(Mandatory = $true)][scriptblock] $Mutation
    )

    $readyPath = Join-Path $Fixture.run_directory 'ready.json'
    $previousHash = Get-TestSha256 -Bytes ([IO.File]::ReadAllBytes($readyPath))
    $records = [Collections.Generic.List[object]]::new()
    foreach ($offset in @(0, 5, 15, 30, 45, 60)) {
        $path = Join-Path $Fixture.run_directory (
            'snapshot-{0:D3}m.json' -f $offset
        )
        $envelope = Read-TestArtifactJson -Path $path
        if ($TargetOffset -eq -1 -or $offset -eq $TargetOffset) {
            [void](& $Mutation $envelope.payload)
        }
        $envelope.payload.previous_snapshot_sha256 = $previousHash
        $record = Write-TestEnvelope -Path $path -Payload $envelope.payload
        $records.Add($record)
        $previousHash = $record.sha256
    }

    $manifestEnvelope = Read-TestArtifactJson -Path $Fixture.manifest_path
    $manifestEnvelope.payload.snapshots = @($records)
    $manifestEnvelope.payload.final_snapshot_sha256 = $previousHash
    $Fixture.manifest_record = Write-TestEnvelope -Path $Fixture.manifest_path `
        -Payload $manifestEnvelope.payload
}

function Update-TestPreflightChain {
    param(
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][scriptblock] $Mutation
    )

    $preflightPath = Join-Path $Fixture.run_directory 'preflight.json'
    $preflightEnvelope = Read-TestArtifactJson -Path $preflightPath
    [void](& $Mutation $preflightEnvelope.payload)
    $preflightRecord = Write-TestEnvelope -Path $preflightPath `
        -Payload $preflightEnvelope.payload

    $leasePath = Join-Path $Fixture.run_directory 'lease-acquire.json'
    $leaseEnvelope = Read-TestArtifactJson -Path $leasePath
    $leaseEnvelope.payload.preflight_artifact_sha256 = $preflightRecord.sha256
    $leaseRecord = Write-TestEnvelope -Path $leasePath -Payload $leaseEnvelope.payload

    $readyPath = Join-Path $Fixture.run_directory 'ready.json'
    $readyEnvelope = Read-TestArtifactJson -Path $readyPath
    $readyEnvelope.payload.preflight_artifact_sha256 = $preflightRecord.sha256
    $readyEnvelope.payload.lease.artifact_sha256 = $leaseRecord.sha256
    $readyRecord = Write-TestEnvelope -Path $readyPath -Payload $readyEnvelope.payload

    $previousHash = $readyRecord.sha256
    $records = [Collections.Generic.List[object]]::new()
    foreach ($offset in @(0, 5, 15, 30, 45, 60)) {
        $path = Join-Path $Fixture.run_directory (
            'snapshot-{0:D3}m.json' -f $offset
        )
        $snapshotEnvelope = Read-TestArtifactJson -Path $path
        $snapshotEnvelope.payload.previous_snapshot_sha256 = $previousHash
        $record = Write-TestEnvelope -Path $path `
            -Payload $snapshotEnvelope.payload
        $records.Add($record)
        $previousHash = $record.sha256
    }
    $manifestEnvelope = Read-TestArtifactJson -Path $Fixture.manifest_path
    $manifestEnvelope.payload.evidence_chain.preflight = $preflightRecord
    $manifestEnvelope.payload.evidence_chain.lease_acquire = $leaseRecord
    $manifestEnvelope.payload.evidence_chain.ready = $readyRecord
    $manifestEnvelope.payload.snapshots = @($records)
    $manifestEnvelope.payload.final_snapshot_sha256 = $previousHash
    $Fixture.manifest_record = Write-TestEnvelope -Path $Fixture.manifest_path `
        -Payload $manifestEnvelope.payload
}

function Invoke-ArtifactValidator {
    param(
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][string] $Mode
    )

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $script:hostExecutable `
            -NoLogo -NoProfile -NonInteractive `
            -File $scriptPath `
            -Root $Fixture.root `
            -Mode $Mode `
            -RunId $Fixture.run_id `
            -ResumeRequestId $Fixture.resume_request_id `
            -ResumeActor $Fixture.resume_actor `
            -ResumeReason $Fixture.resume_reason `
            -EpochId $Fixture.epoch_id `
            -Cohort $Fixture.cohort `
            -ImageId $Fixture.image_id `
            -Revision $Fixture.revision `
            -ReleaseDigest $Fixture.release_digest 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    return [pscustomobject]@{
        exit_code = $exitCode
        output = @($output | ForEach-Object { [string]$_ })
    }
}

function Assert-ValidFixture {
    param(
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][string] $Mode
    )

    $result = Invoke-ArtifactValidator -Fixture $Fixture -Mode $Mode
    if ($result.exit_code -ne 0) {
        throw "$Mode fixture was rejected: $($result.output -join ' | ')"
    }
    $json = ($result.output -join [Environment]::NewLine) | ConvertFrom-Json
    if (
        -not [bool]$json.verified -or
        -not [bool]$json.validated -or
        [string]$json.mode -cne $Mode -or
        [string]$json.canary_run_id -cne $Fixture.run_id -or
        [string]$json.expected_resume_request_id -cne
            $Fixture.resume_request_id -or
        [string]$json.lease_id -cne "paper-lease-$($Fixture.run_id)" -or
        [string]$json.direct_stop_request_id -cne
            "paper-canary-stop-$($Fixture.run_id)" -or
        [string]$json.release_request_id -cne
            "paper-lease-release-$($Fixture.run_id)" -or
        [string]$json.preflight_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$json.lease_acquire_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$json.ready_sha256 -cnotmatch '^[0-9a-f]{64}$'
    ) {
        throw "$Mode validator returned an invalid acceptance record."
    }
    if (
        $Mode -eq 'Ready' -and
        (
            -not [bool]$json.binance_baseline.enabled -or
            [string]$json.binance_baseline.mode -cne 'backtest' -or
            -not [bool]$json.binance_baseline.kill_switch -or
            [string]$json.binance_baseline.halted_reason -cne
                'binance kill remains sealed' -or
            $null -ne $json.binance_baseline.live_confirmation_until
        )
    ) {
        throw 'Ready validator did not return the sealed Binance baseline.'
    }
    if (
        $Mode -eq 'Terminal' -and
        (
            -not [bool]$json.terminal -or
            [string]$json.manifest_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            [string]$json.final_snapshot_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            [double]$json.database_window_seconds -lt 3600 -or
            [double]$json.monotonic_window_seconds -lt 3600 -or
            [int]$json.continuity_lane_count -ne 6
        )
    ) {
        throw 'Terminal validator did not return the terminal chain identity.'
    }
}

function Assert-InvalidFixture {
    param(
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][string] $Mode,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $result = Invoke-ArtifactValidator -Fixture $Fixture -Mode $Mode
    if ($result.exit_code -eq 0) {
        throw "$Label fixture was accepted."
    }
}

$script:hostExecutable = (Get-Process -Id $PID).Path
$script:temporaryRoot = Join-Path (
    [IO.Path]::GetTempPath()
) ("stratus-paper-canary-artifacts-$([Guid]::NewGuid().ToString('N'))")
[void][IO.Directory]::CreateDirectory($script:temporaryRoot)
try {
    $valid = New-ValidFixture -RunId ('1' * 32)
    Assert-ValidFixture -Fixture $valid -Mode 'Ready'
    Assert-ValidFixture -Fixture $valid -Mode 'Terminal'

    $timingTamper = New-ValidFixture -RunId ('a' * 32)
    Update-TestSnapshotChain -Fixture $timingTamper -TargetOffset 30 `
        -Mutation {
            param($payload)
            $payload.anchor.monotonic_elapsed_seconds = 1921.0
        }
    Assert-InvalidFixture -Fixture $timingTamper -Mode 'Terminal' `
        -Label 'rehashed milestone timing tamper'

    $allLate = New-ValidFixture -RunId ('b' * 32)
    Update-TestSnapshotChain -Fixture $allLate -TargetOffset -1 `
        -Mutation {
            param($payload)
            $payload.anchor.monotonic_elapsed_seconds = 3601.0
            $payload.database.database_now = '2026-08-14T01:00:02.0000000+00:00'
            $payload.database.latest_health_audit.ts = (
                '2026-08-14T00:59:57.0000000+00:00'
            )
            $payload.database.canary_lease.heartbeat_at = (
                '2026-08-14T01:00:00.0000000+00:00'
            )
            $payload.database.canary_lease.watchdog_at = (
                '2026-08-14T01:00:01.0000000+00:00'
            )
            $payload.database.canary_lease.expires_at = (
                '2026-08-14T01:00:22.0000000+00:00'
            )
            $payload.worker_logs.latest_docker_timestamp = (
                '2026-08-14T01:00:01.0000000+00:00'
            )
            $payload.captured_at = '2026-08-14T01:00:03.0000000+00:00'
        }
    Assert-InvalidFixture -Fixture $allLate -Mode 'Terminal' `
        -Label 'rehashed all snapshots at T+60'

    $liveTamper = New-ValidFixture -RunId ('c' * 32)
    Update-TestSnapshotChain -Fixture $liveTamper -TargetOffset 15 `
        -Mutation {
            param($payload)
            $payload.database.live.orders = 1
        }
    Assert-InvalidFixture -Fixture $liveTamper -Mode 'Terminal' `
        -Label 'rehashed LIVE rows tamper'

    $binanceTamper = New-ValidFixture -RunId ('d' * 32)
    Update-TestSnapshotChain -Fixture $binanceTamper -TargetOffset 30 `
        -Mutation {
            param($payload)
            $payload.database.venues.binance_spot.kill_switch = $false
            foreach ($venue in @($payload.http.venues.body.items)) {
                if ([string]$venue.venue -ceq 'binance_spot') {
                    $venue.kill_switch = $false
                }
            }
        }
    Assert-InvalidFixture -Fixture $binanceTamper -Mode 'Terminal' `
        -Label 'rehashed Binance seal tamper'

    $maintenanceTamper = New-ValidFixture -RunId ('e' * 32)
    Update-TestSnapshotChain -Fixture $maintenanceTamper -TargetOffset 45 `
        -Mutation {
            param($payload)
            $payload.database.jobs.maintenance_runs = 251
            $payload.database.jobs.maintenance_failures = 1
        }
    Assert-InvalidFixture -Fixture $maintenanceTamper -Mode 'Terminal' `
        -Label 'rehashed snapshot maintenance failure'

    $preflightMaintenanceTamper = New-ValidFixture -RunId ('f' * 32)
    Update-TestPreflightChain -Fixture $preflightMaintenanceTamper `
        -Mutation {
            param($payload)
            $payload.database.jobs.maintenance_runs = 5
            $payload.database.jobs.maintenance_failures = 1
        }
    Assert-InvalidFixture -Fixture $preflightMaintenanceTamper -Mode 'Ready' `
        -Label 'rehashed preflight maintenance failure'

    $nullReadyLive = New-ValidFixture -RunId ('0a' * 16)
    Update-TestPreflightChain -Fixture $nullReadyLive `
        -Mutation {
            param($payload)
            $payload.database.live.orders = $null
        }
    Assert-InvalidFixture -Fixture $nullReadyLive -Mode 'Ready' `
        -Label 'rehashed null preflight LIVE count'

    $nullReadyPending = New-ValidFixture -RunId ('0b' * 16)
    Update-TestPreflightChain -Fixture $nullReadyPending `
        -Mutation {
            param($payload)
            $payload.database.pending_commands = $null
        }
    Assert-InvalidFixture -Fixture $nullReadyPending -Mode 'Ready' `
        -Label 'rehashed null preflight pending-command count'

    $nullReadyRestart = New-ValidFixture -RunId ('0c' * 16)
    Update-TestPreflightChain -Fixture $nullReadyRestart `
        -Mutation {
            param($payload)
            $payload.docker.api.restart_count = $null
        }
    Assert-InvalidFixture -Fixture $nullReadyRestart -Mode 'Ready' `
        -Label 'rehashed null preflight Docker restart count'

    $wrongMemoryLimit = New-ValidFixture -RunId ('1a' * 16)
    Update-TestPreflightChain -Fixture $wrongMemoryLimit `
        -Mutation {
            param($payload)
            $payload.docker.api.memory_limit_bytes = 2147483648L
        }
    Assert-InvalidFixture -Fixture $wrongMemoryLimit -Mode 'Ready' `
        -Label 'rehashed inflated API memory limit'

    $credentialLeak = New-ValidFixture -RunId ('1b' * 16)
    Update-TestPreflightChain -Fixture $credentialLeak `
        -Mutation {
            param($payload)
            $payload.docker.worker.operator_api_key_entry_count = 1
        }
    Assert-InvalidFixture -Fixture $credentialLeak -Mode 'Ready' `
        -Label 'rehashed worker operator-credential leak'

    $nullTerminalCount = New-ValidFixture -RunId ('0d' * 16)
    Update-TestSnapshotChain -Fixture $nullTerminalCount -TargetOffset 15 `
        -Mutation {
            param($payload)
            $payload.database.jobs.maintenance_failures = $null
        }
    Assert-InvalidFixture -Fixture $nullTerminalCount -Mode 'Terminal' `
        -Label 'rehashed null terminal snapshot safety count'

    $unknownHealthSeverity = New-ValidFixture -RunId ('0e' * 16)
    Update-TestSnapshotChain -Fixture $unknownHealthSeverity -TargetOffset 30 `
        -Mutation {
            param($payload)
            $payload.database.latest_health_audit.status = 'warning'
            $payload.database.latest_health_audit.issues = @(
                [ordered]@{
                    code = 'hostile_unknown_severity'
                    detail = 'must fail closed'
                    severity = 'unknown'
                }
            )
        }
    Assert-InvalidFixture -Fixture $unknownHealthSeverity -Mode 'Terminal' `
        -Label 'rehashed unknown health severity'

    $outerArrayEnvelope = New-ValidFixture -RunId ('0f' * 16)
    $outerPreflightPath = Join-Path $outerArrayEnvelope.run_directory `
        'preflight.json'
    $outerPreflight = Read-TestArtifactJson -Path $outerPreflightPath
    $outerJson = ConvertTo-Json -InputObject @($outerPreflight) -Depth 32
    [IO.File]::WriteAllText(
        $outerPreflightPath,
        $outerJson,
        [Text.UTF8Encoding]::new($false)
    )
    $outerPreflightRecord = Get-TestFileRecord -Path $outerPreflightPath
    $outerLeasePath = Join-Path $outerArrayEnvelope.run_directory `
        'lease-acquire.json'
    $outerLease = Read-TestArtifactJson -Path $outerLeasePath
    $outerLease.payload.preflight_artifact_sha256 = $outerPreflightRecord.sha256
    $outerLeaseRecord = Write-TestEnvelope -Path $outerLeasePath `
        -Payload $outerLease.payload
    $outerReadyPath = Join-Path $outerArrayEnvelope.run_directory 'ready.json'
    $outerReady = Read-TestArtifactJson -Path $outerReadyPath
    $outerReady.payload.preflight_artifact_sha256 = $outerPreflightRecord.sha256
    $outerReady.payload.lease.artifact_sha256 = $outerLeaseRecord.sha256
    [void](Write-TestEnvelope -Path $outerReadyPath -Payload $outerReady.payload)
    Assert-InvalidFixture -Fixture $outerArrayEnvelope -Mode 'Ready' `
        -Label 'rehashed singleton outer-array envelope'

    $nullLeaseHeartbeat = New-ValidFixture -RunId ('10' * 16)
    $nullLeasePath = Join-Path $nullLeaseHeartbeat.run_directory `
        'lease-acquire.json'
    $nullLeaseEnvelope = Read-TestArtifactJson -Path $nullLeasePath
    $nullLeaseEnvelope.payload.confirmed.lease.heartbeat_at = $null
    $nullLeaseRecord = Write-TestEnvelope -Path $nullLeasePath `
        -Payload $nullLeaseEnvelope.payload
    $nullLeaseReadyPath = Join-Path $nullLeaseHeartbeat.run_directory 'ready.json'
    $nullLeaseReady = Read-TestArtifactJson -Path $nullLeaseReadyPath
    $nullLeaseReady.payload.lease.artifact_sha256 = $nullLeaseRecord.sha256
    [void](Write-TestEnvelope -Path $nullLeaseReadyPath `
        -Payload $nullLeaseReady.payload)
    Assert-InvalidFixture -Fixture $nullLeaseHeartbeat -Mode 'Ready' `
        -Label 'rehashed null confirmed lease heartbeat'

    $runtimeOwnerTamper = New-ValidFixture -RunId ('ab' * 16)
    Update-TestSnapshotChain -Fixture $runtimeOwnerTamper -TargetOffset 15 `
        -Mutation {
            param($payload)
            $runtime = $payload.database.runtime_database
            $runtime.expected_identities.api.usename = 'stratus'
            $runtime.expected_identities.worker.usename = 'stratus'
            foreach ($row in @($runtime.client_backends)) {
                if ([string]$row.identity -cin @('api', 'worker')) {
                    $row.usename = 'stratus'
                }
            }
        }
    Assert-InvalidFixture -Fixture $runtimeOwnerTamper -Mode 'Terminal' `
        -Label 'rehashed hostile owner runtime sessions'

    $runtimeSourceTamper = New-ValidFixture -RunId ('bc' * 16)
    Update-TestPreflightChain -Fixture $runtimeSourceTamper `
        -Mutation {
            param($payload)
            $runtime = $payload.database.runtime_database
            $runtime.expected_identities.api.client_addresses = @('172.30.0.99')
            foreach ($row in @($runtime.client_backends)) {
                if ([string]$row.identity -ceq 'api') {
                    $row.client_addr = '172.30.0.99'
                }
            }
        }
    Assert-InvalidFixture -Fixture $runtimeSourceTamper -Mode 'Ready' `
        -Label 'rehashed wrong-source runtime sessions'

    $unexpectedRuntimeTamper = New-ValidFixture -RunId ('ad' * 16)
    Update-TestSnapshotChain -Fixture $unexpectedRuntimeTamper -TargetOffset 5 `
        -Mutation {
            param($payload)
            $runtime = $payload.database.runtime_database
            $unexpected = [ordered]@{
                pid = 1401
                backend_type = 'client backend'
                usename = 'stratus'
                application_name = 'stratus-operator-migrate'
                client_addr = '172.30.0.99'
                identity = 'unexpected'
            }
            $runtime.client_backends = @(
                @($runtime.client_backends) + @($unexpected)
            )
            $runtime.client_backend_sessions = 6
            $runtime.unexpected_sessions = 1
        }
    Assert-InvalidFixture -Fixture $unexpectedRuntimeTamper -Mode 'Terminal' `
        -Label 'rehashed unexpected runtime database session'

    $coherenceTamper = New-ValidFixture -RunId ('cd' * 16)
    Update-TestSnapshotChain -Fixture $coherenceTamper -TargetOffset 30 `
        -Mutation {
            param($payload)
            $payload.http.overview.body.active_markets = 12
        }
    Assert-InvalidFixture -Fixture $coherenceTamper -Mode 'Terminal' `
        -Label 'rehashed HTTP versus api_expected mismatch'

    $coherenceSealTamper = New-ValidFixture -RunId ('ac' * 16)
    Update-TestSnapshotChain -Fixture $coherenceSealTamper -TargetOffset 5 `
        -Mutation {
            param($payload)
            $payload.database.api_expected.overview.active_markets = 12
            $payload.http.overview.body.active_markets = 12
        }
    Assert-InvalidFixture -Fixture $coherenceSealTamper -Mode 'Terminal' `
        -Label 'rehashed stale canonical api_expected seal'

    $exposedApiTamper = New-ValidFixture -RunId ('de' * 16)
    Update-TestPreflightChain -Fixture $exposedApiTamper `
        -Mutation {
            param($payload)
            $payload.docker.api.published_ports[0].host_ip = '0.0.0.0'
        }
    Assert-InvalidFixture -Fixture $exposedApiTamper -Mode 'Ready' `
        -Label 'rehashed externally exposed API port'

    $hiddenContainerTamper = New-ValidFixture -RunId ('ef' * 16)
    Update-TestSnapshotChain -Fixture $hiddenContainerTamper -TargetOffset 45 `
        -Mutation {
            param($payload)
            $extra = [ordered]@{
                container_id = '5' * 64
                project = 'stratus-candidate'
                service = 'migrate'
                oneoff = 'False'
                container_name = 'stratus-candidate-migrate-1'
                networks = @('stratus-candidate_default')
            }
            $topology = $payload.docker.topology
            $topology.all_running_containers = @(
                @($topology.all_running_containers) + @($extra)
            )
            $topology.all_running_container_count = @(
                $topology.all_running_containers
            ).Count
        }
    Assert-InvalidFixture -Fixture $hiddenContainerTamper -Mode 'Terminal' `
        -Label 'rehashed hidden relevant container'

    $tampered = New-ValidFixture -RunId ('2' * 32)
    $preflightPath = Join-Path $tampered.run_directory 'preflight.json'
    $tamperedEnvelope = Read-TestArtifactJson -Path $preflightPath
    $tamperedEnvelope.payload.safety.passed = $false
    [IO.File]::WriteAllText(
        $preflightPath,
        ($tamperedEnvelope | ConvertTo-Json -Depth 32),
        [Text.UTF8Encoding]::new($false)
    )
    Assert-InvalidFixture -Fixture $tampered -Mode 'Ready' `
        -Label 'payload tamper'

    $hashMismatch = New-ValidFixture -RunId ('3' * 32)
    $snapshotPath = Join-Path $hashMismatch.run_directory 'snapshot-000m.json'
    [IO.File]::AppendAllText(
        $snapshotPath,
        ' ',
        [Text.UTF8Encoding]::new($false)
    )
    Assert-InvalidFixture -Fixture $hashMismatch -Mode 'Terminal' `
        -Label 'file hash mismatch'

    $identityMismatch = New-ValidFixture -RunId ('4' * 32)
    $identityPath = Join-Path $identityMismatch.run_directory 'snapshot-000m.json'
    $identityEnvelope = Read-TestArtifactJson -Path $identityPath
    $identityEnvelope.payload.expected_identity.cohort = 'wrong-cohort'
    [void](Write-TestEnvelope -Path $identityPath -Payload $identityEnvelope.payload)
    Assert-InvalidFixture -Fixture $identityMismatch -Mode 'Terminal' `
        -Label 'snapshot identity mismatch'

    $divergence = New-ValidFixture -RunId ('5' * 32)
    [IO.File]::WriteAllText(
        (Join-Path $divergence.run_directory 'divergence.json'),
        '{}',
        [Text.UTF8Encoding]::new($false)
    )
    Assert-InvalidFixture -Fixture $divergence -Mode 'Ready' `
        -Label 'divergence evidence'

    $stopMismatch = New-ValidFixture -RunId ('6' * 32)
    $stopEnvelope = Read-TestArtifactJson -Path $stopMismatch.manifest_path
    $stopEnvelope.payload.emergency_stop.stop_command.actor = 'wrong-actor'
    [void](Write-TestEnvelope -Path $stopMismatch.manifest_path `
        -Payload $stopEnvelope.payload)
    Assert-InvalidFixture -Fixture $stopMismatch -Mode 'Terminal' `
        -Label 'release STOP identity mismatch'

    $releasePostInvariant = New-ValidFixture -RunId ('11' * 16)
    $releasePostEnvelope = Read-TestArtifactJson `
        -Path $releasePostInvariant.manifest_path
    $releasePostEnvelope.payload.emergency_stop.created = $false
    $releasePostEnvelope.payload.emergency_stop.post_attempts = 2
    $releasePostEnvelope.payload.emergency_stop.ambiguous_first_post = $false
    [void](Write-TestEnvelope -Path $releasePostInvariant.manifest_path `
        -Payload $releasePostEnvelope.payload)
    Assert-InvalidFixture -Fixture $releasePostInvariant -Mode 'Terminal' `
        -Label 'release POST producer invariant mismatch'

    $nonceMismatch = New-ValidFixture -RunId ('7' * 32)
    $nonceEnvelope = Read-TestArtifactJson -Path $nonceMismatch.manifest_path
    $nonceEnvelope.payload.emergency_stop.lease.nonce_sha256 = 'f' * 64
    [void](Write-TestEnvelope -Path $nonceMismatch.manifest_path `
        -Payload $nonceEnvelope.payload)
    Assert-InvalidFixture -Fixture $nonceMismatch -Mode 'Terminal' `
        -Label 'released lease nonce mismatch'

    $resumeIdentityMismatch = New-ValidFixture -RunId ('8' * 32)
    $leasePath = Join-Path $resumeIdentityMismatch.run_directory `
        'lease-acquire.json'
    $leaseEnvelope = Read-TestArtifactJson -Path $leasePath
    $leaseEnvelope.payload.confirmed.lease.resume_actor = 'wrong-resume-actor'
    $leaseRecord = Write-TestEnvelope -Path $leasePath `
        -Payload $leaseEnvelope.payload
    $readyPath = Join-Path $resumeIdentityMismatch.run_directory 'ready.json'
    $readyEnvelope = Read-TestArtifactJson -Path $readyPath
    $readyEnvelope.payload.lease.artifact_sha256 = $leaseRecord.sha256
    [void](Write-TestEnvelope -Path $readyPath -Payload $readyEnvelope.payload)
    Assert-InvalidFixture -Fixture $resumeIdentityMismatch -Mode 'Ready' `
        -Label 'resume actor mismatch'

    $pathMismatch = New-ValidFixture -RunId ('9' * 32)
    $pathEnvelope = Read-TestArtifactJson -Path $pathMismatch.manifest_path
    $pathEnvelope.payload.evidence_chain.ready.path = (
        Join-Path $pathMismatch.run_directory '..\ready.json'
    )
    [void](Write-TestEnvelope -Path $pathMismatch.manifest_path `
        -Payload $pathEnvelope.payload)
    Assert-InvalidFixture -Fixture $pathMismatch -Mode 'Terminal' `
        -Label 'evidence path mismatch'
}
finally {
    if ([IO.Directory]::Exists($script:temporaryRoot)) {
        Remove-Item -LiteralPath $script:temporaryRoot -Recurse -Force
    }
}

[pscustomobject]@{
    schema_version = 1
    test = 'stratus_paper_canary_artifact_validator'
    passed = $true
    ready_fixture_passed = $true
    terminal_fixture_passed = $true
    rehashed_timing_tamper_rejected = $true
    rehashed_all_snapshots_late_rejected = $true
    rehashed_live_tamper_rejected = $true
    rehashed_binance_tamper_rejected = $true
    rehashed_snapshot_maintenance_failure_rejected = $true
    rehashed_preflight_maintenance_failure_rejected = $true
    rehashed_null_ready_live_count_rejected = $true
    rehashed_null_ready_pending_count_rejected = $true
    rehashed_null_ready_restart_count_rejected = $true
    rehashed_inflated_memory_limit_rejected = $true
    rehashed_operator_credential_leak_rejected = $true
    rehashed_null_terminal_count_rejected = $true
    rehashed_unknown_health_severity_rejected = $true
    rehashed_outer_array_envelope_rejected = $true
    rehashed_null_confirmed_lease_heartbeat_rejected = $true
    rehashed_hostile_runtime_owner_rejected = $true
    rehashed_wrong_runtime_source_rejected = $true
    rehashed_unexpected_runtime_session_rejected = $true
    rehashed_api_database_mismatch_rejected = $true
    rehashed_stale_api_expected_seal_rejected = $true
    rehashed_exposed_api_port_rejected = $true
    rehashed_hidden_relevant_container_rejected = $true
    payload_tamper_rejected = $true
    file_hash_mismatch_rejected = $true
    identity_mismatch_rejected = $true
    divergence_rejected = $true
    stop_identity_mismatch_rejected = $true
    release_post_invariant_mismatch_rejected = $true
    released_lease_nonce_mismatch_rejected = $true
    resume_identity_mismatch_rejected = $true
    evidence_path_mismatch_rejected = $true
    validator_local_read_only = $true
} | ConvertTo-Json -Compress
