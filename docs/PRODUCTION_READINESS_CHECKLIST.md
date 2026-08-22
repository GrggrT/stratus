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

Current phase: code-only fail-closed hardening. Production PostgreSQL, API, worker, and
`safety-control` remain stopped and Polymarket PAPER remains paused until the frozen source and
R1 immutable-image transition evidence are accepted. An already-running backup sidecar may remain
running, but it may not start PostgreSQL and its status while PostgreSQL is stopped is not
fresh-backup evidence. Apart from that sidecar, only disposable candidate/test infrastructure is
permitted before the recorded transition.

## 1. Candidate identity and scope

- [ ] Freeze the reviewed source revision: `pending`.
- [ ] Record the candidate image digest: `pending`.
- [x] Record the dependency/lock identity: Python `uv.lock`; Cockpit
      `apps/StratusConsole/packages.lock.json`; .NET SDK `10.0.301`.
- [ ] Prove the frozen candidate source has exactly one Alembic head: `20260730_0047`, directly
      following `20260730_0046`.
- [x] Record the current pre-cutover production head: `20260730_0046`.
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
- [ ] **Canary acceptance item:** consecutive successful scans of within-grace work meet the
      nominal 15-second cadence and are no more than 20 seconds apart. This runtime observation
      is evaluated in Section 9 and is not a prerequisite for starting that canary after every
      other pre-canary item in Sections 1-8 has passed.
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
- [ ] Final exact-candidate clean-PostgreSQL integration suite passes in CI.
- [x] Scheduler registry/restart tests pass.
- [x] A real-PostgreSQL regression proves repeated `EVENT_JOB_MAX_INSTANCES` persistence is
      idempotent and remains non-success in evidence-continuity uptime.
- [x] Concurrent composite/manual invocation tests pass.
- [x] Phase-B rollback isolation test passes.
- [x] Within-grace polling test passes without wall-clock sleeps.
- [x] First-eligible snapshot determinism test passes.
- [x] Existing append-only/hash/trigger tests pass.
- [x] Compose/config validation passes.
- [x] Focused safety-control verification passes: atomic emergency stop, DB-clock lease
      acquire/heartbeat/release, independent watchdog, exact resume identity, empty-active-set
      acquire-vs-stop serialization, and a long unrelated operator job that cannot block STOP.
- [x] The pre-freeze working tree passed the clean-PostgreSQL suite on a fresh isolated,
      loopback-only PostgreSQL 16 container. On 2026-08-14, a newly recreated disposable
      PostgreSQL 16 container with exact image ID
      `sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416`, new data
      volume, and only `127.0.0.1:55435` exposed passed the full suite from migration `0001`
      through `0047`: `1324 passed, 3 skipped, 11 xfailed in 814.15s`. The implementation-content
      digest before and after that run was exactly
      `c6a5af84681bc850d7a87574024d57f2bc3be9f51c53a2070253e686e60210c2` across 78
      implementation paths changed from baseline revision
      `9123d85013af8b15a3f816c9ba70875b2a9973b6`, excluding only this self-referential checklist;
      the test container remained healthy with restart count zero and `OOMKilled=false`.
- [x] Successive exact-SHA CI runs exposed and the candidate fixed cross-version external-proof
      parsing, native ISO timestamp preservation across backup and canary evidence, Windows
      native-application resolution, expected child-process exit-code containment, LF-normalized
      Unix test launchers, and exact Compose byte-string resource parsing in release contracts. The
      current implementation-content digest is
      `772df653e7d131c9ebbba4b370cf46409f2f2f60ccd106aae43967597c84df21` across the same 78
      implementation paths. All affected contracts pass locally in PowerShell 7.6 and Windows
      PowerShell 5.1; all ten Windows PowerShell 5.1 release contracts pass. Exact-candidate full CI
      and clean-PostgreSQL acceptance remain explicitly pending below.
- [ ] Final exact-candidate Windows PowerShell 5.1 and PowerShell 7 release contracts pass in CI.

Historical baseline evidence: Ruff clean; strict mypy clean over 173 source files;
non-PostgreSQL `880 passed, 3 skipped`; CI-equivalent clean PostgreSQL run
`1169 passed, 3 skipped in 1052.21s`; Cockpit locked restore and Release x64 build completed with
0 warnings and 0 errors; backup-sidecar focused test `7 passed, 3 skipped`; rendered Compose
configuration valid. The final replacement working tree now passes local Ruff, strict mypy over
179 source files, the non-PostgreSQL suite with `974 passed, 364 skipped`, all ten PowerShell 5.1
release contracts, AST parsing for all 20 PowerShell
scripts, and the full fresh-PostgreSQL result recorded above. The rendered Compose model is
fail closed with 0 default services, exactly 10 `release` services, and exactly 13 services when
all explicit profiles are selected. These are content-digest-bound working-tree proofs, not
release acceptance. Final remote Windows/PowerShell 7 and Linux CI, plus the candidate Docker
build and immutable image verification, remain pending and must be bound to the frozen candidate
revision.

From a checkout of the candidate revision, the implementation-content digest above is reproducible
from the repository root with this canonical PowerShell recipe. It selects committed implementation
paths by diffing the recorded baseline against `HEAD`, so it remains reproducible after the candidate
is committed. It excludes only this checklist because embedding the resulting hash in the hashed
input would be self-referential; paths are sorted Git paths, sizes are working-tree byte lengths,
per-file hashes are lowercase SHA-256, records are LF-delimited with no trailing delimiter, and the
record stream is UTF-8 without a BOM.

```powershell
$baseline = '9123d85013af8b15a3f816c9ba70875b2a9973b6'
$excluded = 'docs/PRODUCTION_READINESS_CHECKLIST.md'
$paths = @(git diff --name-only --diff-filter=ACMRT $baseline HEAD |
    Where-Object { $_ -ne $excluded } |
    Sort-Object -Unique)
$records = foreach ($path in $paths) {
    $item = Get-Item -LiteralPath $path
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    "$path`t$($item.Length)`t$hash"
}
$bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(($records -join "`n"))
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
} finally {
    $sha256.Dispose()
}
```

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
- [ ] Re-run the same read-only reconciliation immediately before successor activation and bind
      its query hash, output hash, PostgreSQL clock, exact candidate image, and zero-LIVE/control
      snapshot into the release record.

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
- [x] Record the exact rollback image and operator command sequence.
- [x] With global/Polymarket kill and Binance kill engaged and worker/API stopped, run the exact
      candidate `migrate` service from production head `20260728_0045`.
- [x] Before API staging, prove the database is at exactly `20260730_0046` and all three bounded
      accuracy-overview indexes exist with `indisvalid=true` and `indisready=true`.
- [ ] Only after the R1 immutable-image transition evidence is accepted, start the retained exact
      production PostgreSQL container for the bounded backup/migration/load-gate window; record
      its container/image identity and prove API, worker, Telegram, Binance stream, and
      `safety-control` remain stopped.
- [ ] Retain and isolated-restore a new hash-bound backup of the exact `20260730_0046` database
      before applying the safety migration.
- [ ] With every application writer stopped, apply `20260730_0047` from the exact candidate and
      prove the lease table, both lease indexes, four control-plane triggers, five deferred
      execution commit-fence triggers, four runtime identity/integrity triggers, six trigger
      functions, runtime/safety-role boundaries, and exactly one Alembic head.
- [ ] Stage/deploy the exact candidate artifact while candidate scheduler writes remain blocked
      under the predecessor execution-evidence identity.
- [ ] Verify API/worker identity, schema, configuration, memory limit, and restart count before
      epoch activation.
- [ ] Pass the production-size Dashboard API load gate with all three hash-bound artifacts:
      HTTP schema v3, runtime schema v2, and composite accepted result. They must share one run
      ID, exact candidate image, full observation overlap, raw cadence/sample reconciliation, and
      the final five-second drain; require zero errors/timeouts, endpoint maximum below 3 seconds,
      zero idle-in-transaction sessions and lock waiters, no restart/OOM, and no recurrence of
      predecessor 150-160% sustained PostgreSQL CPU.
- [x] Confirm zero LIVE rows in orders, positions, and execution intents before any PAPER resume.

Backup evidence: marker-bound
`stratus_20260729T203148Z_3241d8921b44_1.backup`, 2,388,137,173 bytes, archive SHA-256
`4ed6ac9932647d3e2c25df3052a5d1e9034594005a6edf3fb508fd2ca856358c`, PostgreSQL
16.13, manifest SHA-256
`52b8adda0148c695bb58e0a3a7b0f2c63574a0b8c24a25158edb6ffb3482e9a8`. Full
isolated restore and owner/restricted-role boundary checks passed after the required external
database-ACL bootstrap. Disposable container and volume were removed.

Deployment evidence: the staged API attempt for revision
`09945edfd4920df8c6102503accfd24c36c000d6`, image
`sha256:3d793e5b64788a37fd0d37635449bf1deeab3cfad4d152c172dd130433951cef`,
and a 1 GiB API limit ran from `2026-07-29T22:32:01Z` through a clean stop at
`2026-07-29T22:39:44Z`. Health returned 200 and readiness returned the expected fail-closed 503
for kill/stale state. Independent observed maxima were 18 PostgreSQL application sessions,
6 active, 13 `idle in transaction`, zero lock waiters, PostgreSQL CPU `308.43%`, API CPU
`126.06%`, PostgreSQL memory `2282.5 MiB`, and API memory `463.7 MiB`. API/PostgreSQL had zero
restart and OOM events. After the API stop, application sessions drained to `0/0/0`
(active / `idle in transaction` / lock-waiting). The worker was not started, the epoch and
Polymarket PAPER state were untouched, and LIVE remained untouched.

**Disposition: failed, not accepted.** The unchecked load-gate item above remains blocking.
Query/cadence/transaction-lifetime/database-budget remediation and a new exact-candidate load
gate are required before successor-epoch activation or PAPER resume.

Rollback record: `stratus:rollback-epoch21-20260729`,
`sha256:f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4`.
After successor activation it is API-only; its legacy worker is forbidden because the old
maintenance topology can write execution evidence even while trading is killed. Exact commands,
fail-closed checks, and forward-fix requirements are in
`docs/RELEASE_CUTOVER_RUNBOOK.md`.

Historical schema cutover evidence: **0046 migration accepted, application candidate not
accepted**. Under stopped
API/worker and engaged global, Polymarket, and Binance kill switches, production advanced once
from `20260728_0045` to `20260730_0046`. Direct SQL proved exactly one Alembic head and
`indisvalid=true` plus `indisready=true` for all three bounded accuracy-overview indexes. The
incident record contains the migration-staging command correction and evidence. This accepted
schema state does not accept any staged application image, activate a successor epoch, resume
PAPER, or authorize LIVE.

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
- [ ] Before starting `safety-control` or the worker, exercise the retained predecessor as an
      API-only post-activation diagnostic while global/Polymarket and Binance kills remain
      engaged. Require its exact retained image/digest, continuity mismatch against the active
      successor, zero application writers/LIVE/pending commands, and unchanged plan, epoch, lane,
      and evidence identities; never run its `migrate` or `worker` service.
- [ ] Stop that predecessor API, restore and identity-check the exact candidate API, and require
      `evidence_continuity_match` plus the unchanged post-activation database snapshot before the
      cold-worker gate. This diagnostic proof is not permission to downgrade schema or resume
      PAPER.

Plan/epoch evidence: `pending`.

## 9. PAPER canary

Canary may begin only after Sections 1-8 pass, except for the explicitly deferred runtime-cadence
acceptance observation in Section 4, which can only be measured during this section. It does not
authorize LIVE.

- [ ] Prepare and dry-verify the immutable T+0/T+5/T+15/T+30/T+45/T+60 snapshot collector, its
      independent authenticated emergency-stop path, DB-clock lease/watchdog contract, and
      terminal atomic release+STOP before any resume request.
- [ ] Stage and identity-check the independent `safety-control` service from the exact candidate
      without acquiring a lease; require loopback-only port 8001, a one-connection pool, a fresh
      watchdog pass, `/healthz=200`, `/readyz=200`, and the same image/revision/release digest as
      API and worker.
- [ ] Start the candidate worker on the activated successor epoch.
- [ ] Keep global and both venue kill switches engaged until a fresh non-critical health audit
      exists, every required job is present and fresh, `stalled_days` is stable, continuity
      matches the exact candidate, pending commands are zero, and LIVE state is zero.
- [ ] Observe at least four consecutive 15-second
      `execution-cost-maintenance` runs without a legacy scheduled run.
- [ ] Verify each run reports the captured clock and separate Phase-A/Phase-B outcomes.
- [ ] Seed no synthetic production fill; wait for natural PAPER work or use a reviewed isolated
      canary fixture outside production evidence. If no fill or cost obligation arises naturally,
      record that absence separately; it does not fail runtime-stability acceptance or prove
      execution quality.
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
- [ ] Only after the killed cold-worker gate passes, acquire exactly one short DB-clock canary
      lease through `safety-control`, prove that the watchdog has observed it, and bind its nonce
      hash plus exact resume/release request identities, actor, and reason into immutable
      preflight/ready artifacts.
- [ ] Only after the create-new ready marker exists, submit the lease-reserved audited command
      whose payload is exactly `venue=polymarket`; wait for that exact request ID to succeed and
      reject any all-venue, payload-mismatched, actor-mismatched, or reason-mismatched command.
- [ ] Verify Polymarket remains PAPER-only and Binance/LIVE controls remain fail closed.
- [ ] On success, atomically turn global and Polymarket kills ON and terminalize the lease as
      `released`; on divergence or lease expiry, independently STOP before diagnostics and
      terminalize the exact pending resume without changing Binance.

Collector implementation evidence: `scripts/Invoke-StratusPaperCanary.ps1` and
`scripts/tests/Test-StratusPaperCanary.ps1` now pass PowerShell 5.1 syntax, no-I/O validation,
executable guard/stop contract tests, and the fixed T+0/5/15/30/45/60 schedule. Exact Guard/Full
SQL execution also passed against a clean migrated PostgreSQL 16 working database. The
frozen-candidate CI job, operator credential, create-new ready marker, and authenticated runtime
stop path remain open; the implementation is not runtime-accepted yet.

Canary start: `pending`.

Canary end: `pending`.

Canary evidence: `pending`.

## 10. Monitoring and alerting

- [ ] Job freshness monitors the composite id and 15-second cadence with a reviewed tolerance.
- [ ] Alert immediately on durable `max_instances`; treat it as non-success and never as
      execution evidence.
- [ ] Alert on stale/repeatedly failing safety watchdog, lease expiry, lease revocation, rejected
      reserved resume, and any safety-control/API image or readiness divergence.
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
