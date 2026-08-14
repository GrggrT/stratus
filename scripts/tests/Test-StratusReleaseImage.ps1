[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$guardPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\Assert-StratusReleaseImage.ps1')
)
$candidate = 'stratus:candidate-0123456789ab'
$digest = 'a' * 64
$revision = 'b' * 40
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'stratus-release-image-contract-' + [guid]::NewGuid().ToString('N')
)
$envPath = Join-Path $tempRoot '.env'
$composePath = Join-Path $tempRoot 'docker-compose.yml'
$global:StratusReleaseImageMockMode = 'accepted'
$global:LASTEXITCODE = 0

function New-MockService {
    param(
        [string] $Image = 'postgres:16-alpine@sha256:' + ('c' * 64),
        [string] $ReleaseDigest = '',
        [long] $Memory = 0,
        [switch] $OperatorApiKey
    )

    $service = [ordered]@{
        image = $Image
        environment = [ordered]@{}
    }
    if (-not [string]::IsNullOrWhiteSpace($ReleaseDigest)) {
        $service['environment']['RELEASE_IMAGE_DIGEST'] = $ReleaseDigest
    }
    if ($OperatorApiKey) {
        $service['environment']['OPERATOR_API_KEY'] = `
            'mock_operator_key_0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    }
    if ($Memory -gt 0) {
        $service['mem_limit'] = $Memory
    }
    return $service
}

function New-MockComposeModel {
    param(
        [ValidateSet(
            'default',
            'release',
            'infrastructure',
            'binance',
            'telegram',
            'offsite',
            'complete'
        )]
        [string] $Model
    )

    $services = [ordered]@{}
    $serviceNames = switch ($Model) {
        'default' { @(); break }
        'release' {
            @(
                'api', 'backup', 'migrate', 'postgres',
                'provision-runtime-role', 'provision-safety-role',
                'safety-control', 'validate-owner-password',
                'validate-safety-credentials', 'worker'
            )
            break
        }
        'infrastructure' {
            @('backup', 'postgres', 'validate-owner-password')
            break
        }
        'binance' {
            @(
                'binance-market-stream', 'migrate', 'postgres',
                'provision-runtime-role', 'validate-owner-password'
            )
            break
        }
        'telegram' {
            @(
                'migrate', 'postgres', 'provision-runtime-role', 'telegram',
                'validate-owner-password'
            )
            break
        }
        'offsite' {
            @('backup', 'backup-offsite', 'postgres', 'validate-owner-password')
            break
        }
        'complete' {
            @(
                'api', 'backup', 'backup-offsite', 'binance-market-stream',
                'migrate', 'postgres', 'provision-runtime-role',
                'provision-safety-role', 'safety-control', 'telegram',
                'validate-owner-password', 'validate-safety-credentials',
                'worker'
            )
            break
        }
    }
    foreach ($name in $serviceNames) {
        if ($name -eq 'validate-safety-credentials') {
            $services[$name] = New-MockService -OperatorApiKey
            continue
        }
        if ($name -eq 'migrate') {
            $services[$name] = New-MockService `
                -Image $candidate -ReleaseDigest $digest
            $services[$name]['command'] = @('alembic', 'upgrade', 'head')
            $services[$name]['entrypoint'] = $null
            $services[$name]['user'] = $null
            $services[$name]['volumes'] = $null
            $services[$name]['depends_on'] = [ordered]@{
                postgres = [ordered]@{
                    condition = 'service_healthy'
                    required = $true
                }
            }
            $services[$name]['restart'] = 'no'
            if ($global:StratusReleaseImageMockMode -eq 'migrate-command-override') {
                $services[$name]['command'] = @('sh', '-ec', 'hostile override')
            }
            if ($global:StratusReleaseImageMockMode -eq 'migrate-entrypoint-override') {
                $services[$name]['entrypoint'] = @('/bin/sh', '-ec')
            }
            continue
        }
        if ($name -in @('worker', 'api', 'safety-control')) {
            $memory = switch ($name) {
                'worker' { 3221225472L; break }
                'api' { 1073741824L; break }
                'safety-control' { 268435456L; break }
            }
            $services[$name] = New-MockService `
                -Image $candidate -ReleaseDigest $digest -Memory $memory `
                -OperatorApiKey:($name -eq 'api')
            if (
                $name -eq 'api' -and
                $global:StratusReleaseImageMockMode -eq 'wrong-api-memory'
            ) {
                $services[$name]['mem_limit'] = 2147483648L
            }
            continue
        }
        if ($name -in @('binance-market-stream', 'telegram')) {
            $services[$name] = New-MockService `
                -Image $candidate -ReleaseDigest $digest
            continue
        }
        $services[$name] = New-MockService
    }
    if (
        $Model -eq 'release' -and
        $global:StratusReleaseImageMockMode -eq 'missing-release-service'
    ) {
        [void]$services.Remove('provision-safety-role')
    }
    if (
        $Model -eq 'complete' -and
        $global:StratusReleaseImageMockMode -eq 'missing-profiled-service'
    ) {
        [void]$services.Remove('provision-safety-role')
    }
    if (
        $Model -eq 'complete' -and
        $global:StratusReleaseImageMockMode -eq 'operator-key-leak'
    ) {
        $services.worker.environment['OPERATOR_API_KEY'] = 'mock-operator-api-key'
    }
    if (
        $Model -eq 'infrastructure' -and
        $global:StratusReleaseImageMockMode -eq 'misassigned-profile'
    ) {
        $services['telegram'] = New-MockService `
            -Image $candidate -ReleaseDigest $digest
    }
    if (
        $Model -eq 'default' -and
        $global:StratusReleaseImageMockMode -eq 'unsafe-default'
    ) {
        $services['api'] = New-MockService `
            -Image $candidate -ReleaseDigest $digest -Memory 268435456
    }
    return [ordered]@{ services = $services }
}

function global:docker {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]] $DockerArguments
    )

    $global:LASTEXITCODE = 0
    if (
        $DockerArguments.Count -ge 3 -and
        [string]$DockerArguments[0] -eq 'image' -and
        [string]$DockerArguments[1] -eq 'inspect'
    ) {
        return [ordered]@{
            Id = "sha256:$digest"
            Os = 'linux'
            Architecture = 'amd64'
            Config = [ordered]@{
                User = 'stratus'
                Entrypoint = $null
                Env = if ($global:StratusReleaseImageMockMode -eq 'baked-operator-key') {
                    @('OPERATOR_API_KEY=baked-control-authority')
                }
                else { @() }
                Labels = [ordered]@{
                    'org.opencontainers.image.revision' = $revision
                }
            }
        } | ConvertTo-Json -Depth 8
    }
    if (
        $DockerArguments.Count -ge 11 -and
        [string]$DockerArguments[0] -eq 'compose' -and
        $DockerArguments -contains 'config'
    ) {
        $expectedPrefix = @(
            'compose',
            '--project-directory',
            $tempRoot,
            '-f',
            $composePath,
            '--env-file',
            $envPath,
            '--project-name',
            'weatherpoly'
        )
        if (
            (@($DockerArguments | Select-Object -First $expectedPrefix.Count) -join "`n") -cne
                ($expectedPrefix -join "`n")
        ) {
            throw "Mock rejected non-exact Compose provenance: $($DockerArguments -join ' ')"
        }
        $profiles = @()
        for ($index = 0; $index -lt $DockerArguments.Count; $index += 1) {
            if ([string]$DockerArguments[$index] -eq '--profile') {
                if ($index + 1 -ge $DockerArguments.Count) {
                    throw 'Mock received --profile without a value.'
                }
                $profiles += [string]$DockerArguments[$index + 1]
            }
        }
        $model = switch ($profiles -join ',') {
            '' { 'default'; break }
            'release' { 'release'; break }
            'infrastructure' { 'infrastructure'; break }
            'binance' { 'binance'; break }
            'telegram' { 'telegram'; break }
            'offsite' { 'offsite'; break }
            'release,infrastructure,binance,telegram,offsite' {
                'complete'
                break
            }
            default {
                throw "Unexpected mocked Compose profiles: $($profiles -join ', ')"
            }
        }
        return New-MockComposeModel -Model $model |
            ConvertTo-Json -Depth 12
    }
    throw "Unexpected mocked docker invocation: $($DockerArguments -join ' ')"
}

function Assert-Throws {
    param(
        [Parameter(Mandatory = $true)][string] $ExpectedMessage,
        [Parameter(Mandatory = $true)][scriptblock] $Action
    )

    try {
        & $Action
    }
    catch {
        if ($_.Exception.Message -notmatch [regex]::Escape($ExpectedMessage)) {
            throw "Expected '$ExpectedMessage', got '$($_.Exception.Message)'."
        }
        return
    }
    throw "Expected failure containing '$ExpectedMessage'."
}

try {
    [void][IO.Directory]::CreateDirectory($tempRoot)
    [IO.File]::WriteAllLines(
        $envPath,
        @(
            "STRATUS_IMAGE=$candidate",
            "STRATUS_RELEASE_IMAGE_DIGEST=$digest"
        ),
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText(
        $composePath,
        "services:`n  migrate:`n    image: $candidate`n",
        [Text.UTF8Encoding]::new($false)
    )

    $accepted = & $guardPath `
        -Image $candidate `
        -ExpectedDigest $digest `
        -ExpectedRevision $revision `
        -EnvFile $envPath `
        -Root $tempRoot | ConvertFrom-Json
    if (
        -not [bool]$accepted.verified -or
        @($accepted.default_safe_services).Count -ne 0 -or
        @($accepted.release_services).Count -ne 10 -or
        @($accepted.isolated_profile_services.infrastructure).Count -ne 3 -or
        @($accepted.isolated_profile_services.binance).Count -ne 5 -or
        @($accepted.isolated_profile_services.telegram).Count -ne 5 -or
        @($accepted.isolated_profile_services.offsite).Count -ne 4 -or
        @($accepted.profiled_services).Count -ne 13 -or
        @($accepted.release_profiles).Count -ne 5
    ) {
        throw 'Accepted release-image guard did not emit the exact profile evidence.'
    }

    $global:StratusReleaseImageMockMode = 'unsafe-default'
    Assert-Throws -ExpectedMessage 'must contain no services' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'missing-release-service'
    Assert-Throws -ExpectedMessage 'exact reviewed release service inventory' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'missing-profiled-service'
    Assert-Throws -ExpectedMessage 'complete reviewed service inventory' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'misassigned-profile'
    Assert-Throws -ExpectedMessage 'exact reviewed isolated service inventory' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'migrate-command-override'
    Assert-Throws -ExpectedMessage 'migrate command/entrypoint/user/mount/dependency provenance' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'migrate-entrypoint-override'
    Assert-Throws -ExpectedMessage 'migrate command/entrypoint/user/mount/dependency provenance' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'wrong-api-memory'
    Assert-Throws -ExpectedMessage 'api memory limit is 2147483648 bytes' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'operator-key-leak'
    Assert-Throws -ExpectedMessage 'OPERATOR_API_KEY exposure is not confined' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'baked-operator-key'
    Assert-Throws -ExpectedMessage 'bakes OPERATOR_API_KEY' -Action {
        & $guardPath `
            -Image $candidate `
            -ExpectedDigest $digest `
            -ExpectedRevision $revision `
            -EnvFile $envPath `
            -Root $tempRoot
    }

    $global:StratusReleaseImageMockMode = 'accepted'
    $env:COMPOSE_FILE = Join-Path $tempRoot 'hostile.yml'
    try {
        Assert-Throws -ExpectedMessage 'override environment variable is forbidden' -Action {
            & $guardPath `
                -Image $candidate `
                -ExpectedDigest $digest `
                -ExpectedRevision $revision `
                -EnvFile $envPath `
                -Root $tempRoot
        }
    }
    finally {
        Remove-Item Env:\COMPOSE_FILE -ErrorAction SilentlyContinue
    }

    $baselineEnvText = Get-Content -LiteralPath $envPath -Raw
    [IO.File]::WriteAllText(
        $envPath,
        $baselineEnvText + "COMPOSE_PROFILES=hostile`n",
        [Text.UTF8Encoding]::new($false)
    )
    try {
        Assert-Throws -ExpectedMessage 'must not define a reserved Compose override' -Action {
            & $guardPath `
                -Image $candidate `
                -ExpectedDigest $digest `
                -ExpectedRevision $revision `
                -EnvFile $envPath `
                -Root $tempRoot
        }
    }
    finally {
        [IO.File]::WriteAllText(
            $envPath,
            $baselineEnvText,
            [Text.UTF8Encoding]::new($false)
        )
    }

    [pscustomobject]@{
        validated = $true
        default_model_fail_closed = $true
        explicit_profiles_complete = $true
        exact_image_guard_executed = $true
    } | ConvertTo-Json -Compress
}
finally {
    Remove-Item Function:\docker -Force -ErrorAction SilentlyContinue
    Remove-Variable StratusReleaseImageMockMode -Scope Global -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
