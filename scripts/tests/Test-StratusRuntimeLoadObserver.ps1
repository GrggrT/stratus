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
    'load_gate_run_id = \$runId',
    'expected_api_image_id = \$ExpectedApiImageId',
    'global_idle_in_tx -ne 0',
    'firstDatabaseSample\.api_sessions -gt \$MaxApiSessions',
    'firstDatabaseSample\.api_idle_in_tx -ne 0',
    'firstDatabaseSample\.lock_waiters -ne 0',
    'firstResourceSample\.api_memory_percent -ge 90',
    'firstResourceSample\.postgres_cpu_percent -ge 200',
    'candidate_identity_changed',
    'application_writer_started',
    'runtime_writer_sessions',
    'database_sample_coverage',
    'stratus_dashboard_runtime_observation_ready',
    'Write-ObserverArtifact -Path \$absoluteOutput -Artifact \$fatal'
)) {
    if ($source -notmatch $required) {
        throw "Runtime observer is missing a required evidence control: $required"
    }
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
    $validation.max_api_sessions -ne 4 -or
    -not $validation.duration_covers_worst_case_http_gate -or
    -not $validation.requires_shared_run_id -or
    -not $validation.requires_exact_api_identity -or
    -not $validation.requires_ready_marker
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
