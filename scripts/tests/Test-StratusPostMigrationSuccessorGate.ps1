$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Windows PowerShell 5.1 can autoload this module after a global test double is
# declared and replace that function export. Load it first, then install mocks.
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$gatePath = Join-Path $repoRoot 'scripts\Assert-StratusPostMigrationSuccessorGate.ps1'
$watchdogPath = Join-Path $repoRoot `
    'scripts\Invoke-StratusSuccessorTransitionWatchdog.ps1'

if (-not (Test-Path -LiteralPath $gatePath -PathType Leaf)) {
    throw "Post-migration successor gate does not exist: $gatePath"
}
if (-not (Test-Path -LiteralPath $watchdogPath -PathType Leaf)) {
    throw "Successor Transition watchdog does not exist: $watchdogPath"
}
$gateSource = Get-Content -LiteralPath $gatePath -Raw
$watchdogSource = Get-Content -LiteralPath $watchdogPath -Raw
$watchdogSourceSha256 = (
    Microsoft.PowerShell.Utility\Get-FileHash `
        -LiteralPath $watchdogPath -Algorithm SHA256
).Hash.ToLowerInvariant()
[void][scriptblock]::Create($gateSource)
[void][scriptblock]::Create($watchdogSource)

$gateTokens = $null
$gateParseErrors = $null
$gateAst = [Management.Automation.Language.Parser]::ParseInput(
    $gateSource, [ref]$gateTokens, [ref]$gateParseErrors
)
if (@($gateParseErrors).Count -ne 0) {
    throw 'Post-migration successor gate did not parse for isolated helper review.'
}
$byteHashFunction = $gateAst.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Get-ByteArraySha256'
}, $true)
if ($null -eq $byteHashFunction) {
    throw 'The core byte-array SHA-256 helper is missing.'
}
$emptyByteHash = & ([scriptblock]::Create(
    $byteHashFunction.Extent.Text +
    "`nGet-ByteArraySha256 -Bytes ([byte[]]@())"
))
if ($emptyByteHash -cne
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855') {
    throw 'The core byte-array SHA-256 helper does not accept the empty journal prefix.'
}

foreach ($required in @(
    "[ValidateSet('Transition')]",
    'PreMigrationAttestationPath',
    'NetworkRepairAttestationPath',
    'PreStartAttestationPath',
    'LiveAcceptancePath',
    'stratus-stopped-postgres-safety-plane-repair-v1',
    'stratus-post-migration-successor-transition-prestart-v2',
    'stratus-post-migration-successor-transition-live-v2',
    'stratus-successor-transition-watchdog-handoff-v1',
    'stratus-successor-transition-watchdog-binding-v1',
    'stratus-successor-transition-watchdog-commit-v1',
    'stratus-successor-transition-watchdog-terminal-v1',
    'Start-TransitionWatchdogProcess',
    'Start-WatchdogHeartbeatPublisher',
    'Assert-TransitionWatchdogSuccessReceipt',
    'Invoke-StratusSuccessorTransitionWatchdog.ps1',
    '$Watchdog.collector.CloseOutputFiles()',
    '$Watchdog.collector.OutputFilesClosed',
    'The successor transition watchdog output handles remained open.',
    '-HeartbeatPublisher $null',
    'The immutable terminal success receipt is the atomic authorization',
    "Assert-NativeJsonObject -Value `$receipt.authorization",
    '[IO.FileMode]::CreateNew',
    "Resolve-PinnedApplication -Name 'docker'",
    "Resolve-PinnedApplication -Name 'git'",
    "'update', '--restart=no', `$productionPostgresId",
    "Invoke-Docker -Arguments @('start', `$productionPostgresId)",
    'Invoke-BoundedPinnedDockerStop',
    'The non-reconnecting Docker event witness exited before final sealing.',
    'Transition requires exactly one retained PostgreSQL start event.',
    'Transition requires exactly one retained PostgreSQL data-volume mount event.',
    'Transition requires exactly one healthy PostgreSQL status event.',
    'Transition did not witness exactly one connect to each production network.',
    'PostgreSQL exec event counts do not match the exact transition commands.',
    'external_postgres_start_permitted',
    'start_owned_by_transition_process',
    "Parameters.ContainsKey('DateKind')",
    "`$parameters['DateKind'] = 'String'",
    'actual.access_method IS DISTINCT FROM ''btree'''
    'index_row.indclass[position]'
    'index_row.indcollation[position]'
    'index_row.indoption[position]<>0'
    'actual.indnatts <> cardinality(expected.key_names)'
    'pg_get_expr(index_row.indpred, index_row.indrelid, true)'
    '(SELECT count(*) FROM actual_indexes)=7'
    '(SELECT count(*) FROM expected_functions)=24'
    '(SELECT count(*) FROM expected_function_shapes)=24'
    'normalized_prosrc_sha256'
    'replace(procedure.prosrc, E''\r\n'', E''\n'')'
    "'functions_exact'"
)) {
    if (-not $gateSource.Contains($required)) {
        throw "Post-migration successor gate is missing required contract text: $required"
    }
}

$watchdogOutputCloseOffset = $gateSource.IndexOf(
    '$Watchdog.collector.CloseOutputFiles()', [StringComparison]::Ordinal
)
$watchdogStdoutReadOffset = -1
if ($watchdogOutputCloseOffset -ge 0) {
    $watchdogStdoutReadOffset = $gateSource.IndexOf(
        "Read-BoundedStrictJsonDocument -LiteralPath `$Watchdog.stdout_path",
        $watchdogOutputCloseOffset, [StringComparison]::Ordinal
    )
}
if ($watchdogOutputCloseOffset -lt 0 -or
    $watchdogStdoutReadOffset -le $watchdogOutputCloseOffset) {
    throw 'The core does not release watchdog output handles before strict terminal parsing.'
}

$preCommitRecheckOffset = $gateSource.IndexOf(
    '$preCommitEngine = Get-DockerEngineBoundary', [StringComparison]::Ordinal
)
$commitPublicationOffset = $gateSource.IndexOf(
    'Write-CreateNewJson -LiteralPath $watchdogCommitPath',
    [StringComparison]::Ordinal
)
$terminalAcceptanceOffset = $gateSource.IndexOf(
    '$watchdogTerminalReceipt = Assert-TransitionWatchdogSuccessReceipt',
    [StringComparison]::Ordinal
)
$transitionSuccessOffset = $gateSource.IndexOf(
    '$transitionSucceeded = $true', $terminalAcceptanceOffset,
    [StringComparison]::Ordinal
)
if (
    $preCommitRecheckOffset -lt 0 -or
    $preCommitRecheckOffset -ge $commitPublicationOffset -or
    $terminalAcceptanceOffset -le $commitPublicationOffset -or
    $transitionSuccessOffset -le $terminalAcceptanceOffset
) {
    throw 'The watchdog terminal receipt is not the atomic rollback boundary.'
}
$postTerminalValidationText = $gateSource.Substring(
    $terminalAcceptanceOffset,
    $transitionSuccessOffset - $terminalAcceptanceOffset
)
foreach ($forbidden in @(
    'Get-FileSha256', 'Assert-NetworkRepairEvidenceUnchanged',
    'Get-HeldHbaReadBoundary', 'Get-DockerEngineBoundary',
    'Get-PostgresBoundary', 'Assert-ExclusiveRuntimeTopology', 'StopAndWait'
)) {
    if ($postTerminalValidationText.Contains($forbidden)) {
        throw "A rollback-capable check remains after terminal acceptance: $forbidden"
    }
}
if ([regex]::Matches(
    $gateSource, 'api_stage_permitted\s*=\s*\$false'
).Count -lt 2) {
    throw 'PreStart and durable Live must both be independently non-authorizing.'
}
if ($gateSource.Contains('interval_milliseconds = 1000')) {
    throw 'Heartbeat evidence contains the retired interval_milliseconds field.'
}
if (
    [regex]::Matches(
        $gateSource,
        [regex]::Escape("`$ErrorActionPreference = 'Continue'")
    ).Count -ne 4
) {
    throw 'Pinned native invocations are not normalized at all four PS5.1 stderr boundaries.'
}
if ([regex]::Matches(
    $gateSource,
    [regex]::Escape('$ErrorActionPreference = $priorErrorActionPreference')
).Count -ne 4) {
    throw 'Pinned native invocations do not restore ErrorActionPreference exactly.'
}

foreach ($required in @(
    'stratus-successor-transition-watchdog-lease-v1',
    'stratus-successor-transition-watchdog-heartbeat-v1',
    'stratus-successor-transition-watchdog-commit-v1',
    'stratus-successor-transition-watchdog-terminal-v1',
    'accepted_live_commit',
    'heartbeat_stale',
    'invalid_or_replayed_heartbeat',
    'invalid_or_forged_commit',
    '[IO.FileMode]::CreateNew',
    "'stop', '--time', [string]`$containmentStopTimeoutSeconds"
)) {
    if (-not $watchdogSource.Contains($required)) {
        throw "Successor Transition watchdog is missing required contract text: $required"
    }
}

function Assert-GateFailure {
    param(
        [Parameter(Mandatory = $true)][scriptblock] $Action,
        [Parameter(Mandatory = $true)][string] $ExpectedMessage,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $observed = $false
    try {
        & $Action | Out-Null
    }
    catch {
        $observed = $true
        if ([string]$_.Exception.Message -notlike "*$ExpectedMessage*") {
            throw "$Label failed for the wrong reason: $($_.Exception.Message)"
        }
    }
    if (-not $observed) {
        throw "$Label was incorrectly accepted."
    }
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $Value
    )

    [IO.File]::WriteAllText(
        $LiteralPath,
        $Value,
        [Text.UTF8Encoding]::new($false)
    )
}

function Write-JsonFixture {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][object] $Value,
        [int] $Depth = 20
    )

    Write-Utf8NoBom -LiteralPath $LiteralPath `
        -Value ($Value | ConvertTo-Json -Depth $Depth)
}

function Get-TextSha256 {
    param([Parameter(Mandatory = $true)][string] $Value)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash([Text.UTF8Encoding]::new($false).GetBytes($Value))
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-TestFileSha256 {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    return (
        Microsoft.PowerShell.Utility\Get-FileHash `
            -LiteralPath $LiteralPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
}

function Assert-ExactTestJsonProperties {
    param(
        [Parameter(Mandatory = $true)][object] $Value,
        [Parameter(Mandatory = $true)][string[]] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $actual = @($Value.PSObject.Properties | ForEach-Object { [string]$_.Name })
    [Array]::Sort($actual, [StringComparer]::Ordinal)
    $wanted = @($Expected)
    [Array]::Sort($wanted, [StringComparer]::Ordinal)
    if (($actual -join "`n") -cne ($wanted -join "`n")) {
        throw "$Label does not have the exact native JSON property set."
    }
}

function Copy-JsonValue {
    param([Parameter(Mandatory = $true)][object] $Value)

    return ConvertFrom-TestJsonText `
        -Json ($Value | ConvertTo-Json -Depth 30 -Compress)
}

function ConvertFrom-TestJsonText {
    param([Parameter(Mandatory = $true)][string] $Json)

    $command = Get-Command `
        -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' -CommandType Cmdlet
    $parameters = @{ InputObject = $Json }
    if ($command.Parameters.ContainsKey('DateKind')) {
        $parameters.DateKind = 'String'
    }
    return Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
}

function Update-EnvelopePayloadHash {
    param([Parameter(Mandatory = $true)][object] $Envelope)

    $payloadJson = $Envelope.payload | ConvertTo-Json -Depth 20 -Compress
    $Envelope.payload_sha256 = Get-TextSha256 -Value $payloadJson
}

$predecessorRevision = '6d093b49cd5397d1e2bdb41fb4640d3e59127281'
$predecessorImage = 'stratus:candidate-6d093b49cd53'
$predecessorImageId =
    'sha256:3d8c2904224328efb5604a28778c138abf26d8f43a06c02c733c8ad270ee2d24'
$successorRevision = '0547bf2a5a7a65c05ad99d3f33301af152462c8b'
$successorImage = 'stratus:candidate-0547bf2a5a7a'
$successorImageId =
    'sha256:a31c5ae9f848a3c05d9577f8b13936a3391eaf15bd10a467504cc62fedfde010'
$postgresId = '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$postgresImage = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$postgresImageId =
    'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$secretSentinel = 'DO-NOT-PERSIST-successor-gate-secret-7a6f62d5'

function New-PreMigrationAttestationFixture {
    $payload = [ordered]@{
        source_schema_head = '20260730_0046'
        target_schema_head = '20260730_0047'
        snapshot = [ordered]@{
            started_at = '2026-08-22T19:56:54Z'
            completed_at = '2026-08-22T20:06:09Z'
            manifest_sha256 = 'e2129a1149936048c164ff934e31a8fb3531adcdf6163568730c3969576d1796'
            archive_sha256 = 'eabd66ff4e07560c00ff04deac18613497bddf3e355fddae6b688917a4e3b5fa'
            archive_bytes = [int64]2388138397
            catalog_sha256 = 'cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65'
            catalog_entries = 1077
        }
        restore = [ordered]@{
            postgres_image = $postgresImage
            postgres_major = 16
            restore_database = 'stratus_restore_c23dcb0821ca49cc9059fa464efbb7ce'
            restore_started_at = '2026-08-22T20:55:10Z'
            restore_completed_at = '2026-08-22T21:24:49Z'
            full_restore_verified = $true
        }
        candidate = [ordered]@{
            revision = $predecessorRevision
            image = $predecessorImage
            image_id = $predecessorImageId
        }
        postgres = [ordered]@{
            container_id = $postgresId
            image_id = $postgresImageId
            config_image = $postgresImage
            restart_count = 0
            started_at = '2026-08-22T19:39:52.897703304Z'
        }
        migrate = [ordered]@{
            container_id = '8d6cbeacdc75d3280e339f14e9685d6bf46c5a81c8c33afb3620a5a14d510b71'
            image_id = $predecessorImageId
            config_image = $predecessorImage
            command = @('alembic', 'upgrade', 'head')
            entrypoint = $null
            user = 'stratus'
            mounts = @()
            compose_project = 'weatherpoly'
            compose_working_dir = 'C:\Users\Administrator\Documents\weatherpoly'
            compose_config_file = 'C:\Users\Administrator\Documents\weatherpoly\docker-compose.yml'
            compose_config_hash = 'd4ff2c35cb59d94427d7f36986ad28cdd38919dd9c5d53fcb00c980bc59edd06'
            compose_depends_on = ''
        }
        initial_live_preconditions = [ordered]@{
            database_clock = '2026-08-22T21:40:24.895505+00:00'
            schema_head = '20260730_0046'
            global_mode = 'paper'
            global_kill_switch = $true
            global_live_confirmation = $null
            polymarket_enabled = $true
            polymarket_mode = 'paper'
            polymarket_kill_switch = $true
            polymarket_live_confirmation = $null
            binance_enabled = $true
            binance_mode = 'backtest'
            binance_kill_switch = $true
            binance_live_confirmation = $null
            pending_commands = 0
            live_orders = 0
            live_positions = 0
            live_intents = 0
            live_fill_legs = 0
            live_enrollments = 0
            live_outcomes = 0
            runtime_sessions = 0
            idle_in_transaction_sessions = 0
            lock_waiters = 0
            active_pg_dump = 0
        }
        accepted_at = '2026-08-22T21:40:24.895505+00:00'
    }
    $payloadJson = $payload | ConvertTo-Json -Depth 20 -Compress
    $payloadHash = Get-TextSha256 -Value $payloadJson
    if ($payloadHash -cne 'd1ad1ed1511653cc394b70a118ca41b8751c9504cd0fdd449936868a5045eabd') {
        throw "Historical pre-migration payload fixture is not canonical: $payloadHash"
    }
    return [ordered]@{
        format = 'stratus-pre-migration-gate-v1'
        accepted = $true
        payload = $payload
        payload_sha256 = $payloadHash
    }
}

function Write-HistoricalLineageFixtures {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $fixtures = [ordered]@{
        '09-stopped-migrate-stage-acceptance.json' = [ordered]@{
            format = 'stratus-section3-stopped-migrate-stage-v1'
            accepted = $true
            compose = [ordered]@{
                source_revision = $predecessorRevision
                container_depends_on_label = ''
                empty_label_accepted = $true
            }
            migrate = [ordered]@{
                image_id = $predecessorImageId
                container_id = '8d6cbeacdc75d3280e339f14e9685d6bf46c5a81c8c33afb3620a5a14d510b71'
                ddl_started = $false
            }
            decision = 'PROCEED_TO_FRESH_PRE_0047_BACKUP'
        }
        '17-pre-migration-wrapper-false-negative.json' = [ordered]@{
            decision = 'HOLD_FAIL_CLOSED'
            migration_started = $false
            pre_migration_attestation_created = $false
            fail_closed = $true
            live_enabled = $false
        }
        '18-pre-migration-compatibility-holds.json' = [ordered]@{
            decision = 'RESOLVED_FAIL_CLOSED_HOLDS'
            accepted_compatibility_normalizations = [ordered]@{
                tracked_guard_modified = $false
                patched_ast_errors = 0
            }
            independent_audits = [ordered]@{
                git_blob_and_eol = 'PASS'
                patch_cardinality_and_ast = 'PASS'
            }
            fail_closed = $true
            live_enabled = $false
        }
        '19-pre-migration-gate-and-0047-migration-accepted.json' = [ordered]@{
            format = 'stratus-section3-migration-acceptance-v1'
            decision = 'ACCEPT'
            candidate_revision = $predecessorRevision
            candidate_image_id = $predecessorImageId
            pre_migration_attestation = [ordered]@{
                sha256 = 'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189'
                payload_sha256 = 'd1ad1ed1511653cc394b70a118ca41b8751c9504cd0fdd449936868a5045eabd'
            }
            migration = [ordered]@{
                exit_code = 0
                restart_count = 0
                oom_killed = $false
            }
            postgres = [ordered]@{
                container_id = $postgresId
                identity_unchanged = $true
            }
            live_enabled = $false
            binance_kill_switch_removed = $false
        }
        '20-strict-post-ddl-0047-accepted.json' = [ordered]@{
            format = 'stratus-section3-strict-post-ddl-v1'
            decision = 'ACCEPT'
            transaction = [ordered]@{ read_only = $true }
            schema = [ordered]@{
                head_count = 1
                head = '20260730_0047'
            }
            indexes = [ordered]@{ all_exact = $true }
            boundaries = [ordered]@{
                runtime = $true
                safety = $true
            }
            lease = [ordered]@{
                total_rows = 0
                active_rows = 0
            }
            live_enabled = $false
            binance_kill_switch_removed = $false
        }
        '21-post-ddl-hba-accepted.json' = [ordered]@{
            format = 'stratus-section3-post-ddl-hba-v1'
            decision = 'ACCEPT'
            postgres_container_id = $postgresId
            host_sha256 = '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
            mounted_sha256 = '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
            loaded_inventory_sha256 = 'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567'
            loaded_line_count = 16
            loaded_rule_count = 15
            loaded_rule_errors = 0
            reload_accepted = $true
            live_enabled = $false
        }
    }
    foreach ($entry in $fixtures.GetEnumerator()) {
        Write-JsonFixture -LiteralPath (Join-Path $LiteralPath $entry.Key) `
            -Value $entry.Value
    }
}

function Write-HistoricalBackupFixtures {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $manifest = @(
        'format=stratus-backup-manifest-v2',
        'archive=stratus.dump',
        'database=stratus',
        'schema_head=20260730_0046',
        'schema_head_before=20260730_0046',
        'schema_head_after=20260730_0046',
        'started_at=2026-08-22T19:56:54Z',
        'completed_at=2026-08-22T20:06:09Z',
        'archive_bytes=2388138397',
        'archive_sha256=eabd66ff4e07560c00ff04deac18613497bddf3e355fddae6b688917a4e3b5fa',
        'catalog_entries=1077',
        'catalog_sha256=cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65',
        'catalog_verified=true',
        'schema_stable_during_dump=true',
        'full_restore_verified=false'
    )
    Write-Utf8NoBom -LiteralPath (Join-Path $LiteralPath 'manifest.txt') `
        -Value (($manifest -join "`n") + "`n")
    Write-Utf8NoBom -LiteralPath (Join-Path $LiteralPath 'stratus.dump') `
        -Value 'small immutable archive fixture; FileInfo length is safely virtualized by the harness'
    Write-Utf8NoBom -LiteralPath (Join-Path $LiteralPath 'catalog.list') `
        -Value "1077; fixture catalog row`n"
    Write-JsonFixture -LiteralPath (Join-Path $LiteralPath 'restore-attestation.json') `
        -Value ([ordered]@{
            format = 'stratus-isolated-pg16-restore-v1'
            accepted = $true
            isolated = $true
            postgres_image = $postgresImage
            postgres_major = 16
            restore_database = 'stratus_restore_c23dcb0821ca49cc9059fa464efbb7ce'
            restore_started_at = '2026-08-22T20:55:10Z'
            restore_completed_at = '2026-08-22T21:24:49Z'
            source_manifest_sha256 = 'e2129a1149936048c164ff934e31a8fb3531adcdf6163568730c3969576d1796'
            source_archive_sha256 = 'eabd66ff4e07560c00ff04deac18613497bddf3e355fddae6b688917a4e3b5fa'
            source_catalog_sha256 = 'cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65'
            source_schema_head = '20260730_0046'
            restored_schema_head = '20260730_0046'
            full_restore_verified = $true
        })
}

function New-SuccessorFixture {
    param([Parameter(Mandatory = $true)][string] $LiteralPath)

    $root = Join-Path $LiteralPath 'root'
    $build = Join-Path $LiteralPath 'build'
    $backup = Join-Path $LiteralPath 'backup'
    $lineage = Join-Path $LiteralPath 'lineage'
    $evidence = Join-Path $root 'successor-evidence'
    foreach ($directory in @($root, $build, $backup, $lineage, $evidence)) {
        [void](New-Item -ItemType Directory -Path $directory)
    }
    foreach ($worktree in @($root, $build)) {
        [void](New-Item -ItemType Directory -Path `
            (Join-Path $worktree 'ops\postgres') -Force)
        Write-Utf8NoBom -LiteralPath (Join-Path $worktree 'docker-compose.yml') `
            -Value "services:`n  postgres: {}`n  migrate: {}`n"
        Write-Utf8NoBom -LiteralPath (Join-Path $worktree '.env') `
            -Value "STRATUS_IMAGE=$successorImage`nSTRATUS_RELEASE_IMAGE_DIGEST=$($successorImageId.Substring(7))`n"
        Copy-Item -LiteralPath (
            Join-Path $repoRoot 'ops\postgres\stratus-pg_hba.conf'
        ) -Destination (Join-Path $worktree 'ops\postgres\stratus-pg_hba.conf')
    }
    $preMigrationPath = Join-Path $backup 'pre-migration-attestation.json'
    Write-JsonFixture -LiteralPath $preMigrationPath `
        -Value (New-PreMigrationAttestationFixture)
    Write-HistoricalBackupFixtures -LiteralPath $backup
    Write-HistoricalLineageFixtures -LiteralPath $lineage
    return [pscustomobject]@{
        root = $root
        build = $build
        backup = $backup
        lineage = $lineage
        evidence = $evidence
        pre_migration = $preMigrationPath
        prestart = Join-Path $evidence 'successor-prestart-attestation.json'
        live = Join-Path $evidence 'successor-live-acceptance.json'
    }
}

$defaultNetworkId = '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
$safetyNetworkId = '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'

function Initialize-HostFixtureState {
    param([Parameter(Mandatory = $true)][object] $Fixture)

    $global:SuccessorFixture = $Fixture
    $global:SuccessorHashDrift = 'none'
    $global:SuccessorHashCallCounts = @{}
    $global:SuccessorTrustedHashes = @{}
    $global:SuccessorTrustedLengths = @{}
    $hashes = @{
        'pre-migration-attestation.json' = 'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189'
        '09-stopped-migrate-stage-acceptance.json' = 'bc8006665fc1365fb51a55904d21f4a6b783e3929a4f1060d8168f01093766dd'
        '17-pre-migration-wrapper-false-negative.json' = 'ba881647611219d29ccf6fb68d42f1a745a3a431ede5f52fc9cf54ba028a4bea'
        '18-pre-migration-compatibility-holds.json' = '829ccf65b644fb7905a525d0a68a29bd3e9483cf2962b2ec2af144accf7c6033'
        '19-pre-migration-gate-and-0047-migration-accepted.json' = '200d1b10bfe05537c4385d87dec81c2bbb2669a69c0abcdfa2ba9217b7d9fb4f'
        '20-strict-post-ddl-0047-accepted.json' = 'c6e30ccea288f3f8c3b57130c4e8af2f40da53ad33b808cb4ddeaa43437503ff'
        '21-post-ddl-hba-accepted.json' = 'f42d979d98ec9feaa949cb23cb0add1925443bb406967cc1802e701bc0a4926c'
        'manifest.txt' = 'e2129a1149936048c164ff934e31a8fb3531adcdf6163568730c3969576d1796'
        'stratus.dump' = 'eabd66ff4e07560c00ff04deac18613497bddf3e355fddae6b688917a4e3b5fa'
        'catalog.list' = 'cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65'
        'restore-attestation.json' = '73939f689e3add8b92ba72cb4824a1c2243fa35228eaed25ffc5dab4144a3e4e'
    }
    foreach ($entry in $hashes.GetEnumerator()) {
        $base = if ([string]$entry.Key -match '^\d{2}-') {
            $Fixture.lineage
        }
        else {
            $Fixture.backup
        }
        $path = [IO.Path]::GetFullPath(
            (Join-Path $base ([string]$entry.Key))
        ).ToLowerInvariant()
        $global:SuccessorTrustedHashes[$path] = [string]$entry.Value
        $global:SuccessorTrustedLengths[$path] = [int64](
            Microsoft.PowerShell.Management\Get-Item -LiteralPath $path
        ).Length
    }
    $hostHba = [IO.Path]::GetFullPath(
        (Join-Path $Fixture.root 'ops\postgres\stratus-pg_hba.conf')
    ).ToLowerInvariant()
    $global:SuccessorTrustedHashes[$hostHba] =
        '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
    $global:SuccessorVirtualArchivePath = [IO.Path]::GetFullPath(
        (Join-Path $Fixture.backup 'stratus.dump')
    )
}

function global:Get-Item {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [switch] $Force
    )

    $fullPath = [IO.Path]::GetFullPath($LiteralPath)
    if ($fullPath -ieq $global:SuccessorVirtualArchivePath) {
        return [pscustomobject]@{
            PSIsContainer = $false
            Attributes = [IO.FileAttributes]::Normal
            Length = [int64]2388138397
            FullName = $fullPath
        }
    }
    return Microsoft.PowerShell.Management\Get-Item `
        -LiteralPath $LiteralPath -Force:$Force
}

function global:Get-FileHash {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [string] $Algorithm = 'SHA256'
    )

    $fullPath = [IO.Path]::GetFullPath($LiteralPath).ToLowerInvariant()
    if ($global:SuccessorTrustedHashes.ContainsKey($fullPath)) {
        if (-not $global:SuccessorHashCallCounts.ContainsKey($fullPath)) {
            $global:SuccessorHashCallCounts[$fullPath] = 0
        }
        $global:SuccessorHashCallCounts[$fullPath] += 1
        $count = [int]$global:SuccessorHashCallCounts[$fullPath]
        $hash = [string]$global:SuccessorTrustedHashes[$fullPath]
        $leaf = [IO.Path]::GetFileName($fullPath)
        if (
            $leaf -in @('stratus.dump', 'restore-attestation.json') -and
            $global:SuccessorTrustedLengths.ContainsKey($fullPath) -and
            [int64](
                Microsoft.PowerShell.Management\Get-Item -LiteralPath $fullPath
            ).Length -ne [int64]$global:SuccessorTrustedLengths[$fullPath]
        ) {
            $hash = '0' * 64
        }
        if (
            ($global:SuccessorHashDrift -eq 'pre-migration' -and
                $leaf -ceq 'pre-migration-attestation.json') -or
            ($global:SuccessorHashDrift -eq 'lineage' -and
                $leaf -ceq '19-pre-migration-gate-and-0047-migration-accepted.json') -or
            ($global:SuccessorHashDrift -eq 'hba' -and
                $fullPath -like '*\root\ops\postgres\stratus-pg_hba.conf')
        ) {
            $hash = '0' * 64
        }
        return [pscustomobject]@{
            Algorithm = 'SHA256'
            Hash = $hash
            Path = $LiteralPath
        }
    }
    return Microsoft.PowerShell.Utility\Get-FileHash `
        -LiteralPath $LiteralPath -Algorithm $Algorithm
}

function Get-FakeCliSource {
    return @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Threading;

public static class StratusFakeCli
{
    public static int Main(string[] args)
    {
        string tool = Path.GetFileNameWithoutExtension(
            System.Reflection.Assembly.GetExecutingAssembly().Location
        ).ToLowerInvariant();
        try
        {
            if (tool == "docker" && args.Length > 0 && args[0] == "events")
                return RunEventWitness();
            return InvokeServer(tool, args);
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("fake " + tool + " failure: " + error.Message);
            return 125;
        }
    }

    private static int InvokeServer(string tool, string[] args)
    {
        string pipeName = Environment.GetEnvironmentVariable("STRATUS_FAKE_PIPE");
        using (NamedPipeClientStream pipe = new NamedPipeClientStream(
            ".", pipeName, PipeDirection.InOut, PipeOptions.None))
        {
            pipe.Connect(10000);
            using (BinaryWriter writer = new BinaryWriter(pipe, Encoding.UTF8, true))
            using (BinaryReader reader = new BinaryReader(pipe, Encoding.UTF8, true))
            {
                writer.Write(tool);
                writer.Write(args.Length);
                foreach (string value in args) writer.Write(value ?? String.Empty);
                writer.Flush();
                int exitCode = reader.ReadInt32();
                int stdoutCount = reader.ReadInt32();
                for (int i = 0; i < stdoutCount; ++i)
                    Console.Out.WriteLine(reader.ReadString());
                int stderrCount = reader.ReadInt32();
                for (int i = 0; i < stderrCount; ++i)
                    Console.Error.WriteLine(reader.ReadString());
                return exitCode;
            }
        }
    }

    private static int RunEventWitness()
    {
        string source = Environment.GetEnvironmentVariable("STRATUS_FAKE_EVENTS");
        string ready = Environment.GetEnvironmentVariable("STRATUS_FAKE_WITNESS_READY");
        string stop = Environment.GetEnvironmentVariable("STRATUS_FAKE_WITNESS_EXIT");
        string exited = Environment.GetEnvironmentVariable("STRATUS_FAKE_WITNESS_EXITED");
        long offset = File.Exists(source) ? new FileInfo(source).Length : 0;
        File.WriteAllText(ready, Process.GetCurrentProcess().Id.ToString(), new UTF8Encoding(false));
        while (!File.Exists(stop))
        {
            if (File.Exists(source))
            {
                using (FileStream stream = new FileStream(
                    source, FileMode.Open, FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete))
                {
                    if (stream.Length > offset)
                    {
                        stream.Seek(offset, SeekOrigin.Begin);
                        byte[] bytes = new byte[stream.Length - offset];
                        int total = 0;
                        while (total < bytes.Length)
                        {
                            int count = stream.Read(bytes, total, bytes.Length - total);
                            if (count <= 0) break;
                            total += count;
                        }
                        offset += total;
                        Console.Out.Write(Encoding.UTF8.GetString(bytes, 0, total));
                        Console.Out.Flush();
                    }
                }
            }
            Thread.Sleep(10);
        }
        File.WriteAllText(exited, Process.GetCurrentProcess().Id.ToString(), new UTF8Encoding(false));
        return 0;
    }
}
'@
}

function Get-FakeServerSourcePart1 {
    return @'
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:configuration = $null
$script:generation = ''
$script:state = $null
$script:artifactLock = $null
$utf8 = [Text.UTF8Encoding]::new($false)
$postgresId = '6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299'
$postgresImage = 'postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$postgresImageId = 'sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
$predecessorRevision = '6d093b49cd5397d1e2bdb41fb4640d3e59127281'
$predecessorImage = 'stratus:candidate-6d093b49cd53'
$predecessorImageId = 'sha256:3d8c2904224328efb5604a28778c138abf26d8f43a06c02c733c8ad270ee2d24'
$successorRevision = '0547bf2a5a7a65c05ad99d3f33301af152462c8b'
$successorImage = 'stratus:candidate-0547bf2a5a7a'
$successorImageId = 'sha256:a31c5ae9f848a3c05d9577f8b13936a3391eaf15bd10a467504cc62fedfde010'
$defaultNetworkId = '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2'
$safetyNetworkId = '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620'
$fixedHba = '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'

function New-Result {
    param([object[]] $Stdout = @(), [int] $ExitCode = 0, [object[]] $Stderr = @())
    return [pscustomobject]@{
        exit_code = $ExitCode
        stdout = [string[]]@($Stdout | ForEach-Object { [string]$_ })
        stderr = [string[]]@($Stderr | ForEach-Object { [string]$_ })
    }
}

function Write-AtomicText {
    param([string] $LiteralPath, [string] $Value)
    $temporary = $LiteralPath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temporary, $Value, $utf8)
    Move-Item -LiteralPath $temporary -Destination $LiteralPath -Force
}

function Import-Configuration {
    $candidate = Get-Content -LiteralPath $env:STRATUS_FAKE_CONFIG -Raw |
        ConvertFrom-Json
    if ([string]$candidate.generation -ceq $script:generation) {
        return
    }
    if ($null -ne $script:artifactLock) {
        $script:artifactLock.Dispose()
        $script:artifactLock = $null
    }
    $script:configuration = $candidate
    $script:generation = [string]$candidate.generation
    $script:state = [ordered]@{
        generation = $script:generation
        fault = [string]$candidate.fault
        postgres_state = 'stopped'
        restart_policy = 'unless-stopped'
        historical_started_at = '2026-08-22T19:39:52.897703304Z'
        historical_finished_at = '2026-08-22T22:05:00.000000000Z'
        running_started_at = ''
        proof_sequence = 0
        barrier_sequence = 0
        exec_sequence = 0
        postgres_inspect_count = 0
        network_inspect_count = 0
        compose_topology_count = 0
        global_topology_count = 0
        hba_sha_count = 0
        hba_fresh_count = 0
        snapshot_count = 0
        start_count = 0
        stop_count = 0
        update_count = 0
        restart_count = 0
        kill_count = 0
        compose_mutation_count = 0
        forbidden_mutation_count = 0
        proof_create_count = 0
        proof_remove_count = 0
        barrier_create_count = 0
        barrier_remove_count = 0
        docker_call_count = 0
        git_call_count = 0
        health_exec_count = 0
        gate_exec_count = 0
        owned = @()
        database_operations = @()
        mutations = @()
        event_nano = [int64]0
        watchdog_fault_injected = $false
        watchdog_publisher_cleanup_delay_count = 0
    }
    foreach ($path in @(
        $env:STRATUS_FAKE_RESULT, $env:STRATUS_FAKE_EVENTS,
        $env:STRATUS_FAKE_WITNESS_READY, $env:STRATUS_FAKE_WITNESS_EXIT,
        $env:STRATUS_FAKE_WITNESS_EXITED
    )) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
    [IO.File]::WriteAllText($env:STRATUS_FAKE_EVENTS, '', $utf8)
}

function Add-WatchdogRaceArtifact {
    param([string] $Suffix, [string] $Value)
    $path = [string]$script:configuration.prestart + $Suffix
    if (-not (Test-Path -LiteralPath $path)) {
        [IO.File]::WriteAllText($path, $Value, $utf8)
    }
    $script:state.watchdog_fault_injected = $true
}

function Lock-WatchdogHeartbeatMalformed {
    if ($script:state.watchdog_fault_injected) { return }
    $path = [string]$script:configuration.prestart + '.watchdog-heartbeat.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return }
    Write-AtomicText -LiteralPath $path `
        -Value '{"format":"malformed-heartbeat"}'
    $script:state.watchdog_fault_injected = $true
}

function Lock-WatchdogHeartbeatPublisher {
    if ($script:state.watchdog_fault_injected) { return }
    $path = [string]$script:configuration.prestart + '.watchdog-heartbeat.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return }
    # Read-sharing deliberately permits the watchdog's final continuity read,
    # while denying the delete-sharing required by the owner's atomic replace.
    $script:artifactLock = [IO.FileStream]::new(
        $path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $script:state.watchdog_fault_injected = $true
}

function Poison-WatchdogHeartbeatAfterContainment {
    $path = [string]$script:configuration.prestart + '.watchdog-heartbeat.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return }
    try {
        Write-AtomicText -LiteralPath $path `
            -Value '{"format":"post-containment-heartbeat-poison"}'
    }
    catch {
        # A concurrent atomic publisher replacement is allowed to win.  The
        # watchdog's original stale-heartbeat timeout remains the fallback.
    }
}

function Export-State {
    if ($null -eq $script:state) { return }
    Write-AtomicText -LiteralPath $env:STRATUS_FAKE_RESULT `
        -Value ($script:state | ConvertTo-Json -Depth 20)
}

function Get-UnixNanoseconds {
    $epoch = [DateTimeOffset]::Parse('1970-01-01T00:00:00Z')
    return [int64]([Math]::Floor(
        ([DateTimeOffset]::UtcNow - $epoch).TotalMilliseconds
    )) * 1000000
}

function Get-NextEventNano {
    $now = Get-UnixNanoseconds
    if ($now -le [int64]$script:state.event_nano) {
        $now = [int64]$script:state.event_nano + 1000000
    }
    $script:state.event_nano = [int64]$now
    return [int64]$now
}

function Wait-WitnessReady {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(5)
    while (-not (Test-Path -LiteralPath $env:STRATUS_FAKE_WITNESS_READY)) {
        if ([DateTimeOffset]::UtcNow -ge $deadline) {
            throw 'fake event witness did not become ready'
        }
        Start-Sleep -Milliseconds 10
    }
}

function Add-Event {
    param(
        [string] $Type,
        [string] $Action,
        [string] $ActorId,
        [Parameter(Mandatory = $true)][object] $Attributes,
        [AllowNull()][object] $TimeNano
    )
    Wait-WitnessReady
    $nano = if ($null -eq $TimeNano) { Get-NextEventNano } else { [int64]$TimeNano }
    if ($nano -gt [int64]$script:state.event_nano) {
        $script:state.event_nano = $nano
    }
    $event = [ordered]@{
        Type = $Type
        Action = $Action
        Actor = [ordered]@{
            ID = $ActorId
            Attributes = $Attributes
        }
        timeNano = [int64]$nano
    }
    [IO.File]::AppendAllText(
        $env:STRATUS_FAKE_EVENTS,
        (($event | ConvertTo-Json -Depth 8 -Compress) + "`n"),
        $utf8
    )
    return [int64]$nano
}

function Add-ContainerEvent {
    param([string] $Action, [string] $Id, [object] $Attributes)
    return Add-Event -Type 'container' -Action $Action -ActorId $Id `
        -Attributes $Attributes -TimeNano $null
}

function Add-ExecTriplet {
    param(
        [ValidateSet('health', 'gate', 'unowned')][string] $Kind,
        [string] $Command = ''
    )
    $script:state.exec_sequence += 1
    $execId = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $command = if (-not [string]::IsNullOrEmpty($Command)) {
        $Command
    }
    else {
        switch ($Kind) {
            'health' { '/bin/sh -c pg_isready -U stratus -d stratus' }
            'gate' { 'PGPASSWORD=x exec psql -XAtq' }
            default { 'rm -rf /unowned' }
        }
    }
    foreach ($action in @(
        "exec_create: $command", "exec_start: $command", 'exec_die'
    )) {
        $attributes = [ordered]@{
            execID = $execId
            name = 'weatherpoly-postgres-1'
        }
        if ($action -ceq 'exec_die') { $attributes.exitCode = '0' }
        [void](Add-ContainerEvent -Action $action -Id $postgresId `
            -Attributes $attributes)
    }
    if ($Kind -eq 'health') { $script:state.health_exec_count += 1 }
    elseif ($Kind -eq 'gate') { $script:state.gate_exec_count += 1 }
}

function Get-GitObjectId {
    param([string] $Path)
    switch -CaseSensitive ($Path) {
        '.gitattributes' { '0e7e5b4fc7af0523d798fc4a01979d2ad20f178b' }
        '.dockerignore' { '7b30162a290eb8df806d9bf299ecba874052ecb3' }
        'Dockerfile' { '554aaec8b175912723b686c7bc2f02c29c037f48' }
        'README.md' { 'e39cc4f5324ce5c6c9e9cce35e9d27e4dc6ea571' }
        'docker-compose.yml' { '53422981828ca0e00635d1c36778285f5e7221b9' }
        'alembic.ini' { 'e109e86453d6913d5cfa41d9318a5f94544d921b' }
        'migrations' { 'c1eabc51a09a79ce80fcc7aeb05156dd27d437e3' }
        'ops/postgres/stratus-pg_hba.conf' { '59071191059e64f02d40f5daa62178b8b467c95a' }
        'pyproject.toml' { 'f148c93eacae0539a6155c10dbd971baeb590fe3' }
        'uv.lock' { '055c2fd18d0c3856c923ad8c9e011c027656aad4' }
        'src/stratus/__init__.py' { '15fef34b5aae8ee9c1c6004685d9c98a581260f7' }
        'src/stratus/config.py' { '4424ea6189413ca026afa7c52517c7c26d79409e' }
        'src/stratus/db/__init__.py' { '331fd004a38d5cfcb60d87492f6628b3ab88a606' }
        'src/stratus/db/base.py' { '3a60ede62c3ffd368d2473e5fb476e8d4656caef' }
        'src/stratus/db/models.py' { '0354452bd74cfd7021bcbe07cee8861a1d957f82' }
        default { '' }
    }
}

function Handle-Git {
    param([string[]] $Arguments)
    $script:state.git_call_count += 1
    if ($Arguments.Count -lt 3 -or $Arguments[0] -cne '-C') {
        return New-Result -ExitCode 90 -Stderr 'unexpected fake git invocation'
    }
    $repository = [IO.Path]::GetFullPath($Arguments[1])
    $command = @($Arguments[2..($Arguments.Count - 1)])
    $joined = $command -join ' '
    $fault = [string]$script:state.fault
    if ($fault -eq 'git-command-failure' -and $joined -ceq 'rev-parse HEAD') {
        return New-Result -ExitCode 72 -Stderr 'synthetic Git provenance failure'
    }
    if ($joined -ceq 'rev-parse HEAD') {
        if (($fault -eq 'git-root-revision' -and
            $repository -ieq [string]$script:configuration.root) -or
            ($fault -eq 'git-build-revision' -and
            $repository -ieq [string]$script:configuration.build)) {
            return New-Result -Stdout $predecessorRevision
        }
        return New-Result -Stdout $successorRevision
    }
    if ($joined -ceq 'status --porcelain=v1 --untracked-files=all') {
        if ($fault -eq 'git-dirty') { return New-Result -Stdout ' M migrations/env.py' }
        return New-Result
    }
    if ($joined -ceq 'symbolic-ref -q --short HEAD') {
        if ($fault -eq 'git-branch') { return New-Result -Stdout 'main' }
        if ($fault -eq 'git-detached-stderr') {
            return New-Result -ExitCode 1 -Stderr 'synthetic detached-worktree stderr'
        }
        return New-Result -ExitCode 1
    }
    if ($joined -like 'merge-base --is-ancestor *') {
        if ($fault -in @('git-ancestry', 'git-ancestry-stderr')) {
            $stderr = if ($fault -eq 'git-ancestry-stderr') {
                'synthetic ancestry stderr'
            } else { @() }
            return New-Result -ExitCode 1 -Stderr $stderr
        }
        return New-Result
    }
    if ($command.Count -eq 2 -and $command[0] -ceq 'rev-parse' -and
        $command[1] -match '^[0-9a-f]{40}:(.+)$') {
        $path = [string]$Matches[1]
        $objectId = Get-GitObjectId -Path $path
        if ([string]::IsNullOrEmpty($objectId)) {
            return New-Result -ExitCode 91 -Stderr 'unknown fake Git object'
        }
        if ($fault -eq 'git-source' -and
            $command[1].StartsWith($successorRevision + ':') -and
            $path -ceq 'migrations') {
            $objectId = 'f' * 40
        }
        return New-Result -Stdout $objectId
    }
    if ($joined -ceq "ls-tree -r --name-only $successorRevision -- migrations") {
        if ($fault -eq 'git-inventory') { return New-Result -Stdout 'migrations' }
        return New-Result -Stdout @(1..47 | ForEach-Object {
            'migrations/versions/{0:d4}_fixture.py' -f $_
        })
    }
    return New-Result -ExitCode 92 -Stderr "unexpected fake git invocation: $joined"
}
'@
}

function Get-FakeServerSourcePart2 {
    return @'
function Add-OwnedContainer {
    param([string] $Id, [string] $Kind, [string] $Name, [string] $Image, [string] $Label)
    $entry = [pscustomobject]@{
        id = $Id
        kind = $Kind
        name = $Name
        image = $Image
        label = $Label
    }
    $script:state.owned = @($script:state.owned) + @($entry)
}

function Get-OwnedContainer {
    param([string] $Id)
    return @($script:state.owned | Where-Object { [string]$_.id -ceq $Id } |
        Select-Object -First 1)
}

function Remove-OwnedContainer {
    param([string] $Id)
    $script:state.owned = @($script:state.owned | Where-Object {
        [string]$_.id -cne $Id
    })
}

function New-ImageInspect {
    param([string] $Reference)
    $isPredecessor = $Reference -ceq $predecessorImage -or
        $Reference -ceq $predecessorImageId
    $id = if ($isPredecessor) { $predecessorImageId } else { $successorImageId }
    $revision = if ($isPredecessor) { $predecessorRevision } else { $successorRevision }
    if (-not $isPredecessor -and $script:state.fault -eq 'image-id') {
        $id = 'sha256:' + ('f' * 64)
    }
    if (-not $isPredecessor -and $script:state.fault -eq 'image-revision') {
        $revision = $predecessorRevision
    }
    return [ordered]@{
        Id = $id
        Os = 'linux'
        Architecture = 'amd64'
        Config = [ordered]@{
            User = 'stratus'
            WorkingDir = '/app'
            Entrypoint = $null
            Cmd = @('stratus-run')
            Env = @('PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin')
            Labels = [ordered]@{ 'org.opencontainers.image.revision' = $revision }
        }
    }
}

function New-NetworkAttachment {
    param([ValidateSet('default', 'safety')][string] $Kind, [bool] $Running)
    $id = if ($Kind -eq 'default') { $defaultNetworkId } else { $safetyNetworkId }
    $gateway = if ($Kind -eq 'default') { '172.20.0.1' } else { '172.22.0.1' }
    $ip = if ($Kind -eq 'default') { '172.20.0.2' } else { '172.22.0.2' }
    $endpoint = if ($Kind -eq 'default') { 'd' * 64 } else { 'e' * 64 }
    $mac = if ($Kind -eq 'default') { '02:42:ac:14:00:02' } else { '02:42:ac:16:00:02' }
    if (-not $Running) {
        $gateway = ''
        $ip = ''
        $endpoint = ''
        $mac = ''
    }
    return [ordered]@{
        IPAMConfig = $null
        Links = $null
        Aliases = @('postgres', 'weatherpoly-postgres-1')
        MacAddress = $mac
        DriverOpts = $null
        GwPriority = 0
        NetworkID = $id
        EndpointID = $endpoint
        Gateway = $gateway
        IPAddress = $ip
        IPPrefixLen = if ($Running) { 16 } else { 0 }
        IPv6Gateway = ''
        GlobalIPv6Address = ''
        GlobalIPv6PrefixLen = 0
        DNSNames = @($postgresId.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1')
    }
}

function New-NetworkInspect {
    param([string] $Id)
    $isDefault = $Id -ceq $defaultNetworkId
    $name = if ($isDefault) { 'weatherpoly_default' } else { 'weatherpoly_safety-plane' }
    $composeName = if ($isDefault) { 'default' } else { 'safety-plane' }
    $subnet = if ($isDefault) { '172.20.0.0/16' } else { '172.22.0.0/16' }
    $gateway = if ($isDefault) { '172.20.0.1' } else { '172.22.0.1' }
    $containers = [ordered]@{}
    if ($script:state.postgres_state -eq 'running') {
        $attachment = New-NetworkAttachment `
            -Kind $(if ($isDefault) { 'default' } else { 'safety' }) -Running $true
        $containers[$postgresId] = [ordered]@{
            Name = 'weatherpoly-postgres-1'
            EndpointID = [string]$attachment.EndpointID
            MacAddress = [string]$attachment.MacAddress
            IPv4Address = "$($attachment.IPAddress)/16"
            IPv6Address = ''
        }
    }
    return [ordered]@{
        Name = $name
        Id = $Id
        Created = if ($isDefault) {
            '2026-07-05T17:30:21.808081932Z'
        }
        else {
            '2026-07-30T09:19:55.325597748Z'
        }
        Scope = 'local'
        Driver = 'bridge'
        EnableIPv4 = $true
        EnableIPv6 = $false
        Internal = $false
        Attachable = $false
        Ingress = $false
        ConfigOnly = $false
        IPAM = [ordered]@{
            Driver = 'default'
            Options = $null
            Config = @([ordered]@{
                Subnet = $subnet
                Gateway = $gateway
                IPRange = ''
                AuxiliaryAddresses = $null
            })
        }
        Labels = [ordered]@{
            'com.docker.compose.config-hash' = if ($isDefault) {
                '46e59c4f5c259be13fae41f47db52fd04daf6179514ee815f6a8a61ccd86aee9'
            }
            else {
                '85d9bb404d6e93d77b9c26b6862a7c2a52dd26aa3ba8eb092f28711a675a26a8'
            }
            'com.docker.compose.network' = $composeName
            'com.docker.compose.project' = 'weatherpoly'
            'com.docker.compose.version' = '2.38.2'
        }
        Options = [ordered]@{
            'com.docker.network.enable_ipv4' = 'true'
            'com.docker.network.enable_ipv6' = 'false'
        }
        ConfigFrom = [ordered]@{ Network = '' }
        Containers = $containers
    }
}

function New-PostgresInspect {
    $script:state.postgres_inspect_count += 1
    $running = $script:state.postgres_state -eq 'running'
    if ($script:state.fault -eq 'postgres-wrong-state') { $running = -not $running }
    $id = if ($script:state.fault -eq 'postgres-id') { 'f' * 64 } else { $postgresId }
    $restartCount = if ($script:state.fault -eq 'postgres-restart-count') { 1 } else { 0 }
    $startedAt = if ($running) {
        [string]$script:state.running_started_at
    }
    else {
        [string]$script:state.historical_started_at
    }
    if ($running -and $script:state.fault -eq 'postgres-final-drift' -and
        $script:state.snapshot_count -ge 2) {
        $startedAt = ([DateTimeOffset]::Parse($startedAt).AddSeconds(1)).ToString('o')
    }
    $defaultAttachment = New-NetworkAttachment -Kind default -Running $running
    $safetyAttachment = New-NetworkAttachment -Kind safety -Running $running
    if ($running -and $script:state.fault -in @('network-endpoint', 'network-final') -and
        ($script:state.fault -ne 'network-final' -or
            $script:state.snapshot_count -ge 2)) {
        $safetyAttachment.EndpointID = 'a' * 64
    }
    $networks = [ordered]@{
        weatherpoly_default = $defaultAttachment
        'weatherpoly_safety-plane' = $safetyAttachment
    }
    if ($script:state.fault -eq 'network-extra') {
        $networks.weatherpoly_extra = New-NetworkAttachment -Kind default -Running $running
    }
    if ($script:state.fault -eq 'network-missing') {
        [void]$networks.Remove('weatherpoly_safety-plane')
    }
    $mounts = @(
        [ordered]@{
            Type = 'bind'
            Source = Join-Path ([string]$script:configuration.root) `
                'ops\postgres\stratus-pg_hba.conf'
            Destination = '/etc/postgresql/stratus-pg_hba.conf'
            Mode = 'ro'
            RW = $false
            Propagation = 'rprivate'
        },
        [ordered]@{
            Type = 'volume'
            Name = 'weatherpoly_stratus-postgres'
            Driver = 'local'
            Source = '/var/lib/docker/volumes/weatherpoly_stratus-postgres/_data'
            Destination = '/var/lib/postgresql/data'
            Mode = 'rw'
            RW = $true
            Propagation = ''
        }
    )
    if ($script:state.fault -eq 'postgres-mount') { $mounts[1].Name = 'other-volume' }
    return [ordered]@{
        Id = $id
        Name = '/weatherpoly-postgres-1'
        Image = $postgresImageId
        RestartCount = $restartCount
        Config = [ordered]@{
            Image = $postgresImage
            Healthcheck = [ordered]@{
                Test = @('CMD-SHELL', 'pg_isready -U stratus -d stratus')
            }
        }
        State = [ordered]@{
            Status = if ($running) { 'running' } else { 'exited' }
            Running = $running
            Paused = $false
            Restarting = $false
            OOMKilled = $false
            Dead = $false
            Pid = if ($running) { 4321 } else { 0 }
            ExitCode = 0
            Error = ''
            StartedAt = $startedAt
            FinishedAt = if ($running) { '0001-01-01T00:00:00Z' } else {
                [string]$script:state.historical_finished_at
            }
            Health = if ($running) { [ordered]@{ Status = 'healthy' } } else { $null }
        }
        HostConfig = [ordered]@{
            RestartPolicy = [ordered]@{
                Name = [string]$script:state.restart_policy
                MaximumRetryCount = 0
            }
            NetworkMode = 'weatherpoly_default'
            PortBindings = [ordered]@{
                '5432/tcp' = @([ordered]@{ HostIp = '127.0.0.1'; HostPort = '5432' })
            }
        }
        NetworkSettings = [ordered]@{
            Networks = $networks
            Ports = if ($running) {
                [ordered]@{
                    '5432/tcp' = @([ordered]@{
                        HostIp = '127.0.0.1'
                        HostPort = '5432'
                    })
                }
            }
            else {
                [ordered]@{}
            }
        }
        Mounts = $mounts
    }
}

function Add-PostgresStartEvents {
    $volumeAttributes = [ordered]@{
        container = $postgresId
        destination = '/var/lib/postgresql/data'
        driver = 'local'
        propagation = ''
        'read/write' = 'true'
    }
    if ($script:state.fault -ne 'event-missing-volume') {
        if ($script:state.fault -eq 'event-volume-drift') {
            $volumeAttributes.destination = '/wrong'
        }
        [void](Add-Event -Type volume -Action mount `
            -ActorId 'weatherpoly_stratus-postgres' `
            -Attributes $volumeAttributes -TimeNano $null)
    }
    $networkEntries = @(
        [pscustomobject]@{ id = $defaultNetworkId; name = 'weatherpoly_default' },
        [pscustomobject]@{ id = $safetyNetworkId; name = 'weatherpoly_safety-plane' }
    )
    if ($script:state.fault -eq 'event-network-missing') {
        $networkEntries = @($networkEntries[0])
    }
    foreach ($network in $networkEntries) {
        $attributes = [ordered]@{
            container = $postgresId
            name = [string]$network.name
            type = 'bridge'
        }
        [void](Add-Event -Type network -Action connect -ActorId ([string]$network.id) `
            -Attributes $attributes -TimeNano $null)
    }
    if ($script:state.fault -eq 'event-network-extra') {
        [void](Add-Event -Type network -Action connect -ActorId ('f' * 64) `
            -Attributes ([ordered]@{ container=$postgresId; name='extra'; type='bridge' }) `
            -TimeNano $null)
    }
    $startNano = Add-ContainerEvent -Action start -Id $postgresId `
        -Attributes ([ordered]@{ name='weatherpoly-postgres-1'; image=$postgresImage })
    $milliseconds = [int64]([Math]::Floor(([double]$startNano) / 1000000.0))
    $script:state.running_started_at =
        [DateTimeOffset]::FromUnixTimeMilliseconds($milliseconds).ToString('o')
    if ($script:state.fault -eq 'start-deadline') {
        $prestart = Get-Content -LiteralPath ([string]$script:configuration.prestart) -Raw |
            ConvertFrom-Json
        $script:state.running_started_at = ([DateTimeOffset]::Parse(
            [string]$prestart.payload.accepted_at
        ).AddSeconds(301)).ToString('o')
    }
    if ($script:state.fault -eq 'event-second-start') {
        [void](Add-ContainerEvent -Action start -Id $postgresId `
            -Attributes ([ordered]@{ name='weatherpoly-postgres-1'; image=$postgresImage }))
    }
    if ($script:state.fault -eq 'event-lifecycle') {
        [void](Add-ContainerEvent -Action die -Id $postgresId `
            -Attributes ([ordered]@{ name='weatherpoly-postgres-1'; exitCode='0' }))
    }
    if ($script:state.fault -eq 'event-global') {
        [void](Add-Event -Type image -Action pull -ActorId $successorImageId `
            -Attributes ([ordered]@{}) -TimeNano $null)
    }
}

function Handle-DockerCore {
    param([string[]] $Arguments)
    $joined = $Arguments -join ' '
    $fault = [string]$script:state.fault
    if ($joined -ceq 'context show') { return New-Result -Stdout 'desktop-linux' }
    if ($Arguments.Count -eq 5 -and $Arguments[0] -ceq 'context' -and
        $Arguments[1] -ceq 'inspect') {
        return New-Result -Stdout (([ordered]@{
            Host = 'npipe:////./pipe/dockerDesktopLinuxEngine'
            SkipTLSVerify = $false
        } | ConvertTo-Json -Depth 6 -Compress))
    }
    if ($joined -ceq 'info --format {{json .}}') {
        return New-Result -Stdout (([ordered]@{
            ID='d98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
            ServerVersion='28.3.2'; Name='docker-desktop'
            DockerRootDir='/var/lib/docker'
            ClientInfo=[ordered]@{
                Plugins=@([ordered]@{
                    Name='compose'
                    Version='v2.38.2-desktop.1'
                    Path=[string]$script:configuration.compose
                })
            }
        } | ConvertTo-Json -Depth 6 -Compress))
    }
    if ($Arguments.Count -eq 5 -and $Arguments[0] -ceq 'image' -and
        $Arguments[1] -ceq 'inspect' -and $Arguments[3] -ceq '--format') {
        $json = New-ImageInspect -Reference $Arguments[2] |
            ConvertTo-Json -Depth 8 -Compress
        if ($fault -eq 'image-array-root' -and $Arguments[2] -ceq $successorImage) {
            $json = '[' + $json + ']'
        }
        return New-Result -Stdout $json
    }
    if ($Arguments.Count -ge 8 -and $Arguments[0] -ceq 'create' -and
        $Arguments -contains '--name') {
        $name = $Arguments[2]
        $isBarrier = $Arguments -contains '--label'
        if ($isBarrier) {
            $script:state.barrier_sequence += 1
            $script:state.barrier_create_count += 1
            $id = if ($script:state.barrier_sequence -eq 1) { '3' * 64 } else { '4' * 64 }
            $labelIndex = [Array]::IndexOf($Arguments, '--label') + 1
            $label = $Arguments[$labelIndex]
            Add-OwnedContainer -Id $id -Kind barrier -Name $name `
                -Image $Arguments[-1] -Label $label
            Wait-WitnessReady
            if ($fault -eq 'witness-early-exit' -and
                $script:state.barrier_sequence -eq 1) {
                [IO.File]::WriteAllText($env:STRATUS_FAKE_WITNESS_EXIT, 'exit', $utf8)
                $deadline = [DateTimeOffset]::UtcNow.AddSeconds(3)
                while (-not (Test-Path -LiteralPath $env:STRATUS_FAKE_WITNESS_EXITED) -and
                    [DateTimeOffset]::UtcNow -lt $deadline) {
                    Start-Sleep -Milliseconds 10
                }
            }
            elseif (-not ($fault -eq 'missing-initial-barrier' -and
                $script:state.barrier_sequence -eq 1)) {
                $parts = $label.Split('=', 2)
                $attrs = [ordered]@{
                    name = $name
                    'stratus.successor.event-barrier' = $parts[1]
                }
                [void](Add-ContainerEvent -Action create -Id $id -Attributes $attrs)
            }
            if ($script:state.barrier_sequence -eq 1 -and
                $fault -eq 'watchdog-lease-race') {
                Add-WatchdogRaceArtifact -Suffix '.watchdog-lease.json' `
                    -Value '{"format":"forged-lease"}'
            }
            if ($script:state.barrier_sequence -eq 1 -and
                $fault -eq 'watchdog-terminal-race') {
                Add-WatchdogRaceArtifact -Suffix '.watchdog-terminal.json' `
                    -Value '{"format":"forged-terminal"}'
            }
            return New-Result -Stdout $id
        }
        $script:state.proof_sequence += 1
        $script:state.proof_create_count += 1
        $id = if ($script:state.proof_sequence % 2 -eq 1) { '1' * 64 } else { '2' * 64 }
        Add-OwnedContainer -Id $id -Kind proof -Name $name -Image $Arguments[-1] -Label ''
        return New-Result -Stdout $id
    }
    $owned = @()
    if ($Arguments.Count -ge 2) { $owned = @(Get-OwnedContainer -Id $Arguments[1]) }
    if ($Arguments.Count -eq 4 -and $Arguments[0] -ceq 'inspect' -and
        $owned.Count -eq 1) {
        $container = $owned[0]
        if ($container.kind -eq 'proof' -and $fault -eq 'proof-inspect-failure') {
            return New-Result -ExitCode 71 -Stderr 'synthetic stopped-proof inspect failure'
        }
        if ($container.kind -eq 'proof' -and $fault -eq 'proof-inspect-malformed') {
            return New-Result -Stdout '{malformed-stopped-proof}'
        }
        $inspect = [ordered]@{
            Id = [string]$container.id
            Name = '/' + [string]$container.name
            Image = [string]$container.image
            State = [ordered]@{ Status='created'; Running=$false; Pid=0 }
            HostConfig = [ordered]@{ NetworkMode='none' }
            Config = [ordered]@{ Labels=[ordered]@{} }
            Mounts = @()
        }
        if ($container.kind -eq 'barrier') {
            $labelParts = ([string]$container.label).Split('=', 2)
            $inspect.Config.Labels[$labelParts[0]] = $labelParts[1]
        }
        elseif ($fault -eq 'proof-inspect-identity') {
            $inspect.Image = if ($container.image -ceq $successorImageId) {
                $predecessorImageId
            } else { $successorImageId }
        }
        return New-Result -Stdout ($inspect | ConvertTo-Json -Depth 8 -Compress)
    }
    if ($Arguments.Count -eq 3 -and $Arguments[0] -ceq 'cp') {
        if ($Arguments[1] -notmatch '^([0-9a-f]{64}):/app/(.+)$') {
            return New-Result -ExitCode 96 -Stderr 'malformed fake docker cp source'
        }
        $container = @(Get-OwnedContainer -Id ([string]$Matches[1]))[0]
        $path = ([string]$Matches[2]).Replace('\', '/')
        $destination = $Arguments[2]
        if ($path -ceq 'migrations') {
            foreach ($number in 1..47) {
                $migrationPath = 'migrations/versions/{0:d4}_fixture.py' -f $number
                $target = Join-Path $destination `
                    $migrationPath.Replace('/', [IO.Path]::DirectorySeparatorChar)
                [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
                $content = "reviewed migration contract: $migrationPath`n"
                if ($container.image -ceq $successorImageId -and
                    $fault -eq 'image-contract-migration' -and $number -eq 47) {
                    $content = "drifted successor migration byte`n"
                }
                [IO.File]::WriteAllText($target, $content, $utf8)
            }
            return New-Result
        }
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
        $content = "reviewed image contract: $path`n"
        if ($container.image -ceq $successorImageId -and
            $fault -eq 'image-contract-source' -and
            $path -ceq 'src/stratus/config.py') {
            $content = "drifted successor source byte`n"
        }
        [IO.File]::WriteAllText($destination, $content, $utf8)
        return New-Result
    }
    if ($Arguments.Count -eq 2 -and $Arguments[0] -ceq 'rm' -and
        $owned.Count -eq 1) {
        $container = $owned[0]
        Remove-OwnedContainer -Id $Arguments[1]
        if ($container.kind -eq 'proof') { $script:state.proof_remove_count += 1 }
        else {
            $script:state.barrier_remove_count += 1
            $phase = if ([string]$container.name -like '*prestart*') { 'prestart' } else { 'final' }
            if (-not (($fault -eq 'missing-final-barrier' -and $phase -eq 'final') -or
                ($fault -eq 'missing-initial-barrier' -and $phase -eq 'prestart'))) {
                $parts = ([string]$container.label).Split('=', 2)
                $attrs = [ordered]@{
                    name = [string]$container.name
                    'stratus.successor.event-barrier' = $parts[1]
                }
                [void](Add-ContainerEvent -Action destroy -Id ([string]$container.id) `
                    -Attributes $attrs)
            }
            if ($fault -eq 'witness-eof-before-seal' -and $phase -eq 'final') {
                [IO.File]::WriteAllText($env:STRATUS_FAKE_WITNESS_EXIT, 'exit', $utf8)
                $deadline = [DateTimeOffset]::UtcNow.AddSeconds(3)
                while (-not (Test-Path -LiteralPath $env:STRATUS_FAKE_WITNESS_EXITED) -and
                    [DateTimeOffset]::UtcNow -lt $deadline) {
                    Start-Sleep -Milliseconds 10
                }
            }
            if ($fault -eq 'witness-truncated' -and $phase -eq 'final') {
                [IO.File]::AppendAllText(
                    $env:STRATUS_FAKE_EVENTS, '{"partial"', $utf8
                )
                Start-Sleep -Milliseconds 100
            }
            if ($fault -eq 'prestart-tamper' -and $phase -eq 'final') {
                [IO.File]::AppendAllText(
                    [string]$script:configuration.prestart, " `n", $utf8
                )
            }
            if ($fault -eq 'repair-tamper' -and $phase -eq 'final') {
                [IO.File]::AppendAllText(
                    [string]$script:configuration.repair, " `n", $utf8
                )
            }
            if ($fault -eq 'watchdog-commit-forged' -and $phase -eq 'final') {
                Add-WatchdogRaceArtifact -Suffix '.watchdog-commit.json' `
                    -Value '{"format":"forged-commit"}'
            }
        }
        return New-Result -Stdout $Arguments[1]
    }
    if ($Arguments.Count -gt 0 -and $Arguments[0] -ceq 'run') {
        if ($joined -like '*pg_restore --list /evidence/stratus.dump*') {
            if ($fault -eq 'catalog-probe') { return New-Result -Stdout ('0' * 64) }
            return New-Result -Stdout 'cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65'
        }
        $script:state.forbidden_mutation_count += 1
        return New-Result -ExitCode 97 -Stderr 'unapproved docker run'
    }
    if ($Arguments.Count -eq 5 -and $Arguments[0] -ceq 'network' -and
        $Arguments[1] -ceq 'inspect') {
        $script:state.network_inspect_count += 1
        return New-Result -Stdout ((New-NetworkInspect -Id $Arguments[2]) |
            ConvertTo-Json -Depth 12 -Compress)
    }
    if ($Arguments.Count -eq 4 -and $Arguments[0] -ceq 'inspect' -and
        $Arguments[1] -ceq $postgresId -and $Arguments[2] -ceq '--format') {
        if ($Arguments[3] -ceq '{{json .State.Health}}') {
            if ($script:state.health_exec_count -eq 0) {
                Add-ExecTriplet -Kind health
                [void](Add-ContainerEvent -Action 'health_status: healthy' -Id $postgresId `
                    -Attributes ([ordered]@{
                        name='weatherpoly-postgres-1'
                        image=$postgresImage
                    }))
            }
            return New-Result -Stdout '{"Status":"healthy"}'
        }
        $json = New-PostgresInspect | ConvertTo-Json -Depth 14 -Compress
        if ($fault -eq 'postgres-array-root') { $json = '[' + $json + ']' }
        return New-Result -Stdout $json
    }
    if ($Arguments.Count -eq 3 -and $Arguments[0] -ceq 'update' -and
        $Arguments[1] -ceq '--restart=no') {
        $script:state.update_count += 1
        $script:state.mutations = @($script:state.mutations) + @(
            "update --restart=no $($Arguments[2])"
        )
        if ($Arguments[2] -cne $postgresId) {
            $script:state.forbidden_mutation_count += 1
            return New-Result -ExitCode 97 -Stderr 'wrong restart-policy update target'
        }
        $script:state.restart_policy = 'no'
        [void](Add-ContainerEvent -Action update -Id $postgresId `
            -Attributes ([ordered]@{
                name='weatherpoly-postgres-1'
                image=$postgresImage
            }))
        return New-Result -Stdout $postgresId
    }
    if ($Arguments.Count -eq 2 -and $Arguments[0] -ceq 'start') {
        $script:state.start_count += 1
        $script:state.mutations = @($script:state.mutations) + @("start $($Arguments[1])")
        if ($Arguments[1] -cne $postgresId) {
            $script:state.forbidden_mutation_count += 1
            return New-Result -ExitCode 97 -Stderr 'wrong start target'
        }
        if ($fault -eq 'start-failure-before-running') {
            return New-Result -ExitCode 1 -Stderr 'synthetic start failure'
        }
        $script:state.postgres_state = 'running'
        Add-PostgresStartEvents
        if ($fault -eq 'backup-live') {
            [IO.File]::AppendAllText(
                (Join-Path ([string]$script:configuration.backup) 'stratus.dump'),
                "`npost-start backup drift", $utf8
            )
        }
        elseif ($fault -eq 'restore-live') {
            [IO.File]::AppendAllText(
                (Join-Path ([string]$script:configuration.backup) `
                    'restore-attestation.json'),
                " `n", $utf8
            )
        }
        if ($fault -eq 'start-return-wrong') { return New-Result -Stdout ('a' * 64) }
        return New-Result -Stdout $postgresId
    }
    if ($Arguments.Count -eq 4 -and $Arguments[0] -ceq 'stop' -and
        $Arguments[1] -ceq '--time') {
        $script:state.stop_count += 1
        $script:state.mutations = @($script:state.mutations) + @(
            "stop --time $($Arguments[2]) $($Arguments[3])"
        )
        if ($Arguments[2] -cne '15' -or $Arguments[3] -cne $postgresId) {
            $script:state.forbidden_mutation_count += 1
            return New-Result -ExitCode 97 -Stderr 'wrong rollback stop boundary'
        }
        $script:state.postgres_state = 'stopped'
        if ($fault -notin @(
            'none', 'watchdog-publisher-cleanup', 'watchdog-lease-drift',
            'writer-final'
        )) {
            Poison-WatchdogHeartbeatAfterContainment
        }
        return New-Result -Stdout $postgresId
    }
    return $null
}
'@
}

function Get-FakeServerSourcePart3 {
    return @'
function Get-HbaInventory {
    return [string[]]@(
        'PATH|/etc/postgresql/stratus-pg_hba.conf',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|19|local|stratus|stratus_app_752875213a111f12d876fef68bbf8259|||scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|20|local|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|||scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|21|local|all|/^stratus_app_[0-9a-f]{32}$|||reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|22|local|all|/^stratus_safety_app_[0-9a-f]{32}$|||reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|24|host|stratus|stratus_app_752875213a111f12d876fef68bbf8259|0.0.0.0|0.0.0.0|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|25|host|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|0.0.0.0|0.0.0.0|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|26|host|all|/^stratus_app_[0-9a-f]{32}$|0.0.0.0|0.0.0.0|reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|27|host|all|/^stratus_safety_app_[0-9a-f]{32}$|0.0.0.0|0.0.0.0|reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|29|host|stratus|stratus_app_752875213a111f12d876fef68bbf8259|::|::|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|30|host|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|::|::|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|31|host|all|/^stratus_app_[0-9a-f]{32}$|::|::|reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|32|host|all|/^stratus_safety_app_[0-9a-f]{32}$|::|::|reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|36|local|all|all|||scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|37|host|all|all|0.0.0.0|0.0.0.0|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|38|host|all|all|::|::|scram-sha-256||'
    )
}

function New-LiveSnapshot {
    $script:state.snapshot_count += 1
    $clock = [DateTimeOffset]::UtcNow.AddMilliseconds($script:state.snapshot_count)
    if ($script:state.fault -eq 'snapshot-clock-window' -and
        $script:state.snapshot_count -gt 1) {
        $clock = $clock.AddSeconds(61)
    }
    $snapshot = [ordered]@{
        database_clock = $clock.ToString('o')
        schema_head_count = 1
        schema_head = '20260730_0047'
        indexes_exact = $true
        functions_exact = $true
        lease_table_count = 1
        lease_rows = 0
        active_lease_rows = 0
        runtime_boundary = $true
        safety_boundary = $true
        control_triggers_exact = $true
        commit_triggers_exact = $true
        identity_triggers_exact = $true
        global_rows = 1
        global_mode = 'paper'
        global_kill_switch = $true
        global_live_confirmation = $null
        polymarket_rows = 1
        polymarket_enabled = $true
        polymarket_mode = 'paper'
        polymarket_kill_switch = $true
        polymarket_live_confirmation = $null
        binance_rows = 1
        binance_enabled = $true
        binance_mode = 'backtest'
        binance_kill_switch = $true
        binance_live_confirmation = $null
        pending_commands = 0
        live_orders = 0
        live_positions = 0
        live_intents = 0
        live_fill_legs = 0
        live_enrollments = 0
        live_outcomes = 0
        runtime_sessions = 0
        idle_in_transaction_sessions = 0
        lock_waiters = 0
        active_pg_dump = 0
        bootstrap_audit_exact = $true
        pending_signals = 0
        live_open_or_pending_orders = 0
        fence_armed = $true
        active_lease_exists = $false
        execution_authorized = $false
    }
    switch ([string]$script:state.fault) {
        'snapshot-schema-head' { $snapshot.schema_head = '20260730_0046' }
        'snapshot-index' { $snapshot.indexes_exact = $false }
        'snapshot-live-row' { $snapshot.live_orders = 1 }
        'snapshot-boolean-string' { $snapshot.global_kill_switch = 'true' }
        'snapshot-function-source' { $snapshot.functions_exact = $false }
        'snapshot-function-noop' { $snapshot.functions_exact = $false }
    }
    if ([string]$script:state.fault -like 'snapshot-index-*') {
        $snapshot.indexes_exact = $false
    }
    return $snapshot
}

function Get-CombinedHbaProof {
    $request = [DateTimeOffset]::UtcNow
    $before = $request.AddSeconds(-10)
    $after = $request.AddMilliseconds(10)
    $postmaster = [DateTimeOffset]::Parse([string]$script:state.running_started_at)
    $backendStart = $request.AddSeconds(-2)
    $script:state['hba_conf_after'] = $after.ToString('o')
    $script:state['hba_postmaster'] = $postmaster.ToString('o')
    $lines = [Collections.Generic.List[string]]::new()
    $lines.Add(('BASE|{0}|{1}|{2}|741|{3}|/etc/postgresql/stratus-pg_hba.conf' -f
        $before.ToString('o'), $request.ToString('o'), $postmaster.ToString('o'),
        $backendStart.ToString('o')))
    $reload = if ($script:state.fault -eq 'hba-no-ack') { 'false' } else { 'true' }
    $lines.Add("RELOAD|$reload")
    foreach ($number in 1..40) {
        $sampleLoad = if ($script:state.fault -eq 'hba-timeout') { $before } else { $after }
        $sampledAt = $request.AddMilliseconds(20 + $number)
        $lines.Add(('POLL|{0}|{1}|{2}|{3}|741|{4}|/etc/postgresql/stratus-pg_hba.conf' -f
            $number, $sampleLoad.ToString('o'), $sampledAt.ToString('o'),
            $postmaster.ToString('o'), $backendStart.ToString('o')))
    }
    $inventory = @(Get-HbaInventory)
    if ($script:state.fault -eq 'hba-inventory') {
        $inventory = @($inventory[0..14])
    }
    foreach ($line in $inventory) { $lines.Add($line) }
    return $lines.ToArray()
}

function Get-FreshHbaProof {
    $script:state.hba_fresh_count += 1
    $conf = [DateTimeOffset]::Parse([string]$script:state.hba_conf_after)
    if ($script:state.fault -eq 'hba-final-generation' -and
        $script:state.hba_fresh_count -gt 1) {
        $conf = $conf.AddSeconds(1)
    }
    $postmaster = [DateTimeOffset]::Parse([string]$script:state.hba_postmaster)
    $backendStart = $conf.AddSeconds(1 + $script:state.hba_fresh_count)
    $backendPid = 800 + $script:state.hba_fresh_count
    $lines = [Collections.Generic.List[string]]::new()
    $lines.Add(('META|{0}|{1}|{2}|{3}|/etc/postgresql/stratus-pg_hba.conf' -f
        $conf.ToString('o'), $postmaster.ToString('o'), $backendPid,
        $backendStart.ToString('o')))
    $inventory = @(Get-HbaInventory)
    if ($script:state.fault -eq 'hba-final-inventory' -and
        $script:state.hba_fresh_count -gt 1) {
        $inventory = @($inventory[0..14])
    }
    foreach ($line in $inventory) { $lines.Add($line) }
    return $lines.ToArray()
}

function Handle-Compose {
    param([string[]] $Arguments)
    if ($script:state.fault -eq 'compose-command-failure') {
        return New-Result -ExitCode 73 -Stderr 'synthetic Compose proof failure'
    }
    $remaining = @($Arguments[9..($Arguments.Count - 1)])
    $joined = $remaining -join ' '
    if ($joined -ceq '--profile release config --format json') {
        $command = if ($script:state.fault -eq 'compose-command-scalar') {
            'alembic'
        } else { @('alembic', 'upgrade', 'head') }
        $required = $script:state.fault -ne 'compose-dependency'
        $model = [ordered]@{
            services = [ordered]@{
                migrate = [ordered]@{
                    image = if ($script:state.fault -eq 'compose-image') {
                        $predecessorImage
                    } else { $successorImage }
                    command = $command
                    restart = 'no'
                    depends_on = [ordered]@{
                        postgres = [ordered]@{
                            condition = 'service_healthy'
                            required = $required
                        }
                    }
                    environment = [ordered]@{
                        MIGRATION_DATABASE_URL = 'postgresql+asyncpg://stratus:DO-NOT-PERSIST-successor-gate-secret-7a6f62d5@postgres:5432/stratus'
                        RELEASE_IMAGE_DIGEST = $successorImageId.Substring(7)
                    }
                }
            }
        }
        return New-Result -Stdout ($model | ConvertTo-Json -Depth 10 -Compress)
    }
    if ($joined -ceq 'ps --status running --services') {
        $script:state.compose_topology_count += 1
        $writer = $script:state.fault -eq 'writer-prestart' -or
            ($script:state.fault -eq 'writer-running' -and
                $script:state.postgres_state -eq 'running') -or
            ($script:state.fault -eq 'writer-final' -and
                $script:state.postgres_state -eq 'running' -and
                $script:state.snapshot_count -ge 2)
        if ($writer) { return New-Result -Stdout @('postgres', 'api') }
        if ($script:state.postgres_state -eq 'running') {
            return New-Result -Stdout 'postgres'
        }
        return New-Result
    }
    if ($remaining -contains 'up' -or $remaining -contains 'run' -or
        $remaining -contains 'start' -or $remaining -contains 'restart') {
        $script:state.compose_mutation_count += 1
        $script:state.forbidden_mutation_count += 1
    }
    return New-Result -ExitCode 99 -Stderr "unexpected fake Compose invocation: $joined"
}

function Handle-PostgresExec {
    param([string[]] $Arguments)
    if ($script:state.fault -eq 'watchdog-lease-drift' -and
        -not $script:state.watchdog_fault_injected) {
        [IO.File]::AppendAllText(
            ([string]$script:configuration.prestart + '.watchdog-lease.json'),
            " `n", $utf8
        )
        $script:state.watchdog_fault_injected = $true
    }
    if ($script:state.fault -eq 'watchdog-heartbeat-lock') {
        Lock-WatchdogHeartbeatMalformed
    }
    $joined = $Arguments -join ' '
    $script:state.database_operations = @($script:state.database_operations) + @(
        if ($Arguments[2] -ceq 'sha256sum') { 'hba-sha' }
        elseif ($joined -like '*BASE|*') { 'hba-reload-session' }
        elseif ($joined -like '*META|*') { 'hba-fresh-session' }
        else { 'snapshot' }
    )
    $eventCommand = if ($Arguments[2] -ceq 'sha256sum') {
        'sha256sum /etc/postgresql/stratus-pg_hba.conf'
    }
    elseif ($joined -like '*BASE|*') {
        'psql SELECT pg_reload_conf()::text BASE|'
    }
    elseif ($joined -like '*META|*') {
        "psql SELECT 'META|'"
    }
    else {
        'psql BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'
    }
    Add-ExecTriplet -Kind gate -Command $eventCommand
    if (-not $script:state.Contains('extra_exec_emitted')) {
        $script:state['extra_exec_emitted'] = $false
    }
    if (-not $script:state.extra_exec_emitted -and
        $script:state.fault -eq 'event-extra-exec') {
        Add-ExecTriplet -Kind gate
        $script:state.extra_exec_emitted = $true
    }
    if (-not $script:state.extra_exec_emitted -and
        $script:state.fault -eq 'event-unowned-exec') {
        Add-ExecTriplet -Kind unowned
        $script:state.extra_exec_emitted = $true
    }
    if ($Arguments[2] -ceq 'sha256sum') {
        $script:state.hba_sha_count += 1
        $hash = $fixedHba
        if (($script:state.fault -eq 'hba-mounted' -and
            $script:state.hba_sha_count -eq 1) -or
            ($script:state.fault -eq 'hba-final-bytes' -and
            $script:state.hba_sha_count -ge 3)) {
            $hash = '0' * 64
        }
        return New-Result -Stdout "$hash  /etc/postgresql/stratus-pg_hba.conf"
    }
    if ($joined -like '*BASE|*') {
        return New-Result -Stdout (Get-CombinedHbaProof)
    }
    if ($joined -like '*META|*') {
        return New-Result -Stdout (Get-FreshHbaProof)
    }
    $snapshot = New-LiveSnapshot
    $json = $snapshot | ConvertTo-Json -Depth 10 -Compress
    if ($script:state.fault -eq 'snapshot-array-root') { $json = '[' + $json + ']' }
    if ($script:state.fault -eq 'snapshot-malformed') { $json = '{malformed-json}' }
    return New-Result -Stdout $json
}

function Handle-Docker {
    param([string[]] $Arguments)
    $script:state.docker_call_count += 1
    if ($Arguments.Count -gt 0 -and $Arguments[0] -ceq 'compose') {
        return Handle-Compose -Arguments $Arguments
    }
    if (($Arguments -join ' ') -ceq 'ps -q --no-trunc') {
        if ($script:state.fault -eq 'watchdog-publisher-cleanup' -and
            -not $script:state.watchdog_fault_injected -and
            (Test-Path -LiteralPath (
                [string]$script:configuration.prestart + '.watchdog-commit.json'
            ))) {
            Lock-WatchdogHeartbeatPublisher
            $script:state.watchdog_publisher_cleanup_delay_count += 1
            Start-Sleep -Milliseconds 3500
        }
        $script:state.global_topology_count += 1
        $writer = $script:state.fault -eq 'writer-prestart' -or
            ($script:state.fault -eq 'writer-running' -and
                $script:state.postgres_state -eq 'running') -or
            ($script:state.fault -eq 'writer-final' -and
                $script:state.postgres_state -eq 'running' -and
                $script:state.snapshot_count -ge 2)
        if ($writer) { return New-Result -Stdout @($postgresId, ('a' * 64)) }
        if ($script:state.postgres_state -eq 'running') {
            return New-Result -Stdout $postgresId
        }
        return New-Result
    }
    if ($Arguments.Count -ge 3 -and $Arguments[0] -ceq 'exec' -and
        $Arguments[1] -ceq $postgresId) {
        return Handle-PostgresExec -Arguments $Arguments
    }
    $result = Handle-DockerCore -Arguments $Arguments
    if ($null -ne $result) { return $result }
    if ($Arguments.Count -gt 0 -and $Arguments[0] -cin @(
        'restart', 'kill', 'update', 'rename'
    )) {
        if ($Arguments[0] -eq 'restart') { $script:state.restart_count += 1 }
        if ($Arguments[0] -eq 'kill') { $script:state.kill_count += 1 }
        $script:state.forbidden_mutation_count += 1
        $script:state.mutations = @($script:state.mutations) + @($Arguments -join ' ')
    }
    return New-Result -ExitCode 100 -Stderr (
        'Unexpected fake Docker invocation: ' + ($Arguments -join ' ')
    )
}

[IO.File]::WriteAllText($env:STRATUS_FAKE_SERVER_READY, 'ready', $utf8)
while ($true) {
    $pipe = [IO.Pipes.NamedPipeServerStream]::new(
        $env:STRATUS_FAKE_PIPE,
        [IO.Pipes.PipeDirection]::InOut,
        1,
        [IO.Pipes.PipeTransmissionMode]::Byte,
        [IO.Pipes.PipeOptions]::None
    )
    try {
        $pipe.WaitForConnection()
        $reader = [IO.BinaryReader]::new($pipe, [Text.Encoding]::UTF8, $true)
        $writer = [IO.BinaryWriter]::new($pipe, [Text.Encoding]::UTF8, $true)
        try {
            $tool = $reader.ReadString()
            $argumentCount = $reader.ReadInt32()
            $arguments = [Collections.Generic.List[string]]::new()
            foreach ($index in 1..$argumentCount) { $arguments.Add($reader.ReadString()) }
            Import-Configuration
            $script:state['last_tool'] = [string]$tool
            $script:state['last_arguments'] = @($arguments.ToArray())
            try {
                $result = if ($tool -ceq 'git') {
                    Handle-Git -Arguments $arguments.ToArray()
                }
                elseif ($tool -ceq 'docker') {
                    Handle-Docker -Arguments $arguments.ToArray()
                }
                else {
                    New-Result -ExitCode 124 -Stderr "unexpected fake tool: $tool"
                }
            }
            catch {
                $script:state['last_server_error'] = [string]$_.Exception.Message
                $result = New-Result -ExitCode 123 -Stderr $_.Exception.Message
            }
            Export-State
            $stdout = @($result.stdout)
            $stderr = @($result.stderr)
            $writer.Write([int]$result.exit_code)
            $writer.Write([int]$stdout.Count)
            foreach ($line in $stdout) { $writer.Write([string]$line) }
            $writer.Write([int]$stderr.Count)
            foreach ($line in $stderr) { $writer.Write([string]$line) }
            $writer.Flush()
        }
        finally {
            $reader.Dispose()
            $writer.Dispose()
        }
    }
    finally {
        $pipe.Dispose()
    }
}
'@
}

function Get-FakeServerSource {
    return @(
        Get-FakeServerSourcePart1
        Get-FakeServerSourcePart2
        Get-FakeServerSourcePart3
    ) -join "`n"
}

function Start-FakeRuntime {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][string] $FixtureRoot
    )

    $shim = Join-Path $LiteralPath 'shim'
    [void](New-Item -ItemType Directory -Path $shim)
    $dockerExe = Join-Path $shim 'docker.exe'
    $gitExe = Join-Path $shim 'git.exe'
    $composeExe = Join-Path $shim 'docker-compose.exe'
    $sourcePath = Join-Path $LiteralPath 'fake-cli.cs'
    Write-Utf8NoBom -LiteralPath $sourcePath -Value (Get-FakeCliSource)
    $compiler = Join-Path $env:WINDIR `
        'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
        $compiler = Join-Path $env:WINDIR `
            'Microsoft.NET\Framework\v4.0.30319\csc.exe'
    }
    $compilerOutput = @(& $compiler /nologo /target:exe "/out:$dockerExe" $sourcePath 2>&1)
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $dockerExe -PathType Leaf)) {
        throw "The isolated fake CLI did not compile: $($compilerOutput -join ' ')"
    }
    Copy-Item -LiteralPath $dockerExe -Destination $gitExe
    Copy-Item -LiteralPath $dockerExe -Destination $composeExe

    $runtime = [pscustomobject]@{
        root = $LiteralPath
        shim = $shim
        docker = $dockerExe
        git = $gitExe
        compose = $composeExe
        gate = Join-Path $LiteralPath 'Assert-StratusPostMigrationSuccessorGate.ps1'
        watchdog = Join-Path $FixtureRoot `
            'scripts\Invoke-StratusSuccessorTransitionWatchdog.ps1'
        server_script = Join-Path $LiteralPath 'fake-server.ps1'
        config = Join-Path $LiteralPath 'fake-config.json'
        result = Join-Path $LiteralPath 'fake-result.json'
        events = Join-Path $LiteralPath 'fake-events.jsonl'
        witness_ready = Join-Path $LiteralPath 'witness.ready'
        witness_exit = Join-Path $LiteralPath 'witness.exit'
        witness_exited = Join-Path $LiteralPath 'witness.exited'
        server_ready = Join-Path $LiteralPath 'server.ready'
        pipe = 'stratus-successor-test-' + [guid]::NewGuid().ToString('N')
        process = $null
    }
    $fakeExecutableSha256 = (
        Microsoft.PowerShell.Utility\Get-FileHash `
            -LiteralPath $dockerExe -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    [void](New-Item -ItemType Directory -Path (
        [IO.Path]::GetDirectoryName($runtime.watchdog)
    ) -Force)
    $pinnedWatchdogSource = $watchdogSource.Replace(
        '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d',
        $fakeExecutableSha256
    )
    if ($pinnedWatchdogSource -ceq $watchdogSource) {
        throw 'The isolated watchdog Docker executable pin was not replaced.'
    }
    Write-Utf8NoBom -LiteralPath $runtime.watchdog -Value $pinnedWatchdogSource
    $pinnedWatchdogSha256 = (
        Microsoft.PowerShell.Utility\Get-FileHash `
            -LiteralPath $runtime.watchdog -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $pinnedGateSource = $gateSource.Replace(
        '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d',
        $fakeExecutableSha256
    ).Replace(
        'fec691d80fccc35fcc309fbc9f720536c1d795b8a562ec169f28c9923da9600f',
        $fakeExecutableSha256
    ).Replace(
        '8482829f77d14da8dfdfd0b553847946cee5161c90312219cf4f47d1f132d2bf',
        $fakeExecutableSha256
    ).Replace(
        $watchdogSourceSha256,
        $pinnedWatchdogSha256
    )
    if (-not $pinnedGateSource.Contains($pinnedWatchdogSha256)) {
        throw 'The isolated core watchdog program pin was not replaced.'
    }
    Write-Utf8NoBom -LiteralPath $runtime.gate -Value $pinnedGateSource
    Write-Utf8NoBom -LiteralPath $runtime.server_script -Value (Get-FakeServerSource)
    $env:STRATUS_FAKE_PIPE = $runtime.pipe
    $env:STRATUS_FAKE_CONFIG = $runtime.config
    $env:STRATUS_FAKE_RESULT = $runtime.result
    $env:STRATUS_FAKE_EVENTS = $runtime.events
    $env:STRATUS_FAKE_WITNESS_READY = $runtime.witness_ready
    $env:STRATUS_FAKE_WITNESS_EXIT = $runtime.witness_exit
    $env:STRATUS_FAKE_WITNESS_EXITED = $runtime.witness_exited
    $env:STRATUS_FAKE_SERVER_READY = $runtime.server_ready
    $env:PATH = $shim + [IO.Path]::PathSeparator + $env:PATH

    $engineName = if ($PSVersionTable.PSEdition -eq 'Desktop') {
        'powershell.exe'
    }
    else {
        'pwsh.exe'
    }
    $engine = Join-Path $PSHOME $engineName
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $engine
    $startInfo.Arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' +
        $runtime.server_script.Replace('"', '\"') + '"'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $runtime.process = [Diagnostics.Process]::Start($startInfo)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $runtime.server_ready)) {
        $runtime.process.Refresh()
        if ($runtime.process.HasExited) {
            throw 'The isolated fake CLI server exited during startup.'
        }
        if ([DateTimeOffset]::UtcNow -ge $deadline) {
            throw 'The isolated fake CLI server did not become ready.'
        }
        Start-Sleep -Milliseconds 20
    }
    return $runtime
}

function Stop-FakeRuntime {
    param([AllowNull()][object] $Runtime)
    if ($null -eq $Runtime -or $null -eq $Runtime.process) {
        return
    }
    $Runtime.process.Refresh()
    if (-not $Runtime.process.HasExited) {
        $Runtime.process.Kill()
        [void]$Runtime.process.WaitForExit(5000)
    }
    $Runtime.process.Dispose()
}

function Add-FakeExecutableTrustedHashes {
    param([Parameter(Mandatory = $true)][object] $Runtime)
    $global:SuccessorTrustedHashes[
        [IO.Path]::GetFullPath($Runtime.docker).ToLowerInvariant()
    ] = '388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d'
    $global:SuccessorTrustedHashes[
        [IO.Path]::GetFullPath($Runtime.git).ToLowerInvariant()
    ] = 'fec691d80fccc35fcc309fbc9f720536c1d795b8a562ec169f28c9923da9600f'
    $global:SuccessorTrustedHashes[
        [IO.Path]::GetFullPath($Runtime.compose).ToLowerInvariant()
    ] = '8482829f77d14da8dfdfd0b553847946cee5161c90312219cf4f47d1f132d2bf'
}

function Get-CanonicalFixtureHash {
    param([Parameter(Mandatory = $true)][object] $Value, [int] $Depth = 20)
    return Get-TextSha256 -Value ($Value | ConvertTo-Json -Depth $Depth -Compress)
}

function New-RepairEngineFixture {
    param([Parameter(Mandatory = $true)][object] $Runtime)
    $body = [ordered]@{
        docker_executable_path = [IO.Path]::GetFullPath($Runtime.docker)
        docker_executable_sha256 = (
            Microsoft.PowerShell.Utility\Get-FileHash `
                -LiteralPath $Runtime.docker -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        docker_executable_bytes = [int64](
            Microsoft.PowerShell.Management\Get-Item -LiteralPath $Runtime.docker
        ).Length
        context = 'desktop-linux'
        endpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
        skip_tls_verify = $false
        engine_id = 'd98e03f5-06d7-4af4-9f21-b6f7a70ebf68'
        server_version = '28.3.2'
        engine_name = 'docker-desktop'
        docker_root_dir = '/var/lib/docker'
    }
    $contract = @(
        [string]$body.docker_executable_path,
        [string]$body.docker_executable_sha256,
        [string]$body.docker_executable_bytes,
        [string]$body.context,
        [string]$body.endpoint,
        ([bool]$body.skip_tls_verify).ToString().ToLowerInvariant(),
        [string]$body.engine_id,
        [string]$body.server_version,
        [string]$body.engine_name,
        [string]$body.docker_root_dir
    ) -join "`0"
    $body.contract_sha256 = Get-TextSha256 -Value ($contract + "`n")
    return $body
}

function New-RepairAttachmentFixture {
    param([Parameter(Mandatory = $true)][string] $Name)
    $body = [ordered]@{
        name = $Name
        network_id = if ($Name -ceq 'weatherpoly_default') {
            $defaultNetworkId
        }
        else {
            $safetyNetworkId
        }
        gateway_priority = 0
        aliases = @('postgres', 'weatherpoly-postgres-1')
        dns_names = @($postgresId.Substring(0, 12), 'postgres', 'weatherpoly-postgres-1')
        endpoint_id = ''
        gateway = ''
        ip_address = ''
        ip_prefix_length = 0
        ipv6_gateway = ''
        global_ipv6_address = ''
        global_ipv6_prefix_length = 0
        mac_address = ''
    }
    $body.canonical_sha256 = Get-CanonicalFixtureHash -Value $body -Depth 8
    return $body
}

function New-RepairNetworkFixture {
    param([Parameter(Mandatory = $true)][ValidateSet('default', 'safety')][string] $Kind)
    $isDefault = $Kind -ceq 'default'
    $body = [ordered]@{
        name = if ($isDefault) { 'weatherpoly_default' } else {
            'weatherpoly_safety-plane'
        }
        id = if ($isDefault) { $defaultNetworkId } else { $safetyNetworkId }
        compose_name = if ($isDefault) { 'default' } else { 'safety-plane' }
        scope = 'local'
        driver = 'bridge'
        subnet = if ($isDefault) { '172.20.0.0/16' } else { '172.22.0.0/16' }
        gateway = if ($isDefault) { '172.20.0.1' } else { '172.22.0.1' }
        enable_ipv4 = $true
        enable_ipv6 = $false
        internal = $false
        attachable = $false
        ingress = $false
        attached_container_ids = @()
        labels_sha256 = if ($isDefault) {
            '4a06f3121da4b275c077a18cc2b17a4c12258754e48c3ac2d8de10be0b96ad0d'
        }
        else {
            '44ff0d23c63cad8c3e898bdb1378e958ddcedbcd202055903cd0c7949de103e7'
        }
        options_sha256 =
            'e0d52002c06510e7c6f227700d07f412845fc15686f0723a262d8c3e1459f569'
    }
    $body.canonical_sha256 = Get-CanonicalFixtureHash -Value $body -Depth 8
    return $body
}

function New-RepairSnapshotFixture {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][object] $Fixture
    )
    $stable = [ordered]@{
        container_id = $postgresId
        name = '/weatherpoly-postgres-1'
        image_id = $postgresImageId
        config_image = $postgresImage
        command_sha256 =
            '3e1396d8c083a0274909460787db163a1f9070eeabb659c403c5c7ca890436b3'
        compose_config_hash =
            'bb02ff39b208731cad693da86ac0f83e76aab0b548eca9a0ae310d35ed2576c1'
        state = 'exited'
        running = $false
        started_at = '2026-08-22T19:39:52.897703304Z'
        finished_at = '2026-08-22T22:05:00.000000000Z'
        restart_count = 0
        oom_killed = $false
        exit_code = 0
        network_mode = 'weatherpoly_default'
        hba_sha256 =
            '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
        hba_source = [IO.Path]::GetFullPath(
            (Join-Path $Fixture.root 'ops\postgres\stratus-pg_hba.conf')
        ).Replace('\', '/')
        data_volume = 'weatherpoly_stratus-postgres'
        published_postgres = '127.0.0.1:5432'
        labels_sha256 = 'c' * 64
    }
    $attachments = [ordered]@{
        weatherpoly_default = New-RepairAttachmentFixture -Name weatherpoly_default
        'weatherpoly_safety-plane' =
            New-RepairAttachmentFixture -Name 'weatherpoly_safety-plane'
    }
    $postgres = [ordered]@{
        stable_identity = $stable
        stable_identity_sha256 = Get-CanonicalFixtureHash -Value $stable -Depth 10
        attachment_state = 'exact'
        attachments = $attachments
        attachments_sha256 = Get-CanonicalFixtureHash -Value $attachments -Depth 12
    }
    $networks = [ordered]@{
        weatherpoly_default = New-RepairNetworkFixture -Kind default
        'weatherpoly_safety-plane' = New-RepairNetworkFixture -Kind safety
    }
    $snapshotBody = [ordered]@{
        docker_engine = New-RepairEngineFixture -Runtime $Runtime
        running_container_ids = @()
        lifecycle_inventory = @()
        lifecycle_inventory_sha256 = 'b' * 64
        postgres = $postgres
        production_networks = $networks
        production_networks_sha256 = Get-CanonicalFixtureHash -Value $networks -Depth 12
    }
    $snapshot = [ordered]@{ canonical_sha256 = '' }
    foreach ($entry in $snapshotBody.GetEnumerator()) {
        $snapshot[[string]$entry.Key] = $entry.Value
    }
    $snapshot.canonical_sha256 = Get-CanonicalFixtureHash -Value $snapshotBody -Depth 20
    return $snapshot
}

function Write-NetworkRepairFixture {
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][object] $Fixture
    )
    $snapshot = New-RepairSnapshotFixture -Runtime $Runtime -Fixture $Fixture
    $payload = [ordered]@{
        accepted_at = [DateTimeOffset]::UtcNow.ToString('o')
        disposition = 'verified_exact_existing'
        fixed_identity = [ordered]@{
            postgres_container_id = $postgresId
            postgres_container_name = 'weatherpoly-postgres-1'
            default_network_id = $defaultNetworkId
            safety_network_id = $safetyNetworkId
        }
        docker_engine = New-RepairEngineFixture -Runtime $Runtime
        snapshots = [ordered]@{
            pre = Copy-JsonValue -Value $snapshot
            armed = Copy-JsonValue -Value $snapshot
            post_first = Copy-JsonValue -Value $snapshot
            post_second = Copy-JsonValue -Value $snapshot
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
            issued = $false
            command = @(
                'network', 'connect', '--alias', 'postgres', '--alias',
                'weatherpoly-postgres-1', '--gw-priority', '0',
                $safetyNetworkId, $postgresId
            )
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
    Write-JsonFixture -LiteralPath $LiteralPath -Value ([ordered]@{
        format = 'stratus-stopped-postgres-safety-plane-repair-v1'
        accepted = $true
        payload = $payload
        payload_sha256 = Get-CanonicalFixtureHash -Value $payload -Depth 30
    }) -Depth 30
}

# The active harness below uses compiled fake docker/git applications and a
# persistent fake engine process.  The retired function-mock harness remains
# below the explicit return only as historical review context while the
# Transition contract is being stabilized.
$script:TransitionCaseNumber = 0
$script:ActiveTransitionCaseFilter = ''
$script:ShowTransitionProgress = $false
$script:SkipTransitionCasesThrough = ''
$script:TransitionSkipBoundaryReached = $false

function Test-TransitionCaseSelected {
    param([Parameter(Mandatory = $true)][string] $Label)
    if (-not [string]::IsNullOrWhiteSpace($script:SkipTransitionCasesThrough) -and
        -not $script:TransitionSkipBoundaryReached) {
        if ($Label -ceq $script:SkipTransitionCasesThrough) {
            $script:TransitionSkipBoundaryReached = $true
        }
        return $false
    }
    return (
        [string]::IsNullOrWhiteSpace($script:ActiveTransitionCaseFilter) -or
        $Label -like "*$($script:ActiveTransitionCaseFilter)*"
    )
}

function Write-TransitionTestProgress {
    param([Parameter(Mandatory = $true)][string] $Label)
    if ($script:ShowTransitionProgress) {
        Write-Host "Transition test: $Label"
    }
}

function New-TransitionCasePaths {
    param(
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $script:TransitionCaseNumber += 1
    # Keep the isolated path below the legacy .NET MAX_PATH boundary used by
    # Windows PowerShell 5.1. The human-readable label remains in evidence,
    # while the disposable directory needs only a collision-free case number.
    $directory = Join-Path $Fixture.evidence (
        'c{0:d3}' -f $script:TransitionCaseNumber
    )
    [void](New-Item -ItemType Directory -Path $directory)
    $case = [pscustomobject]@{
        label = $Label
        directory = $directory
        repair = Join-Path $directory 'network-repair.json'
        prestart = Join-Path $directory 'successor-prestart.json'
        live = Join-Path $directory 'successor-live.json'
        watchdog_lease = Join-Path $directory `
            'successor-prestart.json.watchdog-lease.json'
        watchdog_terminal = Join-Path $directory `
            'successor-prestart.json.watchdog-terminal.json'
        watchdog_heartbeat = Join-Path $directory `
            'successor-prestart.json.watchdog-heartbeat.json'
        watchdog_commit = Join-Path $directory `
            'successor-prestart.json.watchdog-commit.json'
    }
    $worstHeartbeatTemporaryPath = [string]$case.watchdog_heartbeat +
        '.tmp-' + ('f' * 32) + '-2147483647'
    if ($worstHeartbeatTemporaryPath.Length -gt 240) {
        throw "The isolated Transition fixture exceeds the PS5.1 MAX_PATH safety bound: $($worstHeartbeatTemporaryPath.Length)."
    }
    return $case
}

function Initialize-TransitionCase {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][object] $Case,
        [Parameter(Mandatory = $true)][string] $Fault
    )
    Write-HistoricalBackupFixtures -LiteralPath $Fixture.backup
    Initialize-HostFixtureState -Fixture $Fixture
    Write-NetworkRepairFixture -LiteralPath $Case.repair `
        -Runtime $Runtime -Fixture $Fixture
    foreach ($path in @(
        $Runtime.result, $Runtime.events, $Runtime.witness_ready,
        $Runtime.witness_exit, $Runtime.witness_exited, $Case.prestart,
        $Case.live, $Case.watchdog_lease, $Case.watchdog_terminal,
        $Case.watchdog_heartbeat, $Case.watchdog_commit
    )) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
    Write-JsonFixture -LiteralPath $Runtime.config -Depth 10 -Value ([ordered]@{
        generation = [guid]::NewGuid().ToString('N')
        fault = $Fault
        root = $Fixture.root
        build = $Fixture.build
        backup = $Fixture.backup
        compose = $Runtime.compose
        repair = $Case.repair
        prestart = $Case.prestart
        live = $Case.live
    })
}

function Invoke-TransitionCase {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][object] $Case,
        [hashtable] $Overrides = @{}
    )
    $parameters = @{
        Mode = 'Transition'
        Root = $Fixture.root
        BuildContext = $Fixture.build
        BackupDirectory = $Fixture.backup
        PreMigrationAttestationPath = $Fixture.pre_migration
        LineageDirectory = $Fixture.lineage
        NetworkRepairAttestationPath = $Case.repair
        ExpectedRevision = $successorRevision
        ExpectedImageId = $successorImageId
        CandidateImage = $successorImage
        PostgresContainer = $postgresId
        PreStartAttestationPath = $Case.prestart
        LiveAcceptancePath = $Case.live
    }
    foreach ($entry in $Overrides.GetEnumerator()) {
        $parameters[[string]$entry.Key] = $entry.Value
    }
    return & $Runtime.gate @parameters
}

function Get-FakeTransitionState {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [int] $WaitMilliseconds = 2000
    )
    $deadline = [DateTimeOffset]::UtcNow.AddMilliseconds($WaitMilliseconds)
    while (-not (Test-Path -LiteralPath $Runtime.result -PathType Leaf) -and
        [DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 20
    }
    if (-not (Test-Path -LiteralPath $Runtime.result -PathType Leaf)) {
        return $null
    }
    return ConvertFrom-TestJsonText -Json (
        Get-Content -LiteralPath $Runtime.result -Raw
    )
}

function Assert-TransitionWitnessStopped {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][string] $Label
    )
    if (-not (Test-Path -LiteralPath $Runtime.witness_ready -PathType Leaf)) {
        return
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(5)
    while (-not (Test-Path -LiteralPath $Runtime.witness_exited -PathType Leaf) -and
        [DateTimeOffset]::UtcNow -lt $deadline) {
        $witnessProcessId = 0
        $readyText = Get-Content -LiteralPath $Runtime.witness_ready -Raw
        if ([int]::TryParse($readyText.Trim(), [ref]$witnessProcessId)) {
            $witnessProcess = Get-Process -Id $witnessProcessId `
                -ErrorAction SilentlyContinue
            if ($null -eq $witnessProcess) {
                return
            }
        }
        Start-Sleep -Milliseconds 20
    }
    $remainingProcessId = 0
    $remainingReadyText = Get-Content -LiteralPath $Runtime.witness_ready -Raw
    $remainingProcess = $null
    if ([int]::TryParse($remainingReadyText.Trim(), [ref]$remainingProcessId)) {
        $remainingProcess = Get-Process -Id $remainingProcessId `
            -ErrorAction SilentlyContinue
    }
    if (
        -not (Test-Path -LiteralPath $Runtime.witness_exited -PathType Leaf) -and
        $null -ne $remainingProcess
    ) {
        throw "$Label leaked the non-reconnecting Docker event witness."
    }
}

function Assert-FakeTransitionSafety {
    param(
        [AllowNull()][object] $State,
        [Parameter(Mandatory = $true)][string] $Label
    )
    if ($null -eq $State) {
        return
    }
    if (
        [int]$State.forbidden_mutation_count -ne 0 -or
        [int]$State.compose_mutation_count -ne 0 -or
        [int]$State.restart_count -ne 0 -or
        [int]$State.kill_count -ne 0 -or
        @($State.owned).Count -ne 0 -or
        [int]$State.proof_create_count -ne [int]$State.proof_remove_count -or
        [int]$State.barrier_create_count -ne [int]$State.barrier_remove_count
    ) {
        throw "$Label leaked proof state or attempted an unapproved production mutation."
    }
    $allowed = @(
        "update --restart=no $postgresId",
        "start $postgresId",
        "stop --time 15 $postgresId"
    )
    foreach ($mutation in @($State.mutations)) {
        if ([string]$mutation -cnotin $allowed) {
            throw "$Label recorded an unapproved mutation: $mutation"
        }
    }
}

function Assert-ExactTransitionMutation {
    param(
        [Parameter(Mandatory = $true)][object] $State,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Success', 'AfterStartFailure', 'BeforeStartFailure')]
        [string] $Disposition,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $actual = @($State.mutations)
    if ($Disposition -ceq 'Success') {
        $expected = @(
            "update --restart=no $postgresId",
            "start $postgresId"
        )
        if (
            [int]$State.update_count -ne 1 -or [int]$State.start_count -ne 1 -or
            [int]$State.stop_count -ne 0 -or
            [string]$State.postgres_state -cne 'running' -or
            [string]$State.restart_policy -cne 'no' -or
            ($actual -join "`n") -cne ($expected -join "`n")
        ) {
            throw "$Label did not perform the exact single-process success mutation."
        }
        return
    }
    if ($Disposition -ceq 'AfterStartFailure') {
        $expected = @(
            "update --restart=no $postgresId",
            "start $postgresId",
            "stop --time 15 $postgresId"
        )
        if (
            [int]$State.update_count -ne 1 -or [int]$State.start_count -ne 1 -or
            [int]$State.stop_count -ne 1 -or
            [string]$State.postgres_state -cne 'stopped' -or
            [string]$State.restart_policy -cne 'no' -or
            ($actual -join "`n") -cne ($expected -join "`n")
        ) {
            throw "$Label did not execute the exact fail-closed rollback stop."
        }
        return
    }
    if (
        [int]$State.start_count -ne 0 -or [int]$State.stop_count -ne 0 -or
        [int]$State.update_count -gt 1
    ) {
        throw "$Label crossed the retained PostgreSQL start boundary."
    }
}

function Assert-TransitionFailureCase {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][string] $Label,
        [Parameter(Mandatory = $true)][string] $Fault,
        [Parameter(Mandatory = $true)][string[]] $ExpectedMessage,
        [Parameter(Mandatory = $true)]
        [ValidateSet('AfterStartFailure', 'BeforeStartFailure')]
        [string] $Disposition,
        [scriptblock] $Configure = {},
        [scriptblock] $Restore = {},
        [hashtable] $Overrides = @{}
    )
    if (-not (Test-TransitionCaseSelected -Label $Label)) { return }
    Write-TransitionTestProgress -Label $Label
    $case = New-TransitionCasePaths -Fixture $Fixture -Label $Label
    Initialize-TransitionCase -Runtime $Runtime -Fixture $Fixture `
        -Case $case -Fault $Fault
    & $Configure $case
    $observed = $false
    try {
        try {
            Invoke-TransitionCase -Runtime $Runtime -Fixture $Fixture `
                -Case $case -Overrides $Overrides | Out-Null
        }
        catch {
            $observed = $true
            $messageMatched = $false
            foreach ($candidate in $ExpectedMessage) {
                if ([string]$_.Exception.Message -like "*$candidate*") {
                    $messageMatched = $true
                    break
                }
            }
            if (-not $messageMatched) {
                throw "$Label failed for the wrong reason: $($_.Exception.Message)"
            }
        }
        if (-not $observed) {
            throw "$Label was incorrectly accepted."
        }
        $liveOverwriteSentinel = $Label -ceq 'live CreateNew overwrite barrier'
        if ($Label -like '* CreateNew overwrite barrier') {
            $artifactPath = if ($Label.StartsWith('prestart')) {
                $case.prestart
            }
            else {
                $case.live
            }
            if ((Get-Content -LiteralPath $artifactPath -Raw) -cne
                'immutable-sentinel') {
                throw "$Label changed the pre-existing immutable artifact."
            }
        }
        if ((Test-Path -LiteralPath $case.live -PathType Leaf) -and
            -not $liveOverwriteSentinel) {
            $rejectedLive = ConvertFrom-TestJsonText -Json (
                Get-Content -LiteralPath $case.live -Raw
            )
            if (
                $rejectedLive.format -cne
                    'stratus-post-migration-successor-transition-live-v2' -or
                -not $rejectedLive.accepted -or
                $rejectedLive.payload.authorization.api_stage_permitted -or
                $rejectedLive.payload.authorization.worker_start_permitted -or
                $rejectedLive.payload.authorization.safety_control_start_permitted -or
                $rejectedLive.payload.authorization.live_transition_permitted -or
                $rejectedLive.payload.authorization.binance_kill_switch_removal_permitted -or
                $rejectedLive.payload.authorization.migration_container_permitted
            ) {
                throw "$Label left an independently actionable Live candidate."
            }
        }
        $state = Get-FakeTransitionState -Runtime $Runtime
        Assert-FakeTransitionSafety -State $state -Label $Label
        if ($null -ne $state) {
            Assert-ExactTransitionMutation -State $state `
                -Disposition $Disposition -Label $Label
        }
        elseif ($Disposition -ceq 'AfterStartFailure') {
            throw "$Label did not persist fake-engine rollback state."
        }
        Assert-RejectedWatchdogAuthorization -Case $case `
            -Disposition $Disposition -Label $Label
        Assert-TransitionWitnessStopped -Runtime $Runtime -Label $Label
    }
    finally {
        & $Restore $case
    }
}

function Assert-RejectedWatchdogAuthorization {
    param(
        [Parameter(Mandatory = $true)][object] $Case,
        [Parameter(Mandatory = $true)]
        [ValidateSet('AfterStartFailure', 'BeforeStartFailure')]
        [string] $Disposition,
        [Parameter(Mandatory = $true)][string] $Label
    )

    if (-not (Test-Path -LiteralPath $Case.watchdog_terminal -PathType Leaf)) {
        if ($Disposition -ceq 'AfterStartFailure') {
            throw "$Label did not leave a durable fail-closed watchdog terminal receipt."
        }
        return
    }
    $raw = Get-Content -LiteralPath $Case.watchdog_terminal -Raw
    if ($raw -match '"api_stage_permitted"\s*:\s*true') {
        throw "$Label left a terminal artifact with API authorization."
    }
    $terminal = $null
    try { $terminal = ConvertFrom-TestJsonText -Json $raw }
    catch {
        if ($Disposition -ceq 'AfterStartFailure') {
            throw "$Label left a malformed post-start terminal receipt."
        }
        return
    }
    if ($terminal.format -cne 'stratus-successor-transition-watchdog-terminal-v1') {
        if ($Disposition -ceq 'AfterStartFailure') {
            throw "$Label left a forged post-start terminal receipt."
        }
        return
    }
    Assert-ExactTestJsonProperties -Value $terminal `
        -Label "$Label watchdog terminal" -Expected @(
            'format', 'status', 'terminal_at', 'session_id', 'owner_pid',
            'owner_start_time_utc', 'lease_path', 'lease_sha256', 'receipt_path',
            'commit_path', 'live_path', 'trigger', 'docker_engine', 'containment',
            'commit', 'authorization', 'failure'
        )
    Assert-ExactTestJsonProperties -Value $terminal.authorization `
        -Label "$Label watchdog terminal authorization" -Expected @(
            'api_stage_permitted', 'worker_start_permitted',
            'safety_control_start_permitted', 'live_transition_permitted',
            'binance_kill_switch_removal_permitted', 'migration_container_permitted'
        )
    foreach ($name in @(
        'api_stage_permitted', 'worker_start_permitted',
        'safety_control_start_permitted', 'live_transition_permitted',
        'binance_kill_switch_removal_permitted', 'migration_container_permitted'
    )) {
        if ($terminal.authorization.$name -isnot [bool] -or
            [bool]$terminal.authorization.$name) {
            throw "$Label watchdog terminal authorization was not native false."
        }
    }
    if ($Disposition -ceq 'AfterStartFailure') {
        if (
            $terminal.status -cne 'contained' -or
            $terminal.trigger -cnotin @(
                'heartbeat_stale', 'invalid_or_replayed_heartbeat',
                'invalid_or_forged_commit', 'watchdog_internal_failure'
            ) -or
            $null -eq $terminal.containment -or
            -not $terminal.containment.verified_stopped -or
            $terminal.containment.after_state -cne 'exited' -or
            @($terminal.containment.errors).Count -ne 0 -or
            $null -ne $terminal.commit
        ) {
            throw "$Label watchdog did not durably prove exact stopped containment."
        }
    }
}

function Assert-PositiveWatchdogChain {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][object] $Case,
        [Parameter(Mandatory = $true)][object] $PreStart,
        [Parameter(Mandatory = $true)][object] $Live
    )

    foreach ($path in @(
        $Case.watchdog_lease, $Case.watchdog_heartbeat,
        $Case.watchdog_commit, $Case.watchdog_terminal
    )) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Positive Transition omitted a durable watchdog artifact: $path"
        }
    }
    $leaseRaw = Get-Content -LiteralPath $Case.watchdog_lease -Raw
    $heartbeatRaw = Get-Content -LiteralPath $Case.watchdog_heartbeat -Raw
    $commitRaw = Get-Content -LiteralPath $Case.watchdog_commit -Raw
    $terminalRaw = Get-Content -LiteralPath $Case.watchdog_terminal -Raw
    $lease = ConvertFrom-TestJsonText -Json $leaseRaw
    $heartbeat = ConvertFrom-TestJsonText -Json $heartbeatRaw
    $commit = ConvertFrom-TestJsonText -Json $commitRaw
    $terminal = ConvertFrom-TestJsonText -Json $terminalRaw
    $handoff = $PreStart.payload.transition_session.watchdog_handoff
    $binding = $Live.payload.transition_session.watchdog
    $leaseEvidence = $Live.payload.transition_session.watchdog_lease
    $heartbeatAtLive = $Live.payload.transition_session.heartbeat_at_live
    $sessionId = [string]$PreStart.payload.transition_session.session_id
    $preStartSha256 = Get-TestFileSha256 -LiteralPath $Case.prestart
    $liveSha256 = Get-TestFileSha256 -LiteralPath $Case.live
    $leaseSha256 = Get-TestFileSha256 -LiteralPath $Case.watchdog_lease
    $commitSha256 = Get-TestFileSha256 -LiteralPath $Case.watchdog_commit
    $watchdogSha256 = Get-TestFileSha256 -LiteralPath $Runtime.watchdog

    Assert-ExactTestJsonProperties -Value $handoff -Label 'PreStart watchdog handoff' `
        -Expected @(
            'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
            'hard_deadline_utc', 'heartbeat_timeout_seconds', 'lease_path',
            'receipt_path', 'heartbeat_path', 'commit_path', 'live_path',
            'prestart_path'
        )
    Assert-ExactTestJsonProperties -Value $lease -Label 'watchdog lease' -Expected @(
        'format', 'armed', 'armed_at', 'session_id', 'watchdog', 'owner',
        'deadline', 'artifacts', 'docker_engine', 'postgres', 'containment'
    )
    Assert-ExactTestJsonProperties -Value $heartbeat -Label 'watchdog heartbeat' `
        -Expected @(
            'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
            'lease_path', 'lease_sha256', 'sequence', 'written_at'
        )
    Assert-ExactTestJsonProperties -Value $commit -Label 'watchdog commit' -Expected @(
        'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
        'lease_path', 'lease_sha256', 'live_path', 'live_sha256', 'committed_at'
    )
    Assert-ExactTestJsonProperties -Value $terminal -Label 'watchdog terminal receipt' `
        -Expected @(
            'format', 'status', 'terminal_at', 'session_id', 'owner_pid',
            'owner_start_time_utc', 'lease_path', 'lease_sha256', 'receipt_path',
            'commit_path', 'live_path', 'trigger', 'docker_engine', 'containment',
            'commit', 'authorization', 'failure'
        )
    Assert-ExactTestJsonProperties -Value $binding -Label 'Live watchdog binding' `
        -Expected @(
            'format', 'session_id', 'owner_pid', 'owner_start_time_utc',
            'hard_deadline_utc', 'heartbeat_timeout_seconds', 'lease_path',
            'lease_sha256', 'receipt_path', 'heartbeat_path', 'commit_path'
        )
    Assert-ExactTestJsonProperties -Value $heartbeatAtLive `
        -Label 'Live watchdog heartbeat evidence' `
        -Expected @('sequence', 'written_at', 'sha256')
    Assert-ExactTestJsonProperties -Value $terminal.authorization `
        -Label 'terminal composite authorization' -Expected @(
            'api_stage_permitted', 'worker_start_permitted',
            'safety_control_start_permitted', 'live_transition_permitted',
            'binance_kill_switch_removal_permitted', 'migration_container_permitted'
        )

    if (
        $handoff.format -cne 'stratus-successor-transition-watchdog-handoff-v1' -or
        $binding.format -cne 'stratus-successor-transition-watchdog-binding-v1' -or
        $lease.format -cne 'stratus-successor-transition-watchdog-lease-v1' -or
        -not $lease.armed -or
        $heartbeat.format -cne 'stratus-successor-transition-watchdog-heartbeat-v1' -or
        $heartbeat.sequence -lt 1 -or
        $commit.format -cne 'stratus-successor-transition-watchdog-commit-v1' -or
        $terminal.format -cne 'stratus-successor-transition-watchdog-terminal-v1' -or
        $terminal.status -cne 'success' -or
        $terminal.trigger -cne 'accepted_live_commit' -or
        $null -ne $terminal.containment -or $null -ne $terminal.failure -or
        -not $terminal.authorization.api_stage_permitted -or
        $terminal.authorization.worker_start_permitted -or
        $terminal.authorization.safety_control_start_permitted -or
        $terminal.authorization.live_transition_permitted -or
        $terminal.authorization.binance_kill_switch_removal_permitted -or
        $terminal.authorization.migration_container_permitted
    ) {
        throw 'Positive Transition watchdog chain is not armed, live, and terminal-success exact.'
    }
    foreach ($value in @(
        $handoff.session_id, $binding.session_id, $lease.session_id,
        $heartbeat.session_id, $commit.session_id, $terminal.session_id
    )) {
        if ([string]$value -cne $sessionId) {
            throw 'Positive Transition watchdog artifacts do not share one exact session.'
        }
    }
    if (
        $handoff.owner_pid -ne $binding.owner_pid -or
        $handoff.owner_pid -ne $lease.owner.process_id -or
        $handoff.owner_pid -ne $heartbeat.owner_pid -or
        $handoff.owner_pid -ne $commit.owner_pid -or
        $handoff.owner_pid -ne $terminal.owner_pid -or
        $handoff.owner_start_time_utc -cne $binding.owner_start_time_utc -or
        $handoff.owner_start_time_utc -cne $lease.owner.process_started_at -or
        $handoff.owner_start_time_utc -cne $heartbeat.owner_start_time_utc -or
        $handoff.owner_start_time_utc -cne $commit.owner_start_time_utc -or
        $handoff.owner_start_time_utc -cne $terminal.owner_start_time_utc
    ) {
        throw 'Positive Transition watchdog owner binding drifted across the chain.'
    }
    if (
        [string]$PreStart.payload.transition_session.watchdog_program.script_path -cne
            [IO.Path]::GetFullPath($Runtime.watchdog) -or
        [string]$PreStart.payload.transition_session.watchdog_program.script_sha256 -cne
            $watchdogSha256 -or
        -not $PreStart.payload.transition_session.watchdog_program.read_locks_held -or
        [string]$Live.payload.transition_session.watchdog_program.script_sha256 -cne
            $watchdogSha256
    ) {
        throw 'Positive Transition did not bind and read-lock the exact watchdog program.'
    }
    if (
        $lease.artifacts.prestart_sha256 -cne $preStartSha256 -or
        $lease.artifacts.prestart_payload_sha256 -cne $PreStart.payload_sha256 -or
        $binding.lease_sha256 -cne $leaseSha256 -or
        $leaseEvidence.sha256 -cne $leaseSha256 -or
        $heartbeat.lease_sha256 -cne $leaseSha256 -or
        $commit.lease_sha256 -cne $leaseSha256 -or
        $terminal.lease_sha256 -cne $leaseSha256 -or
        $commit.live_sha256 -cne $liveSha256 -or
        $terminal.commit.marker_sha256 -cne $commitSha256 -or
        $terminal.commit.live_sha256 -cne $liveSha256 -or
        $terminal.commit.live_payload_sha256 -cne $Live.payload_sha256
    ) {
        throw 'Positive Transition watchdog raw-byte hash chain is not exact.'
    }
    foreach ($pair in @(
        @([string]$handoff.lease_path, [string]$Case.watchdog_lease),
        @([string]$handoff.receipt_path, [string]$Case.watchdog_terminal),
        @([string]$handoff.heartbeat_path, [string]$Case.watchdog_heartbeat),
        @([string]$handoff.commit_path, [string]$Case.watchdog_commit),
        @([string]$handoff.live_path, [string]$Case.live),
        @([string]$handoff.prestart_path, [string]$Case.prestart)
    )) {
        if ([IO.Path]::GetFullPath($pair[0]) -cne [IO.Path]::GetFullPath($pair[1])) {
            throw 'Positive Transition watchdog artifact path binding drifted.'
        }
    }
    if (
        $lease.postgres.container_id -cne $postgresId -or
        $lease.postgres.restart_policy_before -cne 'unless-stopped' -or
        $lease.postgres.restart_policy_armed -cne 'no' -or
        $lease.postgres.state -cne 'exited' -or
        $lease.containment.stop_timeout_seconds -ne 15 -or
        $lease.containment.stop_wall_clock_timeout_seconds -ne 25 -or
        -not $lease.containment.exact_container_only -or
        -not $lease.containment.ambiguous_start_requires_stop_attempt -or
        $heartbeat.sequence -lt $heartbeatAtLive.sequence -or
        $terminal.commit.postgres_started_at -cne
            $Live.payload.postgres_transition.successor_started_at -or
        $terminal.commit.docker_engine_contract_sha256 -cne
            $lease.docker_engine.contract_sha256
    ) {
        throw 'Positive Transition watchdog lifecycle or containment proof drifted.'
    }
    if (
        $Live.payload.authorization.api_stage_permitted -or
        $Live.payload.authorization.worker_start_permitted -or
        $Live.payload.authorization.safety_control_start_permitted -or
        $Live.payload.authorization.live_transition_permitted -or
        $Live.payload.authorization.binance_kill_switch_removal_permitted -or
        $Live.payload.authorization.migration_container_permitted
    ) {
        throw 'Positive Transition Live was independently actionable before terminal authorization.'
    }
    $allWatchdogRaw = @($leaseRaw, $heartbeatRaw, $commitRaw, $terminalRaw) -join "`n"
    if ($allWatchdogRaw -match [regex]::Escape($secretSentinel) -or
        $allWatchdogRaw -match '(?i)password|api[_-]?key|secret|database_url|dsn|token') {
        throw 'Positive Transition watchdog artifacts exposed secret-shaped evidence.'
    }
}

function Assert-PositiveTransition {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][object] $Fixture,
        [string] $Fault = 'none',
        [string] $Label = 'positive-transition'
    )
    if (-not (Test-TransitionCaseSelected -Label $Label)) { return }
    Write-TransitionTestProgress -Label $Label
    $case = New-TransitionCasePaths -Fixture $Fixture -Label $Label
    Initialize-TransitionCase -Runtime $Runtime -Fixture $Fixture `
        -Case $case -Fault $Fault
    $repairRaw = Get-Content -LiteralPath $case.repair -Raw
    $output = @(Invoke-TransitionCase -Runtime $Runtime -Fixture $Fixture -Case $case)
    if (
        -not (Test-Path -LiteralPath $case.prestart -PathType Leaf) -or
        -not (Test-Path -LiteralPath $case.live -PathType Leaf)
    ) {
        throw 'Positive Transition did not CreateNew both required artifacts.'
    }
    $pre = ConvertFrom-TestJsonText -Json (Get-Content $case.prestart -Raw)
    $liveRaw = Get-Content -LiteralPath $case.live -Raw
    $live = ConvertFrom-TestJsonText -Json $liveRaw
    if (
        $pre.format -cne 'stratus-post-migration-successor-transition-prestart-v2' -or
        $live.format -cne 'stratus-post-migration-successor-transition-live-v2' -or
        -not $pre.accepted -or -not $live.accepted -or
        $pre.payload_sha256 -cne (Get-CanonicalFixtureHash $pre.payload 30) -or
        $live.payload_sha256 -cne (Get-CanonicalFixtureHash $live.payload 30)
    ) {
        throw 'Positive Transition artifact envelopes are not authentic and exact.'
    }
    Assert-PositiveWatchdogChain -Runtime $Runtime -Case $case `
        -PreStart $pre -Live $live
    $repairSha = (
        Microsoft.PowerShell.Utility\Get-FileHash `
            -LiteralPath $case.repair -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
        $pre.payload.network_repair.sha256 -cne $repairSha -or
        $live.payload.network_repair.sha256 -cne $repairSha -or
        (Get-Content -LiteralPath $case.repair -Raw) -cne $repairRaw -or
        $pre.payload.postgres_prestart.restart_policy -cne 'no' -or
        $pre.payload.transition_session.observation.start_command_issued -or
        $pre.payload.authorization.external_postgres_start_permitted -or
        -not $pre.payload.authorization.transition_process_start_permitted
    ) {
        throw 'Positive Transition did not bind repair-v1 and the fail-closed PreStart boundary.'
    }
    $receipt = $live.payload.transition_session.event_witness
    $expectedNetworkIds = @($defaultNetworkId, $safetyNetworkId)
    [Array]::Sort($expectedNetworkIds, [StringComparer]::Ordinal)
    $actualNetworkIds = @($receipt.network_ids)
    [Array]::Sort($actualNetworkIds, [StringComparer]::Ordinal)
    if (
        $pre.payload.transition_session.session_id -cne $receipt.session_id -or
        $receipt.reconnect_count -ne 0 -or $receipt.eof_before_seal -or
        $receipt.truncated -or $receipt.postgres_start_count -ne 1 -or
        $receipt.postgres_restart_policy_update_count -ne 1 -or
        $receipt.postgres_attach_count -ne 0 -or
        $receipt.data_volume_mount_count -ne 1 -or
        $receipt.network_connect_count -ne 2 -or
        ($actualNetworkIds -join "`n") -cne ($expectedNetworkIds -join "`n") -or
        $receipt.gate_exec_triplets -ne 8 -or
        $receipt.healthy_status_event_count -ne 1 -or
        $live.payload.postgres_transition.restart_policy -cne 'no' -or
        -not $live.payload.transition_session.start_proof.start_owned_by_transition_process
    ) {
        throw 'Positive Transition did not preserve one exact continuous event witness.'
    }
    if (
        -not $live.payload.hba.reload_accepted -or
        -not $live.payload.hba.reload_acknowledged -or
        $live.payload.hba.reload_poll_count -lt 1 -or
        $live.payload.hba.proof_backend_pid -le 0 -or
        $live.payload.database.schema_head -cne '20260730_0047' -or
        -not $live.payload.database.indexes_exact -or
        -not $live.payload.database.functions_exact -or
        -not $live.payload.database.zero_live_state -or
        $live.payload.database.execution_authorized
    ) {
        throw 'Positive Transition did not prove same-session HBA acknowledgement and exact DB state.'
    }
    $state = Get-FakeTransitionState -Runtime $Runtime
    if ($Fault -ceq 'watchdog-publisher-cleanup' -and (
        -not $state.watchdog_fault_injected -or
        [int]$state.watchdog_publisher_cleanup_delay_count -ne 1
    )) {
        throw 'Post-terminal heartbeat publisher cleanup fault was not injected.'
    }
    Assert-FakeTransitionSafety -State $state -Label 'positive Transition'
    Assert-ExactTransitionMutation -State $state -Disposition Success `
        -Label 'positive Transition'
    $expectedOperations = @(
        'hba-sha', 'hba-reload-session', 'hba-fresh-session', 'hba-sha',
        'snapshot', 'snapshot', 'hba-fresh-session', 'hba-sha'
    )
    if (
        [int]$state.snapshot_count -ne 2 -or [int]$state.gate_exec_count -ne 8 -or
        (@($state.database_operations) -join "`n") -cne
            ($expectedOperations -join "`n")
    ) {
        throw 'Positive Transition database command sequence was not exact.'
    }
    if (
        $liveRaw -match [regex]::Escape($secretSentinel) -or
        ($output -join "`n") -match [regex]::Escape($secretSentinel) -or
        $liveRaw -match '(?i)password|api[_-]?key|secret|database_url|dsn|token'
    ) {
        throw 'Positive Transition exposed a secret-shaped field or credential value.'
    }
    Assert-TransitionWitnessStopped -Runtime $Runtime -Label 'positive Transition'
}

function Invoke-ActiveTransitionSuite {
    param(
        [Parameter(Mandatory = $true)][object] $Runtime,
        [Parameter(Mandatory = $true)][object] $Fixture
    )
    Assert-PositiveTransition -Runtime $Runtime -Fixture $Fixture
    Assert-PositiveTransition -Runtime $Runtime -Fixture $Fixture `
        -Fault 'watchdog-publisher-cleanup' `
        -Label 'accepted-terminal-publisher-cleanup-failure'
    Assert-PositiveTransition -Runtime $Runtime -Fixture $Fixture `
        -Fault 'git-detached-stderr' `
        -Label 'detached Git native stderr normalization'

    foreach ($entry in @(
        @('tampered PreStart after creation', 'prestart-tamper',
            'PreStart artifact changed', 'AfterStartFailure'),
        @('event witness early exit', 'witness-early-exit',
            'event witness exited before final sealing', 'BeforeStartFailure'),
        @('event witness EOF before seal', 'witness-eof-before-seal',
            'event witness exited before final sealing', 'AfterStartFailure'),
        @('event witness truncated journal', 'witness-truncated',
            'native JSON object at its root', 'AfterStartFailure'),
        @('unexpected second PostgreSQL start', 'event-second-start',
            'exactly one retained PostgreSQL start event', 'AfterStartFailure'),
        @('unexpected PostgreSQL lifecycle event', 'event-lifecycle',
            'emitted a forbidden Docker event: die', 'AfterStartFailure'),
        @('unexpected global Docker event', 'event-global',
            'unexpected global event', 'AfterStartFailure'),
        @('unowned PostgreSQL exec', 'event-unowned-exec',
            'unowned PostgreSQL exec command', 'AfterStartFailure'),
        @('duplicate gate PostgreSQL exec', 'event-extra-exec',
            'unowned PostgreSQL exec command', 'AfterStartFailure'),
        @('missing start network connect', 'event-network-missing',
            'exactly one connect to each production network', 'AfterStartFailure'),
        @('extra start network connect', 'event-network-extra',
            'PostgreSQL network event contract drifted', 'AfterStartFailure'),
        @('missing named-volume mount event', 'event-missing-volume',
            'exactly one retained PostgreSQL data-volume mount event', 'AfterStartFailure'),
        @('drifted named-volume mount event', 'event-volume-drift',
            'data-volume mount event drifted', 'AfterStartFailure'),
        @('ambiguous Docker start return', 'start-return-wrong',
            'emergency containment', 'AfterStartFailure'),
        @('accepted watchdog lease byte drift', 'watchdog-lease-drift',
            'watchdog lease changed before Live evidence creation',
            'AfterStartFailure'),
        @('malformed watchdog heartbeat', 'watchdog-heartbeat-lock',
            @(
                'Production Docker network identity/topology is not exact',
                'Live successor proof permits only the exact production PostgreSQL container'
            ),
            'AfterStartFailure'),
        @('forged watchdog commit publication race', 'watchdog-commit-forged',
            @(
                'Production Docker network identity/topology is not exact',
                'Live successor proof permits only the exact production PostgreSQL container',
                'already exists'
            ),
            'AfterStartFailure')
    )) {
        Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
            -Label $entry[0] -Fault $entry[1] -ExpectedMessage $entry[2] `
            -Disposition $entry[3]
    }

    foreach ($entry in @(
        @('extra configured PostgreSQL network', 'network-extra',
            'unexpected Docker network attachment'),
        @('missing configured PostgreSQL network', 'network-missing',
            'unexpected Docker network attachment'),
        @('wrong running network endpoint', 'network-endpoint',
            'Running PostgreSQL network endpoint is not exact'),
        @('final network endpoint drift', 'network-final',
            'Running PostgreSQL network endpoint is not exact'),
        @('final retained PostgreSQL identity drift', 'postgres-final-drift',
            'identity or network endpoints drifted during Live proof'),
        @('writer appears after start', 'writer-running',
            'permits only the exact production PostgreSQL'),
        @('writer appears at final boundary', 'writer-final',
            'permits only the exact production PostgreSQL'),
        @('mounted HBA drift', 'hba-mounted',
            'HBA bytes drifted before'),
        @('HBA reload no-ack', 'hba-no-ack',
            'HBA inventory is not the exact reviewed'),
        @('HBA reload acknowledgement timeout', 'hba-timeout',
            'did not acknowledge the exact HBA reload'),
        @('HBA inventory drift', 'hba-inventory',
            'HBA inventory is not the exact reviewed'),
        @('final HBA generation drift', 'hba-final-generation',
            'HBA generation drifted after database snapshots'),
        @('final HBA inventory drift', 'hba-final-inventory',
            'did not expose the exact fresh HBA inventory'),
        @('final mounted HBA byte drift', 'hba-final-bytes',
            'HBA bytes drifted after')
    )) {
        $disposition = if ($entry[1] -in @('network-extra', 'network-missing')) {
            'BeforeStartFailure'
        }
        else {
            'AfterStartFailure'
        }
        Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
            -Label $entry[0] -Fault $entry[1] -ExpectedMessage $entry[2] `
            -Disposition $disposition
    }

    foreach ($dimension in @('method', 'opclass', 'collation', 'order', 'include', 'predicate')) {
        Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
            -Label "exact index catalog $dimension drift" `
            -Fault "snapshot-index-$dimension" `
            -ExpectedMessage 'rejected schema, database objects, or fail-closed controls' `
            -Disposition AfterStartFailure
    }
    foreach ($entry in @(
        @('tampered DB function definition', 'snapshot-function-source',
            'rejected schema, database objects, or fail-closed controls'),
        @('no-op DB function definition', 'snapshot-function-noop',
            'rejected schema, database objects, or fail-closed controls'),
        @('wrong retained schema head', 'snapshot-schema-head',
            'rejected schema, database objects, or fail-closed controls'),
        @('non-zero retained live state', 'snapshot-live-row',
            'rejected non-zero live_orders'),
        @('strict snapshot native boolean', 'snapshot-boolean-string',
            'must be a native JSON boolean'),
        @('malformed strict snapshot JSON', 'snapshot-malformed',
            'is not valid strict JSON'),
        @('singleton-array snapshot JSON', 'snapshot-array-root',
            'native JSON object at its root'),
        @('snapshot clock window drift', 'snapshot-clock-window',
            'bounded 60-second window')
    )) {
        Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
            -Label $entry[0] -Fault $entry[1] -ExpectedMessage $entry[2] `
            -Disposition AfterStartFailure
    }

    foreach ($entry in @(
        @('proof inspect command failure cleanup', 'proof-inspect-failure',
            'required Docker proof command failed'),
        @('Git provenance native stderr normalization', 'git-command-failure',
            'A required Git provenance command failed (exit 72)'),
        @('Git ancestry native stderr normalization', 'git-ancestry-stderr',
            'attested predecessor is not an ancestor'),
        @('Docker Compose native stderr normalization', 'compose-command-failure',
            'A required Docker proof command failed (exit 73)'),
        @('malformed proof inspect cleanup', 'proof-inspect-malformed',
            'is not valid strict JSON'),
        @('proof inspect identity drift cleanup', 'proof-inspect-identity',
            'proof container is not the exact newly-created stopped boundary'),
        @('closed migration source drift', 'git-source',
            'Closed migration Git contract drifted'),
        @('successor root revision drift', 'git-root-revision',
            'worktrees must be the exact successor revision'),
        @('successor image migration byte drift', 'image-contract-migration',
            'Successor image migration bytes drifted'),
        @('successor image revision drift', 'image-revision',
            'successor candidate image identity/runtime boundary is not exact'),
        @('successor immutable image ID drift', 'image-id',
            'successor candidate image identity/runtime boundary is not exact'),
        @('image inspect singleton array', 'image-array-root',
            'native JSON object at its root'),
        @('rendered Compose dependency drift', 'compose-dependency',
            'exact no-mount one-shot contract'),
        @('rendered Compose command scalar', 'compose-command-scalar',
            'must be a native JSON array'),
        @('wrong retained PostgreSQL identity', 'postgres-id',
            'identity/topology is not exact'),
        @('retained PostgreSQL lifecycle state drift', 'postgres-wrong-state',
            'Stopped PostgreSQL retains an active network endpoint'),
        @('retained PostgreSQL mount drift', 'postgres-mount',
            'identity/topology is not exact'),
        @('application writer before transition', 'writer-prestart',
            'zero running containers'),
        @('watchdog lease CreateNew reservation race', 'watchdog-lease-race',
            'transition watchdog lease does not have the exact required property set'),
        @('watchdog terminal CreateNew reservation race', 'watchdog-terminal-race',
            'lost its continuous process boundary')
    )) {
        Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
            -Label $entry[0] -Fault $entry[1] -ExpectedMessage $entry[2] `
            -Disposition BeforeStartFailure
    }

    Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
        -Label 'backup archive drift across Transition' -Fault backup-live `
        -ExpectedMessage 'immutable pre-0047 backup payload drifted' `
        -Disposition AfterStartFailure
    Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
        -Label 'restore attestation drift across Transition' -Fault restore-live `
        -ExpectedMessage 'isolated restore no longer binds' `
        -Disposition AfterStartFailure

    Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
        -Label 'tampered network repair before Transition' -Fault none `
        -ExpectedMessage 'network repair envelope is not authentic' `
        -Disposition BeforeStartFailure -Configure {
            param($case)
            $repair = ConvertFrom-TestJsonText -Json (
                Get-Content -LiteralPath $case.repair -Raw
            )
            $repair.payload.authorization.api_start_permitted = $true
            Write-JsonFixture -LiteralPath $case.repair -Value $repair -Depth 30
        }
    Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
        -Label 'network repair drift before Live evidence' -Fault repair-tamper `
        -ExpectedMessage 'Stopped PostgreSQL network repair evidence changed during transition' `
        -Disposition AfterStartFailure

    foreach ($phase in @('initial', 'final')) {
        Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
            -Label "missing $phase event barrier" -Fault "missing-$phase-barrier" `
            -ExpectedMessage 'Docker event witness did not observe the exact barrier' `
            -Disposition $(if ($phase -ceq 'initial') {
                'BeforeStartFailure'
            } else { 'AfterStartFailure' })
    }

    foreach ($artifact in @('prestart', 'live')) {
        $label = "$artifact CreateNew overwrite barrier"
        Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
            -Label $label -Fault none -ExpectedMessage 'refuses to overwrite' `
            -Disposition BeforeStartFailure -Configure {
                param($case)
                Write-Utf8NoBom -LiteralPath $case.$artifact -Value 'immutable-sentinel'
            }
    }

    Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
        -Label 'historical outer SHA-256 drift' -Fault none `
        -ExpectedMessage 'outer SHA-256 is not the reviewed trust root' `
        -Disposition BeforeStartFailure -Configure {
            param($case)
            $global:SuccessorHashDrift = 'pre-migration'
        }
    Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
        -Label 'historical lineage content hash drift' -Fault none `
        -ExpectedMessage 'Historical lineage SHA-256 drifted' `
        -Disposition BeforeStartFailure -Configure {
            param($case)
            $global:SuccessorHashDrift = 'lineage'
        }

    $originalHistorical = Get-Content -LiteralPath $Fixture.pre_migration -Raw
    Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
        -Label 'tampered historical payload' -Fault none `
        -ExpectedMessage 'payload hash is invalid' `
        -Disposition BeforeStartFailure -Configure {
            param($case)
            $tampered = ConvertFrom-TestJsonText -Json $originalHistorical
            $tampered.payload.candidate.revision = 'f' * 40
            Write-JsonFixture -LiteralPath $Fixture.pre_migration -Value $tampered
        } -Restore {
            param($case)
            Write-Utf8NoBom -LiteralPath $Fixture.pre_migration -Value $originalHistorical
        }
    Assert-TransitionFailureCase -Runtime $Runtime -Fixture $Fixture `
        -Label 'historical singleton-array JSON root' -Fault none `
        -ExpectedMessage 'native JSON object at its root' `
        -Disposition BeforeStartFailure -Configure {
            param($case)
            Write-Utf8NoBom -LiteralPath $Fixture.pre_migration `
                -Value '[{"format":"array-root"}]'
        } -Restore {
            param($case)
            Write-Utf8NoBom -LiteralPath $Fixture.pre_migration -Value $originalHistorical
        }

    if (
        $gateSource -notmatch 'Invoke-Docker\s+-Arguments\s+@\(''start'',\s*\$productionPostgresId\)' -or
        $gateSource -notmatch "'update',\s*'--restart=no'" -or
        $gateSource -match '(?is)\$composeArguments\s*\+\s*@\(\s*''(?:up|run|start|restart)''' -or
        $gateSource -match '(?is)Invoke-Docker\s+-Arguments\s+@\(\s*''(?:restart|kill)'''
    ) {
        throw 'Static Transition lifecycle authority is broader than the reviewed boundary.'
    }
}

$activeTempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'sgt-' + [guid]::NewGuid().ToString('N')
)
$activeRuntime = $null
$activeEnvironment = [ordered]@{}
$activePositiveOnly = [Environment]::GetEnvironmentVariable(
    'STRATUS_SUCCESSOR_TEST_POSITIVE_ONLY', [EnvironmentVariableTarget]::Process
)
$activeKeepTemp = [Environment]::GetEnvironmentVariable(
    'STRATUS_SUCCESSOR_TEST_KEEP_TEMP', [EnvironmentVariableTarget]::Process
)
$activeCaseFilter = [Environment]::GetEnvironmentVariable(
    'STRATUS_SUCCESSOR_TEST_FILTER', [EnvironmentVariableTarget]::Process
)
$activeShowProgress = [Environment]::GetEnvironmentVariable(
    'STRATUS_SUCCESSOR_TEST_PROGRESS', [EnvironmentVariableTarget]::Process
)
$activeSkipThrough = [Environment]::GetEnvironmentVariable(
    'STRATUS_SUCCESSOR_TEST_SKIP_THROUGH', [EnvironmentVariableTarget]::Process
)
$environmentNames = @(
    'PATH', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
    'DOCKER_CONFIG', 'DOCKER_CLI_PLUGIN_EXTRA_DIRS', 'COMPOSE_PROVIDER',
    'COMPOSE_FILE', 'COMPOSE_PROFILES', 'COMPOSE_PROJECT_NAME',
    'COMPOSE_PATH_SEPARATOR', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_INDEX_FILE',
    'STRATUS_FAKE_PIPE', 'STRATUS_FAKE_CONFIG', 'STRATUS_FAKE_RESULT',
    'STRATUS_FAKE_EVENTS', 'STRATUS_FAKE_WITNESS_READY',
    'STRATUS_FAKE_WITNESS_EXIT', 'STRATUS_FAKE_WITNESS_EXITED',
    'STRATUS_FAKE_SERVER_READY', 'STRATUS_SUCCESSOR_TEST_POSITIVE_ONLY',
    'STRATUS_SUCCESSOR_TEST_KEEP_TEMP', 'STRATUS_SUCCESSOR_TEST_FILTER',
    'STRATUS_SUCCESSOR_TEST_PROGRESS', 'STRATUS_SUCCESSOR_TEST_SKIP_THROUGH'
)
foreach ($key in [Environment]::GetEnvironmentVariables().Keys) {
    if ([string]$key -imatch '^GIT_CONFIG_') {
        $environmentNames += [string]$key
    }
}
$environmentNames = @($environmentNames | Select-Object -Unique)
foreach ($name in $environmentNames) {
    $activeEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name, [EnvironmentVariableTarget]::Process
    )
}
try {
    [void](New-Item -ItemType Directory -Path $activeTempRoot)
    $activeFixture = New-SuccessorFixture -LiteralPath (
        Join-Path $activeTempRoot 'fixture'
    )
    $activeRuntime = Start-FakeRuntime -LiteralPath (
        Join-Path $activeTempRoot 'runtime'
    ) -FixtureRoot $activeFixture.root
    foreach ($name in $environmentNames) {
        if ($name -cne 'PATH' -and $name -notlike 'STRATUS_FAKE_*') {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
    }
    # Only the compiled fakes are discoverable.  The core itself is copied to
    # the isolated runtime with its three approved executable hashes replaced
    # by the actual compiled fake hash; no production tool is invoked.
    $env:PATH = $activeRuntime.shim
    $script:ActiveTransitionCaseFilter = [string]$activeCaseFilter
    $script:ShowTransitionProgress = $activeShowProgress -ceq '1'
    $script:SkipTransitionCasesThrough = [string]$activeSkipThrough
    $script:TransitionSkipBoundaryReached = $false
    if ($activePositiveOnly -ceq '1') {
        Assert-PositiveTransition -Runtime $activeRuntime -Fixture $activeFixture
    }
    else {
        Invoke-ActiveTransitionSuite -Runtime $activeRuntime -Fixture $activeFixture
    }
    'Post-migration successor Transition gate tests passed.'
}
finally {
    Stop-FakeRuntime -Runtime $activeRuntime
    foreach ($name in $activeEnvironment.Keys) {
        if ($null -eq $activeEnvironment[$name]) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item -LiteralPath "Env:$name" -Value ([string]$activeEnvironment[$name])
        }
    }
    foreach ($functionName in @('Get-Item', 'Get-FileHash')) {
        Remove-Item -LiteralPath "Function:\global:$functionName" `
            -ErrorAction SilentlyContinue
    }
    $resolvedActiveTempRoot = [IO.Path]::GetFullPath($activeTempRoot)
    $resolvedSystemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (
        $resolvedActiveTempRoot.StartsWith(
            $resolvedSystemTemp, [StringComparison]::OrdinalIgnoreCase
        ) -and
        (Split-Path -Leaf $resolvedActiveTempRoot) -like 'sgt-*' -and
        (Test-Path -LiteralPath $resolvedActiveTempRoot) -and
        $activeKeepTemp -cne '1'
    ) {
        Remove-Item -LiteralPath $resolvedActiveTempRoot -Recurse -Force
    }
    elseif ($activeKeepTemp -ceq '1') {
        Write-Warning "Preserved isolated successor test root: $resolvedActiveTempRoot"
    }
}
return

function New-LiveSnapshotFixture {
    $global:SuccessorSnapshotTick += 1
    $clock = $global:SuccessorSnapshotBaseClock.AddMilliseconds(
        $global:SuccessorSnapshotTick
    )
    if ($global:SuccessorSnapshotDrift -eq 'clock-window' -and
        $global:SuccessorSnapshotTick -gt 1) {
        $clock = $clock.AddSeconds(61)
    }
    $snapshot = [ordered]@{
        database_clock = $clock.ToString('o')
        schema_head_count = 1
        schema_head = '20260730_0047'
        indexes_exact = $true
        functions_exact = $true
        lease_table_count = 1
        lease_rows = 0
        active_lease_rows = 0
        runtime_boundary = $true
        safety_boundary = $true
        control_triggers_exact = $true
        commit_triggers_exact = $true
        identity_triggers_exact = $true
        global_rows = 1
        global_mode = 'paper'
        global_kill_switch = $true
        global_live_confirmation = $null
        polymarket_rows = 1
        polymarket_enabled = $true
        polymarket_mode = 'paper'
        polymarket_kill_switch = $true
        polymarket_live_confirmation = $null
        binance_rows = 1
        binance_enabled = $true
        binance_mode = 'backtest'
        binance_kill_switch = $true
        binance_live_confirmation = $null
        pending_commands = 0
        live_orders = 0
        live_positions = 0
        live_intents = 0
        live_fill_legs = 0
        live_enrollments = 0
        live_outcomes = 0
        runtime_sessions = 0
        idle_in_transaction_sessions = 0
        lock_waiters = 0
        active_pg_dump = 0
        bootstrap_audit_exact = $true
        pending_signals = 0
        live_open_or_pending_orders = 0
        fence_armed = $true
        active_lease_exists = $false
        execution_authorized = $false
    }
    switch ($global:SuccessorSnapshotDrift) {
        'schema-head' {
            $snapshot.schema_head = '20260730_0046'
        }
        'index' {
            $snapshot.indexes_exact = $false
        }
        'live-row' {
            $snapshot.live_orders = 1
        }
        'boolean-string' {
            $snapshot.global_kill_switch = 'true'
        }
        'function-source' {
            $snapshot.functions_exact = $false
        }
        'function-noop' {
            $snapshot.functions_exact = $false
        }
    }
    if ($global:SuccessorSnapshotDrift -like 'index-*') {
        $snapshot.indexes_exact = $false
    }
    return $snapshot
}

function Reset-SuccessorMockState {
    param([Parameter(Mandatory = $true)][object] $Fixture)

    $global:SuccessorFixture = $Fixture
    $global:SuccessorGitDrift = 'none'
    $global:SuccessorImageDrift = 'none'
    $global:SuccessorImageContractDrift = 'none'
    $global:SuccessorProofInspectDrift = 'none'
    $global:SuccessorComposeDrift = 'none'
    $global:SuccessorPostgresDrift = 'none'
    $global:SuccessorPostgresState = 'stopped'
    $global:SuccessorPostgresInspectCount = 0
    $global:SuccessorTopologyDrift = 'none'
    $global:SuccessorComposeTopologyCount = 0
    $global:SuccessorGlobalTopologyCount = 0
    $global:SuccessorHbaDrift = 'none'
    $global:SuccessorHbaShaCount = 0
    $global:SuccessorEventDrift = 'none'
    $global:SuccessorEventQueryCount = 0
    $global:SuccessorSnapshotDrift = 'none'
    $global:SuccessorSnapshotTick = 0
    $global:SuccessorSnapshotBaseClock = [DateTimeOffset]::UtcNow
    $global:SuccessorHashDrift = 'none'
    $global:SuccessorDockerCalls = [Collections.Generic.List[object]]::new()
    $global:SuccessorGitCalls = [Collections.Generic.List[string]]::new()
    $global:SuccessorDatabaseOperations = [Collections.Generic.List[string]]::new()
    $global:SuccessorProofImages = @{}
    $global:SuccessorProofSequence = 0
    $global:SuccessorForbiddenMutationAttempted = $false
    $global:SuccessorRunningStartedAt = [DateTimeOffset]::UtcNow.AddMilliseconds(-1).ToString('o')
    if (Test-Path -LiteralPath $Fixture.prestart -PathType Leaf) {
        $preStartJson = Get-Content -LiteralPath $Fixture.prestart -Raw
        $preStartForClock = ConvertFrom-TestJsonText -Json $preStartJson
        $acceptedForClock = [DateTimeOffset]::Parse(
            [string]$preStartForClock.payload.accepted_at,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $global:SuccessorRunningStartedAt = $acceptedForClock.AddTicks(1).ToString('o')
        $global:SuccessorSnapshotBaseClock = $acceptedForClock.AddMilliseconds(1)
    }
    $global:SuccessorMigrationFiles = @(
        1..47 | ForEach-Object {
            'migrations/versions/{0:d4}_fixture.py' -f $_
        }
    )
    $global:SuccessorGitObjects = @{
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
    $global:SuccessorHbaInventory = @(
        'PATH|/etc/postgresql/stratus-pg_hba.conf',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|19|local|stratus|stratus_app_752875213a111f12d876fef68bbf8259|||scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|20|local|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|||scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|21|local|all|/^stratus_app_[0-9a-f]{32}$|||reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|22|local|all|/^stratus_safety_app_[0-9a-f]{32}$|||reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|24|host|stratus|stratus_app_752875213a111f12d876fef68bbf8259|0.0.0.0|0.0.0.0|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|25|host|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|0.0.0.0|0.0.0.0|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|26|host|all|/^stratus_app_[0-9a-f]{32}$|0.0.0.0|0.0.0.0|reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|27|host|all|/^stratus_safety_app_[0-9a-f]{32}$|0.0.0.0|0.0.0.0|reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|29|host|stratus|stratus_app_752875213a111f12d876fef68bbf8259|::|::|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|30|host|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|::|::|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|31|host|all|/^stratus_app_[0-9a-f]{32}$|::|::|reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|32|host|all|/^stratus_safety_app_[0-9a-f]{32}$|::|::|reject||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|36|local|all|all|||scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|37|host|all|all|0.0.0.0|0.0.0.0|scram-sha-256||',
        'RULE|/etc/postgresql/stratus-pg_hba.conf|38|host|all|all|::|::|scram-sha-256||'
    )
    if ((Get-TextSha256 -Value ($global:SuccessorHbaInventory -join "`n")) -cne
        'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567') {
        throw 'HBA inventory fixture is not canonical.'
    }

    $global:SuccessorTrustedHashes = @{}
    $hashes = @{
        'pre-migration-attestation.json' = 'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189'
        '09-stopped-migrate-stage-acceptance.json' = 'bc8006665fc1365fb51a55904d21f4a6b783e3929a4f1060d8168f01093766dd'
        '17-pre-migration-wrapper-false-negative.json' = 'ba881647611219d29ccf6fb68d42f1a745a3a431ede5f52fc9cf54ba028a4bea'
        '18-pre-migration-compatibility-holds.json' = '829ccf65b644fb7905a525d0a68a29bd3e9483cf2962b2ec2af144accf7c6033'
        '19-pre-migration-gate-and-0047-migration-accepted.json' = '200d1b10bfe05537c4385d87dec81c2bbb2669a69c0abcdfa2ba9217b7d9fb4f'
        '20-strict-post-ddl-0047-accepted.json' = 'c6e30ccea288f3f8c3b57130c4e8af2f40da53ad33b808cb4ddeaa43437503ff'
        '21-post-ddl-hba-accepted.json' = 'f42d979d98ec9feaa949cb23cb0add1925443bb406967cc1802e701bc0a4926c'
        'manifest.txt' = 'e2129a1149936048c164ff934e31a8fb3531adcdf6163568730c3969576d1796'
        'stratus.dump' = 'eabd66ff4e07560c00ff04deac18613497bddf3e355fddae6b688917a4e3b5fa'
        'catalog.list' = 'cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65'
        'restore-attestation.json' = '73939f689e3add8b92ba72cb4824a1c2243fa35228eaed25ffc5dab4144a3e4e'
    }
    foreach ($entry in $hashes.GetEnumerator()) {
        $base = $Fixture.backup
        if ([string]$entry.Key -match '^\d{2}-') {
            $base = $Fixture.lineage
        }
        $path = [IO.Path]::GetFullPath((Join-Path $base ([string]$entry.Key))).ToLowerInvariant()
        $global:SuccessorTrustedHashes[$path] = [string]$entry.Value
    }
    $hostHba = [IO.Path]::GetFullPath(
        (Join-Path $Fixture.root 'ops\postgres\stratus-pg_hba.conf')
    ).ToLowerInvariant()
    $global:SuccessorTrustedHashes[$hostHba] =
        '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4'
    $global:SuccessorVirtualArchivePath = [IO.Path]::GetFullPath(
        (Join-Path $Fixture.backup 'stratus.dump')
    )
}

function global:Get-Item {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [switch] $Force
    )

    $fullPath = [IO.Path]::GetFullPath($LiteralPath)
    if ($fullPath -ieq $global:SuccessorVirtualArchivePath) {
        return [pscustomobject]@{
            PSIsContainer = $false
            Attributes = [IO.FileAttributes]::Normal
            Length = [int64]2388138397
            FullName = $fullPath
        }
    }
    return Microsoft.PowerShell.Management\Get-Item `
        -LiteralPath $LiteralPath -Force:$Force
}

function global:Get-FileHash {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string] $LiteralPath,
        [string] $Algorithm = 'SHA256'
    )

    $fullPath = [IO.Path]::GetFullPath($LiteralPath).ToLowerInvariant()
    if ($global:SuccessorTrustedHashes.ContainsKey($fullPath)) {
        $hash = [string]$global:SuccessorTrustedHashes[$fullPath]
        $leaf = [IO.Path]::GetFileName($fullPath)
        if (
            ($global:SuccessorHashDrift -eq 'pre-migration' -and
                $leaf -ceq 'pre-migration-attestation.json') -or
            ($global:SuccessorHashDrift -eq 'lineage' -and
                $leaf -ceq '19-pre-migration-gate-and-0047-migration-accepted.json') -or
            ($global:SuccessorHashDrift -eq 'backup' -and $leaf -ceq 'stratus.dump') -or
            ($global:SuccessorHashDrift -eq 'restore' -and
                $leaf -ceq 'restore-attestation.json') -or
            ($global:SuccessorHashDrift -eq 'hba' -and
                $fullPath -like '*\root\ops\postgres\stratus-pg_hba.conf')
        ) {
            $hash = '0' * 64
        }
        return [pscustomobject]@{
            Algorithm = 'SHA256'
            Hash = $hash
            Path = $LiteralPath
        }
    }
    return Microsoft.PowerShell.Utility\Get-FileHash `
        -LiteralPath $LiteralPath -Algorithm $Algorithm
}

function global:git {
    $arguments = @($args | ForEach-Object { [string]$_ })
    $global:SuccessorGitCalls.Add(($arguments -join ' '))
    $global:LASTEXITCODE = 0
    if ($arguments.Count -lt 3 -or $arguments[0] -cne '-C') {
        $global:LASTEXITCODE = 90
        return 'unexpected fake git invocation'
    }
    $repository = [IO.Path]::GetFullPath($arguments[1])
    $command = @($arguments[2..($arguments.Count - 1)])
    $joined = $command -join ' '
    if ($joined -ceq 'rev-parse HEAD') {
        if ($global:SuccessorGitDrift -eq 'root-revision' -and
            $repository -ieq $global:SuccessorFixture.root) {
            return $predecessorRevision
        }
        if ($global:SuccessorGitDrift -eq 'build-revision' -and
            $repository -ieq $global:SuccessorFixture.build) {
            return $predecessorRevision
        }
        return $successorRevision
    }
    if ($joined -ceq 'status --porcelain=v1 --untracked-files=all') {
        if ($global:SuccessorGitDrift -eq 'dirty') {
            return ' M migrations/env.py'
        }
        return
    }
    if ($joined -ceq 'symbolic-ref -q --short HEAD') {
        if ($global:SuccessorGitDrift -eq 'branch') {
            return 'main'
        }
        $global:LASTEXITCODE = 1
        return
    }
    if ($joined -like 'merge-base --is-ancestor *') {
        if ($global:SuccessorGitDrift -eq 'ancestry') {
            $global:LASTEXITCODE = 1
        }
        return
    }
    if ($command.Count -eq 2 -and $command[0] -ceq 'rev-parse' -and
        $command[1] -match '^[0-9a-f]{40}:(.+)$') {
        $path = [string]$Matches[1]
        if (-not $global:SuccessorGitObjects.ContainsKey($path)) {
            $global:LASTEXITCODE = 91
            return 'unknown fake Git object'
        }
        if ($global:SuccessorGitDrift -eq 'source' -and
            $command[1].StartsWith($successorRevision + ':') -and
            $path -ceq 'migrations') {
            return 'f' * 40
        }
        return [string]$global:SuccessorGitObjects[$path]
    }
    if ($joined -ceq "ls-tree -r --name-only $successorRevision -- migrations") {
        if ($global:SuccessorGitDrift -eq 'inventory') {
            return @('migrations')
        }
        return $global:SuccessorMigrationFiles
    }
    $global:LASTEXITCODE = 92
    return 'unexpected fake git invocation'
}

function global:docker {
    $arguments = @($args | ForEach-Object { [string]$_ })
    $joined = $arguments -join ' '
    $global:SuccessorDockerCalls.Add([pscustomobject]@{
        arguments = $arguments
        joined = $joined
    })
    $global:LASTEXITCODE = 0

    $mutatingTopLevel = @('start', 'restart', 'stop', 'kill', 'update', 'rename')
    if ($arguments.Count -gt 0 -and $arguments[0] -cin $mutatingTopLevel) {
        $global:SuccessorForbiddenMutationAttempted = $true
        $global:LASTEXITCODE = 93
        return 'forbidden lifecycle mutation'
    }
    if ($arguments.Count -gt 0 -and $arguments[0] -ceq 'compose' -and
        @($arguments | Where-Object { $_ -cin @('up', 'run', 'start', 'restart') }).Count -gt 0) {
        $global:SuccessorForbiddenMutationAttempted = $true
        $global:LASTEXITCODE = 94
        return 'forbidden Compose mutation'
    }
    if ($joined -match '(?i)\balembic\s+upgrade\b') {
        $global:SuccessorForbiddenMutationAttempted = $true
        $global:LASTEXITCODE = 95
        return 'forbidden migration rerun'
    }

    if ($arguments.Count -gt 0 -and $arguments[0] -ceq 'events') {
        $global:SuccessorEventQueryCount += 1
        $startedAt = [DateTimeOffset]::Parse(
            $global:SuccessorRunningStartedAt,
            [Globalization.CultureInfo]::InvariantCulture
        )
        $since = [DateTimeOffset]::Parse(
            $arguments[2], [Globalization.CultureInfo]::InvariantCulture
        )
        $until = [DateTimeOffset]::Parse(
            $arguments[4], [Globalization.CultureInfo]::InvariantCulture
        )
        $eventMilliseconds = [int64]$startedAt.ToUnixTimeMilliseconds()
        if ($eventMilliseconds -lt $since.ToUnixTimeMilliseconds()) {
            $eventMilliseconds = $since.ToUnixTimeMilliseconds()
        }
        if ($eventMilliseconds -gt $until.ToUnixTimeMilliseconds()) {
            $eventMilliseconds = $until.ToUnixTimeMilliseconds()
        }
        $startNano = [int64]($eventMilliseconds * [int64]1000000)
        $events = [Collections.Generic.List[object]]::new()
        $events.Add([ordered]@{
            Type = 'container'
            Action = 'start'
            Actor = [ordered]@{ ID = $postgresId }
            timeNano = $startNano
        })
        if ($global:SuccessorEventDrift -eq 'two-starts') {
            $events.Add([ordered]@{
                Type = 'container'
                Action = 'start'
                Actor = [ordered]@{ ID = $postgresId }
                timeNano = [int64]($startNano + 1000000)
            })
        }
        if ($global:SuccessorEventDrift -eq 'after-snapshot' -and
            $global:SuccessorEventQueryCount -gt 1) {
            $events.Add([ordered]@{
                Type = 'container'
                Action = 'die'
                Actor = [ordered]@{ ID = $postgresId }
                timeNano = [int64]($startNano + 1000000)
            })
        }
        return @($events | ForEach-Object {
            $_ | ConvertTo-Json -Depth 5 -Compress
        })
    }

    if ($arguments.Count -eq 5 -and $arguments[0] -ceq 'image' -and
        $arguments[1] -ceq 'inspect' -and $arguments[3] -ceq '--format') {
        $reference = $arguments[2]
        $isPredecessor = $reference -ceq $predecessorImage -or
            $reference -ceq $predecessorImageId
        $id = if ($isPredecessor) { $predecessorImageId } else { $successorImageId }
        $revision = if ($isPredecessor) { $predecessorRevision } else { $successorRevision }
        if (-not $isPredecessor -and $global:SuccessorImageDrift -eq 'id') {
            $id = 'sha256:' + ('f' * 64)
        }
        if (-not $isPredecessor -and $global:SuccessorImageDrift -eq 'revision') {
            $revision = $predecessorRevision
        }
        $inspect = [ordered]@{
            Id = $id
            Os = 'linux'
            Architecture = 'amd64'
            Config = [ordered]@{
                User = 'stratus'
                WorkingDir = '/app'
                Entrypoint = $null
                Cmd = @('stratus-run')
                Env = @('PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin')
                Labels = [ordered]@{
                    'org.opencontainers.image.revision' = $revision
                }
            }
        }
        $json = $inspect | ConvertTo-Json -Depth 8 -Compress
        if (-not $isPredecessor -and $global:SuccessorImageDrift -eq 'array-root') {
            return '[' + $json + ']'
        }
        return $json
    }

    if ($arguments.Count -eq 8 -and $arguments[0] -ceq 'create') {
        $global:SuccessorProofSequence += 1
        $id = if ($global:SuccessorProofSequence % 2 -eq 1) {
            '1' * 64
        }
        else {
            '2' * 64
        }
        $global:SuccessorProofImages[$id] = [pscustomobject]@{
            image = $arguments[7]
            name = $arguments[2]
        }
        return $id
    }
    if ($arguments.Count -eq 4 -and $arguments[0] -ceq 'inspect' -and
        $global:SuccessorProofImages.ContainsKey($arguments[1])) {
        $id = $arguments[1]
        $proof = $global:SuccessorProofImages[$id]
        if ($global:SuccessorProofInspectDrift -eq 'failure') {
            $global:LASTEXITCODE = 71
            return 'synthetic stopped-proof inspect failure'
        }
        if ($global:SuccessorProofInspectDrift -eq 'malformed') {
            return '{malformed-stopped-proof}'
        }
        $proofInspect = [ordered]@{
            Id = $id
            Name = '/' + $proof.name
            Image = $proof.image
            State = [ordered]@{
                Status = 'created'
                Running = $false
                Pid = 0
            }
            Mounts = @()
        }
        if ($global:SuccessorProofInspectDrift -eq 'identity') {
            $proofInspect.Image = if ($proof.image -ceq $successorImageId) {
                $predecessorImageId
            }
            else {
                $successorImageId
            }
        }
        return ($proofInspect | ConvertTo-Json -Depth 6 -Compress)
    }
    if ($arguments.Count -eq 3 -and $arguments[0] -ceq 'cp') {
        $source = $arguments[1]
        $destination = $arguments[2]
        if ($source -notmatch '^([0-9a-f]{64}):/app/(.+)$') {
            $global:LASTEXITCODE = 96
            return 'malformed fake docker cp source'
        }
        $containerId = [string]$Matches[1]
        $path = ([string]$Matches[2]).Replace('\', '/')
        $image = [string]$global:SuccessorProofImages[$containerId].image
        if ($path -ceq 'migrations') {
            foreach ($migrationPath in $global:SuccessorMigrationFiles) {
                $relative = $migrationPath.Replace('/', [IO.Path]::DirectorySeparatorChar)
                $target = Join-Path $destination $relative
                [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
                $content = "reviewed migration contract: $migrationPath`n"
                if ($image -ceq $successorImageId -and
                    $global:SuccessorImageContractDrift -eq 'migration' -and
                    $migrationPath -ceq $global:SuccessorMigrationFiles[-1]) {
                    $content = "drifted successor migration byte`n"
                }
                [IO.File]::WriteAllText($target, $content, [Text.UTF8Encoding]::new($false))
            }
            return
        }
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
        $content = "reviewed image contract: $path`n"
        if ($image -ceq $successorImageId -and
            $global:SuccessorImageContractDrift -eq 'source' -and
            $path -ceq 'src/stratus/config.py') {
            $content = "drifted successor source byte`n"
        }
        [IO.File]::WriteAllText($destination, $content, [Text.UTF8Encoding]::new($false))
        return
    }
    if ($arguments.Count -eq 2 -and $arguments[0] -ceq 'rm' -and
        $global:SuccessorProofImages.ContainsKey($arguments[1])) {
        $removed = $arguments[1]
        [void]$global:SuccessorProofImages.Remove($removed)
        return $removed
    }

    if ($arguments.Count -gt 0 -and $arguments[0] -ceq 'run') {
        if ($arguments -contains $postgresImage -and
            $joined -like '*pg_restore --list /evidence/stratus.dump*') {
            if ($global:SuccessorHashDrift -eq 'catalog-probe') {
                return '0' * 64
            }
            return 'cd7f7d94bd9e3614086ead5d1dd0d20d47e7573c16a1f566c9417845d9935f65'
        }
        $global:SuccessorForbiddenMutationAttempted = $true
        $global:LASTEXITCODE = 97
        return 'unapproved docker run'
    }

    if ($arguments.Count -eq 4 -and $arguments[0] -ceq 'inspect' -and
        $arguments[1] -ceq $postgresId) {
        $global:SuccessorPostgresInspectCount += 1
        $running = $global:SuccessorPostgresState -ceq 'running'
        if ($global:SuccessorPostgresDrift -eq 'wrong-state') {
            $running = -not $running
        }
        $id = $postgresId
        $restartCount = 0
        if ($global:SuccessorPostgresDrift -eq 'id') {
            $id = 'e' * 64
        }
        if ($global:SuccessorPostgresDrift -eq 'restart-count') {
            $restartCount = 1
        }
        $startedAt = if ($running) {
            $global:SuccessorRunningStartedAt
        }
        else {
            '2026-08-22T19:39:52.897703304Z'
        }
        if ($global:SuccessorPostgresDrift -eq 'stale-start') {
            $startedAt = '2026-08-22T19:39:52.897703304Z'
        }
        if ($global:SuccessorPostgresDrift -eq 'restart-after-snapshot' -and
            $global:SuccessorPostgresInspectCount -gt 1) {
            $startedAt = ([DateTimeOffset]::Parse(
                $global:SuccessorRunningStartedAt
            ).AddSeconds(1)).ToString('o')
        }
        $inspect = [ordered]@{
            Id = $id
            Name = '/weatherpoly-postgres-1'
            Image = $postgresImageId
            RestartCount = $restartCount
            Config = [ordered]@{ Image = $postgresImage }
            State = [ordered]@{
                Status = if ($running) { 'running' } else { 'exited' }
                Running = $running
                Paused = $false
                Restarting = $false
                OOMKilled = $false
                Dead = $false
                Pid = if ($running) { 4321 } else { 0 }
                ExitCode = 0
                Error = ''
                StartedAt = $startedAt
                FinishedAt = if ($running) {
                    '0001-01-01T00:00:00Z'
                }
                else {
                    '2026-08-22T22:05:00.000000000Z'
                }
            }
            HostConfig = [ordered]@{
                RestartPolicy = [ordered]@{ Name = 'unless-stopped' }
                PortBindings = [ordered]@{
                    '5432/tcp' = @([ordered]@{
                        HostIp = '127.0.0.1'
                        HostPort = '5432'
                    })
                }
            }
            Mounts = @(
                [ordered]@{
                    Type = 'bind'
                    Source = Join-Path $global:SuccessorFixture.root `
                        'ops\postgres\stratus-pg_hba.conf'
                    Destination = '/etc/postgresql/stratus-pg_hba.conf'
                    RW = $false
                },
                [ordered]@{
                    Type = 'volume'
                    Name = 'weatherpoly_stratus-postgres'
                    Destination = '/var/lib/postgresql/data'
                    RW = $true
                }
            )
        }
        if ($global:SuccessorPostgresDrift -eq 'mount') {
            $inspect.Mounts[1].Name = 'other-volume'
        }
        $json = $inspect | ConvertTo-Json -Depth 10 -Compress
        if ($global:SuccessorPostgresDrift -eq 'array-root') {
            return '[' + $json + ']'
        }
        return $json
    }

    if ($arguments.Count -gt 0 -and $arguments[0] -ceq 'compose') {
        $expectedPrefix = @(
            'compose', '--project-directory', $global:SuccessorFixture.root,
            '-f', (Join-Path $global:SuccessorFixture.root 'docker-compose.yml'),
            '--env-file', (Join-Path $global:SuccessorFixture.root '.env'),
            '--project-name', 'weatherpoly'
        )
        if (($arguments[0..8] -join "`n") -cne ($expectedPrefix -join "`n")) {
            $global:LASTEXITCODE = 98
            return 'unexpected Compose provenance prefix'
        }
        if ($arguments.Count -eq 14 -and
            ($arguments[9..13] -join ' ') -ceq '--profile release config --format json') {
            $command = @('alembic', 'upgrade', 'head')
            if ($global:SuccessorComposeDrift -eq 'command-scalar') {
                $command = 'alembic'
            }
            $required = $true
            if ($global:SuccessorComposeDrift -eq 'dependency') {
                $required = $false
            }
            $model = [ordered]@{
                services = [ordered]@{
                    migrate = [ordered]@{
                        image = $successorImage
                        command = $command
                        restart = 'no'
                        depends_on = [ordered]@{
                            postgres = [ordered]@{
                                condition = 'service_healthy'
                                required = $required
                            }
                        }
                        environment = [ordered]@{
                            MIGRATION_DATABASE_URL = (
                                'postgresql+asyncpg://stratus:' + $secretSentinel +
                                '@postgres:5432/stratus'
                            )
                            RELEASE_IMAGE_DIGEST = $successorImageId.Substring(7)
                        }
                    }
                }
            }
            if ($global:SuccessorComposeDrift -eq 'image') {
                $model.services.migrate.image = $predecessorImage
            }
            return $model | ConvertTo-Json -Depth 10 -Compress
        }
        if ($arguments.Count -eq 13 -and
            ($arguments[9..12] -join ' ') -ceq 'ps --status running --services') {
            $global:SuccessorComposeTopologyCount += 1
            if ($global:SuccessorTopologyDrift -eq 'writer') {
                return @('postgres', 'api')
            }
            if ($global:SuccessorTopologyDrift -eq 'writer-after-snapshot' -and
                $global:SuccessorComposeTopologyCount -gt 1) {
                return @('postgres', 'api')
            }
            if ($global:SuccessorPostgresState -ceq 'running') {
                return 'postgres'
            }
            return
        }
        $global:LASTEXITCODE = 99
        return 'unexpected fake Compose invocation'
    }

    if ($joined -ceq 'ps -q --no-trunc') {
        $global:SuccessorGlobalTopologyCount += 1
        if ($global:SuccessorTopologyDrift -eq 'writer') {
            return @($postgresId, ('a' * 64))
        }
        if ($global:SuccessorTopologyDrift -eq 'writer-after-snapshot' -and
            $global:SuccessorGlobalTopologyCount -gt 1) {
            return @($postgresId, ('a' * 64))
        }
        if ($global:SuccessorPostgresState -ceq 'running') {
            return $postgresId
        }
        return
    }

    if ($arguments.Count -ge 3 -and $arguments[0] -ceq 'exec' -and
        $arguments[1] -ceq $postgresId) {
        if ($arguments[2] -ceq 'sha256sum') {
            $global:SuccessorHbaShaCount += 1
            $global:SuccessorDatabaseOperations.Add('hba-sha')
            if ($global:SuccessorHbaDrift -eq 'mounted') {
                return ('0' * 64) + '  /etc/postgresql/stratus-pg_hba.conf'
            }
            if ($global:SuccessorHbaDrift -eq 'after-snapshot' -and
                $global:SuccessorHbaShaCount -gt 2) {
                return ('0' * 64) + '  /etc/postgresql/stratus-pg_hba.conf'
            }
            return '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4  /etc/postgresql/stratus-pg_hba.conf'
        }
        if ($joined -like '*pg_reload_conf()*') {
            $global:SuccessorDatabaseOperations.Add('hba-reload')
            if ($global:SuccessorHbaDrift -eq 'reload') {
                return 'f'
            }
            return 't'
        }
        if ($joined -like '*pg_hba_file_rules*') {
            $global:SuccessorDatabaseOperations.Add('hba-inventory')
            if ($global:SuccessorHbaDrift -eq 'inventory') {
                return $global:SuccessorHbaInventory[0..14]
            }
            return $global:SuccessorHbaInventory
        }
        $global:SuccessorDatabaseOperations.Add('snapshot')
        $snapshot = New-LiveSnapshotFixture
        $json = $snapshot | ConvertTo-Json -Depth 8 -Compress
        if ($global:SuccessorSnapshotDrift -eq 'array-root') {
            return '[' + $json + ']'
        }
        if ($global:SuccessorSnapshotDrift -eq 'malformed') {
            return '{malformed-json}'
        }
        return $json
    }

    $global:LASTEXITCODE = 100
    throw "Unexpected fake Docker invocation: $joined"
}

function Invoke-SuccessorGateFixture {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('PreStart', 'Live')]
        [string] $Mode,
        [Parameter(Mandatory = $true)][object] $Fixture,
        [string] $ExpectedRevisionOverride = '',
        [string] $ExpectedImageIdOverride = '',
        [string] $CandidateImageOverride = '',
        [string] $PreStartPathOverride = '',
        [string] $LivePathOverride = ''
    )

    $revision = if ([string]::IsNullOrWhiteSpace($ExpectedRevisionOverride)) {
        $successorRevision
    }
    else {
        $ExpectedRevisionOverride
    }
    $imageId = if ([string]::IsNullOrWhiteSpace($ExpectedImageIdOverride)) {
        $successorImageId
    }
    else {
        $ExpectedImageIdOverride
    }
    $image = if ([string]::IsNullOrWhiteSpace($CandidateImageOverride)) {
        $successorImage
    }
    else {
        $CandidateImageOverride
    }
    $preStartPath = if ([string]::IsNullOrWhiteSpace($PreStartPathOverride)) {
        $Fixture.prestart
    }
    else {
        $PreStartPathOverride
    }
    $livePath = if ([string]::IsNullOrWhiteSpace($LivePathOverride)) {
        $Fixture.live
    }
    else {
        $LivePathOverride
    }
    $parameters = @{
        Mode = $Mode
        Root = $Fixture.root
        BuildContext = $Fixture.build
        BackupDirectory = $Fixture.backup
        PreMigrationAttestationPath = $Fixture.pre_migration
        LineageDirectory = $Fixture.lineage
        ExpectedRevision = $revision
        ExpectedImageId = $imageId
        CandidateImage = $image
        PostgresContainer = $postgresId
        PreStartAttestationPath = $preStartPath
    }
    if ($Mode -ceq 'Live') {
        $parameters.LiveAcceptancePath = $livePath
    }
    return & $gatePath @parameters
}

function Assert-NoForbiddenDockerMutation {
    if ($global:SuccessorForbiddenMutationAttempted) {
        throw 'The successor gate attempted a forbidden Docker mutation.'
    }
    foreach ($call in $global:SuccessorDockerCalls) {
        $arguments = @($call.arguments)
        if (
            ($arguments.Count -gt 0 -and
                $arguments[0] -cin @('start', 'restart', 'stop', 'kill', 'update')) -or
            ($arguments.Count -gt 0 -and $arguments[0] -ceq 'compose' -and
                @($arguments | Where-Object {
                    $_ -cin @('up', 'run', 'start', 'restart')
                }).Count -gt 0) -or
            $call.joined -match '(?i)\balembic\s+upgrade\b'
        ) {
            throw "Forbidden Docker mutation was recorded: $($call.joined)"
        }
    }
}

function Assert-ProofContainersCleaned {
    param([Parameter(Mandatory = $true)][string] $Label)

    if ($global:SuccessorProofImages.Count -ne 0) {
        throw "$Label leaked a stopped image-proof container."
    }
}

$script:SuccessorFailureCase = 0
function Assert-MockedGateFailure {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('PreStart', 'Live')]
        [string] $Mode,
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][string] $ExpectedMessage,
        [Parameter(Mandatory = $true)][string] $Label,
        [Parameter(Mandatory = $true)][scriptblock] $Configure,
        [hashtable] $Overrides = @{}
    )

    $script:SuccessorFailureCase += 1
    Reset-SuccessorMockState -Fixture $Fixture
    if ($Mode -ceq 'Live') {
        $global:SuccessorPostgresState = 'running'
    }
    & $Configure
    $outputPath = Join-Path $Fixture.evidence (
        'case-{0:d3}.json' -f $script:SuccessorFailureCase
    )
    $invoke = @{
        Mode = $Mode
        Fixture = $Fixture
    }
    if ($Mode -ceq 'PreStart') {
        $invoke.PreStartPathOverride = $outputPath
    }
    else {
        $invoke.LivePathOverride = $outputPath
    }
    foreach ($entry in $Overrides.GetEnumerator()) {
        $invoke[[string]$entry.Key] = $entry.Value
    }
    Assert-GateFailure -ExpectedMessage $ExpectedMessage -Label $Label -Action {
        Invoke-SuccessorGateFixture @invoke
    }
    if (Test-Path -LiteralPath $outputPath) {
        throw "$Label wrote acceptance evidence on a rejected path."
    }
    Assert-NoForbiddenDockerMutation
    Assert-ProofContainersCleaned -Label $Label
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'stratus-successor-tests-' + [guid]::NewGuid().ToString('N')
)
$reservedComposeEnvironment = [ordered]@{}
foreach ($name in @(
    'COMPOSE_FILE', 'COMPOSE_PROFILES', 'COMPOSE_PROJECT_NAME', 'COMPOSE_PATH_SEPARATOR'
)) {
    $reservedComposeEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name, [EnvironmentVariableTarget]::Process
    )
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}

try {
    [void](New-Item -ItemType Directory -Path $tempRoot)
    $fixture = New-SuccessorFixture -LiteralPath $tempRoot
    $originalPreMigrationRaw = Get-Content -LiteralPath $fixture.pre_migration -Raw

    Reset-SuccessorMockState -Fixture $fixture
    $preStartOutput = @(
        Invoke-SuccessorGateFixture -Mode PreStart -Fixture $fixture
    )
    if ($preStartOutput.Count -ne 1) {
        throw 'Positive PreStart emitted anything other than one JSON envelope.'
    }
    $preStartEnvelope = $preStartOutput[0] | ConvertFrom-Json
    if (
        $preStartEnvelope.format -cne 'stratus-post-migration-successor-prestart-v1' -or
        -not $preStartEnvelope.accepted -or
        $preStartEnvelope.payload.historical.predecessor_revision -cne $predecessorRevision -or
        $preStartEnvelope.payload.successor.revision -cne $successorRevision -or
        $preStartEnvelope.payload.successor.image_id -cne $successorImageId -or
        $preStartEnvelope.payload.successor.image_id -ceq $predecessorImageId -or
        -not $preStartEnvelope.payload.closed_contract.predecessor_contract_equals_successor -or
        -not $preStartEnvelope.payload.authorization.postgres_start_permitted -or
        $preStartEnvelope.payload.authorization.api_stage_permitted
    ) {
        throw 'Distinct-successor positive PreStart envelope is not exact.'
    }
    $preStartRaw = Get-Content -LiteralPath $fixture.prestart -Raw
    if (
        $preStartRaw -match [regex]::Escape($secretSentinel) -or
        ($preStartOutput -join "`n") -match [regex]::Escape($secretSentinel) -or
        $preStartRaw -match '(?i)password|api[_-]?key|secret|database_url|dsn|token'
    ) {
        throw 'PreStart persisted a credential value or secret-shaped field.'
    }
    if ((Get-Content -LiteralPath $fixture.pre_migration -Raw) -cne $originalPreMigrationRaw) {
        throw 'PreStart modified the immutable historical attestation.'
    }
    Assert-NoForbiddenDockerMutation
    Assert-ProofContainersCleaned -Label 'positive PreStart'

    $preStartRawBeforeLive = Get-Content -LiteralPath $fixture.prestart -Raw
    Reset-SuccessorMockState -Fixture $fixture
    $global:SuccessorPostgresState = 'running'
    $liveOutput = @(Invoke-SuccessorGateFixture -Mode Live -Fixture $fixture)
    if ($liveOutput.Count -ne 1) {
        throw 'Positive Live emitted anything other than one JSON envelope.'
    }
    $liveEnvelope = $liveOutput[0] | ConvertFrom-Json
    if (
        $liveEnvelope.format -cne 'stratus-post-migration-successor-live-v1' -or
        -not $liveEnvelope.accepted -or
        $liveEnvelope.payload.successor.revision -cne $successorRevision -or
        $liveEnvelope.payload.postgres_transition.container_id -cne $postgresId -or
        $liveEnvelope.payload.postgres_transition.state -cne 'running' -or
        $liveEnvelope.payload.postgres_transition.start_events.start_events -ne 1 -or
        $liveEnvelope.payload.postgres_transition.start_events.total_transition_events -ne 1 -or
        $liveEnvelope.payload.database.schema_head -cne '20260730_0047' -or
        -not $liveEnvelope.payload.database.indexes_exact -or
        -not $liveEnvelope.payload.database.functions_exact -or
        -not $liveEnvelope.payload.database.zero_live_state -or
        $liveEnvelope.payload.database.execution_authorized -or
        -not $liveEnvelope.payload.final_boundary.no_second_transition -or
        $liveEnvelope.payload.final_boundary.start_events.start_events -ne 1 -or
        -not $liveEnvelope.payload.authorization.api_stage_permitted -or
        $liveEnvelope.payload.authorization.worker_start_permitted -or
        $liveEnvelope.payload.authorization.migration_container_permitted
    ) {
        throw 'Positive Live successor acceptance is not fail-closed and exact.'
    }
    if ($global:SuccessorEventQueryCount -ne 2) {
        throw 'Positive Live did not prove the exact Docker start-event history twice.'
    }
    $expectedOperations = @(
        'hba-sha', 'hba-reload', 'hba-inventory', 'hba-sha', 'snapshot', 'snapshot',
        'hba-sha'
    )
    if (($global:SuccessorDatabaseOperations -join "`n") -cne
        ($expectedOperations -join "`n")) {
        throw 'Live did not prove HBA first and then repeat the strict snapshot exactly twice.'
    }
    $liveRaw = Get-Content -LiteralPath $fixture.live -Raw
    if (
        $liveRaw -match [regex]::Escape($secretSentinel) -or
        ($liveOutput -join "`n") -match [regex]::Escape($secretSentinel) -or
        $liveRaw -match '(?i)password|api[_-]?key|secret|database_url|dsn|token'
    ) {
        throw 'Live acceptance persisted a credential value or secret-shaped field.'
    }
    if (
        (Get-Content -LiteralPath $fixture.prestart -Raw) -cne $preStartRawBeforeLive -or
        (Get-Content -LiteralPath $fixture.pre_migration -Raw) -cne $originalPreMigrationRaw
    ) {
        throw 'Live modified an immutable predecessor or PreStart attestation.'
    }
    Assert-NoForbiddenDockerMutation
    Assert-ProofContainersCleaned -Label 'positive Live'

    Reset-SuccessorMockState -Fixture $fixture
    Assert-GateFailure -Label 'PreStart CreateNew overwrite barrier' `
        -ExpectedMessage 'refuses to overwrite' -Action {
            Invoke-SuccessorGateFixture -Mode PreStart -Fixture $fixture
        }
    if ((Get-Content -LiteralPath $fixture.prestart -Raw) -cne $preStartRawBeforeLive) {
        throw 'PreStart CreateNew barrier altered the existing attestation.'
    }
    if ($global:SuccessorDockerCalls.Count -ne 0) {
        throw 'PreStart overwrite refusal occurred after native proof commands.'
    }

    Reset-SuccessorMockState -Fixture $fixture
    $global:SuccessorPostgresState = 'running'
    Assert-GateFailure -Label 'Live CreateNew overwrite barrier' `
        -ExpectedMessage 'refuses to overwrite' -Action {
            Invoke-SuccessorGateFixture -Mode Live -Fixture $fixture
        }
    if ((Get-Content -LiteralPath $fixture.live -Raw) -cne $liveRaw) {
        throw 'Live CreateNew barrier altered the existing acceptance.'
    }
    if ($global:SuccessorDockerCalls.Count -ne 0) {
        throw 'Live overwrite refusal occurred after native proof commands.'
    }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'pre-migration outer hash drift' `
        -ExpectedMessage 'outer SHA-256 is not the reviewed trust root' `
        -Configure { $global:SuccessorHashDrift = 'pre-migration' }

    $tamperedAttestation = $originalPreMigrationRaw | ConvertFrom-Json
    $tamperedAttestation.payload.candidate.revision = 'f' * 40
    Write-JsonFixture -LiteralPath $fixture.pre_migration -Value $tamperedAttestation
    try {
        Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
            -Label 'tampered old attestation payload' `
            -ExpectedMessage 'payload hash is invalid' -Configure { }
    }
    finally {
        Write-Utf8NoBom -LiteralPath $fixture.pre_migration `
            -Value $originalPreMigrationRaw
    }

    Write-Utf8NoBom -LiteralPath $fixture.pre_migration -Value '[{"format":"array-root"}]'
    try {
        Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
            -Label 'historical JSON array root' `
            -ExpectedMessage 'native JSON object at its root' -Configure { }
    }
    finally {
        Write-Utf8NoBom -LiteralPath $fixture.pre_migration `
            -Value $originalPreMigrationRaw
    }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'lineage content hash drift' `
        -ExpectedMessage 'Historical lineage SHA-256 drifted' `
        -Configure { $global:SuccessorHashDrift = 'lineage' }

    $migrationLineagePath = Join-Path $fixture.lineage `
        '19-pre-migration-gate-and-0047-migration-accepted.json'
    $migrationLineageRaw = Get-Content -LiteralPath $migrationLineagePath -Raw
    $migrationLineage = $migrationLineageRaw | ConvertFrom-Json
    $migrationLineage.migration.exit_code = 1
    Write-JsonFixture -LiteralPath $migrationLineagePath -Value $migrationLineage
    try {
        Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
            -Label 'lineage semantic migration drift' `
            -ExpectedMessage 'predecessor migration acceptance lineage is not exact' `
            -Configure { }
    }
    finally {
        Write-Utf8NoBom -LiteralPath $migrationLineagePath -Value $migrationLineageRaw
    }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'successor revision drift' `
        -ExpectedMessage 'worktrees must be the exact successor revision' `
        -Configure { } -Overrides @{
            ExpectedRevisionOverride = 'a' * 40
            CandidateImageOverride = 'stratus:candidate-' + ('a' * 12)
        }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'closed migration Git source drift' `
        -ExpectedMessage 'Closed migration Git contract drifted' `
        -Configure { $global:SuccessorGitDrift = 'source' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'successor image revision label drift' `
        -ExpectedMessage 'successor candidate image identity/runtime boundary is not exact' `
        -Configure { $global:SuccessorImageDrift = 'revision' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'successor immutable image ID drift' `
        -ExpectedMessage 'successor candidate image identity/runtime boundary is not exact' `
        -Configure { $global:SuccessorImageDrift = 'id' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'image inspect JSON array root' `
        -ExpectedMessage 'native JSON object at its root' `
        -Configure { $global:SuccessorImageDrift = 'array-root' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'stopped proof inspect command failure cleanup' `
        -ExpectedMessage 'required Docker proof command failed' `
        -Configure { $global:SuccessorProofInspectDrift = 'failure' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'malformed stopped proof inspect cleanup' `
        -ExpectedMessage 'is not valid strict JSON' `
        -Configure { $global:SuccessorProofInspectDrift = 'malformed' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'stopped proof identity drift cleanup' `
        -ExpectedMessage 'proof container is not the exact newly-created stopped boundary' `
        -Configure { $global:SuccessorProofInspectDrift = 'identity' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'successor image migration byte drift' `
        -ExpectedMessage 'Successor image migration bytes drifted' `
        -Configure { $global:SuccessorImageContractDrift = 'migration' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'rendered Compose dependency drift' `
        -ExpectedMessage 'exact no-mount one-shot contract' `
        -Configure { $global:SuccessorComposeDrift = 'dependency' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'rendered Compose command scalar' `
        -ExpectedMessage 'must be a native JSON array' `
        -Configure { $global:SuccessorComposeDrift = 'command-scalar' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'retained PostgreSQL wrong identity' `
        -ExpectedMessage 'identity/topology is not exact' `
        -Configure { $global:SuccessorPostgresDrift = 'id' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'retained PostgreSQL unexpectedly running' `
        -ExpectedMessage 'cleanly stopped' `
        -Configure { $global:SuccessorPostgresDrift = 'wrong-state' }

    Assert-MockedGateFailure -Mode PreStart -Fixture $fixture `
        -Label 'unexpected application writer at PreStart' `
        -ExpectedMessage 'zero running containers' `
        -Configure { $global:SuccessorTopologyDrift = 'writer' }

    $tamperedPreStartPath = Join-Path $fixture.evidence 'prestart-tampered.json'
    $tamperedPreStart = $preStartRawBeforeLive | ConvertFrom-Json
    $tamperedPreStart.payload.successor.revision = $predecessorRevision
    Write-JsonFixture -LiteralPath $tamperedPreStartPath -Value $tamperedPreStart
    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'tampered successor PreStart attestation' `
        -ExpectedMessage 'PreStart payload hash is invalid' -Configure { } `
        -Overrides @{ PreStartPathOverride = $tamperedPreStartPath }

    $stalePreStartPath = Join-Path $fixture.evidence 'prestart-stale.json'
    $stalePreStart = ConvertFrom-TestJsonText -Json $preStartRawBeforeLive
    $staleAcceptedAt = [DateTimeOffset]::UtcNow.AddHours(-1)
    $stalePreStart.payload.accepted_at = $staleAcceptedAt.ToString('o')
    $stalePreStart.payload.validity.expires_at =
        $staleAcceptedAt.AddSeconds(600).ToString('o')
    $stalePreStart.payload.validity.postgres_start_deadline =
        $staleAcceptedAt.AddSeconds(300).ToString('o')
    Update-EnvelopePayloadHash -Envelope $stalePreStart
    Write-JsonFixture -LiteralPath $stalePreStartPath -Value $stalePreStart
    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'stale replayed PreStart authorization' `
        -ExpectedMessage 'outside its bounded Live window' -Configure { } `
        -Overrides @{ PreStartPathOverride = $stalePreStartPath }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'replayed PreStart after PostgreSQL start deadline' `
        -ExpectedMessage 'StartedAt does not prove the bounded post-PreStart restart' `
        -Configure {
            $currentPreStart = ConvertFrom-TestJsonText `
                -Json (Get-Content -LiteralPath $fixture.prestart -Raw)
            $currentAcceptedAt = [DateTimeOffset]::Parse(
                [string]$currentPreStart.payload.accepted_at,
                [Globalization.CultureInfo]::InvariantCulture
            )
            $global:SuccessorRunningStartedAt =
                $currentAcceptedAt.AddSeconds(301).ToString('o')
        }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'two PostgreSQL start events before Live validation' `
        -ExpectedMessage 'requires exactly one retained PostgreSQL start' `
        -Configure { $global:SuccessorEventDrift = 'two-starts' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'backup archive drift between PreStart and Live' `
        -ExpectedMessage 'immutable pre-0047 backup payload drifted' `
        -Configure { $global:SuccessorHashDrift = 'backup' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'restore attestation drift between PreStart and Live' `
        -ExpectedMessage 'isolated restore no longer binds' `
        -Configure { $global:SuccessorHashDrift = 'restore' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'retained PostgreSQL stale successor StartedAt' `
        -ExpectedMessage 'StartedAt does not prove' `
        -Configure { $global:SuccessorPostgresDrift = 'stale-start' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'live topology contains an application writer' `
        -ExpectedMessage 'permits only the exact production PostgreSQL' `
        -Configure { $global:SuccessorTopologyDrift = 'writer' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'mounted HBA drift before query' `
        -ExpectedMessage 'HBA bytes drifted before' `
        -Configure { $global:SuccessorHbaDrift = 'mounted' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'loaded HBA inventory drift' `
        -ExpectedMessage 'loaded PostgreSQL HBA inventory' `
        -Configure { $global:SuccessorHbaDrift = 'inventory' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'manual PostgreSQL restart after database snapshots' `
        -ExpectedMessage 'Retained PostgreSQL transitioned during the successor Live proof' `
        -Configure { $global:SuccessorPostgresDrift = 'restart-after-snapshot' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'Docker transition event appears after database snapshots' `
        -ExpectedMessage 'requires exactly one retained PostgreSQL start' `
        -Configure { $global:SuccessorEventDrift = 'after-snapshot' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'application writer appears after database snapshots' `
        -ExpectedMessage 'permits only the exact production PostgreSQL' `
        -Configure { $global:SuccessorTopologyDrift = 'writer-after-snapshot' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'HBA drift after database snapshots' `
        -ExpectedMessage 'HBA bytes drifted after the successor database snapshots' `
        -Configure { $global:SuccessorHbaDrift = 'after-snapshot' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'wrong retained schema head' `
        -ExpectedMessage 'rejected schema, database objects, or fail-closed controls' `
        -Configure { $global:SuccessorSnapshotDrift = 'schema-head' }

    foreach ($indexDimension in @(
        'method', 'opclass', 'collation', 'order', 'include', 'predicate'
    )) {
        Assert-MockedGateFailure -Mode Live -Fixture $fixture `
            -Label "exact index catalog $indexDimension drift" `
            -ExpectedMessage 'rejected schema, database objects, or fail-closed controls' `
            -Configure {
                $global:SuccessorSnapshotDrift = "index-$indexDimension"
        }
    }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'tampered retained database function definition' `
        -ExpectedMessage 'rejected schema, database objects, or fail-closed controls' `
        -Configure { $global:SuccessorSnapshotDrift = 'function-source' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'no-op retained database function definition' `
        -ExpectedMessage 'rejected schema, database objects, or fail-closed controls' `
        -Configure { $global:SuccessorSnapshotDrift = 'function-noop' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'non-zero retained live rows' `
        -ExpectedMessage 'rejected non-zero live_orders' `
        -Configure { $global:SuccessorSnapshotDrift = 'live-row' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'strict live JSON native boolean type' `
        -ExpectedMessage 'must be a native JSON boolean' `
        -Configure { $global:SuccessorSnapshotDrift = 'boolean-string' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'malformed strict live JSON' `
        -ExpectedMessage 'is not valid strict JSON' `
        -Configure { $global:SuccessorSnapshotDrift = 'malformed' }

    Assert-MockedGateFailure -Mode Live -Fixture $fixture `
        -Label 'strict live JSON singleton array root' `
        -ExpectedMessage 'native JSON object at its root' `
        -Configure { $global:SuccessorSnapshotDrift = 'array-root' }

    if (
        $gateSource -match '(?is)Invoke-Docker\s+-Arguments\s+@\(\s*''(?:start|restart|stop|kill)''' -or
        $gateSource -match '(?is)\$composeArguments\s*\+\s*@\(\s*''(?:up|run|start|restart)'''
    ) {
        throw 'Static gate source contains a direct production lifecycle mutation.'
    }

    'Post-migration successor gate tests passed.'
}
finally {
    foreach ($entry in $reservedComposeEnvironment.GetEnumerator()) {
        if ($null -eq $entry.Value) {
            Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([string]$entry.Value)
        }
    }
    foreach ($functionName in @('docker', 'git', 'Get-Item', 'Get-FileHash')) {
        Remove-Item -LiteralPath "Function:\global:$functionName" -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
