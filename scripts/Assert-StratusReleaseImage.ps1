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

    [string] $EnvFile = '.env'
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

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Release environment file does not exist: $EnvFile"
}

$inspect = docker image inspect $Image --format (
    '{{.Id}}|{{.Os}}|{{.Architecture}}|' +
    '{{index .Config.Labels "org.opencontainers.image.revision"}}'
)
if ($LASTEXITCODE -ne 0) {
    throw "Docker image does not exist locally: $Image"
}

$parts = $inspect -split '\|', 4
if ($parts.Count -ne 4) {
    throw 'Unexpected docker image inspect output.'
}

$actualId = $parts[0]
$actualOs = $parts[1]
$actualArchitecture = $parts[2]
$actualRevision = $parts[3]

if ($actualId -ne "sha256:$ExpectedDigest") {
    throw "Image ID mismatch: expected sha256:$ExpectedDigest, observed $actualId."
}
if ($actualOs -ne 'linux' -or $actualArchitecture -ne 'amd64') {
    throw "Release image platform is $actualOs/$actualArchitecture, expected linux/amd64."
}
if ($actualRevision -ne $ExpectedRevision) {
    throw "Image revision mismatch: expected $ExpectedRevision, observed $actualRevision."
}

$envImage = Read-EnvValue -Path $EnvFile -Name 'STRATUS_IMAGE'
$envDigest = Read-EnvValue -Path $EnvFile -Name 'STRATUS_RELEASE_IMAGE_DIGEST'
if ($envImage -ne $Image) {
    throw "STRATUS_IMAGE mismatch: expected $Image, observed $envImage."
}
if ($envDigest -ne $ExpectedDigest) {
    throw 'STRATUS_RELEASE_IMAGE_DIGEST does not match the inspected candidate image.'
}

$composeJson = docker compose `
    --profile binance `
    --profile telegram `
    --profile offsite `
    config `
    --format json
if ($LASTEXITCODE -ne 0) {
    throw 'Rendered Compose configuration is invalid.'
}
$compose = $composeJson | ConvertFrom-Json

$applicationServices = @(
    'migrate',
    'worker',
    'binance-market-stream',
    'api',
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
    $serviceDigest = $service.environment.STRATUS_RELEASE_IMAGE_DIGEST
    if ($serviceDigest -ne $ExpectedDigest) {
        throw "Service $serviceName has a release digest different from the candidate image."
    }
}

[pscustomobject]@{
    image = $Image
    image_id = $actualId
    revision = $actualRevision
    platform = "$actualOs/$actualArchitecture"
    compose_services = $applicationServices
    verified = $true
} | ConvertTo-Json -Compress
