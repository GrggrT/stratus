[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Root,

    [Parameter(Mandatory = $true)]
    [string] $EvidencePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# These identities are deliberately source-pinned. Discovering replacement
# objects at repair time is not accepted as production identity evidence.
$productionPostgresId =
    '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$productionPostgresName = 'weatherpoly-postgres-1'
$postgresImage = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$postgresImageId =
    'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$postgresConfigHash =
    'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1'
$postgresCommandSha256 =
    '3e1396d8c083a0274909460787db163a1f9070eeabb659c403c5c7ca890436b3'
$hbaSha256 =
    '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
$defaultNetworkName = 'weatherpoly_default'
$defaultNetworkId =
    '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
$safetyNetworkName = 'weatherpoly_safety-plane'
$safetyNetworkId =
    '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
$approvedDockerExecutableSha256 =
    '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d'
$approvedDockerContext = 'desktop-linux'
$approvedDockerEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
$approvedDockerEngineId = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
$approvedDockerServerVersion = '28.3.2'
$approvedDockerEngineName = 'docker-desktop'
$approvedDockerRootDir = '/var/lib/docker'
$productionNetworkOptionsSha256 =
    'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569'
$script:dockerExecutable = $null
$script:dockerExecutableSha256 = $null

$networkContract = [ordered]@{
    $defaultNetworkName = [pscustomobject]@{
        id = $defaultNetworkId
        compose_name = 'default'
        subnet = '172.20.0.0/16'
        gateway = '172.20.0.1'
        created = '2026-07-05T17:30:21.808081932Z'
        labels_sha256 = '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d'
    }
    $safetyNetworkName = [pscustomobject]@{
        id = $safetyNetworkId
        compose_name = 'safety-plane'
        subnet = '172.22.0.0/16'
        gateway = '172.22.0.1'
        created = '2026-07-30T09:19:55.325597748Z'
        labels_sha256 = '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7'
    }
}

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string] $Value)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($bytes)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-RegularFile {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -le 0
    ) {
        throw "Required path is not a non-empty regular file: $LiteralPath"
    }
    return $item
}

function Assert-RegularDirectory {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) {
        throw "Required path is not a regular directory: $LiteralPath"
    }
    return $item
}

function Get-OptionalPropertyValue {
    param(
        [AllowNull()][object] $InputObject,
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

function Assert-NativeJsonObject {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value -or $Value -isnot [pscustomobject]) {
        throw "$Label must be a native JSON object."
    }
}

function Assert-NativeJsonString {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($Value -isnot [string]) {
        throw "$Label must be a native JSON string."
    }
}

function Assert-NativeJsonBoolean {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($Value -isnot [bool]) {
        throw "$Label must be a native JSON boolean."
    }
}

function Assert-NativeJsonInteger {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($Value -isnot [int] -and $Value -isnot [long]) {
        throw "$Label must be a native JSON integer."
    }
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-NativeJsonObject -Value $Value -Label $Label
    $actual = @($Value.PSObject.Properties | ForEach-Object { [string]$_.Name })
    $expectedNames = @($Expected)
    [Array]::Sort($actual, [StringComparer]::Ordinal)
    [Array]::Sort($expectedNames, [StringComparer]::Ordinal)
    if (($actual -join "`n") -cne ($expectedNames -join "`n")) {
        throw "$Label does not have the exact expected properties."
    }
}

function ConvertFrom-StrictJsonObjectText {
    param(
        [Parameter(Mandatory = $true)][string] $Json,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $trimmed = $Json.Trim()
    if (-not $trimmed.StartsWith('{') -or -not $trimmed.EndsWith('}')) {
        throw "$Label must have a native JSON object at its root."
    }
    try {
        $cmdlet = Get-Command -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
            -CommandType Cmdlet -ErrorAction Stop
        $parameters = @{ InputObject = $trimmed }
        if ($cmdlet.Parameters.ContainsKey('DateKind')) {
            $parameters['DateKind'] = 'String'
        }
        $value = Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
    }
    catch {
        throw "$Label returned invalid JSON."
    }
    Assert-NativeJsonObject -Value $value -Label $Label
    return $value
}

function Get-OneJsonObject {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $meaningful = @($Lines | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one JSON object."
    }
    return ConvertFrom-StrictJsonObjectText -Json ([string]$meaningful[0]) -Label $Label
}

function Get-OneNativeLine {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $meaningful = @($Lines | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one line."
    }
    return [string]$meaningful[0]
}

function Resolve-PinnedDockerApplication {
    $commands = @(Microsoft.PowerShell.Core\Get-Command -Name 'docker' -All `
        -CommandType Application -ErrorAction Stop)
    if ($commands.Count -lt 1) {
        throw 'Docker application could not be resolved.'
    }
    $matches = [Collections.Generic.List[object]]::new()
    $seenPaths = @{}
    foreach ($command in $commands) {
        $source = [string]$command.Source
        if ([string]::IsNullOrWhiteSpace($source)) {
            continue
        }
        $path = [IO.Path]::GetFullPath($source)
        if ($seenPaths.ContainsKey($path)) {
            continue
        }
        $seenPaths[$path] = $true
        $item = Assert-RegularFile -LiteralPath $path
        $sha256 = Get-FileSha256 -LiteralPath $path
        if ($sha256 -ceq $approvedDockerExecutableSha256) {
            $matches.Add([pscustomobject]@{
                path = $path
                sha256 = $sha256
                bytes = [int64]$item.Length
            })
        }
    }
    if ($matches.Count -ne 1) {
        throw 'Docker application resolution did not find exactly one approved executable hash.'
    }
    return $matches[0]
}

function Get-PinnedDockerApplicationBoundary {
    $item = Assert-RegularFile -LiteralPath $script:dockerExecutable
    $sha256 = Get-FileSha256 -LiteralPath $script:dockerExecutable
    if ($sha256 -cne $script:dockerExecutableSha256) {
        throw 'Docker executable changed after it was pinned.'
    }
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($script:dockerExecutable)
        sha256 = $sha256
        bytes = [int64]$item.Length
    }
}

function Invoke-PinnedDocker {
    param([Parameter(Mandatory = $true)][string[]] $Arguments)

    if ([string]::IsNullOrWhiteSpace([string]$script:dockerExecutable)) {
        throw 'Docker application was not pinned before use.'
    }
    # Windows PowerShell 5.1 turns redirected native stderr into a
    # NativeCommandError when the caller uses ErrorActionPreference=Stop.
    # Normalize that host-specific behavior locally so every non-zero Docker
    # result reaches the same exact fail-closed exit-code boundary.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:dockerExecutable @Arguments 2>&1)
        $exitCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "A required Docker command failed (exit $exitCode)."
    }
    return @($output | ForEach-Object { [string]$_ })
}

function Get-CanonicalPropertyHash {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value) {
        return Get-Sha256Text -Value "<null>`n"
    }
    Assert-NativeJsonObject -Value $Value -Label $Label
    $lines = @($Value.PSObject.Properties | ForEach-Object {
        $serialized = $_.Value | ConvertTo-Json -Depth 12 -Compress
        "$($_.Name)`0$serialized"
    })
    [Array]::Sort($lines, [StringComparer]::Ordinal)
    return Get-Sha256Text -Value (($lines -join "`n") + "`n")
}

function Get-DockerEngineBoundary {
    $application = Get-PinnedDockerApplicationBoundary
    foreach ($name in @(
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
        'DOCKER_CONFIG', 'DOCKER_API_VERSION', 'DOCKER_CLI_PLUGIN_EXTRA_DIRS'
    )) {
        if (Test-Path -LiteralPath "Env:$name") {
            throw "Docker endpoint override is forbidden for stopped repair: $name"
        }
    }
    $context = Get-OneNativeLine -Label 'Docker context name' -Lines (
        Invoke-PinnedDocker -Arguments @('context', 'show')
    )
    $endpoint = Get-OneJsonObject -Label 'Docker context endpoint' -Lines (
        Invoke-PinnedDocker -Arguments @(
            'context', 'inspect', $context, '--format', '{{json .Endpoints.docker}}'
        )
    )
    $info = Get-OneJsonObject -Label 'Docker engine info' -Lines (
        Invoke-PinnedDocker -Arguments @('info', '--format', '{{json .}}')
    )
    Assert-NativeJsonString -Value $endpoint.Host -Label 'Docker endpoint Host'
    foreach ($name in @('ID', 'ServerVersion', 'Name', 'DockerRootDir')) {
        Assert-NativeJsonString -Value $info.$name -Label "Docker engine $name"
    }
    if (
        [string]::IsNullOrWhiteSpace($context) -or
        [string]::IsNullOrWhiteSpace([string]$endpoint.Host) -or
        [string]::IsNullOrWhiteSpace([string]$info.ID) -or
        [string]::IsNullOrWhiteSpace([string]$info.ServerVersion) -or
        [string]::IsNullOrWhiteSpace([string]$info.Name) -or
        [string]::IsNullOrWhiteSpace([string]$info.DockerRootDir)
    ) {
        throw 'Docker context or engine identity is incomplete.'
    }
    $skipTlsVerify = [bool](Get-OptionalPropertyValue `
        -InputObject $endpoint -Name 'SkipTLSVerify')
    if (
        $context -cne $approvedDockerContext -or
        $endpoint.Host -cne $approvedDockerEndpoint -or
        $skipTlsVerify -or
        $info.ID -cne $approvedDockerEngineId -or
        $info.ServerVersion -cne $approvedDockerServerVersion -or
        $info.Name -cne $approvedDockerEngineName -or
        $info.DockerRootDir -cne $approvedDockerRootDir
    ) {
        throw 'Docker context or engine identity is not the exact approved production boundary.'
    }
    $contract = @(
        [string]$application.path,
        [string]$application.sha256,
        [string]$application.bytes,
        $context,
        [string]$endpoint.Host,
        $skipTlsVerify.ToString().ToLowerInvariant(),
        [string]$info.ID,
        [string]$info.ServerVersion,
        [string]$info.Name,
        [string]$info.DockerRootDir
    ) -join "`0"
    return [ordered]@{
        docker_executable_path = [string]$application.path
        docker_executable_sha256 = [string]$application.sha256
        docker_executable_bytes = [int64]$application.bytes
        context = $context
        endpoint = [string]$endpoint.Host
        skip_tls_verify = $skipTlsVerify
        engine_id = [string]$info.ID
        server_version = [string]$info.ServerVersion
        engine_name = [string]$info.Name
        docker_root_dir = [string]$info.DockerRootDir
        contract_sha256 = Get-Sha256Text -Value ($contract + "`n")
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
        [Parameter(Mandatory = $true)][string] $ComposePath,
        [Parameter(Mandatory = $true)][string] $OverridePath
    )

    if ([string]::IsNullOrWhiteSpace($Label)) {
        return $false
    }
    $parts = @($Label -split ',' | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($parts.Count -ne 2) {
        return $false
    }
    return (
        [IO.Path]::GetFullPath([string]$parts[0]) -ieq
            [IO.Path]::GetFullPath($ComposePath) -and
        [IO.Path]::GetFullPath([string]$parts[1]) -ieq
            [IO.Path]::GetFullPath($OverridePath)
    )
}

function Get-CommandSha256 {
    param([Parameter(Mandatory = $true)][object] $Inspect)

    $command = @(@($Inspect.Config.Entrypoint) + @($Inspect.Config.Cmd)) -join "`0"
    return Get-Sha256Text -Value $command
}

function Get-ContainerLifecycleInventory {
    $runningIds = @(
        Invoke-PinnedDocker -Arguments @('ps', '-q', '--no-trunc') |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($runningIds.Count -ne 0) {
        throw 'Stopped network repair requires zero running containers.'
    }
    $containerIds = @(
        Invoke-PinnedDocker -Arguments @('ps', '-a', '-q', '--no-trunc') |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    foreach ($containerId in $containerIds) {
        if ([string]$containerId -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Docker returned a malformed all-container inventory ID.'
        }
    }
    [Array]::Sort($containerIds, [StringComparer]::Ordinal)
    if (@($containerIds | Select-Object -Unique).Count -ne $containerIds.Count) {
        throw 'Docker returned duplicate all-container inventory IDs.'
    }
    $entries = [Collections.Generic.List[object]]::new()
    foreach ($containerId in $containerIds) {
        $inspect = Get-OneJsonObject -Label "container lifecycle $containerId" -Lines (
            Invoke-PinnedDocker -Arguments @(
                'inspect', [string]$containerId, '--format', '{{json .}}'
            )
        )
        foreach ($name in @('Config', 'State')) {
            Assert-NativeJsonObject -Value $inspect.$name `
                -Label "container lifecycle $containerId $name"
        }
        foreach ($name in @('Id', 'Name')) {
            Assert-NativeJsonString -Value $inspect.$name `
                -Label "container lifecycle $containerId $name"
        }
        foreach ($name in @('Status', 'StartedAt', 'FinishedAt')) {
            Assert-NativeJsonString -Value $inspect.State.$name `
                -Label "container lifecycle $containerId State.$name"
        }
        Assert-NativeJsonBoolean -Value $inspect.State.Running `
            -Label "container lifecycle $containerId Running"
        Assert-NativeJsonBoolean -Value $inspect.State.OOMKilled `
            -Label "container lifecycle $containerId OOMKilled"
        Assert-NativeJsonInteger -Value $inspect.RestartCount `
            -Label "container lifecycle $containerId RestartCount"
        if ($inspect.Id -cne [string]$containerId -or [bool]$inspect.State.Running) {
            throw 'All-container lifecycle inventory drifted or contains a running container.'
        }
        $labels = Get-OptionalPropertyValue -InputObject $inspect.Config -Name 'Labels'
        $project = [string](Get-OptionalPropertyValue `
            -InputObject $labels -Name 'com.docker.compose.project')
        $service = [string](Get-OptionalPropertyValue `
            -InputObject $labels -Name 'com.docker.compose.service')
        $composeLines = @(
            "com.docker.compose.project`0$project",
            "com.docker.compose.service`0$service"
        )
        [Array]::Sort($composeLines, [StringComparer]::Ordinal)
        $entries.Add([ordered]@{
            container_id = [string]$inspect.Id
            name = [string]$inspect.Name
            state = [string]$inspect.State.Status
            running = [bool]$inspect.State.Running
            started_at = [string]$inspect.State.StartedAt
            finished_at = [string]$inspect.State.FinishedAt
            restart_count = [int]$inspect.RestartCount
            oom_killed = [bool]$inspect.State.OOMKilled
            compose_project = $project
            compose_service = $service
            compose_labels_sha256 = Get-Sha256Text -Value (
                ($composeLines -join "`n") + "`n"
            )
        })
    }
    if (@($containerIds | Where-Object { $_ -ceq $productionPostgresId }).Count -ne 1) {
        throw 'All-container inventory does not contain the exact retained PostgreSQL once.'
    }
    $inventory = @($entries.ToArray())
    $inventoryJson = $inventory | ConvertTo-Json -Depth 8 -Compress
    return [ordered]@{
        running_container_ids = @()
        containers = $inventory
        inventory_sha256 = Get-Sha256Text -Value $inventoryJson
    }
}

function Get-ProductionNetworkBoundary {
    param([Parameter(Mandatory = $true)][string] $NetworkName)

    $expected = $networkContract[$NetworkName]
    if ($null -eq $expected) {
        throw "Unknown source-pinned production network: $NetworkName"
    }
    $network = Get-OneJsonObject -Label "production network $NetworkName" -Lines (
        Invoke-PinnedDocker -Arguments @(
            'network', 'inspect', [string]$expected.id, '--format', '{{json .}}'
        )
    )
    foreach ($name in @('IPAM', 'Labels', 'Containers', 'Options', 'ConfigFrom')) {
        Assert-NativeJsonObject -Value $network.$name `
            -Label "production network $NetworkName $name"
    }
    foreach ($name in @(
        'EnableIPv4', 'EnableIPv6', 'Internal', 'Attachable', 'Ingress', 'ConfigOnly'
    )) {
        Assert-NativeJsonBoolean -Value $network.$name `
            -Label "production network $NetworkName $name"
    }
    Assert-NativeJsonString -Value $network.Created `
        -Label "production network $NetworkName Created"
    Assert-ExactProperties -Value $network.ConfigFrom `
        -Expected @('Network') -Label "production network $NetworkName ConfigFrom"
    Assert-NativeJsonString -Value $network.ConfigFrom.Network `
        -Label "production network $NetworkName ConfigFrom.Network"
    $ipamConfig = @($network.IPAM.Config)
    $ipRange = $null
    $auxiliaryAddresses = $null
    if ($ipamConfig.Count -eq 1) {
        $ipRange = Get-OptionalPropertyValue -InputObject $ipamConfig[0] -Name 'IPRange'
        $auxiliaryAddresses = Get-OptionalPropertyValue `
            -InputObject $ipamConfig[0] -Name 'AuxiliaryAddresses'
    }
    $members = @($network.Containers.PSObject.Properties | ForEach-Object {
        [string]$_.Name
    })
    [Array]::Sort($members, [StringComparer]::Ordinal)
    if (
        $network.Name -cne $NetworkName -or
        $network.Id -cne [string]$expected.id -or
        $network.Created -cne [string]$expected.created -or
        $network.Scope -cne 'local' -or
        $network.Driver -cne 'bridge' -or
        -not [bool]$network.EnableIPv4 -or
        [bool]$network.EnableIPv6 -or
        [bool]$network.Internal -or
        [bool]$network.Attachable -or
        [bool]$network.Ingress -or
        $network.ConfigOnly -or
        $network.IPAM.Driver -cne 'default' -or
        $null -ne (Get-OptionalPropertyValue -InputObject $network.IPAM -Name 'Options') -or
        $network.ConfigFrom.Network -cne '' -or
        $ipamConfig.Count -ne 1 -or
        $ipamConfig[0].Subnet -cne [string]$expected.subnet -or
        $ipamConfig[0].Gateway -cne [string]$expected.gateway -or
        -not [string]::IsNullOrEmpty([string]$ipRange) -or
        $null -ne $auxiliaryAddresses -or
        $network.Labels.'com.docker.compose.project' -cne 'weatherpoly' -or
        $network.Labels.'com.docker.compose.network' -cne [string]$expected.compose_name -or
        $members.Count -ne 0
    ) {
        throw "Production Docker network identity/topology is not exact: $NetworkName"
    }
    $labelsHash = Get-CanonicalPropertyHash -Value $network.Labels `
        -Label "production network $NetworkName labels"
    $optionsHash = Get-CanonicalPropertyHash `
        -Value (Get-OptionalPropertyValue -InputObject $network -Name 'Options') `
        -Label "production network $NetworkName options"
    if (
        $labelsHash -cne [string]$expected.labels_sha256 -or
        $optionsHash -cne $productionNetworkOptionsSha256
    ) {
        throw "Production Docker network labels/options drifted: $NetworkName"
    }
    $body = [ordered]@{
        name = $NetworkName
        id = [string]$network.Id
        compose_name = [string]$expected.compose_name
        created = [string]$network.Created
        scope = [string]$network.Scope
        driver = [string]$network.Driver
        subnet = [string]$ipamConfig[0].Subnet
        gateway = [string]$ipamConfig[0].Gateway
        enable_ipv4 = [bool]$network.EnableIPv4
        enable_ipv6 = [bool]$network.EnableIPv6
        internal = [bool]$network.Internal
        attachable = [bool]$network.Attachable
        ingress = [bool]$network.Ingress
        attached_container_ids = $members
        labels_sha256 = $labelsHash
        options_sha256 = $optionsHash
    }
    $bodyJson = $body | ConvertTo-Json -Depth 8 -Compress
    $body['canonical_sha256'] = Get-Sha256Text -Value $bodyJson
    return $body
}

function Get-PostgresAttachmentBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Attachment,
        [Parameter(Mandatory = $true)][string] $NetworkName
    )

    Assert-NativeJsonObject -Value $Attachment -Label "PostgreSQL attachment $NetworkName"
    $expected = $networkContract[$NetworkName]
    $aliases = @($Attachment.Aliases)
    $dnsNames = @($Attachment.DNSNames)
    [Array]::Sort($aliases, [StringComparer]::Ordinal)
    [Array]::Sort($dnsNames, [StringComparer]::Ordinal)
    $expectedAliases = @('postgres', $productionPostgresName)
    [Array]::Sort($expectedAliases, [StringComparer]::Ordinal)
    $expectedDnsNames = @(
        $productionPostgresId.Substring(0, 12), 'postgres', $productionPostgresName
    )
    [Array]::Sort($expectedDnsNames, [StringComparer]::Ordinal)
    if (
        $Attachment.NetworkID -cne [string]$expected.id -or
        $null -ne $Attachment.IPAMConfig -or
        $null -ne $Attachment.Links -or
        $null -ne $Attachment.DriverOpts -or
        [int]$Attachment.GwPriority -ne 0 -or
        ($aliases -join "`n") -cne ($expectedAliases -join "`n") -or
        ($dnsNames -join "`n") -cne ($expectedDnsNames -join "`n") -or
        -not [string]::IsNullOrEmpty([string]$Attachment.EndpointID) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.Gateway) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.IPAddress) -or
        [int]$Attachment.IPPrefixLen -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Attachment.IPv6Gateway) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.GlobalIPv6Address) -or
        [int]$Attachment.GlobalIPv6PrefixLen -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Attachment.MacAddress)
    ) {
        throw "Stopped PostgreSQL attachment is not exact: $NetworkName"
    }
    $body = [ordered]@{
        name = $NetworkName
        network_id = [string]$Attachment.NetworkID
        gateway_priority = [int]$Attachment.GwPriority
        aliases = $aliases
        dns_names = $dnsNames
        endpoint_id = [string]$Attachment.EndpointID
        gateway = [string]$Attachment.Gateway
        ip_address = [string]$Attachment.IPAddress
        ip_prefix_length = [int]$Attachment.IPPrefixLen
        ipv6_gateway = [string]$Attachment.IPv6Gateway
        global_ipv6_address = [string]$Attachment.GlobalIPv6Address
        global_ipv6_prefix_length = [int]$Attachment.GlobalIPv6PrefixLen
        mac_address = [string]$Attachment.MacAddress
    }
    $bodyJson = $body | ConvertTo-Json -Depth 8 -Compress
    $body['canonical_sha256'] = Get-Sha256Text -Value $bodyJson
    return $body
}

function Get-PostgresBoundary {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('AllowedPre', 'Exact')]
        [string] $AttachmentRequirement
    )

    $inspect = Get-OneJsonObject -Label 'retained PostgreSQL inspect' -Lines (
        Invoke-PinnedDocker -Arguments @(
            'inspect', $productionPostgresId, '--format', '{{json .}}'
        )
    )
    foreach ($name in @('Config', 'State', 'HostConfig', 'NetworkSettings')) {
        Assert-NativeJsonObject -Value $inspect.$name -Label "PostgreSQL $name"
    }
    foreach ($name in @('Labels')) {
        Assert-NativeJsonObject -Value $inspect.Config.$name -Label "PostgreSQL Config.$name"
    }
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Networks `
        -Label 'PostgreSQL NetworkSettings.Networks'
    foreach ($name in @('Id', 'Name', 'Image')) {
        Assert-NativeJsonString -Value $inspect.$name -Label "PostgreSQL $name"
    }
    foreach ($name in @('Status', 'StartedAt', 'FinishedAt', 'Error')) {
        Assert-NativeJsonString -Value $inspect.State.$name -Label "PostgreSQL State.$name"
    }
    Assert-NativeJsonBoolean -Value $inspect.State.Running -Label 'PostgreSQL Running'
    Assert-NativeJsonBoolean -Value $inspect.State.OOMKilled -Label 'PostgreSQL OOMKilled'
    Assert-NativeJsonInteger -Value $inspect.RestartCount -Label 'PostgreSQL RestartCount'

    $labels = $inspect.Config.Labels
    $composePath = Join-Path $script:absoluteRoot 'docker-compose.yml'
    $overridePath = Join-Path $script:absoluteRoot 'docker-compose.override.yml'
    $hbaPath = Join-Path $script:absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    [void](Assert-RegularFile -LiteralPath $hbaPath)
    $currentHbaSha256 = Get-FileSha256 -LiteralPath $hbaPath
    if ($currentHbaSha256 -cne $hbaSha256) {
        throw 'Source-pinned PostgreSQL HBA file drifted during stopped repair.'
    }
    $hbaSources = @(Get-DockerHostPathAliases -Path $hbaPath)
    $portProperties = @(
        $inspect.HostConfig.PortBindings.PSObject.Properties |
            Where-Object { $null -ne $_.Value }
    )
    $port = $inspect.HostConfig.PortBindings.'5432/tcp'
    $hbaMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/etc/postgresql/stratus-pg_hba.conf'
    })
    $dataMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/var/lib/postgresql/data'
    })
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Name -cne "/$productionPostgresName" -or
        $inspect.Image -cne $postgresImageId -or
        $inspect.Config.Image -cne $postgresImage -or
        (Get-CommandSha256 -Inspect $inspect) -cne $postgresCommandSha256 -or
        $labels.'com.docker.compose.project' -cne 'weatherpoly' -or
        $labels.'com.docker.compose.service' -cne 'postgres' -or
        $labels.'com.docker.compose.config-hash' -cne $postgresConfigHash -or
        $labels.'com.docker.compose.image' -cne $postgresImageId -or
        $labels.'com.docker.compose.oneoff' -cne 'False' -or
        $labels.'com.docker.compose.container-number' -cne '1' -or
        [IO.Path]::GetFullPath(
            [string]$labels.'com.docker.compose.project.working_dir'
        ) -ine $script:absoluteRoot -or
        -not (Test-ComposeConfigFilesLabel `
            -Label ([string]$labels.'com.docker.compose.project.config_files') `
            -ComposePath $composePath -OverridePath $overridePath) -or
        $inspect.State.Status -cne 'exited' -or
        [bool]$inspect.State.Running -or
        [bool]$inspect.State.Paused -or
        [bool]$inspect.State.Restarting -or
        [bool]$inspect.State.OOMKilled -or
        [bool]$inspect.State.Dead -or
        [int]$inspect.State.Pid -ne 0 -or
        [int]$inspect.State.ExitCode -ne 0 -or
        -not [string]::IsNullOrWhiteSpace([string]$inspect.State.Error) -or
        [int]$inspect.RestartCount -ne 0 -or
        $inspect.HostConfig.RestartPolicy.Name -cne 'unless-stopped' -or
        [int]$inspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0 -or
        $inspect.HostConfig.NetworkMode -cne $defaultNetworkName -or
        $portProperties.Count -ne 1 -or
        $portProperties[0].Name -cne '5432/tcp' -or
        @($port).Count -ne 1 -or
        $port[0].HostIp -cne '127.0.0.1' -or
        $port[0].HostPort -cne '5432' -or
        @($inspect.Mounts).Count -ne 2 -or
        $hbaMounts.Count -ne 1 -or
        $hbaMounts[0].Type -cne 'bind' -or
        [bool]$hbaMounts[0].RW -or
        ([string]$hbaMounts[0].Source).Replace('\', '/') -notin $hbaSources -or
        $dataMounts.Count -ne 1 -or
        $dataMounts[0].Type -cne 'volume' -or
        $dataMounts[0].Name -cne 'weatherpoly_stratus-postgres' -or
        -not [bool]$dataMounts[0].RW
    ) {
        throw 'Retained production PostgreSQL stopped identity/configuration is not exact.'
    }
    $startedAt = [DateTimeOffset]::MinValue
    $finishedAt = [DateTimeOffset]::MinValue
    if (
        -not [DateTimeOffset]::TryParse([string]$inspect.State.StartedAt, [ref]$startedAt) -or
        -not [DateTimeOffset]::TryParse([string]$inspect.State.FinishedAt, [ref]$finishedAt) -or
        $finishedAt -le $startedAt
    ) {
        throw 'Retained production PostgreSQL has an invalid stopped lifecycle boundary.'
    }

    $networkProperties = @($inspect.NetworkSettings.Networks.PSObject.Properties)
    $networkNames = @($networkProperties.Name)
    [Array]::Sort($networkNames, [StringComparer]::Ordinal)
    $defaultOnly = @($defaultNetworkName)
    $exactNetworks = @($defaultNetworkName, $safetyNetworkName)
    [Array]::Sort($exactNetworks, [StringComparer]::Ordinal)
    $attachmentState = $null
    if (($networkNames -join "`n") -ceq ($defaultOnly -join "`n")) {
        $attachmentState = 'default_only'
    }
    elseif (($networkNames -join "`n") -ceq ($exactNetworks -join "`n")) {
        $attachmentState = 'exact'
    }
    else {
        throw 'Retained PostgreSQL attachment set is neither exact default-only nor exact final.'
    }
    if ($AttachmentRequirement -ceq 'Exact' -and $attachmentState -cne 'exact') {
        throw 'Retained PostgreSQL does not have the exact final two-network attachment.'
    }
    $attachments = [ordered]@{}
    foreach ($networkName in $networkNames) {
        $property = $inspect.NetworkSettings.Networks.PSObject.Properties[$networkName]
        $attachments[$networkName] = Get-PostgresAttachmentBoundary `
            -Attachment $property.Value -NetworkName $networkName
    }
    $stableBody = [ordered]@{
        container_id = [string]$inspect.Id
        name = [string]$inspect.Name
        image_id = [string]$inspect.Image
        config_image = [string]$inspect.Config.Image
        command_sha256 = $postgresCommandSha256
        compose_config_hash = $postgresConfigHash
        state = [string]$inspect.State.Status
        running = [bool]$inspect.State.Running
        started_at = [string]$inspect.State.StartedAt
        finished_at = [string]$inspect.State.FinishedAt
        restart_count = [int]$inspect.RestartCount
        oom_killed = [bool]$inspect.State.OOMKilled
        exit_code = [int]$inspect.State.ExitCode
        network_mode = [string]$inspect.HostConfig.NetworkMode
        hba_sha256 = $currentHbaSha256
        hba_source = [string]$hbaMounts[0].Source
        data_volume = [string]$dataMounts[0].Name
        published_postgres = '127.0.0.1:5432'
        labels_sha256 = Get-CanonicalPropertyHash -Value $labels -Label 'PostgreSQL labels'
    }
    $stableHash = Get-Sha256Text -Value (
        ($stableBody | ConvertTo-Json -Depth 10 -Compress)
    )
    return [ordered]@{
        stable_identity = $stableBody
        stable_identity_sha256 = $stableHash
        attachment_state = $attachmentState
        attachments = [pscustomobject]$attachments
        attachments_sha256 = Get-Sha256Text -Value (
            ([pscustomobject]$attachments | ConvertTo-Json -Depth 12 -Compress)
        )
    }
}

function Get-RepairSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('AllowedPre', 'Exact')]
        [string] $AttachmentRequirement
    )

    $engine = Get-DockerEngineBoundary
    $lifecycle = Get-ContainerLifecycleInventory
    $postgres = Get-PostgresBoundary -AttachmentRequirement $AttachmentRequirement
    $defaultNetwork = Get-ProductionNetworkBoundary -NetworkName $defaultNetworkName
    $safetyNetwork = Get-ProductionNetworkBoundary -NetworkName $safetyNetworkName
    $networkBody = [ordered]@{
        $defaultNetworkName = $defaultNetwork
        $safetyNetworkName = $safetyNetwork
    }
    $networkHash = Get-Sha256Text -Value (
        ([pscustomobject]$networkBody | ConvertTo-Json -Depth 12 -Compress)
    )
    $body = [ordered]@{
        docker_engine = $engine
        running_container_ids = @($lifecycle.running_container_ids)
        lifecycle_inventory = @($lifecycle.containers)
        lifecycle_inventory_sha256 = [string]$lifecycle.inventory_sha256
        postgres = $postgres
        production_networks = [pscustomobject]$networkBody
        production_networks_sha256 = $networkHash
    }
    $canonicalHash = Get-Sha256Text -Value (
        ($body | ConvertTo-Json -Depth 20 -Compress)
    )
    $snapshot = [ordered]@{ canonical_sha256 = $canonicalHash }
    foreach ($entry in $body.GetEnumerator()) {
        $snapshot[$entry.Key] = $entry.Value
    }
    return $snapshot
}

function Assert-StableCrossSnapshotBoundary {
    param(
        [Parameter(Mandatory = $true)][object[]] $Snapshots
    )

    $first = $Snapshots[0]
    foreach ($snapshot in $Snapshots) {
        if (
            $snapshot.docker_engine.contract_sha256 -cne
                $first.docker_engine.contract_sha256 -or
            $snapshot.lifecycle_inventory_sha256 -cne
                $first.lifecycle_inventory_sha256 -or
            $snapshot.postgres.stable_identity_sha256 -cne
                $first.postgres.stable_identity_sha256 -or
            $snapshot.production_networks_sha256 -cne
                $first.production_networks_sha256
        ) {
            throw 'Docker engine, lifecycle inventory, PostgreSQL identity, or network identity drifted.'
        }
    }
}

function Write-ReservedEvidence {
    param(
        [Parameter(Mandatory = $true)][IO.FileStream] $Stream,
        [Parameter(Mandatory = $true)][object] $Value
    )

    if ($Stream.Length -ne 0 -or $Stream.Position -ne 0) {
        throw 'Reserved evidence stream was unexpectedly modified before acceptance.'
    }
    $serialized = $Value | ConvertTo-Json -Depth 30
    $writer = [IO.StreamWriter]::new($Stream, [Text.UTF8Encoding]::new($false))
    try {
        $writer.Write($serialized)
        $writer.Flush()
        $Stream.Flush($true)
    }
    finally {
        $writer.Dispose()
    }
}

$script:absoluteRoot = [IO.Path]::GetFullPath($Root)
$absoluteEvidencePath = [IO.Path]::GetFullPath($EvidencePath)
[void](Assert-RegularDirectory -LiteralPath $script:absoluteRoot)
$composePath = Join-Path $script:absoluteRoot 'docker-compose.yml'
$overridePath = Join-Path $script:absoluteRoot 'docker-compose.override.yml'
$hbaPath = Join-Path $script:absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
[void](Assert-RegularFile -LiteralPath $composePath)
[void](Assert-RegularFile -LiteralPath $overridePath)
[void](Assert-RegularFile -LiteralPath $hbaPath)
if ((Get-FileSha256 -LiteralPath $hbaPath) -cne $hbaSha256) {
    throw 'Source-pinned PostgreSQL HBA file bytes are not exact.'
}
$evidenceParent = [IO.Path]::GetDirectoryName($absoluteEvidencePath)
[void](Assert-RegularDirectory -LiteralPath $evidenceParent)
if (Test-Path -LiteralPath $absoluteEvidencePath) {
    throw 'Stopped PostgreSQL network repair refuses an existing or partial evidence path.'
}

$evidenceStream = $null
try {
    # Reserving the final path before any Docker command makes concurrent or
    # crash-recovery reuse fail closed. A failed invocation intentionally leaves
    # a zero-byte, non-authorizing partial file; retry with a new evidence path.
    $evidenceStream = [IO.FileStream]::new(
        $absoluteEvidencePath,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::WriteThrough
    )
    $docker = Resolve-PinnedDockerApplication
    $script:dockerExecutable = [string]$docker.path
    $script:dockerExecutableSha256 = [string]$docker.sha256

    $pre = Get-RepairSnapshot -AttachmentRequirement AllowedPre
    $armed = Get-RepairSnapshot -AttachmentRequirement AllowedPre
    if ($armed.canonical_sha256 -cne $pre.canonical_sha256) {
        throw 'Stopped repair boundary changed between pre and armed snapshots.'
    }

    $mutationArguments = @(
        'network', 'connect',
        '--alias', 'postgres',
        '--alias', $productionPostgresName,
        '--gw-priority', '0',
        $safetyNetworkId,
        $productionPostgresId
    )
    $mutationIssued = $false
    $disposition = $null
    if ($armed.postgres.attachment_state -ceq 'default_only') {
        $mutationIssued = $true
        $mutationOutput = @(
            Invoke-PinnedDocker -Arguments $mutationArguments |
                Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
        )
        if ($mutationOutput.Count -ne 0) {
            throw 'Docker network attachment mutation returned unexpected output.'
        }
        $disposition = 'connected_by_this_invocation'
    }
    elseif ($armed.postgres.attachment_state -ceq 'exact') {
        $disposition = 'verified_exact_existing'
    }
    else {
        throw 'Armed PostgreSQL network disposition is not recoverable.'
    }

    $postFirst = Get-RepairSnapshot -AttachmentRequirement Exact
    $postSecond = Get-RepairSnapshot -AttachmentRequirement Exact
    if ($postFirst.canonical_sha256 -cne $postSecond.canonical_sha256) {
        throw 'Exact post-repair boundary was not stable across two consecutive snapshots.'
    }
    Assert-StableCrossSnapshotBoundary -Snapshots @(
        $pre, $armed, $postFirst, $postSecond
    )
    if (
        $postFirst.postgres.attachment_state -cne 'exact' -or
        $postSecond.postgres.attachment_state -cne 'exact'
    ) {
        throw 'Post-repair PostgreSQL attachment is not exact.'
    }
    if (
        -not $mutationIssued -and
        $postSecond.canonical_sha256 -cne $pre.canonical_sha256
    ) {
        throw 'Already-exact recovery changed the stopped Docker boundary.'
    }

    $acceptedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $payload = [ordered]@{
        accepted_at = $acceptedAt
        disposition = $disposition
        fixed_identity = [ordered]@{
            postgres_container_id = $productionPostgresId
            postgres_container_name = $productionPostgresName
            default_network_id = $defaultNetworkId
            safety_network_id = $safetyNetworkId
        }
        docker_engine = $postSecond.docker_engine
        snapshots = [ordered]@{
            pre = $pre
            armed = $armed
            post_first = $postFirst
            post_second = $postSecond
        }
        comparisons = [ordered]@{
            pre_equals_armed = $true
            post_first_equals_post_second = $true
            docker_engine_unchanged = $true
            all_container_lifecycle_inventory_unchanged = $true
            postgres_stopped_identity_unchanged = $true
            production_network_identity_unchanged = $true
            post_attachment_exact_twice = $true
        }
        mutation = [ordered]@{
            issued = $mutationIssued
            command = $mutationArguments
            output_line_count = 0
        }
        authorization = [ordered]@{
            postgres_start_permitted = $false
            api_start_permitted = $false
            worker_start_permitted = $false
            safety_control_start_permitted = $false
            live_permitted = $false
            binance_kill_switch_removal_permitted = $false
        }
    }
    $payloadJson = $payload | ConvertTo-Json -Depth 30 -Compress
    $envelope = [ordered]@{
        format = 'stratus-stopped-postgres-safety-plane-repair-v1'
        accepted = $true
        payload = $payload
        payload_sha256 = Get-Sha256Text -Value $payloadJson
    }
    Write-ReservedEvidence -Stream $evidenceStream -Value $envelope
    $evidenceStream = $null

    [pscustomobject]@{
        accepted = $true
        disposition = $disposition
        evidence_path = $absoluteEvidencePath
    }
}
finally {
    if ($null -ne $evidenceStream) {
        $evidenceStream.Dispose()
    }
}
