[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [string]$PostgresPassword = $env:POSTGRES_PASSWORD,

    [switch]$SkipMigrations,

    [switch]$NoBackup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvScripts = Join-Path $RepoRoot ".venv\Scripts"
$Alembic = Join-Path $VenvScripts "alembic.exe"
$StratusApi = Join-Path $VenvScripts "stratus-api.exe"
$ApiOutLog = Join-Path $RepoRoot "api.out.log"
$ApiErrLog = Join-Path $RepoRoot "api.err.log"

if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
    $PostgresPassword = "stratus"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $FilePath $($ArgumentList -join ' ')"
    }
}

function Get-ListeningPortOwner {
    param(
        [Parameter(Mandatory = $true)]
        [int]$LocalPort
    )

    $connection = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $connection -or $connection.OwningProcess -eq 0) {
        return $null
    }

    return Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)"
}

function Test-IsCurrentRepoApi {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Process
    )

    $commandLine = [string]$Process.CommandLine
    return $commandLine -like "*$RepoRoot*stratus-api.exe*"
}

function Wait-PostgresReady {
    $ready = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        & docker compose exec -T postgres pg_isready -U stratus -d stratus *> $null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 2
    }

    if (-not $ready) {
        throw "Postgres did not become ready within 120 seconds."
    }
}

function Wait-PortFree {
    param(
        [Parameter(Mandatory = $true)]
        [int]$LocalPort
    )

    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if ($null -eq (Get-ListeningPortOwner -LocalPort $LocalPort)) {
            return
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Port $LocalPort is still in LISTEN state."
}

function Wait-ApiHealthy {
    param(
        [Parameter(Mandatory = $true)]
        [int]$LocalPort,

        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process
    )

    for ($attempt = 1; $attempt -le 60; $attempt++) {
        if ($Process.HasExited) {
            $stderr = if (Test-Path $ApiErrLog) { Get-Content $ApiErrLog -Tail 40 } else { @() }
            throw "stratus-api exited early with code $($Process.ExitCode). stderr tail:`n$($stderr -join [Environment]::NewLine)"
        }

        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/healthz" -TimeoutSec 2 | Out-Null
            Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/api/v1/live-summary" -TimeoutSec 5 | Out-Null
            Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/api/v1/live-equity" -TimeoutSec 5 | Out-Null
            return
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }

    $stderr = if (Test-Path $ApiErrLog) { Get-Content $ApiErrLog -Tail 40 } else { @() }
    throw "stratus-api did not become healthy. stderr tail:`n$($stderr -join [Environment]::NewLine)"
}

Set-Location $RepoRoot

foreach ($requiredPath in @($Alembic, $StratusApi)) {
    if (-not (Test-Path $requiredPath)) {
        throw "Missing $requiredPath. Create/update the virtualenv first, for example: uv sync --extra dev"
    }
}

Write-Host "Checking Docker availability..."
Invoke-Checked -FilePath "docker" -ArgumentList @("version")

$composeServices = @("postgres")
if (-not $NoBackup) {
    $composeServices += "backup"
}

Write-Host "Stopping container runtime services that can conflict with host development..."
Invoke-Checked -FilePath "docker" -ArgumentList @("compose", "stop", "api", "worker")

Write-Host "Starting Docker data services: $($composeServices -join ', ')"
Invoke-Checked -FilePath "docker" -ArgumentList (@("compose", "up", "-d") + $composeServices)

Write-Host "Waiting for Postgres..."
Wait-PostgresReady

$owner = Get-ListeningPortOwner -LocalPort $Port
if ($null -ne $owner) {
    if (Test-IsCurrentRepoApi -Process $owner) {
        Write-Host "Stopping existing host stratus-api on port $Port (PID $($owner.ProcessId))..."
        Stop-Process -Id $owner.ProcessId -Force
        Wait-PortFree -LocalPort $Port
    }
    else {
        throw "Port $Port is owned by PID $($owner.ProcessId) ($($owner.Name)): $($owner.CommandLine). Refusing to stop a non-STRATUS host API process."
    }
}

$env:DATABASE_URL = "postgresql+asyncpg://stratus:$PostgresPassword@127.0.0.1:5432/stratus"
$env:API_HOST = "127.0.0.1"
$env:API_PORT = "$Port"

if (-not $SkipMigrations) {
    Write-Host "Applying migrations against 127.0.0.1:5432..."
    Invoke-Checked -FilePath $Alembic -ArgumentList @("upgrade", "head")
}

Write-Host "Starting host stratus-api from current working tree..."
$process = Start-Process `
    -FilePath $StratusApi `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $ApiOutLog `
    -RedirectStandardError $ApiErrLog `
    -PassThru

Wait-ApiHealthy -LocalPort $Port -Process $process

Write-Host "Host stratus-api is ready: http://127.0.0.1:$Port"
Write-Host "PID: $($process.Id)"
Write-Host "Logs: $ApiOutLog and $ApiErrLog"
Write-Host "Docker api/worker are stopped; Postgres remains in Docker."
