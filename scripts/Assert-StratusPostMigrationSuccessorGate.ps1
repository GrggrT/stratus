[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Transition')]
    [string] $Mode,

    [Parameter(Mandatory = $true)]
    [string] $Root,

    [Parameter(Mandatory = $true)]
    [string] $BuildContext,

    [Parameter(Mandatory = $true)]
    [string] $BackupDirectory,

    [Parameter(Mandatory = $true)]
    [string] $PreMigrationAttestationPath,

    [Parameter(Mandatory = $true)]
    [string] $LineageDirectory,

    [Parameter(Mandatory = $true)]
    [string] $NetworkRepairAttestationPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedRevision,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha256:[0-9a-f]{64}$')]
    [string] $ExpectedImageId,

    [Parameter(Mandatory = $true)]
    [string] $CandidateImage,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:[0-9a-f]{64}|[A-Za-z0-9][A-Za-z0-9_.-]{1,127})$')]
    [string] $PostgresContainer,

    [Parameter(Mandatory = $true)]
    [string] $PreStartAttestationPath,

    [Parameter(Mandatory = $true)]
    [string] $LiveAcceptancePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceSchemaHead = '20260730_0046'
$targetSchemaHead = '20260730_0047'
$predecessorRevision = '6d093b49cd5397d1e2bdb41fb4640d3e59127281'
$predecessorImage = 'stratus:candidate-6d093b49cd53'
$predecessorImageId = 'sha256:3d8c2904224328efb5604a28778c138abf26d8f43a06c02c733c8ad270ee2d24'
$productionPostgresId = '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$postgres16Image = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$postgres16ImageId = 'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$fixedHbaSha256 = '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
$fixedHbaInventorySha256 = 'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567'
$preMigrationAttestationSha256 = 'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189'
$preMigrationPayloadSha256 = 'd1ad1ed1511653cc394b70a118ca41b8751c9504cd0fdd449936868a5045eabd'
$restoreAttestationSha256 = '73939f689e3add8b92ba72cb4824a1c2243fa35228eaed25ffc5dab4144a3e4e'
$preStartMaxAgeSeconds = 600
$postgresStartWindowSeconds = 300
$clockSkewSeconds = 5
$dockerEventJournalMaxBytes = 8388608
$dockerEventJournalMaxEvents = 4096
$dockerEventBarrierTimeoutSeconds = 15
$postgresRollbackTimeoutSeconds = 15
$networkRepairMaxAgeSeconds = 600
$watchdogHeartbeatTimeoutSeconds = 15
$watchdogLeaseReadyTimeoutSeconds = 30
$watchdogProcessExitGraceSeconds = 30
$approvedWatchdogSha256 =
    '334970d3f86da1a07da3f701a8349741440285b69206874fc20d63733793152a'
$approvedDockerExecutableSha256 =
    '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d'
$approvedGitExecutableSha256 =
    'fec691d80fccc35fcc309fbc9f720536c1d795b8a562ec169f28c9923da9600f'
$approvedComposePluginSha256 =
    '8482829f77d14da8dfdfd0b553847946cee5161c90312219cf4f47d1f132d2bf'
$approvedComposePluginVersion = 'v2.38.2-desktop.1'
$approvedDockerContext = 'desktop-linux'
$approvedDockerEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
$approvedDockerEngineId = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
$approvedDockerServerVersion = '28.3.2'
$approvedDockerEngineName = 'docker-desktop'
$approvedDockerRootDirectory = '/var/lib/docker'
$productionNetworkOptionsSha256 =
    'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569'
$productionDataVolumeSource = '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data'
$productionPostgresConfigHash =
    'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1'
$productionPostgresCommandSha256 =
    '3e1396d8c083a0274909460787db163a1f9070eeabb659c403c5c7ca890436b3'
$script:dockerExecutable = $null
$script:dockerExecutableSha256 = $null
$script:gitExecutable = $null
$script:gitExecutableSha256 = $null
$script:hbaReadLock = $null
$script:hbaReadLockPath = $null
$script:watchdogScriptReadLock = $null
$script:watchdogHostReadLock = $null
$productionNetworks = [ordered]@{
    'weatherpoly_default' = [pscustomobject]@{
        id = '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
        compose_name = 'default'
        subnet = '172.20.0.0/16'
        gateway = '172.20.0.1'
        ipv4_pattern = '^172\.20\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})$'
        labels_sha256 = '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d'
        created = '2026-07-05T17:30:21.808081932Z'
    }
    'weatherpoly_safety-plane' = [pscustomobject]@{
        id = '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
        compose_name = 'safety-plane'
        subnet = '172.22.0.0/16'
        gateway = '172.22.0.1'
        ipv4_pattern = '^172\.22\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})$'
        labels_sha256 = '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7'
        created = '2026-07-30T09:19:55.325597748Z'
    }
}
$lineageArtifacts = [ordered]@{
    '09-stopped-migrate-stage-acceptance.json' =
        'bc8006665fc1365fb51a55904d21f4a6b783e3929a4f1060d8168f01093766dd'
    '17-pre-migration-wrapper-false-negative.json' =
        'ba881647611219d29ccf6fb68d42f1a745a3a431ede5f52fc9cf54ba028a4bea'
    '18-pre-migration-compatibility-holds.json' =
        '829ccf65b644fb7905a525d0a68a29bd3e9483cf2962b2ec2af144accf7c6033'
    '19-pre-migration-gate-and-0047-migration-accepted.json' =
        '200d1b10bfe05537c4385d87dec81c2bbb2669a69c0abcdfa2ba9217b7d9fb4f'
    '20-strict-post-ddl-0047-accepted.json' =
        'c6e30ccea288f3f8c3b57130c4e8af2f40da53ad33b808cb4ddeaa43437503ff'
    '21-post-ddl-hba-accepted.json' =
        'f42d979d98ec9feaa949cb23cb0add1925443bb406967cc1802e701bc0a4926c'
}

$closedGitContract = [ordered]@{
    '.gitattributes' = '0e7e5b4fc7af0523d798fc4a01979d2ad20f178b'
    '.dockerignore' = '7b30162a290eb8df806d9bf299ecba874052ecb3'
    'Dockerfile' = '554aaec8b175912723b686c7bc2f02c29c037f48'
    'README.md' = 'e39cc4f5324ce5c6c9e9cce35e9d27e4dc6ea571'
    'docker-compose.yml' = '53422981828ca0e00635d1c36778285f5e7221b9'
    'alembic.ini' = 'e109e86453d6913d5cfa41d9318a5f94544d921b'
    'migrations' = 'c1eabc51a09a79ce80fcc7aeb05156dd27d437e3'
    'ops/postgres/stratus-pg_hba.conf' = '59071191059e64f02d40f5daa62178b8b467c95a'
    'pyproject.toml' = 'f148c93eacae0539a6155c10dbd971baeb590fe3'
    'uv.lock' = '055c2fd18d0c3856c923ad8c9e011c027656aad4'
    'src/stratus/__init__.py' = '15fef34b5aae8ee9c1c6004685d9c98a581260f7'
    'src/stratus/config.py' = '4424ea6189413ca026afa7c52517c7c26d79409e'
    'src/stratus/db/__init__.py' = '331fd004a38d5cfcb60d87492f6628b3ab88a606'
    'src/stratus/db/base.py' = '3a60ede62c3ffd368d2473e5fb476e8d4656caef'
    'src/stratus/db/models.py' = '0354452bd74cfd7021bcbe07cee8861a1d957f82'
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

function Get-DirectFileSha256 {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    $stream = [IO.FileStream]::new(
        [IO.Path]::GetFullPath($LiteralPath), [IO.FileMode]::Open,
        [IO.FileAccess]::Read, [IO.FileShare]::Read, 65536,
        [IO.FileOptions]::SequentialScan
    )
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($stream)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-RegularFile {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.Length -le 0
    ) {
        throw "Release evidence must be a non-empty regular file: $LiteralPath"
    }
    return $item
}

function Assert-RegularDirectory {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Release path must be a regular directory, not a link: $LiteralPath"
    }
    return $item
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $actual = [string[]]@(
        $Value.PSObject.Properties |
            ForEach-Object { [string]$_.Name }
    )
    $wanted = [string[]]@($Expected)
    [Array]::Sort($actual, [StringComparer]::Ordinal)
    [Array]::Sort($wanted, [StringComparer]::Ordinal)
    if (($actual -join "`n") -cne ($wanted -join "`n")) {
        throw "$Label does not have the exact required property set."
    }
}

function Assert-NativeJsonObject {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType().FullName -cne 'System.Management.Automation.PSCustomObject') {
        throw "$Label must be a native JSON object."
    }
}

function Assert-NativeJsonString {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType() -ne [string]) {
        throw "$Label must be a native JSON string."
    }
}

function Assert-NativeJsonBoolean {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType() -ne [bool]) {
        throw "$Label must be a native JSON boolean."
    }
}

function Assert-NativeJsonInteger {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType().FullName -cnotin @('System.Int32', 'System.Int64')) {
        throw "$Label must be a native JSON integer."
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
        throw "$Label is not valid strict JSON."
    }
    Assert-NativeJsonObject -Value $value -Label $Label
    return $value
}

function Read-StrictJsonObjectFile {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $Label
    )
    [void](Assert-RegularFile -LiteralPath $LiteralPath)
    return ConvertFrom-StrictJsonObjectText `
        -Json (Get-Content -LiteralPath $LiteralPath -Raw) -Label $Label
}

function Read-BoundedStrictJsonDocument {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $Label,
        [Parameter(Mandatory = $true)][int64] $MaximumBytes
    )
    $item = Assert-RegularFile -LiteralPath $LiteralPath
    if ($MaximumBytes -lt 1 -or $item.Length -gt $MaximumBytes) {
        throw "$Label exceeds its fixed byte limit."
    }
    $stream = [IO.FileStream]::new(
        $item.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read, 4096, [IO.FileOptions]::SequentialScan
    )
    try {
        if ($stream.Length -ne $item.Length -or $stream.Length -gt $MaximumBytes) {
            throw "$Label changed before its bounded read."
        }
        $bytes = [byte[]]::new([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) {
                throw "$Label ended before its declared byte length."
            }
            $offset += $count
        }
    }
    finally {
        $stream.Dispose()
    }
    if (
        $bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and
        $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf
    ) {
        throw "$Label must be UTF-8 without a byte-order mark."
    }
    try {
        $json = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    }
    catch {
        throw "$Label is not canonical UTF-8."
    }
    $value = ConvertFrom-StrictJsonObjectText -Json $json -Label $Label
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($item.FullName)
        bytes = [int64]$bytes.Length
        sha256 = Get-ByteArraySha256 -Bytes $bytes
        json = $json
        value = $value
    }
}

function Assert-PathBelowRoot {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $AbsoluteRoot,
        [Parameter(Mandatory = $true)][string] $Label
    )
    if ($LiteralPath.IndexOf([char]0) -ge 0) {
        throw "$Label contains a forbidden null character."
    }
    $resolved = [IO.Path]::GetFullPath($LiteralPath)
    $rootPrefix = [IO.Path]::GetFullPath($AbsoluteRoot).TrimEnd('\', '/') + `
        [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must be a create-new path below the production root."
    }
    [void](Assert-RegularDirectory -LiteralPath ([IO.Path]::GetDirectoryName($resolved)))
    return $resolved
}

function ConvertTo-CanonicalUtcTime {
    param(
        [Parameter(Mandatory = $true)][string] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $parsed = ConvertTo-RequiredUtcTime -Value $Value -Label $Label
    if ($Value -cne $parsed.ToString('o')) {
        throw "$Label is not a canonical UTC DateTimeOffset value."
    }
    return $parsed
}

function Test-ExactPathEqual {
    param(
        [Parameter(Mandatory = $true)][string] $Left,
        [Parameter(Mandatory = $true)][string] $Right
    )
    return [StringComparer]::OrdinalIgnoreCase.Equals(
        [IO.Path]::GetFullPath($Left), [IO.Path]::GetFullPath($Right)
    )
}

function ConvertTo-WindowsCommandLineArgument {
    param([Parameter(Mandatory = $true)][string] $Value)
    if ($Value.IndexOf([char]0) -ge 0 -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw 'A watchdog process argument contains a forbidden control character.'
    }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($slashes * 2) + 1)))
            [void]$builder.Append('"')
        }
        else {
            if ($slashes -gt 0) {
                [void]$builder.Append(('\' * $slashes))
            }
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-OneJsonObject {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $meaningful = @($Lines | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one JSON object."
    }
    return ConvertFrom-StrictJsonObjectText -Json ([string]$meaningful[0]) -Label $Label
}

function Resolve-PinnedApplication {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Label,
        [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')]
        [string] $ApprovedSha256
    )
    $commands = @(Microsoft.PowerShell.Core\Get-Command -Name $Name -All `
        -CommandType Application -ErrorAction Stop)
    if ($commands.Count -lt 1) {
        throw "$Label application could not be resolved."
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
        if ($sha256 -ceq $ApprovedSha256) {
            $matches.Add([pscustomobject]@{
                path = $path
                sha256 = $sha256
                bytes = [int64]$item.Length
            })
        }
    }
    if ($matches.Count -ne 1) {
        throw "$Label application resolution did not find exactly one approved executable hash."
    }
    return $matches[0]
}

function Assert-ToolEnvironmentBoundary {
    $exactForbidden = @(
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
        'DOCKER_API_VERSION',
        'DOCKER_CONFIG', 'DOCKER_CLI_PLUGIN_EXTRA_DIRS', 'COMPOSE_PROVIDER',
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_INDEX_FILE'
    )
    foreach ($name in $exactForbidden) {
        if (Test-Path -LiteralPath "Env:$name") {
            throw "Release tool override environment variable is forbidden: $name"
        }
    }
    foreach ($key in [Environment]::GetEnvironmentVariables().Keys) {
        $name = [string]$key
        if ($name -imatch '^GIT_CONFIG_') {
            throw "Release Git configuration environment override is forbidden: $name"
        }
    }
}

function Get-PinnedApplicationBoundary {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $ExpectedSha256,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $item = Assert-RegularFile -LiteralPath $LiteralPath
    $actualHash = Get-FileSha256 -LiteralPath $LiteralPath
    if ($actualHash -cne $ExpectedSha256) {
        throw "$Label executable changed after it was pinned."
    }
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($LiteralPath)
        sha256 = $actualHash
        bytes = [int64]$item.Length
    }
}

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]] $Arguments)
    if ([string]::IsNullOrWhiteSpace([string]$script:dockerExecutable)) {
        throw 'The Docker application was not pinned before use.'
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 promotes redirected native stderr to a
        # terminating NativeCommandError when the caller uses Stop. Capture
        # it under Continue and decide only from the immediately sealed exit.
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:dockerExecutable @Arguments 2>&1)
        $nativeExitCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($nativeExitCode -ne 0) {
        throw "A required Docker proof command failed (exit $nativeExitCode)."
    }
    if (
        $script:transitionWitnessArmed -and
        $Arguments.Count -ge 2 -and
        $Arguments[0] -ceq 'exec' -and
        $Arguments[1] -ceq $productionPostgresId
    ) {
        $script:transitionExpectedPostgresExecCount += 1
    }
    return @($output | ForEach-Object { [string]$_ })
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string] $Repository,
        [Parameter(Mandatory = $true)][string[]] $Arguments
    )
    if ([string]::IsNullOrWhiteSpace([string]$script:gitExecutable)) {
        throw 'The Git application was not pinned before use.'
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:gitExecutable -C $Repository @Arguments 2>&1)
        $nativeExitCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($nativeExitCode -ne 0) {
        throw "A required Git provenance command failed (exit $nativeExitCode)."
    }
    return @($output | ForEach-Object { [string]$_ })
}

function ConvertTo-RequiredUtcTime {
    param(
        [Parameter(Mandatory = $true)][string] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        $Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal,
        [ref]$parsed
    )) {
        throw "$Label is not an ISO-8601 timestamp."
    }
    return $parsed.ToUniversalTime()
}

function Write-CreateNewJson {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][object] $Value
    )
    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($LiteralPath))
    [void](Assert-RegularDirectory -LiteralPath $parent)
    $serialized = $Value | ConvertTo-Json -Depth 20
    if ($serialized -match '(?i)password|api[_-]?key|secret|database_url|dsn|token') {
        throw 'Successor evidence serialization contains a forbidden secret-shaped field.'
    }
    $resolvedPath = [IO.Path]::GetFullPath($LiteralPath)
    $created = $false
    $stream = $null
    try {
        $stream = [IO.FileStream]::new(
            $resolvedPath,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        $created = $true
        $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
        try {
            $writer.Write($serialized)
            $writer.Flush()
            $stream.Flush($true)
        }
        finally {
            $writer.Dispose()
        }
    }
    catch {
        if ($created -and (Test-Path -LiteralPath $resolvedPath)) {
            Remove-Item -LiteralPath $resolvedPath -Force -ErrorAction SilentlyContinue
        }
        throw
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
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

function Assert-StringArray {
    param([AllowNull()][object] $Value, [Parameter(Mandatory = $true)][string] $Label)
    if ($null -eq $Value -or $Value.GetType() -ne [object[]]) {
        throw "$Label must be a native JSON array."
    }
    foreach ($entry in $Value) {
        Assert-NativeJsonString -Value $entry -Label "$Label entry"
    }
}

function ConvertFrom-StrictManifest {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)
    $required = @(
        'format', 'archive', 'database', 'schema_head', 'schema_head_before',
        'schema_head_after', 'started_at', 'completed_at', 'archive_bytes',
        'archive_sha256', 'catalog_entries', 'catalog_sha256', 'catalog_verified',
        'schema_stable_during_dump', 'full_restore_verified'
    )
    $values = [ordered]@{}
    foreach ($line in @(Get-Content -LiteralPath $LiteralPath)) {
        if ([string]::IsNullOrWhiteSpace([string]$line)) {
            continue
        }
        if ([string]$line -notmatch '^[a-z0-9_]+=[^\r\n]+$') {
            throw 'Backup manifest contains a malformed line.'
        }
        $pair = ([string]$line).Split('=', 2)
        if ($values.Contains($pair[0])) {
            throw "Backup manifest repeats key $($pair[0])."
        }
        $values[$pair[0]] = $pair[1]
    }
    $actualKeys = [string[]]@($values.Keys)
    $requiredKeys = [string[]]@($required)
    [Array]::Sort($actualKeys, [StringComparer]::Ordinal)
    [Array]::Sort($requiredKeys, [StringComparer]::Ordinal)
    if (($actualKeys -join "`n") -cne ($requiredKeys -join "`n")) {
        throw 'Backup manifest does not have the exact v2 property set.'
    }
    return $values
}

function Assert-HistoricalLineage {
    param(
        [switch] $ValidateBackupPayload,
        [switch] $ValidateCatalogProbe
    )

    if ($ValidateCatalogProbe -and -not $ValidateBackupPayload) {
        throw 'The archive catalog probe requires immutable backup validation.'
    }

    $resolvedBackup = [IO.Path]::GetFullPath($BackupDirectory)
    $resolvedAttestation = [IO.Path]::GetFullPath($PreMigrationAttestationPath)
    $resolvedLineage = [IO.Path]::GetFullPath($LineageDirectory)
    [void](Assert-RegularDirectory -LiteralPath $resolvedBackup)
    [void](Assert-RegularDirectory -LiteralPath $resolvedLineage)
    if ($resolvedAttestation -ine (Join-Path $resolvedBackup 'pre-migration-attestation.json')) {
        throw 'PreMigrationAttestationPath is not the exact file inside BackupDirectory.'
    }
    [void](Assert-RegularFile -LiteralPath $resolvedAttestation)
    if ((Get-FileSha256 -LiteralPath $resolvedAttestation) -cne $preMigrationAttestationSha256) {
        throw 'The historical pre-migration attestation outer SHA-256 is not the reviewed trust root.'
    }

    $attestation = Read-StrictJsonObjectFile -LiteralPath $resolvedAttestation `
        -Label 'historical pre-migration attestation'
    Assert-ExactProperties -Value $attestation -Label 'historical pre-migration attestation' `
        -Expected @('format', 'accepted', 'payload', 'payload_sha256')
    Assert-NativeJsonString -Value $attestation.format -Label 'attestation format'
    Assert-NativeJsonBoolean -Value $attestation.accepted -Label 'attestation accepted'
    Assert-NativeJsonObject -Value $attestation.payload -Label 'attestation payload'
    Assert-NativeJsonString -Value $attestation.payload_sha256 -Label 'attestation payload hash'
    if (
        $attestation.format -cne 'stratus-pre-migration-gate-v1' -or
        -not $attestation.accepted -or
        $attestation.payload_sha256 -cne $preMigrationPayloadSha256
    ) {
        throw 'The historical pre-migration attestation envelope is not exact.'
    }
    Assert-ExactProperties -Value $attestation.payload -Label 'attestation payload' -Expected @(
        'source_schema_head', 'target_schema_head', 'snapshot', 'restore', 'candidate',
        'postgres', 'migrate', 'initial_live_preconditions', 'accepted_at'
    )
    foreach ($name in @('snapshot', 'restore', 'candidate', 'postgres', 'migrate', 'initial_live_preconditions')) {
        Assert-NativeJsonObject -Value $attestation.payload.$name -Label "attestation payload $name"
    }
    foreach ($name in @('source_schema_head', 'target_schema_head', 'accepted_at')) {
        Assert-NativeJsonString -Value $attestation.payload.$name -Label "attestation payload $name"
    }
    $payloadJson = $attestation.payload | ConvertTo-Json -Depth 12 -Compress
    if ((Get-Sha256Text -Value $payloadJson) -cne $attestation.payload_sha256) {
        throw 'The historical pre-migration attestation payload hash is invalid.'
    }

    $candidate = $attestation.payload.candidate
    Assert-ExactProperties -Value $candidate -Label 'historical candidate' `
        -Expected @('revision', 'image', 'image_id')
    $migrate = $attestation.payload.migrate
    Assert-ExactProperties -Value $migrate -Label 'historical migrate identity' -Expected @(
        'container_id', 'image_id', 'config_image', 'command', 'entrypoint', 'user',
        'mounts', 'compose_project', 'compose_working_dir', 'compose_config_file',
        'compose_config_hash', 'compose_depends_on'
    )
    Assert-StringArray -Value $migrate.command -Label 'historical migrate command'
    if (
        $attestation.payload.source_schema_head -cne $sourceSchemaHead -or
        $attestation.payload.target_schema_head -cne $targetSchemaHead -or
        $candidate.revision -cne $predecessorRevision -or
        $candidate.image -cne $predecessorImage -or
        $candidate.image_id -cne $predecessorImageId -or
        $migrate.image_id -cne $predecessorImageId -or
        $migrate.config_image -cne $predecessorImage -or
        ($migrate.command -join "`n") -cne "alembic`nupgrade`nhead" -or
        $null -ne $migrate.entrypoint -or
        $migrate.user -cne 'stratus' -or
        @($migrate.mounts).Count -ne 0 -or
        $migrate.compose_project -cne 'weatherpoly' -or
        $migrate.compose_depends_on -cne ''
    ) {
        throw 'The historical candidate/migrate identity is not the exact reviewed legacy boundary.'
    }

    $historicalPostgres = $attestation.payload.postgres
    Assert-ExactProperties -Value $historicalPostgres -Label 'historical PostgreSQL identity' `
        -Expected @('container_id', 'image_id', 'config_image', 'restart_count', 'started_at')
    if (
        $historicalPostgres.container_id -cne $productionPostgresId -or
        $historicalPostgres.image_id -cne $postgres16ImageId -or
        $historicalPostgres.config_image -cne $postgres16Image -or
        $historicalPostgres.restart_count -ne 0
    ) {
        throw 'The historical PostgreSQL identity is not exact.'
    }
    [void](ConvertTo-RequiredUtcTime -Value ([string]$historicalPostgres.started_at) `
        -Label 'historical PostgreSQL started_at')

    $lineage = [ordered]@{}
    foreach ($entry in $lineageArtifacts.GetEnumerator()) {
        $path = Join-Path $resolvedLineage ([string]$entry.Key)
        [void](Assert-RegularFile -LiteralPath $path)
        $actualHash = Get-FileSha256 -LiteralPath $path
        if ($actualHash -cne [string]$entry.Value) {
            throw "Historical lineage SHA-256 drifted: $($entry.Key)"
        }
        $lineage[[string]$entry.Key] = Read-StrictJsonObjectFile `
            -LiteralPath $path -Label "historical lineage $($entry.Key)"
    }
    $stage = $lineage['09-stopped-migrate-stage-acceptance.json']
    if (
        $stage.format -cne 'stratus-section3-stopped-migrate-stage-v1' -or
        -not $stage.accepted -or
        $stage.compose.source_revision -cne $predecessorRevision -or
        $stage.compose.container_depends_on_label -cne '' -or
        -not $stage.compose.empty_label_accepted -or
        $stage.migrate.image_id -cne $predecessorImageId -or
        $stage.migrate.container_id -cne $migrate.container_id -or
        $stage.migrate.ddl_started -or
        $stage.decision -cne 'PROCEED_TO_FRESH_PRE_0047_BACKUP'
    ) {
        throw 'Stopped predecessor migrate staging lineage is not exact.'
    }
    $wrapperHold = $lineage['17-pre-migration-wrapper-false-negative.json']
    if (
        $wrapperHold.decision -cne 'HOLD_FAIL_CLOSED' -or
        $wrapperHold.migration_started -or
        $wrapperHold.pre_migration_attestation_created -or
        -not $wrapperHold.fail_closed -or
        $wrapperHold.live_enabled
    ) {
        throw 'The wrapper false-negative lineage was not fail closed.'
    }
    $compatibility = $lineage['18-pre-migration-compatibility-holds.json']
    if (
        $compatibility.decision -cne 'RESOLVED_FAIL_CLOSED_HOLDS' -or
        $compatibility.accepted_compatibility_normalizations.tracked_guard_modified -or
        $compatibility.accepted_compatibility_normalizations.patched_ast_errors -ne 0 -or
        $compatibility.independent_audits.git_blob_and_eol -cne 'PASS' -or
        $compatibility.independent_audits.patch_cardinality_and_ast -cne 'PASS' -or
        -not $compatibility.fail_closed -or
        $compatibility.live_enabled
    ) {
        throw 'The exact legacy compatibility normalization lineage is not accepted.'
    }
    $migrationAcceptance = $lineage['19-pre-migration-gate-and-0047-migration-accepted.json']
    if (
        $migrationAcceptance.format -cne 'stratus-section3-migration-acceptance-v1' -or
        $migrationAcceptance.decision -cne 'ACCEPT' -or
        $migrationAcceptance.candidate_revision -cne $predecessorRevision -or
        $migrationAcceptance.candidate_image_id -cne $predecessorImageId -or
        $migrationAcceptance.pre_migration_attestation.sha256 -cne $preMigrationAttestationSha256 -or
        $migrationAcceptance.pre_migration_attestation.payload_sha256 -cne $preMigrationPayloadSha256 -or
        $migrationAcceptance.migration.exit_code -ne 0 -or
        $migrationAcceptance.migration.restart_count -ne 0 -or
        $migrationAcceptance.migration.oom_killed -or
        $migrationAcceptance.postgres.container_id -cne $productionPostgresId -or
        -not $migrationAcceptance.postgres.identity_unchanged -or
        $migrationAcceptance.live_enabled -or
        $migrationAcceptance.binance_kill_switch_removed
    ) {
        throw 'The predecessor migration acceptance lineage is not exact.'
    }
    $postDdl = $lineage['20-strict-post-ddl-0047-accepted.json']
    if (
        $postDdl.format -cne 'stratus-section3-strict-post-ddl-v1' -or
        $postDdl.decision -cne 'ACCEPT' -or
        -not $postDdl.transaction.read_only -or
        $postDdl.schema.head_count -ne 1 -or
        $postDdl.schema.head -cne $targetSchemaHead -or
        -not $postDdl.indexes.all_exact -or
        -not $postDdl.boundaries.runtime -or
        -not $postDdl.boundaries.safety -or
        $postDdl.lease.total_rows -ne 0 -or
        $postDdl.lease.active_rows -ne 0 -or
        $postDdl.live_enabled -or
        $postDdl.binance_kill_switch_removed
    ) {
        throw 'The strict predecessor post-DDL lineage is not exact.'
    }
    $postDdlHba = $lineage['21-post-ddl-hba-accepted.json']
    if (
        $postDdlHba.format -cne 'stratus-section3-post-ddl-hba-v1' -or
        $postDdlHba.decision -cne 'ACCEPT' -or
        $postDdlHba.postgres_container_id -cne $productionPostgresId -or
        $postDdlHba.host_sha256 -cne $fixedHbaSha256 -or
        $postDdlHba.mounted_sha256 -cne $fixedHbaSha256 -or
        $postDdlHba.loaded_inventory_sha256 -cne $fixedHbaInventorySha256 -or
        $postDdlHba.loaded_line_count -ne 16 -or
        $postDdlHba.loaded_rule_count -ne 15 -or
        $postDdlHba.loaded_rule_errors -ne 0 -or
        -not $postDdlHba.reload_accepted -or
        $postDdlHba.live_enabled
    ) {
        throw 'The predecessor post-DDL HBA lineage is not exact.'
    }

    $snapshot = $attestation.payload.snapshot
    $restore = $attestation.payload.restore
    if ($ValidateBackupPayload) {
        $manifestPath = Join-Path $resolvedBackup 'manifest.txt'
        $archivePath = Join-Path $resolvedBackup 'stratus.dump'
        $catalogPath = Join-Path $resolvedBackup 'catalog.list'
        $restorePath = Join-Path $resolvedBackup 'restore-attestation.json'
        $archiveItem = Assert-RegularFile -LiteralPath $archivePath
        [void](Assert-RegularFile -LiteralPath $manifestPath)
        [void](Assert-RegularFile -LiteralPath $catalogPath)
        [void](Assert-RegularFile -LiteralPath $restorePath)
        $manifest = ConvertFrom-StrictManifest -LiteralPath $manifestPath
        if (
            $manifest.format -cne 'stratus-backup-manifest-v2' -or
            $manifest.schema_head -cne $sourceSchemaHead -or
            $manifest.schema_head_before -cne $sourceSchemaHead -or
            $manifest.schema_head_after -cne $sourceSchemaHead -or
            $manifest.catalog_verified -cne 'true' -or
            $manifest.schema_stable_during_dump -cne 'true' -or
            $manifest.full_restore_verified -cne 'false' -or
            [int64]$manifest.archive_bytes -ne $archiveItem.Length -or
            (Get-FileSha256 -LiteralPath $manifestPath) -cne $snapshot.manifest_sha256 -or
            (Get-FileSha256 -LiteralPath $archivePath) -cne $snapshot.archive_sha256 -or
            (Get-FileSha256 -LiteralPath $catalogPath) -cne $snapshot.catalog_sha256
        ) {
            throw 'The immutable pre-0047 backup payload drifted from its attestation.'
        }
        $restoreArtifact = Read-StrictJsonObjectFile -LiteralPath $restorePath `
            -Label 'historical restore attestation'
        if (
            (Get-FileSha256 -LiteralPath $restorePath) -cne $restoreAttestationSha256 -or
            $restoreArtifact.format -cne 'stratus-isolated-pg16-restore-v1' -or
            -not $restoreArtifact.accepted -or
            -not $restoreArtifact.isolated -or
            $restoreArtifact.postgres_image -cne $postgres16Image -or
            $restoreArtifact.postgres_major -ne 16 -or
            $restoreArtifact.source_manifest_sha256 -cne $snapshot.manifest_sha256 -or
            $restoreArtifact.source_archive_sha256 -cne $snapshot.archive_sha256 -or
            $restoreArtifact.source_catalog_sha256 -cne $snapshot.catalog_sha256 -or
            $restoreArtifact.source_schema_head -cne $sourceSchemaHead -or
            $restoreArtifact.restored_schema_head -cne $sourceSchemaHead -or
            -not $restoreArtifact.full_restore_verified -or
            $restoreArtifact.restore_database -cne $restore.restore_database -or
            $restoreArtifact.restore_started_at -cne $restore.restore_started_at -or
            $restoreArtifact.restore_completed_at -cne $restore.restore_completed_at
        ) {
            throw 'The isolated restore no longer binds the exact pre-0047 backup.'
        }
        if ($ValidateCatalogProbe) {
            if ($resolvedBackup -match '[,\r\n]') {
                throw 'BackupDirectory cannot be represented as a read-only Docker mount.'
            }
            $catalogProbe = @(
                Invoke-Docker -Arguments @(
                    'run', '--rm', '--network', 'none', '--read-only',
                    '--mount', "type=bind,source=$resolvedBackup,target=/evidence,readonly",
                    $postgres16Image, 'sh', '-ec',
                    'pg_restore --list /evidence/stratus.dump | sha256sum | cut -d" " -f1'
                ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
            )
            if ($catalogProbe.Count -ne 1 -or $catalogProbe[0].Trim() -cne $snapshot.catalog_sha256) {
                throw 'Pinned PostgreSQL 16 did not reproduce the historical archive catalog.'
            }
        }
    }

    return [pscustomobject]@{
        attestation = $attestation
        attestation_sha256 = $preMigrationAttestationSha256
        payload_sha256 = $preMigrationPayloadSha256
        lineage_sha256 = $lineageArtifacts
        predecessor_revision = $predecessorRevision
        predecessor_image = $predecessorImage
        predecessor_image_id = $predecessorImageId
        postgres = $historicalPostgres
        backup = [pscustomobject]@{
            manifest_sha256 = [string]$snapshot.manifest_sha256
            archive_sha256 = [string]$snapshot.archive_sha256
            archive_bytes = [int64]$snapshot.archive_bytes
            catalog_sha256 = [string]$snapshot.catalog_sha256
            catalog_entries = [int]$snapshot.catalog_entries
            restore_attestation_sha256 = $restoreAttestationSha256
        }
    }
}

function Get-OneNativeLine {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $meaningful = @($Lines | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one line."
    }
    return ([string]$meaningful[0]).Trim()
}

function Assert-ImageIdentity {
    param(
        [Parameter(Mandatory = $true)][string] $Reference,
        [Parameter(Mandatory = $true)][string] $ImageId,
        [Parameter(Mandatory = $true)][string] $Revision,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $inspect = Get-OneJsonObject -Label "$Label image inspect" -Lines (
        Invoke-Docker -Arguments @('image', 'inspect', $Reference, '--format', '{{json .}}')
    )
    Assert-NativeJsonObject -Value $inspect.Config -Label "$Label image Config"
    Assert-NativeJsonObject -Value $inspect.Config.Labels -Label "$Label image Labels"
    Assert-NativeJsonString -Value $inspect.Id -Label "$Label image Id"
    Assert-NativeJsonString -Value $inspect.Os -Label "$Label image Os"
    Assert-NativeJsonString -Value $inspect.Architecture -Label "$Label image Architecture"
    Assert-NativeJsonString -Value $inspect.Config.User -Label "$Label image User"
    Assert-NativeJsonString -Value $inspect.Config.WorkingDir -Label "$Label image WorkingDir"
    Assert-NativeJsonString -Value $inspect.Config.Labels.'org.opencontainers.image.revision' `
        -Label "$Label image revision"
    $entrypoint = Get-OptionalPropertyValue -InputObject $inspect.Config -Name 'Entrypoint'
    $environment = @(Get-OptionalPropertyValue -InputObject $inspect.Config -Name 'Env')
    $cmd = @(Get-OptionalPropertyValue -InputObject $inspect.Config -Name 'Cmd')
    if (
        $inspect.Id -cne $ImageId -or
        $inspect.Os -cne 'linux' -or
        $inspect.Architecture -cne 'amd64' -or
        $inspect.Config.User -cne 'stratus' -or
        $inspect.Config.WorkingDir -cne '/app' -or
        $inspect.Config.Labels.'org.opencontainers.image.revision' -cne $Revision -or
        $null -ne $entrypoint -or
        ($cmd -join "`n") -cne 'stratus-run' -or
        @($environment | Where-Object {
            [string]$_ -imatch '^(?:OPERATOR_API_KEY|SAFETY_API_KEY|DATABASE_URL|MIGRATION_DATABASE_URL|POSTGRES_PASSWORD)='
        }).Count -ne 0
    ) {
        throw "$Label candidate image identity/runtime boundary is not exact."
    }
    return $inspect
}

function Assert-GitAndWorktreeContract {
    $gitBefore = Get-PinnedApplicationBoundary -LiteralPath $script:gitExecutable `
        -ExpectedSha256 $script:gitExecutableSha256 -Label 'Git'
    $rootRevision = Get-OneNativeLine -Label 'production revision' -Lines (
        Invoke-Git -Repository $absoluteRoot -Arguments @('rev-parse', 'HEAD')
    )
    $buildRevision = Get-OneNativeLine -Label 'build revision' -Lines (
        Invoke-Git -Repository $absoluteBuildContext -Arguments @('rev-parse', 'HEAD')
    )
    if ($rootRevision -cne $ExpectedRevision -or $buildRevision -cne $ExpectedRevision) {
        throw 'Production and detached build worktrees must be the exact successor revision.'
    }
    $rootStatus = @(
        Invoke-Git -Repository $absoluteRoot -Arguments @(
            'status', '--porcelain=v1', '--untracked-files=all'
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    $buildStatus = @(
        Invoke-Git -Repository $absoluteBuildContext -Arguments @(
            'status', '--porcelain=v1', '--untracked-files=all'
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($rootStatus.Count -ne 0 -or $buildStatus.Count -ne 0) {
        throw 'Production and detached build worktrees must both be clean.'
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $branchOutput = @(
            & $script:gitExecutable -C $absoluteBuildContext `
                symbolic-ref -q --short HEAD 2>$null
        )
        $branchExit = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($branchExit -ne 1 -or @($branchOutput | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    }).Count -ne 0) {
        throw 'BuildContext must be a detached worktree.'
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $mergeBaseOutput = @(
            & $script:gitExecutable -C $absoluteBuildContext `
                merge-base --is-ancestor $predecessorRevision `
                $ExpectedRevision 2>$null
        )
        $mergeBaseExit = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($mergeBaseExit -ne 0) {
        throw 'The attested predecessor is not an ancestor of the successor revision.'
    }

    $manifestLines = [Collections.Generic.List[string]]::new()
    foreach ($entry in $closedGitContract.GetEnumerator()) {
        $path = [string]$entry.Key
        $expectedOid = [string]$entry.Value
        $oldOid = Get-OneNativeLine -Label "predecessor Git object $path" -Lines (
            Invoke-Git -Repository $absoluteBuildContext -Arguments @(
                'rev-parse', "$predecessorRevision`:$path"
            )
        )
        $newOid = Get-OneNativeLine -Label "successor Git object $path" -Lines (
            Invoke-Git -Repository $absoluteBuildContext -Arguments @(
                'rev-parse', "$ExpectedRevision`:$path"
            )
        )
        if ($oldOid -cne $expectedOid -or $newOid -cne $expectedOid) {
            throw "Closed migration Git contract drifted: $path"
        }
        $manifestLines.Add("$path`0$expectedOid")
    }
    $manifestArray = $manifestLines.ToArray()
    [Array]::Sort($manifestArray, [StringComparer]::Ordinal)
    $migrationFiles = @(
        Invoke-Git -Repository $absoluteBuildContext -Arguments @(
            'ls-tree', '-r', '--name-only', $ExpectedRevision, '--', 'migrations'
        ) | ForEach-Object { ([string]$_).Replace('\', '/') } |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($migrationFiles.Count -lt 47 -or @($migrationFiles | Where-Object {
        $_ -notmatch '^migrations/(?:[^/]+/)*[^/]+$'
    }).Count -ne 0) {
        throw 'Closed migration tree file inventory is malformed.'
    }
    $migrationFilesSorted = [string[]]$migrationFiles.Clone()
    [Array]::Sort($migrationFilesSorted, [StringComparer]::Ordinal)
    $gitAfter = Get-PinnedApplicationBoundary -LiteralPath $script:gitExecutable `
        -ExpectedSha256 $script:gitExecutableSha256 -Label 'Git'
    if (
        $gitAfter.path -cne $gitBefore.path -or
        $gitAfter.sha256 -cne $gitBefore.sha256 -or
        $gitAfter.bytes -ne $gitBefore.bytes
    ) {
        throw 'Git executable identity drifted during successor contract proof.'
    }
    return [pscustomobject]@{
        root_revision = $rootRevision
        build_revision = $buildRevision
        git_executable_path = $gitAfter.path
        git_executable_sha256 = $gitAfter.sha256
        git_executable_bytes = $gitAfter.bytes
        git_contract_sha256 = Get-Sha256Text -Value (($manifestArray -join "`n") + "`n")
        git_contract_entries = $manifestArray.Count
        migration_files = $migrationFilesSorted
    }
}

function Get-ImageContract {
    param(
        [Parameter(Mandatory = $true)][string] $ImageId,
        [Parameter(Mandatory = $true)][string[]] $MigrationFiles,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $explicitPaths = @(
        'Dockerfile', 'README.md', 'docker-compose.yml', 'alembic.ini',
        'ops/postgres/stratus-pg_hba.conf', 'pyproject.toml', 'uv.lock',
        'src/stratus/__init__.py', 'src/stratus/config.py',
        'src/stratus/db/__init__.py', 'src/stratus/db/base.py',
        'src/stratus/db/models.py'
    )
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
        'stratus-successor-contract-' + [guid]::NewGuid().ToString('N')
    )
    [void](New-Item -ItemType Directory -Path $tempRoot)
    $proofContainer = ''
    $proofOwned = $false
    try {
        $proofName = 'stratus-successor-proof-' + [guid]::NewGuid().ToString('N')
        $proofContainer = Get-OneNativeLine -Label "$Label stopped proof container" -Lines (
            Invoke-Docker -Arguments @(
                'create', '--name', $proofName, '--network', 'none',
                '--entrypoint', '/bin/false', $ImageId
            )
        )
        if ($proofContainer -notmatch '^[0-9a-f]{64}$') {
            throw "$Label proof container did not have an exact ID."
        }
        if ($proofContainer -ceq $productionPostgresId) {
            throw "$Label proof container collided with a protected identity."
        }
        # A successful `docker create` returning a new exact ID transfers
        # cleanup ownership immediately. Inspect is validation, not ownership;
        # failures below must not leak the stopped proof container.
        $proofOwned = $true
        $proofInspect = Get-OneJsonObject -Label "$Label stopped proof inspect" -Lines (
            Invoke-Docker -Arguments @(
                'inspect', $proofContainer, '--format', '{{json .}}'
            )
        )
        if (
            $proofInspect.Id -cne $proofContainer -or
            $proofInspect.Name -cne "/$proofName" -or
            $proofInspect.Image -cne $ImageId -or
            $proofInspect.State.Status -cne 'created' -or
            $proofInspect.State.Running -or
            $proofInspect.State.Pid -ne 0 -or
            @($proofInspect.Mounts).Count -ne 0
        ) {
            throw "$Label proof container is not the exact newly-created stopped boundary."
        }
        [void](Invoke-Docker -Arguments @(
            'cp', "${proofContainer}:/app/migrations", $tempRoot
        ))
        foreach ($path in $explicitPaths) {
            $destination = Join-Path $tempRoot ($path.Replace('/', [IO.Path]::DirectorySeparatorChar))
            $parent = [IO.Path]::GetDirectoryName($destination)
            if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
                [void](New-Item -ItemType Directory -Path $parent -Force)
            }
            [void](Invoke-Docker -Arguments @(
                'cp', "${proofContainer}:/app/$path", $destination
            ))
        }
        $linked = @(Get-ChildItem -LiteralPath $tempRoot -Force -Recurse | Where-Object {
            $_.Attributes -band [IO.FileAttributes]::ReparsePoint
        })
        if ($linked.Count -ne 0) {
            throw "$Label image contract contains a link."
        }
        $actualMigrationSources = @(
            Get-ChildItem -LiteralPath (Join-Path $tempRoot 'migrations') -File -Recurse -Force |
                Where-Object { $_.Extension -cne '.pyc' -and $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
                ForEach-Object {
                    $relative = $_.FullName.Substring($tempRoot.Length).TrimStart('\', '/')
                    $relative.Replace('\', '/')
                }
        )
        $actualSorted = [string[]]$actualMigrationSources.Clone()
        [Array]::Sort($actualSorted, [StringComparer]::Ordinal)
        if (($actualSorted -join "`n") -cne ($MigrationFiles -join "`n")) {
            throw "$Label image migration file inventory drifted from Git."
        }
        $allPaths = @($explicitPaths + $MigrationFiles)
        $sortedPaths = [string[]]$allPaths.Clone()
        [Array]::Sort($sortedPaths, [StringComparer]::Ordinal)
        $manifest = [Text.StringBuilder]::new()
        foreach ($path in $sortedPaths) {
            $localPath = Join-Path $tempRoot ($path.Replace('/', [IO.Path]::DirectorySeparatorChar))
            $item = Assert-RegularFile -LiteralPath $localPath
            [void]$manifest.Append($path)
            [void]$manifest.Append("`0")
            [void]$manifest.Append([string]$item.Length)
            [void]$manifest.Append("`0")
            [void]$manifest.Append((Get-FileSha256 -LiteralPath $localPath))
            [void]$manifest.Append("`n")
        }
        return [pscustomobject]@{
            sha256 = Get-Sha256Text -Value $manifest.ToString()
            file_count = $sortedPaths.Count
        }
    }
    finally {
        if ($proofOwned) {
            try {
                $removed = Get-OneNativeLine -Label "$Label proof cleanup" -Lines (
                    Invoke-Docker -Arguments @('rm', $proofContainer)
                )
                if ($removed -cne $proofContainer) {
                    throw "$Label proof cleanup returned the wrong identity."
                }
            }
            catch {
                throw "$Label stopped proof container could not be removed."
            }
        }
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force
        }
    }
}

function Assert-ClosedSuccessorContract {
    $gitContract = Assert-GitAndWorktreeContract
    [void](Assert-ImageIdentity -Reference $predecessorImage -ImageId $predecessorImageId `
        -Revision $predecessorRevision -Label 'predecessor')
    [void](Assert-ImageIdentity -Reference $predecessorImageId -ImageId $predecessorImageId `
        -Revision $predecessorRevision -Label 'predecessor immutable ID')
    [void](Assert-ImageIdentity -Reference $CandidateImage -ImageId $ExpectedImageId `
        -Revision $ExpectedRevision -Label 'successor')
    [void](Assert-ImageIdentity -Reference $ExpectedImageId -ImageId $ExpectedImageId `
        -Revision $ExpectedRevision -Label 'successor immutable ID')
    if ($ExpectedRevision -ceq $predecessorRevision -or $ExpectedImageId -ceq $predecessorImageId) {
        throw 'A post-migration successor must have a distinct revision and immutable image ID.'
    }
    $oldImageContract = Get-ImageContract -ImageId $predecessorImageId `
        -MigrationFiles $gitContract.migration_files -Label 'predecessor'
    $newImageContract = Get-ImageContract -ImageId $ExpectedImageId `
        -MigrationFiles $gitContract.migration_files -Label 'successor'
    if (
        $oldImageContract.file_count -ne $newImageContract.file_count -or
        $oldImageContract.sha256 -cne $newImageContract.sha256
    ) {
        throw 'Successor image migration bytes drifted from the attested predecessor image.'
    }
    return [pscustomobject]@{
        git_executable_path = $gitContract.git_executable_path
        git_executable_sha256 = $gitContract.git_executable_sha256
        git_executable_bytes = $gitContract.git_executable_bytes
        git_contract_sha256 = $gitContract.git_contract_sha256
        git_contract_entries = $gitContract.git_contract_entries
        image_contract_sha256 = $oldImageContract.sha256
        image_contract_files = $oldImageContract.file_count
    }
}

function Assert-RenderedMigrateContract {
    $renderedText = (
        Invoke-Docker -Arguments (
            $composeArguments + @('--profile', 'release', 'config', '--format', 'json')
        )
    ) -join "`n"
    $rendered = ConvertFrom-StrictJsonObjectText -Json $renderedText `
        -Label 'rendered release Compose model'
    Assert-NativeJsonObject -Value $rendered.services -Label 'rendered Compose services'
    $migrate = $rendered.services.migrate
    Assert-NativeJsonObject -Value $migrate -Label 'rendered migrate service'
    Assert-StringArray -Value $migrate.command -Label 'rendered migrate command'
    Assert-NativeJsonObject -Value $migrate.depends_on -Label 'rendered migrate depends_on'
    Assert-NativeJsonObject -Value $migrate.depends_on.postgres `
        -Label 'rendered migrate postgres dependency'
    Assert-NativeJsonObject -Value $migrate.environment -Label 'rendered migrate environment'
    $entrypoint = Get-OptionalPropertyValue -InputObject $migrate -Name 'entrypoint'
    $volumes = Get-OptionalPropertyValue -InputObject $migrate -Name 'volumes'
    $user = Get-OptionalPropertyValue -InputObject $migrate -Name 'user'
    $environmentNames = @(
        $migrate.environment.PSObject.Properties |
            ForEach-Object { [string]$_.Name }
    )
    $migrationUrl = [string](Get-OptionalPropertyValue `
        -InputObject $migrate.environment -Name 'MIGRATION_DATABASE_URL')
    $releaseDigest = [string](Get-OptionalPropertyValue `
        -InputObject $migrate.environment -Name 'RELEASE_IMAGE_DIGEST')
    if (
        $migrate.image -cne $CandidateImage -or
        ($migrate.command -join "`n") -cne "alembic`nupgrade`nhead" -or
        $null -ne $entrypoint -or
        $null -ne $volumes -or
        $null -ne $user -or
        $migrate.restart -cne 'no' -or
        @($migrate.depends_on.PSObject.Properties).Count -ne 1 -or
        $migrate.depends_on.postgres.condition -cne 'service_healthy' -or
        -not $migrate.depends_on.postgres.required -or
        $environmentNames -contains 'OPERATOR_API_KEY' -or
        $environmentNames -contains 'SAFETY_API_KEY' -or
        $migrationUrl -notmatch '^postgresql\+asyncpg://stratus:[A-Za-z0-9._~-]{24,256}@postgres:5432/stratus$' -or
        $releaseDigest -cne $ExpectedImageId.Substring(7)
    ) {
        throw 'Rendered successor migrate service is not the exact no-mount one-shot contract.'
    }
    return [pscustomobject]@{
        command = @('alembic', 'upgrade', 'head')
        restart = 'no'
        dependency = 'postgres:service_healthy'
        image = $CandidateImage
        image_id = $ExpectedImageId
        credential_values_persisted = $false
    }
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
        $serialized = $_.Value | ConvertTo-Json -Depth 10 -Compress
        "$($_.Name)`0$serialized"
    })
    [Array]::Sort($lines, [StringComparer]::Ordinal)
    return Get-Sha256Text -Value (($lines -join "`n") + "`n")
}

function Get-DockerEngineBoundary {
    Assert-ToolEnvironmentBoundary
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'Docker'
    foreach ($overrideName in @(
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
        'DOCKER_API_VERSION'
    )) {
        if (Test-Path -LiteralPath "Env:$overrideName") {
            throw "Docker endpoint override is forbidden for the successor transition: $overrideName"
        }
    }
    $contextName = Get-OneNativeLine -Label 'Docker context name' -Lines (
        Invoke-Docker -Arguments @('context', 'show')
    )
    $endpoint = Get-OneJsonObject -Label 'Docker context endpoint' -Lines (
        Invoke-Docker -Arguments @(
            'context', 'inspect', $contextName,
            '--format', '{{json .Endpoints.docker}}'
        )
    )
    $info = Get-OneJsonObject -Label 'Docker engine info' -Lines (
        Invoke-Docker -Arguments @('info', '--format', '{{json .}}')
    )
    Assert-NativeJsonObject -Value $info.ClientInfo -Label 'Docker ClientInfo'
    if ($info.ClientInfo.Plugins -isnot [Array]) {
        throw 'Docker ClientInfo.Plugins must be a native JSON array.'
    }
    $composePlugins = @($info.ClientInfo.Plugins | Where-Object {
        [string]$_.Name -ceq 'compose'
    })
    if ($composePlugins.Count -ne 1) {
        throw 'Docker ClientInfo did not expose exactly one Compose plugin.'
    }
    $composePlugin = $composePlugins[0]
    Assert-NativeJsonObject -Value $composePlugin -Label 'Docker Compose plugin'
    foreach ($name in @('Name', 'Version', 'Path')) {
        Assert-NativeJsonString -Value $composePlugin.$name `
            -Label "Docker Compose plugin $name"
    }
    if ($composePlugin.Version -cne $approvedComposePluginVersion) {
        throw 'Docker Compose plugin version is not the approved production version.'
    }
    $composeCli = Get-PinnedApplicationBoundary `
        -LiteralPath ([IO.Path]::GetFullPath([string]$composePlugin.Path)) `
        -ExpectedSha256 $approvedComposePluginSha256 `
        -Label 'Docker Compose plugin'
    foreach ($name in @('Host')) {
        Assert-NativeJsonString -Value $endpoint.$name -Label "Docker endpoint $name"
    }
    Assert-NativeJsonBoolean -Value $endpoint.SkipTLSVerify `
        -Label 'Docker endpoint SkipTLSVerify'
    foreach ($name in @('ID', 'ServerVersion', 'Name', 'DockerRootDir')) {
        Assert-NativeJsonString -Value $info.$name -Label "Docker engine $name"
    }
    if (
        [string]::IsNullOrWhiteSpace($contextName) -or
        [string]::IsNullOrWhiteSpace([string]$endpoint.Host) -or
        [string]::IsNullOrWhiteSpace([string]$info.ID) -or
        [string]::IsNullOrWhiteSpace([string]$info.ServerVersion) -or
        [string]::IsNullOrWhiteSpace([string]$info.DockerRootDir) -or
        $contextName -cne $approvedDockerContext -or
        $endpoint.Host -cne $approvedDockerEndpoint -or
        $endpoint.SkipTLSVerify -or
        $info.ID -cne $approvedDockerEngineId -or
        $info.ServerVersion -cne $approvedDockerServerVersion -or
        $info.Name -cne $approvedDockerEngineName -or
        $info.DockerRootDir -cne $approvedDockerRootDirectory
    ) {
        throw 'Docker context or engine identity is incomplete.'
    }
    $skipTlsVerify = [bool](Get-OptionalPropertyValue `
        -InputObject $endpoint -Name 'SkipTLSVerify')
    $contract = @(
        [string]$dockerCli.path,
        [string]$dockerCli.sha256,
        [string]$dockerCli.bytes,
        [string]$composeCli.path,
        [string]$composeCli.sha256,
        [string]$composeCli.bytes,
        [string]$composePlugin.Version,
        $contextName,
        [string]$endpoint.Host,
        $skipTlsVerify.ToString().ToLowerInvariant(),
        [string]$info.ID,
        [string]$info.ServerVersion,
        [string]$info.Name,
        [string]$info.DockerRootDir
    ) -join "`0"
    return [pscustomobject]@{
        docker_executable_path = [string]$dockerCli.path
        docker_executable_sha256 = [string]$dockerCli.sha256
        docker_executable_bytes = [int64]$dockerCli.bytes
        compose_executable_path = [string]$composeCli.path
        compose_executable_sha256 = [string]$composeCli.sha256
        compose_executable_bytes = [int64]$composeCli.bytes
        compose_version = [string]$composePlugin.Version
        context = $contextName
        endpoint = [string]$endpoint.Host
        skip_tls_verify = $skipTlsVerify
        engine_id = [string]$info.ID
        server_version = [string]$info.ServerVersion
        engine_name = [string]$info.Name
        docker_root_dir = [string]$info.DockerRootDir
        contract_sha256 = Get-Sha256Text -Value ($contract + "`n")
    }
}

function Assert-RepairDockerEngineBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $RepairEngine,
        [Parameter(Mandatory = $true)][object] $CurrentEngine,
        [Parameter(Mandatory = $true)][string] $Label
    )
    Assert-NativeJsonObject -Value $RepairEngine -Label $Label
    Assert-ExactProperties -Value $RepairEngine -Label $Label -Expected @(
        'docker_executable_path', 'docker_executable_sha256',
        'docker_executable_bytes', 'context', 'endpoint', 'skip_tls_verify',
        'engine_id', 'server_version', 'engine_name', 'docker_root_dir',
        'contract_sha256'
    )
    foreach ($name in @(
        'docker_executable_path', 'docker_executable_sha256', 'context', 'endpoint',
        'engine_id', 'server_version', 'engine_name', 'docker_root_dir',
        'contract_sha256'
    )) {
        Assert-NativeJsonString -Value $RepairEngine.$name -Label "$Label $name"
    }
    Assert-NativeJsonInteger -Value $RepairEngine.docker_executable_bytes `
        -Label "$Label docker_executable_bytes"
    Assert-NativeJsonBoolean -Value $RepairEngine.skip_tls_verify `
        -Label "$Label skip_tls_verify"
    $legacyContract = @(
        [string]$RepairEngine.docker_executable_path,
        [string]$RepairEngine.docker_executable_sha256,
        [string]$RepairEngine.docker_executable_bytes,
        [string]$RepairEngine.context,
        [string]$RepairEngine.endpoint,
        ([bool]$RepairEngine.skip_tls_verify).ToString().ToLowerInvariant(),
        [string]$RepairEngine.engine_id,
        [string]$RepairEngine.server_version,
        [string]$RepairEngine.engine_name,
        [string]$RepairEngine.docker_root_dir
    ) -join "`0"
    if (
        $RepairEngine.contract_sha256 -cne
            (Get-Sha256Text -Value ($legacyContract + "`n")) -or
        $RepairEngine.docker_executable_sha256 -cne $approvedDockerExecutableSha256 -or
        $RepairEngine.docker_executable_path -ine $CurrentEngine.docker_executable_path -or
        $RepairEngine.docker_executable_bytes -ne $CurrentEngine.docker_executable_bytes -or
        $RepairEngine.context -cne $CurrentEngine.context -or
        $RepairEngine.endpoint -cne $CurrentEngine.endpoint -or
        $RepairEngine.skip_tls_verify -cne $CurrentEngine.skip_tls_verify -or
        $RepairEngine.engine_id -cne $CurrentEngine.engine_id -or
        $RepairEngine.server_version -cne $CurrentEngine.server_version -or
        $RepairEngine.engine_name -cne $CurrentEngine.engine_name -or
        $RepairEngine.docker_root_dir -cne $CurrentEngine.docker_root_dir
    ) {
        throw "$Label does not bind the current approved Docker engine."
    }
}

function Assert-RepairNetworkBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Network,
        [Parameter(Mandatory = $true)][string] $NetworkName,
        [Parameter(Mandatory = $true)][string] $Label
    )
    Assert-NativeJsonObject -Value $Network -Label $Label
    Assert-ExactProperties -Value $Network -Label $Label -Expected @(
        'name', 'id', 'compose_name', 'scope', 'driver', 'subnet', 'gateway',
        'enable_ipv4', 'enable_ipv6', 'internal', 'attachable', 'ingress',
        'attached_container_ids', 'labels_sha256', 'options_sha256',
        'canonical_sha256'
    )
    $expected = $productionNetworks[$NetworkName]
    foreach ($name in @(
        'name', 'id', 'compose_name', 'scope', 'driver', 'subnet', 'gateway',
        'labels_sha256', 'options_sha256', 'canonical_sha256'
    )) {
        Assert-NativeJsonString -Value $Network.$name -Label "$Label $name"
    }
    if ($Network.attached_container_ids -isnot [Array]) {
        throw "$Label attached_container_ids must be a native JSON array."
    }
    $body = [ordered]@{
        name = [string]$Network.name
        id = [string]$Network.id
        compose_name = [string]$Network.compose_name
        scope = [string]$Network.scope
        driver = [string]$Network.driver
        subnet = [string]$Network.subnet
        gateway = [string]$Network.gateway
        enable_ipv4 = [bool]$Network.enable_ipv4
        enable_ipv6 = [bool]$Network.enable_ipv6
        internal = [bool]$Network.internal
        attachable = [bool]$Network.attachable
        ingress = [bool]$Network.ingress
        attached_container_ids = @($Network.attached_container_ids)
        labels_sha256 = [string]$Network.labels_sha256
        options_sha256 = [string]$Network.options_sha256
    }
    foreach ($name in @('enable_ipv4', 'enable_ipv6', 'internal', 'attachable', 'ingress')) {
        Assert-NativeJsonBoolean -Value $Network.$name -Label "$Label $name"
    }
    if (
        $Network.name -cne $NetworkName -or
        $Network.id -cne [string]$expected.id -or
        $Network.compose_name -cne [string]$expected.compose_name -or
        $Network.scope -cne 'local' -or $Network.driver -cne 'bridge' -or
        $Network.subnet -cne [string]$expected.subnet -or
        $Network.gateway -cne [string]$expected.gateway -or
        -not $Network.enable_ipv4 -or $Network.enable_ipv6 -or
        $Network.internal -or $Network.attachable -or $Network.ingress -or
        @($Network.attached_container_ids).Count -ne 0 -or
        $Network.labels_sha256 -cne [string]$expected.labels_sha256 -or
        $Network.options_sha256 -cne $productionNetworkOptionsSha256 -or
        $Network.canonical_sha256 -cne (Get-Sha256Text -Value (
            ($body | ConvertTo-Json -Depth 8 -Compress)
        ))
    ) {
        throw "$Label is not the exact stopped production network boundary."
    }
}

function Assert-RepairAttachmentBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Attachment,
        [Parameter(Mandatory = $true)][string] $NetworkName,
        [Parameter(Mandatory = $true)][string] $Label
    )
    Assert-NativeJsonObject -Value $Attachment -Label $Label
    Assert-ExactProperties -Value $Attachment -Label $Label -Expected @(
        'name', 'network_id', 'gateway_priority', 'aliases', 'dns_names',
        'endpoint_id', 'gateway', 'ip_address', 'ip_prefix_length',
        'ipv6_gateway', 'global_ipv6_address', 'global_ipv6_prefix_length',
        'mac_address', 'canonical_sha256'
    )
    Assert-NativeJsonInteger -Value $Attachment.gateway_priority `
        -Label "$Label gateway_priority"
    Assert-NativeJsonInteger -Value $Attachment.ip_prefix_length `
        -Label "$Label ip_prefix_length"
    Assert-NativeJsonInteger -Value $Attachment.global_ipv6_prefix_length `
        -Label "$Label global_ipv6_prefix_length"
    foreach ($name in @(
        'name', 'network_id', 'endpoint_id', 'gateway', 'ip_address',
        'ipv6_gateway', 'global_ipv6_address', 'mac_address', 'canonical_sha256'
    )) {
        Assert-NativeJsonString -Value $Attachment.$name -Label "$Label $name"
    }
    if ($Attachment.aliases -isnot [Array] -or $Attachment.dns_names -isnot [Array]) {
        throw "$Label aliases and dns_names must be native JSON arrays."
    }
    foreach ($value in @($Attachment.aliases) + @($Attachment.dns_names)) {
        Assert-NativeJsonString -Value $value -Label "$Label attachment name"
    }
    $aliases = @($Attachment.aliases)
    [Array]::Sort($aliases, [StringComparer]::Ordinal)
    $dnsNames = @($Attachment.dns_names)
    [Array]::Sort($dnsNames, [StringComparer]::Ordinal)
    $expectedAliases = @('postgres', 'weatherpoly-postgres-1')
    [Array]::Sort($expectedAliases, [StringComparer]::Ordinal)
    $expectedDns = @(
        $productionPostgresId.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1'
    )
    [Array]::Sort($expectedDns, [StringComparer]::Ordinal)
    $body = [ordered]@{
        name = [string]$Attachment.name
        network_id = [string]$Attachment.network_id
        gateway_priority = [int]$Attachment.gateway_priority
        aliases = $aliases
        dns_names = $dnsNames
        endpoint_id = [string]$Attachment.endpoint_id
        gateway = [string]$Attachment.gateway
        ip_address = [string]$Attachment.ip_address
        ip_prefix_length = [int]$Attachment.ip_prefix_length
        ipv6_gateway = [string]$Attachment.ipv6_gateway
        global_ipv6_address = [string]$Attachment.global_ipv6_address
        global_ipv6_prefix_length = [int]$Attachment.global_ipv6_prefix_length
        mac_address = [string]$Attachment.mac_address
    }
    if (
        $Attachment.name -cne $NetworkName -or
        $Attachment.network_id -cne [string]$productionNetworks[$NetworkName].id -or
        $Attachment.gateway_priority -ne 0 -or
        ($aliases -join "`n") -cne ($expectedAliases -join "`n") -or
        ($dnsNames -join "`n") -cne ($expectedDns -join "`n") -or
        -not [string]::IsNullOrEmpty([string]$Attachment.endpoint_id) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.gateway) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.ip_address) -or
        $Attachment.ip_prefix_length -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Attachment.ipv6_gateway) -or
        -not [string]::IsNullOrEmpty([string]$Attachment.global_ipv6_address) -or
        $Attachment.global_ipv6_prefix_length -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$Attachment.mac_address) -or
        $Attachment.canonical_sha256 -cne (Get-Sha256Text -Value (
            ($body | ConvertTo-Json -Depth 8 -Compress)
        ))
    ) {
        throw "$Label is not an exact stopped PostgreSQL attachment."
    }
}

function Assert-RepairSnapshotBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $Snapshot,
        [Parameter(Mandatory = $true)][object] $CurrentEngine,
        [Parameter(Mandatory = $true)][string] $Label,
        [switch] $RequireExactAttachment
    )
    Assert-NativeJsonObject -Value $Snapshot -Label $Label
    Assert-ExactProperties -Value $Snapshot -Label $Label -Expected @(
        'canonical_sha256', 'docker_engine', 'running_container_ids',
        'lifecycle_inventory', 'lifecycle_inventory_sha256', 'postgres',
        'production_networks', 'production_networks_sha256'
    )
    Assert-RepairDockerEngineBoundary -RepairEngine $Snapshot.docker_engine `
        -CurrentEngine $CurrentEngine -Label "$Label docker_engine"
    foreach ($name in @(
        'canonical_sha256', 'lifecycle_inventory_sha256', 'production_networks_sha256'
    )) {
        Assert-NativeJsonString -Value $Snapshot.$name -Label "$Label $name"
        if ($Snapshot.$name -notmatch '^[0-9a-f]{64}$') {
            throw "$Label $name is not an exact SHA-256."
        }
    }
    if (
        $Snapshot.running_container_ids -isnot [Array] -or
        $Snapshot.lifecycle_inventory -isnot [Array] -or
        @($Snapshot.running_container_ids).Count -ne 0
    ) {
        throw "$Label contains a running container."
    }
    Assert-NativeJsonObject -Value $Snapshot.postgres -Label "$Label postgres"
    Assert-ExactProperties -Value $Snapshot.postgres -Label "$Label postgres" -Expected @(
        'stable_identity', 'stable_identity_sha256', 'attachment_state',
        'attachments', 'attachments_sha256'
    )
    $stable = $Snapshot.postgres.stable_identity
    Assert-NativeJsonObject -Value $stable -Label "$Label stable PostgreSQL identity"
    Assert-ExactProperties -Value $stable -Label "$Label stable PostgreSQL identity" -Expected @(
        'container_id', 'name', 'image_id', 'config_image', 'command_sha256',
        'compose_config_hash', 'state', 'running', 'started_at', 'finished_at',
        'restart_count', 'oom_killed', 'exit_code', 'network_mode', 'hba_sha256',
        'hba_source', 'data_volume', 'published_postgres', 'labels_sha256'
    )
    foreach ($name in @('running', 'oom_killed')) {
        Assert-NativeJsonBoolean -Value $stable.$name -Label "$Label stable $name"
    }
    foreach ($name in @(
        'container_id', 'name', 'image_id', 'config_image', 'command_sha256',
        'compose_config_hash', 'state', 'started_at', 'finished_at',
        'network_mode', 'hba_sha256', 'hba_source', 'data_volume',
        'published_postgres', 'labels_sha256'
    )) {
        Assert-NativeJsonString -Value $stable.$name -Label "$Label stable $name"
    }
    $allowedHbaSources = @(Get-DockerHostPathAliases -Path (
        Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    ))
    foreach ($name in @('restart_count', 'exit_code')) {
        Assert-NativeJsonInteger -Value $stable.$name -Label "$Label stable $name"
    }
    if (
        $stable.container_id -cne $productionPostgresId -or
        $stable.name -cne '/weatherpoly-postgres-1' -or
        $stable.image_id -cne $postgres16ImageId -or
        $stable.config_image -cne $postgres16Image -or
        $stable.command_sha256 -cne $productionPostgresCommandSha256 -or
        $stable.compose_config_hash -cne $productionPostgresConfigHash -or
        $stable.state -cne 'exited' -or $stable.running -or
        $stable.restart_count -ne 0 -or $stable.oom_killed -or
        $stable.exit_code -ne 0 -or $stable.network_mode -cne 'weatherpoly_default' -or
        $stable.hba_sha256 -cne $fixedHbaSha256 -or
        ([string]$stable.hba_source).Replace('\', '/') -notin $allowedHbaSources -or
        $stable.data_volume -cne 'weatherpoly_stratus-postgres' -or
        $stable.published_postgres -cne '127.0.0.1:5432' -or
        $Snapshot.postgres.stable_identity_sha256 -cne (Get-Sha256Text -Value (
            ($stable | ConvertTo-Json -Depth 10 -Compress)
        ))
    ) {
        throw "$Label retained PostgreSQL stable identity is not exact."
    }
    [void](ConvertTo-RequiredUtcTime -Value ([string]$stable.started_at) `
        -Label "$Label PostgreSQL started_at")
    [void](ConvertTo-RequiredUtcTime -Value ([string]$stable.finished_at) `
        -Label "$Label PostgreSQL finished_at")
    Assert-NativeJsonObject -Value $Snapshot.postgres.attachments `
        -Label "$Label PostgreSQL attachments"
    $attachmentProperties = @($Snapshot.postgres.attachments.PSObject.Properties)
    $attachmentNames = @($attachmentProperties.Name)
    [Array]::Sort($attachmentNames, [StringComparer]::Ordinal)
    $expectedAttachmentNames = if ($RequireExactAttachment) {
        @($productionNetworks.Keys)
    }
    elseif ($Snapshot.postgres.attachment_state -ceq 'default_only') {
        @('weatherpoly_default')
    }
    elseif ($Snapshot.postgres.attachment_state -ceq 'exact') {
        @($productionNetworks.Keys)
    }
    else {
        throw "$Label has an invalid stopped attachment disposition."
    }
    [Array]::Sort($expectedAttachmentNames, [StringComparer]::Ordinal)
    if (
        ($RequireExactAttachment -and $Snapshot.postgres.attachment_state -cne 'exact') -or
        ($attachmentNames -join "`n") -cne ($expectedAttachmentNames -join "`n")
    ) {
        throw "$Label does not contain the required stopped attachment set."
    }
    foreach ($name in $expectedAttachmentNames) {
        Assert-RepairAttachmentBoundary `
            -Attachment $Snapshot.postgres.attachments.$name `
            -NetworkName $name -Label "$Label attachment $name"
    }
    if ($Snapshot.postgres.attachments_sha256 -cne (Get-Sha256Text -Value (
        ($Snapshot.postgres.attachments | ConvertTo-Json -Depth 12 -Compress)
    ))) {
        throw "$Label attachment-set digest is invalid."
    }
    Assert-NativeJsonObject -Value $Snapshot.production_networks `
        -Label "$Label production_networks"
    Assert-ExactProperties -Value $Snapshot.production_networks `
        -Label "$Label production_networks" -Expected @($productionNetworks.Keys)
    foreach ($name in @($productionNetworks.Keys)) {
        Assert-RepairNetworkBoundary -Network $Snapshot.production_networks.$name `
            -NetworkName $name -Label "$Label network $name"
    }
    if ($Snapshot.production_networks_sha256 -cne (Get-Sha256Text -Value (
        ($Snapshot.production_networks | ConvertTo-Json -Depth 12 -Compress)
    ))) {
        throw "$Label production-network digest is invalid."
    }
    $body = [ordered]@{
        docker_engine = $Snapshot.docker_engine
        running_container_ids = @($Snapshot.running_container_ids)
        lifecycle_inventory = @($Snapshot.lifecycle_inventory)
        lifecycle_inventory_sha256 = [string]$Snapshot.lifecycle_inventory_sha256
        postgres = $Snapshot.postgres
        production_networks = $Snapshot.production_networks
        production_networks_sha256 = [string]$Snapshot.production_networks_sha256
    }
    if ($Snapshot.canonical_sha256 -cne (Get-Sha256Text -Value (
        ($body | ConvertTo-Json -Depth 20 -Compress)
    ))) {
        throw "$Label canonical digest is invalid."
    }
}

function Assert-NetworkRepairAttestation {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][object] $CurrentEngine
    )
    $item = Assert-RegularFile -LiteralPath $LiteralPath
    $fileSha256 = Get-FileSha256 -LiteralPath $LiteralPath
    $envelope = Read-StrictJsonObjectFile -LiteralPath $LiteralPath `
        -Label 'stopped PostgreSQL network repair attestation'
    Assert-ExactProperties -Value $envelope `
        -Label 'network repair attestation root' `
        -Expected @('format', 'accepted', 'payload', 'payload_sha256')
    Assert-NativeJsonString -Value $envelope.format -Label 'network repair format'
    Assert-NativeJsonBoolean -Value $envelope.accepted -Label 'network repair accepted'
    Assert-NativeJsonObject -Value $envelope.payload -Label 'network repair payload'
    Assert-NativeJsonString -Value $envelope.payload_sha256 `
        -Label 'network repair payload_sha256'
    $payload = $envelope.payload
    Assert-ExactProperties -Value $payload -Label 'network repair payload' -Expected @(
        'accepted_at', 'disposition', 'fixed_identity', 'docker_engine',
        'snapshots', 'comparisons', 'mutation', 'authorization'
    )
    Assert-NativeJsonString -Value $payload.accepted_at `
        -Label 'network repair accepted_at'
    Assert-NativeJsonString -Value $payload.disposition `
        -Label 'network repair disposition'
    if (
        $envelope.format -cne 'stratus-stopped-postgres-safety-plane-repair-v1' -or
        -not $envelope.accepted -or
        $envelope.payload_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $envelope.payload_sha256 -cne (Get-Sha256Text -Value (
            ($payload | ConvertTo-Json -Depth 30 -Compress)
        ))
    ) {
        throw 'Stopped PostgreSQL network repair envelope is not authentic.'
    }
    $acceptedAt = ConvertTo-RequiredUtcTime -Value ([string]$payload.accepted_at) `
        -Label 'network repair accepted_at'
    $now = [DateTimeOffset]::UtcNow
    $lastWrite = [DateTimeOffset]$item.LastWriteTimeUtc
    if (
        $acceptedAt -gt $now.AddSeconds($clockSkewSeconds) -or
        $acceptedAt -lt $now.AddSeconds(-$networkRepairMaxAgeSeconds) -or
        $lastWrite -lt $acceptedAt.AddSeconds(-$clockSkewSeconds) -or
        $lastWrite -gt $now.AddSeconds($clockSkewSeconds)
    ) {
        throw 'Stopped PostgreSQL network repair evidence is stale or has invalid file time.'
    }
    Assert-NativeJsonObject -Value $payload.fixed_identity -Label 'network repair fixed_identity'
    Assert-ExactProperties -Value $payload.fixed_identity -Label 'network repair fixed_identity' `
        -Expected @(
            'postgres_container_id', 'postgres_container_name',
            'default_network_id', 'safety_network_id'
        )
    foreach ($name in @(
        'postgres_container_id', 'postgres_container_name',
        'default_network_id', 'safety_network_id'
    )) {
        Assert-NativeJsonString -Value $payload.fixed_identity.$name `
            -Label "network repair fixed_identity $name"
    }
    if (
        $payload.fixed_identity.postgres_container_id -cne $productionPostgresId -or
        $payload.fixed_identity.postgres_container_name -cne 'weatherpoly-postgres-1' -or
        $payload.fixed_identity.default_network_id -cne
            [string]$productionNetworks['weatherpoly_default'].id -or
        $payload.fixed_identity.safety_network_id -cne
            [string]$productionNetworks['weatherpoly_safety-plane'].id
    ) {
        throw 'Network repair fixed production identity is not exact.'
    }
    Assert-RepairDockerEngineBoundary -RepairEngine $payload.docker_engine `
        -CurrentEngine $CurrentEngine -Label 'network repair Docker engine'
    Assert-NativeJsonObject -Value $payload.snapshots -Label 'network repair snapshots'
    Assert-ExactProperties -Value $payload.snapshots -Label 'network repair snapshots' `
        -Expected @('pre', 'armed', 'post_first', 'post_second')
    Assert-RepairSnapshotBoundary -Snapshot $payload.snapshots.pre `
        -CurrentEngine $CurrentEngine -Label 'network repair pre snapshot'
    Assert-RepairSnapshotBoundary -Snapshot $payload.snapshots.armed `
        -CurrentEngine $CurrentEngine -Label 'network repair armed snapshot'
    Assert-RepairSnapshotBoundary -Snapshot $payload.snapshots.post_first `
        -CurrentEngine $CurrentEngine -Label 'network repair post_first snapshot' `
        -RequireExactAttachment
    Assert-RepairSnapshotBoundary -Snapshot $payload.snapshots.post_second `
        -CurrentEngine $CurrentEngine -Label 'network repair post_second snapshot' `
        -RequireExactAttachment
    Assert-NativeJsonObject -Value $payload.comparisons -Label 'network repair comparisons'
    $comparisonNames = @(
        'pre_equals_armed', 'post_first_equals_post_second',
        'docker_engine_unchanged', 'all_container_lifecycle_inventory_unchanged',
        'postgres_stopped_identity_unchanged',
        'production_network_identity_unchanged', 'post_attachment_exact_twice'
    )
    Assert-ExactProperties -Value $payload.comparisons `
        -Label 'network repair comparisons' -Expected $comparisonNames
    foreach ($name in $comparisonNames) {
        Assert-NativeJsonBoolean -Value $payload.comparisons.$name `
            -Label "network repair comparison $name"
        if (-not $payload.comparisons.$name) {
            throw "Network repair comparison was not accepted: $name"
        }
    }
    if (
        $payload.snapshots.pre.canonical_sha256 -cne
            $payload.snapshots.armed.canonical_sha256 -or
        $payload.snapshots.post_first.canonical_sha256 -cne
            $payload.snapshots.post_second.canonical_sha256 -or
        $payload.snapshots.pre.docker_engine.contract_sha256 -cne
            $payload.snapshots.post_second.docker_engine.contract_sha256 -or
        $payload.snapshots.pre.lifecycle_inventory_sha256 -cne
            $payload.snapshots.post_second.lifecycle_inventory_sha256 -or
        $payload.snapshots.pre.postgres.stable_identity_sha256 -cne
            $payload.snapshots.post_second.postgres.stable_identity_sha256 -or
        $payload.snapshots.pre.production_networks_sha256 -cne
            $payload.snapshots.post_second.production_networks_sha256
    ) {
        throw 'Network repair snapshots do not prove exact stable stopped lineage.'
    }
    Assert-NativeJsonObject -Value $payload.mutation -Label 'network repair mutation'
    Assert-ExactProperties -Value $payload.mutation -Label 'network repair mutation' `
        -Expected @('issued', 'command', 'output_line_count')
    Assert-NativeJsonBoolean -Value $payload.mutation.issued `
        -Label 'network repair mutation issued'
    Assert-NativeJsonInteger -Value $payload.mutation.output_line_count `
        -Label 'network repair mutation output_line_count'
    if ($payload.mutation.command -isnot [Array]) {
        throw 'Network repair mutation command must be a native JSON array.'
    }
    $expectedMutation = @(
        'network', 'connect', '--alias', 'postgres', '--alias',
        'weatherpoly-postgres-1', '--gw-priority', '0',
        [string]$productionNetworks['weatherpoly_safety-plane'].id,
        $productionPostgresId
    )
    if (
        $payload.disposition -cnotin @(
            'connected_by_this_invocation', 'verified_exact_existing'
        ) -or
        ($payload.mutation.command -join "`n") -cne ($expectedMutation -join "`n") -or
        $payload.mutation.output_line_count -ne 0 -or
        ($payload.disposition -ceq 'connected_by_this_invocation') -ne
            [bool]$payload.mutation.issued
    ) {
        throw 'Network repair mutation/disposition evidence is not exact.'
    }
    if (
        ($payload.disposition -ceq 'connected_by_this_invocation' -and (
            $payload.snapshots.pre.postgres.attachment_state -cne 'default_only' -or
            $payload.snapshots.armed.postgres.attachment_state -cne 'default_only'
        )) -or
        ($payload.disposition -ceq 'verified_exact_existing' -and (
            $payload.snapshots.pre.postgres.attachment_state -cne 'exact' -or
            $payload.snapshots.armed.postgres.attachment_state -cne 'exact' -or
            $payload.snapshots.pre.canonical_sha256 -cne
                $payload.snapshots.post_second.canonical_sha256
        ))
    ) {
        throw 'Network repair disposition does not match its before/after attachment state.'
    }
    Assert-NativeJsonObject -Value $payload.authorization -Label 'network repair authorization'
    $authorizationNames = @(
        'postgres_start_permitted', 'api_start_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_permitted',
        'binance_kill_switch_removal_permitted'
    )
    Assert-ExactProperties -Value $payload.authorization `
        -Label 'network repair authorization' -Expected $authorizationNames
    foreach ($name in $authorizationNames) {
        Assert-NativeJsonBoolean -Value $payload.authorization.$name `
            -Label "network repair authorization $name"
        if ($payload.authorization.$name) {
            throw "Network repair evidence granted forbidden authorization: $name"
        }
    }
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($LiteralPath)
        sha256 = $fileSha256
        payload_sha256 = [string]$envelope.payload_sha256
        accepted_at = $acceptedAt.ToString('o')
        expires_at = $acceptedAt.AddSeconds($networkRepairMaxAgeSeconds).ToString('o')
        disposition = [string]$payload.disposition
        post_first_sha256 = [string]$payload.snapshots.post_first.canonical_sha256
        post_second_sha256 = [string]$payload.snapshots.post_second.canonical_sha256
        final_attachment_sha256 =
            [string]$payload.snapshots.post_second.postgres.attachments_sha256
        docker_engine_contract_sha256 = [string]$payload.docker_engine.contract_sha256
        post_second = $payload.snapshots.post_second
    }
}

function Assert-NetworkRepairEvidenceUnchanged {
    param([Parameter(Mandatory = $true)][object] $Evidence)
    [void](Assert-RegularFile -LiteralPath $Evidence.path)
    if ((Get-FileSha256 -LiteralPath $Evidence.path) -cne $Evidence.sha256) {
        throw 'Stopped PostgreSQL network repair evidence changed during transition.'
    }
    $expiresAt = ConvertTo-RequiredUtcTime -Value $Evidence.expires_at `
        -Label 'network repair expires_at'
    if ([DateTimeOffset]::UtcNow -gt $expiresAt.AddSeconds($clockSkewSeconds)) {
        throw 'Stopped PostgreSQL network repair evidence expired during transition.'
    }
}

function Assert-NetworkRepairCurrentPostgres {
    param(
        [Parameter(Mandatory = $true)][object] $Evidence,
        [Parameter(Mandatory = $true)][object] $CurrentPostgres
    )
    $repairPostgres = $Evidence.post_second.postgres
    $stable = $repairPostgres.stable_identity
    if (
        $CurrentPostgres.container_id -cne $stable.container_id -or
        $CurrentPostgres.image_id -cne $stable.image_id -or
        $CurrentPostgres.config_image -cne $stable.config_image -or
        $CurrentPostgres.started_at -cne $stable.started_at -or
        $CurrentPostgres.finished_at -cne $stable.finished_at -or
        $CurrentPostgres.restart_count -ne $stable.restart_count -or
        $CurrentPostgres.state -cne $stable.state -or
        $CurrentPostgres.oom_killed -ne $stable.oom_killed -or
        $CurrentPostgres.exit_code -ne $stable.exit_code -or
        $CurrentPostgres.network_mode -cne $stable.network_mode
    ) {
        throw 'Current retained PostgreSQL drifted from network repair evidence.'
    }
    foreach ($name in @($productionNetworks.Keys)) {
        $current = $CurrentPostgres.networks.$name
        $repair = $repairPostgres.attachments.$name
        if (
            $current.id -cne $repair.network_id -or
            ($current.aliases -join "`n") -cne ($repair.aliases -join "`n") -or
            ($current.dns_names -join "`n") -cne ($repair.dns_names -join "`n") -or
            -not [string]::IsNullOrEmpty([string]$current.endpoint_id) -or
            -not [string]::IsNullOrEmpty([string]$current.ip_address)
        ) {
            throw "Current stopped PostgreSQL attachment drifted from repair evidence: $name"
        }
    }
}

function Get-ProductionNetworkBoundaries {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Stopped', 'Running')]
        [string] $ExpectedState
    )
    $result = [ordered]@{}
    $contractLines = [Collections.Generic.List[string]]::new()
    foreach ($entry in $productionNetworks.GetEnumerator()) {
        $expectedName = [string]$entry.Key
        $expected = $entry.Value
        $network = Get-OneJsonObject -Label "production Docker network $expectedName" -Lines (
            Invoke-Docker -Arguments @(
                'network', 'inspect', [string]$expected.id, '--format', '{{json .}}'
            )
        )
        foreach ($name in @('IPAM', 'Labels', 'Containers', 'ConfigFrom')) {
            Assert-NativeJsonObject -Value $network.$name `
                -Label "production network $expectedName $name"
        }
        Assert-ExactProperties -Value $network.IPAM `
            -Label "production network $expectedName IPAM" `
            -Expected @('Driver', 'Options', 'Config')
        Assert-ExactProperties -Value $network.ConfigFrom `
            -Label "production network $expectedName ConfigFrom" `
            -Expected @('Network')
        foreach ($name in @(
            'EnableIPv4', 'EnableIPv6', 'Internal', 'Attachable', 'Ingress',
            'ConfigOnly'
        )) {
            Assert-NativeJsonBoolean -Value $network.$name `
                -Label "production network $expectedName $name"
        }
        Assert-NativeJsonString -Value $network.ConfigFrom.Network `
            -Label "production network $expectedName ConfigFrom.Network"
        if ($network.IPAM.Config -isnot [Array]) {
            throw "Production network $expectedName IPAM.Config must be a native JSON array."
        }
        $ipamConfig = @($network.IPAM.Config)
        $ipRange = $null
        $auxiliaryAddresses = $null
        if ($ipamConfig.Count -eq 1) {
            $ipRange = Get-OptionalPropertyValue -InputObject $ipamConfig[0] -Name 'IPRange'
            $auxiliaryAddresses = Get-OptionalPropertyValue `
                -InputObject $ipamConfig[0] -Name 'AuxiliaryAddresses'
        }
        $containerIds = @(
            $network.Containers.PSObject.Properties |
                ForEach-Object { [string]$_.Name }
        )
        [Array]::Sort($containerIds, [StringComparer]::Ordinal)
        $expectedContainerIds = if ($ExpectedState -eq 'Running') {
            @($productionPostgresId)
        }
        else {
            @()
        }
        Assert-NativeJsonString -Value $network.Created `
            -Label "production network $expectedName Created"
        if (
            $network.Name -cne $expectedName -or
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
            ($containerIds -join "`n") -cne ($expectedContainerIds -join "`n")
        ) {
            throw "Production Docker network identity/topology is not exact: $expectedName"
        }
        $labelsHash = Get-CanonicalPropertyHash -Value $network.Labels `
            -Label "production network $expectedName labels"
        $optionsHash = Get-CanonicalPropertyHash `
            -Value (Get-OptionalPropertyValue -InputObject $network -Name 'Options') `
            -Label "production network $expectedName options"
        if (
            $labelsHash -cne [string]$expected.labels_sha256 -or
            $optionsHash -cne $productionNetworkOptionsSha256
        ) {
            throw "Production Docker network labels/options drifted: $expectedName"
        }
        $contractLines.Add((@(
            $expectedName, [string]$network.Id, [string]$network.Driver,
            [string]$network.Created, [string]$network.Scope,
            [string]$ipamConfig[0].Subnet,
            [string]$ipamConfig[0].Gateway, $labelsHash, $optionsHash
        ) -join '|'))
        $memberEndpoint = $null
        if ($ExpectedState -eq 'Running') {
            $memberEndpoint = $network.Containers.$productionPostgresId
            Assert-NativeJsonObject -Value $memberEndpoint `
                -Label "production network $expectedName PostgreSQL endpoint"
            Assert-ExactProperties -Value $memberEndpoint `
                -Label "production network $expectedName PostgreSQL endpoint" `
                -Expected @(
                    'Name', 'EndpointID', 'MacAddress', 'IPv4Address', 'IPv6Address'
                )
        }
        $result[$expectedName] = [pscustomobject]@{
            name = $expectedName
            id = [string]$network.Id
            created = [string]$network.Created
            compose_name = [string]$expected.compose_name
            driver = [string]$network.Driver
            scope = [string]$network.Scope
            subnet = [string]$ipamConfig[0].Subnet
            gateway = [string]$ipamConfig[0].Gateway
            labels_sha256 = $labelsHash
            options_sha256 = $optionsHash
            attached_container_ids = $containerIds
            postgres_endpoint = $memberEndpoint
        }
    }
    return [pscustomobject]@{
        entries = [pscustomobject]$result
        contract_sha256 = Get-Sha256Text -Value (
            ($contractLines.ToArray() -join "`n") + "`n"
        )
    }
}

function Get-DockerHostPathAliases {
    param([Parameter(Mandatory = $true)][string] $Path)
    $normalized = [IO.Path]::GetFullPath($Path).Replace('\', '/')
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

function Get-PostgresBoundary {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Stopped', 'Running')]
        [string] $ExpectedState,
        [Parameter(Mandatory = $true)]
        [ValidateSet('unless-stopped', 'no')]
        [string] $ExpectedRestartPolicy
    )
    $inspect = Get-OneJsonObject -Label 'retained PostgreSQL inspect' -Lines (
        Invoke-Docker -Arguments @('inspect', $PostgresContainer, '--format', '{{json .}}')
    )
    foreach ($name in @('Config', 'State', 'HostConfig', 'NetworkSettings')) {
        Assert-NativeJsonObject -Value $inspect.$name -Label "PostgreSQL $name"
    }
    Assert-NativeJsonObject -Value $inspect.Config.Healthcheck `
        -Label 'PostgreSQL Config.Healthcheck'
    if ($inspect.Config.Healthcheck.Test -isnot [Array]) {
        throw 'PostgreSQL Config.Healthcheck.Test must be a native JSON array.'
    }
    $healthcheckTest = @($inspect.Config.Healthcheck.Test)
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Networks `
        -Label 'PostgreSQL NetworkSettings.Networks'
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Ports `
        -Label 'PostgreSQL NetworkSettings.Ports'
    Assert-NativeJsonObject -Value $inspect.HostConfig.PortBindings `
        -Label 'PostgreSQL HostConfig.PortBindings'
    if ($inspect.Mounts -isnot [Array]) {
        throw 'PostgreSQL Mounts must be a native JSON array.'
    }
    foreach ($name in @('Id', 'Name', 'Image')) {
        Assert-NativeJsonString -Value $inspect.$name -Label "PostgreSQL $name"
    }
    Assert-NativeJsonString -Value $inspect.Config.Image -Label 'PostgreSQL Config.Image'
    Assert-NativeJsonString -Value $inspect.State.StartedAt -Label 'PostgreSQL StartedAt'
    Assert-NativeJsonString -Value $inspect.State.FinishedAt -Label 'PostgreSQL FinishedAt'
    Assert-NativeJsonBoolean -Value $inspect.State.Running -Label 'PostgreSQL Running'
    Assert-NativeJsonBoolean -Value $inspect.State.OOMKilled -Label 'PostgreSQL OOMKilled'
    Assert-NativeJsonInteger -Value $inspect.RestartCount -Label 'PostgreSQL RestartCount'
    $hbaMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/etc/postgresql/stratus-pg_hba.conf'
    })
    $dataMounts = @($inspect.Mounts | Where-Object {
        [string]$_.Destination -ceq '/var/lib/postgresql/data'
    })
    $hbaSources = @(Get-DockerHostPathAliases -Path (
        Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    ))
    if ($hbaMounts.Count -eq 1) {
        Assert-NativeJsonObject -Value $hbaMounts[0] -Label 'PostgreSQL HBA mount'
        Assert-ExactProperties -Value $hbaMounts[0] -Label 'PostgreSQL HBA mount' `
            -Expected @('Type', 'Source', 'Destination', 'Mode', 'RW', 'Propagation')
    }
    if ($dataMounts.Count -eq 1) {
        Assert-NativeJsonObject -Value $dataMounts[0] -Label 'PostgreSQL data mount'
        Assert-ExactProperties -Value $dataMounts[0] -Label 'PostgreSQL data mount' `
            -Expected @(
                'Type', 'Name', 'Source', 'Destination', 'Driver', 'Mode', 'RW',
                'Propagation'
            )
    }
    $port = $inspect.HostConfig.PortBindings.'5432/tcp'
    if ($port -isnot [Array]) {
        throw 'PostgreSQL HostConfig.PortBindings 5432/tcp must be a native JSON array.'
    }
    if (@($port).Count -eq 1) {
        Assert-NativeJsonObject -Value $port[0] `
            -Label 'PostgreSQL HostConfig.PortBindings 5432/tcp entry'
        Assert-ExactProperties -Value $port[0] `
            -Label 'PostgreSQL HostConfig.PortBindings 5432/tcp entry' `
            -Expected @('HostIp', 'HostPort')
    }
    $configuredPortProperties = @(
        $inspect.HostConfig.PortBindings.PSObject.Properties |
            Where-Object { $null -ne $_.Value }
    )
    $runtimePortProperties = @($inspect.NetworkSettings.Ports.PSObject.Properties)
    $networkProperties = @($inspect.NetworkSettings.Networks.PSObject.Properties)
    $actualNetworkNames = @($networkProperties.Name)
    [Array]::Sort($actualNetworkNames, [StringComparer]::Ordinal)
    $expectedNetworkNames = @($productionNetworks.Keys)
    [Array]::Sort($expectedNetworkNames, [StringComparer]::Ordinal)
    if (
        $networkProperties.Count -ne 2 -or
        ($actualNetworkNames -join "`n") -cne ($expectedNetworkNames -join "`n")
    ) {
        throw 'Retained production PostgreSQL has an unexpected Docker network attachment.'
    }
    $networkBoundaries = Get-ProductionNetworkBoundaries -ExpectedState $ExpectedState
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Name -cne '/weatherpoly-postgres-1' -or
        $inspect.Image -cne $postgres16ImageId -or
        $inspect.Config.Image -cne $postgres16Image -or
        $healthcheckTest.Count -ne 2 -or
        $healthcheckTest[0] -cne 'CMD-SHELL' -or
        $healthcheckTest[1] -cne 'pg_isready -U stratus -d stratus' -or
        $inspect.RestartCount -ne 0 -or
        $inspect.State.OOMKilled -or
        $inspect.State.Paused -or
        $inspect.State.Restarting -or
        $inspect.State.Dead -or
        -not [string]::IsNullOrWhiteSpace([string]$inspect.State.Error) -or
        $inspect.HostConfig.RestartPolicy.Name -cne $ExpectedRestartPolicy -or
        [int]$inspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0 -or
        $inspect.HostConfig.NetworkMode -cne 'weatherpoly_default' -or
        $configuredPortProperties.Count -ne 1 -or
        $configuredPortProperties[0].Name -cne '5432/tcp' -or
        @($port).Count -ne 1 -or
        $port[0].HostIp -cne '127.0.0.1' -or
        $port[0].HostPort -cne '5432' -or
        @($inspect.Mounts).Count -ne 2 -or
        $hbaMounts.Count -ne 1 -or
        $hbaMounts[0].Type -cne 'bind' -or
        [bool]$hbaMounts[0].RW -or
        $hbaMounts[0].Mode -cne 'ro' -or
        $hbaMounts[0].Propagation -cne 'rprivate' -or
        ([string]$hbaMounts[0].Source).Replace('\', '/') -notin $hbaSources -or
        $dataMounts.Count -ne 1 -or
        $dataMounts[0].Type -cne 'volume' -or
        $dataMounts[0].Name -cne 'weatherpoly_stratus-postgres' -or
        -not [bool]$dataMounts[0].RW -or
        $dataMounts[0].Mode -cne 'rw' -or
        $dataMounts[0].Driver -cne 'local' -or
        $dataMounts[0].Source -cne $productionDataVolumeSource -or
        $dataMounts[0].Propagation -cne ''
    ) {
        throw 'Retained production PostgreSQL identity/topology is not exact.'
    }
    $endpointLines = [Collections.Generic.List[string]]::new()
    $networkEvidence = [ordered]@{}
    foreach ($networkName in $expectedNetworkNames) {
        $networkAttachment = $inspect.NetworkSettings.Networks.$networkName
        Assert-NativeJsonObject -Value $networkAttachment `
            -Label "PostgreSQL network attachment $networkName"
        Assert-ExactProperties -Value $networkAttachment `
            -Label "PostgreSQL network attachment $networkName" -Expected @(
                'IPAMConfig', 'Links', 'Aliases', 'MacAddress', 'DriverOpts',
                'GwPriority', 'NetworkID', 'EndpointID', 'Gateway', 'IPAddress',
                'IPPrefixLen', 'IPv6Gateway', 'GlobalIPv6Address',
                'GlobalIPv6PrefixLen', 'DNSNames'
            )
        $expectedNetwork = $productionNetworks[$networkName]
        $networkBoundary = $networkBoundaries.entries.$networkName
        if (
            $networkAttachment.Aliases -isnot [Array] -or
            $networkAttachment.DNSNames -isnot [Array]
        ) {
            throw "PostgreSQL network attachment arrays are not native: $networkName"
        }
        foreach ($value in @($networkAttachment.Aliases) + @($networkAttachment.DNSNames)) {
            Assert-NativeJsonString -Value $value `
                -Label "PostgreSQL network attachment name $networkName"
        }
        $aliases = [string[]]@($networkAttachment.Aliases)
        $expectedAliases = [string[]]@('postgres', 'weatherpoly-postgres-1')
        $dnsNames = [string[]]@($networkAttachment.DNSNames)
        $expectedDnsNames = [string[]]@(
            $productionPostgresId.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1'
        )
        [Array]::Sort($aliases, [StringComparer]::Ordinal)
        [Array]::Sort($expectedAliases, [StringComparer]::Ordinal)
        [Array]::Sort($dnsNames, [StringComparer]::Ordinal)
        [Array]::Sort($expectedDnsNames, [StringComparer]::Ordinal)
        if (
            $networkAttachment.NetworkID -cne [string]$expectedNetwork.id -or
            $null -ne $networkAttachment.IPAMConfig -or
            $null -ne $networkAttachment.Links -or
            $null -ne $networkAttachment.DriverOpts -or
            [int]$networkAttachment.GwPriority -ne 0 -or
            ($aliases -join "`n") -cne ($expectedAliases -join "`n") -or
            ($dnsNames -join "`n") -cne ($expectedDnsNames -join "`n") -or
            -not [string]::IsNullOrEmpty([string]$networkAttachment.IPv6Gateway) -or
            -not [string]::IsNullOrEmpty([string]$networkAttachment.GlobalIPv6Address) -or
            [int]$networkAttachment.GlobalIPv6PrefixLen -ne 0
        ) {
            throw "Retained PostgreSQL network attachment is not exact: $networkName"
        }
        if ($ExpectedState -eq 'Stopped') {
            if (
                -not [string]::IsNullOrEmpty([string]$networkAttachment.EndpointID) -or
                -not [string]::IsNullOrEmpty([string]$networkAttachment.Gateway) -or
                -not [string]::IsNullOrEmpty([string]$networkAttachment.IPAddress) -or
                [int]$networkAttachment.IPPrefixLen -ne 0 -or
                -not [string]::IsNullOrEmpty([string]$networkAttachment.MacAddress)
            ) {
                throw "Stopped PostgreSQL retains an active network endpoint: $networkName"
            }
        }
        else {
            if (
                [string]$networkAttachment.EndpointID -notmatch '^[0-9a-f]{64}$' -or
                $networkAttachment.Gateway -cne [string]$expectedNetwork.gateway -or
                [string]$networkAttachment.IPAddress -notmatch [string]$expectedNetwork.ipv4_pattern -or
                [int]$networkAttachment.IPPrefixLen -ne 16 -or
                [string]$networkAttachment.MacAddress -notmatch '^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$' -or
                $networkBoundary.postgres_endpoint.EndpointID -cne
                    [string]$networkAttachment.EndpointID -or
                $networkBoundary.postgres_endpoint.MacAddress -cne
                    [string]$networkAttachment.MacAddress -or
                $networkBoundary.postgres_endpoint.IPv4Address -cne
                    "$($networkAttachment.IPAddress)/$($networkAttachment.IPPrefixLen)" -or
                $networkBoundary.postgres_endpoint.Name -cne 'weatherpoly-postgres-1' -or
                -not [string]::IsNullOrEmpty(
                    [string]$networkBoundary.postgres_endpoint.IPv6Address
                )
            ) {
                throw "Running PostgreSQL network endpoint is not exact: $networkName"
            }
        }
        $endpointLine = @(
            $networkName, [string]$networkAttachment.NetworkID,
            [string]$networkAttachment.EndpointID, [string]$networkAttachment.Gateway,
            [string]$networkAttachment.IPAddress, [string]$networkAttachment.IPPrefixLen,
            [string]$networkAttachment.IPv6Gateway,
            [string]$networkAttachment.GlobalIPv6Address,
            [string]$networkAttachment.GlobalIPv6PrefixLen,
            [string]$networkAttachment.MacAddress,
            ($aliases -join ','), ($dnsNames -join ',')
        ) -join '|'
        $endpointLines.Add($endpointLine)
        $networkEvidence[$networkName] = [pscustomobject]@{
            id = [string]$networkAttachment.NetworkID
            endpoint_id = [string]$networkAttachment.EndpointID
            gateway = [string]$networkAttachment.Gateway
            ip_address = [string]$networkAttachment.IPAddress
            ip_prefix_length = [int]$networkAttachment.IPPrefixLen
            mac_address = [string]$networkAttachment.MacAddress
            aliases = $aliases
            dns_names = $dnsNames
        }
    }
    if ($ExpectedState -eq 'Stopped') {
        if (
            $runtimePortProperties.Count -ne 0 -or
            $inspect.State.Status -cne 'exited' -or
            $inspect.State.Running -or
            $inspect.State.Pid -ne 0 -or
            $inspect.State.ExitCode -ne 0
        ) {
            throw 'PreStart requires the retained production PostgreSQL to be cleanly stopped.'
        }
    }
    else {
        Assert-NativeJsonObject -Value $inspect.State.Health -Label 'PostgreSQL State.Health'
        Assert-NativeJsonString -Value $inspect.State.Health.Status `
            -Label 'PostgreSQL State.Health.Status'
        $runtimePort = $inspect.NetworkSettings.Ports.'5432/tcp'
        if ($runtimePort -isnot [Array]) {
            throw 'Running PostgreSQL 5432/tcp runtime port must be a native JSON array.'
        }
        if (@($runtimePort).Count -eq 1) {
            Assert-NativeJsonObject -Value $runtimePort[0] `
                -Label 'running PostgreSQL 5432/tcp runtime port'
            Assert-ExactProperties -Value $runtimePort[0] `
                -Label 'running PostgreSQL 5432/tcp runtime port' `
                -Expected @('HostIp', 'HostPort')
        }
        if (
            $runtimePortProperties.Count -ne 1 -or
            $runtimePortProperties[0].Name -cne '5432/tcp' -or
            @($runtimePort).Count -ne 1 -or
            $runtimePort[0].HostIp -cne '127.0.0.1' -or
            $runtimePort[0].HostPort -cne '5432' -or
            $inspect.State.Status -cne 'running' -or
            -not $inspect.State.Running -or
            $inspect.State.Pid -le 0 -or
            $inspect.State.FinishedAt -cne '0001-01-01T00:00:00Z' -or
            $inspect.State.ExitCode -ne 0 -or
            $inspect.State.Health.Status -cne 'healthy'
        ) {
            throw 'Live successor proof requires exact healthy PostgreSQL and loopback port state.'
        }
    }
    $mountContract = @(
        [string]$hbaMounts[0].Type, [string]$hbaMounts[0].Source,
        [string]$hbaMounts[0].Destination, [string]$hbaMounts[0].Mode,
        [string]$hbaMounts[0].RW, [string]$hbaMounts[0].Propagation,
        [string]$dataMounts[0].Type, [string]$dataMounts[0].Name,
        [string]$dataMounts[0].Driver, [string]$dataMounts[0].Source,
        [string]$dataMounts[0].Destination, [string]$dataMounts[0].Mode,
        [string]$dataMounts[0].RW, [string]$dataMounts[0].Propagation
    ) -join "`0"
    $portsContract = if ($ExpectedState -eq 'Stopped') {
        'stopped-empty'
    }
    else {
        '5432/tcp|127.0.0.1|5432'
    }
    return [pscustomobject]@{
        container_id = [string]$inspect.Id
        image_id = [string]$inspect.Image
        config_image = [string]$inspect.Config.Image
        restart_count = [int]$inspect.RestartCount
        started_at = [string]$inspect.State.StartedAt
        finished_at = [string]$inspect.State.FinishedAt
        state = [string]$inspect.State.Status
        oom_killed = [bool]$inspect.State.OOMKilled
        exit_code = [int]$inspect.State.ExitCode
        restart_policy = [string]$inspect.HostConfig.RestartPolicy.Name
        health_status = if ($ExpectedState -eq 'Running') {
            [string]$inspect.State.Health.Status
        }
        else {
            'not-running'
        }
        network_mode = [string]$inspect.HostConfig.NetworkMode
        mount_contract_sha256 = Get-Sha256Text -Value ($mountContract + "`n")
        ports_contract_sha256 = Get-Sha256Text -Value ($portsContract + "`n")
        networks = [pscustomobject]$networkEvidence
        network_contract_sha256 = [string]$networkBoundaries.contract_sha256
        active_endpoint_sha256 = Get-Sha256Text -Value (
            ($endpointLines.ToArray() -join "`n") + "`n"
        )
    }
}

function Assert-ExclusiveRuntimeTopology {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Stopped', 'Running')]
        [string] $PostgresState
    )
    $services = @(
        Invoke-Docker -Arguments (
            $composeArguments + @('ps', '--status', 'running', '--services')
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    $runningIds = @(
        Invoke-Docker -Arguments @('ps', '-q', '--no-trunc') |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($PostgresState -eq 'Stopped') {
        if ($services.Count -ne 0 -or $runningIds.Count -ne 0) {
            throw 'PreStart requires zero running containers after proof-container cleanup.'
        }
    }
    else {
        if (
            $services.Count -ne 1 -or
            $services[0] -cne 'postgres' -or
            $runningIds.Count -ne 1 -or
            $runningIds[0] -cne $productionPostgresId
        ) {
            throw 'Live successor proof permits only the exact production PostgreSQL container.'
        }
    }
    return [pscustomobject]@{
        running_count = $runningIds.Count
        postgres_only = ($PostgresState -eq 'Running')
        application_writers_running = $false
    }
}

function Wait-PostgresContainerHealthy {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
    do {
        $health = Get-OneJsonObject -Label 'retained PostgreSQL health state' -Lines (
            Invoke-Docker -Arguments @(
                'inspect', $productionPostgresId, '--format', '{{json .State.Health}}'
            )
        )
        Assert-NativeJsonString -Value $health.Status -Label 'PostgreSQL health status'
        if ($health.Status -ceq 'healthy') {
            return [pscustomobject]@{
                status = 'healthy'
                checked_at = [DateTimeOffset]::UtcNow.ToString('o')
            }
        }
        if ($health.Status -cnotin @('starting', 'unhealthy')) {
            throw 'Retained PostgreSQL returned an unexpected health status.'
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'Retained PostgreSQL did not become healthy within the bounded start window.'
}

function Test-TransitionOwnedPostgresRunning {
    param([Parameter(Mandatory = $true)][DateTimeOffset] $NotBefore)
    $inspect = Get-OneJsonObject -Label 'rollback ownership inspect' -Lines (
        Invoke-Docker -Arguments @(
            'inspect', $productionPostgresId, '--format', '{{json .}}'
        )
    )
    foreach ($name in @('Config', 'State', 'HostConfig')) {
        Assert-NativeJsonObject -Value $inspect.$name -Label "rollback ownership $name"
    }
    if (-not [bool]$inspect.State.Running) {
        return $false
    }
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Image -cne $postgres16ImageId -or
        $inspect.Config.Image -cne $postgres16Image -or
        [int]$inspect.RestartCount -ne 0 -or
        $inspect.HostConfig.RestartPolicy.Name -cne 'no' -or
        [int]$inspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0
    ) {
        throw 'Rollback ownership inspect found a running but non-exact PostgreSQL identity.'
    }
    $startedAt = ConvertTo-RequiredUtcTime -Value ([string]$inspect.State.StartedAt) `
        -Label 'rollback ownership StartedAt'
    if ($startedAt -le $NotBefore.AddSeconds(-$clockSkewSeconds)) {
        throw 'Rollback ownership inspect found a running PostgreSQL outside this transition.'
    }
    return $true
}

function Get-HeldHbaReadBoundary {
    if (
        $null -eq $script:hbaReadLock -or
        [string]::IsNullOrWhiteSpace([string]$script:hbaReadLockPath) -or
        $script:hbaReadLock.SafeFileHandle.IsClosed -or
        $script:hbaReadLock.SafeFileHandle.IsInvalid -or
        -not $script:hbaReadLock.CanRead
    ) {
        throw 'The transition no longer holds the exact host HBA read lock.'
    }
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $script:hbaReadLock.Position = 0
        $sha256 = [BitConverter]::ToString(
            $algorithm.ComputeHash($script:hbaReadLock)
        ).Replace('-', '').ToLowerInvariant()
        $script:hbaReadLock.Position = 0
    }
    finally {
        $algorithm.Dispose()
    }
    if ($sha256 -cne $fixedHbaSha256) {
        throw 'The held host HBA bytes are not the exact reviewed file.'
    }
    return [pscustomobject]@{
        held = $true
        path = [string]$script:hbaReadLockPath
        sha256 = $sha256
        bytes = [int64]$script:hbaReadLock.Length
        access = 'Read'
        share = 'Read'
        write_share_permitted = $false
        delete_share_permitted = $false
    }
}

function Open-HeldHbaReadBoundary {
    $path = [IO.Path]::GetFullPath(
        (Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf')
    )
    [void](Assert-RegularFile -LiteralPath $path)
    if ($null -ne $script:hbaReadLock) {
        throw 'The host HBA read lock was already initialized.'
    }
    $stream = [IO.FileStream]::new(
        $path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        4096,
        [IO.FileOptions]::SequentialScan
    )
    $script:hbaReadLock = $stream
    $script:hbaReadLockPath = $path
    try {
        return Get-HeldHbaReadBoundary
    }
    catch {
        $stream.Dispose()
        $script:hbaReadLock = $null
        $script:hbaReadLockPath = $null
        throw
    }
}

function Get-MountedHbaSha256 {
    $lines = @(
        Invoke-Docker -Arguments @(
            'exec', $productionPostgresId, 'sha256sum',
            '/etc/postgresql/stratus-pg_hba.conf'
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    if ($lines.Count -ne 1 -or $lines[0] -notmatch '^([0-9a-f]{64})\s+') {
        throw 'Mounted HBA byte proof did not return one SHA-256.'
    }
    return $Matches[1]
}

function Get-FreshHbaState {
    param([Parameter(Mandatory = $true)][string] $Label)
    $command = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" <<'STRATUS_SQL'
SELECT 'META|' || concat_ws(
  '|', pg_conf_load_time()::text, pg_postmaster_start_time()::text,
  pg_backend_pid()::text,
  (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid()),
  current_setting('hba_file')
);
SELECT 'PATH|' || current_setting('hba_file');
SELECT 'RULE|' || concat_ws(
  '|', file_name, line_number::text, type,
  array_to_string(database, ','), array_to_string(user_name, ','),
  coalesce(address, ''), coalesce(netmask, ''), auth_method,
  coalesce(array_to_string(options, ','), ''), coalesce(error, '')
)
FROM pg_hba_file_rules
ORDER BY rule_number;
STRATUS_SQL
'@
    $lines = @(
        Invoke-Docker -Arguments @(
            'exec', $productionPostgresId, 'sh', '-ec', $command
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    $metadata = @($lines | Where-Object { $_ -like 'META|*' })
    $inventory = @($lines | Where-Object { $_ -notlike 'META|*' })
    $ruleLines = @($inventory | Where-Object { $_ -like 'RULE|*' })
    $errorRules = @($ruleLines | Where-Object { $_ -notmatch '\|$' })
    $inventoryHash = Get-Sha256Text -Value ($inventory -join "`n")
    if (
        $metadata.Count -ne 1 -or
        $inventory.Count -ne 16 -or
        $inventory[0] -cne 'PATH|/etc/postgresql/stratus-pg_hba.conf' -or
        $ruleLines.Count -ne 15 -or
        $errorRules.Count -ne 0 -or
        $inventoryHash -cne $fixedHbaInventorySha256
    ) {
        throw "$Label did not expose the exact fresh HBA inventory."
    }
    $parts = $metadata[0].Split('|')
    if ($parts.Count -ne 6 -or
        $parts[5] -cne '/etc/postgresql/stratus-pg_hba.conf' -or
        [int]$parts[3] -le 0) {
        throw "$Label returned malformed fresh HBA metadata."
    }
    $confLoadTime = ConvertTo-RequiredUtcTime -Value $parts[1] `
        -Label "$Label conf_load_time"
    $postmasterStartedAt = ConvertTo-RequiredUtcTime -Value $parts[2] `
        -Label "$Label postmaster_start_time"
    $backendStartedAt = ConvertTo-RequiredUtcTime -Value $parts[4] `
        -Label "$Label backend_start"
    if ($confLoadTime -gt $backendStartedAt) {
        throw "$Label returned a configuration load time after backend start."
    }
    return [pscustomobject]@{
        conf_load_time = $confLoadTime.ToString('o')
        postmaster_started_at = $postmasterStartedAt.ToString('o')
        backend_pid = [int]$parts[3]
        backend_started_at = $backendStartedAt.ToString('o')
        hba_file = $parts[5]
        inventory_sha256 = $inventoryHash
        inventory_line_count = $inventory.Count
        inventory_rule_count = $ruleLines.Count
        inventory_errors = $errorRules.Count
    }
}

function Assert-HbaFirstBoundary {
    $hbaPath = Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    [void](Assert-RegularFile -LiteralPath $hbaPath)
    $hostBeforeBoundary = Get-HeldHbaReadBoundary
    $hostBefore = [string]$hostBeforeBoundary.sha256
    $mountedBefore = Get-MountedHbaSha256
    if ($hostBefore -cne $fixedHbaSha256 -or $mountedBefore -cne $fixedHbaSha256) {
        throw 'HBA bytes drifted before the first successor database operation.'
    }
    # pg_conf_load_time() is backend-local. One psql process is retained, but
    # each sleep and sample is a separate frontend protocol command so the
    # backend can process SIGHUP between statements.
    $commandPrefix = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" <<'STRATUS_SQL'
SELECT 'BASE|' || concat_ws(
  '|', pg_conf_load_time()::text, clock_timestamp()::text,
  pg_postmaster_start_time()::text, pg_backend_pid()::text,
  (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid()),
  current_setting('hba_file')
);
SELECT 'RELOAD|' || pg_reload_conf()::text;
'@
    $pollCommands = [Text.StringBuilder]::new()
    foreach ($pollNumber in 1..40) {
        [void]$pollCommands.AppendLine('SELECT pg_sleep(0.25);')
        [void]$pollCommands.AppendLine((@"
SELECT 'POLL|$pollNumber|' || concat_ws(
  '|', pg_conf_load_time()::text, clock_timestamp()::text,
  pg_postmaster_start_time()::text, pg_backend_pid()::text,
  (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid()),
  current_setting('hba_file')
);
"@).TrimStart())
    }
    $commandSuffix = @'
SELECT 'PATH|' || current_setting('hba_file');
SELECT 'RULE|' || concat_ws(
  '|', file_name, line_number::text, type,
  array_to_string(database, ','), array_to_string(user_name, ','),
  coalesce(address, ''), coalesce(netmask, ''), auth_method,
  coalesce(array_to_string(options, ','), ''), coalesce(error, '')
)
FROM pg_hba_file_rules
ORDER BY rule_number;
STRATUS_SQL
'@
    $reloadAndInventoryCommand = $commandPrefix + "`n" +
        $pollCommands.ToString() + $commandSuffix
    $proofLines = @(
        Invoke-Docker -Arguments @(
            'exec', $productionPostgresId, 'sh', '-ec', $reloadAndInventoryCommand
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    $baseLines = @($proofLines | Where-Object { $_ -like 'BASE|*' })
    $reloadLines = @($proofLines | Where-Object { $_ -like 'RELOAD|*' })
    $pollLines = @($proofLines | Where-Object { $_ -like 'POLL|*' })
    $inventory = @($proofLines | Where-Object {
        $_ -notlike 'BASE|*' -and $_ -notlike 'RELOAD|*' -and $_ -notlike 'POLL|*'
    })
    $inventoryHash = Get-Sha256Text -Value ($inventory -join "`n")
    $ruleLines = @($inventory | Where-Object { $_ -like 'RULE|*' })
    $errorRules = @($ruleLines | Where-Object { $_ -notmatch '\|$' })
    if (
        $baseLines.Count -ne 1 -or
        $reloadLines.Count -ne 1 -or
        $reloadLines[0] -cne 'RELOAD|true' -or
        $pollLines.Count -ne 40 -or
        $inventory.Count -ne 16 -or
        $inventory[0] -cne 'PATH|/etc/postgresql/stratus-pg_hba.conf' -or
        $ruleLines.Count -ne 15 -or
        $errorRules.Count -ne 0 -or
        $inventoryHash -cne $fixedHbaInventorySha256
    ) {
        throw 'Loaded PostgreSQL HBA inventory is not the exact reviewed 16-line boundary.'
    }
    $baseParts = $baseLines[0].Split('|')
    if ($baseParts.Count -ne 7 -or
        $baseParts[6] -cne '/etc/postgresql/stratus-pg_hba.conf') {
        throw 'PostgreSQL HBA reload metadata is malformed.'
    }
    $confLoadBefore = ConvertTo-RequiredUtcTime -Value $baseParts[1] `
        -Label 'HBA conf_load_time before reload'
    $reloadRequestedAt = ConvertTo-RequiredUtcTime -Value $baseParts[2] `
        -Label 'HBA reload requested_at'
    $postmasterBefore = ConvertTo-RequiredUtcTime -Value $baseParts[3] `
        -Label 'HBA postmaster time before reload'
    $backendPid = [int]$baseParts[4]
    $backendStartedAt = ConvertTo-RequiredUtcTime -Value $baseParts[5] `
        -Label 'HBA proof backend start'
    $acknowledgedPoll = 0
    $confLoadAfter = [DateTimeOffset]::MinValue
    $reloadAcknowledgedAt = [DateTimeOffset]::MinValue
    foreach ($pollIndex in 0..39) {
        $parts = $pollLines[$pollIndex].Split('|')
        if ($parts.Count -ne 8 -or [int]$parts[1] -ne ($pollIndex + 1) -or
            $parts[7] -cne $baseParts[6]) {
            throw 'PostgreSQL HBA poll metadata is malformed.'
        }
        $sampleLoadTime = ConvertTo-RequiredUtcTime -Value $parts[2] `
            -Label 'HBA poll conf_load_time'
        $sampledAt = ConvertTo-RequiredUtcTime -Value $parts[3] `
            -Label 'HBA poll sampled_at'
        $samplePostmaster = ConvertTo-RequiredUtcTime -Value $parts[4] `
            -Label 'HBA poll postmaster time'
        $sampleBackendStart = ConvertTo-RequiredUtcTime -Value $parts[6] `
            -Label 'HBA poll backend start'
        if (
            $samplePostmaster -ne $postmasterBefore -or
            [int]$parts[5] -ne $backendPid -or
            $sampleBackendStart -ne $backendStartedAt -or
            $sampledAt -lt $reloadRequestedAt
        ) {
            throw 'PostgreSQL HBA poll escaped the original backend boundary.'
        }
        if ($acknowledgedPoll -eq 0 -and
            $sampleLoadTime -gt $confLoadBefore -and
            $sampleLoadTime -ge $reloadRequestedAt) {
            $acknowledgedPoll = $pollIndex + 1
            $confLoadAfter = $sampleLoadTime
            $reloadAcknowledgedAt = $sampledAt
        }
        elseif ($acknowledgedPoll -gt 0 -and $sampleLoadTime -ne $confLoadAfter) {
            throw 'PostgreSQL processed an additional configuration reload during HBA proof.'
        }
    }
    if (
        $acknowledgedPoll -eq 0 -or
        $reloadAcknowledgedAt -lt $confLoadAfter -or
        $backendStartedAt -gt $reloadRequestedAt -or
        $backendPid -le 0
    ) {
        throw 'PostgreSQL did not acknowledge the exact HBA reload in one bounded backend.'
    }
    $freshLoadedState = Get-FreshHbaState -Label 'post-reload fresh HBA state'
    $freshConfLoadTime = ConvertTo-RequiredUtcTime `
        -Value $freshLoadedState.conf_load_time -Label 'fresh postmaster HBA load time'
    $freshBackendStartedAt = ConvertTo-RequiredUtcTime `
        -Value $freshLoadedState.backend_started_at -Label 'fresh HBA backend start'
    if (
        $freshConfLoadTime -lt $reloadRequestedAt -or
        $freshConfLoadTime -gt $reloadAcknowledgedAt -or
        $freshBackendStartedAt -lt $reloadAcknowledgedAt -or
        $freshLoadedState.postmaster_started_at -cne $postmasterBefore.ToString('o')
    ) {
        throw 'Fresh PostgreSQL session did not inherit the acknowledged HBA generation.'
    }
    $hostAfterBoundary = Get-HeldHbaReadBoundary
    $hostAfter = [string]$hostAfterBoundary.sha256
    $mountedAfter = Get-MountedHbaSha256
    if ($hostAfter -cne $fixedHbaSha256 -or $mountedAfter -cne $fixedHbaSha256) {
        throw 'HBA bytes changed during the successor loaded-rule proof.'
    }
    return [pscustomobject]@{
        host_sha256 = $hostAfter
        host_read_lock_held = $true
        host_read_lock_path = [string]$hostAfterBoundary.path
        host_read_lock_access = [string]$hostAfterBoundary.access
        host_read_lock_share = [string]$hostAfterBoundary.share
        host_write_share_permitted = [bool]$hostAfterBoundary.write_share_permitted
        host_delete_share_permitted = [bool]$hostAfterBoundary.delete_share_permitted
        mounted_sha256 = $mountedAfter
        loaded_inventory_sha256 = $inventoryHash
        loaded_line_count = $inventory.Count
        loaded_rule_count = $ruleLines.Count
        loaded_rule_errors = $errorRules.Count
        reload_accepted = $true
        reload_acknowledged = $true
        conf_load_time_before = $confLoadBefore.ToString('o')
        reload_requested_at = $reloadRequestedAt.ToString('o')
        conf_load_time_after = $confLoadAfter.ToString('o')
        reload_acknowledged_at = $reloadAcknowledgedAt.ToString('o')
        reload_poll_count = $acknowledgedPoll
        postmaster_started_at = $postmasterBefore.ToString('o')
        proof_backend_pid = $backendPid
        proof_backend_started_at = $backendStartedAt.ToString('o')
        hba_file = $baseParts[6]
        fresh_loaded_state = $freshLoadedState
    }
}

function Get-ByteArraySha256 {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]] $Bytes
    )
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($Bytes)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-DockerEventJournalSnapshot {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [switch] $AllowStopped
    )
    $Witness.process.Refresh()
    if (-not [string]::IsNullOrEmpty([string]$Witness.collector.PumpError)) {
        throw 'The Docker event witness output pump failed.'
    }
    if (-not $AllowStopped -and (
        $Witness.process.HasExited -or $Witness.collector.StdoutEof
    )) {
        throw 'The non-reconnecting Docker event witness exited before final sealing.'
    }
    if (-not (Test-Path -LiteralPath $Witness.stdout_path -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Witness.stderr_path -PathType Leaf)) {
        throw 'The Docker event witness output files disappeared.'
    }
    $stdoutItem = Get-Item -LiteralPath $Witness.stdout_path -Force
    $stderrItem = Get-Item -LiteralPath $Witness.stderr_path -Force
    if (
        ($stdoutItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ($stderrItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $stderrItem.Length -ne 0 -or
        $stdoutItem.Length -gt $dockerEventJournalMaxBytes
    ) {
        throw 'The Docker event witness output is linked, errored, or unbounded.'
    }
    $stream = [IO.File]::Open(
        $Witness.stdout_path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    )
    try {
        $bytes = [byte[]]::new([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) {
                throw 'The Docker event journal could not be read atomically.'
            }
            $offset += $read
        }
    }
    finally {
        $stream.Dispose()
    }
    if ($bytes.Length -lt [int64]$Witness.prefix_length) {
        throw 'The Docker event journal was truncated.'
    }
    if ([int64]$Witness.prefix_length -gt 0) {
        $prefix = [byte[]]::new([int]$Witness.prefix_length)
        [Array]::Copy($bytes, 0, $prefix, 0, $prefix.Length)
        if ((Get-ByteArraySha256 -Bytes $prefix) -cne $Witness.prefix_sha256) {
            throw 'The accepted Docker event journal prefix was rewritten.'
        }
    }
    $completeLength = 0
    for ($index = $bytes.Length - 1; $index -ge 0; $index -= 1) {
        if ($bytes[$index] -eq 10) {
            $completeLength = $index + 1
            break
        }
    }
    $completeBytes = [byte[]]::new($completeLength)
    if ($completeLength -gt 0) {
        [Array]::Copy($bytes, 0, $completeBytes, 0, $completeLength)
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    try {
        $text = $utf8.GetString($completeBytes)
    }
    catch {
        throw 'The Docker event journal is not canonical UTF-8.'
    }
    $events = [Collections.Generic.List[object]]::new()
    foreach ($line in @($text -split "`n")) {
        $candidate = $line.TrimEnd("`r")
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        $events.Add((ConvertFrom-StrictJsonObjectText -Json $candidate `
            -Label 'continuous Docker event'))
    }
    if ($events.Count -gt $dockerEventJournalMaxEvents) {
        throw 'The continuous Docker event journal exceeded its bounded event count.'
    }
    return [pscustomobject]@{
        byte_length = [int64]$bytes.Length
        complete_byte_length = [int64]$completeLength
        complete_sha256 = Get-ByteArraySha256 -Bytes $completeBytes
        event_count = [int]$events.Count
        events = $events.ToArray()
        has_partial_line = ($bytes.Length -ne $completeLength)
    }
}

function Initialize-DockerEventCollectorType {
    $sourceTemplate = @'
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

public sealed class __STRATUS_TYPE__ : IDisposable
{
    public const string ContractIdentity = "__STRATUS_CONTRACT__";
    public sealed class BoundedResult
    {
        public int ExitCode;
        public bool TimedOut;
        public bool OutputExceeded;
        public string Stdout = String.Empty;
        public string Stderr = String.Empty;
        public string PumpError = String.Empty;
    }

    public sealed class HeartbeatPublisher : IDisposable
    {
        public sealed class HeartbeatEvidence
        {
            public long Sequence;
            public string WrittenAt;
            public string Sha256;
        }

        private Thread worker;
        private ManualResetEvent stopSignal = new ManualResetEvent(false);
        private volatile Exception error;
        private object evidenceLock = new object();
        private string path;
        private string sessionId;
        private int ownerPid;
        private string ownerStartTimeUtc;
        private string leasePath;
        private string leaseSha256;
        private int intervalMilliseconds;
        private long sequence;
        private string lastWrittenAt = String.Empty;
        private string lastSha256 = String.Empty;
        private bool initialized;

        public long Sequence { get { lock (evidenceLock) { return sequence; } } }
        public string LastWrittenAt { get { lock (evidenceLock) { return lastWrittenAt; } } }
        public string LastSha256 { get { lock (evidenceLock) { return lastSha256; } } }
        public string Error { get { return error == null ? String.Empty : error.Message; } }

        public HeartbeatEvidence Snapshot()
        {
            lock (evidenceLock)
            {
                return new HeartbeatEvidence {
                    Sequence = sequence,
                    WrittenAt = lastWrittenAt,
                    Sha256 = lastSha256
                };
            }
        }

        private static string JsonQuote(string value)
        {
            if (value == null) throw new ArgumentNullException("value");
            StringBuilder result = new StringBuilder();
            result.Append('"');
            foreach (char character in value)
            {
                switch (character)
                {
                    case '"': result.Append("\\\""); break;
                    case '\\': result.Append("\\\\"); break;
                    case '\b': result.Append("\\b"); break;
                    case '\f': result.Append("\\f"); break;
                    case '\n': result.Append("\\n"); break;
                    case '\r': result.Append("\\r"); break;
                    case '\t': result.Append("\\t"); break;
                    default:
                        if (character < 0x20)
                            result.Append("\\u").Append(
                                ((int)character).ToString("x4", CultureInfo.InvariantCulture));
                        else
                            result.Append(character);
                        break;
                }
            }
            result.Append('"');
            return result.ToString();
        }

        private static string Sha256(byte[] bytes)
        {
            using (SHA256 algorithm = SHA256.Create())
            {
                return BitConverter.ToString(algorithm.ComputeHash(bytes))
                    .Replace("-", String.Empty).ToLowerInvariant();
            }
        }

        private void Publish()
        {
            long next;
            lock (evidenceLock) { next = sequence + 1; }
            string writtenAt = DateTimeOffset.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            string json = "{" +
                "\"format\":\"stratus-successor-transition-watchdog-heartbeat-v1\"," +
                "\"session_id\":" + JsonQuote(sessionId) + "," +
                "\"owner_pid\":" + ownerPid.ToString(CultureInfo.InvariantCulture) + "," +
                "\"owner_start_time_utc\":" + JsonQuote(ownerStartTimeUtc) + "," +
                "\"lease_path\":" + JsonQuote(leasePath) + "," +
                "\"lease_sha256\":" + JsonQuote(leaseSha256) + "," +
                "\"sequence\":" + next.ToString(CultureInfo.InvariantCulture) + "," +
                "\"written_at\":" + JsonQuote(writtenAt) + "}";
            byte[] bytes = new UTF8Encoding(false, true).GetBytes(json);
            string temporary = path + ".tmp-" + sessionId + "-" +
                next.ToString(CultureInfo.InvariantCulture);
            bool moved = false;
            try
            {
                using (FileStream stream = new FileStream(
                    temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                    4096, FileOptions.WriteThrough))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                if (!initialized)
                {
                    if (File.Exists(path))
                        throw new IOException("watchdog heartbeat path was pre-created");
                    File.Move(temporary, path);
                    moved = true;
                    initialized = true;
                }
                else
                {
                    IOException replaceError = null;
                    for (int attempt = 0; attempt < 200; attempt++)
                    {
                        try
                        {
                            File.Replace(temporary, path, null);
                            moved = true;
                            replaceError = null;
                            break;
                        }
                        catch (IOException candidate)
                        {
                            replaceError = candidate;
                            Thread.Sleep(10);
                        }
                    }
                    if (!moved)
                        throw new IOException(
                            "watchdog heartbeat atomic replace failed", replaceError);
                }
                string digest = Sha256(bytes);
                lock (evidenceLock)
                {
                    lastWrittenAt = writtenAt;
                    lastSha256 = digest;
                    sequence = next;
                }
            }
            finally
            {
                if (!moved && File.Exists(temporary))
                {
                    try { File.Delete(temporary); } catch { }
                }
            }
        }

        private void Run()
        {
            try
            {
                while (!stopSignal.WaitOne(intervalMilliseconds)) Publish();
            }
            catch (Exception candidate) { error = candidate; }
        }

        public void Start(
            string heartbeatPath, string transitionSessionId, int transitionOwnerPid,
            string transitionOwnerStartTimeUtc, string acceptedLeasePath,
            string acceptedLeaseSha256, int publishIntervalMilliseconds)
        {
            if (worker != null) throw new InvalidOperationException("heartbeat publisher already started");
            if (publishIntervalMilliseconds < 100 || publishIntervalMilliseconds > 5000)
                throw new ArgumentOutOfRangeException("publishIntervalMilliseconds");
            path = heartbeatPath;
            sessionId = transitionSessionId;
            ownerPid = transitionOwnerPid;
            ownerStartTimeUtc = transitionOwnerStartTimeUtc;
            leasePath = acceptedLeasePath;
            leaseSha256 = acceptedLeaseSha256;
            intervalMilliseconds = publishIntervalMilliseconds;
            Publish();
            worker = new Thread(Run);
            worker.IsBackground = true;
            worker.Name = "STRATUS successor watchdog heartbeat";
            worker.Start();
        }

        public void ThrowIfError()
        {
            if (error != null)
                throw new InvalidOperationException("watchdog heartbeat publisher failed", error);
        }

        public void StopAndWait(int milliseconds)
        {
            stopSignal.Set();
            if (worker != null && !worker.Join(milliseconds))
                throw new TimeoutException("watchdog heartbeat publisher did not stop");
            ThrowIfError();
        }

        public void Dispose()
        {
            try { StopAndWait(5000); } catch { }
            stopSignal.Dispose();
        }
    }

    private sealed class BoundedPump
    {
        private Stream input;
        private int maximumBytes;
        private MemoryStream captured = new MemoryStream();
        public bool Exceeded;
        public Exception Error;

        public BoundedPump(Stream value, int maximum)
        {
            input = value;
            maximumBytes = maximum;
        }

        public void Run()
        {
            try
            {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = input.Read(buffer, 0, buffer.Length)) > 0)
                {
                    int remaining = maximumBytes - (int)captured.Length;
                    if (remaining > 0)
                        captured.Write(buffer, 0, Math.Min(remaining, count));
                    if (count > remaining) Exceeded = true;
                }
            }
            catch (Exception error) { Error = error; }
        }

        public string Text()
        {
            return new UTF8Encoding(false, true).GetString(captured.ToArray());
        }
    }

    public static BoundedResult RunBounded(
        string executable, string arguments, int timeoutMilliseconds,
        int stdoutMaximumBytes, int stderrMaximumBytes)
    {
        BoundedResult result = new BoundedResult();
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = executable;
        info.Arguments = arguments;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        using (Process child = new Process())
        {
            child.StartInfo = info;
            if (!child.Start()) throw new InvalidOperationException("bounded process did not start");
            BoundedPump stdout = new BoundedPump(
                child.StandardOutput.BaseStream, stdoutMaximumBytes);
            BoundedPump stderr = new BoundedPump(
                child.StandardError.BaseStream, stderrMaximumBytes);
            Thread stdoutThread = new Thread(stdout.Run);
            Thread stderrThread = new Thread(stderr.Run);
            stdoutThread.IsBackground = true;
            stderrThread.IsBackground = true;
            stdoutThread.Start();
            stderrThread.Start();
            if (!child.WaitForExit(timeoutMilliseconds))
            {
                result.TimedOut = true;
                try { child.Kill(); } catch (Exception error) { result.PumpError = error.Message; }
                try { child.WaitForExit(1000); } catch { }
            }
            if (!stdoutThread.Join(1000) || !stderrThread.Join(1000))
            {
                result.TimedOut = true;
                if (String.IsNullOrEmpty(result.PumpError))
                    result.PumpError = "bounded output pump did not stop";
            }
            result.OutputExceeded = stdout.Exceeded || stderr.Exceeded;
            if (stdout.Error != null) result.PumpError = "stdout: " + stdout.Error.Message;
            if (stderr.Error != null) result.PumpError = "stderr: " + stderr.Error.Message;
            result.Stdout = stdout.Text();
            result.Stderr = stderr.Text();
            result.ExitCode = child.HasExited ? child.ExitCode : -1;
        }
        return result;
    }

    private Process process;
    private FileStream stdoutFile;
    private FileStream stderrFile;
    private Thread stdoutThread;
    private Thread stderrThread;
    private volatile bool stdoutEof;
    private volatile bool stderrEof;
    private volatile bool stopping;
    private Exception stdoutError;
    private Exception stderrError;
    private long stdoutBytes;
    private string stdoutSha256 = String.Empty;
    private SHA256 stdoutHash = SHA256.Create();

    public Process Process { get { return process; } }
    public bool StdoutEof { get { return stdoutEof; } }
    public bool StderrEof { get { return stderrEof; } }
    public string PumpError
    {
        get
        {
            if (stdoutError != null) return "stdout: " + stdoutError.Message;
            if (stderrError != null) return "stderr: " + stderrError.Message;
            return String.Empty;
        }
    }
    public long StdoutBytes { get { return stdoutBytes; } }
    public string StdoutSha256 { get { return stdoutSha256; } }
    public bool OutputFilesClosed
    {
        get { return stdoutFile == null && stderrFile == null; }
    }

    public void Start(string executable, string arguments, string stdoutPath, string stderrPath)
    {
        stdoutFile = new FileStream(
            stdoutPath, FileMode.CreateNew, FileAccess.Write,
            FileShare.ReadWrite | FileShare.Delete, 4096, FileOptions.WriteThrough);
        stderrFile = new FileStream(
            stderrPath, FileMode.CreateNew, FileAccess.Write,
            FileShare.ReadWrite | FileShare.Delete, 4096, FileOptions.WriteThrough);
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = executable;
        info.Arguments = arguments;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        process = new Process();
        process.StartInfo = info;
        if (!process.Start()) throw new InvalidOperationException("docker events did not start");
        stdoutThread = new Thread(delegate() { Pump(process.StandardOutput, stdoutFile, true); });
        stderrThread = new Thread(delegate() { Pump(process.StandardError, stderrFile, false); });
        stdoutThread.IsBackground = true;
        stderrThread.IsBackground = true;
        stdoutThread.Start();
        stderrThread.Start();
    }

    private void Pump(StreamReader reader, FileStream target, bool isStdout)
    {
        try
        {
            string line;
            UTF8Encoding utf8 = new UTF8Encoding(false, true);
            while ((line = reader.ReadLine()) != null)
            {
                byte[] bytes = utf8.GetBytes(line + "\n");
                target.Write(bytes, 0, bytes.Length);
                target.Flush(true);
                if (isStdout)
                {
                    stdoutHash.TransformBlock(bytes, 0, bytes.Length, bytes, 0);
                    stdoutBytes += bytes.Length;
                }
            }
        }
        catch (Exception error)
        {
            if (!stopping)
            {
                if (isStdout) stdoutError = error; else stderrError = error;
            }
        }
        finally
        {
            if (isStdout)
            {
                try
                {
                    stdoutHash.TransformFinalBlock(new byte[0], 0, 0);
                    stdoutSha256 = BitConverter.ToString(stdoutHash.Hash)
                        .Replace("-", String.Empty).ToLowerInvariant();
                }
                catch (Exception error) { stdoutError = error; }
            }
            try { target.Flush(true); } catch { }
            if (isStdout) stdoutEof = true; else stderrEof = true;
        }
    }

    public void StopAndWait(int milliseconds)
    {
        stopping = true;
        if (process != null && !process.HasExited)
        {
            process.Kill();
            if (!process.WaitForExit(milliseconds))
                throw new TimeoutException("docker events did not stop");
        }
        if (stdoutThread != null && !stdoutThread.Join(milliseconds))
            throw new TimeoutException("docker events stdout pump did not stop");
        if (stderrThread != null && !stderrThread.Join(milliseconds))
            throw new TimeoutException("docker events stderr pump did not stop");
        if (stdoutFile != null) stdoutFile.Flush(true);
        if (stderrFile != null) stderrFile.Flush(true);
    }

    public void CloseOutputFiles()
    {
        if ((stdoutThread != null && stdoutThread.IsAlive) ||
            (stderrThread != null && stderrThread.IsAlive))
            throw new InvalidOperationException(
                "cannot close process output files while a pump is running");
        if (stdoutFile != null)
        {
            stdoutFile.Dispose();
            stdoutFile = null;
        }
        if (stderrFile != null)
        {
            stderrFile.Dispose();
            stderrFile = null;
        }
    }

    public void Dispose()
    {
        try { StopAndWait(5000); } catch { }
        try { CloseOutputFiles(); } catch { }
        if (process != null) process.Dispose();
        if (stdoutHash != null) stdoutHash.Dispose();
    }
}
'@
    $contractSha256 = Get-Sha256Text -Value $sourceTemplate
    $typeName = 'StratusDockerEventCollector_' +
        $ExpectedRevision.Substring(0, 12) + '_' + $contractSha256.Substring(0, 12)
    $contractIdentity = "$ExpectedRevision`:$contractSha256"
    $source = $sourceTemplate.Replace('__STRATUS_TYPE__', $typeName)
    $source = $source.Replace('__STRATUS_CONTRACT__', $contractIdentity)
    $sourceSha256 = Get-Sha256Text -Value $source
    $type = $typeName -as [type]
    if ($null -eq $type) {
        $compiledTypes = @(Microsoft.PowerShell.Utility\Add-Type `
            -TypeDefinition $source -PassThru -ErrorAction Stop)
        $matches = @($compiledTypes | Where-Object { $_.FullName -ceq $typeName })
        if ($matches.Count -ne 1) {
            throw 'The Docker event collector did not compile to one exact revision-bound type.'
        }
        $type = $matches[0]
    }
    $field = $type.GetField(
        'ContractIdentity',
        [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::Static
    )
    if (
        $null -eq $field -or
        -not $field.IsLiteral -or
        [string]$field.GetRawConstantValue() -cne $contractIdentity
    ) {
        throw 'An existing Docker event collector type has the wrong source identity.'
    }
    return [pscustomobject]@{
        type = $type
        type_name = $typeName
        contract_identity = $contractIdentity
        template_sha256 = $contractSha256
        source_sha256 = $sourceSha256
        assembly_full_name_sha256 = Get-Sha256Text -Value (
            [string]$type.Assembly.FullName + "`n"
        )
        assembly_is_dynamic = [bool]$type.Assembly.IsDynamic
        module_version_id = [string]$type.Module.ModuleVersionId
    }
}

function Open-TransitionWatchdogProgramBoundary {
    param([Parameter(Mandatory = $true)][string] $AbsoluteRoot)

    $watchdogPath = Join-Path $AbsoluteRoot `
        'scripts\Invoke-StratusSuccessorTransitionWatchdog.ps1'
    $watchdogItem = Assert-RegularFile -LiteralPath $watchdogPath
    $watchdogHash = Get-DirectFileSha256 -LiteralPath $watchdogItem.FullName
    if ($watchdogHash -cne $approvedWatchdogSha256) {
        throw 'The successor transition watchdog program is not the approved reviewed bytes.'
    }
    $ownerProcess = [Diagnostics.Process]::GetCurrentProcess()
    try {
        $hostPath = [IO.Path]::GetFullPath($ownerProcess.MainModule.FileName)
        $ownerStartedAt = [DateTimeOffset]::new(
            $ownerProcess.StartTime.ToUniversalTime(), [TimeSpan]::Zero
        )
    }
    finally {
        $ownerProcess.Dispose()
    }
    $hostItem = Assert-RegularFile -LiteralPath $hostPath
    $hostHash = Get-DirectFileSha256 -LiteralPath $hostItem.FullName
    $watchdogLock = $null
    $hostLock = $null
    try {
        $watchdogLock = [IO.FileStream]::new(
            $watchdogItem.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $hostLock = [IO.FileStream]::new(
            $hostItem.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        if (
            $watchdogLock.Length -ne $watchdogItem.Length -or
            $hostLock.Length -ne $hostItem.Length -or
            (Get-DirectFileSha256 -LiteralPath $watchdogItem.FullName) -cne $watchdogHash -or
            (Get-DirectFileSha256 -LiteralPath $hostItem.FullName) -cne $hostHash
        ) {
            throw 'The watchdog program or its PowerShell host changed while being locked.'
        }
        $script:watchdogScriptReadLock = $watchdogLock
        $script:watchdogHostReadLock = $hostLock
        return [pscustomobject]@{
            script_path = [IO.Path]::GetFullPath($watchdogItem.FullName)
            script_sha256 = $watchdogHash
            script_bytes = [int64]$watchdogItem.Length
            host_path = [IO.Path]::GetFullPath($hostItem.FullName)
            host_sha256 = $hostHash
            host_bytes = [int64]$hostItem.Length
            owner_pid = [int]$PID
            owner_start_time_utc = $ownerStartedAt.ToString('o')
            read_locks_held = $true
        }
    }
    catch {
        if ($null -ne $hostLock) { $hostLock.Dispose() }
        if ($null -ne $watchdogLock) { $watchdogLock.Dispose() }
        throw
    }
}

function Assert-TransitionWatchdogProgramUnchanged {
    param([Parameter(Mandatory = $true)][object] $Program)
    if (
        $null -eq $script:watchdogScriptReadLock -or
        $null -eq $script:watchdogHostReadLock -or
        $script:watchdogScriptReadLock.Length -ne $Program.script_bytes -or
        $script:watchdogHostReadLock.Length -ne $Program.host_bytes -or
        (Get-DirectFileSha256 -LiteralPath $Program.script_path) -cne $Program.script_sha256 -or
        (Get-DirectFileSha256 -LiteralPath $Program.host_path) -cne $Program.host_sha256
    ) {
        throw 'The read-locked watchdog program boundary changed during transition.'
    }
    return [pscustomobject]@{
        script_sha256 = [string]$Program.script_sha256
        host_sha256 = [string]$Program.host_sha256
        read_locks_held = $true
    }
}

function Start-TransitionWatchdogProcess {
    param(
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $HeartbeatPath,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $PreStartPath,
        [Parameter(Mandatory = $true)][string] $PreStartSha256
    )
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
        'stratus-successor-watchdog-' + $SessionId
    )
    [void](New-Item -ItemType Directory -Path $tempRoot)
    $stdoutPath = Join-Path $tempRoot 'watchdog.stdout.json'
    $stderrPath = Join-Path $tempRoot 'watchdog.stderr.txt'
    $argumentValues = @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', [string]$Program.script_path,
        '-Root', $absoluteRoot,
        '-SessionId', $SessionId,
        '-OwnerPid', [string]$Program.owner_pid,
        '-OwnerStartTimeUtc', [string]$Program.owner_start_time_utc,
        '-DeadlineUtc', $HardDeadline.ToString('o'),
        '-HeartbeatTimeoutSeconds', [string]$watchdogHeartbeatTimeoutSeconds,
        '-LeasePath', $LeasePath,
        '-ReceiptPath', $ReceiptPath,
        '-HeartbeatPath', $HeartbeatPath,
        '-CommitPath', $CommitPath,
        '-LiveAcceptancePath', $LivePath,
        '-PreStartAttestationPath', $PreStartPath,
        '-ExpectedPreStartSha256', $PreStartSha256,
        '-PostgresContainer', $productionPostgresId
    )
    $arguments = (@($argumentValues | ForEach-Object {
        ConvertTo-WindowsCommandLineArgument -Value ([string]$_)
    }) -join ' ')
    $collector = $null
    try {
        $collector = [Activator]::CreateInstance($CollectorType.type)
        $requestedAt = [DateTimeOffset]::UtcNow
        $collector.Start(
            [string]$Program.host_path, $arguments, $stdoutPath, $stderrPath
        )
        $process = $collector.Process
        $process.Refresh()
        if ($process.HasExited) {
            throw 'The successor transition watchdog exited during launch.'
        }
        $processStartedAt = [DateTimeOffset]::new(
            $process.StartTime.ToUniversalTime(), [TimeSpan]::Zero
        )
        return [pscustomobject]@{
            collector = $collector
            process = $process
            process_id = [int]$process.Id
            process_started_at = $processStartedAt.ToString('o')
            requested_at = $requestedAt.ToString('o')
            stdout_path = $stdoutPath
            stderr_path = $stderrPath
            temp_root = $tempRoot
            argument_count = [int]$argumentValues.Count
            command_sha256 = Get-Sha256Text -Value ($arguments + "`n")
            intentionally_stopped = $false
        }
    }
    catch {
        if ($null -ne $collector) { $collector.Dispose() }
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

function Assert-TransitionWatchdogProcessConnected {
    param([Parameter(Mandatory = $true)][object] $Watchdog)
    $Watchdog.process.Refresh()
    if (
        $Watchdog.process.HasExited -or $Watchdog.collector.StdoutEof -or
        $Watchdog.collector.StderrEof -or
        -not [string]::IsNullOrEmpty([string]$Watchdog.collector.PumpError)
    ) {
        throw 'The successor transition watchdog lost its continuous process boundary.'
    }
}

function Start-WatchdogHeartbeatPublisher {
    param(
        [Parameter(Mandatory = $true)][object] $CollectorType,
        [Parameter(Mandatory = $true)][string] $HeartbeatPath,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $LeaseSha256
    )
    $publisherType = $CollectorType.type.GetNestedType(
        'HeartbeatPublisher', [Reflection.BindingFlags]::Public
    )
    if ($null -eq $publisherType) {
        throw 'The revision-bound collector lacks its heartbeat publisher type.'
    }
    $publisher = [Activator]::CreateInstance($publisherType)
    try {
        $publisher.Start(
            $HeartbeatPath, $SessionId, [int]$Program.owner_pid,
            [string]$Program.owner_start_time_utc, $LeasePath, $LeaseSha256,
            [int]1000
        )
        $publisher.ThrowIfError()
        $evidence = $publisher.Snapshot()
        if (
            $evidence.Sequence -lt 1 -or
            [string]$evidence.Sha256 -notmatch '^[0-9a-f]{64}$' -or
            [string]::IsNullOrWhiteSpace([string]$evidence.WrittenAt)
        ) {
            throw 'The first lease-bound watchdog heartbeat was not durably published.'
        }
        return $publisher
    }
    catch {
        $publisher.Dispose()
        throw
    }
}

function Assert-WatchdogHeartbeatPublisher {
    param([Parameter(Mandatory = $true)][object] $Publisher)
    $Publisher.ThrowIfError()
    $evidence = $Publisher.Snapshot()
    if (
        $evidence.Sequence -lt 1 -or
        [string]$evidence.Sha256 -notmatch '^[0-9a-f]{64}$' -or
        [string]::IsNullOrWhiteSpace([string]$evidence.WrittenAt)
    ) {
        throw 'The lease-bound watchdog heartbeat publisher is incomplete.'
    }
    return [pscustomobject]@{
        sequence = [int64]$evidence.Sequence
        written_at = [string]$evidence.WrittenAt
        sha256 = [string]$evidence.Sha256
    }
}

function Assert-WatchdogHeartbeatDocument {
    param(
        [Parameter(Mandatory = $true)][object] $Document,
        [Parameter(Mandatory = $true)][object] $Evidence,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $LeaseProof,
        [Parameter(Mandatory = $true)][string] $LeasePath
    )
    $heartbeat = $Document.value
    Assert-ExactProperties -Value $heartbeat -Label 'watchdog owner heartbeat' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'sequence', 'written_at'
    )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'lease_path',
        'lease_sha256', 'written_at'
    )) {
        Assert-NativeJsonString -Value $heartbeat.$name -Label "watchdog heartbeat $name"
    }
    foreach ($name in @('owner_pid', 'sequence')) {
        Assert-NativeJsonInteger -Value $heartbeat.$name -Label "watchdog heartbeat $name"
    }
    $writtenAt = ConvertTo-CanonicalUtcTime -Value $heartbeat.written_at `
        -Label 'watchdog heartbeat written_at'
    $armedAt = ConvertTo-CanonicalUtcTime -Value $LeaseProof.armed_at `
        -Label 'watchdog lease armed_at'
    if (
        $heartbeat.format -cne 'stratus-successor-transition-watchdog-heartbeat-v1' -or
        $heartbeat.session_id -cne $SessionId -or
        $heartbeat.owner_pid -ne $Program.owner_pid -or
        $heartbeat.owner_start_time_utc -cne $Program.owner_start_time_utc -or
        -not (Test-ExactPathEqual -Left $heartbeat.lease_path -Right $LeasePath) -or
        $heartbeat.lease_sha256 -cne $LeaseProof.sha256 -or
        $heartbeat.sequence -ne $Evidence.sequence -or
        $heartbeat.written_at -cne $Evidence.written_at -or
        $Document.sha256 -cne $Evidence.sha256 -or
        $writtenAt -lt $armedAt.AddSeconds(-$clockSkewSeconds) -or
        $writtenAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds)
    ) {
        throw 'The first watchdog heartbeat is not exact, durable, and lease-bound.'
    }
    return $heartbeat
}

function Assert-TransitionWatchdogLease {
    param(
        [Parameter(Mandatory = $true)][object] $Document,
        [Parameter(Mandatory = $true)][object] $Watchdog,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $HeartbeatPath,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $PreStartPath,
        [Parameter(Mandatory = $true)][string] $PreStartSha256,
        [Parameter(Mandatory = $true)][string] $PreStartPayloadSha256,
        [Parameter(Mandatory = $true)][object] $PreStartPostgres
    )
    $lease = $Document.value
    Assert-ExactProperties -Value $lease -Label 'transition watchdog lease' -Expected @(
        'format', 'armed', 'armed_at', 'session_id', 'watchdog', 'owner',
        'deadline', 'artifacts', 'docker_engine', 'postgres', 'containment'
    )
    Assert-NativeJsonString -Value $lease.format -Label 'watchdog lease format'
    Assert-NativeJsonBoolean -Value $lease.armed -Label 'watchdog lease armed'
    Assert-NativeJsonString -Value $lease.armed_at -Label 'watchdog lease armed_at'
    Assert-NativeJsonString -Value $lease.session_id -Label 'watchdog lease session_id'
    $armedAt = ConvertTo-CanonicalUtcTime -Value $lease.armed_at `
        -Label 'watchdog lease armed_at'
    $requestedAt = ConvertTo-CanonicalUtcTime -Value $Watchdog.requested_at `
        -Label 'watchdog launch requested_at'
    if (
        $lease.format -cne 'stratus-successor-transition-watchdog-lease-v1' -or
        -not $lease.armed -or $lease.session_id -cne $SessionId -or
        $armedAt -lt $requestedAt.AddSeconds(-$clockSkewSeconds) -or
        $armedAt -ge $HardDeadline -or
        $armedAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds)
    ) {
        throw 'The watchdog lease is not a fresh accepted lease for this transition.'
    }

    foreach ($name in @('watchdog', 'owner', 'deadline', 'artifacts', 'docker_engine', 'postgres', 'containment')) {
        Assert-NativeJsonObject -Value $lease.$name -Label "watchdog lease $name"
    }
    Assert-ExactProperties -Value $lease.watchdog -Label 'watchdog lease process' `
        -Expected @('process_id', 'process_started_at')
    Assert-NativeJsonInteger -Value $lease.watchdog.process_id `
        -Label 'watchdog lease process_id'
    Assert-NativeJsonString -Value $lease.watchdog.process_started_at `
        -Label 'watchdog lease process_started_at'
    [void](ConvertTo-CanonicalUtcTime -Value $lease.watchdog.process_started_at `
        -Label 'watchdog lease process_started_at')
    if (
        $lease.watchdog.process_id -ne $Watchdog.process_id -or
        $lease.watchdog.process_started_at -cne $Watchdog.process_started_at
    ) {
        throw 'The watchdog lease was not issued by the launched exact process.'
    }

    Assert-ExactProperties -Value $lease.owner -Label 'watchdog lease owner' `
        -Expected @('process_id', 'process_started_at')
    Assert-NativeJsonInteger -Value $lease.owner.process_id -Label 'watchdog lease owner pid'
    Assert-NativeJsonString -Value $lease.owner.process_started_at `
        -Label 'watchdog lease owner start'
    if (
        $lease.owner.process_id -ne $Program.owner_pid -or
        $lease.owner.process_started_at -cne $Program.owner_start_time_utc
    ) {
        throw 'The watchdog lease is bound to another transition owner.'
    }

    Assert-ExactProperties -Value $lease.deadline -Label 'watchdog lease deadline' `
        -Expected @(
            'hard_deadline_utc', 'heartbeat_timeout_seconds', 'maximum_lease_seconds'
        )
    Assert-NativeJsonString -Value $lease.deadline.hard_deadline_utc `
        -Label 'watchdog lease hard deadline'
    foreach ($name in @('heartbeat_timeout_seconds', 'maximum_lease_seconds')) {
        Assert-NativeJsonInteger -Value $lease.deadline.$name `
            -Label "watchdog lease deadline $name"
    }
    if (
        $lease.deadline.hard_deadline_utc -cne $HardDeadline.ToString('o') -or
        $lease.deadline.heartbeat_timeout_seconds -ne $watchdogHeartbeatTimeoutSeconds -or
        $lease.deadline.maximum_lease_seconds -ne 600
    ) {
        throw 'The watchdog lease deadline contract is not exact.'
    }

    Assert-ExactProperties -Value $lease.artifacts -Label 'watchdog lease artifacts' `
        -Expected @(
            'lease_path', 'receipt_path', 'heartbeat_path', 'commit_path',
            'live_path', 'prestart_path', 'prestart_sha256', 'prestart_payload_sha256'
        )
    foreach ($name in @(
        'lease_path', 'receipt_path', 'heartbeat_path', 'commit_path',
        'live_path', 'prestart_path', 'prestart_sha256', 'prestart_payload_sha256'
    )) {
        Assert-NativeJsonString -Value $lease.artifacts.$name `
            -Label "watchdog lease artifact $name"
    }
    $pathPairs = @(
        @([string]$lease.artifacts.lease_path, $LeasePath),
        @([string]$lease.artifacts.receipt_path, $ReceiptPath),
        @([string]$lease.artifacts.heartbeat_path, $HeartbeatPath),
        @([string]$lease.artifacts.commit_path, $CommitPath),
        @([string]$lease.artifacts.live_path, $LivePath),
        @([string]$lease.artifacts.prestart_path, $PreStartPath)
    )
    foreach ($pair in $pathPairs) {
        if (-not (Test-ExactPathEqual -Left $pair[0] -Right $pair[1])) {
            throw 'The watchdog lease artifact paths do not match the handoff.'
        }
    }
    if (
        $lease.artifacts.prestart_sha256 -cne $PreStartSha256 -or
        $lease.artifacts.prestart_payload_sha256 -cne $PreStartPayloadSha256
    ) {
        throw 'The watchdog lease does not hash-bind the exact PreStart artifact.'
    }

    Assert-ExactProperties -Value $lease.docker_engine -Label 'watchdog lease engine' `
        -Expected @(
            'docker_executable_path', 'docker_executable_sha256',
            'docker_executable_bytes', 'context', 'endpoint', 'skip_tls_verify',
            'engine_id', 'server_version', 'engine_name', 'docker_root_dir',
            'contract_sha256'
        )
    foreach ($name in @(
        'docker_executable_path', 'docker_executable_sha256', 'context', 'endpoint',
        'engine_id', 'server_version', 'engine_name', 'docker_root_dir', 'contract_sha256'
    )) {
        Assert-NativeJsonString -Value $lease.docker_engine.$name `
            -Label "watchdog lease engine $name"
    }
    Assert-NativeJsonInteger -Value $lease.docker_engine.docker_executable_bytes `
        -Label 'watchdog lease engine docker bytes'
    Assert-NativeJsonBoolean -Value $lease.docker_engine.skip_tls_verify `
        -Label 'watchdog lease engine SkipTLSVerify'
    $watchdogEngineContract = @(
        [string]$lease.docker_engine.docker_executable_path,
        [string]$lease.docker_engine.docker_executable_sha256,
        [string]$lease.docker_engine.docker_executable_bytes,
        [string]$lease.docker_engine.context,
        [string]$lease.docker_engine.endpoint,
        'false',
        [string]$lease.docker_engine.engine_id,
        [string]$lease.docker_engine.server_version,
        [string]$lease.docker_engine.engine_name,
        [string]$lease.docker_engine.docker_root_dir
    ) -join "`0"
    if (
        -not (Test-ExactPathEqual -Left $lease.docker_engine.docker_executable_path `
            -Right $script:dockerExecutable) -or
        $lease.docker_engine.docker_executable_sha256 -cne $approvedDockerExecutableSha256 -or
        $lease.docker_engine.docker_executable_bytes -ne $initialDockerEngine.docker_executable_bytes -or
        $lease.docker_engine.context -cne $approvedDockerContext -or
        $lease.docker_engine.endpoint -cne $approvedDockerEndpoint -or
        $lease.docker_engine.skip_tls_verify -or
        $lease.docker_engine.engine_id -cne $approvedDockerEngineId -or
        $lease.docker_engine.server_version -cne $approvedDockerServerVersion -or
        $lease.docker_engine.engine_name -cne $approvedDockerEngineName -or
        $lease.docker_engine.docker_root_dir -cne $approvedDockerRootDirectory -or
        $lease.docker_engine.contract_sha256 -cne (
            Get-Sha256Text -Value ($watchdogEngineContract + "`n")
        )
    ) {
        throw 'The watchdog lease Docker engine boundary is not exact.'
    }

    Assert-ExactProperties -Value $lease.postgres -Label 'watchdog lease PostgreSQL' `
        -Expected @(
            'container_id', 'image_id', 'config_image', 'restart_count',
            'restart_policy_before', 'restart_policy_armed', 'state',
            'started_at', 'finished_at'
        )
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_policy_before',
        'restart_policy_armed', 'state', 'started_at', 'finished_at'
    )) {
        Assert-NativeJsonString -Value $lease.postgres.$name `
            -Label "watchdog lease PostgreSQL $name"
    }
    Assert-NativeJsonInteger -Value $lease.postgres.restart_count `
        -Label 'watchdog lease PostgreSQL restart_count'
    if (
        $lease.postgres.container_id -cne $PreStartPostgres.container_id -or
        $lease.postgres.image_id -cne $PreStartPostgres.image_id -or
        $lease.postgres.config_image -cne $PreStartPostgres.config_image -or
        $lease.postgres.restart_count -ne $PreStartPostgres.restart_count -or
        $lease.postgres.restart_policy_before -cne 'unless-stopped' -or
        $lease.postgres.restart_policy_armed -cne 'no' -or
        $lease.postgres.state -cne 'exited' -or
        $lease.postgres.started_at -cne $PreStartPostgres.started_at -or
        $lease.postgres.finished_at -cne $PreStartPostgres.finished_at
    ) {
        throw 'The watchdog lease PostgreSQL identity is not the accepted PreStart identity.'
    }

    Assert-ExactProperties -Value $lease.containment -Label 'watchdog lease containment' `
        -Expected @(
            'stop_timeout_seconds', 'stop_wall_clock_timeout_seconds',
            'exact_container_only', 'ambiguous_start_requires_stop_attempt'
        )
    foreach ($name in @('stop_timeout_seconds', 'stop_wall_clock_timeout_seconds')) {
        Assert-NativeJsonInteger -Value $lease.containment.$name `
            -Label "watchdog lease containment $name"
    }
    foreach ($name in @('exact_container_only', 'ambiguous_start_requires_stop_attempt')) {
        Assert-NativeJsonBoolean -Value $lease.containment.$name `
            -Label "watchdog lease containment $name"
    }
    if (
        $lease.containment.stop_timeout_seconds -ne $postgresRollbackTimeoutSeconds -or
        $lease.containment.stop_wall_clock_timeout_seconds -ne 25 -or
        -not $lease.containment.exact_container_only -or
        -not $lease.containment.ambiguous_start_requires_stop_attempt
    ) {
        throw 'The watchdog lease containment boundary is not exact.'
    }
    return [pscustomobject]@{
        document = $Document
        lease = $lease
        armed_at = $armedAt.ToString('o')
        sha256 = [string]$Document.sha256
        docker_engine = $lease.docker_engine
    }
}

function Wait-TransitionWatchdogLease {
    param(
        [Parameter(Mandatory = $true)][object] $Watchdog,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $HeartbeatPath,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $PreStartPath,
        [Parameter(Mandatory = $true)][string] $PreStartSha256,
        [Parameter(Mandatory = $true)][string] $PreStartPayloadSha256,
        [Parameter(Mandatory = $true)][object] $PreStartPostgres
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($watchdogLeaseReadyTimeoutSeconds)
    if ($deadline -gt $HardDeadline) { $deadline = $HardDeadline }
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        Assert-TransitionWatchdogProcessConnected -Watchdog $Watchdog
        if (Test-Path -LiteralPath $LeasePath -PathType Leaf) {
            $item = Get-Item -LiteralPath $LeasePath -Force
            if ($item.Length -gt 0) {
                $document = $null
                try {
                    $document = Read-BoundedStrictJsonDocument -LiteralPath $LeasePath `
                        -Label 'transition watchdog lease' -MaximumBytes 262144
                }
                catch [IO.IOException] {
                    Start-Sleep -Milliseconds 50
                    continue
                }
                $accepted = Assert-TransitionWatchdogLease -Document $document `
                    -Watchdog $Watchdog -Program $Program -SessionId $SessionId `
                    -HardDeadline $HardDeadline -LeasePath $LeasePath `
                    -ReceiptPath $ReceiptPath -HeartbeatPath $HeartbeatPath `
                    -CommitPath $CommitPath -LivePath $LivePath `
                    -PreStartPath $PreStartPath -PreStartSha256 $PreStartSha256 `
                    -PreStartPayloadSha256 $PreStartPayloadSha256 `
                    -PreStartPostgres $PreStartPostgres
                if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
                    throw 'The watchdog did not reserve its create-new terminal receipt.'
                }
                $receiptItem = Get-Item -LiteralPath $ReceiptPath -Force
                if ($receiptItem.Length -ne 0) {
                    throw 'The watchdog reached a terminal state before PostgreSQL start.'
                }
                if (
                    (Test-Path -LiteralPath $HeartbeatPath) -or
                    (Test-Path -LiteralPath $CommitPath) -or
                    (Test-Path -LiteralPath $LivePath)
                ) {
                    throw 'A post-lease transition artifact appeared before owner acceptance.'
                }
                return $accepted
            }
        }
        Start-Sleep -Milliseconds 50
    }
    throw 'The successor transition watchdog did not publish an accepted lease in time.'
}

function Assert-WatchdogCommitDocument {
    param(
        [Parameter(Mandatory = $true)][object] $Document,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $LeaseProof,
        [Parameter(Mandatory = $true)][string] $LeasePath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $LiveSha256,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline
    )
    $commit = $Document.value
    Assert-ExactProperties -Value $commit -Label 'watchdog commit marker' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'live_path', 'live_sha256', 'committed_at'
    )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'lease_path',
        'lease_sha256', 'live_path', 'live_sha256', 'committed_at'
    )) {
        Assert-NativeJsonString -Value $commit.$name -Label "watchdog commit $name"
    }
    Assert-NativeJsonInteger -Value $commit.owner_pid -Label 'watchdog commit owner_pid'
    $committedAt = ConvertTo-CanonicalUtcTime -Value $commit.committed_at `
        -Label 'watchdog commit committed_at'
    $armedAt = ConvertTo-CanonicalUtcTime -Value $LeaseProof.armed_at `
        -Label 'accepted watchdog lease armed_at'
    if (
        $commit.format -cne 'stratus-successor-transition-watchdog-commit-v1' -or
        $commit.session_id -cne $SessionId -or
        $commit.owner_pid -ne $Program.owner_pid -or
        $commit.owner_start_time_utc -cne $Program.owner_start_time_utc -or
        -not (Test-ExactPathEqual -Left $commit.lease_path -Right $LeasePath) -or
        $commit.lease_sha256 -cne $LeaseProof.sha256 -or
        -not (Test-ExactPathEqual -Left $commit.live_path -Right $LivePath) -or
        $commit.live_sha256 -cne $LiveSha256 -or
        $committedAt -lt $armedAt -or $committedAt -gt $HardDeadline -or
        $committedAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds)
    ) {
        throw 'The watchdog commit marker is not the exact lease-bound Live commit.'
    }
    return [pscustomobject]@{
        document = $Document
        value = $commit
        sha256 = [string]$Document.sha256
        committed_at = $committedAt.ToString('o')
    }
}

function Wait-TransitionWatchdogExit {
    param(
        [Parameter(Mandatory = $true)][object] $Watchdog,
        [AllowNull()][object] $HeartbeatPublisher,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline,
        [switch] $PermitContainmentExit
    )
    $waitDeadline = $HardDeadline.AddSeconds($watchdogProcessExitGraceSeconds)
    while ([DateTimeOffset]::UtcNow -lt $waitDeadline) {
        if ($null -ne $HeartbeatPublisher) { $HeartbeatPublisher.ThrowIfError() }
        $Watchdog.process.Refresh()
        if ($Watchdog.process.HasExited) { break }
        [void]$Watchdog.process.WaitForExit(200)
    }
    $Watchdog.process.Refresh()
    if (-not $Watchdog.process.HasExited) {
        throw 'The successor transition watchdog exceeded its hard terminal deadline.'
    }
    $Watchdog.collector.StopAndWait(5000)
    $Watchdog.collector.CloseOutputFiles()
    if (-not [bool]$Watchdog.collector.OutputFilesClosed) {
        throw 'The successor transition watchdog output handles remained open.'
    }
    $Watchdog.intentionally_stopped = $true
    if (-not [string]::IsNullOrEmpty([string]$Watchdog.collector.PumpError)) {
        throw 'The successor transition watchdog output pump failed.'
    }
    $exitCode = [int]$Watchdog.process.ExitCode
    if ((-not $PermitContainmentExit -and $exitCode -ne 0) -or
        ($PermitContainmentExit -and $exitCode -notin @(0, 2, 3))) {
        throw "The successor transition watchdog returned unexpected exit $exitCode."
    }
    $stderrItem = Get-Item -LiteralPath $Watchdog.stderr_path -Force
    if ($stderrItem.Length -ne 0) {
        throw 'The successor transition watchdog emitted unexpected stderr.'
    }
    $stdout = Read-BoundedStrictJsonDocument -LiteralPath $Watchdog.stdout_path `
        -Label 'successor transition watchdog stdout' -MaximumBytes 262144
    if (
        $stdout.bytes -ne [int64]$Watchdog.collector.StdoutBytes -or
        $stdout.sha256 -cne [string]$Watchdog.collector.StdoutSha256
    ) {
        throw 'The successor transition watchdog stdout differs from the sealed output pump.'
    }
    return [pscustomobject]@{
        exit_code = $exitCode
        stdout = $stdout
        completed_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
}

function Assert-TransitionWatchdogSuccessReceipt {
    param(
        [Parameter(Mandatory = $true)][object] $ExitProof,
        [Parameter(Mandatory = $true)][string] $ReceiptPath,
        [Parameter(Mandatory = $true)][string] $SessionId,
        [Parameter(Mandatory = $true)][object] $Program,
        [Parameter(Mandatory = $true)][object] $LeaseProof,
        [Parameter(Mandatory = $true)][object] $CommitProof,
        [Parameter(Mandatory = $true)][string] $CommitPath,
        [Parameter(Mandatory = $true)][string] $LivePath,
        [Parameter(Mandatory = $true)][string] $LiveSha256,
        [Parameter(Mandatory = $true)][string] $LivePayloadSha256,
        [Parameter(Mandatory = $true)][string] $PostgresStartedAt,
        [Parameter(Mandatory = $true)][DateTimeOffset] $HardDeadline
    )
    if ($ExitProof.exit_code -ne 0) {
        throw 'The transition watchdog did not accept the committed Live artifact.'
    }
    $receiptDocument = Read-BoundedStrictJsonDocument -LiteralPath $ReceiptPath `
        -Label 'successor transition watchdog terminal receipt' -MaximumBytes 262144
    $receipt = $receiptDocument.value
    Assert-ExactProperties -Value $receipt -Label 'watchdog terminal receipt' -Expected @(
        'format', 'status', 'terminal_at', 'session_id', 'owner_pid',
        'owner_start_time_utc', 'lease_path', 'lease_sha256', 'receipt_path',
        'commit_path', 'live_path', 'trigger', 'docker_engine', 'containment',
        'commit', 'authorization', 'failure'
    )
    foreach ($name in @(
        'format', 'status', 'terminal_at', 'session_id', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'receipt_path', 'commit_path', 'live_path', 'trigger'
    )) {
        Assert-NativeJsonString -Value $receipt.$name -Label "watchdog receipt $name"
    }
    Assert-NativeJsonInteger -Value $receipt.owner_pid -Label 'watchdog receipt owner_pid'
    Assert-NativeJsonObject -Value $receipt.docker_engine -Label 'watchdog receipt engine'
    Assert-NativeJsonObject -Value $receipt.commit -Label 'watchdog receipt commit'
    Assert-NativeJsonObject -Value $receipt.authorization `
        -Label 'watchdog receipt authorization'
    Assert-ExactProperties -Value $receipt.authorization `
        -Label 'watchdog receipt authorization' -Expected @(
            'api_stage_permitted', 'worker_start_permitted',
            'safety_control_start_permitted', 'live_transition_permitted',
            'binance_kill_switch_removal_permitted', 'migration_container_permitted'
        )
    foreach ($name in @(
        'api_stage_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted',
        'binance_kill_switch_removal_permitted', 'migration_container_permitted'
    )) {
        Assert-NativeJsonBoolean -Value $receipt.authorization.$name `
            -Label "watchdog receipt authorization $name"
    }
    $terminalAt = ConvertTo-CanonicalUtcTime -Value $receipt.terminal_at `
        -Label 'watchdog receipt terminal_at'
    $committedAt = ConvertTo-CanonicalUtcTime -Value $CommitProof.committed_at `
        -Label 'accepted watchdog commit time'
    if (
        $null -ne $receipt.containment -or $null -ne $receipt.failure -or
        $receipt.format -cne 'stratus-successor-transition-watchdog-terminal-v1' -or
        $receipt.status -cne 'success' -or $receipt.trigger -cne 'accepted_live_commit' -or
        $receipt.session_id -cne $SessionId -or
        -not $receipt.authorization.api_stage_permitted -or
        $receipt.authorization.worker_start_permitted -or
        $receipt.authorization.safety_control_start_permitted -or
        $receipt.authorization.live_transition_permitted -or
        $receipt.authorization.binance_kill_switch_removal_permitted -or
        $receipt.authorization.migration_container_permitted -or
        $receipt.owner_pid -ne $Program.owner_pid -or
        $receipt.owner_start_time_utc -cne $Program.owner_start_time_utc -or
        -not (Test-ExactPathEqual -Left $receipt.lease_path -Right $LeaseProof.document.path) -or
        $receipt.lease_sha256 -cne $LeaseProof.sha256 -or
        -not (Test-ExactPathEqual -Left $receipt.receipt_path -Right $ReceiptPath) -or
        -not (Test-ExactPathEqual -Left $receipt.commit_path -Right $CommitPath) -or
        -not (Test-ExactPathEqual -Left $receipt.live_path -Right $LivePath) -or
        $terminalAt -lt $committedAt -or $terminalAt -gt $HardDeadline.AddSeconds($clockSkewSeconds)
    ) {
        throw 'The watchdog terminal receipt is not the exact successful transition receipt.'
    }
    if (
        ($receipt.docker_engine | ConvertTo-Json -Depth 10 -Compress) -cne
            ($LeaseProof.docker_engine | ConvertTo-Json -Depth 10 -Compress)
    ) {
        throw 'The watchdog Docker engine changed between lease and terminal receipt.'
    }
    Assert-ExactProperties -Value $receipt.commit -Label 'watchdog terminal commit proof' `
        -Expected @(
            'marker_sha256', 'committed_at', 'live_sha256', 'live_payload_sha256',
            'postgres_started_at', 'docker_engine_contract_sha256'
        )
    foreach ($name in @(
        'marker_sha256', 'committed_at', 'live_sha256', 'live_payload_sha256',
        'postgres_started_at', 'docker_engine_contract_sha256'
    )) {
        Assert-NativeJsonString -Value $receipt.commit.$name `
            -Label "watchdog terminal commit $name"
    }
    if (
        $receipt.commit.marker_sha256 -cne $CommitProof.sha256 -or
        $receipt.commit.committed_at -cne $CommitProof.committed_at -or
        $receipt.commit.live_sha256 -cne $LiveSha256 -or
        $receipt.commit.live_payload_sha256 -cne $LivePayloadSha256 -or
        $receipt.commit.postgres_started_at -cne $PostgresStartedAt -or
        $receipt.commit.docker_engine_contract_sha256 -cne
            $LeaseProof.docker_engine.contract_sha256
    ) {
        throw 'The watchdog terminal receipt does not bind the committed Live proof.'
    }
    $normalizedReceiptJson = $receiptDocument.json.Replace("`r`n", "`n")
    if ($normalizedReceiptJson.Contains("`r") -or
        $ExitProof.stdout.json -cne ($normalizedReceiptJson + "`n")) {
        throw 'The watchdog stdout terminal receipt differs from its durable receipt.'
    }
    return [pscustomobject]@{
        format = [string]$receipt.format
        status = [string]$receipt.status
        trigger = [string]$receipt.trigger
        terminal_at = $terminalAt.ToString('o')
        path = [string]$receiptDocument.path
        bytes = [int64]$receiptDocument.bytes
        sha256 = [string]$receiptDocument.sha256
        lease_sha256 = [string]$receipt.lease_sha256
        commit_marker_sha256 = [string]$receipt.commit.marker_sha256
        live_sha256 = [string]$receipt.commit.live_sha256
        docker_engine_contract_sha256 =
            [string]$receipt.commit.docker_engine_contract_sha256
    }
}

function Invoke-BoundedPinnedDockerStop {
    param(
        [Parameter(Mandatory = $true)][string] $ContainerId,
        [Parameter(Mandatory = $true)][int] $ContainerStopTimeoutSeconds
    )
    if ($ContainerId -notmatch '^[0-9a-f]{64}$') {
        throw 'Bounded PostgreSQL containment received an invalid container identity.'
    }
    if ($ContainerStopTimeoutSeconds -lt 1 -or $ContainerStopTimeoutSeconds -gt 20) {
        throw 'Bounded PostgreSQL containment received an invalid stop timeout.'
    }
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'bounded PostgreSQL containment'
    $collectorType = Initialize-DockerEventCollectorType
    $method = $collectorType.type.GetMethod(
        'RunBounded',
        [Reflection.BindingFlags]::Public -bor [Reflection.BindingFlags]::Static
    )
    if ($null -eq $method) {
        throw 'The revision-bound collector does not expose bounded containment.'
    }
    $arguments = "stop --time $ContainerStopTimeoutSeconds $ContainerId"
    $requestedAt = [DateTimeOffset]::UtcNow
    try {
        $result = $method.Invoke($null, @(
            [string]$dockerCli.path,
            [string]$arguments,
            [int]22000,
            [int]4096,
            [int]4096
        ))
    }
    catch {
        $detail = if ($null -ne $_.Exception.InnerException) {
            $_.Exception.InnerException.Message
        }
        else {
            $_.Exception.Message
        }
        throw "The bounded PostgreSQL containment process failed: $detail"
    }
    $completedAt = [DateTimeOffset]::UtcNow
    [void](Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'bounded PostgreSQL containment completion')
    if ($null -eq $result) {
        throw 'The bounded PostgreSQL containment process returned no result.'
    }
    if (
        [bool]$result.TimedOut -or
        [bool]$result.OutputExceeded -or
        -not [string]::IsNullOrEmpty([string]$result.PumpError) -or
        [int]$result.ExitCode -ne 0 -or
        -not [string]::IsNullOrEmpty([string]$result.Stderr)
    ) {
        throw 'The bounded PostgreSQL containment process timed out, overflowed, or failed.'
    }
    $stdout = ([string]$result.Stdout).Replace("`r`n", "`n")
    if ($stdout.Contains("`r")) {
        throw 'The bounded PostgreSQL containment output is not canonical text.'
    }
    $lines = @($stdout -split "`n" | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    if ($lines.Count -ne 1 -or [string]$lines[0] -cne $ContainerId) {
        throw 'The bounded PostgreSQL containment stopped an unexpected identity.'
    }
    return [pscustomobject]@{
        command = @(
            'stop', '--time', [string]$ContainerStopTimeoutSeconds, $ContainerId
        )
        docker_executable_path = [string]$dockerCli.path
        docker_executable_sha256 = [string]$dockerCli.sha256
        process_timeout_milliseconds = 22000
        overall_hard_timeout_milliseconds = 25000
        stdout_maximum_bytes = 4096
        stderr_maximum_bytes = 4096
        requested_at = $requestedAt.ToString('o')
        completed_at = $completedAt.ToString('o')
        exit_code = [int]$result.ExitCode
        output_container_id = [string]$lines[0]
        timed_out = [bool]$result.TimedOut
        output_exceeded = [bool]$result.OutputExceeded
    }
}

function Start-DockerEventWitness {
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'Docker event witness'
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
        'stratus-successor-events-' + [guid]::NewGuid().ToString('N')
    )
    [void](New-Item -ItemType Directory -Path $tempRoot)
    $stdoutPath = Join-Path $tempRoot 'events.jsonl'
    $stderrPath = Join-Path $tempRoot 'events.stderr'
    $startedAt = [DateTimeOffset]::UtcNow
    $collector = $null
    try {
        $collectorType = Initialize-DockerEventCollectorType
        $collector = [Activator]::CreateInstance($collectorType.type)
        $collector.Start(
            $dockerCli.path, 'events --format "{{json .}}"',
            $stdoutPath, $stderrPath
        )
        $process = $collector.Process
        $process.Refresh()
        if (
            $process.HasExited -or
            -not (Test-Path -LiteralPath $stdoutPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $stderrPath -PathType Leaf)
        ) {
            throw 'The Docker event witness did not remain connected.'
        }
        return [pscustomobject]@{
            session_id = [guid]::NewGuid().ToString('N')
            collector = $collector
            process = $process
            process_id = [int]$process.Id
            process_started_at = $process.StartTime.ToUniversalTime().ToString('o')
            requested_at = $startedAt.ToString('o')
            docker_executable_path = [string]$dockerCli.path
            docker_executable_sha256 = [string]$dockerCli.sha256
            docker_executable_bytes = [int64]$dockerCli.bytes
            collector_type_name = [string]$collectorType.type_name
            collector_contract_identity = [string]$collectorType.contract_identity
            collector_template_sha256 = [string]$collectorType.template_sha256
            collector_source_sha256 = [string]$collectorType.source_sha256
            collector_assembly_full_name_sha256 =
                [string]$collectorType.assembly_full_name_sha256
            collector_assembly_is_dynamic = [bool]$collectorType.assembly_is_dynamic
            collector_module_version_id = [string]$collectorType.module_version_id
            stdout_path = $stdoutPath
            stderr_path = $stderrPath
            temp_root = $tempRoot
            prefix_length = [int64]0
            prefix_sha256 = ''
            intentionally_stopped = $false
        }
    }
    catch {
        if ($null -ne $collector) {
            $collector.Dispose()
        }
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

function Stop-DockerEventWitness {
    param(
        [AllowNull()][object] $Witness,
        [switch] $PreserveFiles,
        [switch] $Seal
    )
    if ($null -eq $Witness) {
        return
    }
    try {
        $Witness.process.Refresh()
        if ($Seal -and (
            $Witness.process.HasExited -or
            $Witness.collector.StdoutEof -or
            -not [string]::IsNullOrEmpty([string]$Witness.collector.PumpError)
        )) {
            throw 'The Docker event witness reached EOF before the explicit final seal.'
        }
        if (-not $Witness.process.HasExited) {
            $Witness.collector.StopAndWait(5000)
        }
        $Witness.intentionally_stopped = $true
    }
    finally {
        if (-not $PreserveFiles) {
            $Witness.collector.Dispose()
        }
        if (-not $PreserveFiles -and (Test-Path -LiteralPath $Witness.temp_root)) {
            Remove-Item -LiteralPath $Witness.temp_root -Recurse -Force `
                -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-DockerEventBarrier {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][ValidateSet('prestart', 'final')]
        [string] $Phase
    )
    $barrierName = "stratus-successor-$Phase-barrier-$($Witness.session_id)"
    $barrierLabel = "stratus.successor.event-barrier=$($Witness.session_id):$Phase"
    $barrierId = ''
    $owned = $false
    try {
        $barrierId = Get-OneNativeLine -Label "$Phase event barrier container" -Lines (
            Invoke-Docker -Arguments @(
                'create', '--name', $barrierName, '--network', 'none',
                '--label', $barrierLabel, '--entrypoint', '/bin/false', $ExpectedImageId
            )
        )
        if ($barrierId -notmatch '^[0-9a-f]{64}$' -or $barrierId -ceq $productionPostgresId) {
            throw 'The event barrier did not return an owned exact container ID.'
        }
        $owned = $true
        $inspect = Get-OneJsonObject -Label "$Phase event barrier inspect" -Lines (
            Invoke-Docker -Arguments @('inspect', $barrierId, '--format', '{{json .}}')
        )
        if (
            $inspect.Id -cne $barrierId -or
            $inspect.Name -cne "/$barrierName" -or
            $inspect.Image -cne $ExpectedImageId -or
            $inspect.State.Status -cne 'created' -or
            $inspect.State.Running -or
            $inspect.State.Pid -ne 0 -or
            $inspect.HostConfig.NetworkMode -cne 'none' -or
            $inspect.Config.Labels.'stratus.successor.event-barrier' -cne
                "$($Witness.session_id):$Phase" -or
            @($inspect.Mounts).Count -ne 0
        ) {
            throw 'The event barrier container boundary is not exact.'
        }
    }
    finally {
        if ($owned) {
            $removed = Get-OneNativeLine -Label "$Phase event barrier cleanup" -Lines (
                Invoke-Docker -Arguments @('rm', $barrierId)
            )
            if ($removed -cne $barrierId) {
                throw 'The event barrier cleanup returned the wrong container ID.'
            }
        }
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($dockerEventBarrierTimeoutSeconds)
    do {
        $snapshot = Get-DockerEventJournalSnapshot -Witness $Witness
        $barrierEvents = @($snapshot.events | Where-Object {
            [string](Get-OptionalPropertyValue -InputObject $_ -Name 'Type') -ceq 'container' -and
            [string]$_.Actor.ID -ceq $barrierId
        })
        $createEvents = @($barrierEvents | Where-Object { [string]$_.Action -ceq 'create' })
        $destroyEvents = @($barrierEvents | Where-Object { [string]$_.Action -ceq 'destroy' })
        if ($barrierEvents.Count -eq 2 -and $createEvents.Count -eq 1 -and
            $destroyEvents.Count -eq 1) {
            break
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    if ($barrierEvents.Count -ne 2 -or $createEvents.Count -ne 1 -or
        $destroyEvents.Count -ne 1) {
        throw 'The continuous Docker event witness did not observe the exact barrier.'
    }
    foreach ($event in $barrierEvents) {
        Assert-NativeJsonObject -Value $event.Actor.Attributes `
            -Label "$Phase event barrier attributes"
        if (
            $event.Actor.Attributes.name -cne $barrierName -or
            $event.Actor.Attributes.'stratus.successor.event-barrier' -cne
                "$($Witness.session_id):$Phase"
        ) {
            throw 'The continuous Docker event barrier attributes drifted.'
        }
    }
    $timeNanos = @($barrierEvents | ForEach-Object { [int64]$_.timeNano })
    $createTimeNano = [int64]$createEvents[0].timeNano
    $destroyTimeNano = [int64]$destroyEvents[0].timeNano
    if ($createTimeNano -le 0 -or $destroyTimeNano -le $createTimeNano) {
        throw 'The Docker event barrier ordering is invalid.'
    }
    return [pscustomobject]@{
        phase = $Phase
        container_id = $barrierId
        create_time_nano = $createTimeNano
        destroy_time_nano = $destroyTimeNano
        observed_event_count = [int]$snapshot.event_count
        prefix_length = [int64]$snapshot.complete_byte_length
        prefix_sha256 = [string]$snapshot.complete_sha256
    }
}

function Assert-DockerEventContract {
    param(
        [Parameter(Mandatory = $true)][object] $Witness,
        [Parameter(Mandatory = $true)][object] $InitialBarrier,
        [Parameter(Mandatory = $true)][object] $FinalBarrier,
        [Parameter(Mandatory = $true)][DateTimeOffset] $ExpectedStartedAt
    )
    $dockerCli = Get-PinnedApplicationBoundary `
        -LiteralPath $script:dockerExecutable `
        -ExpectedSha256 $script:dockerExecutableSha256 `
        -Label 'sealed Docker event witness'
    if (
        $Witness.docker_executable_path -cne $dockerCli.path -or
        $Witness.docker_executable_sha256 -cne $dockerCli.sha256 -or
        $Witness.docker_executable_bytes -ne $dockerCli.bytes
    ) {
        throw 'The Docker event witness executable identity drifted.'
    }
    $snapshot = Get-DockerEventJournalSnapshot -Witness $Witness -AllowStopped
    if (
        -not [bool]$Witness.intentionally_stopped -or
        -not $Witness.process.HasExited -or
        $snapshot.has_partial_line -or
        [int64]$Witness.collector.StdoutBytes -ne $snapshot.complete_byte_length -or
        [string]$Witness.collector.StdoutSha256 -cne $snapshot.complete_sha256
    ) {
        throw 'The Docker event journal was not stably sealed on a line boundary.'
    }
    $postgresStarts = [Collections.Generic.List[object]]::new()
    $postgresUpdates = [Collections.Generic.List[object]]::new()
    $healthyStatusEvents = [Collections.Generic.List[object]]::new()
    $volumeMounts = [Collections.Generic.List[object]]::new()
    $networkConnectIds = [Collections.Generic.List[string]]::new()
    $networkConnectEvents = [Collections.Generic.List[object]]::new()
    $execGroups = @{}
    $allowedBarrierIds = @($InitialBarrier.container_id, $FinalBarrier.container_id)
    foreach ($event in $snapshot.events) {
        $type = Get-OptionalPropertyValue -InputObject $event -Name 'Type'
        $action = Get-OptionalPropertyValue -InputObject $event -Name 'Action'
        $actor = Get-OptionalPropertyValue -InputObject $event -Name 'Actor'
        $timeNano = Get-OptionalPropertyValue -InputObject $event -Name 'timeNano'
        Assert-NativeJsonString -Value $type -Label 'continuous Docker event Type'
        Assert-NativeJsonString -Value $action -Label 'continuous Docker event Action'
        Assert-NativeJsonObject -Value $actor -Label 'continuous Docker event Actor'
        Assert-NativeJsonInteger -Value $timeNano -Label 'continuous Docker event timeNano'
        Assert-NativeJsonString -Value $actor.ID -Label 'continuous Docker event Actor.ID'
        if ($type -ceq 'container' -and $actor.ID -cin $allowedBarrierIds) {
            if ($action -cnotin @('create', 'destroy')) {
                throw 'A barrier container emitted an unexpected Docker event.'
            }
            continue
        }
        if ($type -ceq 'container' -and $actor.ID -ceq $productionPostgresId) {
            $baseAction = ([string]$action -split ':', 2)[0]
            if ($baseAction -ceq 'start') {
                Assert-NativeJsonObject -Value $actor.Attributes `
                    -Label 'PostgreSQL start event attributes'
                if ($action -cne 'start' -or
                    $actor.Attributes.name -cne 'weatherpoly-postgres-1' -or
                    $actor.Attributes.image -cne $postgres16Image) {
                    throw 'The retained PostgreSQL start event identity drifted.'
                }
                $postgresStarts.Add($event)
                continue
            }
            if ($baseAction -ceq 'update') {
                Assert-NativeJsonObject -Value $actor.Attributes `
                    -Label 'PostgreSQL update event attributes'
                if ($action -cne 'update' -or
                    $actor.Attributes.name -cne 'weatherpoly-postgres-1' -or
                    $actor.Attributes.image -cne $postgres16Image) {
                    throw 'The retained PostgreSQL update event identity drifted.'
                }
                $postgresUpdates.Add($event)
                continue
            }
            if ($baseAction -cin @('exec_create', 'exec_start', 'exec_die')) {
                Assert-NativeJsonObject -Value $actor.Attributes `
                    -Label 'PostgreSQL exec event attributes'
                $execId = [string](Get-OptionalPropertyValue `
                    -InputObject $actor.Attributes -Name 'execID')
                if ($execId -notmatch '^[0-9a-f]{64}$') {
                    throw 'PostgreSQL exec event did not have an exact execID.'
                }
                if (-not $execGroups.ContainsKey($execId)) {
                    $execGroups[$execId] = [Collections.Generic.List[object]]::new()
                }
                $execGroups[$execId].Add([pscustomobject]@{
                    base_action = $baseAction
                    action = [string]$action
                    attributes = $actor.Attributes
                    time_nano = [int64]$timeNano
                })
                continue
            }
            if ($baseAction -ceq 'health_status') {
                Assert-NativeJsonObject -Value $actor.Attributes `
                    -Label 'PostgreSQL health-status event attributes'
                if (
                    $action -cne 'health_status: healthy' -or
                    $actor.Attributes.name -cne 'weatherpoly-postgres-1' -or
                    $actor.Attributes.image -cne $postgres16Image
                ) {
                    throw 'PostgreSQL emitted a non-healthy or non-exact health-status event.'
                }
                $healthyStatusEvents.Add($event)
                continue
            }
            throw "Retained PostgreSQL emitted a forbidden Docker event: $action"
        }
        if ($type -ceq 'network') {
            Assert-NativeJsonObject -Value $actor.Attributes `
                -Label 'continuous Docker network event attributes'
            $expectedNetworkName = @($productionNetworks.GetEnumerator() |
                Where-Object { [string]$_.Value.id -ceq [string]$actor.ID }
            )
            if (
                $action -cne 'connect' -or
                [string]$actor.Attributes.container -cne $productionPostgresId -or
                [string]$actor.ID -cnotin @($productionNetworks.Values.id) -or
                $expectedNetworkName.Count -ne 1 -or
                [string]$actor.Attributes.name -cne [string]$expectedNetworkName[0].Key -or
                [string]$actor.Attributes.type -cne 'bridge'
            ) {
                throw 'The PostgreSQL network event contract drifted.'
            }
            $networkConnectIds.Add([string]$actor.ID)
            $networkConnectEvents.Add($event)
            continue
        }
        if ($type -ceq 'volume') {
            Assert-NativeJsonObject -Value $actor.Attributes `
                -Label 'continuous Docker volume event attributes'
            Assert-ExactProperties -Value $actor.Attributes -Expected @(
                'container', 'destination', 'driver', 'propagation', 'read/write'
            ) -Label 'continuous Docker volume event attributes'
            if (
                $action -cne 'mount' -or
                [string]$actor.ID -cne 'weatherpoly_stratus-postgres' -or
                [string]$actor.Attributes.container -cne $productionPostgresId -or
                [string]$actor.Attributes.destination -cne '/var/lib/postgresql/data' -or
                [string]$actor.Attributes.driver -cne 'local' -or
                -not [string]::IsNullOrEmpty([string]$actor.Attributes.propagation) -or
                [string]$actor.Attributes.'read/write' -cne 'true'
            ) {
                throw 'The retained PostgreSQL data-volume mount event drifted.'
            }
            $volumeMounts.Add($event)
            continue
        }
        throw 'The continuous Docker witness observed an unexpected global event.'
    }
    if ($postgresStarts.Count -ne 1) {
        throw 'Transition requires exactly one retained PostgreSQL start event.'
    }
    if ($postgresUpdates.Count -ne 1) {
        throw 'Transition requires exactly one retained PostgreSQL restart-policy update event.'
    }
    if ($healthyStatusEvents.Count -ne 1) {
        throw 'Transition requires exactly one healthy PostgreSQL status event.'
    }
    if ($volumeMounts.Count -ne 1) {
        throw 'Transition requires exactly one retained PostgreSQL data-volume mount event.'
    }
    $startTimeNano = [int64]$postgresStarts[0].timeNano
    $updateTimeNano = [int64]$postgresUpdates[0].timeNano
    $healthyTimeNano = [int64]$healthyStatusEvents[0].timeNano
    $volumeMountTimeNano = [int64]$volumeMounts[0].timeNano
    $startMilliseconds = [int64]([Math]::Floor(([double]$startTimeNano) / 1000000.0))
    if (
        [Math]::Abs($startMilliseconds - $ExpectedStartedAt.ToUnixTimeMilliseconds()) -gt 1000 -or
        $startTimeNano -le [int64]$InitialBarrier.destroy_time_nano -or
        $startTimeNano -ge [int64]$FinalBarrier.create_time_nano -or
        $updateTimeNano -le [int64]$InitialBarrier.destroy_time_nano -or
        $updateTimeNano -ge $volumeMountTimeNano -or
        $volumeMountTimeNano -le [int64]$InitialBarrier.destroy_time_nano -or
        $volumeMountTimeNano -ge $startTimeNano -or
        $healthyTimeNano -le $startTimeNano -or
        $healthyTimeNano -ge [int64]$FinalBarrier.create_time_nano
    ) {
        throw 'The witnessed PostgreSQL start is outside the exact transition barriers.'
    }
    $actualConnectIds = $networkConnectIds.ToArray()
    [Array]::Sort($actualConnectIds, [StringComparer]::Ordinal)
    $expectedConnectIds = [string[]]@($productionNetworks.Values.id)
    [Array]::Sort($expectedConnectIds, [StringComparer]::Ordinal)
    if (($actualConnectIds -join "`n") -cne ($expectedConnectIds -join "`n")) {
        throw 'Transition did not witness exactly one connect to each production network.'
    }
    foreach ($connectEvent in $networkConnectEvents) {
        if (
            [int64]$connectEvent.timeNano -le $volumeMountTimeNano -or
            [int64]$connectEvent.timeNano -ge $startTimeNano
        ) {
            throw 'PostgreSQL volume/connect/start event ordering is invalid.'
        }
    }
    $gateExecCount = 0
    $healthExecCount = 0
    $gateClasses = [ordered]@{
        sha256 = 0
        reload = 0
        fresh_hba = 0
        snapshot = 0
    }
    $gateSequence = [Collections.Generic.List[object]]::new()
    foreach ($execId in @($execGroups.Keys)) {
        $group = @($execGroups[$execId])
        $baseActions = @($group.base_action)
        [Array]::Sort($baseActions, [StringComparer]::Ordinal)
        if (($baseActions -join "`n") -cne "exec_create`nexec_die`nexec_start") {
            throw 'A PostgreSQL exec did not have one exact create/start/die triplet.'
        }
        $createEvent = @($group | Where-Object {
            $_.base_action -ceq 'exec_create'
        })[0]
        $startEvent = @($group | Where-Object {
            $_.base_action -ceq 'exec_start'
        })[0]
        $dieEvent = @($group | Where-Object {
            $_.base_action -ceq 'exec_die'
        })[0]
        $createAction = [string]$createEvent.action
        $dieAttributes = $dieEvent.attributes
        $exitCode = [string](Get-OptionalPropertyValue `
            -InputObject $dieAttributes -Name 'exitCode')
        if (
            $exitCode -cne '0' -or
            [int64]$createEvent.time_nano -le $startTimeNano -or
            [int64]$createEvent.time_nano -ge [int64]$startEvent.time_nano -or
            [int64]$startEvent.time_nano -ge [int64]$dieEvent.time_nano -or
            [int64]$dieEvent.time_nano -ge [int64]$FinalBarrier.create_time_nano
        ) {
            throw 'A PostgreSQL exec did not have exact successful in-barrier event ordering.'
        }
        if ($createAction -ceq
            'exec_create: /bin/sh -c pg_isready -U stratus -d stratus') {
            $healthExecCount += 1
        }
        elseif ($createAction -ceq
            'exec_create: sha256sum /etc/postgresql/stratus-pg_hba.conf') {
            $gateClasses.sha256 += 1
            $gateExecCount += 1
            $gateSequence.Add([pscustomobject]@{
                class = 'sha256'
                create_time_nano = [int64]$createEvent.time_nano
            })
        }
        elseif ($createAction -match 'pg_reload_conf\(\)::text') {
            $gateClasses.reload += 1
            $gateExecCount += 1
            $gateSequence.Add([pscustomobject]@{
                class = 'reload'
                create_time_nano = [int64]$createEvent.time_nano
            })
        }
        elseif ($createAction -match "SELECT 'META\|") {
            $gateClasses.fresh_hba += 1
            $gateExecCount += 1
            $gateSequence.Add([pscustomobject]@{
                class = 'fresh_hba'
                create_time_nano = [int64]$createEvent.time_nano
            })
        }
        elseif ($createAction -match
            'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY') {
            $gateClasses.snapshot += 1
            $gateExecCount += 1
            $gateSequence.Add([pscustomobject]@{
                class = 'snapshot'
                create_time_nano = [int64]$createEvent.time_nano
            })
        }
        else {
            throw 'The witness observed an unowned PostgreSQL exec command.'
        }
    }
    if (
        $gateExecCount -ne $script:transitionExpectedPostgresExecCount -or
        $gateExecCount -ne 8 -or
        $gateClasses.sha256 -ne 3 -or
        $gateClasses.reload -ne 1 -or
        $gateClasses.fresh_hba -ne 2 -or
        $gateClasses.snapshot -ne 2 -or
        $healthExecCount -lt 1 -or $healthExecCount -gt 64
    ) {
        throw 'PostgreSQL exec event counts do not match the exact transition commands.'
    }
    $orderedGateSequence = @($gateSequence.ToArray() | Sort-Object `
        -Property @{ Expression = { [int64]$_.create_time_nano } })
    $expectedGateSequence = @(
        'sha256', 'reload', 'fresh_hba', 'sha256',
        'snapshot', 'snapshot', 'fresh_hba', 'sha256'
    )
    if (
        $orderedGateSequence.Count -ne $expectedGateSequence.Count -or
        (@($orderedGateSequence.class) -join "`n") -cne
            ($expectedGateSequence -join "`n")
    ) {
        throw 'PostgreSQL gate exec commands did not occur in the exact reviewed sequence.'
    }
    for ($sequenceIndex = 1; $sequenceIndex -lt $orderedGateSequence.Count;
        $sequenceIndex += 1) {
        if (
            [int64]$orderedGateSequence[$sequenceIndex].create_time_nano -le
                [int64]$orderedGateSequence[$sequenceIndex - 1].create_time_nano
        ) {
            throw 'PostgreSQL gate exec sequence timeNano values are not strictly ordered.'
        }
    }
    return [pscustomobject]@{
        session_id = [string]$Witness.session_id
        process_id = [int]$Witness.process_id
        process_started_at = [string]$Witness.process_started_at
        docker_executable_path = [string]$dockerCli.path
        docker_executable_sha256 = [string]$dockerCli.sha256
        docker_executable_bytes = [int64]$dockerCli.bytes
        collector_type_name = [string]$Witness.collector_type_name
        collector_contract_identity = [string]$Witness.collector_contract_identity
        collector_template_sha256 = [string]$Witness.collector_template_sha256
        collector_source_sha256 = [string]$Witness.collector_source_sha256
        collector_assembly_full_name_sha256 =
            [string]$Witness.collector_assembly_full_name_sha256
        collector_assembly_is_dynamic = [bool]$Witness.collector_assembly_is_dynamic
        collector_module_version_id = [string]$Witness.collector_module_version_id
        journal_bytes = [int64]$snapshot.complete_byte_length
        journal_sha256 = [string]$snapshot.complete_sha256
        event_count = [int]$snapshot.event_count
        postgres_start_count = 1
        postgres_start_time_nano = $startTimeNano
        postgres_restart_policy_update_count = 1
        postgres_restart_policy_update_time_nano = $updateTimeNano
        postgres_attach_count = 0
        network_connect_count = $actualConnectIds.Count
        network_ids = $actualConnectIds
        data_volume_mount_count = 1
        data_volume_mount_time_nano = $volumeMountTimeNano
        gate_exec_triplets = $gateExecCount
        gate_exec_classes = [pscustomobject]$gateClasses
        gate_exec_sequence = $orderedGateSequence
        health_exec_triplets = $healthExecCount
        healthy_status_event_count = 1
        healthy_status_time_nano = $healthyTimeNano
        reconnect_count = 0
        eof_before_seal = $false
        truncated = $false
        initial_barrier = $InitialBarrier
        final_barrier = $FinalBarrier
    }
}

function Assert-FinalLiveBoundary {
    param(
        [Parameter(Mandatory = $true)][object] $InitialPostgres,
        [Parameter(Mandatory = $true)][object] $InitialDockerEngine,
        [Parameter(Mandatory = $true)][object] $InitialHba,
        [Parameter(Mandatory = $true)][DateTimeOffset] $PreStartExpiresAt
    )
    $finalPostgres = Get-PostgresBoundary -ExpectedState Running `
        -ExpectedRestartPolicy 'no'
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
        'restart_policy', 'health_status', 'network_mode',
        'mount_contract_sha256', 'ports_contract_sha256',
        'network_contract_sha256', 'active_endpoint_sha256'
    )) {
        if ($finalPostgres.$name -cne $InitialPostgres.$name) {
            throw 'Retained PostgreSQL identity or network endpoints drifted during Live proof.'
        }
    }
    $hbaPath = Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    [void](Assert-RegularFile -LiteralPath $hbaPath)
    $freshHbaState = Get-FreshHbaState -Label 'final fresh HBA state'
    foreach ($name in @(
        'conf_load_time', 'postmaster_started_at', 'hba_file',
        'inventory_sha256', 'inventory_line_count',
        'inventory_rule_count', 'inventory_errors'
    )) {
        if ($freshHbaState.$name -cne $InitialHba.fresh_loaded_state.$name) {
            throw 'Loaded PostgreSQL HBA generation drifted after database snapshots.'
        }
    }
    $finalFreshBackendStartedAt = ConvertTo-RequiredUtcTime `
        -Value $freshHbaState.backend_started_at -Label 'final fresh HBA backend start'
    $reloadAcknowledgedAt = ConvertTo-RequiredUtcTime `
        -Value $InitialHba.reload_acknowledged_at -Label 'accepted HBA reload time'
    if ($finalFreshBackendStartedAt -lt $reloadAcknowledgedAt) {
        throw 'Final fresh PostgreSQL session predates the accepted HBA reload.'
    }
    $hostLockBoundary = Get-HeldHbaReadBoundary
    $hostSha256 = [string]$hostLockBoundary.sha256
    $mountedSha256 = Get-MountedHbaSha256
    if ($hostSha256 -cne $fixedHbaSha256 -or $mountedSha256 -cne $fixedHbaSha256) {
        throw 'HBA bytes drifted after the successor database snapshots.'
    }
    $finalDockerEngine = Get-DockerEngineBoundary
    if ($finalDockerEngine.contract_sha256 -cne $InitialDockerEngine.contract_sha256) {
        throw 'Docker context or engine identity drifted during the transition.'
    }
    $finalTopology = Assert-ExclusiveRuntimeTopology -PostgresState Running
    $checkedAt = [DateTimeOffset]::UtcNow
    if ($checkedAt -gt $PreStartExpiresAt.AddSeconds($clockSkewSeconds)) {
        throw 'Successor PreStart authorization expired before final acceptance.'
    }
    return [pscustomobject]@{
        checked_at = $checkedAt.ToString('o')
        postgres = $finalPostgres
        topology = $finalTopology
        docker_engine = $finalDockerEngine
        hba_host_sha256 = $hostSha256
        hba_host_read_lock_held = [bool]$hostLockBoundary.held
        hba_host_read_lock_path = [string]$hostLockBoundary.path
        hba_mounted_sha256 = $mountedSha256
        fresh_hba_state = $freshHbaState
        endpoints_unchanged = $true
    }
}

function Get-StrictLiveSnapshot {
    $queryCommand = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
WITH expected_indexes(
  index_name, table_name, table_regclass, key_names, key_attnums, opclass_names,
  collation_names, is_unique, normalized_predicate
) AS (
  VALUES
    ('ix_accuracy_predictions_eligible_expert_ts'::text, 'accuracy_predictions'::text, 'public.accuracy_predictions'::text, ARRAY['expert','ts']::text[], ARRAY[4,3]::smallint[], ARRAY['pg_catalog.text_ops','pg_catalog.timestamptz_ops']::text[], ARRAY['pg_catalog.default','']::text[], false, 'evidence_eligible IS TRUE'::text),
    ('ix_accuracy_predictions_eligible_expert_version_market_ts'::text, 'accuracy_predictions'::text, 'public.accuracy_predictions'::text, ARRAY['expert','expert_version','market_id','ts']::text[], ARRAY[4,5,2,3]::smallint[], ARRAY['pg_catalog.text_ops','pg_catalog.text_ops','pg_catalog.int8_ops','pg_catalog.timestamptz_ops']::text[], ARRAY['pg_catalog.default','pg_catalog.default','','']::text[], false, 'evidence_eligible IS TRUE'::text),
    ('ix_model_skill_window_evaluated'::text, 'model_skill_reports'::text, 'public.model_skill_reports'::text, ARRAY['window_days','evaluated_at']::text[], ARRAY[7,18]::smallint[], ARRAY['pg_catalog.int2_ops','pg_catalog.timestamptz_ops']::text[], ARRAY['','']::text[], false, ''::text),
    ('ix_signals_pending_stop_cleanup'::text, 'signals'::text, 'public.signals'::text, ARRAY['id']::text[], ARRAY[1]::smallint[], ARRAY['pg_catalog.int8_ops']::text[], ARRAY['']::text[], false, 'status = ''pending''::signal_status'::text),
    ('ix_orders_live_open_stop_cleanup'::text, 'orders'::text, 'public.orders'::text, ARRAY['id']::text[], ARRAY[1]::smallint[], ARRAY['pg_catalog.int8_ops']::text[], ARRAY['']::text[], false, 'mode = ''live''::execution_mode AND (status::text = ANY (ARRAY[''open''::character varying, ''pending''::character varying]::text[]))'::text),
    ('uq_paper_canary_single_active_lease'::text, 'paper_canary_safety_leases'::text, 'public.paper_canary_safety_leases'::text, ARRAY['state']::text[], ARRAY[12]::smallint[], ARRAY['pg_catalog.text_ops']::text[], ARRAY['pg_catalog.default']::text[], true, 'state::text = ''active''::text'::text),
    ('ix_paper_canary_active_expiry'::text, 'paper_canary_safety_leases'::text, 'public.paper_canary_safety_leases'::text, ARRAY['expires_at','id']::text[], ARRAY[16,1]::smallint[], ARRAY['pg_catalog.timestamptz_ops','pg_catalog.int8_ops']::text[], ARRAY['','']::text[], false, 'state::text = ''active''::text'::text)
), actual_indexes AS (
  SELECT
    index_relation.relname::text AS index_name,
    table_relation.relname::text AS table_name,
    table_relation.oid AS table_oid,
    index_namespace.nspname::text AS index_namespace,
    table_namespace.nspname::text AS table_namespace,
    access_method.amname::text AS access_method,
    ARRAY(
      SELECT attribute.attname::text
      FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      JOIN pg_attribute attribute
        ON attribute.attrelid=index_row.indrelid
       AND attribute.attnum=index_row.indkey[position]
      ORDER BY position
    )::text[] AS key_names,
    ARRAY(
      SELECT index_row.indkey[position]::smallint
      FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      ORDER BY position
    )::smallint[] AS key_attnums,
    ARRAY(
      SELECT opclass_namespace.nspname::text || '.' || opclass.opcname::text
      FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      JOIN pg_opclass opclass ON opclass.oid=index_row.indclass[position]
      JOIN pg_namespace opclass_namespace ON opclass_namespace.oid=opclass.opcnamespace
      ORDER BY position
    )::text[] AS opclass_names,
    ARRAY(
      SELECT coalesce(collation_namespace.nspname::text || '.' || collation_row.collname::text, '')
      FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      LEFT JOIN pg_collation collation_row ON collation_row.oid=index_row.indcollation[position]
      LEFT JOIN pg_namespace collation_namespace ON collation_namespace.oid=collation_row.collnamespace
      ORDER BY position
    )::text[] AS collation_names,
    NOT EXISTS (
      SELECT 1 FROM generate_series(0, index_row.indnkeyatts - 1) AS position
      WHERE index_row.indoption[position]<>0
    ) AS key_options_zero,
    index_row.indisunique AS is_unique,
    replace(
      replace(
        btrim(coalesce(pg_get_expr(index_row.indpred, index_row.indrelid, true), '')),
        E'\r\n', E'\n'
      ),
      E'\r', E'\n'
    ) AS normalized_predicate,
    index_relation.relkind::text AS relkind,
    table_relation.relkind::text AS table_relkind,
    pg_get_userbyid(index_relation.relowner)::text AS index_owner,
    pg_get_userbyid(table_relation.relowner)::text AS table_owner,
    index_relation.relpersistence::text AS index_persistence,
    table_relation.relpersistence::text AS table_persistence,
    index_relation.reloptions IS NULL AS default_reloptions,
    index_relation.reltablespace=0 AS default_tablespace,
    index_row.indnkeyatts,
    index_row.indnatts,
    index_row.indexprs IS NULL AS no_expressions,
    NOT index_row.indnullsnotdistinct AS nulls_distinct,
    NOT index_row.indisprimary AS not_primary,
    NOT index_row.indisexclusion AS not_exclusion,
    NOT index_row.indisclustered AS not_clustered,
    NOT index_row.indisreplident AS not_replica_identity,
    index_row.indimmediate AS immediate,
    NOT index_row.indcheckxmin AS no_check_xmin,
    index_row.indisvalid,
    index_row.indisready,
    index_row.indislive
  FROM pg_index AS index_row
  JOIN pg_class AS index_relation ON index_relation.oid=index_row.indexrelid
  JOIN pg_class AS table_relation ON table_relation.oid=index_row.indrelid
  JOIN pg_namespace AS index_namespace ON index_namespace.oid=index_relation.relnamespace
  JOIN pg_namespace AS table_namespace ON table_namespace.oid=table_relation.relnamespace
  JOIN pg_am AS access_method ON access_method.oid=index_relation.relam
  WHERE index_namespace.nspname='public'
    AND index_relation.relname IN (SELECT index_name FROM expected_indexes)
), expected_functions(
  signature, language_name, volatility, security_definer, acl_class,
  return_type, returns_set, default_count, normalized_prosrc_sha256
) AS (
  VALUES
    ('public.paper_canary_fence_armed()'::text, 'sql'::text, 's'::char, true, 'R'::char, 'boolean'::text, false, 0, 'bf52f1b1ec94ff12c0d86fe2021370784dd9de97ad2bea79856aa624444fa142'::text),
    ('public.paper_canary_active_lease_exists()'::text, 'sql'::text, 's'::char, true, 'R'::char, 'boolean'::text, false, 0, '977b394af6c45c274bc4d355689cfae324efa2ad22cde4a03b77c79f4a58c123'::text),
    ('public.paper_canary_execution_authorized()'::text, 'sql'::text, 'v'::char, true, 'R'::char, 'boolean'::text, false, 0, 'd7f0ac33344baf5003601ac475004e681f225e5658c8bba2a3c8bf28b7cb47d3'::text),
    ('public.enforce_paper_canary_lease_audit()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '65164a0ea9ccf00c3c3cd72cccc678c51a3cbb5fb7deabd03ca441dc20e0aa99'::text),
    ('public.enforce_paper_canary_runtime_fence()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '44803e43e5e154aa556967816606d26ceb64d9a0c3f4af5c694329ff5ca7ad73'::text),
    ('public.enforce_paper_canary_venue_fence()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '1d0ecc9b099132017b8f09cf4e04d53c2da780c83c5c0648a57d36742cc567a3'::text),
    ('public.enforce_paper_canary_execution_commit_fence()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '754e9608f08f770c719e7c19d0c40ace1ccdcbae3394e03d127a2a109515a7e5'::text),
    ('public.enforce_paper_canary_execution_identity_fence()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, 'd9bf051176bec6d91fe9aea70a65fe5e369cd9aa5d6d7ff8a5240aac0eaea2c4'::text),
    ('public.paper_canary_stop_request_reserved(text)'::text, 'sql'::text, 's'::char, true, 'R'::char, 'boolean'::text, false, 0, 'c1091906ce61eb1b37348c5ce45191c1e6324dfb4aa16af61acee2128352d544'::text),
    ('public.paper_canary_resume_request_reserved(text)'::text, 'sql'::text, 's'::char, true, 'R'::char, 'boolean'::text, false, 0, '6f2fa5884b5e71a8c4b14e8708dbfd89ec9200195050ae1a382d4dfa43a48fd0'::text),
    ('public.paper_canary_resume_command_authorized(text,text,text)'::text, 'sql'::text, 'v'::char, true, 'R'::char, 'boolean'::text, false, 0, '1444bebaf12329a34fc1f2ec70e1f5c596ffca44da18472ea4433aa23e8517e3'::text),
    ('public.enforce_paper_canary_request_reservation()'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'trigger'::text, false, 0, '6ee1c90a5e9c0476742618ebdfc4037880a2f9f9f6f172d526aa06611596e203'::text),
    ('public.engage_paper_canary_runtime_stop(text,text,text)'::text, 'plpgsql'::text, 'v'::char, true, 'R'::char, 'integer'::text, false, 0, 'b213ba135e4157546d28358401532024043e2b9952f5b11b0092606a445aca3b'::text),
    ('public.resume_paper_canary_exact(bigint)'::text, 'plpgsql'::text, 'v'::char, true, 'R'::char, 'boolean'::text, false, 0, 'b5291fe09209d8446c66dfc49a9e8e2c1d96e9ce44524188d4230e3a461c7b5c'::text),
    ('public.paper_canary_stop_core(text,text,text,text)'::text, 'plpgsql'::text, 'v'::char, false, 'O'::char, 'record'::text, true, 0, '962b8300848aa28d316241641a3e7ee8649e0f5d159ec4d519ddfd7262e9f5b6'::text),
    ('public.safety_paper_canary_stop(text,text,text)'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'record'::text, true, 0, '5ece0778821e57436e9a087537e81a1e6321bce43e4399d1d10923667bd8d215'::text),
    ('public.safety_paper_canary_ready()'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'boolean'::text, false, 0, 'c25ebf350b5e9fbbfdcedaa4c484202ee92321b5b399caf1f56d84f9218d9d75'::text),
    ('public.safety_paper_canary_acquire(text,text,text,text,text,text,text,text,text,integer)'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'record'::text, true, 0, 'd56836d4c3f0e003d5da0c04f56a26456dd727b12fa2612f63723ab778d6c0b2'::text),
    ('public.safety_paper_canary_heartbeat(text,text,text,integer)'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'bigint'::text, false, 0, '5d45bb7f68ae615a6a2ca88daf3c76ef4f1ed084457da6ee0649531bcabe3eeb'::text),
    ('public.safety_paper_canary_release(text,text,text,text)'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'record'::text, true, 0, 'f64005d57f0515e51b3308f16f069ac81e3d783de8abaee5fc763cd91511809d'::text),
    ('public.safety_paper_canary_watchdog_tick()'::text, 'plpgsql'::text, 'v'::char, true, 'S'::char, 'record'::text, true, 0, '2b4cd3aac40815f091a1c04d52e0e4f3aeda7a35422936b288e2582536fc6c45'::text),
    ('public.assert_stratus_safety_boundary(boolean)'::text, 'plpgsql'::text, 'v'::char, false, 'S'::char, 'boolean'::text, false, 1, '888dd128fc696622c7b97beddfedcbc4a57b52856c73a09c0af6ebc58be451f9'::text),
    ('public.assert_stratus_runtime_boundary_v2(boolean)'::text, 'plpgsql'::text, 'v'::char, false, 'R'::char, 'boolean'::text, false, 1, '8ae11290ef847a82769ee6a87cc5a2f14a9ed66e59ee230139b63e50487c0349'::text),
    ('public.assert_stratus_runtime_boundary(boolean)'::text, 'plpgsql'::text, 'v'::char, false, 'R'::char, 'boolean'::text, false, 1, '5aa4d8d0e5a575284a4a3ac717ca07dd1cda9b109b9ec3ae84c02f6f89f6da6d'::text)
), expected_function_shapes(signature, arguments, function_result) AS (
  VALUES
    ('public.paper_canary_fence_armed()'::text, ''::text, 'boolean'::text),
    ('public.paper_canary_active_lease_exists()'::text, ''::text, 'boolean'::text),
    ('public.paper_canary_execution_authorized()'::text, ''::text, 'boolean'::text),
    ('public.enforce_paper_canary_lease_audit()'::text, ''::text, 'trigger'::text),
    ('public.enforce_paper_canary_runtime_fence()'::text, ''::text, 'trigger'::text),
    ('public.enforce_paper_canary_venue_fence()'::text, ''::text, 'trigger'::text),
    ('public.enforce_paper_canary_execution_commit_fence()'::text, ''::text, 'trigger'::text),
    ('public.enforce_paper_canary_execution_identity_fence()'::text, ''::text, 'trigger'::text),
    ('public.paper_canary_stop_request_reserved(text)'::text, 'requested_id text'::text, 'boolean'::text),
    ('public.paper_canary_resume_request_reserved(text)'::text, 'requested_id text'::text, 'boolean'::text),
    ('public.paper_canary_resume_command_authorized(text,text,text)'::text, 'requested_id text, requested_actor text, requested_reason text'::text, 'boolean'::text),
    ('public.enforce_paper_canary_request_reservation()'::text, ''::text, 'trigger'::text),
    ('public.engage_paper_canary_runtime_stop(text,text,text)'::text, 'requested_id text, requested_actor text, requested_reason text'::text, 'integer'::text),
    ('public.resume_paper_canary_exact(bigint)'::text, 'requested_command_id bigint'::text, 'boolean'::text),
    ('public.paper_canary_stop_core(text,text,text,text)'::text, 'requested_id text, requested_actor text, requested_reason text, lease_terminal_state text'::text, 'TABLE(command_id bigint, result_created boolean, result_idempotent boolean, expired_signals integer, cancel_requested_live_orders integer, rejected_pending_resume integer)'::text),
    ('public.safety_paper_canary_stop(text,text,text)'::text, 'requested_id text, requested_actor text, requested_reason text'::text, 'TABLE(command_id bigint, command_status text, command_detail text, command_created_at timestamp with time zone, command_executed_at timestamp with time zone, result_created boolean, result_idempotent boolean, expired_signals integer, cancel_requested_live_orders integer, rejected_pending_resume integer)'::text),
    ('public.safety_paper_canary_ready()'::text, ''::text, 'boolean'::text),
    ('public.safety_paper_canary_acquire(text,text,text,text,text,text,text,text,text,integer)'::text, 'input_lease_id text, input_run_id text, input_acquire_request_id text, input_resume_request_id text, input_direct_stop_request_id text, input_release_request_id text, input_resume_actor text, input_resume_reason text, input_nonce_sha256 text, input_ttl_seconds integer'::text, 'TABLE(lease_pk bigint, result_created boolean, result_idempotent boolean)'::text),
    ('public.safety_paper_canary_heartbeat(text,text,text,integer)'::text, 'input_lease_id text, input_run_id text, input_nonce_sha256 text, input_ttl_seconds integer'::text, 'bigint'::text),
    ('public.safety_paper_canary_release(text,text,text,text)'::text, 'input_lease_id text, input_run_id text, input_nonce_sha256 text, input_release_request_id text'::text, 'TABLE(lease_pk bigint, result_created boolean, result_idempotent boolean)'::text),
    ('public.safety_paper_canary_watchdog_tick()'::text, ''::text, 'TABLE(lease_pk bigint, terminalized boolean)'::text),
    ('public.assert_stratus_safety_boundary(boolean)'::text, 'require_login boolean DEFAULT true'::text, 'boolean'::text),
    ('public.assert_stratus_runtime_boundary_v2(boolean)'::text, 'require_login boolean DEFAULT true'::text, 'boolean'::text),
    ('public.assert_stratus_runtime_boundary(boolean)'::text, 'require_login boolean DEFAULT true'::text, 'boolean'::text)
), expected_control_triggers(relation_name, trigger_name, function_signature, trigger_type) AS (
  VALUES
    ('paper_canary_safety_leases'::text, 'trg_paper_canary_lease_audit'::text, 'public.enforce_paper_canary_lease_audit()'::text, 31::smallint),
    ('runtime_control'::text, 'trg_paper_canary_runtime_fence'::text, 'public.enforce_paper_canary_runtime_fence()'::text, 31::smallint),
    ('venue_runtime_control'::text, 'trg_paper_canary_venue_fence'::text, 'public.enforce_paper_canary_venue_fence()'::text, 31::smallint),
    ('operator_commands'::text, 'trg_paper_canary_request_reservation'::text, 'public.enforce_paper_canary_request_reservation()'::text, 31::smallint)
), expected_commit_triggers(relation_name, trigger_name, trigger_type) AS (
  VALUES
    ('signals'::text, 'trg_paper_canary_signal_commit_fence'::text, 21::smallint),
    ('orders'::text, 'trg_paper_canary_order_commit_fence'::text, 21::smallint),
    ('execution_order_intents'::text, 'trg_paper_canary_intent_commit_fence'::text, 5::smallint),
    ('execution_fill_legs'::text, 'trg_paper_canary_fill_leg_commit_fence'::text, 5::smallint),
    ('positions'::text, 'trg_paper_canary_position_commit_fence'::text, 21::smallint)
), expected_identity_triggers(relation_name, trigger_name, trigger_type) AS (
  VALUES
    ('bankroll_log'::text, 'trg_paper_canary_bankroll_identity_fence'::text, 31::smallint),
    ('orders'::text, 'trg_paper_canary_order_identity_fence'::text, 27::smallint),
    ('positions'::text, 'trg_paper_canary_position_identity_fence'::text, 27::smallint),
    ('signals'::text, 'trg_paper_canary_signal_identity_fence'::text, 27::smallint)
)
SELECT json_build_object(
  'database_clock', clock_timestamp(),
  'schema_head_count', (SELECT count(*) FROM alembic_version),
  'schema_head', (SELECT min(version_num) FROM alembic_version),
  'indexes_exact', (
    (SELECT count(*) FROM actual_indexes)=7
    AND NOT EXISTS (
      SELECT 1 FROM expected_indexes expected
      LEFT JOIN actual_indexes actual USING(index_name)
       WHERE actual.index_name IS NULL
          OR actual.table_name IS DISTINCT FROM expected.table_name
          OR actual.table_oid IS DISTINCT FROM to_regclass(expected.table_regclass)::oid
          OR actual.index_namespace IS DISTINCT FROM 'public'
          OR actual.table_namespace IS DISTINCT FROM 'public'
          OR actual.access_method IS DISTINCT FROM 'btree'
          OR actual.key_names IS DISTINCT FROM expected.key_names
          OR actual.key_attnums IS DISTINCT FROM expected.key_attnums
          OR actual.opclass_names IS DISTINCT FROM expected.opclass_names
          OR actual.collation_names IS DISTINCT FROM expected.collation_names
          OR NOT actual.key_options_zero
          OR actual.is_unique IS DISTINCT FROM expected.is_unique
          OR actual.normalized_predicate IS DISTINCT FROM expected.normalized_predicate
          OR actual.relkind <> 'i'
          OR actual.table_relkind <> 'r'
          OR actual.index_owner IS DISTINCT FROM 'stratus'
          OR actual.table_owner IS DISTINCT FROM 'stratus'
          OR actual.index_persistence IS DISTINCT FROM 'p'
          OR actual.table_persistence IS DISTINCT FROM 'p'
          OR NOT actual.default_reloptions OR NOT actual.default_tablespace
          OR actual.indnkeyatts <> cardinality(expected.key_names)
          OR actual.indnatts <> cardinality(expected.key_names)
          OR NOT actual.no_expressions OR NOT actual.nulls_distinct
          OR NOT actual.not_primary OR NOT actual.not_exclusion
          OR NOT actual.not_clustered OR NOT actual.not_replica_identity
          OR NOT actual.immediate
          OR NOT actual.no_check_xmin
          OR NOT actual.indisvalid OR NOT actual.indisready OR NOT actual.indislive
      )
  ),
  'functions_exact', (
    (SELECT count(*) FROM expected_functions)=24
    AND (SELECT count(*) FROM expected_function_shapes)=24
    AND NOT EXISTS (
      SELECT 1
      FROM expected_functions expected
      JOIN expected_function_shapes shape USING(signature)
      LEFT JOIN pg_proc procedure
        ON procedure.oid=to_regprocedure(expected.signature)
      LEFT JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
      LEFT JOIN pg_language language_row ON language_row.oid=procedure.prolang
      WHERE procedure.oid IS NULL
         OR namespace.nspname IS DISTINCT FROM 'public'
         OR language_row.lanname IS DISTINCT FROM expected.language_name
         OR pg_get_userbyid(procedure.proowner) IS DISTINCT FROM 'stratus'
         OR procedure.provolatile::text IS DISTINCT FROM expected.volatility::text
         OR procedure.prosecdef IS DISTINCT FROM expected.security_definer
         OR procedure.proparallel::text IS DISTINCT FROM 'u'
         OR procedure.proleakproof OR procedure.proisstrict
         OR procedure.prokind::text IS DISTINCT FROM 'f'
         OR procedure.provariadic<>0 OR procedure.prosupport<>0
         OR procedure.prorettype::regtype::text IS DISTINCT FROM expected.return_type
         OR procedure.proretset IS DISTINCT FROM expected.returns_set
         OR procedure.pronargdefaults IS DISTINCT FROM expected.default_count
         OR (
              expected.default_count=0 AND procedure.proargdefaults IS NOT NULL
            )
         OR (
              expected.default_count=1
              AND pg_get_expr(procedure.proargdefaults, 0) IS DISTINCT FROM 'true'
            )
         OR pg_get_function_arguments(procedure.oid) IS DISTINCT FROM shape.arguments
         OR pg_get_function_result(procedure.oid) IS DISTINCT FROM shape.function_result
         OR procedure.proconfig IS DISTINCT FROM
              ARRAY['search_path=pg_catalog, public']::text[]
         OR procedure.procost<>100
         OR procedure.prorows<>CASE WHEN expected.returns_set THEN 1000 ELSE 0 END
         OR procedure.probin IS NOT NULL OR procedure.prosqlbody IS NOT NULL
         OR encode(
              sha256(
                convert_to(
                  btrim(
                    replace(
                      replace(procedure.prosrc, E'\r\n', E'\n'),
                      E'\r', E'\n'
                    ),
                    E' \t\r\n'
                  ),
                  'UTF8'
                )
              ),
              'hex'
            ) IS DISTINCT FROM expected.normalized_prosrc_sha256
         OR NOT (
              SELECT
                count(*)=CASE expected.acl_class WHEN 'O' THEN 1 ELSE 2 END
                AND count(*) FILTER (
                  WHERE privilege.grantee=procedure.proowner
                )=1
                AND count(*) FILTER (
                  WHERE privilege.grantee=to_regrole(
                    'stratus_runtime_' || md5(current_database())
                  )
                )=CASE expected.acl_class WHEN 'R' THEN 1 ELSE 0 END
                AND count(*) FILTER (
                  WHERE privilege.grantee=to_regrole(
                    'stratus_safety_' || md5(current_database())
                  )
                )=CASE expected.acl_class WHEN 'S' THEN 1 ELSE 0 END
                AND coalesce(bool_and(
                  privilege.grantor=procedure.proowner
                  AND privilege.privilege_type='EXECUTE'
                  AND NOT privilege.is_grantable
                  AND (
                    privilege.grantee=procedure.proowner
                    OR (
                      expected.acl_class='R'
                      AND privilege.grantee=to_regrole(
                        'stratus_runtime_' || md5(current_database())
                      )
                    )
                    OR (
                      expected.acl_class='S'
                      AND privilege.grantee=to_regrole(
                        'stratus_safety_' || md5(current_database())
                      )
                    )
                  )
                ), false)
              FROM aclexplode(
                coalesce(
                  procedure.proacl,
                  acldefault('f', procedure.proowner)
                )
              ) privilege
            )
    )
    AND (
      SELECT count(*)
      FROM pg_proc procedure
      JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
      WHERE namespace.nspname='public'
        AND procedure.proname IN (
          SELECT replace(split_part(signature, '(', 1), 'public.', '')
          FROM expected_functions
        )
    )=24
  ),
  'lease_table_count', (
    SELECT count(*) FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
    WHERE namespace.nspname='public'
      AND relation.relname='paper_canary_safety_leases'
      AND relation.relkind='r'
  ),
  'lease_rows', (SELECT count(*) FROM paper_canary_safety_leases),
  'active_lease_rows', (SELECT count(*) FROM paper_canary_safety_leases WHERE state='active'),
  'runtime_boundary', public.assert_stratus_runtime_boundary(false),
  'safety_boundary', public.assert_stratus_safety_boundary(false),
  'control_triggers_exact', (
    (SELECT count(*) FROM expected_control_triggers expected
      JOIN pg_namespace namespace ON namespace.nspname='public'
      JOIN pg_class relation ON relation.relnamespace=namespace.oid AND relation.relname=expected.relation_name
      JOIN pg_trigger trigger_row ON trigger_row.tgrelid=relation.oid AND trigger_row.tgname=expected.trigger_name
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgenabled IN ('O','A')
        AND trigger_row.tgtype=expected.trigger_type
        AND trigger_row.tgfoid=to_regprocedure(expected.function_signature)
        AND trigger_row.tgnargs=0 AND octet_length(trigger_row.tgargs)=0
        AND trigger_row.tgqual IS NULL)=4
    AND (SELECT count(*) FROM pg_trigger trigger_row
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgfoid IN (
          to_regprocedure('public.enforce_paper_canary_lease_audit()'),
          to_regprocedure('public.enforce_paper_canary_runtime_fence()'),
          to_regprocedure('public.enforce_paper_canary_venue_fence()'),
          to_regprocedure('public.enforce_paper_canary_request_reservation()')
        ))=4
  ),
  'commit_triggers_exact', (
    (SELECT count(*) FROM expected_commit_triggers expected
      JOIN pg_namespace namespace ON namespace.nspname='public'
      JOIN pg_class relation ON relation.relnamespace=namespace.oid AND relation.relname=expected.relation_name
      JOIN pg_trigger trigger_row ON trigger_row.tgrelid=relation.oid AND trigger_row.tgname=expected.trigger_name
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgenabled IN ('O','A')
        AND trigger_row.tgtype=expected.trigger_type
        AND trigger_row.tgfoid=to_regprocedure('public.enforce_paper_canary_execution_commit_fence()')
        AND trigger_row.tgconstraint<>0 AND trigger_row.tgdeferrable AND trigger_row.tginitdeferred
        AND trigger_row.tgnargs=0 AND octet_length(trigger_row.tgargs)=0
        AND trigger_row.tgqual IS NULL)=5
    AND (SELECT count(*) FROM pg_trigger trigger_row
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgfoid=to_regprocedure('public.enforce_paper_canary_execution_commit_fence()'))=5
  ),
  'identity_triggers_exact', (
    (SELECT count(*) FROM expected_identity_triggers expected
      JOIN pg_namespace namespace ON namespace.nspname='public'
      JOIN pg_class relation ON relation.relnamespace=namespace.oid AND relation.relname=expected.relation_name
      JOIN pg_trigger trigger_row ON trigger_row.tgrelid=relation.oid AND trigger_row.tgname=expected.trigger_name
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgenabled IN ('O','A')
        AND trigger_row.tgtype=expected.trigger_type
        AND trigger_row.tgfoid=to_regprocedure('public.enforce_paper_canary_execution_identity_fence()')
        AND trigger_row.tgconstraint=0 AND NOT trigger_row.tgdeferrable AND NOT trigger_row.tginitdeferred
        AND trigger_row.tgnargs=0 AND octet_length(trigger_row.tgargs)=0
        AND trigger_row.tgqual IS NULL)=4
    AND (SELECT count(*) FROM pg_trigger trigger_row
      WHERE NOT trigger_row.tgisinternal
        AND trigger_row.tgfoid=to_regprocedure('public.enforce_paper_canary_execution_identity_fence()'))=4
  ),
  'global_rows', (SELECT count(*) FROM runtime_control WHERE id=1),
  'global_mode', (SELECT mode::text FROM runtime_control WHERE id=1),
  'global_kill_switch', (SELECT kill_switch FROM runtime_control WHERE id=1),
  'global_live_confirmation', (SELECT live_confirmation_until FROM runtime_control WHERE id=1),
  'polymarket_rows', (SELECT count(*) FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_enabled', (SELECT enabled FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_mode', (SELECT mode::text FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_kill_switch', (SELECT kill_switch FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_live_confirmation', (SELECT live_confirmation_until FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'binance_rows', (SELECT count(*) FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'binance_enabled', (SELECT enabled FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'binance_mode', (SELECT mode::text FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'binance_kill_switch', (SELECT kill_switch FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'binance_live_confirmation', (SELECT live_confirmation_until FROM venue_runtime_control WHERE venue='binance_spot'::trading_venue),
  'pending_commands', (SELECT count(*) FROM operator_commands WHERE status='pending'),
  'live_orders', (SELECT count(*) FROM orders WHERE mode='live'::execution_mode),
  'live_positions', (SELECT count(*) FROM positions WHERE mode='live'::execution_mode),
  'live_intents', (SELECT count(*) FROM execution_order_intents WHERE mode='live'::execution_mode),
  'live_fill_legs', (SELECT count(*) FROM execution_fill_legs WHERE mode='live'::execution_mode),
  'live_enrollments', (SELECT count(*) FROM prospective_validation_enrollments WHERE mode='live'::execution_mode),
  'live_outcomes', (
    SELECT count(*) FROM prospective_validation_outcomes outcome
    JOIN prospective_validation_enrollments enrollment ON enrollment.id=outcome.enrollment_id
    WHERE enrollment.mode='live'::execution_mode
  ),
  'runtime_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid<>pg_backend_pid() AND datname=current_database()
      AND backend_type='client backend'
  ),
  'idle_in_transaction_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid<>pg_backend_pid() AND datname=current_database()
      AND backend_type='client backend' AND state LIKE 'idle in transaction%'
  ),
  'lock_waiters', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid<>pg_backend_pid() AND datname=current_database()
      AND backend_type='client backend' AND wait_event_type='Lock'
  ),
  'active_pg_dump', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid<>pg_backend_pid() AND datname=current_database()
      AND backend_type='client backend' AND application_name LIKE 'pg_dump%'
  ),
  'bootstrap_audit_exact', (
    (SELECT count(*) FROM operator_commands WHERE request_id='paper-canary-bootstrap-stop-0047')=1
    AND (SELECT count(*) FROM operator_commands
      WHERE request_id='paper-canary-bootstrap-stop-0047'
        AND command='stop'
        AND payload=jsonb_build_object('venue', 'all')
        AND actor='migration-0047'
        AND reason='Fail closed before DB-clock PAPER canary authority.'
        AND status='succeeded'
        AND executed_at IS NOT NULL
        AND detail LIKE 'Migration 0047 forced global/Polymarket PAPER kills ON%')=1
  ),
  'pending_signals', (SELECT count(*) FROM signals WHERE status='pending'::signal_status),
  'live_open_or_pending_orders', (
    SELECT count(*) FROM orders
    WHERE mode='live'::execution_mode AND status IN ('open','pending')
  ),
  'fence_armed', public.paper_canary_fence_armed(),
  'active_lease_exists', public.paper_canary_active_lease_exists(),
  'execution_authorized', public.paper_canary_execution_authorized()
)::text;
COMMIT;"
'@
    return Get-OneJsonObject -Label 'strict successor live-state transaction' -Lines (
        Invoke-Docker -Arguments @(
            'exec', $productionPostgresId, 'sh', '-ec', $queryCommand
        )
    )
}

function Assert-StrictLiveSnapshot {
    param(
        [Parameter(Mandatory = $true)][object] $Snapshot,
        [Parameter(Mandatory = $true)][string] $Label
    )
    Assert-NativeJsonObject -Value $Snapshot -Label $Label
    Assert-ExactProperties -Value $Snapshot -Label $Label -Expected @(
        'database_clock', 'schema_head_count', 'schema_head', 'indexes_exact', 'functions_exact',
        'lease_table_count', 'lease_rows', 'active_lease_rows', 'runtime_boundary',
        'safety_boundary', 'control_triggers_exact', 'commit_triggers_exact',
        'identity_triggers_exact', 'global_rows', 'global_mode', 'global_kill_switch',
        'global_live_confirmation', 'polymarket_rows', 'polymarket_enabled',
        'polymarket_mode', 'polymarket_kill_switch', 'polymarket_live_confirmation',
        'binance_rows', 'binance_enabled', 'binance_mode', 'binance_kill_switch',
        'binance_live_confirmation', 'pending_commands', 'live_orders', 'live_positions',
        'live_intents', 'live_fill_legs', 'live_enrollments', 'live_outcomes',
        'runtime_sessions', 'idle_in_transaction_sessions', 'lock_waiters',
        'active_pg_dump', 'bootstrap_audit_exact', 'pending_signals',
        'live_open_or_pending_orders', 'fence_armed', 'active_lease_exists',
        'execution_authorized'
    )
    foreach ($name in @(
        'database_clock', 'schema_head', 'global_mode', 'polymarket_mode', 'binance_mode'
    )) {
        Assert-NativeJsonString -Value $Snapshot.$name -Label "$Label $name"
    }
    foreach ($name in @(
        'indexes_exact', 'functions_exact', 'runtime_boundary', 'safety_boundary', 'control_triggers_exact',
        'commit_triggers_exact', 'identity_triggers_exact', 'global_kill_switch',
        'polymarket_enabled', 'polymarket_kill_switch', 'binance_enabled',
        'binance_kill_switch', 'bootstrap_audit_exact', 'fence_armed',
        'active_lease_exists', 'execution_authorized'
    )) {
        Assert-NativeJsonBoolean -Value $Snapshot.$name -Label "$Label $name"
    }
    foreach ($name in @(
        'schema_head_count', 'lease_table_count', 'lease_rows', 'active_lease_rows',
        'global_rows', 'polymarket_rows', 'binance_rows', 'pending_commands',
        'live_orders', 'live_positions', 'live_intents', 'live_fill_legs',
        'live_enrollments', 'live_outcomes', 'runtime_sessions',
        'idle_in_transaction_sessions', 'lock_waiters', 'active_pg_dump',
        'pending_signals', 'live_open_or_pending_orders'
    )) {
        Assert-NativeJsonInteger -Value $Snapshot.$name -Label "$Label $name"
    }
    foreach ($name in @(
        'global_live_confirmation', 'polymarket_live_confirmation',
        'binance_live_confirmation'
    )) {
        if ($null -ne $Snapshot.$name) {
            throw "$Label $name must be native JSON null."
        }
    }
    if (
        $Snapshot.schema_head_count -ne 1 -or
        $Snapshot.schema_head -cne $targetSchemaHead -or
        -not $Snapshot.indexes_exact -or
        -not $Snapshot.functions_exact -or
        $Snapshot.lease_table_count -ne 1 -or
        $Snapshot.lease_rows -ne 0 -or
        $Snapshot.active_lease_rows -ne 0 -or
        -not $Snapshot.runtime_boundary -or
        -not $Snapshot.safety_boundary -or
        -not $Snapshot.control_triggers_exact -or
        -not $Snapshot.commit_triggers_exact -or
        -not $Snapshot.identity_triggers_exact -or
        $Snapshot.global_rows -ne 1 -or
        $Snapshot.global_mode -cne 'paper' -or
        -not $Snapshot.global_kill_switch -or
        $Snapshot.polymarket_rows -ne 1 -or
        -not $Snapshot.polymarket_enabled -or
        $Snapshot.polymarket_mode -cne 'paper' -or
        -not $Snapshot.polymarket_kill_switch -or
        $Snapshot.binance_rows -ne 1 -or
        -not $Snapshot.binance_enabled -or
        $Snapshot.binance_mode -cne 'backtest' -or
        -not $Snapshot.binance_kill_switch -or
        -not $Snapshot.bootstrap_audit_exact -or
        -not $Snapshot.fence_armed -or
        $Snapshot.active_lease_exists -or
        $Snapshot.execution_authorized
    ) {
        throw "$Label rejected schema, database objects, or fail-closed controls."
    }
    foreach ($name in @(
        'pending_commands', 'live_orders', 'live_positions', 'live_intents',
        'live_fill_legs', 'live_enrollments', 'live_outcomes', 'runtime_sessions',
        'idle_in_transaction_sessions', 'lock_waiters', 'active_pg_dump',
        'pending_signals', 'live_open_or_pending_orders'
    )) {
        if ($Snapshot.$name -ne 0) {
            throw "$Label rejected non-zero $name."
        }
    }
    return ConvertTo-RequiredUtcTime -Value ([string]$Snapshot.database_clock) `
        -Label "$Label database_clock"
}

function New-AttestationEnvelope {
    param(
        [Parameter(Mandatory = $true)][string] $Format,
        [Parameter(Mandatory = $true)][object] $Payload
    )
    $payloadJson = $Payload | ConvertTo-Json -Depth 20 -Compress
    return [ordered]@{
        format = $Format
        accepted = $true
        payload = $Payload
        payload_sha256 = Get-Sha256Text -Value $payloadJson
    }
}

$absoluteRoot = [IO.Path]::GetFullPath($Root)
$absoluteBuildContext = [IO.Path]::GetFullPath($BuildContext)
$absoluteNetworkRepairPath = [IO.Path]::GetFullPath($NetworkRepairAttestationPath)
[void](Assert-RegularDirectory -LiteralPath $absoluteRoot)
[void](Assert-RegularDirectory -LiteralPath $absoluteBuildContext)
$absolutePreStartPath = Assert-PathBelowRoot -LiteralPath $PreStartAttestationPath `
    -AbsoluteRoot $absoluteRoot -Label 'PreStartAttestationPath'
$absoluteLivePath = Assert-PathBelowRoot -LiteralPath $LiveAcceptancePath `
    -AbsoluteRoot $absoluteRoot -Label 'LiveAcceptancePath'
$watchdogLeasePath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-lease.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog LeasePath'
$watchdogReceiptPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-terminal.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog ReceiptPath'
$watchdogHeartbeatPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-heartbeat.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog HeartbeatPath'
$watchdogCommitPath = Assert-PathBelowRoot `
    -LiteralPath ($absolutePreStartPath + '.watchdog-commit.json') `
    -AbsoluteRoot $absoluteRoot -Label 'watchdog CommitPath'
Assert-ToolEnvironmentBoundary
$dockerApplication = Resolve-PinnedApplication -Name 'docker' -Label 'Docker' `
    -ApprovedSha256 $approvedDockerExecutableSha256
$script:dockerExecutable = [string]$dockerApplication.path
$script:dockerExecutableSha256 = [string]$dockerApplication.sha256
$gitApplication = Resolve-PinnedApplication -Name 'git' -Label 'Git' `
    -ApprovedSha256 $approvedGitExecutableSha256
$script:gitExecutable = [string]$gitApplication.path
$script:gitExecutableSha256 = [string]$gitApplication.sha256
$composePath = Join-Path $absoluteRoot 'docker-compose.yml'
$composeEnvPath = Join-Path $absoluteRoot '.env'
[void](Assert-RegularFile -LiteralPath $composePath)
[void](Assert-RegularFile -LiteralPath $composeEnvPath)
foreach ($overrideName in @(
    'COMPOSE_FILE', 'COMPOSE_PROFILES', 'COMPOSE_PROJECT_NAME',
    'COMPOSE_PATH_SEPARATOR', 'COMPOSE_PROVIDER'
)) {
    if (Test-Path -LiteralPath "Env:$overrideName") {
        throw "Release Compose override environment variable is forbidden: $overrideName"
    }
}
if (@(
    Get-Content -LiteralPath $composeEnvPath | Where-Object {
        $_ -match '^\s*(?:export\s+)?(?:COMPOSE_(?:FILE|PROFILES|PROJECT_NAME|PATH_SEPARATOR|PROVIDER)|DOCKER_(?:CONFIG|CLI_PLUGIN_EXTRA_DIRS|HOST|CONTEXT|TLS_VERIFY|CERT_PATH|API_VERSION))\s*='
    }
).Count -ne 0) {
    throw 'Release .env must not define a reserved Compose override variable.'
}
$composeArguments = @(
    'compose', '--project-directory', $absoluteRoot, '-f', $composePath,
    '--env-file', $composeEnvPath, '--project-name', 'weatherpoly'
)
if ($CandidateImage -cne "stratus:candidate-$($ExpectedRevision.Substring(0, 12))") {
    throw 'CandidateImage is not the exact revision-derived successor tag.'
}
if ($PostgresContainer -cne $productionPostgresId) {
    throw 'PostgresContainer must be the exact retained production container ID.'
}
if (
    [StringComparer]::OrdinalIgnoreCase.Equals($absolutePreStartPath, $absoluteLivePath) -or
    [StringComparer]::OrdinalIgnoreCase.Equals(
        $absoluteNetworkRepairPath, $absolutePreStartPath
    ) -or
    [StringComparer]::OrdinalIgnoreCase.Equals(
        $absoluteNetworkRepairPath, $absoluteLivePath
    )
) {
    throw 'Repair, PreStart, and Live evidence paths must all be distinct.'
}
if (Test-Path -LiteralPath $absolutePreStartPath) {
    throw 'Transition refuses to overwrite an existing successor PreStart attestation.'
}
if (Test-Path -LiteralPath $absoluteLivePath) {
    throw 'Transition refuses to overwrite an existing successor Live acceptance.'
}
foreach ($path in @(
    $watchdogLeasePath, $watchdogReceiptPath, $watchdogHeartbeatPath,
    $watchdogCommitPath
)) {
    if (Test-Path -LiteralPath $path) {
        throw "Transition refuses a pre-existing watchdog artifact path: $path"
    }
}
$watchdogArtifactPaths = @(
    $absolutePreStartPath, $absoluteLivePath, $watchdogLeasePath,
    $watchdogReceiptPath, $watchdogHeartbeatPath, $watchdogCommitPath
)
if (@($watchdogArtifactPaths | Sort-Object -Unique).Count -ne 6) {
    throw 'Transition watchdog artifact paths must be pairwise distinct.'
}

$eventWitness = $null
$script:transitionWitnessArmed = $false
$script:transitionExpectedPostgresExecCount = 0
$postgresStartCommandIssued = $false
$postgresStartedByTransition = $false
$emergencyContainmentTriggered = $false
$restartPolicyUpdateIssued = $false
$transitionSucceeded = $false
$preStartAcceptedAt = [DateTimeOffset]::MinValue
$watchdogProgram = $null
$watchdogCollectorType = $null
$watchdogProcess = $null
$watchdogLeaseProof = $null
$watchdogHeartbeatPublisher = $null
$watchdogCommitProof = $null
$watchdogExitProof = $null
$watchdogTerminalReceipt = $null
$watchdogHardDeadline = [DateTimeOffset]::MinValue
try {
    $watchdogProgram = Open-TransitionWatchdogProgramBoundary `
        -AbsoluteRoot $absoluteRoot
    # This must be the first Docker proof in the invocation. Every later
    # Docker boundary is compared to it.
    $initialDockerEngine = Get-DockerEngineBoundary
    $hbaLockBoundary = Open-HeldHbaReadBoundary
    $networkRepair = Assert-NetworkRepairAttestation `
        -LiteralPath $absoluteNetworkRepairPath `
        -CurrentEngine $initialDockerEngine
    $networkRepairBinding = [ordered]@{
        path = [string]$networkRepair.path
        sha256 = [string]$networkRepair.sha256
        payload_sha256 = [string]$networkRepair.payload_sha256
        accepted_at = [string]$networkRepair.accepted_at
        expires_at = [string]$networkRepair.expires_at
        disposition = [string]$networkRepair.disposition
        post_first_sha256 = [string]$networkRepair.post_first_sha256
        post_second_sha256 = [string]$networkRepair.post_second_sha256
        final_attachment_sha256 = [string]$networkRepair.final_attachment_sha256
        docker_engine_contract_sha256 =
            [string]$networkRepair.docker_engine_contract_sha256
    }
    $historical = Assert-HistoricalLineage -ValidateBackupPayload -ValidateCatalogProbe
    $contract = Assert-ClosedSuccessorContract
    $composeMigrate = Assert-RenderedMigrateContract
    $postgres = Get-PostgresBoundary -ExpectedState Stopped `
        -ExpectedRestartPolicy 'unless-stopped'
    Assert-NetworkRepairCurrentPostgres -Evidence $networkRepair `
        -CurrentPostgres $postgres
    if (
        $postgres.started_at -cne [string]$historical.postgres.started_at -or
        $postgres.finished_at -eq '0001-01-01T00:00:00Z'
    ) {
        throw 'Stopped PostgreSQL does not continue the exact historical migration startup.'
    }
    $finishedAt = ConvertTo-RequiredUtcTime -Value $postgres.finished_at `
        -Label 'stopped PostgreSQL finished_at'
    $historicalStartedAt = ConvertTo-RequiredUtcTime -Value $postgres.started_at `
        -Label 'stopped PostgreSQL started_at'
    if ($finishedAt -le $historicalStartedAt) {
        throw 'Stopped PostgreSQL has an invalid retained start/finish boundary.'
    }
    $topology = Assert-ExclusiveRuntimeTopology -PostgresState Stopped
    $preWitnessDockerEngine = Get-DockerEngineBoundary
    if ($preWitnessDockerEngine.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
        throw 'Docker context, engine, CLI, or Compose plugin drifted before witness arming.'
    }
    $eventWitness = Start-DockerEventWitness
    $script:transitionWitnessArmed = $true
    $initialBarrier = Invoke-DockerEventBarrier -Witness $eventWitness -Phase prestart
    $eventWitness.prefix_length = [int64]$initialBarrier.prefix_length
    $eventWitness.prefix_sha256 = [string]$initialBarrier.prefix_sha256
    [void](Get-DockerEventJournalSnapshot -Witness $eventWitness)
    $postBarrierPostgres = Get-PostgresBoundary -ExpectedState Stopped `
        -ExpectedRestartPolicy 'unless-stopped'
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
        'restart_policy', 'health_status', 'network_mode',
        'mount_contract_sha256', 'ports_contract_sha256',
        'network_contract_sha256', 'active_endpoint_sha256'
    )) {
        if ($postBarrierPostgres.$name -cne $postgres.$name) {
            throw 'Retained PostgreSQL drifted while arming the transition witness.'
        }
    }
    $topology = Assert-ExclusiveRuntimeTopology -PostgresState Stopped
    Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
    [void](Get-HeldHbaReadBoundary)

    $restartPolicyUpdateIssued = $true
    $updatedId = Get-OneNativeLine -Label 'retained PostgreSQL restart-policy update' `
        -Lines (Invoke-Docker -Arguments @(
            'update', '--restart=no', $productionPostgresId
        ))
    if ($updatedId -cne $productionPostgresId) {
        throw 'Docker restart-policy update returned an unexpected container identity.'
    }
    $armedPostgres = Get-PostgresBoundary -ExpectedState Stopped `
        -ExpectedRestartPolicy 'no'
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
        'health_status', 'network_mode', 'mount_contract_sha256',
        'ports_contract_sha256', 'network_contract_sha256',
        'active_endpoint_sha256'
    )) {
        if ($armedPostgres.$name -cne $postBarrierPostgres.$name) {
            throw 'Retained PostgreSQL drifted while disabling automatic restart.'
        }
    }
    if (
        $postBarrierPostgres.restart_policy -cne 'unless-stopped' -or
        $armedPostgres.restart_policy -cne 'no'
    ) {
        throw 'Retained PostgreSQL restart policy did not transition fail closed.'
    }
    $postgres = $armedPostgres
    $topology = Assert-ExclusiveRuntimeTopology -PostgresState Stopped
    $armedDockerEngine = Get-DockerEngineBoundary
    if ($armedDockerEngine.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
        throw 'Docker context or engine changed while arming the transition witness.'
    }
    Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
    $hbaLockBoundary = Get-HeldHbaReadBoundary
    $watchdogCollectorType = Initialize-DockerEventCollectorType
    $acceptedAtValue = [DateTimeOffset]::UtcNow
    $preStartAcceptedAt = $acceptedAtValue
    $acceptedAt = $acceptedAtValue.ToString('o')
    $preStartExpiresAt = $acceptedAtValue.AddSeconds($preStartMaxAgeSeconds)
    $postgresStartDeadline = $acceptedAtValue.AddSeconds($postgresStartWindowSeconds)
    $watchdogHardDeadline = $postgresStartDeadline
    $watchdogHandoff = [ordered]@{
        format = 'stratus-successor-transition-watchdog-handoff-v1'
        session_id = [string]$eventWitness.session_id
        owner_pid = [int]$watchdogProgram.owner_pid
        owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
        hard_deadline_utc = $watchdogHardDeadline.ToString('o')
        heartbeat_timeout_seconds = [int]$watchdogHeartbeatTimeoutSeconds
        lease_path = $watchdogLeasePath
        receipt_path = $watchdogReceiptPath
        heartbeat_path = $watchdogHeartbeatPath
        commit_path = $watchdogCommitPath
        live_path = $absoluteLivePath
        prestart_path = $absolutePreStartPath
    }
    $lineageHashes = [ordered]@{}
    foreach ($entry in $lineageArtifacts.GetEnumerator()) {
        $lineageHashes[[string]$entry.Key] = [string]$entry.Value
    }
    $payload = [ordered]@{
        accepted_at = $acceptedAt
        validity = [ordered]@{
            max_age_seconds = $preStartMaxAgeSeconds
            postgres_start_window_seconds = $postgresStartWindowSeconds
            expires_at = $acceptedAtValue.AddSeconds($preStartMaxAgeSeconds).ToString('o')
            postgres_start_deadline = $acceptedAtValue.AddSeconds(
                $postgresStartWindowSeconds
            ).ToString('o')
        }
        historical = [ordered]@{
            pre_migration_attestation_sha256 = $historical.attestation_sha256
            pre_migration_payload_sha256 = $historical.payload_sha256
            predecessor_revision = $historical.predecessor_revision
            predecessor_image = $historical.predecessor_image
            predecessor_image_id = $historical.predecessor_image_id
            lineage_sha256 = $lineageHashes
            backup = $historical.backup
        }
        network_repair = $networkRepairBinding
        successor = [ordered]@{
            revision = $ExpectedRevision
            image = $CandidateImage
            image_id = $ExpectedImageId
        }
        closed_contract = [ordered]@{
            git_executable_path = $contract.git_executable_path
            git_executable_sha256 = $contract.git_executable_sha256
            git_executable_bytes = [int64]$contract.git_executable_bytes
            git_sha256 = $contract.git_contract_sha256
            git_entries = [int]$contract.git_contract_entries
            image_sha256 = $contract.image_contract_sha256
            image_files = [int]$contract.image_contract_files
            predecessor_contract_equals_successor = $true
        }
        compose_migrate = $composeMigrate
        postgres_prestart = $postgres
        hba_host_read_lock = $hbaLockBoundary
        topology = $topology
        transition_session = [ordered]@{
            mode = 'single-process'
            session_id = [string]$eventWitness.session_id
            collector_process_id = [int]$eventWitness.process_id
            collector_process_started_at = [string]$eventWitness.process_started_at
            collector_requested_at = [string]$eventWitness.requested_at
            docker_engine = $initialDockerEngine
            initial_barrier = $initialBarrier
            accepted_journal_prefix_bytes = [int64]$eventWitness.prefix_length
            accepted_journal_prefix_sha256 = [string]$eventWitness.prefix_sha256
            reconnect_permitted = $false
            watchdog_program = $watchdogProgram
            watchdog_handoff = $watchdogHandoff
            restart_policy_transition = [ordered]@{
                previous = 'unless-stopped'
                armed = 'no'
                command = @('update', '--restart=no', $productionPostgresId)
                command_issued = $true
                stopped_identity_rechecked = $true
                restore_on_success = $false
            }
            observation = [ordered]@{
                start_command_issued = $false
                start_completed = $false
            }
            start_proof = [ordered]@{
                start_owned_by_transition_process = $false
                successor_started_at = $null
                docker_start_event_time_nano = $null
            }
        }
        authorization = [ordered]@{
            postgres_start_permitted = $false
            external_postgres_start_permitted = $false
            transition_process_start_permitted = $true
            api_stage_permitted = $false
            worker_start_permitted = $false
            safety_control_start_permitted = $false
            live_transition_permitted = $false
            binance_kill_switch_removal_permitted = $false
        }
    }
    Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
    [void](Get-HeldHbaReadBoundary)
    $envelope = New-AttestationEnvelope `
        -Format 'stratus-post-migration-successor-transition-prestart-v2' -Payload $payload
    Write-CreateNewJson -LiteralPath $absolutePreStartPath -Value $envelope
    $preStartSha256 = Get-FileSha256 -LiteralPath $absolutePreStartPath
    [void](Get-DockerEventJournalSnapshot -Witness $eventWitness)

    $watchdogProcess = Start-TransitionWatchdogProcess `
        -Program $watchdogProgram -CollectorType $watchdogCollectorType `
        -SessionId ([string]$eventWitness.session_id) `
        -HardDeadline $watchdogHardDeadline -LeasePath $watchdogLeasePath `
        -ReceiptPath $watchdogReceiptPath -HeartbeatPath $watchdogHeartbeatPath `
        -CommitPath $watchdogCommitPath -LivePath $absoluteLivePath `
        -PreStartPath $absolutePreStartPath -PreStartSha256 $preStartSha256
    $watchdogLeaseProof = Wait-TransitionWatchdogLease `
        -Watchdog $watchdogProcess -Program $watchdogProgram `
        -SessionId ([string]$eventWitness.session_id) `
        -HardDeadline $watchdogHardDeadline -LeasePath $watchdogLeasePath `
        -ReceiptPath $watchdogReceiptPath -HeartbeatPath $watchdogHeartbeatPath `
        -CommitPath $watchdogCommitPath -LivePath $absoluteLivePath `
        -PreStartPath $absolutePreStartPath -PreStartSha256 $preStartSha256 `
        -PreStartPayloadSha256 ([string]$envelope.payload_sha256) `
        -PreStartPostgres $payload.postgres_prestart
    $watchdogHeartbeatPublisher = Start-WatchdogHeartbeatPublisher `
        -CollectorType $watchdogCollectorType -HeartbeatPath $watchdogHeartbeatPath `
        -SessionId ([string]$eventWitness.session_id) -Program $watchdogProgram `
        -LeasePath $watchdogLeasePath -LeaseSha256 $watchdogLeaseProof.sha256
    $firstHeartbeatEvidence = Assert-WatchdogHeartbeatPublisher `
        -Publisher $watchdogHeartbeatPublisher
    $firstHeartbeatDocument = Read-BoundedStrictJsonDocument `
        -LiteralPath $watchdogHeartbeatPath -Label 'first watchdog owner heartbeat' `
        -MaximumBytes 65536
    [void](Assert-WatchdogHeartbeatDocument -Document $firstHeartbeatDocument `
        -Evidence $firstHeartbeatEvidence -SessionId ([string]$eventWitness.session_id) `
        -Program $watchdogProgram -LeaseProof $watchdogLeaseProof `
        -LeasePath $watchdogLeasePath)
    Start-Sleep -Milliseconds 250
    [void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
    Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
    if ((Get-FileSha256 -LiteralPath $watchdogLeasePath) -cne $watchdogLeaseProof.sha256) {
        throw 'The accepted watchdog lease changed before PostgreSQL start.'
    }
    if ((Get-FileSha256 -LiteralPath $absolutePreStartPath) -cne $preStartSha256) {
        throw 'The watchdog-bound PreStart artifact changed before PostgreSQL start.'
    }
    $watchdogArmedPostgres = Get-PostgresBoundary -ExpectedState Stopped `
        -ExpectedRestartPolicy 'no'
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
        'restart_policy', 'health_status', 'network_mode',
        'mount_contract_sha256', 'ports_contract_sha256',
        'network_contract_sha256', 'active_endpoint_sha256'
    )) {
        if ($watchdogArmedPostgres.$name -cne $payload.postgres_prestart.$name) {
            throw 'Retained PostgreSQL drifted after watchdog lease acceptance.'
        }
    }
    [void](Assert-ExclusiveRuntimeTopology -PostgresState Stopped)
    $watchdogArmedEngine = Get-DockerEngineBoundary
    if ($watchdogArmedEngine.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
        throw 'Docker context or engine drifted after watchdog lease acceptance.'
    }
    Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
    [void](Get-HeldHbaReadBoundary)
    [void](Assert-TransitionWatchdogProgramUnchanged -Program $watchdogProgram)
    [void](Get-DockerEventJournalSnapshot -Witness $eventWitness)
    if ([DateTimeOffset]::UtcNow -ge $watchdogHardDeadline) {
        throw 'The watchdog lease expired before the retained PostgreSQL start.'
    }
    [void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
    Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess

    $postgresStartCommandIssued = $true
    try {
        $startOutput = @(Invoke-Docker -Arguments @('start', $productionPostgresId))
        $startedId = Get-OneNativeLine -Lines $startOutput `
            -Label 'retained PostgreSQL transition start'
        if ($startedId -cne $productionPostgresId) {
            throw 'Docker start returned an unexpected retained PostgreSQL identity.'
        }
        $postgresStartedByTransition = $true
    }
    catch {
        $emergencyContainmentTriggered = $true
        throw
    }
    $health = Wait-PostgresContainerHealthy
    [void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
    Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
    $liveValidationStartedAt = [DateTimeOffset]::UtcNow
    if ($liveValidationStartedAt -gt $preStartExpiresAt.AddSeconds($clockSkewSeconds)) {
        throw 'Successor PreStart authorization is outside its bounded Live window.'
    }
    $postgres = Get-PostgresBoundary -ExpectedState Running `
        -ExpectedRestartPolicy 'no'
    $topology = Assert-ExclusiveRuntimeTopology -PostgresState Running
    $preStartPostgres = $payload.postgres_prestart
if (
    $postgres.container_id -cne $preStartPostgres.container_id -or
    $postgres.image_id -cne $preStartPostgres.image_id -or
    $postgres.config_image -cne $preStartPostgres.config_image -or
    $postgres.restart_count -ne $preStartPostgres.restart_count -or
    $postgres.restart_policy -cne 'no' -or
    $preStartPostgres.restart_policy -cne 'no' -or
    $postgres.mount_contract_sha256 -cne $preStartPostgres.mount_contract_sha256
) {
    throw 'Running PostgreSQL identity drifted after successor PreStart acceptance.'
}
$newStartedAt = ConvertTo-RequiredUtcTime -Value $postgres.started_at `
    -Label 'successor PostgreSQL started_at'
$priorFinishedAt = ConvertTo-RequiredUtcTime -Value $preStartPostgres.finished_at `
    -Label 'prestart PostgreSQL finished_at'
if (
    $newStartedAt -le $preStartAcceptedAt -or
    $newStartedAt -gt $postgresStartDeadline -or
    $newStartedAt -gt $liveValidationStartedAt.AddSeconds($clockSkewSeconds) -or
    $newStartedAt -le $priorFinishedAt -or
    $postgres.started_at -ceq $preStartPostgres.started_at
) {
    throw 'Running PostgreSQL StartedAt does not prove the bounded post-PreStart restart.'
}
$initialDockerEngineAfterStart = Get-DockerEngineBoundary
if ($initialDockerEngineAfterStart.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
    throw 'Docker context or engine changed during the retained PostgreSQL start.'
}
[void](Assert-HistoricalLineage -ValidateBackupPayload)
[void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
$initialEventCheckedAt = [DateTimeOffset]::UtcNow
if ($initialEventCheckedAt -gt $preStartExpiresAt.AddSeconds($clockSkewSeconds)) {
    throw 'Successor PreStart authorization expired before the database proof.'
}
[void](Get-DockerEventJournalSnapshot -Witness $eventWitness)

# No PostgreSQL query is permitted above this point. The fixed byte proof,
# reload, normalized inventory, and byte recheck are the first privileged
# database operations after the retained container start.
$hba = Assert-HbaFirstBoundary
[void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
$firstSnapshot = Get-StrictLiveSnapshot
$firstClock = Assert-StrictLiveSnapshot -Snapshot $firstSnapshot `
    -Label 'initial successor live state'
$secondSnapshot = Get-StrictLiveSnapshot
[void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
$secondClock = Assert-StrictLiveSnapshot -Snapshot $secondSnapshot `
    -Label 'immediate successor live state'
if ($secondClock -lt $firstClock -or ($secondClock - $firstClock).TotalSeconds -gt 60) {
    throw 'Successor live-state proof was not repeated within the bounded 60-second window.'
}
$finalBoundary = Assert-FinalLiveBoundary -InitialPostgres $postgres `
    -InitialDockerEngine $initialDockerEngine `
    -InitialHba $hba `
    -PreStartExpiresAt $preStartExpiresAt
[void](Assert-WatchdogHeartbeatPublisher -Publisher $watchdogHeartbeatPublisher)
Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
$finalBarrier = Invoke-DockerEventBarrier -Witness $eventWitness -Phase final
Stop-DockerEventWitness -Witness $eventWitness -PreserveFiles -Seal
$eventReceipt = Assert-DockerEventContract -Witness $eventWitness `
    -InitialBarrier $initialBarrier -FinalBarrier $finalBarrier `
    -ExpectedStartedAt $newStartedAt
[void](Assert-RegularFile -LiteralPath $absolutePreStartPath)
if ((Get-FileSha256 -LiteralPath $absolutePreStartPath) -cne $preStartSha256) {
    throw 'The transition-owned PreStart artifact changed before Live acceptance.'
}
Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
[void](Get-HeldHbaReadBoundary)
$finalBoundaryMilliseconds = [int64]([Math]::Floor(
    ([double][int64]$finalBarrier.destroy_time_nano) / 1000000.0
))
$finalAcceptedAt = [DateTimeOffset]::FromUnixTimeMilliseconds($finalBoundaryMilliseconds)
$artifactWrittenAt = [DateTimeOffset]::UtcNow
$lastCheckedAt = ConvertTo-RequiredUtcTime -Value $finalBoundary.checked_at `
    -Label 'final checked_at'
if (
    $finalAcceptedAt -lt $lastCheckedAt.AddSeconds(-1) -or
    $finalAcceptedAt -gt $artifactWrittenAt.AddSeconds($clockSkewSeconds) -or
    $artifactWrittenAt -gt $preStartExpiresAt.AddSeconds($clockSkewSeconds) -or
    $artifactWrittenAt -gt $watchdogHardDeadline
) {
    throw 'Successor PreStart authorization expired before Live evidence creation.'
}
$watchdogHeartbeatAtLive = Assert-WatchdogHeartbeatPublisher `
    -Publisher $watchdogHeartbeatPublisher
Assert-TransitionWatchdogProcessConnected -Watchdog $watchdogProcess
[void](Assert-TransitionWatchdogProgramUnchanged -Program $watchdogProgram)
if ((Get-FileSha256 -LiteralPath $watchdogLeasePath) -cne $watchdogLeaseProof.sha256) {
    throw 'The watchdog lease changed before Live evidence creation.'
}
$watchdogBinding = [ordered]@{
    format = 'stratus-successor-transition-watchdog-binding-v1'
    session_id = [string]$eventWitness.session_id
    owner_pid = [int]$watchdogProgram.owner_pid
    owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
    hard_deadline_utc = $watchdogHardDeadline.ToString('o')
    heartbeat_timeout_seconds = [int]$watchdogHeartbeatTimeoutSeconds
    lease_path = $watchdogLeasePath
    lease_sha256 = [string]$watchdogLeaseProof.sha256
    receipt_path = $watchdogReceiptPath
    heartbeat_path = $watchdogHeartbeatPath
    commit_path = $watchdogCommitPath
}

$livePayload = [ordered]@{
    accepted_at = $finalAcceptedAt.ToString('o')
    artifact_written_at = $artifactWrittenAt.ToString('o')
    historical = [ordered]@{
        pre_migration_attestation_sha256 = $historical.attestation_sha256
        pre_migration_payload_sha256 = $historical.payload_sha256
        predecessor_revision = $historical.predecessor_revision
        predecessor_image_id = $historical.predecessor_image_id
        backup = $historical.backup
    }
    network_repair = $networkRepairBinding
    prestart = [ordered]@{
        path = $absolutePreStartPath
        sha256 = $preStartSha256
        payload_sha256 = $envelope.payload_sha256
        accepted_at = $acceptedAt
    }
    successor = [ordered]@{
        revision = $ExpectedRevision
        image = $CandidateImage
        image_id = $ExpectedImageId
    }
    closed_contract = [ordered]@{
        git_executable_path = $contract.git_executable_path
        git_executable_sha256 = $contract.git_executable_sha256
        git_executable_bytes = [int64]$contract.git_executable_bytes
        git_sha256 = $contract.git_contract_sha256
        git_entries = [int]$contract.git_contract_entries
        image_sha256 = $contract.image_contract_sha256
        image_files = [int]$contract.image_contract_files
        predecessor_contract_equals_successor = $true
    }
    postgres_transition = [ordered]@{
        container_id = $postgres.container_id
        image_id = $postgres.image_id
        config_image = $postgres.config_image
        restart_count = $postgres.restart_count
        previous_started_at = $preStartPostgres.started_at
        previous_finished_at = $preStartPostgres.finished_at
        successor_started_at = $postgres.started_at
        state = $postgres.state
        oom_killed = $postgres.oom_killed
        restart_policy = $postgres.restart_policy
        health_status = $postgres.health_status
        health = $health
        network_mode = $postgres.network_mode
        mount_contract_sha256 = $postgres.mount_contract_sha256
        ports_contract_sha256 = $postgres.ports_contract_sha256
        network_contract_sha256 = $postgres.network_contract_sha256
        active_endpoint_sha256 = $postgres.active_endpoint_sha256
    }
    transition_session = [ordered]@{
        mode = 'single-process'
        session_id = [string]$eventWitness.session_id
        docker_engine = $initialDockerEngine
        event_witness = $eventReceipt
        watchdog_program = $watchdogProgram
        watchdog = $watchdogBinding
        watchdog_lease = [ordered]@{
            path = $watchdogLeasePath
            sha256 = [string]$watchdogLeaseProof.sha256
            armed_at = [string]$watchdogLeaseProof.armed_at
            docker_engine_contract_sha256 =
                [string]$watchdogLeaseProof.docker_engine.contract_sha256
        }
        heartbeat_at_live = $watchdogHeartbeatAtLive
        restart_policy_transition = [ordered]@{
            previous = 'unless-stopped'
            armed = 'no'
            command = @('update', '--restart=no', $productionPostgresId)
            command_issued = $true
            stopped_identity_rechecked = $true
            restore_on_success = $false
        }
        observation = [ordered]@{
            start_command_issued = $true
            start_completed = $true
        }
        start_proof = [ordered]@{
            start_owned_by_transition_process = $true
            successor_started_at = $postgres.started_at
            docker_start_event_time_nano = [int64]$eventReceipt.postgres_start_time_nano
        }
        rollback = [ordered]@{
            on_failure = $true
            stop_timeout_seconds = $postgresRollbackTimeoutSeconds
            executed = $false
            emergency_containment = $false
        }
    }
    topology = $topology
    hba = $hba
    final_boundary = $finalBoundary
    database = [ordered]@{
        schema_head = $secondSnapshot.schema_head
        indexes_exact = $secondSnapshot.indexes_exact
        functions_exact = $secondSnapshot.functions_exact
        runtime_boundary = $secondSnapshot.runtime_boundary
        safety_boundary = $secondSnapshot.safety_boundary
        control_triggers_exact = $secondSnapshot.control_triggers_exact
        commit_triggers_exact = $secondSnapshot.commit_triggers_exact
        identity_triggers_exact = $secondSnapshot.identity_triggers_exact
        fail_closed_controls = $true
        zero_live_state = $true
        zero_pending_commands = $true
        zero_other_sessions = $true
        bootstrap_audit_exact = $secondSnapshot.bootstrap_audit_exact
        fence_armed = $secondSnapshot.fence_armed
        active_lease_exists = $secondSnapshot.active_lease_exists
        execution_authorized = $secondSnapshot.execution_authorized
        first_database_clock = [string]$firstSnapshot.database_clock
        second_database_clock = [string]$secondSnapshot.database_clock
    }
    authorization = [ordered]@{
        api_stage_permitted = $false
        worker_start_permitted = $false
        safety_control_start_permitted = $false
        live_transition_permitted = $false
        binance_kill_switch_removal_permitted = $false
        migration_container_permitted = $false
    }
}
$liveEnvelope = New-AttestationEnvelope `
    -Format 'stratus-post-migration-successor-transition-live-v2' -Payload $livePayload
$liveJson = $liveEnvelope | ConvertTo-Json -Depth 20 -Compress
Write-CreateNewJson -LiteralPath $absoluteLivePath -Value $liveEnvelope
$liveSha256 = Get-FileSha256 -LiteralPath $absoluteLivePath
if (
    (Get-FileSha256 -LiteralPath $absolutePreStartPath) -cne $preStartSha256 -or
    (Get-FileSha256 -LiteralPath $absoluteLivePath) -cne $liveSha256 -or
    (Get-FileSha256 -LiteralPath $watchdogLeasePath) -cne $watchdogLeaseProof.sha256
) {
    throw 'A watchdog-bound transition artifact changed before commit publication.'
}
Assert-NetworkRepairEvidenceUnchanged -Evidence $networkRepair
[void](Get-HeldHbaReadBoundary)
$preCommitEngine = Get-DockerEngineBoundary
if ($preCommitEngine.contract_sha256 -cne $initialDockerEngine.contract_sha256) {
    throw 'Docker context or engine drifted before watchdog commit publication.'
}
$preCommitPostgres = Get-PostgresBoundary -ExpectedState Running `
    -ExpectedRestartPolicy 'no'
foreach ($name in @(
    'container_id', 'image_id', 'config_image', 'restart_count',
    'started_at', 'finished_at', 'state', 'oom_killed', 'exit_code',
    'restart_policy', 'health_status', 'network_mode',
    'mount_contract_sha256', 'ports_contract_sha256',
    'network_contract_sha256', 'active_endpoint_sha256'
)) {
    if ($preCommitPostgres.$name -cne $finalBoundary.postgres.$name) {
        throw 'Retained PostgreSQL drifted before watchdog commit publication.'
    }
}
[void](Assert-ExclusiveRuntimeTopology -PostgresState Running)
if ([DateTimeOffset]::UtcNow -gt $watchdogHardDeadline) {
    throw 'The watchdog deadline expired before the Live commit marker.'
}
$commitMarker = [ordered]@{
    format = 'stratus-successor-transition-watchdog-commit-v1'
    session_id = [string]$eventWitness.session_id
    owner_pid = [int]$watchdogProgram.owner_pid
    owner_start_time_utc = [string]$watchdogProgram.owner_start_time_utc
    lease_path = $watchdogLeasePath
    lease_sha256 = [string]$watchdogLeaseProof.sha256
    live_path = $absoluteLivePath
    live_sha256 = $liveSha256
    committed_at = [DateTimeOffset]::UtcNow.ToString('o')
}
Write-CreateNewJson -LiteralPath $watchdogCommitPath -Value $commitMarker
$commitDocument = Read-BoundedStrictJsonDocument -LiteralPath $watchdogCommitPath `
    -Label 'successor transition watchdog commit' -MaximumBytes 65536
$watchdogCommitProof = Assert-WatchdogCommitDocument -Document $commitDocument `
    -SessionId ([string]$eventWitness.session_id) -Program $watchdogProgram `
    -LeaseProof $watchdogLeaseProof -LeasePath $watchdogLeasePath `
    -LivePath $absoluteLivePath -LiveSha256 $liveSha256 `
    -HardDeadline $watchdogHardDeadline
$watchdogExitProof = Wait-TransitionWatchdogExit -Watchdog $watchdogProcess `
    -HeartbeatPublisher $null `
    -HardDeadline $watchdogHardDeadline
$watchdogTerminalReceipt = Assert-TransitionWatchdogSuccessReceipt `
    -ExitProof $watchdogExitProof -ReceiptPath $watchdogReceiptPath `
    -SessionId ([string]$eventWitness.session_id) -Program $watchdogProgram `
    -LeaseProof $watchdogLeaseProof -CommitProof $watchdogCommitProof `
    -CommitPath $watchdogCommitPath -LivePath $absoluteLivePath `
    -LiveSha256 $liveSha256 -LivePayloadSha256 ([string]$liveEnvelope.payload_sha256) `
    -PostgresStartedAt ([string]$postgres.started_at) `
    -HardDeadline $watchdogHardDeadline
$transitionSucceeded = $true
try {
    $watchdogHeartbeatPublisher.StopAndWait(5000)
}
catch {
    # The immutable terminal success receipt is the atomic authorization
    # point.  A bounded publisher cleanup failure cannot revoke it or trigger
    # a post-authorization PostgreSQL rollback; Dispose retries in finally.
}
$liveJson
}
catch {
    $transitionFailure = $_
    $heartbeatStopFailure = $null
    if ($null -ne $watchdogHeartbeatPublisher) {
        try { $watchdogHeartbeatPublisher.StopAndWait(5000) }
        catch { $heartbeatStopFailure = $_.Exception.Message }
    }
    $ownershipFailure = $null
    if (
        -not $postgresStartedByTransition -and
        $postgresStartCommandIssued -and
        $preStartAcceptedAt -ne [DateTimeOffset]::MinValue
    ) {
        try {
            $postgresStartedByTransition = Test-TransitionOwnedPostgresRunning `
                -NotBefore $preStartAcceptedAt
            if ($postgresStartedByTransition) {
                $emergencyContainmentTriggered = $true
            }
        }
        catch {
            $ownershipFailure = $_.Exception.Message
            # The exact protected start command was issued.  An ambiguous
            # response must be contained even when the follow-up inspect fails.
            $postgresStartedByTransition = $true
            $emergencyContainmentTriggered = $true
        }
    }
    $rollbackFailure = $null
    if ($postgresStartedByTransition -and -not $transitionSucceeded) {
        try {
            $rollbackPostgres = $null
            try {
                # The independent watchdog can win the containment race.  An
                # exact stopped/no proof is already the required terminal
                # state, so do not issue a redundant second production stop.
                $rollbackPostgres = Get-PostgresBoundary -ExpectedState Stopped `
                    -ExpectedRestartPolicy 'no'
            }
            catch {
                $boundedContainment = Invoke-BoundedPinnedDockerStop `
                    -ContainerId $productionPostgresId `
                    -ContainerStopTimeoutSeconds $postgresRollbackTimeoutSeconds
                $rollbackPostgres = Get-PostgresBoundary -ExpectedState Stopped `
                    -ExpectedRestartPolicy 'no'
            }
            if (
                $rollbackPostgres.container_id -cne $productionPostgresId -or
                $rollbackPostgres.image_id -cne $postgres16ImageId -or
                $rollbackPostgres.config_image -cne $postgres16Image -or
                $rollbackPostgres.restart_count -ne 0 -or
                $rollbackPostgres.restart_policy -cne 'no' -or
                $rollbackPostgres.state -cne 'exited'
            ) {
                throw 'Rollback did not restore the exact retained PostgreSQL stopped identity.'
            }
            [void](Assert-ExclusiveRuntimeTopology -PostgresState Stopped)
        }
        catch {
            $rollbackFailure = $_.Exception.Message
        }
    }
    $watchdogTerminalFailure = $null
    if ($null -ne $watchdogProcess) {
        try {
            $failureWaitBoundary = [DateTimeOffset]::UtcNow.AddSeconds(20)
            if (
                $watchdogHardDeadline -ne [DateTimeOffset]::MinValue -and
                $watchdogHardDeadline -lt $failureWaitBoundary
            ) {
                $failureWaitBoundary = $watchdogHardDeadline
            }
            [void](Wait-TransitionWatchdogExit -Watchdog $watchdogProcess `
                -HeartbeatPublisher $null -HardDeadline $failureWaitBoundary `
                -PermitContainmentExit)
            $failureReceipt = Read-BoundedStrictJsonDocument `
                -LiteralPath $watchdogReceiptPath `
                -Label 'failed successor transition watchdog terminal receipt' `
                -MaximumBytes 262144
            Assert-ExactProperties -Value $failureReceipt.value `
                -Label 'failed transition watchdog receipt' -Expected @(
                    'format', 'status', 'terminal_at', 'session_id', 'owner_pid',
                    'owner_start_time_utc', 'lease_path', 'lease_sha256',
                    'receipt_path', 'commit_path', 'live_path', 'trigger',
                    'docker_engine', 'containment', 'commit', 'authorization',
                    'failure'
                )
            Assert-NativeJsonObject -Value $failureReceipt.value.authorization `
                -Label 'failed transition watchdog authorization'
            Assert-ExactProperties -Value $failureReceipt.value.authorization `
                -Label 'failed transition watchdog authorization' -Expected @(
                    'api_stage_permitted', 'worker_start_permitted',
                    'safety_control_start_permitted', 'live_transition_permitted',
                    'binance_kill_switch_removal_permitted',
                    'migration_container_permitted'
                )
            foreach ($name in @(
                'api_stage_permitted', 'worker_start_permitted',
                'safety_control_start_permitted', 'live_transition_permitted',
                'binance_kill_switch_removal_permitted',
                'migration_container_permitted'
            )) {
                Assert-NativeJsonBoolean `
                    -Value $failureReceipt.value.authorization.$name `
                    -Label "failed transition watchdog authorization $name"
            }
            if (
                $failureReceipt.value.format -cne
                    'stratus-successor-transition-watchdog-terminal-v1' -or
                $failureReceipt.value.session_id -cne [string]$eventWitness.session_id -or
                $failureReceipt.value.status -cnotin @('contained', 'failure') -or
                $failureReceipt.value.authorization.api_stage_permitted -or
                $failureReceipt.value.authorization.worker_start_permitted -or
                $failureReceipt.value.authorization.safety_control_start_permitted -or
                $failureReceipt.value.authorization.live_transition_permitted -or
                $failureReceipt.value.authorization.binance_kill_switch_removal_permitted -or
                $failureReceipt.value.authorization.migration_container_permitted
            ) {
                throw 'The failed transition watchdog receipt is not session-bound.'
            }
        }
        catch {
            $watchdogTerminalFailure = $_.Exception.Message
        }
    }
    if ($null -ne $rollbackFailure) {
        throw "Successor transition failed and bounded PostgreSQL rollback also failed. Original: $($transitionFailure.Exception.Message) Rollback: $rollbackFailure Watchdog: $watchdogTerminalFailure"
    }
    if ($emergencyContainmentTriggered) {
        $ownershipDetail = if ($null -ne $ownershipFailure) {
            " Ownership: $ownershipFailure"
        }
        else { '' }
        throw [InvalidOperationException]::new(
            "Successor transition failed after an ambiguous start; emergency containment stopped and re-inspected the exact retained PostgreSQL with restart policy no. Original: $($transitionFailure.Exception.Message)$ownershipDetail Watchdog: $watchdogTerminalFailure"
        )
    }
    if ($null -ne $heartbeatStopFailure -or
        ($postgresStartCommandIssued -and $null -ne $watchdogTerminalFailure)) {
        throw "Successor transition failed with an incomplete watchdog terminal handoff. Original: $($transitionFailure.Exception.Message) Heartbeat: $heartbeatStopFailure Watchdog: $watchdogTerminalFailure"
    }
    throw $transitionFailure
}
finally {
    try {
        if ($null -ne $eventWitness) {
            Stop-DockerEventWitness -Witness $eventWitness
        }
    }
    finally {
        try {
            if ($null -ne $watchdogHeartbeatPublisher) {
                $watchdogHeartbeatPublisher.Dispose()
            }
            if ($null -ne $watchdogProcess) {
                $watchdogProcess.process.Refresh()
                if ($watchdogProcess.process.HasExited) {
                    $watchdogProcess.collector.Dispose()
                    if (Test-Path -LiteralPath $watchdogProcess.temp_root) {
                        Remove-Item -LiteralPath $watchdogProcess.temp_root -Recurse -Force `
                            -ErrorAction SilentlyContinue
                    }
                }
            }
        }
        finally {
            if ($null -ne $script:hbaReadLock) {
                $script:hbaReadLock.Dispose()
                $script:hbaReadLock = $null
                $script:hbaReadLockPath = $null
            }
            if ($null -ne $script:watchdogScriptReadLock) {
                $script:watchdogScriptReadLock.Dispose()
                $script:watchdogScriptReadLock = $null
            }
            if ($null -ne $script:watchdogHostReadLock) {
                $script:watchdogHostReadLock.Dispose()
                $script:watchdogHostReadLock = $null
            }
        }
    }
}
