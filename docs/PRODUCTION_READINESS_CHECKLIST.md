# Production readiness checklist: execution-cost maintenance P0

- Date opened: 2026-07-29
- Owner: Codex engineering
- Reviewer: pending
- Status: not ready for production acceptance
- Incident: `docs/incidents/2026-07-29_EXECUTION_MARKOUT_STARVATION.md`

This checklist is the release gate for the execution-markout starvation remediation. An unchecked
item is pending, not implicitly satisfied. Command output, timestamps, identifiers, hashes, and
PostgreSQL query results must be attached to the release record; prose confirmation alone is not
acceptance.

Deployment, successor-epoch activation, and canary are currently **pending**.

## 1. Candidate identity and scope

- [ ] Freeze the reviewed source revision: `pending`.
- [ ] Record the candidate image digest: `pending`.
- [x] Record the dependency/lock identity: Python `uv.lock`; Cockpit
      `apps/StratusConsole/packages.lock.json`; .NET SDK `10.0.301`.
- [x] Record the Alembic head and prove there is exactly one head: `20260728_0045`.
- [ ] Confirm the diff is limited to the reviewed P0 scope plus required tests/docs.
- [ ] Confirm no execution-cost definition, horizon, grace duration, algebra, or immutable hash
      contract changed without an explicit reviewed migration and continuity decision.
- [ ] Confirm LIVE defaults, venue controls, risk limits, and legal/quantitative gates are
      unchanged and fail closed.

## 2. Scheduler topology

- [x] Scheduler registration contains exactly one recurring job with id
      `execution-cost-maintenance`.
- [x] Its interval is exactly 15 seconds.
- [x] `execution-markouts` is absent from recurring scheduler registration.
- [x] `execution-cost-finalization` is absent from recurring scheduler registration.
- [x] Both legacy entry points are documented and tested as manual-only.
- [x] Startup-job registration cannot run the legacy jobs as an accidental second recurring path.
- [x] Overlap/restart behavior is bounded and idempotent; a second composite invocation cannot
      create duplicate marks or seals.
- [x] Every `EVENT_JOB_MAX_INSTANCES` scheduled time is durably and idempotently recorded as
      `max_instances`, alerts the operator once, and counts as non-success for uptime/continuity.
- [x] No `EVENT_JOB_MAX_INSTANCES` path synthesizes a completed run or Phase-A/Phase-B evidence.
- [x] Health-required job names and freshness thresholds use
      `execution-cost-maintenance`, not the manual-only ids.

Evidence: scheduler registry/runtime unit tests plus real-PostgreSQL
`tests/integration/test_scheduler_events_pg.py`; included in the 1169-test clean-PostgreSQL run.

## 3. Composite transaction contract

- [x] One invocation captures exactly one PostgreSQL-authoritative clock value.
- [x] The same captured timestamp is passed to Phase A and Phase B.
- [x] Phase A uses a short transaction and commits markouts before Phase B begins.
- [x] Phase B uses a separate short transaction and commits finalization independently.
- [x] A forced Phase-B exception rolls back Phase B but leaves valid Phase-A marks durable.
- [x] A retry after that exception appends no duplicate mark and produces at most one immutable
      cost seal.
- [x] No network call or unbounded scan occurs while an obligation row lock is held.

Evidence: composite maintenance unit tests and clean-PostgreSQL rollback/lock tests in the
1169-test release run.

## 4. Repository ownership and grace polling

- [x] The markout claim predicate selects only obligations with unresolved applicable mark work
      whose target is due at the captured clock.
- [x] The finalizer claim predicate selects only obligations with no unresolved applicable marks,
      or obligations whose evidence contract makes marks not applicable.
- [x] A PostgreSQL concurrency test proves the predicates are disjoint in the same database state.
- [x] Manual and scheduled callers use those same disjoint repository predicates.
- [x] Every pass searches for the deterministic first eligible immutable snapshot in the inclusive
      interval `[target_at, target_at + mark_grace]`.
- [x] Before grace expiry, absence of a snapshot sets the stored next-due clock to the captured
      clock, making the mark immediately eligible for the next composite invocation.
- [ ] Consecutive successful scans of within-grace work meet the nominal 15-second cadence and are
      no more than 20 seconds apart.
- [x] At or after grace expiry, absence of an eligible snapshot appends exactly one immutable
      `missing` mark with the stable reason.
- [x] Boundary tests cover snapshots exactly at target, inside grace, exactly at grace end, and
      after grace.
- [x] Purchased-token side, token id, completeness, eligibility, bid/ask/midpoint, record hash,
      and first-seen database clock filters remain fail closed.

Evidence: execution-cost planner/repository unit and PostgreSQL integration tests in the
1169-test release run. Runtime cadence still requires the candidate canary in Section 9.

## 5. Verification

- [x] Scoped Ruff passes.
- [x] Strict mypy passes for every changed Python module.
- [x] Focused unit tests pass.
- [x] Full non-PostgreSQL suite passes.
- [x] Focused clean-PostgreSQL execution-cost tests pass.
- [x] Full clean-PostgreSQL integration suite passes.
- [x] Scheduler registry/restart tests pass.
- [x] A real-PostgreSQL regression proves repeated `EVENT_JOB_MAX_INSTANCES` persistence is
      idempotent and remains non-success in evidence-continuity uptime.
- [x] Concurrent composite/manual invocation tests pass.
- [x] Phase-B rollback isolation test passes.
- [x] Within-grace polling test passes without wall-clock sleeps.
- [x] First-eligible snapshot determinism test passes.
- [x] Existing append-only/hash/trigger tests pass.
- [x] Compose/config validation passes.

Record exact commands, counts, skips, and timestamps: Ruff clean; strict mypy clean over 173 source
files; non-PostgreSQL `880 passed, 3 skipped`; CI-equivalent clean PostgreSQL run
`1169 passed, 3 skipped in 1052.21s`; Cockpit locked restore and Release x64 build completed with
0 warnings and 0 errors; backup-sidecar focused test `7 passed, 3 skipped`; rendered Compose
configuration valid. Candidate Docker build remains part of Section 1.

## 6. Incident reconciliation

Run read-only reconciliation against the production backup and again immediately before cutover.
Do not repair rows as part of the query.

- [x] Count all open obligations by mode, state, epoch, and oldest due time.
- [x] Enumerate every required leg/horizon mark and its observed/missing/unresolved status.
- [x] Independently derive the first eligible snapshot in each frozen mark window.
- [x] Compare every stored observed mark id/hash/time with that first eligible snapshot.
- [x] Identify every mark materialized after target plus grace.
- [x] Identify any cost seal whose applicable marks were unresolved when sealed.
- [x] Prove uniqueness of `(fill_leg_id, horizon_seconds)` marks and one seal per obligation/order.
- [x] Recompute immutable record hashes and report zero invalid rows.
- [x] Report PAPER and LIVE separately.
- [x] Preserve a hash-bound reconciliation artifact and query text.

Production reconciliation result: predecessor at `2026-07-29T20:18:14.093844Z` and the exact
restored post-stop backup both passed. Restored copy: 857 sealed obligations, 192 valid mark
hashes, 857 valid seal hashes, and zero unresolved-after-grace, projection mismatch, false
missing, premature seal, duplicate, nonsealed, or LIVE findings. Query:
`scripts/sql/execution_cost_incident_reconciliation.sql`; local artifact:
`.stratus-cache/restore-20260729T203148Z/execution-cost-reconciliation.txt`.

## 7. Backup, rollback, and fail-closed cutover

- [x] Hold new execution behind the kill switch before replacement.
- [x] Record a complete pre-cutover backup path, size, SHA-256, and PostgreSQL version.
- [x] Restore that exact backup into an isolated database and run integrity checks.
- [ ] Record the exact rollback image and operator command sequence.
- [ ] Prove rollback cannot relabel candidate evidence into the predecessor epoch.
- [ ] Stage/deploy the exact candidate artifact while candidate scheduler writes remain blocked
      under the predecessor execution-evidence identity.
- [ ] Verify API/worker identity, schema, configuration, memory limit, and restart count before
      epoch activation.
- [x] Confirm zero nonterminal LIVE orders and zero LIVE positions before any PAPER resume.

Backup evidence: marker-bound
`stratus_20260729T203148Z_3241d8921b44_1.backup`, 2,388,137,173 bytes, archive SHA-256
`4ed6ac9932647d3e2c25df3052a5d1e9034594005a6edf3fb508fd2ca856358c`, PostgreSQL
16.13, manifest SHA-256
`52b8adda0148c695bb58e0a3a7b0f2c63574a0b8c24a25158edb6ffb3482e9a8`. Full
isolated restore and owner/restricted-role boundary checks passed after the required external
database-ACL bootstrap. Disposable container and volume were removed.

Deployment evidence: `pending`.

## 8. Continuity plan and successor epoch

The successor epoch must be created after test verification and exact deployed/staged artifact
verification, but before the candidate scheduler writes execution evidence or PAPER execution is
resumed.

- [ ] Generate a plan against the exact deployed candidate identity.
- [ ] Fork `execution_evidence` at a PostgreSQL-clock zero-backfill boundary.
- [ ] Derive the action for every other lane, including `operational_legacy` and
      `selection_policy`, strictly from the complete computed release-plan hash diff; do not
      predeclare fork or continue.
- [ ] Record rationale that separates the material execution-evidence semantic change from any
      incidental hash change caused by a shared source file such as `runtime.py`.
- [ ] Fail closed on every mismatched or unclassified lane identity.
- [ ] Record reviewer, rationale, parent epoch, plan hash, release-manifest hash, lane-manifest
      hash, and candidate image digest.
- [ ] Activate exactly one complete epoch; no partial or duplicate lane set exists.
- [ ] Record the database activation timestamp and epoch-record hash.
- [ ] Verify `evidence_continuity_match` against the exact candidate process.
- [ ] Prove no predecessor row was copied, backfilled, or relabelled into the new lane.

Plan/epoch evidence: `pending`.

## 9. PAPER canary

Canary may begin only after Sections 1–8 pass. It does not authorize LIVE.

- [ ] Start the candidate worker on the activated successor epoch.
- [ ] Observe at least four consecutive 15-second
      `execution-cost-maintenance` runs without a legacy scheduled run.
- [ ] Verify each run reports the captured clock and separate Phase-A/Phase-B outcomes.
- [ ] Seed no synthetic production fill; wait for natural PAPER work or use a reviewed isolated
      canary fixture outside production evidence.
- [ ] For due production work, prove Phase A commits before Phase B observes finalizability.
- [ ] Prove an unresolved within-grace mark remains eligible on successive passes.
- [ ] Measure consecutive successful within-grace scans: nominal interval 15 seconds and every
      observed interval no greater than 20 seconds.
- [ ] Prove the earliest eligible persisted snapshot is selected when it arrives during grace.
- [ ] Verify oldest-due age decreases or has an explicit within-grace/no-snapshot explanation.
- [ ] Verify zero duplicate marks/seals and zero invalid hashes.
- [ ] Verify no new error/critical/traceback log, worker restart, OOM event,
      `max_instances` event, or scheduler overlap.
- [ ] Verify `/healthz`, `/readyz`, execution-cost reporting, and continuity state against direct
      PostgreSQL counts.
- [ ] Verify Polymarket remains PAPER-only and Binance/LIVE controls remain fail closed.

Canary start: `pending`.

Canary end: `pending`.

Canary evidence: `pending`.

## 10. Monitoring and alerting

- [ ] Job freshness monitors the composite id and 15-second cadence with a reviewed tolerance.
- [ ] Alert immediately on durable `max_instances`; treat it as non-success and never as
      execution evidence.
- [ ] Alert when consecutive successful within-grace scans exceed the 20-second SLO.
- [ ] Alert on oldest unresolved target age, not merely most recent successful job run.
- [ ] Alert on an unresolved mark after target plus grace.
- [ ] Alert on a finalizable obligation that remains unsealed.
- [ ] Alert on predicate overlap/invariant failure, duplicate mark/seal, or invalid hash.
- [ ] Dashboard/log fields separate Phase A claimed/observed/missing/deferred counts from Phase B
      claimed/sealed/deferred counts.
- [ ] Read-only monitoring has no authority to switch mode, write evidence, or enable LIVE.
- [ ] Operator runbook explains manual-only jobs and forbids scheduling them alongside the
      composite job.

Monitoring evidence: `pending`.

## 11. Acceptance and closure

- [ ] Independent reviewer confirms every item above has attached evidence.
- [ ] No unresolved critical/blocking health or reconciliation finding remains.
- [ ] Incident record is updated append-only with exact acceptance facts.
- [ ] `docs/QUALITY_ROADMAP.md` is updated with the accepted epoch/image only after acceptance.
- [ ] Deployment status is changed from `pending` only from an authoritative deployment record.
- [ ] Canary status is changed from `pending` only from the completed canary artifact.
- [ ] Incident is closed by named owner and reviewer with a database timestamp.

Final disposition: `pending`.
