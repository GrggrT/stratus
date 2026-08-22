[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^stratus:candidate-[0-9a-f]{12}$')]
    [string] $Image,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedDigest,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedRevision,

    [string] $EnvFile = '.env',

    [string] $Root = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    $matches = @(
        Get-Content -LiteralPath $Path |
            Where-Object { $_ -match "^$([regex]::Escape($Name))=" }
    )
    if ($matches.Count -ne 1) {
        throw "Expected exactly one $Name entry in $Path."
    }
    return ($matches[0] -split '=', 2)[1]
}

$absoluteEnvFile = [IO.Path]::GetFullPath($EnvFile)
$absoluteRoot = if ([string]::IsNullOrWhiteSpace($Root)) {
    [IO.Path]::GetDirectoryName($absoluteEnvFile)
}
else {
    [IO.Path]::GetFullPath($Root)
}
$composePath = Join-Path $absoluteRoot 'docker-compose.yml'
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
    -not (Test-Path -LiteralPath $absoluteEnvFile -PathType Leaf) -or
    ((Get-Item -LiteralPath $absoluteEnvFile -Force).Attributes -band
        [IO.FileAttributes]::ReparsePoint)
) {
    throw "Release environment file does not exist or is a link: $absoluteEnvFile"
}
if (
    -not (Test-Path -LiteralPath $composePath -PathType Leaf) -or
    ((Get-Item -LiteralPath $composePath -Force).Attributes -band
        [IO.FileAttributes]::ReparsePoint)
) {
    throw "Exact release Compose file does not exist or is a link: $composePath"
}
if (@(
    Get-Content -LiteralPath $absoluteEnvFile |
        Where-Object {
            $_ -match '^\s*(?:export\s+)?COMPOSE_(?:FILE|PROFILES|PROJECT_NAME|PATH_SEPARATOR)\s*='
        }
).Count -ne 0) {
    throw 'Release environment file must not define a reserved Compose override variable.'
}
$composeArguments = @(
    'compose',
    '--project-directory',
    $absoluteRoot,
    '-f',
    $composePath,
    '--env-file',
    $absoluteEnvFile,
    '--project-name',
    $composeProjectName
)

$inspectJson = docker image inspect $Image
if ($LASTEXITCODE -ne 0) {
    throw "Docker image does not exist locally: $Image"
}

$inspectedImages = @($inspectJson | ConvertFrom-Json)
if ($inspectedImages.Count -ne 1) {
    throw 'Unexpected docker image inspect output.'
}
$inspectedImage = $inspectedImages[0]

$actualId = [string] $inspectedImage.Id
$actualOs = [string] $inspectedImage.Os
$actualArchitecture = [string] $inspectedImage.Architecture
$actualRevision = [string] $inspectedImage.Config.Labels.'org.opencontainers.image.revision'

if ($actualId -ne "sha256:$ExpectedDigest") {
    throw "Image ID mismatch: expected sha256:$ExpectedDigest, observed $actualId."
}
if ($actualOs -ne 'linux' -or $actualArchitecture -ne 'amd64') {
    throw "Release image platform is $actualOs/$actualArchitecture, expected linux/amd64."
}
if ($actualRevision -ne $ExpectedRevision) {
    throw "Image revision mismatch: expected $ExpectedRevision, observed $actualRevision."
}
if (@($inspectedImage.Config.Env | Where-Object {
    [string]$_ -imatch '^OPERATOR_API_KEY='
}).Count -ne 0) {
    throw 'Candidate image bakes OPERATOR_API_KEY into its immutable environment.'
}
if (
    [string]$inspectedImage.Config.User -cne 'stratus' -or
    $null -ne $inspectedImage.Config.Entrypoint
) {
    throw 'Candidate image must inherit no entrypoint and must run as the fixed stratus user.'
}

$envImage = Read-EnvValue -Path $absoluteEnvFile -Name 'STRATUS_IMAGE'
$envDigest = Read-EnvValue -Path $absoluteEnvFile -Name 'STRATUS_RELEASE_IMAGE_DIGEST'
if ($envImage -ne $Image) {
    throw "STRATUS_IMAGE mismatch: expected $Image, observed $envImage."
}
if ($envDigest -ne $ExpectedDigest) {
    throw 'STRATUS_RELEASE_IMAGE_DIGEST does not match the inspected candidate image.'
}

$defaultComposeJson = & docker @composeArguments config --format json
if ($LASTEXITCODE -ne 0) {
    throw 'Default rendered Compose configuration is invalid.'
}
$defaultCompose = $defaultComposeJson | ConvertFrom-Json
$defaultServices = @()
if ($null -ne $defaultCompose.services) {
    $defaultServices = @(
        $defaultCompose.services.PSObject.Properties |
            ForEach-Object { $_.Name } |
            Sort-Object
    )
}
if ($defaultServices.Count -ne 0) {
    throw (
        'Default Compose model must contain no services; ' +
        "observed: $($defaultServices -join ', ')."
    )
}

$releaseComposeJson = & docker @composeArguments `
    --profile release `
    config `
    --format json
if ($LASTEXITCODE -ne 0) {
    throw 'Rendered release-profile Compose configuration is invalid.'
}
$releaseCompose = $releaseComposeJson | ConvertFrom-Json
$releaseServices = @(
    $releaseCompose.services.PSObject.Properties |
        ForEach-Object { $_.Name } |
        Sort-Object
)
$expectedReleaseServices = @(
    'api',
    'backup',
    'migrate',
    'postgres',
    'provision-runtime-role',
    'provision-safety-role',
    'safety-control',
    'validate-owner-password',
    'validate-safety-credentials',
    'worker'
) | Sort-Object
if (
    $releaseServices.Count -ne $expectedReleaseServices.Count -or
    ($releaseServices -join "`n") -cne ($expectedReleaseServices -join "`n")
) {
    throw 'The release profile does not render the exact reviewed release service inventory.'
}

$expectedIsolatedProfileServices = [ordered]@{
    infrastructure = @(
        'backup',
        'postgres',
        'validate-owner-password'
    ) | Sort-Object
    binance = @(
        'binance-market-stream',
        'migrate',
        'postgres',
        'provision-runtime-role',
        'validate-owner-password'
    ) | Sort-Object
    telegram = @(
        'migrate',
        'postgres',
        'provision-runtime-role',
        'telegram',
        'validate-owner-password'
    ) | Sort-Object
    offsite = @(
        'backup',
        'backup-offsite',
        'postgres',
        'validate-owner-password'
    ) | Sort-Object
}
$isolatedProfileServices = [ordered]@{
    release = $releaseServices
}
foreach ($profileExpectation in $expectedIsolatedProfileServices.GetEnumerator()) {
    $profileName = [string]$profileExpectation.Key
    $profileComposeJson = & docker @composeArguments `
        --profile $profileName `
        config `
        --format json
    if ($LASTEXITCODE -ne 0) {
        throw "Rendered $profileName-profile Compose configuration is invalid."
    }
    $profileCompose = $profileComposeJson | ConvertFrom-Json
    $profileServices = @(
        $profileCompose.services.PSObject.Properties |
            ForEach-Object { $_.Name } |
            Sort-Object
    )
    $expectedProfileServices = @($profileExpectation.Value)
    if (
        $profileServices.Count -ne $expectedProfileServices.Count -or
        ($profileServices -join "`n") -cne ($expectedProfileServices -join "`n")
    ) {
        throw (
            "The $profileName profile does not render its exact reviewed isolated " +
            'service inventory.'
        )
    }
    $isolatedProfileServices[$profileName] = $profileServices
}

$composeJson = & docker @composeArguments `
    --profile release `
    --profile infrastructure `
    --profile binance `
    --profile telegram `
    --profile offsite `
    config `
    --format json
if ($LASTEXITCODE -ne 0) {
    throw 'Rendered Compose configuration is invalid.'
}
$compose = $composeJson | ConvertFrom-Json

$profiledServices = @(
    $compose.services.PSObject.Properties |
        ForEach-Object { $_.Name } |
        Sort-Object
)
$expectedProfiledServices = @(
    'api',
    'backup',
    'backup-offsite',
    'binance-market-stream',
    'migrate',
    'postgres',
    'provision-runtime-role',
    'provision-safety-role',
    'safety-control',
    'telegram',
    'validate-owner-password',
    'validate-safety-credentials',
    'worker'
) | Sort-Object
if (
    $profiledServices.Count -ne $expectedProfiledServices.Count -or
    ($profiledServices -join "`n") -cne ($expectedProfiledServices -join "`n")
) {
    throw 'Explicit release profiles do not render the complete reviewed service inventory.'
}

$applicationServices = @(
    'migrate',
    'worker',
    'binance-market-stream',
    'api',
    'safety-control',
    'telegram'
)
foreach ($serviceName in $applicationServices) {
    $service = $compose.services.$serviceName
    if ($null -eq $service) {
        throw "Rendered Compose configuration is missing service $serviceName."
    }
    if ($service.image -ne $Image) {
        throw "Service $serviceName uses $($service.image), expected $Image."
    }
    $serviceDigest = $service.environment.RELEASE_IMAGE_DIGEST
    if ($serviceDigest -ne $ExpectedDigest) {
        throw "Service $serviceName has a release digest different from the candidate image."
    }
}

$migrateService = $compose.services.migrate
$migrateCommand = $migrateService.command
$migrateDependsOn = $migrateService.depends_on
$migrateDependsOnProperties = @($migrateDependsOn.PSObject.Properties.Name)
$migrateEntrypointProperty = $migrateService.PSObject.Properties['entrypoint']
$migrateUserProperty = $migrateService.PSObject.Properties['user']
$migrateVolumesProperty = $migrateService.PSObject.Properties['volumes']
$migrateEntrypoint = if ($null -eq $migrateEntrypointProperty) {
    $null
}
else { $migrateEntrypointProperty.Value }
$migrateUser = if ($null -eq $migrateUserProperty) {
    $null
}
else { $migrateUserProperty.Value }
$migrateVolumes = if ($null -eq $migrateVolumesProperty) {
    $null
}
else { $migrateVolumesProperty.Value }
if (
    $migrateCommand -isnot [object[]] -or
    $migrateCommand.Count -ne 3 -or
    ($migrateCommand -join "`n") -cne "alembic`nupgrade`nhead" -or
    $null -ne $migrateEntrypoint -or
    $null -ne $migrateUser -or
    ($null -ne $migrateVolumes -and (
        $migrateVolumes -isnot [object[]] -or $migrateVolumes.Count -ne 0
    )) -or
    $migrateDependsOnProperties.Count -ne 1 -or
    $migrateDependsOnProperties[0] -cne 'postgres' -or
    [string]$migrateDependsOn.postgres.condition -cne 'service_healthy' -or
    $migrateDependsOn.postgres.required -isnot [bool] -or
    -not $migrateDependsOn.postgres.required -or
    [string]$migrateService.restart -cne 'no'
) {
    throw 'Rendered migrate command/entrypoint/user/mount/dependency provenance is not exact.'
}

$expectedMemoryLimits = [ordered]@{
    worker = '3221225472'
    api = '1073741824'
    'safety-control' = '268435456'
}
$resourceLimitedServices = @($expectedMemoryLimits.Keys)
foreach ($serviceName in $resourceLimitedServices) {
    $memoryLimit = $compose.services.$serviceName.mem_limit
    if ($memoryLimit -isnot [string]) {
        throw (
            "Service $serviceName memory limit is not a canonical rendered " +
            'Compose byte string.'
        )
    }
    if (-not [string]::Equals(
        $memoryLimit,
        [string]$expectedMemoryLimits[$serviceName],
        [StringComparison]::Ordinal
    )) {
        throw (
            "Service $serviceName memory limit is $memoryLimit bytes; expected " +
            "$($expectedMemoryLimits[$serviceName]) bytes."
        )
    }
}

$operatorApiKeyServices = @(
    $compose.services.PSObject.Properties |
        Where-Object {
            $environmentProperty = $_.Value.PSObject.Properties['environment']
            $null -ne $environmentProperty -and
            $null -ne $environmentProperty.Value.PSObject.Properties['OPERATOR_API_KEY']
        } |
        ForEach-Object Name |
        Sort-Object
)
$expectedOperatorApiKeyServices = @('api', 'validate-safety-credentials')
if (
    $operatorApiKeyServices.Count -ne $expectedOperatorApiKeyServices.Count -or
    ($operatorApiKeyServices -join "`n") -cne
        ($expectedOperatorApiKeyServices -join "`n")
) {
    throw (
        'Rendered OPERATOR_API_KEY exposure is not confined exactly to API and ' +
        'the one-shot credential validator.'
    )
}
$apiOperatorApiKey = $compose.services.api.environment.OPERATOR_API_KEY
$validatorOperatorApiKey = `
    $compose.services.'validate-safety-credentials'.environment.OPERATOR_API_KEY
if (
    $apiOperatorApiKey -isnot [string] -or
    [string]::IsNullOrWhiteSpace([string]$apiOperatorApiKey) -or
    $validatorOperatorApiKey -isnot [string] -or
    [string]::IsNullOrWhiteSpace([string]$validatorOperatorApiKey) -or
    [string]$apiOperatorApiKey -cne [string]$validatorOperatorApiKey
) {
    throw 'Rendered API and validator OPERATOR_API_KEY values are not the same non-empty credential.'
}

[pscustomobject]@{
    image = $Image
    image_id = $actualId
    revision = $actualRevision
    platform = "$actualOs/$actualArchitecture"
    compose_services = $applicationServices
    default_safe_services = $defaultServices
    release_services = $releaseServices
    isolated_profile_services = $isolatedProfileServices
    profiled_services = $profiledServices
    release_profiles = @(
        'release',
        'infrastructure',
        'binance',
        'telegram',
        'offsite'
    )
    compose_project = $composeProjectName
    compose_file = $composePath
    compose_env_file = $absoluteEnvFile
    migrate_command = @($migrateCommand)
    resource_limited_services = $resourceLimitedServices
    verified = $true
} | ConvertTo-Json -Compress
