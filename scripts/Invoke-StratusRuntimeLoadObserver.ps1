[CmdletBinding()]
param(
    [string] $Root = '.',

    [ValidateSet(390)]
    [int] $DurationSeconds = 390,

    [ValidateSet(1)]
    [int] $DatabaseIntervalSeconds = 1,

    [ValidateSet(5)]
    [int] $ResourceIntervalSeconds = 5,

    [ValidateSet(5)]
    [int] $DrainSeconds = 5,

    [ValidateSet('20260730_0046')]
    [string] $ExpectedSchemaHead = '20260730_0046',

    [ValidateSet(4)]
    [int] $MaxApiSessions = 4,

    [ValidatePattern('^[0-9a-fA-F]{32}$')]
    [string] $LoadGateRunId,

    [ValidatePattern('^sha256:[0-9a-f]{64}$')]
    [string] $ExpectedApiImageId,

    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedRevision,

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedReleaseDigest,

    [string] $ReadyPath,

    [string] $OutputPath = '.stratus-cache\load-gates\candidate-runtime.json',

    [switch] $ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$validation = [pscustomobject]@{
    schema_version = 2
    gate = 'stratus_dashboard_runtime_observation'
    validated = $true
    read_only = $true
    duration_seconds = $DurationSeconds
    database_interval_seconds = $DatabaseIntervalSeconds
    resource_interval_seconds = $ResourceIntervalSeconds
    drain_seconds = $DrainSeconds
    expected_schema_head = $ExpectedSchemaHead
    max_api_sessions = $MaxApiSessions
    duration_covers_worst_case_http_gate = $DurationSeconds -ge 390
    requires_shared_run_id = $true
    requires_exact_api_identity = $true
    requires_ready_marker = $true
    thresholds = [pscustomobject]@{
        api_idle_in_transaction = 0
        global_idle_in_transaction = 0
        lock_waiters = 0
        api_memory_percent_lt = 90
        postgres_cpu_single_percent_lt = 200
        postgres_cpu_two_consecutive_percent_lt = 150
        postgres_cpu_rolling_30s_percent_lt = 140
        restarts = 0
        oom_events = 0
        pending_commands = 0
        live_rows = 0
    }
}
if ($ValidateOnly) {
    $validation | ConvertTo-Json -Depth 6 -Compress
    return
}

function Convert-Percent {
    param([Parameter(Mandatory = $true)][string] $Value)

    [double]::Parse(
        $Value.Trim().TrimEnd('%'),
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function Get-Maximum {
    param(
        [Parameter(Mandatory = $true)][object[]] $Rows,
        [Parameter(Mandatory = $true)][string] $Property
    )

    if ($Rows.Count -eq 0) {
        return $null
    }
    return ($Rows | Measure-Object -Property $Property -Maximum).Maximum
}

function Invoke-DatabaseSnapshot {
    param([Parameter(Mandatory = $true)][string] $PostgresContainer)

    $sql = @"
SELECT json_build_object(
  'at', clock_timestamp(),
  'api_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database() AND application_name='stratus-api'
  ),
  'api_active', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database()
      AND application_name='stratus-api' AND state='active'
  ),
  'api_idle_in_tx', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database()
      AND application_name='stratus-api'
      AND state LIKE 'idle in transaction%'
  ),
  'runtime_writer_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database()
      AND application_name LIKE 'stratus-%'
      AND application_name <> 'stratus-api'
  ),
  'global_idle_in_tx', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database() AND state LIKE 'idle in transaction%'
  ),
  'max_api_idle_tx_seconds', (
    SELECT COALESCE(
      max(extract(epoch FROM clock_timestamp() - xact_start)),
      0
    )
    FROM pg_stat_activity
    WHERE datname=current_database()
      AND application_name='stratus-api'
      AND state LIKE 'idle in transaction%'
  ),
  'lock_waiters', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database() AND wait_event_type='Lock'
  ),
  'oldest_api_query_seconds', (
    SELECT COALESCE(
      max(extract(epoch FROM clock_timestamp() - query_start)),
      0
    )
    FROM pg_stat_activity
    WHERE datname=current_database()
      AND application_name='stratus-api' AND state='active'
  ),
  'schema_head', (SELECT version_num FROM alembic_version),
  'controls_ok', (
    (SELECT mode::text='paper' AND kill_switch FROM runtime_control WHERE id=1)
    AND (
      SELECT enabled AND mode::text='paper' AND kill_switch
      FROM venue_runtime_control WHERE venue::text='polymarket'
    )
    AND (
      SELECT enabled AND mode::text='backtest' AND kill_switch
      FROM venue_runtime_control WHERE venue::text='binance_spot'
    )
  ),
  'pending_commands', (
    SELECT count(*) FROM operator_commands WHERE status='pending'
  ),
  'live_orders', (SELECT count(*) FROM orders WHERE mode::text='live'),
  'live_positions', (SELECT count(*) FROM positions WHERE mode::text='live'),
  'live_intents', (
    SELECT count(*) FROM execution_order_intents WHERE mode::text='live'
  ),
  'live_fill_legs', (
    SELECT count(*) FROM execution_fill_legs WHERE mode::text='live'
  ),
  'live_enrollments', (
    SELECT count(*) FROM prospective_validation_enrollments WHERE mode::text='live'
  )
)::text;
"@
    $raw = $sql |
        docker exec -i $PostgresContainer sh -lc `
            'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'
    if ($LASTEXITCODE -ne 0) {
        throw 'Read-only PostgreSQL runtime sample failed.'
    }
    return (($raw -join [Environment]::NewLine) | ConvertFrom-Json)
}

function Get-ResourceSnapshot {
    param(
        [Parameter(Mandatory = $true)][string] $ApiContainer,
        [Parameter(Mandatory = $true)][string] $PostgresContainer
    )

    $stats = @{}
    docker stats --no-stream --format '{{json .}}' $ApiContainer $PostgresContainer |
        ForEach-Object {
            $item = $_ | ConvertFrom-Json
            $stats[$item.Name] = $item
        }
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker resource sample failed.'
    }
    if (
        -not $stats.ContainsKey($ApiContainer) -or
        -not $stats.ContainsKey($PostgresContainer)
    ) {
        throw 'Docker resource sample omitted a required container.'
    }
    return [pscustomobject]@{
        at = [DateTimeOffset]::UtcNow.ToString('O')
        api_cpu_percent = Convert-Percent $stats[$ApiContainer].CPUPerc
        api_memory_percent = Convert-Percent $stats[$ApiContainer].MemPerc
        postgres_cpu_percent = Convert-Percent $stats[$PostgresContainer].CPUPerc
        postgres_memory_percent = Convert-Percent $stats[$PostgresContainer].MemPerc
    }
}

function Write-ObserverArtifact {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][object] $Artifact
    )

    $directory = [IO.Path]::GetDirectoryName($Path)
    [void][IO.Directory]::CreateDirectory($directory)
    $temporaryOutput = "$Path.tmp"
    [IO.File]::WriteAllText(
        $temporaryOutput,
        ($Artifact | ConvertTo-Json -Depth 8),
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporaryOutput -Destination $Path -Force
}

$startedAt = [DateTimeOffset]::UtcNow
$absoluteOutput = $null
$absoluteReady = $null
$runId = if ([string]::IsNullOrWhiteSpace($LoadGateRunId)) {
    [guid]::NewGuid().ToString('N')
}
else {
    $LoadGateRunId.ToLowerInvariant()
}
trap {
    $fatal = [pscustomobject]@{
        schema_version = 2
        gate = 'stratus_dashboard_runtime_observation'
        runtime_passed = $false
        load_gate_run_id = $runId
        started_at = $startedAt.ToString('O')
        completed_at = [DateTimeOffset]::UtcNow.ToString('O')
        fatal_error = $_.Exception.GetBaseException().Message
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$absoluteOutput)) {
        try {
            Write-ObserverArtifact -Path $absoluteOutput -Artifact $fatal
        }
        catch {
            [Console]::Error.WriteLine(
                "Runtime observer could not write its fatal artifact: $($_.Exception.Message)"
            )
        }
    }
    $fatal | ConvertTo-Json -Compress
    exit 2
}

$absoluteRoot = [IO.Path]::GetFullPath($Root)
$absoluteOutput = if ([IO.Path]::IsPathRooted($OutputPath)) {
    [IO.Path]::GetFullPath($OutputPath)
}
else {
    [IO.Path]::GetFullPath((Join-Path $absoluteRoot $OutputPath))
}
if ([string]::IsNullOrWhiteSpace($ReadyPath)) {
    throw 'A unique runtime-observer ready-marker path is required.'
}
$absoluteReady = if ([IO.Path]::IsPathRooted($ReadyPath)) {
    [IO.Path]::GetFullPath($ReadyPath)
}
else {
    [IO.Path]::GetFullPath((Join-Path $absoluteRoot $ReadyPath))
}
Remove-Item -LiteralPath $absoluteOutput -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$absoluteOutput.tmp" -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $absoluteReady -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$absoluteReady.tmp" -Force -ErrorAction SilentlyContinue
if (
    [string]::IsNullOrWhiteSpace($LoadGateRunId) -or
    [string]::IsNullOrWhiteSpace($ReadyPath) -or
    [string]::IsNullOrWhiteSpace($ExpectedApiImageId) -or
    [string]::IsNullOrWhiteSpace($ExpectedRevision) -or
    [string]::IsNullOrWhiteSpace($ExpectedReleaseDigest)
) {
    throw 'Shared run ID and exact API image identity are required.'
}
Set-Location -LiteralPath $absoluteRoot
$unexpectedServices = @(
    docker compose ps --status running --services |
        Where-Object { $_ -in @('worker', 'telegram', 'binance-market-stream') }
)
if ($unexpectedServices.Count -ne 0) {
    throw "Runtime gate requires application writers stopped: $($unexpectedServices -join ', ')"
}
$apiIds = @(docker compose ps -q api)
$postgresIds = @(docker compose ps -q postgres)
if ($apiIds.Count -ne 1 -or $postgresIds.Count -ne 1) {
    throw 'Runtime gate requires exactly one running Compose API and PostgreSQL container.'
}
$baselineApi = @((docker inspect $apiIds[0] | ConvertFrom-Json))[0]
$baselinePostgres = @((docker inspect $postgresIds[0] | ConvertFrom-Json))[0]
if (-not $baselineApi.State.Running -or -not $baselinePostgres.State.Running) {
    throw 'Runtime gate requires running API and PostgreSQL containers.'
}
if (
    [string]$baselineApi.State.Health.Status -ne 'healthy' -or
    [string]$baselinePostgres.State.Health.Status -ne 'healthy'
) {
    throw 'Runtime gate requires healthy API and PostgreSQL containers.'
}
if (
    $baselineApi.Image -ne $ExpectedApiImageId -or
    $baselineApi.Config.Labels.'org.opencontainers.image.revision' -ne $ExpectedRevision -or
    @(
        $baselineApi.Config.Env |
            Where-Object { $_ -eq "RELEASE_IMAGE_DIGEST=$ExpectedReleaseDigest" }
    ).Count -ne 1
) {
    throw 'Runtime gate API identity does not match the exact candidate.'
}
$apiName = ([string]$baselineApi.Name).TrimStart('/')
$postgresName = ([string]$baselinePostgres.Name).TrimStart('/')
$databaseSamples = [Collections.Generic.List[object]]::new()
$resourceSamples = [Collections.Generic.List[object]]::new()
$sampleErrors = [Collections.Generic.List[object]]::new()
$observationStartedAt = [DateTimeOffset]::UtcNow
$deadline = $observationStartedAt.AddSeconds($DurationSeconds)
$nextResourceAt = $observationStartedAt
$readyWritten = $false

while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $sampleStarted = [DateTimeOffset]::UtcNow
    try {
        $databaseSamples.Add((Invoke-DatabaseSnapshot -PostgresContainer $postgresName))
    }
    catch {
        $sampleErrors.Add([pscustomobject]@{
            at = [DateTimeOffset]::UtcNow.ToString('O')
            source = 'database'
            error = $_.Exception.GetBaseException().Message
        })
    }

    if ([DateTimeOffset]::UtcNow -ge $nextResourceAt) {
        try {
            $resourceSamples.Add(
                (Get-ResourceSnapshot -ApiContainer $apiName -PostgresContainer $postgresName)
            )
        }
        catch {
            $sampleErrors.Add([pscustomobject]@{
                at = [DateTimeOffset]::UtcNow.ToString('O')
                source = 'docker'
                error = $_.Exception.GetBaseException().Message
            })
        }
        $nextResourceAt = [DateTimeOffset]::UtcNow.AddSeconds($ResourceIntervalSeconds)
    }

    if (
        -not $readyWritten -and
        $databaseSamples.Count -gt 0 -and
        $resourceSamples.Count -gt 0
    ) {
        $firstDatabaseSample = $databaseSamples[0]
        $firstResourceSample = $resourceSamples[0]
        if (
            [string]$firstDatabaseSample.schema_head -ne $ExpectedSchemaHead -or
            -not [bool]$firstDatabaseSample.controls_ok -or
            [int]$firstDatabaseSample.api_sessions -gt $MaxApiSessions -or
            [int]$firstDatabaseSample.api_idle_in_tx -ne 0 -or
            [int]$firstDatabaseSample.global_idle_in_tx -ne 0 -or
            [int]$firstDatabaseSample.lock_waiters -ne 0 -or
            [int]$firstDatabaseSample.pending_commands -ne 0 -or
            [int]$firstDatabaseSample.live_orders -ne 0 -or
            [int]$firstDatabaseSample.live_positions -ne 0 -or
            [int]$firstDatabaseSample.live_intents -ne 0 -or
            [int]$firstDatabaseSample.live_fill_legs -ne 0 -or
            [int]$firstDatabaseSample.live_enrollments -ne 0 -or
            [int]$firstDatabaseSample.runtime_writer_sessions -ne 0 -or
            [double]$firstResourceSample.api_memory_percent -ge 90 -or
            [double]$firstResourceSample.postgres_cpu_percent -ge 200
        ) {
            throw 'Runtime observer preflight is not fail-closed, writer-free, and contention-free.'
        }
        $readyMarker = [pscustomobject]@{
            schema_version = 1
            gate = 'stratus_dashboard_runtime_observation_ready'
            load_gate_run_id = $runId
            ready_at = [DateTimeOffset]::UtcNow.ToString('O')
            first_database_sample_at = [string]$firstDatabaseSample.at
            first_resource_sample_at = [string]$resourceSamples[0].at
            api_image_id = $ExpectedApiImageId
            revision = $ExpectedRevision
            release_digest = $ExpectedReleaseDigest
            preflight_safe = $true
            database_preflight = [pscustomobject]@{
                api_sessions = [int]$firstDatabaseSample.api_sessions
                api_idle_in_tx = [int]$firstDatabaseSample.api_idle_in_tx
                global_idle_in_tx = [int]$firstDatabaseSample.global_idle_in_tx
                lock_waiters = [int]$firstDatabaseSample.lock_waiters
                runtime_writer_sessions = [int]$firstDatabaseSample.runtime_writer_sessions
            }
            resource_preflight = [pscustomobject]@{
                api_memory_percent = [double]$firstResourceSample.api_memory_percent
                postgres_cpu_percent = [double]$firstResourceSample.postgres_cpu_percent
            }
        }
        Write-ObserverArtifact -Path $absoluteReady -Artifact $readyMarker
        $readyWritten = $true
    }

    $remaining = $DatabaseIntervalSeconds - (
        [DateTimeOffset]::UtcNow - $sampleStarted
    ).TotalSeconds
    if ($remaining -gt 0 -and [DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds ([int]($remaining * 1000))
    }
}

Start-Sleep -Seconds $DrainSeconds
$drain = Invoke-DatabaseSnapshot -PostgresContainer $postgresName
$finalApi = @((docker inspect $apiName | ConvertFrom-Json))[0]
$finalPostgres = @((docker inspect $postgresName | ConvertFrom-Json))[0]
$dbRows = @($databaseSamples)
$resourceRows = @($resourceSamples)
$pgCpu = @($resourceRows | ForEach-Object { [double]$_.postgres_cpu_percent })
$twoAt150 = $false
for ($index = 1; $index -lt $pgCpu.Count; $index += 1) {
    if ($pgCpu[$index - 1] -ge 150 -and $pgCpu[$index] -ge 150) {
        $twoAt150 = $true
        break
    }
}
$rolling30Max = 0.0
if ($pgCpu.Count -ge 7) {
    for ($index = 6; $index -lt $pgCpu.Count; $index += 1) {
        $window = @($pgCpu[($index - 6)..$index])
        $average = [double](($window | Measure-Object -Average).Average)
        $rolling30Max = [Math]::Max($rolling30Max, $average)
    }
}

$identityChanged = (
    $finalApi.Id -ne $baselineApi.Id -or
    $finalPostgres.Id -ne $baselinePostgres.Id
)
$restartChanged = (
    [int]$finalApi.RestartCount -ne [int]$baselineApi.RestartCount -or
    [int]$finalPostgres.RestartCount -ne [int]$baselinePostgres.RestartCount
)
$oomObserved = [bool]$finalApi.State.OOMKilled -or [bool]$finalPostgres.State.OOMKilled
$containersHealthy = (
    [bool]$finalApi.State.Running -and
    [bool]$finalPostgres.State.Running -and
    [string]$finalApi.State.Health.Status -eq 'healthy' -and
    [string]$finalPostgres.State.Health.Status -eq 'healthy'
)
$candidateIdentityHeld = (
    $finalApi.Image -eq $ExpectedApiImageId -and
    $finalApi.Config.Labels.'org.opencontainers.image.revision' -eq $ExpectedRevision -and
    @(
        $finalApi.Config.Env |
            Where-Object { $_ -eq "RELEASE_IMAGE_DIGEST=$ExpectedReleaseDigest" }
    ).Count -eq 1
)
$finalUnexpectedServices = @(
    docker compose ps --status running --services |
        Where-Object { $_ -in @('worker', 'telegram', 'binance-market-stream') }
)
$invariantFailures = @(
    $dbRows | Where-Object {
        [string]$_.schema_head -ne $ExpectedSchemaHead -or
        -not [bool]$_.controls_ok -or
        [int]$_.pending_commands -ne 0 -or
        [int]$_.live_orders -ne 0 -or
        [int]$_.live_positions -ne 0 -or
        [int]$_.live_intents -ne 0 -or
        [int]$_.live_fill_legs -ne 0 -or
        [int]$_.live_enrollments -ne 0 -or
        [int]$_.runtime_writer_sessions -ne 0
    }
)
$violations = [Collections.Generic.List[string]]::new()
if ($sampleErrors.Count -ne 0) { $violations.Add('sample_error') }
if ($dbRows.Count -eq 0) { $violations.Add('no_database_samples') }
if ($resourceRows.Count -eq 0) { $violations.Add('no_resource_samples') }
if (-not $readyWritten) { $violations.Add('ready_marker_missing') }
$minimumDatabaseSamples = [Math]::Floor(
    ($DurationSeconds / $DatabaseIntervalSeconds) * 0.9
)
$minimumResourceSamples = [Math]::Floor(
    ($DurationSeconds / $ResourceIntervalSeconds) * 0.9
)
if ($dbRows.Count -lt $minimumDatabaseSamples) {
    $violations.Add('database_sample_coverage')
}
if ($resourceRows.Count -lt $minimumResourceSamples) {
    $violations.Add('resource_sample_coverage')
}
if ((Get-Maximum $dbRows 'api_sessions') -gt $MaxApiSessions) {
    $violations.Add('api_session_bound')
}
if ((Get-Maximum $dbRows 'api_idle_in_tx') -ne 0) {
    $violations.Add('api_idle_in_transaction')
}
if ((Get-Maximum $dbRows 'global_idle_in_tx') -ne 0) {
    $violations.Add('global_idle_in_transaction')
}
if ((Get-Maximum $dbRows 'lock_waiters') -ne 0) { $violations.Add('lock_waiter') }
if ((Get-Maximum $resourceRows 'api_memory_percent') -ge 90) {
    $violations.Add('api_memory')
}
if ((Get-Maximum $resourceRows 'postgres_cpu_percent') -ge 200) {
    $violations.Add('postgres_cpu_single')
}
if ($twoAt150) { $violations.Add('postgres_cpu_consecutive') }
if ($rolling30Max -ge 140) { $violations.Add('postgres_cpu_rolling') }
if ($identityChanged) { $violations.Add('container_identity_changed') }
if ($restartChanged) { $violations.Add('container_restart') }
if ($oomObserved) { $violations.Add('container_oom') }
if (-not $containersHealthy) { $violations.Add('container_not_healthy') }
if (-not $candidateIdentityHeld) { $violations.Add('candidate_identity_changed') }
if ($finalUnexpectedServices.Count -ne 0) {
    $violations.Add('application_writer_started')
}
if ($invariantFailures.Count -ne 0) { $violations.Add('runtime_invariant') }
$drainInvariantFailure = (
    [string]$drain.schema_head -ne $ExpectedSchemaHead -or
    -not [bool]$drain.controls_ok -or
    [int]$drain.pending_commands -ne 0 -or
    [int]$drain.live_orders -ne 0 -or
    [int]$drain.live_positions -ne 0 -or
    [int]$drain.live_intents -ne 0 -or
    [int]$drain.live_fill_legs -ne 0 -or
    [int]$drain.live_enrollments -ne 0 -or
    [int]$drain.runtime_writer_sessions -ne 0
)
if (
    [int]$drain.api_active -ne 0 -or
    [int]$drain.api_idle_in_tx -ne 0 -or
    [int]$drain.global_idle_in_tx -ne 0 -or
    [int]$drain.lock_waiters -ne 0 -or
    $drainInvariantFailure
) {
    $violations.Add('post_load_drain')
}

$summary = [pscustomobject]@{
    database_sample_count = $dbRows.Count
    resource_sample_count = $resourceRows.Count
    minimum_database_samples = $minimumDatabaseSamples
    minimum_resource_samples = $minimumResourceSamples
    sample_errors = $sampleErrors.Count
    max_api_sessions = Get-Maximum $dbRows 'api_sessions'
    max_api_active = Get-Maximum $dbRows 'api_active'
    max_runtime_writer_sessions = Get-Maximum $dbRows 'runtime_writer_sessions'
    max_api_idle_in_tx = Get-Maximum $dbRows 'api_idle_in_tx'
    max_global_idle_in_tx = Get-Maximum $dbRows 'global_idle_in_tx'
    max_api_idle_tx_seconds = Get-Maximum $dbRows 'max_api_idle_tx_seconds'
    max_lock_waiters = Get-Maximum $dbRows 'lock_waiters'
    max_oldest_api_query_seconds = Get-Maximum $dbRows 'oldest_api_query_seconds'
    max_api_cpu_percent = Get-Maximum $resourceRows 'api_cpu_percent'
    max_api_memory_percent = Get-Maximum $resourceRows 'api_memory_percent'
    max_postgres_cpu_percent = Get-Maximum $resourceRows 'postgres_cpu_percent'
    rolling_30s_postgres_cpu_max = $rolling30Max
    consecutive_postgres_cpu_150 = $twoAt150
    identity_changed = $identityChanged
    restart_changed = $restartChanged
    oom_observed = $oomObserved
    containers_healthy = $containersHealthy
    candidate_identity_held = $candidateIdentityHeld
    application_writers_stopped = $finalUnexpectedServices.Count -eq 0
    invariants_changed = $invariantFailures.Count -ne 0
    drain_passed = (
        [int]$drain.api_active -eq 0 -and
        [int]$drain.api_idle_in_tx -eq 0 -and
        [int]$drain.global_idle_in_tx -eq 0 -and
        [int]$drain.lock_waiters -eq 0 -and
        -not $drainInvariantFailure
    )
    runtime_passed = $violations.Count -eq 0
}
$result = [pscustomobject]@{
    schema_version = 2
    gate = 'stratus_dashboard_runtime_observation'
    load_gate_run_id = $runId
    started_at = $startedAt.ToString('O')
    observation_started_at = $observationStartedAt.ToString('O')
    completed_at = [DateTimeOffset]::UtcNow.ToString('O')
    duration_seconds = $DurationSeconds
    database_interval_seconds = $DatabaseIntervalSeconds
    resource_interval_seconds = $ResourceIntervalSeconds
    drain_seconds = $DrainSeconds
    expected_schema_head = $ExpectedSchemaHead
    maximum_api_sessions = $MaxApiSessions
    expected_api_image_id = $ExpectedApiImageId
    expected_revision = $ExpectedRevision
    expected_release_digest = $ExpectedReleaseDigest
    ready_marker = $absoluteReady
    baseline = [pscustomobject]@{
        api_container_id = $baselineApi.Id
        api_image_id = $baselineApi.Image
        api_restart_count = [int]$baselineApi.RestartCount
        postgres_container_id = $baselinePostgres.Id
        postgres_image_id = $baselinePostgres.Image
        postgres_restart_count = [int]$baselinePostgres.RestartCount
    }
    summary = $summary
    violations = @($violations)
    drain = $drain
    sample_errors = @($sampleErrors)
    database_samples = $dbRows
    resource_samples = $resourceRows
}
Write-ObserverArtifact -Path $absoluteOutput -Artifact $result
$summary | ConvertTo-Json -Compress
if (-not $summary.runtime_passed) {
    exit 1
}
