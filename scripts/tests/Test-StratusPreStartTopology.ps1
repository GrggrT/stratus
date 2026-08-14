$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$gatePath = Join-Path $repoRoot 'scripts\Assert-StratusPreStartTopology.ps1'
$source = Get-Content -LiteralPath $gatePath -Raw
[void][scriptblock]::Create($source)

foreach ($required in @(
    "'--project-directory'",
    "'--env-file'",
    "'--project-name'",
    "'COMPOSE_FILE'",
    "'COMPOSE_PROFILES'",
    "'COMPOSE_PROJECT_NAME'",
    "'COMPOSE_PATH_SEPARATOR'",
    'image inspect $ImageReference',
    'RepoDigests',
    "'linux'",
    "'amd64'",
    'docker ps -q --no-trunc',
    'com.docker.compose.project',
    'com.docker.compose.project.working_dir',
    'com.docker.compose.service',
    'sharesProtectedNetwork',
    'RequirePostgresStopped',
    'ExpectedPostgresId',
    'ExpectedBackupId',
    'ExpectedPostgresConfigHash',
    'ExpectedBackupConfigHash',
    'ExpectedHbaSha256',
    'Get-CommandSha256',
    'PortBindings',
    'stratus-pg_hba.conf',
    'weatherpoly_stratus-postgres',
    'Test-ComposeConfigFilesLabel',
    'read_only = $true'
)) {
    if (-not $source.Contains($required)) {
        throw "Pre-start topology gate is missing required contract text: $required"
    }
}
foreach ($forbidden in @(
    'docker start',
    'docker stop',
    'docker restart',
    'docker rm',
    'docker compose up',
    'docker compose run'
)) {
    if ($source.Contains($forbidden)) {
        throw "Pre-start topology gate must remain read-only: $forbidden"
    }
}

function Assert-GateFailure {
    param(
        [Parameter(Mandatory = $true)][scriptblock] $Action,
        [Parameter(Mandatory = $true)][string] $ExpectedMessage,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $failed = $false
    try {
        & $Action | Out-Null
    }
    catch {
        $failed = $true
        if ([string]$_.Exception.Message -notlike "*$ExpectedMessage*") {
            throw "$Label failed for the wrong reason: $($_.Exception.Message)"
        }
    }
    if (-not $failed) {
        throw "$Label was incorrectly accepted."
    }
}

$tempRoot = Join-Path (
    [IO.Path]::GetTempPath()
) ('stratus-pre-start-topology-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $tempRoot)
[IO.File]::WriteAllText(
    (Join-Path $tempRoot 'docker-compose.yml'),
    "services:`n  postgres:`n    image: postgres:16-alpine`n",
    [Text.UTF8Encoding]::new($false)
)
[IO.File]::WriteAllText(
    (Join-Path $tempRoot '.env'),
    "POSTGRES_PASSWORD=fixture-owner-password-000000000000`n",
    [Text.UTF8Encoding]::new($false)
)
[IO.File]::WriteAllText(
    (Join-Path $tempRoot 'docker-compose.override.yml'),
    "services:`n  migrate:`n    command: [alembic, upgrade, head]`n",
    [Text.UTF8Encoding]::new($false)
)
$fixtureHbaPath = Join-Path $tempRoot 'ops\postgres\stratus-pg_hba.conf'
[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($fixtureHbaPath))
[IO.File]::WriteAllText(
    $fixtureHbaPath,
    "host all all 127.0.0.1/32 scram-sha-256`n",
    [Text.UTF8Encoding]::new($false)
)
[void][IO.Directory]::CreateDirectory((Join-Path $tempRoot 'backups'))
$fixtureHbaSha256 = (
    Get-FileHash -LiteralPath $fixtureHbaPath -Algorithm SHA256
).Hash.ToLowerInvariant()

$global:StratusTopologyCommands = @()
$global:StratusTopologyPostgresRunning = $false
$global:StratusTopologyExtra = 'none'
$global:StratusTopologyDrift = 'none'
$global:StratusTopologyPinnedImageDrift = 'none'
$postgresId = '1' * 64
$backupId = '2' * 64
$migrateId = '3' * 64
$unknownId = '4' * 64
$unlabeledId = '5' * 64
$projectName = 'weatherpoly'
$defaultNetwork = 'weatherpoly_default'
$safetyNetwork = 'weatherpoly_safety-plane'
$pinnedImage = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$pinnedImageId = 'sha256:' + ('e' * 64)
$pinnedRepoDigest = 'postgres@sha256:' + (
    $pinnedImage.Substring($pinnedImage.IndexOf('sha256:') + 7)
)
$composePrefix = @(
    'compose',
    '--project-directory',
    $tempRoot,
    '-f',
    (Join-Path $tempRoot 'docker-compose.yml'),
    '--env-file',
    (Join-Path $tempRoot '.env'),
    '--project-name',
    'weatherpoly'
) -join ' '
$fakePostgresConfigHash = 'b' * 64
$fakeBackupConfigHash = 'c' * 64
$postgresEntrypoint = @('docker-entrypoint.sh')
$postgresCommand = @(
    'postgres', '-c', 'hba_file=/etc/postgresql/stratus-pg_hba.conf',
    '-c', 'password_encryption=scram-sha-256'
)
$backupEntrypoint = @('docker-entrypoint.sh')
$backupCommand = @('/bin/sh', '-c', 'fixture-backup-loop')

function Get-FixtureCommandHash {
    param([object[]] $Entrypoint, [object[]] $Command)

    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(
        (@(@($Entrypoint) + @($Command)) -join "`0")
    )
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $hasher.ComputeHash($bytes)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}
$fakePostgresCommandHash = Get-FixtureCommandHash `
    -Entrypoint $postgresEntrypoint -Command $postgresCommand
$fakeBackupCommandHash = Get-FixtureCommandHash `
    -Entrypoint $backupEntrypoint -Command $backupCommand

function New-FakeInspect {
    param(
        [string] $Id,
        [string] $Name,
        [string] $Project,
        [string] $Service,
        [string[]] $Networks,
        [bool] $Running,
        [string] $ConfigImage = $pinnedImage
    )

    $networkObject = [ordered]@{}
    foreach ($network in $Networks) {
        $alias = if ($Service -eq 'postgres') { 'postgres' } elseif ($Service -eq 'backup') { 'backup' } else { $Service }
        $networkObject[$network] = [ordered]@{
            Aliases = @($Name, $alias)
            IPAddress = if ($Running) { '172.20.0.10' } else { '' }
        }
    }
    $isPostgres = $Service -eq 'postgres'
    $isBackup = $Service -eq 'backup'
    $entrypoint = if ($isBackup) { $backupEntrypoint } else { $postgresEntrypoint }
    $command = if ($isBackup) { $backupCommand } else { $postgresCommand }
    $configHash = if ($isBackup) { $fakeBackupConfigHash } else { $fakePostgresConfigHash }
    $mounts = if ($isBackup) {
        @(
            [ordered]@{
                Type = 'bind'; Name = $null; Source = (Join-Path $tempRoot 'backups')
                Destination = '/backups'; RW = $true
            },
            [ordered]@{
                Type = 'volume'; Name = ('e' * 64); Source = '/fixture/backup-data'
                Destination = '/var/lib/postgresql/data'; RW = $true
            }
        )
    }
    else {
        @(
            [ordered]@{
                Type = 'bind'; Name = $null
                Source = (Join-Path $tempRoot 'ops\postgres\stratus-pg_hba.conf')
                Destination = '/etc/postgresql/stratus-pg_hba.conf'; RW = $false
            },
            [ordered]@{
                Type = 'volume'; Name = 'weatherpoly_stratus-postgres'
                Source = '/fixture/postgres-data'
                Destination = '/var/lib/postgresql/data'; RW = $true
            }
        )
    }
    $inspect = [ordered]@{
        Id = $Id
        Name = "/$Name"
        Image = if ($ConfigImage -eq $pinnedImage) { $pinnedImageId } else { 'sha256:' + ('a' * 64) }
        Config = [ordered]@{
            Image = $ConfigImage
            Entrypoint = $entrypoint
            Cmd = $command
            Labels = [ordered]@{
                'com.docker.compose.project' = $Project
                'com.docker.compose.project.working_dir' = $tempRoot
                'com.docker.compose.service' = $Service
                'com.docker.compose.config-hash' = $configHash
                'com.docker.compose.image' = $pinnedImageId
                'com.docker.compose.oneoff' = 'False'
                'com.docker.compose.container-number' = '1'
                'com.docker.compose.project.config_files' = @(
                    (Join-Path $tempRoot 'docker-compose.yml'),
                    (Join-Path $tempRoot 'docker-compose.override.yml')
                ) -join ','
            }
        }
        State = [ordered]@{
            Status = if ($Running) { 'running' } else { 'exited' }
            Running = $Running; Paused = $false; Restarting = $false
            OOMKilled = $false; Dead = $false; Pid = if ($Running) { 123 } else { 0 }
            ExitCode = 0; Error = ''
        }
        RestartCount = 0
        HostConfig = [ordered]@{
            RestartPolicy = if ($isBackup) {
                [ordered]@{ Name = 'on-failure'; MaximumRetryCount = 5 }
            }
            else {
                [ordered]@{ Name = 'unless-stopped'; MaximumRetryCount = 0 }
            }
            PortBindings = if ($isPostgres) {
                [ordered]@{
                    '5432/tcp' = @([ordered]@{ HostIp = '127.0.0.1'; HostPort = '5432' })
                }
            }
            else { [ordered]@{} }
        }
        Mounts = $mounts
        NetworkSettings = [ordered]@{ Networks = $networkObject }
    }
    if ($isPostgres -and $global:StratusTopologyDrift -eq 'postgres-dirty-exit') {
        $inspect.State.ExitCode = 137
        $inspect.State.Error = 'fixture crash'
    }
    if ($isPostgres -and $global:StratusTopologyDrift -eq 'postgres-port') {
        $inspect.HostConfig.PortBindings.'5432/tcp'[0].HostIp = '0.0.0.0'
    }
    if ($isPostgres -and $global:StratusTopologyDrift -eq 'postgres-mount') {
        $inspect.Mounts[1].Name = 'wrong-volume'
    }
    if ($isPostgres -and $global:StratusTopologyDrift -eq 'postgres-config-files') {
        $inspect.Config.Labels.'com.docker.compose.project.config_files' = (
            Join-Path $tempRoot 'docker-compose.override.yml'
        )
    }
    if ($isPostgres -and $global:StratusTopologyDrift -eq 'postgres-main-only') {
        $inspect.Config.Labels.'com.docker.compose.project.config_files' = (
            Join-Path $tempRoot 'docker-compose.yml'
        )
    }
    if ($isBackup -and $global:StratusTopologyDrift -eq 'backup-command') {
        $inspect.Config.Cmd = @('/bin/sh', '-c', 'unexpected-writer')
    }
    return $inspect
}

function global:docker {
    $arguments = @($args | ForEach-Object { [string]$_ })
    $joined = $arguments -join ' '
    $global:StratusTopologyCommands += $joined
    $global:LASTEXITCODE = 0
    if ($joined -eq "image inspect $pinnedImage --format {{json .}}") {
        $imageInspect = [ordered]@{
            Id = if ($global:StratusTopologyPinnedImageDrift -eq 'local-id') {
                'sha256:' + ('f' * 64)
            }
            else { $pinnedImageId }
            RepoDigests = @($pinnedRepoDigest)
            Os = if ($global:StratusTopologyPinnedImageDrift -eq 'platform') {
                'windows'
            }
            else { 'linux' }
            Architecture = 'amd64'
        }
        if ($global:StratusTopologyPinnedImageDrift -eq 'scalar-repodigests') {
            $imageInspect.RepoDigests = $pinnedRepoDigest
        }
        elseif ($global:StratusTopologyPinnedImageDrift -eq 'repo-digest') {
            $imageInspect.RepoDigests = @('postgres@sha256:' + ('f' * 64))
        }
        return ($imageInspect | ConvertTo-Json -Depth 6 -Compress)
    }
    if ($joined -eq "$composePrefix ps --all -q postgres") {
        return $postgresId
    }
    if ($joined -eq "$composePrefix ps --status running -q backup") {
        return $backupId
    }
    if ($joined -eq 'ps -q --no-trunc') {
        $ids = @($backupId)
        if ($global:StratusTopologyPostgresRunning) {
            $ids += $postgresId
        }
        if ($global:StratusTopologyExtra -eq 'migrate') {
            $ids += $migrateId
        }
        if ($global:StratusTopologyExtra -eq 'unknown') {
            $ids += $unknownId
        }
        if ($global:StratusTopologyExtra -eq 'unlabeled') {
            $ids += $unlabeledId
        }
        return $ids
    }
    if ($joined -eq "inspect $postgresId --format {{json .}}") {
        return (New-FakeInspect -Id $postgresId -Name 'weatherpoly-postgres-1' `
            -Project $projectName -Service 'postgres' `
            -Networks @($defaultNetwork) `
            -Running $global:StratusTopologyPostgresRunning | ConvertTo-Json -Depth 8 -Compress)
    }
    if ($joined -eq "inspect $backupId --format {{json .}}") {
        return (New-FakeInspect -Id $backupId -Name 'weatherpoly-backup-1' `
            -Project $projectName -Service 'backup' -Networks @($defaultNetwork) `
            -Running $true | ConvertTo-Json -Depth 8 -Compress)
    }
    if ($joined -eq "inspect $migrateId --format {{json .}}") {
        return (New-FakeInspect -Id $migrateId -Name 'weatherpoly-migrate-run-deadbeef' `
            -Project $projectName -Service 'migrate' -Networks @($defaultNetwork) `
            -Running $true -ConfigImage 'stratus:candidate-fixture' |
            ConvertTo-Json -Depth 8 -Compress)
    }
    if ($joined -eq "inspect $unknownId --format {{json .}}") {
        return (New-FakeInspect -Id $unknownId -Name 'hidden-writer' `
            -Project 'other' -Service '' -Networks @($safetyNetwork) `
            -Running $true -ConfigImage 'unknown:latest' |
            ConvertTo-Json -Depth 8 -Compress)
    }
    if ($joined -eq "inspect $unlabeledId --format {{json .}}") {
        return ([ordered]@{
            Id = $unlabeledId
            Name = '/unrelated-unlabeled'
            Image = 'sha256:' + ('f' * 64)
            Config = [ordered]@{ Image = 'unrelated:latest'; Labels = $null }
            State = [ordered]@{ Running = $true; OOMKilled = $false }
            NetworkSettings = [ordered]@{
                Networks = [ordered]@{ unrelated_default = [ordered]@{} }
            }
        } | ConvertTo-Json -Depth 8 -Compress)
    }
    $global:LASTEXITCODE = 90
    return 'unexpected fake docker invocation: ' + $joined
}

$gateArguments = @{
    Root = $tempRoot
    RequirePostgresStopped = $true
    ExpectedPostgresId = $postgresId
    ExpectedBackupId = $backupId
    ExpectedPostgresConfigHash = $fakePostgresConfigHash
    ExpectedBackupConfigHash = $fakeBackupConfigHash
    ExpectedPostgresCommandSha256 = $fakePostgresCommandHash
    ExpectedBackupCommandSha256 = $fakeBackupCommandHash
    ExpectedHbaSha256 = $fixtureHbaSha256
}

function Invoke-FixtureGate {
    & $gatePath @gateArguments
}

try {
    $accepted = Invoke-FixtureGate | ConvertFrom-Json
    if (
        -not $accepted.verified -or
        -not $accepted.read_only -or
        $accepted.allowed_running_backup_count -ne 1 -or
        $accepted.allowed_running_backup_id -ne $backupId -or
        $accepted.postgres_config_hash -ne $fakePostgresConfigHash -or
        $accepted.postgres_command_sha256 -ne $fakePostgresCommandHash -or
        $accepted.hba_sha256 -ne $fixtureHbaSha256 -or
        $accepted.unexpected_running_count -ne 0 -or
        @($accepted.protected_networks).Count -ne 2 -or
        @($accepted.protected_networks) -notcontains $safetyNetwork
    ) {
        throw 'Safe stopped-PostgreSQL fixture did not pass the topology gate.'
    }

    $global:StratusTopologyExtra = 'migrate'
    Assert-GateFailure -Label 'running migrate container' `
        -ExpectedMessage 'Unexpected running project/protected-network containers' -Action {
            Invoke-FixtureGate
        }
    if (@($global:StratusTopologyCommands | Where-Object {
        $_ -match '(^|\s)start(\s|$)'
    }).Count -ne 0) {
        throw 'Running migrate fixture reached a Docker start command.'
    }

    $global:StratusTopologyExtra = 'unknown'
    Assert-GateFailure -Label 'hidden protected-network container' `
        -ExpectedMessage 'Unexpected running project/protected-network containers' -Action {
            Invoke-FixtureGate
        }

    $global:StratusTopologyExtra = 'none'
    $global:StratusTopologyExtra = 'unlabeled'
    $unlabeledAccepted = Invoke-FixtureGate |
        ConvertFrom-Json
    if (-not $unlabeledAccepted.verified) {
        throw 'Unrelated unlabeled container was not safely ignored.'
    }

    $global:StratusTopologyExtra = 'none'
    $originalExpectedPostgresId = $gateArguments.ExpectedPostgresId
    $gateArguments.ExpectedPostgresId = 'f' * 64
    Assert-GateFailure -Label 'recreated PostgreSQL identity' `
        -ExpectedMessage 'identity is invalid' -Action {
            Invoke-FixtureGate
        }
    $gateArguments.ExpectedPostgresId = $originalExpectedPostgresId

    $originalExpectedBackupId = $gateArguments.ExpectedBackupId
    $gateArguments.ExpectedBackupId = 'd' * 64
    Assert-GateFailure -Label 'recreated backup identity' `
        -ExpectedMessage 'exact allowed Compose identity' -Action {
            Invoke-FixtureGate
        }
    $gateArguments.ExpectedBackupId = $originalExpectedBackupId

    foreach ($driftFixture in @(
        [pscustomobject]@{ Drift = 'postgres-dirty-exit'; Message = 'identity is invalid' },
        [pscustomobject]@{ Drift = 'postgres-port'; Message = 'identity is invalid' },
        [pscustomobject]@{ Drift = 'postgres-mount'; Message = 'identity is invalid' },
        [pscustomobject]@{ Drift = 'postgres-config-files'; Message = 'identity is invalid' },
        [pscustomobject]@{ Drift = 'postgres-main-only'; Message = 'identity is invalid' },
        [pscustomobject]@{ Drift = 'backup-command'; Message = 'exact allowed Compose identity' }
    )) {
        $global:StratusTopologyDrift = $driftFixture.Drift
        Assert-GateFailure -Label $driftFixture.Drift `
            -ExpectedMessage $driftFixture.Message -Action {
                Invoke-FixtureGate
            }
    }
    $global:StratusTopologyDrift = 'none'

    foreach ($imageDrift in @('repo-digest', 'scalar-repodigests', 'platform')) {
        $global:StratusTopologyPinnedImageDrift = $imageDrift
        Assert-GateFailure -Label "pinned image $imageDrift" `
            -ExpectedMessage 'exact pinned RepoDigest and linux/amd64 config image' -Action {
                Invoke-FixtureGate
            }
    }
    $global:StratusTopologyPinnedImageDrift = 'local-id'
    Assert-GateFailure -Label 'registry index digest used as local image ID' `
        -ExpectedMessage 'identity is invalid' -Action {
            Invoke-FixtureGate
        }
    $global:StratusTopologyPinnedImageDrift = 'none'

    $env:COMPOSE_PROFILES = 'hostile'
    try {
        Assert-GateFailure -Label 'hostile Compose profile override' `
            -ExpectedMessage 'override environment variable is forbidden' -Action {
                Invoke-FixtureGate
            }
    }
    finally {
        Remove-Item Env:\COMPOSE_PROFILES -ErrorAction SilentlyContinue
    }

    $originalExpectedHbaSha = $gateArguments.ExpectedHbaSha256
    $gateArguments.ExpectedHbaSha256 = 'a' * 64
    Assert-GateFailure -Label 'drifted pre-start HBA' `
        -ExpectedMessage 'identity is invalid' -Action {
            Invoke-FixtureGate
        }
    $gateArguments.ExpectedHbaSha256 = $originalExpectedHbaSha

    $global:StratusTopologyPostgresRunning = $true
    Assert-GateFailure -Label 'already-running PostgreSQL' `
        -ExpectedMessage 'must still be stopped' -Action {
            Invoke-FixtureGate
        }
}
finally {
    Remove-Item Function:\docker -ErrorAction SilentlyContinue
    Remove-Variable StratusTopologyPinnedImageDrift -Scope Global -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

[pscustomobject]@{
    script = $gatePath
    syntax_valid = $true
    read_only = $true
    running_migrate_rejected_before_start = $true
    hidden_network_container_rejected = $true
    unrelated_unlabeled_container_ignored = $true
    exact_retained_identities_required = $true
    stopped_postgres_required = $true
    validated = $true
} | ConvertTo-Json -Compress
