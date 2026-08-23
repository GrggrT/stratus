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
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$expectedReleaseDockerSha256 =
  "388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d"
$expectedReleaseGitSha256 =
  "fec691d80fccc35fcc309fbc9f720536c1d795b8a562ec169f28c9923da9600f"
$expectedReleaseComposeSha256 =
  "8482829f77d14da8dfdfd0b553847946cee5161c90312219cf4f47d1f132d2bf"
$releaseComposePlugin = [IO.Path]::GetFullPath(
  "C:\Program Files\Docker\cli-plugins\docker-compose.exe"
)

function Resolve-ApprovedReleaseApplication {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$ExpectedSha256
  )
  $approved = @(
    Get-Command -Name $Name -CommandType Application -All -ErrorAction Stop |
      ForEach-Object { [IO.Path]::GetFullPath([string]$_.Source) } |
      Sort-Object -Unique |
      Where-Object {
        $item = Get-Item -LiteralPath $_ -Force
        -not $item.PSIsContainer -and
          -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
          (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant() -ceq
            $ExpectedSha256
      }
  )
  if ($approved.Count -ne 1) {
    throw "$Name did not resolve to exactly one source-approved executable."
  }
  return $approved[0]
}

$script:releaseDockerExecutable = Resolve-ApprovedReleaseApplication `
  -Name "docker" -ExpectedSha256 $expectedReleaseDockerSha256
$script:releaseGitExecutable = Resolve-ApprovedReleaseApplication `
  -Name "git" -ExpectedSha256 $expectedReleaseGitSha256
foreach ($forbiddenOverride in @(
  "DOCKER_API_VERSION",
  "DOCKER_CERT_PATH",
  "DOCKER_CLI_PLUGIN_EXTRA_DIRS",
  "DOCKER_CONFIG",
  "DOCKER_CONTEXT",
  "DOCKER_HOST",
  "DOCKER_TLS_VERIFY",
  "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  "GIT_CONFIG_COUNT",
  "GIT_CONFIG_GLOBAL",
  "GIT_CONFIG_NOSYSTEM",
  "GIT_CONFIG_SYSTEM",
  "GIT_DIR",
  "GIT_EXEC_PATH",
  "GIT_INDEX_FILE",
  "GIT_OBJECT_DIRECTORY",
  "GIT_WORK_TREE"
)) {
  if (Test-Path -LiteralPath "Env:$forbiddenOverride") {
    throw "Release executable override environment variable is forbidden: $forbiddenOverride"
  }
}
$userComposePlugin = Join-Path $HOME ".docker\cli-plugins\docker-compose.exe"
if (Test-Path -LiteralPath $userComposePlugin) {
  throw "A user-scoped Docker Compose plugin shadows the source-approved system plugin."
}

function Assert-ReleaseExecutableBoundary {
  if (
    (Get-FileHash -LiteralPath $script:releaseDockerExecutable -Algorithm SHA256).
      Hash.ToLowerInvariant() -cne $expectedReleaseDockerSha256 -or
    (Get-FileHash -LiteralPath $script:releaseGitExecutable -Algorithm SHA256).
      Hash.ToLowerInvariant() -cne $expectedReleaseGitSha256 -or
    -not (Test-Path -LiteralPath $releaseComposePlugin -PathType Leaf) -or
    ((Get-Item -LiteralPath $releaseComposePlugin -Force).Attributes -band
      [IO.FileAttributes]::ReparsePoint) -or
    (Get-FileHash -LiteralPath $releaseComposePlugin -Algorithm SHA256).
      Hash.ToLowerInvariant() -cne $expectedReleaseComposeSha256
  ) {
    throw "Release Docker, Compose, or Git executable provenance changed."
  }
}

function Assert-ReleaseDockerEngineBoundary {
  $contextOutput = @(& $script:releaseDockerExecutable context show 2>&1)
  if (
    $LASTEXITCODE -ne 0 -or
    $contextOutput.Count -ne 1 -or
    [string]$contextOutput[0] -cne "desktop-linux"
  ) {
    throw "Release Docker context is not the pinned desktop-linux context."
  }
  $endpointOutput = @(
    & $script:releaseDockerExecutable context inspect desktop-linux `
      --format "{{json .Endpoints.docker}}" 2>&1
  )
  $engineOutput = @(
    & $script:releaseDockerExecutable info --format "{{json .}}" 2>&1
  )
  if (
    $endpointOutput.Count -ne 1 -or
    $engineOutput.Count -ne 1 -or
    $LASTEXITCODE -ne 0
  ) {
    throw "Release Docker endpoint or engine identity could not be read exactly."
  }
  $endpoint = $endpointOutput[0] | ConvertFrom-Json
  $engine = $engineOutput[0] | ConvertFrom-Json
  if (
    [string]$endpoint.Host -cne "npipe:////./pipe/dockerDesktopLinuxEngine" -or
    [string]$engine.ID -cne "d98e03f5-06d7-4af4-9f21-b6f7a70ebf68" -or
    [string]$engine.ServerVersion -cne "28.3.2" -or
    [string]$engine.Name -cne "docker-desktop"
  ) {
    throw "Release Docker endpoint or engine identity drifted."
  }
}

function docker {
  Assert-ReleaseExecutableBoundary
  Assert-ReleaseDockerEngineBoundary
  & $script:releaseDockerExecutable @args
}
function git {
  Assert-ReleaseExecutableBoundary
  & $script:releaseGitExecutable @args
}
Assert-ReleaseExecutableBoundary
$releaseDockerPlugins = @(
  docker info --format "{{json .ClientInfo.Plugins}}" | ConvertFrom-Json
)
$releaseComposePluginProof = @(
  $releaseDockerPlugins | Where-Object { [string]$_.Name -ceq "compose" }
)
if (
  $LASTEXITCODE -ne 0 -or
  $releaseComposePluginProof.Count -ne 1 -or
  [IO.Path]::GetFullPath([string]$releaseComposePluginProof[0].Path) -cne
    $releaseComposePlugin -or
  [string]$releaseComposePluginProof[0].Version -cne "v2.38.2-desktop.1"
) {
  throw "Docker did not expose the exact source-approved Compose plugin."
}
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
  "COMPOSE_PATH_SEPARATOR",
  "COMPOSE_PROVIDER"
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
      $_ -match '^\s*(?:export\s+)?(?:COMPOSE_(?:FILE|PROFILES|PROJECT_NAME|PATH_SEPARATOR|PROVIDER)|DOCKER_CONFIG|DOCKER_CLI_PLUGIN_EXTRA_DIRS)\s*='
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
PostgreSQL container be considered for the bounded backup/migration/load-gate window. Set
`STRATUS_CUTOVER_SCHEMA_PATH` explicitly to exactly `20260730_0046` or `20260730_0047` before
entering the executable block. An absent, blank, or different value stops the cutover before any
container start. Section 2 alone is not sufficient to choose either path.

On the `0046` path, the runbook starts only the retained container by ID, without Compose recreation
or pull. On the `0047` path, Transition performs that same exact start internally. In both cases,
record the container ID, image ID, restart count, and OOM state before and after startup. Every
application writer, including API, worker, Telegram, Binance market stream, and `safety-control`,
remains stopped. The backup sidecar that was allowed during the code-only phase must also be stopped
before PostgreSQL starts. A different or recreated PostgreSQL container stops the cutover.

For a first-time `20260730_0046` migration, the fixed HBA proof remains the first database action,
followed by the schema-head query and the create-new backup/restore gate. For an explicit
already-`20260730_0047` successor, one
`Assert-StratusPostMigrationSuccessorGate.ps1 -Mode Transition` invocation owns the complete
stopped-to-live boundary in one uninterrupted process: stopped PreStart proof, exactly one start of
the retained PostgreSQL container, fail-closed restart-policy arming, byte proof, controlled HBA
reload, exact loaded-rule proof, byte recheck, two bounded read-only snapshots, and final transition
proof. The runbook must not start
PostgreSQL on this path and must not invoke public `PreStart` or `Live` modes. The accepted Live
envelope supplies the schema head but authorizes no API, worker, safety-control, LIVE, Binance, or
migration action. Only its matching successful terminal receipt may authorize API staging. Together
they replace the legacy pre-migration `Resume` gate. Any `0047`
database reached without that Transition lineage, or any other head, stops the cutover.

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
byte recheck. Transition owns this sequence internally for `0047`; the guarded block below owns it
for `0046`. That proof must finish before the schema-head query, backup, restore, or migration so no
privileged cutover activity can run under unproved HBA rules.

For an already-`0047` successor, set all four paths before entering the block:
`STRATUS_PRE_MIGRATION_BACKUP_DIRECTORY` and `STRATUS_PRE_MIGRATION_ATTESTATION` identify the
immutable accepted pre-`0047` backup; `STRATUS_SUCCESSOR_LINEAGE_DIRECTORY` contains the exact
reviewed Section 3 artifacts; and `STRATUS_SUCCESSOR_EVIDENCE_DIRECTORY` is an existing regular
directory. After the backup sidecar is stopped and while every container remains stopped, the
source-pinned repair attaches the retained PostgreSQL container to the exact safety-plane network
with both reviewed aliases, or proves that exact attachment already exists. Each invocation
generates one random 32-hex attempt ID and derives the network-repair, PreStart, Live, watchdog
lease, terminal receipt, owner-heartbeat, and Live-commit artifact names from the revision and that
same attempt ID. All seven paths must be below the production root, pairwise distinct, and absent.
Setting only some of these values, or finding any attempt-scoped output path already present,
stops the procedure before PostgreSQL starts. Every artifact published by a failed attempt,
including its PreStart, lease, heartbeat, terminal receipt, Live, or commit artifact, is terminal
lineage: never delete or reuse it; retry with the automatically generated new attempt ID and new
create-new paths. For a first-time `0046` migration all four successor values
must be unset; a stray value stops the procedure. The Transition gate's internal PreStart
authorization expires after ten minutes, its retained-PostgreSQL start must occur within five
minutes. Before PreStart, Transition changes the exact stopped PostgreSQL restart policy once from
`unless-stopped` to `no` and leaves it fail closed; a host restart therefore cannot auto-start an
unaccepted database. Docker event history must prove that one update, exactly one start, and no
stop, die, restart, kill, OOM, pause, unpause, or destroy transition throughout Live acceptance.

```powershell
$cutoverSchemaPath = [string]$env:STRATUS_CUTOVER_SCHEMA_PATH
function Assert-CutoverJsonObject {
  param([AllowNull()][object]$Value, [Parameter(Mandatory = $true)][string]$Label)
  if (
    $null -eq $Value -or
    $Value.GetType().FullName -cne "System.Management.Automation.PSCustomObject"
  ) {
    throw "$Label must be a native JSON object."
  }
}
function Assert-CutoverJsonString {
  param([AllowNull()][object]$Value, [Parameter(Mandatory = $true)][string]$Label)
  if ($null -eq $Value -or $Value.GetType() -ne [string]) {
    throw "$Label must be a native JSON string."
  }
}
function Assert-CutoverJsonBoolean {
  param([AllowNull()][object]$Value, [Parameter(Mandatory = $true)][string]$Label)
  if ($null -eq $Value -or $Value.GetType() -ne [bool]) {
    throw "$Label must be a native JSON boolean."
  }
}
function Assert-CutoverJsonInteger {
  param([AllowNull()][object]$Value, [Parameter(Mandatory = $true)][string]$Label)
  if (
    $null -eq $Value -or
    $Value.GetType().FullName -cnotin @("System.Int32", "System.Int64")
  ) {
    throw "$Label must be a native JSON integer."
  }
}
function Assert-CutoverJsonArray {
  param([AllowNull()][object]$Value, [Parameter(Mandatory = $true)][string]$Label)
  if ($null -eq $Value -or $Value -isnot [Array]) {
    throw "$Label must be a native JSON array."
  }
}
function Assert-CutoverExactProperties {
  param(
    [Parameter(Mandatory = $true)][object]$Value,
    [Parameter(Mandatory = $true)][string[]]$Expected,
    [Parameter(Mandatory = $true)][string]$Label
  )
  Assert-CutoverJsonObject -Value $Value -Label $Label
  $actual = @($Value.PSObject.Properties.Name | Sort-Object)
  $wanted = @($Expected | Sort-Object)
  if (($actual -join "`n") -cne ($wanted -join "`n")) {
    throw "$Label does not have the exact reviewed property set."
  }
}
function Get-CutoverSha256Text {
  param([Parameter(Mandatory = $true)][string]$Value)
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
    return [BitConverter]::ToString(
      $algorithm.ComputeHash($bytes)
    ).Replace("-", "").ToLowerInvariant()
  }
  finally {
    $algorithm.Dispose()
  }
}
function Get-CutoverFileSha256 {
  param([Parameter(Mandatory = $true)][string]$LiteralPath)
  $item = Get-Item -LiteralPath $LiteralPath -Force
  if (
    $item.PSIsContainer -or
    ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    $item.Length -le 0
  ) {
    throw "Cutover evidence must be a non-empty regular file: $LiteralPath"
  }
  return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).
    Hash.ToLowerInvariant()
}
function ConvertFrom-CutoverJsonText {
  param(
    [Parameter(Mandatory = $true)][string]$Json,
    [Parameter(Mandatory = $true)][string]$Label
  )
  $parameters = @{
    InputObject = $Json
  }
  $cmdlet = Get-Command -Name "Microsoft.PowerShell.Utility\ConvertFrom-Json" `
    -CommandType Cmdlet -ErrorAction Stop
  if ($cmdlet.Parameters.ContainsKey("DateKind")) {
    $parameters["DateKind"] = "String"
  }
  $value = Microsoft.PowerShell.Utility\ConvertFrom-Json @parameters
  Assert-CutoverJsonObject -Value $value -Label $Label
  return $value
}
function Read-CutoverJsonFile {
  param(
    [Parameter(Mandatory = $true)][string]$LiteralPath,
    [Parameter(Mandatory = $true)][string]$Label
  )
  [void](Get-CutoverFileSha256 -LiteralPath $LiteralPath)
  return ConvertFrom-CutoverJsonText `
    -Json (Get-Content -LiteralPath $LiteralPath -Raw) -Label $Label
}
function ConvertTo-CutoverCanonicalUtcTime {
  param(
    [AllowNull()][object]$Value,
    [Parameter(Mandatory = $true)][string]$Label
  )
  Assert-CutoverJsonString -Value $Value -Label $Label
  try {
    $parsed = [DateTimeOffset]::ParseExact(
      [string]$Value,
      "o",
      [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::None
    )
  }
  catch {
    throw "$Label must be a canonical round-trip timestamp."
  }
  if ($parsed.Offset -ne [TimeSpan]::Zero -or $parsed.ToString("o") -cne [string]$Value) {
    throw "$Label must be a canonical UTC timestamp."
  }
  return $parsed
}
if ($cutoverSchemaPath -cnotin @(
  "20260730_0046",
  "20260730_0047"
)) {
  throw "STRATUS_CUTOVER_SCHEMA_PATH must explicitly select 20260730_0046 or 20260730_0047."
}
$successorPathNames = @(
  "STRATUS_PRE_MIGRATION_BACKUP_DIRECTORY",
  "STRATUS_PRE_MIGRATION_ATTESTATION",
  "STRATUS_SUCCESSOR_LINEAGE_DIRECTORY",
  "STRATUS_SUCCESSOR_EVIDENCE_DIRECTORY"
)
if ($cutoverSchemaPath -ceq "20260730_0046") {
  if (@(
    $successorPathNames | Where-Object {
      Test-Path -LiteralPath "Env:$_"
    }
  ).Count -ne 0) {
    throw "The 0046 path requires every successor lineage/evidence value to be unset."
  }
}
if ($cutoverSchemaPath -ceq "20260730_0047") {
  if (@(
    $successorPathNames | Where-Object {
      -not (Test-Path -LiteralPath "Env:$_") -or
      [string]::IsNullOrWhiteSpace((Get-Item -LiteralPath "Env:$_").Value)
    }
  ).Count -ne 0) {
    throw "The 0047 Transition path requires all four immutable lineage/evidence paths."
  }
}

$cutoverAttemptId = [guid]::NewGuid().ToString("N")
$successorPreStartAttestation = $null
$successorLiveAcceptance = $null
$successorWatchdogLease = $null
$successorWatchdogTerminal = $null
$successorWatchdogHeartbeat = $null
$successorWatchdogCommit = $null
if ($cutoverSchemaPath -ceq "20260730_0047") {
  $backupDirectory = [IO.Path]::GetFullPath(
    $env:STRATUS_PRE_MIGRATION_BACKUP_DIRECTORY
  )
  $preMigrationAttestation = [IO.Path]::GetFullPath(
    $env:STRATUS_PRE_MIGRATION_ATTESTATION
  )
  $successorLineageDirectory = [IO.Path]::GetFullPath(
    $env:STRATUS_SUCCESSOR_LINEAGE_DIRECTORY
  )
  $successorEvidenceDirectory = [IO.Path]::GetFullPath(
    $env:STRATUS_SUCCESSOR_EVIDENCE_DIRECTORY
  )
  $networkRepairEvidenceDirectory = $successorEvidenceDirectory
  $successorPreStartAttestation = Join-Path $successorEvidenceDirectory (
    "post-migration-successor-prestart-$($revision.Substring(0, 12))-$cutoverAttemptId.json"
  )
  $successorLiveAcceptance = Join-Path $successorEvidenceDirectory (
    "post-migration-successor-live-$($revision.Substring(0, 12))-$cutoverAttemptId.json"
  )
  $successorWatchdogLease = $successorPreStartAttestation + ".watchdog-lease.json"
  $successorWatchdogTerminal = $successorPreStartAttestation + ".watchdog-terminal.json"
  $successorWatchdogHeartbeat = $successorPreStartAttestation + ".watchdog-heartbeat.json"
  $successorWatchdogCommit = $successorPreStartAttestation + ".watchdog-commit.json"
}
else {
  $networkRepairEvidenceDirectory = [IO.Path]::GetFullPath(
    (Join-Path $productionRoot "backups")
  )
}
$networkRepairAttestation = Join-Path $networkRepairEvidenceDirectory (
  "stopped-postgres-network-repair-$($revision.Substring(0, 12))-$cutoverAttemptId.json"
)
$attemptScopedEvidencePaths = @($networkRepairAttestation)
if ($cutoverSchemaPath -ceq "20260730_0047") {
  $attemptScopedEvidencePaths += @(
    $successorPreStartAttestation,
    $successorLiveAcceptance,
    $successorWatchdogLease,
    $successorWatchdogTerminal,
    $successorWatchdogHeartbeat,
    $successorWatchdogCommit
  )
}
if (@($attemptScopedEvidencePaths | Sort-Object -Unique).Count -ne
  $attemptScopedEvidencePaths.Count
) {
  throw "Attempt-scoped repair/Transition evidence paths must be pairwise distinct."
}
foreach ($attemptScopedPath in $attemptScopedEvidencePaths) {
  $attemptScopedFullPath = [IO.Path]::GetFullPath($attemptScopedPath)
  $attemptScopedRootPrefix = [IO.Path]::GetFullPath($productionRoot).TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
  ) + [IO.Path]::DirectorySeparatorChar
  if (-not $attemptScopedFullPath.StartsWith(
    $attemptScopedRootPrefix,
    [StringComparison]::OrdinalIgnoreCase
  )) {
    throw "Attempt-scoped evidence path escaped the production root: $attemptScopedFullPath"
  }
}
if (@($attemptScopedEvidencePaths | Where-Object {
  Test-Path -LiteralPath $_
}).Count -ne 0) {
  throw "Every attempt-scoped repair/Transition evidence path must be absent."
}
$cutoverHbaSource = [IO.Path]::GetFullPath(
  (Join-Path $productionRoot "ops\postgres\stratus-pg_hba.conf")
)
$cutoverHbaSourceItem = Get-Item -LiteralPath $cutoverHbaSource -Force
if (
  $cutoverHbaSourceItem.PSIsContainer -or
  ($cutoverHbaSourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
  (Get-FileHash -LiteralPath $cutoverHbaSource -Algorithm SHA256).
    Hash.ToLowerInvariant() -cne
    "164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4"
) {
  throw "Cutover HBA source is not the fixed reviewed regular file."
}
# FileShare.Read denies write/delete/replace for the entire shell-owned cutover.
# Keep this handle open until the release is stopped or completed; do not dispose
# it between Transition, role provisioning, and application admission.
$runbookHbaReadLock = [IO.FileStream]::new(
  $cutoverHbaSource,
  [IO.FileMode]::Open,
  [IO.FileAccess]::Read,
  [IO.FileShare]::Read
)

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
$networkRepairResult = & (
  Join-Path $buildContext "scripts\Invoke-StratusStoppedPostgresNetworkRepair.ps1"
) -Root $productionRoot -EvidencePath $networkRepairAttestation
Assert-CutoverExactProperties -Value $networkRepairResult -Label "network repair result" `
  -Expected @("accepted", "disposition", "evidence_path")
Assert-CutoverJsonBoolean -Value $networkRepairResult.accepted `
  -Label "network repair result accepted"
Assert-CutoverJsonString -Value $networkRepairResult.disposition `
  -Label "network repair result disposition"
Assert-CutoverJsonString -Value $networkRepairResult.evidence_path `
  -Label "network repair result evidence_path"
if (
  $networkRepairResult.accepted -ne $true -or
  $networkRepairResult.evidence_path -cne $networkRepairAttestation -or
  $networkRepairResult.disposition -cnotin @(
    "connected_by_this_invocation",
    "verified_exact_existing"
  )
) {
  throw "Stopped PostgreSQL safety-plane repair was not accepted."
}
$networkRepairEnvelope = Read-CutoverJsonFile `
  -LiteralPath $networkRepairAttestation -Label "network repair evidence"
Assert-CutoverExactProperties -Value $networkRepairEnvelope `
  -Label "network repair evidence" `
  -Expected @("format", "accepted", "payload", "payload_sha256")
Assert-CutoverJsonString -Value $networkRepairEnvelope.format `
  -Label "network repair format"
Assert-CutoverJsonBoolean -Value $networkRepairEnvelope.accepted `
  -Label "network repair accepted"
Assert-CutoverJsonObject -Value $networkRepairEnvelope.payload `
  -Label "network repair payload"
Assert-CutoverJsonString -Value $networkRepairEnvelope.payload_sha256 `
  -Label "network repair payload_sha256"
$networkRepairPayload = $networkRepairEnvelope.payload
Assert-CutoverExactProperties -Value $networkRepairPayload `
  -Label "network repair payload" -Expected @(
    "accepted_at",
    "disposition",
    "fixed_identity",
    "docker_engine",
    "snapshots",
    "comparisons",
    "mutation",
    "authorization"
  )
$networkRepairPayloadJson = $networkRepairPayload |
  ConvertTo-Json -Depth 30 -Compress
$networkRepairPayloadSha256 = Get-CutoverSha256Text -Value $networkRepairPayloadJson
$networkRepairAttestationSha256 = Get-CutoverFileSha256 `
  -LiteralPath $networkRepairAttestation
if (
  $networkRepairEnvelope.format -cne
    "stratus-stopped-postgres-safety-plane-repair-v1" -or
  $networkRepairEnvelope.accepted -ne $true -or
  $networkRepairEnvelope.payload_sha256 -cne $networkRepairPayloadSha256 -or
  $networkRepairPayload.disposition -cne $networkRepairResult.disposition
) {
  throw "Stopped PostgreSQL network repair evidence envelope is invalid."
}
foreach ($objectName in @(
  "fixed_identity",
  "docker_engine",
  "snapshots",
  "comparisons",
  "mutation",
  "authorization"
)) {
  Assert-CutoverJsonObject -Value $networkRepairPayload.$objectName `
    -Label "network repair $objectName"
}
if (
  $networkRepairPayload.fixed_identity.postgres_container_id -cne
    "6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299" -or
  $networkRepairPayload.fixed_identity.default_network_id -cne
    "77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2" -or
  $networkRepairPayload.fixed_identity.safety_network_id -cne
    "0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620" -or
  $networkRepairPayload.docker_engine.context -cne "desktop-linux" -or
  $networkRepairPayload.docker_engine.endpoint -cne
    "npipe:////./pipe/dockerDesktopLinuxEngine" -or
  $networkRepairPayload.docker_engine.engine_id -cne
    "d98e03f5-06d7-4af4-9f21-b6f7a70ebf68" -or
  $networkRepairPayload.docker_engine.server_version -cne "28.3.2" -or
  $networkRepairPayload.docker_engine.engine_name -cne "docker-desktop"
) {
  throw "Stopped PostgreSQL network repair identity/engine binding is invalid."
}
foreach ($name in @(
  "pre_equals_armed",
  "post_first_equals_post_second",
  "docker_engine_unchanged",
  "all_container_lifecycle_inventory_unchanged",
  "postgres_stopped_identity_unchanged",
  "production_network_identity_unchanged",
  "post_attachment_exact_twice"
)) {
  Assert-CutoverJsonBoolean -Value $networkRepairPayload.comparisons.$name `
    -Label "network repair comparison $name"
  if ($networkRepairPayload.comparisons.$name -ne $true) {
    throw "Stopped PostgreSQL network repair comparison was not accepted: $name"
  }
}
foreach ($name in @(
  "postgres_start_permitted",
  "api_start_permitted",
  "worker_start_permitted",
  "safety_control_start_permitted",
  "live_permitted",
  "binance_kill_switch_removal_permitted"
)) {
  Assert-CutoverJsonBoolean -Value $networkRepairPayload.authorization.$name `
    -Label "network repair authorization $name"
  if ($networkRepairPayload.authorization.$name -ne $false) {
    throw "Stopped network repair unexpectedly authorized runtime activity: $name"
  }
}
Assert-CutoverJsonBoolean -Value $networkRepairPayload.mutation.issued `
  -Label "network repair mutation issued"
if (
  $networkRepairPayload.snapshots.post_first.postgres.attachment_state -cne "exact" -or
  $networkRepairPayload.snapshots.post_second.postgres.attachment_state -cne "exact" -or
  $networkRepairPayload.snapshots.post_first.canonical_sha256 -cne
    $networkRepairPayload.snapshots.post_second.canonical_sha256 -or
  ($networkRepairPayload.disposition -ceq "connected_by_this_invocation") -ne
    $networkRepairPayload.mutation.issued
) {
  throw "Stopped PostgreSQL network repair did not prove one stable exact attachment."
}
$postRepairTopology = & (
  Join-Path $buildContext "scripts\Assert-StratusPreStartTopology.ps1"
) -Root $productionRoot -RequirePostgresStopped | ConvertFrom-Json
if (
  -not $postRepairTopology.verified -or
  $postRepairTopology.postgres_running -or
  [int]$postRepairTopology.allowed_running_backup_count -ne 0 -or
  [int]$postRepairTopology.unexpected_running_count -ne 0 -or
  [string]$postRepairTopology.postgres_container_id -ne
    [string]$postBackupStopTopology.postgres_container_id
) {
  throw "Stopped PostgreSQL topology was not exact after safety-plane repair."
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
$successorTransitionGate = $null
$successorApiStagePermitted = $false

if ($cutoverSchemaPath -ceq "20260730_0046") {
  if ($postgresBeforeStart.State.Running) {
    throw "The explicit 0046 path requires the retained PostgreSQL container to be stopped."
  }
  $expectedHbaSource = $cutoverHbaSource
  docker start $postgresContainer[0] | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not start the retained production PostgreSQL container."
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
  function Invoke-AcknowledgedRunbookHbaReload {
    $reloadPrefix = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" <<'STRATUS_SQL'
SELECT 'BASE|' || concat_ws(
  '|', pg_conf_load_time()::text, clock_timestamp()::text,
  pg_postmaster_start_time()::text, pg_backend_pid()::text,
  (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid())
);
SELECT 'RELOAD|' || pg_reload_conf()::text;
'@
    $reloadPolls = [Text.StringBuilder]::new()
    foreach ($pollNumber in 1..40) {
      [void]$reloadPolls.AppendLine("SELECT pg_sleep(0.25);")
      [void]$reloadPolls.AppendLine((@"
SELECT 'POLL|$pollNumber|' || concat_ws(
  '|', pg_conf_load_time()::text, clock_timestamp()::text,
  pg_postmaster_start_time()::text, pg_backend_pid()::text,
  (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid())
);
"@).TrimStart())
    }
    $reloadCommand = $reloadPrefix + "`n" + $reloadPolls.ToString() + "STRATUS_SQL`n"
    $reloadRows = @(
      docker compose @releaseComposeArguments exec -T postgres sh -ec $reloadCommand
    )
    $baseRows = @($reloadRows | Where-Object { [string]$_ -like "BASE|*" })
    $acceptedRows = @($reloadRows | Where-Object { [string]$_ -like "RELOAD|*" })
    $pollRows = @($reloadRows | Where-Object { [string]$_ -like "POLL|*" })
    if (
      $LASTEXITCODE -ne 0 -or
      $baseRows.Count -ne 1 -or
      $acceptedRows.Count -ne 1 -or
      [string]$acceptedRows[0] -cne "RELOAD|true" -or
      $pollRows.Count -ne 40
    ) {
      throw "PostgreSQL did not return the bounded HBA reload acknowledgement proof."
    }
    $baseParts = ([string]$baseRows[0]).Split('|')
    if ($baseParts.Count -ne 6 -or [int]$baseParts[4] -le 0) {
      throw "PostgreSQL returned malformed HBA reload baseline metadata."
    }
    $loadBefore = [DateTimeOffset]::Parse($baseParts[1]).ToUniversalTime()
    $requestedAt = [DateTimeOffset]::Parse($baseParts[2]).ToUniversalTime()
    $postmasterAt = [DateTimeOffset]::Parse($baseParts[3]).ToUniversalTime()
    $backendPid = [int]$baseParts[4]
    $backendStartedAt = [DateTimeOffset]::Parse($baseParts[5]).ToUniversalTime()
    $acknowledgedPoll = 0
    $loadAfter = [DateTimeOffset]::MinValue
    $acknowledgedAt = [DateTimeOffset]::MinValue
    foreach ($pollIndex in 0..39) {
      $parts = ([string]$pollRows[$pollIndex]).Split('|')
      if (
        $parts.Count -ne 7 -or
        [int]$parts[1] -ne ($pollIndex + 1) -or
        [int]$parts[5] -ne $backendPid -or
        [DateTimeOffset]::Parse($parts[4]).ToUniversalTime() -ne $postmasterAt -or
        [DateTimeOffset]::Parse($parts[6]).ToUniversalTime() -ne $backendStartedAt
      ) {
        throw "PostgreSQL HBA acknowledgement escaped its original backend."
      }
      $sampleLoad = [DateTimeOffset]::Parse($parts[2]).ToUniversalTime()
      $sampledAt = [DateTimeOffset]::Parse($parts[3]).ToUniversalTime()
      if (
        $acknowledgedPoll -eq 0 -and
        $sampleLoad -gt $loadBefore -and
        $sampleLoad -ge $requestedAt
      ) {
        $acknowledgedPoll = $pollIndex + 1
        $loadAfter = $sampleLoad
        $acknowledgedAt = $sampledAt
      }
      elseif ($acknowledgedPoll -gt 0 -and $sampleLoad -ne $loadAfter) {
        throw "An additional HBA reload occurred during the bounded acknowledgement proof."
      }
    }
    if (
      $acknowledgedPoll -eq 0 -or
      $loadAfter -lt $requestedAt -or
      $loadAfter -gt $acknowledgedAt -or
      $backendStartedAt -gt $requestedAt
    ) {
      throw "PostgreSQL did not acknowledge the requested HBA generation in time."
    }
    $freshCommand = @'
PGPASSWORD="$POSTGRES_PASSWORD" exec psql -XAtq --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT concat_ws('|', pg_conf_load_time()::text, clock_timestamp()::text, pg_postmaster_start_time()::text, pg_backend_pid()::text, (SELECT backend_start::text FROM pg_stat_activity WHERE pid = pg_backend_pid()))"
'@
    $freshRows = @(docker compose @releaseComposeArguments exec -T postgres sh -ec $freshCommand)
    if ($LASTEXITCODE -ne 0 -or $freshRows.Count -ne 1) {
      throw "A fresh backend did not return the acknowledged HBA generation."
    }
    $freshParts = ([string]$freshRows[0]).Split('|')
    if ($freshParts.Count -ne 5 -or [int]$freshParts[3] -le 0) {
      throw "Fresh HBA backend metadata is malformed."
    }
    $freshLoad = [DateTimeOffset]::Parse($freshParts[0]).ToUniversalTime()
    $freshObservedAt = [DateTimeOffset]::Parse($freshParts[1]).ToUniversalTime()
    $freshPostmaster = [DateTimeOffset]::Parse($freshParts[2]).ToUniversalTime()
    $freshBackendStarted = [DateTimeOffset]::Parse($freshParts[4]).ToUniversalTime()
    if (
      $freshLoad -lt $requestedAt -or
      $freshLoad -gt $acknowledgedAt -or
      $freshObservedAt -lt $freshLoad -or
      $freshPostmaster -ne $postmasterAt -or
      $freshBackendStarted -lt $acknowledgedAt
    ) {
      throw "Fresh backend did not inherit the exact acknowledged HBA generation."
    }
    [pscustomobject]@{
      reload_requested_at = $requestedAt.ToString('o')
      conf_load_time_after = $loadAfter.ToString('o')
      reload_acknowledged_at = $acknowledgedAt.ToString('o')
      reload_poll_count = $acknowledgedPoll
      postmaster_started_at = $postmasterAt.ToString('o')
      fresh_backend_started_at = $freshBackendStarted.ToString('o')
    }
  }
  $immediateHbaReloadReceipt = Invoke-AcknowledgedRunbookHbaReload
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
      'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567'
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
  if ($LASTEXITCODE -ne 0 -or $preMigrationHead -cne $cutoverSchemaPath) {
    throw "The explicit 0046 path did not find the exact 20260730_0046 schema head."
  }
}

if ($cutoverSchemaPath -ceq "20260730_0047") {
  # Transition owns the stopped PreStart proof, exact retained-container
  # start, first database operations, Live proof, and final acceptance.
  $successorTransitionOutput = @(& (
    Join-Path $buildContext "scripts\Assert-StratusPostMigrationSuccessorGate.ps1"
  ) `
    -Mode Transition `
    -Root $productionRoot `
    -BuildContext $buildContext `
    -BackupDirectory $backupDirectory `
    -PreMigrationAttestationPath $preMigrationAttestation `
    -LineageDirectory $successorLineageDirectory `
    -ExpectedRevision $revision `
    -ExpectedImageId "sha256:$digest" `
    -CandidateImage $image `
    -PostgresContainer $postgresContainer[0] `
    -NetworkRepairAttestationPath $networkRepairAttestation `
    -PreStartAttestationPath $successorPreStartAttestation `
    -LiveAcceptancePath $successorLiveAcceptance)
  $successorTransitionOutput = @(
    $successorTransitionOutput |
      Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
  )
  if ($successorTransitionOutput.Count -ne 1) {
    throw "Successor Transition did not return exactly one Live JSON envelope."
  }
  $successorTransitionOutputObject = ConvertFrom-CutoverJsonText `
    -Json ([string]$successorTransitionOutput[0]) `
    -Label "successor Transition stdout Live acceptance"
  $successorTransitionGate = Read-CutoverJsonFile `
    -LiteralPath $successorLiveAcceptance -Label "successor Live acceptance"
  $successorPreStartGate = Read-CutoverJsonFile `
    -LiteralPath $successorPreStartAttestation -Label "successor PreStart attestation"
  foreach ($envelopeProof in @(
    [pscustomobject]@{
      value = $successorTransitionGate
      label = "successor Live acceptance"
    },
    [pscustomobject]@{
      value = $successorPreStartGate
      label = "successor PreStart attestation"
    }
  )) {
    Assert-CutoverExactProperties -Value $envelopeProof.value `
      -Label $envelopeProof.label `
      -Expected @("format", "accepted", "payload", "payload_sha256")
    Assert-CutoverJsonString -Value $envelopeProof.value.format `
      -Label "$($envelopeProof.label) format"
    Assert-CutoverJsonBoolean -Value $envelopeProof.value.accepted `
      -Label "$($envelopeProof.label) accepted"
    Assert-CutoverJsonObject -Value $envelopeProof.value.payload `
      -Label "$($envelopeProof.label) payload"
    Assert-CutoverJsonString -Value $envelopeProof.value.payload_sha256 `
      -Label "$($envelopeProof.label) payload_sha256"
    $recomputedPayloadSha256 = Get-CutoverSha256Text -Value (
      $envelopeProof.value.payload | ConvertTo-Json -Depth 30 -Compress
    )
    if (
      $envelopeProof.value.accepted -ne $true -or
      $envelopeProof.value.payload_sha256 -cne $recomputedPayloadSha256
    ) {
      throw "$($envelopeProof.label) payload hash or acceptance is invalid."
    }
  }
  if (
    ($successorTransitionOutputObject | ConvertTo-Json -Depth 30 -Compress) -cne
      ($successorTransitionGate | ConvertTo-Json -Depth 30 -Compress) -or
    $successorPreStartGate.format -cne
      "stratus-post-migration-successor-transition-prestart-v2" -or
    $successorTransitionGate.payload.prestart.path -cne
      $successorPreStartAttestation -or
    $successorTransitionGate.payload.prestart.sha256 -cne
      (Get-CutoverFileSha256 -LiteralPath $successorPreStartAttestation) -or
    $successorTransitionGate.payload.prestart.payload_sha256 -cne
      $successorPreStartGate.payload_sha256
  ) {
    throw "Successor Transition stdout/disk/PreStart lineage binding is invalid."
  }

  # Live is not an API-stage authorization by itself. The create-new watchdog
  # terminal receipt below is the authoritative out-of-cycle acceptance anchor.
  $successorPreStartAttestationSha256 = Get-CutoverFileSha256 `
    -LiteralPath $successorPreStartAttestation
  $successorLiveAcceptanceSha256 = Get-CutoverFileSha256 `
    -LiteralPath $successorLiveAcceptance
  $successorWatchdogLeaseSha256 = Get-CutoverFileSha256 `
    -LiteralPath $successorWatchdogLease
  $successorWatchdogCommitSha256 = Get-CutoverFileSha256 `
    -LiteralPath $successorWatchdogCommit
  $successorWatchdogHeartbeatSha256 = Get-CutoverFileSha256 `
    -LiteralPath $successorWatchdogHeartbeat
  $successorWatchdogTerminalSha256 = Get-CutoverFileSha256 `
    -LiteralPath $successorWatchdogTerminal
  $successorWatchdogLeaseProof = Read-CutoverJsonFile `
    -LiteralPath $successorWatchdogLease -Label "successor watchdog lease"
  $successorWatchdogCommitProof = Read-CutoverJsonFile `
    -LiteralPath $successorWatchdogCommit -Label "successor watchdog Live commit"
  $successorWatchdogHeartbeatProof = Read-CutoverJsonFile `
    -LiteralPath $successorWatchdogHeartbeat -Label "successor watchdog final heartbeat"
  $successorWatchdogTerminalProof = Read-CutoverJsonFile `
    -LiteralPath $successorWatchdogTerminal -Label "successor watchdog terminal receipt"

  Assert-CutoverExactProperties -Value $successorPreStartGate.payload `
    -Label "successor PreStart payload" -Expected @(
      "accepted_at", "validity", "historical", "network_repair", "successor",
      "closed_contract", "compose_migrate", "postgres_prestart",
      "hba_host_read_lock", "topology", "transition_session", "authorization"
    )
  Assert-CutoverExactProperties `
    -Value $successorPreStartGate.payload.transition_session `
    -Label "successor PreStart transition_session" -Expected @(
      "mode", "session_id", "collector_process_id", "collector_process_started_at",
      "collector_requested_at", "docker_engine", "initial_barrier",
      "accepted_journal_prefix_bytes", "accepted_journal_prefix_sha256",
      "reconnect_permitted", "watchdog_program", "watchdog_handoff",
      "restart_policy_transition", "observation", "start_proof"
    )
  Assert-CutoverExactProperties -Value $successorTransitionGate.payload `
    -Label "successor Live payload" -Expected @(
      "accepted_at", "artifact_written_at", "historical", "network_repair",
      "prestart", "successor", "closed_contract", "postgres_transition",
      "transition_session", "topology", "hba", "final_boundary", "database",
      "authorization"
    )
  Assert-CutoverExactProperties `
    -Value $successorTransitionGate.payload.transition_session `
    -Label "successor Live transition_session" -Expected @(
      "mode", "session_id", "docker_engine", "event_witness",
      "watchdog_program", "watchdog", "watchdog_lease", "heartbeat_at_live",
      "restart_policy_transition", "observation", "start_proof", "rollback"
    )

  $successorPreStartSession = $successorPreStartGate.payload.transition_session
  $successorLiveSession = $successorTransitionGate.payload.transition_session
  $successorSessionId = [string]$successorLiveSession.session_id
  $currentCutoverProcess = [Diagnostics.Process]::GetCurrentProcess()
  try {
    $expectedWatchdogOwnerPid = [int]$currentCutoverProcess.Id
    $expectedWatchdogOwnerStartedAt = ([DateTimeOffset]::new(
      $currentCutoverProcess.StartTime.ToUniversalTime(), [TimeSpan]::Zero
    )).ToString("o")
    $expectedWatchdogHostPath = [IO.Path]::GetFullPath(
      $currentCutoverProcess.MainModule.FileName
    )
  }
  finally {
    $currentCutoverProcess.Dispose()
  }
  $expectedWatchdogScriptPath = [IO.Path]::GetFullPath(
    (Join-Path $productionRoot "scripts\Invoke-StratusSuccessorTransitionWatchdog.ps1")
  )
  $expectedWatchdogScriptSha256 =
    "334970d3f86da1a07da3f701a8349741440285b69206874fc20d63733793152a"
  $expectedWatchdogScriptItem = Get-Item -LiteralPath $expectedWatchdogScriptPath -Force
  $expectedWatchdogHostItem = Get-Item -LiteralPath $expectedWatchdogHostPath -Force
  if (
    $successorSessionId -notmatch '^[0-9a-f]{32}$' -or
    $successorPreStartSession.session_id -cne $successorSessionId -or
    $successorPreStartSession.mode -cne "single-process" -or
    $successorLiveSession.mode -cne "single-process"
  ) {
    throw "Successor PreStart/Live session identity is invalid."
  }

  foreach ($programProof in @(
    [pscustomobject]@{
      value = $successorPreStartSession.watchdog_program
      label = "PreStart watchdog program"
    },
    [pscustomobject]@{
      value = $successorLiveSession.watchdog_program
      label = "Live watchdog program"
    }
  )) {
    Assert-CutoverExactProperties -Value $programProof.value -Label $programProof.label `
      -Expected @(
        "script_path", "script_sha256", "script_bytes", "host_path", "host_sha256",
        "host_bytes", "owner_pid", "owner_start_time_utc", "read_locks_held"
      )
    foreach ($name in @(
      "script_path", "script_sha256", "host_path", "host_sha256",
      "owner_start_time_utc"
    )) {
      Assert-CutoverJsonString -Value $programProof.value.$name `
        -Label "$($programProof.label) $name"
    }
    foreach ($name in @("script_bytes", "host_bytes", "owner_pid")) {
      Assert-CutoverJsonInteger -Value $programProof.value.$name `
        -Label "$($programProof.label) $name"
    }
    Assert-CutoverJsonBoolean -Value $programProof.value.read_locks_held `
      -Label "$($programProof.label) read_locks_held"
    [void](ConvertTo-CutoverCanonicalUtcTime `
      -Value $programProof.value.owner_start_time_utc `
      -Label "$($programProof.label) owner_start_time_utc")
    if (
      $programProof.value.script_path -cne $expectedWatchdogScriptPath -or
      $programProof.value.script_sha256 -cne $expectedWatchdogScriptSha256 -or
      $programProof.value.script_sha256 -cne
        (Get-CutoverFileSha256 -LiteralPath $programProof.value.script_path) -or
      $programProof.value.script_bytes -ne [int64]$expectedWatchdogScriptItem.Length -or
      $programProof.value.host_path -cne $expectedWatchdogHostPath -or
      $programProof.value.host_sha256 -cne
        (Get-CutoverFileSha256 -LiteralPath $programProof.value.host_path) -or
      $programProof.value.host_bytes -ne [int64]$expectedWatchdogHostItem.Length -or
      $programProof.value.owner_pid -ne $expectedWatchdogOwnerPid -or
      $programProof.value.owner_start_time_utc -cne $expectedWatchdogOwnerStartedAt -or
      $programProof.value.read_locks_held -ne $true
    ) {
      throw "$($programProof.label) is not the exact read-locked process boundary."
    }
  }
  if (
    ($successorPreStartSession.watchdog_program |
      ConvertTo-Json -Depth 10 -Compress) -cne
    ($successorLiveSession.watchdog_program |
      ConvertTo-Json -Depth 10 -Compress)
  ) {
    throw "Successor watchdog program identity drifted between PreStart and Live."
  }

  foreach ($restartProof in @(
    [pscustomobject]@{
      value = $successorPreStartSession.restart_policy_transition
      label = "PreStart restart-policy transition"
    },
    [pscustomobject]@{
      value = $successorLiveSession.restart_policy_transition
      label = "Live restart-policy transition"
    }
  )) {
    Assert-CutoverExactProperties -Value $restartProof.value -Label $restartProof.label `
      -Expected @(
        "previous", "armed", "command", "command_issued",
        "stopped_identity_rechecked", "restore_on_success"
      )
    Assert-CutoverJsonString -Value $restartProof.value.previous `
      -Label "$($restartProof.label) previous"
    Assert-CutoverJsonString -Value $restartProof.value.armed `
      -Label "$($restartProof.label) armed"
    Assert-CutoverJsonArray -Value $restartProof.value.command `
      -Label "$($restartProof.label) command"
    foreach ($name in @(
      "command_issued", "stopped_identity_rechecked", "restore_on_success"
    )) {
      Assert-CutoverJsonBoolean -Value $restartProof.value.$name `
        -Label "$($restartProof.label) $name"
    }
    if (
      $restartProof.value.previous -cne "unless-stopped" -or
      $restartProof.value.armed -cne "no" -or
      (@($restartProof.value.command) -join "`n") -cne
        (@("update", "--restart=no", $postgresContainer[0]) -join "`n") -or
      $restartProof.value.command_issued -ne $true -or
      $restartProof.value.stopped_identity_rechecked -ne $true -or
      $restartProof.value.restore_on_success -ne $false
    ) {
      throw "$($restartProof.label) is not the exact fail-closed one-way transition."
    }
  }
  if (
    ($successorPreStartSession.restart_policy_transition |
      ConvertTo-Json -Depth 5 -Compress) -cne
    ($successorLiveSession.restart_policy_transition |
      ConvertTo-Json -Depth 5 -Compress)
  ) {
    throw "Restart-policy transition drifted between PreStart and Live."
  }

  Assert-CutoverExactProperties -Value $successorPreStartSession.observation `
    -Label "PreStart observation" `
    -Expected @("start_command_issued", "start_completed")
  Assert-CutoverExactProperties -Value $successorLiveSession.observation `
    -Label "Live observation" `
    -Expected @("start_command_issued", "start_completed")
  foreach ($name in @("start_command_issued", "start_completed")) {
    Assert-CutoverJsonBoolean -Value $successorPreStartSession.observation.$name `
      -Label "PreStart observation $name"
    Assert-CutoverJsonBoolean -Value $successorLiveSession.observation.$name `
      -Label "Live observation $name"
  }
  Assert-CutoverExactProperties -Value $successorPreStartSession.start_proof `
    -Label "PreStart start_proof" -Expected @(
      "start_owned_by_transition_process", "successor_started_at",
      "docker_start_event_time_nano"
    )
  Assert-CutoverExactProperties -Value $successorLiveSession.start_proof `
    -Label "Live start_proof" -Expected @(
      "start_owned_by_transition_process", "successor_started_at",
      "docker_start_event_time_nano"
    )
  Assert-CutoverJsonBoolean `
    -Value $successorPreStartSession.start_proof.start_owned_by_transition_process `
    -Label "PreStart start ownership"
  Assert-CutoverJsonBoolean `
    -Value $successorLiveSession.start_proof.start_owned_by_transition_process `
    -Label "Live start ownership"
  Assert-CutoverJsonString -Value $successorLiveSession.start_proof.successor_started_at `
    -Label "Live start_proof successor_started_at"
  Assert-CutoverJsonInteger `
    -Value $successorLiveSession.start_proof.docker_start_event_time_nano `
    -Label "Live start_proof Docker event time"
  if (
    $successorPreStartSession.observation.start_command_issued -ne $false -or
    $successorPreStartSession.observation.start_completed -ne $false -or
    $successorPreStartSession.start_proof.start_owned_by_transition_process -ne $false -or
    $null -ne $successorPreStartSession.start_proof.successor_started_at -or
    $null -ne $successorPreStartSession.start_proof.docker_start_event_time_nano -or
    $successorLiveSession.observation.start_command_issued -ne $true -or
    $successorLiveSession.observation.start_completed -ne $true -or
    $successorLiveSession.start_proof.start_owned_by_transition_process -ne $true -or
    $successorLiveSession.start_proof.successor_started_at -cne
      $successorTransitionGate.payload.postgres_transition.successor_started_at -or
    $successorLiveSession.start_proof.docker_start_event_time_nano -ne
      $successorLiveSession.event_witness.postgres_start_time_nano
  ) {
    throw "Successor start ownership escaped start_proof or is not exact."
  }

  $successorPreStartRepairBinding = $successorPreStartGate.payload.network_repair
  $successorLiveRepairBinding = $successorTransitionGate.payload.network_repair
  $networkRepairBindingProperties = @(
    "path", "sha256", "payload_sha256", "accepted_at", "expires_at",
    "disposition", "post_first_sha256", "post_second_sha256",
    "final_attachment_sha256", "docker_engine_contract_sha256"
  )
  foreach ($repairBindingProof in @(
    [pscustomobject]@{ value = $successorPreStartRepairBinding; label = "PreStart network_repair" },
    [pscustomobject]@{ value = $successorLiveRepairBinding; label = "Live network_repair" }
  )) {
    Assert-CutoverExactProperties -Value $repairBindingProof.value `
      -Label $repairBindingProof.label -Expected $networkRepairBindingProperties
    foreach ($name in $networkRepairBindingProperties) {
      Assert-CutoverJsonString -Value $repairBindingProof.value.$name `
        -Label "$($repairBindingProof.label) $name"
    }
  }
  $networkRepairAcceptedAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $networkRepairPayload.accepted_at -Label "network repair accepted_at"
  $networkRepairExpiresAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorLiveRepairBinding.expires_at -Label "network repair expires_at"
  if (
    ($successorPreStartRepairBinding | ConvertTo-Json -Depth 10 -Compress) -cne
      ($successorLiveRepairBinding | ConvertTo-Json -Depth 10 -Compress) -or
    $successorLiveRepairBinding.path -cne $networkRepairAttestation -or
    $successorLiveRepairBinding.sha256 -cne $networkRepairAttestationSha256 -or
    $successorLiveRepairBinding.payload_sha256 -cne
      $networkRepairEnvelope.payload_sha256 -or
    $successorLiveRepairBinding.accepted_at -cne
      $networkRepairAcceptedAt.ToString("o") -or
    $networkRepairExpiresAt -ne $networkRepairAcceptedAt.AddSeconds(600) -or
    $successorLiveRepairBinding.disposition -cne $networkRepairPayload.disposition -or
    $successorLiveRepairBinding.post_first_sha256 -cne
      $networkRepairPayload.snapshots.post_first.canonical_sha256 -or
    $successorLiveRepairBinding.post_second_sha256 -cne
      $networkRepairPayload.snapshots.post_second.canonical_sha256 -or
    $successorLiveRepairBinding.final_attachment_sha256 -cne
      $networkRepairPayload.snapshots.post_second.postgres.attachments_sha256 -or
    $successorLiveRepairBinding.docker_engine_contract_sha256 -cne
      $networkRepairPayload.docker_engine.contract_sha256 -or
    (Get-CutoverFileSha256 -LiteralPath $successorLiveRepairBinding.path) -cne
      $successorLiveRepairBinding.sha256
  ) {
    throw "PreStart/Live network_repair is not byte-identically bound to disk evidence."
  }

  $successorPreStartValidity = $successorPreStartGate.payload.validity
  Assert-CutoverExactProperties -Value $successorPreStartValidity `
    -Label "PreStart validity" -Expected @(
      "max_age_seconds", "postgres_start_window_seconds", "expires_at",
      "postgres_start_deadline"
    )
  foreach ($name in @("max_age_seconds", "postgres_start_window_seconds")) {
    Assert-CutoverJsonInteger -Value $successorPreStartValidity.$name `
      -Label "PreStart validity $name"
  }
  $successorPreStartAcceptedAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorPreStartGate.payload.accepted_at -Label "PreStart accepted_at"
  $successorPreStartExpiresAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorPreStartValidity.expires_at -Label "PreStart expires_at"
  $successorWatchdogHardDeadline = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorPreStartValidity.postgres_start_deadline `
    -Label "PreStart PostgreSQL start deadline"
  if (
    $successorPreStartValidity.max_age_seconds -ne 600 -or
    $successorPreStartValidity.postgres_start_window_seconds -ne 300 -or
    $successorPreStartExpiresAt -ne $successorPreStartAcceptedAt.AddSeconds(600) -or
    $successorWatchdogHardDeadline -ne $successorPreStartAcceptedAt.AddSeconds(300)
  ) {
    throw "Successor PreStart validity is not the exact bounded window."
  }
  $successorLiveAcceptedAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorTransitionGate.payload.accepted_at -Label "Live accepted_at"
  $successorLiveArtifactWrittenAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorTransitionGate.payload.artifact_written_at `
    -Label "Live artifact_written_at"
  if (
    $successorLiveAcceptedAt -lt $successorPreStartAcceptedAt -or
    $successorLiveArtifactWrittenAt -lt $successorLiveAcceptedAt -or
    $successorLiveArtifactWrittenAt -gt $successorWatchdogHardDeadline
  ) {
    throw "Successor Live artifact was not sealed inside its exact watchdog window."
  }

  $successorWatchdogProgram = $successorLiveSession.watchdog_program
  $successorWatchdogHandoff = $successorPreStartSession.watchdog_handoff
  $successorWatchdogBinding = $successorLiveSession.watchdog
  Assert-CutoverExactProperties -Value $successorWatchdogHandoff `
    -Label "PreStart watchdog handoff" -Expected @(
      "format", "session_id", "owner_pid", "owner_start_time_utc",
      "hard_deadline_utc", "heartbeat_timeout_seconds", "lease_path",
      "receipt_path", "heartbeat_path", "commit_path", "live_path", "prestart_path"
    )
  Assert-CutoverExactProperties -Value $successorWatchdogBinding `
    -Label "Live watchdog binding" -Expected @(
      "format", "session_id", "owner_pid", "owner_start_time_utc",
      "hard_deadline_utc", "heartbeat_timeout_seconds", "lease_path",
      "lease_sha256", "receipt_path", "heartbeat_path", "commit_path"
    )
  foreach ($integerProof in @(
    [pscustomobject]@{ value = $successorWatchdogHandoff.owner_pid; label = "handoff owner_pid" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.heartbeat_timeout_seconds; label = "handoff heartbeat timeout" },
    [pscustomobject]@{ value = $successorWatchdogBinding.owner_pid; label = "Live watchdog owner_pid" },
    [pscustomobject]@{ value = $successorWatchdogBinding.heartbeat_timeout_seconds; label = "Live watchdog heartbeat timeout" }
  )) {
    Assert-CutoverJsonInteger -Value $integerProof.value -Label $integerProof.label
  }
  foreach ($stringProof in @(
    [pscustomobject]@{ value = $successorWatchdogHandoff.format; label = "handoff format" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.session_id; label = "handoff session_id" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.owner_start_time_utc; label = "handoff owner start" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.hard_deadline_utc; label = "handoff deadline" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.lease_path; label = "handoff lease path" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.receipt_path; label = "handoff receipt path" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.heartbeat_path; label = "handoff heartbeat path" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.commit_path; label = "handoff commit path" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.live_path; label = "handoff Live path" },
    [pscustomobject]@{ value = $successorWatchdogHandoff.prestart_path; label = "handoff PreStart path" },
    [pscustomobject]@{ value = $successorWatchdogBinding.format; label = "Live watchdog format" },
    [pscustomobject]@{ value = $successorWatchdogBinding.session_id; label = "Live watchdog session_id" },
    [pscustomobject]@{ value = $successorWatchdogBinding.owner_start_time_utc; label = "Live watchdog owner start" },
    [pscustomobject]@{ value = $successorWatchdogBinding.hard_deadline_utc; label = "Live watchdog deadline" },
    [pscustomobject]@{ value = $successorWatchdogBinding.lease_path; label = "Live watchdog lease path" },
    [pscustomobject]@{ value = $successorWatchdogBinding.lease_sha256; label = "Live watchdog lease hash" },
    [pscustomobject]@{ value = $successorWatchdogBinding.receipt_path; label = "Live watchdog receipt path" },
    [pscustomobject]@{ value = $successorWatchdogBinding.heartbeat_path; label = "Live watchdog heartbeat path" },
    [pscustomobject]@{ value = $successorWatchdogBinding.commit_path; label = "Live watchdog commit path" }
  )) {
    Assert-CutoverJsonString -Value $stringProof.value -Label $stringProof.label
  }
  if (
    $successorWatchdogHandoff.format -cne
      "stratus-successor-transition-watchdog-handoff-v1" -or
    $successorWatchdogBinding.format -cne
      "stratus-successor-transition-watchdog-binding-v1" -or
    $successorWatchdogHandoff.session_id -cne $successorSessionId -or
    $successorWatchdogBinding.session_id -cne $successorSessionId -or
    $successorWatchdogHandoff.owner_pid -ne $successorWatchdogProgram.owner_pid -or
    $successorWatchdogBinding.owner_pid -ne $successorWatchdogProgram.owner_pid -or
    $successorWatchdogHandoff.owner_start_time_utc -cne
      $successorWatchdogProgram.owner_start_time_utc -or
    $successorWatchdogBinding.owner_start_time_utc -cne
      $successorWatchdogProgram.owner_start_time_utc -or
    $successorWatchdogHandoff.hard_deadline_utc -cne
      $successorWatchdogHardDeadline.ToString("o") -or
    $successorWatchdogBinding.hard_deadline_utc -cne
      $successorWatchdogHardDeadline.ToString("o") -or
    $successorWatchdogHandoff.heartbeat_timeout_seconds -ne 15 -or
    $successorWatchdogBinding.heartbeat_timeout_seconds -ne 15 -or
    $successorWatchdogHandoff.lease_path -cne $successorWatchdogLease -or
    $successorWatchdogBinding.lease_path -cne $successorWatchdogLease -or
    $successorWatchdogHandoff.receipt_path -cne $successorWatchdogTerminal -or
    $successorWatchdogBinding.receipt_path -cne $successorWatchdogTerminal -or
    $successorWatchdogHandoff.heartbeat_path -cne $successorWatchdogHeartbeat -or
    $successorWatchdogBinding.heartbeat_path -cne $successorWatchdogHeartbeat -or
    $successorWatchdogHandoff.commit_path -cne $successorWatchdogCommit -or
    $successorWatchdogBinding.commit_path -cne $successorWatchdogCommit -or
    $successorWatchdogHandoff.live_path -cne $successorLiveAcceptance -or
    $successorWatchdogHandoff.prestart_path -cne $successorPreStartAttestation -or
    $successorWatchdogBinding.lease_sha256 -cne $successorWatchdogLeaseSha256
  ) {
    throw "PreStart/Live watchdog handoff is not exact and path-bound."
  }

  Assert-CutoverExactProperties -Value $successorWatchdogLeaseProof `
    -Label "successor watchdog lease" -Expected @(
      "format", "armed", "armed_at", "session_id", "watchdog", "owner",
      "deadline", "artifacts", "docker_engine", "postgres", "containment"
    )
  Assert-CutoverJsonString -Value $successorWatchdogLeaseProof.format `
    -Label "watchdog lease format"
  Assert-CutoverJsonBoolean -Value $successorWatchdogLeaseProof.armed `
    -Label "watchdog lease armed"
  Assert-CutoverJsonString -Value $successorWatchdogLeaseProof.armed_at `
    -Label "watchdog lease armed_at"
  Assert-CutoverJsonString -Value $successorWatchdogLeaseProof.session_id `
    -Label "watchdog lease session_id"
  $successorWatchdogLeaseArmedAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorWatchdogLeaseProof.armed_at -Label "watchdog lease armed_at"
  foreach ($name in @(
    "watchdog", "owner", "deadline", "artifacts", "docker_engine", "postgres",
    "containment"
  )) {
    Assert-CutoverJsonObject -Value $successorWatchdogLeaseProof.$name `
      -Label "watchdog lease $name"
  }
  Assert-CutoverExactProperties -Value $successorWatchdogLeaseProof.watchdog `
    -Label "watchdog lease process" -Expected @("process_id", "process_started_at")
  Assert-CutoverJsonInteger -Value $successorWatchdogLeaseProof.watchdog.process_id `
    -Label "watchdog lease process_id"
  Assert-CutoverJsonString `
    -Value $successorWatchdogLeaseProof.watchdog.process_started_at `
    -Label "watchdog lease process_started_at"
  [void](ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorWatchdogLeaseProof.watchdog.process_started_at `
    -Label "watchdog lease process_started_at")
  Assert-CutoverExactProperties -Value $successorWatchdogLeaseProof.owner `
    -Label "watchdog lease owner" -Expected @("process_id", "process_started_at")
  Assert-CutoverJsonInteger -Value $successorWatchdogLeaseProof.owner.process_id `
    -Label "watchdog lease owner process_id"
  Assert-CutoverJsonString -Value $successorWatchdogLeaseProof.owner.process_started_at `
    -Label "watchdog lease owner process_started_at"
  Assert-CutoverExactProperties -Value $successorWatchdogLeaseProof.deadline `
    -Label "watchdog lease deadline" -Expected @(
      "hard_deadline_utc", "heartbeat_timeout_seconds", "maximum_lease_seconds"
    )
  Assert-CutoverJsonString -Value $successorWatchdogLeaseProof.deadline.hard_deadline_utc `
    -Label "watchdog lease hard deadline"
  Assert-CutoverJsonInteger `
    -Value $successorWatchdogLeaseProof.deadline.heartbeat_timeout_seconds `
    -Label "watchdog lease heartbeat timeout"
  Assert-CutoverJsonInteger `
    -Value $successorWatchdogLeaseProof.deadline.maximum_lease_seconds `
    -Label "watchdog lease maximum"
  Assert-CutoverExactProperties -Value $successorWatchdogLeaseProof.artifacts `
    -Label "watchdog lease artifacts" -Expected @(
      "lease_path", "receipt_path", "heartbeat_path", "commit_path", "live_path",
      "prestart_path", "prestart_sha256", "prestart_payload_sha256"
    )
  foreach ($name in @(
    "lease_path", "receipt_path", "heartbeat_path", "commit_path", "live_path",
    "prestart_path", "prestart_sha256", "prestart_payload_sha256"
  )) {
    Assert-CutoverJsonString -Value $successorWatchdogLeaseProof.artifacts.$name `
      -Label "watchdog lease artifact $name"
  }
  Assert-CutoverExactProperties -Value $successorWatchdogLeaseProof.postgres `
    -Label "watchdog lease PostgreSQL" -Expected @(
      "container_id", "image_id", "config_image", "restart_count",
      "restart_policy_before", "restart_policy_armed", "state", "started_at",
      "finished_at"
    )
  foreach ($name in @(
    "container_id", "image_id", "config_image", "restart_policy_before",
    "restart_policy_armed", "state", "started_at", "finished_at"
  )) {
    Assert-CutoverJsonString -Value $successorWatchdogLeaseProof.postgres.$name `
      -Label "watchdog lease PostgreSQL $name"
  }
  Assert-CutoverJsonInteger -Value $successorWatchdogLeaseProof.postgres.restart_count `
    -Label "watchdog lease PostgreSQL restart_count"
  Assert-CutoverExactProperties -Value $successorWatchdogLeaseProof.containment `
    -Label "watchdog lease containment" -Expected @(
      "stop_timeout_seconds", "stop_wall_clock_timeout_seconds",
      "exact_container_only", "ambiguous_start_requires_stop_attempt"
    )
  foreach ($name in @("stop_timeout_seconds", "stop_wall_clock_timeout_seconds")) {
    Assert-CutoverJsonInteger -Value $successorWatchdogLeaseProof.containment.$name `
      -Label "watchdog lease containment $name"
  }
  foreach ($name in @("exact_container_only", "ambiguous_start_requires_stop_attempt")) {
    Assert-CutoverJsonBoolean -Value $successorWatchdogLeaseProof.containment.$name `
      -Label "watchdog lease containment $name"
  }
  if (
    $successorWatchdogLeaseProof.format -cne
      "stratus-successor-transition-watchdog-lease-v1" -or
    $successorWatchdogLeaseProof.armed -ne $true -or
    $successorWatchdogLeaseProof.session_id -cne $successorSessionId -or
    $successorWatchdogLeaseArmedAt -lt $successorPreStartAcceptedAt -or
    $successorWatchdogLeaseArmedAt -ge $successorWatchdogHardDeadline -or
    $successorWatchdogLeaseProof.owner.process_id -ne $successorWatchdogProgram.owner_pid -or
    $successorWatchdogLeaseProof.owner.process_started_at -cne
      $successorWatchdogProgram.owner_start_time_utc -or
    $successorWatchdogLeaseProof.deadline.hard_deadline_utc -cne
      $successorWatchdogHardDeadline.ToString("o") -or
    $successorWatchdogLeaseProof.deadline.heartbeat_timeout_seconds -ne 15 -or
    $successorWatchdogLeaseProof.deadline.maximum_lease_seconds -ne 600 -or
    $successorWatchdogLeaseProof.artifacts.lease_path -cne $successorWatchdogLease -or
    $successorWatchdogLeaseProof.artifacts.receipt_path -cne $successorWatchdogTerminal -or
    $successorWatchdogLeaseProof.artifacts.heartbeat_path -cne
      $successorWatchdogHeartbeat -or
    $successorWatchdogLeaseProof.artifacts.commit_path -cne $successorWatchdogCommit -or
    $successorWatchdogLeaseProof.artifacts.live_path -cne $successorLiveAcceptance -or
    $successorWatchdogLeaseProof.artifacts.prestart_path -cne
      $successorPreStartAttestation -or
    $successorWatchdogLeaseProof.artifacts.prestart_sha256 -cne
      $successorPreStartAttestationSha256 -or
    $successorWatchdogLeaseProof.artifacts.prestart_payload_sha256 -cne
      $successorPreStartGate.payload_sha256 -or
    $successorWatchdogLeaseProof.postgres.container_id -cne $postgresContainer[0] -or
    $successorWatchdogLeaseProof.postgres.image_id -cne $postgresImageId -or
    $successorWatchdogLeaseProof.postgres.config_image -cne
      $successorPreStartGate.payload.postgres_prestart.config_image -or
    $successorWatchdogLeaseProof.postgres.restart_count -ne $postgresRestartCount -or
    $successorWatchdogLeaseProof.postgres.restart_policy_before -cne "unless-stopped" -or
    $successorWatchdogLeaseProof.postgres.restart_policy_armed -cne "no" -or
    $successorWatchdogLeaseProof.postgres.state -cne "exited" -or
    $successorWatchdogLeaseProof.postgres.started_at -cne
      $successorPreStartGate.payload.postgres_prestart.started_at -or
    $successorWatchdogLeaseProof.postgres.finished_at -cne
      $successorPreStartGate.payload.postgres_prestart.finished_at -or
    $successorWatchdogLeaseProof.containment.stop_timeout_seconds -ne 15 -or
    $successorWatchdogLeaseProof.containment.stop_wall_clock_timeout_seconds -ne 25 -or
    $successorWatchdogLeaseProof.containment.exact_container_only -ne $true -or
    $successorWatchdogLeaseProof.containment.ambiguous_start_requires_stop_attempt -ne $true
  ) {
    throw "Successor watchdog lease is not the exact armed stopped-PostgreSQL boundary."
  }

  # The owner Transition binds the Compose-aware engine contract. The standalone
  # watchdog independently binds the exact legacy Docker-engine projection; its
  # lease/terminal hash is therefore intentionally different from the owner's
  # Compose-aware hash. Recompute both and cross-bind every shared raw field.
  $successorCoreEnginePropertyNames = @(
    "docker_executable_path", "docker_executable_sha256", "docker_executable_bytes",
    "compose_executable_path", "compose_executable_sha256", "compose_executable_bytes",
    "compose_version", "context", "endpoint", "skip_tls_verify", "engine_id",
    "server_version", "engine_name", "docker_root_dir", "contract_sha256"
  )
  $successorWatchdogEnginePropertyNames = @(
    "docker_executable_path", "docker_executable_sha256", "docker_executable_bytes",
    "context", "endpoint", "skip_tls_verify", "engine_id", "server_version",
    "engine_name", "docker_root_dir", "contract_sha256"
  )
  foreach ($engineProof in @(
    [pscustomobject]@{ value = $successorPreStartSession.docker_engine; label = "PreStart Docker engine" },
    [pscustomobject]@{ value = $successorLiveSession.docker_engine; label = "Live Docker engine" }
  )) {
    Assert-CutoverExactProperties -Value $engineProof.value -Label $engineProof.label `
      -Expected $successorCoreEnginePropertyNames
    foreach ($name in @(
      "docker_executable_path", "docker_executable_sha256", "compose_executable_path",
      "compose_executable_sha256", "compose_version", "context", "endpoint",
      "engine_id", "server_version", "engine_name", "docker_root_dir", "contract_sha256"
    )) {
      Assert-CutoverJsonString -Value $engineProof.value.$name `
        -Label "$($engineProof.label) $name"
    }
    foreach ($name in @("docker_executable_bytes", "compose_executable_bytes")) {
      Assert-CutoverJsonInteger -Value $engineProof.value.$name `
        -Label "$($engineProof.label) $name"
    }
    Assert-CutoverJsonBoolean -Value $engineProof.value.skip_tls_verify `
      -Label "$($engineProof.label) skip_tls_verify"
  }
  foreach ($engineProof in @(
    [pscustomobject]@{ value = $successorWatchdogLeaseProof.docker_engine; label = "watchdog lease Docker engine" },
    [pscustomobject]@{ value = $successorWatchdogTerminalProof.docker_engine; label = "watchdog terminal Docker engine" }
  )) {
    Assert-CutoverExactProperties -Value $engineProof.value -Label $engineProof.label `
      -Expected $successorWatchdogEnginePropertyNames
    foreach ($name in @(
      "docker_executable_path", "docker_executable_sha256", "context", "endpoint",
      "engine_id", "server_version", "engine_name", "docker_root_dir", "contract_sha256"
    )) {
      Assert-CutoverJsonString -Value $engineProof.value.$name `
        -Label "$($engineProof.label) $name"
    }
    Assert-CutoverJsonInteger -Value $engineProof.value.docker_executable_bytes `
      -Label "$($engineProof.label) docker_executable_bytes"
    Assert-CutoverJsonBoolean -Value $engineProof.value.skip_tls_verify `
      -Label "$($engineProof.label) skip_tls_verify"
  }
  $successorCoreEngineProof = $successorPreStartSession.docker_engine
  $successorWatchdogEngineProof = $successorWatchdogLeaseProof.docker_engine
  $successorDockerItem = Get-Item `
    -LiteralPath $successorCoreEngineProof.docker_executable_path -Force
  $successorComposeItem = Get-Item `
    -LiteralPath $successorCoreEngineProof.compose_executable_path -Force
  $successorCoreEngineContract = @(
    [string]$successorCoreEngineProof.docker_executable_path,
    [string]$successorCoreEngineProof.docker_executable_sha256,
    [string]$successorCoreEngineProof.docker_executable_bytes,
    [string]$successorCoreEngineProof.compose_executable_path,
    [string]$successorCoreEngineProof.compose_executable_sha256,
    [string]$successorCoreEngineProof.compose_executable_bytes,
    [string]$successorCoreEngineProof.compose_version,
    [string]$successorCoreEngineProof.context,
    [string]$successorCoreEngineProof.endpoint,
    ([bool]$successorCoreEngineProof.skip_tls_verify).ToString().ToLowerInvariant(),
    [string]$successorCoreEngineProof.engine_id,
    [string]$successorCoreEngineProof.server_version,
    [string]$successorCoreEngineProof.engine_name,
    [string]$successorCoreEngineProof.docker_root_dir
  ) -join "`0"
  $successorLegacyEngineContract = @(
    [string]$successorCoreEngineProof.docker_executable_path,
    [string]$successorCoreEngineProof.docker_executable_sha256,
    [string]$successorCoreEngineProof.docker_executable_bytes,
    [string]$successorCoreEngineProof.context,
    [string]$successorCoreEngineProof.endpoint,
    ([bool]$successorCoreEngineProof.skip_tls_verify).ToString().ToLowerInvariant(),
    [string]$successorCoreEngineProof.engine_id,
    [string]$successorCoreEngineProof.server_version,
    [string]$successorCoreEngineProof.engine_name,
    [string]$successorCoreEngineProof.docker_root_dir
  ) -join "`0"
  $successorLegacyEngineSha256 = Get-CutoverSha256Text `
    -Value ($successorLegacyEngineContract + "`n")
  $successorCoreLegacyEngineProof = [ordered]@{
    docker_executable_path = [string]$successorCoreEngineProof.docker_executable_path
    docker_executable_sha256 = [string]$successorCoreEngineProof.docker_executable_sha256
    docker_executable_bytes = [int64]$successorCoreEngineProof.docker_executable_bytes
    context = [string]$successorCoreEngineProof.context
    endpoint = [string]$successorCoreEngineProof.endpoint
    skip_tls_verify = [bool]$successorCoreEngineProof.skip_tls_verify
    engine_id = [string]$successorCoreEngineProof.engine_id
    server_version = [string]$successorCoreEngineProof.server_version
    engine_name = [string]$successorCoreEngineProof.engine_name
    docker_root_dir = [string]$successorCoreEngineProof.docker_root_dir
    contract_sha256 = $successorLegacyEngineSha256
  }
  $successorWatchdogEngineJson = $successorWatchdogEngineProof |
    ConvertTo-Json -Depth 10 -Compress
  if (
    ($successorLiveSession.docker_engine | ConvertTo-Json -Depth 10 -Compress) -cne
      ($successorCoreEngineProof | ConvertTo-Json -Depth 10 -Compress) -or
    ($successorWatchdogTerminalProof.docker_engine |
      ConvertTo-Json -Depth 10 -Compress) -cne $successorWatchdogEngineJson -or
    ($successorCoreLegacyEngineProof | ConvertTo-Json -Depth 10 -Compress) -cne
      $successorWatchdogEngineJson -or
    ($networkRepairPayload.docker_engine | ConvertTo-Json -Depth 10 -Compress) -cne
      $successorWatchdogEngineJson -or
    $successorCoreEngineProof.docker_executable_sha256 -cne
      "388f231898cff8674a2aa341e306b77fd1f93a5954dd33ff556575a69227824d" -or
    $successorCoreEngineProof.docker_executable_sha256 -cne
      (Get-CutoverFileSha256 -LiteralPath $successorCoreEngineProof.docker_executable_path) -or
    $successorCoreEngineProof.docker_executable_bytes -ne
      [int64]$successorDockerItem.Length -or
    $successorCoreEngineProof.compose_executable_sha256 -cne
      "8482829f77d14da8dfdfd0b553847946cee5161c90312219cf4f47d1f132d2bf" -or
    $successorCoreEngineProof.compose_executable_sha256 -cne
      (Get-CutoverFileSha256 -LiteralPath $successorCoreEngineProof.compose_executable_path) -or
    $successorCoreEngineProof.compose_executable_bytes -ne
      [int64]$successorComposeItem.Length -or
    $successorCoreEngineProof.compose_version -cne "v2.38.2-desktop.1" -or
    $successorCoreEngineProof.context -cne "desktop-linux" -or
    $successorCoreEngineProof.endpoint -cne "npipe:////./pipe/dockerDesktopLinuxEngine" -or
    $successorCoreEngineProof.skip_tls_verify -ne $false -or
    $successorCoreEngineProof.engine_id -cne
      "d98e03f5-06d7-4af4-9f21-b6f7a70ebf68" -or
    $successorCoreEngineProof.server_version -cne "28.3.2" -or
    $successorCoreEngineProof.engine_name -cne "docker-desktop" -or
    $successorCoreEngineProof.docker_root_dir -cne "/var/lib/docker" -or
    $successorCoreEngineProof.contract_sha256 -cne
      (Get-CutoverSha256Text -Value ($successorCoreEngineContract + "`n")) -or
    $successorWatchdogEngineProof.contract_sha256 -cne $successorLegacyEngineSha256 -or
    $successorLiveRepairBinding.docker_engine_contract_sha256 -cne
      $successorLegacyEngineSha256
  ) {
    throw "Successor owner/watchdog Docker engine contracts are not exactly cross-bound."
  }

  Assert-CutoverExactProperties -Value $successorLiveSession.watchdog_lease `
    -Label "Live watchdog_lease" -Expected @(
      "path", "sha256", "armed_at", "docker_engine_contract_sha256"
    )
  foreach ($name in @("path", "sha256", "armed_at", "docker_engine_contract_sha256")) {
    Assert-CutoverJsonString -Value $successorLiveSession.watchdog_lease.$name `
      -Label "Live watchdog_lease $name"
  }
  Assert-CutoverExactProperties -Value $successorLiveSession.heartbeat_at_live `
    -Label "Live heartbeat_at_live" -Expected @("sequence", "written_at", "sha256")
  Assert-CutoverJsonInteger -Value $successorLiveSession.heartbeat_at_live.sequence `
    -Label "Live heartbeat sequence"
  Assert-CutoverJsonString -Value $successorLiveSession.heartbeat_at_live.written_at `
    -Label "Live heartbeat written_at"
  Assert-CutoverJsonString -Value $successorLiveSession.heartbeat_at_live.sha256 `
    -Label "Live heartbeat sha256"
  $successorHeartbeatAtLiveTime = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorLiveSession.heartbeat_at_live.written_at `
    -Label "Live heartbeat written_at"
  if (
    $successorLiveSession.watchdog_lease.path -cne $successorWatchdogLease -or
    $successorLiveSession.watchdog_lease.sha256 -cne $successorWatchdogLeaseSha256 -or
    $successorLiveSession.watchdog_lease.armed_at -cne
      $successorWatchdogLeaseProof.armed_at -or
    $successorLiveSession.watchdog_lease.docker_engine_contract_sha256 -cne
      $successorWatchdogLeaseProof.docker_engine.contract_sha256 -or
    $successorLiveSession.heartbeat_at_live.sequence -lt 1 -or
    $successorLiveSession.heartbeat_at_live.sha256 -notmatch '^[0-9a-f]{64}$' -or
    $successorHeartbeatAtLiveTime -lt $successorWatchdogLeaseArmedAt -or
    $successorHeartbeatAtLiveTime -gt $successorWatchdogHardDeadline
  ) {
    throw "Live does not bind the accepted watchdog lease and heartbeat."
  }

  Assert-CutoverExactProperties -Value $successorWatchdogCommitProof `
    -Label "successor watchdog Live commit" -Expected @(
      "format", "session_id", "owner_pid", "owner_start_time_utc", "lease_path",
      "lease_sha256", "live_path", "live_sha256", "committed_at"
    )
  foreach ($name in @(
    "format", "session_id", "owner_start_time_utc", "lease_path", "lease_sha256",
    "live_path", "live_sha256", "committed_at"
  )) {
    Assert-CutoverJsonString -Value $successorWatchdogCommitProof.$name `
      -Label "watchdog commit $name"
  }
  Assert-CutoverJsonInteger -Value $successorWatchdogCommitProof.owner_pid `
    -Label "watchdog commit owner_pid"
  $successorWatchdogCommittedAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorWatchdogCommitProof.committed_at -Label "watchdog commit committed_at"
  if (
    $successorWatchdogCommitProof.format -cne
      "stratus-successor-transition-watchdog-commit-v1" -or
    $successorWatchdogCommitProof.session_id -cne $successorSessionId -or
    $successorWatchdogCommitProof.owner_pid -ne $successorWatchdogProgram.owner_pid -or
    $successorWatchdogCommitProof.owner_start_time_utc -cne
      $successorWatchdogProgram.owner_start_time_utc -or
    $successorWatchdogCommitProof.lease_path -cne $successorWatchdogLease -or
    $successorWatchdogCommitProof.lease_sha256 -cne $successorWatchdogLeaseSha256 -or
    $successorWatchdogCommitProof.live_path -cne $successorLiveAcceptance -or
    $successorWatchdogCommitProof.live_sha256 -cne $successorLiveAcceptanceSha256 -or
    $successorWatchdogCommittedAt -lt $successorWatchdogLeaseArmedAt -or
    $successorWatchdogCommittedAt -gt $successorWatchdogHardDeadline
  ) {
    throw "Successor watchdog commit is not the exact lease-bound Live commit."
  }

  Assert-CutoverExactProperties -Value $successorWatchdogHeartbeatProof `
    -Label "successor watchdog final heartbeat" -Expected @(
      "format", "session_id", "owner_pid", "owner_start_time_utc", "lease_path",
      "lease_sha256", "sequence", "written_at"
    )
  foreach ($name in @(
    "format", "session_id", "owner_start_time_utc", "lease_path", "lease_sha256",
    "written_at"
  )) {
    Assert-CutoverJsonString -Value $successorWatchdogHeartbeatProof.$name `
      -Label "final watchdog heartbeat $name"
  }
  foreach ($name in @("owner_pid", "sequence")) {
    Assert-CutoverJsonInteger -Value $successorWatchdogHeartbeatProof.$name `
      -Label "final watchdog heartbeat $name"
  }
  $successorWatchdogFinalHeartbeatAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorWatchdogHeartbeatProof.written_at `
    -Label "final watchdog heartbeat written_at"
  if (
    $successorWatchdogHeartbeatProof.format -cne
      "stratus-successor-transition-watchdog-heartbeat-v1" -or
    $successorWatchdogHeartbeatProof.session_id -cne $successorSessionId -or
    $successorWatchdogHeartbeatProof.owner_pid -ne $successorWatchdogProgram.owner_pid -or
    $successorWatchdogHeartbeatProof.owner_start_time_utc -cne
      $successorWatchdogProgram.owner_start_time_utc -or
    $successorWatchdogHeartbeatProof.lease_path -cne $successorWatchdogLease -or
    $successorWatchdogHeartbeatProof.lease_sha256 -cne $successorWatchdogLeaseSha256 -or
    $successorWatchdogHeartbeatProof.sequence -lt
      $successorLiveSession.heartbeat_at_live.sequence -or
    $successorWatchdogFinalHeartbeatAt -lt $successorHeartbeatAtLiveTime -or
    $successorWatchdogFinalHeartbeatAt -gt $successorWatchdogHardDeadline -or
    ($successorWatchdogHeartbeatProof.sequence -eq
      $successorLiveSession.heartbeat_at_live.sequence -and
      $successorWatchdogHeartbeatSha256 -cne
        $successorLiveSession.heartbeat_at_live.sha256)
  ) {
    throw "Final watchdog heartbeat is not the monotonic lease-bound owner heartbeat."
  }

  Assert-CutoverExactProperties -Value $successorWatchdogTerminalProof `
    -Label "successor watchdog terminal receipt" -Expected @(
      "format", "status", "terminal_at", "session_id", "owner_pid",
      "owner_start_time_utc", "lease_path", "lease_sha256", "receipt_path",
      "commit_path", "live_path", "trigger", "docker_engine", "containment",
      "commit", "authorization", "failure"
    )
  foreach ($name in @(
    "format", "status", "terminal_at", "session_id", "owner_start_time_utc",
    "lease_path", "lease_sha256", "receipt_path", "commit_path", "live_path",
    "trigger"
  )) {
    Assert-CutoverJsonString -Value $successorWatchdogTerminalProof.$name `
      -Label "watchdog terminal $name"
  }
  Assert-CutoverJsonInteger -Value $successorWatchdogTerminalProof.owner_pid `
    -Label "watchdog terminal owner_pid"
  Assert-CutoverJsonObject -Value $successorWatchdogTerminalProof.commit `
    -Label "watchdog terminal commit"
  Assert-CutoverJsonObject -Value $successorWatchdogTerminalProof.authorization `
    -Label "watchdog terminal authorization"
  Assert-CutoverExactProperties -Value $successorWatchdogTerminalProof.authorization `
    -Label "watchdog terminal authorization" -Expected @(
      "api_stage_permitted", "worker_start_permitted",
      "safety_control_start_permitted", "live_transition_permitted",
      "binance_kill_switch_removal_permitted", "migration_container_permitted"
    )
  foreach ($name in @(
    "api_stage_permitted", "worker_start_permitted",
    "safety_control_start_permitted", "live_transition_permitted",
    "binance_kill_switch_removal_permitted", "migration_container_permitted"
  )) {
    Assert-CutoverJsonBoolean -Value $successorWatchdogTerminalProof.authorization.$name `
      -Label "watchdog terminal authorization $name"
  }
  Assert-CutoverExactProperties -Value $successorWatchdogTerminalProof.commit `
    -Label "watchdog terminal commit" -Expected @(
      "marker_sha256", "committed_at", "live_sha256", "live_payload_sha256",
      "postgres_started_at", "docker_engine_contract_sha256"
    )
  foreach ($name in @(
    "marker_sha256", "committed_at", "live_sha256", "live_payload_sha256",
    "postgres_started_at", "docker_engine_contract_sha256"
  )) {
    Assert-CutoverJsonString -Value $successorWatchdogTerminalProof.commit.$name `
      -Label "watchdog terminal commit $name"
  }
  $successorWatchdogTerminalAt = ConvertTo-CutoverCanonicalUtcTime `
    -Value $successorWatchdogTerminalProof.terminal_at `
    -Label "watchdog terminal terminal_at"
  if (
    $successorWatchdogTerminalSha256 -notmatch '^[0-9a-f]{64}$' -or
    $successorWatchdogTerminalProof.format -cne
      "stratus-successor-transition-watchdog-terminal-v1" -or
    $successorWatchdogTerminalProof.status -cne "success" -or
    $successorWatchdogTerminalProof.trigger -cne "accepted_live_commit" -or
    $null -ne $successorWatchdogTerminalProof.containment -or
    $null -ne $successorWatchdogTerminalProof.failure -or
    $successorWatchdogTerminalProof.authorization.api_stage_permitted -ne $true -or
    $successorWatchdogTerminalProof.authorization.worker_start_permitted -ne $false -or
    $successorWatchdogTerminalProof.authorization.safety_control_start_permitted -ne $false -or
    $successorWatchdogTerminalProof.authorization.live_transition_permitted -ne $false -or
    $successorWatchdogTerminalProof.authorization.binance_kill_switch_removal_permitted -ne
      $false -or
    $successorWatchdogTerminalProof.authorization.migration_container_permitted -ne $false -or
    $successorWatchdogTerminalProof.session_id -cne $successorSessionId -or
    $successorWatchdogTerminalProof.owner_pid -ne $successorWatchdogProgram.owner_pid -or
    $successorWatchdogTerminalProof.owner_start_time_utc -cne
      $successorWatchdogProgram.owner_start_time_utc -or
    $successorWatchdogTerminalProof.lease_path -cne $successorWatchdogLease -or
    $successorWatchdogTerminalProof.lease_sha256 -cne $successorWatchdogLeaseSha256 -or
    $successorWatchdogTerminalProof.receipt_path -cne $successorWatchdogTerminal -or
    $successorWatchdogTerminalProof.commit_path -cne $successorWatchdogCommit -or
    $successorWatchdogTerminalProof.live_path -cne $successorLiveAcceptance -or
    $successorWatchdogTerminalAt -lt $successorWatchdogCommittedAt -or
    $successorWatchdogTerminalAt -gt $successorWatchdogHardDeadline.AddSeconds(2) -or
    $successorWatchdogTerminalProof.commit.marker_sha256 -cne
      $successorWatchdogCommitSha256 -or
    $successorWatchdogTerminalProof.commit.committed_at -cne
      $successorWatchdogCommitProof.committed_at -or
    $successorWatchdogTerminalProof.commit.live_sha256 -cne
      $successorLiveAcceptanceSha256 -or
    $successorWatchdogTerminalProof.commit.live_payload_sha256 -cne
      $successorTransitionGate.payload_sha256 -or
    $successorTransitionGate.payload.postgres_transition.previous_started_at -cne
      $successorWatchdogLeaseProof.postgres.started_at -or
    $successorTransitionGate.payload.postgres_transition.previous_finished_at -cne
      $successorWatchdogLeaseProof.postgres.finished_at -or
    $successorWatchdogTerminalProof.commit.postgres_started_at -cne
      $successorTransitionGate.payload.postgres_transition.successor_started_at -or
    $successorWatchdogTerminalProof.commit.docker_engine_contract_sha256 -cne
      $successorWatchdogLeaseProof.docker_engine.contract_sha256
  ) {
    throw "Authoritative watchdog terminal receipt did not accept the exact Live commit."
  }

  $successorEventWitness = $successorLiveSession.event_witness
  Assert-CutoverExactProperties -Value $successorEventWitness `
    -Label "successor Docker event witness" -Expected @(
      "session_id", "process_id", "process_started_at", "docker_executable_path",
      "docker_executable_sha256", "docker_executable_bytes", "collector_type_name",
      "collector_contract_identity", "collector_template_sha256",
      "collector_source_sha256", "collector_assembly_full_name_sha256",
      "collector_assembly_is_dynamic", "collector_module_version_id", "journal_bytes",
      "journal_sha256", "event_count", "postgres_start_count",
      "postgres_start_time_nano", "postgres_restart_policy_update_count",
      "postgres_restart_policy_update_time_nano", "postgres_attach_count",
      "network_connect_count", "network_ids", "data_volume_mount_count",
      "data_volume_mount_time_nano", "gate_exec_triplets", "gate_exec_classes",
      "gate_exec_sequence", "health_exec_triplets", "healthy_status_event_count",
      "healthy_status_time_nano", "reconnect_count", "eof_before_seal", "truncated",
      "initial_barrier", "final_barrier"
    )
  foreach ($name in @(
    "session_id", "process_started_at", "docker_executable_path",
    "docker_executable_sha256", "collector_type_name", "collector_contract_identity",
    "collector_template_sha256", "collector_source_sha256",
    "collector_assembly_full_name_sha256", "collector_module_version_id", "journal_sha256"
  )) {
    Assert-CutoverJsonString -Value $successorEventWitness.$name `
      -Label "event witness $name"
  }
  foreach ($name in @(
    "process_id", "docker_executable_bytes", "journal_bytes", "event_count",
    "postgres_start_count", "postgres_start_time_nano",
    "postgres_restart_policy_update_count", "postgres_restart_policy_update_time_nano",
    "postgres_attach_count", "network_connect_count", "data_volume_mount_count",
    "data_volume_mount_time_nano", "gate_exec_triplets", "health_exec_triplets",
    "healthy_status_event_count", "healthy_status_time_nano", "reconnect_count"
  )) {
    Assert-CutoverJsonInteger -Value $successorEventWitness.$name `
      -Label "event witness $name"
  }
  foreach ($name in @("collector_assembly_is_dynamic", "eof_before_seal", "truncated")) {
    Assert-CutoverJsonBoolean -Value $successorEventWitness.$name `
      -Label "event witness $name"
  }
  Assert-CutoverJsonArray -Value $successorEventWitness.network_ids `
    -Label "event witness network_ids"
  Assert-CutoverJsonArray -Value $successorEventWitness.gate_exec_sequence `
    -Label "event witness gate_exec_sequence"
  Assert-CutoverExactProperties -Value $successorEventWitness.gate_exec_classes `
    -Label "event witness gate classes" `
    -Expected @("sha256", "reload", "fresh_hba", "snapshot")
  foreach ($name in @("sha256", "reload", "fresh_hba", "snapshot")) {
    Assert-CutoverJsonInteger -Value $successorEventWitness.gate_exec_classes.$name `
      -Label "event witness gate class $name"
  }
  $successorExpectedGateSequence = @(
    "sha256", "reload", "fresh_hba", "sha256",
    "snapshot", "snapshot", "fresh_hba", "sha256"
  )
  if (@($successorEventWitness.gate_exec_sequence).Count -ne
    $successorExpectedGateSequence.Count
  ) {
    throw "Successor gate event sequence does not contain exactly eight entries."
  }
  $successorGateSequenceTimes = @()
  foreach ($gateProof in @($successorEventWitness.gate_exec_sequence)) {
    Assert-CutoverExactProperties -Value $gateProof -Label "event witness gate entry" `
      -Expected @("class", "create_time_nano")
    Assert-CutoverJsonString -Value $gateProof.class -Label "event witness gate class"
    Assert-CutoverJsonInteger -Value $gateProof.create_time_nano `
      -Label "event witness gate create_time_nano"
    $successorGateSequenceTimes += [int64]$gateProof.create_time_nano
  }
  foreach ($gateIndex in 1..($successorGateSequenceTimes.Count - 1)) {
    if ($successorGateSequenceTimes[$gateIndex] -le
      $successorGateSequenceTimes[$gateIndex - 1]
    ) {
      throw "Successor gate event sequence is not strictly ordered."
    }
  }
  if (
    $successorEventWitness.session_id -cne $successorSessionId -or
    $successorEventWitness.docker_executable_sha256 -cne
      $successorWatchdogLeaseProof.docker_engine.docker_executable_sha256 -or
    $successorEventWitness.postgres_start_count -ne 1 -or
    $successorEventWitness.postgres_restart_policy_update_count -ne 1 -or
    $successorEventWitness.healthy_status_event_count -ne 1 -or
    $successorEventWitness.postgres_restart_policy_update_time_nano -ge
      $successorEventWitness.postgres_start_time_nano -or
    $successorEventWitness.healthy_status_time_nano -le
      $successorEventWitness.postgres_start_time_nano -or
    $successorEventWitness.gate_exec_triplets -ne 8 -or
    $successorEventWitness.gate_exec_classes.sha256 -ne 3 -or
    $successorEventWitness.gate_exec_classes.reload -ne 1 -or
    $successorEventWitness.gate_exec_classes.fresh_hba -ne 2 -or
    $successorEventWitness.gate_exec_classes.snapshot -ne 2 -or
    (@($successorEventWitness.gate_exec_sequence.class) -join "`n") -cne
      ($successorExpectedGateSequence -join "`n") -or
    $successorEventWitness.eof_before_seal -ne $false -or
    $successorEventWitness.truncated -ne $false
  ) {
    throw "Successor Docker update/start/healthy or exact gate-class sequence is invalid."
  }

  Assert-CutoverExactProperties -Value $successorLiveSession.rollback `
    -Label "successor Live rollback" -Expected @(
      "on_failure", "stop_timeout_seconds", "executed", "emergency_containment"
    )
  foreach ($name in @("on_failure", "executed", "emergency_containment")) {
    Assert-CutoverJsonBoolean -Value $successorLiveSession.rollback.$name `
      -Label "successor Live rollback $name"
  }
  Assert-CutoverJsonInteger -Value $successorLiveSession.rollback.stop_timeout_seconds `
    -Label "successor Live rollback stop timeout"
  if (
    $successorLiveSession.rollback.on_failure -ne $true -or
    $successorLiveSession.rollback.stop_timeout_seconds -ne 15 -or
    $successorLiveSession.rollback.executed -ne $false -or
    $successorLiveSession.rollback.emergency_containment -ne $false
  ) {
    throw "Successor Live rollback/emergency containment is not fail closed."
  }

  $successorPreStartAuthorization = $successorPreStartGate.payload.authorization
  Assert-CutoverExactProperties -Value $successorPreStartAuthorization `
    -Label "successor PreStart authorization" -Expected @(
      "postgres_start_permitted", "external_postgres_start_permitted",
      "transition_process_start_permitted", "api_stage_permitted",
      "worker_start_permitted", "safety_control_start_permitted",
      "live_transition_permitted", "binance_kill_switch_removal_permitted"
    )
  foreach ($name in @(
    "postgres_start_permitted", "external_postgres_start_permitted",
    "transition_process_start_permitted", "api_stage_permitted",
    "worker_start_permitted", "safety_control_start_permitted",
    "live_transition_permitted", "binance_kill_switch_removal_permitted"
  )) {
    Assert-CutoverJsonBoolean -Value $successorPreStartAuthorization.$name `
      -Label "successor PreStart authorization $name"
  }
  Assert-CutoverExactProperties -Value $successorTransitionGate.payload.authorization `
    -Label "successor Live authorization" -Expected @(
      "api_stage_permitted", "worker_start_permitted",
      "safety_control_start_permitted", "live_transition_permitted",
      "binance_kill_switch_removal_permitted", "migration_container_permitted"
    )
  foreach ($name in @(
    "api_stage_permitted", "worker_start_permitted",
    "safety_control_start_permitted", "live_transition_permitted",
    "binance_kill_switch_removal_permitted", "migration_container_permitted"
  )) {
    Assert-CutoverJsonBoolean -Value $successorTransitionGate.payload.authorization.$name `
      -Label "successor Live authorization $name"
  }
  if (
    $successorPreStartAuthorization.postgres_start_permitted -ne $false -or
    $successorPreStartAuthorization.external_postgres_start_permitted -ne $false -or
    $successorPreStartAuthorization.transition_process_start_permitted -ne $true -or
    $successorPreStartAuthorization.api_stage_permitted -ne $false -or
    $successorPreStartAuthorization.worker_start_permitted -ne $false -or
    $successorPreStartAuthorization.safety_control_start_permitted -ne $false -or
    $successorPreStartAuthorization.live_transition_permitted -ne $false -or
    $successorPreStartAuthorization.binance_kill_switch_removal_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.api_stage_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.worker_start_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.safety_control_start_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.live_transition_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.binance_kill_switch_removal_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.migration_container_permitted -ne $false
  ) {
    throw "Successor authorization escaped the PAPER-only fail-closed boundary."
  }
  # This assignment deliberately occurs only after the authoritative terminal
  # success/accepted_live_commit receipt and all of its raw hash bindings pass.
  $successorApiStagePermitted = [bool](
    $successorWatchdogTerminalProof.authorization.api_stage_permitted
  )
  function Assert-SuccessorTerminalApiAuthorizationUnchanged {
    foreach ($overrideName in @(
      "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
      "DOCKER_CONFIG", "DOCKER_API_VERSION", "DOCKER_CLI_PLUGIN_EXTRA_DIRS"
    )) {
      if (Test-Path -LiteralPath "Env:$overrideName") {
        throw "Terminal API authorization forbids Docker override drift: $overrideName"
      }
    }
    $currentDockerExecutable = [string]$successorCoreEngineProof.docker_executable_path
    $currentDockerItem = Get-Item -LiteralPath $currentDockerExecutable -Force
    if (
      $currentDockerItem.PSIsContainer -or
      ($currentDockerItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
      [int64]$currentDockerItem.Length -ne
        [int64]$successorCoreEngineProof.docker_executable_bytes -or
      (Get-CutoverFileSha256 -LiteralPath $currentDockerExecutable) -cne
        $successorCoreEngineProof.docker_executable_sha256
    ) {
      throw "Terminal API authorization lost its source-pinned Docker executable."
    }
    $currentDockerContextLines = @(& $currentDockerExecutable context show)
    $currentDockerContextExit = $LASTEXITCODE
    if (
      $currentDockerContextExit -ne 0 -or
      $currentDockerContextLines.Count -ne 1 -or
      [string]::IsNullOrWhiteSpace([string]$currentDockerContextLines[0])
    ) {
      throw "Terminal API authorization could not re-read the Docker context."
    }
    $currentDockerContext = [string]$currentDockerContextLines[0]
    $currentDockerEndpointLines = @(
      & $currentDockerExecutable context inspect $currentDockerContext `
        --format "{{json .Endpoints.docker}}"
    )
    $currentDockerEndpointExit = $LASTEXITCODE
    $currentDockerInfoLines = @(
      & $currentDockerExecutable info --format "{{json .}}"
    )
    $currentDockerInfoExit = $LASTEXITCODE
    if (
      $currentDockerEndpointExit -ne 0 -or $currentDockerEndpointLines.Count -ne 1 -or
      $currentDockerInfoExit -ne 0 -or $currentDockerInfoLines.Count -ne 1
    ) {
      throw "Terminal API authorization could not re-read the Docker engine."
    }
    $currentDockerEndpoint = ConvertFrom-CutoverJsonText `
      -Json ([string]$currentDockerEndpointLines[0]) `
      -Label "current terminal-authorized Docker endpoint"
    $currentDockerInfo = ConvertFrom-CutoverJsonText `
      -Json ([string]$currentDockerInfoLines[0]) `
      -Label "current terminal-authorized Docker engine"
    Assert-CutoverJsonString -Value $currentDockerEndpoint.Host `
      -Label "current Docker endpoint Host"
    Assert-CutoverJsonBoolean -Value $currentDockerEndpoint.SkipTLSVerify `
      -Label "current Docker endpoint SkipTLSVerify"
    foreach ($name in @("ID", "ServerVersion", "Name", "DockerRootDir")) {
      Assert-CutoverJsonString -Value $currentDockerInfo.$name `
        -Label "current Docker engine $name"
    }
    Assert-CutoverJsonObject -Value $currentDockerInfo.ClientInfo `
      -Label "current Docker ClientInfo"
    Assert-CutoverJsonArray -Value $currentDockerInfo.ClientInfo.Plugins `
      -Label "current Docker ClientInfo.Plugins"
    $currentComposePlugins = @(
      $currentDockerInfo.ClientInfo.Plugins |
        Where-Object { [string]$_.Name -ceq "compose" }
    )
    if ($currentComposePlugins.Count -ne 1) {
      throw "Terminal API authorization requires one exact Compose plugin."
    }
    $currentComposePlugin = $currentComposePlugins[0]
    Assert-CutoverJsonObject -Value $currentComposePlugin `
      -Label "current Docker Compose plugin"
    foreach ($name in @("Name", "Version", "Path")) {
      Assert-CutoverJsonString -Value $currentComposePlugin.$name `
        -Label "current Docker Compose plugin $name"
    }
    $currentComposePath = [IO.Path]::GetFullPath([string]$currentComposePlugin.Path)
    $currentComposeItem = Get-Item -LiteralPath $currentComposePath -Force
    if (
      $currentComposeItem.PSIsContainer -or
      ($currentComposeItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) {
      throw "Terminal API authorization requires a regular Compose plugin file."
    }
    $currentEngineContract = @(
      [string]$currentDockerItem.FullName,
      (Get-CutoverFileSha256 -LiteralPath $currentDockerItem.FullName),
      [string][int64]$currentDockerItem.Length,
      [string]$currentComposeItem.FullName,
      (Get-CutoverFileSha256 -LiteralPath $currentComposeItem.FullName),
      [string][int64]$currentComposeItem.Length,
      [string]$currentComposePlugin.Version,
      $currentDockerContext,
      [string]$currentDockerEndpoint.Host,
      ([bool]$currentDockerEndpoint.SkipTLSVerify).ToString().ToLowerInvariant(),
      [string]$currentDockerInfo.ID,
      [string]$currentDockerInfo.ServerVersion,
      [string]$currentDockerInfo.Name,
      [string]$currentDockerInfo.DockerRootDir
    ) -join "`0"
    $currentCoreEngineProof = [ordered]@{
      docker_executable_path = [string]$currentDockerItem.FullName
      docker_executable_sha256 = Get-CutoverFileSha256 `
        -LiteralPath $currentDockerItem.FullName
      docker_executable_bytes = [int64]$currentDockerItem.Length
      compose_executable_path = [string]$currentComposeItem.FullName
      compose_executable_sha256 = Get-CutoverFileSha256 `
        -LiteralPath $currentComposeItem.FullName
      compose_executable_bytes = [int64]$currentComposeItem.Length
      compose_version = [string]$currentComposePlugin.Version
      context = $currentDockerContext
      endpoint = [string]$currentDockerEndpoint.Host
      skip_tls_verify = [bool]$currentDockerEndpoint.SkipTLSVerify
      engine_id = [string]$currentDockerInfo.ID
      server_version = [string]$currentDockerInfo.ServerVersion
      engine_name = [string]$currentDockerInfo.Name
      docker_root_dir = [string]$currentDockerInfo.DockerRootDir
      contract_sha256 = Get-CutoverSha256Text -Value ($currentEngineContract + "`n")
    }
    $currentLegacyEngineContract = @(
      [string]$currentCoreEngineProof.docker_executable_path,
      [string]$currentCoreEngineProof.docker_executable_sha256,
      [string]$currentCoreEngineProof.docker_executable_bytes,
      [string]$currentCoreEngineProof.context,
      [string]$currentCoreEngineProof.endpoint,
      ([bool]$currentCoreEngineProof.skip_tls_verify).ToString().ToLowerInvariant(),
      [string]$currentCoreEngineProof.engine_id,
      [string]$currentCoreEngineProof.server_version,
      [string]$currentCoreEngineProof.engine_name,
      [string]$currentCoreEngineProof.docker_root_dir
    ) -join "`0"
    $currentLegacyEngineProof = [ordered]@{
      docker_executable_path = [string]$currentCoreEngineProof.docker_executable_path
      docker_executable_sha256 = [string]$currentCoreEngineProof.docker_executable_sha256
      docker_executable_bytes = [int64]$currentCoreEngineProof.docker_executable_bytes
      context = [string]$currentCoreEngineProof.context
      endpoint = [string]$currentCoreEngineProof.endpoint
      skip_tls_verify = [bool]$currentCoreEngineProof.skip_tls_verify
      engine_id = [string]$currentCoreEngineProof.engine_id
      server_version = [string]$currentCoreEngineProof.server_version
      engine_name = [string]$currentCoreEngineProof.engine_name
      docker_root_dir = [string]$currentCoreEngineProof.docker_root_dir
      contract_sha256 = Get-CutoverSha256Text `
        -Value ($currentLegacyEngineContract + "`n")
    }
    if (
      ($currentCoreEngineProof | ConvertTo-Json -Depth 10 -Compress) -cne
        ($successorCoreEngineProof | ConvertTo-Json -Depth 10 -Compress) -or
      ($currentLegacyEngineProof | ConvertTo-Json -Depth 10 -Compress) -cne
        ($successorWatchdogEngineProof | ConvertTo-Json -Depth 10 -Compress)
    ) {
      throw "Terminal API authorization requires the current exact Docker engine contract."
    }
    $currentRunningContainerIds = @(
      & $currentDockerExecutable ps --no-trunc --format "{{.ID}}"
    )
    if (
      $LASTEXITCODE -ne 0 -or
      $currentRunningContainerIds.Count -ne 1 -or
      [string]$currentRunningContainerIds[0] -cne $postgresContainer[0]
    ) {
      throw "Terminal API authorization requires an exact PostgreSQL-only topology."
    }
    $currentAuthorizedPostgresJson = @(
      & $currentDockerExecutable inspect $postgresContainer[0] --format "{{json .}}"
    )
    if ($LASTEXITCODE -ne 0 -or $currentAuthorizedPostgresJson.Count -ne 1) {
      throw "Terminal API authorization could not re-inspect retained PostgreSQL exactly."
    }
    $currentAuthorizedPostgres = @(
      ConvertFrom-CutoverJsonText -Json ([string]$currentAuthorizedPostgresJson[0]) `
        -Label "current terminal-authorized PostgreSQL inspect"
    )
    $currentAuthorizedPostgresNetworks = @(
      $currentAuthorizedPostgres[0].NetworkSettings.Networks.PSObject.Properties.Name |
        Sort-Object
    )
    $currentDefaultPostgresNetwork =
      $currentAuthorizedPostgres[0].NetworkSettings.Networks.weatherpoly_default
    $currentSafetyPostgresNetwork =
      $currentAuthorizedPostgres[0].NetworkSettings.Networks.'weatherpoly_safety-plane'
    foreach ($networkProof in @(
      [pscustomobject]@{
        value = $currentDefaultPostgresNetwork
        label = "current PostgreSQL default attachment"
        id = "77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2"
        gateway = "172.20.0.1"
        address_pattern = '^172\.20\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})$'
      },
      [pscustomobject]@{
        value = $currentSafetyPostgresNetwork
        label = "current PostgreSQL safety attachment"
        id = "0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620"
        gateway = "172.22.0.1"
        address_pattern = '^172\.22\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})$'
      }
    )) {
      Assert-CutoverExactProperties -Value $networkProof.value `
        -Label $networkProof.label -Expected @(
          "IPAMConfig", "Links", "Aliases", "MacAddress", "DriverOpts",
          "GwPriority", "NetworkID", "EndpointID", "Gateway", "IPAddress",
          "IPPrefixLen", "IPv6Gateway", "GlobalIPv6Address",
          "GlobalIPv6PrefixLen", "DNSNames"
        )
      Assert-CutoverJsonArray -Value $networkProof.value.Aliases `
        -Label "$($networkProof.label) aliases"
      Assert-CutoverJsonArray -Value $networkProof.value.DNSNames `
        -Label "$($networkProof.label) DNS names"
      $currentAliases = @([string[]]$networkProof.value.Aliases | Sort-Object)
      $currentDnsNames = @([string[]]$networkProof.value.DNSNames | Sort-Object)
      if (
        $networkProof.value.NetworkID -cne $networkProof.id -or
        $null -ne $networkProof.value.IPAMConfig -or
        $null -ne $networkProof.value.Links -or
        $null -ne $networkProof.value.DriverOpts -or
        [int]$networkProof.value.GwPriority -ne 0 -or
        ($currentAliases -join "`n") -cne
          ((@("postgres", "weatherpoly-postgres-1") | Sort-Object) -join "`n") -or
        ($currentDnsNames -join "`n") -cne
          ((@($postgresContainer[0].Substring(0, 12), "postgres", "weatherpoly-postgres-1") |
            Sort-Object) -join "`n") -or
        [string]$networkProof.value.EndpointID -notmatch '^[0-9a-f]{64}$' -or
        $networkProof.value.Gateway -cne $networkProof.gateway -or
        [string]$networkProof.value.IPAddress -notmatch $networkProof.address_pattern -or
        [int]$networkProof.value.IPPrefixLen -ne 16 -or
        [string]$networkProof.value.MacAddress -notmatch
          '^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$' -or
        -not [string]::IsNullOrEmpty([string]$networkProof.value.IPv6Gateway) -or
        -not [string]::IsNullOrEmpty([string]$networkProof.value.GlobalIPv6Address) -or
        [int]$networkProof.value.GlobalIPv6PrefixLen -ne 0
      ) {
        throw "$($networkProof.label) drifted after terminal authorization."
      }
    }
    $currentConfiguredPostgresPort = @(
      $currentAuthorizedPostgres[0].HostConfig.PortBindings.'5432/tcp'
    )
    $currentRuntimePostgresPort = @(
      $currentAuthorizedPostgres[0].NetworkSettings.Ports.'5432/tcp'
    )
    if (
      [string]$currentAuthorizedPostgres[0].Id -cne $postgresContainer[0] -or
      [string]$currentAuthorizedPostgres[0].Image -cne $postgresImageId -or
      [string]$currentAuthorizedPostgres[0].Config.Image -cne
        $successorTransitionGate.payload.postgres_transition.config_image -or
      [int]$currentAuthorizedPostgres[0].RestartCount -ne $postgresRestartCount -or
      [bool]$currentAuthorizedPostgres[0].State.Running -ne $true -or
      [bool]$currentAuthorizedPostgres[0].State.OOMKilled -ne $false -or
      [string]$currentAuthorizedPostgres[0].State.Status -cne "running" -or
      [bool]$currentAuthorizedPostgres[0].State.Paused -or
      [bool]$currentAuthorizedPostgres[0].State.Restarting -or
      [bool]$currentAuthorizedPostgres[0].State.Dead -or
      -not [string]::IsNullOrWhiteSpace(
        [string]$currentAuthorizedPostgres[0].State.Error
      ) -or
      [int]$currentAuthorizedPostgres[0].State.Pid -le 0 -or
      [int]$currentAuthorizedPostgres[0].State.ExitCode -ne 0 -or
      [string]$currentAuthorizedPostgres[0].State.FinishedAt -cne
        "0001-01-01T00:00:00Z" -or
      [string]$currentAuthorizedPostgres[0].State.StartedAt -cne
        $successorWatchdogTerminalProof.commit.postgres_started_at -or
      [string]$currentAuthorizedPostgres[0].State.Health.Status -cne "healthy" -or
      [string]$currentAuthorizedPostgres[0].HostConfig.RestartPolicy.Name -cne "no" -or
      [int]$currentAuthorizedPostgres[0].HostConfig.RestartPolicy.MaximumRetryCount -ne 0 -or
      [string]$currentAuthorizedPostgres[0].HostConfig.NetworkMode -cne
        "weatherpoly_default" -or
      $currentConfiguredPostgresPort.Count -ne 1 -or
      [string]$currentConfiguredPostgresPort[0].HostIp -cne "127.0.0.1" -or
      [string]$currentConfiguredPostgresPort[0].HostPort -cne "5432" -or
      $currentRuntimePostgresPort.Count -ne 1 -or
      [string]$currentRuntimePostgresPort[0].HostIp -cne "127.0.0.1" -or
      [string]$currentRuntimePostgresPort[0].HostPort -cne "5432" -or
      ($currentAuthorizedPostgresNetworks -join "`n") -cne
        ((@("weatherpoly_default", "weatherpoly_safety-plane") |
          Sort-Object) -join "`n")
    ) {
      throw "Terminal API authorization requires the current exact retained PostgreSQL boundary."
    }
    $currentLiveProof = Read-CutoverJsonFile `
      -LiteralPath $successorLiveAcceptance `
      -Label "current non-authorizing successor Live acceptance"
    $currentCommitProof = Read-CutoverJsonFile `
      -LiteralPath $successorWatchdogCommit `
      -Label "current successor watchdog commit"
    $currentTerminalProof = Read-CutoverJsonFile `
      -LiteralPath $successorWatchdogTerminal `
      -Label "current successor watchdog terminal authorization"
    if (
      $successorApiStagePermitted -ne $true -or
      $currentLiveProof.payload.authorization.api_stage_permitted -ne $false -or
      $currentTerminalProof.status -cne "success" -or
      $currentTerminalProof.trigger -cne "accepted_live_commit" -or
      $currentTerminalProof.authorization.api_stage_permitted -ne $true -or
      $currentTerminalProof.authorization.worker_start_permitted -ne $false -or
      $currentTerminalProof.authorization.safety_control_start_permitted -ne $false -or
      $currentTerminalProof.authorization.live_transition_permitted -ne $false -or
      $currentTerminalProof.authorization.binance_kill_switch_removal_permitted -ne
        $false -or
      $currentTerminalProof.authorization.migration_container_permitted -ne $false -or
      (Get-CutoverFileSha256 -LiteralPath $networkRepairAttestation) -cne
        $networkRepairAttestationSha256 -or
      (Get-CutoverFileSha256 -LiteralPath $successorPreStartAttestation) -cne
        $successorPreStartAttestationSha256 -or
      (Get-CutoverFileSha256 -LiteralPath $successorWatchdogLease) -cne
        $successorWatchdogLeaseSha256 -or
      (Get-CutoverFileSha256 -LiteralPath $successorWatchdogHeartbeat) -cne
        $successorWatchdogHeartbeatSha256 -or
      (Get-CutoverFileSha256 -LiteralPath $successorLiveAcceptance) -cne
        $successorLiveAcceptanceSha256 -or
      (Get-CutoverFileSha256 -LiteralPath $successorWatchdogCommit) -cne
        $successorWatchdogCommitSha256 -or
      (Get-CutoverFileSha256 -LiteralPath $successorWatchdogTerminal) -cne
        $successorWatchdogTerminalSha256 -or
      (Get-CutoverFileSha256 -LiteralPath $successorWatchdogProgram.script_path) -cne
        $successorWatchdogProgram.script_sha256 -or
      (Get-CutoverFileSha256 -LiteralPath $successorWatchdogProgram.host_path) -cne
        $successorWatchdogProgram.host_sha256 -or
      $currentCommitProof.live_sha256 -cne $successorLiveAcceptanceSha256 -or
      $currentTerminalProof.commit.marker_sha256 -cne $successorWatchdogCommitSha256 -or
      $currentTerminalProof.commit.live_sha256 -cne $successorLiveAcceptanceSha256 -or
      ($currentLiveProof | ConvertTo-Json -Depth 30 -Compress) -cne
        ($successorTransitionGate | ConvertTo-Json -Depth 30 -Compress) -or
      ($currentCommitProof | ConvertTo-Json -Depth 10 -Compress) -cne
        ($successorWatchdogCommitProof | ConvertTo-Json -Depth 10 -Compress) -or
      ($currentTerminalProof | ConvertTo-Json -Depth 30 -Compress) -cne
        ($successorWatchdogTerminalProof | ConvertTo-Json -Depth 30 -Compress)
    ) {
      throw "Live-only consumption is forbidden; terminal composite API authorization drifted."
    }
  }
  Assert-SuccessorTerminalApiAuthorizationUnchanged
  foreach ($objectProof in @(
    [pscustomobject]@{ value = $successorTransitionGate.payload.successor; label = "Live successor" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database; label = "Live database" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.prestart; label = "Live prestart" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session; label = "Live transition_session" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness; label = "Live event_witness" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.rollback; label = "Live rollback" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.postgres_transition; label = "Live postgres_transition" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.topology; label = "Live topology" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.final_boundary; label = "Live final_boundary" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba; label = "Live hba" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.fresh_loaded_state; label = "Live fresh HBA" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.final_boundary.fresh_hba_state; label = "Live final fresh HBA" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.authorization; label = "Live authorization" }
  )) {
    Assert-CutoverJsonObject -Value $objectProof.value -Label $objectProof.label
  }
  foreach ($stringProof in @(
    [pscustomobject]@{ value = $successorTransitionGate.payload.successor.revision; label = "Live revision" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.successor.image_id; label = "Live image_id" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.schema_head; label = "Live schema_head" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.prestart.path; label = "Live PreStart path" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.prestart.sha256; label = "Live PreStart sha256" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.mode; label = "Live transition mode" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.session_id; label = "Live session_id" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.session_id; label = "Live witness session_id" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.postgres_transition.container_id; label = "Live PostgreSQL ID" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.postgres_transition.image_id; label = "Live PostgreSQL image" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.postgres_transition.state; label = "Live PostgreSQL state" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.host_sha256; label = "Live HBA host hash" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.mounted_sha256; label = "Live HBA mounted hash" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.loaded_inventory_sha256; label = "Live HBA inventory hash" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.fresh_loaded_state.inventory_sha256; label = "Live fresh HBA hash" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.final_boundary.fresh_hba_state.conf_load_time; label = "Live final HBA load time" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.final_boundary.fresh_hba_state.postmaster_started_at; label = "Live final postmaster time" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.final_boundary.fresh_hba_state.inventory_sha256; label = "Live final HBA hash" }
  )) {
    Assert-CutoverJsonString -Value $stringProof.value -Label $stringProof.label
  }
  foreach ($booleanProof in @(
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.indexes_exact; label = "indexes_exact" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.functions_exact; label = "functions_exact" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.runtime_boundary; label = "runtime_boundary" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.safety_boundary; label = "safety_boundary" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.control_triggers_exact; label = "control_triggers_exact" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.commit_triggers_exact; label = "commit_triggers_exact" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.identity_triggers_exact; label = "identity_triggers_exact" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.fail_closed_controls; label = "fail_closed_controls" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.zero_live_state; label = "zero_live_state" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.zero_pending_commands; label = "zero_pending_commands" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.zero_other_sessions; label = "zero_other_sessions" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.bootstrap_audit_exact; label = "bootstrap_audit_exact" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.fence_armed; label = "fence_armed" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.active_lease_exists; label = "active_lease_exists" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.database.execution_authorized; label = "execution_authorized" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.eof_before_seal; label = "witness EOF" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.truncated; label = "witness truncated" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.rollback.on_failure; label = "rollback on_failure" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.rollback.executed; label = "rollback executed" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.rollback.emergency_containment; label = "rollback emergency containment" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.postgres_transition.oom_killed; label = "PostgreSQL OOMKilled" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.topology.postgres_only; label = "topology postgres_only" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.topology.application_writers_running; label = "writers running" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.final_boundary.endpoints_unchanged; label = "endpoints unchanged" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.reload_accepted; label = "HBA reload accepted" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.reload_acknowledged; label = "HBA reload acknowledged" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.authorization.api_stage_permitted; label = "API stage permitted" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.authorization.worker_start_permitted; label = "worker start permitted" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.authorization.safety_control_start_permitted; label = "safety start permitted" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.authorization.live_transition_permitted; label = "LIVE permitted" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.authorization.binance_kill_switch_removal_permitted; label = "Binance kill removal permitted" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.authorization.migration_container_permitted; label = "migration permitted" }
  )) {
    Assert-CutoverJsonBoolean -Value $booleanProof.value -Label $booleanProof.label
  }
  foreach ($integerProof in @(
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.postgres_start_count; label = "start count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.postgres_restart_policy_update_count; label = "restart-policy update count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.healthy_status_event_count; label = "healthy status count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.postgres_attach_count; label = "attach count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.network_connect_count; label = "connect count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.data_volume_mount_count; label = "mount count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.gate_exec_triplets; label = "gate exec triplets" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.health_exec_triplets; label = "health exec triplets" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.event_witness.reconnect_count; label = "reconnect count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.transition_session.rollback.stop_timeout_seconds; label = "rollback timeout" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.postgres_transition.restart_count; label = "PostgreSQL restart count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.topology.running_count; label = "running count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.loaded_line_count; label = "HBA line count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.loaded_rule_count; label = "HBA rule count" },
    [pscustomobject]@{ value = $successorTransitionGate.payload.hba.loaded_rule_errors; label = "HBA error count" }
  )) {
    Assert-CutoverJsonInteger -Value $integerProof.value -Label $integerProof.label
  }
  if (
    $successorTransitionGate.format -cne
      "stratus-post-migration-successor-transition-live-v2" -or
    $successorTransitionGate.accepted -ne $true -or
    $successorTransitionGate.payload.successor.revision -cne $revision -or
    $successorTransitionGate.payload.successor.image_id -cne "sha256:$digest" -or
    $successorTransitionGate.payload.database.schema_head -cne $cutoverSchemaPath -or
    $successorTransitionGate.payload.database.indexes_exact -ne $true -or
    $successorTransitionGate.payload.database.functions_exact -ne $true -or
    $successorTransitionGate.payload.database.runtime_boundary -ne $true -or
    $successorTransitionGate.payload.database.safety_boundary -ne $true -or
    $successorTransitionGate.payload.database.control_triggers_exact -ne $true -or
    $successorTransitionGate.payload.database.commit_triggers_exact -ne $true -or
    $successorTransitionGate.payload.database.identity_triggers_exact -ne $true -or
    $successorTransitionGate.payload.database.fail_closed_controls -ne $true -or
    $successorTransitionGate.payload.database.zero_live_state -ne $true -or
    $successorTransitionGate.payload.database.zero_pending_commands -ne $true -or
    $successorTransitionGate.payload.database.zero_other_sessions -ne $true -or
    $successorTransitionGate.payload.database.bootstrap_audit_exact -ne $true -or
    $successorTransitionGate.payload.database.fence_armed -ne $true -or
    $successorTransitionGate.payload.database.active_lease_exists -ne $false -or
    $successorTransitionGate.payload.database.execution_authorized -ne $false -or
    $successorTransitionGate.payload.prestart.path -cne
      $successorPreStartAttestation -or
    $successorTransitionGate.payload.prestart.sha256 -notmatch '^[0-9a-f]{64}$' -or
    $successorTransitionGate.payload.transition_session.mode -cne
      "single-process" -or
    $successorTransitionGate.payload.transition_session.event_witness.session_id -cne
      $successorTransitionGate.payload.transition_session.session_id -or
    $successorTransitionGate.payload.transition_session.event_witness.postgres_start_count -ne 1 -or
    $successorTransitionGate.payload.transition_session.event_witness.postgres_restart_policy_update_count -ne 1 -or
    $successorTransitionGate.payload.transition_session.event_witness.healthy_status_event_count -ne 1 -or
    $successorTransitionGate.payload.transition_session.event_witness.postgres_attach_count -ne 0 -or
    $successorTransitionGate.payload.transition_session.event_witness.network_connect_count -ne 2 -or
    $successorTransitionGate.payload.transition_session.event_witness.data_volume_mount_count -ne 1 -or
    $successorTransitionGate.payload.transition_session.event_witness.gate_exec_triplets -ne 8 -or
    $successorTransitionGate.payload.transition_session.event_witness.health_exec_triplets -lt 1 -or
    $successorTransitionGate.payload.transition_session.event_witness.health_exec_triplets -gt 64 -or
    $successorTransitionGate.payload.transition_session.event_witness.reconnect_count -ne 0 -or
    $successorTransitionGate.payload.transition_session.event_witness.eof_before_seal -ne $false -or
    $successorTransitionGate.payload.transition_session.event_witness.truncated -ne $false -or
    $successorTransitionGate.payload.transition_session.rollback.on_failure -ne $true -or
    $successorTransitionGate.payload.transition_session.rollback.stop_timeout_seconds -ne 15 -or
    $successorTransitionGate.payload.transition_session.rollback.executed -ne $false -or
    $successorTransitionGate.payload.transition_session.rollback.emergency_containment -ne $false -or
    $successorTransitionGate.payload.postgres_transition.container_id -cne
      $postgresContainer[0] -or
    $successorTransitionGate.payload.postgres_transition.image_id -cne
      $postgresImageId -or
    $successorTransitionGate.payload.postgres_transition.restart_count -ne
      $postgresRestartCount -or
    $successorTransitionGate.payload.postgres_transition.restart_policy -cne "no" -or
    $successorTransitionGate.payload.postgres_transition.state -cne "running" -or
    $successorTransitionGate.payload.postgres_transition.oom_killed -ne $false -or
    $successorTransitionGate.payload.topology.running_count -ne 1 -or
    $successorTransitionGate.payload.topology.postgres_only -ne $true -or
    $successorTransitionGate.payload.topology.application_writers_running -ne $false -or
    $successorTransitionGate.payload.final_boundary.endpoints_unchanged -ne $true -or
    $successorTransitionGate.payload.hba.host_sha256 -cne
      "164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4" -or
    $successorTransitionGate.payload.hba.mounted_sha256 -cne
      "164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4" -or
    $successorTransitionGate.payload.hba.loaded_inventory_sha256 -cne
      "e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567" -or
    $successorTransitionGate.payload.hba.loaded_line_count -ne 16 -or
    $successorTransitionGate.payload.hba.loaded_rule_count -ne 15 -or
    $successorTransitionGate.payload.hba.loaded_rule_errors -ne 0 -or
    $successorTransitionGate.payload.hba.reload_accepted -ne $true -or
    $successorTransitionGate.payload.hba.reload_acknowledged -ne $true -or
    $successorTransitionGate.payload.hba.fresh_loaded_state.inventory_sha256 -cne
      "e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567" -or
    $successorTransitionGate.payload.final_boundary.fresh_hba_state.conf_load_time -cne
      $successorTransitionGate.payload.hba.fresh_loaded_state.conf_load_time -or
    $successorTransitionGate.payload.final_boundary.fresh_hba_state.postmaster_started_at -cne
      $successorTransitionGate.payload.hba.fresh_loaded_state.postmaster_started_at -or
    $successorTransitionGate.payload.final_boundary.fresh_hba_state.inventory_sha256 -cne
      $successorTransitionGate.payload.hba.fresh_loaded_state.inventory_sha256 -or
    $successorTransitionGate.payload.authorization.api_stage_permitted -ne $false -or
    $successorApiStagePermitted -ne $true -or
    $successorTransitionGate.payload.authorization.worker_start_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.safety_control_start_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.live_transition_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.binance_kill_switch_removal_permitted -ne $false -or
    $successorTransitionGate.payload.authorization.migration_container_permitted -ne $false -or
    (Get-CutoverFileSha256 -LiteralPath $networkRepairAttestation) -cne
      $networkRepairAttestationSha256 -or
    (Get-CutoverFileSha256 -LiteralPath $successorPreStartAttestation) -cne
      $successorPreStartAttestationSha256 -or
    (Get-CutoverFileSha256 -LiteralPath $successorLiveAcceptance) -cne
      $successorLiveAcceptanceSha256 -or
    (Get-CutoverFileSha256 -LiteralPath $successorWatchdogLease) -cne
      $successorWatchdogLeaseSha256 -or
    (Get-CutoverFileSha256 -LiteralPath $successorWatchdogCommit) -cne
      $successorWatchdogCommitSha256 -or
    (Get-CutoverFileSha256 -LiteralPath $successorWatchdogHeartbeat) -cne
      $successorWatchdogHeartbeatSha256 -or
    (Get-CutoverFileSha256 -LiteralPath $successorWatchdogTerminal) -cne
      $successorWatchdogTerminalSha256
  ) {
    throw "Post-migration successor Transition gate was not accepted fail closed."
  }
  $preMigrationHead = [string]$successorTransitionGate.payload.database.schema_head
}

if ($cutoverSchemaPath -ceq "20260730_0046") {
  if ($preMigrationHead -cne "20260730_0046") {
    throw "Migration staging requires the exact admitted 20260730_0046 schema head."
  }
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
    $null -eq $successorTransitionGate -or
    $preMigrationHead -cne "20260730_0047"
  ) {
    throw "Schema 0047 requires the accepted post-migration successor Transition lineage."
  }
  Write-Host "Schema 0047 successor is accepted; no migrate container was staged or started."
}
```

The running-service assertion must return no application writer. Starting from `0045`, the guarded
command is no longer an accepted release path. Starting from `0046`, the guarded command applies
the safety-lease migration. Starting from `0047`, only the single public Transition invocation may
cross the stopped-to-live boundary; the runbook contains no PostgreSQL start on that branch. The
migration service is never created or started on this path: the accepted schema, all seven indexes,
safety table, all 24 exact function bodies and catalog/ACL contracts, triggers, fail-closed
controls, zero-LIVE state, session/lock boundary, and bootstrap audit are proved twice read-only
within 60 seconds. If Transition fails after its own exact start, it must perform its bounded exact
PostgreSQL stop and re-inspection before returning failure. Every create-new artifact it managed to
publish remains terminal failure lineage; never remove one. Retry generates a fresh attempt ID, new
output paths, and a new transition session (the same evidence directory may be reused if it remains
a regular directory). Live remains non-authorizing. Only the independently written terminal receipt
may grant API staging, and only when it says `status=success` and `trigger=accepted_live_commit` and
hash-binds the exact lease, commit, Live bytes, Docker engine, and retained PostgreSQL `StartedAt`.
The historical
pre-migration attestation remains immutable and is not reissued or accepted through the legacy
`Resume` mode.
In the `0046` path, `--pull never`,
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
returned by the stopped-container pre-start gate (never to a newly computed expected value). The
`0046` path performs and acknowledges its controlled post-schema reload in a bounded original
backend, then proves the generation in a fresh backend. The `0047` path must only re-read the
Transition-owned immutable acknowledgement and current bytes; it must never issue a second reload.
Both paths recheck the exact normalized loaded rules and byte sources while the shell-owned HBA
read lock still denies write/delete/replace:

```powershell
if ($cutoverSchemaPath -ceq "20260730_0046") {
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
$postSchemaHbaReloadReceipt = Invoke-AcknowledgedRunbookHbaReload
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
}
else {
  # Transition already owned the only HBA reload and acknowledged its loaded
  # generation before it sealed Live. Re-read the immutable receipt and bytes;
  # never issue a second reload on an accepted 0047 successor.
  $successorLiveAcceptanceSha256 = Get-CutoverFileSha256 `
    -LiteralPath $successorLiveAcceptance
  $successorTransitionGateRecheck = Read-CutoverJsonFile `
    -LiteralPath $successorLiveAcceptance -Label "successor Live HBA receipt recheck"
  if (
    ($successorTransitionGateRecheck | ConvertTo-Json -Depth 30 -Compress) -cne
      ($successorTransitionGate | ConvertTo-Json -Depth 30 -Compress) -or
    $successorTransitionGateRecheck.payload.hba.reload_accepted -ne $true -or
    $successorTransitionGateRecheck.payload.hba.reload_acknowledged -ne $true -or
    $successorTransitionGateRecheck.payload.hba.host_read_lock_held -ne $true -or
    $successorTransitionGateRecheck.payload.hba.host_write_share_permitted -ne $false -or
    $successorTransitionGateRecheck.payload.hba.host_delete_share_permitted -ne $false -or
    $successorTransitionGateRecheck.payload.hba.loaded_inventory_sha256 -cne
      "e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567" -or
    $successorTransitionGateRecheck.payload.hba.fresh_loaded_state.inventory_sha256 -cne
      "e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567" -or
    $successorTransitionGateRecheck.payload.final_boundary.fresh_hba_state.inventory_sha256 -cne
      "e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567"
  ) {
    throw "Accepted 0047 Transition did not retain its exact acknowledged HBA receipt."
  }
  $currentHostHbaSha256 = (
    Get-FileHash -LiteralPath (
      Join-Path $productionRoot "ops\postgres\stratus-pg_hba.conf"
    ) -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  $currentMountedHbaProof = @(
    docker exec $postgresContainer[0] sha256sum `
      /etc/postgresql/stratus-pg_hba.conf
  )
  $currentMountedHbaSha256 = if (
    $LASTEXITCODE -eq 0 -and
    $currentMountedHbaProof.Count -eq 1 -and
    [string]$currentMountedHbaProof[0] -match '^([0-9a-f]{64})\s+'
  ) { $Matches[1] } else { "" }
  $postTransitionInspect = @(docker inspect $postgresContainer[0] | ConvertFrom-Json)[0]
  if (
    $currentHostHbaSha256 -cne
      "164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4" -or
    $currentMountedHbaSha256 -cne $currentHostHbaSha256 -or
    [string]$postTransitionInspect.Id -cne $postgresContainer[0] -or
    -not [bool]$postTransitionInspect.State.Running -or
    [bool]$postTransitionInspect.State.OOMKilled -or
    [string]$postTransitionInspect.HostConfig.RestartPolicy.Name -cne "no" -or
    (Get-CutoverFileSha256 -LiteralPath $successorLiveAcceptance) -cne
      $successorLiveAcceptanceSha256 -or
    (Get-CutoverFileSha256 -LiteralPath $networkRepairAttestation) -cne
      $networkRepairAttestationSha256
  ) {
    throw "Post-Transition 0047 HBA bytes, receipt, or fail-closed identity drifted."
  }
}

if (
  $null -eq $runbookHbaReadLock -or
  -not $runbookHbaReadLock.CanRead -or
  (Get-FileHash -LiteralPath $cutoverHbaSource -Algorithm SHA256).
    Hash.ToLowerInvariant() -cne
    "164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4"
) {
  throw "The shell-owned HBA read lock did not survive through role admission."
}
if ($cutoverSchemaPath -ceq "20260730_0047") {
  Assert-SuccessorTerminalApiAuthorizationUnchanged
}
docker compose @releaseComposeArguments --profile release run --rm --no-deps provision-runtime-role
if ($LASTEXITCODE -ne 0) {
  throw "Runtime database role activation or exact boundary proof failed."
}
if ($cutoverSchemaPath -ceq "20260730_0047") {
  Assert-SuccessorTerminalApiAuthorizationUnchanged
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
if ($cutoverSchemaPath -ceq "20260730_0047") {
  Assert-SuccessorTerminalApiAuthorizationUnchanged
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
$observerExit = 2
$httpInvocationFailed = $false
$observerTimedOut = $false
$observerStopFailed = $false
try {
  & $hostPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File (Join-Path $buildContext "scripts\Invoke-StratusDashboardLoadGate.ps1") `
    -BaseUri "http://127.0.0.1:8000/" `
    -LoadGateRunId $runId `
    -SteadyStateSeconds 300 `
    -OutputPath $httpArtifact
  $httpExit = $LASTEXITCODE
}
catch {
  $httpExit = 125
  $httpInvocationFailed = $true
}
finally {
  if (-not $observer.WaitForExit(150000)) {
    $observerTimedOut = $true
    $observerExit = 124
    try {
      Stop-GateObserver -Process $observer
    }
    catch {
      $observerStopFailed = $true
      $observerExit = 125
    }
  }
  else {
    $observerExit = [int]$observer.ExitCode
  }
}
# STRATUS_LOAD_GATE_COMPOSITE_FINALIZATION_BEGIN
$rawArtifactsComplete = (
  (Test-Path -LiteralPath $httpArtifact -PathType Leaf) -and
  (Test-Path -LiteralPath $runtimeArtifact -PathType Leaf)
)
$assertionExit = 2
$compositeAttempted = $false
$compositeArtifactRecorded = $false
$compositeContractValid = $false
$compositePassed = $false
$compositeSha256 = $null

# Even a failed HTTP or runtime subgate must be composited when both raw
# artifacts exist. The assertion writes the durable rejected artifact before
# this wrapper reports the preserved subgate exit codes and stops the cutover.
if ($rawArtifactsComplete) {
  $compositeAttempted = $true
  try {
    & $hostPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass `
      -File (Join-Path $buildContext "scripts\Assert-StratusLoadGateArtifacts.ps1") `
      -HttpArtifactPath $httpArtifact `
      -RuntimeArtifactPath $runtimeArtifact `
      -ExpectedRunId $runId `
      -ExpectedApiImageId "sha256:$digest" `
      -ExpectedRevision $revision `
      -ExpectedReleaseDigest $digest `
      -OutputPath $acceptedArtifact
    $assertionExit = $LASTEXITCODE
  }
  catch {
    $assertionExit = 125
  }
  $compositeArtifactRecorded = Test-Path -LiteralPath $acceptedArtifact -PathType Leaf
  if ($compositeArtifactRecorded) {
    try {
      $compositeSha256 = (
        Get-FileHash -LiteralPath $acceptedArtifact -Algorithm SHA256 -ErrorAction Stop
      ).Hash.ToLowerInvariant()
      $compositeJson = (
        Get-Content -LiteralPath $acceptedArtifact -Raw -ErrorAction Stop
      ).Trim()
      if (-not $compositeJson.StartsWith('{') -or -not $compositeJson.EndsWith('}')) {
        throw "Composite load-gate artifact root is not a JSON object."
      }
      $convertFromJson = Get-Command `
        -Name 'Microsoft.PowerShell.Utility\ConvertFrom-Json' `
        -CommandType Cmdlet `
        -ErrorAction Stop
      $convertParameters = @{
        InputObject = $compositeJson
        ErrorAction = 'Stop'
      }
      if ($convertFromJson.Parameters.ContainsKey('DateKind')) {
        $convertParameters['DateKind'] = 'String'
      }
      if ($convertFromJson.Parameters.ContainsKey('NoEnumerate')) {
        $convertParameters['NoEnumerate'] = $true
      }
      $composite = Microsoft.PowerShell.Utility\ConvertFrom-Json @convertParameters
      if (
        $null -eq $composite -or
        $composite.GetType().FullName -cne
          'System.Management.Automation.PSCustomObject'
      ) {
        throw "Composite load-gate artifact root is not a native JSON object."
      }
      $schemaProperty = $composite.PSObject.Properties['schema_version']
      $gateProperty = $composite.PSObject.Properties['gate']
      $passedProperty = $composite.PSObject.Properties['production_gate_passed']
      $exitProperty = $composite.PSObject.Properties['exit_code']
      $runProperty = $composite.PSObject.Properties['run_id']
      if (
        $null -ne $schemaProperty -and
        ($schemaProperty.Value -is [int] -or $schemaProperty.Value -is [long]) -and
        [long]$schemaProperty.Value -eq 1 -and
        $null -ne $gateProperty -and
        [string]$gateProperty.Value -ceq 'stratus_candidate_production_load' -and
        $null -ne $passedProperty -and $passedProperty.Value -is [bool] -and
        $null -ne $exitProperty -and
        ($exitProperty.Value -is [int] -or $exitProperty.Value -is [long]) -and
        [long]$exitProperty.Value -eq $assertionExit -and
        $null -ne $runProperty -and [string]$runProperty.Value -ceq $runId
      ) {
        $compositeContractValid = $true
        $compositePassed = [bool]$passedProperty.Value
      }
      if ($compositePassed) {
        $candidateProperty = $composite.PSObject.Properties['candidate']
        $candidate = if ($null -ne $candidateProperty) {
          $candidateProperty.Value
        }
        else {
          $null
        }
        $compositeContractValid = (
          $null -ne $candidate -and
          $candidate.GetType().FullName -ceq
            'System.Management.Automation.PSCustomObject' -and
          [string]$candidate.api_image_id -ceq "sha256:$digest" -and
          [string]$candidate.revision -ceq $revision -and
          [string]$candidate.release_digest -ceq $digest
        )
        if (-not $compositeContractValid) {
          $compositePassed = $false
        }
      }
    }
    catch {
      # Preserve the finalization milestone and all child exits; the invalid
      # composite contract is rejected below without masking that evidence.
      $compositeContractValid = $false
      $compositePassed = $false
    }
  }
}

$loadGateOutcome = [pscustomobject]@{
  milestone = "LOAD_GATE_COMPOSITE_FINALIZED"
  run_id = $runId
  http_exit = $httpExit
  runtime_exit = $observerExit
  assertion_exit = $assertionExit
  http_invocation_failed = $httpInvocationFailed
  observer_timed_out = $observerTimedOut
  observer_stop_failed = $observerStopFailed
  raw_artifacts_complete = $rawArtifactsComplete
  composite_attempted = $compositeAttempted
  composite_artifact_recorded = $compositeArtifactRecorded
  composite_contract_valid = $compositeContractValid
  production_gate_passed = $compositePassed
  http_artifact = $httpArtifact
  runtime_artifact = $runtimeArtifact
  composite_artifact = $acceptedArtifact
  composite_sha256 = $compositeSha256
}
$loadGateOutcome | ConvertTo-Json -Compress | Write-Output

if (-not $rawArtifactsComplete) {
  throw (
    "Candidate load gate raw evidence is incomplete; " +
    "http_exit=$httpExit; runtime_exit=$observerExit."
  )
}
if (-not $compositeArtifactRecorded -or -not $compositeContractValid) {
  throw (
    "Composite candidate load gate did not record a valid result; " +
    "http_exit=$httpExit; runtime_exit=$observerExit; assertion_exit=$assertionExit."
  )
}
if (
  $httpExit -ne 0 -or
  $observerExit -ne 0 -or
  $assertionExit -ne 0 -or
  -not $compositePassed
) {
  throw (
    "Composite candidate load gate rejected the evidence; " +
    "http_exit=$httpExit; runtime_exit=$observerExit; " +
    "assertion_exit=$assertionExit; artifact=$acceptedArtifact."
  )
}
# STRATUS_LOAD_GATE_COMPOSITE_FINALIZATION_END
```

The finalization block above deliberately runs the composite assertion before raising a subgate
failure whenever both raw artifacts exist. A rejected `*-accepted.json` is therefore retained as
the durable composite decision, and the emitted `LOAD_GATE_COMPOSITE_FINALIZED` record preserves
the HTTP, runtime-observer, and assertion exit codes. Missing raw evidence, a missing or malformed
composite artifact, any non-zero exit, or `production_gate_passed=false` remains fail closed.

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
$postgresNetworkNames = @($postgresNetworkNames | Sort-Object)
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
function ConvertFrom-ExactCanaryJson {
  param(
    [Parameter(Mandatory = $true)]
    [AllowNull()]
    [object]$Json
  )

  if ($Json -isnot [string]) {
    throw "Canary proof command must emit exactly one JSON string."
  }

  $convertFromJson = Get-Command `
    -Name "Microsoft.PowerShell.Utility\ConvertFrom-Json" `
    -CommandType Cmdlet -ErrorAction Stop
  $convertParameters = @{ InputObject = $Json }
  if ($convertFromJson.Parameters.ContainsKey("NoEnumerate")) {
    $convertParameters["NoEnumerate"] = $true
  }

  return Microsoft.PowerShell.Utility\ConvertFrom-Json @convertParameters
}

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
  $proofJson = & $canaryStopBarrier `
    -Root $canaryRoot `
    -RunId $canaryRunId `
    -ResumeRequestId $requestId `
    -PostgresContainerId $postgresContainer[0] `
    -SafetyApiKey $safetyApiKey
  $proof = ConvertFrom-ExactCanaryJson -Json $proofJson
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
  $canaryReadyJson = & $canaryArtifactValidator `
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
    -ReleaseDigest $digest
  $canaryReadyProof = ConvertFrom-ExactCanaryJson -Json $canaryReadyJson
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
  $canaryTerminalJson = & $canaryArtifactValidator `
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
    -ReleaseDigest $digest
  $canaryTerminalProof = ConvertFrom-ExactCanaryJson -Json $canaryTerminalJson
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
