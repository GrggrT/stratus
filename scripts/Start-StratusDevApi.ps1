[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [string]$PostgresPassword = $env:POSTGRES_PASSWORD,

    [string]$RuntimePasswordFile = $env:STRATUS_RUNTIME_PASSWORD_FILE,

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
$LocalRuntimeRoot = [Environment]::GetFolderPath("LocalApplicationData")
if ([string]::IsNullOrWhiteSpace($LocalRuntimeRoot)) {
    $LocalRuntimeRoot = [IO.Path]::GetTempPath()
}
$RuntimeWorkingDirectory = Join-Path $LocalRuntimeRoot "STRATUS\Runtime\Api"
$RuntimeEnvironmentFile = Join-Path $RuntimeWorkingDirectory ".env"
$RepoEnvironmentFile = Join-Path $RepoRoot ".env"

if ([string]::IsNullOrWhiteSpace($RuntimePasswordFile)) {
    $RuntimePasswordFile = Join-Path $RepoRoot ".secrets\stratus_runtime_password"
}
if (-not [IO.Path]::IsPathRooted($RuntimePasswordFile)) {
    $RuntimePasswordFile = Join-Path $RepoRoot $RuntimePasswordFile
}
$RuntimePasswordFile = [IO.Path]::GetFullPath($RuntimePasswordFile)
if (-not (Test-Path -LiteralPath $RuntimePasswordFile -PathType Leaf)) {
    throw "Missing runtime database password file: $RuntimePasswordFile"
}

$env:STRATUS_RUNTIME_PASSWORD_FILE = $RuntimePasswordFile

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

function Write-RuntimeSafeEnvironmentFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,

        [Parameter(Mandatory = $true)]
        [string]$DestinationPath
    )

    $safeLines = [Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $SourcePath -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $SourcePath) {
            if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
                continue
            }
            if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
                throw "Unsupported line in $SourcePath; refusing to construct runtime environment."
            }
            $name = $Matches[1]
            $isOwnerName = $name -match '(?i)(^POSTGRES(?:_|$)|DATABASE_URL|^STRATUS_TEST_DATABASE_URL$|^PG(?:PASSWORD|USER|HOST|PORT|DATABASE|SERVICE|SERVICEFILE)$|^STRATUS_OWNER_)'
            $referencesOwnerValue = $line -match '(?i)(POSTGRES_PASSWORD|MIGRATION_DATABASE_URL|DATABASE_URL|STRATUS_TEST_DATABASE_URL|PGPASSWORD|postgres(?:ql)?(?:\+asyncpg)?://)'
            if (-not $isOwnerName -and -not $referencesOwnerValue) {
                $safeLines.Add($line)
            }
        }
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $DestinationPath) -Force | Out-Null
    $temporaryPath = "$DestinationPath.tmp"
    $utf8WithoutBom = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllLines($temporaryPath, $safeLines, $utf8WithoutBom)
    Move-Item -LiteralPath $temporaryPath -Destination $DestinationPath -Force
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $escapedName = [Regex]::Escape($Name)
    $values = @(
        Get-Content -LiteralPath $Path |
            ForEach-Object {
                if ($_ -match "^\s*$escapedName\s*=\s*(.*)$") {
                    $Matches[1].Trim()
                }
            }
    )
    if ($values.Count -gt 1) {
        throw "Duplicate $Name entries in $Path."
    }
    if ($values.Count -eq 0) {
        return $null
    }
    $value = $values[0]
    if (
        $value.Length -ge 2 -and
        (($value[0] -eq '"' -and $value[-1] -eq '"') -or
         ($value[0] -eq "'" -and $value[-1] -eq "'"))
    ) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Start-SanitizedRuntimeProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$OwnerSecret
    )

    $runtimeOverrides = @{
        STRATUS_RUNTIME_PASSWORD_FILE = $RuntimePasswordFile
        STRATUS_DATABASE_HOST = "127.0.0.1"
        STRATUS_DATABASE_PORT = "5432"
        STRATUS_DATABASE_NAME = "stratus"
        API_HOST = "127.0.0.1"
        API_PORT = "$Port"
    }
    $sensitiveNamePattern = '(?i)(^POSTGRES(?:_|$)|DATABASE_URL|^STRATUS_TEST_DATABASE_URL$|^PG(?:PASSWORD|USER|HOST|PORT|DATABASE|SERVICE|SERVICEFILE)$|^STRATUS_OWNER_)'
    $databaseUrlPattern = '(?i)postgres(?:ql)?(?:\+asyncpg)?://'
    $namesToReplace = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    $processEnvironment = [Environment]::GetEnvironmentVariables("Process")
    foreach ($entry in $processEnvironment.GetEnumerator()) {
        $name = [string]$entry.Key
        $value = [string]$entry.Value
        if (
            $name -match $sensitiveNamePattern -or
            $value -match $databaseUrlPattern -or
            (-not [string]::IsNullOrEmpty($OwnerSecret) -and $value.Contains($OwnerSecret))
        ) {
            [void]$namesToReplace.Add($name)
        }
    }
    foreach ($name in $runtimeOverrides.Keys) {
        [void]$namesToReplace.Add($name)
    }

    $originalValues = @{}
    foreach ($name in $namesToReplace) {
        $existingValue = [Environment]::GetEnvironmentVariable($name, "Process")
        $originalValues[$name] = [PSCustomObject]@{
            Exists = $null -ne $existingValue
            Value = $existingValue
        }
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
    foreach ($entry in $runtimeOverrides.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }

    try {
        return Start-Process `
            -FilePath $FilePath `
            -WorkingDirectory $WorkingDirectory `
            -WindowStyle Hidden `
            -RedirectStandardOutput $ApiOutLog `
            -RedirectStandardError $ApiErrLog `
            -PassThru
    }
    finally {
        foreach ($name in $namesToReplace) {
            $snapshot = $originalValues[$name]
            $value = if ($snapshot.Exists) { $snapshot.Value } else { $null }
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$ownerEnvironmentNames = @(
    "POSTGRES_PASSWORD",
    "MIGRATION_DATABASE_URL",
    "DATABASE_URL",
    "STRATUS_RUNTIME_DATABASE_URL",
    "STRATUS_TEST_DATABASE_URL",
    "PGPASSWORD",
    "PGUSER",
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "STRATUS_OWNER_PASSWORD",
    "STRATUS_OWNER_PASSWORD_FILE"
)
$ownerEnvironmentSnapshot = @{}
foreach ($name in $ownerEnvironmentNames) {
    $existingValue = [Environment]::GetEnvironmentVariable($name, "Process")
    $ownerEnvironmentSnapshot[$name] = [PSCustomObject]@{
        Exists = $null -ne $existingValue
        Value = $existingValue
    }
}

try {
Set-Location $RepoRoot

if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
    $PostgresPassword = Get-DotEnvValue `
        -Path $RepoEnvironmentFile `
        -Name "POSTGRES_PASSWORD"
}
if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
    throw "POSTGRES_PASSWORD is required; set a strong non-default owner password."
}
if ($PostgresPassword -notmatch '^[A-Za-z0-9._~-]{24,}$') {
    throw "POSTGRES_PASSWORD must be non-default, URL-safe, and contain at least 24 characters."
}
$env:POSTGRES_PASSWORD = $PostgresPassword

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

if (-not $SkipMigrations) {
    Write-Host "Applying migrations against 127.0.0.1:5432..."
    $encodedOwnerPassword = [Uri]::EscapeDataString($PostgresPassword)
    $previousMigrationUrl = [Environment]::GetEnvironmentVariable(
        "MIGRATION_DATABASE_URL",
        "Process"
    )
    try {
        $migrationUrl = "postgresql+asyncpg://stratus:$encodedOwnerPassword@127.0.0.1:5432/stratus"
        [Environment]::SetEnvironmentVariable(
            "MIGRATION_DATABASE_URL",
            $migrationUrl,
            "Process"
        )
        Invoke-Checked -FilePath $Alembic -ArgumentList @("upgrade", "head")
    }
    finally {
        [Environment]::SetEnvironmentVariable(
            "MIGRATION_DATABASE_URL",
            $previousMigrationUrl,
            "Process"
        )
        $migrationUrl = $null
        $encodedOwnerPassword = $null
    }
}

Write-Host "Provisioning the database-scoped runtime login..."
Invoke-Checked -FilePath "docker" -ArgumentList @(
    "compose", "run", "--rm", "--no-deps", "provision-runtime-role"
)

Write-RuntimeSafeEnvironmentFile `
    -SourcePath $RepoEnvironmentFile `
    -DestinationPath $RuntimeEnvironmentFile

Write-Host "Starting host stratus-api from current working tree..."
$process = Start-SanitizedRuntimeProcess `
    -FilePath $StratusApi `
    -WorkingDirectory $RuntimeWorkingDirectory `
    -OwnerSecret $PostgresPassword
$PostgresPassword = $null

Wait-ApiHealthy -LocalPort $Port -Process $process

Write-Host "Host stratus-api is ready: http://127.0.0.1:$Port"
Write-Host "PID: $($process.Id)"
Write-Host "Logs: $ApiOutLog and $ApiErrLog"
Write-Host "Docker api/worker are stopped; Postgres remains in Docker."
}
finally {
    $PostgresPassword = $null
    $migrationUrl = $null
    $encodedOwnerPassword = $null
    $previousMigrationUrl = $null
    foreach ($name in $ownerEnvironmentNames) {
        $snapshot = $ownerEnvironmentSnapshot[$name]
        $value = if ($snapshot.Exists) { $snapshot.Value } else { $null }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}
