[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Root,

    [Parameter(Mandatory = $true)]
    [ValidateSet('Ready', 'Terminal')]
    [string] $Mode,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{32}$')]
    [string] $RunId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._:-]{8,64}$')]
    [string] $ResumeRequestId,

    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 64)]
    [string] $ResumeActor,

    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 500)]
    [string] $ResumeReason,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, [long]::MaxValue)]
    [long] $EpochId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9._+-]{1,191}$')]
    [string] $Cohort,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha256:[0-9a-f]{64}$')]
    [string] $ImageId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $Revision,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string] $ReleaseDigest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ExpectedOffsets = @(0, 5, 15, 30, 45, 60)
$ExpectedHttpEndpoints = @(
    'healthz', 'readyz', 'overview', 'venues', 'commands', 'jobs',
    'continuity', 'execution_cost', 'validation_ledger'
)
$ExpectedContinuityLanes = @(
    'execution_evidence',
    'official_resolution_truth',
    'operational_legacy',
    'predictive_validation',
    'selection_policy',
    'station_lifecycle'
)
$ExpectedSchemaHead = '20260730_0047'
$LeaseId = "paper-lease-$RunId"
$LeaseAcquireRequestId = "paper-lease-acquire-$RunId"
$DirectStopRequestId = "paper-canary-stop-$RunId"
$LeaseReleaseRequestId = "paper-lease-release-$RunId"

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][byte[]] $Bytes)

    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return (
            [BitConverter]::ToString($hasher.ComputeHash($Bytes))
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}

function Get-Md5Hex {
    param([Parameter(Mandatory = $true)][string] $Value)

    $hasher = [Security.Cryptography.MD5]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        return (
            [BitConverter]::ToString($hasher.ComputeHash($bytes))
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}

function Get-ApiExpectedSha256 {
    param([Parameter(Mandatory = $true)][object] $Database)

    Assert-Properties -Value $Database -Expected @('api_expected') `
        -Label 'database API expectations'
    $json = $Database.api_expected | ConvertTo-Json -Depth 12 -Compress
    return Get-Sha256 -Bytes (
        [Text.UTF8Encoding]::new($false).GetBytes($json)
    )
}

function Get-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string] $Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $pathRoot = [IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Length -gt $pathRoot.Length) {
        return $fullPath.TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
    }
    return $fullPath
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value -or $Value -isnot [pscustomobject]) {
        throw "$Label is missing or is not a JSON object."
    }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $expectedNames = @($Expected | Sort-Object)
    if (
        $actual.Count -ne $expectedNames.Count -or
        ($actual -join "`n") -cne ($expectedNames -join "`n")
    ) {
        throw "$Label has an unexpected property inventory."
    }
}

function Assert-Properties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value -or $Value -isnot [pscustomobject]) {
        throw "$Label is missing or is not a JSON object."
    }
    $actual = @($Value.PSObject.Properties.Name)
    foreach ($name in $Expected) {
        if ($actual -cnotcontains $name) {
            throw "$Label is missing property $name."
        }
    }
}

function Assert-ExactOffsets {
    param(
        [AllowNull()][object[]] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $actual = @($Value)
    if ($actual.Count -ne $ExpectedOffsets.Count) {
        throw "$Label does not contain the six release offsets."
    }
    for ($index = 0; $index -lt $ExpectedOffsets.Count; $index++) {
        if (
            $actual[$index] -isnot [int] -and
            $actual[$index] -isnot [long]
        ) {
            throw "$Label contains a non-integer offset."
        }
        if ([long]$actual[$index] -ne $ExpectedOffsets[$index]) {
            throw "$Label is not exactly 0,5,15,30,45,60."
        }
    }
}

function Test-TrueBoolean {
    param([AllowNull()][object] $Value)

    return $Value -is [bool] -and $Value
}

function Test-FalseBoolean {
    param([AllowNull()][object] $Value)

    return $Value -is [bool] -and -not $Value
}

function Test-JsonNumber {
    param([AllowNull()][object] $Value)

    if ($Value -is [double]) {
        return -not [double]::IsNaN($Value) -and
            -not [double]::IsInfinity($Value)
    }
    return $Value -is [int] -or $Value -is [long] -or $Value -is [decimal]
}

function Test-JsonInteger {
    param([AllowNull()][object] $Value)

    return $Value -is [int] -or $Value -is [long]
}

function Assert-JsonIntegerProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Names,
        [switch] $NonNegative,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Value -Expected $Names -Label $Label
    foreach ($name in $Names) {
        $item = $Value.PSObject.Properties[$name].Value
        if (
            -not (Test-JsonInteger -Value $item) -or
            ($NonNegative -and [long]$item -lt 0)
        ) {
            throw "$Label.$name is not a valid JSON integer."
        }
    }
}

function Assert-JsonNumberProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Names,
        [switch] $NonNegative,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Value -Expected $Names -Label $Label
    foreach ($name in $Names) {
        $item = $Value.PSObject.Properties[$name].Value
        if (
            -not (Test-JsonNumber -Value $item) -or
            ($NonNegative -and [double]$item -lt 0)
        ) {
            throw "$Label.$name is not a valid JSON number."
        }
    }
}

function Assert-JsonStringProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Names,
        [switch] $NonEmpty,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Value -Expected $Names -Label $Label
    foreach ($name in $Names) {
        $item = $Value.PSObject.Properties[$name].Value
        if (
            $item -isnot [string] -or
            ($NonEmpty -and [string]::IsNullOrWhiteSpace([string]$item))
        ) {
            throw "$Label.$name is not a valid JSON string."
        }
    }
}

function Assert-JsonArrayProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Names,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Value -Expected $Names -Label $Label
    foreach ($name in $Names) {
        if ($Value.PSObject.Properties[$name].Value -isnot [Array]) {
            throw "$Label.$name is not a JSON array."
        }
    }
}

function Get-NormalizedIpAddressList {
    param(
        [AllowNull()][object[]] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $items = @($Value)
    if ($items.Count -eq 0) {
        throw "$Label contains no client addresses."
    }
    $normalized = [Collections.Generic.List[string]]::new()
    foreach ($item in $items) {
        if ($item -isnot [string] -or [string]::IsNullOrWhiteSpace($item)) {
            throw "$Label contains a non-string client address."
        }
        $parsed = $null
        if (-not [Net.IPAddress]::TryParse([string]$item, [ref]$parsed)) {
            throw "$Label contains an invalid client address."
        }
        $canonical = $parsed.ToString()
        if (
            [string]$item -cne $canonical -or
            $normalized.Contains($canonical)
        ) {
            throw "$Label contains a non-canonical or duplicate client address."
        }
        $normalized.Add($canonical)
    }
    return @($normalized | Sort-Object)
}

function ConvertTo-UtcInstant {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value)) {
        throw "$Label is not a non-empty timestamp string."
    }
    $parsed = [DateTimeOffset]::MinValue
    if (
        -not [DateTimeOffset]::TryParse(
            [string]$Value,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AllowWhiteSpaces,
            [ref]$parsed
        ) -or
        $parsed.Offset -ne [TimeSpan]::Zero
    ) {
        throw "$Label is not an explicit UTC timestamp."
    }
    return $parsed.ToUniversalTime()
}

function Test-ExactNullableString {
    param(
        [AllowNull()][object] $Actual,
        [AllowNull()][object] $Expected
    )

    if (($null -eq $Actual) -ne ($null -eq $Expected)) {
        return $false
    }
    if ($null -eq $Actual) {
        return $true
    }
    return (
        $Actual -is [string] -and
        $Expected -is [string] -and
        [string]$Actual -ceq [string]$Expected
    )
}

function Get-StableJson {
    param([AllowNull()][object] $Value)

    return $Value | ConvertTo-Json -Depth 16 -Compress
}

function Assert-NoReparsePath {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $Boundary,
        [switch] $LeafMustBeFile
    )

    $absolutePath = Get-NormalizedFullPath -Path $Path
    $absoluteBoundary = Get-NormalizedFullPath -Path $Boundary
    $boundaryPrefix = $absoluteBoundary + [IO.Path]::DirectorySeparatorChar
    if (
        -not [string]::Equals(
            $absolutePath,
            $absoluteBoundary,
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        -not $absolutePath.StartsWith(
            $boundaryPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Artifact path escapes the canary run directory: $absolutePath"
    }

    $item = Get-Item -LiteralPath $absolutePath -Force
    if (
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "Reparse points are forbidden in canary evidence: $absolutePath"
    }
    if ($LeafMustBeFile -and $item.PSIsContainer) {
        throw "Canary evidence leaf is not a file: $absolutePath"
    }

    $current = if ($item.PSIsContainer) {
        $item
    }
    else {
        $item.Directory
    }
    while ($null -ne $current) {
        $currentPath = Get-NormalizedFullPath -Path $current.FullName
        if (
            ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Reparse points are forbidden in canary evidence: $currentPath"
        }
        if (
            [string]::Equals(
                $currentPath,
                $absoluteBoundary,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $absolutePath
        }
        if (
            -not $currentPath.StartsWith(
                $boundaryPrefix,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            break
        }
        $current = $current.Parent
    }
    throw "Canary evidence ancestry is outside the run directory: $absolutePath"
}

function Read-ImmutableEnvelope {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $RunDirectory,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $absolutePath = Assert-NoReparsePath -Path $Path `
        -Boundary $RunDirectory -LeafMustBeFile
    $bytes = [IO.File]::ReadAllBytes($absolutePath)
    if ($bytes.Length -eq 0) {
        throw "$Label is empty."
    }
    if (
        $bytes.Length -ge 3 -and
        $bytes[0] -eq 0xEF -and
        $bytes[1] -eq 0xBB -and
        $bytes[2] -eq 0xBF
    ) {
        throw "$Label must be UTF-8 without a byte-order mark."
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    try {
        $json = $utf8.GetString($bytes)
    }
    catch {
        throw "$Label is not strict UTF-8."
    }
    if ($json -cnotmatch '^\s*\{') {
        throw "$Label outer JSON value is not an object."
    }
    try {
        $envelope = $json | ConvertFrom-Json
    }
    catch {
        throw "$Label is not valid JSON."
    }
    Assert-ExactProperties -Value $envelope `
        -Expected @('schema_version', 'payload_sha256', 'payload') `
        -Label "$Label envelope"
    Assert-JsonStringProperties -Value $envelope -Names @('payload_sha256') `
        -NonEmpty -Label "$Label envelope"
    if (
        -not (Test-JsonInteger -Value $envelope.schema_version) -or
        [int]$envelope.schema_version -ne 1 -or
        [string]$envelope.payload_sha256 -cnotmatch '^[0-9a-f]{64}$'
    ) {
        throw "$Label envelope schema or payload hash format is invalid."
    }
    Assert-Properties -Value $envelope.payload -Expected @('schema_version') `
        -Label "$Label payload"
    if (
        -not (Test-JsonInteger -Value $envelope.payload.schema_version) -or
        [int]$envelope.payload.schema_version -ne 1
    ) {
        throw "$Label payload schema is not version 1."
    }
    $payloadJson = $envelope.payload | ConvertTo-Json -Depth 30 -Compress
    $payloadBytes = $utf8.GetBytes($payloadJson)
    $payloadSha256 = Get-Sha256 -Bytes $payloadBytes
    if ($payloadSha256 -cne [string]$envelope.payload_sha256) {
        throw (
            "$Label compact payload SHA-256 does not match its envelope " +
            "(actual=$payloadSha256 expected=$($envelope.payload_sha256))."
        )
    }
    return [pscustomobject]@{
        payload = $envelope.payload
        record = [pscustomobject]@{
            path = $absolutePath
            bytes = $bytes.Length
            sha256 = Get-Sha256 -Bytes $bytes
        }
    }
}

function Assert-RunPayload {
    param(
        [Parameter(Mandatory = $true)][object] $Payload,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Payload -Expected @('canary_run_id') -Label $Label
    Assert-JsonStringProperties -Value $Payload -Names @('canary_run_id') `
        -NonEmpty -Label $Label
    if ([string]$Payload.canary_run_id -cne $RunId) {
        throw "$Label belongs to a different canary run."
    }
}

function Assert-SafetyPassed {
    param(
        [Parameter(Mandatory = $true)][object] $Safety,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Safety -Expected @('passed', 'violations') -Label $Label
    Assert-JsonArrayProperties -Value $Safety -Names @('violations') -Label $Label
    if (
        -not (Test-TrueBoolean -Value $Safety.passed) -or
        @($Safety.violations).Count -ne 0
    ) {
        throw "$Label is not a zero-violation safety pass."
    }
}

function Assert-ExpectedContainerIdentity {
    param(
        [Parameter(Mandatory = $true)][object] $Docker,
        [Parameter(Mandatory = $true)][string] $Label
    )

    foreach ($service in @('api', 'safety_control', 'worker')) {
        Assert-Properties -Value $Docker -Expected @($service) -Label $Label
        $container = $Docker.PSObject.Properties[$service].Value
        Assert-Properties -Value $container `
            -Expected @('image_id', 'revision', 'release_digest') `
            -Label "$Label.$service"
        Assert-JsonStringProperties -Value $container `
            -Names @('image_id', 'revision', 'release_digest') -NonEmpty `
            -Label "$Label.$service"
        if (
            [string]$container.image_id -cne $ImageId -or
            [string]$container.revision -cne $Revision -or
            [string]$container.release_digest -cne $ReleaseDigest
        ) {
            throw "$Label.$service has the wrong immutable image identity."
        }
    }
}

function Assert-ExpectedDatabaseIdentity {
    param(
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Database `
        -Expected @(
            'database_now', 'schema_head', 'active_epoch', 'database_boundaries',
            'global_control', 'venues', 'pending_commands',
            'active_canary_leases', 'canary_lease', 'live'
        ) -Label $Label
    Assert-Properties -Value $Database.active_epoch `
        -Expected @('id', 'opened_at', 'cohort', 'image_digest') `
        -Label "$Label.active_epoch"
    Assert-JsonIntegerProperties -Value $Database.active_epoch -Names @('id') `
        -NonNegative -Label "$Label.active_epoch"
    Assert-JsonIntegerProperties -Value $Database -Names @(
        'pending_commands', 'unexpected_pending_commands',
        'unexpected_commands_since_boundary', 'active_canary_leases'
    ) -NonNegative -Label $Label
    Assert-JsonStringProperties -Value $Database -Names @('schema_head') `
        -NonEmpty -Label $Label
    Assert-JsonStringProperties -Value $Database.active_epoch `
        -Names @('opened_at', 'cohort', 'image_digest') -NonEmpty `
        -Label "$Label.active_epoch"
    $databaseNow = ConvertTo-UtcInstant -Value $Database.database_now `
        -Label "$Label.database_now"
    $epochOpenedAt = ConvertTo-UtcInstant -Value $Database.active_epoch.opened_at `
        -Label "$Label.active_epoch.opened_at"
    if (
        [string]$Database.schema_head -cne $ExpectedSchemaHead -or
        [long]$Database.active_epoch.id -ne $EpochId -or
        [string]$Database.active_epoch.cohort -cne $Cohort -or
        [string]$Database.active_epoch.image_digest -cne $ReleaseDigest -or
        $epochOpenedAt -gt $databaseNow
    ) {
        throw "$Label has the wrong schema or active-epoch identity."
    }
    Assert-Properties -Value $Database.database_boundaries `
        -Expected @('runtime_exact', 'safety_exact') `
        -Label "$Label.database_boundaries"
    if (
        -not (Test-TrueBoolean -Value $Database.database_boundaries.runtime_exact) -or
        -not (Test-TrueBoolean -Value $Database.database_boundaries.safety_exact)
    ) {
        throw "$Label does not prove exact database authority boundaries."
    }
    Assert-Properties -Value $Database.global_control `
        -Expected @('mode', 'kill_switch', 'live_confirmation_until') `
        -Label "$Label.global_control"
    Assert-JsonStringProperties -Value $Database.global_control -Names @('mode') `
        -NonEmpty -Label "$Label.global_control"
    Assert-Properties -Value $Database.venues `
        -Expected @('polymarket', 'binance_spot') -Label "$Label.venues"
    $polymarket = $Database.venues.polymarket
    $binance = $Database.venues.binance_spot
    Assert-Properties -Value $polymarket `
        -Expected @(
            'enabled', 'mode', 'kill_switch', 'halted_reason',
            'live_confirmation_until'
        ) `
        -Label "$Label.venues.polymarket"
    Assert-Properties -Value $binance `
        -Expected @(
            'enabled', 'mode', 'kill_switch', 'halted_reason',
            'live_confirmation_until'
        ) `
        -Label "$Label.venues.binance_spot"
    Assert-JsonStringProperties -Value $polymarket -Names @('mode') `
        -NonEmpty -Label "$Label.venues.polymarket"
    Assert-JsonStringProperties -Value $binance -Names @('mode') `
        -NonEmpty -Label "$Label.venues.binance_spot"
    if (
        [string]$Database.global_control.mode -cne 'paper' -or
        -not (Test-TrueBoolean -Value $Database.global_control.kill_switch) -or
        $null -ne $Database.global_control.live_confirmation_until -or
        -not (Test-TrueBoolean -Value $polymarket.enabled) -or
        [string]$polymarket.mode -cne 'paper' -or
        -not (Test-TrueBoolean -Value $polymarket.kill_switch) -or
        $null -ne $polymarket.halted_reason -or
        $null -ne $polymarket.live_confirmation_until -or
        -not (Test-TrueBoolean -Value $binance.enabled) -or
        [string]$binance.mode -cne 'backtest' -or
        -not (Test-TrueBoolean -Value $binance.kill_switch) -or
        $null -ne $binance.live_confirmation_until -or
        [int]$Database.pending_commands -ne 0 -or
        [int]$Database.active_canary_leases -ne 0 -or
        $null -ne $Database.canary_lease
    ) {
        throw "$Label is not the required fail-closed preflight state."
    }
    foreach ($name in @(
        'orders', 'positions', 'intents', 'fill_legs', 'enrollments', 'outcomes'
    )) {
        Assert-JsonIntegerProperties -Value $Database.live -Names @($name) `
            -NonNegative -Label "$Label.live"
        if ([int]$Database.live.PSObject.Properties[$name].Value -ne 0) {
            throw "$Label contains unexpected LIVE evidence."
        }
    }
}

function Assert-ExpectedSnapshotIdentity {
    param(
        [Parameter(Mandatory = $true)][object] $Identity,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-ExactProperties -Value $Identity `
        -Expected @(
            'epoch_id', 'cohort', 'image_id', 'revision',
            'release_digest', 'schema_head'
        ) -Label $Label
    Assert-JsonIntegerProperties -Value $Identity -Names @('epoch_id') `
        -NonNegative -Label $Label
    Assert-JsonStringProperties -Value $Identity -Names @(
        'cohort', 'image_id', 'revision', 'release_digest', 'schema_head'
    ) -NonEmpty -Label $Label
    if (
        [long]$Identity.epoch_id -ne $EpochId -or
        [string]$Identity.cohort -cne $Cohort -or
        [string]$Identity.image_id -cne $ImageId -or
        [string]$Identity.revision -cne $Revision -or
        [string]$Identity.release_digest -cne $ReleaseDigest -or
        [string]$Identity.schema_head -cne $ExpectedSchemaHead
    ) {
        throw "$Label does not match the expected release identity."
    }
}

function Get-ContinuityLaneSeal {
    param(
        [Parameter(Mandatory = $true)][object] $Body,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Body -Expected @(
        'active_epoch', 'cohort_match', 'image_match',
        'execution_gate_allowed', 'execution_gate_reason', 'frozen_lanes',
        'lane_identity_mismatches', 'collection_window', 'continuity_lanes'
    ) -Label $Label
    Assert-Properties -Value $Body.active_epoch `
        -Expected @('id', 'cohort', 'image_digest') `
        -Label "$Label.active_epoch"
    Assert-Properties -Value $Body.collection_window `
        -Expected @('stalled_days', 'missing_scheduler_jobs') `
        -Label "$Label.collection_window"
    Assert-JsonIntegerProperties -Value $Body.active_epoch -Names @('id') `
        -NonNegative -Label "$Label.active_epoch"
    Assert-JsonIntegerProperties -Value $Body.collection_window `
        -Names @('stalled_days') -NonNegative `
        -Label "$Label.collection_window"
    Assert-JsonStringProperties -Value $Body.active_epoch `
        -Names @('cohort', 'image_digest') -NonEmpty `
        -Label "$Label.active_epoch"
    Assert-JsonStringProperties -Value $Body -Names @('execution_gate_reason') `
        -NonEmpty -Label $Label
    Assert-JsonArrayProperties -Value $Body -Names @(
        'frozen_lanes', 'lane_identity_mismatches', 'continuity_lanes'
    ) -Label $Label
    Assert-JsonArrayProperties -Value $Body.collection_window `
        -Names @('missing_scheduler_jobs') `
        -Label "$Label.collection_window"
    if (
        [long]$Body.active_epoch.id -ne $EpochId -or
        [string]$Body.active_epoch.cohort -cne $Cohort -or
        [string]$Body.active_epoch.image_digest -cne $ReleaseDigest -or
        -not (Test-TrueBoolean -Value $Body.cohort_match) -or
        -not (Test-TrueBoolean -Value $Body.image_match) -or
        -not (Test-TrueBoolean -Value $Body.execution_gate_allowed) -or
        [string]$Body.execution_gate_reason -cne 'evidence_continuity_match' -or
        @($Body.frozen_lanes).Count -ne 0 -or
        @($Body.lane_identity_mismatches).Count -ne 0 -or
        @($Body.collection_window.missing_scheduler_jobs).Count -ne 0 -or
        [int]$Body.collection_window.stalled_days -lt 0
    ) {
        throw "$Label does not prove the exact active release continuity gate."
    }

    $lanes = @($Body.continuity_lanes)
    $laneNames = @($lanes | ForEach-Object { [string]$_.lane_name } | Sort-Object)
    if (
        $lanes.Count -ne $ExpectedContinuityLanes.Count -or
        ($laneNames -join "`n") -cne
            (($ExpectedContinuityLanes | Sort-Object) -join "`n")
    ) {
        throw "$Label does not contain the exact six continuity lanes."
    }
    $seenIds = [Collections.Generic.HashSet[long]]::new()
    $records = [Collections.Generic.List[string]]::new()
    foreach ($lane in @($lanes | Sort-Object -Property lane_name)) {
        Assert-Properties -Value $lane -Expected @(
            'id', 'epoch_id', 'lane_name', 'action', 'identity_hash',
            'parent_lane_id', 'boundary_at', 'manifest', 'rationale', 'record_hash'
        ) -Label "$Label continuity lane"
        Assert-JsonIntegerProperties -Value $lane -Names @('id', 'epoch_id') `
            -NonNegative -Label "$Label continuity lane"
        Assert-JsonStringProperties -Value $lane -Names @(
            'lane_name', 'action', 'identity_hash', 'boundary_at',
            'rationale', 'record_hash'
        ) -NonEmpty -Label "$Label continuity lane"
        $laneId = [long]$lane.id
        $parentLaneId = $null
        if ($null -ne $lane.parent_lane_id) {
            if (
                -not (Test-JsonInteger -Value $lane.parent_lane_id) -or
                [long]$lane.parent_lane_id -le 0
            ) {
                throw "$Label continuity lane parent ID is invalid."
            }
            $parentLaneId = [long]$lane.parent_lane_id
        }
        $boundary = ConvertTo-UtcInstant -Value $lane.boundary_at `
            -Label "$Label.$([string]$lane.lane_name).boundary_at"
        if (
            $laneId -le 0 -or
            -not $seenIds.Add($laneId) -or
            [long]$lane.epoch_id -ne $EpochId -or
            [string]::IsNullOrWhiteSpace([string]$lane.action) -or
            [string]$lane.identity_hash -cnotmatch '^[0-9a-f]{64}$' -or
            [string]$lane.record_hash -cnotmatch '^[0-9a-f]{64}$' -or
            ($null -ne $parentLaneId -and $parentLaneId -le 0) -or
            $lane.manifest -isnot [pscustomobject] -or
            [string]::IsNullOrWhiteSpace([string]$lane.rationale)
        ) {
            throw "$Label contains an invalid continuity-lane identity."
        }
        $parentText = if ($null -eq $parentLaneId) {
            '<null>'
        }
        else {
            [string]$parentLaneId
        }
        $records.Add((
            @(
                [string]$laneId,
                [string]$lane.epoch_id,
                [string]$lane.lane_name,
                [string]$lane.action,
                [string]$lane.identity_hash,
                $parentText,
                $boundary.ToString('O'),
                [string]$lane.record_hash
            ) -join '|'
        ))
    }
    return $records -join "`n"
}

function Assert-RuntimeDatabaseEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][object] $Docker,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Database -Expected @('runtime_database') `
        -Label $Label
    $runtime = $Database.runtime_database
    Assert-ExactProperties -Value $runtime -Expected @(
        'database_name', 'collector', 'collector_exact',
        'expected_identities', 'client_backends', 'client_backend_sessions',
        'api_sessions', 'worker_sessions', 'safety_control_sessions',
        'unexpected_sessions', 'idle_in_transaction', 'lock_waiters'
    ) -Label "$Label.runtime_database"
    Assert-JsonStringProperties -Value $runtime -Names @('database_name') `
        -NonEmpty -Label "$Label.runtime_database"
    Assert-JsonArrayProperties -Value $runtime -Names @('client_backends') `
        -Label "$Label.runtime_database"
    if ([string]$runtime.database_name -cne 'stratus') {
        throw "$Label runtime database name is not stratus."
    }

    Assert-ExactProperties -Value $runtime.collector -Expected @(
        'pid', 'backend_type', 'usename', 'current_user',
        'application_name', 'client_addr'
    ) -Label "$Label.runtime_database.collector"
    Assert-JsonStringProperties -Value $runtime.collector -Names @(
        'backend_type', 'usename', 'current_user', 'application_name'
    ) -NonEmpty -Label "$Label.runtime_database.collector"
    if (
        -not (Test-TrueBoolean -Value $runtime.collector_exact) -or
        -not (Test-JsonInteger -Value $runtime.collector.pid) -or
        [long]$runtime.collector.pid -le 0 -or
        [string]$runtime.collector.backend_type -cne 'client backend' -or
        [string]$runtime.collector.usename -cne 'stratus' -or
        [string]$runtime.collector.current_user -cne 'stratus' -or
        [string]$runtime.collector.application_name -cne
            'stratus-paper-canary-collector' -or
        $null -ne $runtime.collector.client_addr
    ) {
        throw "$Label runtime database collector identity is not exact."
    }

    Assert-ExactProperties -Value $runtime.expected_identities `
        -Expected @('api', 'worker', 'safety_control') `
        -Label "$Label.runtime_database.expected_identities"
    $contracts = [ordered]@{
        api = [pscustomobject]@{
            application_name = 'stratus-api'
            max_sessions = 4
            docker_service = 'api'
        }
        worker = [pscustomobject]@{
            application_name = 'stratus-runtime'
            max_sessions = 15
            docker_service = 'worker'
        }
        safety_control = [pscustomobject]@{
            application_name = 'stratus-safety-control'
            max_sessions = 2
            docker_service = 'safety_control'
        }
    }
    $profiles = @{}
    foreach ($identity in $contracts.Keys) {
        $profile = $runtime.expected_identities.PSObject.Properties[$identity].Value
        Assert-ExactProperties -Value $profile -Expected @(
            'backend_type', 'usename', 'application_name',
            'client_addresses', 'max_sessions'
        ) -Label "$Label.runtime_database.expected_identities.$identity"
        Assert-JsonStringProperties -Value $profile -Names @(
            'backend_type', 'usename', 'application_name'
        ) -NonEmpty -Label "$Label.runtime_database.expected_identities.$identity"
        Assert-JsonArrayProperties -Value $profile -Names @('client_addresses') `
            -Label "$Label.runtime_database.expected_identities.$identity"
        $profileAddresses = @(
            Get-NormalizedIpAddressList -Value @($profile.client_addresses) `
                -Label "$Label runtime $identity profile"
        )
        $dockerService = [string]$contracts[$identity].docker_service
        Assert-Properties -Value $Docker -Expected @($dockerService) `
            -Label "$Label docker"
        $dockerContainer = $Docker.PSObject.Properties[$dockerService].Value
        Assert-Properties -Value $dockerContainer -Expected @('client_addresses') `
            -Label "$Label docker.$dockerService"
        $dockerAddresses = @(
            Get-NormalizedIpAddressList `
                -Value @($dockerContainer.client_addresses) `
                -Label "$Label docker $dockerService"
        )
        if (
            [string]$profile.backend_type -cne 'client backend' -or
            [string]$profile.application_name -cne
                [string]$contracts[$identity].application_name -or
            -not (Test-JsonInteger -Value $profile.max_sessions) -or
            [int]$profile.max_sessions -ne
                [int]$contracts[$identity].max_sessions -or
            ($profileAddresses -join "`n") -cne ($dockerAddresses -join "`n")
        ) {
            throw "$Label runtime $identity profile or source boundary is invalid."
        }
        $profiles[$identity] = [pscustomobject]@{
            backend_type = [string]$profile.backend_type
            usename = [string]$profile.usename
            application_name = [string]$profile.application_name
            client_addresses = @($profileAddresses)
            max_sessions = [int]$profile.max_sessions
        }
    }

    $runtimeRole = [string]$profiles.api.usename
    $runtimeRoleMatch = [regex]::Match(
        $runtimeRole,
        '^stratus_app_([0-9a-f]{32})$'
    )
    if (
        -not $runtimeRoleMatch.Success -or
        [string]$runtimeRoleMatch.Groups[1].Value -cne
            (Get-Md5Hex -Value ([string]$runtime.database_name)) -or
        [string]$profiles.worker.usename -cne $runtimeRole
    ) {
        throw "$Label runtime API/worker role identity is invalid."
    }
    $roleSuffix = [string]$runtimeRoleMatch.Groups[1].Value
    if (
        [string]$profiles.safety_control.usename -cne
            "stratus_safety_app_$roleSuffix"
    ) {
        throw "$Label runtime safety role identity is invalid."
    }

    foreach ($countName in @(
        'client_backend_sessions', 'api_sessions', 'worker_sessions',
        'safety_control_sessions', 'unexpected_sessions',
        'idle_in_transaction', 'lock_waiters'
    )) {
        $count = $runtime.PSObject.Properties[$countName].Value
        if (-not (Test-JsonInteger -Value $count) -or [long]$count -lt 0) {
            throw "$Label runtime database $countName is not a non-negative integer."
        }
    }

    $observed = @{
        api = 0
        worker = 0
        safety_control = 0
    }
    $pids = [Collections.Generic.HashSet[long]]::new()
    $rows = @($runtime.client_backends)
    foreach ($row in $rows) {
        Assert-ExactProperties -Value $row -Expected @(
            'pid', 'backend_type', 'usename', 'application_name',
            'client_addr', 'identity'
        ) -Label "$Label.runtime_database client backend"
        Assert-JsonStringProperties -Value $row -Names @(
            'backend_type', 'usename', 'application_name',
            'client_addr', 'identity'
        ) -NonEmpty -Label "$Label.runtime_database client backend"
        $identity = [string]$row.identity
        if (-not $profiles.ContainsKey($identity)) {
            throw "$Label contains an unexpected runtime database session."
        }
        if (
            -not (Test-JsonInteger -Value $row.pid) -or
            [long]$row.pid -le 0 -or
            -not $pids.Add([long]$row.pid) -or
            [long]$row.pid -eq [long]$runtime.collector.pid
        ) {
            throw "$Label runtime database session PID inventory is invalid."
        }
        $profile = $profiles[$identity]
        $rowAddresses = @(
            Get-NormalizedIpAddressList -Value @($row.client_addr) `
                -Label "$Label runtime $identity session"
        )
        if (
            $rowAddresses.Count -ne 1 -or
            [string]$row.backend_type -cne [string]$profile.backend_type -or
            [string]$row.usename -cne [string]$profile.usename -or
            [string]$row.application_name -cne
                [string]$profile.application_name -or
            [string]$rowAddresses[0] -cnotin @($profile.client_addresses)
        ) {
            throw "$Label runtime $identity session identity or source is invalid."
        }
        $observed[$identity] += 1
    }
    if (
        [int]$runtime.client_backend_sessions -ne $rows.Count -or
        [int]$runtime.api_sessions -ne [int]$observed.api -or
        [int]$runtime.worker_sessions -ne [int]$observed.worker -or
        [int]$runtime.safety_control_sessions -ne
            [int]$observed.safety_control -or
        [int]$runtime.unexpected_sessions -ne 0 -or
        [int]$runtime.idle_in_transaction -ne 0 -or
        [int]$runtime.lock_waiters -ne 0
    ) {
        throw "$Label runtime database inventory counts or safety state diverged."
    }
    foreach ($identity in $contracts.Keys) {
        if ([int]$observed[$identity] -gt [int]$profiles[$identity].max_sessions) {
            throw "$Label runtime $identity session pool exceeded its exact budget."
        }
    }
}

function Assert-ActiveDockerEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Docker,
        [Parameter(Mandatory = $true)][object] $Baseline,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Docker `
        -Expected @('api', 'safety_control', 'worker', 'postgres', 'topology') `
        -Label $Label
    Assert-Properties -Value $Baseline `
        -Expected @('api', 'safety_control', 'worker', 'postgres', 'topology') `
        -Label "$Label baseline"
    Assert-ExpectedContainerIdentity -Docker $Docker -Label $Label
    $expectedServices = @{
        api = 'api'
        safety_control = 'safety-control'
        worker = 'worker'
        postgres = 'postgres'
    }
    $expectedMemoryLimits = @{
        api = 1073741824L
        safety_control = 268435456L
        worker = 3221225472L
    }
    $project = [string]$Docker.api.compose_project
    if ([string]::IsNullOrWhiteSpace($project)) {
        throw "$Label has no Compose project identity."
    }
    $expectedNetworks = @{
        api = @("${project}_default")
        safety_control = @("${project}_safety-plane")
        worker = @("${project}_default")
        postgres = @("${project}_default", "${project}_safety-plane")
    }
    $expectedBindings = @{
        api = [pscustomobject]@{
            container_port = '8000/tcp'
            host_ip = '127.0.0.1'
            host_port = '8000'
        }
        safety_control = [pscustomobject]@{
            container_port = '8001/tcp'
            host_ip = '127.0.0.1'
            host_port = '8001'
        }
        postgres = [pscustomobject]@{
            container_port = '5432/tcp'
            host_ip = '127.0.0.1'
            host_port = '5432'
        }
    }
    foreach ($service in @('api', 'safety_control', 'worker', 'postgres')) {
        $container = $Docker.PSObject.Properties[$service].Value
        $baselineContainer = $Baseline.PSObject.Properties[$service].Value
        Assert-Properties -Value $container -Expected @(
            'service', 'container_id', 'container_name', 'status', 'running',
            'oom_killed', 'health', 'started_at', 'image_id', 'revision',
            'release_digest', 'restart_count', 'memory_limit_bytes',
            'operator_api_key_entry_count', 'published_ports',
            'compose_project', 'compose_service', 'compose_oneoff', 'networks',
            'client_addresses'
        ) -Label "$Label.$service"
        Assert-JsonStringProperties -Value $container -Names @(
            'service', 'container_id', 'container_name', 'status', 'started_at',
            'image_id', 'compose_project', 'compose_service', 'compose_oneoff'
        ) -NonEmpty -Label "$Label.$service"
        Assert-JsonArrayProperties -Value $container -Names @(
            'published_ports', 'networks', 'client_addresses'
        ) -Label "$Label.$service"
        foreach ($nullableStringName in @('revision', 'release_digest', 'health')) {
            $nullableString = $container.PSObject.Properties[$nullableStringName].Value
            if (
                $null -ne $nullableString -and
                (
                    $nullableString -isnot [string] -or
                    [string]::IsNullOrWhiteSpace([string]$nullableString)
                )
            ) {
                throw "$Label.$service.$nullableStringName is not null or a non-empty string."
            }
        }
        Assert-Properties -Value $baselineContainer -Expected @(
            'container_id', 'container_name', 'started_at', 'image_id', 'compose_project',
            'compose_service', 'compose_oneoff', 'networks', 'published_ports',
            'client_addresses', 'memory_limit_bytes', 'operator_api_key_entry_count'
        ) -Label "$Label baseline.$service"
        Assert-JsonIntegerProperties -Value $container `
            -Names @(
                'restart_count', 'memory_limit_bytes', 'operator_api_key_entry_count'
            ) -NonNegative `
            -Label "$Label.$service"
        Assert-JsonIntegerProperties -Value $baselineContainer `
            -Names @('memory_limit_bytes', 'operator_api_key_entry_count') -NonNegative `
            -Label "$Label baseline.$service"
        [void](ConvertTo-UtcInstant -Value $container.started_at `
            -Label "$Label.$service.started_at")
        $actualNetworks = @($container.networks | Sort-Object)
        $requiredNetworks = @($expectedNetworks[$service] | Sort-Object)
        if (
            @($container.networks | Where-Object {
                $_ -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$_)
            }).Count -ne 0 -or
            @($container.networks | Sort-Object -Unique).Count -ne
                @($container.networks).Count
        ) {
            throw "$Label.$service contains invalid network identities."
        }
        [void]@(
            Get-NormalizedIpAddressList -Value @($container.client_addresses) `
                -Label "$Label.$service"
        )
        $published = @($container.published_ports)
        if ($service -eq 'worker') {
            if ($published.Count -ne 0) {
                throw "$Label.worker unexpectedly publishes a host port."
            }
        }
        else {
            if ($published.Count -ne 1) {
                throw "$Label.$service does not have exactly one release binding."
            }
            Assert-ExactProperties -Value $published[0] -Expected @(
                'container_port', 'host_ip', 'host_port'
            ) -Label "$Label.$service published port"
            Assert-JsonStringProperties -Value $published[0] -Names @(
                'container_port', 'host_ip', 'host_port'
            ) -NonEmpty -Label "$Label.$service published port"
            $binding = $expectedBindings[$service]
            if (
                [string]$published[0].container_port -cne
                    [string]$binding.container_port -or
                [string]$published[0].host_ip -cne [string]$binding.host_ip -or
                [string]$published[0].host_port -cne [string]$binding.host_port
            ) {
                throw "$Label.$service is not bound to the exact loopback port."
            }
        }
        if (
            [string]$container.container_id -cnotmatch '^[0-9a-f]{64}$' -or
            [string]::IsNullOrWhiteSpace([string]$container.container_name) -or
            [string]$container.service -cne $expectedServices[$service] -or
            [string]$container.status -cne 'running' -or
            -not (Test-TrueBoolean -Value $container.running) -or
            -not (Test-FalseBoolean -Value $container.oom_killed) -or
            [int]$container.restart_count -ne 0 -or
            [int]$container.operator_api_key_entry_count -ne
                $(if ($service -eq 'api') { 1 } else { 0 }) -or
            (
                $expectedMemoryLimits.ContainsKey($service) -and
                [int64]$container.memory_limit_bytes -ne
                    [int64]$expectedMemoryLimits[$service]
            ) -or
            [string]$container.compose_project -cne $project -or
            [string]$container.compose_service -cne $expectedServices[$service] -or
            [string]$container.compose_oneoff -cne 'False' -or
            ($actualNetworks -join "`n") -cne ($requiredNetworks -join "`n") -or
            [string]$container.container_id -cne
                [string]$baselineContainer.container_id -or
            [string]$container.container_name -cne
                [string]$baselineContainer.container_name -or
            [string]$container.started_at -cne
                [string]$baselineContainer.started_at -or
            [string]$container.image_id -cne [string]$baselineContainer.image_id -or
            [int64]$container.memory_limit_bytes -ne
                [int64]$baselineContainer.memory_limit_bytes -or
            [int]$container.operator_api_key_entry_count -ne
                [int]$baselineContainer.operator_api_key_entry_count -or
            [string]$container.compose_project -cne
                [string]$baselineContainer.compose_project -or
            [string]$container.compose_service -cne
                [string]$baselineContainer.compose_service -or
            [string]$container.compose_oneoff -cne
                [string]$baselineContainer.compose_oneoff -or
            (Get-StableJson -Value @($container.networks)) -cne
                (Get-StableJson -Value @($baselineContainer.networks)) -or
            (Get-StableJson -Value @($container.published_ports)) -cne
                (Get-StableJson -Value @($baselineContainer.published_ports)) -or
            (Get-StableJson -Value @($container.client_addresses)) -cne
                (Get-StableJson -Value @($baselineContainer.client_addresses))
        ) {
            throw "$Label.$service drifted from the exact safe container baseline."
        }
    }
    foreach ($service in @('api', 'safety_control', 'postgres')) {
        $health = $Docker.PSObject.Properties[$service].Value.health
        if ($health -isnot [string] -or [string]::IsNullOrWhiteSpace($health)) {
            throw "$Label.$service health is not a non-empty JSON string."
        }
    }
    if (
        $null -ne $Docker.worker.health -and
        $Docker.worker.health -isnot [string]
    ) {
        throw "$Label.worker health is neither null nor a JSON string."
    }
    if (
        [string]$Docker.api.health -cne 'healthy' -or
        [string]$Docker.safety_control.health -cne 'healthy' -or
        [string]$Docker.postgres.health -cne 'healthy' -or
        (
            $null -ne $Docker.worker.health -and
            [string]$Docker.worker.health -cne 'healthy'
        )
    ) {
        throw "$Label contains an unhealthy required container."
    }

    $topology = $Docker.topology
    Assert-Properties -Value $topology -Expected @(
        'captured_at', 'project', 'allowed_services', 'protected_networks',
        'all_running_container_count', 'all_running_containers',
        'running_containers', 'unexpected_containers',
        'missing_container_ids', 'safe'
    ) -Label "$Label.topology"
    Assert-JsonStringProperties -Value $topology -Names @('project') `
        -NonEmpty -Label "$Label.topology"
    [void](ConvertTo-UtcInstant -Value $topology.captured_at `
        -Label "$Label.topology.captured_at")
    Assert-JsonArrayProperties -Value $topology -Names @(
        'allowed_services', 'protected_networks', 'all_running_containers',
        'running_containers', 'unexpected_containers', 'missing_container_ids'
    ) -Label "$Label.topology"
    $allowedServices = @($topology.allowed_services | Sort-Object)
    $requiredServices = @('api', 'safety-control', 'worker', 'postgres' | Sort-Object)
    $protectedNetworks = @($topology.protected_networks | Sort-Object)
    $requiredProtectedNetworks = @(
        "${project}_default", "${project}_safety-plane" | Sort-Object
    )
    $allRows = @($topology.all_running_containers)
    $runningRows = @($topology.running_containers)
    foreach ($listName in @('allowed_services', 'protected_networks')) {
        $listItems = @($topology.PSObject.Properties[$listName].Value)
        if (
            @($listItems | Where-Object {
                $_ -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$_)
            }).Count -ne 0 -or
            @($listItems | Sort-Object -Unique).Count -ne $listItems.Count
        ) {
            throw "$Label topology contains invalid $listName identities."
        }
    }
    if (
        -not (Test-JsonInteger -Value $topology.all_running_container_count) -or
        [int]$topology.all_running_container_count -ne $allRows.Count -or
        $allRows.Count -lt 4
    ) {
        throw "$Label topology has an inconsistent all-running inventory."
    }
    $allowedById = @{}
    $containersById = @{}
    foreach ($service in @('api', 'safety_control', 'worker', 'postgres')) {
        $container = $Docker.PSObject.Properties[$service].Value
        $allowedById[[string]$container.container_id] = [string]$expectedServices[$service]
        $containersById[[string]$container.container_id] = $container
    }
    if ($allowedById.Count -ne 4) {
        throw "$Label topology contains duplicate required container identities."
    }
    $allIds = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    $derivedRelevant = [Collections.Generic.List[object]]::new()
    foreach ($row in $allRows) {
        Assert-ExactProperties -Value $row -Expected @(
            'container_id', 'project', 'service', 'oneoff',
            'container_name', 'networks'
        ) -Label "$Label topology all-running row"
        Assert-JsonStringProperties -Value $row `
            -Names @('project', 'service', 'oneoff') `
            -Label "$Label topology all-running row"
        Assert-JsonStringProperties -Value $row `
            -Names @('container_id', 'container_name') -NonEmpty `
            -Label "$Label topology all-running row"
        Assert-JsonArrayProperties -Value $row -Names @('networks') `
            -Label "$Label topology all-running row"
        if (
            [string]$row.container_id -cnotmatch '^[0-9a-f]{64}$' -or
            -not $allIds.Add([string]$row.container_id) -or
            [string]::IsNullOrWhiteSpace([string]$row.container_name)
        ) {
            throw "$Label topology all-running identity inventory is invalid."
        }
        $rowNetworks = @($row.networks)
        if (
            @($rowNetworks | Where-Object {
                $_ -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$_)
            }).Count -ne 0 -or
            @($rowNetworks | Sort-Object -Unique).Count -ne $rowNetworks.Count
        ) {
            throw "$Label topology contains invalid network evidence."
        }
        $isRelevant = (
            $allowedById.ContainsKey([string]$row.container_id) -or
            [string]$row.project -ceq $project -or
            @($rowNetworks | Where-Object {
                [string]$_ -cin $requiredProtectedNetworks
            }).Count -ne 0
        )
        if ($isRelevant) {
            $derivedRelevant.Add($row)
            $expectedContainer = if (
                $containersById.ContainsKey([string]$row.container_id)
            ) {
                $containersById[[string]$row.container_id]
            }
            else {
                $null
            }
            if (
                -not $allowedById.ContainsKey([string]$row.container_id) -or
                [string]$row.project -cne $project -or
                [string]$row.service -cne
                    [string]$allowedById[[string]$row.container_id] -or
                [string]$row.oneoff -cne 'False' -or
                $null -eq $expectedContainer -or
                [string]$row.container_name -cne
                    [string]$expectedContainer.container_name -or
                ((@($row.networks | Sort-Object)) -join "`n") -cne
                    ((@($expectedContainer.networks | Sort-Object)) -join "`n")
            ) {
                throw "$Label topology independently exposes an unexpected container."
            }
        }
    }
    $derivedIds = @(
        $derivedRelevant | ForEach-Object { [string]$_.container_id } | Sort-Object
    )
    $reportedIds = @(
        $runningRows | ForEach-Object { [string]$_.container_id } | Sort-Object
    )
    if (
        -not (Test-TrueBoolean -Value $topology.safe) -or
        [string]$topology.project -cne $project -or
        ($allowedServices -join "`n") -cne ($requiredServices -join "`n") -or
        ($protectedNetworks -join "`n") -cne
            ($requiredProtectedNetworks -join "`n") -or
        $derivedRelevant.Count -ne 4 -or
        $runningRows.Count -ne 4 -or
        ($derivedIds -join "`n") -cne ($reportedIds -join "`n") -or
        @($topology.unexpected_containers).Count -ne 0 -or
        @($topology.missing_container_ids).Count -ne 0
    ) {
        throw "$Label does not prove the exact safe running topology."
    }
    foreach ($service in @('api', 'safety_control', 'worker', 'postgres')) {
        $container = $Docker.PSObject.Properties[$service].Value
        $expectedService = $expectedServices[$service]
        $rows = @($runningRows | Where-Object {
            [string]$_.container_id -ceq [string]$container.container_id
        })
        if ($rows.Count -ne 1) {
            throw "$Label topology omits or duplicates $service."
        }
        Assert-ExactProperties -Value $rows[0] `
            -Expected @(
                'container_id', 'project', 'service', 'oneoff',
                'container_name', 'networks'
            ) `
            -Label "$Label topology $service"
        Assert-JsonStringProperties -Value $rows[0] -Names @(
            'container_id', 'project', 'service', 'oneoff', 'container_name'
        ) -NonEmpty -Label "$Label topology $service"
        Assert-JsonArrayProperties -Value $rows[0] -Names @('networks') `
            -Label "$Label topology $service"
        if (
            @($rows[0].networks | Where-Object {
                $_ -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$_)
            }).Count -ne 0 -or
            @($rows[0].networks | Sort-Object -Unique).Count -ne
                @($rows[0].networks).Count
        ) {
            throw "$Label topology $service contains invalid networks."
        }
        if (
            [string]$rows[0].project -cne $project -or
            [string]$rows[0].service -cne $expectedService -or
            [string]$rows[0].oneoff -cne 'False' -or
            [string]$rows[0].container_name -cne
                [string]$container.container_name -or
            ((@($rows[0].networks | Sort-Object)) -join "`n") -cne
                ((@($container.networks | Sort-Object)) -join "`n")
        ) {
            throw "$Label topology identity for $service is unsafe."
        }
    }
}

function Assert-HttpSample {
    param(
        [Parameter(Mandatory = $true)][object] $Sample,
        [Parameter(Mandatory = $true)][string] $ExpectedPath,
        [int] $ExpectedStatusCode = 200,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Sample -Expected @(
        'path', 'status_code', 'elapsed_ms', 'bytes', 'content_sha256', 'body'
    ) -Label $Label
    Assert-JsonIntegerProperties -Value $Sample `
        -Names @('status_code', 'bytes') -NonNegative -Label $Label
    Assert-JsonNumberProperties -Value $Sample -Names @('elapsed_ms') `
        -NonNegative -Label $Label
    Assert-JsonStringProperties -Value $Sample `
        -Names @('path', 'content_sha256') -NonEmpty -Label $Label
    if (
        [string]$Sample.path -cne $ExpectedPath -or
        [int]$Sample.status_code -ne $ExpectedStatusCode -or
        [double]$Sample.elapsed_ms -lt 0 -or
        [double]$Sample.elapsed_ms -ge 3000 -or
        [long]$Sample.bytes -le 0 -or
        [string]$Sample.content_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $null -eq $Sample.body
    ) {
        throw "$Label is not an exact successful bounded HTTP sample."
    }
}

function Get-ExactPositiveIdList {
    param(
        [AllowNull()][object[]] $Value,
        [switch] $AllowEmpty,
        [switch] $RequireDescending,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $items = @($Value)
    if (-not $AllowEmpty -and $items.Count -eq 0) {
        throw "$Label contains no IDs."
    }
    $ids = [Collections.Generic.List[long]]::new()
    $seen = [Collections.Generic.HashSet[long]]::new()
    $previous = $null
    foreach ($item in $items) {
        if (
            -not (Test-JsonInteger -Value $item) -or
            [long]$item -le 0 -or
            -not $seen.Add([long]$item) -or
            (
                $RequireDescending -and
                $null -ne $previous -and
                [long]$item -ge [long]$previous
            )
        ) {
            throw "$Label is not a valid unique positive ID sequence."
        }
        $ids.Add([long]$item)
        $previous = [long]$item
    }
    return @($ids)
}

function Assert-ApiDatabaseCoherenceEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Http,
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][object] $Coherence,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Database -Expected @(
        'api_expected', 'global_control', 'venues', 'active_epoch'
    ) -Label "$Label database"
    $expected = $Database.api_expected
    Assert-ExactProperties -Value $expected -Expected @(
        'overview', 'venue_count', 'command_ids', 'job_ids',
        'execution_cost', 'validation_ledger'
    ) -Label "$Label database.api_expected"
    Assert-JsonArrayProperties -Value $expected `
        -Names @('command_ids', 'job_ids') `
        -Label "$Label database.api_expected"

    $expectedOverview = $expected.overview
    $overview = $Http.overview.body
    Assert-ExactProperties -Value $expectedOverview -Expected @(
        'mode', 'kill_switch', 'active_markets', 'blocked_markets',
        'open_positions', 'pending_signals', 'bankroll_usd',
        'open_exposure_usd'
    ) -Label "$Label database.api_expected.overview"
    Assert-Properties -Value $overview -Expected @(
        'runtime', 'active_markets', 'blocked_markets', 'open_positions',
        'pending_signals', 'bankroll_usd', 'open_exposure_usd'
    ) -Label "$Label overview body"
    Assert-Properties -Value $overview.runtime -Expected @('mode', 'kill_switch') `
        -Label "$Label overview runtime"
    Assert-JsonStringProperties -Value $expectedOverview -Names @('mode') `
        -NonEmpty -Label "$Label database.api_expected.overview"
    Assert-JsonStringProperties -Value $overview.runtime -Names @('mode') `
        -NonEmpty -Label "$Label overview runtime"
    foreach ($countName in @(
        'active_markets', 'blocked_markets', 'open_positions', 'pending_signals'
    )) {
        $expectedCount = $expectedOverview.PSObject.Properties[$countName].Value
        $actualCount = $overview.PSObject.Properties[$countName].Value
        if (
            -not (Test-JsonInteger -Value $expectedCount) -or
            -not (Test-JsonInteger -Value $actualCount) -or
            [long]$expectedCount -lt 0 -or
            [long]$actualCount -ne [long]$expectedCount
        ) {
            throw "$Label overview $countName diverged from PostgreSQL."
        }
    }
    if (
        [string]$expectedOverview.mode -cne
            [string]$Database.global_control.mode -or
        [string]$overview.runtime.mode -cne [string]$expectedOverview.mode -or
        $expectedOverview.kill_switch -isnot [bool] -or
        $overview.runtime.kill_switch -isnot [bool] -or
        [bool]$expectedOverview.kill_switch -ne
            [bool]$Database.global_control.kill_switch -or
        [bool]$overview.runtime.kill_switch -ne
            [bool]$expectedOverview.kill_switch -or
        -not (Test-JsonNumber -Value $expectedOverview.open_exposure_usd) -or
        -not (Test-JsonNumber -Value $overview.open_exposure_usd) -or
        [Math]::Abs(
            [double]$overview.open_exposure_usd -
            [double]$expectedOverview.open_exposure_usd
        ) -gt 0.01
    ) {
        throw "$Label overview controls or exposure diverged from PostgreSQL."
    }
    if ($null -eq $expectedOverview.bankroll_usd) {
        if ($null -ne $overview.bankroll_usd) {
            throw "$Label overview bankroll nullability diverged from PostgreSQL."
        }
    }
    elseif (
        -not (Test-JsonNumber -Value $expectedOverview.bankroll_usd) -or
        -not (Test-JsonNumber -Value $overview.bankroll_usd) -or
        [Math]::Abs(
            [double]$overview.bankroll_usd -
            [double]$expectedOverview.bankroll_usd
        ) -gt 0.01
    ) {
        throw "$Label overview bankroll diverged from PostgreSQL."
    }

    if (
        -not (Test-JsonInteger -Value $expected.venue_count) -or
        [int]$expected.venue_count -ne 2
    ) {
        throw "$Label database venue count is not the exact release inventory."
    }
    $venueItems = @($Http.venues.body.items)
    Assert-JsonArrayProperties -Value $Http.venues.body -Names @('items') `
        -Label "$Label HTTP venues body"
    if ($venueItems.Count -ne [int]$expected.venue_count) {
        throw "$Label HTTP venue inventory diverged from PostgreSQL."
    }
    foreach ($venueName in @('polymarket', 'binance_spot')) {
        $items = @($venueItems | Where-Object {
            [string]$_.venue -ceq $venueName
        })
        if ($items.Count -ne 1) {
            throw "$Label HTTP venue inventory is not exact."
        }
        $item = $items[0]
        $databaseVenue = $Database.venues.PSObject.Properties[$venueName].Value
        Assert-Properties -Value $item -Expected @(
            'venue', 'enabled', 'mode', 'kill_switch', 'halted_reason',
            'live_confirmation_until'
        ) -Label "$Label HTTP venue $venueName"
        Assert-JsonStringProperties -Value $item -Names @('venue', 'mode') `
            -NonEmpty -Label "$Label HTTP venue $venueName"
        if (
            $item.enabled -isnot [bool] -or
            $item.kill_switch -isnot [bool] -or
            [bool]$item.enabled -ne [bool]$databaseVenue.enabled -or
            [string]$item.mode -cne [string]$databaseVenue.mode -or
            [bool]$item.kill_switch -ne [bool]$databaseVenue.kill_switch -or
            -not (Test-ExactNullableString -Actual $item.halted_reason `
                -Expected $databaseVenue.halted_reason) -or
            -not (Test-ExactNullableString `
                -Actual $item.live_confirmation_until `
                -Expected $databaseVenue.live_confirmation_until)
        ) {
            throw "$Label HTTP venue $venueName diverged from PostgreSQL."
        }
    }

    $expectedCommandIds = @(
        Get-ExactPositiveIdList -Value @($expected.command_ids) -AllowEmpty `
            -RequireDescending `
            -Label "$Label database command IDs"
    )
    Assert-Properties -Value $Http.commands.body -Expected @('items') `
        -Label "$Label commands body"
    Assert-JsonArrayProperties -Value $Http.commands.body -Names @('items') `
        -Label "$Label commands body"
    $actualCommandIdsRaw = @(
        @($Http.commands.body.items) | ForEach-Object {
            Assert-Properties -Value $_ -Expected @('id') `
                -Label "$Label command item"
            $_.id
        }
    )
    $actualCommandIds = @(
        Get-ExactPositiveIdList -Value $actualCommandIdsRaw -AllowEmpty `
            -RequireDescending `
            -Label "$Label HTTP command IDs"
    )
    if (($actualCommandIds -join ',') -cne ($expectedCommandIds -join ',')) {
        throw "$Label HTTP command IDs diverged from PostgreSQL."
    }

    $expectedJobIds = @(
        Get-ExactPositiveIdList -Value @($expected.job_ids) `
            -Label "$Label database job IDs"
    )
    Assert-Properties -Value $Http.jobs.body -Expected @('items') `
        -Label "$Label jobs body"
    Assert-JsonArrayProperties -Value $Http.jobs.body -Names @('items') `
        -Label "$Label jobs body"
    $actualJobIdsRaw = @(
        @($Http.jobs.body.items) | ForEach-Object {
            Assert-Properties -Value $_ -Expected @('id') `
                -Label "$Label job item"
            $_.id
        }
    )
    $actualJobIds = @(
        Get-ExactPositiveIdList -Value $actualJobIdsRaw `
            -Label "$Label HTTP job IDs"
    )
    if (($actualJobIds -join ',') -cne ($expectedJobIds -join ',')) {
        throw "$Label HTTP job IDs diverged from PostgreSQL."
    }

    $expectedCost = $expected.execution_cost
    $cost = $Http.execution_cost.body
    $costFields = @(
        'opened_at', 'definition_version', 'definition_hash', 'attempts',
        'filled_attempts', 'no_fill_attempts', 'sealed_attempts',
        'pending_attempts', 'maker_attempts', 'maker_fills'
    )
    Assert-ExactProperties -Value $expectedCost -Expected $costFields `
        -Label "$Label database execution cost"
    Assert-Properties -Value $cost -Expected (@('mode') + $costFields) `
        -Label "$Label HTTP execution cost"
    Assert-JsonStringProperties -Value $expectedCost -Names @(
        'opened_at', 'definition_version', 'definition_hash'
    ) -NonEmpty -Label "$Label database execution cost"
    Assert-JsonStringProperties -Value $cost -Names @(
        'mode', 'opened_at', 'definition_version', 'definition_hash'
    ) -NonEmpty -Label "$Label HTTP execution cost"
    $expectedOpenedAt = ConvertTo-UtcInstant -Value $expectedCost.opened_at `
        -Label "$Label database execution cost opened_at"
    $actualOpenedAt = ConvertTo-UtcInstant -Value $cost.opened_at `
        -Label "$Label HTTP execution cost opened_at"
    foreach ($countName in @(
        'attempts', 'filled_attempts', 'no_fill_attempts', 'sealed_attempts',
        'pending_attempts', 'maker_attempts', 'maker_fills'
    )) {
        $expectedCount = $expectedCost.PSObject.Properties[$countName].Value
        $actualCount = $cost.PSObject.Properties[$countName].Value
        if (
            -not (Test-JsonInteger -Value $expectedCount) -or
            -not (Test-JsonInteger -Value $actualCount) -or
            [long]$expectedCount -lt 0 -or
            [long]$actualCount -ne [long]$expectedCount
        ) {
            throw "$Label execution-cost $countName diverged from PostgreSQL."
        }
    }
    if (
        [string]$cost.mode -cne 'paper' -or
        $actualOpenedAt -ne $expectedOpenedAt -or
        [string]::IsNullOrWhiteSpace([string]$expectedCost.definition_version) -or
        [string]$cost.definition_version -cne
            [string]$expectedCost.definition_version -or
        [string]$expectedCost.definition_hash -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$cost.definition_hash -cne [string]$expectedCost.definition_hash -or
        [int]$expectedCost.pending_attempts -ne [Math]::Max(
            0,
            [int]$expectedCost.attempts - [int]$expectedCost.sealed_attempts
        ) -or
        [int]$expectedCost.filled_attempts -gt [int]$expectedCost.attempts -or
        [int]$expectedCost.sealed_attempts -gt [int]$expectedCost.attempts -or
        [int]$expectedCost.maker_attempts -gt [int]$expectedCost.attempts -or
        [int]$expectedCost.maker_fills -gt [int]$expectedCost.maker_attempts
    ) {
        throw "$Label execution-cost identity or count invariants are invalid."
    }

    $expectedLedger = $expected.validation_ledger
    $ledger = $Http.validation_ledger.body
    Assert-ExactProperties -Value $expectedLedger -Expected @(
        'selected_pipeline_cohort', 'enrolled_orders', 'pending_outcomes'
    ) -Label "$Label database validation ledger"
    Assert-Properties -Value $ledger -Expected @(
        'mode', 'selected_pipeline_cohort', 'selected_is_current',
        'enrolled_orders', 'pending_outcomes'
    ) -Label "$Label HTTP validation ledger"
    Assert-JsonStringProperties -Value $expectedLedger `
        -Names @('selected_pipeline_cohort') -NonEmpty `
        -Label "$Label database validation ledger"
    Assert-JsonStringProperties -Value $ledger `
        -Names @('mode', 'selected_pipeline_cohort') -NonEmpty `
        -Label "$Label HTTP validation ledger"
    Assert-JsonIntegerProperties -Value $Database.active_epoch -Names @('id') `
        -NonNegative -Label "$Label database active epoch"
    Assert-JsonIntegerProperties `
        -Value $Http.continuity.body.active_epoch -Names @('id') `
        -NonNegative -Label "$Label HTTP continuity active epoch"
    foreach ($countName in @('enrolled_orders', 'pending_outcomes')) {
        $expectedCount = $expectedLedger.PSObject.Properties[$countName].Value
        $actualCount = $ledger.PSObject.Properties[$countName].Value
        if (
            -not (Test-JsonInteger -Value $expectedCount) -or
            -not (Test-JsonInteger -Value $actualCount) -or
            [long]$expectedCount -lt 0 -or
            [long]$actualCount -ne [long]$expectedCount
        ) {
            throw "$Label validation-ledger $countName diverged from PostgreSQL."
        }
    }
    if (
        [string]$ledger.mode -cne 'paper' -or
        -not (Test-TrueBoolean -Value $ledger.selected_is_current) -or
        [string]$expectedLedger.selected_pipeline_cohort -cne
            [string]$Database.active_epoch.cohort -or
        [string]$ledger.selected_pipeline_cohort -cne
            [string]$expectedLedger.selected_pipeline_cohort -or
        [long]$Http.continuity.body.active_epoch.id -ne
            [long]$Database.active_epoch.id -or
        [string]$Http.continuity.body.active_epoch.cohort -cne
            [string]$Database.active_epoch.cohort -or
        [string]$Http.continuity.body.active_epoch.image_digest -cne
            [string]$Database.active_epoch.image_digest
    ) {
        throw "$Label continuity or validation-ledger identity diverged from PostgreSQL."
    }

    Assert-ExactProperties -Value $Coherence -Expected @(
        'attempts', 'stable_api_expected', 'api_expected_sha256'
    ) -Label "$Label API/database coherence"
    Assert-JsonStringProperties -Value $Coherence `
        -Names @('api_expected_sha256') -NonEmpty `
        -Label "$Label API/database coherence"
    $canonicalExpectedJson = $expected | ConvertTo-Json -Depth 12 -Compress
    $canonicalExpectedHash = Get-Sha256 -Bytes (
        [Text.UTF8Encoding]::new($false).GetBytes($canonicalExpectedJson)
    )
    if (
        -not (Test-JsonInteger -Value $Coherence.attempts) -or
        [int]$Coherence.attempts -lt 1 -or
        [int]$Coherence.attempts -gt 2 -or
        -not (Test-TrueBoolean -Value $Coherence.stable_api_expected) -or
        [string]$Coherence.api_expected_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$Coherence.api_expected_sha256 -cne $canonicalExpectedHash
    ) {
        throw "$Label canonical API/database expectation seal is invalid."
    }
}

function Assert-SnapshotHttpEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Http,
        [Parameter(Mandatory = $true)][object] $SafetyHttp,
        [Parameter(Mandatory = $true)][object] $Coherence,
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][object] $BinanceBaseline,
        [Parameter(Mandatory = $true)][int] $BaselineStalledDays,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $paths = @{
        healthz = 'healthz'
        readyz = 'readyz'
        overview = 'api/v1/overview'
        venues = 'api/v1/venues'
        commands = 'api/v1/commands?limit=200'
        jobs = 'api/v1/jobs?limit=200'
        continuity = 'api/v1/evidence-continuity'
        execution_cost = 'api/v1/execution-cost-report?mode=paper'
        validation_ledger = 'api/v1/validation-ledger?mode=paper'
    }
    Assert-Properties -Value $Http -Expected $ExpectedHttpEndpoints -Label $Label
    foreach ($name in $ExpectedHttpEndpoints) {
        Assert-HttpSample -Sample $Http.PSObject.Properties[$name].Value `
            -ExpectedPath $paths[$name] -Label "$Label.$name"
    }
    Assert-Properties -Value $Http.healthz.body -Expected @('status') `
        -Label "$Label.healthz.body"
    Assert-Properties -Value $Http.readyz.body -Expected @('status', 'reasons') `
        -Label "$Label.readyz.body"
    Assert-Properties -Value $Http.overview.body -Expected @('runtime') `
        -Label "$Label.overview.body"
    Assert-Properties -Value $Http.overview.body.runtime `
        -Expected @('mode', 'kill_switch') -Label "$Label.overview.runtime"
    Assert-JsonStringProperties -Value $Http.healthz.body -Names @('status') `
        -NonEmpty -Label "$Label.healthz.body"
    Assert-JsonStringProperties -Value $Http.readyz.body -Names @('status') `
        -NonEmpty -Label "$Label.readyz.body"
    Assert-JsonArrayProperties -Value $Http.readyz.body -Names @('reasons') `
        -Label "$Label.readyz.body"
    Assert-JsonStringProperties -Value $Http.overview.body.runtime `
        -Names @('mode') -NonEmpty -Label "$Label.overview.runtime"
    if (
        [string]$Http.healthz.body.status -cne 'ok' -or
        [string]$Http.readyz.body.status -cne 'ready' -or
        @($Http.readyz.body.reasons).Count -ne 0 -or
        [string]$Http.overview.body.runtime.mode -cne 'paper' -or
        -not (Test-FalseBoolean -Value $Http.overview.body.runtime.kill_switch)
    ) {
        throw "$Label does not prove an active PAPER-only API."
    }

    Assert-Properties -Value $Http.venues.body -Expected @('items') `
        -Label "$Label.venues.body"
    Assert-JsonArrayProperties -Value $Http.venues.body -Names @('items') `
        -Label "$Label.venues.body"
    $venueItems = @($Http.venues.body.items)
    foreach ($venueName in @('polymarket', 'binance_spot')) {
        $items = @($venueItems | Where-Object { [string]$_.venue -ceq $venueName })
        if ($items.Count -ne 1) {
            throw "$Label does not contain exactly one $venueName HTTP control."
        }
        $item = $items[0]
        $databaseVenue = $Database.venues.PSObject.Properties[$venueName].Value
        Assert-Properties -Value $item -Expected @(
            'enabled', 'mode', 'kill_switch', 'halted_reason',
            'live_confirmation_until'
        ) -Label "$Label.venues.$venueName"
        Assert-JsonStringProperties -Value $item -Names @('venue', 'mode') `
            -NonEmpty -Label "$Label.venues.$venueName"
        if (
            $item.enabled -isnot [bool] -or
            $item.kill_switch -isnot [bool] -or
            [bool]$item.enabled -ne [bool]$databaseVenue.enabled -or
            [string]$item.mode -cne [string]$databaseVenue.mode -or
            [bool]$item.kill_switch -ne [bool]$databaseVenue.kill_switch -or
            -not (Test-ExactNullableString -Actual $item.halted_reason `
                -Expected $databaseVenue.halted_reason) -or
            -not (Test-ExactNullableString `
                -Actual $item.live_confirmation_until `
                -Expected $databaseVenue.live_confirmation_until)
        ) {
            throw "$Label HTTP/database $venueName controls diverged."
        }
    }
    $httpBinance = @($venueItems | Where-Object {
        [string]$_.venue -ceq 'binance_spot'
    })[0]
    if (
        -not (Test-TrueBoolean -Value $httpBinance.enabled) -or
        [string]$httpBinance.mode -cne 'backtest' -or
        -not (Test-TrueBoolean -Value $httpBinance.kill_switch) -or
        -not (Test-ExactNullableString -Actual $httpBinance.halted_reason `
            -Expected $BinanceBaseline.halted_reason) -or
        -not (Test-ExactNullableString `
            -Actual $httpBinance.live_confirmation_until `
            -Expected $BinanceBaseline.live_confirmation_until)
    ) {
        throw "$Label changed the sealed Binance HTTP baseline."
    }

    Assert-Properties -Value $SafetyHttp -Expected @('healthz', 'readyz') `
        -Label "$Label safety-control"
    Assert-HttpSample -Sample $SafetyHttp.healthz -ExpectedPath 'healthz' `
        -Label "$Label safety-control.healthz"
    Assert-HttpSample -Sample $SafetyHttp.readyz -ExpectedPath 'readyz' `
        -Label "$Label safety-control.readyz"
    Assert-Properties -Value $SafetyHttp.healthz.body -Expected @('status') `
        -Label "$Label safety-control.healthz.body"
    Assert-Properties -Value $SafetyHttp.readyz.body `
        -Expected @('status', 'database_boundary', 'reasons') `
        -Label "$Label safety-control.readyz.body"
    Assert-JsonStringProperties -Value $SafetyHttp.healthz.body `
        -Names @('status') -NonEmpty -Label "$Label safety-control.healthz.body"
    Assert-JsonStringProperties -Value $SafetyHttp.readyz.body `
        -Names @('status', 'database_boundary') -NonEmpty `
        -Label "$Label safety-control.readyz.body"
    Assert-JsonArrayProperties -Value $SafetyHttp.readyz.body -Names @('reasons') `
        -Label "$Label safety-control.readyz.body"
    if (
        [string]$SafetyHttp.healthz.body.status -cne 'ok' -or
        [string]$SafetyHttp.readyz.body.status -cne 'ready' -or
        [string]$SafetyHttp.readyz.body.database_boundary -cne 'exact' -or
        @($SafetyHttp.readyz.body.reasons).Count -ne 0
    ) {
        throw "$Label safety-control HTTP state is not ready and exact."
    }

    Assert-Properties -Value $Coherence `
        -Expected @('attempts', 'stable_api_expected', 'api_expected_sha256') `
        -Label "$Label api/database coherence"
    Assert-JsonIntegerProperties -Value $Coherence -Names @('attempts') `
        -NonNegative -Label "$Label api/database coherence"
    Assert-JsonStringProperties -Value $Coherence `
        -Names @('api_expected_sha256') -NonEmpty `
        -Label "$Label api/database coherence"
    if (
        $Coherence.attempts -isnot [int] -and
        $Coherence.attempts -isnot [long]
    ) {
        throw "$Label API/database coherence attempt count is not an integer."
    }
    if (
        [int]$Coherence.attempts -lt 1 -or
        [int]$Coherence.attempts -gt 2 -or
        -not (Test-TrueBoolean -Value $Coherence.stable_api_expected) -or
        [string]$Coherence.api_expected_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$Coherence.api_expected_sha256 -cne
            (Get-ApiExpectedSha256 -Database $Database)
    ) {
        throw "$Label does not prove a bounded coherent API/database sample."
    }
    Assert-ApiDatabaseCoherenceEvidence -Http $Http -Database $Database `
        -Coherence $Coherence -Label $Label
    $laneSeal = Get-ContinuityLaneSeal -Body $Http.continuity.body `
        -Label "$Label.continuity.body"
    if (
        [int]$Http.continuity.body.collection_window.stalled_days -gt
            $BaselineStalledDays
    ) {
        throw "$Label increased continuity stalled_days."
    }
    return $laneSeal
}

function Assert-PreflightHttpEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Http,
        [Parameter(Mandatory = $true)][object] $SafetyHttp,
        [Parameter(Mandatory = $true)][object] $Coherence,
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][object] $BinanceBaseline,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $paths = @{
        healthz = 'healthz'
        readyz = 'readyz'
        overview = 'api/v1/overview'
        venues = 'api/v1/venues'
        commands = 'api/v1/commands?limit=200'
        jobs = 'api/v1/jobs?limit=200'
        continuity = 'api/v1/evidence-continuity'
        execution_cost = 'api/v1/execution-cost-report?mode=paper'
        validation_ledger = 'api/v1/validation-ledger?mode=paper'
    }
    Assert-Properties -Value $Http -Expected $ExpectedHttpEndpoints -Label $Label
    foreach ($name in $ExpectedHttpEndpoints) {
        $status = if ($name -eq 'readyz') { 503 } else { 200 }
        Assert-HttpSample -Sample $Http.PSObject.Properties[$name].Value `
            -ExpectedPath $paths[$name] -ExpectedStatusCode $status `
            -Label "$Label.$name"
    }
    Assert-Properties -Value $Http.healthz.body -Expected @('status') `
        -Label "$Label.healthz.body"
    Assert-Properties -Value $Http.readyz.body -Expected @('status', 'reasons') `
        -Label "$Label.readyz.body"
    Assert-Properties -Value $Http.overview.body -Expected @('runtime') `
        -Label "$Label.overview.body"
    Assert-Properties -Value $Http.overview.body.runtime `
        -Expected @('mode', 'kill_switch') -Label "$Label.overview.runtime"
    Assert-JsonStringProperties -Value $Http.healthz.body -Names @('status') `
        -NonEmpty -Label "$Label.healthz.body"
    Assert-JsonStringProperties -Value $Http.readyz.body -Names @('status') `
        -NonEmpty -Label "$Label.readyz.body"
    Assert-JsonArrayProperties -Value $Http.readyz.body -Names @('reasons') `
        -Label "$Label.readyz.body"
    Assert-JsonStringProperties -Value $Http.overview.body.runtime `
        -Names @('mode') -NonEmpty -Label "$Label.overview.runtime"
    if (
        [string]$Http.healthz.body.status -cne 'ok' -or
        [string]$Http.readyz.body.status -cne 'not_ready' -or
        @($Http.readyz.body.reasons).Count -ne 1 -or
        $Http.readyz.body.reasons[0] -isnot [string] -or
        [string]$Http.readyz.body.reasons[0] -cne 'kill_switch' -or
        [string]$Http.overview.body.runtime.mode -cne 'paper' -or
        -not (Test-TrueBoolean -Value $Http.overview.body.runtime.kill_switch)
    ) {
        throw "$Label does not prove the exact fail-closed API preflight."
    }
    Assert-Properties -Value $Http.venues.body -Expected @('items') `
        -Label "$Label.venues.body"
    Assert-JsonArrayProperties -Value $Http.venues.body -Names @('items') `
        -Label "$Label.venues.body"
    $venueItems = @($Http.venues.body.items)
    foreach ($venueName in @('polymarket', 'binance_spot')) {
        $items = @($venueItems | Where-Object { [string]$_.venue -ceq $venueName })
        if ($items.Count -ne 1) {
            throw "$Label does not contain exactly one $venueName control."
        }
        $databaseVenue = $Database.venues.PSObject.Properties[$venueName].Value
        Assert-Properties -Value $items[0] -Expected @(
            'venue', 'enabled', 'mode', 'kill_switch', 'halted_reason',
            'live_confirmation_until'
        ) -Label "$Label.venues.$venueName"
        Assert-JsonStringProperties -Value $items[0] -Names @('venue', 'mode') `
            -NonEmpty -Label "$Label.venues.$venueName"
        if (
            $items[0].enabled -isnot [bool] -or
            $items[0].kill_switch -isnot [bool] -or
            [bool]$items[0].enabled -ne [bool]$databaseVenue.enabled -or
            [string]$items[0].mode -cne [string]$databaseVenue.mode -or
            [bool]$items[0].kill_switch -ne [bool]$databaseVenue.kill_switch -or
            -not (Test-ExactNullableString -Actual $items[0].halted_reason `
                -Expected $databaseVenue.halted_reason) -or
            -not (Test-ExactNullableString `
                -Actual $items[0].live_confirmation_until `
                -Expected $databaseVenue.live_confirmation_until)
        ) {
            throw "$Label HTTP/database $venueName controls diverged."
        }
    }
    $httpBinance = @($venueItems | Where-Object {
        [string]$_.venue -ceq 'binance_spot'
    })[0]
    if (
        -not (Test-TrueBoolean -Value $httpBinance.enabled) -or
        [string]$httpBinance.mode -cne 'backtest' -or
        -not (Test-TrueBoolean -Value $httpBinance.kill_switch) -or
        -not (Test-ExactNullableString -Actual $httpBinance.halted_reason `
            -Expected $BinanceBaseline.halted_reason) -or
        -not (Test-ExactNullableString `
            -Actual $httpBinance.live_confirmation_until `
            -Expected $BinanceBaseline.live_confirmation_until)
    ) {
        throw "$Label changed the sealed Binance baseline."
    }

    Assert-Properties -Value $SafetyHttp -Expected @('healthz', 'readyz') `
        -Label "$Label safety-control"
    Assert-HttpSample -Sample $SafetyHttp.healthz -ExpectedPath 'healthz' `
        -Label "$Label safety-control.healthz"
    Assert-HttpSample -Sample $SafetyHttp.readyz -ExpectedPath 'readyz' `
        -Label "$Label safety-control.readyz"
    Assert-Properties -Value $SafetyHttp.healthz.body -Expected @('status') `
        -Label "$Label safety-control.healthz.body"
    Assert-Properties -Value $SafetyHttp.readyz.body `
        -Expected @('status', 'database_boundary', 'reasons') `
        -Label "$Label safety-control.readyz.body"
    Assert-JsonStringProperties -Value $SafetyHttp.healthz.body `
        -Names @('status') -NonEmpty -Label "$Label safety-control.healthz.body"
    Assert-JsonStringProperties -Value $SafetyHttp.readyz.body `
        -Names @('status', 'database_boundary') -NonEmpty `
        -Label "$Label safety-control.readyz.body"
    Assert-JsonArrayProperties -Value $SafetyHttp.readyz.body -Names @('reasons') `
        -Label "$Label safety-control.readyz.body"
    if (
        [string]$SafetyHttp.healthz.body.status -cne 'ok' -or
        [string]$SafetyHttp.readyz.body.status -cne 'ready' -or
        [string]$SafetyHttp.readyz.body.database_boundary -cne 'exact' -or
        @($SafetyHttp.readyz.body.reasons).Count -ne 0
    ) {
        throw "$Label safety-control HTTP state is not ready and exact."
    }
    Assert-Properties -Value $Coherence `
        -Expected @('attempts', 'stable_api_expected', 'api_expected_sha256') `
        -Label "$Label API/database coherence"
    Assert-JsonIntegerProperties -Value $Coherence -Names @('attempts') `
        -NonNegative -Label "$Label API/database coherence"
    Assert-JsonStringProperties -Value $Coherence `
        -Names @('api_expected_sha256') -NonEmpty `
        -Label "$Label API/database coherence"
    if (
        $Coherence.attempts -isnot [int] -and
        $Coherence.attempts -isnot [long]
    ) {
        throw "$Label API/database coherence attempt count is not an integer."
    }
    if (
        [int]$Coherence.attempts -lt 1 -or
        [int]$Coherence.attempts -gt 2 -or
        -not (Test-TrueBoolean -Value $Coherence.stable_api_expected) -or
        [string]$Coherence.api_expected_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$Coherence.api_expected_sha256 -cne
            (Get-ApiExpectedSha256 -Database $Database)
    ) {
        throw "$Label API/database coherence is invalid."
    }
    Assert-ApiDatabaseCoherenceEvidence -Http $Http -Database $Database `
        -Coherence $Coherence -Label $Label
    return Get-ContinuityLaneSeal -Body $Http.continuity.body `
        -Label "$Label.continuity.body"
}

function Assert-RawOperationalSafetyEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][object] $WorkerLogs,
        [Parameter(Mandatory = $true)][object] $Resources,
        [Parameter(Mandatory = $true)][DateTimeOffset] $WorkerStartedAt,
        [Parameter(Mandatory = $true)][DateTimeOffset] $ExpectedLogSince,
        [Parameter(Mandatory = $true)][int] $OffsetMinutes,
        [switch] $Preflight,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Database `
        -Expected @(
            'database_now', 'latest_health_audit', 'scheduler', 'jobs', 'execution'
        ) `
        -Label "$Label database"
    $databaseNow = ConvertTo-UtcInstant -Value $Database.database_now `
        -Label "$Label database.database_now"
    Assert-Properties -Value $Database.latest_health_audit `
        -Expected @('ts', 'status', 'issues') `
        -Label "$Label database.latest_health_audit"
    Assert-JsonStringProperties -Value $Database.latest_health_audit `
        -Names @('status') -NonEmpty `
        -Label "$Label database.latest_health_audit"
    Assert-JsonArrayProperties -Value $Database.latest_health_audit `
        -Names @('issues') -Label "$Label database.latest_health_audit"
    $auditAt = ConvertTo-UtcInstant -Value $Database.latest_health_audit.ts `
        -Label "$Label database.latest_health_audit.ts"
    $healthSeverityRank = @{
        info = 0
        warning = 1
    }
    $strongestHealthStatus = 'healthy'
    $strongestHealthRank = -1
    foreach ($issue in @($Database.latest_health_audit.issues)) {
        Assert-Properties -Value $issue -Expected @('code', 'detail', 'severity') `
            -Label "$Label database.latest_health_audit issue"
        Assert-JsonStringProperties -Value $issue `
            -Names @('code', 'detail', 'severity') -NonEmpty `
            -Label "$Label database.latest_health_audit issue"
        if (-not $healthSeverityRank.ContainsKey([string]$issue.severity)) {
            throw "$Label contains an unknown or blocking health issue severity."
        }
        $issueRank = [int]$healthSeverityRank[[string]$issue.severity]
        if ($issueRank -gt $strongestHealthRank) {
            $strongestHealthRank = $issueRank
            $strongestHealthStatus = [string]$issue.severity
        }
    }
    if (
        $auditAt -le $WorkerStartedAt -or
        $auditAt -gt $databaseNow -or
        ($databaseNow - $auditAt).TotalMinutes -gt 20 -or
        [string]$Database.latest_health_audit.status -cne $strongestHealthStatus
    ) {
        throw "$Label contains a stale or blocking health audit."
    }

    Assert-Properties -Value $Database.scheduler -Expected @(
        'latest_maintenance_age_seconds', 'recent_maintenance_failures',
        'recent_legacy_runs'
    ) -Label "$Label database.scheduler"
    Assert-JsonIntegerProperties -Value $Database.scheduler -Names @(
        'recent_maintenance_failures', 'recent_legacy_runs'
    ) -NonNegative -Label "$Label database.scheduler"
    if (
        $null -eq $Database.scheduler.latest_maintenance_age_seconds -or
        -not (Test-JsonNumber `
            -Value $Database.scheduler.latest_maintenance_age_seconds) -or
        [double]$Database.scheduler.latest_maintenance_age_seconds -lt 0 -or
        [double]$Database.scheduler.latest_maintenance_age_seconds -gt 20 -or
        [int]$Database.scheduler.recent_maintenance_failures -ne 0 -or
        [int]$Database.scheduler.recent_legacy_runs -ne 0
    ) {
        throw "$Label contains a stale or divergent scheduler guard."
    }

    Assert-Properties -Value $Database.jobs -Expected @(
        'maintenance_runs', 'maintenance_success', 'maintenance_failures',
        'max_scheduled_gap_seconds', 'first_success_delay_seconds',
        'latest_maintenance_at', 'latest_maintenance_age_seconds', 'legacy_runs'
    ) -Label "$Label database.jobs"
    Assert-Properties -Value $WorkerLogs -Expected @(
        'since', 'tail_limit', 'nonempty_lines', 'timestamp_parse_failures',
        'possibly_truncated', 'latest_docker_timestamp', 'maintenance_rows',
        'rows', 'claim_identity_mismatches', 'phase_a_b_runs',
        'within_grace_deferred_runs', 'zero_leg_sealed_runs',
        'zero_mark_finalize_events', 'repeated_zero_mark_finalize_ids',
        'dangerous_log_lines'
    ) -Label "$Label worker_logs"
    Assert-JsonArrayProperties -Value $WorkerLogs -Names @(
        'rows', 'zero_mark_finalize_events', 'repeated_zero_mark_finalize_ids',
        'dangerous_log_lines'
    ) -Label "$Label worker_logs"
    $jobCountNames = @(
        'maintenance_runs', 'maintenance_success', 'maintenance_failures',
        'legacy_runs'
    )
    Assert-JsonIntegerProperties -Value $Database.jobs -Names $jobCountNames `
        -NonNegative -Label "$Label database.jobs"
    $workerLogCountNames = @(
        'tail_limit', 'nonempty_lines', 'timestamp_parse_failures',
        'maintenance_rows', 'claim_identity_mismatches', 'phase_a_b_runs',
        'within_grace_deferred_runs', 'zero_leg_sealed_runs'
    )
    Assert-JsonIntegerProperties -Value $WorkerLogs `
        -Names $workerLogCountNames -NonNegative -Label "$Label worker_logs"
    $minimumRuns = 0
    if ($Preflight) {
        $minimumRuns = 4
    }
    elseif ($OffsetMinutes -gt 0) {
        $minimumRuns = [int][Math]::Max(
            1,
            [Math]::Floor(($OffsetMinutes * 60) / 15) - 2
        )
    }
    foreach ($name in $jobCountNames) {
        if ([long]$Database.jobs.PSObject.Properties[$name].Value -lt 0) {
            throw "$Label contains a negative scheduler job count."
        }
    }
    if (
        [long]$Database.jobs.maintenance_runs -ne
            (
                [long]$Database.jobs.maintenance_success +
                [long]$Database.jobs.maintenance_failures
            )
    ) {
        throw "$Label scheduler run totals are internally inconsistent."
    }
    if ($null -ne $Database.jobs.latest_maintenance_at) {
        [void](ConvertTo-UtcInstant -Value $Database.jobs.latest_maintenance_at `
            -Label "$Label database.jobs.latest_maintenance_at")
    }
    foreach ($name in @(
        'latest_maintenance_age_seconds', 'max_scheduled_gap_seconds',
        'first_success_delay_seconds'
    )) {
        $value = $Database.jobs.PSObject.Properties[$name].Value
        if ($null -ne $value -and -not (Test-JsonNumber -Value $value)) {
            throw "$Label database.jobs.$name is not a finite JSON number."
        }
    }
    if (
        [int]$Database.jobs.maintenance_failures -ne 0 -or
        [int]$Database.jobs.legacy_runs -ne 0 -or
        $null -eq $Database.jobs.latest_maintenance_age_seconds -or
        [double]$Database.jobs.latest_maintenance_age_seconds -lt 0 -or
        [double]$Database.jobs.latest_maintenance_age_seconds -gt 20 -or
        (
            $null -ne $Database.jobs.max_scheduled_gap_seconds -and
            (
                [double]$Database.jobs.max_scheduled_gap_seconds -lt 0 -or
                [double]$Database.jobs.max_scheduled_gap_seconds -gt 20
            )
        ) -or
        [int]$Database.jobs.maintenance_success -lt $minimumRuns -or
        [int]$WorkerLogs.maintenance_rows -lt $minimumRuns -or
        (
            ($Preflight -or $OffsetMinutes -gt 0) -and
            (
                $null -eq $Database.jobs.first_success_delay_seconds -or
                [double]$Database.jobs.first_success_delay_seconds -lt 0 -or
                [double]$Database.jobs.first_success_delay_seconds -gt 20
            )
        )
    ) {
        throw "$Label contains missing, failed, legacy, or delayed scheduler jobs."
    }

    $logSince = ConvertTo-UtcInstant -Value $WorkerLogs.since `
        -Label "$Label worker_logs.since"
    if (
        $logSince -ne $ExpectedLogSince -or
        [int]$WorkerLogs.tail_limit -ne 5000 -or
        [int]$WorkerLogs.nonempty_lines -lt 0 -or
        [int]$WorkerLogs.maintenance_rows -lt 0 -or
        -not (Test-FalseBoolean -Value $WorkerLogs.possibly_truncated) -or
        [int]$WorkerLogs.timestamp_parse_failures -ne 0 -or
        [int]$WorkerLogs.claim_identity_mismatches -ne 0 -or
        @($WorkerLogs.dangerous_log_lines).Count -ne 0 -or
        @($WorkerLogs.repeated_zero_mark_finalize_ids).Count -ne 0
    ) {
        throw "$Label contains unsafe or incomplete worker logs."
    }
    if ($null -ne $WorkerLogs.latest_docker_timestamp) {
        $latestLogAt = ConvertTo-UtcInstant `
            -Value $WorkerLogs.latest_docker_timestamp `
            -Label "$Label worker_logs.latest_docker_timestamp"
        if ($latestLogAt -lt $ExpectedLogSince) {
            throw "$Label worker log timestamp predates its scan boundary."
        }
    }
    $logRows = @($WorkerLogs.rows)
    if (
        $logRows.Count -gt 250 -or
        [int]$WorkerLogs.maintenance_rows -lt $logRows.Count
    ) {
        throw "$Label worker log row inventory is inconsistent."
    }
    foreach ($row in $logRows) {
        Assert-Properties -Value $row -Expected @(
            'docker_timestamp', 'observed_at', 'mark_claimed', 'marks_observed',
            'marks_missing', 'mark_deferred', 'mark_ids', 'finalize_claimed',
            'finalized_sealed', 'finalize_deferred', 'finalize_ids',
            'claim_identity_valid'
        ) -Label "$Label worker log row"
        $claimCountNames = @(
            'mark_claimed', 'marks_observed', 'marks_missing', 'mark_deferred',
            'finalize_claimed', 'finalized_sealed', 'finalize_deferred'
        )
        Assert-JsonIntegerProperties -Value $row -Names $claimCountNames `
            -NonNegative -Label "$Label worker log row"
        Assert-JsonStringProperties -Value $row -Names @('observed_at') `
            -NonEmpty -Label "$Label worker log row"
        [void](ConvertTo-UtcInstant -Value $row.observed_at `
            -Label "$Label worker log row observed_at")
        Assert-JsonArrayProperties -Value $row -Names @('mark_ids', 'finalize_ids') `
            -Label "$Label worker log row"
        foreach ($identityListName in @('mark_ids', 'finalize_ids')) {
            $identityIds = [Collections.Generic.HashSet[long]]::new()
            foreach ($identityId in @(
                $row.PSObject.Properties[$identityListName].Value
            )) {
                if (
                    -not (Test-JsonInteger -Value $identityId) -or
                    [long]$identityId -le 0 -or
                    -not $identityIds.Add([long]$identityId)
                ) {
                    throw "$Label worker log row has invalid claim IDs."
                }
            }
        }
        if (
            -not (Test-TrueBoolean -Value $row.claim_identity_valid) -or
            [int]$row.mark_claimed -lt 0 -or
            [int]$row.finalize_claimed -lt 0 -or
            @($row.mark_ids).Count -ne [int]$row.mark_claimed -or
            @($row.finalize_ids).Count -ne [int]$row.finalize_claimed
        ) {
            throw "$Label contains an invalid worker claim-identity row."
        }
        if ($null -ne $row.docker_timestamp) {
            [void](ConvertTo-UtcInstant -Value $row.docker_timestamp `
                -Label "$Label worker log row docker_timestamp")
        }
    }
    $eventKeys = [Collections.Generic.HashSet[string]]::new()
    $eventObligations = [Collections.Generic.HashSet[long]]::new()
    foreach ($event in @($WorkerLogs.zero_mark_finalize_events)) {
        Assert-Properties -Value $event `
            -Expected @('obligation_id', 'docker_timestamp', 'event_key') `
            -Label "$Label zero-mark finalize event"
        Assert-JsonStringProperties -Value $event `
            -Names @('docker_timestamp', 'event_key') -NonEmpty `
            -Label "$Label zero-mark finalize event"
        $eventAt = ConvertTo-UtcInstant -Value $event.docker_timestamp `
            -Label "$Label zero-mark finalize event timestamp"
        Assert-JsonIntegerProperties -Value $event -Names @('obligation_id') `
            -NonNegative -Label "$Label zero-mark finalize event"
        $expectedKey = [string]$event.docker_timestamp + '|' +
            [string]$event.obligation_id
        if (
            [long]$event.obligation_id -le 0 -or
            $eventAt -lt $ExpectedLogSince -or
            [string]$event.event_key -cne $expectedKey -or
            -not $eventKeys.Add([string]$event.event_key) -or
            -not $eventObligations.Add([long]$event.obligation_id)
        ) {
            throw "$Label contains repeated or malformed finalizer evidence."
        }
    }

    $executionCountNames = @(
        'maker_intents', 'resting_orders', 'fill_legs', 'cost_seals',
        'mark_5m_observed', 'mark_5m_missing', 'mark_5m_unresolved',
        'mark_30m_observed', 'mark_30m_missing', 'mark_30m_unresolved',
        'waiting_for_marks', 'cohort_waiting_obligations',
        'global_waiting_obligations', 'cohort_post_grace_missing_marks',
        'global_post_grace_missing_marks', 'duplicate_marks', 'duplicate_seals',
        'finalizable_unsealed', 'seals_before_required_marks',
        'invalid_record_hashes', 'hash_rows_checked', 'enrollments', 'outcomes'
    )
    Assert-Properties -Value $Database.execution -Expected @(
        @($executionCountNames) + @('oldest_due_age_seconds', 'finalizable_unsealed_ids')
    ) -Label "$Label database.execution"
    Assert-JsonIntegerProperties -Value $Database.execution `
        -Names $executionCountNames -NonNegative `
        -Label "$Label database.execution"
    Assert-JsonArrayProperties -Value $Database.execution `
        -Names @('finalizable_unsealed_ids') `
        -Label "$Label database.execution"
    foreach ($name in $executionCountNames) {
        if ([long]$Database.execution.PSObject.Properties[$name].Value -lt 0) {
            throw "$Label contains a negative execution evidence count."
        }
    }
    foreach ($name in @(
        'global_post_grace_missing_marks', 'duplicate_marks', 'duplicate_seals',
        'finalizable_unsealed', 'seals_before_required_marks',
        'invalid_record_hashes'
    )) {
        if ([long]$Database.execution.PSObject.Properties[$name].Value -ne 0) {
            throw "$Label contains invalid execution evidence ($name)."
        }
    }
    if (
        (
            $null -ne $Database.execution.oldest_due_age_seconds -and
            -not (Test-JsonNumber `
                -Value $Database.execution.oldest_due_age_seconds)
        ) -or
        @($Database.execution.finalizable_unsealed_ids).Count -ne 0 -or
        (
            $null -ne $Database.execution.oldest_due_age_seconds -and
            [double]$Database.execution.oldest_due_age_seconds -lt 0
        )
    ) {
        throw "$Label contains inconsistent execution due evidence."
    }

    $resourceNames = @(
        'api_cpu_percent', 'api_memory_percent',
        'safety_cpu_percent', 'safety_memory_percent',
        'worker_cpu_percent', 'worker_memory_percent',
        'postgres_cpu_percent', 'postgres_memory_percent'
    )
    Assert-Properties -Value $Resources -Expected @(
        @('captured_at') + @($resourceNames)
    ) `
        -Label "$Label resources"
    $resourceCapturedAt = ConvertTo-UtcInstant -Value $Resources.captured_at `
        -Label "$Label resources.captured_at"
    if (-not $Preflight -and $resourceCapturedAt -gt $databaseNow) {
        throw "$Label resource capture is later than its database snapshot."
    }
    foreach ($name in $resourceNames) {
        $value = $Resources.PSObject.Properties[$name].Value
        if (-not (Test-JsonNumber -Value $value) -or [double]$value -lt 0) {
            throw "$Label contains a non-numeric or negative resource sample."
        }
    }
    if (
        [double]$Resources.api_memory_percent -ge 90 -or
        [double]$Resources.safety_memory_percent -ge 90 -or
        [double]$Resources.worker_memory_percent -ge 90 -or
        [double]$Resources.postgres_memory_percent -ge 90 -or
        [double]$Resources.postgres_cpu_percent -ge 200
    ) {
        throw "$Label exceeds a container resource budget."
    }
}

function Assert-SnapshotTimingEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Resources,
        [Parameter(Mandatory = $true)][object] $ResourceSampler,
        [Parameter(Mandatory = $true)][DateTimeOffset] $AnchorDatabaseAt,
        [Parameter(Mandatory = $true)][DateTimeOffset] $DatabaseNow,
        [Parameter(Mandatory = $true)][DateTimeOffset] $CapturedAt,
        [Parameter(Mandatory = $true)][double] $MonotonicSeconds,
        [Parameter(Mandatory = $true)][double] $TargetSeconds,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Resources -Expected @(
        'captured_at', 'scheduled_elapsed_seconds', 'actual_elapsed_seconds',
        'schedule_lag_seconds', 'completed_elapsed_seconds'
    ) -Label "$Label resources"
    Assert-Properties -Value $ResourceSampler -Expected @(
        'total_samples', 'expected_interval_seconds', 'rolling_window', 'violations'
    ) -Label "$Label resource_sampler"
    Assert-JsonArrayProperties -Value $ResourceSampler `
        -Names @('rolling_window', 'violations') -Label "$Label resource_sampler"
    Assert-JsonIntegerProperties -Value $ResourceSampler `
        -Names @('total_samples', 'expected_interval_seconds') -NonNegative `
        -Label "$Label resource_sampler"
    foreach ($name in @(
        'scheduled_elapsed_seconds', 'actual_elapsed_seconds',
        'schedule_lag_seconds', 'completed_elapsed_seconds'
    )) {
        if (-not (Test-JsonNumber -Value $Resources.PSObject.Properties[$name].Value)) {
            throw "$Label resources.$name is not numeric."
        }
    }
    $resourceCapturedAt = ConvertTo-UtcInstant -Value $Resources.captured_at `
        -Label "$Label resources.captured_at"
    $scheduled = [double]$Resources.scheduled_elapsed_seconds
    $actual = [double]$Resources.actual_elapsed_seconds
    $lag = [double]$Resources.schedule_lag_seconds
    $completed = [double]$Resources.completed_elapsed_seconds
    $expectedLag = [Math]::Max(0.0, $actual - $scheduled)
    $databaseElapsed = ($DatabaseNow - $AnchorDatabaseAt).TotalSeconds
    $capturedElapsed = ($CapturedAt - $AnchorDatabaseAt).TotalSeconds
    $resourceCapturedElapsed = (
        $resourceCapturedAt - $AnchorDatabaseAt
    ).TotalSeconds
    $maximumCompletionLag = 120.0
    if (
        [Math]::Abs($scheduled - $TargetSeconds) -gt 0.01 -or
        $actual -lt $TargetSeconds -or
        $actual -gt ($TargetSeconds + 2.5) -or
        $lag -lt 0 -or
        $lag -gt 2.5 -or
        [Math]::Abs($lag - $expectedLag) -gt 0.01 -or
        $completed -lt $actual -or
        $completed -gt ($actual + 15) -or
        $MonotonicSeconds -lt $completed -or
        $MonotonicSeconds -gt ($TargetSeconds + $maximumCompletionLag) -or
        $databaseElapsed -lt $TargetSeconds -or
        $databaseElapsed -gt ($TargetSeconds + $maximumCompletionLag) -or
        $capturedElapsed -lt $TargetSeconds -or
        $capturedElapsed -gt ($TargetSeconds + $maximumCompletionLag) -or
        $resourceCapturedElapsed -lt $TargetSeconds -or
        $resourceCapturedElapsed -gt ($TargetSeconds + 20) -or
        $resourceCapturedAt -gt $DatabaseNow -or
        $DatabaseNow -gt $CapturedAt -or
        [Math]::Abs($databaseElapsed - $MonotonicSeconds) -gt
            $maximumCompletionLag -or
        [Math]::Abs($capturedElapsed - $MonotonicSeconds) -gt
            $maximumCompletionLag
    ) {
        throw "$Label violates the raw resource-sampler milestone timing contract."
    }
    $expectedTotalSamples = [int][Math]::Floor($TargetSeconds / 5) + 1
    if (
        [int]$ResourceSampler.expected_interval_seconds -ne 5 -or
        [int]$ResourceSampler.total_samples -ne $expectedTotalSamples -or
        @($ResourceSampler.violations).Count -ne 0
    ) {
        throw "$Label resource sampler is incomplete or reports violations."
    }
    $window = @($ResourceSampler.rolling_window)
    $expectedWindowCount = [int][Math]::Min(7, $expectedTotalSamples)
    if ($window.Count -ne $expectedWindowCount) {
        throw "$Label resource sampler rolling window is incomplete."
    }
    $rollingResourceNames = @(
        'captured_at', 'api_cpu_percent', 'api_memory_percent',
        'safety_cpu_percent', 'safety_memory_percent',
        'worker_cpu_percent', 'worker_memory_percent',
        'postgres_cpu_percent', 'postgres_memory_percent',
        'scheduled_elapsed_seconds', 'actual_elapsed_seconds',
        'schedule_lag_seconds', 'completed_elapsed_seconds'
    )
    for ($index = 0; $index -lt $window.Count; $index++) {
        $sample = $window[$index]
        Assert-Properties -Value $sample -Expected $rollingResourceNames `
            -Label "$Label resource sampler sample $index"
        [void](ConvertTo-UtcInstant -Value $sample.captured_at `
            -Label "$Label resource sampler sample $index captured_at")
        Assert-JsonNumberProperties -Value $sample `
            -Names @($rollingResourceNames | Where-Object { $_ -cne 'captured_at' }) `
            -NonNegative -Label "$Label resource sampler sample $index"
        if (
            [double]$sample.api_memory_percent -ge 90 -or
            [double]$sample.safety_memory_percent -ge 90 -or
            [double]$sample.worker_memory_percent -ge 90 -or
            [double]$sample.postgres_memory_percent -ge 90 -or
            [double]$sample.postgres_cpu_percent -ge 200
        ) {
            throw "$Label resource sampler contains an unsafe raw sample."
        }
        if (
            $index -gt 0 -and
            [Math]::Abs(
                [double]$sample.scheduled_elapsed_seconds -
                [double]$window[$index - 1].scheduled_elapsed_seconds - 5
            ) -gt 0.01
        ) {
            throw "$Label resource sampler schedule is not a 5-second sequence."
        }
        if (
            $index -gt 0 -and
            [double]$sample.postgres_cpu_percent -ge 150 -and
            [double]$window[$index - 1].postgres_cpu_percent -ge 150
        ) {
            throw "$Label resource sampler has consecutive PostgreSQL CPU spikes."
        }
    }
    if ($window.Count -eq 7) {
        $postgresAverage = (
            $window | Measure-Object -Property postgres_cpu_percent -Average
        ).Average
        if ([double]$postgresAverage -ge 140) {
            throw "$Label resource sampler PostgreSQL CPU average is unsafe."
        }
    }
    $latest = $window[-1]
    foreach ($name in $rollingResourceNames) {
        $latestValue = $latest.PSObject.Properties[$name].Value
        $resourceValue = $Resources.PSObject.Properties[$name].Value
        if ($name -eq 'captured_at') {
            if ([string]$latestValue -cne [string]$resourceValue) {
                throw "$Label resource sampler latest sample does not match resources."
            }
        }
        elseif ([double]$latestValue -ne [double]$resourceValue) {
            throw "$Label resource sampler latest sample does not match resources."
        }
    }
}

function Assert-SnapshotDatabaseEvidence {
    param(
        [Parameter(Mandatory = $true)][object] $Database,
        [Parameter(Mandatory = $true)][object] $BinanceBaseline,
        [Parameter(Mandatory = $true)][long] $ExpectedLeaseRowId,
        [Parameter(Mandatory = $true)][DateTimeOffset] $ExpectedAcquiredAt,
        [Parameter(Mandatory = $true)][string] $ExpectedWatchdogStopRequestId,
        [Parameter(Mandatory = $true)][DateTimeOffset] $ExpectedResumeExecutedAt,
        [Parameter(Mandatory = $true)][string] $ExpectedNonceSha256,
        [Parameter(Mandatory = $true)][DateTimeOffset] $ExpectedEpochOpenedAt,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Database -Expected @(
        'database_now', 'schema_head', 'database_boundaries', 'resume', 'stop',
        'release_stop', 'canary_lease', 'active_canary_leases', 'active_epoch',
        'global_control', 'venues', 'pending_commands',
        'unexpected_pending_commands', 'unexpected_commands_since_boundary', 'live'
    ) -Label $Label
    $databaseNow = ConvertTo-UtcInstant -Value $Database.database_now `
        -Label "$Label.database_now"
    Assert-Properties -Value $Database.active_epoch `
        -Expected @('id', 'opened_at', 'cohort', 'image_digest') `
        -Label "$Label.active_epoch"
    Assert-Properties -Value $Database.database_boundaries `
        -Expected @('runtime_exact', 'safety_exact') `
        -Label "$Label.database_boundaries"
    Assert-JsonIntegerProperties -Value $Database.active_epoch -Names @('id') `
        -NonNegative -Label "$Label.active_epoch"
    Assert-JsonIntegerProperties -Value $Database -Names @(
        'pending_commands', 'unexpected_pending_commands',
        'unexpected_commands_since_boundary', 'active_canary_leases'
    ) -NonNegative -Label $Label
    Assert-JsonStringProperties -Value $Database -Names @('schema_head') `
        -NonEmpty -Label $Label
    Assert-JsonStringProperties -Value $Database.active_epoch `
        -Names @('opened_at', 'cohort', 'image_digest') -NonEmpty `
        -Label "$Label.active_epoch"
    $epochOpenedAt = ConvertTo-UtcInstant -Value $Database.active_epoch.opened_at `
        -Label "$Label.active_epoch.opened_at"
    if (
        [string]$Database.schema_head -cne $ExpectedSchemaHead -or
        [long]$Database.active_epoch.id -ne $EpochId -or
        [string]$Database.active_epoch.cohort -cne $Cohort -or
        [string]$Database.active_epoch.image_digest -cne $ReleaseDigest -or
        $epochOpenedAt -ne $ExpectedEpochOpenedAt -or
        -not (Test-TrueBoolean -Value $Database.database_boundaries.runtime_exact) -or
        -not (Test-TrueBoolean -Value $Database.database_boundaries.safety_exact)
    ) {
        throw "$Label changed the schema, epoch, cohort, image, or DB boundary."
    }

    Assert-Properties -Value $Database.global_control -Expected @(
        'mode', 'kill_switch', 'halted_reason', 'live_confirmation_until'
    ) -Label "$Label.global_control"
    Assert-JsonStringProperties -Value $Database.global_control -Names @('mode') `
        -NonEmpty -Label "$Label.global_control"
    Assert-Properties -Value $Database.venues `
        -Expected @('polymarket', 'binance_spot') -Label "$Label.venues"
    $polymarket = $Database.venues.polymarket
    $binance = $Database.venues.binance_spot
    foreach ($entry in @(
        [pscustomobject]@{ Value = $polymarket; Name = 'polymarket' },
        [pscustomobject]@{ Value = $binance; Name = 'binance_spot' }
    )) {
        Assert-Properties -Value $entry.Value -Expected @(
            'enabled', 'mode', 'kill_switch', 'halted_reason',
            'live_confirmation_until'
        ) -Label "$Label.venues.$($entry.Name)"
        Assert-JsonStringProperties -Value $entry.Value -Names @('mode') `
            -NonEmpty -Label "$Label.venues.$($entry.Name)"
    }
    if (
        [string]$Database.global_control.mode -cne 'paper' -or
        -not (Test-FalseBoolean -Value $Database.global_control.kill_switch) -or
        $null -ne $Database.global_control.halted_reason -or
        $null -ne $Database.global_control.live_confirmation_until -or
        -not (Test-TrueBoolean -Value $polymarket.enabled) -or
        [string]$polymarket.mode -cne 'paper' -or
        -not (Test-FalseBoolean -Value $polymarket.kill_switch) -or
        $null -ne $polymarket.halted_reason -or
        $null -ne $polymarket.live_confirmation_until -or
        -not (Test-TrueBoolean -Value $binance.enabled) -or
        [string]$binance.mode -cne 'backtest' -or
        -not (Test-TrueBoolean -Value $binance.kill_switch) -or
        -not (Test-ExactNullableString -Actual $binance.halted_reason `
            -Expected $BinanceBaseline.halted_reason) -or
        -not (Test-ExactNullableString -Actual $binance.live_confirmation_until `
            -Expected $BinanceBaseline.live_confirmation_until) -or
        [int]$Database.pending_commands -ne 0 -or
        [int]$Database.unexpected_pending_commands -ne 0 -or
        [int]$Database.unexpected_commands_since_boundary -ne 0 -or
        $null -ne $Database.stop -or
        $null -ne $Database.release_stop
    ) {
        throw "$Label is not the exact active PAPER-only control state."
    }
    foreach ($name in @(
        'orders', 'positions', 'intents', 'fill_legs', 'enrollments', 'outcomes'
    )) {
        Assert-JsonIntegerProperties -Value $Database.live -Names @($name) `
            -NonNegative -Label "$Label.live"
        if ([long]$Database.live.PSObject.Properties[$name].Value -ne 0) {
            throw "$Label contains unexpected LIVE $name."
        }
    }

    Assert-Properties -Value $Database.resume -Expected @(
        'request_id', 'command', 'payload', 'actor', 'reason', 'status',
        'created_at', 'executed_at'
    ) -Label "$Label.resume"
    Assert-JsonStringProperties -Value $Database.resume -Names @(
        'request_id', 'command', 'actor', 'reason', 'status',
        'created_at', 'executed_at'
    ) -NonEmpty -Label "$Label.resume"
    Assert-Properties -Value $Database.resume.payload -Expected @('venue') `
        -Label "$Label.resume.payload"
    Assert-JsonStringProperties -Value $Database.resume.payload -Names @('venue') `
        -NonEmpty -Label "$Label.resume.payload"
    $resumeCreatedAt = ConvertTo-UtcInstant -Value $Database.resume.created_at `
        -Label "$Label.resume.created_at"
    $resumeExecutedAt = ConvertTo-UtcInstant -Value $Database.resume.executed_at `
        -Label "$Label.resume.executed_at"
    if (
        [string]$Database.resume.request_id -cne $ResumeRequestId -or
        [string]$Database.resume.command -cne 'resume' -or
        [string]$Database.resume.actor -cne $ResumeActor -or
        [string]$Database.resume.reason -cne $ResumeReason -or
        [string]$Database.resume.status -cne 'succeeded' -or
        @($Database.resume.payload.PSObject.Properties).Count -ne 1 -or
        [string]$Database.resume.payload.venue -cne 'polymarket' -or
        $resumeCreatedAt -gt $resumeExecutedAt -or
        $resumeExecutedAt -ne $ExpectedResumeExecutedAt -or
        $resumeExecutedAt -gt $databaseNow
    ) {
        throw "$Label resume identity or execution anchor is invalid."
    }

    $lease = $Database.canary_lease
    Assert-LeaseIdentity -Lease $lease -ExpectedState 'active' `
        -Label "$Label active lease"
    Assert-Properties -Value $lease -Expected @(
        'id', 'watchdog_stop_request_id', 'acquired_at', 'heartbeat_at',
        'watchdog_at', 'expires_at', 'released_at', 'stop_command_id'
    ) -Label "$Label active lease"
    Assert-JsonIntegerProperties -Value $lease -Names @('id') -NonNegative `
        -Label "$Label active lease"
    $acquiredAt = ConvertTo-UtcInstant -Value $lease.acquired_at `
        -Label "$Label lease.acquired_at"
    $heartbeatAt = ConvertTo-UtcInstant -Value $lease.heartbeat_at `
        -Label "$Label lease.heartbeat_at"
    $watchdogAt = ConvertTo-UtcInstant -Value $lease.watchdog_at `
        -Label "$Label lease.watchdog_at"
    $expiresAt = ConvertTo-UtcInstant -Value $lease.expires_at `
        -Label "$Label lease.expires_at"
    if (
        [long]$lease.id -ne $ExpectedLeaseRowId -or
        [string]$lease.watchdog_stop_request_id -cne
            $ExpectedWatchdogStopRequestId -or
        [string]$lease.nonce_sha256 -cne $ExpectedNonceSha256 -or
        $acquiredAt -ne $ExpectedAcquiredAt -or
        [int]$Database.active_canary_leases -ne 1 -or
        $heartbeatAt -lt $acquiredAt -or
        $heartbeatAt -gt $databaseNow -or
        ($databaseNow - $heartbeatAt).TotalSeconds -gt 10 -or
        $watchdogAt -le $acquiredAt -or
        $watchdogAt -gt $databaseNow -or
        ($databaseNow - $watchdogAt).TotalSeconds -gt 7 -or
        $expiresAt -le $databaseNow -or
        $null -ne $lease.released_at -or
        $null -ne $lease.stop_command_id
    ) {
        throw "$Label active lease is not singular, fresh, exact, and unexpired."
    }
    return $databaseNow
}

function Assert-ExactEvidenceRecord {
    param(
        [Parameter(Mandatory = $true)][object] $Actual,
        [Parameter(Mandatory = $true)][object] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-ExactProperties -Value $Actual `
        -Expected @('path', 'bytes', 'sha256') -Label $Label
    Assert-JsonIntegerProperties -Value $Actual -Names @('bytes') -NonNegative `
        -Label $Label
    Assert-JsonStringProperties -Value $Actual -Names @('path', 'sha256') `
        -NonEmpty -Label $Label
    if (
        [string]$Actual.path -cne [string]$Expected.path -or
        [long]$Actual.bytes -ne [long]$Expected.bytes -or
        [string]$Actual.sha256 -cne [string]$Expected.sha256
    ) {
        throw "$Label does not match the exact artifact path, byte count, and hash."
    }
}

function Assert-ExecutionEvidenceSummary {
    param(
        [Parameter(Mandatory = $true)][object] $Summary,
        [Parameter(Mandatory = $true)][object] $Baseline,
        [Parameter(Mandatory = $true)][object] $Final,
        [Parameter(Mandatory = $true)][object] $FinalWorkerLogs,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $deltaNames = @(
        'maker_intents', 'fill_legs', 'cost_seals', 'mark_5m_observed',
        'mark_30m_observed', 'enrollments', 'outcomes'
    )
    $caseNames = @(
        'earliest_snapshot_observed', 'phase_a_b_observed',
        'within_grace_deferred_observed', 'natural_maker_intent_observed',
        'natural_fill_observed', 'natural_mark_observed',
        'natural_seal_observed', 'natural_enrollment_observed',
        'natural_outcome_observed'
    )
    Assert-ExactProperties -Value $Summary `
        -Expected @('runtime_gate', 'all_cases_observed', 'cases', 'deltas') `
        -Label $Label
    Assert-JsonStringProperties -Value $Summary -Names @('runtime_gate') `
        -NonEmpty -Label $Label
    Assert-ExactProperties -Value $Summary.cases -Expected $caseNames `
        -Label "$Label cases"
    Assert-ExactProperties -Value $Summary.deltas -Expected $deltaNames `
        -Label "$Label deltas"
    Assert-JsonIntegerProperties -Value $Summary.deltas -Names $deltaNames `
        -Label "$Label deltas"
    if ($Summary.all_cases_observed -isnot [bool]) {
        throw "$Label all_cases_observed is not a JSON boolean."
    }
    foreach ($caseName in $caseNames) {
        if ($Summary.cases.PSObject.Properties[$caseName].Value -isnot [bool]) {
            throw "$Label case $caseName is not a JSON boolean."
        }
    }
    $expectedDeltas = @{}
    foreach ($deltaName in $deltaNames) {
        $expectedDelta = (
            [long]$Final.PSObject.Properties[$deltaName].Value -
            [long]$Baseline.PSObject.Properties[$deltaName].Value
        )
        $expectedDeltas[$deltaName] = $expectedDelta
        if ([long]$Summary.deltas.PSObject.Properties[$deltaName].Value -ne $expectedDelta) {
            throw "$Label delta $deltaName does not match the snapshot chain."
        }
    }
    $expectedCases = @{
        earliest_snapshot_observed = $true
        phase_a_b_observed = [long]$FinalWorkerLogs.phase_a_b_runs -gt 0
        within_grace_deferred_observed = (
            [long]$FinalWorkerLogs.within_grace_deferred_runs -gt 0
        )
        natural_maker_intent_observed = [long]$expectedDeltas.maker_intents -gt 0
        natural_fill_observed = [long]$expectedDeltas.fill_legs -gt 0
        natural_mark_observed = (
            [long]$expectedDeltas.mark_5m_observed +
            [long]$expectedDeltas.mark_30m_observed
        ) -gt 0
        natural_seal_observed = [long]$expectedDeltas.cost_seals -gt 0
        natural_enrollment_observed = [long]$expectedDeltas.enrollments -gt 0
        natural_outcome_observed = [long]$expectedDeltas.outcomes -gt 0
    }
    $expectedAllCases = $true
    foreach ($caseName in $caseNames) {
        $actual = [bool]$Summary.cases.PSObject.Properties[$caseName].Value
        $expected = [bool]$expectedCases[$caseName]
        if ($actual -ne $expected) {
            throw "$Label case $caseName does not match the snapshot chain."
        }
        if (-not $expected) {
            $expectedAllCases = $false
        }
    }
    if (
        [string]$Summary.runtime_gate -cne 'informational_only' -or
        [bool]$Summary.all_cases_observed -ne $expectedAllCases
    ) {
        throw "$Label runtime gate or aggregate case result is invalid."
    }
}

function Assert-LeaseIdentity {
    param(
        [Parameter(Mandatory = $true)][object] $Lease,
        [Parameter(Mandatory = $true)][string] $ExpectedState,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-Properties -Value $Lease -Expected @(
        'lease_id', 'run_id', 'acquire_request_id', 'resume_request_id',
        'resume_actor', 'resume_reason', 'watchdog_stop_request_id',
        'direct_stop_request_id', 'release_request_id', 'nonce_sha256', 'state'
    ) -Label $Label
    Assert-JsonStringProperties -Value $Lease -Names @(
        'lease_id', 'run_id', 'acquire_request_id', 'resume_request_id',
        'resume_actor', 'resume_reason', 'watchdog_stop_request_id',
        'direct_stop_request_id', 'release_request_id', 'nonce_sha256', 'state'
    ) -NonEmpty -Label $Label
    if (
        [string]$Lease.lease_id -cne $LeaseId -or
        [string]$Lease.run_id -cne $RunId -or
        [string]$Lease.acquire_request_id -cne $LeaseAcquireRequestId -or
        [string]$Lease.resume_request_id -cne $ResumeRequestId -or
        [string]$Lease.resume_actor -cne $ResumeActor -or
        [string]$Lease.resume_reason -cne $ResumeReason -or
        [string]::IsNullOrWhiteSpace([string]$Lease.watchdog_stop_request_id) -or
        [string]$Lease.direct_stop_request_id -cne $DirectStopRequestId -or
        [string]$Lease.release_request_id -cne $LeaseReleaseRequestId -or
        [string]$Lease.nonce_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$Lease.state -cne $ExpectedState
    ) {
        throw "$Label has the wrong lease identity or state."
    }
}

function Assert-NoFailureEvidence {
    param([Parameter(Mandatory = $true)][string] $RunDirectory)

    foreach ($name in @('failure.json', 'divergence.json')) {
        $path = Join-Path $RunDirectory $name
        if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) {
            throw "Canary run contains $name."
        }
    }
}

$absoluteRoot = Get-NormalizedFullPath -Path $Root
if (-not [IO.Directory]::Exists($absoluteRoot)) {
    throw "Root directory does not exist: $absoluteRoot"
}
$runDirectory = Get-NormalizedFullPath -Path (
    Join-Path $absoluteRoot (Join-Path '.stratus-cache\paper-canary' $RunId)
)
if (-not [IO.Directory]::Exists($runDirectory)) {
    throw "PAPER canary run directory does not exist: $runDirectory"
}
[void](Assert-NoReparsePath -Path $runDirectory -Boundary $absoluteRoot)
Assert-NoFailureEvidence -RunDirectory $runDirectory

$preflightEvidence = Read-ImmutableEnvelope `
    -Path (Join-Path $runDirectory 'preflight.json') `
    -RunDirectory $runDirectory -Label 'preflight.json'
$leaseEvidence = Read-ImmutableEnvelope `
    -Path (Join-Path $runDirectory 'lease-acquire.json') `
    -RunDirectory $runDirectory -Label 'lease-acquire.json'
$readyEvidence = Read-ImmutableEnvelope `
    -Path (Join-Path $runDirectory 'ready.json') `
    -RunDirectory $runDirectory -Label 'ready.json'

$preflight = $preflightEvidence.payload
$leaseArtifact = $leaseEvidence.payload
$ready = $readyEvidence.payload
Assert-RunPayload -Payload $preflight -Label 'preflight.json payload'
Assert-RunPayload -Payload $leaseArtifact -Label 'lease-acquire.json payload'
Assert-RunPayload -Payload $ready -Label 'ready.json payload'
Assert-Properties -Value $preflight -Expected @(
    'captured_at', 'emergency_stop_armed', 'expected_resume', 'emergency_stop',
    'docker', 'database', 'http', 'api_database_coherence',
    'safety_http', 'safety'
) -Label 'preflight.json payload'
Assert-JsonStringProperties -Value $preflight -Names @('captured_at') `
    -NonEmpty -Label 'preflight.json payload'
$preflightCapturedAt = ConvertTo-UtcInstant -Value $preflight.captured_at `
    -Label 'preflight captured_at'
$preflightDatabaseAt = ConvertTo-UtcInstant `
    -Value $preflight.database.database_now -Label 'preflight database_now'
if (
    $preflightCapturedAt -lt $preflightDatabaseAt -or
    ($preflightCapturedAt - $preflightDatabaseAt).TotalSeconds -gt 120
) {
    throw 'Preflight capture time is not a bounded post-database observation.'
}
Assert-JsonStringProperties -Value $preflight.expected_resume `
    -Names @('request_id', 'actor', 'reason', 'command') -NonEmpty `
    -Label 'expected resume'
Assert-SafetyPassed -Safety $preflight.safety -Label 'preflight safety'
Assert-ExpectedContainerIdentity -Docker $preflight.docker -Label 'preflight docker'
Assert-ExpectedDatabaseIdentity -Database $preflight.database `
    -Label 'preflight database'
$expectedEpochOpenedAt = ConvertTo-UtcInstant `
    -Value $preflight.database.active_epoch.opened_at `
    -Label 'preflight active_epoch.opened_at'
Assert-ActiveDockerEvidence -Docker $preflight.docker `
    -Baseline $preflight.docker -Label 'preflight docker'
Assert-RuntimeDatabaseEvidence -Database $preflight.database `
    -Docker $preflight.docker -Label 'preflight database boundary'
$binanceBaseline = [pscustomobject]@{
    enabled = $preflight.database.venues.binance_spot.enabled
    mode = $preflight.database.venues.binance_spot.mode
    kill_switch = $preflight.database.venues.binance_spot.kill_switch
    halted_reason = $preflight.database.venues.binance_spot.halted_reason
    live_confirmation_until = (
        $preflight.database.venues.binance_spot.live_confirmation_until
    )
}
$baselineLaneSeal = Assert-PreflightHttpEvidence -Http $preflight.http `
    -SafetyHttp $preflight.safety_http `
    -Coherence $preflight.api_database_coherence `
    -Database $preflight.database -BinanceBaseline $binanceBaseline `
    -Label 'preflight HTTP'
$baselineStalledDays = [int](
    $preflight.http.continuity.body.collection_window.stalled_days
)
$workerStartedAt = ConvertTo-UtcInstant `
    -Value $preflight.docker.worker.started_at `
    -Label 'preflight worker started_at'
Assert-RawOperationalSafetyEvidence -Database $preflight.database `
    -WorkerLogs $preflight.worker_logs -Resources $preflight.resources `
    -WorkerStartedAt $workerStartedAt -ExpectedLogSince $workerStartedAt `
    -OffsetMinutes 0 -Preflight `
    -Label 'preflight raw safety'
Assert-Properties -Value $preflight.database -Expected @(
    'resume', 'stop', 'release_stop', 'unexpected_pending_commands',
    'unexpected_commands_since_boundary'
) -Label 'preflight database'
Assert-JsonIntegerProperties -Value $preflight.database -Names @(
    'unexpected_pending_commands', 'unexpected_commands_since_boundary'
) -NonNegative -Label 'preflight database'
if (
    $null -ne $preflight.database.resume -or
    $null -ne $preflight.database.stop -or
    $null -ne $preflight.database.release_stop -or
    [int]$preflight.database.unexpected_pending_commands -ne 0 -or
    [int]$preflight.database.unexpected_commands_since_boundary -ne 0
) {
    throw 'Preflight contains a reserved or unexpected operator command.'
}
Assert-Properties -Value $preflight.expected_resume `
    -Expected @('request_id', 'actor', 'reason', 'command', 'payload') `
    -Label 'expected resume'
Assert-Properties -Value $preflight.expected_resume.payload -Expected @('venue') `
    -Label 'expected resume payload'
Assert-JsonStringProperties -Value $preflight.expected_resume.payload `
    -Names @('venue') -NonEmpty -Label 'expected resume payload'
if (
    -not (Test-TrueBoolean -Value $preflight.emergency_stop_armed) -or
    [string]$preflight.expected_resume.request_id -cne $ResumeRequestId -or
    [string]$preflight.expected_resume.actor -cne $ResumeActor -or
    [string]$preflight.expected_resume.reason -cne $ResumeReason -or
    [string]$preflight.expected_resume.command -cne 'resume' -or
    @($preflight.expected_resume.payload.PSObject.Properties).Count -ne 1 -or
    [string]$preflight.expected_resume.payload.venue -cne 'polymarket'
) {
    throw 'Preflight expected-resume identity or emergency-stop arm is invalid.'
}
Assert-Properties -Value $preflight.emergency_stop `
    -Expected @('request_id', 'command', 'actor', 'reason', 'payload') `
    -Label 'preflight emergency stop'
Assert-JsonStringProperties -Value $preflight.emergency_stop -Names @(
    'request_id', 'command', 'actor', 'reason'
) -NonEmpty -Label 'preflight emergency stop'
Assert-Properties -Value $preflight.emergency_stop.payload -Expected @('venue') `
    -Label 'preflight emergency stop payload'
Assert-JsonStringProperties -Value $preflight.emergency_stop.payload `
    -Names @('venue') -NonEmpty -Label 'preflight emergency stop payload'
if (
    [string]$preflight.emergency_stop.request_id -cne $DirectStopRequestId -or
    [string]$preflight.emergency_stop.command -cne 'stop' -or
    [string]$preflight.emergency_stop.actor -cne 'codex-paper-canary' -or
    [string]$preflight.emergency_stop.reason -cne
        'STRATUS PAPER canary terminal fail-closed stop.' -or
    @($preflight.emergency_stop.payload.PSObject.Properties).Count -ne 1 -or
    [string]$preflight.emergency_stop.payload.venue -cne 'polymarket'
) {
    throw 'Preflight emergency-stop identity is invalid.'
}

Assert-Properties -Value $leaseArtifact -Expected @(
    'preflight_artifact_sha256', 'lease_id', 'acquire_request_id',
    'resume_request_id', 'direct_stop_request_id', 'nonce_sha256',
    'acquire', 'confirmed'
) -Label 'lease-acquire.json payload'
Assert-JsonStringProperties -Value $leaseArtifact -Names @(
    'preflight_artifact_sha256', 'lease_id', 'acquire_request_id',
    'resume_request_id', 'direct_stop_request_id', 'nonce_sha256'
) -NonEmpty -Label 'lease-acquire.json payload'
if (
    [string]$leaseArtifact.preflight_artifact_sha256 -cne
        [string]$preflightEvidence.record.sha256 -or
    [string]$leaseArtifact.lease_id -cne $LeaseId -or
    [string]$leaseArtifact.acquire_request_id -cne $LeaseAcquireRequestId -or
    [string]$leaseArtifact.resume_request_id -cne $ResumeRequestId -or
    [string]$leaseArtifact.direct_stop_request_id -cne $DirectStopRequestId -or
    [string]$leaseArtifact.nonce_sha256 -cnotmatch '^[0-9a-f]{64}$'
) {
    throw 'Lease-acquire artifact identity or preflight link is invalid.'
}
Assert-ExactProperties -Value $leaseArtifact.acquire `
    -Expected @('created', 'post_attempts', 'ambiguous_first_post', 'item') `
    -Label 'lease acquisition response'
Assert-JsonIntegerProperties -Value $leaseArtifact.acquire `
    -Names @('post_attempts') -NonNegative -Label 'lease acquisition response'
if (
    ($null -ne $leaseArtifact.acquire.created -and
        $leaseArtifact.acquire.created -isnot [bool]) -or
    $leaseArtifact.acquire.ambiguous_first_post -isnot [bool] -or
    [int]$leaseArtifact.acquire.post_attempts -lt 1 -or
    [int]$leaseArtifact.acquire.post_attempts -gt 2 -or
    (
        -not [bool]$leaseArtifact.acquire.ambiguous_first_post -and
        (
            -not (Test-TrueBoolean -Value $leaseArtifact.acquire.created) -or
            [int]$leaseArtifact.acquire.post_attempts -ne 1
        )
    ) -or
    (
        [bool]$leaseArtifact.acquire.ambiguous_first_post -and
        [int]$leaseArtifact.acquire.post_attempts -eq 1 -and
        $null -ne $leaseArtifact.acquire.created
    )
) {
    throw 'Lease acquisition response does not prove a bounded exact POST.'
}
Assert-LeaseIdentity -Lease $leaseArtifact.acquire.item `
    -ExpectedState 'active' -Label 'lease acquisition response item'
Assert-Properties -Value $leaseArtifact.acquire.item `
    -Expected @('id', 'acquired_at') -Label 'lease acquisition response item'
Assert-JsonIntegerProperties -Value $leaseArtifact.acquire.item -Names @('id') `
    -NonNegative -Label 'lease acquisition response item'
$acquireItemAt = ConvertTo-UtcInstant `
    -Value $leaseArtifact.acquire.item.acquired_at `
    -Label 'lease acquisition response item acquired_at'

Assert-ExactProperties -Value $leaseArtifact.confirmed -Expected @(
    'database_now', 'lease', 'stop_command', 'active_leases', 'global_control',
    'polymarket', 'binance', 'pending_commands',
    'unexpected_commands_since_boundary', 'live_rows'
) -Label 'confirmed lease state'
Assert-LeaseIdentity -Lease $leaseArtifact.confirmed.lease `
    -ExpectedState 'active' -Label 'confirmed active lease'
Assert-Properties -Value $leaseArtifact.confirmed.lease -Expected @(
    'id', 'acquired_at', 'watchdog_at', 'heartbeat_at', 'expires_at',
    'released_at', 'stop_command_id'
) -Label 'confirmed active lease'
Assert-JsonIntegerProperties -Value $leaseArtifact.confirmed.lease `
    -Names @('id') -NonNegative -Label 'confirmed active lease'
Assert-JsonIntegerProperties -Value $leaseArtifact.confirmed `
    -Names @(
        'active_leases', 'pending_commands',
        'unexpected_commands_since_boundary', 'live_rows'
    ) -NonNegative -Label 'confirmed lease state'
$confirmedDatabaseAt = ConvertTo-UtcInstant `
    -Value $leaseArtifact.confirmed.database_now `
    -Label 'confirmed lease database_now'
$expectedLeaseRowId = [long]$leaseArtifact.confirmed.lease.id
$expectedLeaseAcquiredAt = ConvertTo-UtcInstant `
    -Value $leaseArtifact.confirmed.lease.acquired_at `
    -Label 'confirmed active lease acquired_at'
$confirmedLeaseHeartbeatAt = ConvertTo-UtcInstant `
    -Value $leaseArtifact.confirmed.lease.heartbeat_at `
    -Label 'confirmed active lease heartbeat_at'
$confirmedLeaseWatchdogAt = ConvertTo-UtcInstant `
    -Value $leaseArtifact.confirmed.lease.watchdog_at `
    -Label 'confirmed active lease watchdog_at'
$confirmedLeaseExpiresAt = ConvertTo-UtcInstant `
    -Value $leaseArtifact.confirmed.lease.expires_at `
    -Label 'confirmed active lease expires_at'
$expectedWatchdogStopRequestId = [string](
    $leaseArtifact.confirmed.lease.watchdog_stop_request_id
)
foreach ($controlName in @('global_control', 'polymarket', 'binance')) {
    $confirmedControl = $leaseArtifact.confirmed.PSObject.Properties[$controlName].Value
    $confirmedControlProperties = @(
        'mode', 'kill_switch', 'halted_reason', 'live_confirmation_until'
    )
    if ($controlName -ne 'global_control') {
        $confirmedControlProperties = @('enabled') + $confirmedControlProperties
    }
    Assert-ExactProperties -Value $confirmedControl `
        -Expected $confirmedControlProperties `
        -Label "confirmed lease $controlName control"
    Assert-JsonStringProperties -Value $confirmedControl -Names @('mode') `
        -NonEmpty -Label "confirmed lease $controlName control"
}
$confirmedGlobal = $leaseArtifact.confirmed.global_control
$confirmedPolymarket = $leaseArtifact.confirmed.polymarket
$confirmedBinance = $leaseArtifact.confirmed.binance
if (
    $expectedLeaseRowId -le 0 -or
    [long]$leaseArtifact.acquire.item.id -ne $expectedLeaseRowId -or
    $acquireItemAt -ne $expectedLeaseAcquiredAt -or
    [int]$leaseArtifact.confirmed.active_leases -ne 1 -or
    [int]$leaseArtifact.confirmed.pending_commands -ne 0 -or
    [int]$leaseArtifact.confirmed.unexpected_commands_since_boundary -ne 0 -or
    [int]$leaseArtifact.confirmed.live_rows -ne 0 -or
    [string]$leaseArtifact.confirmed.lease.nonce_sha256 -cne
        [string]$leaseArtifact.nonce_sha256 -or
    $confirmedDatabaseAt -lt $expectedLeaseAcquiredAt -or
    $confirmedLeaseHeartbeatAt -lt $expectedLeaseAcquiredAt -or
    $confirmedLeaseHeartbeatAt -gt $confirmedDatabaseAt -or
    ($confirmedDatabaseAt - $confirmedLeaseHeartbeatAt).TotalSeconds -gt 10 -or
    $confirmedLeaseWatchdogAt -le $expectedLeaseAcquiredAt -or
    $confirmedLeaseWatchdogAt -gt $confirmedDatabaseAt -or
    ($confirmedDatabaseAt - $confirmedLeaseWatchdogAt).TotalSeconds -gt 7 -or
    $confirmedLeaseExpiresAt -le $confirmedDatabaseAt -or
    $null -ne $leaseArtifact.confirmed.lease.released_at -or
    $null -ne $leaseArtifact.confirmed.lease.stop_command_id -or
    $null -ne $leaseArtifact.confirmed.stop_command -or
    [string]$confirmedGlobal.mode -cne 'paper' -or
    -not (Test-TrueBoolean -Value $confirmedGlobal.kill_switch) -or
    -not (Test-ExactNullableString -Actual $confirmedGlobal.halted_reason `
        -Expected $preflight.database.global_control.halted_reason) -or
    -not (Test-ExactNullableString -Actual $confirmedGlobal.live_confirmation_until `
        -Expected $preflight.database.global_control.live_confirmation_until) -or
    -not (Test-TrueBoolean -Value $confirmedPolymarket.enabled) -or
    [string]$confirmedPolymarket.mode -cne 'paper' -or
    -not (Test-TrueBoolean -Value $confirmedPolymarket.kill_switch) -or
    -not (Test-ExactNullableString -Actual $confirmedPolymarket.halted_reason `
        -Expected $preflight.database.venues.polymarket.halted_reason) -or
    -not (Test-ExactNullableString `
        -Actual $confirmedPolymarket.live_confirmation_until `
        -Expected $preflight.database.venues.polymarket.live_confirmation_until) -or
    -not (Test-TrueBoolean -Value $confirmedBinance.enabled) -or
    [string]$confirmedBinance.mode -cne 'backtest' -or
    -not (Test-TrueBoolean -Value $confirmedBinance.kill_switch) -or
    -not (Test-ExactNullableString -Actual $confirmedBinance.halted_reason `
        -Expected $binanceBaseline.halted_reason) -or
    -not (Test-ExactNullableString `
        -Actual $confirmedBinance.live_confirmation_until `
        -Expected $binanceBaseline.live_confirmation_until)
) {
    throw 'Lease-acquire artifact does not prove one matching active lease.'
}

Assert-Properties -Value $ready -Expected @(
    'ready_at', 'preflight_database_at', 'command_boundary_at',
    'preflight_artifact_sha256', 'preflight_safe',
    'expected_resume_request_id', 'emergency_stop_request_id',
    'emergency_stop_payload', 'emergency_stop_armed', 'lease', 'offsets'
) -Label 'ready.json payload'
Assert-JsonArrayProperties -Value $ready -Names @('offsets') `
    -Label 'ready.json payload'
Assert-JsonStringProperties -Value $ready -Names @(
    'ready_at', 'preflight_database_at', 'command_boundary_at',
    'preflight_artifact_sha256', 'expected_resume_request_id',
    'emergency_stop_request_id'
) -NonEmpty -Label 'ready.json payload'
$readyAt = ConvertTo-UtcInstant -Value $ready.ready_at -Label 'ready ready_at'
$readyPreflightDatabaseAt = ConvertTo-UtcInstant `
    -Value $ready.preflight_database_at -Label 'ready preflight_database_at'
$readyCommandBoundaryAt = ConvertTo-UtcInstant `
    -Value $ready.command_boundary_at -Label 'ready command_boundary_at'
if (
    $readyPreflightDatabaseAt -ne $preflightDatabaseAt -or
    [string]$ready.preflight_database_at -cne
        [string]$preflight.database.database_now -or
    $readyCommandBoundaryAt -gt $preflightDatabaseAt -or
    $readyAt -lt $preflightCapturedAt -or
    $readyAt -lt $confirmedDatabaseAt
) {
    throw 'Ready timing identity does not bind the preflight and lease boundary.'
}
Assert-ExactOffsets -Value @($ready.offsets) -Label 'ready offsets'
Assert-Properties -Value $ready.lease -Expected @(
    'lease_id', 'acquire_request_id', 'direct_stop_request_id',
    'release_request_id', 'nonce_sha256', 'artifact_sha256', 'state'
) -Label 'ready lease'
Assert-JsonStringProperties -Value $ready.lease -Names @(
    'lease_id', 'acquire_request_id', 'direct_stop_request_id',
    'release_request_id', 'nonce_sha256', 'artifact_sha256', 'state'
) -NonEmpty -Label 'ready lease'
Assert-Properties -Value $ready.emergency_stop_payload -Expected @('venue') `
    -Label 'ready emergency stop payload'
Assert-JsonStringProperties -Value $ready.emergency_stop_payload -Names @('venue') `
    -NonEmpty -Label 'ready emergency stop payload'
if (
    [string]$ready.preflight_artifact_sha256 -cne
        [string]$preflightEvidence.record.sha256 -or
    -not (Test-TrueBoolean -Value $ready.preflight_safe) -or
    [string]$ready.expected_resume_request_id -cne $ResumeRequestId -or
    [string]$ready.emergency_stop_request_id -cne $DirectStopRequestId -or
    @($ready.emergency_stop_payload.PSObject.Properties).Count -ne 1 -or
    [string]$ready.emergency_stop_payload.venue -cne 'polymarket' -or
    -not (Test-TrueBoolean -Value $ready.emergency_stop_armed) -or
    [string]$ready.lease.lease_id -cne $LeaseId -or
    [string]$ready.lease.acquire_request_id -cne $LeaseAcquireRequestId -or
    [string]$ready.lease.direct_stop_request_id -cne $DirectStopRequestId -or
    [string]$ready.lease.release_request_id -cne $LeaseReleaseRequestId -or
    [string]$ready.lease.nonce_sha256 -cne [string]$leaseArtifact.nonce_sha256 -or
    [string]$ready.lease.artifact_sha256 -cne
        [string]$leaseEvidence.record.sha256 -or
    [string]$ready.lease.state -cne 'active'
) {
    throw 'Ready artifact identity, safety, or evidence links are invalid.'
}

if ($Mode -eq 'Ready') {
    Assert-NoFailureEvidence -RunDirectory $runDirectory
    [ordered]@{
        schema_version = 1
        validator = 'stratus_paper_canary_artifacts'
        mode = 'Ready'
        verified = $true
        validated = $true
        canary_run_id = $RunId
        expected_resume_request_id = $ResumeRequestId
        lease_id = $LeaseId
        direct_stop_request_id = $DirectStopRequestId
        release_request_id = $LeaseReleaseRequestId
        preflight_sha256 = $preflightEvidence.record.sha256
        lease_acquire_sha256 = $leaseEvidence.record.sha256
        ready_sha256 = $readyEvidence.record.sha256
        offsets = @($ExpectedOffsets)
        binance_baseline = [ordered]@{
            enabled = [bool]$binanceBaseline.enabled
            mode = [string]$binanceBaseline.mode
            kill_switch = [bool]$binanceBaseline.kill_switch
            halted_reason = $binanceBaseline.halted_reason
            live_confirmation_until = $binanceBaseline.live_confirmation_until
        }
        failure_evidence_absent = $true
    } | ConvertTo-Json -Depth 4 -Compress
    return
}

$snapshotEvidence = [Collections.Generic.List[object]]::new()
$previousHash = [string]$readyEvidence.record.sha256
$anchorDatabaseInstant = $null
$anchorExecutedInstant = $null
$anchorDatabaseText = $null
$anchorExecutedText = $null
$previousCapturedAt = $null
$previousDatabaseNow = $null
$previousMonotonicSeconds = $null
$lastCapturedAt = $null
$lastDatabaseNow = $null
$lastMonotonicSeconds = $null
$expectedSnapshotNames = @(
    $ExpectedOffsets | ForEach-Object { 'snapshot-{0:D3}m.json' -f $_ }
)
$actualSnapshotNames = @(
    Get-ChildItem -LiteralPath $runDirectory -Force -File |
        Where-Object { $_.Name -like 'snapshot-*.json' } |
        ForEach-Object Name |
        Sort-Object
)
if (
    $actualSnapshotNames.Count -ne $expectedSnapshotNames.Count -or
    ($actualSnapshotNames -join "`n") -cne
        (($expectedSnapshotNames | Sort-Object) -join "`n")
) {
    throw 'Terminal canary evidence does not contain exactly six release snapshots.'
}
for ($sequence = 0; $sequence -lt $ExpectedOffsets.Count; $sequence++) {
    $offset = $ExpectedOffsets[$sequence]
    $fileName = 'snapshot-{0:D3}m.json' -f $offset
    $evidence = Read-ImmutableEnvelope `
        -Path (Join-Path $runDirectory $fileName) `
        -RunDirectory $runDirectory -Label $fileName
    $snapshot = $evidence.payload
    Assert-RunPayload -Payload $snapshot -Label "$fileName payload"
    Assert-Properties -Value $snapshot -Expected @(
        'sequence', 'offset_minutes', 'captured_at', 'anchor',
        'expected_identity', 'previous_snapshot_sha256', 'docker', 'database',
        'resources', 'resource_sampler', 'worker_logs', 'http',
        'api_database_coherence', 'safety_http', 'safety'
    ) -Label "$fileName payload"
    Assert-Properties -Value $snapshot.anchor -Expected @(
        'resume_request_id', 'resume_executed_at',
        'first_observed_database_at', 'monotonic_elapsed_seconds'
    ) `
        -Label "$fileName anchor"
    Assert-JsonStringProperties -Value $snapshot `
        -Names @('captured_at', 'previous_snapshot_sha256') -NonEmpty `
        -Label "$fileName payload"
    Assert-JsonStringProperties -Value $snapshot.anchor -Names @(
        'resume_request_id', 'resume_executed_at', 'first_observed_database_at'
    ) -NonEmpty -Label "$fileName anchor"
    Assert-JsonNumberProperties -Value $snapshot.anchor `
        -Names @('monotonic_elapsed_seconds') -NonNegative `
        -Label "$fileName anchor"
    Assert-JsonIntegerProperties -Value $snapshot `
        -Names @('sequence', 'offset_minutes') -NonNegative `
        -Label "$fileName payload"
    if (
        [int]$snapshot.sequence -ne $sequence -or
        [int]$snapshot.offset_minutes -ne $offset -or
        [string]$snapshot.anchor.resume_request_id -cne $ResumeRequestId -or
        [string]$snapshot.previous_snapshot_sha256 -cne $previousHash
    ) {
        throw "$fileName sequence, anchor, or previous-hash link is invalid."
    }
    Assert-ExpectedSnapshotIdentity -Identity $snapshot.expected_identity `
        -Label "$fileName expected identity"
    Assert-SafetyPassed -Safety $snapshot.safety -Label "$fileName safety"

    $capturedAt = ConvertTo-UtcInstant -Value $snapshot.captured_at `
        -Label "$fileName captured_at"
    $snapshotAnchorDatabase = ConvertTo-UtcInstant `
        -Value $snapshot.anchor.first_observed_database_at `
        -Label "$fileName anchor first_observed_database_at"
    $snapshotAnchorExecuted = ConvertTo-UtcInstant `
        -Value $snapshot.anchor.resume_executed_at `
        -Label "$fileName anchor resume_executed_at"
    if (-not (Test-JsonNumber -Value $snapshot.anchor.monotonic_elapsed_seconds)) {
        throw "$fileName anchor monotonic_elapsed_seconds is not numeric."
    }
    $monotonicSeconds = [double]$snapshot.anchor.monotonic_elapsed_seconds
    if ($sequence -eq 0) {
        $anchorDatabaseInstant = $snapshotAnchorDatabase
        $anchorExecutedInstant = $snapshotAnchorExecuted
        $anchorDatabaseText = [string]$snapshot.anchor.first_observed_database_at
        $anchorExecutedText = [string]$snapshot.anchor.resume_executed_at
        if ($anchorExecutedInstant -gt $anchorDatabaseInstant) {
            throw 'The resume execution is later than its first DB observation.'
        }
    }
    elseif (
        $snapshotAnchorDatabase -ne $anchorDatabaseInstant -or
        $snapshotAnchorExecuted -ne $anchorExecutedInstant -or
        [string]$snapshot.anchor.first_observed_database_at -cne
            $anchorDatabaseText -or
        [string]$snapshot.anchor.resume_executed_at -cne $anchorExecutedText
    ) {
        throw "$fileName changed the canary database or resume anchor."
    }

    Assert-ActiveDockerEvidence -Docker $snapshot.docker `
        -Baseline $preflight.docker -Label "$fileName docker"
    Assert-RuntimeDatabaseEvidence -Database $snapshot.database `
        -Docker $snapshot.docker -Label "$fileName database boundary"
    $databaseNow = Assert-SnapshotDatabaseEvidence `
        -Database $snapshot.database -BinanceBaseline $binanceBaseline `
        -ExpectedLeaseRowId $expectedLeaseRowId `
        -ExpectedAcquiredAt $expectedLeaseAcquiredAt `
        -ExpectedWatchdogStopRequestId $expectedWatchdogStopRequestId `
        -ExpectedResumeExecutedAt $anchorExecutedInstant `
        -ExpectedNonceSha256 ([string]$leaseArtifact.nonce_sha256) `
        -ExpectedEpochOpenedAt $expectedEpochOpenedAt `
        -Label "$fileName database"
    $laneSeal = Assert-SnapshotHttpEvidence -Http $snapshot.http `
        -SafetyHttp $snapshot.safety_http `
        -Coherence $snapshot.api_database_coherence `
        -Database $snapshot.database -BinanceBaseline $binanceBaseline `
        -BaselineStalledDays $baselineStalledDays -Label "$fileName HTTP"
    if ([string]$laneSeal -cne [string]$baselineLaneSeal) {
        throw "$fileName changed the exact six continuity-lane identities."
    }

    $targetSeconds = [double]($offset * 60)
    Assert-RawOperationalSafetyEvidence -Database $snapshot.database `
        -WorkerLogs $snapshot.worker_logs -Resources $snapshot.resources `
        -WorkerStartedAt $workerStartedAt `
        -ExpectedLogSince $anchorDatabaseInstant -OffsetMinutes $offset `
        -Label "$fileName raw safety"
    Assert-SnapshotTimingEvidence -Resources $snapshot.resources `
        -ResourceSampler $snapshot.resource_sampler `
        -AnchorDatabaseAt $anchorDatabaseInstant -DatabaseNow $databaseNow `
        -CapturedAt $capturedAt -MonotonicSeconds $monotonicSeconds `
        -TargetSeconds $targetSeconds -Label $fileName
    $databaseElapsed = ($databaseNow - $anchorDatabaseInstant).TotalSeconds
    $capturedElapsed = ($capturedAt - $anchorDatabaseInstant).TotalSeconds
    if (
        $monotonicSeconds -lt $targetSeconds -or
        $databaseElapsed -lt $targetSeconds -or
        $capturedElapsed -lt $targetSeconds -or
        $capturedAt -lt $databaseNow -or
        (
            $null -ne $previousMonotonicSeconds -and
            $monotonicSeconds -lt [double]$previousMonotonicSeconds
        ) -or
        ($null -ne $previousDatabaseNow -and $databaseNow -lt $previousDatabaseNow) -or
        ($null -ne $previousCapturedAt -and $capturedAt -lt $previousCapturedAt)
    ) {
        throw "$fileName violates the DB-clock/monotonic milestone timing contract."
    }
    $snapshotEvidence.Add($evidence)
    $previousHash = [string]$evidence.record.sha256
    $previousMonotonicSeconds = $monotonicSeconds
    $previousDatabaseNow = $databaseNow
    $previousCapturedAt = $capturedAt
    $lastMonotonicSeconds = $monotonicSeconds
    $lastDatabaseNow = $databaseNow
    $lastCapturedAt = $capturedAt
}
if (
    $null -eq $anchorDatabaseInstant -or
    $null -eq $anchorExecutedInstant -or
    $null -eq $lastDatabaseNow -or
    $null -eq $lastCapturedAt -or
    [double]$lastMonotonicSeconds -lt 3600 -or
    ($lastDatabaseNow - $anchorDatabaseInstant).TotalSeconds -lt 3600 -or
    ($lastCapturedAt - $anchorDatabaseInstant).TotalSeconds -lt 3600
) {
    throw 'Terminal evidence does not prove the full 60-minute canary window.'
}

$manifestEvidence = Read-ImmutableEnvelope `
    -Path (Join-Path $runDirectory 'manifest.json') `
    -RunDirectory $runDirectory -Label 'manifest.json'
$manifest = $manifestEvidence.payload
Assert-RunPayload -Payload $manifest -Label 'manifest.json payload'
Assert-Properties -Value $manifest -Expected @(
    'passed', 'runtime_canary_passed', 'expected_resume_request_id',
    'anchor_database_time', 'anchor_executed_at', 'completed_at', 'offsets',
    'evidence_chain', 'snapshots', 'final_snapshot_sha256', 'execution_evidence',
    'emergency_stop_submitted', 'emergency_stop', 'safety_lease_released',
    'safety_lease_id', 'safety_lease_release_request_id', 'terminal_runtime'
) -Label 'manifest.json payload'
Assert-JsonArrayProperties -Value $manifest -Names @('offsets', 'snapshots') `
    -Label 'manifest.json payload'
Assert-JsonStringProperties -Value $manifest -Names @(
    'expected_resume_request_id', 'anchor_database_time', 'anchor_executed_at',
    'completed_at', 'final_snapshot_sha256', 'safety_lease_id',
    'safety_lease_release_request_id', 'terminal_runtime'
) -NonEmpty -Label 'manifest.json payload'
Assert-ExactOffsets -Value @($manifest.offsets) -Label 'manifest offsets'
$manifestAnchorDatabase = ConvertTo-UtcInstant `
    -Value $manifest.anchor_database_time `
    -Label 'manifest anchor_database_time'
$manifestAnchorExecuted = ConvertTo-UtcInstant `
    -Value $manifest.anchor_executed_at `
    -Label 'manifest anchor_executed_at'
$manifestCompletedAt = ConvertTo-UtcInstant -Value $manifest.completed_at `
    -Label 'manifest completed_at'
if (
    -not (Test-TrueBoolean -Value $manifest.passed) -or
    -not (Test-TrueBoolean -Value $manifest.runtime_canary_passed) -or
    [string]$manifest.expected_resume_request_id -cne $ResumeRequestId -or
    -not (Test-TrueBoolean -Value $manifest.emergency_stop_submitted) -or
    -not (Test-TrueBoolean -Value $manifest.safety_lease_released) -or
    [string]$manifest.safety_lease_id -cne $LeaseId -or
    [string]$manifest.safety_lease_release_request_id -cne
        $LeaseReleaseRequestId -or
    [string]$manifest.terminal_runtime -cne 'polymarket_paper_fail_closed' -or
    $manifestAnchorDatabase -ne $anchorDatabaseInstant -or
    $manifestAnchorExecuted -ne $anchorExecutedInstant -or
    [string]$manifest.anchor_database_time -cne $anchorDatabaseText -or
    [string]$manifest.anchor_executed_at -cne $anchorExecutedText -or
    $manifestCompletedAt -lt $lastCapturedAt
) {
    throw 'Manifest pass flags, resume identity, or terminal runtime are invalid.'
}
Assert-ExactProperties -Value $manifest.evidence_chain `
    -Expected @('preflight', 'lease_acquire', 'ready') `
    -Label 'manifest evidence chain'
Assert-ExactEvidenceRecord -Actual $manifest.evidence_chain.preflight `
    -Expected $preflightEvidence.record -Label 'manifest preflight evidence'
Assert-ExactEvidenceRecord -Actual $manifest.evidence_chain.lease_acquire `
    -Expected $leaseEvidence.record -Label 'manifest lease-acquire evidence'
Assert-ExactEvidenceRecord -Actual $manifest.evidence_chain.ready `
    -Expected $readyEvidence.record -Label 'manifest ready evidence'

$manifestSnapshots = @($manifest.snapshots)
if ($manifestSnapshots.Count -ne $ExpectedOffsets.Count) {
    throw 'Manifest does not link exactly six snapshot artifacts.'
}
for ($sequence = 0; $sequence -lt $ExpectedOffsets.Count; $sequence++) {
    Assert-ExactEvidenceRecord -Actual $manifestSnapshots[$sequence] `
        -Expected $snapshotEvidence[$sequence].record `
        -Label "manifest snapshot $sequence evidence"
}
if ([string]$manifest.final_snapshot_sha256 -cne $previousHash) {
    throw 'Manifest final snapshot hash does not close the snapshot chain.'
}
Assert-ExecutionEvidenceSummary -Summary $manifest.execution_evidence `
    -Baseline $snapshotEvidence[0].payload.database.execution `
    -Final $snapshotEvidence[$snapshotEvidence.Count - 1].payload.database.execution `
    -FinalWorkerLogs $snapshotEvidence[$snapshotEvidence.Count - 1].payload.worker_logs `
    -Label 'manifest execution evidence'

$release = $manifest.emergency_stop
Assert-Properties -Value $release -Expected @(
    'release_request_id', 'created', 'post_attempts', 'ambiguous_first_post',
    'status', 'controls_confirmed', 'confirmed_at', 'lease', 'stop_command'
) -Label 'manifest released lease'
Assert-JsonStringProperties -Value $release -Names @(
    'release_request_id', 'status', 'confirmed_at'
) -NonEmpty -Label 'manifest released lease'
Assert-JsonIntegerProperties -Value $release -Names @('post_attempts') `
    -NonNegative -Label 'manifest released lease'
if (
    $release.created -isnot [bool] -and
    $null -ne $release.created
) {
    throw 'Manifest lease release created flag is neither boolean nor null.'
}
if (
    [string]$release.release_request_id -cne $LeaseReleaseRequestId -or
    $release.post_attempts -isnot [int] -and
        $release.post_attempts -isnot [long] -or
    [int]$release.post_attempts -lt 1 -or
    [int]$release.post_attempts -gt 2 -or
    $release.ambiguous_first_post -isnot [bool] -or
    (
        -not [bool]$release.ambiguous_first_post -and
        (
            -not (Test-TrueBoolean -Value $release.created) -or
            [int]$release.post_attempts -ne 1
        )
    ) -or
    (
        [bool]$release.ambiguous_first_post -and
        [int]$release.post_attempts -eq 1 -and
        $null -ne $release.created
    ) -or
    [string]$release.status -cne 'released' -or
    -not (Test-TrueBoolean -Value $release.controls_confirmed)
) {
    throw 'Manifest does not contain the exact confirmed lease release.'
}
Assert-LeaseIdentity -Lease $release.lease -ExpectedState 'released' `
    -Label 'manifest released lease identity'
if (
    [string]$release.lease.nonce_sha256 -cne
        [string]$leaseArtifact.nonce_sha256
) {
    throw 'Manifest released lease nonce does not match the acquired lease.'
}
Assert-Properties -Value $release.stop_command -Expected @(
    'id', 'request_id', 'command', 'actor', 'reason', 'status',
    'payload', 'created_at', 'executed_at'
) -Label 'manifest release STOP command'
Assert-JsonStringProperties -Value $release.stop_command -Names @(
    'request_id', 'command', 'actor', 'reason', 'status',
    'created_at', 'executed_at'
) -NonEmpty -Label 'manifest release STOP command'
Assert-Properties -Value $release.stop_command.payload -Expected @('venue') `
    -Label 'manifest release STOP command payload'
Assert-JsonStringProperties -Value $release.stop_command.payload -Names @('venue') `
    -NonEmpty -Label 'manifest release STOP command payload'
Assert-Properties -Value $release.lease `
    -Expected @(
        'id', 'watchdog_stop_request_id', 'acquired_at', 'released_at',
        'stop_command_id'
    ) `
    -Label 'manifest released lease identity'
Assert-JsonIntegerProperties -Value $release.lease -Names @('id', 'stop_command_id') `
    -NonNegative -Label 'manifest released lease identity'
Assert-JsonIntegerProperties -Value $release.stop_command -Names @('id') `
    -NonNegative -Label 'manifest release STOP command'
$releaseConfirmedAt = ConvertTo-UtcInstant -Value $release.confirmed_at `
    -Label 'manifest lease release confirmed_at'
$releaseAcquiredAt = ConvertTo-UtcInstant -Value $release.lease.acquired_at `
    -Label 'manifest released lease acquired_at'
$releaseAt = ConvertTo-UtcInstant -Value $release.lease.released_at `
    -Label 'manifest released lease released_at'
$stopCreatedAt = ConvertTo-UtcInstant -Value $release.stop_command.created_at `
    -Label 'manifest release STOP created_at'
$stopExecutedAt = ConvertTo-UtcInstant -Value $release.stop_command.executed_at `
    -Label 'manifest release STOP executed_at'
if (
    [long]$release.lease.id -ne $expectedLeaseRowId -or
    [string]$release.lease.watchdog_stop_request_id -cne
        $expectedWatchdogStopRequestId -or
    $releaseAcquiredAt -ne $expectedLeaseAcquiredAt -or
    $null -eq $release.lease.stop_command_id -or
    [long]$release.lease.stop_command_id -ne [long]$release.stop_command.id -or
    [string]$release.stop_command.request_id -cne $LeaseReleaseRequestId -or
    [string]$release.stop_command.command -cne 'stop' -or
    [string]$release.stop_command.actor -cne 'stratus-safety-release' -or
    [string]$release.stop_command.reason -cne
        "Successful PAPER canary $LeaseId completed." -or
    [string]$release.stop_command.status -cne 'succeeded' -or
    @($release.stop_command.payload.PSObject.Properties).Count -ne 1 -or
    [string]$release.stop_command.payload.venue -cne 'polymarket' -or
    $stopCreatedAt -gt $stopExecutedAt -or
    $releaseAt -ne $stopExecutedAt -or
    $stopExecutedAt -lt $lastDatabaseNow -or
    $releaseConfirmedAt -lt $stopExecutedAt -or
    $manifestCompletedAt -lt $releaseConfirmedAt
) {
    throw 'Manifest release STOP command identity is invalid.'
}

Assert-NoFailureEvidence -RunDirectory $runDirectory
[ordered]@{
    schema_version = 1
    validator = 'stratus_paper_canary_artifacts'
    mode = 'Terminal'
    verified = $true
    validated = $true
    terminal = $true
    canary_run_id = $RunId
    expected_resume_request_id = $ResumeRequestId
    lease_id = $LeaseId
    direct_stop_request_id = $DirectStopRequestId
    release_request_id = $LeaseReleaseRequestId
    preflight_sha256 = $preflightEvidence.record.sha256
    lease_acquire_sha256 = $leaseEvidence.record.sha256
    ready_sha256 = $readyEvidence.record.sha256
    manifest_sha256 = $manifestEvidence.record.sha256
    final_snapshot_sha256 = $previousHash
    offsets = @($ExpectedOffsets)
    anchor_database_time = $anchorDatabaseText
    anchor_executed_at = $anchorExecutedText
    database_window_seconds = [Math]::Floor(
        ($lastDatabaseNow - $anchorDatabaseInstant).TotalSeconds
    )
    monotonic_window_seconds = [Math]::Floor([double]$lastMonotonicSeconds)
    continuity_lane_count = $ExpectedContinuityLanes.Count
    safety_lease_released = $true
    terminal_runtime = 'polymarket_paper_fail_closed'
    failure_evidence_absent = $true
} | ConvertTo-Json -Depth 4 -Compress
