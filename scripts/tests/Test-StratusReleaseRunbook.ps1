[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$runbookPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..\docs\RELEASE_CUTOVER_RUNBOOK.md')
)
$source = Get-Content -LiteralPath $runbookPath -Raw

$powerShellBlocks = @(
    [regex]::Matches($source, '(?s)```powershell\s*(.*?)```')
)
if ($powerShellBlocks.Count -eq 0) {
    throw 'Release runbook contains no executable PowerShell blocks.'
}
for ($blockIndex = 0; $blockIndex -lt $powerShellBlocks.Count; $blockIndex++) {
    $tokens = $null
    $parseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseInput(
        $powerShellBlocks[$blockIndex].Groups[1].Value,
        [ref]$tokens,
        [ref]$parseErrors
    )
    if (@($parseErrors).Count -ne 0) {
        throw (
            "Release runbook PowerShell block $blockIndex does not parse: " +
            (@($parseErrors | ForEach-Object { $_.Message }) -join '; ')
        )
    }
}

foreach ($forbidden in @(
    'docker compose create',
    'Start-Process -FilePath "pwsh"',
    'docker inspect weatherpoly-worker-1',
    'expected_plan_hash = \$plan\.plan_hash',
    '\$secureApiKey',
    '(?m)^\s*\.\\scripts\\Invoke-StratusDashboardLoadGate\.ps1',
    '(?m)^\s*\.\\scripts\\Assert-StratusLoadGateArtifacts\.ps1',
    '(?m)^\s*\.\\scripts\\Assert-StratusReleaseImage\.ps1'
)) {
    if ($source -match $forbidden) {
        throw "Release runbook contains an unsafe or non-portable command: $forbidden"
    }
}
foreach ($required in @(
    'docker compose @releaseComposeArguments stop backup',
    'docker compose @releaseComposeArguments --profile release up --no-start --no-deps --no-build --pull never --force-recreate migrate',
    '\$releaseState = "FREEZE"',
    '\$releaseComposeArguments = @\(',
    '"--project-directory", \$productionRoot',
    '"-f", \$releaseComposeFile',
    '"--env-file", \$releaseComposeEnvFile',
    '"--project-name", "weatherpoly"',
    'COMPOSE_PATH_SEPARATOR',
    'Release \.env must not define a reserved Compose override variable',
    'Join-Path \$buildContext "scripts\\Assert-StratusPreStartTopology\.ps1"',
    '-Root \$productionRoot -RequirePostgresStopped',
    '\$preStartTopology\.unexpected_running_count -ne 0',
    '\$postBackupStopTopology\.allowed_running_backup_count -ne 0',
    'Backup or application process remained after the pre-start stop boundary',
    'STRATUS_RELEASE_WORKTREE',
    'git -C \$productionRoot status --porcelain=v1 --untracked-files=all',
    '\$productionRevision -ne \$revision',
    'Production checkout must be the exact clean release revision',
    'git -C \$buildContext rev-parse HEAD',
    'git -C \$buildContext status --porcelain=v1 --untracked-files=all',
    'git -C \$buildContext symbolic-ref -q --short HEAD',
    '--tag \$image \$buildContext',
    'Join-Path \$buildContext "scripts\\Assert-StratusReleaseImage\.ps1"',
    'Exact candidate image build failed',
    '\$preMigrationHead -notin @\(',
    '"20260730_0046"',
    '"20260730_0047"',
    '\$preMigrationHead -eq "20260730_0046"',
    'Schema 0047 is already accepted; skip migration service',
    'paper_canary_safety_leases',
    'uq_paper_canary_single_active_lease',
    'ix_paper_canary_active_expiry',
    'trg_paper_canary_request_reservation',
    'enforce_paper_canary_request_reservation',
    'trg_paper_canary_signal_commit_fence',
    'trg_paper_canary_order_commit_fence',
    'trg_paper_canary_intent_commit_fence',
    'trg_paper_canary_fill_leg_commit_fence',
    'trg_paper_canary_position_commit_fence',
    'enforce_paper_canary_execution_commit_fence',
    'trg_paper_canary_bankroll_identity_fence',
    'trg_paper_canary_order_identity_fence',
    'trg_paper_canary_position_identity_fence',
    'trg_paper_canary_signal_identity_fence',
    'enforce_paper_canary_execution_identity_fence',
    'assert_stratus_runtime_boundary\(false\)',
    'assert_stratus_safety_boundary\(false\)',
    'trigger_row\.tgfoid::regprocedure::text',
    'trigger_row\.tgenabled',
    'trigger_row\.tgtype',
    'trigger_row\.tgconstraint <> 0 AS constraint_backed',
    'trigger_row\.tgdeferrable',
    'trigger_row\.tginitdeferred',
    '31::smallint',
    '21::smallint',
    '5::smallint',
    '27::smallint',
    'commit-fence query must',
    'identity-fence query must',
    'exactly four rows',
    'Compose could not stage the exact-candidate migrate service',
    'Assert-StratusPreMigrationBackupGate\.ps1',
    '-Mode Create',
    '-Mode Resume',
    'Create-new pre-0047 backup failed',
    'Full isolated PostgreSQL 16 restore acceptance failed',
    'STRATUS_PRE_MIGRATION_BACKUP_DIRECTORY',
    'STRATUS_PRE_MIGRATION_ATTESTATION',
    'docker compose @releaseComposeArguments --profile release run --rm --no-deps provision-runtime-role',
    'ix_signals_pending_stop_cleanup',
    'ix_orders_live_open_stop_cleanup',
    'exactly five rows and ten `true` flags',
    'docker compose @releaseComposeArguments --profile release run --rm --no-deps validate-safety-credentials',
    'docker compose @releaseComposeArguments --profile release run --rm --no-deps provision-safety-role',
    'must not repair ACL, RLS, trigger, function, or ownership drift',
    '43-256 character URL-safe high-diversity random values',
    'passwords must be pairwise different',
    "current_setting\('hba_file'\)",
    'pg_hba_file_rules',
    '\$postgresInspect\.Mounts',
    '\[string\]\$hbaMount\[0\]\.Type -cne ''bind''',
    '\[bool\]\$hbaMount\[0\]\.RW',
    '\$actualHbaSource -cnotin @\(\$expectedSourceAliases\)',
    'Get-FileHash -LiteralPath \$expectedHbaSource -Algorithm SHA256',
    'sha256sum /etc/postgresql/stratus-pg_hba\.conf',
    'file_name, line_number::text, type',
    'array_to_string\(database, '',''\)',
    'array_to_string\(user_name, '',''\)',
    'ORDER BY rule_number',
    '\$expectedHbaRules\.Count',
    'exact normalized release HBA rule inventory',
    'PGPASSWORD="\$POSTGRES_PASSWORD" exec psql',
    '--username="\$POSTGRES_USER" --dbname="\$POSTGRES_DB"',
    'PATH\|/etc/postgresql/stratus-pg_hba.conf',
    '\$fixedReleaseHbaHash = \[string\]\$preStartTopology\.hba_sha256',
    '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4',
    '\$immediateHbaBytesBeforeReload = Get-ImmediateReleaseHbaByteProof',
    '\$immediateHbaBytesAfterRules = Get-ImmediateReleaseHbaByteProof',
    '0115ddeb48f40fd145eaf53a7dee524f609e8a4e28eee46d300a3993b4695df5',
    'first post-start loaded HBA inventory did not match the fixed release proof',
    'SELECT pg_reload_conf\(\)',
    '\$hbaBytesBeforeReload = Get-FixedReleaseHbaByteProof',
    '\$hbaBytesAfterReload = Get-FixedReleaseHbaByteProof',
    '\$hbaBytesAfterRules = Get-FixedReleaseHbaByteProof',
    'HBA bytes changed while the normalized loaded-rule proof was collected',
    'docker compose @releaseComposeArguments --profile release up --no-start --no-build --pull never --no-deps --force-recreate safety-control',
    'default Compose model contains zero services',
    'plain `docker compose up` cannot start production',
    'The `release` profile',
    '`infrastructure`, `binance`',
    '\$safetyInspect\.Image -ne "sha256:\$digest"',
    'RELEASE_IMAGE_DIGEST=\$digest',
    'SAFETY_CANARY_LEASE_TTL_SECONDS=15',
    'SAFETY_WATCHDOG_POLL_SECONDS=3',
    'STRATUS_DATABASE_POOL_SIZE=1',
    'STRATUS_DATABASE_MAX_OVERFLOW=0',
    '\$safetyPublishedPorts\.Count -ne 1',
    '\$safetyNetworks\.Count -ne 1',
    'docker network inspect \$expectedSafetyNetworks\[0\]',
    '\$safetyNetworks\[0\] -cne \[string\]\$expectedSafetyNetworks\[0\]',
    'com\.docker\.compose\.network'' -ne\s+"safety-plane"',
    'docker network connect --alias postgres',
    '\$expectedPostgresNetworks = @\(',
    '\$postgresNetworkInspect\.Image -ne \$postgresImageId',
    '@\(\$postgresSafetyEndpoint\.Aliases\) -notcontains "postgres"',
    '\[string\]\$safetyPort\[0\]\.HostIp -ne "127\.0\.0\.1"',
    '\[string\]\$safetyPort\[0\]\.HostPort -ne "8001"',
    'http://127\.0\.0\.1:8001/healthz',
    'http://127\.0\.0\.1:8001/readyz',
    '\[string\]\$safetyReady\.database_boundary -ne "exact"',
    '\$digest = \$candidateImageId\.Substring\(7\)',
    '\$candidateImageId = \[string\]\$imageInspect\.Id',
    '\$candidateRevision = \$revision',
    '\$hostPowerShell = \(Get-Process -Id \$PID\)\.Path',
    'Join-Path \$buildContext "scripts\\Invoke-StratusRuntimeLoadObserver\.ps1"',
    'STRATUS_GATE_READY_ARTIFACT',
    'Test-Path -LiteralPath \$readyArtifact',
    '\$ready\.preflight_safe',
    '\$observer\.WaitForExit\(150000\)',
    'Stop-GateObserver -Process \$observer',
    '& \$hostPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass',
    'Join-Path \$buildContext "scripts\\Invoke-StratusDashboardLoadGate\.ps1"',
    'Join-Path \$buildContext "scripts\\Assert-StratusLoadGateArtifacts\.ps1"',
    '\$recomputedPlan = Invoke-RestMethod',
    '\$successorEpochId = \[long\]\$activation\.activated\.id',
    '\$cohort = \[string\]\$activation\.activated\.cohort',
    '\[string\]\$activation\.activated\.image_digest -ne \$digest',
    '\$cohort -ne \[string\]\$recomputedPlan\.cohort',
    'expected_plan_hash = \$recomputedPlan\.plan_hash',
    '\$stoppedWorker = @\(docker compose @releaseComposeArguments ps --all -q worker\)',
    'Assert-StratusLoadGateArtifacts\.ps1',
    '-SafetyBaseUri "http://127\.0\.0\.1:8001/"',
    '\$canaryScript = Join-Path \$canaryRoot "scripts\\Invoke-StratusPaperCanary\.ps1"',
    '-ArgumentList @\(',
    '\$operatorApiKey = ConvertTo-SecureString',
    '\$safetyApiKey = ConvertTo-SecureString',
    '\$canaryJob = Start-Job',
    'Wait-Job -Job \$canaryJob -Timeout 1',
    '\$canaryJob\.State -ne ''Running''',
    '\$canaryLaunchedAtUtc = \[DateTime\]::UtcNow',
    '\$canaryReadyDeadlineUtc = \$canaryLaunchedAtUtc\.AddMinutes\(5\)',
    '\$canaryTerminalDeadlineUtc = \$canaryLaunchedAtUtc\.AddSeconds\(3900\)',
    '\$remainingCanarySeconds = \[int\]\[Math\]::Floor',
    'Wait-Job -Job \$canaryJob -Timeout \$remainingCanarySeconds',
    'Assert-StratusPaperCanaryArtifacts\.ps1',
    'Invoke-StratusPaperCanaryStopBarrier\.ps1',
    'function Invoke-VerifiedCanaryStopBarrier',
    '# STRATUS_CANARY_FAILURE_CLEANUP_BEGIN',
    'function New-CanaryLifecycleGuard',
    'function Stop-CanaryJobAfterBarrierAttempt',
    '\$null = Invoke-VerifiedCanaryStopBarrier',
    'Wait-Job -Job \$canaryJob -Timeout 5 -ErrorAction Stop',
    'Receive-Job -Job \$canaryJob -ErrorAction Stop',
    'Remove-Job -Job \$canaryJob -Force -ErrorAction Stop',
    '\$expectedCanaryStopRequestId = "paper-canary-stop-\$canaryRunId"',
    '# STRATUS_CANARY_EXTERNAL_PROOF_VALIDATORS_BEGIN',
    'function Test-NativeCanaryJsonInteger',
    'function Test-NativeCanaryJsonBoolean',
    'Test-NativeCanaryJsonInteger -Value \$Proof\.live\.fill_legs',
    'Test-NativeCanaryJsonBoolean -Value \$Proof\.verified',
    '\$Proof\.request_id -is \[string\]',
    'Test-ExactCanaryStopBarrierProof -Proof \$proof',
    'Test-ExactCanaryReadyProof -Proof \$canaryReadyProof',
    'Test-ExactCanaryTerminalProof -Proof \$canaryTerminalProof',
    '-Mode Ready',
    '-Mode Terminal',
    '-ResumeActor \$resumeActor',
    '-ResumeReason \$resumeReason',
    '\$Proof\.terminal_runtime -ceq "polymarket_paper_fail_closed"',
    'without placing either\s+secret in its command arguments',
    'STRATUS_SAFETY_API_KEY',
    'Reusing one credential',
    '-CanaryRunId \$RunId',
    '# STRATUS_RESUME_SUBMIT_BEGIN',
    '\$resumeAccepted = Invoke-RestMethod',
    '-TimeoutSec \$resumePostTimeoutSeconds',
    '\$resumeReconcileAttempts = 6',
    'api/v1/commands\?limit=200',
    'Test-ExactCanaryResumeCommand',
    'Stop-CanaryAfterResumeSubmissionFailure',
    '# STRATUS_CANARY_LIFETIME_SCOPE_BEGIN',
    '\. \$canaryReadyPhase',
    '\. \$canaryResumePhase',
    '\. \$canaryTerminalPhase',
    '\$canaryLifecycleGuard\.Succeeded = \$true',
    '\$rollbackStopRequestId = "rollback-stop-"',
    'http://127\.0\.0\.1:8001/api/v1/emergency-stop',
    'Independent safety-control did not prove the exact rollback STOP',
    'HostConfig\.Memory -ne 1073741824L',
    'HostConfig\.Memory -ne 3221225472L',
    'HostConfig\.Memory -ne 268435456L',
    "\^OPERATOR_API_KEY=",
    'credential-isolation mismatch'
)) {
    if ($source -notmatch $required) {
        throw "Release runbook is missing a required safety command: $required"
    }
}

$externalProofValidatorsMatch = [regex]::Match(
    $source,
    '(?s)# STRATUS_CANARY_EXTERNAL_PROOF_VALIDATORS_BEGIN\s*\r?\n' +
        '(?<body>.*?)# STRATUS_CANARY_EXTERNAL_PROOF_VALIDATORS_END'
)
if (-not $externalProofValidatorsMatch.Success) {
    throw 'Release runbook is missing native-type external proof validators.'
}
$externalProofValidatorsBlock = [scriptblock]::Create(
    $externalProofValidatorsMatch.Groups['body'].Value
)

$canaryDataflowMatch = [regex]::Match(
    $source,
    '(?s)# STRATUS_CANARY_DATAFLOW_BEGIN\s*\r?\n' +
        '(?<body>.*?)# STRATUS_CANARY_DATAFLOW_END'
)
if (-not $canaryDataflowMatch.Success) {
    throw 'Release runbook is missing the executable canary identity dataflow block.'
}
$canaryDataflow = [scriptblock]::Create($canaryDataflowMatch.Groups['body'].Value)

function Invoke-CanaryDataflowFixture {
    param(
        [string] $ActivationDigest,
        [string] $ActivationCohort = 'release-cohort-v47',
        [switch] $ReuseControlKey,
        [switch] $MissingOperatorKey
    )

    $productionRoot = [IO.Path]::GetTempPath()
    $revision = 'a' * 40
    $digest = 'b' * 64
    $imageInspect = [pscustomobject]@{
        Id = "sha256:$digest"
        Config = [pscustomobject]@{
            Labels = [pscustomobject]@{
                'org.opencontainers.image.revision' = $revision
            }
        }
    }
    $recomputedPlan = [pscustomobject]@{
        plan_hash = 'c' * 64
        cohort = 'release-cohort-v47'
    }
    $activation = [pscustomobject]@{
        activated = [pscustomobject]@{
            id = 22
            cohort = $ActivationCohort
            image_digest = $ActivationDigest
            plan_hash = 'c' * 64
        }
    }
    $env:STRATUS_OPERATOR_API_KEY = if ($MissingOperatorKey) {
        $null
    }
    else {
        'operator_' + ('o' * 50)
    }
    $env:STRATUS_SAFETY_API_KEY = if ($ReuseControlKey) {
        $env:STRATUS_OPERATOR_API_KEY
    }
    else {
        'safety_' + ('s' * 52)
    }
    . $canaryDataflow
    return [pscustomobject]@{
        root = $canaryRoot
        image_id = $candidateImageId
        revision = $candidateRevision
        epoch_id = $successorEpochId
        cohort = $cohort
        operator_key_type = $operatorApiKey.GetType().FullName
        safety_key_type = $safetyApiKey.GetType().FullName
    }
}

$previousOperatorKey = $env:STRATUS_OPERATOR_API_KEY
$previousSafetyKey = $env:STRATUS_SAFETY_API_KEY
try {
    $acceptedDataflow = Invoke-CanaryDataflowFixture -ActivationDigest ('b' * 64)
    if (
        $acceptedDataflow.image_id -ne ('sha256:' + ('b' * 64)) -or
        $acceptedDataflow.revision -ne ('a' * 40) -or
        $acceptedDataflow.epoch_id -ne 22 -or
        $acceptedDataflow.cohort -ne 'release-cohort-v47' -or
        $acceptedDataflow.operator_key_type -ne 'System.Security.SecureString' -or
        $acceptedDataflow.safety_key_type -ne 'System.Security.SecureString'
    ) {
        throw 'Executable canary dataflow did not derive the exact release identities.'
    }
    foreach ($negative in @(
        [pscustomobject]@{ Digest = ('d' * 64); Cohort = 'release-cohort-v47'; Reuse = $false; Missing = $false },
        [pscustomobject]@{ Digest = ('b' * 64); Cohort = 'wrong-cohort'; Reuse = $false; Missing = $false },
        [pscustomobject]@{ Digest = ('b' * 64); Cohort = 'release-cohort-v47'; Reuse = $true; Missing = $false },
        [pscustomobject]@{ Digest = ('b' * 64); Cohort = 'release-cohort-v47'; Reuse = $false; Missing = $true }
    )) {
        $failed = $false
        try {
            Invoke-CanaryDataflowFixture -ActivationDigest $negative.Digest `
                -ActivationCohort $negative.Cohort -ReuseControlKey:$negative.Reuse `
                -MissingOperatorKey:$negative.Missing | Out-Null
        }
        catch {
            $failed = $true
        }
        if (-not $failed) {
            throw 'Executable canary dataflow accepted a mismatched identity or reused key.'
        }
    }
}
finally {
    $env:STRATUS_OPERATOR_API_KEY = $previousOperatorKey
    $env:STRATUS_SAFETY_API_KEY = $previousSafetyKey
}

function Invoke-ExternalProofTypeFixtures {
    $canaryRunId = 'a' * 32
    $requestId = 'release-paper-' + ('b' * 32)
    $expectedCanaryStopRequestId = "paper-canary-stop-$canaryRunId"
    $postgresContainer = @('c' * 64)
    . $externalProofValidatorsBlock

    function Copy-ProofFixture {
        param([Parameter(Mandatory = $true)][object] $Proof)
        return $Proof | ConvertTo-Json -Depth 16 -Compress | ConvertFrom-Json
    }

    function Assert-ExternalProofRootShapesRejected {
        param(
            [Parameter(Mandatory = $true)]
            [ValidateSet('STOP', 'Ready', 'Terminal')]
            [string] $Name,
            [Parameter(Mandatory = $true)][object] $Proof
        )

        $proofJson = $Proof | ConvertTo-Json -Depth 16 -Compress
        foreach ($rootCase in @(
            [pscustomobject]@{ Name = 'null'; Json = 'null' },
            [pscustomobject]@{ Name = 'string'; Json = '"proof"' },
            [pscustomobject]@{ Name = 'scalar'; Json = '1' },
            [pscustomobject]@{
                Name = 'singleton-array'
                Json = "[$proofJson]"
            }
        )) {
            $malformedProof = ConvertFrom-Json -InputObject $rootCase.Json
            if (
                $rootCase.Name -ceq 'singleton-array' -and
                (
                    $malformedProof -isnot [array] -or
                    @($malformedProof).Count -ne 1
                )
            ) {
                throw 'PowerShell 5.1 did not preserve the singleton-array fixture.'
            }
            try {
                $rootAccepted = switch ($Name) {
                    'STOP' {
                        Test-ExactCanaryStopBarrierProof `
                            -Proof ([object]$malformedProof)
                    }
                    'Ready' {
                        Test-ExactCanaryReadyProof `
                            -Proof ([object]$malformedProof)
                    }
                    'Terminal' {
                        Test-ExactCanaryTerminalProof `
                            -Proof ([object]$malformedProof)
                    }
                }
            }
            catch {
                $malformedType = if ($null -eq $malformedProof) {
                    'null'
                }
                else {
                    $malformedProof.GetType().FullName
                }
                throw (
                    "$Name $($rootCase.Name) root ($malformedType) raised: " +
                    $_.Exception.Message
                )
            }
            if ($rootAccepted) {
                throw "$Name proof validator accepted a $($rootCase.Name) JSON root."
            }
        }
    }

    $stopProof = [pscustomobject]@{
        schema_version = 1
        verified = $true
        run_id = $canaryRunId
        request_id = $expectedCanaryStopRequestId
        stop_request_id = $expectedCanaryStopRequestId
        resume_request_id = $requestId
        postgres_container_id = $postgresContainer[0]
        command = 'stop'
        actor = 'codex-paper-canary'
        reason = 'STRATUS PAPER canary terminal fail-closed stop.'
        payload = [pscustomobject]@{ venue = 'polymarket' }
        post_attempts = 2
        accepted_responses = 2
        exact_id_replays = 1
        initially_created = $true
        database_now = '2026-08-14T08:00:00Z'
        command_status = 'succeeded'
        matching_lease_terminal = $true
        matching_lease_state = 'released'
        active_canary_leases = 0
        pending_commands = 0
        pending_signals = 0
        live = [pscustomobject]@{
            orders = 0
            positions = 0
            intents = 0
            fill_legs = 0
            enrollments = 0
            outcomes = 0
        }
        controls = [pscustomobject]@{
            global = [pscustomobject]@{ mode = 'paper'; kill_switch = $true }
            polymarket = [pscustomobject]@{
                enabled = $true; mode = 'paper'; kill_switch = $true
            }
            binance_spot = [pscustomobject]@{
                enabled = $true; mode = 'backtest'; kill_switch = $true
            }
        }
    }
    if (-not (Test-ExactCanaryStopBarrierProof -Proof $stopProof)) {
        throw 'Native STOP proof fixture was rejected.'
    }
    Assert-ExternalProofRootShapesRejected -Name 'STOP' -Proof $stopProof
    foreach ($mutation in @(
        { param($proof) $proof.verified = 'false' },
        { param($proof) $proof.matching_lease_terminal = 'false' },
        { param($proof) $proof.initially_created = 'false' },
        { param($proof) $proof.post_attempts = $null },
        { param($proof) $proof.live.fill_legs = $null },
        { param($proof) $proof.controls.global.kill_switch = 'false' },
        { param($proof) $proof.controls.polymarket.enabled = 'true' },
        { param($proof) $proof.payload = @($proof.payload) },
        { param($proof) $proof.live = @($proof.live) },
        { param($proof) $proof.controls = @($proof.controls) },
        { param($proof) $proof.controls.global = @($proof.controls.global) },
        { param($proof) $proof.controls.polymarket = @($proof.controls.polymarket) },
        { param($proof) $proof.controls.binance_spot = @($proof.controls.binance_spot) }
    )) {
        $malformedSource = Copy-ProofFixture -Proof $stopProof
        & $mutation $malformedSource
        $malformed = Copy-ProofFixture -Proof $malformedSource
        if (Test-ExactCanaryStopBarrierProof -Proof $malformed) {
            throw 'STOP proof validator accepted a malformed native JSON type.'
        }
    }

    $readyProof = [pscustomobject]@{
        schema_version = 1
        validator = 'stratus_paper_canary_artifacts'
        verified = $true
        validated = $true
        mode = 'Ready'
        canary_run_id = $canaryRunId
        expected_resume_request_id = $requestId
        lease_id = "paper-lease-$canaryRunId"
        direct_stop_request_id = "paper-canary-stop-$canaryRunId"
        release_request_id = "paper-lease-release-$canaryRunId"
        offsets = @(0, 5, 15, 30, 45, 60)
        preflight_sha256 = 'd' * 64
        lease_acquire_sha256 = 'e' * 64
        ready_sha256 = 'f' * 64
        failure_evidence_absent = $true
        binance_baseline = [pscustomobject]@{
            enabled = $true
            mode = 'backtest'
            kill_switch = $true
        }
    }
    if (-not (Test-ExactCanaryReadyProof -Proof $readyProof)) {
        throw 'Native ready proof fixture was rejected.'
    }
    Assert-ExternalProofRootShapesRejected -Name 'Ready' -Proof $readyProof
    foreach ($mutation in @(
        { param($proof) $proof.schema_version = '1' },
        { param($proof) $proof.verified = 'false' },
        { param($proof) $proof.validated = $null },
        { param($proof) $proof.offsets[0] = '0' },
        { param($proof) $proof.failure_evidence_absent = 'true' },
        { param($proof) $proof.binance_baseline.kill_switch = $null },
        { param($proof) $proof.binance_baseline = @($proof.binance_baseline) }
    )) {
        $malformedSource = Copy-ProofFixture -Proof $readyProof
        & $mutation $malformedSource
        $malformed = Copy-ProofFixture -Proof $malformedSource
        if (Test-ExactCanaryReadyProof -Proof $malformed) {
            throw 'Ready proof validator accepted a malformed native JSON type.'
        }
    }

    $terminalProof = [pscustomobject]@{
        schema_version = 1
        validator = 'stratus_paper_canary_artifacts'
        verified = $true
        validated = $true
        terminal = $true
        mode = 'Terminal'
        canary_run_id = $canaryRunId
        expected_resume_request_id = $requestId
        lease_id = "paper-lease-$canaryRunId"
        direct_stop_request_id = "paper-canary-stop-$canaryRunId"
        release_request_id = "paper-lease-release-$canaryRunId"
        offsets = @(0, 5, 15, 30, 45, 60)
        preflight_sha256 = 'd' * 64
        lease_acquire_sha256 = 'e' * 64
        ready_sha256 = 'f' * 64
        safety_lease_released = $true
        terminal_runtime = 'polymarket_paper_fail_closed'
        manifest_sha256 = '1' * 64
        final_snapshot_sha256 = '2' * 64
        failure_evidence_absent = $true
    }
    if (-not (Test-ExactCanaryTerminalProof -Proof $terminalProof)) {
        throw 'Native terminal proof fixture was rejected.'
    }
    Assert-ExternalProofRootShapesRejected -Name 'Terminal' -Proof $terminalProof
    foreach ($mutation in @(
        { param($proof) $proof.terminal = 'false' },
        { param($proof) $proof.safety_lease_released = $null },
        { param($proof) $proof.offsets[5] = '60' },
        { param($proof) $proof.failure_evidence_absent = 'true' },
        { param($proof) $proof.manifest_sha256 = 111 }
    )) {
        $malformedSource = Copy-ProofFixture -Proof $terminalProof
        & $mutation $malformedSource
        $malformed = Copy-ProofFixture -Proof $malformedSource
        if (Test-ExactCanaryTerminalProof -Proof $malformed) {
            throw 'Terminal proof validator accepted a malformed native JSON type.'
        }
    }
}

Invoke-ExternalProofTypeFixtures

$resumeSubmitMatch = [regex]::Match(
    $source,
    '(?s)# STRATUS_RESUME_SUBMIT_BEGIN\s*\r?\n' +
        '(?<body>.*?)# STRATUS_RESUME_SUBMIT_END'
)
if (-not $resumeSubmitMatch.Success) {
    throw 'Release runbook is missing the executable bounded resume-submit block.'
}
$resumeSubmitBlock = [scriptblock]::Create(
    $resumeSubmitMatch.Groups['body'].Value
)
$canaryFailureCleanupMatch = [regex]::Match(
    $source,
    '(?s)# STRATUS_CANARY_FAILURE_CLEANUP_BEGIN\s*\r?\n' +
        '(?<body>.*?)# STRATUS_CANARY_FAILURE_CLEANUP_END'
)
if (-not $canaryFailureCleanupMatch.Success) {
    throw 'Release runbook is missing the exception-safe canary cleanup helper.'
}
$canaryFailureCleanupBlock = [scriptblock]::Create(
    $canaryFailureCleanupMatch.Groups['body'].Value
)
$canaryLifetimeScopeMatch = [regex]::Match(
    $source,
    '(?s)# STRATUS_CANARY_LIFETIME_SCOPE_BEGIN\s*\r?\n' +
        '(?<body>.*?)# STRATUS_CANARY_LIFETIME_SCOPE_END'
)
if (-not $canaryLifetimeScopeMatch.Success) {
    throw 'Release runbook is missing the outer canary lifetime scope.'
}
$canaryLifetimeScopeBlock = [scriptblock]::Create(
    $canaryLifetimeScopeMatch.Groups['body'].Value
)

function Invoke-ResumeSubmitFixture {
    param(
        [ValidateSet(
            'Success',
            'ResponseLostCommitted',
            'ResponseLostMalformedAudit',
            'Timeout',
            'Exception',
            'IdentityMismatch',
            'BarrierThrows',
            'CreatedStringFalse',
            'NullItemId',
            'AcceptanceNull',
            'AcceptanceString',
            'AcceptanceScalar',
            'AcceptanceSingletonArray',
            'ItemNull',
            'ItemString',
            'ItemScalar',
            'ItemSingletonArray'
        )]
        [string] $Scenario
    )

    $events = [Collections.Generic.List[string]]::new()
    $fixtureState = [pscustomobject]@{
        GetCalls = 0
        PostTimeouts = [Collections.Generic.List[int]]::new()
        GetTimeouts = [Collections.Generic.List[int]]::new()
    }
    $requestId = 'release-paper-fixture-00000001'
    $resumeActor = 'codex-release-owner'
    $resumeReason = (
        'Successor epoch and cold scheduler canary accepted; ' +
        'resume Polymarket PAPER only.'
    )
    $headers = @{ 'X-API-Key' = 'fixture-only' }
    $resumeBody = @{
        request_id = $requestId
        command = 'resume'
        reason = $resumeReason
        actor = $resumeActor
        payload = @{ venue = 'polymarket' }
    } | ConvertTo-Json -Depth 4 -Compress
    $canaryJob = [pscustomobject]@{ State = 'Running' }

    function New-FixtureResumeItem {
        param(
            [string] $Status,
            [AllowNull()][object] $ExecutedAt = $null,
            [string] $Actor = $resumeActor,
            [AllowNull()][object] $Id = 47
        )
        return [pscustomobject]@{
            id = $Id
            request_id = $requestId
            command = 'resume'
            payload = [pscustomobject]@{ venue = 'polymarket' }
            actor = $Actor
            reason = $resumeReason
            status = $Status
            detail = $null
            created_at = '2026-08-14T08:00:00Z'
            executed_at = $ExecutedAt
        }
    }

    function Invoke-VerifiedCanaryStopBarrier {
        [void]$events.Add('barrier')
        if ($Scenario -ceq 'BarrierThrows') {
            throw [TimeoutException]::new('fixture barrier failure')
        }
        return [pscustomobject]@{ verified = $true }
    }

    function Stop-Job {
        [CmdletBinding()]
        param([object] $Job)
        [void]$events.Add('stop-job')
        $Job.State = 'Stopped'
    }

    function Wait-Job {
        [CmdletBinding()]
        param([object] $Job, [int] $Timeout)
        [void]$events.Add("wait-job:$Timeout")
        return $Job
    }

    function Receive-Job {
        [CmdletBinding()]
        param([object] $Job, [switch] $Wait)
        [void]$events.Add('receive-job')
        return 'fixture collector output'
    }

    function Remove-Job {
        [CmdletBinding()]
        param([object] $Job, [switch] $Force)
        [void]$events.Add('remove-job')
    }

    function Start-Sleep {
        param([int] $Seconds)
        [void]$events.Add("sleep:$Seconds")
    }

    function Invoke-RestMethod {
        param(
            [string] $Method,
            [object] $Headers,
            [string] $ContentType,
            [string] $Body,
            [int] $TimeoutSec,
            [string] $Uri
        )
        if ($Method -ceq 'Post') {
            [void]$events.Add('post')
            [void]$fixtureState.PostTimeouts.Add($TimeoutSec)
            $validAcceptance = [pscustomobject]@{
                created = $true
                item = New-FixtureResumeItem -Status 'pending'
            }
            switch ($Scenario) {
                'Timeout' {
                    throw [TimeoutException]::new('fixture POST timeout')
                }
                'Exception' {
                    throw [InvalidOperationException]::new(
                        'fixture POST transport exception'
                    )
                }
                'ResponseLostCommitted' {
                    throw [IO.IOException]::new('fixture response lost')
                }
                'ResponseLostMalformedAudit' {
                    throw [IO.IOException]::new('fixture response lost')
                }
                'IdentityMismatch' {
                    return [pscustomobject]@{
                        created = $true
                        item = New-FixtureResumeItem -Status 'pending' `
                            -Actor 'wrong-actor'
                    }
                }
                'BarrierThrows' {
                    return [pscustomobject]@{
                        created = $true
                        item = New-FixtureResumeItem -Status 'pending' `
                            -Actor 'wrong-actor'
                    }
                }
                'CreatedStringFalse' {
                    return [pscustomobject]@{
                        created = 'false'
                        item = New-FixtureResumeItem -Status 'pending'
                    }
                }
                'NullItemId' {
                    return [pscustomobject]@{
                        created = $true
                        item = New-FixtureResumeItem -Status 'pending' -Id $null
                    }
                }
                'AcceptanceNull' {
                    return ConvertFrom-Json -InputObject 'null'
                }
                'AcceptanceString' {
                    return ConvertFrom-Json -InputObject '"proof"'
                }
                'AcceptanceScalar' {
                    return ConvertFrom-Json -InputObject '1'
                }
                'AcceptanceSingletonArray' {
                    $acceptanceJson = $validAcceptance |
                        ConvertTo-Json -Depth 8 -Compress
                    return ConvertFrom-Json -InputObject "[$acceptanceJson]"
                }
                'ItemNull' {
                    return ConvertFrom-Json -InputObject `
                        '{"created":true,"item":null}'
                }
                'ItemString' {
                    return ConvertFrom-Json -InputObject `
                        '{"created":true,"item":"proof"}'
                }
                'ItemScalar' {
                    return ConvertFrom-Json -InputObject `
                        '{"created":true,"item":1}'
                }
                'ItemSingletonArray' {
                    $itemArrayAcceptance = [pscustomobject]@{
                        created = $true
                        item = @(New-FixtureResumeItem -Status 'pending')
                    }
                    $acceptanceJson = $itemArrayAcceptance |
                        ConvertTo-Json -Depth 8 -Compress
                    return ConvertFrom-Json -InputObject $acceptanceJson
                }
                default {
                    return $validAcceptance
                }
            }
        }

        if ($Method -cne 'Get') {
            throw "Unexpected fixture HTTP method: $Method"
        }
        [void]$events.Add('get')
        [void]$fixtureState.GetTimeouts.Add($TimeoutSec)
        $fixtureState.GetCalls++
        switch ($Scenario) {
            'ResponseLostCommitted' {
                $item = if ($fixtureState.GetCalls -eq 1) {
                    New-FixtureResumeItem -Status 'pending'
                }
                else {
                    New-FixtureResumeItem -Status 'succeeded' `
                        -ExecutedAt '2026-08-14T08:00:20Z'
                }
                return [pscustomobject]@{ items = @($item) }
            }
            'ResponseLostMalformedAudit' {
                $auditEnvelope = [pscustomobject]@{
                    items = @(
                        New-FixtureResumeItem -Status 'succeeded' `
                            -ExecutedAt '2026-08-14T08:00:20Z'
                    )
                }
                $auditJson = $auditEnvelope | ConvertTo-Json -Depth 8 -Compress
                return ConvertFrom-Json -InputObject "[$auditJson]"
            }
            'Exception' {
                throw [IO.IOException]::new('fixture audit read exception')
            }
            default {
                return [pscustomobject]@{ items = @() }
            }
        }
    }

    $failure = $null
    try {
        . $externalProofValidatorsBlock
        . $canaryFailureCleanupBlock
        $canaryLifecycleGuard = New-CanaryLifecycleGuard
        . $resumeSubmitBlock
    }
    catch {
        $failure = $_.Exception.Message
    }
    return [pscustomobject]@{
        Scenario = $Scenario
        Events = @($events)
        Failure = $failure
        Accepted = $resumeAccepted
        GetCalls = [int]$fixtureState.GetCalls
        PostTimeouts = @($fixtureState.PostTimeouts)
        GetTimeouts = @($fixtureState.GetTimeouts)
    }
}

function Assert-ResumeFailureBarrierOrder {
    param(
        [Parameter(Mandatory = $true)][object] $Fixture,
        [Parameter(Mandatory = $true)][int] $ExpectedGetCalls
    )

    if ([string]::IsNullOrWhiteSpace([string]$Fixture.Failure)) {
        throw "Resume-submit $($Fixture.Scenario) fixture did not fail closed."
    }
    $barrierIndex = [Array]::IndexOf($Fixture.Events, 'barrier')
    $stopIndex = [Array]::IndexOf($Fixture.Events, 'stop-job')
    $receiveIndex = [Array]::IndexOf($Fixture.Events, 'receive-job')
    $removeIndex = [Array]::IndexOf($Fixture.Events, 'remove-job')
    if (
        $barrierIndex -lt 0 -or
        $stopIndex -le $barrierIndex -or
        $receiveIndex -le $barrierIndex -or
        $removeIndex -le $barrierIndex -or
        $Fixture.GetCalls -ne $ExpectedGetCalls
    ) {
        throw (
            "Resume-submit $($Fixture.Scenario) fixture did not prove STOP " +
            'before collector stop/receive with bounded reconciliation: ' +
            ($Fixture.Events -join ',')
        )
    }
}

$acceptedResume = Invoke-ResumeSubmitFixture -Scenario Success
if (
    $null -ne $acceptedResume.Failure -or
    ($acceptedResume.Events -join ',') -cne 'post' -or
    @($acceptedResume.PostTimeouts).Count -ne 1 -or
    [int]$acceptedResume.PostTimeouts[0] -ne 10 -or
    [string]$acceptedResume.Accepted.item.status -cne 'pending'
) {
    throw (
        'Bounded resume-submit fixture rejected an exact new pending acceptance: ' +
        ($acceptedResume | ConvertTo-Json -Depth 6 -Compress)
    )
}

$reconciledResume = Invoke-ResumeSubmitFixture -Scenario ResponseLostCommitted
if (
    $null -ne $reconciledResume.Failure -or
    $reconciledResume.GetCalls -ne 2 -or
    @($reconciledResume.GetTimeouts | Where-Object { $_ -ne 5 }).Count -ne 0 -or
    @($reconciledResume.Events | Where-Object { $_ -eq 'barrier' }).Count -ne 0 -or
    -not [bool]$reconciledResume.Accepted.reconciled_after_response_loss -or
    [string]$reconciledResume.Accepted.item.status -cne 'succeeded'
) {
    throw 'Resume response-loss fixture did not require exact succeeded audit evidence.'
}

$timeoutResume = Invoke-ResumeSubmitFixture -Scenario Timeout
Assert-ResumeFailureBarrierOrder -Fixture $timeoutResume -ExpectedGetCalls 6
if (
    @($timeoutResume.GetTimeouts).Count -ne 6 -or
    @($timeoutResume.GetTimeouts | Where-Object { $_ -ne 5 }).Count -ne 0
) {
    throw 'Resume timeout reconciliation was not bounded to six five-second reads.'
}

$exceptionResume = Invoke-ResumeSubmitFixture -Scenario Exception
Assert-ResumeFailureBarrierOrder -Fixture $exceptionResume -ExpectedGetCalls 0

$mismatchedResume = Invoke-ResumeSubmitFixture -Scenario IdentityMismatch
Assert-ResumeFailureBarrierOrder -Fixture $mismatchedResume -ExpectedGetCalls 0

$stringCreatedResume = Invoke-ResumeSubmitFixture -Scenario CreatedStringFalse
Assert-ResumeFailureBarrierOrder -Fixture $stringCreatedResume -ExpectedGetCalls 0

$nullIdResume = Invoke-ResumeSubmitFixture -Scenario NullItemId
Assert-ResumeFailureBarrierOrder -Fixture $nullIdResume -ExpectedGetCalls 0

foreach ($malformedResumeScenario in @(
    'AcceptanceNull',
    'AcceptanceString',
    'AcceptanceScalar',
    'AcceptanceSingletonArray',
    'ItemNull',
    'ItemString',
    'ItemScalar',
    'ItemSingletonArray'
)) {
    $malformedResume = Invoke-ResumeSubmitFixture `
        -Scenario $malformedResumeScenario
    Assert-ResumeFailureBarrierOrder -Fixture $malformedResume -ExpectedGetCalls 0
}

$malformedAuditResume = Invoke-ResumeSubmitFixture `
    -Scenario ResponseLostMalformedAudit
Assert-ResumeFailureBarrierOrder -Fixture $malformedAuditResume -ExpectedGetCalls 1

$barrierFailureResume = Invoke-ResumeSubmitFixture -Scenario BarrierThrows
Assert-ResumeFailureBarrierOrder -Fixture $barrierFailureResume -ExpectedGetCalls 0
if (
    [Array]::IndexOf($barrierFailureResume.Events, 'stop-job') -lt 0 -or
    [Array]::IndexOf($barrierFailureResume.Events, 'remove-job') -lt 0 -or
    [string]$barrierFailureResume.Failure -cnotmatch
        'Resume submission failed:.*barrier_error=fixture barrier failure'
) {
    throw (
        'A throwing STOP barrier left the fixture collector alive or lost ' +
        'the original/barrier errors: ' +
        ($barrierFailureResume | ConvertTo-Json -Depth 6 -Compress)
    )
}

function Invoke-CanaryLifetimeFailureFixture {
    param(
        [ValidateSet('AfterResume', 'DuringTerminalWait')]
        [string] $Scenario
    )

    $events = [Collections.Generic.List[string]]::new()
    function Invoke-VerifiedCanaryStopBarrier {
        [void]$events.Add('barrier')
        return [pscustomobject]@{ verified = $true }
    }
    function Stop-Job {
        [CmdletBinding()]
        param([object] $Job)
        [void]$events.Add('stop-job')
        $Job.State = 'Stopped'
    }
    function Wait-Job {
        [CmdletBinding()]
        param([object] $Job, [int] $Timeout)
        [void]$events.Add("wait-job:$Timeout")
        return $Job
    }
    function Receive-Job {
        [CmdletBinding()]
        param([object] $Job)
        [void]$events.Add('receive-job')
        return 'outer lifetime fixture output'
    }
    function Remove-Job {
        [CmdletBinding()]
        param([object] $Job, [switch] $Force)
        [void]$events.Add('remove-job')
    }

    . $canaryFailureCleanupBlock
    $canaryReadyPhase = {
        [void]$events.Add('start-job')
        $canaryJob = [pscustomobject]@{ State = 'Running' }
    }
    $canaryResumePhase = {
        [void]$events.Add('resume-complete')
        if ($Scenario -ceq 'AfterResume') {
            throw 'fixture failure after resume'
        }
    }
    $canaryTerminalPhase = {
        [void]$events.Add('terminal-wait')
        throw 'fixture failure during terminal wait'
    }

    $failure = $null
    try {
        . $canaryLifetimeScopeBlock
    }
    catch {
        $failure = $_.Exception.Message
    }
    return [pscustomobject]@{
        Scenario = $Scenario
        Events = @($events)
        Failure = $failure
        CleanupCompleted = [bool]$canaryLifecycleGuard.CleanupCompleted
        Succeeded = [bool]$canaryLifecycleGuard.Succeeded
    }
}

function Assert-CanaryLifetimeFailureCleanup {
    param([Parameter(Mandatory = $true)][object] $Fixture)

    $barrierIndex = [Array]::IndexOf($Fixture.Events, 'barrier')
    $stopIndex = [Array]::IndexOf($Fixture.Events, 'stop-job')
    $waitIndex = [Array]::IndexOf($Fixture.Events, 'wait-job:5')
    $receiveIndex = [Array]::IndexOf($Fixture.Events, 'receive-job')
    $removeIndex = [Array]::IndexOf($Fixture.Events, 'remove-job')
    foreach ($requiredEvent in @(
        'barrier', 'stop-job', 'wait-job:5', 'receive-job', 'remove-job'
    )) {
        if (@($Fixture.Events | Where-Object { $_ -ceq $requiredEvent }).Count -ne 1) {
            throw (
                "Outer $($Fixture.Scenario) fixture did not execute $requiredEvent " +
                'exactly once: ' + ($Fixture.Events -join ',')
            )
        }
    }
    if (
        [string]::IsNullOrWhiteSpace([string]$Fixture.Failure) -or
        $barrierIndex -lt 0 -or
        $stopIndex -le $barrierIndex -or
        $waitIndex -le $stopIndex -or
        $receiveIndex -le $waitIndex -or
        $removeIndex -le $receiveIndex -or
        -not [bool]$Fixture.CleanupCompleted -or
        [bool]$Fixture.Succeeded
    ) {
        throw (
            "Outer $($Fixture.Scenario) fixture bypassed cancellation-safe " +
            'cleanup or doubled diagnostics: ' +
            ($Fixture | ConvertTo-Json -Depth 6 -Compress)
        )
    }
}

$afterResumeLifetime = Invoke-CanaryLifetimeFailureFixture -Scenario AfterResume
Assert-CanaryLifetimeFailureCleanup -Fixture $afterResumeLifetime
if ([string]$afterResumeLifetime.Failure -cnotmatch 'fixture failure after resume') {
    throw 'Outer lifetime cleanup lost the post-resume failure.'
}

$terminalWaitLifetime = Invoke-CanaryLifetimeFailureFixture `
    -Scenario DuringTerminalWait
Assert-CanaryLifetimeFailureCleanup -Fixture $terminalWaitLifetime
if ([string]$terminalWaitLifetime.Failure -cnotmatch 'fixture failure during terminal wait') {
    throw 'Outer lifetime cleanup lost the terminal-wait failure.'
}

$hbaRuleMatches = @(
    [regex]::Matches(
        $source,
        "(?m)^\s*'RULE\|/etc/postgresql/stratus-pg_hba\.conf\|"
    )
)
if ($hbaRuleMatches.Count -ne 15) {
    throw "Release runbook must pin exactly 15 normalized HBA rules; found $($hbaRuleMatches.Count)."
}
$hbaComparison = @(
    [regex]::Matches(
        $source,
        '\(@\(\$hbaProof \| Select-Object -Skip 1\) -join "`n"\) -cne ' +
            '\(\$expectedHbaRules -join "`n"\)'
    )
)
if ($hbaComparison.Count -ne 1) {
    throw 'Release runbook HBA proof does not reject missing, extra, reordered, or changed rules.'
}
$fixedHbaFixture = 'a' * 64
$hbaByteProofs = @(
    [pscustomobject]@{ host_sha256 = $fixedHbaFixture; mounted_sha256 = $fixedHbaFixture },
    [pscustomobject]@{ host_sha256 = $fixedHbaFixture; mounted_sha256 = $fixedHbaFixture },
    [pscustomobject]@{ host_sha256 = ('b' * 64); mounted_sha256 = ('b' * 64) }
)
$toctouAccepted = @($hbaByteProofs | Where-Object {
    $_.host_sha256 -cne $fixedHbaFixture -or
    $_.mounted_sha256 -cne $fixedHbaFixture
}).Count -eq 0
if ($toctouAccepted) {
    throw 'HBA TOCTOU fixture accepted bytes changed after the loaded-rule query.'
}
$expectedHbaFixture = 1..15 | ForEach-Object { "rule-$_" }
$driftedHbaFixture = @($expectedHbaFixture)
$driftedHbaFixture[7] = 'rule-8-auth-drift'
if (
    ($expectedHbaFixture -join "`n") -ceq ($driftedHbaFixture -join "`n") -or
    ($expectedHbaFixture -join "`n") -ceq (@($expectedHbaFixture | Select-Object -Skip 1) -join "`n") -or
    ($expectedHbaFixture -join "`n") -ceq ((@($expectedHbaFixture) + 'extra-rule') -join "`n")
) {
    throw 'Exact HBA comparison fixture accepted changed, missing, or extra normalized rules.'
}

$runtimeProvisionIndex = $source.IndexOf(
    'docker compose @releaseComposeArguments --profile release run --rm --no-deps provision-runtime-role',
    [StringComparison]::Ordinal
)
$apiStageIndex = $source.IndexOf(
    'docker compose @releaseComposeArguments --profile release up --no-start --no-build --pull never --no-deps --force-recreate api',
    [StringComparison]::Ordinal
)
if (
    $runtimeProvisionIndex -lt 0 -or
    $apiStageIndex -le $runtimeProvisionIndex
) {
    throw (
        'Release runbook must activate and prove the runtime database role ' +
        'before staging the candidate API.'
    )
}
$hbaReloadIndex = $source.IndexOf('SELECT pg_reload_conf()', [StringComparison]::Ordinal)
$postgresStartIndex = $source.IndexOf(
    'docker start $postgresContainer[0]',
    [StringComparison]::Ordinal
)
$immediateHbaFinalByteProofIndex = $source.IndexOf(
    '$immediateHbaBytesAfterRules = Get-ImmediateReleaseHbaByteProof',
    [StringComparison]::Ordinal
)
$preMigrationQueryIndex = $source.IndexOf(
    '$preMigrationHead = (',
    [StringComparison]::Ordinal
)
if (
    $postgresStartIndex -lt 0 -or
    $immediateHbaFinalByteProofIndex -le $postgresStartIndex -or
    $preMigrationQueryIndex -le $immediateHbaFinalByteProofIndex
) {
    throw (
        'The fixed HBA byte/reload/rules/TOCTOU proof must complete ' +
        'immediately after PostgreSQL start and before any migration query.'
    )
}
$hbaFinalByteProofIndex = $source.IndexOf(
    '$hbaBytesAfterRules = Get-FixedReleaseHbaByteProof',
    [StringComparison]::Ordinal
)
if (
    $hbaReloadIndex -lt 0 -or
    $hbaFinalByteProofIndex -le $hbaReloadIndex -or
    $runtimeProvisionIndex -le $hbaFinalByteProofIndex
) {
    throw 'Fixed HBA reload/rules/TOCTOU proof must complete before runtime-role admission.'
}

$preMigrationCreateGateIndex = $source.IndexOf(
    '-Mode Create',
    [StringComparison]::Ordinal
)
$migrationStartIndex = $source.IndexOf(
    'docker start $migrateContainer',
    [StringComparison]::Ordinal
)
if (
    $preMigrationCreateGateIndex -lt 0 -or
    $migrationStartIndex -le $preMigrationCreateGateIndex
) {
    throw (
        'Release runbook must accept the create-new backup/restore/live-state ' +
        'gate immediately before starting migration DDL.'
    )
}

$safetyReadyIndex = $source.IndexOf(
    '$safetyReady = Invoke-RestMethod',
    [StringComparison]::Ordinal
)
$workerStartIndex = $source.IndexOf(
    'docker start $workerContainer[0]',
    [StringComparison]::Ordinal
)
if (
    $safetyReadyIndex -lt 0 -or
    $workerStartIndex -le $safetyReadyIndex
) {
    throw (
        'Release runbook must prove safety-control exact readiness before ' +
        'starting the candidate worker.'
    )
}

$safetyStageIndex = $source.IndexOf(
    'docker compose @releaseComposeArguments --profile release up --no-start --no-build --pull never --no-deps --force-recreate safety-control',
    [StringComparison]::Ordinal
)
$postgresSafetyAttachIndex = $source.IndexOf(
    'docker network connect --alias postgres',
    $safetyStageIndex,
    [StringComparison]::Ordinal
)
$safetyNetworkIdentityIndex = $source.IndexOf(
    '[string]$safetyNetworks[0] -cne [string]$expectedSafetyNetworks[0]',
    $safetyStageIndex,
    [StringComparison]::Ordinal
)
$safetyStartIndex = $source.IndexOf(
    'docker start $safetyContainer[0]',
    $postgresSafetyAttachIndex,
    [StringComparison]::Ordinal
)
if (
    $safetyStageIndex -lt 0 -or
    $safetyNetworkIdentityIndex -le $safetyStageIndex -or
    $postgresSafetyAttachIndex -le $safetyNetworkIdentityIndex -or
    $safetyStartIndex -le $postgresSafetyAttachIndex
) {
    throw (
        'Release runbook must attach and prove the retained PostgreSQL ' +
        'safety-plane endpoint before safety-control starts.'
    )
}

$rollbackHeadingIndex = $source.IndexOf(
    '## 10. Safe rollback',
    [StringComparison]::Ordinal
)
$rollbackSafetyStopIndex = $source.IndexOf(
    '$rollbackStop = Invoke-RestMethod',
    [StringComparison]::Ordinal
)
$rollbackWorkerStopIndex = $source.IndexOf(
    'docker compose @releaseComposeArguments stop worker',
    $rollbackHeadingIndex,
    [StringComparison]::Ordinal
)
if (
    $rollbackHeadingIndex -lt 0 -or
    $rollbackSafetyStopIndex -le $rollbackHeadingIndex -or
    $rollbackWorkerStopIndex -le $rollbackSafetyStopIndex
) {
    throw (
        'Rollback must prove the independent safety STOP before stopping ' +
        'the worker or staging an API-only predecessor.'
    )
}

$collectorIndex = $source.IndexOf(
    '$canaryJob = Start-Job',
    [StringComparison]::Ordinal
)
$readyValidationIndex = $source.IndexOf(
    '$canaryReadyProof = & $canaryArtifactValidator',
    $collectorIndex,
    [StringComparison]::Ordinal
)
$resumeSubmitIndex = $source.IndexOf(
    '$resumeAccepted = Invoke-RestMethod',
    [StringComparison]::Ordinal
)
if (
    $collectorIndex -lt 0 -or
    $readyValidationIndex -le $collectorIndex -or
    $resumeSubmitIndex -le $readyValidationIndex
) {
    throw (
        'Release runbook must start the collector, validate its lease-bound ' +
        'ready marker, and only then submit the exact resume.'
    )
}

$canaryArgumentStart = $source.IndexOf(
    '-ArgumentList @(',
    $collectorIndex,
    [StringComparison]::Ordinal
)
$canaryArgumentEnd = $source.IndexOf(
    'Wait-Job -Job $canaryJob -Timeout 1',
    $canaryArgumentStart,
    [StringComparison]::Ordinal
)
if ($canaryArgumentStart -lt 0 -or $canaryArgumentEnd -le $canaryArgumentStart) {
    throw 'Release runbook does not expose the bounded canary job argument block.'
}
$canaryArguments = $source.Substring(
    $canaryArgumentStart,
    $canaryArgumentEnd - $canaryArgumentStart
)
if ($canaryArguments -match '(?i)operatorApiKey|safetyApiKey|STRATUS_.*API_KEY') {
    throw 'Release runbook places a control-plane secret in canary job arguments.'
}

$failureCleanupSource = $canaryFailureCleanupMatch.Groups['body'].Value
$cleanupBarrierIndex = $failureCleanupSource.IndexOf(
    '$null = Invoke-VerifiedCanaryStopBarrier',
    [StringComparison]::Ordinal
)
$cleanupFinallyIndex = $failureCleanupSource.IndexOf(
    'finally {',
    $cleanupBarrierIndex,
    [StringComparison]::Ordinal
)
$cleanupStopIndex = $failureCleanupSource.IndexOf(
    'Stop-Job -Job $canaryJob -ErrorAction Stop',
    $cleanupFinallyIndex,
    [StringComparison]::Ordinal
)
$cleanupWaitIndex = $failureCleanupSource.IndexOf(
    'Wait-Job -Job $canaryJob -Timeout 5 -ErrorAction Stop',
    $cleanupStopIndex,
    [StringComparison]::Ordinal
)
$cleanupReceiveIndex = $failureCleanupSource.IndexOf(
    'Receive-Job -Job $canaryJob -ErrorAction Stop',
    $cleanupWaitIndex,
    [StringComparison]::Ordinal
)
$cleanupRemoveIndex = $failureCleanupSource.IndexOf(
    'Remove-Job -Job $canaryJob -Force -ErrorAction Stop',
    $cleanupReceiveIndex,
    [StringComparison]::Ordinal
)
if (
    $cleanupBarrierIndex -lt 0 -or
    $cleanupFinallyIndex -le $cleanupBarrierIndex -or
    $cleanupStopIndex -le $cleanupFinallyIndex -or
    $cleanupWaitIndex -le $cleanupStopIndex -or
    $cleanupReceiveIndex -le $cleanupWaitIndex -or
    $cleanupRemoveIndex -le $cleanupReceiveIndex -or
    $failureCleanupSource -match
        'Receive-Job\s+-Job\s+\$canaryJob[^\r\n]*-Wait'
) {
    throw (
        'Canary failure cleanup must attempt the barrier first, guarantee ' +
        'Stop-Job from finally, and only then perform bounded receive/remove.'
    )
}
$failureCleanupCalls = @(
    [regex]::Matches(
        $source,
        '(?m)^\s*Stop-CanaryJobAfterBarrierAttempt\s+`'
    )
)
if ($failureCleanupCalls.Count -ne 9) {
    throw (
        'Every Section 9 canary failure path must use the shared ' +
        "exception-safe cleanup helper; found $($failureCleanupCalls.Count)."
    )
}

$readyPhaseMatch = [regex]::Match(
    $source,
    '(?s)# STRATUS_CANARY_READY_PHASE_BEGIN\s*\r?\n' +
        '(?<body>.*?)# STRATUS_CANARY_READY_PHASE_END'
)
$terminalPhaseMatch = [regex]::Match(
    $source,
    '(?s)# STRATUS_CANARY_TERMINAL_PHASE_BEGIN\s*\r?\n' +
        '(?<body>.*?)# STRATUS_CANARY_TERMINAL_PHASE_END'
)
$lifetimeScopeSource = $canaryLifetimeScopeMatch.Groups['body'].Value
$lifetimeTryIndex = $lifetimeScopeSource.IndexOf(
    'try {',
    [StringComparison]::Ordinal
)
$lifetimeReadyIndex = $lifetimeScopeSource.IndexOf(
    '. $canaryReadyPhase',
    $lifetimeTryIndex,
    [StringComparison]::Ordinal
)
$lifetimeResumeIndex = $lifetimeScopeSource.IndexOf(
    '. $canaryResumePhase',
    $lifetimeReadyIndex,
    [StringComparison]::Ordinal
)
$lifetimeTerminalIndex = $lifetimeScopeSource.IndexOf(
    '. $canaryTerminalPhase',
    $lifetimeResumeIndex,
    [StringComparison]::Ordinal
)
$lifetimeFinallyIndex = $lifetimeScopeSource.IndexOf(
    'finally {',
    $lifetimeTerminalIndex,
    [StringComparison]::Ordinal
)
$lifetimeCleanupIndex = $lifetimeScopeSource.IndexOf(
    'Stop-CanaryJobAfterBarrierAttempt',
    $lifetimeFinallyIndex,
    [StringComparison]::Ordinal
)
$phaseInvocationCounts = @(
    [regex]::Matches(
        $source,
        '(?m)^\s*\. \$canary(?:Ready|Resume|Terminal)Phase\s*$'
    )
)
if (
    -not $readyPhaseMatch.Success -or
    -not $terminalPhaseMatch.Success -or
    $readyPhaseMatch.Groups['body'].Value -notmatch
        '\$canaryJob = Start-Job' -or
    $resumeSubmitMatch.Groups['body'].Value -notmatch
        'Invoke-RestMethod -Method Post' -or
    $terminalPhaseMatch.Groups['body'].Value -notmatch
        'Wait-Job -Job \$canaryJob -Timeout \$remainingCanarySeconds' -or
    $terminalPhaseMatch.Groups['body'].Value -notmatch
        '\$canaryLifecycleGuard\.Succeeded = \$true' -or
    $phaseInvocationCounts.Count -ne 3 -or
    $lifetimeTryIndex -lt 0 -or
    $lifetimeReadyIndex -le $lifetimeTryIndex -or
    $lifetimeResumeIndex -le $lifetimeReadyIndex -or
    $lifetimeTerminalIndex -le $lifetimeResumeIndex -or
    $lifetimeFinallyIndex -le $lifetimeTerminalIndex -or
    $lifetimeCleanupIndex -le $lifetimeFinallyIndex
) {
    throw (
        'The outer try/finally must exclusively span Start-Job through exact ' +
        'resume, terminal wait, terminal receive/remove, and success flag.'
    )
}

$terminalDeadlineIndex = $source.IndexOf(
    '$canaryTerminalDeadlineUtc = $canaryLaunchedAtUtc.AddSeconds(3900)',
    [StringComparison]::Ordinal
)
$terminalWaitIndex = $source.IndexOf(
    'Wait-Job -Job $canaryJob -Timeout $remainingCanarySeconds',
    $resumeSubmitIndex,
    [StringComparison]::Ordinal
)
$terminalManifestIndex = $source.IndexOf(
    '$canaryTerminalProof = & $canaryArtifactValidator',
    $terminalWaitIndex,
    [StringComparison]::Ordinal
)
$terminalBarrierIndex = $source.IndexOf(
    '$terminalBarrierProof = Invoke-VerifiedCanaryStopBarrier',
    $terminalWaitIndex,
    [StringComparison]::Ordinal
)
$rollbackHeadingIndexForCanary = $source.IndexOf(
    '## 10. Safe rollback',
    [StringComparison]::Ordinal
)
if (
    $terminalDeadlineIndex -lt 0 -or
    $terminalDeadlineIndex -ge $collectorIndex -or
    $terminalWaitIndex -le $resumeSubmitIndex -or
    $terminalBarrierIndex -le $terminalWaitIndex -or
    $terminalManifestIndex -le $terminalBarrierIndex -or
    $rollbackHeadingIndexForCanary -le $terminalManifestIndex
) {
    throw (
        'Release runbook must join the canary, prove its fail-closed terminal ' +
        'manifest, and only then continue to rollback guidance.'
    )
}

[pscustomobject]@{
    runbook = $runbookPath
    exact_digest_initialized = $true
    dependency_safe_migration_staging = $true
    portable_background_host = $true
    runtime_preflight_marker_required = $true
    continuity_plan_recomputed = $true
    rollback_container_resolved = $true
    safety_control_required = $true
    safety_before_worker_required = $true
    lease_before_resume_required = $true
    bounded_resume_submission_required = $true
    exception_safe_canary_cleanup_required = $true
    outer_canary_lifetime_scope_required = $true
    native_external_proof_types_required = $true
    terminal_stop_barrier_required = $true
    validated = $true
} | ConvertTo-Json -Compress

$global:LASTEXITCODE = 0
