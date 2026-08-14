# STRATUS release cutover and rollback runbook

This runbook governs the execution-cost maintenance remediation opened on 2026-07-29. It is a
PAPER-only procedure. It does not authorize LIVE, does not relax the live gate, and never removes
the Binance kill switch.

## 1. Non-negotiable invariants

- Global runtime is `paper`.
- Polymarket is enabled, `paper`, and kill-switched until the final audited resume command.
- Binance is enabled, `backtest`, and kill-switched throughout the cutover and canary.
- LIVE orders, positions, intents, fill legs, and enrollments remain zero.
- The exact candidate is built from a clean detached commit and is not rebuilt or pulled during
  deployment.
- A predecessor worker is never started after successor-epoch activation.
- `reviewed_by` in the activation API is an audit assertion, not proof of an independent human
  identity. Independent acceptance remains pending until a separately identified reviewer signs
  the release record.

During the current code-only phase, production PostgreSQL, API, worker, and `safety-control`
remain stopped and Polymarket PAPER remains paused. An already-running backup sidecar may remain
running, but it may not start PostgreSQL and its status while PostgreSQL is stopped is not
fresh-backup evidence. Apart from that sidecar, only disposable candidate/test infrastructure may
run until Section 2 has recorded the accepted immutable image identity.

Any failed invariant stops the procedure. Do not restart Docker, change venue modes, or bypass an
evidence-continuity mismatch to make a check pass.

## 2. Freeze and verify the candidate

Use a 40-character commit and a tag derived from its first 12 characters:

```powershell
$productionRoot = (Get-Location).Path
$releaseState = "FREEZE"
$releaseComposeFile = [IO.Path]::GetFullPath(
  (Join-Path $productionRoot "docker-compose.yml")
)
$releaseComposeEnvFile = [IO.Path]::GetFullPath(
  (Join-Path $productionRoot ".env")
)
foreach ($overrideName in @(
  "COMPOSE_FILE",
  "COMPOSE_PROFILES",
  "COMPOSE_PROJECT_NAME",
  "COMPOSE_PATH_SEPARATOR"
)) {
  if (Test-Path -LiteralPath "Env:$overrideName") {
    throw "Release Compose override environment variable is forbidden: $overrideName"
  }
}
foreach ($requiredFile in @($releaseComposeFile, $releaseComposeEnvFile)) {
  if (
    -not (Test-Path -LiteralPath $requiredFile -PathType Leaf) -or
    ((Get-Item -LiteralPath $requiredFile -Force).Attributes -band
      [IO.FileAttributes]::ReparsePoint)
  ) {
    throw "Release Compose provenance file is missing or linked: $requiredFile"
  }
}
if (@(
  Get-Content -LiteralPath $releaseComposeEnvFile |
    Where-Object {
      $_ -match '^\s*(?:export\s+)?COMPOSE_(?:FILE|PROFILES|PROJECT_NAME|PATH_SEPARATOR)\s*='
    }
).Count -ne 0) {
  throw "Release .env must not define a reserved Compose override variable."
}
$releaseComposeArguments = @(
  "--project-directory", $productionRoot,
  "-f", $releaseComposeFile,
  "--env-file", $releaseComposeEnvFile,
  "--project-name", "weatherpoly"
)
$revision = (git rev-parse HEAD).Trim()
if ($revision -notmatch "^[0-9a-f]{40}$") {
  throw "Release revision is not a full Git commit."
}
$productionRevision = (git -C $productionRoot rev-parse HEAD).Trim()
$productionStatus = @(
  git -C $productionRoot status --porcelain=v1 --untracked-files=all
)
if (
  $LASTEXITCODE -ne 0 -or
  $productionRevision -ne $revision -or
  $productionStatus.Count -ne 0
) {
  throw "Production checkout must be the exact clean release revision."
}
if ([string]::IsNullOrWhiteSpace($env:STRATUS_RELEASE_WORKTREE)) {
  throw "STRATUS_RELEASE_WORKTREE must name the clean detached build worktree."
}
$buildContext = [IO.Path]::GetFullPath($env:STRATUS_RELEASE_WORKTREE)
if (-not (Test-Path -LiteralPath $buildContext -PathType Container)) {
  throw "Release build worktree does not exist: $buildContext"
}
$buildContextRevision = (git -C $buildContext rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $buildContextRevision -ne $revision) {
  throw "Build worktree HEAD does not match release revision $revision."
}
$buildContextStatus = @(
  git -C $buildContext status --porcelain=v1 --untracked-files=all
)
if ($LASTEXITCODE -ne 0 -or $buildContextStatus.Count -ne 0) {
  throw "Build worktree is not clean."
}
$buildContextBranch = git -C $buildContext symbolic-ref -q --short HEAD 2>$null
if ($LASTEXITCODE -eq 0 -or -not [string]::IsNullOrWhiteSpace(
  [string]$buildContextBranch
)) {
  throw "Build worktree must be detached, not attached to a branch."
}
if ([string]::IsNullOrWhiteSpace($env:STRATUS_OPERATOR_API_KEY)) {
  throw "STRATUS_OPERATOR_API_KEY must be present in the release shell."
}
$image = "stratus:candidate-$($revision.Substring(0, 12))"
docker buildx build --platform linux/amd64 --pull --load `
  --label "org.opencontainers.image.revision=$revision" `
  --tag $image $buildContext
if ($LASTEXITCODE -ne 0) {
  throw "Exact candidate image build failed."
}
$imageInspect = @(
  docker image inspect $image | ConvertFrom-Json
)[0]
$candidateImageId = [string]$imageInspect.Id
$candidateRevision = $revision
if ($candidateImageId -notmatch "^sha256:[0-9a-f]{64}$") {
  throw "Candidate image does not have an exact local image ID."
}
$digest = $candidateImageId.Substring(7)
```

The block deterministically records the local image's 64-hex image ID without the `sha256:`
prefix. Set only
`STRATUS_IMAGE` and `STRATUS_RELEASE_IMAGE_DIGEST` in the ignored production `.env`, then run:

```powershell
& (Join-Path $buildContext "scripts\Assert-StratusReleaseImage.ps1") `
  -Image $image `
  -ExpectedDigest $digest `
  -ExpectedRevision $revision `
  -EnvFile $releaseComposeEnvFile `
  -Root $productionRoot
```

The guard must return `verified:true`, `linux/amd64`, the exact revision and digest, these Compose
services: `migrate`, `worker`, `binance-market-stream`, `api`, `safety-control`, and `telegram`,
and exact immutable resource limits of 3 GiB, 1 GiB, and 256 MiB for `worker`, `api`, and
`safety-control`, respectively. It must also prove that `OPERATOR_API_KEY` is rendered only for
`api` and the one-shot `validate-safety-credentials` service, and that the
default Compose model contains zero services, so plain `docker compose up` cannot start production
PostgreSQL, backups, validators, migrations, provisioners, or applications. The `release` profile
must render the exact reviewed ten-service release inventory; `infrastructure`, `binance`,
`telegram`, and `offsite` remain explicit opt-in profiles, and their union must render the exact
reviewed thirteen-service Compose inventory.

## 3. Apply the candidate migration and stage the API while fail closed

Entry into this section is the recorded immutable-image/load-gate transition. Only after the
Section 2 guard, revision, image ID, and release digest are accepted may the retained production
PostgreSQL container be started for the bounded backup/migration/load-gate window. Start that
existing container by ID without Compose recreation or pull; record its container ID, image ID,
restart count, and OOM state before and after startup. Every application writer, including API,
worker, Telegram, Binance market stream, and `safety-control`, remains stopped. The backup
sidecar that was allowed during the code-only phase must also be stopped before PostgreSQL starts;
the create-new backup below is the only permitted backup process during this boundary. A different
or recreated PostgreSQL container stops the cutover.

Before replacement, query PostgreSQL directly and record runtime controls, zero LIVE state,
pending commands, active epoch, schema head, and container restart counts. This procedure is
resumable across the already accepted index migration: `20260730_0046` enters the full safety
migration path; `20260730_0047` enters the verification/no-op path; any other head stops the
cutover.

Revision `20260730_0046` is the already accepted index-only operational migration for bounded
Cockpit/API reads. Revision `20260730_0047` adds the independent PAPER-canary safety lease,
PostgreSQL fences, and audited watchdog/terminal-stop contract. Apply `0047` from the exact
verified candidate while global/Polymarket kill and Binance kill are both engaged and every
application writer is stopped. In addition to the API and worker, this includes the Telegram
service, safety-control, and Binance market stream. Before the DDL starts, require zero
`stratus-api`/`stratus-runtime` database sessions, zero idle-in-transaction sessions, zero lock
waiters, and no active `pg_dump`. Retain a hash-bound, successfully restored backup of the exact
pre-migration `20260730_0046` database. The earlier `0045 → 0046` migration and restore evidence
remain historical evidence; they are not a substitute for the new `0046 → 0047` backup:

Immediately after the retained PostgreSQL container starts, its first database operations must be
the fixed pre-start-hash byte proof, controlled HBA reload, exact normalized-rule proof, and final
byte recheck below. That proof must finish before the schema-head query, backup, restore, or
migration so no privileged cutover activity can run under unproved HBA rules.

```powershell
$preStartTopology = & (
  Join-Path $buildContext "scripts\Assert-StratusPreStartTopology.ps1"
) -Root $productionRoot -RequirePostgresStopped | ConvertFrom-Json
if (
  -not $preStartTopology.verified -or
  -not $preStartTopology.read_only -or
  $preStartTopology.postgres_running -or
  [int]$preStartTopology.unexpected_running_count -ne 0
) {
  throw "Pre-start production topology was not proved safe."
}
$runningBackupCount = [int]$preStartTopology.allowed_running_backup_count
if ($runningBackupCount -eq 1) {
  docker compose @releaseComposeArguments stop backup
  if ($LASTEXITCODE -ne 0) {
    throw "Could not stop the allowed backup sidecar before PostgreSQL startup."
  }
}
$postBackupStopTopology = & (
  Join-Path $buildContext "scripts\Assert-StratusPreStartTopology.ps1"
) -Root $productionRoot -RequirePostgresStopped | ConvertFrom-Json
if (
  -not $postBackupStopTopology.verified -or
  $postBackupStopTopology.postgres_running -or
  [int]$postBackupStopTopology.allowed_running_backup_count -ne 0 -or
  [int]$postBackupStopTopology.unexpected_running_count -ne 0 -or
  [string]$postBackupStopTopology.postgres_container_id -ne
    [string]$preStartTopology.postgres_container_id
) {
  throw "Backup or application process remained after the pre-start stop boundary."
}
$postgresContainer = @([string]$preStartTopology.postgres_container_id)
$postgresBeforeStart = @(
  docker inspect $postgresContainer[0] | ConvertFrom-Json
)[0]
if (
  [string]$postgresBeforeStart.Name -ne "/weatherpoly-postgres-1" -or
  [string]$postgresBeforeStart.Image -notmatch "^sha256:[0-9a-f]{64}$" -or
  $postgresBeforeStart.State.OOMKilled
) {
  throw "Retained production PostgreSQL identity or pre-start state is invalid."
}
$postgresImageId = [string]$postgresBeforeStart.Image
$postgresRestartCount = [int]$postgresBeforeStart.RestartCount
if (-not $postgresBeforeStart.State.Running) {
  docker start $postgresContainer[0] | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not start the retained production PostgreSQL container."
  }
}
$postgresAfterStart = @(
  docker inspect $postgresContainer[0] | ConvertFrom-Json
)[0]
if (
  -not $postgresAfterStart.State.Running -or
  [string]$postgresAfterStart.Image -ne $postgresImageId -or
  [int]$postgresAfterStart.RestartCount -ne $postgresRestartCount -or
  $postgresAfterStart.State.OOMKilled
) {
  throw "Production PostgreSQL changed identity, restarted, OOMed, or failed to start."
}
$fixedReleaseHbaHash = [string]$preStartTopology.hba_sha256
if ($fixedReleaseHbaHash -cne
  "164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4"
) {
  throw "Pre-start topology did not supply the fixed reviewed release HBA hash."
}
$expectedHbaSource = [IO.Path]::GetFullPath(
  (Join-Path $productionRoot "ops\postgres\stratus-pg_hba.conf")
)
function Get-ImmediateReleaseHbaByteProof {
  $hostItem = Get-Item -LiteralPath $expectedHbaSource -Force
  if (
    $hostItem.PSIsContainer -or
    ($hostItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
  ) {
    throw "Release HBA host source is not a regular non-linked file."
  }
  $hostHash = (
    Get-FileHash -LiteralPath $expectedHbaSource -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  $mountedHashOutput = @(
    docker compose @releaseComposeArguments exec -T postgres `
      sha256sum /etc/postgresql/stratus-pg_hba.conf
  )
  $mountedHash = if (
    $LASTEXITCODE -eq 0 -and $mountedHashOutput.Count -eq 1
  ) {
    ([string]$mountedHashOutput[0] -split '\s+', 2)[0].ToLowerInvariant()
  }
  else { '' }
  [pscustomobject]@{
    host_sha256 = $hostHash
    mounted_sha256 = $mountedHash
  }
}
$immediateHbaBytesBeforeReload = Get-ImmediateReleaseHbaByteProof
if (
  $immediateHbaBytesBeforeReload.host_sha256 -cne $fixedReleaseHbaHash -or
  $immediateHbaBytesBeforeReload.mounted_sha256 -cne $fixedReleaseHbaHash
) {
  throw "HBA bytes drifted before the first post-start database operation."
}
$immediateHbaReloadProof = @(
  docker compose @releaseComposeArguments exec -T postgres sh -ec `
    'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT pg_reload_conf()"'
)
if (
  $LASTEXITCODE -ne 0 -or
  $immediateHbaReloadProof.Count -ne 1 -or
  [string]$immediateHbaReloadProof[0] -cne 't'
) {
  throw "PostgreSQL did not accept the immediate fixed-HBA reload."
}
$immediateHbaSql = @'
SELECT 'PATH|' || current_setting('hba_file');
SELECT 'RULE|' || concat_ws(
  '|', file_name, line_number::text, type,
  array_to_string(database, ','), array_to_string(user_name, ','),
  coalesce(address, ''), coalesce(netmask, ''), auth_method,
  coalesce(array_to_string(options, ','), ''), coalesce(error, '')
)
FROM pg_hba_file_rules
ORDER BY rule_number;
'@
$immediateHbaProof = @(
  $immediateHbaSql | docker compose @releaseComposeArguments exec -T postgres sh -ec `
    'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
)
$immediateHbaProofBytes = [Text.UTF8Encoding]::new($false).GetBytes(
  ($immediateHbaProof -join "`n")
)
$immediateHbaProofHasher = [Security.Cryptography.SHA256]::Create()
try {
  $immediateHbaProofSha256 = [BitConverter]::ToString(
    $immediateHbaProofHasher.ComputeHash($immediateHbaProofBytes)
  ).Replace('-', '').ToLowerInvariant()
}
finally {
  $immediateHbaProofHasher.Dispose()
}
if (
  $LASTEXITCODE -ne 0 -or
  $immediateHbaProof.Count -ne 16 -or
  [string]$immediateHbaProof[0] -cne 'PATH|/etc/postgresql/stratus-pg_hba.conf' -or
  $immediateHbaProofSha256 -cne
    '0115ddeb48f40fd145eaf53a7dee524f609e8a4e28eee46d300a3993b4695df5'
) {
  throw "The first post-start loaded HBA inventory did not match the fixed release proof."
}
$immediateHbaBytesAfterRules = Get-ImmediateReleaseHbaByteProof
if (
  $immediateHbaBytesAfterRules.host_sha256 -cne $fixedReleaseHbaHash -or
  $immediateHbaBytesAfterRules.mounted_sha256 -cne $fixedReleaseHbaHash
) {
  throw "HBA bytes changed during the first post-start loaded-rule proof."
}
$preMigrationHead = (
  docker exec $postgresContainer[0] sh -lc `
    'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"'
).Trim()
if ($LASTEXITCODE -ne 0 -or $preMigrationHead -notin @(
  "20260730_0046",
  "20260730_0047"
)) {
  throw "Unsupported schema head before candidate migration: $preMigrationHead"
}

if ($preMigrationHead -eq "20260730_0046") {
  # Stage without starting so the image can be proved before any DDL executes.
  # Compose v2.38 does not support --no-deps on `compose create`; use up
  # --no-start, which supports it and touches only the named service.
  docker compose @releaseComposeArguments --profile release up --no-start --no-deps --no-build --pull never --force-recreate migrate
  if ($LASTEXITCODE -ne 0) {
    throw "Compose could not stage the exact-candidate migrate service."
  }
  $migrateContainer = docker compose @releaseComposeArguments ps --all -q migrate
  if (@($migrateContainer).Count -ne 1) {
    throw "Expected exactly one stopped migrate container."
  }
  $migrateImageId = docker inspect $migrateContainer --format "{{.Image}}"
  if ($migrateImageId -ne "sha256:$digest") {
    docker rm -f $migrateContainer | Out-Null
    throw "Migrate container image mismatch: $migrateImageId"
  }

  # Produce one create-new source archive through the owner-scoped backup
  # service. The service is one-shot here; the ordinary looping sidecar is not
  # accepted as evidence for this migration boundary.
  $postgres16Image = (
    "postgres:16-alpine@sha256:" +
    "20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416"
  )
  $preMigrationBackupId = (
    "stratus_pre_0047_" +
    (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "_" +
    [guid]::NewGuid().ToString("N") + ".backup"
  )
  if ($preMigrationBackupId -notmatch '^stratus_pre_0047_[0-9TZ]+_[0-9a-f]{32}\.backup$') {
    throw "Generated backup evidence identity is unsafe."
  }
  $backupRoot = [IO.Path]::GetFullPath((Join-Path $productionRoot "backups"))
  $backupDirectory = Join-Path $backupRoot $preMigrationBackupId
  $preMigrationAttestation = Join-Path `
    $backupDirectory "pre-migration-attestation.json"
  $backupCommand = @'
set -eu
umask 077
target="/backups/$STRATUS_BACKUP_EVIDENCE_ID"
partial="/backups/.$STRATUS_BACKUP_EVIDENCE_ID.partial"
case "$target" in /backups/stratus_pre_0047_*.backup) ;; *) exit 1 ;; esac
test ! -e "$target"
test ! -e "$partial"
mkdir "$partial"
trap 'rm -rf -- "$partial"' EXIT HUP INT TERM
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
schema_before="$(psql -XAtq --set=ON_ERROR_STOP=1 --command='SELECT version_num FROM alembic_version')"
test "$schema_before" = 20260730_0046
timeout -s TERM 21600 pg_dump --format=custom --compress=6 --file="$partial/stratus.dump"
pg_restore --list "$partial/stratus.dump" >"$partial/catalog.list"
test -s "$partial/catalog.list"
schema_after="$(psql -XAtq --set=ON_ERROR_STOP=1 --command='SELECT version_num FROM alembic_version')"
test "$schema_after" = "$schema_before"
archive_bytes="$(stat -c '%s' "$partial/stratus.dump")"
archive_sha256="$(sha256sum "$partial/stratus.dump" | awk '{print $1}')"
catalog_entries="$(grep -c '^[0-9]' "$partial/catalog.list" || :)"
catalog_sha256="$(sha256sum "$partial/catalog.list" | awk '{print $1}')"
completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
test "$archive_bytes" -gt 0
test "$catalog_entries" -gt 0
{
  printf 'format=stratus-backup-manifest-v2\n'
  printf 'archive=stratus.dump\n'
  printf 'database=stratus\n'
  printf 'schema_head=%s\n' "$schema_before"
  printf 'schema_head_before=%s\n' "$schema_before"
  printf 'schema_head_after=%s\n' "$schema_after"
  printf 'started_at=%s\n' "$started_at"
  printf 'completed_at=%s\n' "$completed_at"
  printf 'archive_bytes=%s\n' "$archive_bytes"
  printf 'archive_sha256=%s\n' "$archive_sha256"
  printf 'catalog_entries=%s\n' "$catalog_entries"
  printf 'catalog_sha256=%s\n' "$catalog_sha256"
  printf 'catalog_verified=true\n'
  printf 'schema_stable_during_dump=true\n'
  printf 'full_restore_verified=false\n'
} >"$partial/manifest.txt"
sync
mv -T "$partial" "$target"
sync
trap - EXIT HUP INT TERM
'@
  docker compose @releaseComposeArguments --profile release run --rm --no-deps `
    -e "STRATUS_BACKUP_EVIDENCE_ID=$preMigrationBackupId" `
    backup sh -ec $backupCommand
  if ($LASTEXITCODE -ne 0) {
    throw "Create-new pre-0047 backup failed."
  }

  # Restore the complete custom archive into a network-isolated, disposable,
  # exact PostgreSQL 16 container. The evidence directory is the only mount;
  # the database lives in the disposable container layer.
  $restoreDatabase = "stratus_restore_" + [guid]::NewGuid().ToString("N")
  $restoreCommand = @'
set -eu
umask 077
export PGDATA=/tmp/stratus-restore-pgdata
restore_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
initdb --no-sync --auth=trust >/dev/null
pg_ctl -D "$PGDATA" -o "-c listen_addresses='' -c unix_socket_directories=/tmp" -w start >/dev/null
trap 'pg_ctl -D "$PGDATA" -m immediate -w stop >/dev/null 2>&1 || :; rm -f -- /evidence/.restore-attestation.json.partial' EXIT HUP INT TERM
createdb -h /tmp -U postgres "$STRATUS_RESTORE_DATABASE"
pg_restore -h /tmp -U postgres -d "$STRATUS_RESTORE_DATABASE" --exit-on-error --no-owner --no-privileges /evidence/stratus.dump
restored_schema="$(psql -h /tmp -U postgres -d "$STRATUS_RESTORE_DATABASE" -XAtq --set=ON_ERROR_STOP=1 --command='SELECT version_num FROM alembic_version')"
test "$restored_schema" = 20260730_0046
server_version_num="$(psql -h /tmp -U postgres -d "$STRATUS_RESTORE_DATABASE" -XAtq --set=ON_ERROR_STOP=1 --command='SHOW server_version_num')"
case "$server_version_num" in 16*) ;; *) exit 1 ;; esac
source_schema="$(awk -F= '$1 == "schema_head" {print $2}' /evidence/manifest.txt)"
source_manifest_sha256="$(sha256sum /evidence/manifest.txt | awk '{print $1}')"
source_archive_sha256="$(sha256sum /evidence/stratus.dump | awk '{print $1}')"
source_catalog_sha256="$(sha256sum /evidence/catalog.list | awk '{print $1}')"
source_catalog_entries="$(grep -c '^[0-9]' /evidence/catalog.list || :)"
restore_completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
test ! -e /evidence/restore-attestation.json
{
  printf '{\n'
  printf '  "format": "stratus-isolated-pg16-restore-v1",\n'
  printf '  "accepted": true,\n'
  printf '  "isolated": true,\n'
  printf '  "postgres_image": "%s",\n' "$STRATUS_POSTGRES16_IMAGE"
  printf '  "postgres_major": 16,\n'
  printf '  "restore_database": "%s",\n' "$STRATUS_RESTORE_DATABASE"
  printf '  "restore_started_at": "%s",\n' "$restore_started_at"
  printf '  "restore_completed_at": "%s",\n' "$restore_completed_at"
  printf '  "source_manifest_sha256": "%s",\n' "$source_manifest_sha256"
  printf '  "source_archive_sha256": "%s",\n' "$source_archive_sha256"
  printf '  "source_catalog_sha256": "%s",\n' "$source_catalog_sha256"
  printf '  "source_catalog_entries": %s,\n' "$source_catalog_entries"
  printf '  "source_schema_head": "%s",\n' "$source_schema"
  printf '  "restored_schema_head": "%s",\n' "$restored_schema"
  printf '  "full_restore_verified": true\n'
  printf '}\n'
} >/evidence/.restore-attestation.json.partial
mv /evidence/.restore-attestation.json.partial /evidence/restore-attestation.json
sync
pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null
trap - EXIT HUP INT TERM
'@
  docker run --rm --network none --user postgres `
    --mount "type=bind,source=$backupDirectory,target=/evidence" `
    -e "STRATUS_POSTGRES16_IMAGE=$postgres16Image" `
    -e "STRATUS_RESTORE_DATABASE=$restoreDatabase" `
    $postgres16Image sh -ec $restoreCommand
  if ($LASTEXITCODE -ne 0) {
    throw "Full isolated PostgreSQL 16 restore acceptance failed."
  }

  $preMigrationGate = & (
    Join-Path $buildContext "scripts\Assert-StratusPreMigrationBackupGate.ps1"
  ) `
    -Mode Create `
    -BackupDirectory $backupDirectory `
    -AttestationPath $preMigrationAttestation `
    -ExpectedRevision $revision `
    -ExpectedImageId "sha256:$digest" `
    -CandidateImage $image `
    -PostgresContainer $postgresContainer[0] `
    -MigrateContainer $migrateContainer `
    -Root $productionRoot | ConvertFrom-Json
  if (-not $preMigrationGate.verified) {
    throw "Pre-migration backup/restore/live-state gate was not accepted."
  }

  # No diagnostic or mutation belongs between the gate's second live-state
  # proof and this DDL start.
  docker start $migrateContainer | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not start the exact-candidate migrate container."
  }
  $migrationDeadline = (Get-Date).AddMinutes(15)
  do {
    Start-Sleep -Seconds 2
    $migrateState = @(
      docker inspect $migrateContainer | ConvertFrom-Json
    )[0].State
  } while ($migrateState.Running -and (Get-Date) -lt $migrationDeadline)

  if ($migrateState.Running) {
    docker stop --time 30 $migrateContainer | Out-Null
    docker logs $migrateContainer
    throw "Exact-candidate migration exceeded the 15-minute deadline."
  }

  $migrateExitCode = [int]$migrateState.ExitCode
  docker logs $migrateContainer
  docker rm $migrateContainer | Out-Null
  if ($migrateExitCode -ne 0) {
    throw "Exact-candidate migration failed with exit code $migrateExitCode."
  }
}
else {
  if (
    [string]::IsNullOrWhiteSpace($env:STRATUS_PRE_MIGRATION_BACKUP_DIRECTORY) -or
    [string]::IsNullOrWhiteSpace($env:STRATUS_PRE_MIGRATION_ATTESTATION)
  ) {
    throw "Already-0047 resume requires the existing accepted backup attestation paths."
  }
  $backupDirectory = [IO.Path]::GetFullPath(
    $env:STRATUS_PRE_MIGRATION_BACKUP_DIRECTORY
  )
  $preMigrationAttestation = [IO.Path]::GetFullPath(
    $env:STRATUS_PRE_MIGRATION_ATTESTATION
  )
  $preMigrationGate = & (
    Join-Path $buildContext "scripts\Assert-StratusPreMigrationBackupGate.ps1"
  ) `
    -Mode Resume `
    -BackupDirectory $backupDirectory `
    -AttestationPath $preMigrationAttestation `
    -ExpectedRevision $revision `
    -ExpectedImageId "sha256:$digest" `
    -CandidateImage $image `
    -PostgresContainer $postgresContainer[0] `
    -Root $productionRoot | ConvertFrom-Json
  if (-not $preMigrationGate.verified) {
    throw "Existing pre-migration attestation/live-state gate was not accepted."
  }
  Write-Host "Schema 0047 is already accepted; skip migration service and re-prove safety objects."
}
```

The running-service assertion must return no application writer. Starting from `0045`, the guarded
command is no longer an accepted release path. Starting from `0046`, the guarded command applies
the safety-lease migration. Starting from `0047`, the migration service is not created or started:
the accepted schema, indexes, safety table, functions, and triggers are re-proved read-only,
avoiding repeated DDL and dependency churn. In the `0046` path, `--pull never`,
`--no-deps`, the pre-start image-ID assertion, and stopped-container staging are mandatory:
no DDL may execute before the exact candidate identity is proven. In the 2026-07-29 rehearsal,
dependency-enabled staging caused Compose to recreate and stop PostgreSQL while preparing the
migration container. The database recovered and no DDL had started. Both `compose create`
(because the installed version cannot combine it with `--no-deps`) and any dependency-enabled
form are prohibited. Do not use the predecessor image's migration service. After migration,
require exactly one database head, `20260730_0047`, then query `pg_index`/`pg_class` and require
all five release indexes to exist with both `indisvalid=true` and `indisready=true`. This includes
the two partial indexes that bound terminal-STOP cleanup; an invalid or not-ready cleanup index
blocks the release even when no rows currently need cleanup. Also
require `paper_canary_safety_leases`, its single-active/expiry indexes,
the lease audit/runtime/venue/request-reservation triggers, all five deferred
  execution commit-fence triggers, all four runtime identity/integrity triggers, and all six
referenced fence functions:

- `ix_accuracy_predictions_eligible_expert_ts`;
- `ix_accuracy_predictions_eligible_expert_version_market_ts`;
- `ix_model_skill_window_evaluated`;
- `ix_signals_pending_stop_cleanup`;
- `ix_orders_live_open_stop_cleanup`.

Use the following read-only assertions through the administrative cutover connection:

```sql
SELECT version_num
FROM alembic_version;

SELECT c.relname AS index_name, i.indisvalid, i.indisready
FROM pg_index AS i
JOIN pg_class AS c ON c.oid = i.indexrelid
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN (
    'ix_accuracy_predictions_eligible_expert_ts',
    'ix_accuracy_predictions_eligible_expert_version_market_ts',
    'ix_model_skill_window_evaluated',
    'ix_signals_pending_stop_cleanup',
    'ix_orders_live_open_stop_cleanup'
  )
ORDER BY c.relname;

SELECT to_regclass('public.paper_canary_safety_leases') AS lease_table;

SELECT c.relname AS index_name, i.indisvalid, i.indisready
FROM pg_index AS i
JOIN pg_class AS c ON c.oid = i.indexrelid
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN (
    'uq_paper_canary_single_active_lease',
    'ix_paper_canary_active_expiry'
  )
ORDER BY c.relname;

SELECT
  public.assert_stratus_runtime_boundary(false) AS runtime_boundary,
  public.assert_stratus_safety_boundary(false) AS safety_boundary;

WITH expected(
  relation_name,
  trigger_name,
  function_signature,
  trigger_type
) AS (
  VALUES
    (
      'paper_canary_safety_leases'::text,
      'trg_paper_canary_lease_audit'::text,
      'public.enforce_paper_canary_lease_audit()'::text,
      31::smallint
    ),
    (
      'runtime_control'::text,
      'trg_paper_canary_runtime_fence'::text,
      'public.enforce_paper_canary_runtime_fence()'::text,
      31::smallint
    ),
    (
      'venue_runtime_control'::text,
      'trg_paper_canary_venue_fence'::text,
      'public.enforce_paper_canary_venue_fence()'::text,
      31::smallint
    ),
    (
      'operator_commands'::text,
      'trg_paper_canary_request_reservation'::text,
      'public.enforce_paper_canary_request_reservation()'::text,
      31::smallint
    )
)
SELECT
  expected.relation_name,
  expected.trigger_name,
  trigger_row.tgenabled,
  trigger_row.tgtype,
  trigger_row.tgfoid::regprocedure::text AS trigger_function
FROM expected
LEFT JOIN pg_namespace AS namespace
  ON namespace.nspname = 'public'
LEFT JOIN pg_class AS relation
  ON relation.relnamespace = namespace.oid
 AND relation.relname = expected.relation_name
LEFT JOIN pg_trigger AS trigger_row
  ON trigger_row.tgrelid = relation.oid
 AND trigger_row.tgname = expected.trigger_name
 AND NOT trigger_row.tgisinternal
ORDER BY expected.relation_name;

WITH expected(relation_name, trigger_name, trigger_type) AS (
  VALUES
    ('signals'::text, 'trg_paper_canary_signal_commit_fence'::text, 21::smallint),
    ('orders'::text, 'trg_paper_canary_order_commit_fence'::text, 21::smallint),
    (
      'execution_order_intents'::text,
      'trg_paper_canary_intent_commit_fence'::text,
      5::smallint
    ),
    (
      'execution_fill_legs'::text,
      'trg_paper_canary_fill_leg_commit_fence'::text,
      5::smallint
    ),
    ('positions'::text, 'trg_paper_canary_position_commit_fence'::text, 21::smallint)
)
SELECT
  expected.relation_name,
  expected.trigger_name,
  trigger_row.tgenabled,
  trigger_row.tgtype,
  trigger_row.tgconstraint <> 0 AS constraint_backed,
  trigger_row.tgdeferrable,
  trigger_row.tginitdeferred,
  trigger_row.tgfoid::regprocedure::text AS trigger_function
FROM expected
LEFT JOIN pg_namespace AS namespace
  ON namespace.nspname = 'public'
LEFT JOIN pg_class AS relation
  ON relation.relnamespace = namespace.oid
 AND relation.relname = expected.relation_name
LEFT JOIN pg_trigger AS trigger_row
  ON trigger_row.tgrelid = relation.oid
 AND trigger_row.tgname = expected.trigger_name
 AND NOT trigger_row.tgisinternal
ORDER BY expected.relation_name;

WITH expected(relation_name, trigger_name, trigger_type) AS (
  VALUES
    ('bankroll_log'::text, 'trg_paper_canary_bankroll_identity_fence'::text, 31::smallint),
    ('orders'::text, 'trg_paper_canary_order_identity_fence'::text, 27::smallint),
    ('positions'::text, 'trg_paper_canary_position_identity_fence'::text, 27::smallint),
    ('signals'::text, 'trg_paper_canary_signal_identity_fence'::text, 27::smallint)
)
SELECT
  expected.relation_name,
  expected.trigger_name,
  trigger_row.tgenabled,
  trigger_row.tgtype,
  trigger_row.tgconstraint = 0 AS not_constraint_backed,
  NOT trigger_row.tgdeferrable AS not_deferrable,
  NOT trigger_row.tginitdeferred AS not_initially_deferred,
  trigger_row.tgfoid::regprocedure::text AS trigger_function
FROM expected
LEFT JOIN pg_namespace AS namespace
  ON namespace.nspname = 'public'
LEFT JOIN pg_class AS relation
  ON relation.relnamespace = namespace.oid
 AND relation.relname = expected.relation_name
LEFT JOIN pg_trigger AS trigger_row
  ON trigger_row.tgrelid = relation.oid
 AND trigger_row.tgname = expected.trigger_name
 AND NOT trigger_row.tgisinternal
ORDER BY expected.relation_name;
```

The first query must return exactly one row containing `20260730_0047`; the first index query must
return exactly five rows and ten `true` flags. The remaining queries must return the exact lease
table and both lease indexes with four `true` flags. Both persistent boundary assertions must
return `true`; they prove exact database-scoped role provenance and membership, no cross-role
membership, exact database/schema/table/sequence/function privileges, the single exact RLS policy,
function owner/security/search-path/PUBLIC-EXECUTE hardening, and the complete trigger boundary.
The first trigger query must return four rows; every row must have `tgenabled` equal to `O` or
`A`, the documented `tgtype`, and the exact function signature. The commit-fence query must
return five rows with the documented `tgtype`, `tgenabled` equal to `O` or `A`, all three
constraint/deferral columns `true`, and trigger function
`enforce_paper_canary_execution_commit_fence()`. A null, omission, duplicate, disabled,
immediate/non-constraint trigger, or substitute function blocks release.
The identity-fence query must return exactly four rows with `tgenabled` equal to `O` or `A`,
the documented `tgtype` (`27` for Signal/Order/Position and `31` for BankrollLog), all three
negative constraint/deferral assertions `true`, and trigger function
`enforce_paper_canary_execution_identity_fence()`. A null, omission, duplicate, disabled,
deferred/constraint trigger, or substitute function blocks release.

Any migration error, unexpected head, missing index, invalid index, or not-ready index stops the
cutover with the API and worker still off. Only after these checks pass may the verified candidate
API be staged. Create it stopped and prove its image/revision/digest before the first application
process starts. Before the runtime role is provisioned, bind the HBA proof to the fixed hash
returned by the stopped-container pre-start gate (never to a newly computed expected value),
verify both host and mounted bytes, force a controlled reload, compare the exact normalized loaded
rules, and recheck both byte sources after the query to close the TOCTOU window:

```powershell
$fixedReleaseHbaHash = [string]$preStartTopology.hba_sha256
if ($fixedReleaseHbaHash -cne
  "164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4"
) {
  throw "Pre-start topology did not supply the fixed reviewed release HBA hash."
}
$expectedHbaSource = [IO.Path]::GetFullPath(
  (Join-Path $productionRoot "ops\postgres\stratus-pg_hba.conf")
)
$postgresInspect = @(docker inspect $postgresContainer[0] | ConvertFrom-Json)[0]
$hbaMount = @(
  $postgresInspect.Mounts |
    Where-Object { [string]$_.Destination -ceq "/etc/postgresql/stratus-pg_hba.conf" }
)
$expectedSourceAliases = [Collections.Generic.List[string]]::new()
$normalizedExpectedSource = $expectedHbaSource.Replace('\', '/').TrimEnd('/').ToLowerInvariant()
$expectedSourceAliases.Add($normalizedExpectedSource)
if ($normalizedExpectedSource -match '^([a-z]):/(.*)$') {
  $drive = $Matches[1]
  $tail = $Matches[2]
  $expectedSourceAliases.Add("/run/desktop/mnt/host/$drive/$tail")
  $expectedSourceAliases.Add("/host_mnt/$drive/$tail")
}
$actualHbaSource = if ($hbaMount.Count -eq 1) {
  ([string]$hbaMount[0].Source).Replace('\', '/').TrimEnd('/').ToLowerInvariant()
}
else { '' }
if (
  $hbaMount.Count -ne 1 -or
  [string]$hbaMount[0].Type -cne 'bind' -or
  [bool]$hbaMount[0].RW -or
  $actualHbaSource -cnotin @($expectedSourceAliases)
) {
  throw "PostgreSQL HBA runtime bind mount is not the exact read-only release file."
}

function Get-FixedReleaseHbaByteProof {
  $hostItem = Get-Item -LiteralPath $expectedHbaSource -Force
  if (
    $hostItem.PSIsContainer -or
    ($hostItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
  ) {
    throw "Release HBA host source is not a regular non-linked file."
  }
  $hostHash = (
    Get-FileHash -LiteralPath $expectedHbaSource -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  $mountedHashOutput = @(
    docker compose @releaseComposeArguments exec -T postgres `
      sha256sum /etc/postgresql/stratus-pg_hba.conf
  )
  $mountedHash = if (
    $LASTEXITCODE -eq 0 -and $mountedHashOutput.Count -eq 1
  ) {
    ([string]$mountedHashOutput[0] -split '\s+', 2)[0].ToLowerInvariant()
  }
  else { '' }
  [pscustomobject]@{
    host_sha256 = $hostHash
    mounted_sha256 = $mountedHash
  }
}

$hbaBytesBeforeReload = Get-FixedReleaseHbaByteProof
if (
  $hbaBytesBeforeReload.host_sha256 -cne $fixedReleaseHbaHash -or
  $hbaBytesBeforeReload.mounted_sha256 -cne $fixedReleaseHbaHash
) {
  throw "Host or mounted HBA bytes drifted from the fixed pre-start release proof."
}
$hbaReloadProof = @(
  docker compose @releaseComposeArguments exec -T postgres sh -ec `
    'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT pg_reload_conf()"'
)
if (
  $LASTEXITCODE -ne 0 -or
  $hbaReloadProof.Count -ne 1 -or
  [string]$hbaReloadProof[0] -cne 't'
) {
  throw "PostgreSQL did not accept the controlled release-HBA reload."
}
$hbaBytesAfterReload = Get-FixedReleaseHbaByteProof
if (
  $hbaBytesAfterReload.host_sha256 -cne $fixedReleaseHbaHash -or
  $hbaBytesAfterReload.mounted_sha256 -cne $fixedReleaseHbaHash
) {
  throw "HBA bytes changed across the controlled reload boundary."
}

$expectedHbaRules = @(
  'RULE|/etc/postgresql/stratus-pg_hba.conf|19|local|stratus|stratus_app_752875213a111f12d876fef68bbf8259|||scram-sha-256||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|20|local|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|||scram-sha-256||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|21|local|all|/^stratus_app_[0-9a-f]{32}$/|||reject||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|22|local|all|/^stratus_safety_app_[0-9a-f]{32}$/|||reject||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|24|host|stratus|stratus_app_752875213a111f12d876fef68bbf8259|0.0.0.0|0.0.0.0|scram-sha-256||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|25|host|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|0.0.0.0|0.0.0.0|scram-sha-256||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|26|host|all|/^stratus_app_[0-9a-f]{32}$/|0.0.0.0|0.0.0.0|reject||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|27|host|all|/^stratus_safety_app_[0-9a-f]{32}$/|0.0.0.0|0.0.0.0|reject||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|29|host|stratus|stratus_app_752875213a111f12d876fef68bbf8259|::|::|scram-sha-256||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|30|host|stratus|stratus_safety_app_752875213a111f12d876fef68bbf8259|::|::|scram-sha-256||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|31|host|all|/^stratus_app_[0-9a-f]{32}$/|::|::|reject||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|32|host|all|/^stratus_safety_app_[0-9a-f]{32}$/|::|::|reject||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|36|local|all|all|||scram-sha-256||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|37|host|all|all|0.0.0.0|0.0.0.0|scram-sha-256||',
  'RULE|/etc/postgresql/stratus-pg_hba.conf|38|host|all|all|::|::|scram-sha-256||'
)
$hbaSql = @'
SELECT 'PATH|' || current_setting('hba_file');
SELECT 'RULE|' || concat_ws(
  '|', file_name, line_number::text, type,
  array_to_string(database, ','), array_to_string(user_name, ','),
  coalesce(address, ''), coalesce(netmask, ''), auth_method,
  coalesce(array_to_string(options, ','), ''), coalesce(error, '')
)
FROM pg_hba_file_rules
ORDER BY rule_number;
'@
$hbaProof = @(
  $hbaSql | docker compose @releaseComposeArguments exec -T postgres sh -ec `
    'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
)
if (
  $LASTEXITCODE -ne 0 -or
  $hbaProof.Count -ne (1 + $expectedHbaRules.Count) -or
  [string]$hbaProof[0] -cne 'PATH|/etc/postgresql/stratus-pg_hba.conf' -or
  (@($hbaProof | Select-Object -Skip 1) -join "`n") -cne ($expectedHbaRules -join "`n")
) {
  throw "PostgreSQL did not prove the exact normalized release HBA rule inventory after reload."
}
$hbaBytesAfterRules = Get-FixedReleaseHbaByteProof
if (
  $hbaBytesAfterRules.host_sha256 -cne $fixedReleaseHbaHash -or
  $hbaBytesAfterRules.mounted_sha256 -cne $fixedReleaseHbaHash
) {
  throw "HBA bytes changed while the normalized loaded-rule proof was collected."
}

docker compose @releaseComposeArguments --profile release run --rm --no-deps provision-runtime-role
if ($LASTEXITCODE -ne 0) {
  throw "Runtime database role activation or exact boundary proof failed."
}
docker compose @releaseComposeArguments --profile release up --no-start --no-build --pull never --no-deps --force-recreate api
if ($LASTEXITCODE -ne 0) {
  throw "Compose could not stage the exact-candidate API service."
}
$apiContainer = @(docker compose @releaseComposeArguments ps --all -q api)
if ($apiContainer.Count -ne 1) {
  throw "Expected exactly one stopped API container."
}
$apiInspect = @(
  docker inspect $apiContainer[0] | ConvertFrom-Json
)[0]
if ($apiInspect.State.Running) {
  throw "API started before candidate identity was proved."
}
if ($apiInspect.Image -ne "sha256:$digest") {
  throw "API container image mismatch: $($apiInspect.Image)"
}
if ([int64]$apiInspect.HostConfig.Memory -ne 1073741824L) {
  throw "API container memory limit is not the exact immutable 1 GiB ceiling."
}
if (
  $apiInspect.Config.Labels.'org.opencontainers.image.revision' -ne $revision
) {
  throw "API revision label mismatch."
}
if (
  @(
    $apiInspect.Config.Env |
      Where-Object { $_ -eq "RELEASE_IMAGE_DIGEST=$digest" }
  ).Count -ne 1 -or
  @(
    $apiInspect.Config.Env |
      Where-Object { [string]$_ -imatch '^OPERATOR_API_KEY=' }
  ).Count -ne 1 -or
  @(
    $apiInspect.Config.Env |
      Where-Object { [string]$_ -cmatch '^OPERATOR_API_KEY=.+$' }
  ).Count -ne 1
) {
  throw "API release digest or operator-credential inventory mismatch."
}
docker start $apiContainer[0] | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Could not start the exact-candidate API container."
}
```

Verify the running container image ID, `RELEASE_IMAGE_DIGEST`, revision label, schema
`20260730_0047`, memory/restart/OOM state, and loopback-only port. `/healthz` must return 200.
`/readyz` is expected to return 503 with `kill_switch` while the global kill is on; this is the
correct fail-closed state, not an API failure.

The candidate API must serve operator reads through the shared-pool, statement-scoped
`AUTOCOMMIT` read lane. At most three read sessions may be admitted when the production pool size
is four, leaving one connection for control writes. Read admission and pool timeouts must return
retryable HTTP 503, never an unhandled HTTP 500. Mutations continue to use normal transactional
sessions. A candidate that leaves any PostgreSQL session `idle in transaction` under the gate is
rejected even if its HTTP responses succeed.

Do not start the worker yet.

## 4. Production-size Dashboard HTTP subgate and runtime observation gate

The predecessor API was stopped after repeated Cockpit refreshes drove PostgreSQL to roughly
150-160% CPU and left long-lived sessions. Before epoch activation, the candidate must pass both
the five-minute Dashboard **HTTP subgate** and a separate, simultaneous PostgreSQL/container/
zero-LIVE/drain observation gate. Neither artifact alone is the production gate. Reproduce the
intended tiered Cockpit cadence, rather than sending the complete diagnostic request set every ten
seconds:

- every 10 seconds, refresh core trading state: health/readiness, overview, signals, positions,
  paper equity, commands, jobs, and the alerts badge;
- every 60 seconds, refresh evidence/execution state: edge, prospective validation ledger,
  selection policy, resolution reconciliation, settlement cases, storage capacity, execution
  cost, and evidence continuity;
- every 5 minutes, refresh diagnostics: Brier, CLV, root-cause actions, accuracy, forecast
  provenance, and official-resolution sources.

Before steady-state sampling, perform exactly one cold full/manual refresh that requests all three
tiers. This proves cold-path behavior without turning diagnostic queries into a ten-second storm.
Continue the tiered pattern for at least five minutes after that cold refresh.

Use the read-only Windows harness against the staged candidate API. It issues GET requests only,
uses a work-conserving queue of at most three concurrent reads, and starts each request's timeout
only when that GET actually starts. It accepts `/readyz` **only** as HTTP 503 with the validated
kill-switch fail-closed body. `/readyz` HTTP 200 is a release failure during staging, even if every
other request succeeds. The harness exits non-zero on any other HTTP error, timeout, or
three-second latency breach. It reads `STRATUS_OPERATOR_API_KEY` without including it in output;
alternatively pass a `SecureString`:

Because the worker is intentionally off during this gate, `health_audit_stale` or
`health_audit_missing` is allowed only when the readiness response also contains `kill_switch`.
`project_halted` is likewise compatible with the staged stop. `database_unavailable`,
critical/blocking audit reasons, missing `kill_switch`, and unknown reasons fail the gate. The
harness uses three transport connections because it is GET-only; the Cockpit itself limits reads
to three while reserving a fourth connection for operator mutations such as emergency Stop.

Close every running `StratusConsole`/Cockpit process before taking the telemetry baseline. The
harness assigns one 32-hex `X-Stratus-Load-Gate-Run` ID to its requests and reconciles its local
request ledger with global and per-run API-side start/completion/active counters. Any foreign
valid run ID, untagged `/api/v1` request, in-flight foreign request, or counter mismatch marks the
artifact contaminated and fails the gate. Do not dismiss contamination as harmless background
activity.

Generate one run ID and unique, commit-bound artifact paths. Start the committed runtime observer
from the verified clean detached worktree in a hidden PowerShell process first. It does not read
or serialize the inherited API key. The observer's `-Root` remains the production Compose
directory:

```powershell
$runId = [guid]::NewGuid().ToString("N")
$artifactPrefix = ".stratus-cache\load-gates\$($revision.Substring(0, 12))-$runId"
$httpArtifact = "$artifactPrefix-http.json"
$runtimeArtifact = "$artifactPrefix-runtime.json"
$acceptedArtifact = "$artifactPrefix-accepted.json"
$readyArtifact = "$artifactPrefix-ready.json"
$env:STRATUS_GATE_OBSERVER_SCRIPT = (
  Resolve-Path (
    Join-Path $buildContext "scripts\Invoke-StratusRuntimeLoadObserver.ps1"
  )
).Path
$env:STRATUS_GATE_ROOT = $productionRoot
$env:STRATUS_GATE_RUN_ID = $runId
$env:STRATUS_GATE_IMAGE_ID = "sha256:$digest"
$env:STRATUS_GATE_REVISION = $revision
$env:STRATUS_GATE_RELEASE_DIGEST = $digest
$env:STRATUS_GATE_RUNTIME_ARTIFACT = $runtimeArtifact
$env:STRATUS_GATE_READY_ARTIFACT = $readyArtifact
$observerCommand = @'
& $env:STRATUS_GATE_OBSERVER_SCRIPT `
  -Root $env:STRATUS_GATE_ROOT `
  -LoadGateRunId $env:STRATUS_GATE_RUN_ID `
  -ExpectedApiImageId $env:STRATUS_GATE_IMAGE_ID `
  -ExpectedRevision $env:STRATUS_GATE_REVISION `
  -ExpectedReleaseDigest $env:STRATUS_GATE_RELEASE_DIGEST `
  -ReadyPath $env:STRATUS_GATE_READY_ARTIFACT `
  -OutputPath $env:STRATUS_GATE_RUNTIME_ARTIFACT
exit $LASTEXITCODE
'@
$encodedObserverCommand = [Convert]::ToBase64String(
  [Text.Encoding]::Unicode.GetBytes($observerCommand)
)
$hostPowerShell = (Get-Process -Id $PID).Path
$observer = Start-Process -FilePath $hostPowerShell `
  -ArgumentList @(
    "-NoLogo", "-NoProfile", "-NonInteractive",
    "-EncodedCommand", $encodedObserverCommand
  ) -PassThru -WindowStyle Hidden
function Stop-GateObserver {
  param([Parameter(Mandatory = $true)][Diagnostics.Process]$Process)

  $Process.Refresh()
  if (-not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
  }
  if (-not $Process.WaitForExit(10000)) {
    throw "Runtime observer process could not be terminated."
  }
}
$readyDeadline = (Get-Date).AddSeconds(30)
do {
  Start-Sleep -Milliseconds 250
  $observer.Refresh()
} while (
  -not (Test-Path -LiteralPath $readyArtifact) -and
  -not $observer.HasExited -and
  (Get-Date) -lt $readyDeadline
)
if (-not (Test-Path -LiteralPath $readyArtifact)) {
  Stop-GateObserver -Process $observer
  throw "Runtime observer did not publish its safe first-sample marker."
}
$ready = Get-Content -LiteralPath $readyArtifact -Raw | ConvertFrom-Json
if (
  $ready.gate -ne "stratus_dashboard_runtime_observation_ready" -or
  $ready.load_gate_run_id -ne $runId -or
  -not $ready.preflight_safe -or
  $ready.api_image_id -ne "sha256:$digest" -or
  $ready.revision -ne $revision
) {
  Stop-GateObserver -Process $observer
  throw "Runtime observer ready marker failed identity or safety checks."
}
```

Immediately after it begins sampling, run the HTTP harness in the release terminal using the same
run ID:

```powershell
$httpExit = 2
try {
  & $hostPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File (Join-Path $buildContext "scripts\Invoke-StratusDashboardLoadGate.ps1") `
    -BaseUri "http://127.0.0.1:8000/" `
    -LoadGateRunId $runId `
    -SteadyStateSeconds 300 `
    -OutputPath $httpArtifact
  $httpExit = $LASTEXITCODE
}
finally {
  if (-not $observer.WaitForExit(150000)) {
    Stop-GateObserver -Process $observer
    throw "Runtime observer exceeded its bounded post-HTTP completion window."
  }
}
if ($httpExit -ne 0) {
    throw "Candidate Dashboard HTTP subgate failed with exit code $httpExit."
}
if ($observer.ExitCode -ne 0) {
    throw "Candidate runtime observation failed with exit code $($observer.ExitCode)."
}

& $hostPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File (Join-Path $buildContext "scripts\Assert-StratusLoadGateArtifacts.ps1") `
  -HttpArtifactPath $httpArtifact `
  -RuntimeArtifactPath $runtimeArtifact `
  -ExpectedRunId $runId `
  -ExpectedApiImageId "sha256:$digest" `
  -ExpectedRevision $revision `
  -ExpectedReleaseDigest $digest `
  -OutputPath $acceptedArtifact
if ($LASTEXITCODE -ne 0) {
    throw "Composite candidate load gate rejected the evidence."
}
```

Do not run the harness against the predecessor API or while the worker is being started/stopped.
The JSON artifact contains every endpoint status/latency/error plus per-endpoint aggregates; it
contains neither response bodies nor the API key. Its contract is
`schema_version=3`, `gate=stratus_dashboard_http_load`, `http_passed=true|false`,
`contaminated=false`, `telemetry.foreign_started=0`,
`telemetry.foreign_completed=0`,
`telemetry.counter_mismatch=false`,
`base_uri=http://127.0.0.1:8000`,
`requires_external_runtime_observation=true`, and `production_gate_passed=null`. Exit code zero
means only that this HTTP subgate passed; it never accepts the complete production gate.

Collect a separate, timestamp-aligned, read-only runtime-observation artifact in parallel. It must
contain PostgreSQL session/transaction/lock samples, API and PostgreSQL CPU/memory/restart/OOM
samples, unchanged global and venue controls, proof that LIVE orders/positions/intents remain
zero, and the five-second post-load query/session drain result. The committed observer runs for
exactly 390 seconds so it covers the worst-case HTTP schedule, samples PostgreSQL every second,
samples container resources every five seconds, then waits five seconds and proves the drain.
Its contract is `schema_version=2`,
`gate=stratus_dashboard_runtime_observation`, and `summary.runtime_passed=true`. Without both this
runtime artifact and the HTTP artifact, plus a successful
`stratus_candidate_production_load` composite assertion with the same run ID, full time overlap,
raw sample-count verification, the exact loopback endpoint, and exact image identity, the overall
production load gate is **not accepted**.

HTTP-subgate thresholds:

- zero request errors and zero client timeouts;
- zero foreign `/api/v1` requests and an exact API-side/local counter match;
- every Dashboard endpoint completes in less than the Cockpit's three-second client timeout;
- every `/readyz` sample is HTTP 503 with `status=not_ready`, contains `kill_switch`, and contains
  no disallowed readiness reason.

External runtime-observation thresholds:

- the observed API retains the exact image ID, revision label, and release digest; Compose worker,
  Telegram, and Binance-stream services remain stopped; and every database sample contains zero
  non-API `stratus-*` runtime-writer sessions, including host/external workers;
- API database sessions never exceed the configured pool bound of four;
- PostgreSQL `idle in transaction` sessions remain zero;
- PostgreSQL lock waiters remain zero;
- all active Dashboard queries drain within five seconds after load stops;
- the final drain also re-proves schema `20260730_0047`, runtime controls, zero pending commands,
  and every zero-LIVE invariant;
- at least 90% of the required one-second database and five-second resource samples are present;
- API and PostgreSQL containers have zero new restarts and are not OOM-killed;
- API memory remains below 90% of its 1 GiB limit;
- PostgreSQL CPU fails the gate if any 30-second rolling average is at least 140%, two consecutive
  five-second samples are at least 150%, or any single sample is at least 200%;
- global/venue controls and all zero-LIVE invariants remain unchanged.

Record per-endpoint count, maximum latency, error count, PostgreSQL session/lock samples, container
CPU/memory samples, and the five-second drain result. If this gate fails, stop the candidate API
and fix query bounds, server/database timeouts, or Cockpit cancellation/coalescing before
continuing.

Immediately before continuity planning, re-run
`scripts/sql/execution_cost_incident_reconciliation.sql` through a read-only production session.
Bind the SQL hash, output hash, PostgreSQL clock, exact candidate image, schema/index state,
runtime controls, and zero-LIVE counts into one release record. This second reconciliation is
required even when all application writers have remained stopped; predecessor or restore-time
artifacts cannot substitute for the pre-activation observation.

## 5. Persist and review the continuity plan

With the candidate API running and the worker stopped, authenticate using the configured
`X-API-Key`. Do not print or store the key in the release artifact.

```powershell
$headers = @{ "X-API-Key" = $env:STRATUS_OPERATOR_API_KEY }
$reason = @"
Execution-evidence semantics changed from competing markout/finalizer schedules to one
15-second composite maintenance job with disjoint claims and separate Phase-A/Phase-B
transactions. Fork execution_evidence. Derive every other lane action only from the complete
candidate manifest diff; shared-file hash changes are not by themselves semantic approval.
"@
$planBody = @{
  reviewed_by = "codex-release-owner"
  approval_reason = $reason
} | ConvertTo-Json
$plan = Invoke-RestMethod -Method Post -Headers $headers `
  -ContentType "application/json" -Body $planBody `
  -Uri "http://127.0.0.1:8000/api/v1/evidence-continuity/release-plan"
```

Require the exact candidate image digest, predecessor epoch 21, one complete six-lane plan,
`execution_evidence=fork`, no unclassified/mismatched lane, and actions for
`operational_legacy` and `selection_policy` derived from the returned manifest diff. Record the
plan ID, record hash, plan hash, release-manifest hash, lane-manifest hash, lane actions,
reviewer assertion, rationale, and candidate digest.

The endpoint persists a plan when activation is required. It does not activate it.

## 6. Activate exactly the reviewed plan

Before activation, create the candidate worker without starting it and prove its exact identity.
This prevents any scheduler process from starting before the successor epoch exists:

```powershell
docker compose @releaseComposeArguments --profile release up --no-start --no-build --pull never --no-deps --force-recreate worker
if ($LASTEXITCODE -ne 0) {
  throw "Compose could not stage the exact-candidate worker service."
}
$workerContainer = @(docker compose @releaseComposeArguments ps --all -q worker)
if ($workerContainer.Count -ne 1) {
  throw "Expected exactly one stopped worker container."
}
$workerInspect = @(
  docker inspect $workerContainer[0] | ConvertFrom-Json
)[0]
if ($workerInspect.State.Running) {
  throw "Worker started before successor activation."
}
if ($workerInspect.Image -ne "sha256:$digest") {
  throw "Worker container image mismatch: $($workerInspect.Image)"
}
if ([int64]$workerInspect.HostConfig.Memory -ne 3221225472L) {
  throw "Worker container memory limit is not the exact immutable 3 GiB ceiling."
}
if (
  $workerInspect.Config.Labels.'org.opencontainers.image.revision' -ne $revision
) {
  throw "Worker revision label mismatch."
}
if (
  @(
    $workerInspect.Config.Env |
      Where-Object { $_ -eq "RELEASE_IMAGE_DIGEST=$digest" }
  ).Count -ne 1 -or
  @(
    $workerInspect.Config.Env |
      Where-Object { [string]$_ -imatch '^OPERATOR_API_KEY=' }
  ).Count -ne 0
) {
  throw "Worker release digest or credential-isolation mismatch."
}
```

Before activation, inspect and record the retained rollback tag and exact image ID from Section
10, plus the exact API-only diagnostic procedure below. Do not start the predecessor yet: until
the successor epoch is active, the predecessor image still matches epoch 21 and cannot provide a
meaningful continuity-mismatch proof. Activation remains forbidden until the retained identity,
the candidate plan, and all killed-state preconditions are accepted.

Recompute the plan immediately before activation and require the plan hash to be unchanged. Then:

```powershell
$recomputedPlan = Invoke-RestMethod -Method Post -Headers $headers `
  -ContentType "application/json" -Body $planBody `
  -Uri "http://127.0.0.1:8000/api/v1/evidence-continuity/release-plan"
$stablePlanFields = @(
  "plan_hash",
  "release_manifest_hash",
  "lane_manifest_hash",
  "activation_manifest_hash",
  "cohort",
  "image_digest",
  "previous_epoch_id",
  "persisted_plan_id",
  "persisted_record_hash"
)
foreach ($field in $stablePlanFields) {
  if ([string]$recomputedPlan.$field -ne [string]$plan.$field) {
    throw "Continuity release plan changed before activation: $field"
  }
}
$activationBody = @{
  reviewed_by = "codex-release-owner"
  approval_reason = $reason
  expected_plan_hash = $recomputedPlan.plan_hash
} | ConvertTo-Json
$activation = Invoke-RestMethod -Method Post -Headers $headers `
  -ContentType "application/json" -Body $activationBody `
  -Uri "http://127.0.0.1:8000/api/v1/evidence-continuity/activate"
$successorEpochId = [long]$activation.activated.id
$cohort = [string]$activation.activated.cohort
if (
  $successorEpochId -lt 1 -or
  [string]::IsNullOrWhiteSpace($cohort) -or
  [string]$activation.activated.image_digest -ne $digest -or
  [string]$activation.activated.plan_hash -ne [string]$recomputedPlan.plan_hash -or
  $cohort -ne [string]$recomputedPlan.cohort
) {
  throw "Activated successor identity does not match the reviewed candidate plan."
}
```

Require one successor epoch, parent 21, the exact plan/image/manifest identities, exactly six lane
rows, and a PostgreSQL activation timestamp. `GET /api/v1/evidence-continuity` must report an
identity match for the candidate. Direct SQL must prove no predecessor evidence row was copied,
backfilled, or relabelled into a forked lane.

The activation above is a metadata-only transition while global, Polymarket, and Binance kills
remain engaged and the candidate worker remains stopped. Before Section 7 starts
`safety-control` or the worker, perform one post-activation API-only rollback-isolation proof:

1. Capture a direct PostgreSQL snapshot containing the successor epoch ID/hash, persisted plan
   ID/hash, all six lane IDs/hashes, immutable evidence counts/hashes, controls, pending commands,
   and every zero-LIVE invariant.
2. Stop the candidate API. Set only `STRATUS_IMAGE=stratus:rollback-epoch21-20260729` and
   `STRATUS_RELEASE_IMAGE_DIGEST=f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4`,
   then stage only `api` with `--no-start --no-build --pull never --no-deps --force-recreate`.
   Before starting it, require the exact retained image ID from Section 10, a stopped container,
   the same exact 1 GiB `HostConfig.Memory` and single non-empty `OPERATOR_API_KEY` entry proved
   for the candidate API in Section 3, and no running worker, migration, provisioner, or other
   application writer. Never run the
   predecessor `migrate` or `worker` service.
3. Start that predecessor API only. Require `/healthz=200`, `/readyz=503` with `kill_switch` and a
   continuity-mismatch reason, and `GET /api/v1/evidence-continuity` to report the active successor
   epoch but `execution_gate_allowed=false` for the predecessor identity. Direct PostgreSQL must
   reproduce the snapshot from step 1 exactly: no plan, epoch, lane, or evidence relabel; zero
   pending commands and zero LIVE state.
4. Stop the predecessor API. Restore only the exact candidate image and digest in `.env`, stage
   and identity-check only the candidate API with the same no-build/no-pull/no-deps procedure and
   the exact Section-3 1 GiB/operator-credential admission checks, and
   start it. Require `/healthz=200`, `/readyz=503` with `kill_switch` and no disallowed reason
   (the worker-off `health_audit_stale`/`health_audit_missing` and `project_halted` reasons allowed
   by Section 4 remain acceptable), `evidence_continuity_match`, and the unchanged step-1
   PostgreSQL snapshot.

Any ambiguity or mutation fails the release with the worker still stopped. This diagnostic swap
does not authorize a schema downgrade, predecessor worker, PAPER resume, or the operational
rollback in Section 10. Only after the exact candidate API is restored and this proof is accepted
may Section 7 begin.

## 7. Arm independent safety control and run the cold scheduler canary

Do not enqueue a Polymarket resume. The worker is not admitted until the independent safety
control is healthy, its database boundary is exact, and its watchdog has completed a fresh pass.
The runtime role was already activated and proved immediately before the API in Section 3. Now
run the two safety-control one-shot boundaries. Both control-plane API keys must be distinct,
43-256 character URL-safe high-diversity random values. Owner, runtime, and safety database
passwords must be pairwise different. The provisioner may activate the migration-owned safety
login, but must not repair ACL, RLS, trigger, function, or ownership drift:

```powershell
docker compose @releaseComposeArguments --profile release run --rm --no-deps validate-safety-credentials
if ($LASTEXITCODE -ne 0) {
  throw "Independent API credential separation/strength failed."
}
docker compose @releaseComposeArguments --profile release run --rm --no-deps provision-safety-role
if ($LASTEXITCODE -ne 0) {
  throw "Safety database role activation or exact boundary proof failed."
}
```

The fixed-hash HBA bind, controlled reload, exact normalized loaded-rule inventory, and
post-query byte recheck were already completed before the runtime role and first candidate API
were admitted in Section 3. Do not repeat provisioning or substitute a hash of the current file;
the proof remains bound to `preStartTopology.hba_sha256`. Successful runtime/safety provisioning
proves the two exact target allows, while the isolated CI HBA contract proves local/IPv4/IPv6
family rejects and sibling-database denials.

Stage `safety-control` stopped with `--no-deps`. Prove the exact candidate image/revision/digest,
fixed pool/watchdog/lease parameters, one loopback-only port, one safety-only Docker network, the
exact immutable 256 MiB memory cap, and a readiness-based container healthcheck:

```powershell
docker compose @releaseComposeArguments --profile release up --no-start --no-build --pull never --no-deps --force-recreate safety-control
if ($LASTEXITCODE -ne 0) {
  throw "Compose could not stage the exact-candidate safety-control service."
}
$safetyContainer = @(docker compose @releaseComposeArguments ps --all -q safety-control)
if ($safetyContainer.Count -ne 1) {
  throw "Expected exactly one stopped safety-control container."
}
$safetyInspect = @(docker inspect $safetyContainer[0] | ConvertFrom-Json)[0]
$safetyPublishedPorts = @(
  $safetyInspect.HostConfig.PortBindings.PSObject.Properties |
    Where-Object { $null -ne $_.Value }
)
$safetyPort = $safetyInspect.HostConfig.PortBindings.'8001/tcp'
$safetyNetworks = @(
  $safetyInspect.NetworkSettings.Networks.PSObject.Properties.Name
)
$expectedSafetyNetworks = @(
  @($preStartTopology.protected_networks) |
    Where-Object { $_ -match '(^|_)safety-plane$' }
)
$safetyNetworkProof = if ($expectedSafetyNetworks.Count -eq 1) {
  @(docker network inspect $expectedSafetyNetworks[0] | ConvertFrom-Json)
}
else {
  @()
}
$requiredSafetyEnvironment = @(
  "RELEASE_IMAGE_DIGEST=$digest",
  "SAFETY_CANARY_LEASE_TTL_SECONDS=15",
  "SAFETY_WATCHDOG_POLL_SECONDS=3",
  "STRATUS_DATABASE_POOL_SIZE=1",
  "STRATUS_DATABASE_MAX_OVERFLOW=0"
)
$missingSafetyEnvironment = @()
foreach ($expectedEnvironment in $requiredSafetyEnvironment) {
  if (@($safetyInspect.Config.Env | Where-Object { $_ -ceq $expectedEnvironment }).Count -ne 1) {
    $missingSafetyEnvironment += $expectedEnvironment
  }
}
if (
  $safetyInspect.State.Running -or
  $safetyInspect.Image -ne "sha256:$digest" -or
  $safetyInspect.Config.Labels.'org.opencontainers.image.revision' -ne $revision -or
  [int64]$safetyInspect.HostConfig.Memory -ne 268435456L -or
  @($safetyInspect.Config.Env | Where-Object {
      [string]$_ -imatch '^OPERATOR_API_KEY='
  }).Count -ne 0 -or
  $missingSafetyEnvironment.Count -ne 0 -or
  $safetyPublishedPorts.Count -ne 1 -or
  $safetyPublishedPorts[0].Name -ne "8001/tcp" -or
  @($safetyPort).Count -ne 1 -or
  [string]$safetyPort[0].HostIp -ne "127.0.0.1" -or
  [string]$safetyPort[0].HostPort -ne "8001" -or
  $safetyNetworks.Count -ne 1 -or
  $expectedSafetyNetworks.Count -ne 1 -or
  [string]$safetyNetworks[0] -cne [string]$expectedSafetyNetworks[0] -or
  $safetyNetworkProof.Count -ne 1 -or
  [string]$safetyNetworkProof[0].Name -cne [string]$expectedSafetyNetworks[0] -or
  [string]$safetyNetworkProof[0].Driver -ne "bridge" -or
  [string]$safetyNetworkProof[0].Scope -ne "local" -or
  [bool]$safetyNetworkProof[0].Internal -or
  [string]$safetyNetworkProof[0].Labels.'com.docker.compose.project' -ne
    [string]$preStartTopology.compose_project -or
  [string]$safetyNetworkProof[0].Labels.'com.docker.compose.network' -ne
    "safety-plane" -or
  (@($safetyInspect.Config.Healthcheck.Test) -join " ") -notmatch "/readyz" -or
  (@($safetyInspect.Config.Healthcheck.Test) -join " ") -match "/healthz"
) {
  throw "Safety-control stopped-container identity or isolation check failed."
}
$safetyNetworkName = [string]$safetyNetworks[0]
$postgresNetworkInspect = @(docker inspect $postgresContainer[0] | ConvertFrom-Json)[0]
$postgresNetworkNames = @(
  $postgresNetworkInspect.NetworkSettings.Networks.PSObject.Properties.Name
)
if ($postgresNetworkNames -notcontains $safetyNetworkName) {
  docker network connect --alias postgres `
    $safetyNetworkName $postgresContainer[0]
  if ($LASTEXITCODE -ne 0) {
    throw "Could not attach the retained PostgreSQL container to safety-plane."
  }
}
$postgresNetworkInspect = @(docker inspect $postgresContainer[0] | ConvertFrom-Json)[0]
$postgresNetworkNames = @(
  $postgresNetworkInspect.NetworkSettings.Networks.PSObject.Properties.Name |
    Sort-Object
)
$expectedPostgresNetworks = @(
  @($preStartTopology.protected_networks) | Sort-Object
)
$postgresSafetyEndpoint = (
  $postgresNetworkInspect.NetworkSettings.Networks.PSObject.Properties[
    $safetyNetworkName
  ].Value
)
if (
  [string]$postgresNetworkInspect.Id -ne $postgresContainer[0] -or
  [string]$postgresNetworkInspect.Image -ne $postgresImageId -or
  [int]$postgresNetworkInspect.RestartCount -ne $postgresRestartCount -or
  $postgresNetworkInspect.State.OOMKilled -or
  $postgresNetworkNames.Count -ne 2 -or
  ($postgresNetworkNames -join "`n") -cne ($expectedPostgresNetworks -join "`n") -or
  $null -eq $postgresSafetyEndpoint -or
  [string]::IsNullOrWhiteSpace([string]$postgresSafetyEndpoint.IPAddress) -or
  @($postgresSafetyEndpoint.Aliases) -notcontains "postgres"
) {
  throw "Retained PostgreSQL safety-plane attachment or identity proof failed."
}
docker start $safetyContainer[0] | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Could not start the exact-candidate safety-control container."
}
$safetyReady = $null
$safetyReadyDeadline = [DateTime]::UtcNow.AddSeconds(30)
do {
  try {
    $safetyReady = Invoke-RestMethod -TimeoutSec 3 `
      -Uri "http://127.0.0.1:8001/readyz"
  } catch {
    $safetyReady = $null
  }
  if ($null -eq $safetyReady) {
    Start-Sleep -Seconds 1
  }
} while ($null -eq $safetyReady -and [DateTime]::UtcNow -lt $safetyReadyDeadline)
$safetyHealth = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 `
  -Uri "http://127.0.0.1:8001/healthz"
if (
  $null -eq $safetyReady -or
  [string]$safetyReady.status -ne "ready" -or
  [string]$safetyReady.database_boundary -ne "exact" -or
  $safetyHealth.StatusCode -ne 200
) {
  throw "Safety-control exact boundary/watchdog readiness failed."
}
```

Only now start the verified candidate worker:

```powershell
docker start $workerContainer[0] | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Could not start the exact-candidate worker container."
}
```

Keep both venue kill switches on. Observe at least four consecutive successful
`execution-cost-maintenance` runs:

- nominal interval 15 seconds and every observed gap no greater than 20 seconds;
- one captured PostgreSQL clock per run;
- separate committed Phase-A and Phase-B results;
- no scheduled `execution-markouts` or `execution-cost-finalization`;
- no `max_instances`, overlap, traceback, error/critical alert, restart, or OOM;
- no overdue unresolved mark, no finalizable unsealed obligation, no duplicate mark/seal, and no
  invalid immutable hash;
- zero LIVE activity and unchanged Binance controls.

If there is no naturally due obligation, the scheduler topology and liveness can pass, but do not
invent production fills. Evidence-specific cases remain pending until naturally observed or
proved with an isolated non-production fixture.

Do not remove a kill switch merely because the four scheduler runs passed. The long maintenance
stop makes the predecessor health audit stale, and worker startup does not itself guarantee that
`audit_health` has completed. Wait for a new non-critical audit whose database timestamp is later
than the candidate worker start. Directly require every health-required job to be present and
fresh, stable `stalled_days`, exact continuity match, zero pending commands, and every zero-LIVE
invariant. `/readyz` may remain the expected 503 solely because the kill switch is still engaged;
any unrelated readiness reason blocks resume.

## 8. Reserve the exact PAPER resume identity

The independent safety-control and watchdog remain running from Section 7. Do not enqueue a
Polymarket resume yet. Revision 0047 accepts the exact resume identity only inside a short,
DB-clock, watchdog-observed lease; any unleased command is rejected by PostgreSQL.

Create the exact immutable resume identity, but keep it only in the release shell until the
collector emits its create-new ready marker:

```powershell
$requestId = "release-paper-" + [guid]::NewGuid().ToString("N")
$canaryRunId = [guid]::NewGuid().ToString("N")
$resumeActor = "codex-release-owner"
$resumeReason = (
  "Successor epoch and cold scheduler canary accepted; " +
  "resume Polymarket PAPER only."
)
$resumeBody = @{
  request_id = $requestId
  command = "resume"
  reason = $resumeReason
  actor = $resumeActor
  payload = @{ venue = "polymarket" }
} | ConvertTo-Json -Depth 4 -Compress
```

## 9. Sixty-minute PAPER canary

Use `scripts/Invoke-StratusPaperCanary.ps1` to capture immutable snapshots at T+0, T+5, T+15,
T+30, T+45, and T+60. Start it before submitting the reviewed Polymarket-only resume request.
The collector acquires the sole DB-clock lease, waits for an independent watchdog observation,
binds the exact resume and terminal-release identities into immutable artifacts, heartbeats every
five seconds, and uses `safety-control` rather than the worker/main API for emergency STOP. Run
`scripts/tests/Test-StratusPaperCanary.ps1`,
`scripts/tests/Test-StratusPaperCanaryArtifacts.ps1`, and
`scripts/tests/Test-StratusPaperCanaryStopBarrier.ps1` before cutover; collector
`-ValidateOnly` performs no I/O.
The release collector accepts only the fixed `0/5/15/30/45/60` schedule and refuses to start
unless the independent Polymarket-only emergency stop is armed. In a dedicated PowerShell
process, pass distinct operator and safety-control keys as `SecureString` values (or the
process-scoped `STRATUS_OPERATOR_API_KEY` and `STRATUS_SAFETY_API_KEY`). Reusing one credential
for both control planes is a release failure. Invoke:

The next two executable blocks define the ready and resume phases without running them. Execute
those definitions in the same release shell, then execute the final block, which defines the
terminal phase and invokes all three exactly once inside one outer `try/finally`. Never invoke a
phase directly: the outer scope guarantees cancellation-safe authority cleanup from `Start-Job`
through terminal receive/removal.

```powershell
# STRATUS_CANARY_DATAFLOW_BEGIN
$canaryRoot = [IO.Path]::GetFullPath($productionRoot)
$candidateImageId = [string]$imageInspect.Id
$candidateRevision = $revision
$successorEpochId = [long]$activation.activated.id
$cohort = [string]$activation.activated.cohort
if (
  $candidateImageId -ne "sha256:$digest" -or
  $candidateRevision -ne [string]$imageInspect.Config.Labels.'org.opencontainers.image.revision' -or
  $successorEpochId -lt 1 -or
  [string]::IsNullOrWhiteSpace($cohort) -or
  [string]$activation.activated.image_digest -ne $digest -or
  [string]$activation.activated.plan_hash -ne [string]$recomputedPlan.plan_hash -or
  $cohort -ne [string]$recomputedPlan.cohort
) {
  throw "Canary identity does not match the activated reviewed candidate."
}
if (
  [string]::IsNullOrWhiteSpace($env:STRATUS_OPERATOR_API_KEY) -or
  [string]::IsNullOrWhiteSpace($env:STRATUS_SAFETY_API_KEY)
) {
  throw "Both control-plane API keys must be present in the dedicated canary process."
}
if ($env:STRATUS_OPERATOR_API_KEY -ceq $env:STRATUS_SAFETY_API_KEY) {
  throw "Operator and safety-control API keys must be distinct."
}
$operatorApiKey = ConvertTo-SecureString `
  $env:STRATUS_OPERATOR_API_KEY -AsPlainText -Force
$safetyApiKey = ConvertTo-SecureString `
  $env:STRATUS_SAFETY_API_KEY -AsPlainText -Force
# STRATUS_CANARY_DATAFLOW_END
$canaryScript = Join-Path $canaryRoot "scripts\Invoke-StratusPaperCanary.ps1"
$canaryArtifactValidator = Join-Path $canaryRoot (
  "scripts\Assert-StratusPaperCanaryArtifacts.ps1"
)
$canaryStopBarrier = Join-Path $canaryRoot (
  "scripts\Invoke-StratusPaperCanaryStopBarrier.ps1"
)
foreach ($requiredCanaryScript in @(
  $canaryScript,
  $canaryArtifactValidator,
  $canaryStopBarrier
)) {
  if (-not (Test-Path -LiteralPath $requiredCanaryScript -PathType Leaf)) {
    throw "Required PAPER canary release script is missing: $requiredCanaryScript"
  }
}
$expectedCanaryStopRequestId = "paper-canary-stop-$canaryRunId"
# STRATUS_CANARY_EXTERNAL_PROOF_VALIDATORS_BEGIN
function Test-NativeCanaryJsonInteger {
  param([AllowNull()][object]$Value)

  return ($Value -is [int] -or $Value -is [long])
}

function Test-NativeCanaryJsonBoolean {
  param([AllowNull()][object]$Value)

  return ($Value -is [bool])
}

function Test-NativeCanaryJsonObject {
  param([AllowNull()][object]$Value)

  return (
    $null -ne $Value -and
    $Value.GetType().FullName -ceq
      "System.Management.Automation.PSCustomObject"
  )
}

function Test-ExactCanaryJsonTimestamp {
  param([AllowNull()][object]$Value)

  if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value)) {
    return $false
  }
  $parsedTimestamp = [DateTimeOffset]::MinValue
  return [DateTimeOffset]::TryParse(
    $Value,
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::RoundtripKind,
    [ref]$parsedTimestamp
  )
}

function Test-ExactCanaryOffsets {
  param([AllowNull()][object]$Offsets)

  if ($Offsets -isnot [array] -or @($Offsets).Count -ne 6) {
    return $false
  }
  $expectedOffsets = @(0, 5, 15, 30, 45, 60)
  for ($offsetIndex = 0; $offsetIndex -lt 6; $offsetIndex++) {
    if (
      -not (Test-NativeCanaryJsonInteger -Value $Offsets[$offsetIndex]) -or
      [long]$Offsets[$offsetIndex] -ne [long]$expectedOffsets[$offsetIndex]
    ) {
      return $false
    }
  }
  return $true
}

function Test-ExactCanaryStopBarrierProof {
  param([AllowNull()][object]$Proof)

  if (-not (Test-NativeCanaryJsonObject -Value $Proof)) {
    return $false
  }
  if (
    -not (Test-NativeCanaryJsonObject -Value $Proof.payload) -or
    -not (Test-NativeCanaryJsonObject -Value $Proof.live) -or
    -not (Test-NativeCanaryJsonObject -Value $Proof.controls) -or
    -not (Test-NativeCanaryJsonObject -Value $Proof.controls.global) -or
    -not (Test-NativeCanaryJsonObject -Value $Proof.controls.polymarket) -or
    -not (Test-NativeCanaryJsonObject -Value $Proof.controls.binance_spot)
  ) {
    return $false
  }
  try {
    $leaseState = $Proof.matching_lease_state
    $payloadProperties = @($Proof.payload.PSObject.Properties)
    return (
      (Test-NativeCanaryJsonInteger -Value $Proof.schema_version) -and
      [long]$Proof.schema_version -eq 1 -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.verified) -and
      $Proof.verified -eq $true -and
      $Proof.run_id -is [string] -and
      $Proof.run_id -ceq $canaryRunId -and
      $Proof.request_id -is [string] -and
      $Proof.request_id -ceq $expectedCanaryStopRequestId -and
      $Proof.stop_request_id -is [string] -and
      $Proof.stop_request_id -ceq $expectedCanaryStopRequestId -and
      $Proof.resume_request_id -is [string] -and
      $Proof.resume_request_id -ceq $requestId -and
      $Proof.postgres_container_id -is [string] -and
      $Proof.postgres_container_id -ceq [string]$postgresContainer[0] -and
      $Proof.command -is [string] -and
      $Proof.command -ceq "stop" -and
      $Proof.actor -is [string] -and
      $Proof.actor -ceq "codex-paper-canary" -and
      $Proof.reason -is [string] -and
      $Proof.reason -ceq "STRATUS PAPER canary terminal fail-closed stop." -and
      $payloadProperties.Count -eq 1 -and
      $payloadProperties[0].Name -ceq "venue" -and
      $payloadProperties[0].Value -is [string] -and
      $payloadProperties[0].Value -ceq "polymarket" -and
      (Test-NativeCanaryJsonInteger -Value $Proof.post_attempts) -and
      [long]$Proof.post_attempts -ge 1 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.accepted_responses) -and
      [long]$Proof.accepted_responses -ge 0 -and
      [long]$Proof.accepted_responses -le [long]$Proof.post_attempts -and
      (Test-NativeCanaryJsonInteger -Value $Proof.exact_id_replays) -and
      [long]$Proof.exact_id_replays -eq ([long]$Proof.post_attempts - 1) -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.initially_created) -and
      (Test-ExactCanaryJsonTimestamp -Value $Proof.database_now) -and
      $Proof.command_status -is [string] -and
      $Proof.command_status -ceq "succeeded" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.matching_lease_terminal) -and
      $Proof.matching_lease_terminal -eq $true -and
      (
        $null -eq $leaseState -or
        (
          $leaseState -is [string] -and
          $leaseState -cin @(
            "released", "expired_stopped", "expired_safe", "revoked_stopped"
          )
        )
      ) -and
      (Test-NativeCanaryJsonInteger -Value $Proof.active_canary_leases) -and
      [long]$Proof.active_canary_leases -eq 0 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.pending_commands) -and
      [long]$Proof.pending_commands -eq 0 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.pending_signals) -and
      [long]$Proof.pending_signals -eq 0 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.live.orders) -and
      [long]$Proof.live.orders -eq 0 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.live.positions) -and
      [long]$Proof.live.positions -eq 0 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.live.intents) -and
      [long]$Proof.live.intents -eq 0 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.live.fill_legs) -and
      [long]$Proof.live.fill_legs -eq 0 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.live.enrollments) -and
      [long]$Proof.live.enrollments -eq 0 -and
      (Test-NativeCanaryJsonInteger -Value $Proof.live.outcomes) -and
      [long]$Proof.live.outcomes -eq 0 -and
      $Proof.controls.global.mode -is [string] -and
      $Proof.controls.global.mode -ceq "paper" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.controls.global.kill_switch) -and
      $Proof.controls.global.kill_switch -eq $true -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.controls.polymarket.enabled) -and
      $Proof.controls.polymarket.enabled -eq $true -and
      $Proof.controls.polymarket.mode -is [string] -and
      $Proof.controls.polymarket.mode -ceq "paper" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.controls.polymarket.kill_switch) -and
      $Proof.controls.polymarket.kill_switch -eq $true -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.controls.binance_spot.enabled) -and
      $Proof.controls.binance_spot.enabled -eq $true -and
      $Proof.controls.binance_spot.mode -is [string] -and
      $Proof.controls.binance_spot.mode -ceq "backtest" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.controls.binance_spot.kill_switch) -and
      $Proof.controls.binance_spot.kill_switch -eq $true
    )
  }
  catch {
    return $false
  }
}

function Test-ExactCanaryReadyProof {
  param([AllowNull()][object]$Proof)

  if (-not (Test-NativeCanaryJsonObject -Value $Proof)) {
    return $false
  }
  if (-not (Test-NativeCanaryJsonObject -Value $Proof.binance_baseline)) {
    return $false
  }
  try {
    return (
      (Test-NativeCanaryJsonInteger -Value $Proof.schema_version) -and
      [long]$Proof.schema_version -eq 1 -and
      $Proof.validator -is [string] -and
      $Proof.validator -ceq "stratus_paper_canary_artifacts" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.verified) -and
      $Proof.verified -eq $true -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.validated) -and
      $Proof.validated -eq $true -and
      $Proof.mode -is [string] -and
      $Proof.mode -ceq "Ready" -and
      $Proof.canary_run_id -is [string] -and
      $Proof.canary_run_id -ceq $canaryRunId -and
      $Proof.expected_resume_request_id -is [string] -and
      $Proof.expected_resume_request_id -ceq $requestId -and
      $Proof.lease_id -is [string] -and
      $Proof.lease_id -ceq "paper-lease-$canaryRunId" -and
      $Proof.direct_stop_request_id -is [string] -and
      $Proof.direct_stop_request_id -ceq "paper-canary-stop-$canaryRunId" -and
      $Proof.release_request_id -is [string] -and
      $Proof.release_request_id -ceq "paper-lease-release-$canaryRunId" -and
      (Test-ExactCanaryOffsets -Offsets $Proof.offsets) -and
      $Proof.preflight_sha256 -is [string] -and
      $Proof.preflight_sha256 -cmatch "^[0-9a-f]{64}$" -and
      $Proof.lease_acquire_sha256 -is [string] -and
      $Proof.lease_acquire_sha256 -cmatch "^[0-9a-f]{64}$" -and
      $Proof.ready_sha256 -is [string] -and
      $Proof.ready_sha256 -cmatch "^[0-9a-f]{64}$" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.failure_evidence_absent) -and
      $Proof.failure_evidence_absent -eq $true -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.binance_baseline.enabled) -and
      $Proof.binance_baseline.enabled -eq $true -and
      $Proof.binance_baseline.mode -is [string] -and
      $Proof.binance_baseline.mode -ceq "backtest" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.binance_baseline.kill_switch) -and
      $Proof.binance_baseline.kill_switch -eq $true
    )
  }
  catch {
    return $false
  }
}

function Test-ExactCanaryTerminalProof {
  param([AllowNull()][object]$Proof)

  if (-not (Test-NativeCanaryJsonObject -Value $Proof)) {
    return $false
  }
  try {
    return (
      (Test-NativeCanaryJsonInteger -Value $Proof.schema_version) -and
      [long]$Proof.schema_version -eq 1 -and
      $Proof.validator -is [string] -and
      $Proof.validator -ceq "stratus_paper_canary_artifacts" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.verified) -and
      $Proof.verified -eq $true -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.validated) -and
      $Proof.validated -eq $true -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.terminal) -and
      $Proof.terminal -eq $true -and
      $Proof.mode -is [string] -and
      $Proof.mode -ceq "Terminal" -and
      $Proof.canary_run_id -is [string] -and
      $Proof.canary_run_id -ceq $canaryRunId -and
      $Proof.expected_resume_request_id -is [string] -and
      $Proof.expected_resume_request_id -ceq $requestId -and
      $Proof.lease_id -is [string] -and
      $Proof.lease_id -ceq "paper-lease-$canaryRunId" -and
      $Proof.direct_stop_request_id -is [string] -and
      $Proof.direct_stop_request_id -ceq "paper-canary-stop-$canaryRunId" -and
      $Proof.release_request_id -is [string] -and
      $Proof.release_request_id -ceq "paper-lease-release-$canaryRunId" -and
      (Test-ExactCanaryOffsets -Offsets $Proof.offsets) -and
      $Proof.preflight_sha256 -is [string] -and
      $Proof.preflight_sha256 -cmatch "^[0-9a-f]{64}$" -and
      $Proof.lease_acquire_sha256 -is [string] -and
      $Proof.lease_acquire_sha256 -cmatch "^[0-9a-f]{64}$" -and
      $Proof.ready_sha256 -is [string] -and
      $Proof.ready_sha256 -cmatch "^[0-9a-f]{64}$" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.safety_lease_released) -and
      $Proof.safety_lease_released -eq $true -and
      $Proof.terminal_runtime -is [string] -and
      $Proof.terminal_runtime -ceq "polymarket_paper_fail_closed" -and
      $Proof.manifest_sha256 -is [string] -and
      $Proof.manifest_sha256 -cmatch "^[0-9a-f]{64}$" -and
      $Proof.final_snapshot_sha256 -is [string] -and
      $Proof.final_snapshot_sha256 -cmatch "^[0-9a-f]{64}$" -and
      (Test-NativeCanaryJsonBoolean -Value $Proof.failure_evidence_absent) -and
      $Proof.failure_evidence_absent -eq $true
    )
  }
  catch {
    return $false
  }
}
# STRATUS_CANARY_EXTERNAL_PROOF_VALIDATORS_END

function Invoke-VerifiedCanaryStopBarrier {
  $proof = & $canaryStopBarrier `
    -Root $canaryRoot `
    -RunId $canaryRunId `
    -ResumeRequestId $requestId `
    -PostgresContainerId $postgresContainer[0] `
    -SafetyApiKey $safetyApiKey | ConvertFrom-Json
  if (-not (Test-ExactCanaryStopBarrierProof -Proof $proof)) {
    throw "PAPER canary STOP barrier returned a mismatched fail-closed proof."
  }
  return $proof
}
# STRATUS_CANARY_FAILURE_CLEANUP_BEGIN
function New-CanaryLifecycleGuard {
  return [pscustomobject]@{
    Succeeded = $false
    CleanupCompleted = $false
    PrimaryFailure = $null
    BarrierAttempted = $false
    BarrierVerified = $false
    BarrierError = $null
    StopAttempted = $false
    StopError = $null
    WaitAttempted = $false
    WaitError = $null
    ReceiveAttempted = $false
    ReceiveError = $null
    RemoveAttempted = $false
    RemoveError = $null
    CollectorOutput = ""
  }
}

function Stop-CanaryJobAfterBarrierAttempt {
  param([Parameter(Mandatory = $true)][string]$Failure)

  if ([string]::IsNullOrWhiteSpace([string]$canaryLifecycleGuard.PrimaryFailure)) {
    $canaryLifecycleGuard.PrimaryFailure = $Failure
  }
  try {
    if (-not [bool]$canaryLifecycleGuard.BarrierAttempted) {
      $canaryLifecycleGuard.BarrierAttempted = $true
      $null = Invoke-VerifiedCanaryStopBarrier
      $canaryLifecycleGuard.BarrierVerified = $true
    }
  }
  catch {
    $canaryLifecycleGuard.BarrierError = $_.Exception.Message
  }
  finally {
    if (-not [bool]$canaryLifecycleGuard.StopAttempted) {
      $canaryLifecycleGuard.StopAttempted = $true
      try {
        if ([string]$canaryJob.State -notin @("Completed", "Failed", "Stopped")) {
          Stop-Job -Job $canaryJob -ErrorAction Stop
        }
      }
      catch {
        $canaryLifecycleGuard.StopError = $_.Exception.Message
      }
    }
  }

  if (-not [bool]$canaryLifecycleGuard.WaitAttempted) {
    $canaryLifecycleGuard.WaitAttempted = $true
    try {
      $settledCanaryJob = Wait-Job -Job $canaryJob -Timeout 5 -ErrorAction Stop
      if ($null -eq $settledCanaryJob) {
        $canaryLifecycleGuard.WaitError = (
          "Collector did not settle within five seconds."
        )
      }
    }
    catch {
      $canaryLifecycleGuard.WaitError = $_.Exception.Message
    }
  }
  try {
    if (-not [bool]$canaryLifecycleGuard.ReceiveAttempted) {
      $canaryLifecycleGuard.ReceiveAttempted = $true
      try {
        $canaryLifecycleGuard.CollectorOutput = (
          Receive-Job -Job $canaryJob -ErrorAction Stop 2>&1 | Out-String
        )
      }
      catch {
        $canaryLifecycleGuard.ReceiveError = $_.Exception.Message
      }
    }
  }
  finally {
    if (-not [bool]$canaryLifecycleGuard.RemoveAttempted) {
      $canaryLifecycleGuard.RemoveAttempted = $true
      try {
        Remove-Job -Job $canaryJob -Force -ErrorAction Stop
      }
      catch {
        $canaryLifecycleGuard.RemoveError = $_.Exception.Message
      }
    }
  }
  $canaryLifecycleGuard.CleanupCompleted = $true

  $failureDetails = [Collections.Generic.List[string]]::new()
  [void]$failureDetails.Add(
    "failure=$($canaryLifecycleGuard.PrimaryFailure)"
  )
  if ([bool]$canaryLifecycleGuard.BarrierVerified) {
    [void]$failureDetails.Add("barrier=verified")
  }
  elseif ($null -ne $canaryLifecycleGuard.BarrierError) {
    [void]$failureDetails.Add(
      "barrier_error=$($canaryLifecycleGuard.BarrierError)"
    )
  }
  else {
    [void]$failureDetails.Add(
      "barrier_error=barrier attempt did not return a verified proof"
    )
  }
  foreach ($cleanupFailure in @(
    [pscustomobject]@{
      Name = "stop_error"; Value = $canaryLifecycleGuard.StopError
    },
    [pscustomobject]@{
      Name = "wait_error"; Value = $canaryLifecycleGuard.WaitError
    },
    [pscustomobject]@{
      Name = "receive_error"; Value = $canaryLifecycleGuard.ReceiveError
    },
    [pscustomobject]@{
      Name = "remove_error"; Value = $canaryLifecycleGuard.RemoveError
    }
  )) {
    if ($null -ne $cleanupFailure.Value) {
      [void]$failureDetails.Add(
        "$($cleanupFailure.Name)=$($cleanupFailure.Value)"
      )
    }
  }
  if (-not [string]::IsNullOrWhiteSpace(
    [string]$canaryLifecycleGuard.CollectorOutput
  )) {
    [void]$failureDetails.Add(
      "collector_output=$($canaryLifecycleGuard.CollectorOutput.Trim())"
    )
  }
  throw "PAPER canary failed closed: $($failureDetails -join '; ')"
}
# STRATUS_CANARY_FAILURE_CLEANUP_END
$canaryJobScript = {
  param(
    $ScriptPath, $Root, $ResumeRequestId, $ResumeActor, $ResumeReason,
    $EpochId, $Cohort, $ImageId, $Revision, $ReleaseDigest, $RunId
  )
  & $ScriptPath `
    -Root $Root `
    -BaseUri "http://127.0.0.1:8000/" `
    -SafetyBaseUri "http://127.0.0.1:8001/" `
    -ExpectedResumeRequestId $ResumeRequestId `
    -ExpectedResumeActor $ResumeActor `
    -ExpectedResumeReason $ResumeReason `
    -ExpectedEpochId $EpochId `
    -ExpectedCohort $Cohort `
    -ExpectedImageId $ImageId `
    -ExpectedRevision $Revision `
    -ExpectedReleaseDigest $ReleaseDigest `
    -CanaryRunId $RunId `
    -StopPolymarketOnDivergence
}
# STRATUS_CANARY_READY_PHASE_BEGIN
$canaryReadyPhase = {
$canaryRunDirectory = Join-Path $canaryRoot (
  ".stratus-cache\paper-canary\$canaryRunId"
)
if (Test-Path -LiteralPath $canaryRunDirectory) {
  throw "PAPER canary run directory already exists; refusing identity reuse."
}
$canaryLaunchedAtUtc = [DateTime]::UtcNow
$canaryReadyDeadlineUtc = $canaryLaunchedAtUtc.AddMinutes(5)
$canaryTerminalDeadlineUtc = $canaryLaunchedAtUtc.AddSeconds(3900)
$canaryJob = Start-Job `
  -Name "stratus-paper-canary-$canaryRunId" `
  -ScriptBlock $canaryJobScript `
  -ArgumentList @(
    $canaryScript, $canaryRoot, $requestId, $resumeActor, $resumeReason,
    $successorEpochId, $cohort, $candidateImageId, $candidateRevision,
    $digest, $canaryRunId
  )
Wait-Job -Job $canaryJob -Timeout 1 | Out-Null
if ($canaryJob.State -ne 'Running') {
  Stop-CanaryJobAfterBarrierAttempt `
    -Failure "Collector did not remain armed after launch."
}
$canaryReadyPath = Join-Path $canaryRunDirectory "ready.json"
while (-not (Test-Path -LiteralPath $canaryReadyPath -PathType Leaf)) {
  if (
    $canaryJob.State -ne 'Running' -or
    [DateTime]::UtcNow -ge $canaryReadyDeadlineUtc
  ) {
    Stop-CanaryJobAfterBarrierAttempt `
      -Failure "Collector did not produce a verified ready artifact in time."
  }
  Start-Sleep -Milliseconds 250
}
try {
  $canaryReadyProof = & $canaryArtifactValidator `
    -Root $canaryRoot `
    -Mode Ready `
    -RunId $canaryRunId `
    -ResumeRequestId $requestId `
    -ResumeActor $resumeActor `
    -ResumeReason $resumeReason `
    -EpochId $successorEpochId `
    -Cohort $cohort `
    -ImageId $candidateImageId `
    -Revision $candidateRevision `
    -ReleaseDigest $digest | ConvertFrom-Json
  if (
    $canaryJob.State -ne "Running" -or
    -not (Test-ExactCanaryReadyProof -Proof $canaryReadyProof)
  ) {
    throw "Ready artifact validator returned a mismatched canary proof."
  }
}
catch {
  $readyValidationError = $_.Exception.Message
  Stop-CanaryJobAfterBarrierAttempt `
    -Failure "Ready evidence validation failed: $readyValidationError"
}
}
# STRATUS_CANARY_READY_PHASE_END
```

When the final outer scope invokes the ready phase above, the background job inherits the two
process-scoped control-plane keys without placing either secret in its command arguments. It
remains running and waits at most five minutes for the reserved resume. The ready phase polls only
to the launch-derived ready deadline and invokes
`Assert-StratusPaperCanaryArtifacts.ps1 -Mode Ready`; it does not trust an unparsed marker. The
validator proves the exact immutable envelope, payload and file hashes, request/lease/STOP/release
identities, six offsets, and sealed Binance backtest+kill baseline. Any early job exit, timeout, or
validation failure invokes `Invoke-StratusPaperCanaryStopBarrier.ps1` before stopping the job or
reading its output. Every failure branch uses the same exception-safe cleanup: the barrier attempt
starts first, `Stop-Job` runs from `finally` even if the barrier throws or times out, and only then
does a five-second bounded settle, non-waiting receive, and forced removal preserve both the
original and barrier errors. The marker is emitted only after killed-state controls,
exact image/epoch/lanes, a fresh post-worker audit, four cold maintenance runs, zero LIVE state,
database contention, resource, hash, reserved-ID collision, lease freshness, and independent
watchdog checks pass. A pre-existing run directory or request ID is a release failure; never
reuse either. STOP, ready, terminal, and resume JSON are validated without PowerShell coercion:
booleans must be native JSON booleans, counters and numeric row IDs must be native JSON integers,
and request identity, timestamp, mode, state, and hash fields must retain their exact string
shapes. String values such
as `"false"`/`"true"` and missing or `null` counters always fail closed.

The resume phase below submits the already prepared body only after the ready phase validates that
marker:

```powershell
$canaryResumePhase = {
# STRATUS_RESUME_SUBMIT_BEGIN
function Test-ExactCanaryResumeCommand {
  param([AllowNull()][object]$Item)

  if (-not (Test-NativeCanaryJsonObject -Value $Item)) {
    return $false
  }
  try {
    $requiredProperties = @(
      "id", "request_id", "command", "payload", "actor", "reason",
      "status", "detail", "created_at", "executed_at"
    )
    foreach ($propertyName in $requiredProperties) {
      if ($null -eq $Item.PSObject.Properties[$propertyName]) {
        return $false
      }
    }
    if (-not (Test-NativeCanaryJsonObject -Value $Item.payload)) {
      return $false
    }
    $payloadProperties = @($Item.payload.PSObject.Properties)
    return (
      (Test-NativeCanaryJsonInteger -Value $Item.id) -and
      [long]$Item.id -gt 0 -and
      $Item.request_id -is [string] -and
      $Item.request_id -ceq $requestId -and
      $Item.command -is [string] -and
      $Item.command -ceq "resume" -and
      $Item.actor -is [string] -and
      $Item.actor -ceq $resumeActor -and
      $Item.reason -is [string] -and
      $Item.reason -ceq $resumeReason -and
      $payloadProperties.Count -eq 1 -and
      $payloadProperties[0].Name -ceq "venue" -and
      $payloadProperties[0].Value -is [string] -and
      $payloadProperties[0].Value -ceq "polymarket" -and
      $Item.status -is [string] -and
      $Item.status -cin @("pending", "succeeded") -and
      ($null -eq $Item.detail -or $Item.detail -is [string]) -and
      (Test-ExactCanaryJsonTimestamp -Value $Item.created_at) -and
      (
        ($Item.status -ceq "pending" -and $null -eq $Item.executed_at) -or
        (
          $Item.status -ceq "succeeded" -and
          (Test-ExactCanaryJsonTimestamp -Value $Item.executed_at)
        )
      )
    )
  }
  catch {
    return $false
  }
}

function Test-ExactCanaryResumeAcceptance {
  param([AllowNull()][object]$Acceptance)

  if (-not (Test-NativeCanaryJsonObject -Value $Acceptance)) {
    return $false
  }
  try {
    if (
      $null -eq $Acceptance.PSObject.Properties["created"] -or
      $null -eq $Acceptance.PSObject.Properties["item"] -or
      -not (Test-NativeCanaryJsonBoolean -Value $Acceptance.created) -or
      $Acceptance.created -ne $true -or
      -not (Test-ExactCanaryResumeCommand -Item $Acceptance.item) -or
      $Acceptance.item.status -cne "pending" -or
      $null -ne $Acceptance.item.executed_at
    ) {
      return $false
    }
    return $true
  }
  catch {
    return $false
  }
}

function Stop-CanaryAfterResumeSubmissionFailure {
  param([Parameter(Mandatory = $true)][string]$Failure)

  Stop-CanaryJobAfterBarrierAttempt `
    -Failure "Resume submission failed: $Failure"
}

$resumePostTimeoutSeconds = 10
$resumeReconcileAttempts = 6
$resumeReconcileTimeoutSeconds = 5
$resumeReconcileDelaySeconds = 5
$resumeAccepted = $null
$resumePostFailure = $null
$resumePostMayHaveCommitted = $false
if ($canaryJob.State -ne "Running") {
  Stop-CanaryAfterResumeSubmissionFailure `
    -Failure "Collector exited before the lease-reserved resume POST."
}
try {
  $resumeAccepted = Invoke-RestMethod -Method Post -Headers $headers `
    -ContentType "application/json" -Body $resumeBody `
    -TimeoutSec $resumePostTimeoutSeconds `
    -Uri "http://127.0.0.1:8000/api/v1/commands"
}
catch {
  # A timeout or transport exception can mean the server committed but its response was lost.
  # Do not retry the mutation. Reconcile the immutable request identity through bounded reads.
  $resumePostFailure = $_.Exception.Message
  $resumePostMayHaveCommitted = (
    $_.Exception -is [TimeoutException] -or
    $_.Exception -is [IO.IOException] -or
    $_.Exception -is [OperationCanceledException] -or
    [string]$_.Exception.GetType().FullName -ceq
      "System.Net.Http.HttpRequestException" -or
    (
      $_.Exception -is [Net.WebException] -and
      $null -eq $_.Exception.Response
    )
  )
}

if ($null -eq $resumePostFailure) {
  if (
    -not (Test-ExactCanaryResumeAcceptance -Acceptance $resumeAccepted) -or
    $canaryJob.State -ne "Running"
  ) {
    Stop-CanaryAfterResumeSubmissionFailure `
      -Failure "Main API returned a mismatched lease-reserved resume acceptance."
  }
}
elseif (-not $resumePostMayHaveCommitted) {
  Stop-CanaryAfterResumeSubmissionFailure `
    -Failure "Resume POST failed before an exact acceptance: $resumePostFailure"
}
else {
  $reconciledResume = $null
  $reconcileFailure = $resumePostFailure
  for (
    $resumeReconcileAttempt = 1;
    $resumeReconcileAttempt -le $resumeReconcileAttempts;
    $resumeReconcileAttempt++
  ) {
    if ($canaryJob.State -ne "Running") {
      $reconcileFailure = "Collector exited while reconciling the resume POST."
      break
    }
    try {
      $commandAudit = Invoke-RestMethod -Method Get -Headers $headers `
        -TimeoutSec $resumeReconcileTimeoutSeconds `
        -Uri "http://127.0.0.1:8000/api/v1/commands?limit=200"
      if (
        -not (Test-NativeCanaryJsonObject -Value $commandAudit) -or
        $null -eq $commandAudit.PSObject.Properties["items"] -or
        $commandAudit.items -isnot [array]
      ) {
        $reconcileFailure = "Command audit returned a malformed JSON envelope."
        break
      }
      $matchingCommands = @(
        $commandAudit.items | Where-Object {
          $_.request_id -is [string] -and $_.request_id -ceq $requestId
        }
      )
      if ($matchingCommands.Count -gt 1) {
        $reconcileFailure = "Command audit returned duplicate exact request identities."
        break
      }
      if ($matchingCommands.Count -eq 1) {
        $candidateResume = $matchingCommands[0]
        if (-not (Test-ExactCanaryResumeCommand -Item $candidateResume)) {
          $reconcileFailure = "Command audit identity mismatched the reserved resume."
          break
        }
        if ($candidateResume.status -ceq "succeeded") {
          if (-not (Test-ExactCanaryJsonTimestamp -Value $candidateResume.executed_at)) {
            $reconcileFailure = "Succeeded resume audit lacked executed_at."
            break
          }
          $reconciledResume = $candidateResume
          break
        }
        if ($candidateResume.status -cne "pending") {
          $reconcileFailure = (
            "Resume audit reached unsafe status: " +
            $candidateResume.status
          )
          break
        }
        $reconcileFailure = "Exact resume remained pending after response loss."
      }
      else {
        $reconcileFailure = "Exact resume was absent after response loss."
      }
    }
    catch {
      $reconcileFailure = "Resume audit read failed: $($_.Exception.Message)"
    }
    if ($resumeReconcileAttempt -lt $resumeReconcileAttempts) {
      Start-Sleep -Seconds $resumeReconcileDelaySeconds
    }
  }
  if ($null -eq $reconciledResume -or $canaryJob.State -ne "Running") {
    Stop-CanaryAfterResumeSubmissionFailure -Failure $reconcileFailure
  }
  $resumeAccepted = [pscustomobject]@{
    created = $true
    reconciled_after_response_loss = $true
    item = $reconciledResume
  }
}
# STRATUS_RESUME_SUBMIT_END
}
```

The mutation has a ten-second loopback HTTP timeout. A normal acceptance must be a newly created,
exact Polymarket-only `pending` row while the collector remains running. A definite HTTP or local
validation exception stops immediately. A timeout or response-less transport exception is treated
as possible server-commit/response-loss: never retry the POST. Instead, make
at most six authenticated five-second audit reads, separated by five seconds, and continue only
after the one exact immutable identity is `succeeded` with `executed_at`. Missing, still-pending,
rejected, failed, duplicate, malformed, actor/reason/payload-mismatched, unbounded, or unreadable
evidence invokes the independent verified STOP barrier before the collector is stopped or its
output is read. A failed barrier attempt still stops and removes the collector, so its lease
heartbeat cannot keep PAPER enabled while the release shell unwinds. The collector must observe
that same exact request as `succeeded` and anchor T+0
to its PostgreSQL `executed_at`. An all-venue request or missing watchdog observation also fails
closed. During collection require global kill false, Polymarket enabled+paper+kill=false, Binance
enabled+backtest+kill=true, and zero LIVE state.

- `/healthz`, `/readyz`, API/worker/PostgreSQL state, exact image ID, restarts, OOM, CPU/memory;
- global and per-venue controls, zero LIVE state, and pending operator commands;
- latest health audit and all critical/blocking findings;
- active epoch/image/cohort/gate and every lane identity;
- required job freshness, missing jobs, `stalled_days`, and composite run gaps;
- PAPER maker intents, resting orders, fills, cost seals, 5m/30m markouts,
  enrollments/outcomes, oldest due age, duplicates, and hash validity.

Do not create a synthetic production fill to satisfy the canary. Every naturally created or due
cost obligation must progress according to its immutable contract, but a run with no natural fill
or obligation records that absence separately and may still prove runtime stability. It does not
claim that execution-quality cases were observed; those cases remain pending until natural PAPER
evidence or a reviewed isolated non-production fixture exists.

Immediately stop Polymarket PAPER through `safety-control` on any safety divergence, unexpected
LIVE activity, digest/mode change, missing required job, growing `stalled_days`, unresolved
post-grace mark, or the same `finalize_ids` obligation appearing in more than one maintenance
event while `mark_claimed=0` (classified as `scheduler_lock_contention`), sustained API/DB load
regression, restart/OOM, or a critical finding. STOP must execute before diagnostics and atomically
revoke the active lease plus reject its exact pending resume. Do not automatically restart Docker.

A successful T+60 is not permission to leave trading enabled. The collector's terminal release
must atomically write the audited Polymarket STOP, turn global and Polymarket kills ON, reject any
still-pending exact resume, link the stop command to the immutable lease, and transition the lease
to `released`. The final manifest is accepted only after direct PostgreSQL confirmation of that
fail-closed terminal state and unchanged Binance controls.

The final block defines the terminal phase, then enters the only permitted outer lifecycle scope.
That scope starts the collector through the ready phase, submits the resume, joins the collector,
and validates its terminal artifact. Any exception or PowerShell cancellation after `Start-Job`
enters the idempotent failure cleanup from `finally`; staged guard flags prevent double receive or
removal. A collector that exceeds the bounded 65-minute launch-to-terminal window is stopped
through its precommitted independent safety request **before** the job is terminated or diagnostics
are read:

```powershell
# STRATUS_CANARY_TERMINAL_PHASE_BEGIN
$canaryTerminalPhase = {
$remainingCanarySeconds = [int][Math]::Floor(
  ($canaryTerminalDeadlineUtc - [DateTime]::UtcNow).TotalSeconds
)
$completedCanaryJob = if ($remainingCanarySeconds -gt 0) {
  Wait-Job -Job $canaryJob -Timeout $remainingCanarySeconds
}
else {
  $null
}
if ($null -eq $completedCanaryJob) {
  Stop-CanaryJobAfterBarrierAttempt `
    -Failure "Collector exceeded its launch-bounded terminal window."
}

$canaryTerminalState = [string]$canaryJob.State
if ($canaryTerminalState -ne "Completed") {
  Stop-CanaryJobAfterBarrierAttempt `
    -Failure "Collector entered terminal state $canaryTerminalState."
}
try {
  $terminalBarrierProof = Invoke-VerifiedCanaryStopBarrier
}
catch {
  $terminalBarrierError = $_.Exception.Message
  Stop-CanaryJobAfterBarrierAttempt `
    -Failure "Terminal STOP barrier failed: $terminalBarrierError"
}
try {
  $canaryTerminalProof = & $canaryArtifactValidator `
    -Root $canaryRoot `
    -Mode Terminal `
    -RunId $canaryRunId `
    -ResumeRequestId $requestId `
    -ResumeActor $resumeActor `
    -ResumeReason $resumeReason `
    -EpochId $successorEpochId `
    -Cohort $cohort `
    -ImageId $candidateImageId `
    -Revision $candidateRevision `
    -ReleaseDigest $digest | ConvertFrom-Json
  if (-not (Test-ExactCanaryTerminalProof -Proof $canaryTerminalProof)) {
    throw "Terminal artifact validator returned a mismatched canary proof."
  }
}
catch {
  $terminalValidationError = $_.Exception.Message
  Stop-CanaryJobAfterBarrierAttempt `
    -Failure "Terminal evidence validation failed: $terminalValidationError"
}
$canaryLifecycleGuard.ReceiveAttempted = $true
$canaryTerminalOutput = Receive-Job -Job $canaryJob 2>&1 | Out-String
$canaryLifecycleGuard.CollectorOutput = $canaryTerminalOutput
$canaryLifecycleGuard.RemoveAttempted = $true
Remove-Job -Job $canaryJob -Force -ErrorAction Stop
$canaryLifecycleGuard.CleanupCompleted = $true
$canaryLifecycleGuard.Succeeded = $true
}
# STRATUS_CANARY_TERMINAL_PHASE_END

# STRATUS_CANARY_LIFETIME_SCOPE_BEGIN
$canaryJob = $null
$canaryLifecycleGuard = New-CanaryLifecycleGuard
$canaryLifecycleFailure = "PowerShell cancellation interrupted the canary lifecycle."
try {
  . $canaryReadyPhase
  . $canaryResumePhase
  . $canaryTerminalPhase
  if (-not [bool]$canaryLifecycleGuard.Succeeded) {
    throw "Terminal phase returned without exact successful cleanup."
  }
}
catch {
  $canaryLifecycleFailure = $_.Exception.Message
}
finally {
  if (
    -not [bool]$canaryLifecycleGuard.Succeeded -and
    -not [bool]$canaryLifecycleGuard.CleanupCompleted -and
    $null -ne $canaryJob
  ) {
    Stop-CanaryJobAfterBarrierAttempt `
      -Failure "Outer canary lifecycle aborted: $canaryLifecycleFailure"
  }
}
if (-not [bool]$canaryLifecycleGuard.Succeeded) {
  throw "PAPER canary lifecycle failed: $canaryLifecycleFailure"
}
# STRATUS_CANARY_LIFETIME_SCOPE_END
```

## 10. Safe rollback

The retained predecessor is:

- tag: `stratus:rollback-epoch21-20260729`;
- image ID: `sha256:f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4`.

After successor activation this image is an **API-only diagnostic rollback**. Its worker contains
the old competing scheduler topology and must never be started, even while trading is killed,
because maintenance jobs can still write execution evidence. Revisions `20260730_0046` and
`20260730_0047` are additive forward schema changes: do not downgrade production merely to run
the predecessor API. This rollback is only a fail-closed diagnostic bridge to a
forward-fixed candidate; the predecessor `migrate` and `worker` services remain forbidden.

Rollback sequence:

1. Before touching the worker, send an authenticated STOP to the independent `safety-control`
   endpoint with a fresh, immutable request ID. Do not use the worker queue or the main operator
   API for rollback authority:

   ```powershell
   $rollbackStopRequestId = "rollback-stop-" + [guid]::NewGuid().ToString("N")
   $rollbackStopBody = @{
     request_id = $rollbackStopRequestId
     command = "stop"
     actor = "release-owner"
     reason = "Fail-closed API-only rollback"
     payload = @{ venue = "polymarket" }
   } | ConvertTo-Json -Depth 4 -Compress
   $safetyCredential = [pscredential]::new("safety", $safetyApiKey)
   $safetyHeaders = @{
     "X-API-Key" = $safetyCredential.GetNetworkCredential().Password
   }
   try {
     $rollbackStop = Invoke-RestMethod -Method Post -TimeoutSec 3 `
       -Headers $safetyHeaders -ContentType "application/json" `
       -Body $rollbackStopBody `
       -Uri "http://127.0.0.1:8001/api/v1/emergency-stop"
   }
   finally {
     $safetyHeaders["X-API-Key"] = $null
     $safetyCredential = $null
   }
   if (
     [string]$rollbackStop.item.request_id -ne $rollbackStopRequestId -or
     [string]$rollbackStop.item.command -ne "stop" -or
     [string]$rollbackStop.item.status -ne "succeeded" -or
     [string]$rollbackStop.item.payload.venue -ne "polymarket"
   ) {
     throw "Independent safety-control did not prove the exact rollback STOP."
   }
   ```

   Immediately confirm through the administrative PostgreSQL connection that global and
   Polymarket kills are true, Polymarket remains PAPER, Binance remains
   enabled+backtest+kill=true, the exact STOP is succeeded, every LIVE inventory table is empty,
   and pending operator commands are zero. Any ambiguity blocks rollback; it is not permission
   to fall back to the worker command queue.

2. Only after that independent STOP and direct database confirmation, stop the worker and verify
   it is not running:

   ```powershell
   docker compose @releaseComposeArguments stop worker
   if ($LASTEXITCODE -ne 0) {
     throw "Rollback could not stop the successor worker."
   }
   $stoppedWorker = @(docker compose @releaseComposeArguments ps --all -q worker)
   if (
     $stoppedWorker.Count -ne 1 -or
     (docker inspect $stoppedWorker[0] --format "{{.State.Running}}") -ne "false"
   ) {
     throw "Candidate worker did not stop cleanly."
   }
   ```

3. Re-prove global and Polymarket kill are true, Binance is still backtest+kill=true, LIVE and
   pending commands are zero after the worker has drained.
4. Inspect the retained rollback tag and require the exact image ID above. Set `.env`
   `STRATUS_IMAGE=stratus:rollback-epoch21-20260729` and
   `STRATUS_RELEASE_IMAGE_DIGEST=f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4`.
5. Recreate **only** the API:

   ```powershell
   docker compose @releaseComposeArguments --profile release up --no-start --no-build --pull never --no-deps --force-recreate api
   if ($LASTEXITCODE -ne 0) {
     throw "Rollback could not stage the predecessor API."
   }
   $rollbackApi = @(docker compose @releaseComposeArguments ps --all -q api)
   $rollbackApiInspect = if ($rollbackApi.Count -eq 1) {
     @(docker inspect $rollbackApi[0] | ConvertFrom-Json)[0]
   }
   else { $null }
   if (
     $rollbackApi.Count -ne 1 -or
     $null -eq $rollbackApiInspect -or
     [bool]$rollbackApiInspect.State.Running -or
     [string]$rollbackApiInspect.Image -cne
       "sha256:f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4" -or
     [int64]$rollbackApiInspect.HostConfig.Memory -ne 1073741824L -or
     @($rollbackApiInspect.Config.Env | Where-Object {
         [string]$_ -imatch '^OPERATOR_API_KEY='
     }).Count -ne 1 -or
     @($rollbackApiInspect.Config.Env | Where-Object {
         [string]$_ -cmatch '^OPERATOR_API_KEY=.+$'
     }).Count -ne 1
   ) {
     throw "Predecessor API stopped-container identity check failed."
   }
   docker start $rollbackApi[0] | Out-Null
   if ($LASTEXITCODE -ne 0) {
     throw "Rollback could not start the predecessor API."
   }
   ```

6. Verify the API reports the predecessor digest and a continuity mismatch/fail-closed state.
   Do not activate a plan for the old image and do not start its worker.
7. Forward-fix against the current schema, build a new exact candidate, restore the candidate
   environment, and activate a new reviewed successor epoch before any worker or PAPER resume.

If `safety-control` cannot prove the exact STOP, rollback is blocked. Stop the worker to prevent
additional writes, retain all evidence, and use only the separately audited break-glass
procedure to restore database kills. The rollback remains API-only; no worker may start until
that action is recorded and the full fail-closed state is independently re-proved.
