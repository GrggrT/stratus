# P0 incident: execution markout starvation

- Incident date: 2026-07-29
- Severity: P0
- Status: remediation in progress; verification, deployment, canary, and successor evidence epoch
  are pending
- Scope: execution-cost evidence maintenance; no LIVE authority is granted by this incident work

Related documents:

- `docs/EXECUTION_COST_PROTOCOL.md`
- `docs/EVIDENCE_CONTINUITY_PROTOCOL.md`
- `docs/PRODUCTION_READINESS_CHECKLIST.md`
- `docs/QUALITY_ROADMAP.md`

## Summary

The production scheduler registered `execution-markouts` and
`execution-cost-finalization` as independent jobs with the same cadence and start time. Both
repository paths could claim the same due `execution_cost_obligations` rows with
`FOR UPDATE SKIP LOCKED`. A finalizer that won the row lock could repeatedly prevent the markout
worker from examining the obligation. The predicates were overlapping even though only the
markout phase could make the obligation finalizable.

The observed epoch-21 fill eventually received its valid 5-minute mark from an already persisted
eligible snapshot, but that success does not bound the general delay. Root-cause review therefore
reclassifies the earlier P2 scheduler-race note as a P0 execution-evidence liveness incident.

A second liveness defect amplified the contention. When no eligible snapshot existed at the first
poll after a mark target, the markout path advanced `next_due_at` to the end of the grace window.
It did not poll for snapshots that became eligible during the grace window. The immutable
snapshot ledger preserved source evidence, but mark materialization could be delayed until grace
expiry.

This incident concerns evidence completion and scheduler correctness. It does not establish
forecasting skill, edge, profitability, or LIVE readiness.

## Known impact and non-claims

Known impact:

- due markout work and finalization work competed for the same obligation lock;
- repeated finalizer lock wins could starve markout progress for an unbounded number of runs;
- an obligation without a snapshot at its first target poll was not reconsidered on each scheduler
  pass during grace;
- job freshness alone could look healthy while the evidence obligation made no useful progress;
- the previous two-job topology could not guarantee markout-before-finalizer ordering.

The epoch-21 observation showed delayed materialization, not corrupted evidence: the stored
5-minute mark cited the correct pre-existing eligible snapshot. This single observation is not
proof that every affected obligation was eventually completed correctly. Production reconciliation
must therefore enumerate every open, overdue, and recently sealed obligation before acceptance.

No document in this incident record claims that:

- all affected obligations have been identified;
- no mark was missed or delayed beyond its service objective;
- tests have passed;
- a candidate image has been deployed;
- a production canary has passed;
- a successor evidence epoch has been activated; or
- LIVE is safe or permitted.

## Detection and evidence

The epoch-21 acceptance record observed one eligible PAPER fill. For its 5-minute horizon, the
independent finalizer won the obligation lock at `11:44`, `11:49`, and `11:50 UTC` on
2026-07-28. The markout job won at `11:51 UTC` and sealed the first eligible persisted snapshot,
observed 61 seconds after the target.

That sequence originally appeared to be a bounded scheduling nuisance because the correct
snapshot remained available. The P0 review identified that neither the shared predicate nor the
co-scheduled jobs imposed a finite fairness guarantee. The correct historical outcome for one row
does not make the topology safe.

Authoritative reconciliation must come from PostgreSQL rows and immutable job-run records. Logs
are supporting evidence only.

## Root cause

### Overlapping ownership

Both standalone jobs selected obligations in `pending` or `waiting_for_marks` state where
`next_due_at <= observed_now`. They differed in what they did after the claim, but not in which
row they were allowed to lock. `SKIP LOCKED` prevented simultaneous mutation; it did not assign
ownership or guarantee that the productive worker would eventually win.

### Missing ordered transaction boundary

Mark materialization and obligation finalization were separate scheduler entries. Their shared
start time provided concurrency, not sequencing. There was no committed markout phase that the
finalizer was required to observe.

### Grace-window polling gap

After a target became due, absence of an immediately eligible snapshot moved the next poll to
`target_at + mark_grace`. Newly persisted snapshots inside the frozen grace interval were
therefore not consumed on the next scheduler pass.

### Monitoring gap

The scheduler and health surfaces could report recent successful job runs without proving that
the oldest due markout advanced. Freshness of an overlapping no-op/deferral path was not an
adequate liveness signal.

## Corrective design

### One scheduled composite job

Production scheduling must register exactly one recurring job:
`execution-cost-maintenance`. It runs every 15 seconds. The legacy
`execution-markouts` and `execution-cost-finalization` entry points remain available only for
explicit manual recovery and focused testing; they must not be registered as recurring scheduler
jobs.

### One database clock, two short transactions

Each composite invocation captures one PostgreSQL-authoritative timestamp and passes that exact
value to both phases.

1. **Phase A — markouts.** Open a short transaction, claim only obligations with unresolved
   applicable mark work, seal observed or terminally missing marks, flush, and commit.
2. **Phase B — finalization.** After Phase A commits, open a new short transaction, claim only
   obligations that are already finalizable, append immutable cost seals, and commit.

The two phases deliberately do not share one transaction. If Phase B fails, its transaction rolls
back without rolling back valid marks committed by Phase A. A retry uses idempotent uniqueness and
the same repository invariants; it cannot append a competing mark or cost seal.

### Disjoint repository predicates

Repository ownership must be explicit:

- the markout predicate selects an obligation only while at least one applicable
  `(fill_leg_id, horizon_seconds)` mark is unresolved and its target is due;
- the finalizer predicate selects an obligation only when no applicable mark remains unresolved,
  or when the execution mode/evidence contract makes post-fill marks not applicable;
- an obligation eligible for the markout claim in a given database state must not be eligible for
  the finalizer claim in that same state.

The disjointness invariant applies to scheduled, manual, retry, and concurrent invocation paths.
`SKIP LOCKED` remains a concurrency tool, not the correctness boundary.

### Poll throughout the frozen grace interval

For every unresolved mark whose target is due, each composite pass searches the immutable CLOB
ledger for the first eligible snapshot in the inclusive frozen interval
`[target_at, target_at + mark_grace]`.

- If an eligible snapshot exists, Phase A seals the earliest one deterministically.
- If none exists and the captured database clock is still before grace expiry, the mark remains
  unresolved and its next-due clock is set to that captured clock, making it immediately eligible
  for the next composite invocation.
- If none exists at or after grace expiry, Phase A appends the immutable `missing` mark.

The scheduler cadence is nominally 15 seconds, and consecutive successful scans of within-grace
work must be no more than 20 seconds apart. Polling frequency and this operational SLO do not
change the target, grace interval, first-eligible selection rule, or source evidence. They remove
the avoidable wait until grace expiry and prevent a stored `observed_now + 15 seconds` value from
missing the next scheduler tick and delaying collection to a near-30-second pass.

### Make scheduler overlap visible

APScheduler reports an invocation rejected by `max_instances=1` as
`EVENT_JOB_MAX_INSTANCES`; it does not emit a job-execution result for that scheduled time. The
listener must persist every scheduled time carried by the event as an idempotent `job_runs` row
with status `max_instances` and enqueue a deduplicated operator alert. The row is a durable
non-success for uptime and evidence continuity. It must never be represented as a completed
composite run or as invented Phase-A/Phase-B evidence.

## Evidence-preserving remediation

The remediation must not update/delete immutable marks, seals, fills, intents, snapshots, job
runs, release epochs, or continuity lanes. It must not synthesize job runs or backfill a successor
epoch.

Before acceptance, reconciliation must report:

- every obligation open at incident discovery;
- every applicable leg/horizon and whether its mark is observed, missing, or unresolved;
- the first eligible snapshot in each frozen window, independently recomputed;
- any stored mark that does not cite that deterministic first eligible snapshot;
- obligations delayed beyond target plus grace;
- seals created while applicable marks were unresolved;
- duplicate marks/seals or invalid record hashes; and
- counts split by PAPER/LIVE and evidence epoch.

Any mismatch is a blocking integrity finding. Ordinary absence of a snapshot after a fully elapsed
grace window remains an honest `missing` observation and must not be repaired with synthetic data.

## Continuity and cutover rule

The scheduler topology, claim ownership, and grace-window polling semantics are material to the
`execution_evidence` identity. The candidate release must create a reviewed successor epoch with
a zero-backfill `execution_evidence` fork. Every other lane action, including
`operational_legacy` and `selection_policy`, must be derived from the complete computed release
plan and its hash diff; this incident record does not predeclare a fork or continue for those
lanes. The reviewer rationale must distinguish the material execution-evidence semantic change
from an incidental hash change caused by a shared source file such as `runtime.py`. Any mismatch
or unclassified change fails closed.

The successor epoch is activated only after the required tests pass and the exact candidate
artifact has been deployed/staged and its identity verified. Candidate scheduler writes must not
run under the old execution-evidence identity. PAPER execution remains blocked during cutover
until the reviewed plan is activated. This rule does not authorize LIVE.

## Acceptance criteria

Acceptance requires all items in `docs/PRODUCTION_READINESS_CHECKLIST.md`, including:

- exactly one recurring `execution-cost-maintenance` job at 15-second cadence;
- no recurring legacy standalone markout/finalizer jobs;
- one captured database clock shared by two sequential committed phases;
- a demonstrated Phase-B failure that leaves Phase-A marks committed;
- database-proven disjoint claim predicates under concurrency;
- repeated within-grace polling and deterministic first-eligible snapshot selection;
- immediately eligible within-grace retries with nominal 15-second cadence and no more than 20
  seconds between consecutive successful scans;
- durable, idempotent, non-success `max_instances` scheduler evidence and operator alerting;
- clean incident reconciliation with no immutable evidence rewrite;
- a reviewed, activated successor evidence epoch after test and deployment verification; and
- a bounded PAPER canary with zero LIVE authority.

## Status log

| Milestone | Status | Evidence |
|---|---|---|
| Incident recorded and P0 scope frozen | documented | this record |
| Corrective implementation | implemented, not deployed | composite scheduler, disjoint claims, immediate grace retry, durable overlap events, bounded liveness query |
| Static/unit verification | passed | Ruff clean; strict mypy 173 modules; 880 tests passed, 3 skipped |
| Clean PostgreSQL verification | passed | clean PostgreSQL 16 CI-equivalent: 1169 passed, 3 skipped in 1052.21s; Release x64 Cockpit build: 0 warnings/errors |
| Predecessor incident reconciliation | passed | 2026-07-29 20:18:14Z; epoch 21 has 403/403 sealed obligations, zero open rows, zero projection/duplicate/premature-seal violations, zero LIVE |
| Candidate image/build identity | pending | pending digest |
| Backup and restore proof | passed | marker-bound post-stop backup `stratus_20260729T203148Z_3241d8921b44_1.backup`; independent hashes and full isolated restore accepted |
| Deployment under fail-closed controls | stopped/drained, candidate pending | audited Polymarket-only stop succeeded 20:16:15Z; quarantine 9/9 then idempotent 0/0; worker stopped; Binance remains killed |
| Successor continuity plan/epoch | pending | pending plan, hashes, and DB timestamp |
| PAPER canary | pending | pending canary record |
| Incident closure | pending | requires every acceptance item |

### Verified predecessor cutover facts — 2026-07-29

The live reproduction showed obligation `839` with both terminal marks while it remained
`waiting_for_marks`; concurrent legacy jobs repeatedly reclaimed it until it finally sealed at
`20:17:15.623070Z`. This was a valid delayed finalization, not evidence corruption.

Audited operator request `codex-pm-stop-1785356170` stopped only Polymarket at
`20:16:15.668847Z` and expired nine pending signals. The old image then ran the supported
execution-state quarantine: the first pass terminalized and formally no-fill-sealed nine PAPER
orders/intents, and the second pass returned all zeros. The legacy worker was stopped before any
candidate build. Read-only reconciliation at PostgreSQL clock `20:18:14.093844Z` found:

- global runtime `paper + kill=true`;
- Polymarket `enabled + paper + kill=true`;
- Binance `enabled + backtest + kill=true`;
- zero active PAPER orders and zero nonsealed execution-cost obligations;
- zero LIVE orders, positions, or execution intents;
- zero unresolved marks, false missing marks, observed projection mismatches, premature seals, or
  duplicate mark/seal identities.

These facts make the predecessor safe to preserve and back up. They do not accept the candidate,
activate a successor epoch, resume PAPER, or authorize LIVE.

### Post-stop backup and isolated restore proof — 2026-07-29

The accepted health marker names
`backups/stratus_20260729T203148Z_3241d8921b44_1.backup`. The snapshot began at
`20:31:48Z`, after the `20:18:14Z` reconciliation boundary, and completed at `20:58:46Z`.
Independent host verification matched:

- archive size `2,388,137,173` bytes and SHA-256
  `4ed6ac9932647d3e2c25df3052a5d1e9034594005a6edf3fb508fd2ca856358c`;
- catalog size `89,396` bytes, `1,074` entries, and SHA-256
  `2a19ac617413912631779d36887a42f3d6482ee51a21bbf473d88f7e20607ff2`;
- manifest SHA-256
  `52b8adda0148c695bb58e0a3a7b0f2c63574a0b8c24a25158edb6ffb3482e9a8`;
- stable Alembic head `20260728_0045` before and after the dump.

The exact archive restored with `pg_restore --jobs=2 --exit-on-error` into an isolated
PostgreSQL 16.13 container with `--network none`, no published port, a disposable volume, and
fresh non-production credentials. Because `pg_dump` does not archive cluster roles or the target
database ACL, disaster recovery must first recreate the database-scoped runtime/app roles and
must apply the revision-0044 database ACL bootstrap after restore. Omitting that bootstrap left
the PostgreSQL default `TEMPORARY` privilege on `PUBLIC`, and
`assert_stratus_runtime_boundary(false)` correctly failed closed. Applying the reviewed,
idempotent bootstrap (`REVOKE TEMPORARY ... FROM PUBLIC`, runtime-only `CONNECT`) made both the
owner and real restricted-login boundary assertions pass. This is an environmental restore
step, not a repair of dumped application data.

The restored database had 79 public tables, database size `9,621,322,775` bytes, one Alembic row
at `20260728_0045`, zero invalid indexes, zero unvalidated constraints, zero disabled user
triggers, and 9/9 valid HBA rules. It preserved global `paper + kill=true`, Polymarket
`enabled + paper + kill=true`, Binance `enabled + backtest + kill=true`, epoch 21, 26 reviewed
plans, 126 continuity lanes, zero incomplete six-lane epochs, zero pending operator commands,
and zero LIVE orders, positions, intents, fill legs, or prospective enrollments.

Independent execution-cost reconciliation on the restored copy found 857/857 sealed
obligations, including epoch 21 at 403/403; zero unresolved-after-grace marks, projection
mismatches, false missing observations, premature seals, duplicate marks/seals, or nonsealed
obligations; 192/192 mark hashes and 857/857 seal hashes valid; all four immutable
insert/append-only triggers enabled with the expected functions/events; and the known hash vector
matching the inline SHA-256 calculation. The disposable container and volume were removed after
acceptance. Evidence logs remain in the ignored local release-artifact directory
`.stratus-cache/restore-20260729T203148Z/`.

### Maintenance-load finding

During the restore drill, the predecessor API was observed creating repeated expensive Cockpit
refresh queries and multiple `idle in transaction` sessions. Production PostgreSQL consumed
approximately 150–160% CPU while the old API was running and immediately fell to approximately
0% after that API alone was stopped. The worker was already stopped and both venue kill switches
remained engaged. The predecessor API will not be restarted; the candidate must pass a bounded
dashboard-query/load check under the kill switch before PAPER resume. Query cancellation,
refresh coalescing, and bounded analytics plans are tracked as P1 operational work.
