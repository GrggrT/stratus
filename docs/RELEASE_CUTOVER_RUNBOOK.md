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

Any failed invariant stops the procedure. Do not restart Docker, change venue modes, or bypass an
evidence-continuity mismatch to make a check pass.

## 2. Freeze and verify the candidate

Use a 40-character commit and a tag derived from its first 12 characters:

```powershell
$revision = git rev-parse HEAD
$image = "stratus:candidate-$($revision.Substring(0, 12))"
docker buildx build --platform linux/amd64 --pull --load `
  --label "org.opencontainers.image.revision=$revision" `
  --tag $image C:\path\to\clean-detached-worktree
```

Inspect the local image and record its 64-hex image ID without the `sha256:` prefix. Set only
`STRATUS_IMAGE` and `STRATUS_RELEASE_IMAGE_DIGEST` in the ignored production `.env`, then run:

```powershell
.\scripts\Assert-StratusReleaseImage.ps1 `
  -Image $image `
  -ExpectedDigest $digest `
  -ExpectedRevision $revision `
  -EnvFile .env
```

The guard must return `verified:true`, `linux/amd64`, the exact revision and digest, these Compose
services: `migrate`, `worker`, `binance-market-stream`, `api`, and `telegram`, and positive
resource limits for both `worker` and `api`.

## 3. Apply the candidate migration and stage the API while fail closed

Before replacement, query PostgreSQL directly and record runtime controls, zero LIVE state,
pending commands, active epoch, schema head, and container restart counts. The expected
pre-migration production head is exactly `20260728_0045`; a different value stops the cutover.

Revision `20260730_0046` is an index-only operational migration for bounded Cockpit/API reads.
Apply it from the exact verified candidate while global/Polymarket kill and Binance kill are both
engaged and every application writer is stopped. In addition to the API and worker, this includes
the Telegram service and Binance market stream. Before the DDL starts, require zero
`stratus-api`/`stratus-runtime` database sessions, zero idle-in-transaction sessions, zero lock
waiters, and no active `pg_dump`. Retain a hash-bound, successfully restored backup of the exact
pre-migration `20260728_0045` database:

```powershell
docker compose stop worker api telegram binance-market-stream
$unexpectedRunning = @(
  docker compose ps --status running --services |
    Where-Object {
      $_ -in @("worker", "api", "telegram", "binance-market-stream")
    }
)
if ($unexpectedRunning.Count -ne 0) {
  throw "Application writers must be stopped before migration: $($unexpectedRunning -join ', ')"
}

# Create without starting so the image can be proved before any DDL executes.
docker compose create --no-build --pull never --force-recreate migrate
$migrateContainer = docker compose ps --all -q migrate
if (@($migrateContainer).Count -ne 1) {
  throw "Expected exactly one stopped migrate container."
}
$migrateImageId = docker inspect $migrateContainer --format "{{.Image}}"
if ($migrateImageId -ne "sha256:$digest") {
  docker rm -f $migrateContainer | Out-Null
  throw "Migrate container image mismatch: $migrateImageId"
}

docker start $migrateContainer | Out-Null
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
```

The running-service assertion must return no API or worker before `migrate` runs. `--pull never`,
the pre-start image-ID assertion, and the stopped-container creation are mandatory: no DDL may
execute before the exact candidate identity is proven. Do not use the predecessor image's
migration service. After migration, require exactly one database head, `20260730_0046`, then query
`pg_index`/`pg_class` and require all three candidate indexes to exist with both
`indisvalid=true` and `indisready=true`:

- `ix_accuracy_predictions_eligible_expert_ts`;
- `ix_accuracy_predictions_eligible_expert_version_market_ts`;
- `ix_model_skill_window_evaluated`.

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
    'ix_model_skill_window_evaluated'
  )
ORDER BY c.relname;
```

The first query must return exactly one row containing `20260730_0046`; the second must return
exactly three rows and six `true` flags.

Any migration error, unexpected head, missing index, invalid index, or not-ready index stops the
cutover with the API and worker still off. Only after these checks pass may the verified candidate
API be staged. Create it stopped and prove its image/revision/digest before the first application
process starts:

```powershell
docker compose up --no-start --no-build --pull never --no-deps --force-recreate api
$apiContainer = @(docker compose ps --all -q api)
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
if (
  $apiInspect.Config.Labels.'org.opencontainers.image.revision' -ne $revision
) {
  throw "API revision label mismatch."
}
if (
  @(
    $apiInspect.Config.Env |
      Where-Object { $_ -eq "RELEASE_IMAGE_DIGEST=$digest" }
  ).Count -ne 1
) {
  throw "API release digest environment mismatch."
}
docker start $apiContainer[0] | Out-Null
```

Verify the running container image ID, `RELEASE_IMAGE_DIGEST`, revision label, schema
`20260730_0046`, memory/restart/OOM state, and loopback-only port. `/healthz` must return 200.
`/readyz` is expected to return 503 with `kill_switch` while the global kill is on; this is the
correct fail-closed state, not an API failure.

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

```powershell
$secureApiKey = Read-Host "Staged operator API key" -AsSecureString
.\scripts\Invoke-StratusDashboardLoadGate.ps1 `
  -BaseUri "http://127.0.0.1:8000/" `
  -ApiKey $secureApiKey `
  -SteadyStateSeconds 300 `
  -OutputPath ".stratus-cache\load-gates\candidate-dashboard.json"
if ($LASTEXITCODE -ne 0) {
    throw "Candidate Dashboard HTTP subgate failed with exit code $LASTEXITCODE."
}
```

Do not run the harness against the predecessor API or while the worker is being started/stopped.
The JSON artifact contains every endpoint status/latency/error plus per-endpoint aggregates; it
contains neither response bodies nor the API key. Its contract is
`gate=stratus_dashboard_http_load`, `http_passed=true|false`,
`requires_external_runtime_observation=true`, and `production_gate_passed=null`. Exit code zero
means only that this HTTP subgate passed; it never accepts the complete production gate.

Collect a separate, timestamp-aligned, read-only runtime-observation artifact in parallel. It must
contain PostgreSQL session/transaction/lock samples, API and PostgreSQL CPU/memory/restart/OOM
samples, unchanged global and venue controls, proof that LIVE orders/positions/intents remain
zero, and the five-second post-load query/session drain result. Without both this runtime artifact
and the HTTP artifact, the overall production load gate is **not accepted**.

HTTP-subgate thresholds:

- zero request errors and zero client timeouts;
- every Dashboard endpoint completes in less than the Cockpit's three-second client timeout;
- every `/readyz` sample is HTTP 503 with `status=not_ready`, contains `kill_switch`, and contains
  no disallowed readiness reason.

External runtime-observation thresholds:

- API database sessions never exceed the configured pool bound of four;
- PostgreSQL `idle in transaction` sessions remain zero;
- PostgreSQL lock waiters remain zero;
- all active Dashboard queries drain within five seconds after load stops;
- API and PostgreSQL containers have zero new restarts and are not OOM-killed;
- API memory remains below 90% of its 1 GiB limit;
- PostgreSQL CPU fails the gate if any 30-second rolling average is at least 140%, two consecutive
  five-second samples are at least 150%, or any single sample is at least 200%;
- global/venue controls and all zero-LIVE invariants remain unchanged.

Record per-endpoint count, maximum latency, error count, PostgreSQL session/lock samples, container
CPU/memory samples, and the five-second drain result. If this gate fails, stop the candidate API
and fix query bounds, server/database timeouts, or Cockpit cancellation/coalescing before
continuing.

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
docker compose up --no-start --no-build --pull never --no-deps --force-recreate worker
$workerContainer = @(docker compose ps --all -q worker)
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
if (
  $workerInspect.Config.Labels.'org.opencontainers.image.revision' -ne $revision
) {
  throw "Worker revision label mismatch."
}
if (
  @(
    $workerInspect.Config.Env |
      Where-Object { $_ -eq "RELEASE_IMAGE_DIGEST=$digest" }
  ).Count -ne 1
) {
  throw "Worker release digest environment mismatch."
}
```

Recompute the plan immediately before activation and require the plan hash to be unchanged. Then:

```powershell
$activationBody = @{
  reviewed_by = "codex-release-owner"
  approval_reason = $reason
  expected_plan_hash = $plan.plan_hash
} | ConvertTo-Json
$activation = Invoke-RestMethod -Method Post -Headers $headers `
  -ContentType "application/json" -Body $activationBody `
  -Uri "http://127.0.0.1:8000/api/v1/evidence-continuity/activate"
```

Require one successor epoch, parent 21, the exact plan/image/manifest identities, exactly six lane
rows, and a PostgreSQL activation timestamp. `GET /api/v1/evidence-continuity` must report an
identity match for the candidate. Direct SQL must prove no predecessor evidence row was copied,
backfilled, or relabelled into a forked lane.

## 7. Cold scheduler canary

Start only the verified candidate worker:

```powershell
docker start $workerContainer[0] | Out-Null
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

## 8. Resume Polymarket PAPER only

Enqueue one authenticated, idempotent command:

```powershell
$requestId = "release-paper-" + [guid]::NewGuid().ToString("N")
$resumeBody = @{
  request_id = $requestId
  command = "resume"
  reason = "Successor epoch and cold scheduler canary accepted; resume Polymarket PAPER only."
  actor = "codex-release-owner"
  payload = @{ venue = "polymarket" }
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Headers $headers `
  -ContentType "application/json" -Body $resumeBody `
  -Uri "http://127.0.0.1:8000/api/v1/commands"
```

Wait for that exact request ID to become `succeeded`. Require global kill false, Polymarket
enabled+paper+kill=false, Binance enabled+backtest+kill=true, and zero LIVE state. A pending,
rejected, failed, duplicate-with-different-payload, or all-venue command stops the release.

## 9. Sixty-minute PAPER canary

Capture immutable snapshots at T+0, T+5, T+15, T+30, T+45, and T+60. Each snapshot records:

- `/healthz`, `/readyz`, API/worker/PostgreSQL state, exact image ID, restarts, OOM, CPU/memory;
- global and per-venue controls, zero LIVE state, and pending operator commands;
- latest health audit and all critical/blocking findings;
- active epoch/image/cohort/gate and every lane identity;
- required job freshness, missing jobs, `stalled_days`, and composite run gaps;
- PAPER maker intents, resting orders, fills, cost seals, 5m/30m markouts,
  enrollments/outcomes, oldest due age, duplicates, and hash validity.

Immediately stop Polymarket PAPER through the audited queue on any safety divergence, unexpected
LIVE activity, digest/mode change, missing required job, growing `stalled_days`, unresolved
post-grace mark, repeat finalizer claim with `claimed=0`, scheduler contention, sustained API/DB
load regression, restart/OOM, or critical finding. Do not automatically restart Docker.

## 10. Safe rollback

The retained predecessor is:

- tag: `stratus:rollback-epoch21-20260729`;
- image ID: `sha256:f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4`.

After successor activation this image is an **API-only diagnostic rollback**. Its worker contains
the old competing scheduler topology and must never be started, even while trading is killed,
because maintenance jobs can still write execution evidence. Revision `20260730_0046` is an
additive, index-only forward schema change: do not downgrade production to `20260728_0045` merely
to run the predecessor API. This rollback is only a fail-closed diagnostic bridge to a
forward-fixed candidate; the predecessor `migrate` and `worker` services remain forbidden.

Rollback sequence:

1. While the candidate worker is healthy, enqueue `stop` with
   `payload.venue="polymarket"` and wait for the exact request ID to succeed.
2. Stop the worker and verify it is not running:

   ```powershell
   docker compose stop worker
   docker inspect weatherpoly-worker-1 --format "{{.State.Running}}"
   ```

3. Confirm global and Polymarket kill are true, Binance is still backtest+kill=true, LIVE and
   pending commands are zero.
4. Inspect the retained rollback tag and require the exact image ID above. Set `.env`
   `STRATUS_IMAGE=stratus:rollback-epoch21-20260729` and
   `STRATUS_RELEASE_IMAGE_DIGEST=f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4`.
5. Recreate **only** the API:

   ```powershell
   docker compose up --no-start --no-build --pull never --no-deps --force-recreate api
   $rollbackApi = @(docker compose ps --all -q api)
   if (
     $rollbackApi.Count -ne 1 -or
     (docker inspect $rollbackApi[0] --format "{{.State.Running}}") -ne "false" -or
     (docker inspect $rollbackApi[0] --format "{{.Image}}") -ne
       "sha256:f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4"
   ) {
     throw "Predecessor API stopped-container identity check failed."
   }
   docker start $rollbackApi[0] | Out-Null
   ```

6. Verify the API reports the predecessor digest and a continuity mismatch/fail-closed state.
   Do not activate a plan for the old image and do not start its worker.
7. Forward-fix against the current schema, build a new exact candidate, restore the candidate
   environment, and activate a new reviewed successor epoch before any worker or PAPER resume.

If the candidate worker cannot process the stop command, stop the worker first. The rollback
remains API-only; no worker may start until a separately audited break-glass action has restored
the database kill switches and the release record identifies that action.
