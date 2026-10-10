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
$powerShellAsts = [Collections.Generic.List[Management.Automation.Language.ScriptBlockAst]]::new()
for ($blockIndex = 0; $blockIndex -lt $powerShellBlocks.Count; $blockIndex++) {
    $tokens = $null
    $parseErrors = $null
    $blockAst = [Management.Automation.Language.Parser]::ParseInput(
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
    $powerShellAsts.Add($blockAst)
}

foreach ($forbidden in @(
    'docker compose create',
    'Start-Process -FilePath "pwsh"',
    'docker inspect weatherpoly-worker-1',
    'expected_plan_hash = \$plan\.plan_hash',
    '\$secureApiKey',
    '(?m)^\s*\.\\scripts\\Invoke-StratusDashboardLoadGate\.ps1',
    '(?m)^\s*\.\\scripts\\Assert-StratusLoadGateArtifacts\.ps1',
    '(?m)^\s*\.\\scripts\\Assert-StratusReleaseImage\.ps1',
    '\-Mode Resume',
    '(?m)^\s+-Mode (?:PreStart|Live)\s+`\r?$',
    '\$script:release(?:Docker|Git)Executable'
)) {
    if ($source -match $forbidden) {
        throw "Release runbook contains an unsafe or non-portable command: $forbidden"
    }
}
foreach ($required in @(
    'docker compose @releaseComposeArguments stop backup',
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
    '\$global:StratusReleaseDockerExecutable',
    '\$global:StratusReleaseGitExecutable',
    '\$global:StratusReleaseComposePlugin',
    '\$global:StratusExpectedReleaseDockerSha256',
    '\$global:StratusExpectedReleaseGitSha256',
    '\$global:StratusExpectedReleaseComposeSha256',
    'git -C \$productionRoot status --porcelain=v1 --untracked-files=all',
    '\$productionRevision -ne \$revision',
    'Production checkout must be the exact clean release revision',
    'git -C \$buildContext rev-parse HEAD',
    'git -C \$buildContext status --porcelain=v1 --untracked-files=all',
    'git -C \$buildContext symbolic-ref -q --short HEAD',
    '--tag \$image \$buildContext',
    'Join-Path \$buildContext "scripts\\Assert-StratusReleaseImage\.ps1"',
    'Join-Path \$buildContext "scripts\\New-StratusR1AcceptanceSeal\.ps1"',
    'stratus-r1-seal-result-v3',
    'Get-R1SealRegularBytes',
    'Accepted R1 directory contains an extra or missing artifact',
    'R1 stdout hashes do not match the published bytes',
    'Published R1 acceptance/seal/reservation contract did not survive strict readback',
    'Assert-R1SealNonReparseDirectoryChain',
    '\$expectedSuccessorAttemptName',
    '\$r1Acceptance\.candidate\.source_aggregate_sha256 -cne',
    'source-manifest\.tsv',
    '\$r1Seal\.input_manifest_sha256 -cne',
    '\$r1Reservation\.attempt_id -cne',
    '\.stratus-cache\\release-transitions\\r1-\$revision\.accepted',
    'ACCEPTED_R1_PREREQUISITE_PARENT_STAGE_PENDING',
    'requires_parent_stage_watchdog_acceptance',
    'Assert-R1SealPrerequisiteAuthority',
    'Assert-R1SealDockerCollector',
    'Assert-R1SealGitProof',
    'STRATUS_R1_EVIDENCE_INPUT_DIRECTORY',
    '\$releaseState = "R1_PREREQUISITE_ACCEPTED_PARENT_STAGE_PENDING"',
    'Section 3 requires the durable exact-revision R1 acceptance seal',
    'Section 3 remains blocked until the crash-resilient parent-stage watchdog',
    'Invoke-StratusParentStageWatchdog\.ps1',
    'owner PID plus owner process start time',
    'two-party create-new request/acknowledgement',
    'input-manifest\.tsv',
    'stratus-r0-exact-sha-ci-evidence-v3',
    'stratus-local-image-inspect-v4',
    'stratus-build-command-v4',
    'stratus-buildx-history-v4',
    'stratus-r1-stopped-topology-v4',
    'tracked_bytes_match_tree_blobs',
    'replacement_refs_absent',
    'build_context_entries_exact',
    'root_git_entry_docker_excluded',
    'https://github\.com/GrggrT/stratus\.git',
    'bounded non-interactive live `ls-remote` proof',
    'sole authority',
    'Exact candidate image build failed',
    'STRATUS_CUTOVER_SCHEMA_PATH',
    '\$cutoverSchemaPath -cnotin @\(',
    '"20260730_0046"',
    '"20260730_0047"',
    '\$cutoverSchemaPath -ceq "20260730_0046"',
    '\$cutoverSchemaPath -ceq "20260730_0047"',
    'Test-Path -LiteralPath "Env:\$_"',
    'The 20260730_0046 production cutover path is disabled before PostgreSQL start',
    'The 0047 Transition path requires all four immutable lineage/evidence paths',
    'Every attempt-scoped repair/Transition evidence path must be absent',
    'Schema 0047 successor is accepted; no migrate container was staged or started',
    'That\s+migration is historical and must never be repeated on this path',
    'Do not create, stage, start, resume, or otherwise invoke a migrate container',
    'backups/stratus_pre_0047_20260822T195653Z_d4c5ce2295714d439bf4f0fb21191547\.backup',
    'a8717ba0656bc0f5abde739dd0352284f48f598f89597866c55bc7627d97d189',
    '\.stratus-cache/cutovers/section3-6d093b49cd53-20260822T193939Z-c70786a5a00349ca93e9c1d53ca7f3d7\.in-progress',
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
    'Assert-StratusPostMigrationSuccessorGate\.ps1',
    '-Mode Transition',
    'STRATUS_PRE_MIGRATION_BACKUP_DIRECTORY',
    'STRATUS_PRE_MIGRATION_ATTESTATION',
    'STRATUS_SUCCESSOR_LINEAGE_DIRECTORY',
    'STRATUS_SUCCESSOR_EVIDENCE_DIRECTORY',
    '\$cutoverAttemptId = \[guid\]::NewGuid\(\)\.ToString\("N"\)',
    'stopped-postgres-network-repair-\$\(\$revision\.Substring\(0, 12\)\)-\$cutoverAttemptId\.json',
    'post-migration-successor-prestart-\$\(\$revision\.Substring\(0, 12\)\)-\$cutoverAttemptId\.json',
    'post-migration-successor-live-\$\(\$revision\.Substring\(0, 12\)\)-\$cutoverAttemptId\.json',
    '\$successorPreStartAttestation \+ "\.watchdog-lease\.json"',
    '\$successorPreStartAttestation \+ "\.watchdog-terminal\.json"',
    '\$successorPreStartAttestation \+ "\.watchdog-heartbeat\.json"',
    '\$successorPreStartAttestation \+ "\.watchdog-commit\.json"',
    '\$successorPreStartAttestation \+ "\.watchdog-start-fence\.lock"',
    '\$successorPreStartAttestation \+ "\.watchdog-start-revoked\.json"',
    '\$successorPreStartAttestation \+ "\.watchdog-start-attempt\.json"',
    '\$successorPreStartAttestation \+ "\.watchdog-start-complete\.json"',
    '\$successorPreStartAttestation \+ "\.parent-containment\.json"',
    '\$successorPreStartAttestation \+ "\.parent-authorization\.json"',
    'All thirteen paths must be below the production root, pairwise distinct, and\s+absent',
    '\$attemptScopedEvidencePaths\.Count -ne 13',
    'Attempt-scoped repair/Transition evidence paths must be pairwise distinct',
    'Attempt-scoped evidence path escaped the production root',
    'Invoke-StratusStoppedPostgresNetworkRepair\.ps1',
    '-NetworkRepairAttestationPath \$networkRepairAttestation',
    'stratus-stopped-postgres-safety-plane-repair-v2',
    'stratus-stopped-repair-docker-event-witness-v1',
    'verified_configured_pending_existing',
    'continuous_docker_event_history_exact',
    'post_attachment_admissible_twice',
    'A zero-byte reserved repair file means the mutation was not accepted',
    'Only the already-`0047` Transition path may accept that form',
    'stratus-post-migration-successor-transition-live-v2',
    '-PreStartAttestationPath \$successorPreStartAttestation',
    '-LiveAcceptancePath \$successorLiveAcceptance',
    'bounded exact\s+PostgreSQL stop and re-inspection',
    'terminal failure lineage',
    'new\s+transition session',
    'functions_exact',
    'transition_session\.mode',
    'event_witness\.postgres_start_count',
    'event_witness\.postgres_restart_policy_update_count',
    'event_witness\.healthy_status_event_count',
    'event_witness\.postgres_attach_count',
    'event_witness\.network_connect_count',
    'event_witness\.data_volume_mount_count',
    'event_witness\.gate_exec_triplets',
    'event_witness\.health_exec_triplets',
    'event_witness\.reconnect_count',
    'event_witness\.eof_before_seal',
    'event_witness\.truncated',
    'rollback\.on_failure',
    'rollback\.stop_timeout_seconds',
    'rollback\.executed',
    'rollback\.emergency_containment',
    'restart_policy_transition',
    'restore_on_success',
    'watchdog_program',
    '407ff7e0ef2806725d906d968a293040e5105f89a7bb320d0e6f03f185bb5539',
    'watchdog_handoff',
    'watchdog_lease',
    'heartbeat_at_live',
    'Live heartbeat_at_live" -Expected @\("sequence", "written_at", "sha256"\)',
    'start_proof',
    'stratus-successor-transition-watchdog-handoff-v3',
    'stratus-successor-transition-watchdog-binding-v3',
    'stratus-successor-transition-watchdog-lease-v3',
    'stratus-successor-transition-watchdog-heartbeat-v1',
    'stratus-successor-transition-watchdog-commit-v1',
    'stratus-successor-transition-watchdog-terminal-v1',
    'accepted_live_commit',
    'Non-authorizing watchdog terminal receipt did not prove the exact Live commit',
    'PreStart/Live network_repair is not byte-identically bound to disk evidence',
    '\$successorLiveRepairBinding\.accepted_at -cne',
    '\$networkRepairExpiresAt -ne \$networkRepairAcceptedAt\.AddSeconds\(600\)',
    'compose_executable_sha256',
    'v2\.38\.2-desktop\.1',
    'Successor owner/watchdog Docker engine contracts are not exactly cross-bound',
    '\$successorWatchdogTerminalProof\.status -cne "success"',
    '\$successorWatchdogTerminalProof\.trigger -cne "accepted_live_commit"',
    '\$successorWatchdogTerminalProof\.authorization\.api_stage_permitted -ne \$false',
    '\$successorTransitionGate\.payload\.authorization\.api_stage_permitted -ne \$false',
    '\$successorWatchdogTerminalProof\.commit\.postgres_started_at -cne',
    '\$successorWatchdogTerminalProof\.commit\.docker_engine_contract_sha256 -cne',
    '\$successorEventWitness\.postgres_restart_policy_update_count -ne 1',
    '\$successorEventWitness\.postgres_start_count -ne 1',
    '\$successorEventWitness\.healthy_status_event_count -ne 1',
    '\$successorExpectedGateSequence = @\(',
    '\$successorApiStagePermitted = \[bool\]\(',
    '\$successorParentAuthorizationProof\.authorization\.api_stage_permitted',
    'Assert-SuccessorParentApiAuthorizationUnchanged',
    'Path-only recovery is forbidden; direct parent API authorization drifted',
    'Parent API authorization requires an exact PostgreSQL-only topology',
    'stratus-successor-transition-parent-authorization-v1',
    '\$successorTransitionReturnedNormally = \$true',
    '\$successorTransitionOutput\.Count -ne 1',
    '\$successorTransitionOutput\[0\].*-cne',
    '\$successorParentAuthorizationDocument\.json',
    '\$successorParentAuthorizationSha256',
    '\$successorParentValidUntil',
    '\$successorWatchdogStartRevoke',
    'authorization_witness',
    '\$currentAuthorizedPostgres\[0\]\.State\.StartedAt -cne',
    '\$successorWatchdogTerminalProof\.commit\.postgres_started_at',
    'weatherpoly_default", "weatherpoly_safety-plane',
    'reload_acknowledged',
    'fresh_loaded_state\.conf_load_time',
    'final_boundary\.fresh_hba_state\.conf_load_time',
    'migration_container_permitted',
    '\$runtimeProvisionContainerName = "stratus-runtime-role-\$cutoverAttemptId"',
    'attempt-bound runtime-role provision',
    'Invoke-PostTransitionFailClosedCleanup',
    'Invoke-BoundedPinnedReleaseDockerProcess',
    '\$nativeArguments = @\("--context", "desktop-linux"\)',
    'MaximumStdoutCharacters = 1048576',
    'MaximumStderrCharacters = 262144',
    'refresh=\$\(\$_\.Exception\.Message\)',
    'identity=\$\(\$_\.Exception\.Message\)',
    'kill=\$\(\$_\.Exception\.Message\)',
    'Get-HeldReleaseExecutableSha256',
    'Open-ReleaseExecutableReadLock',
    'Invoke-BoundedExactCandidateApiStop',
    '-DockerArguments @\("stop", "--timeout", "15", \$ContainerId\)',
    'Invoke-BoundedExactRetainedPostgresContainment',
    'Parent containment evidence path already exists and cannot be trusted or reused',
    'Get-RetainedPostgresStaticContainmentSha256',
    'unexpected_post_containment_topology',
    'Refusing cleanup because the API is not the exact admitted candidate identity',
    '\[string\]\$cleanupApi\.Config\.Labels\.\x27com\.docker\.compose\.service\x27 -cne "api"',
    '\$HbaReadLock\.Dispose\(\)',
    'Cleanup never stops or starts the worker or `safety-control`',
    'ix_signals_pending_stop_cleanup',
    'ix_orders_live_open_stop_cleanup',
    'exactly five rows and ten `true` flags',
    'docker compose @releaseComposeArguments --profile release run --rm --no-deps validate-safety-credentials',
    'docker compose @releaseComposeArguments --profile release run --rm --no-deps provision-safety-role',
    'must not repair ACL, RLS, trigger, function, or ownership drift',
    '43-256 character URL-safe high-diversity random values',
    'passwords must be pairwise different',
    '164276f8a7337705c3933e3201cb53a5a10e354ec7627768c4ac8b3e951380a4',
    'e6b26bdffeba11e892100ce05f855db8ede8ce407a24fbef6144d2a458cc3567',
    '\$successorTransitionGateRecheck\.payload\.hba\.reload_acknowledged -ne \$true',
    'Post-Transition 0047 HBA bytes, receipt, or fail-closed identity drifted',
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
    'Retained PostgreSQL safety-plane attachment or identity proof failed',
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
    'Stop-BoundedExactGateObserver -Process \$observer',
    '\$observerStartedAtUtcTicks',
    '\$observerExecutablePath',
    'R3_ACCEPTED_PARENT_WATCHDOG_HANDOFF',
    '& \$hostPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass',
    'Join-Path \$buildContext "scripts\\Invoke-StratusDashboardLoadGate\.ps1"',
    'Join-Path \$buildContext "scripts\\Assert-StratusLoadGateArtifacts\.ps1"',
    '# STRATUS_LOAD_GATE_COMPOSITE_FINALIZATION_BEGIN',
    '\$rawArtifactsComplete = \(',
    '\$compositeArtifactRecorded = Test-Path -LiteralPath \$acceptedArtifact -PathType Leaf',
    'milestone = "LOAD_GATE_COMPOSITE_FINALIZED"',
    'http_exit=\$httpExit; runtime_exit=\$observerExit; assertion_exit=\$assertionExit',
    '\$observerExit = 124',
    '\$observerTimedOut = \$true',
    '\$observerStopFailed = \$true',
    'observer_timed_out = \$observerTimedOut',
    'observer_stop_failed = \$observerStopFailed',
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
    'function ConvertFrom-ExactCanaryJson',
    '\$proofJson = & \$canaryStopBarrier',
    '\$proof = ConvertFrom-ExactCanaryJson -Json \$proofJson',
    '\$canaryReadyJson = & \$canaryArtifactValidator',
    '\$canaryReadyProof = ConvertFrom-ExactCanaryJson -Json \$canaryReadyJson',
    '\$canaryTerminalJson = & \$canaryArtifactValidator',
    '\$canaryTerminalProof = ConvertFrom-ExactCanaryJson -Json \$canaryTerminalJson',
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

# STRATUS_R1_CONSUMER_FIXTURES_BEGIN
function Invoke-R1ConsumerFixtures {
    $r1Blocks = @($powerShellAsts | Where-Object {
        $_.Extent.Text -match 'function Assert-R1SealDockerCollector'
    })
    if ($r1Blocks.Count -ne 1) { throw 'Exactly one R1 consumer block is required.' }
    $r1Ast = $r1Blocks[0]
    foreach ($name in @(
        'ConvertFrom-R1SealJson', 'Assert-R1SealExactProperties', 'Assert-R1SealJsonString',
        'Assert-R1SealJsonBoolean', 'Assert-R1SealJsonInteger', 'Assert-R1SealJsonArray',
        'ConvertFrom-R1SealCanonicalUtcTimestamp', 'Assert-R1SealPrerequisiteAuthority',
        'Assert-R1SealDockerCollector', 'Assert-R1SealGitProof'
    )) {
        $definitions = @($r1Ast.FindAll({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -ceq $name
        }, $true))
        if ($definitions.Count -ne 1) { throw "R1 fixture needs one function: $name" }
        . ([scriptblock]::Create($definitions[0].Extent.Text))
    }
    function Assert-R1ConsumerRejection {
        param([scriptblock]$Action, [string]$Label)
        $rejected = $false
        try { & $Action } catch { $rejected = $true }
        if (-not $rejected) { throw "R1 consumer accepted drift: $Label" }
    }
    $authority = [pscustomobject]@{
        section3_authorized = $false
        requires_parent_stage_watchdog_acceptance = $true
    }
    Assert-R1SealPrerequisiteAuthority -Value $authority
    foreach ($change in @(
        @{ name = 'section3_authorized'; value = $true },
        @{ name = 'section3_authorized'; value = 'false' },
        @{ name = 'requires_parent_stage_watchdog_acceptance'; value = $false },
        @{ name = 'requires_parent_stage_watchdog_acceptance'; value = 'true' },
        @{ name = 'requires_parent_stage_watchdog_acceptance'; value = $null }
    )) {
        $mutant = ConvertFrom-R1SealJson -Json ($authority | ConvertTo-Json)
        $mutant.($change.name) = $change.value
        Assert-R1ConsumerRejection { Assert-R1SealPrerequisiteAuthority -Value $mutant } `
            -Label "authority $($change.name)=$($change.value)"
    }
    $authorityCalls = @($r1Ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.CommandAst] -and
            $node.GetCommandName() -ceq 'Assert-R1SealPrerequisiteAuthority'
    }, $true))
    if ($authorityCalls.Count -ne 3 -or
        $r1Ast.Extent.Text -match 'ACCEPTED_R1_SECTION3_NOT_STARTED' -or
        $r1Ast.Extent.Text -match 'section3_authorized\s+-ne\s+\$true' -or
        $r1Ast.Extent.Text -match
            'retained_postgres_start_permitted_only_inside_single_process_transition\s+-ne\s+\$true'
    ) { throw 'R1 consumer must reject start authority in result, acceptance, and seal.' }

    $revision = 'a' * 40
    $image = 'stratus:candidate-' + $revision.Substring(0, 12)
    $buildContext = 'C:\fixture\build'
    $r1BuildHistory = [pscustomobject]@{ build_ref = 'fixture-builder/default/build' }
    foreach ($variable in @('r1DockerBindingProperties', 'r1DockerCollectorContracts')) {
        $assignments = @($r1Ast.FindAll({
            param($node)
            $node -is [Management.Automation.Language.AssignmentStatementAst] -and
                $node.Left.Extent.Text -ceq ('$' + $variable)
        }, $true))
        if ($assignments.Count -ne 1) { throw "R1 fixture needs one assignment: $variable" }
        . ([scriptblock]::Create($assignments[0].Extent.Text))
    }
    if ($r1DockerCollectorContracts.Count -ne 4) { throw 'R1 requires four Docker-bound inputs.' }
    $dockerPath = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    $dockerHash = 'b' * 64
    $dockerCases = 0
    foreach ($contract in $r1DockerCollectorContracts) {
        $properties = @($contract.properties + $r1DockerBindingProperties)
        $fixture = [ordered]@{}
        foreach ($name in $properties) { $fixture[$name] = 'fixture' }
        $fixture.format = $contract.format
        $fixture.docker_executable_path = $dockerPath
        $fixture.docker_executable_sha256 = $dockerHash
        $fixture.docker_context = 'desktop-linux'
        $fixture.docker_endpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
        $fixture.collector = $contract.collector
        $fixture.collector_argv = @($contract.argv)
        $valid = ConvertFrom-R1SealJson -Json ($fixture | ConvertTo-Json -Depth 10)
        $arguments = @{
            ExpectedFormat = $contract.format; ExpectedProperties = $properties
            ExpectedCollector = $contract.collector; ExpectedArgv = $contract.argv
            ExpectedDockerPath = $dockerPath; ExpectedDockerSha256 = $dockerHash
        }
        Assert-R1SealDockerCollector -Value $valid @arguments
        foreach ($change in @(
            @{ name = 'format'; value = ($contract.format -replace '-v4$', '-v3') },
            @{ name = 'docker_executable_path'; value = 'C:\other\docker.exe' },
            @{ name = 'docker_executable_sha256'; value = ('c' * 64) },
            @{ name = 'docker_context'; value = 'default' },
            @{ name = 'docker_endpoint'; value = 'npipe:////./pipe/docker_engine' },
            @{ name = 'collector'; value = 'different-collector' },
            @{ name = 'collector_argv'; value = 'not-an-array' },
            @{ name = 'collector_argv'; value = @($contract.argv + '--extra') }
        )) {
            $mutant = ConvertFrom-R1SealJson -Json ($valid | ConvertTo-Json -Depth 10)
            $mutant.($change.name) = $change.value
            Assert-R1ConsumerRejection { Assert-R1SealDockerCollector -Value $mutant @arguments } `
                -Label "$($contract.name) $($change.name)"
            $dockerCases++
        }
        $mutant = ConvertFrom-R1SealJson -Json ($valid | ConvertTo-Json -Depth 10)
        $mutant.collector_argv[0] = $contract.argv[1]
        $mutant.collector_argv[1] = $contract.argv[0]
        Assert-R1ConsumerRejection { Assert-R1SealDockerCollector -Value $mutant @arguments } `
            -Label "$($contract.name) argv order"
        $mutant = ConvertFrom-R1SealJson -Json ($valid | ConvertTo-Json -Depth 10)
        $mutant.PSObject.Properties.Remove('docker_context')
        Assert-R1ConsumerRejection { Assert-R1SealDockerCollector -Value $mutant @arguments } `
            -Label "$($contract.name) missing binding"
        $mutant = ConvertFrom-R1SealJson -Json ($valid | ConvertTo-Json -Depth 10)
        $mutant | Add-Member -NotePropertyName extra_binding -NotePropertyValue 'unexpected'
        Assert-R1ConsumerRejection { Assert-R1SealDockerCollector -Value $mutant @arguments } `
            -Label "$($contract.name) extra binding"
        $dockerCases += 3
    }
    $gitFlags = @('production_checkout_clean', 'replacement_refs_absent',
        'dangerous_git_configuration_absent', 'mutable_git_attributes_absent',
        'tracked_bytes_match_tree_blobs', 'live_remote_repository_config_absent',
        'build_worktree_clean', 'build_worktree_detached', 'build_worktree_ignored_entries_absent',
        'build_context_entries_exact', 'root_git_entry_docker_excluded')
    $gitFixture = [ordered]@{
        format = 'stratus-r0-git-evidence-v3'; captured_at_utc = [DateTime]::UtcNow.ToString('o')
        production_root = 'C:\fixture\production'; revision = $revision; tree = ('d' * 40)
        branch = 'codex/release-baseline-20260729'; upstream = 'origin/codex/release-baseline-20260729'
        remote_url = 'https://github.com/GrggrT/stratus.git'
        remote_ref = 'refs/remotes/origin/codex/release-baseline-20260729'
        remote_revision = $revision; ahead = 0; behind = 0
        build_worktree = $buildContext; build_worktree_revision = $revision
    }
    foreach ($name in $gitFlags) { $gitFixture[$name] = $true }
    $gitProof = ConvertFrom-R1SealJson -Json ($gitFixture | ConvertTo-Json)
    $gitArguments = @{
        ExpectedRevision = $revision; ExpectedTree = ('d' * 40)
        ExpectedProductionRoot = 'C:\fixture\production'; ExpectedBuildWorktree = $buildContext
    }
    Assert-R1SealGitProof -Value $gitProof @gitArguments
    foreach ($name in $gitFlags) {
        foreach ($bad in @($false, 'true', $null)) {
            $mutant = ConvertFrom-R1SealJson -Json ($gitProof | ConvertTo-Json)
            $mutant.$name = $bad
            Assert-R1ConsumerRejection { Assert-R1SealGitProof -Value $mutant @gitArguments } `
                -Label "Git/source $name=$bad"
        }
    }
    [pscustomobject]@{
        prerequisite_authority_rejections = 5
        docker_binding_rejections = $dockerCases
        git_flag_rejections = $gitFlags.Count * 3
    }
}
$r1ConsumerFixtures = Invoke-R1ConsumerFixtures
# STRATUS_R1_CONSUMER_FIXTURES_END

$loadGateFinalizationMatch = [regex]::Match(
    $source,
    '(?s)# STRATUS_LOAD_GATE_COMPOSITE_FINALIZATION_BEGIN\s*\r?\n' +
        '(?<body>.*?)# STRATUS_LOAD_GATE_COMPOSITE_FINALIZATION_END'
)
if (-not $loadGateFinalizationMatch.Success) {
    throw 'Release runbook is missing the load-gate composite finalization block.'
}
$loadGateFinalizationBlock = [scriptblock]::Create(
    $loadGateFinalizationMatch.Groups['body'].Value
)

$script:LoadGateFixtureAssertionExit = 0
$script:LoadGateFixtureCompositePassed = $true
$script:LoadGateFixtureSuppressArtifact = $false
$script:LoadGateFixtureMalformedArtifact = $false
$script:LoadGateFixtureArrayArtifact = $false
$script:LoadGateFixtureAssertionCalls = 0

function Invoke-LoadGateFixtureHost {
    [CmdletBinding()]
    param(
        [switch] $NoProfile,
        [switch] $NonInteractive,
        [string] $ExecutionPolicy,
        [string] $File,
        [string] $HttpArtifactPath,
        [string] $RuntimeArtifactPath,
        [string] $ExpectedRunId,
        [string] $ExpectedApiImageId,
        [string] $ExpectedRevision,
        [string] $ExpectedReleaseDigest,
        [string] $OutputPath
    )

    $script:LoadGateFixtureAssertionCalls += 1
    if (-not $script:LoadGateFixtureSuppressArtifact) {
        if ($script:LoadGateFixtureMalformedArtifact) {
            [IO.File]::WriteAllText($OutputPath, '{"production_gate_passed":')
        }
        else {
            $artifact = [pscustomobject]@{
                schema_version = 1
                gate = 'stratus_candidate_production_load'
                production_gate_passed = $script:LoadGateFixtureCompositePassed
                exit_code = $script:LoadGateFixtureAssertionExit
                run_id = $ExpectedRunId
                candidate = [pscustomobject]@{
                    api_image_id = $ExpectedApiImageId
                    revision = $ExpectedRevision
                    release_digest = $ExpectedReleaseDigest
                }
            }
            $json = $artifact | ConvertTo-Json -Depth 4 -Compress
            if ($script:LoadGateFixtureArrayArtifact) {
                $json = '[' + $json + ']'
            }
            [IO.File]::WriteAllText(
                $OutputPath,
                $json,
                [Text.UTF8Encoding]::new($false)
            )
        }
    }
    $global:LASTEXITCODE = $script:LoadGateFixtureAssertionExit
}

function Invoke-LoadGateFinalizationFixture {
    param(
        [int] $HttpExit,
        [int] $RuntimeExit,
        [int] $AssertionExit,
        [bool] $CompositePassed,
        [switch] $MissingHttpArtifact,
        [switch] $MissingRuntimeArtifact,
        [switch] $SuppressCompositeArtifact,
        [switch] $MalformedCompositeArtifact,
        [switch] $ArrayCompositeArtifact,
        [switch] $ObserverTimedOut,
        [switch] $ObserverStopFailed
    )

    $fixtureRoot = Join-Path (
        [IO.Path]::GetTempPath()
    ) ("stratus-load-gate-finalization-" + [guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $fixtureRoot)
    try {
        $httpArtifact = Join-Path $fixtureRoot 'candidate-http.json'
        $runtimeArtifact = Join-Path $fixtureRoot 'candidate-runtime.json'
        $acceptedArtifact = Join-Path $fixtureRoot 'candidate-accepted.json'
        if (-not $MissingHttpArtifact) {
            [IO.File]::WriteAllText($httpArtifact, '{"http_passed":false}')
        }
        if (-not $MissingRuntimeArtifact) {
            [IO.File]::WriteAllText($runtimeArtifact, '{"runtime_passed":false}')
        }

        $observer = [pscustomobject]@{ ExitCode = $RuntimeExit }
        $observerExit = $RuntimeExit
        $httpInvocationFailed = $false
        $observerTimedOut = [bool]$ObserverTimedOut
        $observerStopFailed = [bool]$ObserverStopFailed
        $hostPowerShell = 'Invoke-LoadGateFixtureHost'
        $buildContext = $fixtureRoot
        $runId = 'a' * 32
        $digest = 'b' * 64
        $revision = 'c' * 40
        $script:LoadGateFixtureAssertionExit = $AssertionExit
        $script:LoadGateFixtureCompositePassed = $CompositePassed
        $script:LoadGateFixtureSuppressArtifact = [bool]$SuppressCompositeArtifact
        $script:LoadGateFixtureMalformedArtifact = [bool]$MalformedCompositeArtifact
        $script:LoadGateFixtureArrayArtifact = [bool]$ArrayCompositeArtifact
        $script:LoadGateFixtureAssertionCalls = 0
        $failure = $null
        $loadGateOutcome = $null
        try {
            $null = . $loadGateFinalizationBlock
        }
        catch {
            $failure = $_.Exception.Message
        }

        $compositeArtifact = $null
        if (Test-Path -LiteralPath $acceptedArtifact -PathType Leaf) {
            try {
                $compositeArtifact = Get-Content -LiteralPath $acceptedArtifact -Raw |
                    ConvertFrom-Json -ErrorAction Stop
            }
            catch {
                $compositeArtifact = $null
            }
        }
        return [pscustomobject]@{
            failure = $failure
            assertion_calls = $script:LoadGateFixtureAssertionCalls
            composite_artifact_exists = Test-Path -LiteralPath $acceptedArtifact -PathType Leaf
            composite_artifact = $compositeArtifact
            outcome = $loadGateOutcome
        }
    }
    finally {
        Remove-Item -LiteralPath $fixtureRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$rejectedLoadGate = Invoke-LoadGateFinalizationFixture `
    -HttpExit 1 -RuntimeExit 1 -AssertionExit 1 -CompositePassed $false
if (
    $rejectedLoadGate.assertion_calls -ne 1 -or
    -not $rejectedLoadGate.composite_artifact_exists -or
    [bool]$rejectedLoadGate.composite_artifact.production_gate_passed -or
    $rejectedLoadGate.failure -notlike '*http_exit=1; runtime_exit=1; assertion_exit=1*' -or
    $rejectedLoadGate.outcome.http_exit -ne 1 -or
    $rejectedLoadGate.outcome.runtime_exit -ne 1 -or
    $rejectedLoadGate.outcome.assertion_exit -ne 1 -or
    -not $rejectedLoadGate.outcome.raw_artifacts_complete -or
    -not $rejectedLoadGate.outcome.composite_attempted -or
    -not $rejectedLoadGate.outcome.composite_artifact_recorded -or
    -not $rejectedLoadGate.outcome.composite_contract_valid -or
    $rejectedLoadGate.outcome.production_gate_passed
) {
    throw (
        'A failed load subgate did not retain its exits and durable rejected ' +
        'composite artifact.'
    )
}

$independentlyFailedLoadGate = Invoke-LoadGateFinalizationFixture `
    -HttpExit 1 -RuntimeExit 0 -AssertionExit 0 -CompositePassed $true
if (
    $independentlyFailedLoadGate.assertion_calls -ne 1 -or
    -not $independentlyFailedLoadGate.composite_artifact_exists -or
    $independentlyFailedLoadGate.failure -notlike '*http_exit=1; runtime_exit=0; assertion_exit=0*'
) {
    throw 'A non-zero subgate exit was not independently preserved fail closed.'
}

$timedOutObserverLoadGate = Invoke-LoadGateFinalizationFixture `
    -HttpExit 0 -RuntimeExit 124 -AssertionExit 1 -CompositePassed $false `
    -ObserverTimedOut
if (
    $timedOutObserverLoadGate.assertion_calls -ne 1 -or
    -not $timedOutObserverLoadGate.composite_artifact_exists -or
    $timedOutObserverLoadGate.failure -notlike
        '*http_exit=0; runtime_exit=124; assertion_exit=1*' -or
    -not $timedOutObserverLoadGate.outcome.observer_timed_out -or
    $timedOutObserverLoadGate.outcome.observer_stop_failed
) {
    throw 'A timed-out observer bypassed durable composite finalization.'
}

$missingRawLoadGate = Invoke-LoadGateFinalizationFixture `
    -HttpExit 1 -RuntimeExit 2 -AssertionExit 0 -CompositePassed $true `
    -MissingRuntimeArtifact
if (
    $missingRawLoadGate.assertion_calls -ne 0 -or
    $missingRawLoadGate.composite_artifact_exists -or
    $missingRawLoadGate.failure -notlike '*raw evidence is incomplete*' -or
    $missingRawLoadGate.outcome.raw_artifacts_complete -or
    $missingRawLoadGate.outcome.composite_attempted
) {
    throw 'Incomplete raw evidence did not remain fail closed without assertion.'
}

$missingCompositeLoadGate = Invoke-LoadGateFinalizationFixture `
    -HttpExit 0 -RuntimeExit 0 -AssertionExit 2 -CompositePassed $false `
    -SuppressCompositeArtifact
if (
    $missingCompositeLoadGate.assertion_calls -ne 1 -or
    $missingCompositeLoadGate.composite_artifact_exists -or
    $missingCompositeLoadGate.failure -notlike '*did not record a valid result*'
) {
    throw 'A missing composite result artifact did not remain fail closed.'
}

$malformedCompositeLoadGate = Invoke-LoadGateFinalizationFixture `
    -HttpExit 0 -RuntimeExit 0 -AssertionExit 2 -CompositePassed $false `
    -MalformedCompositeArtifact
if (
    $malformedCompositeLoadGate.assertion_calls -ne 1 -or
    -not $malformedCompositeLoadGate.composite_artifact_exists -or
    $malformedCompositeLoadGate.failure -notlike '*did not record a valid result*' -or
    -not $malformedCompositeLoadGate.outcome.composite_artifact_recorded -or
    $malformedCompositeLoadGate.outcome.composite_contract_valid -or
    [string]::IsNullOrWhiteSpace(
        [string]$malformedCompositeLoadGate.outcome.composite_sha256
    )
) {
    throw 'A malformed composite result did not retain its fail-closed milestone.'
}

$arrayCompositeLoadGate = Invoke-LoadGateFinalizationFixture `
    -HttpExit 0 -RuntimeExit 0 -AssertionExit 0 -CompositePassed $true `
    -ArrayCompositeArtifact
if (
    $arrayCompositeLoadGate.assertion_calls -ne 1 -or
    -not $arrayCompositeLoadGate.composite_artifact_exists -or
    $arrayCompositeLoadGate.failure -notlike '*did not record a valid result*' -or
    -not $arrayCompositeLoadGate.outcome.composite_artifact_recorded -or
    $arrayCompositeLoadGate.outcome.composite_contract_valid -or
    $arrayCompositeLoadGate.outcome.production_gate_passed
) {
    throw 'A singleton-array composite root was not rejected consistently.'
}

$acceptedLoadGate = Invoke-LoadGateFinalizationFixture `
    -HttpExit 0 -RuntimeExit 0 -AssertionExit 0 -CompositePassed $true
if (
    $null -ne $acceptedLoadGate.failure -or
    $acceptedLoadGate.assertion_calls -ne 1 -or
    -not $acceptedLoadGate.composite_artifact_exists -or
    -not $acceptedLoadGate.outcome.production_gate_passed
) {
    throw 'A fully accepted load gate did not complete composite finalization.'
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

    $multipleOutputRejected = $false
    try {
        [void](ConvertFrom-ExactCanaryJson -Json @('{"proof":1}', '{"proof":2}'))
    }
    catch {
        $multipleOutputRejected = (
            $_.Exception.Message -like '*must emit exactly one JSON string*'
        )
    }
    if (-not $multipleOutputRejected) {
        throw 'External proof parser accepted multiple success-output objects.'
    }

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
            $malformedProof = ConvertFrom-ExactCanaryJson -Json $rootCase.Json
            if (
                $rootCase.Name -ceq 'singleton-array' -and
                (
                    $malformedProof -isnot [array] -or
                    @($malformedProof).Count -ne 1
                )
            ) {
                throw 'External proof parser did not preserve the singleton-array fixture.'
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
                    return ConvertFrom-ExactCanaryJson -Json 'null'
                }
                'AcceptanceString' {
                    return ConvertFrom-ExactCanaryJson -Json '"proof"'
                }
                'AcceptanceScalar' {
                    return ConvertFrom-ExactCanaryJson -Json '1'
                }
                'AcceptanceSingletonArray' {
                    $acceptanceJson = $validAcceptance |
                        ConvertTo-Json -Depth 8 -Compress
                    return ConvertFrom-ExactCanaryJson -Json "[$acceptanceJson]"
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
                return ConvertFrom-ExactCanaryJson -Json "[$auditJson]"
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

$runtimeProvisionIndex = $source.IndexOf(
    '$runtimeProvisionResult = Invoke-BoundedReleaseDockerCommand',
    [StringComparison]::Ordinal
)
$apiStageIndex = $source.IndexOf(
    '$apiStageResult = Invoke-BoundedReleaseDockerCommand',
    [StringComparison]::Ordinal
)
$apiStartIndex = $source.IndexOf(
    'Invoke-BoundedExactCandidateApiStart -ContainerId $candidateApiCleanupId',
    [StringComparison]::Ordinal
)
$apiCleanupIdentityIndex = $source.IndexOf(
    '$candidateApiCleanupId = [string]$apiContainer[0]',
    [StringComparison]::Ordinal
)
$apiFirstInspectIndex = $source.IndexOf(
    '$apiInspect = Get-BoundedReleaseDockerInspect',
    [StringComparison]::Ordinal
)
$parentApiAuthorizationCallMatches = @(
    [regex]::Matches(
        $source,
        '(?m)^\s*Assert-SuccessorParentApiAuthorizationUnchanged\s*$'
    )
)
if (
    $runtimeProvisionIndex -lt 0 -or
    $apiStageIndex -le $runtimeProvisionIndex -or
    $apiCleanupIdentityIndex -le $apiStageIndex -or
    $apiFirstInspectIndex -le $apiCleanupIdentityIndex -or
    $apiStartIndex -le $apiStageIndex
) {
    throw (
        'Release runbook must activate and prove the runtime database role ' +
        'before staging the candidate API, and must retain its exact ID before inspection.'
    )
}
function Test-AstInsideExactIfClause {
    param(
        [Parameter(Mandatory = $true)]
        [Management.Automation.Language.Ast] $Node,

        [Parameter(Mandatory = $true)]
        [Management.Automation.Language.ScriptBlockAst] $Root,

        [Parameter(Mandatory = $true)]
        [string] $ConditionPattern
    )

    $matchingIfs = @(
        $Root.FindAll({
            param($candidate)
            $candidate -is [Management.Automation.Language.IfStatementAst]
        }, $true)
    )
    foreach ($ifAst in $matchingIfs) {
        foreach ($clause in $ifAst.Clauses) {
            $condition = $clause.Item1
            $body = $clause.Item2
            if (
                $condition.Extent.Text -match $ConditionPattern -and
                $Node.Extent.StartOffset -ge $body.Extent.StartOffset -and
                $Node.Extent.EndOffset -le $body.Extent.EndOffset
            ) {
                return $true
            }
        }
    }
    return $false
}

function Test-AstInsideCatchClause {
    param(
        [Parameter(Mandatory = $true)]
        [Management.Automation.Language.Ast] $Node
    )

    $parent = $Node.Parent
    while ($null -ne $parent) {
        if ($parent -is [Management.Automation.Language.CatchClauseAst]) {
            return $true
        }
        $parent = $parent.Parent
    }
    return $false
}

function Test-AstCommandExactExpectedStrings {
    param(
        [Parameter(Mandatory = $true)]
        [Management.Automation.Language.CommandAst] $Command,

        [Parameter(Mandatory = $true)]
        [string[]] $Expected
    )

    $expectedSorted = @($Expected | Sort-Object)
    $arrays = @(
        $Command.FindAll({
            param($node)
            $node -is [Management.Automation.Language.ArrayExpressionAst]
        }, $true)
    )
    foreach ($array in $arrays) {
        $actual = @(
            $array.FindAll({
                param($node)
                $node -is [Management.Automation.Language.StringConstantExpressionAst]
            }, $true) |
                ForEach-Object { [string]$_.Value } |
                Sort-Object
        )
        if (
            $actual.Count -eq $expectedSorted.Count -and
            ($actual -join "`n") -ceq ($expectedSorted -join "`n")
        ) {
            return $true
        }
    }
    return $false
}

$cutoverBlocks = @(
    for ($index = 0; $index -lt $powerShellBlocks.Count; $index++) {
        $blockText = $powerShellBlocks[$index].Groups[1].Value
        if (
            $blockText -match '\$cutoverSchemaPath\s*=' -and
            $blockText -match '\$successorTransitionOutput\s*=\s*@\(\s*&'
        ) {
            [pscustomobject]@{
                Text = $blockText
                Ast = $powerShellAsts[$index]
            }
        }
    }
)
if ($cutoverBlocks.Count -ne 1) {
    throw 'Expected exactly one fenced PowerShell cutover block with Transition admission.'
}
$cutoverBlock = $cutoverBlocks[0]
$cutoverAst = $cutoverBlock.Ast
$cutoverAssignments = @(
    $cutoverAst.FindAll({
        param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst]
    }, $true)
)
$cutoverIfs = @(
    $cutoverAst.FindAll({
        param($node)
        $node -is [Management.Automation.Language.IfStatementAst]
    }, $true)
)
$cutoverCommands = @(
    $cutoverAst.FindAll({
        param($node)
        $node -is [Management.Automation.Language.CommandAst]
    }, $true)
)
$cutoverFunctions = @(
    $cutoverAst.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst]
    }, $true)
)
$boundedApiStopFunctions = @(
    $cutoverFunctions | Where-Object {
        $_.Name -ceq 'Invoke-BoundedExactCandidateApiStop'
    }
)
$boundedDockerProcessFunctions = @(
    foreach ($blockAst in $powerShellAsts) {
        $blockAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
              $node.Name -ceq 'Invoke-BoundedPinnedReleaseDockerProcess'
        }, $true)
    }
)
$boundedObserverStopFunctions = @(
    $cutoverFunctions | Where-Object {
        $_.Name -ceq 'Stop-BoundedExactGateObserver'
    }
)
$boundedProvisionCleanupFunctions = @(
    $cutoverFunctions | Where-Object {
        $_.Name -ceq 'Invoke-BoundedExactRuntimeProvisionCleanup'
    }
)
$boundedPostgresCleanupFunctions = @(
    $cutoverFunctions | Where-Object {
        $_.Name -ceq 'Invoke-BoundedExactRetainedPostgresContainment'
    }
)
$boundedObserverStopContractComplete = $false
if ($boundedObserverStopFunctions.Count -eq 1) {
    $observerStopText = $boundedObserverStopFunctions[0].Extent.Text
    $boundedObserverStopContractComplete = @(
        @(
            '\[Diagnostics\.Process\]\$Process',
            '\$ExpectedProcessId',
            '\$ExpectedStartedAtUtcTicks',
            '\$ExpectedExecutablePath',
            '\$Process\.StartTime\.ToUniversalTime\(\)\.Ticks',
            '\$Process\.MainModule\.FileName',
            '\$Process\.Kill\(\)',
            '\$Process\.WaitForExit\(10000\)',
            '\$Process\.Dispose\(\)'
        ) | Where-Object { $observerStopText -notmatch $_ }
    ).Count -eq 0 -and $observerStopText -notmatch 'Stop-Process'
}
$boundedProvisionCleanupContractComplete = $false
$namedContainerInventoryFunctions = @(
    $cutoverFunctions | Where-Object {
        $_.Name -ceq 'Get-BoundedExactNamedContainerIds'
    }
)
$namedContainerInventoryComplete = $false
if ($namedContainerInventoryFunctions.Count -eq 1) {
    $inventoryText = $namedContainerInventoryFunctions[0].Extent.Text
    $namedContainerInventoryComplete =
      $inventoryText.Contains('"name=^/$ContainerName`$"') -and
      $inventoryText -match 'Invoke-BoundedReleaseDockerCommand' -and
      $inventoryText -match '"container",\s*"ls",\s*"--all",\s*"--quiet",\s*"--no-trunc"' -and
      $inventoryText -match 'malformed identity'
}
if ($boundedProvisionCleanupFunctions.Count -eq 1) {
    $provisionCleanupText = $boundedProvisionCleanupFunctions[0].Extent.Text
    $boundedProvisionCleanupContractComplete = @(
        @(
            'Get-BoundedExactNamedContainerIds',
            'provision-runtime-role',
            'com\.docker\.compose\.oneoff',
            'HostConfig\.AutoRemove',
            'RestartPolicy\.Name\s+-cne\s+"no"',
            'Invoke-BoundedReleaseDockerCommand',
            '"stop",\s*"--timeout",\s*"15",\s*\$provisionId',
            'remainingProvisionIds'
        ) | Where-Object { $provisionCleanupText -notmatch $_ }
    ).Count -eq 0 -and $namedContainerInventoryComplete
}
$boundedPostgresCleanupContractComplete = $false
if ($boundedPostgresCleanupFunctions.Count -eq 1) {
    $postgresCleanupText = $boundedPostgresCleanupFunctions[0].Extent.Text
    $boundedPostgresCleanupContractComplete = @(
        @(
            'Get-RetainedPostgresStaticContainmentSha256',
            '\$observedStartedAt',
            '\$Identity\.pre_start_started_at',
            '\$stoppedPostgres\.State\.StartedAt\s+-cne\s+\$observedStartedAt',
            '\$postgres\.Config\.Image\s+-cne\s+\[string\]\$Identity\.config_image',
            'com\.docker\.compose\.project',
            'com\.docker\.compose\.service',
            'com\.docker\.compose\.oneoff',
            '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2',
            '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620',
            '"update",\s*"--restart",\s*"no",\s*\$postgresId',
            '"stop",\s*"--timeout",\s*"15",\s*\$postgresId',
            'pre-containment running-container inventory',
            'post-containment running-container inventory',
            'RestartPolicy\.Name\s+-cne\s+"no"',
            '\[int\]\$stoppedPostgres\.State\.Pid\s+-ne\s+0',
            'Write-RunbookParentContainmentEvidence'
        ) | Where-Object { $postgresCleanupText -notmatch $_ }
    ).Count -eq 0
}
$postTransitionCleanupFunctions = @(
    $cutoverFunctions | Where-Object {
        $_.Name -ceq 'Invoke-PostTransitionFailClosedCleanup'
    }
)
$allPowerShellCommands = @(
    foreach ($blockAst in $powerShellAsts) {
        $blockAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.CommandAst]
        }, $true)
    }
)
$postTransitionCleanupCalls = @(
    $allPowerShellCommands | Where-Object {
        $_.GetCommandName() -ceq 'Invoke-PostTransitionFailClosedCleanup'
    }
)
$postTransitionCleanupCallsAllCaught = $true
foreach ($cleanupCall in $postTransitionCleanupCalls) {
    if (
        -not (Test-AstInsideCatchClause -Node $cleanupCall) -or
        @(
            @(
                '-CandidateApiContainerId', '-RuntimeProvisionContainerName',
                '-ObserverProcess', '-ObserverProcessId', '-ObserverStartedAtUtcTicks',
                '-ObserverExecutablePath', '-RetainedPostgresIdentity',
                '-ContainRetainedPostgres:', '-ParentContainmentPath',
                '-CleanupTrigger', '-HbaReadLock\s+\$runbookHbaReadLock'
            ) | Where-Object { $cleanupCall.Extent.Text -notmatch $_ }
        ).Count -ne 0
    ) {
        $postTransitionCleanupCallsAllCaught = $false
    }
}
$boundedApiStopContractComplete = $false
if ($boundedApiStopFunctions.Count -eq 1) {
    $boundedStopText = $boundedApiStopFunctions[0].Extent.Text
    $boundedApiStopContractComplete = @(
        @(
            'ValidatePattern\(''\^\[0-9a-f\]\{64\}\$''\)',
            'Invoke-BoundedReleaseDockerCommand',
            '-DockerArguments\s+@\("stop",\s*"--timeout",\s*"15",\s*\$ContainerId\)',
            '-WallClockTimeoutMilliseconds\s+22000',
            '\$stopResult\.stdout\s+-cne\s+\$ContainerId',
            '\[string\]::IsNullOrWhiteSpace\(\$stopResult\.stderr\)'
        ) | Where-Object { $boundedStopText -notmatch $_ }
    ).Count -eq 0
}
$boundedDockerProcessContractComplete = $false
if ($boundedDockerProcessFunctions.Count -eq 1) {
    $boundedProcessText = $boundedDockerProcessFunctions[0].Extent.Text
    $boundedDockerProcessContractComplete = @(
        @(
            '\$nativeArguments\s*=\s*@\("--context",\s*"desktop-linux"\)',
            'Assert-ReleaseExecutableBoundary',
            '\$startInfo\.FileName\s*=\s*\$global:StratusReleaseDockerExecutable',
            '\$startInfo\.UseShellExecute\s*=\s*\$false',
            '\$startInfo\.RedirectStandardOutput\s*=\s*\$true',
            '\$startInfo\.RedirectStandardError\s*=\s*\$true',
            'MaximumStdoutCharacters\s*=\s*1048576',
            'MaximumStderrCharacters\s*=\s*262144',
            '\.ReadAsync\(',
            '\$processExitDeadline',
            '\$pipeDrainDeadline',
            '\$startedAtUtcTicks',
            '\$startedExecutablePath',
            '\$processWasObservedExited',
            'processCleanupFailures\s*\+=\s*"identity=',
            '\$process\.Kill\(\)',
            'processCleanupFailures\s*\+=\s*"kill=',
            '\$process\.WaitForExit\(2000\)',
            '\$processCleanupFailures',
            'exact process cleanup was incomplete',
            'finally\s*\{(?s:.*?)\$process\.Dispose\(\)'
        ) | Where-Object { $boundedProcessText -notmatch $_ }
    ).Count -eq 0 -and
      $boundedProcessText -notmatch 'ReadToEndAsync' -and
      $boundedProcessText -notmatch 'catch\s*\{\s*\}'
}
$postTransitionCleanupContractComplete = $false
if ($postTransitionCleanupFunctions.Count -eq 1) {
    $cleanupText = $postTransitionCleanupFunctions[0].Extent.Text
    $postTransitionCleanupContractComplete = @(
        @(
            'Stop-BoundedExactGateObserver',
            'Invoke-BoundedExactRuntimeProvisionCleanup',
            'Get-BoundedReleaseDockerInspect',
            '\[string\]\$cleanupApi\.Id\s+-cne\s+\$CandidateApiContainerId',
            '\[string\]\$cleanupApi\.Image\s+-cne\s+"sha256:\$digest"',
            '\[string\]\$cleanupApi\.Config\.Image\s+-cne\s+\$image',
            'org\.opencontainers\.image\.revision',
            'com\.docker\.compose\.project',
            'com\.docker\.compose\.service',
            'com\.docker\.compose\.oneoff',
            '\[int64\]\$cleanupApi\.HostConfig\.Memory\s+-ne\s+1073741824L',
            'RestartPolicy\.Name\s+-cne\s+"unless-stopped"',
            'RELEASE_IMAGE_DIGEST=\$digest',
            '\^OPERATOR_API_KEY=\.\+\$',
            'if\s*\(\[bool\]\$cleanupApi\.State\.Running\)',
            'Invoke-BoundedExactCandidateApiStop\s+-ContainerId\s+\$CandidateApiContainerId',
            '\[bool\]\$stoppedCleanupApi\.State\.Running',
            'Invoke-BoundedExactRetainedPostgresContainment',
            'cleanupFailures\s*\+=\s*"observer=',
            'cleanupFailures\s*\+=\s*"runtime_provision=',
            'cleanupFailures\s*\+=\s*"candidate_api=',
            'cleanupFailures\s*\+=\s*"retained_postgres=',
            'finally\s*\{(?s:.*?)\$HbaReadLock\.Dispose\(\)',
            'Close-ReleaseExecutableBoundary',
            'Fail-closed cleanup was incomplete'
        ) | Where-Object { $cleanupText -notmatch $_ }
    ).Count -eq 0 -and
      $cleanupText -notmatch '(?im)^\s*docker\s+(?:compose\s+)?(?:start|stop)\s+(?:worker|safety-control)\b'
}
if (
    $boundedApiStopFunctions.Count -ne 1 -or
    -not $boundedApiStopContractComplete -or
    $boundedDockerProcessFunctions.Count -ne 1 -or
    -not $boundedDockerProcessContractComplete -or
    $boundedObserverStopFunctions.Count -ne 1 -or
    -not $boundedObserverStopContractComplete -or
    $boundedProvisionCleanupFunctions.Count -ne 1 -or
    -not $boundedProvisionCleanupContractComplete -or
    $boundedPostgresCleanupFunctions.Count -ne 1 -or
    -not $boundedPostgresCleanupContractComplete -or
    $postTransitionCleanupFunctions.Count -ne 1 -or
    -not $postTransitionCleanupContractComplete -or
    $postTransitionCleanupCalls.Count -ne 4 -or
    -not $postTransitionCleanupCallsAllCaught
) {
    throw (
        'Post-Transition cleanup must independently contain the exact observer/provision/API/PG ' +
        'from every Transition/admission/load-gate catch and release every held handle. ' +
        "functions=$($boundedApiStopFunctions.Count)/$boundedApiStopContractComplete/" +
        "$($boundedDockerProcessFunctions.Count)/$boundedDockerProcessContractComplete/" +
        "$($boundedObserverStopFunctions.Count)/$boundedObserverStopContractComplete/" +
        "$($boundedProvisionCleanupFunctions.Count)/$boundedProvisionCleanupContractComplete/" +
        "$($boundedPostgresCleanupFunctions.Count)/$boundedPostgresCleanupContractComplete/" +
        "$($postTransitionCleanupFunctions.Count)/$postTransitionCleanupContractComplete; " +
        "calls=$($postTransitionCleanupCalls.Count)/$postTransitionCleanupCallsAllCaught"
    )
}

function Invoke-PostTransitionCleanupAggregationFixture {
    $events = [Collections.Generic.List[string]]::new()
    function Stop-BoundedExactGateObserver {
        [CmdletBinding()]
        param(
            [object] $Process,
            [int] $ExpectedProcessId,
            [int64] $ExpectedStartedAtUtcTicks,
            [string] $ExpectedExecutablePath
        )
        [void]$events.Add('observer')
        throw 'fixture observer cleanup failure'
    }
    function Invoke-BoundedExactRuntimeProvisionCleanup {
        [CmdletBinding()]
        param([string] $ContainerName, [string] $ComposeProject)
        [void]$events.Add('provision')
        throw 'fixture provision cleanup failure'
    }
    function Invoke-BoundedExactRetainedPostgresContainment {
        [CmdletBinding()]
        param([object] $Identity, [string] $ContainmentPath, [string] $Trigger)
        [void]$events.Add('postgres')
        throw 'fixture PostgreSQL cleanup failure'
    }
    function Close-ReleaseExecutableBoundary {
        [void]$events.Add('handles')
    }
    $preStartTopology = [pscustomobject]@{ compose_project = 'weatherpoly' }
    $observerFixture = [Diagnostics.Process]::new()
    $hbaFixturePath = [IO.Path]::GetTempFileName()
    $hbaFixture = [IO.FileStream]::new(
        $hbaFixturePath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $cleanupFailure = $null
    try {
        . ([scriptblock]::Create($postTransitionCleanupFunctions[0].Extent.Text))
        Invoke-PostTransitionFailClosedCleanup `
            -CandidateApiContainerId 'malformed-api-id' `
            -RuntimeProvisionContainerName 'stratus-runtime-role-fixture' `
            -ObserverProcess $observerFixture `
            -ObserverProcessId 1 `
            -ObserverStartedAtUtcTicks 1 `
            -ObserverExecutablePath 'fixture.exe' `
            -RetainedPostgresIdentity ([pscustomobject]@{ container_id = ('a' * 64) }) `
            -ContainRetainedPostgres `
            -ParentContainmentPath $hbaFixturePath `
            -CleanupTrigger 'fixture_failure' `
            -HbaReadLock $hbaFixture
    }
    catch {
        $cleanupFailure = $_.Exception.Message
    }
    finally {
        $hbaReadableAfter = $hbaFixture.CanRead
        $observerFixture.Dispose()
        Remove-Item -LiteralPath $hbaFixturePath -Force -ErrorAction SilentlyContinue
    }
    return [pscustomobject]@{
        events = @($events)
        failure = $cleanupFailure
        hba_readable_after = $hbaReadableAfter
    }
}
$cleanupAggregationFixture = Invoke-PostTransitionCleanupAggregationFixture
if (
    ($cleanupAggregationFixture.events -join ',') -cne
      'observer,provision,postgres,handles' -or
    [bool]$cleanupAggregationFixture.hba_readable_after -or
    [string]$cleanupAggregationFixture.failure -cnotmatch 'observer=' -or
    [string]$cleanupAggregationFixture.failure -cnotmatch 'runtime_provision=' -or
    [string]$cleanupAggregationFixture.failure -cnotmatch 'candidate_api=' -or
    [string]$cleanupAggregationFixture.failure -cnotmatch 'retained_postgres='
) {
    throw (
        'Hermetic parent cleanup fixture did not independently attempt every component, ' +
        'aggregate failures, and release HBA/executable handles.'
    )
}

$parentStageBlockerIndex = $source.IndexOf(
    'Section 3 remains blocked until the crash-resilient parent-stage watchdog',
    [StringComparison]::Ordinal
)
$transitionInvocationIndex = $source.IndexOf(
    '$successorTransitionOutput = @(& (',
    [StringComparison]::Ordinal
)
$hbaOuterScopeMatches = @(
    [regex]::Matches(
        $cutoverBlock.Text,
        '(?s)\$runbookHbaReadLock\s*=\s*\[IO\.FileStream\]::new\(.*?' +
        '\)\s*\r?\ntry\s*\{.*?catch\s*\{\s*\$transitionValidationFailure'
    )
)
$successHbaHandoffMatches = @(
    [regex]::Matches(
        $source,
        '(?s)\$parentStageWatchdogHandoffAccepted\s+-ne\s+\$true.*?' +
        'Get-HeldReleaseExecutableSha256\s+-Handle\s+\$runbookHbaReadLock.*?' +
        '\$runbookHbaReadLock\.Dispose\(\).*?' +
        '\$releaseState\s*=\s*"R3_ACCEPTED_PARENT_WATCHDOG_HANDOFF"'
    )
)
if (
    $parentStageBlockerIndex -lt 0 -or
    $transitionInvocationIndex -le $parentStageBlockerIndex -or
    $hbaOuterScopeMatches.Count -ne 1 -or
    $successHbaHandoffMatches.Count -ne 1
) {
    throw (
        'Section 3 must remain explicitly blocked for the missing crash-resilient parent ' +
        'watchdog, retain HBA in an outer failure scope, and expose one exact success handoff.'
    )
}

$schemaSelectionGuards = @(
    $cutoverIfs | Where-Object {
        $_.Extent.Text -match (
            '\$cutoverSchemaPath\s+-cnotin\s+@\(\s*"20260730_0046"' +
            '\s*,\s*"20260730_0047"\s*\)'
        ) -and
        $_.Extent.Text -match 'STRATUS_CUTOVER_SCHEMA_PATH must explicitly select'
    }
)
$containerLifecycleCommands = @(
    $cutoverCommands | Where-Object {
        $_.GetCommandName() -ceq 'docker'
    }
)
$firstContainerStartOffset = if ($containerLifecycleCommands.Count -gt 0) {
    [int]@(
        $containerLifecycleCommands |
            ForEach-Object { $_.Extent.StartOffset } |
            Sort-Object
    )[0]
}
else { -1 }
if (
    $schemaSelectionGuards.Count -ne 1 -or
    $firstContainerStartOffset -lt 0 -or
    $schemaSelectionGuards[0].Extent.EndOffset -ge $firstContainerStartOffset
) {
    throw 'Exact 0046/0047 path selection must fail closed before every container start.'
}

$path0046Pattern = '\$cutoverSchemaPath\s+-ceq\s+"20260730_0046"'
$path0047Pattern = '\$cutoverSchemaPath\s+-ceq\s+"20260730_0047"'
$schema0046Blockers = @(
    $cutoverAst.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.IfStatementAst] -and
        $_.Clauses.Count -eq 1 -and
        $_.Clauses[0].Item1.Extent.Text -match "^\s*$path0046Pattern\s*$"
    }
)
$allSchema0046Conditions = @(
    $cutoverIfs | Where-Object {
        $_.Clauses.Count -gt 0 -and
        $_.Clauses[0].Item1.Extent.Text -match "^\s*$path0046Pattern\s*$"
    }
)
$successorRequiredGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match '\$successorPathNames' -and
        $_.Extent.Text -match '0047 Transition path requires all four immutable lineage/evidence paths'
    }
)
$firstLifecycleCommandOffset = $firstContainerStartOffset
$schema0046BlockerExact = $false
if ($schema0046Blockers.Count -eq 1) {
    $schema0046Blocker = $schema0046Blockers[0]
    $schema0046Statements = @($schema0046Blocker.Clauses[0].Item2.Statements)
    $schema0046BlockerExact = (
        $allSchema0046Conditions.Count -eq 1 -and
        $schema0046Blocker.Clauses.Count -eq 1 -and
        $null -eq $schema0046Blocker.ElseClause -and
        $schema0046Statements.Count -eq 1 -and
        $schema0046Statements[0] -is
            [Management.Automation.Language.ThrowStatementAst] -and
        $schema0046Blocker.Extent.Text -match
            'disabled before PostgreSQL start' -and
        $schema0046Blocker.Extent.EndOffset -lt $firstLifecycleCommandOffset
    )
}
if (
    -not $schema0046BlockerExact -or
    $successorRequiredGuards.Count -ne 1 -or
    -not (Test-AstInsideExactIfClause -Node $successorRequiredGuards[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    $successorRequiredGuards[0].Extent.EndOffset -ge $firstContainerStartOffset
) {
    throw (
        'Schema 0046 must be exactly one top-level throw without else before every ' +
        'lifecycle command; successor paths must be complete for 0047 before any start.'
    )
}

$transitionAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match '^\s*\$successorTransitionOutput\s*=\s*@\(\s*&' -and
        $_.Extent.Text -match '(?m)^\s+-Mode Transition\s+`\r?$'
    }
)
$transitionAcceptanceGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match '\$successorTransitionGate\.accepted' -and
        $_.Extent.Text -match 'Post-migration successor Transition gate was not accepted'
    }
)
$transitionSchemaAdmissions = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$preMigrationHead\s*=\s*\[string\]' +
            '\$successorTransitionGate\.payload\.database\.schema_head\s*$'
        )
    }
)
$outputCreateNewGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match '\$attemptScopedEvidencePaths' -and
        $_.Extent.Text -match 'Every attempt-scoped repair/Transition evidence path must be absent'
    }
)
$attemptAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$cutoverAttemptId\s*=\s*\[guid\]::NewGuid\(\)' +
            '\.ToString\("N"\)\s*$'
        )
    }
)
$preStartPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match '^\s*\$successorPreStartAttestation\s*=\s*Join-Path' -and
        $_.Extent.Text -match (
            'post-migration-successor-prestart-\$\(\$revision\.Substring\(0, 12\)\)' +
            '-\$cutoverAttemptId\.json'
        )
    }
)
$networkRepairPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match '^\s*\$networkRepairAttestation\s*=\s*Join-Path' -and
        $_.Extent.Text -match (
            'stopped-postgres-network-repair-\$\(\$revision\.Substring\(0, 12\)\)' +
            '-\$cutoverAttemptId\.json'
        )
    }
)
$livePathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match '^\s*\$successorLiveAcceptance\s*=\s*Join-Path' -and
        $_.Extent.Text -match (
            'post-migration-successor-live-\$\(\$revision\.Substring\(0, 12\)\)' +
            '-\$cutoverAttemptId\.json'
        )
    }
)
$watchdogLeasePathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorWatchdogLease\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.watchdog-lease\.json"\s*$'
        )
    }
)
$watchdogTerminalPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorWatchdogTerminal\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.watchdog-terminal\.json"\s*$'
        )
    }
)
$watchdogHeartbeatPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorWatchdogHeartbeat\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.watchdog-heartbeat\.json"\s*$'
        )
    }
)
$watchdogCommitPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorWatchdogCommit\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.watchdog-commit\.json"\s*$'
        )
    }
)
$watchdogStartFencePathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorWatchdogStartFence\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.watchdog-start-fence\.lock"\s*$'
        )
    }
)
$watchdogStartRevokePathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorWatchdogStartRevoke\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.watchdog-start-revoked\.json"\s*$'
        )
    }
)
$watchdogStartAttemptPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorWatchdogStartAttempt\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.watchdog-start-attempt\.json"\s*$'
        )
    }
)
$watchdogStartCompletionPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorWatchdogStartCompletion\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.watchdog-start-complete\.json"\s*$'
        )
    }
)
$parentContainmentPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorParentContainment\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.parent-containment\.json"\s*$'
        )
    }
)
$parentAuthorizationPathAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorParentAuthorization\s*=\s*' +
            '\$successorPreStartAttestation\s*\+\s*"\.parent-authorization\.json"\s*$'
        )
    }
)
$watchdogAttemptPathSets = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match '^\s*\$attemptScopedEvidencePaths\s*\+=' -and
        $_.Extent.Text -match '\$successorPreStartAttestation' -and
        $_.Extent.Text -match '\$successorLiveAcceptance' -and
        $_.Extent.Text -match '\$successorWatchdogLease' -and
        $_.Extent.Text -match '\$successorWatchdogTerminal' -and
        $_.Extent.Text -match '\$successorWatchdogHeartbeat' -and
        $_.Extent.Text -match '\$successorWatchdogCommit' -and
        $_.Extent.Text -match '\$successorWatchdogStartFence' -and
        $_.Extent.Text -match '\$successorWatchdogStartRevoke' -and
        $_.Extent.Text -match '\$successorWatchdogStartAttempt' -and
        $_.Extent.Text -match '\$successorWatchdogStartCompletion' -and
        $_.Extent.Text -match '\$successorParentContainment' -and
        $_.Extent.Text -match '\$successorParentAuthorization'
    }
)
$allPowerShellAssignments = @(
    foreach ($blockAst in $powerShellAsts) {
        $blockAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.AssignmentStatementAst]
        }, $true)
    }
)
$unsafePinnedDockerInvocations = @(
    $allPowerShellCommands | Where-Object {
        $_.Extent.Text -match
          '^\s*&\s*\$global:StratusReleaseDockerExecutable(?:\s|$)'
    }
)
$dockerProcessStartAssignments = @(
    $allPowerShellAssignments | Where-Object {
        $_.Extent.Text -match
          '^\s*\$startInfo\.FileName\s*=\s*\$global:StratusReleaseDockerExecutable\s*$'
    }
)
$dockerProcessStartOwnedByBoundedRunner = $false
if ($dockerProcessStartAssignments.Count -eq 1) {
    $owner = $dockerProcessStartAssignments[0].Parent
    while ($null -ne $owner -and
      $owner -isnot [Management.Automation.Language.FunctionDefinitionAst]
    ) {
        $owner = $owner.Parent
    }
    $dockerProcessStartOwnedByBoundedRunner = (
        $null -ne $owner -and
        $owner.Name -ceq 'Invoke-BoundedPinnedReleaseDockerProcess'
    )
}
$engineBoundaryFunctions = @(
    foreach ($blockAst in $powerShellAsts) {
        $blockAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
              $node.Name -ceq 'Assert-ReleaseDockerEngineBoundary'
        }, $true)
    }
)
$engineBoundaryContractComplete = $false
if ($engineBoundaryFunctions.Count -eq 1) {
    $engineBoundaryText = $engineBoundaryFunctions[0].Extent.Text
    $engineBoundaryContractComplete = @(
        @(
            '\$contextResult\s*=\s*Invoke-BoundedPinnedReleaseDockerProcess',
            '\$endpointResult\s*=\s*Invoke-BoundedPinnedReleaseDockerProcess',
            '\$engineResult\s*=\s*Invoke-BoundedPinnedReleaseDockerProcess',
            '\$contextResult\.exit_code\s+-ne\s+0',
            '\$endpointResult\.exit_code\s+-ne\s+0',
            '\$engineResult\.exit_code\s+-ne\s+0',
            '\$contextOutput\.Count\s+-ne\s+1',
            '\$endpointOutput\.Count\s+-ne\s+1',
            '\$engineOutput\.Count\s+-ne\s+1',
            '\$contextResult\.stderr',
            '\$endpointResult\.stderr',
            '\$engineResult\.stderr',
            '"context",\s*"inspect",\s*"desktop-linux"'
        ) | Where-Object { $engineBoundaryText -notmatch $_ }
    ).Count -eq 0
}
$executableBoundaryFunctions = @(
    foreach ($blockAst in $powerShellAsts) {
        $blockAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
              $node.Name -ceq 'Assert-ReleaseExecutableBoundary'
        }, $true)
    }
)
$executableBoundaryContractComplete = $false
if ($executableBoundaryFunctions.Count -eq 1) {
    $executableBoundaryText = $executableBoundaryFunctions[0].Extent.Text
    $executableBoundaryContractComplete = @(
        @(
            'StratusReleaseDockerExecutableHandle',
            'StratusReleaseGitExecutableHandle',
            'StratusReleaseComposePluginHandle',
            'Get-HeldReleaseExecutableSha256',
            'StratusExpectedReleaseDockerSha256',
            'StratusExpectedReleaseGitSha256',
            'StratusExpectedReleaseComposeSha256'
        ) | Where-Object { $executableBoundaryText -notmatch $_ }
    ).Count -eq 0
}
$openExecutableHandleFunctions = @(
    foreach ($blockAst in $powerShellAsts) {
        $blockAst.FindAll({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
              $node.Name -ceq 'Open-ReleaseExecutableReadLock'
        }, $true)
    }
)
$openExecutableHandleContractComplete = (
    $openExecutableHandleFunctions.Count -eq 1 -and
    $openExecutableHandleFunctions[0].Extent.Text -match '\[IO\.FileShare\]::Read' -and
    $openExecutableHandleFunctions[0].Extent.Text -match 'Get-HeldReleaseExecutableSha256'
)
if (
    $unsafePinnedDockerInvocations.Count -ne 0 -or
    $dockerProcessStartAssignments.Count -ne 1 -or
    -not $dockerProcessStartOwnedByBoundedRunner -or
    $engineBoundaryFunctions.Count -ne 1 -or
    -not $engineBoundaryContractComplete -or
    $executableBoundaryFunctions.Count -ne 1 -or
    -not $executableBoundaryContractComplete -or
    -not $openExecutableHandleContractComplete
) {
    throw (
        'Every pinned Docker process must run only through the exact held-executable, ' +
        'explicit desktop-linux, bounded engine/process boundary. ' +
        "direct=$($unsafePinnedDockerInvocations.Count); process=" +
        "$($dockerProcessStartAssignments.Count)/$dockerProcessStartOwnedByBoundedRunner; " +
        "engine=$($engineBoundaryFunctions.Count)/$engineBoundaryContractComplete; " +
        "held=$($executableBoundaryFunctions.Count)/$executableBoundaryContractComplete/" +
        "$($openExecutableHandleFunctions.Count)/$openExecutableHandleContractComplete"
    )
}
$attemptPathInitialAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match '^\s*\$attemptScopedEvidencePaths\s*=\s*@\(' -and
        $_.Extent.Text -notmatch '\+='
    }
)
$attemptPathCountGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match
          '\$attemptScopedEvidencePaths\.Count\s+-ne\s+13' -and
        $_.Extent.Text -match
          'Schema 0047 requires exactly thirteen attempt-scoped evidence paths'
    }
)
$expectedSuccessorAttemptPathVariables = @(
    'successorPreStartAttestation',
    'successorLiveAcceptance',
    'successorWatchdogLease',
    'successorWatchdogTerminal',
    'successorWatchdogHeartbeat',
    'successorWatchdogCommit',
    'successorWatchdogStartFence',
    'successorWatchdogStartRevoke',
    'successorWatchdogStartAttempt',
    'successorWatchdogStartCompletion',
    'successorParentContainment',
    'successorParentAuthorization'
) | Sort-Object
$successorAttemptPathSetExact = $false
if ($watchdogAttemptPathSets.Count -eq 1 -and
    $attemptPathInitialAssignments.Count -eq 1
) {
    $actualInitialAttemptPathVariables = @(
        $attemptPathInitialAssignments[0].Right.FindAll({
            param($node)
            $node -is [Management.Automation.Language.VariableExpressionAst]
        }, $true) | ForEach-Object { [string]$_.VariablePath.UserPath } | Sort-Object
    )
    $actualSuccessorAttemptPathVariables = @(
        $watchdogAttemptPathSets[0].Right.FindAll({
            param($node)
            $node -is [Management.Automation.Language.VariableExpressionAst]
        }, $true) | ForEach-Object { [string]$_.VariablePath.UserPath } | Sort-Object
    )
    $successorAttemptPathSetExact = (
        $actualInitialAttemptPathVariables.Count -eq 1 -and
        $actualInitialAttemptPathVariables[0] -ceq 'networkRepairAttestation' -and
        $actualSuccessorAttemptPathVariables.Count -eq 12 -and
        ($actualSuccessorAttemptPathVariables -join "`n") -ceq
          ($expectedSuccessorAttemptPathVariables -join "`n")
    )
}
$attemptPathRuntimeProof = $false
if ($successorAttemptPathSetExact) {
    $attemptPathRuntimeProbe = & {
        $networkRepairAttestation = 'path-01'
        $successorPreStartAttestation = 'path-02'
        $successorLiveAcceptance = 'path-03'
        $successorWatchdogLease = 'path-04'
        $successorWatchdogTerminal = 'path-05'
        $successorWatchdogHeartbeat = 'path-06'
        $successorWatchdogCommit = 'path-07'
        $successorWatchdogStartFence = 'path-08'
        $successorWatchdogStartRevoke = 'path-09'
        $successorWatchdogStartAttempt = 'path-10'
        $successorWatchdogStartCompletion = 'path-11'
        $successorParentContainment = 'path-12'
        $successorParentAuthorization = 'path-13'
        . ([scriptblock]::Create($attemptPathInitialAssignments[0].Extent.Text))
        . ([scriptblock]::Create($watchdogAttemptPathSets[0].Extent.Text))
        return ,@($attemptScopedEvidencePaths)
    }
    $attemptPathRuntimeProof = (
        $attemptPathRuntimeProbe.Count -eq 13 -and
        @($attemptPathRuntimeProbe | Sort-Object -Unique).Count -eq 13 -and
        ($attemptPathRuntimeProbe -join "`n") -ceq
          ((1..13 | ForEach-Object { 'path-{0:d2}' -f $_ }) -join "`n")
    )
}
$normalTransitionReturnAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match
            '^\s*\$successorTransitionReturnedNormally\s*=\s*\$true\s*$'
    }
)
$directParentOutputCardinalityGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match
            '\$successorTransitionReturnedNormally' -and
        $_.Clauses[0].Item1.Extent.Text -match
            '\$successorTransitionOutput\.Count\s+-ne\s+1' -and
        $_.Extent.Text -match
            'did not return normally with exactly one parent authorization'
    }
)
$directParentOutputEqualityGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match
            '(?s)\$successorTransitionOutput\[0\].*' +
            '\$successorParentAuthorizationDocument\.json' -and
        $_.Extent.Text -match
            'stdout is not byte-text-identical to parent authorization'
    }
)
$parentAuthorizationSchemaCommands = @(
    $cutoverCommands | Where-Object {
        $_.GetCommandName() -ceq 'Assert-CutoverExactProperties' -and
        $_.Extent.Text -match '-Label\s+"successor parent authorization"'
    }
)
$parentAuthorizerSchemaCommands = @(
    $cutoverCommands | Where-Object {
        $_.GetCommandName() -ceq 'Assert-CutoverExactProperties' -and
        $_.Extent.Text -match '-Label\s+"parent authorization authorizer"'
    }
)
$parentWitnessSchemaCommands = @(
    $cutoverCommands | Where-Object {
        $_.GetCommandName() -ceq 'Assert-CutoverExactProperties' -and
        $_.Extent.Text -match '-Label\s+"parent authorization Docker witness"'
    }
)
$parentPermissionSchemaCommands = @(
    $cutoverCommands | Where-Object {
        $_.GetCommandName() -ceq 'Assert-CutoverExactProperties' -and
        $_.Extent.Text -match '-Label\s+"parent authorization permissions"'
    }
)
$expectedParentAuthorizationProperties = @(
    'format', 'status', 'authorized_at', 'publication_started_at', 'valid_until',
    'docker_state_observed_through_time_nano', 'session_id', 'owner_pid',
    'owner_start_time_utc', 'authorizer', 'parent_authorization_path',
    'watchdog_program_path', 'watchdog_program_sha256', 'watchdog_process_id',
    'watchdog_process_started_at', 'watchdog_exit_code', 'sealed_stdout_sha256',
    'sealed_stdout_bytes', 'sealed_stderr_sha256', 'sealed_stderr_bytes',
    'lease_path', 'lease_sha256', 'receipt_path', 'receipt_sha256',
    'commit_path', 'commit_sha256', 'live_path', 'live_sha256',
    'live_payload_sha256', 'start_fence_path', 'start_fence_sha256',
    'start_attempt_path', 'start_attempt_sha256', 'start_completion_path',
    'start_completion_sha256', 'docker_engine_contract_sha256',
    'postgres_container_id', 'postgres_image_id', 'postgres_config_image',
    'postgres_restart_count', 'postgres_started_at', 'authorization_witness',
    'authorization'
)
$expectedParentAuthorizerProperties = @(
    'gate_script_path', 'gate_script_sha256', 'gate_script_bytes',
    'host_executable_path', 'host_executable_sha256', 'host_executable_bytes',
    'process_id', 'process_started_at'
)
$expectedParentWitnessProperties = @(
    'format', 'session_id', 'process_id', 'process_started_at',
    'docker_executable_path', 'docker_executable_sha256', 'reconnect_count',
    'eof_before_seal', 'truncated', 'journal_bytes', 'journal_sha256',
    'event_count', 'initial_barrier_id', 'initial_barrier_destroy_time_nano',
    'final_barrier_id', 'final_barrier_create_time_nano',
    'final_barrier_destroy_time_nano', 'unexpected_event_count'
)
$expectedParentPermissionProperties = @(
    'api_stage_permitted', 'worker_start_permitted',
    'safety_control_start_permitted', 'live_transition_permitted',
    'binance_kill_switch_removal_permitted', 'migration_container_permitted'
)
$parentAuthorizationExactSchemas = (
    $parentAuthorizationSchemaCommands.Count -eq 1 -and
    (Test-AstCommandExactExpectedStrings `
        -Command $parentAuthorizationSchemaCommands[0] `
        -Expected $expectedParentAuthorizationProperties) -and
    $parentAuthorizerSchemaCommands.Count -eq 1 -and
    (Test-AstCommandExactExpectedStrings `
        -Command $parentAuthorizerSchemaCommands[0] `
        -Expected $expectedParentAuthorizerProperties) -and
    $parentWitnessSchemaCommands.Count -eq 1 -and
    (Test-AstCommandExactExpectedStrings `
        -Command $parentWitnessSchemaCommands[0] `
        -Expected $expectedParentWitnessProperties) -and
    $parentPermissionSchemaCommands.Count -eq 1 -and
    (Test-AstCommandExactExpectedStrings `
        -Command $parentPermissionSchemaCommands[0] `
        -Expected $expectedParentPermissionProperties)
)
$parentAuthorizationAcceptanceGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match
            '\$successorParentAuthorizationProof\.format' -and
        $_.Extent.Text -match
            'Direct parent authorization is late, stale, forged, or not transition-bound'
    }
)
$parentAuthorizationAcceptanceContractComplete = $false
if ($parentAuthorizationAcceptanceGuards.Count -eq 1) {
    $parentAcceptanceText = $parentAuthorizationAcceptanceGuards[0].Extent.Text
    $parentAuthorizationAcceptanceContractComplete = @(
        @(
            'stratus-successor-transition-parent-authorization-v1',
            '\.status\s+-cne\s+"authorized"',
            '\.authorization\.api_stage_permitted\s+-ne\s+\$true',
            '\.authorization\.worker_start_permitted\s+-ne\s+\$false',
            '\.authorization\.safety_control_start_permitted\s+-ne\s+\$false',
            '\.authorization\.live_transition_permitted\s+-ne\s+\$false',
            '\.authorization\.binance_kill_switch_removal_permitted\s+-ne',
            '\.authorization\.migration_container_permitted\s+-ne\s+\$false',
            '\.authorization\.migration_container_permitted\s+-ne\s+\$false',
            '\.parent_authorization_path\s+-cne',
            '\.watchdog_program_path\s+-cne',
            '\.watchdog_program_sha256\s+-cne',
            '\.watchdog_process_id\s+-ne',
            '\.watchdog_process_started_at\s+-cne',
            '\.watchdog_exit_code\s+-ne\s+0',
            '\.sealed_stdout_sha256\s+-cne',
            '\.sealed_stdout_bytes\s+-ne',
            '\.sealed_stderr_sha256\s+-cne',
            '\.sealed_stderr_bytes\s+-ne\s+0',
            '\.lease_path\s+-cne', '\.lease_sha256\s+-cne',
            '\.receipt_path\s+-cne', '\.receipt_sha256\s+-cne',
            '\.commit_path\s+-cne', '\.commit_sha256\s+-cne',
            '\.live_path\s+-cne', '\.live_sha256\s+-cne',
            '\.live_payload_sha256\s+-cne',
            '\.start_fence_path\s+-cne', '\.start_fence_sha256\s+-cne',
            '\.start_attempt_path\s+-cne', '\.start_attempt_sha256\s+-cne',
            '\.start_completion_path\s+-cne',
            '\.start_completion_sha256\s+-cne',
            '\.docker_engine_contract_sha256\s+-cne',
            '\.postgres_container_id\s+-cne', '\.postgres_image_id\s+-cne',
            '\.postgres_config_image\s+-cne', '\.postgres_restart_count\s+-ne',
            '\.postgres_started_at\s+-cne',
            '\.authorizer\.gate_script_path', '\.authorizer\.gate_script_sha256',
            '\.authorizer\.gate_script_bytes', '\.authorizer\.host_executable_path',
            '\.authorizer\.host_executable_sha256',
            '\.authorizer\.host_executable_bytes', '\.authorizer\.process_id',
            '\.authorizer\.process_started_at',
            '\$successorAuthorizationWitness\.format\s+-cne',
            '\$successorAuthorizationWitness\.event_count\s+-ne\s+4',
            '\$successorAuthorizationWitness\.unexpected_event_count\s+-ne\s+0',
            '\$successorAuthorizationWitness\.eof_before_seal',
            '\$successorAuthorizationWitness\.truncated',
            '\$successorAuthorizationObservedNano\s+-ne',
            '\$successorParentAuthorizedAt\.UtcTicks\s+-ne',
            '\$successorParentPublicationStartedAt\s+-gt',
            '\$successorParentValidUntil\.UtcTicks\s+-ne',
            '\[DateTimeOffset\]::UtcNow\s+-gt\s+\$successorParentValidUntil',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorParentAuthorization'
        ) | Where-Object { $parentAcceptanceText -notmatch $_ }
    ).Count -eq 0
}
$terminalAcceptanceGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match '\$successorWatchdogTerminalProof\.status' -and
        $_.Extent.Text -match (
            'Non-authorizing watchdog terminal receipt did not prove the exact Live commit'
        )
    }
)
$apiStageEffectAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match (
            '^\s*\$successorApiStagePermitted\s*=\s*\[bool\]\(\s*' +
            '\$successorParentAuthorizationProof\.authorization\.' +
            'api_stage_permitted\s*\)\s*$'
        )
    }
)
$parentApiAuthorizationCalls = @(
    $cutoverCommands | Where-Object {
        $_.GetCommandName() -ceq 'Assert-SuccessorParentApiAuthorizationUnchanged'
    } | Sort-Object -Property { $_.Extent.StartOffset }
)
$runtimeProvisionCommands = @(
    $cutoverCommands | Where-Object {
        $_.Extent.Text -match (
            '^docker\s+compose\s+@releaseComposeArguments\s+--profile\s+release\s+' +
            'run\s+--rm\s+--no-deps\s+provision-runtime-role$'
        )
    }
)
$apiStageCommands = @(
    $cutoverCommands | Where-Object {
        $_.Extent.Text -match (
            '^docker\s+compose\s+@releaseComposeArguments\s+--profile\s+release\s+' +
            'up\s+--no-start\s+--no-build\s+--pull\s+never\s+--no-deps\s+' +
            '--force-recreate\s+api$'
        )
    }
)
$apiStartCommands = @(
    $cutoverCommands | Where-Object {
        $_.Extent.Text -match '^docker\s+start\s+\$apiContainer\[0\]'
    }
)
$parentApiAuthorizationFunctions = @(
    $cutoverFunctions | Where-Object {
        $_.Name -ceq 'Assert-SuccessorParentApiAuthorizationUnchanged'
    }
)
$parentApiAuthorizationFunctionComplete = $false
if ($parentApiAuthorizationFunctions.Count -eq 1) {
    $functionText = $parentApiAuthorizationFunctions[0].Extent.Text
    $parentApiAuthorizationFunctionComplete = @(
        @(
            '\$currentDockerContextResult\s*=\s*Invoke-BoundedPinnedReleaseDockerProcess',
            '\$currentDockerInfoResult\s*=\s*Invoke-BoundedPinnedReleaseDockerProcess',
            '\$currentRunningContainersResult\s*=\s*Invoke-BoundedReleaseDockerCommand',
            '\$currentAuthorizedPostgresResult\s*=\s*Invoke-BoundedReleaseDockerCommand',
            '\$currentCoreEngineProof\s*\|\s*ConvertTo-Json',
            '\$successorCoreEngineProof\s*\|\s*ConvertTo-Json',
            '\$currentLegacyEngineProof\s*\|\s*ConvertTo-Json',
            '\$successorWatchdogEngineProof\s*\|\s*ConvertTo-Json',
            '\$currentRunningContainerIds\.Count\s+-ne\s+1',
            '\.State\.StartedAt\s+-cne',
            '\.State\.Health\.Status\s+-cne\s+"healthy"',
            '\.HostConfig\.RestartPolicy\.Name\s+-cne\s+"no"',
            '\.HostConfig\.NetworkMode\s+-cne',
            '77551e8d1119201f778fdb90e151ec26f1d9cc23832ba7b88c4db6203e3d8ba2',
            '0d1ad122718694dbc5e6714f69a601a7d429e23a0b74eecdb10ace944e5c8620',
            '\$successorTransitionReturnedNormally',
            '\$successorTransitionOutput\.Count\s+-ne\s+1',
            '\$currentParentAuthorizationDocument\.json',
            '\$successorParentAuthorizationSha256',
            '\$successorParentValidUntil',
            '\$currentParentAuthorizationProof\.authorization\.api_stage_permitted\s+-ne\s+\$true',
            '\$currentLiveProof\.payload\.authorization\.api_stage_permitted\s+-ne\s+\$false',
            '\$currentTerminalProof\.authorization\.api_stage_permitted\s+-ne\s+\$false',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$networkRepairAttestation',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorLiveAcceptance',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorPreStartAttestation',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorWatchdogLease',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorWatchdogHeartbeat',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorWatchdogCommit',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorWatchdogTerminal',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorParentAuthorization',
            'Test-Path\s+-LiteralPath\s+\$successorWatchdogStartRevoke',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorWatchdogProgram\.script_path',
            'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorWatchdogProgram\.host_path'
        ) | Where-Object { $functionText -notmatch $_ }
    ).Count -eq 0 -and @(
        [regex]::Matches(
            $functionText,
            '\[DateTimeOffset\]::UtcNow\s+-gt\s+\$successorParentValidUntil'
        )
    ).Count -eq 2
}
$parentApiAuthorizationCallsAllGuarded = $true
foreach ($call in $parentApiAuthorizationCalls) {
    if (-not (Test-AstInsideExactIfClause -Node $call -Root $cutoverAst `
        -ConditionPattern $path0047Pattern)) {
        $parentApiAuthorizationCallsAllGuarded = $false
    }
}
$terminalAcceptanceContractComplete = $false
if ($terminalAcceptanceGuards.Count -eq 1) {
    $terminalText = $terminalAcceptanceGuards[0].Extent.Text
    $terminalAcceptanceContractComplete = @(
        @(
            '\.status\s+-cne\s+"success"',
            '\.trigger\s+-cne\s+"accepted_live_commit"',
            '\$null\s+-ne\s+\$successorWatchdogTerminalProof\.containment',
            '\$null\s+-ne\s+\$successorWatchdogTerminalProof\.failure',
            '\.authorization\.api_stage_permitted\s+-ne\s+\$false',
            '\.authorization\.worker_start_permitted\s+-ne\s+\$false',
            '\.authorization\.safety_control_start_permitted\s+-ne\s+\$false',
            '\.authorization\.live_transition_permitted\s+-ne\s+\$false',
            '\.authorization\.binance_kill_switch_removal_permitted\s+-ne',
            '\.lease_sha256\s+-cne\s+\$successorWatchdogLeaseSha256',
            '\.commit\.marker_sha256\s+-cne',
            '\.commit\.live_sha256\s+-cne',
            '\.commit\.live_payload_sha256\s+-cne',
            '\.commit\.postgres_started_at\s+-cne',
            '\.commit\.docker_engine_contract_sha256\s+-cne'
        ) | Where-Object { $terminalText -notmatch $_ }
    ).Count -eq 0
}
$transitionAcceptanceContractComplete = $false
if ($transitionAcceptanceGuards.Count -eq 1) {
    $acceptanceText = $transitionAcceptanceGuards[0].Extent.Text
    $requiredAcceptancePatterns = @(
        'stratus-post-migration-successor-transition-live-v2',
        '\.payload\.prestart\.path',
        '\.payload\.transition_session\.mode',
        '\.event_witness\.postgres_start_count\s+-ne\s+1',
        '\.event_witness\.postgres_restart_policy_update_count\s+-ne\s+1',
        '\.event_witness\.healthy_status_event_count\s+-ne\s+1',
        '\.event_witness\.postgres_attach_count\s+-ne\s+0',
        '\.event_witness\.network_connect_count\s+-ne\s+2',
        '\.event_witness\.data_volume_mount_count\s+-ne\s+1',
        '\.event_witness\.gate_exec_triplets\s+-ne\s+8',
        '\.event_witness\.health_exec_triplets\s+-lt\s+1',
        '\.event_witness\.health_exec_triplets\s+-gt\s+64',
        '\.event_witness\.reconnect_count\s+-ne\s+0',
        '\.event_witness\.eof_before_seal',
        '\.event_witness\.truncated',
        '\.rollback\.on_failure',
        '\.rollback\.stop_timeout_seconds\s+-ne\s+15',
        '\.rollback\.executed',
        '\.rollback\.emergency_containment',
        '\.postgres_transition\.restart_policy\s+-cne\s+"no"',
        '\.hba\.reload_acknowledged',
        '\.final_boundary\.fresh_hba_state\.conf_load_time',
        '\.authorization\.live_transition_permitted',
        '\.authorization\.binance_kill_switch_removal_permitted',
        '\.authorization\.api_stage_permitted\s+-ne\s+\$false',
        '\$successorApiStagePermitted\s+-ne\s+\$true',
        'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorWatchdogTerminal',
        'Get-CutoverFileSha256\s+-LiteralPath\s+\$successorParentAuthorization',
        'Test-Path\s+-LiteralPath\s+\$successorWatchdogStartRevoke'
    )
    $transitionAcceptanceContractComplete = @(
        $requiredAcceptancePatterns | Where-Object {
            $acceptanceText -notmatch $_
        }
    ).Count -eq 0
}
if (
    $transitionAssignments.Count -ne 1 -or
    $transitionAcceptanceGuards.Count -ne 1 -or
    $transitionSchemaAdmissions.Count -ne 1 -or
    $outputCreateNewGuards.Count -ne 1 -or
    $attemptAssignments.Count -ne 1 -or
    $preStartPathAssignments.Count -ne 1 -or
    $networkRepairPathAssignments.Count -ne 1 -or
    $livePathAssignments.Count -ne 1 -or
    $watchdogLeasePathAssignments.Count -ne 1 -or
    $watchdogTerminalPathAssignments.Count -ne 1 -or
    $watchdogHeartbeatPathAssignments.Count -ne 1 -or
    $watchdogCommitPathAssignments.Count -ne 1 -or
    $watchdogStartFencePathAssignments.Count -ne 1 -or
    $watchdogStartRevokePathAssignments.Count -ne 1 -or
    $watchdogStartAttemptPathAssignments.Count -ne 1 -or
    $watchdogStartCompletionPathAssignments.Count -ne 1 -or
    $parentContainmentPathAssignments.Count -ne 1 -or
    $parentAuthorizationPathAssignments.Count -ne 1 -or
    $attemptPathInitialAssignments.Count -ne 1 -or
    $watchdogAttemptPathSets.Count -ne 1 -or
    -not $successorAttemptPathSetExact -or
    $attemptPathCountGuards.Count -ne 1 -or
    -not $attemptPathRuntimeProof -or
    $normalTransitionReturnAssignments.Count -ne 1 -or
    $directParentOutputCardinalityGuards.Count -ne 1 -or
    $directParentOutputEqualityGuards.Count -ne 1 -or
    -not $parentAuthorizationExactSchemas -or
    $parentAuthorizationAcceptanceGuards.Count -ne 1 -or
    -not $parentAuthorizationAcceptanceContractComplete -or
    $cutoverBlock.Text -match (
        '\$successorTransitionOutput\s*=\s*@\(\s*' +
        '\$successorTransitionOutput\s*\|'
    ) -or
    $terminalAcceptanceGuards.Count -ne 1 -or
    $apiStageEffectAssignments.Count -ne 1 -or
    $parentApiAuthorizationCalls.Count -ne 1 -or
    $parentApiAuthorizationFunctions.Count -ne 1 -or
    -not $parentApiAuthorizationFunctionComplete -or
    -not (Test-AstInsideExactIfClause -Node $parentApiAuthorizationFunctions[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not $parentApiAuthorizationCallsAllGuarded -or
    $parentApiAuthorizationCallMatches.Count -ne 4 -or
    -not $terminalAcceptanceContractComplete -or
    -not $transitionAcceptanceContractComplete -or
    -not (Test-AstInsideExactIfClause -Node $transitionAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $transitionAcceptanceGuards[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $transitionSchemaAdmissions[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $preStartPathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $livePathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogLeasePathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogTerminalPathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogHeartbeatPathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogCommitPathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogStartFencePathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogStartRevokePathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogStartAttemptPathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogStartCompletionPathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $parentContainmentPathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $parentAuthorizationPathAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $normalTransitionReturnAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $directParentOutputCardinalityGuards[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $directParentOutputEqualityGuards[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $parentAuthorizationAcceptanceGuards[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $watchdogAttemptPathSets[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $attemptPathCountGuards[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $terminalAcceptanceGuards[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    -not (Test-AstInsideExactIfClause -Node $apiStageEffectAssignments[0] `
        -Root $cutoverAst -ConditionPattern $path0047Pattern) -or
    $attemptAssignments[0].Extent.EndOffset -ge
        $preStartPathAssignments[0].Extent.StartOffset -or
    $attemptAssignments[0].Extent.EndOffset -ge
        $livePathAssignments[0].Extent.StartOffset -or
    $attemptAssignments[0].Extent.EndOffset -ge
        $networkRepairPathAssignments[0].Extent.StartOffset -or
    $preStartPathAssignments[0].Extent.EndOffset -ge
        $outputCreateNewGuards[0].Extent.StartOffset -or
    $livePathAssignments[0].Extent.EndOffset -ge
        $outputCreateNewGuards[0].Extent.StartOffset -or
    $networkRepairPathAssignments[0].Extent.EndOffset -ge
        $outputCreateNewGuards[0].Extent.StartOffset -or
    $watchdogLeasePathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $watchdogTerminalPathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $watchdogHeartbeatPathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $watchdogCommitPathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $watchdogStartFencePathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $watchdogStartRevokePathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $watchdogStartAttemptPathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $watchdogStartCompletionPathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $parentContainmentPathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $parentAuthorizationPathAssignments[0].Extent.EndOffset -ge
        $watchdogAttemptPathSets[0].Extent.StartOffset -or
    $watchdogAttemptPathSets[0].Extent.EndOffset -ge
        $outputCreateNewGuards[0].Extent.StartOffset -or
    $outputCreateNewGuards[0].Extent.EndOffset -ge
        $transitionAssignments[0].Extent.StartOffset -or
    $transitionAssignments[0].Extent.EndOffset -ge
        $normalTransitionReturnAssignments[0].Extent.StartOffset -or
    $normalTransitionReturnAssignments[0].Extent.EndOffset -ge
        $directParentOutputCardinalityGuards[0].Extent.StartOffset -or
    $directParentOutputCardinalityGuards[0].Extent.EndOffset -ge
        $directParentOutputEqualityGuards[0].Extent.StartOffset -or
    $transitionAssignments[0].Extent.EndOffset -ge
        $terminalAcceptanceGuards[0].Extent.StartOffset -or
    $terminalAcceptanceGuards[0].Extent.EndOffset -ge
        $parentAuthorizationAcceptanceGuards[0].Extent.StartOffset -or
    $parentAuthorizationAcceptanceGuards[0].Extent.EndOffset -ge
        $apiStageEffectAssignments[0].Extent.StartOffset -or
    $apiStageEffectAssignments[0].Extent.EndOffset -ge
        $transitionAcceptanceGuards[0].Extent.StartOffset -or
    $transitionAcceptanceGuards[0].Extent.EndOffset -ge
        $transitionSchemaAdmissions[0].Extent.StartOffset -or
    $parentApiAuthorizationCalls[0].Extent.StartOffset -le
        $apiStageEffectAssignments[0].Extent.EndOffset -or
    $parentApiAuthorizationCallMatches[1].Index -ge $runtimeProvisionIndex -or
    $parentApiAuthorizationCallMatches[2].Index -le $runtimeProvisionIndex -or
    $parentApiAuthorizationCallMatches[2].Index -ge $apiStageIndex -or
    $parentApiAuthorizationCallMatches[3].Index -le $apiStageIndex -or
    $parentApiAuthorizationCallMatches[3].Index -ge $apiStartIndex
) {
    throw (
        'The cutover must use one fresh attempt ID for all thirteen create-new paths, ' +
        'require non-authorizing watchdog success plus direct parent authority, fully ' +
        'accept 0047, and freshly recheck that authority before each API-side mutation. ' +
        "paths=$($watchdogLeasePathAssignments.Count)/$($watchdogTerminalPathAssignments.Count)/" +
        "$($watchdogHeartbeatPathAssignments.Count)/$($watchdogCommitPathAssignments.Count)/" +
        "$($watchdogStartFencePathAssignments.Count)/$($watchdogStartRevokePathAssignments.Count)/" +
        "$($watchdogStartAttemptPathAssignments.Count)/$($watchdogStartCompletionPathAssignments.Count)/" +
        "$($parentContainmentPathAssignments.Count)/$($parentAuthorizationPathAssignments.Count); " +
        "set=$($attemptPathInitialAssignments.Count)/$($watchdogAttemptPathSets.Count)/" +
        "$successorAttemptPathSetExact/$($attemptPathCountGuards.Count)/" +
        "$attemptPathRuntimeProof; direct=" +
        "$($normalTransitionReturnAssignments.Count)/$($directParentOutputCardinalityGuards.Count)/" +
        "$($directParentOutputEqualityGuards.Count); schemas=$parentAuthorizationExactSchemas; " +
        "parent=$($parentAuthorizationAcceptanceGuards.Count)/" +
        "$parentAuthorizationAcceptanceContractComplete; terminal=$($terminalAcceptanceGuards.Count)/" +
        "$terminalAcceptanceContractComplete; effective=$($apiStageEffectAssignments.Count); " +
        "acceptance=$transitionAcceptanceContractComplete; recheck=" +
        "$($parentApiAuthorizationFunctions.Count)/$parentApiAuthorizationFunctionComplete; calls=" +
        "$($parentApiAuthorizationCalls.Count)/$parentApiAuthorizationCallsAllGuarded/" +
        "$($parentApiAuthorizationCallMatches.Count)"
    )
}

$postgresStartCommands = @(
    $cutoverCommands | Where-Object {
        $_.Extent.Text -match '^docker\s+start\s+\$postgresContainer\[0\]'
    }
)
$migrateStageCommands = @(
    $cutoverCommands | Where-Object {
        $_.Extent.Text -match (
            '(?s)^docker\s+compose\b.*--no-start\b.*' +
            '--force-recreate\s+migrate\s*$'
        )
    }
)
$migrateStartCommands = @(
    $cutoverCommands | Where-Object {
        $_.Extent.Text -match '^docker\s+start\s+\$migrateContainer\b'
    }
)
function Test-SemanticMigrateLifecycleText {
    param([Parameter(Mandatory = $true)][string] $Text)

    $hasLifecycleVerb = $Text -match (
        '(?i)(?:^|[\s,"''(])(?:run|up|create|start|restart)' +
        '(?:$|[\s,"'')])'
    )
    $hasMigrateTarget = $Text -match (
        '(?i)(?:["'']migrate["'']|\bmigrate(?:Container|Service|ContainerName)?\b|' +
        '\$[A-Za-z0-9_]*migrate[A-Za-z0-9_]*)'
    )
    return ($hasLifecycleVerb -and $hasMigrateTarget)
}
$semanticMigrateLifecycleNodes = @(
    $cutoverAst.FindAll({
        param($node)
        (
            $node -is [Management.Automation.Language.CommandAst] -or
            $node -is [Management.Automation.Language.AssignmentStatementAst]
        ) -and (Test-SemanticMigrateLifecycleText -Text $node.Extent.Text)
    }, $true)
)
$semanticMigrateMutants = @(
    'docker compose --profile release run migrate',
    'docker start $migrateContainer',
    'Invoke-BoundedReleaseDockerCommand -DockerArguments @("compose","up","migrate") -WallClockTimeoutMilliseconds 1000 -Label "x"',
    '$startInfo.Arguments = "--context desktop-linux restart migrate"'
)
$semanticMigrateMutantsCaught = @(
    $semanticMigrateMutants | Where-Object {
        $tokens = $null
        $errors = $null
        $mutantAst = [Management.Automation.Language.Parser]::ParseInput(
            $_, [ref]$tokens, [ref]$errors
        )
        @($errors).Count -eq 0 -and @(
            $mutantAst.FindAll({
                param($node)
                (
                    $node -is [Management.Automation.Language.CommandAst] -or
                    $node -is [Management.Automation.Language.AssignmentStatementAst]
                ) -and (Test-SemanticMigrateLifecycleText -Text $node.Extent.Text)
            }, $true)
        ).Count -gt 0
    }
).Count -eq $semanticMigrateMutants.Count
$preMigrationGateAssignments = @(
    $cutoverAssignments | Where-Object {
        $_.Extent.Text -match '^\s*\$preMigrationGate\s*=\s*&' -and
        $_.Extent.Text -match '(?m)^\s+-Mode Create\s+`\r?$'
    }
)
$preMigrationAcceptanceGuards = @(
    $cutoverIfs | Where-Object {
        $_.Clauses[0].Item1.Extent.Text -match '\$preMigrationGate\.verified' -and
        $_.Extent.Text -match 'Pre-migration backup/restore/live-state gate was not accepted'
    }
)
if (
    $postgresStartCommands.Count -ne 0 -or
    $migrateStageCommands.Count -ne 0 -or
    $migrateStartCommands.Count -ne 0 -or
    $semanticMigrateLifecycleNodes.Count -ne 0 -or
    -not $semanticMigrateMutantsCaught -or
    $preMigrationGateAssignments.Count -ne 0 -or
    $preMigrationAcceptanceGuards.Count -ne 0 -or
    $cutoverBlock.Text -match 'Get-ImmediateReleaseHbaByteProof' -or
    $cutoverBlock.Text -match 'Invoke-AcknowledgedRunbookHbaReload' -or
    $cutoverBlock.Text -match 'Get-FixedReleaseHbaByteProof' -or
    $cutoverBlock.Text -match "SELECT\s+'RELOAD\|'\s+\|\|\s+pg_reload_conf"
) {
    throw (
        'Disabled schema 0046 must contain no dormant PostgreSQL start, HBA reload, ' +
        'backup gate, migrate staging, or migrate start body.'
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
    $safetyNetworkIdentityIndex,
    [StringComparison]::Ordinal
)
if (
    $safetyStageIndex -lt 0 -or
    $safetyNetworkIdentityIndex -le $safetyStageIndex -or
    $postgresSafetyAttachIndex -ne -1 -or
    $safetyStartIndex -le $safetyNetworkIdentityIndex
) {
    throw (
        'Release runbook must read-only prove the already repaired PostgreSQL ' +
        'safety-plane endpoint before safety-control starts, without a late attach.'
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
    '$canaryReadyProof = ConvertFrom-ExactCanaryJson -Json $canaryReadyJson',
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
    '$canaryTerminalProof = ConvertFrom-ExactCanaryJson -Json $canaryTerminalJson',
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

foreach ($parentPrimitiveBoundary in @(
    '40 distinct attempt paths',
    'Stratus.ParentStage.Runtime.cs',
    'Test-StratusParentStageWatchdog.ps1',
    'Parent-stage invocation skeleton is WIP and non-authorizing.',
    'Continuous downstream\s+supervision remains mandatory'
)) {
    if ($source -notmatch $parentPrimitiveBoundary) {
        throw "Parent-stage WIP boundary must remain explicit: $parentPrimitiveBoundary"
    }
}
$parentWatchdogPath = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\Invoke-StratusParentStageWatchdog.ps1')
)
$parentTokens = $null
$parentErrors = $null
$parentAst = [Management.Automation.Language.Parser]::ParseFile(
    $parentWatchdogPath, [ref]$parentTokens, [ref]$parentErrors
)
$parentStatements = @($parentAst.EndBlock.Statements)
if (@($parentErrors).Count -ne 0 -or $parentStatements.Count -lt 3 -or
    $parentStatements[2] -isnot [Management.Automation.Language.ThrowStatementAst] -or
    $parentStatements[2].Extent.Text -notmatch 'WIP and non-authorizing') {
    throw 'The independent parent watchdog must retain its unconditional early WIP entry blocker.'
}

[pscustomobject]@{
    runbook = $runbookPath
    r1_prerequisite_authority_rejections = $r1ConsumerFixtures.prerequisite_authority_rejections
    r1_docker_binding_rejections = $r1ConsumerFixtures.docker_binding_rejections
    r1_git_flag_rejections = $r1ConsumerFixtures.git_flag_rejections
    r1_seal_is_non_authorizing = $true
    parent_stage_protocol_acceptance_blocked = $true
    exact_digest_initialized = $true
    schema_0046_fail_closed_blocker_required = $true
    portable_background_host = $true
    runtime_preflight_marker_required = $true
    failed_load_gate_composite_recorded = $true
    continuity_plan_recomputed = $true
    rollback_container_resolved = $true
    safety_control_required = $true
    safety_before_worker_required = $true
    lease_before_resume_required = $true
    bounded_resume_submission_required = $true
    exception_safe_canary_cleanup_required = $true
    outer_canary_lifetime_scope_required = $true
    native_external_proof_types_required = $true
    cutover_schema_path_explicit_before_start_required = $true
    successor_transition_create_new_required = $true
    successor_attempt_path_universe_exact = $true
    successor_transition_before_schema_admission_required = $true
    successor_transition_non_authorizing_watchdog_terminal_required = $true
    successor_parent_authorization_required = $true
    successor_transition_failure_rollback_required = $true
    successor_migration_rerun_forbidden = $true
    historical_0047_inputs_read_only_required = $true
    post_transition_parent_containment_required = $true
    terminal_stop_barrier_required = $true
    validated = $true
} | ConvertTo-Json -Compress

$global:LASTEXITCODE = 0
