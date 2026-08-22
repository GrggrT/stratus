[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\Invoke-StratusRuntimeLoadObserver.ps1')
)
$tokens = $null
$parseErrors = $null
[void][Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    $details = $parseErrors |
        ForEach-Object { "$($_.Extent.StartLineNumber): $($_.Message)" }
    throw "PowerShell syntax errors:`n$($details -join [Environment]::NewLine)"
}

$source = Get-Content -LiteralPath $scriptPath -Raw
foreach ($forbidden in @(
    'POSTGRES_PASSWORD\s*=',
    'Get-Content[^\r\n]+\.env',
    'Invoke-RestMethod',
    'Invoke-WebRequest',
    'docker\s+(start|stop|restart|rm|compose\s+up)',
    '\$nextResourceAt\s*=\s*\[DateTimeOffset\]::UtcNow\.AddSeconds',
    '\$sampleStarted\s*=\s*\[DateTimeOffset\]::UtcNow'
)) {
    if ($source -match $forbidden) {
        throw "Runtime observer contains a forbidden pattern: $forbidden"
    }
}
foreach ($required in @(
    'ValidateSet\(390\)',
    'ValidateSet\(4\)',
    "ValidateSet\('20260730_0047'\)",
    "ExpectedSchemaHead = '20260730_0047'",
    'load_gate_run_id = \$runId',
    'expected_api_image_id = \$ExpectedApiImageId',
    'ExpectedApiMemoryLimitBytes = 1073741824L',
    'baselineApi\.HostConfig\.Memory -ne \$ExpectedApiMemoryLimitBytes',
    'finalApi\.HostConfig\.Memory -eq \$ExpectedApiMemoryLimitBytes',
    'global_idle_in_tx -ne 0',
    'Sample\.api_sessions -le \$MaxApiSessions',
    'Sample\.api_idle_in_tx -eq 0',
    'Sample\.lock_waiters -eq 0',
    'Sample\.api_memory_percent -lt 90',
    'Sample\.api_memory_limit_bytes -eq \$ExpectedApiMemoryLimitBytes',
    'api_memory_limit_changed',
    'api_operator_key_entry_count',
    'api_operator_credential_changed',
    'Sample\.postgres_cpu_percent -lt 200',
    'candidate_identity_changed',
    'application_writer_started',
    'container_topology_changed',
    'runtime_writer_sessions',
    "backend_type='client backend'",
    'pid <> pg_backend_pid\(\)',
    'client_addr = ANY\(ARRAY\[\$apiAddressArray\]\)',
    'NetworkSettings\.Networks\.PSObject\.Properties',
    'Get-RunningProjectTopology',
    'docker ps --no-trunc',
    'StratusReleaseComposeArguments',
    "'--project-directory'",
    "'--env-file'",
    "'--project-name'",
    'reserved Compose override variable',
    '\{\{\.Networks\}\}',
    '\$_ -in \$AllowedNetworks',
    "allowedServices = @\('api', 'postgres'\)",
    'exact_project_topology_held',
    'topology_sample_coverage',
    'database_sample_coverage',
    'stratus_dashboard_runtime_observation_ready',
    'Write-ObserverArtifact -Path \$absoluteOutput -Artifact \$fatal',
    'Collections\.Concurrent\.ConcurrentQueue',
    'Get-NextAnchoredSampleAt',
    'Start-ObserverSampler',
    'Complete-ObserverSampler',
    'Stop-ObserverSamplerSafely',
    'Receive-ObserverSamplerMessages',
    '\$nextDatabaseAt = \$observationStartedAt',
    '\$nextSampleAt = \$ObservationStartedAt',
    '\$topologySamples\.Count -gt 1',
    '\$sampleErrors\.Count -ne 0',
    '\[bool\]\$resourceSampler\.async_result\.IsCompleted',
    '\[bool\]\$topologySampler\.async_result\.IsCompleted',
    'Test-DatabasePreflightSample',
    'Test-ResourcePreflightSample',
    'Test-TopologyPreflightSample',
    '\$unsafeDatabaseSamples\.Count -ne 0',
    '\$unsafeResourceSamples\.Count -ne 0',
    '\$unsafeTopologySamples\.Count -ne 0',
    'first_dynamic_topology_sample_at = \[string\]\$topologySamples\[1\]\.at',
    '\$pipeline\.BeginInvoke\(\)',
    '\$Sampler\.pipeline\.BeginStop\(\$null, \$null\)',
    '\.WaitOne\(5000\)',
    'finally\s*\{\s*Stop-ObserverSamplerSafely -Sampler \$resourceSampler\s*Stop-ObserverSamplerSafely -Sampler \$topologySampler'
)) {
    if ($source -notmatch $required) {
        throw "Runtime observer is missing a required evidence control: $required"
    }
}
if ($source -match "application_name LIKE 'stratus-%'") {
    throw 'Runtime observer still trusts application_name as a writer inventory.'
}
$topologyCallCount = @(
    [regex]::Matches(
        $source,
        'Get-RunningProjectTopology\s*(?:`\r?\n\s*)?-ProjectName'
    )
).Count
if ($topologyCallCount -lt 3) {
    throw 'Runtime observer does not prove project topology before, during, and after load.'
}
$independentSamplerNames = @(
    [regex]::Matches(
        $source,
        "Start-ObserverSampler\s+``\r?\n\s+-Name '(resource|topology)'"
    ) | ForEach-Object { $_.Groups[1].Value }
)
if (
    $independentSamplerNames.Count -ne 2 -or
    @($independentSamplerNames | Sort-Object -Unique).Count -ne 2 -or
    $independentSamplerNames -notcontains 'resource' -or
    $independentSamplerNames -notcontains 'topology'
) {
    throw 'Resource and topology probes must run in separate sampler runspaces.'
}
$runtimeRole = 'stratus_app_752875213a111f12d876fef68bbf8259'
$apiAddresses = @('172.20.0.4')
$sessionFixtures = @(
    [pscustomobject]@{
        name = 'candidate-api'
        user = $runtimeRole
        application = 'stratus-api'
        address = '172.20.0.4'
        expected_api = $true
    },
    [pscustomobject]@{
        name = 'spoofed-app-name-from-other-container'
        user = $runtimeRole
        application = 'stratus-api'
        address = '172.20.0.99'
        expected_api = $false
    },
    [pscustomobject]@{
        name = 'unlabelled-psql'
        user = 'stratus'
        application = 'psql'
        address = '172.20.0.99'
        expected_api = $false
    },
    [pscustomobject]@{
        name = 'alembic-with-empty-app-name'
        user = 'stratus'
        application = ''
        address = '172.20.0.99'
        expected_api = $false
    }
)
foreach ($session in $sessionFixtures) {
    $isExpectedApi = (
        $session.user -ceq $runtimeRole -and
        $session.application -ceq 'stratus-api' -and
        $session.address -in $apiAddresses
    )
    if ($isExpectedApi -ne [bool]$session.expected_api) {
        throw "Runtime backend classification fixture failed: $($session.name)."
    }
}
if (@($sessionFixtures | Where-Object { -not [bool]$_.expected_api }).Count -ne 3) {
    throw 'Runtime backend negative inventory is incomplete.'
}

$hostExecutable = (Get-Process -Id $PID).Path
$output = & $hostExecutable `
    -NoLogo `
    -NoProfile `
    -NonInteractive `
    -File $scriptPath `
    -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw "Runtime observer validation mode exited with $LASTEXITCODE."
}
$validation = ($output -join [Environment]::NewLine) | ConvertFrom-Json
if (
    $validation.schema_version -ne 2 -or
    $validation.gate -ne 'stratus_dashboard_runtime_observation' -or
    -not $validation.validated -or
    -not $validation.read_only -or
    $validation.duration_seconds -ne 390 -or
    $validation.database_interval_seconds -ne 1 -or
    $validation.resource_interval_seconds -ne 5 -or
    $validation.drain_seconds -ne 5 -or
    $validation.expected_schema_head -ne '20260730_0047' -or
    $validation.max_api_sessions -ne 4 -or
    -not $validation.duration_covers_worst_case_http_gate -or
    -not $validation.requires_shared_run_id -or
    -not $validation.requires_exact_api_identity -or
    -not $validation.requires_ready_marker -or
    -not $validation.requires_exhaustive_project_topology -or
    -not $validation.counts_all_other_client_backends
) {
    throw 'Runtime observer validation contract is unsafe or incomplete.'
}
if (
    $validation.thresholds.api_idle_in_transaction -ne 0 -or
    $validation.thresholds.global_idle_in_transaction -ne 0 -or
    $validation.thresholds.lock_waiters -ne 0 -or
    $validation.thresholds.live_rows -ne 0
) {
    throw 'Runtime observer validation thresholds were relaxed.'
}

$null = . $scriptPath -ValidateOnly
$safeDatabasePreflight = [pscustomobject]@{
    schema_head = '20260730_0047'
    controls_ok = $true
    api_sessions = 3
    api_idle_in_tx = 0
    global_idle_in_tx = 0
    lock_waiters = 0
    pending_commands = 0
    live_orders = 0
    live_positions = 0
    live_intents = 0
    live_fill_legs = 0
    live_enrollments = 0
    runtime_writer_sessions = 0
}
$unsafeDatabasePreflight = $safeDatabasePreflight | Select-Object *
$unsafeDatabasePreflight.api_idle_in_tx = 1
$databasePreflightRows = @($safeDatabasePreflight, $unsafeDatabasePreflight)
$rejectedDatabasePreflight = @(
    $databasePreflightRows | Where-Object {
        -not (Test-DatabasePreflightSample `
            -Sample $_ `
            -ExpectedSchemaHead '20260730_0047' `
            -MaxApiSessions 4)
    }
)

$safeResourcePreflight = [pscustomobject]@{
    api_memory_percent = 20.0
    api_memory_limit_bytes = 1073741824L
    api_operator_key_entry_count = 1
    postgres_cpu_percent = 30.0
}
$unsafeResourcePreflight = $safeResourcePreflight | Select-Object *
$unsafeResourcePreflight.postgres_cpu_percent = 210.0
$resourcePreflightRows = @($safeResourcePreflight, $unsafeResourcePreflight)
$rejectedResourcePreflight = @(
    $resourcePreflightRows | Where-Object {
        -not (Test-ResourcePreflightSample `
            -Sample $_ `
            -ExpectedApiMemoryLimitBytes 1073741824L)
    }
)

$topologyPreflightRows = @(
    [pscustomobject]@{ safe = $true },
    [pscustomobject]@{ safe = $false }
)
$rejectedTopologyPreflight = @(
    $topologyPreflightRows | Where-Object {
        -not (Test-TopologyPreflightSample -Sample $_)
    }
)
if (
    $rejectedDatabasePreflight.Count -ne 1 -or
    $rejectedResourcePreflight.Count -ne 1 -or
    $rejectedTopologyPreflight.Count -ne 1
) {
    throw 'Cumulative preflight validation ignored a later unsafe sample.'
}
$anchor = [DateTimeOffset]::Parse('2026-08-23T00:00:00Z')
$nextAnchored = Get-NextAnchoredSampleAt `
    -PreviousAt $anchor `
    -Now $anchor.AddSeconds(3.4) `
    -IntervalSeconds 1
if ($nextAnchored -ne $anchor.AddSeconds(4)) {
    throw 'Delayed sampling did not advance to the next absolute anchored slot.'
}
$rollingRows = @(
    [pscustomobject]@{ at = $anchor.ToString('O'); postgres_cpu_percent = 0.0 }
    1..6 | ForEach-Object {
        [pscustomobject]@{
            at = $anchor.AddSeconds(30 + $_).ToString('O')
            postgres_cpu_percent = 145.0
        }
    }
)
$rollingMaximum = Get-TimestampedRollingMaximum `
    -Rows $rollingRows `
    -TimestampProperty 'at' `
    -ValueProperty 'postgres_cpu_percent' `
    -WindowSeconds 30
if ($rollingMaximum -ne 145.0) {
    throw 'Rolling PostgreSQL CPU calculation still assumes seven adjacent samples.'
}

$samplerQueue = [Collections.Concurrent.ConcurrentQueue[object]]::new()
$samplerErrors = [Collections.Generic.List[object]]::new()
$testNetworks = [string[]]@('weatherpoly_default', 'weatherpoly_safety-plane')
$testComposeArguments = [object[]]@('compose', '--project-name', 'weatherpoly')
$samplerWorker = {
    param(
        [object] $Queue,
        [DateTimeOffset] $StartedAt,
        [string[]] $Networks,
        [object[]] $ComposeArguments
    )

    $nextAt = Get-NextAnchoredSampleAt `
        -PreviousAt $StartedAt `
        -Now $StartedAt.AddSeconds(3.4) `
        -IntervalSeconds 1
    $Queue.Enqueue([pscustomobject]@{
        kind = 'sample'
        source = 'resource'
        value = [pscustomobject]@{
            at = $nextAt.ToString('O')
            networks = @($Networks)
            compose_arguments = @($ComposeArguments)
        }
    })
}
$sampler = Start-ObserverSampler `
    -Name 'test' `
    -FunctionNames @('Get-NextAnchoredSampleAt') `
    -WorkerScript $samplerWorker `
    -Arguments @(
        $samplerQueue,
        $anchor,
        $testNetworks,
        $testComposeArguments
    )
Complete-ObserverSampler -Sampler $sampler -Errors $samplerErrors
$samplerMessage = $null
if (
    $samplerErrors.Count -ne 0 -or
    -not $samplerQueue.TryDequeue([ref]$samplerMessage) -or
    [string]$samplerMessage.source -ne 'resource' -or
    [DateTimeOffset]::Parse([string]$samplerMessage.value.at) -ne $anchor.AddSeconds(4) -or
    (@($samplerMessage.value.networks) -join ',') -cne ($testNetworks -join ',') -or
    (@($samplerMessage.value.compose_arguments) -join ',') -cne
        ($testComposeArguments -join ',')
) {
    throw 'Cross-runspace anchored sampler transport or array binding failed.'
}

[pscustomobject]@{
    script = $scriptPath
    syntax_valid = $true
    read_only = $true
    one_second_database_sampling = $true
    five_second_resource_sampling = $true
    independent_sampling = $true
    absolute_deadline_sampling = $true
    post_load_drain_required = $true
    validated = $true
} | ConvertTo-Json -Compress
