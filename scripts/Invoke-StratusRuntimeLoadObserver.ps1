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

    [ValidateSet('20260730_0047')]
    [string] $ExpectedSchemaHead = '20260730_0047',

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
$ExpectedApiMemoryLimitBytes = 1073741824L

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
    requires_exhaustive_project_topology = $true
    counts_all_other_client_backends = $true
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

function Test-DatabasePreflightSample {
    param(
        [Parameter(Mandatory = $true)][object] $Sample,
        [Parameter(Mandatory = $true)][string] $ExpectedSchemaHead,
        [Parameter(Mandatory = $true)][ValidateRange(1, 100)][int] $MaxApiSessions
    )

    return (
        [string]$Sample.schema_head -ceq $ExpectedSchemaHead -and
        $Sample.controls_ok -is [bool] -and
        [bool]$Sample.controls_ok -and
        [int]$Sample.api_sessions -le $MaxApiSessions -and
        [int]$Sample.api_idle_in_tx -eq 0 -and
        [int]$Sample.global_idle_in_tx -eq 0 -and
        [int]$Sample.lock_waiters -eq 0 -and
        [int]$Sample.pending_commands -eq 0 -and
        [int]$Sample.live_orders -eq 0 -and
        [int]$Sample.live_positions -eq 0 -and
        [int]$Sample.live_intents -eq 0 -and
        [int]$Sample.live_fill_legs -eq 0 -and
        [int]$Sample.live_enrollments -eq 0 -and
        [int]$Sample.runtime_writer_sessions -eq 0
    )
}

function Test-ResourcePreflightSample {
    param(
        [Parameter(Mandatory = $true)][object] $Sample,
        [Parameter(Mandatory = $true)][int64] $ExpectedApiMemoryLimitBytes
    )

    return (
        [double]$Sample.api_memory_percent -lt 90 -and
        [int64]$Sample.api_memory_limit_bytes -eq $ExpectedApiMemoryLimitBytes -and
        [int]$Sample.api_operator_key_entry_count -eq 1 -and
        [double]$Sample.postgres_cpu_percent -lt 200
    )
}

function Test-TopologyPreflightSample {
    param([Parameter(Mandatory = $true)][object] $Sample)

    return ($Sample.safe -is [bool] -and [bool]$Sample.safe)
}

function Get-TimestampedRollingMaximum {
    param(
        [Parameter(Mandatory = $true)][object[]] $Rows,
        [Parameter(Mandatory = $true)][string] $TimestampProperty,
        [Parameter(Mandatory = $true)][string] $ValueProperty,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3600)][int] $WindowSeconds
    )

    if ($Rows.Count -eq 0) {
        return 0.0
    }
    $times = @(
        $Rows | ForEach-Object {
            [DateTimeOffset]::Parse(
                [string]$_.$TimestampProperty,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            )
        }
    )
    $maximum = 0.0
    for ($endIndex = 0; $endIndex -lt $Rows.Count; $endIndex += 1) {
        if (($times[$endIndex] - $times[0]).TotalSeconds -lt $WindowSeconds) {
            continue
        }
        $windowStartedAt = $times[$endIndex].AddSeconds(-$WindowSeconds)
        $values = @(
            for ($index = 0; $index -le $endIndex; $index += 1) {
                if ($times[$index] -ge $windowStartedAt) {
                    [double]$Rows[$index].$ValueProperty
                }
            }
        )
        if ($values.Count -ne 0) {
            $average = [double](($values | Measure-Object -Average).Average)
            $maximum = [Math]::Max($maximum, $average)
        }
    }
    return $maximum
}

function Invoke-DatabaseSnapshot {
    param(
        [Parameter(Mandatory = $true)][string] $PostgresContainer,
        [Parameter(Mandatory = $true)][string[]] $ApiClientAddresses
    )

    $apiAddressLiterals = @(
        $ApiClientAddresses | ForEach-Object {
            $parsedAddress = $null
            if (-not [Net.IPAddress]::TryParse([string]$_, [ref]$parsedAddress)) {
                throw "Invalid candidate API client address: $_"
            }
            "'$($parsedAddress.ToString())'::inet"
        }
    )
    if ($apiAddressLiterals.Count -eq 0) {
        throw 'Candidate API has no PostgreSQL client address allowlist.'
    }
    $apiAddressArray = $apiAddressLiterals -join ','

    $sql = @"
SELECT json_build_object(
  'at', clock_timestamp(),
  'api_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database()
      AND backend_type='client backend'
      AND usename=('stratus_app_' || md5(current_database()))
      AND application_name='stratus-api'
      AND client_addr = ANY(ARRAY[$apiAddressArray])
  ),
  'api_active', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database()
      AND backend_type='client backend'
      AND usename=('stratus_app_' || md5(current_database()))
      AND application_name='stratus-api'
      AND client_addr = ANY(ARRAY[$apiAddressArray])
      AND state='active'
  ),
  'api_idle_in_tx', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database()
      AND backend_type='client backend'
      AND usename=('stratus_app_' || md5(current_database()))
      AND application_name='stratus-api'
      AND client_addr = ANY(ARRAY[$apiAddressArray])
      AND state LIKE 'idle in transaction%'
  ),
  'runtime_writer_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE datname=current_database()
      AND pid <> pg_backend_pid()
      AND backend_type='client backend'
      AND NOT (
        usename=('stratus_app_' || md5(current_database()))
        AND application_name='stratus-api'
        AND client_addr = ANY(ARRAY[$apiAddressArray])
      )
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
      AND backend_type='client backend'
      AND usename=('stratus_app_' || md5(current_database()))
      AND application_name='stratus-api'
      AND client_addr = ANY(ARRAY[$apiAddressArray])
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
      AND backend_type='client backend'
      AND usename=('stratus_app_' || md5(current_database()))
      AND application_name='stratus-api'
      AND client_addr = ANY(ARRAY[$apiAddressArray])
      AND state='active'
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
    $apiInspectRows = @((docker inspect $ApiContainer | ConvertFrom-Json))
    if ($LASTEXITCODE -ne 0 -or $apiInspectRows.Count -ne 1) {
        throw 'Docker API memory-limit sample failed.'
    }
    return [pscustomobject]@{
        at = [DateTimeOffset]::UtcNow.ToString('O')
        api_cpu_percent = Convert-Percent $stats[$ApiContainer].CPUPerc
        api_memory_percent = Convert-Percent $stats[$ApiContainer].MemPerc
        api_memory_limit_bytes = [int64]$apiInspectRows[0].HostConfig.Memory
        api_operator_key_entry_count = @(
            $apiInspectRows[0].Config.Env | Where-Object {
                [string]$_ -imatch '^OPERATOR_API_KEY='
            }
        ).Count
        postgres_cpu_percent = Convert-Percent $stats[$PostgresContainer].CPUPerc
        postgres_memory_percent = Convert-Percent $stats[$PostgresContainer].MemPerc
    }
}

function Get-RunningProjectTopology {
    param(
        [Parameter(Mandatory = $true)][string] $ProjectName,
        [Parameter(Mandatory = $true)][string] $ApiContainerId,
        [Parameter(Mandatory = $true)][string] $PostgresContainerId,
        [Parameter(Mandatory = $true)][string[]] $AllowedNetworks
    )

    $allowedServices = @('api', 'postgres')
    $composeServices = @(
        & docker @script:StratusReleaseComposeArguments `
            ps --status running --services |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose running-service inventory failed.'
    }

    $format = (
        '{{.ID}}|{{.Label "com.docker.compose.project"}}|' +
        '{{.Label "com.docker.compose.service"}}|' +
        '{{.Label "com.docker.compose.oneoff"}}|{{.Names}}|{{.Networks}}'
    )
    $runningRows = @(
        docker ps --no-trunc `
            --format $format
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker running-container project inventory failed.'
    }

    $allowedById = @{
        $ApiContainerId = 'api'
        $PostgresContainerId = 'postgres'
    }
    $containers = [Collections.Generic.List[object]]::new()
    $allContainers = [Collections.Generic.List[object]]::new()
    $unexpected = [Collections.Generic.List[string]]::new()
    foreach ($line in $runningRows) {
        if ([string]::IsNullOrWhiteSpace([string]$line)) {
            continue
        }
        $parts = @([string]$line -split '\|', 6)
        if ($parts.Count -ne 6) {
            throw 'Docker running-container inventory returned a malformed row.'
        }
        $row = [pscustomobject]@{
            container_id = [string]$parts[0]
            project = [string]$parts[1]
            service = [string]$parts[2]
            oneoff = [string]$parts[3]
            container_name = [string]$parts[4]
            networks = @([string]$parts[5] -split ',' | Where-Object { $_ -ne '' })
        }
        $allContainers.Add($row)
        $isRelevant = (
            $allowedById.ContainsKey($row.container_id) -or
            $row.project -eq $ProjectName -or
            @($row.networks | Where-Object { $_ -in $AllowedNetworks }).Count -ne 0
        )
        if (-not $isRelevant) {
            continue
        }
        $containers.Add($row)
        if (
            -not $allowedById.ContainsKey($row.container_id) -or
            [string]$allowedById[$row.container_id] -ne $row.service -or
            $row.project -ne $ProjectName -or
            $row.oneoff -ne 'False'
        ) {
            $unexpected.Add(
                "$($row.container_name):$($row.service):$($row.container_id)"
            )
        }
    }

    $missingIds = @(
        $allowedById.Keys |
            Where-Object { $_ -notin @($containers | ForEach-Object container_id) }
    )
    $serviceSetSafe = (
        $composeServices.Count -eq $allowedServices.Count -and
        @($composeServices | Sort-Object -Unique).Count -eq $allowedServices.Count -and
        @($composeServices | Where-Object { $_ -notin $allowedServices }).Count -eq 0 -and
        @($allowedServices | Where-Object { $_ -notin $composeServices }).Count -eq 0
    )
    return [pscustomobject]@{
        at = [DateTimeOffset]::UtcNow.ToString('O')
        project = $ProjectName
        allowed_services = $allowedServices
        protected_networks = @($AllowedNetworks | Sort-Object -Unique)
        running_services = @($composeServices)
        all_running_container_count = $allContainers.Count
        all_running_containers = @($allContainers)
        running_containers = @($containers)
        unexpected_containers = @($unexpected)
        missing_container_ids = @($missingIds)
        safe = (
            $serviceSetSafe -and
            $containers.Count -eq $allowedById.Count -and
            $unexpected.Count -eq 0 -and
            $missingIds.Count -eq 0
        )
    }
}

function Get-NextAnchoredSampleAt {
    param(
        [Parameter(Mandatory = $true)][DateTimeOffset] $PreviousAt,
        [Parameter(Mandatory = $true)][DateTimeOffset] $Now,
        [Parameter(Mandatory = $true)][ValidateRange(1, 3600)][int] $IntervalSeconds
    )

    $nextAt = $PreviousAt.AddSeconds($IntervalSeconds)
    while ($nextAt -le $Now) {
        $nextAt = $nextAt.AddSeconds($IntervalSeconds)
    }
    return $nextAt
}

function Start-ObserverSampler {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string[]] $FunctionNames,
        [Parameter(Mandatory = $true)][scriptblock] $WorkerScript,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]] $Arguments
    )

    $initialSessionState = [Management.Automation.Runspaces.InitialSessionState]::CreateDefault()
    foreach ($functionName in $FunctionNames) {
        $command = Get-Command -Name $functionName -CommandType Function -ErrorAction Stop
        $entry = [Management.Automation.Runspaces.SessionStateFunctionEntry]::new(
            $functionName,
            $command.Definition
        )
        $initialSessionState.Commands.Add($entry)
    }

    $runspace = [Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace(
        $initialSessionState
    )
    $pipeline = $null
    try {
        $runspace.Open()
        $pipeline = [Management.Automation.PowerShell]::Create()
        $pipeline.Runspace = $runspace
        [void]$pipeline.AddScript($WorkerScript.ToString())
        foreach ($argument in $Arguments) {
            [void]$pipeline.AddArgument($argument)
        }
        $asyncResult = $pipeline.BeginInvoke()
        return [pscustomobject]@{
            name = $Name
            pipeline = $pipeline
            runspace = $runspace
            async_result = $asyncResult
            disposed = $false
        }
    }
    catch {
        if ($null -ne $pipeline) {
            $pipeline.Dispose()
        }
        $runspace.Dispose()
        throw
    }
}

function Stop-ObserverSamplerSafely {
    param([AllowNull()][object] $Sampler)

    if ($null -eq $Sampler) {
        return
    }
    try {
        if ([bool]$Sampler.disposed) {
            return
        }
        $safeToDispose = [bool]$Sampler.async_result.IsCompleted
        if (-not $safeToDispose) {
            try {
                $stopResult = $Sampler.pipeline.BeginStop($null, $null)
                if ($stopResult.AsyncWaitHandle.WaitOne(5000)) {
                    try {
                        $Sampler.pipeline.EndStop($stopResult)
                    }
                    catch {
                        # Cleanup must preserve the original observer failure.
                    }
                    $safeToDispose = $true
                }
            }
            catch {
                # The top-level trap owns the original fail-closed evidence.
            }
        }
        if ($safeToDispose) {
            try {
                $Sampler.pipeline.Dispose()
            }
            catch {
                # Cleanup must never replace the original observer failure.
            }
            try {
                $Sampler.runspace.Dispose()
            }
            catch {
                # Cleanup must never replace the original observer failure.
            }
            $Sampler.disposed = $true
        }
    }
    catch {
        # Process exit remains the final bound if a runspace cannot be stopped.
    }
}

function Complete-ObserverSampler {
    param(
        [Parameter(Mandatory = $true)][object] $Sampler,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[object]] $Errors
    )

    if (-not $Sampler.async_result.AsyncWaitHandle.WaitOne(30000)) {
        $Errors.Add([pscustomobject]@{
            at = [DateTimeOffset]::UtcNow.ToString('O')
            source = [string]$Sampler.name
            error = 'Sampler did not stop within its bounded completion window.'
        })
        Stop-ObserverSamplerSafely -Sampler $Sampler
        return
    }
    try {
        [void]$Sampler.pipeline.EndInvoke($Sampler.async_result)
    }
    catch {
        $Errors.Add([pscustomobject]@{
            at = [DateTimeOffset]::UtcNow.ToString('O')
            source = [string]$Sampler.name
            error = $_.Exception.GetBaseException().Message
        })
    }
    foreach ($streamError in $Sampler.pipeline.Streams.Error) {
        $Errors.Add([pscustomobject]@{
            at = [DateTimeOffset]::UtcNow.ToString('O')
            source = [string]$Sampler.name
            error = $streamError.Exception.GetBaseException().Message
        })
    }
    Stop-ObserverSamplerSafely -Sampler $Sampler
}

function Receive-ObserverSamplerMessages {
    param(
        [Parameter(Mandatory = $true)]
        [Collections.Concurrent.ConcurrentQueue[object]] $Queue,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[object]] $ResourceSamples,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[object]] $TopologySamples,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[object]] $Errors
    )

    $message = $null
    while ($Queue.TryDequeue([ref]$message)) {
        if ([string]$message.kind -eq 'sample') {
            if ([string]$message.source -eq 'resource') {
                $ResourceSamples.Add($message.value)
            }
            elseif ([string]$message.source -eq 'topology') {
                $TopologySamples.Add($message.value)
            }
            else {
                $Errors.Add([pscustomobject]@{
                    at = [DateTimeOffset]::UtcNow.ToString('O')
                    source = 'observer'
                    error = 'Sampler returned an unknown sample source.'
                })
            }
        }
        elseif ([string]$message.kind -eq 'error') {
            $Errors.Add([pscustomobject]@{
                at = [string]$message.at
                source = [string]$message.source
                error = [string]$message.error
            })
        }
        else {
            $Errors.Add([pscustomobject]@{
                at = [DateTimeOffset]::UtcNow.ToString('O')
                source = 'observer'
                error = 'Sampler returned a malformed message.'
            })
        }
        $message = $null
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

if ($ValidateOnly) {
    $validation | ConvertTo-Json -Depth 6 -Compress
    return
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
$script:StratusReleaseComposeArguments = @(
    'compose', '--project-directory', $absoluteRoot,
    '-f', $composePath,
    '--env-file', $composeEnvPath,
    '--project-name', 'weatherpoly'
)
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
$apiIds = @(& docker @script:StratusReleaseComposeArguments ps -q api)
$postgresIds = @(& docker @script:StratusReleaseComposeArguments ps -q postgres)
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
    [int64]$baselineApi.HostConfig.Memory -ne $ExpectedApiMemoryLimitBytes -or
    @($baselineApi.Config.Env | Where-Object {
        [string]$_ -imatch '^OPERATOR_API_KEY='
    }).Count -ne 1 -or
    @($baselineApi.Config.Env | Where-Object {
        [string]$_ -cmatch '^OPERATOR_API_KEY=.+$'
    }).Count -ne 1 -or
    $baselineApi.Config.Labels.'org.opencontainers.image.revision' -ne $ExpectedRevision -or
    @(
        $baselineApi.Config.Env |
            Where-Object { $_ -eq "RELEASE_IMAGE_DIGEST=$ExpectedReleaseDigest" }
    ).Count -ne 1
) {
    throw 'Runtime gate API identity does not match the exact candidate.'
}
$projectName = [string]$baselineApi.Config.Labels.'com.docker.compose.project'
if (
    [string]::IsNullOrWhiteSpace($projectName) -or
    [string]$baselinePostgres.Config.Labels.'com.docker.compose.project' -ne $projectName -or
    [string]$baselineApi.Config.Labels.'com.docker.compose.service' -ne 'api' -or
    [string]$baselinePostgres.Config.Labels.'com.docker.compose.service' -ne 'postgres' -or
    [string]$baselineApi.Config.Labels.'com.docker.compose.oneoff' -ne 'False' -or
    [string]$baselinePostgres.Config.Labels.'com.docker.compose.oneoff' -ne 'False'
) {
    throw 'Runtime gate containers do not have the exact Compose project/service identity.'
}
$apiName = ([string]$baselineApi.Name).TrimStart('/')
$postgresName = ([string]$baselinePostgres.Name).TrimStart('/')
$allowedNetworkNames = @(
    @($baselineApi.NetworkSettings.Networks.PSObject.Properties.Name) +
    @($baselinePostgres.NetworkSettings.Networks.PSObject.Properties.Name) |
        Sort-Object -Unique
)
if ($allowedNetworkNames.Count -eq 0) {
    throw 'Runtime gate containers have no authoritative Docker networks.'
}
$apiClientAddresses = @(
    $baselineApi.NetworkSettings.Networks.PSObject.Properties |
        ForEach-Object { [string]$_.Value.IPAddress } |
        Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } |
        Sort-Object -Unique
)
if ($apiClientAddresses.Count -eq 0) {
    throw 'Runtime gate API has no authoritative Docker network address.'
}
$databaseSamples = [Collections.Generic.List[object]]::new()
$resourceSamples = [Collections.Generic.List[object]]::new()
$topologySamples = [Collections.Generic.List[object]]::new()
$sampleErrors = [Collections.Generic.List[object]]::new()
$observationStartedAt = [DateTimeOffset]::UtcNow
$baselineTopology = Get-RunningProjectTopology -ProjectName $projectName `
    -ApiContainerId ([string]$baselineApi.Id) `
    -PostgresContainerId ([string]$baselinePostgres.Id) `
    -AllowedNetworks $allowedNetworkNames
$topologySamples.Add($baselineTopology)
if (-not [bool]$baselineTopology.safe) {
    throw 'Runtime gate requires the exact API/PostgreSQL project topology only.'
}
$deadline = $observationStartedAt.AddSeconds($DurationSeconds)
$samplerMessages = [Collections.Concurrent.ConcurrentQueue[object]]::new()
$resourceWorkerScript = {
    param(
        [object] $Queue,
        [DateTimeOffset] $ObservationStartedAt,
        [DateTimeOffset] $Deadline,
        [int] $IntervalSeconds,
        [string] $ApiContainer,
        [string] $PostgresContainer
    )

    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    $nextSampleAt = $ObservationStartedAt
    while ([DateTimeOffset]::UtcNow -lt $Deadline) {
        $now = [DateTimeOffset]::UtcNow
        if ($now -lt $nextSampleAt) {
            $remainingMilliseconds = [Math]::Min(
                ($nextSampleAt - $now).TotalMilliseconds,
                ($Deadline - $now).TotalMilliseconds
            )
            if ($remainingMilliseconds -gt 0) {
                Start-Sleep -Milliseconds ([Math]::Max(
                    1,
                    [int][Math]::Ceiling($remainingMilliseconds)
                ))
            }
            continue
        }
        try {
            $sample = Get-ResourceSnapshot `
                -ApiContainer $ApiContainer `
                -PostgresContainer $PostgresContainer
            $Queue.Enqueue([pscustomobject]@{
                kind = 'sample'
                source = 'resource'
                value = $sample
            })
        }
        catch {
            $Queue.Enqueue([pscustomobject]@{
                kind = 'error'
                at = [DateTimeOffset]::UtcNow.ToString('O')
                source = 'docker'
                error = $_.Exception.GetBaseException().Message
            })
        }
        $nextSampleAt = Get-NextAnchoredSampleAt `
            -PreviousAt $nextSampleAt `
            -Now ([DateTimeOffset]::UtcNow) `
            -IntervalSeconds $IntervalSeconds
    }
}
$topologyWorkerScript = {
    param(
        [object] $Queue,
        [DateTimeOffset] $ObservationStartedAt,
        [DateTimeOffset] $Deadline,
        [int] $IntervalSeconds,
        [string] $ProjectName,
        [string] $ApiContainerId,
        [string] $PostgresContainerId,
        [string[]] $AllowedNetworks,
        [object[]] $ComposeArguments
    )

    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    $script:StratusReleaseComposeArguments = @($ComposeArguments)
    $nextSampleAt = $ObservationStartedAt
    while ([DateTimeOffset]::UtcNow -lt $Deadline) {
        $now = [DateTimeOffset]::UtcNow
        if ($now -lt $nextSampleAt) {
            $remainingMilliseconds = [Math]::Min(
                ($nextSampleAt - $now).TotalMilliseconds,
                ($Deadline - $now).TotalMilliseconds
            )
            if ($remainingMilliseconds -gt 0) {
                Start-Sleep -Milliseconds ([Math]::Max(
                    1,
                    [int][Math]::Ceiling($remainingMilliseconds)
                ))
            }
            continue
        }
        try {
            $sample = Get-RunningProjectTopology `
                -ProjectName $ProjectName `
                -ApiContainerId $ApiContainerId `
                -PostgresContainerId $PostgresContainerId `
                -AllowedNetworks $AllowedNetworks
            $Queue.Enqueue([pscustomobject]@{
                kind = 'sample'
                source = 'topology'
                value = $sample
            })
        }
        catch {
            $Queue.Enqueue([pscustomobject]@{
                kind = 'error'
                at = [DateTimeOffset]::UtcNow.ToString('O')
                source = 'topology'
                error = $_.Exception.GetBaseException().Message
            })
        }
        $nextSampleAt = Get-NextAnchoredSampleAt `
            -PreviousAt $nextSampleAt `
            -Now ([DateTimeOffset]::UtcNow) `
            -IntervalSeconds $IntervalSeconds
    }
}
$resourceSampler = $null
$topologySampler = $null
try {
$resourceSampler = Start-ObserverSampler `
    -Name 'resource' `
    -FunctionNames @(
        'Convert-Percent',
        'Get-ResourceSnapshot',
        'Get-NextAnchoredSampleAt'
    ) `
    -WorkerScript $resourceWorkerScript `
    -Arguments @(
        $samplerMessages,
        $observationStartedAt,
        $deadline,
        $ResourceIntervalSeconds,
        $apiName,
        $postgresName
    )
$topologySampler = Start-ObserverSampler `
    -Name 'topology' `
    -FunctionNames @(
        'Get-RunningProjectTopology',
        'Get-NextAnchoredSampleAt'
    ) `
    -WorkerScript $topologyWorkerScript `
    -Arguments @(
        $samplerMessages,
        $observationStartedAt,
        $deadline,
        $ResourceIntervalSeconds,
        $projectName,
        [string]$baselineApi.Id,
        [string]$baselinePostgres.Id,
        $allowedNetworkNames,
        $script:StratusReleaseComposeArguments
    )
$nextDatabaseAt = $observationStartedAt
$readyWritten = $false

while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $now = [DateTimeOffset]::UtcNow
    if ($now -lt $nextDatabaseAt) {
        $remainingMilliseconds = [Math]::Min(
            ($nextDatabaseAt - $now).TotalMilliseconds,
            ($deadline - $now).TotalMilliseconds
        )
        if ($remainingMilliseconds -gt 0) {
            Start-Sleep -Milliseconds ([Math]::Max(
                1,
                [int][Math]::Ceiling($remainingMilliseconds)
            ))
        }
        continue
    }
    try {
        $databaseSamples.Add(
            (Invoke-DatabaseSnapshot -PostgresContainer $postgresName `
                -ApiClientAddresses $apiClientAddresses)
        )
    }
    catch {
        $sampleErrors.Add([pscustomobject]@{
            at = [DateTimeOffset]::UtcNow.ToString('O')
            source = 'database'
            error = $_.Exception.GetBaseException().Message
        })
    }
    $nextDatabaseAt = Get-NextAnchoredSampleAt `
        -PreviousAt $nextDatabaseAt `
        -Now ([DateTimeOffset]::UtcNow) `
        -IntervalSeconds $DatabaseIntervalSeconds
    Receive-ObserverSamplerMessages `
        -Queue $samplerMessages `
        -ResourceSamples $resourceSamples `
        -TopologySamples $topologySamples `
        -Errors $sampleErrors

    if (-not $readyWritten -and $sampleErrors.Count -ne 0) {
        throw 'Runtime observer sampler failed before the safe preflight marker.'
    }
    if (
        -not $readyWritten -and
        (
            [bool]$resourceSampler.async_result.IsCompleted -or
            [bool]$topologySampler.async_result.IsCompleted
        )
    ) {
        throw 'Runtime observer sampler exited before the safe preflight marker.'
    }
    if (
        -not $readyWritten -and
        $databaseSamples.Count -gt 0 -and
        $resourceSamples.Count -gt 0 -and
        $topologySamples.Count -gt 1
    ) {
        $firstDatabaseSample = $databaseSamples[0]
        $firstResourceSample = $resourceSamples[0]
        $unsafeDatabaseSamples = @(
            $databaseSamples | Where-Object {
                -not (Test-DatabasePreflightSample `
                    -Sample $_ `
                    -ExpectedSchemaHead $ExpectedSchemaHead `
                    -MaxApiSessions $MaxApiSessions)
            }
        )
        $unsafeResourceSamples = @(
            $resourceSamples | Where-Object {
                -not (Test-ResourcePreflightSample `
                    -Sample $_ `
                    -ExpectedApiMemoryLimitBytes $ExpectedApiMemoryLimitBytes)
            }
        )
        $unsafeTopologySamples = @(
            $topologySamples | Where-Object {
                -not (Test-TopologyPreflightSample -Sample $_)
            }
        )
        if (
            $unsafeDatabaseSamples.Count -ne 0 -or
            $unsafeResourceSamples.Count -ne 0 -or
            $unsafeTopologySamples.Count -ne 0
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
            first_dynamic_topology_sample_at = [string]$topologySamples[1].at
            preflight_database_sample_count = $databaseSamples.Count
            preflight_resource_sample_count = $resourceSamples.Count
            preflight_topology_sample_count = $topologySamples.Count
            api_image_id = $ExpectedApiImageId
            revision = $ExpectedRevision
            release_digest = $ExpectedReleaseDigest
            compose_project = $projectName
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
}

Complete-ObserverSampler -Sampler $resourceSampler -Errors $sampleErrors
Complete-ObserverSampler -Sampler $topologySampler -Errors $sampleErrors
Receive-ObserverSamplerMessages `
    -Queue $samplerMessages `
    -ResourceSamples $resourceSamples `
    -TopologySamples $topologySamples `
    -Errors $sampleErrors
}
finally {
    Stop-ObserverSamplerSafely -Sampler $resourceSampler
    Stop-ObserverSamplerSafely -Sampler $topologySampler
}

Start-Sleep -Seconds $DrainSeconds
$drain = Invoke-DatabaseSnapshot -PostgresContainer $postgresName `
    -ApiClientAddresses $apiClientAddresses
$finalTopology = Get-RunningProjectTopology -ProjectName $projectName `
    -ApiContainerId ([string]$baselineApi.Id) `
    -PostgresContainerId ([string]$baselinePostgres.Id) `
    -AllowedNetworks $allowedNetworkNames
$topologySamples.Add($finalTopology)
$finalApi = @((docker inspect $apiName | ConvertFrom-Json))[0]
$finalPostgres = @((docker inspect $postgresName | ConvertFrom-Json))[0]
$dbRows = @($databaseSamples)
$resourceRows = @($resourceSamples)
$topologyRows = @($topologySamples)
$pgCpu = @($resourceRows | ForEach-Object { [double]$_.postgres_cpu_percent })
$twoAt150 = $false
for ($index = 1; $index -lt $pgCpu.Count; $index += 1) {
    if ($pgCpu[$index - 1] -ge 150 -and $pgCpu[$index] -ge 150) {
        $twoAt150 = $true
        break
    }
}
$rolling30Max = Get-TimestampedRollingMaximum `
    -Rows $resourceRows `
    -TimestampProperty 'at' `
    -ValueProperty 'postgres_cpu_percent' `
    -WindowSeconds 30

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
    [int64]$finalApi.HostConfig.Memory -eq $ExpectedApiMemoryLimitBytes -and
    @($finalApi.Config.Env | Where-Object {
        [string]$_ -imatch '^OPERATOR_API_KEY='
    }).Count -eq 1 -and
    @($finalApi.Config.Env | Where-Object {
        [string]$_ -cmatch '^OPERATOR_API_KEY=.+$'
    }).Count -eq 1 -and
    $finalApi.Config.Labels.'org.opencontainers.image.revision' -eq $ExpectedRevision -and
    @(
        $finalApi.Config.Env |
            Where-Object { $_ -eq "RELEASE_IMAGE_DIGEST=$ExpectedReleaseDigest" }
    ).Count -eq 1
)
$topologyFailures = @($topologyRows | Where-Object { -not [bool]$_.safe })
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
if ($topologyRows.Count -eq 0) { $violations.Add('no_topology_samples') }
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
if ($topologyRows.Count -lt $minimumResourceSamples) {
    $violations.Add('topology_sample_coverage')
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
if (
    @($resourceRows | Where-Object {
        [int64]$_.api_memory_limit_bytes -ne $ExpectedApiMemoryLimitBytes
    }).Count -ne 0
) {
    $violations.Add('api_memory_limit_changed')
}
if (
    @($resourceRows | Where-Object {
        [int]$_.api_operator_key_entry_count -ne 1
    }).Count -ne 0
) {
    $violations.Add('api_operator_credential_changed')
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
if ($topologyFailures.Count -ne 0) {
    $violations.Add('application_writer_started')
    $violations.Add('container_topology_changed')
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
    topology_sample_count = $topologyRows.Count
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
    application_writers_stopped = $topologyFailures.Count -eq 0
    exact_project_topology_held = $topologyFailures.Count -eq 0
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
        compose_project = $projectName
    }
    summary = $summary
    violations = @($violations)
    drain = $drain
    sample_errors = @($sampleErrors)
    database_samples = $dbRows
    resource_samples = $resourceRows
    topology_samples = $topologyRows
}
Write-ObserverArtifact -Path $absoluteOutput -Artifact $result
$summary | ConvertTo-Json -Compress
if (-not $summary.runtime_passed) {
    exit 1
}
