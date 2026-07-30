[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$runbookPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..\docs\RELEASE_CUTOVER_RUNBOOK.md')
)
$source = Get-Content -LiteralPath $runbookPath -Raw

foreach ($forbidden in @(
    'docker compose create',
    'Start-Process -FilePath "pwsh"',
    'docker inspect weatherpoly-worker-1',
    'expected_plan_hash = \$plan\.plan_hash',
    '\$secureApiKey',
    '(?m)^\s*\.\\scripts\\Invoke-StratusDashboardLoadGate\.ps1',
    '(?m)^\s*\.\\scripts\\Assert-StratusLoadGateArtifacts\.ps1',
    '(?m)^\s*\.\\scripts\\Assert-StratusReleaseImage\.ps1'
)) {
    if ($source -match $forbidden) {
        throw "Release runbook contains an unsafe or non-portable command: $forbidden"
    }
}
foreach ($required in @(
    'docker compose up --no-start --no-deps --no-build --pull never --force-recreate migrate',
    'STRATUS_RELEASE_WORKTREE',
    'git -C \$productionRoot status --porcelain=v1 --untracked-files=all',
    '\$productionRevision -ne \$revision',
    'Production checkout must be the exact clean release revision',
    'git -C \$buildContext rev-parse HEAD',
    'git -C \$buildContext status --porcelain=v1 --untracked-files=all',
    'git -C \$buildContext symbolic-ref -q --short HEAD',
    '--tag \$image \$buildContext',
    'Join-Path \$buildContext "scripts\\Assert-StratusReleaseImage\.ps1"',
    'Exact candidate image build failed',
    '\$preMigrationHead -notin @\(',
    '"20260728_0045"',
    '"20260730_0046"',
    '\$preMigrationHead -eq "20260728_0045"',
    'Schema 0046 is already accepted; skip migration service',
    'Compose could not stage the exact-candidate migrate service',
    '\$digest = \(\[string\]\$imageInspect\.Id\)\.Substring\(7\)',
    '\$hostPowerShell = \(Get-Process -Id \$PID\)\.Path',
    'Join-Path \$buildContext "scripts\\Invoke-StratusRuntimeLoadObserver\.ps1"',
    'STRATUS_GATE_READY_ARTIFACT',
    'Test-Path -LiteralPath \$readyArtifact',
    '\$ready\.preflight_safe',
    '\$observer\.WaitForExit\(150000\)',
    'Stop-GateObserver -Process \$observer',
    '& \$hostPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass',
    'Join-Path \$buildContext "scripts\\Invoke-StratusDashboardLoadGate\.ps1"',
    'Join-Path \$buildContext "scripts\\Assert-StratusLoadGateArtifacts\.ps1"',
    '\$recomputedPlan = Invoke-RestMethod',
    'expected_plan_hash = \$recomputedPlan\.plan_hash',
    '\$stoppedWorker = @\(docker compose ps --all -q worker\)',
    'Assert-StratusLoadGateArtifacts\.ps1'
)) {
    if ($source -notmatch $required) {
        throw "Release runbook is missing a required safety command: $required"
    }
}

[pscustomobject]@{
    runbook = $runbookPath
    exact_digest_initialized = $true
    dependency_safe_migration_staging = $true
    portable_background_host = $true
    runtime_preflight_marker_required = $true
    continuity_plan_recomputed = $true
    rollback_container_resolved = $true
    validated = $true
} | ConvertTo-Json -Compress

$global:LASTEXITCODE = 0
