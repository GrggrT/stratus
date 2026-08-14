[CmdletBinding()]
param(
    [string] $Root = '.',

    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$')]
    [string] $ExpectedPostgresName = 'weatherpoly-postgres-1',

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedPostgresId =
        '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299',

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedBackupId =
        '3241d8921b44c6dad0b8c7196daa8dd23f13c111349b90a898234316db68f977',

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedPostgresConfigHash =
        'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1',

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedBackupConfigHash =
        '99f74f44079be815bc9e3a18cfa7282695c9ca660be74b5bafafc0e0a0a737b4',

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedPostgresCommandSha256 =
        '3e1396d8c083a0274909460787db163a1f9070eeabb659c403c5c7ca890436b3',

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedBackupCommandSha256 =
        'c39fc498a3b7eee28c15a18ef502adb9f807e818cb7a8ff49a2d2175baa6dad2',

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedHbaSha256 =
        '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4',

    [switch] $RequirePostgresStopped
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The default IDs and hashes are the code-reviewed code-only production
# baseline. A recreated retained database or backup sidecar requires a new
# reviewed source change; discovering the current container at cutover time is
# deliberately not accepted as identity evidence.

function Invoke-DockerJsonInspect {
    param([Parameter(Mandatory = $true)][string] $ContainerId)

    $raw = docker inspect $ContainerId --format '{{json .}}'
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect Docker container $ContainerId."
    }
    try {
        return $raw | ConvertFrom-Json
    }
    catch {
        throw "Docker returned invalid inspect JSON for $ContainerId."
    }
}

function Invoke-DockerImageJsonInspect {
    param([Parameter(Mandatory = $true)][string] $ImageReference)

    $raw = docker image inspect $ImageReference --format '{{json .}}'
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect exact local Docker image $ImageReference."
    }
    try {
        return $raw | ConvertFrom-Json
    }
    catch {
        throw "Docker returned invalid image-inspect JSON for $ImageReference."
    }
}

function Get-NetworkNames {
    param([Parameter(Mandatory = $true)] $Inspect)

    if ($null -eq $Inspect.NetworkSettings.Networks) {
        return @()
    }
    return @($Inspect.NetworkSettings.Networks.PSObject.Properties.Name)
}

function Get-OptionalPropertyValue {
    param(
        [AllowNull()] $InputObject,
        [Parameter(Mandatory = $true)][string] $Name
    )

    if ($null -eq $InputObject) {
        return $null
    }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-CommandSha256 {
    param([Parameter(Mandatory = $true)] $Inspect)

    $command = @(@($Inspect.Config.Entrypoint) + @($Inspect.Config.Cmd)) -join "`0"
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($command)
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

function Get-DockerHostPathAliases {
    param([Parameter(Mandatory = $true)][string] $Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $normalized = $fullPath.Replace('\', '/')
    $aliases = [Collections.Generic.List[string]]::new()
    $aliases.Add($normalized)
    if ($normalized -match '^([A-Za-z]):/(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $tail = $Matches[2]
        $aliases.Add("/run/desktop/mnt/host/$drive/$tail")
        $aliases.Add("/host_mnt/$drive/$tail")
    }
    return @($aliases)
}

function Test-ComposeConfigFilesLabel {
    param(
        [AllowNull()][string] $Label,
        [Parameter(Mandatory = $true)][string] $ExpectedComposePath
    )

    if ([string]::IsNullOrWhiteSpace($Label)) {
        return $false
    }
    $paths = @(
        $Label -split ',' |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($paths.Count -ne 2) {
        return $false
    }
    $expectedMain = [IO.Path]::GetFullPath($ExpectedComposePath)
    $resolved = @($paths | ForEach-Object {
        [IO.Path]::GetFullPath([string]$_)
    })
    if ($resolved[0] -ine $expectedMain) {
        return $false
    }
    $expectedOverride = Join-Path (
        [IO.Path]::GetDirectoryName($expectedMain)
    ) 'docker-compose.override.yml'
    return (
        $resolved[1] -ieq [IO.Path]::GetFullPath($expectedOverride) -and
        (Test-Path -LiteralPath $expectedOverride -PathType Leaf) -and
        -not ((Get-Item -LiteralPath $expectedOverride -Force).Attributes -band
            [IO.FileAttributes]::ReparsePoint)
    )
}

$absoluteRoot = [IO.Path]::GetFullPath($Root)
$composePath = Join-Path $absoluteRoot 'docker-compose.yml'
$composeEnvPath = Join-Path $absoluteRoot '.env'
$composeProjectName = 'weatherpoly'
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
if (
    -not (Test-Path -LiteralPath $composePath -PathType Leaf) -or
    ((Get-Item -LiteralPath $composePath -Force).Attributes -band
        [IO.FileAttributes]::ReparsePoint)
) {
    throw "Missing docker-compose.yml under $absoluteRoot."
}
if (
    -not (Test-Path -LiteralPath $composeEnvPath -PathType Leaf) -or
    ((Get-Item -LiteralPath $composeEnvPath -Force).Attributes -band
        [IO.FileAttributes]::ReparsePoint)
) {
    throw "Missing regular release .env under $absoluteRoot."
}
if (@(
    Get-Content -LiteralPath $composeEnvPath |
        Where-Object {
            $_ -match '^\s*(?:export\s+)?COMPOSE_(?:FILE|PROFILES|PROJECT_NAME|PATH_SEPARATOR)\s*='
        }
).Count -ne 0) {
    throw 'Release .env must not define a reserved Compose override variable.'
}
$composeArguments = @(
    'compose',
    '--project-directory',
    $absoluteRoot,
    '-f',
    $composePath,
    '--env-file',
    $composeEnvPath,
    '--project-name',
    $composeProjectName
)

Push-Location $absoluteRoot
try {
    $postgresIds = @(
        & docker @composeArguments ps --all -q postgres |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($LASTEXITCODE -ne 0 -or $postgresIds.Count -ne 1) {
        throw 'Expected exactly one retained production PostgreSQL container.'
    }
    $postgres = Invoke-DockerJsonInspect -ContainerId ([string]$postgresIds[0])
    $postgresLabels = $postgres.Config.Labels
    $projectName = [string](Get-OptionalPropertyValue `
        -InputObject $postgresLabels -Name 'com.docker.compose.project')
    $projectWorkingDir = [string](Get-OptionalPropertyValue `
        -InputObject $postgresLabels `
        -Name 'com.docker.compose.project.working_dir')
    $postgresService = [string](Get-OptionalPropertyValue `
        -InputObject $postgresLabels -Name 'com.docker.compose.service')
    $postgresConfigHash = [string](Get-OptionalPropertyValue `
        -InputObject $postgresLabels -Name 'com.docker.compose.config-hash')
    $postgresComposeImage = [string](Get-OptionalPropertyValue `
        -InputObject $postgresLabels -Name 'com.docker.compose.image')
    $postgresOneoff = [string](Get-OptionalPropertyValue `
        -InputObject $postgresLabels -Name 'com.docker.compose.oneoff')
    $postgresContainerNumber = [string](Get-OptionalPropertyValue `
        -InputObject $postgresLabels -Name 'com.docker.compose.container-number')
    $postgresConfigFiles = [string](Get-OptionalPropertyValue `
        -InputObject $postgresLabels -Name 'com.docker.compose.project.config_files')
    $postgresNetworks = @(Get-NetworkNames -Inspect $postgres)
    $expectedDefaultNetwork = "${projectName}_default"
    $expectedSafetyNetwork = "${projectName}_safety-plane"
    $protectedNetworks = @($expectedDefaultNetwork, $expectedSafetyNetwork)
    $pinnedPostgresImage = (
        'postgres:16-alpine@sha256:' +
        '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
    )
    $pinnedPostgresManifestDigest = $pinnedPostgresImage.Substring(
        $pinnedPostgresImage.IndexOf('sha256:') + 7
    )
    $pinnedPostgresRepoDigestPattern = (
        '^(?:docker\.io/)?(?:library/)?postgres@sha256:' +
        [regex]::Escape($pinnedPostgresManifestDigest) +
        '$'
    )
    $pinnedPostgresInspect = Invoke-DockerImageJsonInspect `
        -ImageReference $pinnedPostgresImage
    $pinnedPostgresImageId = [string]$pinnedPostgresInspect.Id
    $pinnedRepoDigests = $pinnedPostgresInspect.RepoDigests
    if (
        $pinnedPostgresImageId -notmatch '^sha256:[0-9a-f]{64}$' -or
        [string]$pinnedPostgresInspect.Os -ne 'linux' -or
        [string]$pinnedPostgresInspect.Architecture -ne 'amd64' -or
        $pinnedRepoDigests -isnot [object[]] -or
        @($pinnedRepoDigests | Where-Object {
            $_ -is [string] -and [string]$_ -match $pinnedPostgresRepoDigestPattern
        }).Count -ne 1
    ) {
        throw 'Local PostgreSQL image does not prove the exact pinned RepoDigest and linux/amd64 config image.'
    }
    $postgresPortProperties = @(
        $postgres.HostConfig.PortBindings.PSObject.Properties |
            Where-Object { $null -ne $_.Value }
    )
    $postgresPort = $postgres.HostConfig.PortBindings.'5432/tcp'
    $postgresHbaMounts = @($postgres.Mounts | Where-Object {
        [string]$_.Destination -eq '/etc/postgresql/stratus-pg_hba.conf'
    })
    $postgresDataMounts = @($postgres.Mounts | Where-Object {
        [string]$_.Destination -eq '/var/lib/postgresql/data'
    })
    $expectedHbaPath = Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    $expectedHbaItem = Get-Item -LiteralPath $expectedHbaPath -Force
    $expectedHbaHash = (
        Get-FileHash -LiteralPath $expectedHbaPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $expectedHbaSources = @(Get-DockerHostPathAliases -Path $expectedHbaPath)
    $postgresDefaultNetworkProperty = $postgres.NetworkSettings.Networks.PSObject.Properties[
        $expectedDefaultNetwork
    ]
    $postgresDefaultEndpoint = if ($null -eq $postgresDefaultNetworkProperty) {
        $null
    }
    else {
        $postgresDefaultNetworkProperty.Value
    }
    if ($RequirePostgresStopped -and $postgres.State.Running) {
        throw 'Production PostgreSQL must still be stopped at the pre-start boundary.'
    }
    if (
        [string]$postgres.Name -ne "/$ExpectedPostgresName" -or
        [string]$postgres.Id -ne $ExpectedPostgresId -or
        [string]$postgres.Image -ne $pinnedPostgresImageId -or
        [string]$postgres.Config.Image -ne $pinnedPostgresImage -or
        (Get-CommandSha256 -Inspect $postgres) -ne $ExpectedPostgresCommandSha256 -or
        $postgresService -ne 'postgres' -or
        $postgresConfigHash -ne $ExpectedPostgresConfigHash -or
        $postgresComposeImage -ne $pinnedPostgresImageId -or
        $postgresOneoff -ne 'False' -or
        $postgresContainerNumber -ne '1' -or
        -not (Test-ComposeConfigFilesLabel -Label $postgresConfigFiles `
            -ExpectedComposePath $composePath) -or
        $projectName -ne $composeProjectName -or
        [string]::IsNullOrWhiteSpace($projectWorkingDir) -or
        [IO.Path]::GetFullPath($projectWorkingDir) -ine $absoluteRoot -or
        $postgresNetworks.Count -lt 1 -or
        $postgresNetworks.Count -gt 2 -or
        $postgresNetworks -notcontains $expectedDefaultNetwork -or
        @($postgresNetworks | Where-Object { $_ -notin $protectedNetworks }).Count -ne 0 -or
        $null -eq $postgresDefaultEndpoint -or
        @($postgresDefaultEndpoint.Aliases) -notcontains 'postgres' -or
        $postgres.State.Status -ne 'exited' -or
        $postgres.State.Running -or
        $postgres.State.Paused -or
        $postgres.State.Restarting -or
        $postgres.State.OOMKilled -or
        $postgres.State.Dead -or
        [int]$postgres.State.Pid -ne 0 -or
        [int]$postgres.State.ExitCode -ne 0 -or
        -not [string]::IsNullOrWhiteSpace([string]$postgres.State.Error) -or
        [int]$postgres.RestartCount -ne 0 -or
        [string]$postgres.HostConfig.RestartPolicy.Name -ne 'unless-stopped' -or
        $postgresPortProperties.Count -ne 1 -or
        $postgresPortProperties[0].Name -ne '5432/tcp' -or
        @($postgresPort).Count -ne 1 -or
        [string]$postgresPort[0].HostIp -ne '127.0.0.1' -or
        [string]$postgresPort[0].HostPort -ne '5432' -or
        @($postgres.Mounts).Count -ne 2 -or
        $postgresHbaMounts.Count -ne 1 -or
        ($expectedHbaItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $expectedHbaHash -ne $ExpectedHbaSha256 -or
        [string]$postgresHbaMounts[0].Type -ne 'bind' -or
        [bool]$postgresHbaMounts[0].RW -or
        ([string]$postgresHbaMounts[0].Source).Replace('\', '/') -notin
            $expectedHbaSources -or
        $postgresDataMounts.Count -ne 1 -or
        [string]$postgresDataMounts[0].Type -ne 'volume' -or
        [string]$postgresDataMounts[0].Name -ne 'weatherpoly_stratus-postgres' -or
        -not [bool]$postgresDataMounts[0].RW
    ) {
        throw 'Retained production PostgreSQL Compose identity is invalid.'
    }

    $backupIds = @(
        & docker @composeArguments ps --status running -q backup |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($LASTEXITCODE -ne 0 -or $backupIds.Count -gt 1) {
        throw 'Expected at most one running Compose backup sidecar.'
    }
    $allowedIds = @([string]$postgres.Id)
    foreach ($backupId in $backupIds) {
        $backup = Invoke-DockerJsonInspect -ContainerId ([string]$backupId)
        $backupLabels = $backup.Config.Labels
        $backupNetworks = @(Get-NetworkNames -Inspect $backup)
        $backupNetworkProperty = $backup.NetworkSettings.Networks.PSObject.Properties[
            $expectedDefaultNetwork
        ]
        $backupDefaultEndpoint = if ($null -eq $backupNetworkProperty) {
            $null
        }
        else {
            $backupNetworkProperty.Value
        }
        $backupConfigFiles = [string](Get-OptionalPropertyValue `
            -InputObject $backupLabels -Name 'com.docker.compose.project.config_files')
        $backupPortProperties = @(
            $backup.HostConfig.PortBindings.PSObject.Properties |
                Where-Object { $null -ne $_.Value }
        )
        $backupArchiveMounts = @($backup.Mounts | Where-Object {
            [string]$_.Destination -eq '/backups'
        })
        $backupDataMounts = @($backup.Mounts | Where-Object {
            [string]$_.Destination -eq '/var/lib/postgresql/data'
        })
        $expectedBackupSources = @(Get-DockerHostPathAliases -Path (
            Join-Path $absoluteRoot 'backups'
        ))
        if (
            [string]$backup.Name -ne '/weatherpoly-backup-1' -or
            [string]$backup.Id -ne $ExpectedBackupId -or
            [string]$backup.Image -ne $pinnedPostgresImageId -or
            -not $backup.State.Running -or
            [string]$backup.State.Status -ne 'running' -or
            $backup.State.Paused -or
            $backup.State.Restarting -or
            $backup.State.OOMKilled -or
            $backup.State.Dead -or
            [int]$backup.State.Pid -le 0 -or
            -not [string]::IsNullOrWhiteSpace([string]$backup.State.Error) -or
            [int]$backup.RestartCount -ne 0 -or
            [string]$backup.HostConfig.RestartPolicy.Name -ne 'on-failure' -or
            [int]$backup.HostConfig.RestartPolicy.MaximumRetryCount -ne 5 -or
            [string](Get-OptionalPropertyValue -InputObject $backupLabels `
                -Name 'com.docker.compose.project') -ne $projectName -or
            [string](Get-OptionalPropertyValue -InputObject $backupLabels `
                -Name 'com.docker.compose.service') -ne 'backup' -or
            [string](Get-OptionalPropertyValue -InputObject $backupLabels `
                -Name 'com.docker.compose.project.working_dir') -ine $absoluteRoot -or
            [string](Get-OptionalPropertyValue -InputObject $backupLabels `
                -Name 'com.docker.compose.config-hash') -ne $ExpectedBackupConfigHash -or
            [string](Get-OptionalPropertyValue -InputObject $backupLabels `
                -Name 'com.docker.compose.image') -ne $pinnedPostgresImageId -or
            [string](Get-OptionalPropertyValue -InputObject $backupLabels `
                -Name 'com.docker.compose.oneoff') -ne 'False' -or
            [string](Get-OptionalPropertyValue -InputObject $backupLabels `
                -Name 'com.docker.compose.container-number') -ne '1' -or
            -not (Test-ComposeConfigFilesLabel -Label $backupConfigFiles `
                -ExpectedComposePath $composePath) -or
            [string]$backup.Config.Image -ne $pinnedPostgresImage -or
            (Get-CommandSha256 -Inspect $backup) -ne $ExpectedBackupCommandSha256 -or
            $backupNetworks.Count -ne 1 -or
            $backupNetworks[0] -ne $expectedDefaultNetwork -or
            $null -eq $backupDefaultEndpoint -or
            @($backupDefaultEndpoint.Aliases) -notcontains 'backup' -or
            $backupPortProperties.Count -ne 0 -or
            @($backup.Mounts).Count -ne 2 -or
            $backupArchiveMounts.Count -ne 1 -or
            [string]$backupArchiveMounts[0].Type -ne 'bind' -or
            -not [bool]$backupArchiveMounts[0].RW -or
            ([string]$backupArchiveMounts[0].Source).Replace('\', '/') -notin
                $expectedBackupSources -or
            $backupDataMounts.Count -ne 1 -or
            [string]$backupDataMounts[0].Type -ne 'volume' -or
            -not [bool]$backupDataMounts[0].RW
        ) {
            throw 'Running backup sidecar does not have the exact allowed Compose identity.'
        }
        $allowedIds += [string]$backup.Id
    }

    $runningIds = @(
        docker ps -q --no-trunc |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not enumerate running Docker containers before PostgreSQL start.'
    }
    $unexpected = @()
    foreach ($runningId in $runningIds) {
        $running = Invoke-DockerJsonInspect -ContainerId ([string]$runningId)
        if ($allowedIds -contains [string]$running.Id) {
            continue
        }
        $runningProject = [string](Get-OptionalPropertyValue `
            -InputObject $running.Config.Labels -Name 'com.docker.compose.project')
        $runningNetworks = @(Get-NetworkNames -Inspect $running)
        $sharesProtectedNetwork = @(
            $runningNetworks | Where-Object { $_ -in $protectedNetworks }
        ).Count -ne 0
        if ($runningProject -eq $projectName -or $sharesProtectedNetwork) {
            $service = [string](Get-OptionalPropertyValue `
                -InputObject $running.Config.Labels -Name 'com.docker.compose.service')
            if ([string]::IsNullOrWhiteSpace($service)) {
                $service = 'non-compose-or-unknown'
            }
            $unexpected += "$($running.Name):$service"
        }
    }
    if ($unexpected.Count -ne 0) {
        throw (
            'Unexpected running project/protected-network containers before PostgreSQL start: ' +
            ($unexpected -join ', ')
        )
    }

    [pscustomobject]@{
        verified = $true
        read_only = $true
        postgres_container_id = [string]$postgres.Id
        postgres_image_id = [string]$postgres.Image
        postgres_pinned_repo_digest = "postgres@sha256:$pinnedPostgresManifestDigest"
        postgres_config_hash = $postgresConfigHash
        postgres_command_sha256 = Get-CommandSha256 -Inspect $postgres
        postgres_running = [bool]$postgres.State.Running
        hba_sha256 = $ExpectedHbaSha256
        compose_project = $projectName
        protected_networks = $protectedNetworks
        allowed_running_backup_count = $backupIds.Count
        allowed_running_backup_id = if ($backupIds.Count -eq 1) {
            [string]$backupIds[0]
        }
        else {
            $null
        }
        unexpected_running_count = 0
    } | ConvertTo-Json -Depth 4 -Compress
}
finally {
    Pop-Location
}
