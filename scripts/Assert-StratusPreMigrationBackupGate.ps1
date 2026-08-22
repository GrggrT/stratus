[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Create', 'Resume')]
    [string] $Mode,

    [Parameter(Mandatory = $true)]
    [string] $BackupDirectory,

    [Parameter(Mandatory = $true)]
    [string] $AttestationPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedRevision,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha256:[0-9a-f]{64}$')]
    [string] $ExpectedImageId,

    [Parameter(Mandatory = $true)]
    [string] $CandidateImage,

    [Parameter(Mandatory = $true)]
    [string] $PostgresContainer,

    [string] $MigrateContainer = '',

    [string] $Root = '.',

    [ValidatePattern('^postgres:16-alpine@sha256:[0-9a-f]{64}$')]
    [string] $Postgres16Image = (
        'postgres:16-alpine@sha256:' +
        '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
    )
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceSchemaHead = '20260730_0046'
$targetSchemaHead = '20260730_0047'
$expectedPostgres16Image = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
if ($Postgres16Image -ne $expectedPostgres16Image) {
    throw 'The pre-migration gate requires the release-pinned PostgreSQL 16 image.'
}
$pinnedPostgresManifestDigest = ($Postgres16Image -split '@sha256:', 2)[1]
$pinnedPostgresRepoDigestPattern = (
    '^(?:docker\.io/)?(?:library/)?postgres@sha256:' +
    [regex]::Escape($pinnedPostgresManifestDigest) +
    '$'
)
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
    throw "Exact release Compose file does not exist or is a link: $composePath"
}
if (
    -not (Test-Path -LiteralPath $composeEnvPath -PathType Leaf) -or
    ((Get-Item -LiteralPath $composeEnvPath -Force).Attributes -band
        [IO.FileAttributes]::ReparsePoint)
) {
    throw "Exact release Compose environment file does not exist or is a link: $composeEnvPath"
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
$writerServices = @(
    'api',
    'worker',
    'telegram',
    'binance-market-stream',
    'safety-control',
    'migrate',
    'provision-runtime-role',
    'provision-safety-role'
)
$manifestKeys = @(
    'format',
    'archive',
    'database',
    'schema_head',
    'schema_head_before',
    'schema_head_after',
    'started_at',
    'completed_at',
    'archive_bytes',
    'archive_sha256',
    'catalog_entries',
    'catalog_sha256',
    'catalog_verified',
    'schema_stable_during_dump',
    'full_restore_verified'
)
$restoreKeys = @(
    'format',
    'accepted',
    'isolated',
    'postgres_image',
    'postgres_major',
    'restore_database',
    'restore_started_at',
    'restore_completed_at',
    'source_manifest_sha256',
    'source_archive_sha256',
    'source_catalog_sha256',
    'source_catalog_entries',
    'source_schema_head',
    'restored_schema_head',
    'full_restore_verified'
)

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][string] $Value)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
        return -join @(
            $algorithm.ComputeHash($bytes) |
                ForEach-Object { $_.ToString('x2') }
        )
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-ExactProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if (($actual -join "`n") -ne ($wanted -join "`n")) {
        throw "$Label does not have the exact required property set."
    }
}

function Assert-NativeJsonObject {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if (
        $null -eq $Value -or
        $Value.GetType().FullName -cne 'System.Management.Automation.PSCustomObject'
    ) {
        throw "$Label must be a native JSON object."
    }
}

function Assert-NativeJsonString {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value -or $Value.GetType() -ne [string]) {
        throw "$Label must be a native JSON string."
    }
}

function Assert-NativeJsonBoolean {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value -or $Value.GetType() -ne [bool]) {
        throw "$Label must be a native JSON boolean."
    }
}

function Assert-NativeJsonInteger {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if (
        $null -eq $Value -or
        $Value.GetType().FullName -cnotin @('System.Int32', 'System.Int64')
    ) {
        throw "$Label must be a native JSON integer."
    }
}

function Assert-NativeJsonArray {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -eq $Value -or $Value.GetType() -ne [object[]]) {
        throw "$Label must be a native JSON array."
    }
}

function Assert-NativeJsonStringArray {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-NativeJsonArray -Value $Value -Label $Label
    foreach ($entry in $Value) {
        Assert-NativeJsonString -Value $entry -Label "$Label entry"
    }
}

function Assert-NativeJsonNull {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if ($null -ne $Value) {
        throw "$Label must be native JSON null."
    }
}

function Test-ExactComposeConfigFilesLabel {
    param(
        [AllowNull()][object] $Value,
        [Parameter(Mandatory = $true)][string] $ExpectedPath
    )

    if ($null -eq $Value -or $Value.GetType() -ne [string]) {
        return $false
    }
    $paths = @(
        [string]$Value -split ',' |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
    )
    return (
        $paths.Count -eq 1 -and
        [IO.Path]::GetFullPath([string]$paths[0]) -ieq
            [IO.Path]::GetFullPath($ExpectedPath)
    )
}

function ConvertFrom-StrictManifest {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $values = [ordered]@{}
    foreach ($line in @(Get-Content -LiteralPath $LiteralPath)) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line -notmatch '^[a-z0-9_]+=[^\r\n]+$') {
            throw 'Backup manifest contains a blank or malformed line.'
        }
        $pair = $line.Split('=', 2)
        if ($values.Contains($pair[0])) {
            throw "Backup manifest repeats key $($pair[0])."
        }
        $values[$pair[0]] = $pair[1]
    }
    $actual = @($values.Keys | Sort-Object)
    $wanted = @($manifestKeys | Sort-Object)
    if (($actual -join "`n") -ne ($wanted -join "`n")) {
        throw 'Backup manifest does not have the exact v2 property set.'
    }
    return $values
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

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]] $Arguments)

    $output = @(& docker @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "A required Docker read/proof command failed (exit $LASTEXITCODE)."
    }
    return @($output | ForEach-Object { [string]$_ })
}

function ConvertFrom-StrictJsonObjectText {
    param(
        [Parameter(Mandatory = $true)][string] $Json,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $trimmed = $Json.Trim()
    if (-not $trimmed.StartsWith('{') -or -not $trimmed.EndsWith('}')) {
        throw "$Label must have a native JSON object at its root, not a scalar or array."
    }
    try {
        $convertFromJson = Get-Command `
            -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
            -CommandType Cmdlet -ErrorAction Stop
        $convertParameters = @{ InputObject = $trimmed }
        if ($convertFromJson.Parameters.ContainsKey('DateKind')) {
            $convertParameters['DateKind'] = 'String'
        }
        $value = Microsoft.PowerShell.Utility\ConvertFrom-Json @convertParameters
    }
    catch {
        throw "$Label did not return valid JSON."
    }
    Assert-NativeJsonObject -Value $value -Label $Label
    return $value
}

function Get-OneJsonObject {
    param(
        [Parameter(Mandatory = $true)][string[]] $Lines,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $meaningful = @($Lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($meaningful.Count -ne 1) {
        throw "$Label did not return exactly one JSON row."
    }
    return ConvertFrom-StrictJsonObjectText -Json $meaningful[0] -Label $Label
}

function Assert-FileArtifact {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $item = Get-Item -LiteralPath $LiteralPath -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Release evidence must be a regular file: $LiteralPath"
    }
    if ($item.Length -le 0) {
        throw "Release evidence file is empty: $LiteralPath"
    }
    return $item
}

function Assert-SafeLiveSnapshot {
    param(
        [Parameter(Mandatory = $true)][object] $Snapshot,
        [Parameter(Mandatory = $true)][string] $ExpectedSchemaHead,
        [Parameter(Mandatory = $true)][string] $Label
    )

    Assert-NativeJsonObject -Value $Snapshot -Label $Label
    Assert-ExactProperties -Value $Snapshot -Label $Label -Expected @(
        'database_clock',
        'schema_head',
        'global_mode',
        'global_kill_switch',
        'global_live_confirmation',
        'polymarket_enabled',
        'polymarket_mode',
        'polymarket_kill_switch',
        'polymarket_live_confirmation',
        'binance_enabled',
        'binance_mode',
        'binance_kill_switch',
        'binance_live_confirmation',
        'pending_commands',
        'live_orders',
        'live_positions',
        'live_intents',
        'live_fill_legs',
        'live_enrollments',
        'live_outcomes',
        'runtime_sessions',
        'idle_in_transaction_sessions',
        'lock_waiters',
        'active_pg_dump'
    )
    foreach ($property in @(
        'database_clock',
        'schema_head',
        'global_mode',
        'polymarket_mode',
        'binance_mode'
    )) {
        Assert-NativeJsonString -Value $Snapshot.$property `
            -Label "$Label $property"
    }
    foreach ($property in @(
        'global_kill_switch',
        'polymarket_enabled',
        'polymarket_kill_switch',
        'binance_enabled',
        'binance_kill_switch'
    )) {
        Assert-NativeJsonBoolean -Value $Snapshot.$property `
            -Label "$Label $property"
    }
    foreach ($property in @(
        'global_live_confirmation',
        'polymarket_live_confirmation',
        'binance_live_confirmation'
    )) {
        Assert-NativeJsonNull -Value $Snapshot.$property `
            -Label "$Label $property"
    }
    foreach ($property in @(
        'pending_commands',
        'live_orders',
        'live_positions',
        'live_intents',
        'live_fill_legs',
        'live_enrollments',
        'live_outcomes',
        'runtime_sessions',
        'idle_in_transaction_sessions',
        'lock_waiters',
        'active_pg_dump'
    )) {
        Assert-NativeJsonInteger -Value $Snapshot.$property `
            -Label "$Label $property"
    }
    if (
        $Snapshot.schema_head -ne $ExpectedSchemaHead -or
        $Snapshot.global_mode -ne 'paper' -or
        -not $Snapshot.global_kill_switch -or
        $null -ne $Snapshot.global_live_confirmation -or
        -not $Snapshot.polymarket_enabled -or
        $Snapshot.polymarket_mode -ne 'paper' -or
        -not $Snapshot.polymarket_kill_switch -or
        $null -ne $Snapshot.polymarket_live_confirmation -or
        -not $Snapshot.binance_enabled -or
        $Snapshot.binance_mode -ne 'backtest' -or
        -not $Snapshot.binance_kill_switch -or
        $null -ne $Snapshot.binance_live_confirmation
    ) {
        throw "$Label rejected schema or fail-closed runtime controls."
    }
    foreach ($property in @(
        'pending_commands', 'live_orders', 'live_positions', 'live_intents',
        'live_fill_legs', 'live_enrollments', 'live_outcomes', 'runtime_sessions',
        'idle_in_transaction_sessions', 'lock_waiters', 'active_pg_dump'
    )) {
        if ($Snapshot.$property -ne 0) {
            throw "$Label rejected non-zero $property."
        }
    }
    [void](ConvertTo-RequiredUtcTime -Value ([string]$Snapshot.database_clock) -Label "$Label clock")
}

function Get-LiveSnapshot {
    $sqlCommand = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="
SELECT json_build_object(
  'database_clock', clock_timestamp(),
  'schema_head', (SELECT version_num FROM alembic_version),
  'global_mode', (SELECT mode::text FROM runtime_control WHERE id=1),
  'global_kill_switch', (SELECT kill_switch FROM runtime_control WHERE id=1),
  'global_live_confirmation', (SELECT live_confirmation_until FROM runtime_control WHERE id=1),
  'polymarket_enabled', (SELECT enabled FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_mode', (SELECT mode::text FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_kill_switch', (SELECT kill_switch FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
  'polymarket_live_confirmation', (SELECT live_confirmation_until FROM venue_runtime_control WHERE venue='polymarket'::trading_venue),
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
    WHERE pid <> pg_backend_pid()
      AND datname=current_database()
      AND backend_type='client backend'
  ),
  'idle_in_transaction_sessions', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid <> pg_backend_pid()
      AND datname=current_database()
      AND backend_type='client backend'
      AND state LIKE 'idle in transaction%'
  ),
  'lock_waiters', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid <> pg_backend_pid()
      AND datname=current_database()
      AND backend_type='client backend'
      AND wait_event_type='Lock'
  ),
  'active_pg_dump', (
    SELECT count(*) FROM pg_stat_activity
    WHERE pid <> pg_backend_pid()
      AND datname=current_database()
      AND backend_type='client backend'
      AND application_name LIKE 'pg_dump%'
  )
)::text"
'@
    return Get-OneJsonObject -Label 'live precondition query' -Lines (
        Invoke-Docker -Arguments @(
            'exec',
            $PostgresContainer,
            'sh',
            '-ec',
            $sqlCommand
        )
    )
}

$resolvedBackupDirectory = [IO.Path]::GetFullPath($BackupDirectory)
$backupItem = Get-Item -LiteralPath $resolvedBackupDirectory -Force
if (-not $backupItem.PSIsContainer -or ($backupItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'BackupDirectory must be an existing regular directory, not a link.'
}
$resolvedAttestationPath = [IO.Path]::GetFullPath($AttestationPath)
$backupPrefix = $resolvedBackupDirectory.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar
if (-not $resolvedAttestationPath.StartsWith(
    $backupPrefix,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw 'AttestationPath must be inside the exact backup evidence directory.'
}

$manifestPath = Join-Path $resolvedBackupDirectory 'manifest.txt'
$archivePath = Join-Path $resolvedBackupDirectory 'stratus.dump'
$catalogPath = Join-Path $resolvedBackupDirectory 'catalog.list'
$restorePath = Join-Path $resolvedBackupDirectory 'restore-attestation.json'
$manifestItem = Assert-FileArtifact -LiteralPath $manifestPath
$archiveItem = Assert-FileArtifact -LiteralPath $archivePath
$catalogItem = Assert-FileArtifact -LiteralPath $catalogPath
$restoreItem = Assert-FileArtifact -LiteralPath $restorePath
[void]$manifestItem
[void]$catalogItem
[void]$restoreItem

$manifest = ConvertFrom-StrictManifest -LiteralPath $manifestPath
if (
    [string]$manifest.format -ne 'stratus-backup-manifest-v2' -or
    [string]$manifest.archive -ne 'stratus.dump' -or
    [string]$manifest.database -ne 'stratus' -or
    [string]$manifest.schema_head -ne $sourceSchemaHead -or
    [string]$manifest.schema_head_before -ne $sourceSchemaHead -or
    [string]$manifest.schema_head_after -ne $sourceSchemaHead -or
    [string]$manifest.catalog_verified -ne 'true' -or
    [string]$manifest.schema_stable_during_dump -ne 'true' -or
    [string]$manifest.full_restore_verified -ne 'false'
) {
    throw 'Backup manifest is not the exact immutable pre-0047 snapshot contract.'
}
$snapshotStarted = ConvertTo-RequiredUtcTime -Value $manifest.started_at -Label 'backup started_at'
$snapshotCompleted = ConvertTo-RequiredUtcTime -Value $manifest.completed_at -Label 'backup completed_at'
if ($snapshotCompleted -lt $snapshotStarted) {
    throw 'Backup snapshot boundary is inverted.'
}
if (
    [string]$manifest.archive_bytes -notmatch '^[1-9][0-9]*$' -or
    [int64]$manifest.archive_bytes -ne $archiveItem.Length -or
    [string]$manifest.archive_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$manifest.catalog_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$manifest.catalog_entries -notmatch '^[1-9][0-9]*$'
) {
    throw 'Backup manifest size, hash, or catalog metadata is malformed.'
}
$archiveSha256 = Get-FileSha256 -LiteralPath $archivePath
$catalogSha256 = Get-FileSha256 -LiteralPath $catalogPath
$manifestSha256 = Get-FileSha256 -LiteralPath $manifestPath
$catalogEntries = @(
    Select-String -LiteralPath $catalogPath -Pattern '^[0-9]+;'
).Count
if (
    $archiveSha256 -ne [string]$manifest.archive_sha256 -or
    $catalogSha256 -ne [string]$manifest.catalog_sha256 -or
    $catalogEntries -ne [int]$manifest.catalog_entries
) {
    throw 'Backup archive or catalog no longer matches the immutable manifest.'
}

if ($resolvedBackupDirectory -match '[,\r\n]') {
    throw 'BackupDirectory cannot be represented safely as a read-only Docker mount.'
}
$catalogProbe = @(
    Invoke-Docker -Arguments @(
        'run',
        '--rm',
        '--network',
        'none',
        '--mount',
        "type=bind,source=$resolvedBackupDirectory,target=/evidence,readonly",
        $Postgres16Image,
        'sh',
        '-ec',
        'pg_restore --list /evidence/stratus.dump | sha256sum | cut -d" " -f1'
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($catalogProbe.Count -ne 1 -or $catalogProbe[0].Trim() -ne $catalogSha256) {
    throw 'Pinned PostgreSQL 16 did not reproduce the exact archive catalog.'
}

$restore = ConvertFrom-StrictJsonObjectText `
    -Json (Get-Content -LiteralPath $restorePath -Raw) `
    -Label 'Restore attestation'
Assert-NativeJsonObject -Value $restore -Label 'restore attestation'
Assert-ExactProperties -Value $restore -Expected $restoreKeys -Label 'restore attestation'
foreach ($property in @(
    'format',
    'postgres_image',
    'restore_database',
    'restore_started_at',
    'restore_completed_at',
    'source_manifest_sha256',
    'source_archive_sha256',
    'source_catalog_sha256',
    'source_schema_head',
    'restored_schema_head'
)) {
    Assert-NativeJsonString -Value $restore.$property `
        -Label "restore attestation $property"
}
foreach ($property in @('accepted', 'isolated', 'full_restore_verified')) {
    Assert-NativeJsonBoolean -Value $restore.$property `
        -Label "restore attestation $property"
}
foreach ($property in @('postgres_major', 'source_catalog_entries')) {
    Assert-NativeJsonInteger -Value $restore.$property `
        -Label "restore attestation $property"
}
$restoreStarted = ConvertTo-RequiredUtcTime -Value $restore.restore_started_at -Label 'restore started_at'
$restoreCompleted = ConvertTo-RequiredUtcTime -Value $restore.restore_completed_at -Label 'restore completed_at'
if (
    $restore.format -ne 'stratus-isolated-pg16-restore-v1' -or
    -not $restore.accepted -or
    -not $restore.isolated -or
    $restore.postgres_image -ne $Postgres16Image -or
    $restore.postgres_major -ne 16 -or
    $restore.restore_database -notmatch '^stratus_restore_[0-9a-f]{32}$' -or
    $restoreCompleted -lt $restoreStarted -or
    $restoreStarted -lt $snapshotCompleted -or
    $restore.source_manifest_sha256 -ne $manifestSha256 -or
    $restore.source_archive_sha256 -ne $archiveSha256 -or
    $restore.source_catalog_sha256 -ne $catalogSha256 -or
    $restore.source_catalog_entries -ne $catalogEntries -or
    $restore.source_schema_head -ne $sourceSchemaHead -or
    $restore.restored_schema_head -ne $sourceSchemaHead -or
    -not $restore.full_restore_verified
) {
    throw 'Full isolated PostgreSQL 16 restore acceptance does not bind the exact backup.'
}

$imageInspect = Get-OneJsonObject -Label 'candidate image inspect' -Lines (
    Invoke-Docker -Arguments @('image', 'inspect', $CandidateImage, '--format', '{{json .}}')
)
Assert-NativeJsonObject -Value $imageInspect.Config -Label 'candidate image Config'
Assert-NativeJsonObject -Value $imageInspect.Config.Labels -Label 'candidate image Labels'
Assert-NativeJsonString -Value $imageInspect.Id -Label 'candidate image Id'
Assert-NativeJsonString -Value $imageInspect.Config.User -Label 'candidate image User'
Assert-NativeJsonString `
    -Value $imageInspect.Config.Labels.'org.opencontainers.image.revision' `
    -Label 'candidate image revision label'
if (
    $imageInspect.Id -ne $ExpectedImageId -or
    $imageInspect.Config.Labels.'org.opencontainers.image.revision' -ne $ExpectedRevision -or
    $imageInspect.Config.User -cne 'stratus' -or
    $null -ne $imageInspect.Config.Entrypoint
) {
    throw 'Candidate image identity or revision does not match the release gate.'
}

$pinnedPostgresImageId = $null
if ($Mode -eq 'Create') {
    $pinnedPostgresInspect = Get-OneJsonObject -Label 'pinned PostgreSQL image inspect' -Lines (
        Invoke-Docker -Arguments @(
            'image',
            'inspect',
            $Postgres16Image,
            '--format',
            '{{json .}}'
        )
    )
    Assert-NativeJsonString -Value $pinnedPostgresInspect.Id `
        -Label 'pinned PostgreSQL image Id'
    Assert-NativeJsonString -Value $pinnedPostgresInspect.Os `
        -Label 'pinned PostgreSQL image Os'
    Assert-NativeJsonString -Value $pinnedPostgresInspect.Architecture `
        -Label 'pinned PostgreSQL image Architecture'
    Assert-NativeJsonStringArray -Value $pinnedPostgresInspect.RepoDigests `
        -Label 'pinned PostgreSQL image RepoDigests'
    $pinnedPostgresImageId = [string]$pinnedPostgresInspect.Id
    if (
        $pinnedPostgresImageId -notmatch '^sha256:[0-9a-f]{64}$' -or
        [string]$pinnedPostgresInspect.Os -ne 'linux' -or
        [string]$pinnedPostgresInspect.Architecture -ne 'amd64' -or
        @($pinnedPostgresInspect.RepoDigests | Where-Object {
            [string]$_ -match $pinnedPostgresRepoDigestPattern
        }).Count -ne 1
    ) {
        throw 'Local PostgreSQL image does not prove the exact pinned linux/amd64 release image.'
    }
}

$postgresInspect = Get-OneJsonObject -Label 'PostgreSQL container inspect' -Lines (
    Invoke-Docker -Arguments @('inspect', $PostgresContainer, '--format', '{{json .}}')
)
Assert-NativeJsonObject -Value $postgresInspect.Config `
    -Label 'PostgreSQL container Config'
Assert-NativeJsonObject -Value $postgresInspect.State `
    -Label 'PostgreSQL container State'
foreach ($property in @('Id', 'Name', 'Image')) {
    Assert-NativeJsonString -Value $postgresInspect.$property `
        -Label "PostgreSQL container $property"
}
Assert-NativeJsonString -Value $postgresInspect.Config.Image `
    -Label 'PostgreSQL container Config.Image'
Assert-NativeJsonString -Value $postgresInspect.State.StartedAt `
    -Label 'PostgreSQL container State.StartedAt'
Assert-NativeJsonBoolean -Value $postgresInspect.State.Running `
    -Label 'PostgreSQL container State.Running'
Assert-NativeJsonBoolean -Value $postgresInspect.State.OOMKilled `
    -Label 'PostgreSQL container State.OOMKilled'
Assert-NativeJsonInteger -Value $postgresInspect.RestartCount `
    -Label 'PostgreSQL container RestartCount'
if (
    $postgresInspect.Name -ne '/weatherpoly-postgres-1' -or
    -not $postgresInspect.State.Running -or
    $postgresInspect.State.OOMKilled
) {
    throw 'The retained production PostgreSQL container is not the exact running boundary.'
}
if (
    $Mode -eq 'Create' -and (
        [string]$postgresInspect.Config.Image -ne $Postgres16Image -or
        [string]$postgresInspect.Image -ne $pinnedPostgresImageId
    )
) {
    throw 'Production PostgreSQL does not use the exact pinned image ID and Config.Image.'
}
$postgresStarted = ConvertTo-RequiredUtcTime -Value (
    [string]$postgresInspect.State.StartedAt
) -Label 'PostgreSQL started_at'
if ($Mode -eq 'Create' -and $snapshotStarted -lt $postgresStarted) {
    throw 'Backup is not a fresh snapshot from this bounded PostgreSQL startup.'
}

$runningServices = @(
    Invoke-Docker -Arguments (
        $composeArguments + @('ps', '--status', 'running', '--services')
    ) |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
$runningWriters = @($runningServices | Where-Object { $_ -in $writerServices })
if ($runningWriters.Count -ne 0) {
    throw "Application writers are running: $($runningWriters -join ', ')."
}

$migrateIdentity = $null
if ($Mode -eq 'Create') {
    if ([string]::IsNullOrWhiteSpace($MigrateContainer)) {
        throw 'Create mode requires the stopped exact-candidate migrate container.'
    }
    $migrateInspect = Get-OneJsonObject -Label 'migrate container inspect' -Lines (
        Invoke-Docker -Arguments @('inspect', $MigrateContainer, '--format', '{{json .}}')
    )
    Assert-NativeJsonObject -Value $migrateInspect.State `
        -Label 'migrate container State'
    Assert-NativeJsonObject -Value $migrateInspect.Config `
        -Label 'migrate container Config'
    Assert-NativeJsonObject -Value $migrateInspect.Config.Labels `
        -Label 'migrate container Labels'
    Assert-NativeJsonObject -Value $migrateInspect.HostConfig `
        -Label 'migrate container HostConfig'
    Assert-NativeJsonObject -Value $migrateInspect.HostConfig.RestartPolicy `
        -Label 'migrate container RestartPolicy'
    Assert-NativeJsonObject -Value $migrateInspect.NetworkSettings `
        -Label 'migrate container NetworkSettings'
    Assert-NativeJsonObject -Value $migrateInspect.NetworkSettings.Networks `
        -Label 'migrate container Networks'
    foreach ($property in @('Id', 'Name', 'Image')) {
        Assert-NativeJsonString -Value $migrateInspect.$property `
            -Label "migrate container $property"
    }
    foreach ($property in @('Image', 'User')) {
        Assert-NativeJsonString -Value $migrateInspect.Config.$property `
            -Label "migrate container Config.$property"
    }
    Assert-NativeJsonStringArray -Value $migrateInspect.Config.Cmd `
        -Label 'migrate container Cmd'
    Assert-NativeJsonStringArray -Value $migrateInspect.Config.Env `
        -Label 'migrate container Env'
    Assert-NativeJsonArray -Value $migrateInspect.Mounts `
        -Label 'migrate container Mounts'
    foreach ($property in @(
        'Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead'
    )) {
        Assert-NativeJsonBoolean -Value $migrateInspect.State.$property `
            -Label "migrate container State.$property"
    }
    foreach ($property in @('Pid', 'ExitCode')) {
        Assert-NativeJsonInteger -Value $migrateInspect.State.$property `
            -Label "migrate container State.$property"
    }
    Assert-NativeJsonInteger -Value $migrateInspect.RestartCount `
        -Label 'migrate container RestartCount'
    Assert-NativeJsonString -Value $migrateInspect.State.Status `
        -Label 'migrate container State.Status'
    Assert-NativeJsonString -Value $migrateInspect.State.Error `
        -Label 'migrate container State.Error'
    Assert-NativeJsonString -Value $migrateInspect.HostConfig.RestartPolicy.Name `
        -Label 'migrate container RestartPolicy.Name'
    Assert-NativeJsonInteger `
        -Value $migrateInspect.HostConfig.RestartPolicy.MaximumRetryCount `
        -Label 'migrate container RestartPolicy.MaximumRetryCount'
    $releaseDigest = $ExpectedImageId.Substring(7)
    $migrateLabels = $migrateInspect.Config.Labels
    $migrateConfigHash = $migrateLabels.'com.docker.compose.config-hash'
    $migrateConfigFiles = $migrateLabels.'com.docker.compose.project.config_files'
    $migrateNetworks = @(
        $migrateInspect.NetworkSettings.Networks.PSObject.Properties.Name
    )
    if (
        $migrateInspect.Id -notmatch '^[0-9a-f]{64}$' -or
        $migrateInspect.Name -cne '/weatherpoly-migrate-1' -or
        $migrateInspect.State.Status -cne 'created' -or
        $migrateInspect.State.Running -or
        $migrateInspect.State.Paused -or
        $migrateInspect.State.Restarting -or
        $migrateInspect.State.OOMKilled -or
        $migrateInspect.State.Dead -or
        $migrateInspect.State.Pid -ne 0 -or
        $migrateInspect.State.ExitCode -ne 0 -or
        -not [string]::IsNullOrWhiteSpace($migrateInspect.State.Error) -or
        $migrateInspect.RestartCount -ne 0 -or
        $migrateInspect.HostConfig.RestartPolicy.Name -cne 'no' -or
        $migrateInspect.HostConfig.RestartPolicy.MaximumRetryCount -ne 0 -or
        $migrateInspect.Image -ne $ExpectedImageId -or
        $migrateInspect.Config.Image -cne $CandidateImage -or
        $migrateInspect.Config.User -cne 'stratus' -or
        $null -ne $migrateInspect.Config.Entrypoint -or
        ($migrateInspect.Config.Cmd -join "`n") -cne "alembic`nupgrade`nhead" -or
        $migrateInspect.Mounts.Count -ne 0 -or
        $migrateNetworks.Count -ne 1 -or
        $migrateNetworks[0] -cne "${composeProjectName}_default" -or
        $migrateLabels.'org.opencontainers.image.revision' -cne $ExpectedRevision -or
        $migrateLabels.'com.docker.compose.project' -cne $composeProjectName -or
        $migrateLabels.'com.docker.compose.service' -cne 'migrate' -or
        $migrateLabels.'com.docker.compose.project.working_dir' -ine $absoluteRoot -or
        $migrateLabels.'com.docker.compose.image' -cne $ExpectedImageId -or
        $migrateLabels.'com.docker.compose.oneoff' -cne 'False' -or
        $migrateLabels.'com.docker.compose.container-number' -cne '1' -or
        $migrateLabels.'com.docker.compose.depends_on' -cne
            'postgres:service_healthy:false' -or
        $null -eq $migrateConfigHash -or
        $migrateConfigHash.GetType() -ne [string] -or
        $migrateConfigHash -notmatch '^[0-9a-f]{64}$' -or
        -not (Test-ExactComposeConfigFilesLabel `
            -Value $migrateConfigFiles -ExpectedPath $composePath) -or
        @($migrateInspect.Config.Env | Where-Object {
            $_ -ceq "RELEASE_IMAGE_DIGEST=$releaseDigest"
        }).Count -ne 1 -or
        @($migrateInspect.Config.Env | Where-Object {
            [string]$_ -imatch '^OPERATOR_API_KEY='
        }).Count -ne 0
    ) {
        throw 'Stopped migrate container command/entrypoint/user/mount/dependency/config provenance is not exact.'
    }
    $migrateIdentity = [ordered]@{
        container_id = $migrateInspect.Id
        image_id = $migrateInspect.Image
        config_image = $migrateInspect.Config.Image
        command = @($migrateInspect.Config.Cmd)
        entrypoint = $null
        user = $migrateInspect.Config.User
        mounts = @()
        compose_project = $composeProjectName
        compose_working_dir = $absoluteRoot
        compose_config_file = $composePath
        compose_config_hash = [string]$migrateConfigHash
        compose_depends_on = 'postgres:service_healthy:false'
    }
}

$expectedLiveSchema = if ($Mode -eq 'Create') { $sourceSchemaHead } else { $targetSchemaHead }
$firstLive = Get-LiveSnapshot
Assert-SafeLiveSnapshot -Snapshot $firstLive -ExpectedSchemaHead $expectedLiveSchema -Label 'initial live preconditions'

if ($Mode -eq 'Create') {
    if (Test-Path -LiteralPath $resolvedAttestationPath) {
        throw 'Create mode refuses to overwrite an existing pre-migration attestation.'
    }
    $payload = [ordered]@{
        source_schema_head = $sourceSchemaHead
        target_schema_head = $targetSchemaHead
        snapshot = [ordered]@{
            started_at = [string]$manifest.started_at
            completed_at = [string]$manifest.completed_at
            manifest_sha256 = $manifestSha256
            archive_sha256 = $archiveSha256
            archive_bytes = [int64]$archiveItem.Length
            catalog_sha256 = $catalogSha256
            catalog_entries = $catalogEntries
        }
        restore = [ordered]@{
            postgres_image = [string]$restore.postgres_image
            postgres_major = [int]$restore.postgres_major
            restore_database = [string]$restore.restore_database
            restore_started_at = [string]$restore.restore_started_at
            restore_completed_at = [string]$restore.restore_completed_at
            full_restore_verified = $true
        }
        candidate = [ordered]@{
            revision = $ExpectedRevision
            image = $CandidateImage
            image_id = $ExpectedImageId
        }
        postgres = [ordered]@{
            container_id = [string]$postgresInspect.Id
            image_id = [string]$postgresInspect.Image
            config_image = [string]$postgresInspect.Config.Image
            restart_count = [int]$postgresInspect.RestartCount
            started_at = [string]$postgresInspect.State.StartedAt
        }
        migrate = $migrateIdentity
        initial_live_preconditions = $firstLive
        accepted_at = [string]$firstLive.database_clock
    }
    $payloadJson = $payload | ConvertTo-Json -Depth 12 -Compress
    $envelope = [ordered]@{
        format = 'stratus-pre-migration-gate-v1'
        accepted = $true
        payload = $payload
        payload_sha256 = Get-Sha256Text -Value $payloadJson
    }
    $serialized = $envelope | ConvertTo-Json -Depth 12
    $stream = [IO.File]::Open(
        $resolvedAttestationPath,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
        try {
            $writer.Write($serialized)
            $writer.Flush()
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}
else {
    if (-not (Test-Path -LiteralPath $resolvedAttestationPath -PathType Leaf)) {
        throw 'Already-0047 resume requires the existing accepted pre-migration attestation.'
    }
    $envelope = ConvertFrom-StrictJsonObjectText `
        -Json (Get-Content -LiteralPath $resolvedAttestationPath -Raw) `
        -Label 'Existing pre-migration attestation'
    Assert-NativeJsonObject -Value $envelope -Label 'pre-migration attestation'
    Assert-ExactProperties -Value $envelope -Label 'pre-migration attestation' -Expected @(
        'format', 'accepted', 'payload', 'payload_sha256'
    )
    Assert-NativeJsonString -Value $envelope.format `
        -Label 'pre-migration attestation format'
    Assert-NativeJsonBoolean -Value $envelope.accepted `
        -Label 'pre-migration attestation accepted'
    Assert-NativeJsonObject -Value $envelope.payload `
        -Label 'pre-migration attestation payload'
    Assert-NativeJsonString -Value $envelope.payload_sha256 `
        -Label 'pre-migration attestation payload_sha256'
    if (
        $envelope.format -ne 'stratus-pre-migration-gate-v1' -or
        -not $envelope.accepted -or
        $envelope.payload_sha256 -notmatch '^[0-9a-f]{64}$'
    ) {
        throw 'Existing pre-migration attestation is not accepted.'
    }
    Assert-ExactProperties -Value $envelope.payload `
        -Label 'pre-migration attestation payload' -Expected @(
            'source_schema_head',
            'target_schema_head',
            'snapshot',
            'restore',
            'candidate',
            'postgres',
            'migrate',
            'initial_live_preconditions',
            'accepted_at'
        )
    foreach ($property in @('source_schema_head', 'target_schema_head', 'accepted_at')) {
        Assert-NativeJsonString -Value $envelope.payload.$property `
            -Label "pre-migration payload $property"
    }
    foreach ($property in @('snapshot', 'restore', 'candidate', 'postgres', 'migrate')) {
        Assert-NativeJsonObject -Value $envelope.payload.$property `
            -Label "pre-migration payload $property"
    }

    $attestedSnapshot = $envelope.payload.snapshot
    Assert-ExactProperties -Value $attestedSnapshot -Label 'attested snapshot' -Expected @(
        'started_at', 'completed_at', 'manifest_sha256', 'archive_sha256',
        'archive_bytes', 'catalog_sha256', 'catalog_entries'
    )
    foreach ($property in @(
        'started_at', 'completed_at', 'manifest_sha256', 'archive_sha256',
        'catalog_sha256'
    )) {
        Assert-NativeJsonString -Value $attestedSnapshot.$property `
            -Label "attested snapshot $property"
    }
    foreach ($property in @('archive_bytes', 'catalog_entries')) {
        Assert-NativeJsonInteger -Value $attestedSnapshot.$property `
            -Label "attested snapshot $property"
    }

    $attestedRestore = $envelope.payload.restore
    Assert-ExactProperties -Value $attestedRestore -Label 'attested restore' -Expected @(
        'postgres_image', 'postgres_major', 'restore_database',
        'restore_started_at', 'restore_completed_at', 'full_restore_verified'
    )
    foreach ($property in @(
        'postgres_image', 'restore_database', 'restore_started_at',
        'restore_completed_at'
    )) {
        Assert-NativeJsonString -Value $attestedRestore.$property `
            -Label "attested restore $property"
    }
    Assert-NativeJsonInteger -Value $attestedRestore.postgres_major `
        -Label 'attested restore postgres_major'
    Assert-NativeJsonBoolean -Value $attestedRestore.full_restore_verified `
        -Label 'attested restore full_restore_verified'

    $attestedCandidate = $envelope.payload.candidate
    Assert-ExactProperties -Value $attestedCandidate -Label 'attested candidate' -Expected @(
        'revision', 'image', 'image_id'
    )
    foreach ($property in @('revision', 'image', 'image_id')) {
        Assert-NativeJsonString -Value $attestedCandidate.$property `
            -Label "attested candidate $property"
    }

    $attestedPostgres = $envelope.payload.postgres
    Assert-ExactProperties -Value $attestedPostgres -Label 'attested PostgreSQL identity' -Expected @(
        'container_id', 'image_id', 'config_image', 'restart_count', 'started_at'
    )
    foreach ($property in @('container_id', 'image_id', 'config_image', 'started_at')) {
        Assert-NativeJsonString -Value $attestedPostgres.$property `
            -Label "attested PostgreSQL $property"
    }
    Assert-NativeJsonInteger -Value $attestedPostgres.restart_count `
        -Label 'attested PostgreSQL restart_count'

    $attestedMigrate = $envelope.payload.migrate
    Assert-ExactProperties -Value $attestedMigrate -Label 'attested migrate identity' -Expected @(
        'container_id', 'image_id', 'config_image', 'command', 'entrypoint',
        'user', 'mounts', 'compose_project', 'compose_working_dir',
        'compose_config_file', 'compose_config_hash', 'compose_depends_on'
    )
    foreach ($property in @(
        'container_id', 'image_id', 'config_image', 'user', 'compose_project',
        'compose_working_dir', 'compose_config_file', 'compose_config_hash',
        'compose_depends_on'
    )) {
        Assert-NativeJsonString -Value $attestedMigrate.$property `
            -Label "attested migrate $property"
    }
    Assert-NativeJsonStringArray -Value $attestedMigrate.command `
        -Label 'attested migrate command'
    Assert-NativeJsonNull -Value $attestedMigrate.entrypoint `
        -Label 'attested migrate entrypoint'
    Assert-NativeJsonArray -Value $attestedMigrate.mounts `
        -Label 'attested migrate mounts'

    Assert-SafeLiveSnapshot `
        -Snapshot $envelope.payload.initial_live_preconditions `
        -ExpectedSchemaHead $sourceSchemaHead `
        -Label 'attested pre-migration preconditions'
    [void](ConvertTo-RequiredUtcTime -Value $envelope.payload.accepted_at `
        -Label 'attested accepted_at')
    $payloadJson = $envelope.payload | ConvertTo-Json -Depth 12 -Compress
    if ((Get-Sha256Text -Value $payloadJson) -ne $envelope.payload_sha256) {
        throw 'Existing pre-migration attestation payload hash is invalid.'
    }
    if (
        $envelope.payload.source_schema_head -ne $sourceSchemaHead -or
        $envelope.payload.target_schema_head -ne $targetSchemaHead -or
        $attestedSnapshot.started_at -ne [string]$manifest.started_at -or
        $attestedSnapshot.completed_at -ne [string]$manifest.completed_at -or
        $attestedSnapshot.manifest_sha256 -ne $manifestSha256 -or
        $attestedSnapshot.archive_sha256 -ne $archiveSha256 -or
        $attestedSnapshot.archive_bytes -ne $archiveItem.Length -or
        $attestedSnapshot.catalog_sha256 -ne $catalogSha256 -or
        $attestedSnapshot.catalog_entries -ne $catalogEntries -or
        $attestedRestore.postgres_image -ne $restore.postgres_image -or
        $attestedRestore.postgres_major -ne $restore.postgres_major -or
        $attestedRestore.restore_database -ne $restore.restore_database -or
        $attestedRestore.restore_started_at -ne $restore.restore_started_at -or
        $attestedRestore.restore_completed_at -ne $restore.restore_completed_at -or
        -not $attestedRestore.full_restore_verified -or
        $attestedCandidate.revision -ne $ExpectedRevision -or
        $attestedCandidate.image -ne $CandidateImage -or
        $attestedCandidate.image_id -ne $ExpectedImageId -or
        $attestedMigrate.container_id -notmatch '^[0-9a-f]{64}$' -or
        $attestedMigrate.image_id -ne $ExpectedImageId -or
        $attestedMigrate.config_image -cne $CandidateImage -or
        ($attestedMigrate.command -join "`n") -cne "alembic`nupgrade`nhead" -or
        $attestedMigrate.user -cne 'stratus' -or
        $attestedMigrate.mounts.Count -ne 0 -or
        $attestedMigrate.compose_project -cne $composeProjectName -or
        $attestedMigrate.compose_working_dir -ine $absoluteRoot -or
        $attestedMigrate.compose_config_file -ine $composePath -or
        $attestedMigrate.compose_config_hash -notmatch '^[0-9a-f]{64}$' -or
        $attestedMigrate.compose_depends_on -cne 'postgres:service_healthy:false'
    ) {
        throw 'Existing accepted attestation does not bind this backup/restore/candidate.'
    }
    if (
        $postgresInspect.Id -ne $attestedPostgres.container_id -or
        $postgresInspect.Image -ne $attestedPostgres.image_id -or
        $postgresInspect.Config.Image -ne $attestedPostgres.config_image -or
        $postgresInspect.RestartCount -ne $attestedPostgres.restart_count -or
        $postgresInspect.State.StartedAt -ne $attestedPostgres.started_at
    ) {
        throw 'Current PostgreSQL container identity drifted from the accepted pre-migration attestation.'
    }
}

$secondLive = Get-LiveSnapshot
Assert-SafeLiveSnapshot -Snapshot $secondLive -ExpectedSchemaHead $expectedLiveSchema -Label 'immediate pre-DDL live preconditions'
$firstClock = ConvertTo-RequiredUtcTime -Value $firstLive.database_clock -Label 'initial live clock'
$secondClock = ConvertTo-RequiredUtcTime -Value $secondLive.database_clock -Label 'immediate pre-DDL clock'
if ($secondClock -lt $firstClock -or ($secondClock - $firstClock).TotalSeconds -gt 60) {
    throw 'Live preconditions were not repeated in the bounded immediate pre-DDL window.'
}

[pscustomobject]@{
    verified = $true
    mode = $Mode.ToLowerInvariant()
    source_schema_head = $sourceSchemaHead
    current_schema_head = $expectedLiveSchema
    candidate_revision = $ExpectedRevision
    candidate_image_id = $ExpectedImageId
    backup_manifest_sha256 = $manifestSha256
    archive_sha256 = $archiveSha256
    catalog_sha256 = $catalogSha256
    attestation_path = $resolvedAttestationPath
    immediate_pre_ddl_database_clock = [string]$secondLive.database_clock
    secrets_persisted = $false
} | ConvertTo-Json -Compress
