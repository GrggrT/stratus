[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Root,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{32}$')]
    [string] $SessionId,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int] $OwnerPid,

    [Parameter(Mandatory = $true)]
    [string] $OwnerStartTimeUtc,

    [Parameter(Mandatory = $true)]
    [string] $DeadlineUtc,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 60)]
    [int] $HeartbeatTimeoutSeconds,

    [Parameter(Mandatory = $true)]
    [string] $LeasePath,

    [Parameter(Mandatory = $true)]
    [string] $ReceiptPath,

    [Parameter(Mandatory = $true)]
    [string] $HeartbeatPath,

    [Parameter(Mandatory = $true)]
    [string] $CommitPath,

    [Parameter(Mandatory = $true)]
    [string] $LiveAcceptancePath,

    [Parameter(Mandatory = $true)]
    [string] $PreStartAttestationPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedPreStartSha256,

    [Parameter(Mandatory = $true)]
    [string] $StartFencePath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ExpectedStartFenceSha256,

    [Parameter(Mandatory = $true)]
    [string] $StartRevokePath,

    [Parameter(Mandatory = $true)]
    [string] $StartAttemptPath,

    [Parameter(Mandatory = $true)]
    [string] $StartCompletionPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $PostgresContainer,

    [ValidatePattern('\A[0-9a-f]{64}\z')]
    [string] $ParentStageNonce,

    [string] $ParentCommandSessionHandle
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:parentTransitionMode = $PSBoundParameters.ContainsKey('ParentStageNonce')
if ($script:parentTransitionMode -ne $PSBoundParameters.ContainsKey('ParentCommandSessionHandle')) {
    throw 'Parent Transition nonce and command-session handle must be supplied together.'
}
if ($script:parentTransitionMode -and -not [regex]::IsMatch($ParentStageNonce, '\A[0-9a-f]{64}\z')) {
    throw 'Parent Transition mode requires an explicit lowercase 64-hex nonce.'
}
if ($script:parentTransitionMode) {
    # Syntax is not native ownership or permission to load an unqualified DLL.
    # This cut precedes reservations, fence access and the very first Docker call.
    [uint64] $commandHandleNumber = 0
    if (-not [uint64]::TryParse($ParentCommandSessionHandle, [Globalization.NumberStyles]::None,
        [Globalization.CultureInfo]::InvariantCulture, [ref]$commandHandleNumber) -or
        $commandHandleNumber -eq 0 -or
        $commandHandleNumber.ToString([Globalization.CultureInfo]::InvariantCulture) -cne $ParentCommandSessionHandle -or
        ([IntPtr]::Size -eq 4 -and $commandHandleNumber -ge [uint32]::MaxValue) -or
        ([IntPtr]::Size -eq 8 -and $commandHandleNumber -ge [int64]::MaxValue)) {
        throw 'Parent Transition command-session handle must use canonical native-width syntax.'
    }
    throw 'Parent Transition qualified runtime import and command adoption are WIP; no command may run.'
}
$script:parentTransitionStartedAt = $null
$script:parentTransitionTerminalAttempted = $false

# This program is deliberately useful only for the one retained production
# PostgreSQL object and the reviewed Docker Desktop engine.  It is a recovery
# boundary, not a general container supervisor.
$productionPostgresId =
    '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$productionPostgresName = '/weatherpoly-postgres-1'
$postgresImage = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$postgresImageId =
    'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$approvedDockerExecutableSha256 =
    '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d'
$expectedDockerContext = 'desktop-linux'
$expectedDockerEndpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
$expectedDockerEngineId = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
$expectedDockerServerVersion = '28.3.2'
$expectedDockerEngineName = 'docker-desktop'
$expectedDockerRootDir = '/var/lib/docker'
$defaultNetworkName = 'weatherpoly_default'
$defaultNetworkId =
    '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
$defaultNetworkLabelsSha256 =
    '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d'
$defaultNetworkCreated = '2026-07-05T17:30:21.808081932Z'
$safetyNetworkName = 'weatherpoly_safety-plane'
$safetyNetworkId =
    '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
$safetyNetworkLabelsSha256 =
    '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7'
$safetyNetworkCreated = '2026-07-30T09:19:55.325597748Z'
$networkOptionsSha256 =
    'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569'
$dataVolumeSource = '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data'
$containmentStopTimeoutSeconds = 15
$dockerProofWallClockTimeoutSeconds = 15
$dockerStopWallClockTimeoutSeconds = 25
$expectedStartProcessTimeoutMilliseconds = 15000
$startFenceAcquireAdvisorySeconds = 20
$maximumLeaseSeconds = 600
$clockSkewSeconds = 2
$pollMilliseconds = 200

$script:dockerExecutable = $null
$script:dockerExecutableSha256 = $null
$script:dockerExecutableLock = $null
$script:cleanupFailures = [Collections.Generic.List[string]]::new()
$script:receiptStream = $null
$script:terminalReceiptWritten = $false
$script:leaseArmed = $false
$script:leaseSha256 = $null
$script:leaseArmedAt = [DateTimeOffset]::MinValue
$script:initialEngine = $null
$script:initialPostgres = $null
$script:startFenceLock = $null

function Get-Sha256Bytes {
    param([Parameter(Mandatory = $true)][byte[]] $Bytes)

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

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string] $Value)

    return Get-Sha256Bytes -Bytes ([Text.UTF8Encoding]::new($false).GetBytes($Value))
}

function Assert-ParentTransitionWireIdentity {
    # Pure scalar validation only; actual identity comes from our launch call site.
    param([object] $RunId, [object] $Nonce, [object] $ProcessId,
        [object] $StartedAt, [object] $Path, [object] $Sha256)
    foreach ($pair in @(@($RunId, 32), @($Nonce, 64), @($Sha256, 64))) {
        if ($pair[0] -isnot [string] -or -not [regex]::IsMatch($pair[0], ('\A[0-9a-f]{' + $pair[1] + '}\z'))) {
            throw 'Parent Transition frame requires exact native lowercase hex strings.'
        }
    }
    if (($ProcessId -isnot [int] -and $ProcessId -isnot [long]) -or $ProcessId -lt 1 -or $ProcessId -gt [int]::MaxValue) {
        throw 'Parent Transition frame requires a native positive process ID.'
    }
    if ($StartedAt -isnot [string]) { throw 'Parent Transition process birth must be a native canonical UTC string.' }
    $birth = [DateTimeOffset]::ParseExact($StartedAt, 'o', [Globalization.CultureInfo]::InvariantCulture)
    if ($birth.Offset -ne [TimeSpan]::Zero -or $birth.ToString('o') -cne $StartedAt) {
        throw 'Parent Transition process birth must be canonical UTC.'
    }
    if ($Path -isnot [string] -or -not [IO.Path]::IsPathRooted($Path) -or
        [IO.Path]::GetFullPath($Path) -cne $Path -or $Path.IndexOf([char]0) -ge 0 -or
        $Path.Contains("`r") -or $Path.Contains("`n")) {
        throw 'Parent Transition artifact path must be canonical and absolute.'
    }
}

function New-ParentTransitionReadyFrameBytes {
    param([object] $RunId, [object] $Nonce, [object] $ProcessId,
        [object] $StartedAt, [object] $LeasePath, [object] $LeaseSha256)
    Assert-ParentTransitionWireIdentity $RunId $Nonce $ProcessId $StartedAt $LeasePath $LeaseSha256
    $frame = [ordered]@{
        format = 'stratus-parent-transition-ready-v1'; session_id = $RunId; nonce = $Nonce
        pid = [int]$ProcessId; started_at = $StartedAt; lease_path = $LeasePath; lease_sha256 = $LeaseSha256
    }
    $bytes = [Text.UTF8Encoding]::new($false, $true).GetBytes(($frame | ConvertTo-Json -Depth 4 -Compress) + "`n")
    if ($bytes.Length -gt 8192) { throw 'Parent Transition ready frame exceeds its fixed byte bound.' }
    return ,$bytes
}

function New-ParentTransitionCompleteFrameBytes {
    param([object] $RunId, [object] $Nonce, [object] $ProcessId,
        [object] $StartedAt, [object] $ReceiptPath, [object] $ReceiptSha256, [object] $ReceiptBytes)
    Assert-ParentTransitionWireIdentity $RunId $Nonce $ProcessId $StartedAt $ReceiptPath $ReceiptSha256
    if (($ReceiptBytes -isnot [int] -and $ReceiptBytes -isnot [long]) -or $ReceiptBytes -lt 1 -or $ReceiptBytes -gt 262144) {
        throw 'Parent Transition receipt size must be a native integer within 1..262144.'
    }
    $frame = [ordered]@{
        format = 'stratus-parent-transition-complete-v1'; session_id = $RunId; nonce = $Nonce
        pid = [int]$ProcessId; started_at = $StartedAt; receipt_path = $ReceiptPath
        receipt_sha256 = $ReceiptSha256; receipt_bytes = [int]$ReceiptBytes
    }
    $bytes = [Text.UTF8Encoding]::new($false, $true).GetBytes(($frame | ConvertTo-Json -Depth 4 -Compress) + "`n")
    if ($bytes.Length -gt 8192) { throw 'Parent Transition complete frame exceeds its fixed byte bound.' }
    return ,$bytes
}

function Assert-ParentTransitionWirePaths {
    param([string] $RootPath, [string[]] $ArtifactPaths)
    if ([IO.Path]::GetFullPath($RootPath) -cne $RootPath -or -not [IO.Path]::IsPathRooted($RootPath)) {
        throw 'Parent Transition root must be supplied as a canonical absolute path.'
    }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $ArtifactPaths) {
        if ([IO.Path]::GetFullPath($path) -cne $path -or -not [IO.Path]::IsPathRooted($path) -or
            -not $path.StartsWith($RootPath.TrimEnd('\', '/') + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $seen.Add($path) -or $path.IndexOf([char]0) -ge 0 -or $path.Contains("`r") -or $path.Contains("`n")) {
            throw 'Parent Transition paths must be canonical, distinct and below the approved root.'
        }
        $cursor = $path; $leaf = $true
        while (-not [string]::IsNullOrEmpty($cursor)) {
            $item = $null
            try { $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop }
            catch [Management.Automation.ItemNotFoundException] {
                if (-not $leaf) { throw 'Parent Transition artifact parent directory must already exist.' }
            }
            if ($null -ne $item) {
                if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                    ($leaf -and $item.PSIsContainer) -or (-not $leaf -and -not $item.PSIsContainer)) {
                    throw 'Parent Transition artifact ancestry is not regular.'
                }
            }
            $leaf = $false
            $cursor = [IO.Path]::GetDirectoryName($cursor)
        }
    }
    if ($seen.Count -ne 10) { throw 'Parent Transition requires exactly ten distinct artifact paths.' }
}

function Write-TransitionTerminalOutput {
    param([object] $Terminal)
    if (-not $script:parentTransitionMode) { $Terminal.json; return }
    if (-not $script:terminalReceiptWritten -or $script:parentTransitionTerminalAttempted) {
        throw 'Parent Transition terminal wire requires its single published receipt.'
    }
    $frame = New-ParentTransitionCompleteFrameBytes -RunId $SessionId -Nonce $ParentStageNonce -ProcessId $PID `
        -StartedAt $script:parentTransitionStartedAt -ReceiptPath $absoluteReceiptPath `
        -ReceiptSha256 $Terminal.sha256 -ReceiptBytes $Terminal.byte_count
    $script:parentTransitionTerminalAttempted = $true
    $stdout = [Console]::OpenStandardOutput()
    $stdout.Write($frame, 0, $frame.Length)
    $stdout.Flush()
}

function Assert-RegularDirectory {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) {
        throw "Required directory is missing or linked: $LiteralPath"
    }
    return $item
}

function Assert-RegularFile {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [switch] $AllowEmpty
    )

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (-not $AllowEmpty -and $item.Length -le 0)
    ) {
        throw "Required file is missing, empty, or linked: $LiteralPath"
    }
    return $item
}

function Assert-PathBelowRoot {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $AbsoluteRoot,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $fullPath = [IO.Path]::GetFullPath($LiteralPath)
    $prefix = $AbsoluteRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must remain below the exact transition root."
    }
    return $fullPath
}

function Read-RegularFileBytes {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $Label,
        [int64] $MaximumBytes = 4194304
    )

    $item = Assert-RegularFile -LiteralPath $LiteralPath
    if ([int64]$item.Length -gt $MaximumBytes) {
        throw "$Label exceeds its bounded file size."
    }
    $stream = [IO.FileStream]::new(
        [IO.Path]::GetFullPath($LiteralPath),
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        4096,
        [IO.FileOptions]::SequentialScan
    )
    try {
        if ($stream.Length -le 0 -or $stream.Length -gt $MaximumBytes) {
            throw "$Label changed size while it was opened."
        }
        $bytes = [byte[]]::new([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) {
                throw "$Label ended before its declared length."
            }
            $offset += $read
        }
        if ($stream.ReadByte() -ne -1) {
            throw "$Label grew while it was read."
        }
        return $bytes
    }
    finally {
        $stream.Dispose()
    }
}

function ConvertFrom-StrictUtf8JsonObjectBytes {
    param(
        [Parameter(Mandatory = $true)][byte[]] $Bytes,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if (
        $Bytes.Length -ge 3 -and
        $Bytes[0] -eq 0xef -and $Bytes[1] -eq 0xbb -and $Bytes[2] -eq 0xbf
    ) {
        throw "$Label must be canonical UTF-8 without a byte-order mark."
    }
    try {
        $json = [Text.UTF8Encoding]::new($false, $true).GetString($Bytes)
    }
    catch {
        throw "$Label is not canonical UTF-8."
    }
    $trimmed = $json.Trim()
    if (-not $trimmed.StartsWith('{') -or -not $trimmed.EndsWith('}')) {
        throw "$Label must contain one JSON object."
    }
    try {
        $command = Get-Command -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
            -CommandType Cmdlet -ErrorAction Stop
        $parameters = @{ InputObject = $trimmed }
        if ($command.Parameters.ContainsKey('DateKind')) {
            $parameters['DateKind'] = 'String'
        }
        $value = Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
    }
    catch {
        throw "$Label contains invalid JSON."
    }
    Assert-NativeJsonObject -Value $value -Label $Label
    return $value
}

function Read-StrictJsonFile {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $Label,
        [int64] $MaximumBytes = 4194304
    )

    $bytes = Read-RegularFileBytes -LiteralPath $LiteralPath -Label $Label `
        -MaximumBytes $MaximumBytes
    return [pscustomobject]@{
        bytes = $bytes
        sha256 = Get-Sha256Bytes -Bytes $bytes
        value = ConvertFrom-StrictUtf8JsonObjectBytes -Bytes $bytes -Label $Label
    }
}

function Get-HeldFileStreamSha256 {
    param(
        [Parameter(Mandatory = $true)][IO.FileStream] $Stream,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($Stream.SafeFileHandle.IsClosed -or $Stream.SafeFileHandle.IsInvalid) {
        throw "$Label handle is not held."
    }
    $Stream.Position = 0
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($Stream)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $Stream.Position = 0
    }
}

function Open-ExclusiveStartFenceOnce {
    try {
        return [IO.FileStream]::new(
            $absoluteStartFencePath,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::SequentialScan
        )
    }
    catch [IO.IOException] {
        return $null
    }
}

function Assert-HeldStartFence {
    param([Parameter(Mandatory = $true)][IO.FileStream] $Stream)

    if ($Stream.Length -le 0 -or $Stream.Length -gt 65536) {
        throw 'The successor PostgreSQL start fence has an invalid byte length.'
    }
    $bytes = [byte[]]::new([int]$Stream.Length)
    $Stream.Position = 0
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $read = $Stream.Read($bytes, $offset, $bytes.Length - $offset)
        if ($read -le 0) {
            throw 'The successor PostgreSQL start fence ended during its held read.'
        }
        $offset += $read
    }
    $Stream.Position = 0
    $sha256 = Get-Sha256Bytes -Bytes $bytes
    if ($sha256 -cne $ExpectedStartFenceSha256) {
        throw 'The successor PostgreSQL start fence bytes do not match the handoff.'
    }
    $fence = ConvertFrom-StrictUtf8JsonObjectBytes -Bytes $bytes `
        -Label 'successor PostgreSQL start fence'
    Assert-ExactProperties -Value $fence -Label 'successor PostgreSQL start fence' `
        -Expected @(
            'format', 'session_id', 'owner_pid', 'owner_start_time_utc', 'created_at'
        )
    foreach ($name in @('format', 'session_id', 'owner_start_time_utc', 'created_at')) {
        Assert-NativeJsonString -Value $fence.$name `
            -Label "successor PostgreSQL start fence $name"
    }
    Assert-NativeJsonInteger -Value $fence.owner_pid `
        -Label 'successor PostgreSQL start fence owner_pid'
    $createdAt = ConvertTo-RequiredUtcTime -Value $fence.created_at `
        -Label 'successor PostgreSQL start fence created_at' -RequireCanonical
    if (
        $fence.format -cne 'stratus-successor-transition-start-fence-v1' -or
        $fence.session_id -cne $SessionId -or
        $fence.owner_pid -ne $OwnerPid -or
        $fence.owner_start_time_utc -cne $ownerStart.ToString('o') -or
        $createdAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds) -or
        $createdAt -gt $hardDeadline
    ) {
        throw 'The successor PostgreSQL start fence identity is not exact.'
    }
    return [pscustomobject]@{
        path = $absoluteStartFencePath
        sha256 = $sha256
        bytes = [int64]$bytes.Length
        created_at = $createdAt.ToString('o')
    }
}

function Publish-StartRevokeMarker {
    param([Parameter(Mandatory = $true)][string] $Trigger)

    $revokedAt = [DateTimeOffset]::UtcNow
    $marker = [ordered]@{
        format = 'stratus-successor-transition-start-revoked-v1'
        session_id = $SessionId
        watchdog_pid = [int]$PID
        owner_pid = [int]$OwnerPid
        trigger = $Trigger
        revoked_at = $revokedAt.ToString('o')
        start_fence_path = $absoluteStartFencePath
        start_fence_sha256 = $ExpectedStartFenceSha256
        postgres_start_permitted = $false
    }
    $json = $marker | ConvertTo-Json -Depth 8
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream = $null
    $created = $false
    $publicationError = $null
    try {
        $stream = [IO.FileStream]::new(
            $absoluteStartRevokePath,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        $created = $true
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    catch {
        $publicationError = $_.Exception.Message
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
    return [pscustomobject]@{
        path = $absoluteStartRevokePath
        created = [bool]$created
        present = [bool](Test-Path -LiteralPath $absoluteStartRevokePath)
        intended_sha256 = Get-Sha256Bytes -Bytes $bytes
        publication_error = $publicationError
        revoked_at = $revokedAt.ToString('o')
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

    if (
        $null -eq $Value -or
        $Value.GetType().FullName -cnotin @('System.Int32', 'System.Int64')
    ) {
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
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if (($actual -join "`n") -cne ($wanted -join "`n")) {
        throw "$Label does not have the exact required property set."
    }
}

function Assert-RequiredProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Required,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-NativeJsonObject -Value $Value -Label $Label
    foreach ($name in $Required) {
        if ($null -eq $Value.PSObject.Properties[$name]) {
            throw "$Label is missing required property $name."
        }
    }
}

function ConvertTo-RequiredUtcTime {
    param(
        [Parameter(Mandatory = $true)][string] $Value,
        [Parameter(Mandatory = $true)][string] $Label,
        [switch] $RequireCanonical
    )

    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        $Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsed
    )) {
        throw "$Label is not an ISO-8601 timestamp."
    }
    $utc = $parsed.ToUniversalTime()
    if ($RequireCanonical -and $Value -cne $utc.ToString('o')) {
        throw "$Label must be a canonical UTC timestamp."
    }
    return $utc
}

function Assert-StartAttemptDocument {
    param([Parameter(Mandatory = $true)][object] $Document)

    $attempt = $Document.value
    Assert-ExactProperties -Value $attempt -Label 'PostgreSQL start attempt' `
        -Expected @(
            'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
            'start_fence_path', 'start_fence_sha256', 'command', 'issued_at'
        )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'start_fence_path',
        'start_fence_sha256', 'issued_at'
    )) {
        Assert-NativeJsonString -Value $attempt.$name `
            -Label "PostgreSQL start attempt $name"
    }
    Assert-NativeJsonInteger -Value $attempt.owner_pid `
        -Label 'PostgreSQL start attempt owner_pid'
    if ($attempt.command -isnot [Array]) {
        throw 'PostgreSQL start attempt command must be a native JSON array.'
    }
    foreach ($token in @($attempt.command)) {
        Assert-NativeJsonString -Value $token `
            -Label 'PostgreSQL start attempt command token'
    }
    $command = @($attempt.command)
    $issuedAt = ConvertTo-RequiredUtcTime -Value $attempt.issued_at `
        -Label 'PostgreSQL start attempt issued_at' -RequireCanonical
    if (
        $attempt.format -cne 'stratus-successor-transition-start-attempt-v1' -or
        $attempt.session_id -cne $SessionId -or
        $attempt.owner_pid -ne $OwnerPid -or
        $attempt.owner_start_time_utc -cne $ownerStart.ToString('o') -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($attempt.start_fence_path),
            $absoluteStartFencePath
        ) -or
        $attempt.start_fence_sha256 -cne $ExpectedStartFenceSha256 -or
        $command.Count -ne 2 -or $command[0] -cne 'start' -or
        $command[1] -cne $productionPostgresId -or
        $issuedAt -lt $script:leaseArmedAt.AddSeconds(-$clockSkewSeconds) -or
        $issuedAt -gt $hardDeadline -or
        $issuedAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds)
    ) {
        throw 'PostgreSQL start attempt is not exact and lease-bound.'
    }
    return [pscustomobject]@{
        document = $Document
        value = $attempt
        sha256 = [string]$Document.sha256
        issued_at = $issuedAt.ToString('o')
    }
}

function Assert-StartCompletionDocument {
    param(
        [Parameter(Mandatory = $true)][object] $Document,
        [Parameter(Mandatory = $true)][object] $Attempt
    )

    $completion = $Document.value
    Assert-ExactProperties -Value $completion -Label 'PostgreSQL start completion' `
        -Expected @(
            'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
            'start_attempt_path', 'start_attempt_sha256', 'outcome',
            'completed_at', 'bounded_start'
        )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'start_attempt_path',
        'start_attempt_sha256', 'outcome', 'completed_at'
    )) {
        Assert-NativeJsonString -Value $completion.$name `
            -Label "PostgreSQL start completion $name"
    }
    Assert-NativeJsonInteger -Value $completion.owner_pid `
        -Label 'PostgreSQL start completion owner_pid'
    Assert-NativeJsonObject -Value $completion.bounded_start `
        -Label 'PostgreSQL bounded start completion'
    $bounded = $completion.bounded_start
    Assert-ExactProperties -Value $bounded -Label 'PostgreSQL bounded start completion' `
        -Expected @(
            'command', 'docker_executable_path', 'docker_executable_sha256',
            'process_timeout_milliseconds', 'stdout_maximum_bytes',
            'stderr_maximum_bytes', 'requested_at', 'completed_at', 'exit_code',
            'output_container_id', 'timed_out', 'output_exceeded',
            'process_started', 'process_exited', 'process_id', 'kill_issued'
        )
    if ($bounded.command -isnot [Array]) {
        throw 'PostgreSQL bounded start command must be a native JSON array.'
    }
    foreach ($token in @($bounded.command)) {
        Assert-NativeJsonString -Value $token `
            -Label 'PostgreSQL bounded start command token'
    }
    foreach ($name in @(
        'docker_executable_path', 'docker_executable_sha256', 'requested_at',
        'completed_at', 'output_container_id'
    )) {
        Assert-NativeJsonString -Value $bounded.$name `
            -Label "PostgreSQL bounded start $name"
    }
    foreach ($name in @(
        'process_timeout_milliseconds', 'stdout_maximum_bytes',
        'stderr_maximum_bytes', 'exit_code', 'process_id'
    )) {
        Assert-NativeJsonInteger -Value $bounded.$name `
            -Label "PostgreSQL bounded start $name"
    }
    foreach ($name in @(
        'timed_out', 'output_exceeded', 'process_started', 'process_exited',
        'kill_issued'
    )) {
        Assert-NativeJsonBoolean -Value $bounded.$name `
            -Label "PostgreSQL bounded start $name"
    }
    $completedAt = ConvertTo-RequiredUtcTime -Value $completion.completed_at `
        -Label 'PostgreSQL start completion completed_at' -RequireCanonical
    $requestedAt = ConvertTo-RequiredUtcTime -Value $bounded.requested_at `
        -Label 'PostgreSQL bounded start requested_at' -RequireCanonical
    $boundedCompletedAt = ConvertTo-RequiredUtcTime -Value $bounded.completed_at `
        -Label 'PostgreSQL bounded start completed_at' -RequireCanonical
    $command = @($bounded.command)
    if (
        $completion.format -cne 'stratus-successor-transition-start-complete-v1' -or
        $completion.session_id -cne $SessionId -or
        $completion.owner_pid -ne $OwnerPid -or
        $completion.owner_start_time_utc -cne $ownerStart.ToString('o') -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($completion.start_attempt_path),
            $absoluteStartAttemptPath
        ) -or
        $completion.start_attempt_sha256 -cne $Attempt.sha256 -or
        $completion.outcome -cne 'started' -or
        $completedAt -ne $boundedCompletedAt -or
        $requestedAt -lt (
            (ConvertTo-RequiredUtcTime -Value $Attempt.issued_at `
                -Label 'accepted start attempt issued_at' -RequireCanonical)
        ).AddSeconds(-$clockSkewSeconds) -or
        $boundedCompletedAt -lt $requestedAt -or
        $boundedCompletedAt -gt $hardDeadline -or
        $boundedCompletedAt -gt [DateTimeOffset]::UtcNow.AddSeconds($clockSkewSeconds) -or
        $command.Count -ne 2 -or $command[0] -cne 'start' -or
        $command[1] -cne $productionPostgresId -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($bounded.docker_executable_path),
            $script:dockerExecutable
        ) -or
        $bounded.docker_executable_sha256 -cne $script:dockerExecutableSha256 -or
        $bounded.process_timeout_milliseconds -ne
            $expectedStartProcessTimeoutMilliseconds -or
        $bounded.stdout_maximum_bytes -ne 4096 -or
        $bounded.stderr_maximum_bytes -ne 4096 -or
        $bounded.exit_code -ne 0 -or
        $bounded.output_container_id -cne $productionPostgresId -or
        $bounded.timed_out -or $bounded.output_exceeded -or
        -not $bounded.process_started -or -not $bounded.process_exited -or
        $bounded.process_id -lt 1 -or $bounded.kill_issued
    ) {
        throw 'PostgreSQL start completion is not an exact positive child-process acknowledgement.'
    }
    return [pscustomobject]@{
        document = $Document
        value = $completion
        sha256 = [string]$Document.sha256
        completed_at = $completedAt.ToString('o')
        bounded_start = $bounded
    }
}

function Get-StartProtocolState {
    param([switch] $ThrowOnInvalid)

    try {
        $attemptPresent = Test-Path -LiteralPath $absoluteStartAttemptPath
        $completionPresent = Test-Path -LiteralPath $absoluteStartCompletionPath
        if (-not $attemptPresent) {
            if ($completionPresent) {
                throw 'PostgreSQL start completion exists without its start attempt.'
            }
            return [pscustomobject]@{
                status = 'none'
                attempt = $null
                completion = $null
                failure = $null
            }
        }
        $attemptDocument = Read-StrictJsonFile `
            -LiteralPath $absoluteStartAttemptPath `
            -Label 'PostgreSQL start attempt' -MaximumBytes 131072
        $attempt = Assert-StartAttemptDocument -Document $attemptDocument
        if (-not $completionPresent) {
            return [pscustomobject]@{
                status = 'unresolved'
                attempt = $attempt
                completion = $null
                failure = $null
            }
        }
        $completionDocument = Read-StrictJsonFile `
            -LiteralPath $absoluteStartCompletionPath `
            -Label 'PostgreSQL start completion' -MaximumBytes 262144
        $completion = Assert-StartCompletionDocument -Document $completionDocument `
            -Attempt $attempt
        return [pscustomobject]@{
            status = 'completed'
            attempt = $attempt
            completion = $completion
            failure = $null
        }
    }
    catch {
        if ($ThrowOnInvalid) { throw }
        return [pscustomobject]@{
            status = 'invalid'
            attempt = $null
            completion = $null
            failure = $_.Exception.Message
        }
    }
}

function Assert-NetworkRepairBinding {
    param(
        [Parameter(Mandatory = $true)][object] $Binding,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-ExactProperties -Value $Binding -Label $Label -Expected @(
        'path', 'sha256', 'payload_sha256', 'accepted_at', 'expires_at',
        'disposition', 'post_first_sha256', 'post_second_sha256',
        'final_attachment_sha256', 'docker_engine_contract_sha256'
    )
    foreach ($name in @(
        'path', 'sha256', 'payload_sha256', 'accepted_at', 'expires_at',
        'disposition', 'post_first_sha256', 'post_second_sha256',
        'final_attachment_sha256', 'docker_engine_contract_sha256'
    )) {
        Assert-NativeJsonString -Value $Binding.$name -Label "$Label $name"
    }
    foreach ($name in @(
        'sha256', 'payload_sha256', 'post_first_sha256', 'post_second_sha256',
        'final_attachment_sha256', 'docker_engine_contract_sha256'
    )) {
        if ($Binding.$name -notmatch '^[0-9a-f]{64}$') {
            throw "$Label $name is not a lowercase SHA-256."
        }
    }
    if ($Binding.disposition -cnotin @(
        'connected_by_this_invocation', 'verified_configured_pending_existing',
        'verified_exact_existing'
    )) {
        throw "$Label has an invalid repair disposition."
    }
    $acceptedAt = ConvertTo-RequiredUtcTime -Value $Binding.accepted_at `
        -Label "$Label accepted_at" -RequireCanonical
    $expiresAt = ConvertTo-RequiredUtcTime -Value $Binding.expires_at `
        -Label "$Label expires_at" -RequireCanonical
    if (
        $expiresAt -ne $acceptedAt.AddSeconds(600) -or
        [DateTimeOffset]::UtcNow -gt $expiresAt.AddSeconds($clockSkewSeconds)
    ) {
        throw "$Label is expired or has an invalid validity window."
    }
    $repairPath = [IO.Path]::GetFullPath([string]$Binding.path)
    $repairBytes = Read-RegularFileBytes -LiteralPath $repairPath `
        -Label "$Label evidence"
    if ((Get-Sha256Bytes -Bytes $repairBytes) -cne $Binding.sha256) {
        throw "$Label evidence bytes do not match its bound SHA-256."
    }
    return ($Binding | ConvertTo-Json -Depth 8 -Compress)
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

function ConvertTo-NativeArgumentToken {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string] $Value)

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
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) {
            [void]$builder.Append(('\' * $slashes))
            $slashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('\' * ($slashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-PinnedDockerBounded {
    param(
        [Parameter(Mandatory = $true)][string[]] $Arguments,
        [int] $WallClockTimeoutSeconds = $dockerProofWallClockTimeoutSeconds
    )

    if (
        [string]::IsNullOrWhiteSpace([string]$script:dockerExecutable) -or
        $null -eq $script:dockerExecutableLock
    ) {
        throw 'Docker was not pinned before use.'
    }
    if (
        $script:dockerExecutableLock.SafeFileHandle.IsClosed -or
        $script:dockerExecutableLock.SafeFileHandle.IsInvalid
    ) {
        throw 'The pinned Docker executable lock is no longer held.'
    }
    $script:dockerExecutableLock.Position = 0
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $currentHash = [BitConverter]::ToString(
            $algorithm.ComputeHash($script:dockerExecutableLock)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $script:dockerExecutableLock.Position = 0
    }
    if ($currentHash -cne $script:dockerExecutableSha256) {
        throw 'Docker executable bytes drifted after pinning.'
    }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $script:dockerExecutable
    $effectiveArguments = @('--context', $expectedDockerContext) + @($Arguments)
    $startInfo.Arguments = (($effectiveArguments | ForEach-Object {
        ConvertTo-NativeArgumentToken -Value ([string]$_)
    }) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw 'Docker child process did not start.'
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $milliseconds = [int]([Math]::Min(
            [int]::MaxValue,
            [Math]::Max(1, $WallClockTimeoutSeconds) * 1000
        ))
        if (-not $process.WaitForExit($milliseconds)) {
            $timeoutFailure =
                "Docker child exceeded the bounded $WallClockTimeoutSeconds-second wall clock."
            $terminationFailures = [Collections.Generic.List[string]]::new()
            try { $process.Kill() }
            catch {
                $terminationFailures.Add(
                    "exact child kill failed: $($_.Exception.Message)"
                )
            }
            try {
                if (-not $process.WaitForExit(2000)) {
                    $terminationFailures.Add(
                        'exact child remained live after the bounded kill wait'
                    )
                }
            }
            catch {
                $terminationFailures.Add(
                    "exact child exit wait failed: $($_.Exception.Message)"
                )
            }
            if ($terminationFailures.Count -gt 0) {
                throw [InvalidOperationException]::new(
                    "$timeoutFailure Cleanup: $($terminationFailures -join ' | ')"
                )
            }
            throw $timeoutFailure
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            throw "Docker child failed with exit code $($process.ExitCode)."
        }
        if (-not [string]::IsNullOrWhiteSpace($stderr)) {
            throw 'Docker child wrote unexpected stderr output.'
        }
        return @($stdout -split "`r?`n" | Where-Object {
            -not [string]::IsNullOrWhiteSpace([string]$_)
        })
    }
    finally {
        $process.Dispose()
    }
}

function Resolve-AndLockApprovedDocker {
    $commands = @(Microsoft.PowerShell.Core\Get-Command -Name 'docker' -All `
        -CommandType Application -ErrorAction Stop)
    $seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    $matches = [Collections.Generic.List[object]]::new()
    foreach ($command in $commands) {
        if ([string]::IsNullOrWhiteSpace([string]$command.Source)) {
            continue
        }
        $path = [IO.Path]::GetFullPath([string]$command.Source)
        if (-not $seen.Add($path)) {
            continue
        }
        try {
            [void](Assert-RegularFile -LiteralPath $path)
            $bytes = [IO.File]::ReadAllBytes($path)
            $sha256 = Get-Sha256Bytes -Bytes $bytes
            if ($sha256 -ceq $approvedDockerExecutableSha256) {
                $matches.Add([pscustomobject]@{ path = $path; sha256 = $sha256 })
            }
        }
        catch {
            continue
        }
    }
    if ($matches.Count -ne 1) {
        throw 'Exactly one approved Docker executable must resolve from PATH.'
    }
    $match = $matches[0]
    $lock = [IO.FileStream]::new(
        [string]$match.path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read,
        4096,
        [IO.FileOptions]::SequentialScan
    )
    try {
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $lockedHash = [BitConverter]::ToString(
                $algorithm.ComputeHash($lock)
            ).Replace('-', '').ToLowerInvariant()
            $lock.Position = 0
        }
        finally {
            $algorithm.Dispose()
        }
        if ($lockedHash -cne $approvedDockerExecutableSha256) {
            throw 'Docker executable changed while acquiring its byte lock.'
        }
        $script:dockerExecutable = [string]$match.path
        $script:dockerExecutableSha256 = $lockedHash
        $script:dockerExecutableLock = $lock
        return [pscustomobject]@{
            path = [string]$match.path
            sha256 = $lockedHash
            bytes = [int64]$lock.Length
        }
    }
    catch {
        $lock.Dispose()
        throw
    }
}

function Get-OneLine {
    param([Parameter(Mandatory = $true)][string[]] $Lines, [string] $Label)

    if ($Lines.Count -ne 1) {
        throw "$Label did not return exactly one line."
    }
    return [string]$Lines[0]
}

function Get-OneDockerJsonObject {
    param([Parameter(Mandatory = $true)][string[]] $Arguments, [string] $Label)

    $line = Get-OneLine -Lines (Invoke-PinnedDockerBounded -Arguments $Arguments) `
        -Label $Label
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($line)
    return ConvertFrom-StrictUtf8JsonObjectBytes -Bytes $bytes -Label $Label
}

function Get-DockerEngineBoundary {
    foreach ($name in @(
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
        'DOCKER_CONFIG', 'DOCKER_API_VERSION', 'DOCKER_CLI_PLUGIN_EXTRA_DIRS'
    )) {
        if (Test-Path -LiteralPath "Env:$name") {
            throw "Docker endpoint override is forbidden for watchdog: $name"
        }
    }
    $context = Get-OneLine -Label 'Docker context' -Lines (
        Invoke-PinnedDockerBounded -Arguments @('context', 'show')
    )
    $endpoint = Get-OneDockerJsonObject -Label 'Docker endpoint' -Arguments @(
        'context', 'inspect', $context, '--format', '{{json .Endpoints.docker}}'
    )
    $info = Get-OneDockerJsonObject -Label 'Docker engine' -Arguments @(
        'info', '--format', '{{json .}}'
    )
    Assert-NativeJsonString -Value $endpoint.Host -Label 'Docker endpoint Host'
    foreach ($name in @('ID', 'ServerVersion', 'Name', 'DockerRootDir')) {
        Assert-NativeJsonString -Value $info.$name -Label "Docker engine $name"
    }
    $skipTls = $false
    if ($null -ne $endpoint.PSObject.Properties['SkipTLSVerify']) {
        Assert-NativeJsonBoolean -Value $endpoint.SkipTLSVerify `
            -Label 'Docker endpoint SkipTLSVerify'
        $skipTls = [bool]$endpoint.SkipTLSVerify
    }
    if (
        $context -cne $expectedDockerContext -or
        $endpoint.Host -cne $expectedDockerEndpoint -or
        $skipTls -or
        $info.ID -cne $expectedDockerEngineId -or
        $info.ServerVersion -cne $expectedDockerServerVersion -or
        $info.Name -cne $expectedDockerEngineName -or
        $info.DockerRootDir -cne $expectedDockerRootDir
    ) {
        throw 'Docker context or engine does not match the exact approved boundary.'
    }
    $contract = @(
        $script:dockerExecutable, $script:dockerExecutableSha256,
        [string]$script:dockerExecutableLock.Length, $context,
        [string]$endpoint.Host, 'false', [string]$info.ID,
        [string]$info.ServerVersion, [string]$info.Name,
        [string]$info.DockerRootDir
    ) -join "`0"
    return [pscustomobject]@{
        docker_executable_path = $script:dockerExecutable
        docker_executable_sha256 = $script:dockerExecutableSha256
        docker_executable_bytes = [int64]$script:dockerExecutableLock.Length
        context = $context
        endpoint = [string]$endpoint.Host
        skip_tls_verify = $false
        engine_id = [string]$info.ID
        server_version = [string]$info.ServerVersion
        engine_name = [string]$info.Name
        docker_root_dir = [string]$info.DockerRootDir
        contract_sha256 = Get-Sha256Text -Value ($contract + "`n")
    }
}

function Assert-EngineUnchanged {
    param([Parameter(Mandatory = $true)][object] $Boundary)

    if (
        $null -eq $script:initialEngine -or
        $Boundary.contract_sha256 -cne $script:initialEngine.contract_sha256
    ) {
        throw 'Docker engine identity drifted during watchdog ownership.'
    }
}

function Get-HostPathAliases {
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

function Assert-ProductionNetworks {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Stopped', 'Running')]
        [string] $State
    )

    foreach ($contract in @(
        [pscustomobject]@{
            name = $defaultNetworkName; id = $defaultNetworkId
            subnet = '172.20.0.0/16'; gateway = '172.20.0.1'
            labels_sha256 = $defaultNetworkLabelsSha256
            created = $defaultNetworkCreated
        },
        [pscustomobject]@{
            name = $safetyNetworkName; id = $safetyNetworkId
            subnet = '172.22.0.0/16'; gateway = '172.22.0.1'
            labels_sha256 = $safetyNetworkLabelsSha256
            created = $safetyNetworkCreated
        }
    )) {
        $network = Get-OneDockerJsonObject -Label "Docker network $($contract.name)" `
            -Arguments @(
                'network', 'inspect', [string]$contract.id,
                '--format', '{{json .}}'
            )
        Assert-NativeJsonObject -Value $network.IPAM -Label 'Docker network IPAM'
        Assert-NativeJsonObject -Value $network.Options -Label 'Docker network options'
        Assert-NativeJsonObject -Value $network.Labels -Label 'Docker network labels'
        Assert-NativeJsonObject -Value $network.ConfigFrom -Label 'Docker network ConfigFrom'
        Assert-NativeJsonObject -Value $network.Containers -Label 'Docker network Containers'
        foreach ($name in @('Name', 'Id', 'Created', 'Driver', 'Scope')) {
            Assert-NativeJsonString -Value $network.$name -Label "Docker network $name"
        }
        foreach ($name in @(
            'EnableIPv4', 'EnableIPv6', 'Internal', 'Attachable', 'Ingress', 'ConfigOnly'
        )) {
            Assert-NativeJsonBoolean -Value $network.$name -Label "Docker network $name"
        }
        Assert-NativeJsonString -Value $network.IPAM.Driver -Label 'Docker network IPAM.Driver'
        Assert-NativeJsonString -Value $network.ConfigFrom.Network `
            -Label 'Docker network ConfigFrom.Network'
        if ($network.IPAM.Config -isnot [Array]) {
            throw 'Docker network IPAM.Config is not a native JSON array.'
        }
        if (@($network.IPAM.Config).Count -eq 1) {
            Assert-NativeJsonObject -Value $network.IPAM.Config[0] `
                -Label 'Docker network IPAM.Config'
            Assert-ExactProperties -Value $network.IPAM.Config[0] `
                -Label 'Docker network IPAM.Config' `
                -Expected @('Subnet', 'Gateway', 'IPRange', 'AuxiliaryAddresses')
            Assert-NativeJsonString -Value $network.IPAM.Config[0].Subnet `
                -Label 'Docker network subnet'
            Assert-NativeJsonString -Value $network.IPAM.Config[0].Gateway `
                -Label 'Docker network gateway'
            Assert-NativeJsonString -Value $network.IPAM.Config[0].IPRange `
                -Label 'Docker network IP range'
        }
        $containerIds = @($network.Containers.PSObject.Properties | ForEach-Object {
            [string]$_.Name
        })
        [Array]::Sort($containerIds, [StringComparer]::Ordinal)
        $expectedContainerIds = if ($State -ceq 'Running') {
            @($productionPostgresId)
        }
        else {
            @()
        }
        if (
            $network.Name -cne $contract.name -or
            $network.Id -cne $contract.id -or
            $network.Created -cne $contract.created -or
            $network.Driver -cne 'bridge' -or
            $network.Scope -cne 'local' -or
            -not $network.EnableIPv4 -or
            $network.Internal -or $network.Attachable -or
            $network.Ingress -or $network.EnableIPv6 -or $network.ConfigOnly -or
            $network.IPAM.Driver -cne 'default' -or
            $null -ne $network.IPAM.Options -or
            $network.ConfigFrom.Network -cne '' -or
            @($network.IPAM.Config).Count -ne 1 -or
            $network.IPAM.Config[0].Subnet -cne $contract.subnet -or
            $network.IPAM.Config[0].Gateway -cne $contract.gateway -or
            $network.IPAM.Config[0].IPRange -cne '' -or
            $null -ne $network.IPAM.Config[0].AuxiliaryAddresses -or
            (Get-CanonicalPropertyHash -Value $network.Options `
                -Label 'Docker network options') -cne $networkOptionsSha256 -or
            (Get-CanonicalPropertyHash -Value $network.Labels `
                -Label 'Docker network labels') -cne $contract.labels_sha256 -or
            ($containerIds -join "`n") -cne ($expectedContainerIds -join "`n")
        ) {
            throw "Docker network identity/topology is not exact: $($contract.name)"
        }
    }
}

function Get-PostgresBoundary {
    param([Parameter(Mandatory = $true)][ValidateSet('Stopped', 'Running')] $State)

    $inspect = Get-OneDockerJsonObject -Label 'retained PostgreSQL inspect' -Arguments @(
        'inspect', $productionPostgresId, '--format', '{{json .}}'
    )
    foreach ($name in @('Config', 'State', 'HostConfig', 'NetworkSettings')) {
        Assert-NativeJsonObject -Value $inspect.$name -Label "PostgreSQL $name"
    }
    Assert-NativeJsonObject -Value $inspect.HostConfig.RestartPolicy `
        -Label 'PostgreSQL restart policy'
    Assert-NativeJsonObject -Value $inspect.HostConfig.PortBindings `
        -Label 'PostgreSQL port bindings'
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Networks `
        -Label 'PostgreSQL networks'
    Assert-NativeJsonObject -Value $inspect.NetworkSettings.Ports `
        -Label 'PostgreSQL runtime ports'
    Assert-NativeJsonString -Value $inspect.HostConfig.RestartPolicy.Name `
        -Label 'PostgreSQL restart policy name'
    Assert-NativeJsonInteger -Value $inspect.HostConfig.RestartPolicy.MaximumRetryCount `
        -Label 'PostgreSQL restart policy maximum retry count'
    Assert-NativeJsonString -Value $inspect.HostConfig.NetworkMode `
        -Label 'PostgreSQL network mode'
    foreach ($name in @('Id', 'Name', 'Image')) {
        Assert-NativeJsonString -Value $inspect.$name -Label "PostgreSQL $name"
    }
    foreach ($name in @('Image')) {
        Assert-NativeJsonString -Value $inspect.Config.$name `
            -Label "PostgreSQL Config.$name"
    }
    foreach ($name in @('Status', 'StartedAt', 'FinishedAt', 'Error')) {
        Assert-NativeJsonString -Value $inspect.State.$name `
            -Label "PostgreSQL State.$name"
    }
    foreach ($name in @('Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead')) {
        Assert-NativeJsonBoolean -Value $inspect.State.$name `
            -Label "PostgreSQL State.$name"
    }
    Assert-NativeJsonInteger -Value $inspect.State.Pid -Label 'PostgreSQL State.Pid'
    Assert-NativeJsonInteger -Value $inspect.State.ExitCode `
        -Label 'PostgreSQL State.ExitCode'
    Assert-NativeJsonInteger -Value $inspect.RestartCount -Label 'PostgreSQL RestartCount'
    if (
        $inspect.Id -cne $productionPostgresId -or
        $inspect.Name -cne $productionPostgresName -or
        $inspect.Image -cne $postgresImageId -or
        $inspect.Config.Image -cne $postgresImage -or
        $inspect.RestartCount -ne 0 -or
        $inspect.State.OOMKilled -or $inspect.State.Paused -or
        $inspect.State.Restarting -or $inspect.State.Dead -or
        -not [string]::IsNullOrEmpty([string]$inspect.State.Error) -or
        $inspect.HostConfig.RestartPolicy.Name -cne 'no' -or
        [int]$inspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0 -or
        $inspect.HostConfig.NetworkMode -cne $defaultNetworkName
    ) {
        throw 'Retained PostgreSQL identity or protected restart policy is not exact.'
    }

    $configuredProperties = @(
        $inspect.HostConfig.PortBindings.PSObject.Properties |
            Where-Object { $null -ne $_.Value }
    )
    $configuredPort = $inspect.HostConfig.PortBindings.'5432/tcp'
    if ($configuredPort -isnot [Array]) {
        throw 'PostgreSQL configured port is not a native JSON array.'
    }
    if (@($configuredPort).Count -eq 1) {
        Assert-NativeJsonObject -Value $configuredPort[0] `
            -Label 'PostgreSQL configured port'
        Assert-NativeJsonString -Value $configuredPort[0].HostIp `
            -Label 'PostgreSQL configured HostIp'
        Assert-NativeJsonString -Value $configuredPort[0].HostPort `
            -Label 'PostgreSQL configured HostPort'
    }
    if (
        $configuredProperties.Count -ne 1 -or
        $configuredProperties[0].Name -cne '5432/tcp' -or
        @($configuredPort).Count -ne 1 -or
        $configuredPort[0].HostIp -cne '127.0.0.1' -or
        $configuredPort[0].HostPort -cne '5432'
    ) {
        throw 'Retained PostgreSQL configured port boundary is not exact.'
    }

    $hbaSources = @(Get-HostPathAliases -Path (
        Join-Path $absoluteRoot 'ops\postgres\stratus-pg_hba.conf'
    ))
    if ($inspect.Mounts -isnot [Array]) {
        throw 'PostgreSQL mounts are not a native JSON array.'
    }
    $mounts = @($inspect.Mounts)
    $hbaMount = @($mounts | Where-Object {
        $_.Destination -ceq '/etc/postgresql/stratus-pg_hba.conf'
    })
    $dataMount = @($mounts | Where-Object {
        $_.Destination -ceq '/var/lib/postgresql/data'
    })
    foreach ($mount in $mounts) {
        Assert-NativeJsonObject -Value $mount -Label 'PostgreSQL mount'
        foreach ($name in @('Type', 'Source', 'Destination', 'Mode', 'Propagation')) {
            Assert-NativeJsonString -Value $mount.$name -Label "PostgreSQL mount $name"
        }
        Assert-NativeJsonBoolean -Value $mount.RW -Label 'PostgreSQL mount RW'
    }
    if ($dataMount.Count -eq 1) {
        Assert-NativeJsonString -Value $dataMount[0].Name `
            -Label 'PostgreSQL data volume name'
        Assert-NativeJsonString -Value $dataMount[0].Driver `
            -Label 'PostgreSQL data volume driver'
    }
    if (
        $mounts.Count -ne 2 -or $hbaMount.Count -ne 1 -or
        $hbaMount[0].Type -cne 'bind' -or [bool]$hbaMount[0].RW -or
        $hbaMount[0].Mode -cne 'ro' -or
        $hbaMount[0].Propagation -cne 'rprivate' -or
        ([string]$hbaMount[0].Source).Replace('\', '/') -notin $hbaSources -or
        $dataMount.Count -ne 1 -or $dataMount[0].Type -cne 'volume' -or
        $dataMount[0].Name -cne 'weatherpoly_stratus-postgres' -or
        $dataMount[0].Driver -cne 'local' -or
        $dataMount[0].Source -cne $dataVolumeSource -or
        $dataMount[0].Destination -cne '/var/lib/postgresql/data' -or
        $dataMount[0].Mode -cne 'rw' -or -not [bool]$dataMount[0].RW -or
        $dataMount[0].Propagation -cne ''
    ) {
        throw 'Retained PostgreSQL mount topology is not exact.'
    }

    $networkNames = @(
        $inspect.NetworkSettings.Networks.PSObject.Properties |
            ForEach-Object { [string]$_.Name }
    )
    [Array]::Sort($networkNames, [StringComparer]::Ordinal)
    $expectedNetworkNames = @($defaultNetworkName, $safetyNetworkName)
    [Array]::Sort($expectedNetworkNames, [StringComparer]::Ordinal)
    if (($networkNames -join "`n") -cne ($expectedNetworkNames -join "`n")) {
        throw 'Retained PostgreSQL network membership is not exact.'
    }
    foreach ($networkName in $expectedNetworkNames) {
        $attachment = $inspect.NetworkSettings.Networks.$networkName
        Assert-NativeJsonObject -Value $attachment `
            -Label "PostgreSQL network attachment $networkName"
        Assert-ExactProperties -Value $attachment `
            -Label "PostgreSQL network attachment $networkName" -Expected @(
                'IPAMConfig', 'Links', 'Aliases', 'MacAddress', 'DriverOpts',
                'GwPriority', 'NetworkID', 'EndpointID', 'Gateway', 'IPAddress',
                'IPPrefixLen', 'IPv6Gateway', 'GlobalIPv6Address',
                'GlobalIPv6PrefixLen', 'DNSNames'
            )
        $expectedId = if ($networkName -ceq $defaultNetworkName) {
            $defaultNetworkId
        }
        else { $safetyNetworkId }
        if ($attachment.Aliases -isnot [Array] -or $attachment.DNSNames -isnot [Array]) {
            throw "PostgreSQL attachment arrays are not native: $networkName"
        }
        $aliases = [string[]]@($attachment.Aliases)
        $expectedAliases = [string[]]@('postgres', 'weatherpoly-postgres-1')
        $dnsNames = [string[]]@($attachment.DNSNames)
        $expectedDnsNames = [string[]]@(
            $productionPostgresId.Substring(0, 12), 'postgres',
            'weatherpoly-postgres-1'
        )
        [Array]::Sort($aliases, [StringComparer]::Ordinal)
        [Array]::Sort($expectedAliases, [StringComparer]::Ordinal)
        [Array]::Sort($dnsNames, [StringComparer]::Ordinal)
        [Array]::Sort($expectedDnsNames, [StringComparer]::Ordinal)
        foreach ($name in @(
            'NetworkID', 'EndpointID', 'Gateway', 'IPAddress', 'IPv6Gateway',
            'GlobalIPv6Address', 'MacAddress'
        )) {
            Assert-NativeJsonString -Value $attachment.$name `
                -Label "PostgreSQL attachment $networkName $name"
        }
        foreach ($name in @('IPPrefixLen', 'GlobalIPv6PrefixLen', 'GwPriority')) {
            Assert-NativeJsonInteger -Value $attachment.$name `
                -Label "PostgreSQL attachment $networkName $name"
        }
        foreach ($value in @($attachment.Aliases) + @($attachment.DNSNames)) {
            Assert-NativeJsonString -Value $value `
                -Label "PostgreSQL attachment $networkName name"
        }
        $ipamConfiguration = if ($null -eq $attachment.IPAMConfig) {
            'null'
        }
        elseif (
            $attachment.IPAMConfig -is [pscustomobject] -and
            @($attachment.IPAMConfig.PSObject.Properties).Count -eq 0
        ) {
            'empty_object'
        }
        else { 'other' }
        $driverOptions = if ($null -eq $attachment.DriverOpts) {
            'null'
        }
        elseif (
            $attachment.DriverOpts -is [pscustomobject] -and
            @($attachment.DriverOpts.PSObject.Properties).Count -eq 0
        ) {
            'empty_object'
        }
        else { 'other' }
        $configuredPending =
            $State -ceq 'Stopped' -and
            $networkName -ceq $safetyNetworkName -and
            $attachment.NetworkID -ceq '' -and
            $ipamConfiguration -ceq 'empty_object' -and
            $driverOptions -ceq 'empty_object'
        $materializedConfiguration =
            $attachment.NetworkID -ceq $expectedId -and
            $ipamConfiguration -ceq 'null' -and
            $driverOptions -ceq 'null'
        if (
            (-not $materializedConfiguration -and -not $configuredPending) -or
            $null -ne $attachment.Links -or
            [int]$attachment.GwPriority -ne 0 -or
            ($aliases -join "`n") -cne ($expectedAliases -join "`n") -or
            ($dnsNames -join "`n") -cne ($expectedDnsNames -join "`n") -or
            -not [string]::IsNullOrEmpty([string]$attachment.IPv6Gateway) -or
            -not [string]::IsNullOrEmpty([string]$attachment.GlobalIPv6Address) -or
            [int]$attachment.GlobalIPv6PrefixLen -ne 0
        ) {
            throw "Retained PostgreSQL attachment is not exact or configured-pending: $networkName"
        }
        if ($State -ceq 'Stopped') {
            if (
                -not [string]::IsNullOrEmpty([string]$attachment.EndpointID) -or
                -not [string]::IsNullOrEmpty([string]$attachment.Gateway) -or
                -not [string]::IsNullOrEmpty([string]$attachment.IPAddress) -or
                [int]$attachment.IPPrefixLen -ne 0 -or
                -not [string]::IsNullOrEmpty([string]$attachment.MacAddress)
            ) {
                throw "Stopped PostgreSQL has an active endpoint: $networkName"
            }
        }
        else {
            $expectedGateway = if ($networkName -ceq $defaultNetworkName) {
                '172.20.0.1'
            }
            else { '172.22.0.1' }
            $expectedIp = if ($networkName -ceq $defaultNetworkName) {
                '^172\.20\.'
            }
            else { '^172\.22\.' }
            if (
                [string]$attachment.EndpointID -notmatch '^[0-9a-f]{64}$' -or
                $attachment.Gateway -cne $expectedGateway -or
                [string]$attachment.IPAddress -notmatch $expectedIp -or
                [int]$attachment.IPPrefixLen -ne 16 -or
                [string]$attachment.MacAddress -notmatch '^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$'
            ) {
                throw "Running PostgreSQL endpoint is not exact: $networkName"
            }
        }
    }

    $runtimeProperties = @($inspect.NetworkSettings.Ports.PSObject.Properties)
    if ($State -ceq 'Stopped') {
        if (
            $inspect.State.Status -cne 'exited' -or $inspect.State.Running -or
            $inspect.State.Pid -ne 0 -or $inspect.State.ExitCode -ne 0 -or
            $runtimeProperties.Count -ne 0
        ) {
            throw 'Retained PostgreSQL is not cleanly stopped.'
        }
    }
    else {
        Assert-NativeJsonObject -Value $inspect.State.Health `
            -Label 'PostgreSQL health'
        Assert-NativeJsonString -Value $inspect.State.Health.Status `
            -Label 'PostgreSQL health status'
        $runtimePort = $inspect.NetworkSettings.Ports.'5432/tcp'
        if ($runtimePort -isnot [Array]) {
            throw 'PostgreSQL runtime port is not a native JSON array.'
        }
        if (@($runtimePort).Count -eq 1) {
            Assert-NativeJsonObject -Value $runtimePort[0] `
                -Label 'PostgreSQL runtime port'
            Assert-NativeJsonString -Value $runtimePort[0].HostIp `
                -Label 'PostgreSQL runtime HostIp'
            Assert-NativeJsonString -Value $runtimePort[0].HostPort `
                -Label 'PostgreSQL runtime HostPort'
        }
        if (
            $inspect.State.Status -cne 'running' -or -not $inspect.State.Running -or
            $inspect.State.Pid -le 0 -or $inspect.State.ExitCode -ne 0 -or
            $inspect.State.FinishedAt -cne '0001-01-01T00:00:00Z' -or
            $inspect.State.Health.Status -cne 'healthy' -or
            $runtimeProperties.Count -ne 1 -or
            $runtimeProperties[0].Name -cne '5432/tcp' -or
            @($runtimePort).Count -ne 1 -or
            $runtimePort[0].HostIp -cne '127.0.0.1' -or
            $runtimePort[0].HostPort -cne '5432'
        ) {
            throw 'Retained PostgreSQL is not exact, healthy, and loopback-only.'
        }
    }
    Assert-ProductionNetworks -State $State
    $runningIds = @(Invoke-PinnedDockerBounded -Arguments @('ps', '-q', '--no-trunc'))
    if ($State -ceq 'Stopped') {
        if ($runningIds.Count -ne 0) {
            throw 'Containment requires zero running Docker containers.'
        }
    }
    elseif ($runningIds.Count -ne 1 -or $runningIds[0] -cne $productionPostgresId) {
        throw 'Accepted commit permits only the exact retained PostgreSQL container.'
    }
    return [pscustomobject]@{
        container_id = [string]$inspect.Id
        image_id = [string]$inspect.Image
        config_image = [string]$inspect.Config.Image
        restart_count = [int]$inspect.RestartCount
        restart_policy = [string]$inspect.HostConfig.RestartPolicy.Name
        state = [string]$inspect.State.Status
        started_at = [string]$inspect.State.StartedAt
        finished_at = [string]$inspect.State.FinishedAt
        oom_killed = [bool]$inspect.State.OOMKilled
    }
}

function Assert-OwnerIdentity {
    param([switch] $ReturnBoolean)

    try {
        $owner = [Diagnostics.Process]::GetProcessById($OwnerPid)
        try {
            if ($owner.HasExited) {
                if ($ReturnBoolean) { return $false }
                throw 'Transition owner process already exited.'
            }
            $actual = [DateTimeOffset]::new(
                $owner.StartTime.ToUniversalTime(), [TimeSpan]::Zero
            )
            if ($actual.UtcTicks -ne $ownerStart.UtcTicks) {
                if ($ReturnBoolean) { return $false }
                throw 'Transition owner PID was reused by another process.'
            }
            if ($ReturnBoolean) { return $true }
            return $actual
        }
        finally {
            $owner.Dispose()
        }
    }
    catch [ArgumentException] {
        if ($ReturnBoolean) { return $false }
        throw 'Transition owner process does not exist.'
    }
}

function Stop-ExactTransitionOwnerForFenceRecovery {
    $owner = $null
    try {
        if ($OwnerPid -eq $PID) {
            throw 'The watchdog refuses to terminate its own process.'
        }
        $owner = [Diagnostics.Process]::GetProcessById($OwnerPid)
        # Force and retain the native process handle before reading identity;
        # subsequent Kill/Wait operations therefore target this exact process,
        # not a later occupant of the numeric PID.
        [void]$owner.Handle
        if ($owner.HasExited) {
            return [pscustomobject]@{
                attempted = $false
                terminated = $false
                already_absent = $true
                terminated_at = $null
            }
        }
        $actualStart = [DateTimeOffset]::new(
            $owner.StartTime.ToUniversalTime(), [TimeSpan]::Zero
        )
        if ($actualStart.UtcTicks -ne $ownerStart.UtcTicks) {
            throw 'Transition owner PID was reused; fence recovery will not kill it.'
        }

        # Keep this exact process handle open from identity validation through
        # termination and wait.  Killing by the retained handle avoids a
        # second PID lookup/reuse race and never requests descendant teardown.
        $owner.Kill()
        if (-not $owner.WaitForExit(5000)) {
            throw 'Exact transition owner did not exit after bounded termination.'
        }
        return [pscustomobject]@{
            attempted = $true
            terminated = $true
            already_absent = $false
            terminated_at = [DateTimeOffset]::UtcNow.ToString('o')
        }
    }
    catch [ArgumentException] {
        return [pscustomobject]@{
            attempted = $false
            terminated = $false
            already_absent = $true
            terminated_at = $null
        }
    }
    finally {
        if ($null -ne $owner) { $owner.Dispose() }
    }
}

function Assert-PreStartEnvelope {
    $document = Read-StrictJsonFile -LiteralPath $absolutePreStartPath `
        -Label 'successor PreStart attestation'
    if ($document.sha256 -cne $ExpectedPreStartSha256) {
        throw 'Successor PreStart bytes do not match the expected SHA-256.'
    }
    $envelope = $document.value
    Assert-ExactProperties -Value $envelope -Label 'successor PreStart envelope' `
        -Expected @('format', 'accepted', 'payload', 'payload_sha256')
    Assert-NativeJsonString -Value $envelope.format -Label 'PreStart format'
    Assert-NativeJsonBoolean -Value $envelope.accepted -Label 'PreStart accepted'
    Assert-NativeJsonString -Value $envelope.payload_sha256 `
        -Label 'PreStart payload_sha256'
    Assert-NativeJsonObject -Value $envelope.payload -Label 'PreStart payload'
    $payloadJson = $envelope.payload | ConvertTo-Json -Depth 20 -Compress
    if (
        $envelope.format -cne 'stratus-post-migration-successor-transition-prestart-v2' -or
        -not $envelope.accepted -or
        $envelope.payload_sha256 -cne (Get-Sha256Text -Value $payloadJson)
    ) {
        throw 'Successor PreStart envelope is not accepted or hash-bound.'
    }
    Assert-RequiredProperties -Value $envelope.payload -Label 'PreStart payload' `
        -Required @(
            'accepted_at', 'validity', 'network_repair', 'postgres_prestart',
            'transition_session', 'authorization'
        )
    Assert-NativeJsonString -Value $envelope.payload.accepted_at `
        -Label 'PreStart accepted_at'
    $preStartAccepted = ConvertTo-RequiredUtcTime -Value $envelope.payload.accepted_at `
        -Label 'PreStart accepted_at'
    $validity = $envelope.payload.validity
    Assert-ExactProperties -Value $validity -Label 'PreStart validity' -Expected @(
        'max_age_seconds', 'postgres_start_window_seconds', 'expires_at',
        'postgres_start_deadline'
    )
    foreach ($name in @('max_age_seconds', 'postgres_start_window_seconds')) {
        Assert-NativeJsonInteger -Value $validity.$name -Label "PreStart validity $name"
    }
    foreach ($name in @('expires_at', 'postgres_start_deadline')) {
        Assert-NativeJsonString -Value $validity.$name -Label "PreStart validity $name"
    }
    $expiresAt = ConvertTo-RequiredUtcTime -Value $validity.expires_at `
        -Label 'PreStart expires_at'
    $startDeadline = ConvertTo-RequiredUtcTime -Value $validity.postgres_start_deadline `
        -Label 'PreStart postgres_start_deadline'
    if (
        $validity.max_age_seconds -ne 600 -or
        $validity.postgres_start_window_seconds -ne 300 -or
        $expiresAt -ne $preStartAccepted.AddSeconds(600) -or
        $startDeadline -ne $preStartAccepted.AddSeconds(300) -or
        $hardDeadline -gt $startDeadline -or
        [DateTimeOffset]::UtcNow -gt $expiresAt.AddSeconds($clockSkewSeconds)
    ) {
        throw 'PreStart validity does not bound the watchdog deadline.'
    }
    $networkRepairJson = Assert-NetworkRepairBinding `
        -Binding $envelope.payload.network_repair -Label 'PreStart network repair'
    $postgres = $envelope.payload.postgres_prestart
    Assert-RequiredProperties -Value $postgres -Label 'PreStart PostgreSQL' -Required @(
        'container_id', 'image_id', 'config_image', 'restart_count',
        'restart_policy', 'state', 'oom_killed', 'started_at', 'finished_at'
    )
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_policy', 'state',
        'started_at', 'finished_at'
    )) {
        Assert-NativeJsonString -Value $postgres.$name -Label "PreStart PostgreSQL $name"
    }
    Assert-NativeJsonInteger -Value $postgres.restart_count `
        -Label 'PreStart PostgreSQL restart_count'
    Assert-NativeJsonBoolean -Value $postgres.oom_killed `
        -Label 'PreStart PostgreSQL oom_killed'
    if (
        $postgres.container_id -cne $productionPostgresId -or
        $postgres.image_id -cne $postgresImageId -or
        $postgres.config_image -cne $postgresImage -or
        $postgres.restart_count -ne 0 -or $postgres.restart_policy -cne 'no' -or
        $postgres.state -cne 'exited' -or $postgres.oom_killed
    ) {
        throw 'PreStart does not bind the protected stopped PostgreSQL identity.'
    }
    $session = $envelope.payload.transition_session
    Assert-RequiredProperties -Value $session -Label 'PreStart transition session' `
        -Required @(
            'session_id', 'restart_policy_transition', 'observation',
            'watchdog_handoff'
        )
    Assert-NativeJsonString -Value $session.session_id `
        -Label 'PreStart transition session_id'
    if ($session.session_id -cne $SessionId) {
        throw 'PreStart is bound to another transition session.'
    }
    $restart = $session.restart_policy_transition
    Assert-ExactProperties -Value $restart -Label 'PreStart restart-policy transition' `
        -Expected @(
            'previous', 'armed', 'command', 'command_issued',
            'stopped_identity_rechecked', 'restore_on_success'
        )
    Assert-NativeJsonString -Value $restart.previous -Label 'restart previous'
    Assert-NativeJsonString -Value $restart.armed -Label 'restart armed'
    Assert-NativeJsonBoolean -Value $restart.command_issued `
        -Label 'restart command_issued'
    Assert-NativeJsonBoolean -Value $restart.stopped_identity_rechecked `
        -Label 'restart stopped_identity_rechecked'
    Assert-NativeJsonBoolean -Value $restart.restore_on_success `
        -Label 'restart restore_on_success'
    if ($restart.command -isnot [Array]) {
        throw 'PreStart restart command must be a native JSON array.'
    }
    foreach ($token in @($restart.command)) {
        Assert-NativeJsonString -Value $token -Label 'PreStart restart command token'
    }
    $command = @($restart.command)
    if (
        $restart.previous -cne 'unless-stopped' -or $restart.armed -cne 'no' -or
        -not $restart.command_issued -or -not $restart.stopped_identity_rechecked -or
        $restart.restore_on_success -or
        $command.Count -ne 3 -or $command[0] -cne 'update' -or
        $command[1] -cne '--restart=no' -or $command[2] -cne $productionPostgresId
    ) {
        throw 'PreStart restart-policy transition is not exact.'
    }
    $observation = $session.observation
    Assert-ExactProperties -Value $observation -Label 'PreStart start observation' `
        -Expected @('start_command_issued', 'start_completed')
    foreach ($name in @('start_command_issued', 'start_completed')) {
        Assert-NativeJsonBoolean -Value $observation.$name `
            -Label "PreStart observation $name"
        if ($observation.$name) {
            throw 'PreStart cannot claim that PostgreSQL start was issued or completed.'
        }
    }
    $handoff = $session.watchdog_handoff
    Assert-ExactProperties -Value $handoff -Label 'PreStart watchdog handoff' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'hard_deadline_utc', 'heartbeat_timeout_seconds', 'lease_path',
        'receipt_path', 'heartbeat_path', 'commit_path', 'live_path', 'prestart_path',
        'start_fence_path', 'start_fence_sha256', 'start_revoke_path',
        'start_attempt_path', 'start_completion_path'
    )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'hard_deadline_utc',
        'lease_path', 'receipt_path', 'heartbeat_path', 'commit_path',
        'live_path', 'prestart_path', 'start_fence_path', 'start_fence_sha256',
        'start_revoke_path', 'start_attempt_path', 'start_completion_path'
    )) {
        Assert-NativeJsonString -Value $handoff.$name `
            -Label "PreStart watchdog handoff $name"
    }
    Assert-NativeJsonInteger -Value $handoff.owner_pid `
        -Label 'PreStart watchdog handoff owner_pid'
    Assert-NativeJsonInteger -Value $handoff.heartbeat_timeout_seconds `
        -Label 'PreStart watchdog handoff heartbeat_timeout_seconds'
    if (
        $handoff.format -cne 'stratus-successor-transition-watchdog-handoff-v3' -or
        $handoff.session_id -cne $SessionId -or $handoff.owner_pid -ne $OwnerPid -or
        $handoff.owner_start_time_utc -cne $ownerStart.ToString('o') -or
        $handoff.hard_deadline_utc -cne $hardDeadline.ToString('o') -or
        $handoff.heartbeat_timeout_seconds -ne $HeartbeatTimeoutSeconds -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.lease_path), $absoluteLeasePath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.receipt_path), $absoluteReceiptPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.heartbeat_path), $absoluteHeartbeatPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.commit_path), $absoluteCommitPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.live_path), $absoluteLivePath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.prestart_path), $absolutePreStartPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.start_fence_path), $absoluteStartFencePath
        ) -or
        $handoff.start_fence_sha256 -cne $ExpectedStartFenceSha256 -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.start_revoke_path), $absoluteStartRevokePath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.start_attempt_path), $absoluteStartAttemptPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($handoff.start_completion_path),
            $absoluteStartCompletionPath
        )
    ) {
        throw 'PreStart watchdog handoff is not exact.'
    }
    $authorization = $envelope.payload.authorization
    Assert-RequiredProperties -Value $authorization -Label 'PreStart authorization' `
        -Required @(
            'postgres_start_permitted', 'external_postgres_start_permitted',
            'transition_process_start_permitted', 'api_stage_permitted',
            'worker_start_permitted', 'safety_control_start_permitted',
            'live_transition_permitted', 'binance_kill_switch_removal_permitted'
        )
    foreach ($name in @(
        'postgres_start_permitted', 'external_postgres_start_permitted',
        'transition_process_start_permitted', 'api_stage_permitted',
        'worker_start_permitted', 'safety_control_start_permitted',
        'live_transition_permitted', 'binance_kill_switch_removal_permitted'
    )) {
        Assert-NativeJsonBoolean -Value $authorization.$name `
            -Label "PreStart authorization $name"
    }
    if (
        $authorization.postgres_start_permitted -or
        $authorization.external_postgres_start_permitted -or
        -not $authorization.transition_process_start_permitted -or
        $authorization.api_stage_permitted -or $authorization.worker_start_permitted -or
        $authorization.safety_control_start_permitted -or
        $authorization.live_transition_permitted -or
        $authorization.binance_kill_switch_removal_permitted
    ) {
        throw 'PreStart authorization is not fail-closed.'
    }
    return [pscustomobject]@{
        sha256 = $document.sha256
        payload_sha256 = [string]$envelope.payload_sha256
        accepted_at = [string]$envelope.payload.accepted_at
        network_repair_json = $networkRepairJson
        postgres = $postgres
    }
}

function Write-JsonToReservedStream {
    param(
        [Parameter(Mandatory = $true)][IO.FileStream] $Stream,
        [Parameter(Mandatory = $true)][object] $Value
    )

    if ($Stream.Length -ne 0 -or $Stream.Position -ne 0) {
        throw 'Reserved durable artifact stream was already written.'
    }
    $json = $Value | ConvertTo-Json -Depth 30
    if ($json -match '(?i)password|api[_-]?key|secret|database_url|dsn|token') {
        throw 'Watchdog evidence contains a forbidden secret-shaped field.'
    }
    $bytes = if ($script:parentTransitionMode) { [Text.UTF8Encoding]::new($false, $true).GetBytes($json) }
        else { [Text.UTF8Encoding]::new($false).GetBytes($json) }
    if ($script:parentTransitionMode -and $bytes.Length -gt 262144) { throw 'Parent Transition durable artifact exceeds its fixed byte bound.' }
    $Stream.Write($bytes, 0, $bytes.Length)
    $Stream.Flush($true)
    return [pscustomobject]@{
        json = $json
        bytes = $bytes
        sha256 = Get-Sha256Bytes -Bytes $bytes
    }
}

function Write-TerminalReceipt {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('success', 'contained', 'failure')]
        [string] $Status,
        [Parameter(Mandatory = $true)][string] $Trigger,
        [AllowNull()][object] $Containment,
        [AllowNull()][object] $Commit,
        [Parameter(Mandatory = $true)][AllowNull()][object] $Failure
    )

    if ($script:terminalReceiptWritten) {
        throw 'Watchdog terminal receipt was already written.'
    }
    if ($null -ne $Failure -and $Failure.GetType() -ne [string]) {
        throw 'Watchdog terminal failure detail must be a native string or null.'
    }
    $authorization = [ordered]@{
        # A watchdog receipt is evidence, not an independently actionable
        # release authorization. Only the parent can authorize API staging
        # after it seals this exact receipt to exit 0 and captured stdout.
        api_stage_permitted = $false
        worker_start_permitted = $false
        safety_control_start_permitted = $false
        live_transition_permitted = $false
        binance_kill_switch_removal_permitted = $false
        migration_container_permitted = $false
    }
    $receipt = [ordered]@{
        format = 'stratus-successor-transition-watchdog-terminal-v1'
        status = $Status
        terminal_at = [DateTimeOffset]::UtcNow.ToString('o')
        session_id = $SessionId
        owner_pid = [int]$OwnerPid
        owner_start_time_utc = $ownerStart.ToString('o')
        lease_path = $absoluteLeasePath
        lease_sha256 = if ($script:leaseArmed) { [string]$script:leaseSha256 } else { $null }
        receipt_path = $absoluteReceiptPath
        commit_path = $absoluteCommitPath
        live_path = $absoluteLivePath
        trigger = $Trigger
        docker_engine = $script:initialEngine
        containment = $Containment
        commit = $Commit
        failure = $Failure
        authorization = $authorization
    }
    $result = Write-JsonToReservedStream -Stream $script:receiptStream -Value $receipt
    $script:terminalReceiptWritten = $true
    return [pscustomobject]@{
        receipt = $receipt
        sha256 = $result.sha256
        json = $result.json
        byte_count = [int]$result.bytes.Length
    }
}

function Assert-Heartbeat {
    param(
        [int64] $PreviousSequence,
        [string] $PreviousSha256,
        [DateTimeOffset] $PreviousWrittenAt = [DateTimeOffset]::MinValue
    )

    $document = Read-StrictJsonFile -LiteralPath $absoluteHeartbeatPath `
        -Label 'watchdog heartbeat' -MaximumBytes 65536
    if ($document.sha256 -ceq $PreviousSha256) {
        return [pscustomobject]@{
            changed = $false
            sequence = $PreviousSequence
            sha256 = $PreviousSha256
            written_at = $PreviousWrittenAt
        }
    }
    $heartbeat = $document.value
    Assert-ExactProperties -Value $heartbeat -Label 'watchdog heartbeat' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'sequence', 'written_at'
    )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'lease_path',
        'lease_sha256', 'written_at'
    )) {
        Assert-NativeJsonString -Value $heartbeat.$name -Label "heartbeat $name"
    }
    Assert-NativeJsonInteger -Value $heartbeat.owner_pid -Label 'heartbeat owner_pid'
    Assert-NativeJsonInteger -Value $heartbeat.sequence -Label 'heartbeat sequence'
    $writtenAt = ConvertTo-RequiredUtcTime -Value $heartbeat.written_at `
        -Label 'heartbeat written_at' -RequireCanonical
    $now = [DateTimeOffset]::UtcNow
    if (
        $heartbeat.format -cne 'stratus-successor-transition-watchdog-heartbeat-v1' -or
        $heartbeat.session_id -cne $SessionId -or
        $heartbeat.owner_pid -ne $OwnerPid -or
        $heartbeat.owner_start_time_utc -cne $ownerStart.ToString('o') -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($heartbeat.lease_path), $absoluteLeasePath
        ) -or
        $heartbeat.lease_sha256 -cne $script:leaseSha256 -or
        $heartbeat.sequence -le $PreviousSequence -or
        ($PreviousSequence -gt 0 -and $writtenAt -le $PreviousWrittenAt) -or
        $writtenAt -lt $script:leaseArmedAt.AddSeconds(-$clockSkewSeconds) -or
        $writtenAt -lt $now.AddSeconds(-(
            $HeartbeatTimeoutSeconds + $clockSkewSeconds
        )) -or
        $writtenAt -gt $now.AddSeconds($clockSkewSeconds)
    ) {
        throw 'Watchdog heartbeat is stale, replayed, or not lease-bound.'
    }
    return [pscustomobject]@{
        changed = $true
        sequence = [int64]$heartbeat.sequence
        sha256 = [string]$document.sha256
        written_at = $writtenAt
    }
}

function Get-WatchdogContinuity {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Stopwatch] $Watch,
        [Parameter(Mandatory = $true)][TimeSpan] $LastHeartbeatElapsed,
        [Parameter(Mandatory = $true)][int64] $LastHeartbeatSequence,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string] $LastHeartbeatSha256,
        [Parameter(Mandatory = $true)][DateTimeOffset] $LastHeartbeatWrittenAt,
        [switch] $RequireObservedHeartbeat
    )

    $result = [ordered]@{
        trigger = $null
        heartbeat_elapsed = $LastHeartbeatElapsed
        heartbeat_sequence = $LastHeartbeatSequence
        heartbeat_sha256 = $LastHeartbeatSha256
        heartbeat_written_at = $LastHeartbeatWrittenAt
    }
    $now = [DateTimeOffset]::UtcNow
    if ($now -ge $hardDeadline) {
        $result.trigger = 'hard_deadline_expired'
        return [pscustomobject]$result
    }
    if (-not (Assert-OwnerIdentity -ReturnBoolean)) {
        $result.trigger = 'owner_process_lost'
        return [pscustomobject]$result
    }
    if (Test-Path -LiteralPath $absoluteHeartbeatPath) {
        try {
            $heartbeat = Assert-Heartbeat -PreviousSequence $LastHeartbeatSequence `
                -PreviousSha256 $LastHeartbeatSha256 `
                -PreviousWrittenAt $LastHeartbeatWrittenAt
            if ($heartbeat.changed) {
                $result.heartbeat_elapsed = $Watch.Elapsed
                $result.heartbeat_sequence = [int64]$heartbeat.sequence
                $result.heartbeat_sha256 = [string]$heartbeat.sha256
                $result.heartbeat_written_at = [DateTimeOffset]$heartbeat.written_at
            }
        }
        catch [IO.IOException] {
            # Atomic CreateNew/replace publication can expose a temporarily
            # unreadable path. Preserve the prior accepted monotonic boundary;
            # neither its age nor the hard deadline is reset by this retry.
        }
        catch {
            $result.trigger = 'invalid_or_replayed_heartbeat'
            return [pscustomobject]$result
        }
    }
    if (
        $RequireObservedHeartbeat -and
        [int64]$result.heartbeat_sequence -lt 1
    ) {
        $result.trigger = 'heartbeat_stale'
        return [pscustomobject]$result
    }
    if (
        [int64]$result.heartbeat_sequence -gt 0 -and (
            ($Watch.Elapsed - [TimeSpan]$result.heartbeat_elapsed).TotalSeconds -ge
                $HeartbeatTimeoutSeconds -or
            [DateTimeOffset]$result.heartbeat_written_at -lt
                [DateTimeOffset]::UtcNow.AddSeconds(-(
                    $HeartbeatTimeoutSeconds + $clockSkewSeconds
                ))
        )
    ) {
        $result.trigger = 'heartbeat_stale'
        return [pscustomobject]$result
    }
    if (
        [int64]$result.heartbeat_sequence -eq 0 -and
        ($Watch.Elapsed - [TimeSpan]$result.heartbeat_elapsed).TotalSeconds -ge
            $HeartbeatTimeoutSeconds
    ) {
        $result.trigger = 'heartbeat_stale'
    }
    return [pscustomobject]$result
}

function Assert-WatchdogBinding {
    param([Parameter(Mandatory = $true)][object] $Binding)

    Assert-ExactProperties -Value $Binding -Label 'Live watchdog binding' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'hard_deadline_utc', 'heartbeat_timeout_seconds', 'lease_path',
        'lease_sha256', 'receipt_path', 'heartbeat_path', 'commit_path',
        'start_fence_path', 'start_fence_sha256', 'start_revoke_path',
        'start_attempt_path', 'start_attempt_sha256', 'start_completion_path',
        'start_completion_sha256', 'start_revoke_absent'
    )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'hard_deadline_utc',
        'lease_path', 'lease_sha256', 'receipt_path', 'heartbeat_path', 'commit_path',
        'start_fence_path', 'start_fence_sha256', 'start_revoke_path',
        'start_attempt_path', 'start_attempt_sha256', 'start_completion_path',
        'start_completion_sha256'
    )) {
        Assert-NativeJsonString -Value $Binding.$name -Label "Live watchdog $name"
    }
    Assert-NativeJsonInteger -Value $Binding.owner_pid -Label 'Live watchdog owner_pid'
    Assert-NativeJsonInteger -Value $Binding.heartbeat_timeout_seconds `
        -Label 'Live watchdog heartbeat_timeout_seconds'
    Assert-NativeJsonBoolean -Value $Binding.start_revoke_absent `
        -Label 'Live watchdog start_revoke_absent'
    if (
        $Binding.format -cne 'stratus-successor-transition-watchdog-binding-v3' -or
        $Binding.session_id -cne $SessionId -or $Binding.owner_pid -ne $OwnerPid -or
        $Binding.owner_start_time_utc -cne $ownerStart.ToString('o') -or
        $Binding.hard_deadline_utc -cne $hardDeadline.ToString('o') -or
        $Binding.heartbeat_timeout_seconds -ne $HeartbeatTimeoutSeconds -or
        $Binding.lease_sha256 -cne $script:leaseSha256 -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($Binding.lease_path), $absoluteLeasePath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($Binding.receipt_path), $absoluteReceiptPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($Binding.heartbeat_path), $absoluteHeartbeatPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($Binding.commit_path), $absoluteCommitPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($Binding.start_fence_path), $absoluteStartFencePath
        ) -or
        $Binding.start_fence_sha256 -cne $ExpectedStartFenceSha256 -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($Binding.start_revoke_path), $absoluteStartRevokePath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($Binding.start_attempt_path),
            $absoluteStartAttemptPath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($Binding.start_completion_path),
            $absoluteStartCompletionPath
        ) -or
        $Binding.start_attempt_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $Binding.start_completion_sha256 -notmatch '^[0-9a-f]{64}$' -or
        -not $Binding.start_revoke_absent -or
        (Test-Path -LiteralPath $absoluteStartRevokePath)
    ) {
        throw 'Live watchdog binding is not exact.'
    }
    $startProtocol = Get-StartProtocolState -ThrowOnInvalid
    if (
        $startProtocol.status -cne 'completed' -or
        $Binding.start_attempt_sha256 -cne $startProtocol.attempt.sha256 -or
        $Binding.start_completion_sha256 -cne $startProtocol.completion.sha256
    ) {
        throw 'Live watchdog binding does not bind the completed start protocol.'
    }
    return [pscustomobject]@{
        start_attempt_sha256 = [string]$Binding.start_attempt_sha256
        start_completion_sha256 = [string]$Binding.start_completion_sha256
    }
}

function Assert-LiveEnvelope {
    param([Parameter(Mandatory = $true)][string] $ExpectedSha256)

    $document = Read-StrictJsonFile -LiteralPath $absoluteLivePath `
        -Label 'successor Live acceptance'
    if ($document.sha256 -cne $ExpectedSha256) {
        throw 'Live acceptance bytes do not match the committed SHA-256.'
    }
    $envelope = $document.value
    Assert-ExactProperties -Value $envelope -Label 'Live envelope' -Expected @(
        'format', 'accepted', 'payload', 'payload_sha256'
    )
    Assert-NativeJsonString -Value $envelope.format -Label 'Live format'
    Assert-NativeJsonBoolean -Value $envelope.accepted -Label 'Live accepted'
    Assert-NativeJsonObject -Value $envelope.payload -Label 'Live payload'
    Assert-NativeJsonString -Value $envelope.payload_sha256 -Label 'Live payload_sha256'
    $payloadJson = $envelope.payload | ConvertTo-Json -Depth 20 -Compress
    if (
        $envelope.format -cne 'stratus-post-migration-successor-transition-live-v2' -or
        -not $envelope.accepted -or
        $envelope.payload_sha256 -cne (Get-Sha256Text -Value $payloadJson)
    ) {
        throw 'Live envelope is not accepted or payload-hash-bound.'
    }
    Assert-ExactProperties -Value $envelope.payload -Label 'Live payload' -Expected @(
        'accepted_at', 'artifact_written_at', 'historical', 'network_repair', 'prestart',
        'successor', 'closed_contract', 'postgres_transition',
        'transition_session', 'topology', 'hba', 'final_boundary',
        'database', 'authorization'
    )
    foreach ($name in @(
        'historical', 'network_repair', 'prestart', 'successor', 'closed_contract',
        'postgres_transition', 'transition_session', 'topology', 'hba',
        'final_boundary', 'database', 'authorization'
    )) {
        Assert-NativeJsonObject -Value $envelope.payload.$name -Label "Live $name"
    }
    foreach ($name in @('accepted_at', 'artifact_written_at')) {
        Assert-NativeJsonString -Value $envelope.payload.$name -Label "Live $name"
    }
    $acceptedAt = ConvertTo-RequiredUtcTime -Value $envelope.payload.accepted_at `
        -Label 'Live accepted_at'
    $writtenAt = ConvertTo-RequiredUtcTime -Value $envelope.payload.artifact_written_at `
        -Label 'Live artifact_written_at'
    if ($writtenAt -lt $acceptedAt -or $writtenAt -gt $hardDeadline) {
        throw 'Live acceptance was not created within the watchdog deadline.'
    }
    $liveNetworkRepairJson = Assert-NetworkRepairBinding `
        -Binding $envelope.payload.network_repair -Label 'Live network repair'
    if ($liveNetworkRepairJson -cne $preStartProof.network_repair_json) {
        throw 'Live network repair binding does not equal the accepted PreStart binding.'
    }

    $prestart = $envelope.payload.prestart
    Assert-ExactProperties -Value $prestart -Label 'Live PreStart binding' -Expected @(
        'path', 'sha256', 'payload_sha256', 'accepted_at'
    )
    foreach ($name in @('path', 'sha256', 'payload_sha256', 'accepted_at')) {
        Assert-NativeJsonString -Value $prestart.$name -Label "Live PreStart $name"
    }
    if (
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($prestart.path), $absolutePreStartPath
        ) -or
        $prestart.sha256 -cne $ExpectedPreStartSha256 -or
        $prestart.payload_sha256 -cne $preStartProof.payload_sha256
    ) {
        throw 'Live acceptance does not bind the exact PreStart artifact.'
    }

    $successor = $envelope.payload.successor
    Assert-ExactProperties -Value $successor -Label 'Live successor' -Expected @(
        'revision', 'image', 'image_id'
    )
    foreach ($name in @('revision', 'image', 'image_id')) {
        Assert-NativeJsonString -Value $successor.$name -Label "Live successor $name"
    }
    if (
        $successor.revision -notmatch '^[0-9a-f]{40}$' -or
        $successor.image -cne "stratus:candidate-$($successor.revision.Substring(0, 12))" -or
        $successor.image_id -notmatch '^sha256:[0-9a-f]{64}$'
    ) {
        throw 'Live successor image identity is malformed or not revision-derived.'
    }

    $postgres = $envelope.payload.postgres_transition
    Assert-RequiredProperties -Value $postgres -Label 'Live PostgreSQL transition' `
        -Required @(
            'container_id', 'image_id', 'config_image', 'restart_count',
            'restart_policy', 'successor_started_at', 'state', 'oom_killed', 'health'
        )
    foreach ($name in @(
        'container_id', 'image_id', 'config_image', 'restart_policy',
        'successor_started_at', 'state'
    )) {
        Assert-NativeJsonString -Value $postgres.$name `
            -Label "Live PostgreSQL $name"
    }
    Assert-NativeJsonInteger -Value $postgres.restart_count `
        -Label 'Live PostgreSQL restart_count'
    Assert-NativeJsonBoolean -Value $postgres.oom_killed `
        -Label 'Live PostgreSQL oom_killed'
    Assert-ExactProperties -Value $postgres.health -Label 'Live PostgreSQL health' `
        -Expected @('status', 'checked_at')
    Assert-NativeJsonString -Value $postgres.health.status `
        -Label 'Live PostgreSQL health status'
    Assert-NativeJsonString -Value $postgres.health.checked_at `
        -Label 'Live PostgreSQL health checked_at'
    if (
        $postgres.container_id -cne $productionPostgresId -or
        $postgres.image_id -cne $postgresImageId -or
        $postgres.config_image -cne $postgresImage -or
        $postgres.restart_count -ne 0 -or $postgres.restart_policy -cne 'no' -or
        $postgres.state -cne 'running' -or $postgres.oom_killed -or
        $postgres.health.status -cne 'healthy'
    ) {
        throw 'Live PostgreSQL transition is not exact and healthy.'
    }

    $session = $envelope.payload.transition_session
    Assert-RequiredProperties -Value $session -Label 'Live transition session' `
        -Required @('session_id', 'observation', 'watchdog', 'rollback')
    Assert-NativeJsonString -Value $session.session_id -Label 'Live session_id'
    if ($session.session_id -cne $SessionId) {
        throw 'Live acceptance belongs to another transition session.'
    }
    $observation = $session.observation
    Assert-ExactProperties -Value $observation -Label 'Live start observation' `
        -Expected @('start_command_issued', 'start_completed')
    foreach ($name in @('start_command_issued', 'start_completed')) {
        Assert-NativeJsonBoolean -Value $observation.$name `
            -Label "Live observation $name"
        if (-not $observation.$name) {
            throw 'Live acceptance lacks transition-owned start completion.'
        }
    }
    $watchdogBinding = Assert-WatchdogBinding -Binding $session.watchdog
    Assert-RequiredProperties -Value $session.rollback -Label 'Live rollback' `
        -Required @('on_failure', 'stop_timeout_seconds', 'executed')
    foreach ($name in @('on_failure', 'executed')) {
        Assert-NativeJsonBoolean -Value $session.rollback.$name `
            -Label "Live rollback $name"
    }
    Assert-NativeJsonInteger -Value $session.rollback.stop_timeout_seconds `
        -Label 'Live rollback stop_timeout_seconds'
    if (
        -not $session.rollback.on_failure -or $session.rollback.executed -or
        $session.rollback.stop_timeout_seconds -ne $containmentStopTimeoutSeconds
    ) {
        throw 'Live rollback declaration is not fail-closed.'
    }

    $topology = $envelope.payload.topology
    Assert-ExactProperties -Value $topology -Label 'Live topology' -Expected @(
        'running_count', 'postgres_only', 'application_writers_running'
    )
    Assert-NativeJsonInteger -Value $topology.running_count -Label 'Live running_count'
    Assert-NativeJsonBoolean -Value $topology.postgres_only -Label 'Live postgres_only'
    Assert-NativeJsonBoolean -Value $topology.application_writers_running `
        -Label 'Live application_writers_running'
    if (
        $topology.running_count -ne 1 -or -not $topology.postgres_only -or
        $topology.application_writers_running
    ) {
        throw 'Live topology is not the PostgreSQL-only transition boundary.'
    }

    $database = $envelope.payload.database
    Assert-ExactProperties -Value $database -Label 'Live database' -Expected @(
        'schema_head', 'indexes_exact', 'functions_exact', 'runtime_boundary',
        'safety_boundary', 'control_triggers_exact', 'commit_triggers_exact',
        'identity_triggers_exact', 'fail_closed_controls', 'zero_live_state',
        'zero_pending_commands', 'zero_other_sessions', 'bootstrap_audit_exact',
        'fence_armed', 'active_lease_exists', 'execution_authorized',
        'first_database_clock', 'second_database_clock'
    )
    foreach ($name in @('schema_head', 'first_database_clock', 'second_database_clock')) {
        Assert-NativeJsonString -Value $database.$name -Label "Live database $name"
    }
    foreach ($name in @(
        'indexes_exact', 'functions_exact', 'runtime_boundary', 'safety_boundary',
        'control_triggers_exact', 'commit_triggers_exact', 'identity_triggers_exact',
        'fail_closed_controls', 'zero_live_state', 'zero_pending_commands',
        'zero_other_sessions', 'bootstrap_audit_exact', 'fence_armed',
        'active_lease_exists', 'execution_authorized'
    )) {
        Assert-NativeJsonBoolean -Value $database.$name -Label "Live database $name"
    }
    if (
        $database.schema_head -cne '20260730_0047' -or
        -not $database.indexes_exact -or -not $database.functions_exact -or
        -not $database.runtime_boundary -or -not $database.safety_boundary -or
        -not $database.control_triggers_exact -or
        -not $database.commit_triggers_exact -or
        -not $database.identity_triggers_exact -or
        -not $database.fail_closed_controls -or -not $database.zero_live_state -or
        -not $database.zero_pending_commands -or -not $database.zero_other_sessions -or
        -not $database.bootstrap_audit_exact -or -not $database.fence_armed -or
        $database.active_lease_exists -or $database.execution_authorized
    ) {
        throw 'Live database evidence is not the exact fail-closed schema boundary.'
    }
    $firstClock = ConvertTo-RequiredUtcTime -Value $database.first_database_clock `
        -Label 'Live first_database_clock'
    $secondClock = ConvertTo-RequiredUtcTime -Value $database.second_database_clock `
        -Label 'Live second_database_clock'
    if ($secondClock -lt $firstClock -or ($secondClock - $firstClock).TotalSeconds -gt 60) {
        throw 'Live database clocks do not prove the repeated bounded snapshot.'
    }

    $authorization = $envelope.payload.authorization
    Assert-ExactProperties -Value $authorization -Label 'Live authorization' -Expected @(
        'api_stage_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted',
        'binance_kill_switch_removal_permitted', 'migration_container_permitted'
    )
    foreach ($name in @(
        'api_stage_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted',
        'binance_kill_switch_removal_permitted', 'migration_container_permitted'
    )) {
        Assert-NativeJsonBoolean -Value $authorization.$name `
            -Label "Live authorization $name"
    }
    if (
        $authorization.api_stage_permitted -or
        $authorization.worker_start_permitted -or
        $authorization.safety_control_start_permitted -or
        $authorization.live_transition_permitted -or
        $authorization.binance_kill_switch_removal_permitted -or
        $authorization.migration_container_permitted
    ) {
        throw 'Pre-terminal Live authorization is not fully fail-closed.'
    }
    return [pscustomobject]@{
        sha256 = $document.sha256
        payload_sha256 = [string]$envelope.payload_sha256
        accepted_at = $acceptedAt.ToString('o')
        artifact_written_at = $writtenAt.ToString('o')
        postgres_started_at = [string]$postgres.successor_started_at
        start_attempt_sha256 = [string]$watchdogBinding.start_attempt_sha256
        start_completion_sha256 = [string]$watchdogBinding.start_completion_sha256
    }
}

function Assert-CommitMarker {
    $document = Read-StrictJsonFile -LiteralPath $absoluteCommitPath `
        -Label 'watchdog commit marker' -MaximumBytes 65536
    $commit = $document.value
    Assert-ExactProperties -Value $commit -Label 'watchdog commit marker' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'live_path', 'live_sha256', 'committed_at'
    )
    foreach ($name in @(
        'format', 'session_id', 'owner_start_time_utc', 'lease_path',
        'lease_sha256', 'live_path', 'live_sha256', 'committed_at'
    )) {
        Assert-NativeJsonString -Value $commit.$name -Label "commit $name"
    }
    Assert-NativeJsonInteger -Value $commit.owner_pid -Label 'commit owner_pid'
    $committedAt = ConvertTo-RequiredUtcTime -Value $commit.committed_at `
        -Label 'commit committed_at' -RequireCanonical
    $now = [DateTimeOffset]::UtcNow
    if (
        $commit.format -cne 'stratus-successor-transition-watchdog-commit-v1' -or
        $commit.session_id -cne $SessionId -or $commit.owner_pid -ne $OwnerPid -or
        $commit.owner_start_time_utc -cne $ownerStart.ToString('o') -or
        $commit.lease_sha256 -cne $script:leaseSha256 -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($commit.lease_path), $absoluteLeasePath
        ) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath($commit.live_path), $absoluteLivePath
        ) -or
        $commit.live_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $committedAt -lt $script:leaseArmedAt -or
        $committedAt -gt $hardDeadline -or
        $committedAt -gt $now.AddSeconds($clockSkewSeconds) -or
        $now -gt $hardDeadline
    ) {
        throw 'Watchdog commit marker is late, forged, or not lease-bound.'
    }
    $leaseNow = Read-StrictJsonFile -LiteralPath $absoluteLeasePath `
        -Label 'watchdog lease' -MaximumBytes 262144
    if ($leaseNow.sha256 -cne $script:leaseSha256) {
        throw 'Watchdog lease changed before commit validation.'
    }
    $live = Assert-LiveEnvelope -ExpectedSha256 $commit.live_sha256
    $liveWrittenAt = ConvertTo-RequiredUtcTime -Value $live.artifact_written_at `
        -Label 'committed Live artifact_written_at'
    if ($committedAt -lt $liveWrittenAt) {
        throw 'Commit marker predates the Live artifact it claims to commit.'
    }
    $engine = Get-DockerEngineBoundary
    Assert-EngineUnchanged -Boundary $engine
    $postgres = Get-PostgresBoundary -State Running
    if ($postgres.started_at -cne $live.postgres_started_at) {
        throw 'Live acceptance does not match the currently running PostgreSQL start.'
    }
    $engineAfter = Get-DockerEngineBoundary
    Assert-EngineUnchanged -Boundary $engineAfter
    $liveAgain = Read-StrictJsonFile -LiteralPath $absoluteLivePath `
        -Label 'successor Live acceptance recheck'
    if ($liveAgain.sha256 -cne $commit.live_sha256) {
        throw 'Live acceptance changed during commit validation.'
    }
    $commitAgain = Read-StrictJsonFile -LiteralPath $absoluteCommitPath `
        -Label 'watchdog commit marker recheck' -MaximumBytes 65536
    if ($commitAgain.sha256 -cne $document.sha256) {
        throw 'Watchdog commit marker changed during validation.'
    }
    if (Test-Path -LiteralPath $absoluteStartRevokePath) {
        throw 'PostgreSQL start authorization was revoked before commit acceptance.'
    }
    $successFenceLock = Open-ExclusiveStartFenceOnce
    if ($null -eq $successFenceLock) {
        throw 'PostgreSQL start fence is still held at commit acceptance.'
    }
    try {
        [void](Assert-HeldStartFence -Stream $successFenceLock)
        if (Test-Path -LiteralPath $absoluteStartRevokePath) {
            throw 'PostgreSQL start authorization was revoked under the commit fence.'
        }
        $committedStartProtocol = Get-StartProtocolState -ThrowOnInvalid
        if (
            $committedStartProtocol.status -cne 'completed' -or
            $committedStartProtocol.attempt.sha256 -cne
                $live.start_attempt_sha256 -or
            $committedStartProtocol.completion.sha256 -cne
                $live.start_completion_sha256
        ) {
            throw 'PostgreSQL start lacks the Live-bound completed protocol under the commit fence.'
        }
    }
    finally {
        $successFenceLock.Dispose()
    }
    return [pscustomobject]@{
        marker_sha256 = $document.sha256
        committed_at = $committedAt.ToString('o')
        live_sha256 = [string]$commit.live_sha256
        live_payload_sha256 = [string]$live.payload_sha256
        postgres_started_at = [string]$postgres.started_at
        docker_engine_contract_sha256 = [string]$engineAfter.contract_sha256
    }
}

function Invoke-Containment {
    param(
        [Parameter(Mandatory = $true)][string] $Trigger,
        [Parameter(Mandatory = $true)][object] $StartProtocol
    )

    $requiresMaterialization = $StartProtocol.status -cin @('unresolved', 'invalid')
    $materializationObserved = $false
    $stopSucceeded = $false
    $stopAttemptCount = 0
    $stoppedProofCount = 0
    $transitionObservationCount = 0
    $beforeState = 'unobserved'
    $after = $null
    $lastError = $null

    # There is deliberately no wall-clock completion deadline here.  An
    # attempt without a positive completion can still be queued inside the
    # daemon after its CLI was killed.  A stopped snapshot cannot resolve that
    # request.  Keep the fence and revoke marker until the request visibly
    # materializes and a causally later exact-ID stop succeeds.  If it never
    # materializes, this watchdog remains nonterminal and fail-closed.
    while ($true) {
        try {
            $engine = Get-DockerEngineBoundary
            Assert-EngineUnchanged -Boundary $engine
        }
        catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Milliseconds $pollMilliseconds
            continue
        }
        try {
            $candidate = Get-PostgresBoundary -State Stopped
            $stoppedProofCount += 1
            if ($beforeState -ceq 'unobserved') { $beforeState = 'stopped' }
            if (-not $requiresMaterialization -or $stopSucceeded) {
                $after = $candidate
                $lastError = $null
                break
            }
            $lastError = $null
        }
        catch {
            if ($beforeState -ceq 'unobserved') { $beforeState = 'ambiguous' }
            $transitionObservationCount += 1
            $materializationObserved = $true
            $lastError = $_.Exception.Message
        }

        if ($materializationObserved) {
            $stopAttemptCount += 1
            try {
                $stopped = Get-OneLine -Label 'watchdog causal Docker stop' -Lines (
                    Invoke-PinnedDockerBounded -WallClockTimeoutSeconds `
                        $dockerStopWallClockTimeoutSeconds -Arguments @(
                            'stop', '--timeout', [string]$containmentStopTimeoutSeconds,
                            $productionPostgresId
                        )
                )
                if ($stopped -cne $productionPostgresId) {
                    throw 'Causal Docker stop returned another container identity.'
                }
                $stopSucceeded = $true
                $lastError = $null
            }
            catch {
                # Docker returns a non-successful already-stopped result while
                # a delayed start is still pre-running.  That is unresolved,
                # not containment; retry after the request becomes observable.
                $lastError = $_.Exception.Message
            }
        }
        Start-Sleep -Milliseconds $pollMilliseconds
    }

    return [pscustomobject]@{
        trigger = $Trigger
        start_protocol_status = [string]$StartProtocol.status
        start_attempt_path = $absoluteStartAttemptPath
        start_attempt_sha256 = if ($null -ne $StartProtocol.attempt) {
            [string]$StartProtocol.attempt.sha256
        } else { $null }
        start_completion_path = $absoluteStartCompletionPath
        start_completion_sha256 = if ($null -ne $StartProtocol.completion) {
            [string]$StartProtocol.completion.sha256
        } else { $null }
        causal_materialization_required = [bool]$requiresMaterialization
        materialization_observed = [bool]$materializationObserved
        attempted_stop = [bool]($stopAttemptCount -gt 0)
        docker_stop_succeeded = [bool]$stopSucceeded
        stop_attempt_count = [int]$stopAttemptCount
        stop_timeout_seconds = [int]$containmentStopTimeoutSeconds
        stop_wall_clock_timeout_seconds = [int]$dockerStopWallClockTimeoutSeconds
        stopped_proof_count = [int]$stoppedProofCount
        transition_observation_count = [int]$transitionObservationCount
        verified_stopped = $true
        before_state = $beforeState
        after_state = [string]$after.state
        last_error = $lastError
        errors = @()
    }
}

function Complete-RevokedContainment {
    param(
        [Parameter(Mandatory = $true)][string] $Trigger,
        [AllowNull()][string] $Failure
    )

    $fenceWait = [Diagnostics.Stopwatch]::StartNew()
    $fenceAcquireAdvisoryExceeded = $false
    $ownerTerminationAttemptCount = 0
    $ownerTerminationSucceeded = $false
    $ownerTerminationFailure = $null
    $ownerTerminationAt = $null
    $ownerTakeoverEligible = $Trigger -cin @(
        'hard_deadline_expired', 'heartbeat_stale',
        'invalid_or_replayed_heartbeat', 'owner_process_lost'
    )
    while ($null -eq $script:startFenceLock) {
        $script:startFenceLock = Open-ExclusiveStartFenceOnce
        if ($null -ne $script:startFenceLock) { break }
        if ($fenceWait.Elapsed.TotalSeconds -ge $startFenceAcquireAdvisorySeconds) {
            $fenceAcquireAdvisoryExceeded = $true
            $ownerTakeoverAllowedNow = $ownerTakeoverEligible -or
                [DateTimeOffset]::UtcNow -ge $hardDeadline
            if ($ownerTakeoverAllowedNow -and -not $ownerTerminationSucceeded -and
                (Assert-OwnerIdentity -ReturnBoolean)) {
                $ownerTerminationAttemptCount += 1
                try {
                    $ownerTermination = Stop-ExactTransitionOwnerForFenceRecovery
                    $ownerTerminationSucceeded = [bool]$ownerTermination.terminated
                    $ownerTerminationAt = $ownerTermination.terminated_at
                    $ownerTerminationFailure = $null
                }
                catch {
                    # Never bypass or delete a live owner's fence.  A denied or
                    # ambiguous termination simply remains nonterminal and
                    # fail closed; retry exact identity on the next poll.
                    $ownerTerminationFailure = $_.Exception.Message
                }
            }
        }
        Start-Sleep -Milliseconds $pollMilliseconds
    }

    try {
        $fenceFailure = $null
        $fenceProof = $null
        try {
            $fenceProof = Assert-HeldStartFence -Stream $script:startFenceLock
        }
        catch {
            $fenceFailure = $_.Exception.Message
        }
        $startProtocol = if ($null -eq $fenceFailure) {
            Get-StartProtocolState
        }
        else {
            [pscustomobject]@{
                status = 'invalid'
                attempt = $null
                completion = $null
                failure = $fenceFailure
            }
        }
        # The authoritative revoke is published only while holding the same
        # exclusive fence required by the parent around check-and-start.  The
        # two decisions therefore cannot pass one another between check/use.
        $revokeAttempts = 0
        do {
            $revokeAttempts += 1
            $revoke = Publish-StartRevokeMarker -Trigger $Trigger
            if (-not $revoke.present) {
                Start-Sleep -Milliseconds $pollMilliseconds
            }
        } while (-not $revoke.present)
        $containment = Invoke-Containment -Trigger $Trigger `
            -StartProtocol $startProtocol
        $containment | Add-Member -NotePropertyName start_revoke_path `
            -NotePropertyValue $absoluteStartRevokePath
        $containment | Add-Member -NotePropertyName start_revoke_present `
            -NotePropertyValue ([bool](Test-Path -LiteralPath $absoluteStartRevokePath))
        $containment | Add-Member -NotePropertyName start_revoke_created `
            -NotePropertyValue ([bool]$revoke.created)
        $containment | Add-Member -NotePropertyName start_fence_path `
            -NotePropertyValue $absoluteStartFencePath
        $containment | Add-Member -NotePropertyName start_fence_sha256 `
            -NotePropertyValue $ExpectedStartFenceSha256
        $containment | Add-Member -NotePropertyName start_fence_lock_acquired `
            -NotePropertyValue $true
        $containment | Add-Member -NotePropertyName start_revoke_attempt_count `
            -NotePropertyValue ([int]$revokeAttempts)
        $containment | Add-Member -NotePropertyName final_after_fence `
            -NotePropertyValue $true
        $containment | Add-Member -NotePropertyName fence_acquire_advisory_exceeded `
            -NotePropertyValue ([bool]$fenceAcquireAdvisoryExceeded)
        $containment | Add-Member -NotePropertyName owner_termination_attempt_count `
            -NotePropertyValue ([int]$ownerTerminationAttemptCount)
        $containment | Add-Member -NotePropertyName owner_terminated_for_fence_recovery `
            -NotePropertyValue ([bool]$ownerTerminationSucceeded)
        $containment | Add-Member -NotePropertyName owner_terminated_at `
            -NotePropertyValue $ownerTerminationAt
        $containment | Add-Member -NotePropertyName owner_termination_failure `
            -NotePropertyValue $ownerTerminationFailure

        $status = if (
            $containment.verified_stopped -and $null -eq $fenceFailure -and
            $startProtocol.status -cne 'invalid' -and
            (Test-Path -LiteralPath $absoluteStartRevokePath)
        ) { 'contained' } else { 'failure' }
        $failureDetail = $Failure
        if ($startProtocol.status -ceq 'invalid') {
            $failureDetail = @(
                $failureDetail,
                "start protocol: $($startProtocol.failure)"
            ) | Where-Object {
                -not [string]::IsNullOrWhiteSpace([string]$_)
            } | ForEach-Object { [string]$_ }
            $failureDetail = $failureDetail -join ' '
        }
        if ($null -ne $fenceFailure) {
            $failureDetail = @($failureDetail, "start fence: $fenceFailure") `
                | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } `
                | ForEach-Object { [string]$_ }
            $failureDetail = $failureDetail -join ' '
        }
        if ($status -ceq 'failure' -and [string]::IsNullOrWhiteSpace($failureDetail)) {
            $failureDetail = 'Watchdog could not prove exact stopped containment under the start fence.'
        }
        $terminal = Write-TerminalReceipt -Status $status -Trigger $Trigger `
            -Containment $containment -Commit $null -Failure $failureDetail
        return [pscustomobject]@{
            terminal = $terminal
            status = $status
            exit_code = if ($status -ceq 'contained') { 2 } else { 3 }
            fence = $fenceProof
            revoke = $revoke
            start_protocol = $startProtocol
        }
    }
    finally {
        if ($null -ne $script:startFenceLock) {
            try { $script:startFenceLock.Dispose() }
            catch {
                $script:cleanupFailures.Add(
                    "containment start fence cleanup failed: $($_.Exception.Message)"
                )
            }
            $script:startFenceLock = $null
        }
    }
}

$exitCode = 3
$primaryFailureMessage = $null
$terminalHandlingFailure = $null
try {
    if ($PostgresContainer -cne $productionPostgresId) {
        throw 'PostgresContainer must be the exact retained production PostgreSQL ID.'
    }
    $absoluteRoot = [IO.Path]::GetFullPath($Root)
    [void](Assert-RegularDirectory -LiteralPath $absoluteRoot)
    $absoluteLeasePath = Assert-PathBelowRoot -LiteralPath $LeasePath `
        -AbsoluteRoot $absoluteRoot -Label 'LeasePath'
    $absoluteReceiptPath = Assert-PathBelowRoot -LiteralPath $ReceiptPath `
        -AbsoluteRoot $absoluteRoot -Label 'ReceiptPath'
    $absoluteHeartbeatPath = Assert-PathBelowRoot -LiteralPath $HeartbeatPath `
        -AbsoluteRoot $absoluteRoot -Label 'HeartbeatPath'
    $absoluteCommitPath = Assert-PathBelowRoot -LiteralPath $CommitPath `
        -AbsoluteRoot $absoluteRoot -Label 'CommitPath'
    $absoluteLivePath = Assert-PathBelowRoot -LiteralPath $LiveAcceptancePath `
        -AbsoluteRoot $absoluteRoot -Label 'LiveAcceptancePath'
    $absolutePreStartPath = Assert-PathBelowRoot -LiteralPath $PreStartAttestationPath `
        -AbsoluteRoot $absoluteRoot -Label 'PreStartAttestationPath'
    $absoluteStartFencePath = Assert-PathBelowRoot -LiteralPath $StartFencePath `
        -AbsoluteRoot $absoluteRoot -Label 'StartFencePath'
    $absoluteStartRevokePath = Assert-PathBelowRoot -LiteralPath $StartRevokePath `
        -AbsoluteRoot $absoluteRoot -Label 'StartRevokePath'
    $absoluteStartAttemptPath = Assert-PathBelowRoot -LiteralPath $StartAttemptPath `
        -AbsoluteRoot $absoluteRoot -Label 'StartAttemptPath'
    $absoluteStartCompletionPath = Assert-PathBelowRoot `
        -LiteralPath $StartCompletionPath -AbsoluteRoot $absoluteRoot `
        -Label 'StartCompletionPath'
    if ($script:parentTransitionMode) {
        Assert-ParentTransitionWirePaths -RootPath $Root -ArtifactPaths @($LeasePath, $ReceiptPath, $HeartbeatPath,
            $CommitPath, $LiveAcceptancePath, $PreStartAttestationPath, $StartFencePath, $StartRevokePath,
            $StartAttemptPath, $StartCompletionPath)
        $ownProcess = [Diagnostics.Process]::GetCurrentProcess()
        try { $script:parentTransitionStartedAt = [DateTimeOffset]::new($ownProcess.StartTime.ToUniversalTime(), [TimeSpan]::Zero).ToString('o') }
        finally { $ownProcess.Dispose() }
        Assert-ParentTransitionWireIdentity $SessionId $ParentStageNonce $PID $script:parentTransitionStartedAt $PreStartAttestationPath $ExpectedPreStartSha256
        Assert-ParentTransitionWireIdentity $SessionId $ParentStageNonce $PID $script:parentTransitionStartedAt $StartFencePath $ExpectedStartFenceSha256
    }
    $distinctPaths = @(
        $absoluteLeasePath, $absoluteReceiptPath, $absoluteHeartbeatPath,
        $absoluteCommitPath, $absoluteLivePath, $absolutePreStartPath,
        $absoluteStartFencePath, $absoluteStartRevokePath,
        $absoluteStartAttemptPath, $absoluteStartCompletionPath
    ) | Sort-Object -Unique
    if ($distinctPaths.Count -ne 10) {
        throw 'Watchdog artifact paths must be pairwise distinct.'
    }
    foreach ($path in @(
        $absoluteLeasePath, $absoluteReceiptPath, $absoluteHeartbeatPath,
        $absoluteCommitPath, $absoluteLivePath, $absoluteStartRevokePath,
        $absoluteStartAttemptPath, $absoluteStartCompletionPath
    )) {
        [void](Assert-RegularDirectory -LiteralPath ([IO.Path]::GetDirectoryName($path)))
        if (Test-Path -LiteralPath $path) {
            throw "Watchdog refuses a pre-existing artifact path: $path"
        }
    }
    [void](Assert-RegularFile -LiteralPath $absolutePreStartPath)
    [void](Assert-RegularFile -LiteralPath $absoluteStartFencePath)
    $ownerStart = ConvertTo-RequiredUtcTime -Value $OwnerStartTimeUtc `
        -Label 'OwnerStartTimeUtc' -RequireCanonical
    $hardDeadline = ConvertTo-RequiredUtcTime -Value $DeadlineUtc `
        -Label 'DeadlineUtc' -RequireCanonical
    $startedAt = [DateTimeOffset]::UtcNow
    if (
        $hardDeadline -le $startedAt -or
        $hardDeadline -gt $startedAt.AddSeconds($maximumLeaseSeconds)
    ) {
        throw 'Watchdog deadline must be future and within the fixed lease maximum.'
    }
    [void](Assert-OwnerIdentity)
    $initialStartFenceLock = Open-ExclusiveStartFenceOnce
    if ($null -eq $initialStartFenceLock) {
        throw 'The successor PostgreSQL start fence was already held during watchdog arming.'
    }
    try {
        [void](Assert-HeldStartFence -Stream $initialStartFenceLock)
    }
    finally {
        $initialStartFenceLock.Dispose()
    }

    # Reserve both terminal artifacts with atomic create-new semantics before
    # the first Docker command.  The receipt stream remains exclusively open;
    # no second watchdog can publish a competing terminal result.
    $script:receiptStream = [IO.FileStream]::new(
        $absoluteReceiptPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough
    )
    $leaseStream = $null
    try {
        $leaseStream = [IO.FileStream]::new(
            $absoluteLeasePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
            [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough
        )
    }
    catch {
        $failure = "lease reservation: $($_.Exception.Message)"
        $terminalFailure = Write-TerminalReceipt -Status failure `
            -Trigger initialization_failure -Containment $null `
            -Commit $null -Failure $failure
        Write-TransitionTerminalOutput $terminalFailure
        throw
    }

    try {
        $docker = Resolve-AndLockApprovedDocker
        $script:initialEngine = Get-DockerEngineBoundary
        $preStartProof = Assert-PreStartEnvelope
        $script:initialPostgres = Get-PostgresBoundary -State Stopped
        $armedAt = [DateTimeOffset]::UtcNow
        if ($armedAt -ge $hardDeadline) {
            throw 'Watchdog expired while arming its exact boundary.'
        }
        $script:leaseArmedAt = $armedAt
        $lease = [ordered]@{
            format = 'stratus-successor-transition-watchdog-lease-v3'
            armed = $true
            armed_at = $armedAt.ToString('o')
            session_id = $SessionId
            watchdog = [ordered]@{
                process_id = [int]$PID
                process_started_at = if ($script:parentTransitionMode) { $script:parentTransitionStartedAt } else { ([DateTimeOffset]::new(
                    (Get-Process -Id $PID).StartTime.ToUniversalTime(),
                    [TimeSpan]::Zero
                )).ToString('o') }
            }
            owner = [ordered]@{
                process_id = [int]$OwnerPid
                process_started_at = $ownerStart.ToString('o')
            }
            deadline = [ordered]@{
                hard_deadline_utc = $hardDeadline.ToString('o')
                heartbeat_timeout_seconds = [int]$HeartbeatTimeoutSeconds
                maximum_lease_seconds = [int]$maximumLeaseSeconds
            }
            artifacts = [ordered]@{
                lease_path = $absoluteLeasePath
                receipt_path = $absoluteReceiptPath
                heartbeat_path = $absoluteHeartbeatPath
                commit_path = $absoluteCommitPath
                live_path = $absoluteLivePath
                prestart_path = $absolutePreStartPath
                prestart_sha256 = [string]$preStartProof.sha256
                prestart_payload_sha256 = [string]$preStartProof.payload_sha256
                start_fence_path = $absoluteStartFencePath
                start_fence_sha256 = $ExpectedStartFenceSha256
                start_revoke_path = $absoluteStartRevokePath
                start_attempt_path = $absoluteStartAttemptPath
                start_completion_path = $absoluteStartCompletionPath
            }
            docker_engine = $script:initialEngine
            postgres = [ordered]@{
                container_id = [string]$script:initialPostgres.container_id
                image_id = [string]$script:initialPostgres.image_id
                config_image = [string]$script:initialPostgres.config_image
                restart_count = [int]$script:initialPostgres.restart_count
                restart_policy_before = 'unless-stopped'
                restart_policy_armed = [string]$script:initialPostgres.restart_policy
                state = [string]$script:initialPostgres.state
                started_at = [string]$script:initialPostgres.started_at
                finished_at = [string]$script:initialPostgres.finished_at
            }
            containment = [ordered]@{
                stop_timeout_seconds = [int]$containmentStopTimeoutSeconds
                stop_wall_clock_timeout_seconds = [int]$dockerStopWallClockTimeoutSeconds
                exact_container_only = $true
                ambiguous_start_requires_causal_resolution = $true
            }
        }
        $leaseResult = Write-JsonToReservedStream -Stream $leaseStream -Value $lease
        $script:leaseSha256 = $leaseResult.sha256
        $script:leaseArmed = $true
    }
    finally {
        if ($null -ne $leaseStream) { $leaseStream.Dispose() }
    }
    if ($script:parentTransitionMode) {
        if (-not $script:leaseArmed -or $script:leaseSha256 -cne $leaseResult.sha256) {
            throw 'Parent Transition readiness requires the actual published armed lease.'
        }
        $readyFrame = New-ParentTransitionReadyFrameBytes -RunId $SessionId -Nonce $ParentStageNonce -ProcessId $PID `
            -StartedAt $script:parentTransitionStartedAt -LeasePath $absoluteLeasePath -LeaseSha256 $leaseResult.sha256
        $stdout = [Console]::OpenStandardOutput()
        $stdout.Write($readyFrame, 0, $readyFrame.Length)
        $stdout.Flush()
    }

    $watch = [Diagnostics.Stopwatch]::StartNew()
    $lastHeartbeatElapsed = $watch.Elapsed
    $lastHeartbeatSequence = [int64]0
    $lastHeartbeatSha = ''
    $lastHeartbeatWrittenAt = [DateTimeOffset]::MinValue
    while ($true) {
        $startProtocolTrigger = $null
        $startProtocolFailure = $null
        if (
            (Test-Path -LiteralPath $absoluteStartAttemptPath) -or
            (Test-Path -LiteralPath $absoluteStartCompletionPath)
        ) {
            $protocolFenceLock = Open-ExclusiveStartFenceOnce
            if ($null -ne $protocolFenceLock) {
                try {
                    [void](Assert-HeldStartFence -Stream $protocolFenceLock)
                    $protocolProbe = Get-StartProtocolState
                    if ($protocolProbe.status -cin @('unresolved', 'invalid')) {
                        # Retain the exclusive lock into containment.  An
                        # attempt without a valid completion after the parent
                        # leaves the fence is an unresolved daemon request.
                        $script:startFenceLock = $protocolFenceLock
                        $protocolFenceLock = $null
                        $startProtocolTrigger = 'ambiguous_start_attempt'
                        $startProtocolFailure = $protocolProbe.failure
                    }
                }
                catch {
                    $script:startFenceLock = $protocolFenceLock
                    $protocolFenceLock = $null
                    $startProtocolTrigger = 'invalid_start_protocol'
                    $startProtocolFailure = $_.Exception.Message
                }
                finally {
                    if ($null -ne $protocolFenceLock) {
                        $protocolFenceLock.Dispose()
                    }
                }
            }
        }
        $commitPresent = Test-Path -LiteralPath $absoluteCommitPath
        $continuity = Get-WatchdogContinuity -Watch $watch `
            -LastHeartbeatElapsed $lastHeartbeatElapsed `
            -LastHeartbeatSequence $lastHeartbeatSequence `
            -LastHeartbeatSha256 $lastHeartbeatSha `
            -LastHeartbeatWrittenAt $lastHeartbeatWrittenAt `
            -RequireObservedHeartbeat:$commitPresent
        $lastHeartbeatElapsed = [TimeSpan]$continuity.heartbeat_elapsed
        $lastHeartbeatSequence = [int64]$continuity.heartbeat_sequence
        $lastHeartbeatSha = [string]$continuity.heartbeat_sha256
        $lastHeartbeatWrittenAt = [DateTimeOffset]$continuity.heartbeat_written_at
        $trigger = $continuity.trigger
        if ($null -ne $startProtocolTrigger) {
            $trigger = $startProtocolTrigger
        }
        if (Test-Path -LiteralPath $absoluteStartRevokePath) {
            $trigger = 'start_revoke_present'
        }
        $failure = $startProtocolFailure

        if ($null -eq $trigger -and $commitPresent) {
            $commitProof = $null
            $commitValidationFailure = $null
            try {
                $commitProof = Assert-CommitMarker
            }
            catch [IO.IOException] {
                # CreateNew exposes the pathname before the transition process
                # closes its write-through, FileShare.None publication handle.
                # A sharing violation is not malformed evidence: retry it within
                # the same hard deadline. A writer that never releases the path
                # is still contained by the deadline/heartbeat boundary.
            }
            catch {
                $commitValidationFailure = $_.Exception.Message
            }
            # Commit validation performs all bound Docker/live/commit proofs and
            # can consume a material fraction of the lease. Re-establish owner,
            # deadline, and fresh monotonic heartbeat continuity after those
            # Docker calls and immediately before any success receipt.
            $postCommitContinuity = Get-WatchdogContinuity -Watch $watch `
                -LastHeartbeatElapsed $lastHeartbeatElapsed `
                -LastHeartbeatSequence $lastHeartbeatSequence `
                -LastHeartbeatSha256 $lastHeartbeatSha `
                -LastHeartbeatWrittenAt $lastHeartbeatWrittenAt `
                -RequireObservedHeartbeat
            $lastHeartbeatElapsed = [TimeSpan]$postCommitContinuity.heartbeat_elapsed
            $lastHeartbeatSequence = [int64]$postCommitContinuity.heartbeat_sequence
            $lastHeartbeatSha = [string]$postCommitContinuity.heartbeat_sha256
            $lastHeartbeatWrittenAt = [DateTimeOffset]$postCommitContinuity.heartbeat_written_at
            if ($null -ne $postCommitContinuity.trigger) {
                $trigger = $postCommitContinuity.trigger
            }
            elseif ($null -ne $commitValidationFailure) {
                $trigger = 'invalid_or_forged_commit'
                $failure = $commitValidationFailure
            }
            elseif ($null -ne $commitProof) {
                $terminal = Write-TerminalReceipt -Status success `
                    -Trigger accepted_live_commit -Containment $null `
                    -Commit $commitProof -Failure $null
                Write-TransitionTerminalOutput $terminal
                $exitCode = 0
                break
            }
        }
        if ($null -ne $trigger) {
            $completion = Complete-RevokedContainment -Trigger $trigger `
                -Failure $failure
            Write-TransitionTerminalOutput $completion.terminal
            $exitCode = [int]$completion.exit_code
            break
        }
        Start-Sleep -Milliseconds $pollMilliseconds
    }
}
catch {
    $failureMessage = $_.Exception.Message
    $primaryFailureMessage = $failureMessage
    if ($null -ne $script:receiptStream -and -not $script:terminalReceiptWritten) {
        try {
            if ($null -ne $script:initialEngine -and $null -ne $script:dockerExecutable) {
                if ($script:leaseArmed) {
                    $completion = Complete-RevokedContainment `
                        -Trigger watchdog_internal_failure -Failure $failureMessage
                    Write-TransitionTerminalOutput $completion.terminal
                    $exitCode = [int]$completion.exit_code
                }
                else {
                    $terminalFailure = Write-TerminalReceipt -Status failure `
                        -Trigger initialization_failure -Containment $null `
                        -Commit $null -Failure $failureMessage
                    Write-TransitionTerminalOutput $terminalFailure
                    $exitCode = 3
                }
            }
            else {
                $terminalFailure = Write-TerminalReceipt -Status failure `
                    -Trigger initialization_failure -Containment $null `
                    -Commit $null -Failure $failureMessage
                Write-TransitionTerminalOutput $terminalFailure
                $exitCode = 3
            }
        }
        catch {
            $terminalHandlingFailure = $_.Exception.Message
            $exitCode = 3
        }
    }
    elseif ($null -eq $script:receiptStream) {
        Write-Error -Message $failureMessage -ErrorAction Continue
    }
}
finally {
    if ($null -ne $script:startFenceLock) {
        try { $script:startFenceLock.Dispose() }
        catch {
            $script:cleanupFailures.Add(
                "start fence cleanup failed: $($_.Exception.Message)"
            )
        }
        $script:startFenceLock = $null
    }
    if ($null -ne $script:dockerExecutableLock) {
        try { $script:dockerExecutableLock.Dispose() }
        catch {
            $script:cleanupFailures.Add(
                "Docker executable lock cleanup failed: $($_.Exception.Message)"
            )
        }
    }
    if ($null -ne $script:receiptStream) {
        try { $script:receiptStream.Dispose() }
        catch {
            $script:cleanupFailures.Add(
                "terminal receipt cleanup failed: $($_.Exception.Message)"
            )
        }
    }
}

if ($null -ne $terminalHandlingFailure -or $script:cleanupFailures.Count -gt 0) {
    $details = [Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace([string]$primaryFailureMessage)) {
        $details.Add("Original: $primaryFailureMessage")
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$terminalHandlingFailure)) {
        $details.Add("Terminal handling: $terminalHandlingFailure")
    }
    foreach ($cleanupFailure in $script:cleanupFailures) {
        $details.Add("Cleanup: $cleanupFailure")
    }
    Write-Error -Message (
        'Watchdog failed without masking earlier state. ' + ($details -join ' | ')
    ) -ErrorAction Continue
    $exitCode = 3
}

exit $exitCode
