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

The guard must return `verified:true`, `linux/amd64`, the exact revision and digest, and these
Compose services: `migrate`, `worker`, `binance-market-stream`, `api`, and `telegram`.

## 3. Stage the candidate API while fail closed

Before replacement, query PostgreSQL directly and record runtime controls, zero LIVE state,
pending commands, active epoch, schema head, and container restart counts. Start only the verified
API:

```powershell
docker compose up -d --no-build --pull never --no-deps --force-recreate api
```

Verify the running container image ID, `RELEASE_IMAGE_DIGEST`, revision label, schema
`20260728_0045`, memory/restart/OOM state, and loopback-only port. `/healthz` must return 200.
`/readyz` is expected to return 503 with `kill_switch` while the global kill is on; this is the
correct fail-closed state, not an API failure.

Do not start the worker yet.

## 4. Production-size API load gate

The predecessor API was stopped after repeated Cockpit refreshes drove PostgreSQL to roughly
150-160% CPU and left long-lived sessions. The candidate must pass a five-minute Dashboard load
gate before epoch activation. Exercise the actual Dashboard request set every 10 seconds,
including health/readiness, overview, signals, positions, paper equity, commands, jobs, edge,
validation ledger, Brier, CLV, root-cause actions, accuracy, selection policy, resolution
reconciliation, settlement cases, storage capacity, forecast provenance, execution cost,
official-resolution sources, evidence continuity, and the alerts badge.

Acceptance thresholds:

- zero request errors and zero client timeouts;
- every Dashboard endpoint completes in less than the Cockpit's three-second client timeout;
- PostgreSQL `idle in transaction` sessions remain zero;
- PostgreSQL lock waiters remain zero;
- all active Dashboard queries drain within five seconds after load stops;
- API and PostgreSQL containers have zero new restarts and are not OOM-killed;
- no recurrence of predecessor-scale sustained PostgreSQL CPU; any sustained approach to
  150% is a release failure;
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
docker compose up -d --no-build --pull never --no-deps --force-recreate worker
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
because maintenance jobs can still write execution evidence.

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
   docker compose up -d --no-build --pull never --no-deps --force-recreate api
   ```

6. Verify the API reports the predecessor digest and a continuity mismatch/fail-closed state.
   Do not activate a plan for the old image and do not start its worker.
7. Forward-fix, build a new exact candidate, restore the candidate environment, and activate a
   new reviewed successor epoch before any worker or PAPER resume.

If the candidate worker cannot process the stop command, stop the worker first. The rollback
remains API-only; no worker may start until a separately audited break-glass action has restored
the database kill switches and the release record identifies that action.

