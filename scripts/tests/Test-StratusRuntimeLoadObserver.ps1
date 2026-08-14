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
    'docker\s+(start|stop|restart|rm|compose\s+up)'
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
    'firstDatabaseSample\.api_sessions -gt \$MaxApiSessions',
    'firstDatabaseSample\.api_idle_in_tx -ne 0',
    'firstDatabaseSample\.lock_waiters -ne 0',
    'firstResourceSample\.api_memory_percent -ge 90',
    'firstResourceSample\.api_memory_limit_bytes -ne',
    'api_memory_limit_changed',
    'api_operator_key_entry_count',
    'api_operator_credential_changed',
    'firstResourceSample\.postgres_cpu_percent -ge 200',
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
    'Write-ObserverArtifact -Path \$absoluteOutput -Artifact \$fatal'
)) {
    if ($source -notmatch $required) {
        throw "Runtime observer is missing a required evidence control: $required"
    }
}
if ($source -match "application_name LIKE 'stratus-%'") {
    throw 'Runtime observer still trusts application_name as a writer inventory.'
}
$topologyCallCount = @(
    [regex]::Matches($source, 'Get-RunningProjectTopology\s+-ProjectName')
).Count
if ($topologyCallCount -lt 3) {
    throw 'Runtime observer does not prove project topology before, during, and after load.'
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

[pscustomobject]@{
    script = $scriptPath
    syntax_valid = $true
    read_only = $true
    one_second_database_sampling = $true
    five_second_resource_sampling = $true
    post_load_drain_required = $true
    validated = $true
} | ConvertTo-Json -Compress
