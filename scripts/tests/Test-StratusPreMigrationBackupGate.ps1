$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$gatePath = Join-Path $repoRoot 'scripts\Assert-StratusPreMigrationBackupGate.ps1'
$runbookPath = Join-Path $repoRoot 'docs\RELEASE_CUTOVER_RUNBOOK.md'
$gateSource = Get-Content -LiteralPath $gatePath -Raw
$runbookSource = Get-Content -LiteralPath $runbookPath -Raw
[void][scriptblock]::Create($gateSource)

foreach ($required in @(
    "[ValidateSet('Create', 'Resume')]",
    'stratus-backup-manifest-v2',
    '20260730_0046',
    '20260730_0047',
    'stratus-isolated-pg16-restore-v1',
    'Pinned PostgreSQL 16 did not reproduce the exact archive catalog',
    'Create mode refuses to overwrite an existing pre-migration attestation',
    'Already-0047 resume requires the existing accepted pre-migration attestation',
    'runtime_sessions',
    'idle_in_transaction_sessions',
    'lock_waiters',
    'active_pg_dump',
    "backend_type='client backend'",
    'Local PostgreSQL image does not prove the exact pinned linux/amd64 release image',
    'Production PostgreSQL does not use the exact pinned image ID and Config.Image',
    'Current PostgreSQL container identity drifted from the accepted pre-migration attestation',
    "'container_id', 'image_id', 'config_image', 'restart_count', 'started_at'",
    'live_fill_legs',
    'live_enrollments',
    'live_outcomes',
    'immediate pre-DDL live preconditions',
    'Assert-NativeJsonObject',
    'Assert-NativeJsonBoolean',
    'Assert-NativeJsonInteger',
    'Assert-NativeJsonStringArray',
    'ConvertFrom-StrictJsonObjectText',
    'command/entrypoint/user/mount/dependency/config provenance',
    "'COMPOSE_FILE'",
    "'--project-directory'",
    "'--env-file'",
    "'--project-name'",
    '[IO.FileMode]::CreateNew',
    'secrets_persisted = $false'
)) {
    if (-not $gateSource.Contains($required)) {
        throw "Pre-migration gate is missing required contract text: $required"
    }
}
foreach ($forbidden in @(
    'alembic upgrade',
    'docker compose up',
    'docker start',
    'docker stop',
    'docker restart'
)) {
    if ($gateSource.Contains($forbidden)) {
        throw "Pre-migration gate must not execute deployment/migration mutation: $forbidden"
    }
}
if ($gateSource.Contains("application_name LIKE 'stratus-%'")) {
    throw 'Pre-migration gate must count all other client backends, regardless of application_name.'
}

function Assert-GateFailure {
    param(
        [Parameter(Mandatory = $true)][scriptblock] $Action,
        [Parameter(Mandatory = $true)][string] $ExpectedMessage,
        [Parameter(Mandatory = $true)][string] $Label
    )

    $observedFailure = $false
    try {
        & $Action | Out-Null
    }
    catch {
        $observedFailure = $true
        if ([string]$_.Exception.Message -notlike "*$ExpectedMessage*") {
            throw "$Label failed for the wrong reason: $($_.Exception.Message)"
        }
    }
    if (-not $observedFailure) {
        throw "$Label was incorrectly accepted."
    }
}

$tempRoot = Join-Path (
    [IO.Path]::GetTempPath()
) ("stratus-pre-migration-gate-" + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $tempRoot)
$revision = 'a' * 40
$imageId = 'sha256:' + ('b' * 64)
$candidateImage = 'stratus:candidate-test'
$pg16Image = (
    'postgres:16-alpine@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$pg16RepoDigest = (
    'postgres@sha256:' +
    '20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416'
)
$attestationPath = Join-Path $tempRoot 'pre-migration-attestation.json'
$composePath = Join-Path $tempRoot 'docker-compose.yml'
$envPath = Join-Path $tempRoot '.env'
$composePrefix = @(
    'compose', '--project-directory', $tempRoot, '-f', $composePath,
    '--env-file', $envPath, '--project-name', 'weatherpoly'
) -join ' '

try {
    [IO.File]::WriteAllText(
        $composePath,
        "services:`n  migrate:`n    image: $candidateImage`n",
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText(
        $envPath,
        "STRATUS_IMAGE=$candidateImage`nSTRATUS_RELEASE_IMAGE_DIGEST=$($imageId.Substring(7))`n",
        [Text.UTF8Encoding]::new($false)
    )
    $archivePath = Join-Path $tempRoot 'stratus.dump'
    $catalogPath = Join-Path $tempRoot 'catalog.list'
    $manifestPath = Join-Path $tempRoot 'manifest.txt'
    $restorePath = Join-Path $tempRoot 'restore-attestation.json'
    [IO.File]::WriteAllBytes($archivePath, [Text.Encoding]::UTF8.GetBytes('archive-fixture'))
    [IO.File]::WriteAllText(
        $catalogPath,
        "1; 1259 1 TABLE public fixture stratus`n",
        [Text.UTF8Encoding]::new($false)
    )
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $catalogHash = (Get-FileHash -LiteralPath $catalogPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $archiveBytes = (Get-Item -LiteralPath $archivePath).Length
    $manifestLines = @(
        'format=stratus-backup-manifest-v2',
        'archive=stratus.dump',
        'database=stratus',
        'schema_head=20260730_0046',
        'schema_head_before=20260730_0046',
        'schema_head_after=20260730_0046',
        'started_at=2026-08-14T09:01:00Z',
        'completed_at=2026-08-14T09:02:00Z',
        "archive_bytes=$archiveBytes",
        "archive_sha256=$archiveHash",
        'catalog_entries=1',
        "catalog_sha256=$catalogHash",
        'catalog_verified=true',
        'schema_stable_during_dump=true',
        'full_restore_verified=false'
    )
    [IO.File]::WriteAllText(
        $manifestPath,
        ($manifestLines -join "`n") + "`n",
        [Text.UTF8Encoding]::new($false)
    )
    $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $restore = [ordered]@{
        format = 'stratus-isolated-pg16-restore-v1'
        accepted = $true
        isolated = $true
        postgres_image = $pg16Image
        postgres_major = 16
        restore_database = 'stratus_restore_' + ('c' * 32)
        restore_started_at = '2026-08-14T09:03:00Z'
        restore_completed_at = '2026-08-14T09:04:00Z'
        source_manifest_sha256 = $manifestHash
        source_archive_sha256 = $archiveHash
        source_catalog_sha256 = $catalogHash
        source_catalog_entries = 1
        source_schema_head = '20260730_0046'
        restored_schema_head = '20260730_0046'
        full_restore_verified = $true
    }
    [IO.File]::WriteAllText(
        $restorePath,
        ($restore | ConvertTo-Json -Depth 5),
        [Text.UTF8Encoding]::new($false)
    )

    $global:StratusFakeSchemaHead = '20260730_0046'
    $global:StratusFakeClockTick = 0
    $global:StratusFakeCatalogHash = $catalogHash
    $global:StratusFakeRevision = $revision
    $global:StratusFakeImageId = $imageId
    $global:StratusFakePinnedPostgresImageId = 'sha256:' + ('e' * 64)
    $global:StratusFakePinnedPostgresRepoDigest = $pg16RepoDigest
    $global:StratusFakePostgresImageId = $global:StratusFakePinnedPostgresImageId
    $global:StratusFakePostgresConfigImage = $pg16Image
    $global:StratusFakePostgresRestartCount = 0
    $global:StratusFakePostgresStartedAt = '2026-08-14T09:00:00Z'
    $global:StratusFakePostgresContainerId = 'd' * 64
    $global:StratusFakeRuntimeSessions = 0
    $global:StratusFakePinnedRepoDigestsScalar = $false
    $global:StratusFakeMigrateDrift = 'none'
    $global:StratusFakeLiveTypeDrift = 'none'
    function global:docker {
        $arguments = @($args | ForEach-Object { [string]$_ })
        $joined = $arguments -join ' '
        $global:LASTEXITCODE = 0
        if ($joined -like 'run --rm --network none*') {
            return $global:StratusFakeCatalogHash
        }
        if ($joined -eq "image inspect $candidateImage --format {{json .}}") {
            return ([ordered]@{
                Id = $global:StratusFakeImageId
                Config = [ordered]@{
                    User = 'stratus'
                    Entrypoint = $null
                    Labels = [ordered]@{
                        'org.opencontainers.image.revision' = $global:StratusFakeRevision
                    }
                }
            } | ConvertTo-Json -Depth 6 -Compress)
        }
        if ($joined -eq "image inspect $pg16Image --format {{json .}}") {
            $pinnedInspect = [ordered]@{
                Id = $global:StratusFakePinnedPostgresImageId
                RepoDigests = @($global:StratusFakePinnedPostgresRepoDigest)
                Os = 'linux'
                Architecture = 'amd64'
            }
            if ($global:StratusFakePinnedRepoDigestsScalar) {
                $pinnedInspect.RepoDigests = $global:StratusFakePinnedPostgresRepoDigest
            }
            return ($pinnedInspect | ConvertTo-Json -Depth 6 -Compress)
        }
        if ($joined -eq 'inspect postgres-id --format {{json .}}') {
            return ([ordered]@{
                Id = $global:StratusFakePostgresContainerId
                Name = '/weatherpoly-postgres-1'
                Image = $global:StratusFakePostgresImageId
                RestartCount = $global:StratusFakePostgresRestartCount
                Config = [ordered]@{
                    Image = $global:StratusFakePostgresConfigImage
                }
                State = [ordered]@{
                    Running = $true
                    OOMKilled = $false
                    StartedAt = $global:StratusFakePostgresStartedAt
                }
            } | ConvertTo-Json -Depth 6 -Compress)
        }
        if ($joined -eq 'inspect migrate-id --format {{json .}}') {
            $migrateInspect = [ordered]@{
                Id = 'c' * 64
                Name = '/weatherpoly-migrate-1'
                Image = $global:StratusFakeImageId
                RestartCount = 0
                State = [ordered]@{
                    Status = 'created'
                    Running = $false
                    Paused = $false
                    Restarting = $false
                    OOMKilled = $false
                    Dead = $false
                    Pid = 0
                    ExitCode = 0
                    Error = ''
                }
                Config = [ordered]@{
                    Image = $candidateImage
                    User = 'stratus'
                    Entrypoint = $null
                    Cmd = @('alembic', 'upgrade', 'head')
                    Labels = [ordered]@{
                        'org.opencontainers.image.revision' = $global:StratusFakeRevision
                        'com.docker.compose.project' = 'weatherpoly'
                        'com.docker.compose.service' = 'migrate'
                        'com.docker.compose.project.working_dir' = $tempRoot
                        'com.docker.compose.project.config_files' = $composePath
                        'com.docker.compose.config-hash' = '9' * 64
                        'com.docker.compose.image' = $global:StratusFakeImageId
                        'com.docker.compose.oneoff' = 'False'
                        'com.docker.compose.container-number' = '1'
                        'com.docker.compose.depends_on' = 'postgres:service_healthy:false'
                    }
                    Env = @('RELEASE_IMAGE_DIGEST=' + $global:StratusFakeImageId.Substring(7))
                }
                HostConfig = [ordered]@{
                    RestartPolicy = [ordered]@{
                        Name = 'no'
                        MaximumRetryCount = 0
                    }
                }
                Mounts = @()
                NetworkSettings = [ordered]@{
                    Networks = [ordered]@{
                        weatherpoly_default = [ordered]@{}
                    }
                }
            }
            if ($global:StratusFakeMigrateDrift -eq 'command') {
                $migrateInspect.Config.Cmd = @('sh', '-ec', 'hostile override')
            }
            elseif ($global:StratusFakeMigrateDrift -eq 'env-scalar') {
                $migrateInspect.Config.Env = 'RELEASE_IMAGE_DIGEST=' + $global:StratusFakeImageId.Substring(7)
            }
            elseif ($global:StratusFakeMigrateDrift -eq 'mount') {
                $migrateInspect.Mounts = @([ordered]@{
                    Type = 'bind'; Source = $tempRoot; Destination = '/host'; RW = $true
                })
            }
            elseif ($global:StratusFakeMigrateDrift -eq 'operator-key') {
                $migrateInspect.Config.Env += @('OPERATOR_API_KEY=leaked-control-authority')
            }
            return ($migrateInspect | ConvertTo-Json -Depth 10 -Compress)
        }
        if ($joined -eq "$composePrefix ps --status running --services") {
            return @()
        }
        if ($arguments.Count -ge 2 -and $arguments[0] -eq 'exec' -and $arguments[1] -eq 'postgres-id') {
            $global:StratusFakeClockTick += 1
            $clock = [DateTimeOffset]::Parse('2026-08-14T09:05:00Z').AddSeconds(
                $global:StratusFakeClockTick
            ).ToString('O')
            $live = [ordered]@{
                database_clock = $clock
                schema_head = $global:StratusFakeSchemaHead
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
                runtime_sessions = $global:StratusFakeRuntimeSessions
                idle_in_transaction_sessions = 0
                lock_waiters = 0
                active_pg_dump = 0
            }
            if ($global:StratusFakeLiveTypeDrift -eq 'boolean-string') {
                $live.global_kill_switch = 'false'
            }
            elseif ($global:StratusFakeLiveTypeDrift -eq 'integer-string') {
                $live.runtime_sessions = '0'
            }
            elseif ($global:StratusFakeLiveTypeDrift -eq 'integer-null') {
                $live.runtime_sessions = $null
            }
            return ($live | ConvertTo-Json -Depth 4 -Compress)
        }
        $global:LASTEXITCODE = 90
        return 'unexpected fake docker invocation'
    }

    $created = & $gatePath `
        -Mode Create `
        -BackupDirectory $tempRoot `
        -AttestationPath $attestationPath `
        -ExpectedRevision $revision `
        -ExpectedImageId $imageId `
        -CandidateImage $candidateImage `
        -PostgresContainer postgres-id `
        -MigrateContainer migrate-id `
        -Root $tempRoot | ConvertFrom-Json
    if (-not $created.verified -or $created.mode -ne 'create') {
        throw 'Create-mode fixture did not pass the strict pre-migration gate.'
    }
    $attestationRaw = Get-Content -LiteralPath $attestationPath -Raw
    if (
        $attestationRaw -match '(?i)password|secret|api[_-]?key|token|dsn' -or
        $attestationRaw -notmatch '"accepted"\s*:\s*true'
    ) {
        throw 'Create-mode attestation is not accepted or contains a secret-shaped field.'
    }

    $global:StratusFakeSchemaHead = '20260730_0047'
    $resumed = & $gatePath `
        -Mode Resume `
        -BackupDirectory $tempRoot `
        -AttestationPath $attestationPath `
        -ExpectedRevision $revision `
        -ExpectedImageId $imageId `
        -CandidateImage $candidateImage `
        -PostgresContainer postgres-id `
        -Root $tempRoot | ConvertFrom-Json
    if (-not $resumed.verified -or $resumed.mode -ne 'resume') {
        throw 'Already-0047 fixture did not require and accept the existing attestation.'
    }

    $negativeAttestationPath = Join-Path $tempRoot 'negative-attestation.json'
    foreach ($migrateDrift in @(
        [pscustomobject]@{
            value = 'command'
            message = 'command/entrypoint/user/mount/dependency/config provenance'
        },
        [pscustomobject]@{
            value = 'env-scalar'
            message = 'must be a native JSON array'
        },
        [pscustomobject]@{
            value = 'mount'
            message = 'command/entrypoint/user/mount/dependency/config provenance'
        },
        [pscustomobject]@{
            value = 'operator-key'
            message = 'command/entrypoint/user/mount/dependency/config provenance'
        }
    )) {
        $global:StratusFakeSchemaHead = '20260730_0046'
        $global:StratusFakeMigrateDrift = $migrateDrift.value
        Assert-GateFailure -Label "migrate $($migrateDrift.value) override" `
            -ExpectedMessage $migrateDrift.message -Action {
                & $gatePath `
                    -Mode Create `
                    -BackupDirectory $tempRoot `
                    -AttestationPath $negativeAttestationPath `
                    -ExpectedRevision $revision `
                    -ExpectedImageId $imageId `
                    -CandidateImage $candidateImage `
                    -PostgresContainer postgres-id `
                    -MigrateContainer migrate-id `
                    -Root $tempRoot
            }
    }
    $global:StratusFakeMigrateDrift = 'none'

    foreach ($liveTypeDrift in @(
        [pscustomobject]@{ value = 'boolean-string'; message = 'native JSON boolean' },
        [pscustomobject]@{ value = 'integer-string'; message = 'native JSON integer' },
        [pscustomobject]@{ value = 'integer-null'; message = 'native JSON integer' }
    )) {
        $global:StratusFakeLiveTypeDrift = $liveTypeDrift.value
        Assert-GateFailure -Label "live JSON $($liveTypeDrift.value)" `
            -ExpectedMessage $liveTypeDrift.message -Action {
                & $gatePath `
                    -Mode Create `
                    -BackupDirectory $tempRoot `
                    -AttestationPath $negativeAttestationPath `
                    -ExpectedRevision $revision `
                    -ExpectedImageId $imageId `
                    -CandidateImage $candidateImage `
                    -PostgresContainer postgres-id `
                    -MigrateContainer migrate-id `
                    -Root $tempRoot
            }
    }
    $global:StratusFakeLiveTypeDrift = 'none'

    $global:StratusFakePinnedRepoDigestsScalar = $true
    Assert-GateFailure -Label 'scalar pinned RepoDigests' `
        -ExpectedMessage 'native JSON array' -Action {
            & $gatePath `
                -Mode Create `
                -BackupDirectory $tempRoot `
                -AttestationPath $negativeAttestationPath `
                -ExpectedRevision $revision `
                -ExpectedImageId $imageId `
                -CandidateImage $candidateImage `
                -PostgresContainer postgres-id `
                -MigrateContainer migrate-id `
                -Root $tempRoot
        }
    $global:StratusFakePinnedRepoDigestsScalar = $false

    $baselineRestoreJson = $restore | ConvertTo-Json -Depth 6
    $restoreTypeCases = @(
        [pscustomobject]@{
            label = 'restore string false'
            message = 'native JSON boolean'
            mutate = { param($Value) $Value.accepted = 'false' }
        },
        [pscustomobject]@{
            label = 'restore string number'
            message = 'native JSON integer'
            mutate = { param($Value) $Value.postgres_major = '16' }
        },
        [pscustomobject]@{
            label = 'restore null boolean'
            message = 'native JSON boolean'
            mutate = { param($Value) $Value.full_restore_verified = $null }
        }
    )
    foreach ($case in $restoreTypeCases) {
        $mutatedRestore = $baselineRestoreJson | ConvertFrom-Json
        & $case.mutate $mutatedRestore
        [IO.File]::WriteAllText(
            $restorePath,
            ($mutatedRestore | ConvertTo-Json -Depth 6),
            [Text.UTF8Encoding]::new($false)
        )
        Assert-GateFailure -Label $case.label -ExpectedMessage $case.message -Action {
            & $gatePath `
                -Mode Create `
                -BackupDirectory $tempRoot `
                -AttestationPath $negativeAttestationPath `
                -ExpectedRevision $revision `
                -ExpectedImageId $imageId `
                -CandidateImage $candidateImage `
                -PostgresContainer postgres-id `
                -MigrateContainer migrate-id `
                -Root $tempRoot
        }
    }
    [IO.File]::WriteAllText(
        $restorePath,
        (ConvertTo-Json -InputObject @($restore) -Depth 6),
        [Text.UTF8Encoding]::new($false)
    )
    Assert-GateFailure -Label 'singleton-array restore root' `
        -ExpectedMessage 'native JSON object at its root' -Action {
            & $gatePath `
                -Mode Create `
                -BackupDirectory $tempRoot `
                -AttestationPath $negativeAttestationPath `
                -ExpectedRevision $revision `
                -ExpectedImageId $imageId `
                -CandidateImage $candidateImage `
                -PostgresContainer postgres-id `
                -MigrateContainer migrate-id `
                -Root $tempRoot
        }
    [IO.File]::WriteAllText(
        $restorePath,
        $baselineRestoreJson,
        [Text.UTF8Encoding]::new($false)
    )

    $global:StratusFakeSchemaHead = '20260730_0047'
    $baselineAttestationJson = Get-Content -LiteralPath $attestationPath -Raw
    $attestationTypeCases = @(
        [pscustomobject]@{
            label = 'attestation string false'
            message = 'native JSON boolean'
            mutate = { param($Value) $Value.accepted = 'false' }
        },
        [pscustomobject]@{
            label = 'attestation string number'
            message = 'native JSON integer'
            mutate = { param($Value) $Value.payload.snapshot.archive_bytes = '15' }
        },
        [pscustomobject]@{
            label = 'attestation null payload'
            message = 'native JSON object'
            mutate = { param($Value) $Value.payload = $null }
        },
        [pscustomobject]@{
            label = 'attestation scalar command'
            message = 'native JSON array'
            mutate = { param($Value) $Value.payload.migrate.command = 'alembic' }
        }
    )
    foreach ($case in $attestationTypeCases) {
        $mutatedAttestation = $baselineAttestationJson | ConvertFrom-Json
        & $case.mutate $mutatedAttestation
        [IO.File]::WriteAllText(
            $attestationPath,
            ($mutatedAttestation | ConvertTo-Json -Depth 12),
            [Text.UTF8Encoding]::new($false)
        )
        Assert-GateFailure -Label $case.label -ExpectedMessage $case.message -Action {
            & $gatePath `
                -Mode Resume `
                -BackupDirectory $tempRoot `
                -AttestationPath $attestationPath `
                -ExpectedRevision $revision `
                -ExpectedImageId $imageId `
                -CandidateImage $candidateImage `
                -PostgresContainer postgres-id `
                -Root $tempRoot
        }
    }
    $baselineAttestationObject = $baselineAttestationJson | ConvertFrom-Json
    [IO.File]::WriteAllText(
        $attestationPath,
        (ConvertTo-Json -InputObject @($baselineAttestationObject) -Depth 12),
        [Text.UTF8Encoding]::new($false)
    )
    Assert-GateFailure -Label 'singleton-array attestation root' `
        -ExpectedMessage 'native JSON object at its root' -Action {
            & $gatePath `
                -Mode Resume `
                -BackupDirectory $tempRoot `
                -AttestationPath $attestationPath `
                -ExpectedRevision $revision `
                -ExpectedImageId $imageId `
                -CandidateImage $candidateImage `
                -PostgresContainer postgres-id `
                -Root $tempRoot
        }
    [IO.File]::WriteAllText(
        $attestationPath,
        $baselineAttestationJson,
        [Text.UTF8Encoding]::new($false)
    )

    $env:COMPOSE_PROJECT_NAME = 'hostile-project'
    try {
        Assert-GateFailure -Label 'hostile Compose project override' `
            -ExpectedMessage 'override environment variable is forbidden' -Action {
                & $gatePath `
                    -Mode Resume `
                    -BackupDirectory $tempRoot `
                    -AttestationPath $attestationPath `
                    -ExpectedRevision $revision `
                    -ExpectedImageId $imageId `
                    -CandidateImage $candidateImage `
                    -PostgresContainer postgres-id `
                    -Root $tempRoot
            }
    }
    finally {
        Remove-Item Env:\COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue
    }

    $global:StratusFakeRuntimeSessions = 1
    Assert-GateFailure -Label 'evil application_name client session' `
        -ExpectedMessage 'non-zero runtime_sessions' -Action {
            & $gatePath `
                -Mode Resume `
                -BackupDirectory $tempRoot `
                -AttestationPath $attestationPath `
                -ExpectedRevision $revision `
                -ExpectedImageId $imageId `
                -CandidateImage $candidateImage `
                -PostgresContainer postgres-id `
                -Root $tempRoot
        }
    $global:StratusFakeRuntimeSessions = 0

    $wrongPinnedAttestation = Join-Path $tempRoot 'wrong-pinned-image-attestation.json'
    $global:StratusFakePinnedPostgresRepoDigest = 'postgres@sha256:' + ('f' * 64)
    Assert-GateFailure -Label 'wrong pinned PostgreSQL RepoDigest' `
        -ExpectedMessage 'exact pinned linux/amd64 release image' -Action {
            & $gatePath `
                -Mode Create `
                -BackupDirectory $tempRoot `
                -AttestationPath $wrongPinnedAttestation `
                -ExpectedRevision $revision `
                -ExpectedImageId $imageId `
                -CandidateImage $candidateImage `
                -PostgresContainer postgres-id `
                -MigrateContainer migrate-id `
                -Root $tempRoot
        }
    if (Test-Path -LiteralPath $wrongPinnedAttestation) {
        throw 'Wrong pinned PostgreSQL image produced an attestation.'
    }
    $global:StratusFakePinnedPostgresRepoDigest = $pg16RepoDigest

    $global:StratusFakePinnedPostgresImageId = 'sha256:' + ('f' * 64)
    Assert-GateFailure -Label 'production PostgreSQL local image ID mismatch' `
        -ExpectedMessage 'exact pinned image ID and Config.Image' -Action {
            & $gatePath `
                -Mode Create `
                -BackupDirectory $tempRoot `
                -AttestationPath $wrongPinnedAttestation `
                -ExpectedRevision $revision `
                -ExpectedImageId $imageId `
                -CandidateImage $candidateImage `
                -PostgresContainer postgres-id `
                -MigrateContainer migrate-id `
                -Root $tempRoot
        }
    $global:StratusFakePinnedPostgresImageId = $global:StratusFakePostgresImageId

    $identityDrifts = @(
        [pscustomobject]@{
            label = 'resume PostgreSQL Image drift'
            variable = 'StratusFakePostgresImageId'
            value = 'sha256:' + ('1' * 64)
        },
        [pscustomobject]@{
            label = 'resume PostgreSQL Config.Image drift'
            variable = 'StratusFakePostgresConfigImage'
            value = 'postgres:16-alpine@sha256:' + ('2' * 64)
        },
        [pscustomobject]@{
            label = 'resume PostgreSQL RestartCount drift'
            variable = 'StratusFakePostgresRestartCount'
            value = 1
        },
        [pscustomobject]@{
            label = 'resume PostgreSQL StartedAt drift'
            variable = 'StratusFakePostgresStartedAt'
            value = '2026-08-14T09:00:01Z'
        }
    )
    foreach ($drift in $identityDrifts) {
        $oldValue = Get-Variable -Name $drift.variable -Scope Global -ValueOnly
        Set-Variable -Name $drift.variable -Scope Global -Value $drift.value
        try {
            Assert-GateFailure -Label $drift.label `
                -ExpectedMessage 'drifted from the accepted pre-migration attestation' -Action {
                    & $gatePath `
                        -Mode Resume `
                        -BackupDirectory $tempRoot `
                        -AttestationPath $attestationPath `
                        -ExpectedRevision $revision `
                        -ExpectedImageId $imageId `
                        -CandidateImage $candidateImage `
                        -PostgresContainer postgres-id `
                        -Root $tempRoot
                }
        }
        finally {
            Set-Variable -Name $drift.variable -Scope Global -Value $oldValue
        }
    }

    if (-not $runbookSource.Contains('Assert-StratusPreMigrationBackupGate.ps1')) {
        throw 'Release runbook does not invoke the executable pre-migration gate.'
    }
}
finally {
    Remove-Item Function:\docker -ErrorAction SilentlyContinue
    Remove-Variable StratusFakeSchemaHead -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakeClockTick -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakeCatalogHash -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakeRevision -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakeImageId -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakePinnedPostgresImageId -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakePinnedPostgresRepoDigest -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakePostgresImageId -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakePostgresConfigImage -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakePostgresRestartCount -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakePostgresStartedAt -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakePostgresContainerId -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakeRuntimeSessions -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakePinnedRepoDigestsScalar -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakeMigrateDrift -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable StratusFakeLiveTypeDrift -Scope Global -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

[pscustomobject]@{
    gate = $gatePath
    create_new_attestation = $true
    already_0047_requires_accepted_attestation = $true
    exact_backup_restore_candidate_binding = $true
    immediate_live_preconditions_repeated = $true
    validated = $true
} | ConvertTo-Json -Compress
