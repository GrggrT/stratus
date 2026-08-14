# STRATUS quality roadmap

Status: roadmap for moving STRATUS from a working paper-trading prototype to a
validated, operator-grade trading system.

Last updated: 2026-07-29.

Autonomous operating contract:
`docs/AUTONOMOUS_PRODUCT_EXECUTION_PLAN.md` defines the product outcome,
decision rights, non-negotiable safety invariants, operating cadence, and
current execution queue.

Related detail plan: `docs/EDGE_VALIDATION_PLAN.md` expands the anti-luck
workstream: skill/luck PnL decomposition, bootstrap confidence intervals,
execution-cost filters, potential-win sizing caps, calibration shrinkage, and
the formal "edge proven" gate.

Accuracy research: `docs/ACCURACY_RESEARCH.md` documents settlement-native
truth, point-in-time market benchmarks, model-version drift, regional/AI model
selection, and the proposed Accuracy v2 architecture.

Execution evidence protocol: `docs/EXECUTION_COST_PROTOCOL.md` records the deployed
revision-0038 zero-backfill contract for order intents, atomic maker/taker fill
legs, exact cost decomposition, and 5m/30m post-fill markouts. It follows revision 0037 forecast
provenance and does not reinterpret revision 0035.

Current P0 record: `docs/incidents/2026-07-29_EXECUTION_MARKOUT_STARVATION.md` records the
execution-cost scheduler starvation root cause and the evidence-preserving remediation boundary.
`docs/PRODUCTION_READINESS_CHECKLIST.md` is the fail-closed release gate. Verification,
deployment, successor-epoch activation, and PAPER canary remain pending.

## Active autonomous delivery board — 2026-07-29

### Product objective and responsibility boundary

The engineering objective is to turn STRATUS into a production-capable, evidence-driven trading
system that can demonstrate whether it has a net edge and can operate safely when that edge is
present. Engineering owns implementation quality, reproducibility, observability, incident
response, release integrity, and honest measurement. It does **not** promise profit or permit a
weak statistical result to be relabelled as success. Market outcomes remain uncertain; the
quantitative gates below decide whether capital may be exposed.

The active execution order is P0 through P7. Work may be researched in parallel, but no downstream
stage may bypass the exit evidence of an upstream safety gate.

### Permanent safety invariants

- Polymarket remains PAPER until the formal edge, legal, executor, and limited-live gates all pass.
- Binance remains `backtest + kill=true` unless a separate reviewed product plan explicitly
  authorizes a different scope.
- No release may create a LIVE order, intent, or position during a PAPER canary.
- Evidence is append-only. Historical rows are never rewritten to improve a metric.
- Material model, selection, truth, or execution changes receive an explicit prospective
  continuity boundary; retrospective gains do not count as prospective proof.
- A healthy process is not enough: readiness also requires data freshness, obligation progress,
  exact artifact identity, and zero critical integrity findings.
- Legal/KYC/tax/geographic decisions require current primary-source review and operator acceptance;
  code cannot decide them on the operator's behalf.

### Milestones

| ID | Milestone | Status | Dependencies | Required exit evidence | Owner |
|---|---|---|---|---|---|
| P0 | Execution-evidence scheduler integrity | in progress | none | composite scheduler; disjoint claims; clean reconciliation; tested image; successor epoch; PAPER canary | engineering |
| P1 | Operational production foundation | active after P0 | P0 | health/ready SLOs; alert routing; verified backup + isolated restore; rollback drill; secrets/supply-chain review; capacity/retention plan | engineering + operator |
| P2 | Settlement-native truth for every traded city | open | P1 | exact resolution-source/rule registry; rounding/timezone tests; licensed/attested artifact retention; reconciliation coverage and quarantine | data engineering + legal |
| P3 | Point-in-time accuracy stack | open | P2 | latency-aware regional-model inventory; version/structural-break detection; walk-forward calibration; station/regime diagnostics; shadow promotion protocol | quantitative engineering |
| P4 | Realistic execution and benchmark attribution | open | P0, P3 | horizon-consistent market benchmark; maker/taker fill realism; fee/slippage/adverse-selection attribution; CLV/Brier drill-down by stable slices | quantitative + execution |
| P5 | Prospective net-edge proof | collecting | P2–P4 | independent station-day denominator; required sample counts; model beats market; positive CLV/skill/ROI lower bounds after costs; no dominant single-trade luck | quantitative gate |
| P6 | LIVE capability without LIVE authority | blocked | P1, P5 | wallet/signature/nonce lifecycle; preflight; partial fills; cancel/reconcile/redeem; risk and kill drills; current Austria/KYC/tax/geo approval | security + execution + operator |
| P7 | Limited-LIVE canary and scale decision | blocked | P6 | tiny capped canary; exact reconciliation; zero criticals; stop/rollback proof; post-canary review; explicit operator approval | operator + independent reviewer |

`P5 collecting` means PAPER may collect prospective evidence only. It does not imply that the
edge is positive. `P6 blocked` and `P7 blocked` are intentional fail-closed states.

### Active queue

| Priority | Task | Depends on | Completion evidence |
|---|---|---|---|
| Now | Close the 2026-07-29 execution-markout starvation incident | none | `docs/PRODUCTION_READINESS_CHECKLIST.md` fully evidenced |
| Now | Freeze, build, and stage the exact candidate while both venue kill switches remain engaged | verified source + restore | clean commit, green CI, immutable image digest, staged identity |
| Now | Prove the independent PAPER-canary safety authority | verified source | schema 0047; DB-clock lease; watchdog; shared advisory-lock races; exact reserved resume/release identities; stop-before-diagnostics; terminal fail-closed release |
| Next | Replace heartbeat-only detection with durable execution-obligation SLO metrics and alerts | P0 code | oldest-due/finalizable/late-pickup metrics, tests, operator surface |
| Completed | Replace the application-only API read lane with a separate PostgreSQL-enforced read pool | P0 load acceptance | physically separate 3-read/1-write pools; `default_transaction_read_only=on`; application DML/CTE guard; integration proof |
| Next | Give the read pool a dedicated least-privilege database credential | P1 security review | separate login; SELECT-only grants; mutable-function EXECUTE review/revoke; credential rotation and rollback drill |
| Completed | Prove the current backup by isolated restore and retain a hash-bound release artifact | P0 candidate | marker-bound archive, independent hashes, PostgreSQL 16.13 full restore, ACL/runtime and ledger reconciliation |
| Runtime acceptance | Bound Cockpit/API refresh cost and cancel superseded refreshes — implementation complete, acceptance pending | P0 candidate API | tiered/coalesced refresh, bounded indexed queries, database timeouts, and a passing production-size load test on a new exact candidate |
| Next | Automate disaster-recovery role/database-ACL bootstrap | verified restore drill | marker-bound restore command, role provenance, PUBLIC TEMP revoke, restricted-login assertion |
| Next | Partition/retain the dominant evidence tables without rewriting accepted evidence | P1 capacity review | size/age report, legal/evidence retention decision, reversible partition/archival drill |
| Next | Freeze the 20-city resolution contract registry before expanding traded stations | P1 | per-city source, station, timezone, day window, unit, rounding, artifact policy |
| Next | Build point-in-time benchmark and structural-break datasets | P2 | versioned dataset, no look-ahead tests, coverage/latency report |
| Next | Run regional-model shadow tournaments per station and horizon | P3 dataset | walk-forward Brier/log score/CRPS and availability-adjusted promotion evidence |
| Continuous | Accumulate unmodified PAPER outcomes and execution evidence | P0 safety; paused during code-only hardening | resumes only through R7 after every prior release gate; live gate dashboard, independent station-days, skill/luck and cost attribution |
| Gate | Re-review Austria/KYC/tax/geographic access and executor security | positive P5 | current primary-source record and explicit operator decisions |

### Active release sprint: successor PAPER candidate

Current phase: code-only fail-closed hardening. Production PostgreSQL, API, worker, and
`safety-control` are stopped; Polymarket PAPER is paused. An already-running backup sidecar may
remain running, but it cannot start PostgreSQL and is not fresh-backup evidence while PostgreSQL
is stopped. Apart from that sidecar, only disposable candidate/test infrastructure may run until
R0 is accepted and the R1 immutable-image transition is recorded.

This is the executable order for the current P0 release. A later step may be prepared in
parallel, but it cannot mutate production state until every preceding exit condition is recorded.

| Order | Task | Current state | Exit condition |
|---|---|---|---|
| R0 | Qualify replacement source | local candidate green; SHA/remote CI pending | final SHA is clean/pushed; complete Linux CI, PostgreSQL integration, Compose validation, and Docker build pass |
| R1 | Build immutable candidate | blocked by R0 | clean detached worktree; exact revision label; `linux/amd64`; recorded tag, image ID, and digest |
| R2 | Prove backup/restore and apply the safety migration | blocked by R1 | only after the R1 transition is recorded, briefly start the retained exact production PostgreSQL container while every application writer remains stopped; a fresh hash-bound `0046` backup restores independently; the exact candidate migrates PostgreSQL to exactly `0047`; PAPER fence, roles, ACL/RLS, triggers, functions, and fail-closed controls pass |
| R3 | Re-run production-size read-only load gate | blocked by R2 | HTTP v3 + runtime v2 + composite artifacts share run/image/schema `0047`/overlap and all unrelaxed thresholds pass |
| R4 | Close pre-activation evidence | blocked by R3 | P0-only scope attested; schema/indexes and controls re-proved; immediate reconciliation passes; retained predecessor API image identity and post-activation diagnostic procedure are recorded |
| R5 | Activate successor continuity epoch and prove API-only rollback isolation | blocked by R4 | complete reviewed lane plan and zero-backfill execution-evidence fork activate on the exact image; while kills remain engaged and the worker remains stopped, the retained predecessor API reports continuity mismatch against that active successor without changing plans, epochs, lanes, or evidence; the exact candidate API is restored and reports continuity match |
| R6 | Cold-worker canary under kills | blocked by R5 | safety-control healthy without a lease; four consecutive composite runs, <=20-second gaps, separate phase commits, no legacy jobs/contention/errors/restarts/LIVE |
| R7 | Arm the lease and resume only Polymarket PAPER | blocked by R6 | exact safety-control image; fresh watchdog; immutable ready marker; lease-reserved venue command; Binance remains backtest+kill |
| R8 | Sixty-minute PAPER canary | collector and safety control implemented; runtime blocked by R7 | immutable T+0/5/15/30/45/60 evidence; every naturally created/due cost obligation progresses, or its absence is recorded without synthetic production fills; no divergence; atomic terminal STOP leaves runtime fail closed |
| R9 | Close incident and enter P1 | blocked by R8 | independent review, append-only acceptance record, durable alerts, DR/security/capacity work scheduled |

Automatic rollback is deliberately narrow: a failed gate stops the candidate API/worker and
preserves both kills; it never enables LIVE, changes Binance, rewrites evidence, or starts the
predecessor worker. A stopped or unprofitable strategy is a valid product outcome when the
measured edge fails.

### Change-control rule

Every implementation batch must update this board only from verified evidence. A task is
`completed` only when its tests and runtime acceptance artifact exist. A temporary lack of
profitable signals is not an engineering failure and must not be “fixed” by weakening admission or
statistical gates. The correct response is better data, a better falsifiable model, or a decision
to stop.

### Verified operational evidence — 2026-07-29 post-stop snapshot

The clean CI-equivalent run passed 1,169 tests with three intentional skips against a disposable
PostgreSQL 16 database. Cockpit dependency restore is locked to
`apps/StratusConsole/packages.lock.json` and SDK `10.0.301`; its Release x64 build completed with
zero warnings and errors.

The marker-bound post-stop backup
`stratus_20260729T203148Z_3241d8921b44_1.backup` independently matched its manifest and restored
fully into an unexposed PostgreSQL 16.13 container. The drill proved 79 tables, head
`20260728_0045`, zero invalid indexes/unvalidated constraints/disabled user triggers, a valid
restricted runtime login, safe PAPER/BACKTEST controls, complete epoch/lane history, zero LIVE,
and a clean execution-cost hash/trigger reconciliation. It also exposed a disaster-recovery
requirement: cluster roles and target-database ACL are outside ordinary `pg_dump`; the restore
procedure must recreate role provenance and revoke PostgreSQL's default `TEMPORARY` grant from
`PUBLIC` before the runtime-boundary assertion can pass.

Production-size inspection identified the largest storage consumers as `model_probs`
(`5,348,524,032` bytes total, largely TOAST), `accuracy_predictions` (`2,228,781,056` bytes), and
`forecasts` (`569,376,768` bytes). No row was removed. A reviewed capacity/retention and
partitioning decision is now explicit P1 work.

The predecessor API also produced repeated expensive Cockpit refresh queries and multiple
`idle in transaction` sessions: production PostgreSQL fell from roughly 150–160% CPU to
approximately 0% when that API alone was stopped. It will not be restarted. Candidate staging
must include bounded endpoint/load verification before PAPER resume.

The Cockpit/API bounding work is implemented in source: core reads remain on the 10-second tier,
evidence/execution reads move to 60 seconds, diagnostics move to 5 minutes, superseded refreshes
are cancelled/coalesced, database statements and transaction lifetimes are bounded, and the
accuracy overview has purpose-built indexes under source head `20260730_0046`. That index-only
migration was applied while every application writer was stopped; the database is now at `0046`.

The next candidate source head is `20260730_0047`, directly following that accepted operational
head. It adds a short DB-clock Polymarket PAPER-canary lease, PostgreSQL runtime/venue/request
fences, an independently supervised loopback-only safety-control service, a shared transaction
advisory lock for empty-set and queued-resume races, and atomic terminal STOP semantics. Focused
working-tree tests are green, but 0047 is not production-applied or release-accepted. A fresh
0046 backup/restore, exact-candidate migration proof, full CI, image identity, successor epoch,
and PAPER canary remain mandatory.

Runtime acceptance remains pending. The first staged gate on revision `09945edfd492` failed with
13 observed `idle in transaction` sessions and peak PostgreSQL CPU of `308.43%`; the candidate was
stopped and not accepted. The immutable candidate must first prove a fresh isolated restore of
`0046`. After the R1 immutable-image transition is recorded, the retained exact production
PostgreSQL container may be started for the bounded R2/R3 cutover window while every application
writer remains stopped; the candidate then applies its own `0047` migration. Only after the
database is proven at exactly `0047` may the implemented remediation run a new exact-candidate
load gate. Epoch activation and PAPER resume remain forbidden until that gate passes.

## Definition of quality

STRATUS is not considered "quality" merely because tests pass or the console
looks professional. For this project, quality means:

1. Source data is fresh, reconciled, and auditable.
2. Settlement and calibration targets match Polymarket outcomes closely enough
   to avoid systematic edge leakage.
3. Signal quality is demonstrated by Brier score, CLV, ROI, and robust ROI,
   not by one lucky realised PnL tail.
4. Risk limits block bad states before execution, not after exposure has already
   accumulated.
5. The operator can understand system health, edge, exposure, and failure
   causes within 30 seconds.
6. Live trading remains impossible until the paper gate and legal/operational
   gates are explicitly satisfied.

## Current P0 release disposition — execution markout starvation

The 2026-07-29 root-cause review reclassified the scheduler race described below from P2 to P0.
The independent `execution-markouts` and `execution-cost-finalization` jobs used overlapping
`FOR UPDATE SKIP LOCKED` claim predicates against the same due obligations. Repeated finalizer
lock wins had no fairness bound, and a mark without an eligible snapshot at its first target poll
was not reconsidered until grace expiry.

The required candidate topology has one recurring `execution-cost-maintenance` job every 15
seconds. Each invocation captures one PostgreSQL-authoritative clock, commits markouts in Phase A,
then opens a separate short transaction and commits finalization in Phase B. Repository claim
predicates are disjoint, eligible snapshots are polled throughout the frozen grace interval, and
the legacy standalone jobs remain manual-only. Separating the phase commits ensures a finalizer
failure cannot roll back valid marks.

This is a release requirement, not a production-acceptance claim. The implementation review,
tests, reconciliation, deployment, canary, and new evidence epoch are pending. The material
execution semantics require a zero-backfill `execution_evidence` fork. Every other lane action,
including `operational_legacy` and `selection_policy`, must come from the complete computed
release-plan hash diff rather than a predeclared fork or continue. Its rationale must distinguish
the semantic execution change from incidental shared-source changes. Candidate scheduler writes
and PAPER resume must remain blocked until the exact deployed/staged artifact has a reviewed,
activated successor epoch. Nothing in this remediation authorizes LIVE.

## Last production acceptance status — epoch 21 bounded PAPER runtime

Epoch 21 was opened at `2026-07-28T11:22:41.119811Z` from reviewed plan 33
(`908e8b064e9a95b70d544e4477716fea76a76402054c69c7309b5d0937e9cd96`) using exact
image
`sha256:f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4`.
The plan forked only `operational_legacy`; execution, official truth, predictive validation,
selection policy, and station lifecycle continued their exact reviewed identities. The release
manifest is
`014f10c81bcd7060fb6e6901d374816e101c089d641f743cb981b49d2678043b`,
the lane manifest is
`8c65b8786d809a9c9efc3e96dbf5efd897a81f9ce2a9249660c2f5ae4317a076`,
and the immutable epoch record is
`c12fa85a9b3e6598700d5fc31d0ae40c24cabb85b2e67dbd369a1112784734ac`.

Revision `20260728_0045` was the predecessor production schema head. It preserves the 8-decimal source
close probability while requiring the stored 6-decimal evaluation value to equal the explicitly
rounded source value. The predictive boundary was opened once in epoch 20 for this material
evaluation change; epoch 21 continues that exact predictive identity.

The release closes four runtime defects without weakening any gate:

- risk-capped signal sizes are rounded down to the execution quantum before admission, and
  non-positive legacy pending signals fail closed before an order or intent can be created;
- point-in-time forecast evaluation is SQL-bounded, anti-joined, streamed in batches, and capped at
  1,000 rows per run instead of loading the full eligible trajectory;
- complete, hash-valid shadow cohorts replay idempotently, while partial or invalid cohorts still
  fail closed; and
- the health audit scans accuracy predictions through a bounded watermark and server-side stream
  instead of materializing a multi-gigabyte ORM result.

Production smoke checks under the worker's enforced 3 GiB memory limit completed without OOM or
restart. Forecast evaluation selected and wrote 1,000 rows; the shadow replay recognized the
complete cohort as a no-op; and the post-resume health audit streamed 14,564 rows with zero invalid
records at approximately 107–131 MiB observed memory. Thirty-three pre-fix nonterminal PAPER
maker attempts were quarantined under the kill switch before activation.

Audited command 23 reaffirmed `paper`; command 24 resumed only `venue=polymarket`. The accepted
runtime state is:

- Polymarket `paper + kill=false`;
- Binance `backtest + kill=true`;
- global mode `paper`, global kill switch off;
- API and worker on the exact epoch-21 image, worker restart count zero;
- `/readyz=200`, continuity matched, and execution gate
  `evidence_continuity_match`;
- zero LIVE positions and zero LIVE orders.

The first natural cycle evaluated 212 markets, admitted 12 positive-size signals, and posted 12
maker-simulated PAPER orders. Every intent had positive notional and shares and carried epoch-21
continuity; no synthetic fill was created. The following cycle maintained the resting orders and
admitted no new order. By the later monitoring snapshot the lane contained 15 intents: one natural
eligible PAPER fill and 14 resting maker orders. The fill matched its requested `$0.05500000`
price and `$1.069700` notional. Its 5-minute markout was captured from an eligible snapshot 61
seconds after the target; the 30-minute mark remained prospectively due. A read-only guard now
checks service health, exact image and epoch continuity, venue modes, LIVE-row invariants, job
freshness, restart/OOM state, and non-positive execution values every 30 minutes.

The same observation exposed the scheduler race now recorded as the 2026-07-29 P0 incident.
`execution-markouts` and `execution-cost-finalization` started together and could contend for the
same obligation through `FOR UPDATE SKIP LOCKED`. The observed mark was delayed but retained the
correct historical snapshot, so that row's price evidence was not changed or lost. Root-cause
review later established that the delay had no general fairness bound. The epoch-21 facts remain
historical evidence; they do not accept the successor composite scheduler or close the P0.

This is operational acceptance for continued PAPER evidence collection only. It does not prove
forecasting skill, positive CLV, profitability, licensed exact-source coverage, or LIVE readiness.
LIVE remains prohibited until every independent quantitative, evidence, legal, risk, and executor
gate passes.

## Previous production acceptance status — epoch 18 PAPER recovery

Epoch 18 was opened at `2026-07-28T05:11:57.435240Z` from reviewed plan 28
(`babb63b834ef39f8d9090642a1c527fc11424daf0d814a77035e2d8bdf547c21`) using exact
image
`sha256:de7d033f14ee5527473525ac8b31c5e8235edcf2ce7d667196fe0efa2d41942a`.
It continues all six exact epoch-17 lane boundaries. Epoch 17 had admitted no worker execution or
formal evidence; it was superseded to state unambiguously that its operational fork reset only
collection/ETA while its E3 `SIGNAL_MAX_EDGE=0.20` selection fork intentionally opened a new
selection-compatible formal boundary.

This recovery follows health audit 799, which remains immutable and records seven full stalled
days from missing required scheduler coverage. Those days were not deleted, backfilled, or
excluded as operator maintenance. The reporting fix uses the operational-lane boundary only for
new liveness/ETA collection; formal evidence continues to use the newest applicable prospective
lane boundary. Health audit 800 is `warning` with no critical finding, no missing required
scheduler job, and zero new stalled days. Collection is honestly
`waiting_for_due / insufficient_full_days_7_required`.

Polymarket was resumed through two separate audited commands: command 21 reaffirmed `paper` while
the kill switch remained on, then command 22 resumed only `venue=polymarket`. Binance remains
`backtest + kill`; LIVE positions and nonterminal LIVE orders remain zero. API and worker run the
same exact epoch-18 image with zero restarts, `/healthz` and `/readyz` return 200, and continuity
matches with `evidence_continuity_match`.

The first natural post-resume cycle admitted 15 signals and posted 15 midpoint maker-simulated
PAPER orders. It produced zero fills, which is correct: the simulator does not manufacture an
execution until the observed market crosses the resting price with sufficient volume. The current
formal lane therefore has 15 intents but still zero fills, seals, enrollments, or outcomes.

The E3 edge ceiling is now deployed and bound into selection identity. It is a risk-reducing
admission rule, not evidence of improved forecasting accuracy. Predictive edge is still unproven;
exact licensed/attested truth coverage remains incomplete; the current full-day denominator is
zero; and LIVE remains prohibited.

Known non-execution remediation: the shadow `censored-challenger` startup run sealed 825
`applied=false` rows, while its same-day scheduled replay failed closed on a changed
`training_cutoff` for the same immutable keys. Production probabilities and PAPER execution were
unchanged. The follow-up is cohort-level idempotent replay recognition that accepts only a
complete, hash-valid existing cohort and continues to reject partial or invalid cohorts.

## Previous production acceptance status — epoch 16 truth quarantine

Epoch 16 was opened at `2026-07-20T16:27:06.935419Z` from reviewed plan 25
(`05bede4926514ba9248cbba549681c42b8c8e864eb2fad89f103f1edff4814ec`) using the
exact image
`sha256:d081e2e640eb5ad713954dbd87d0a3b3d8b1d05b1581017e2e48ff9941531843`.
This release is a truth-quarantine correction, not evidence that predictive edge has been proven.

The production trigger was RKSI HIGH for `2026-07-19`: the venue settled at `26 °C`, while the
current METAR-derived daily actual was `27 °C`. The affected markets are `2283` and `2284`; the
observation evidence is artifact `68`, containing 48 samples, with a 1,800-second terminal gap.
That cell can no longer train or score a model as if the METAR label were settlement truth.

Training-label selection now has these fail-closed rules:

- a current reconciliation mismatch quarantines the corresponding METAR daily actual;
- canonical exact settlement values take precedence, identical exact duplicates collapse to one
  label, and conflicting exact values exclude the cell entirely;
- a cell known to have exact settlement evidence never falls back to METAR when that exact evidence
  conflicts or is otherwise unusable;
- resolved/attested market cells without reconciliation are ineligible rather than implicitly
  treated as clean.

This is deliberately a **current-remediated** training and diagnostic view. It does not reconstruct
which reconciliation revision would have been visible at an old prediction timestamp, and it does
not rewrite sealed prospective predictions, immutable Gamma outcomes, or execution evidence. Exact
licensed/attested settlement-value coverage remains zero, so no official exact truth is claimed.

The weighted retrospective diagnostic moved RMSE from `1.4255` to `1.3586`, but its independent
settlement comparison confidence interval still crosses zero; therefore `applied=false` and the
candidate remains shadow-only. Post-obligation/capacity health is warning-only, while RKSI remains
selection-blocked and in cooldown. LIVE remains prohibited.

Verification completed with `860 passed, 3 skipped`, `280` unique PostgreSQL integration tests,
and a separate PostgreSQL HBA-boundary test. The release changes label eligibility and health
containment; it does not turn that verification count or retrospective RMSE movement into a skill
claim.

## Previous production acceptance status — epoch 15, 2026-07-20

That release was epoch 15 through reviewed plan 22, exact image
`sha256:c8813973ad897e9b7e0cc12de77eca4a58a636e901ae8e391f53843ce6b38a2c`.
It corrects the automatic observation-recovery window so a run after the configured station-local
hour can still drain the bounded backlog. The reviewed continuity plan continued
`execution_evidence` and `official_resolution_truth` and forked `operational_legacy`,
`predictive_validation`, `selection_policy`, and `station_lifecycle`. Recovery increased accepted
artifacts from 42 to 80 and recovered HIGH/LOW rows from 84 to 160, with zero missing active
station-days in the final three-day lookback and zero broken artifact/actual pairs.

Audit 721 had zero critical or blocking findings. Polymarket is PAPER with kill off; Binance remains
`backtest + kill`; `/healthz` and `/readyz` return 200; and no LIVE position, order, intent, or
authority exists. The correction improves future label availability but does not retroactively
improve model scores. At the acceptance snapshot the current predictive comparison remained model
Brier 0.07275 versus market Brier 0.06641 on 30 independent blocks, so edge is not proven.

Revision `20260719_0044` is the production schema head. The initial all-six-fork epoch 10 exposed a
least-privilege storage-capacity read defect while every execution kill remained engaged. That
worker was stopped before resume. The corrected runtime explicitly binds the mounted capacity path,
grants no settings-reader privilege, and expands the `operational_legacy` source closure so the
change cannot silently continue an old operational identity.

The epoch-13 image `sha256:c5076ba953fe44dca9b885c1c9823e2bca82f84f5ecc90fe70ddc89c17bcf2f5`
is active as epoch 13 through reviewed plan 20. The two successor operational epochs after the
capacity fix make backup hardening explicit: epoch 12 exposed a pre-start durability weakness in
the sidecar contract, and epoch 13 closes it with shared locking, bounded retry, atomic
dump/catalog/manifest publication, a retention floor, an encrypted verified off-site publication
path, and digest-pinned tools. The optional `crypt` remote was not configured in this acceptance,
so no off-site copy is claimed. Only `operational_legacy` forked in plans 18, 19, and 20; the five
unchanged evidence lanes continued their exact parent identities throughout.

The `America/New_York` weekly boundary passed at `2026-07-20T04:00:00Z`. The fresh audit at
`04:05:06Z` was `warning` with 18 issues, zero critical/blocking, and no daily/weekly loss issue.
Audited command 12 resumed only Polymarket PAPER at `04:09:39.767520Z`; Binance remains
`backtest` with its kill switch on. Natural jobs 36479 (`prices-and-signals`) and 36478
(`execution`) both succeeded after the command. The post-cycle audit at `04:10:58.811269Z` was
`warning` with 17 issues and zero critical/blocking/loss findings; `/healthz` and `/readyz` both
return 200, continuity exactly matches epoch 13, and no LIVE position or nonterminal LIVE order
exists. LIVE remains prohibited.

The first epoch-13 automatic restore point was published as
`backups/stratus_20260720T041256Z_9379ef5bda23_1.backup`: 1,265,297,862-byte custom archive,
SHA-256 `f422dd32866a697f12d2d3077901512fadec929b21c9d05ffb0c87c538e9b7b2`, 1,074 catalog entries,
and stable head `20260719_0044`. A full isolated restore produced a 6,749,789,207-byte database
with 79 public tables, no invalid index/constraint/trigger state, epoch 13/78 lanes, and zero LIVE
state. The sidecar is healthy. Audit 709 at `2026-07-20T05:04:19.761296Z` remains `warning` with
17 issues, zero critical/blocking findings, and no loss-limit finding. By the
`2026-07-20T05:14:19Z` acceptance check, all 60 orders created after the resume snapshot were
PAPER.

## Historical baseline — pre-0035 snapshot (2026-07-14)

Snapshot from the 2026-07-14 pre-0035 production audit:

- Mode: `paper`; kill switch: `false`; health: non-blocking `warning`.
- The legacy cohort has 100 paper positions (60 closed and 40 still open), but it predates the
  strict prospective dual-token protocol and is diagnostic only.
- The current `clob-dual-v1` cohort has 24 open positions and 0 closed positions. The formal live
  gate is therefore `0/10`, regardless of historical PnL.
- The durable resolution ledger contains 924 rows that are all consistent with METAR-derived
  bucket truth, but 0/924 have an attested official exact-source value. METAR consistency is not
  official resolution truth and must never be labelled as such.
- Fifteen settlement cases remain honestly classified as `venue_pending`.

The earlier 24-trade sample remains useful historical context: headline PnL was `+$102.97`, while
skill PnL was `-$16.93` and the profit was dominated by one lucky outcome. It does not satisfy the
current pipeline, provenance, independence, execution-benchmark, or sample-size requirements.
STRATUS is operational, but edge is not proven and live trading remains blocked.

Revision `20260715_0038` is now deployed. Its first post-boundary PAPER evidence consists of two
sealed no-fill attempts, so execution reporting correctly remains `evidence_incomplete`: protocol
plumbing is working, but there are still zero eligible fill-cost or markout samples. This release
changes the available prospective evidence, not the negative edge conclusion or live authority.

## Live gate

The formal quantitative gate in `calculate_live_gate` uses only closed `clob-dual-v1` paper
positions linked to attested Gamma outcomes and current-pipeline probability rows. Every one of
these eleven checks must pass:

1. At least three stations each have at least 100 current-pipeline samples and station Brier
   `< 0.18`.
2. At least 200 strict closed paper positions exist.
3. At least 100 distinct `station × local date × kind` samples exist.
4. At least 100 resolved trades belong to the current probability pipeline version.
5. At least 100 strict CLV samples exist.
6. Average CLV is `> +0.015`.
7. CLV beat rate is at least `52%`.
8. The lower bound of the bootstrap 95% ROI interval is above zero.
9. The lower bound of the bootstrap 95% skill-PnL interval is above zero.
10. Current-pipeline model Brier is lower than the same-cohort market Brier.
11. Every latest active-market probability in the current pipeline binds complete immutable
    forecast source, run, manifest, and input-record provenance.

Quantitative passage is necessary but not sufficient. Readiness must also have no unresolved
`critical` or `blocking` health issue, Austria/KYC/tax readiness must be explicitly confirmed, and
wallet, funder, signature type, cancellation, reconciliation and emergency-stop paths must be
tested without real-money exposure.

Failure to pass the gate twice should be treated as a strategy stop condition,
not as a UI or infrastructure problem.

## Phase 1: Data quality and health

Goal: eliminate hidden data defects before improving UI or live execution.

Tasks:

1. Add a `resolution_reconciliation` job:
   - read resolved markets from Gamma;
   - compare Polymarket outcome with STRATUS `daily_actuals`;
   - record mismatches by station, date, market kind, and bucket;
   - flag boundary-sensitive discrepancies separately.
2. Resolve or classify `settlement_stale` markets:
   - settled;
   - unresolved but still pending at venue;
   - skipped due to missing/ambiguous Gamma data;
   - requires manual review.
3. Fix `event_probability_sum`:
   - normalise probabilities inside one event where safe;
   - fail closed for an event when probability mass is inconsistent beyond
     tolerance;
   - record the failed event id in health details.
4. Split health severity into `info`, `warning`, `critical`, and `blocking`.
5. Make `collector_uptime_slo` robust to startup backfills and short controlled
   maintenance windows.
6. Keep `clv_missing = 0` for all closed paper positions.

Acceptance criteria:

- Health is `healthy` or contains only explicitly non-blocking warnings.
- No CLV-missing closed positions.
- Stale settlements have actionable classification.
- Kill switch is not triggered by stale historical state or non-material drift.

## Phase 2: Model validation

Goal: prove or reject the forecasting/trading hypothesis with paper data.

Tasks:

1. Add a paper monitoring checkpoint:
   - update after each new market resolution;
   - publish a daily snapshot while paper mode is running;
   - record closed count, ROI, robust ROI, average CLV, CLV beat rate, largest
     trade contribution, and drawdown;
   - explicitly label samples below 100 resolved positions as inconclusive.
2. Add validation summaries by:
   - station;
   - market kind;
   - forecast horizon;
   - confidence class;
   - event/date.
3. Add CLV diagnostics by:
   - station;
   - forecast horizon;
   - confidence class;
   - market kind;
   - entry bucket / price band;
   - before and after fees, spread, and expected slippage.
4. Block or down-rank signal classes with persistently negative CLV once enough
   samples exist for that slice.
5. Track:
   - Brier score;
   - average CLV;
   - CLV beat rate;
   - ROI;
   - robust ROI;
   - largest trade contribution;
   - drawdown.
6. Add live gate reporting that explains which criteria failed.
7. Add "single-trade dependency" alerts when realised performance is dominated
   by one or two outcomes.

Acceptance criteria:

- All eleven formal LIVE-gate checks in the section above pass on the strict prospective cohort;
  the old 100-position checkpoint is diagnostic only and cannot authorize LIVE.
- Robust ROI is positive and the largest-trade contribution is below 25-30% as additional
  anti-luck diagnostics; they do not replace any formal gate check.

## Phase 3: Signal engine hardening

Goal: ensure every executed signal has a defensible, risk-adjusted edge.

Tasks:

1. Normalise probabilities within a Polymarket event.
2. Block trading for events with invalid probability mass.
3. Compute final edge after fees, spread, expected slippage, and executable
   depth.
4. Add station reliability score derived from reconciliation history.
5. Add station/date cooldown after resolution mismatches or missing observations.
6. Strengthen correlated exposure limits:
   - event-level cap;
   - station-day cap;
   - market cap;
   - open plus pending plus in-flight exposure.
7. Keep paper position aggregation by `(market_id, side)` while preserving
   order-level audit history.

Acceptance criteria:

- No executed signal from an invalid event probability set.
- Every executed signal has stored pre-trade inputs and final risk-adjusted edge.
- Exposure caps are enforced before execution.
- Health audit can explain any skipped trade with specific risk/data reasons.

## Phase 4: Operator console

Goal: provide a professional interface that exposes edge, risk, and health,
not just dry database summaries.

Tasks:

1. Dashboard:
   - equity curve;
   - drawdown;
   - open exposure;
   - realised PnL;
   - robust ROI;
   - health severity timeline.
2. Validation:
   - Brier trend;
   - CLV distribution;
   - CLV by station/horizon/confidence;
   - CLV before and after fees/spread/slippage;
   - largest trade contribution;
   - robust ROI trend;
   - calibration chart.
3. Market drill-down:
   - market price series;
   - model probability series;
   - signal history;
   - order history;
   - settlement/reconciliation result.
4. Risk:
   - exposure heatmap by station/date/event;
   - cap utilisation;
   - breached and near-breached caps.
5. Health center:
   - actionable issue list;
   - severity;
   - first seen / last seen;
   - suggested operator action;
   - links to affected markets.
6. Trade journal:
   - all entries/exits;
   - PnL;
   - CLV;
   - reason for trade;
   - tags for later analysis.

Acceptance criteria:

- Operator can answer these questions within 30 seconds:
  - is the system healthy?
  - why is it trading or not trading?
  - where is exposure concentrated?
  - is the edge improving or deteriorating?
  - which data source or station is causing risk?

## Phase 5: Live readiness

Goal: prepare live trading without weakening the paper gate.

Tasks:

1. Confirm Austria/Polymarket access, KYC, tax treatment, and legal constraints.
2. Validate wallet/funder/signature configuration.
3. Run live preflight with no order submission.
4. Add dry-run order construction validation.
5. Verify cancellation and emergency stop paths.
6. Require double confirmation for live mode.
7. Keep all live executor failures fail-closed.

Acceptance criteria:

- Paper gate passed.
- Legal/tax/KYC checklist complete.
- Live preflight green.
- Emergency stop tested.
- Cancellation path tested.
- Operator explicitly confirms live transition.

## Immediate implementation order

The next engineering work should follow this order:

1. `resolution_reconciliation` job and tables/API.
2. Settlement stale classification and operator visibility.
3. CLV diagnostics by station, horizon, confidence, and cost/slippage layer.
4. Event probability normalisation and fail-closed signal guard.
5. Health severity model.
6. Validation summary endpoint/dashboard data.
7. Console pages for validation, risk heatmap, and market drill-down.

Status 2026-07-14: items 1, 2, 4, and 5 are code-complete in block 0034; the later sections
record the already delivered diagnostics/dashboard work and the empirical gates that remain.

UI polish should not outrun the validation layer. A professional-looking
console is useful only if it exposes correct edge and risk information.

## Accuracy engineering status — 2026-07-13

The point-in-time accuracy stack now includes settlement calibration, hourly trajectory,
remaining-day nowcast, conformal uncertainty, hierarchical partial pooling, and a
version/regime-aware EMOS challenger. Every challenger is fail-closed and must prove lower
Brier on the fixed first 60 future distinct `station/day/kind/horizon` blocks, with the
preregistered martingale Hoeffding lower bound for mean improvement above the minimum effect,
before it can enter the regularized stack.

Latest production evidence remains negative: 39 resolved trades, model Brier 0.214301 versus
market 0.168090, average CLV -0.034629, and skill P&L -$147.262. The ten-part live gate
therefore remains blocked. Paper evidence collection is allowed to continue; it does not grant
or imply live authority.

The newly deployed regime challenger detected 132 active residual shifts and produced 143
current shadow forecasts, but has 0/60 prospective blocks and is not applied. The next accuracy
priority is evidence collection and root-cause separation:

1. accumulate prospective settlement-native blocks for all challengers without relaxing gates;
2. license or explicitly approve storage of exact resolution values so temperature error can be
   separated from source/rounding error;
3. add version metadata for operational GFS/ICON/GEM feeds; until then their detected boundaries
   remain rolling change points rather than attributed software upgrades;
4. promote no regional model, station slice, or regime correction without a latency-aware
   point-in-time bake-off followed by the immutable `fixed-range-brier-v3` prospective gate;
5. keep new entries clipped to the exact 5% market and 12% station/day caps; classify a small
   legacy overage as a warning and engage the global kill switch only above 125% of a cap.

Nonlinear hardening is also complete: QRF, distance-weighted Analog Ensemble, and residual
Gaussian Mixture now have fixed 60-block prospective gates and Cockpit telemetry. They
cover 198 current markets but have 0 resolved prospective blocks, so all remain shadow-only and
the regularized stack remains empty. Their large raw probability deviations make prospective
validation, rather than immediate promotion, the correct next step.

The regularized stack itself is now subject to the same standard. A fit may be created after 30
historical cases, but it is saved only as `regularized_stack/regularized-brier-v1` shadow
predictions. It cannot replace the market-shrunk baseline until its fixed first 60 future distinct
blocks produce a preregistered mean-improvement LCB above the minimum effect. Cockpit exposes fit
depth, prospective blocks, CI, and `SHADOW/APPLIED` state. This closes the previous path where
an in-sample stack could have entered production automatically after 30 correlated buckets.

Operational verification at 2026-07-13 10:48 UTC: health is `warning`, not `critical`; the
audited `resume` command succeeded; Polymarket is `paper` with kill switch off; `/readyz` is
200. Two valid $21.26 maker orders were posted on uncapped EGLC/RKSI slices and remained
resting rather than paying the spread. Mild legacy cap overages did not grow.

The stack cold-start path is now hierarchical and leave-one-station-out. When a local scope has
fewer than 30 resolved bucket cases, weights may be fitted from at least 30 cases across at
least three other stations with the same horizon/kind. The target station is excluded from the
fit, the result remains a shadow prediction, and application still requires that target scope's
own 60-block prospective gate. Live deployment produced 154 peer-pool shadow predictions with
33-44 training cases from 3-4 peer stations, 0 eligible slices, and 0 active stack weights.

## Probability pipeline cohorts - 2026-07-13

Material probability changes now have an explicit cohort boundary. Every new `model_probs`
row records `components.pipeline.version`; the initial version is
`stratus-accuracy-v4-20260713`. Resolved order attribution carries the version into Brier
diagnostics and supports version-level drill-down in Cockpit.

The live gate remains deliberately asymmetric:

- portfolio maturity, CLV, ROI confidence and skill P&L retain the complete paper history;
- station Brier and model-vs-market Brier use only the currently deployed pipeline;
- a separate check requires at least 100 resolved trades on the current version;
- if current version metadata is absent, the version and Brier checks fail closed;
- historical cohorts stay visible in `/api/v1/brier-report`, but cannot validate a new model.

This prevents a new architecture from inheriting apparent accuracy from earlier forecasts.
Changing a material forecast, calibration, shrinkage or stacking policy therefore requires a
new `PROBABILITY_PIPELINE_VERSION` and a fresh prospective current-version sample.

The semantic version is backed by an automatic 16-hex fingerprint over the material model
source files and probability settings. The effective cohort is
`<version>+<fingerprint>`. This makes forgotten manual version bumps fail safe: a rebuilt model
cannot inherit the previous implementation's evidence under the same text label.

Deployment verification at 2026-07-13 14:57 UTC:

- all 308 latest probability rows carry effective cohort
  `stratus-accuracy-v4-20260713+288a6515612324d1`;
- the API reports 39 historical resolved trades and 0 current-version resolved trades;
- legacy trades are visible under the `pipeline_version=unknown` Brier slice;
- current-version station and Brier checks fail closed until future resolutions arrive;
- Polymarket remains in paper mode, kill switch is off, `/readyz` returns 200, and the
  minute price/probability/signal/execution cycle is running.

## Trade-selection shadow policies - 2026-07-13

The all-market point-in-time evaluation and executed-trade evaluation currently disagree:
1,080 forecast evaluations have model Brier 0.0625 versus 0.0723 for the market, while 50
legacy executed orders have model Brier 0.2128 versus 0.1368 and an edge slope of -1.68.
This is evidence of adverse selection in the trading subset, not proof that the base forecast
is globally worse.

Three fixed protective policies are now evaluated shadow-only on the current pipeline cohort:

- `edge-cap-10c-v1`;
- `edge-cap-15c-v1`;
- `edge-cap-20c-v1`.

Rejected opportunities contribute zero PnL and zero information value. Candidate improvement
is measured against baseline per `station/day/kind` block. Qualification requires at least 100
current-cohort opportunities, 60 independent blocks, 30 retained trades, retention of at least
35%, model Brier below the same-time market benchmark, and a strictly positive lower bound of
the block-bootstrap information-value improvement. The report and Cockpit are diagnostic only;
no policy can activate itself or alter execution.

Deployment verification at 2026-07-13 15:43 UTC:

- `/api/v1/selection-policy-report?mode=paper` returns the effective cohort
  `stratus-accuracy-v4-20260713+288a6515612324d1`;
- 51 legacy opportunities remain diagnostic only, with edge slope -1.7177 and
  model-minus-market Brier +0.075803;
- the effective v4 cohort has 0 resolved opportunities, so baseline and all three caps remain
  ineligible and collect prospective evidence from zero;
- `/readyz` returns 200, Polymarket remains in paper mode, and no selection challenger has an
  execution or automatic-promotion path;
- the worker is running the 20-station forecast/observation cycle and Cockpit has been restarted
  on the build that exposes the challenger card.

### Prospective selection-policy ledger - 2026-07-13

The challenger report no longer reconstructs policy membership from resolved trades alone.
Migration `20260713_0026` adds an immutable `(order, policy)` ledger written only after a real
paper/live fill and while the market is still unresolved. Each row freezes the signal edge,
threshold, accept/reject result, effective probability cohort, signal/fill/decision timestamps,
and a deterministic policy-definition hash.

Report eligibility now uses only ledger rows that satisfy all of these conditions:

- the decision predates the declared resolution time;
- signal and fill timestamps predate the decision;
- the stored definition hash matches the current versioned policy;
- the stored probability cohort matches the trade's current-cohort filter;
- both baseline and challenger decisions exist for the same filled order.

Missing, stale-definition, wrong-cohort, or post-resolution rows reduce the sample instead of
being inferred. There remains no automatic activation path.

Production verification at 2026-07-13 16:00 UTC:

- Alembic is at `20260713_0026`, `/readyz` is 200, API and worker are healthy;
- the execution cycle captured 56 decisions for 14 unresolved filled orders;
- one recorded order belongs to effective v4 cohort
  `stratus-accuracy-v4-20260713+288a6515612324d1` and none has resolved yet;
- all caps remain ineligible; Cockpit reports both recorded and resolved opportunity counts;
- probability fingerprint is unchanged, Polymarket remains paper-only, and the kill switch is
  not bypassed.

### Interval-censored resolution challenger - 2026-07-13

Exact published source temperatures remain unavailable for licensed long-term storage
(`101` parsed resolution contracts, `0` storage-approved exact sources, `0` exact settlement
actuals). The system now uses the information that is legally and operationally available
without manufacturing a point target: the official winning Polymarket bucket is treated as an
interval-censored observation.

`censored_gaussian/interval-censored-gaussian-v1` fits two global shadow parameters:

- additive source bias in degrees Celsius;
- a multiplicative Gaussian sigma scale.

The objective integrates probability across the complete winning interval, including open tail
buckets. It never substitutes a bucket midpoint for the unknown source value. Fit assessment is
deterministic blocked five-fold OOF by independent event; future application would additionally
require a passing fixed 60-block `fixed-range-brier-v3` prospective look. The
challenger writes only `accuracy_predictions`, is excluded from production probabilities, and
has no automatic activation path. Its CPU-bound grid search runs off the scheduler event loop.

First production fit at 2026-07-13 17:41 UTC:

- 57 complete historical winning-bucket blocks and 198 current markets covered;
- fitted bias `+0.098°C`, sigma scale `0.913`;
- historical OOF Brier worsened from `0.06131` to `0.07113`;
- delta-Brier 95% CI was fully positive: `[+0.00389, +0.01589]`;
- prospective blocks `0/60`, status `SHADOW`, `applied=false`;
- effective production cohort remained
  `stratus-accuracy-v4-20260713+288a6515612324d1`.

This is a successful negative result: a plausible source/rounding correction was tested and
rejected before it could degrade trading probabilities. Collection continues so the conclusion
can be checked prospectively rather than promoted from the historical fit.

### Pre-registered weak-slice selection challengers - 2026-07-13

Legacy diagnostics identify two trade slices that fail on both sides of the decision:

- `buy_yes`: model-minus-market Brier `+0.1304`, average CLV `-0.0488`;
- `6-24h`: model-minus-market Brier `+0.1064`, average CLV `-0.0510`.

Those results cannot justify changing the v4 production policy because all 51 resolved orders
belong to the legacy/unknown probability cohort. They can, however, define hypotheses before
new outcomes arrive. Two new immutable shadow policies are therefore registered:

- `exclude-buy-yes-v1`;
- `exclude-horizon-6-24h-v1`.

For every filled unresolved order, each accept/reject decision is frozen with the current
policy-definition hash and probability cohort. The existing baseline and three edge-cap hashes
remain unchanged, preserving all evidence already recorded. The slice policies use only side,
signal timestamp, and declared resolution timestamp; no outcome or closing price is available
to the decision. Qualification retains the same hard gate: at least 100 recorded resolved
opportunities, 60 independent station/day/kind blocks, 30 retained orders, retention of at
least 35%, model Brier below the same-time market, and a strictly positive lower confidence
bound for information-value improvement. They remain diagnostic and cannot alter execution or
activate themselves.

A separate blocked OOF experiment also tested fitting independent interval-censored Gaussian
corrections for daily highs and lows. The high-temperature subset had 40 events and still
worsened Brier from `0.06895` to `0.07643`; the low subset had only 17 events and was below the
minimum fit depth. This variant was rejected before runtime implementation.

Production verification at 2026-07-13 17:55 UTC:

- the effective cohort remains `stratus-accuracy-v4-20260713+288a6515612324d1` and has zero
  resolved trades;
- 11 current-cohort orders have all six policy decisions stored before resolution;
- `exclude-buy-yes-v1` retained 9/11 and `exclude-horizon-6-24h-v1` retained 3/11;
- 23 previously unresolved orders received only the two newly registered decisions; one new
  fill received all six, producing exactly 52 new immutable rows with no resolved backfill;
- Polymarket remains in paper mode, the kill switch is off, and the policies have no execution
  path;
- API, worker, and Postgres are healthy. The first normal minute cycle after startup completed
  execution and decision capture successfully.

### Historical convex temperature ensemble challenger - 2026-07-13

The first regional-model archive is only about one day deep, so promoting regional forecasts
would currently measure availability rather than skill. The next accuracy experiment therefore
uses the mature archive shared by all 20 stations: ECMWF, GFS, ICON, and GEM daily high/low
forecasts paired with the stored METAR-derived daily extremes.

`historical_weighted/convex-affine-temperature-v1` is a shadow-only two-stage estimator:

1. each model receives an affine bias/slope correction fitted only on the local station,
   high/low kind, and horizon scope;
2. corrected temperatures are combined with non-negative weights constrained to sum to one,
   regularized toward the pre-registered prior (ECMWF 35%, GFS 40%, ICON 15%, GEM 10%).

The evaluation is a true rolling walk-forward. Every validation prediction is produced from the
preceding 365 days only; the final 180 days are evaluated in 30-day folds. Confidence intervals
bootstrap independent `station/date/kind` blocks, so multiple horizons or buckets from one
weather event cannot masquerade as independent evidence. Current predictions additionally use
only forecast rows whose `available_at` precedes the exact stored production `model_prob.ts`.

The first full production assessment covered 20,604 validation predictions and 7,088
independent blocks. Temperature RMSE improved from `1.42547 C` to `1.35860 C`; mean squared
error changed by `-0.18618 C^2` with a 95% block-bootstrap interval
`[-0.20684, -0.15553]`. The job fitted 48 local scopes and wrote 528 current shadow
probabilities. Cockpit reports the scope-averaged weights and the local training-depth range,
not a cherry-picked representative scope.

This is strong evidence that the ensemble predicts the stored METAR extreme more accurately;
it is not yet evidence that it predicts the market's licensed settlement source more accurately.
Consequently `applied=false`, the effective production fingerprint is unchanged, and activation
still requires the fixed first 60 prospective resolution blocks to produce a martingale
Hoeffding LCB for mean Brier improvement above the preregistered minimum. There is
no automatic activation path.

Production verification at 2026-07-13 19:07 UTC:

- API and worker are healthy; the scheduler exposes 22 jobs and the weighted startup run
  completed successfully in 34 seconds;
- `/accuracy-overview` reports 528 current markets, 48 fitted scopes and local training depth
  195-364 samples;
- weights averaged once per unique scope are ECMWF 34.5%, GFS 32.5%, ICON 19.6%, GEM 13.4%;
- temperature-RMSE evidence is eligible, while settlement-aligned historical evidence is not
  proven and prospective resolution evidence is correctly `0/60`;
- `applied=false`, Polymarket remains `paper`, the kill switch is off, and the effective v4
  fingerprint still has zero resolved trades;
- the one startup command-poller miss occurred before the heavy challenger began; no missed or
  failed scheduler runs were recorded after startup completed.

The deployment was then extended with a second, settlement-aligned historical barrier. It uses
only resolved event snapshots for which all four global models were stored with
`available_at <= decision_at`; each local ensemble fit sees only the preceding 365 weather dates.
Horizons sharing the same station/date/high-or-low outcome remain one bootstrap block. This
direct test covered 63 fitted horizon-events, 45 independent weather blocks and 693 resolved
bucket rows. It did **not** reproduce the temperature-RMSE gain: production Brier was `0.05858`
and weighted Brier `0.06145`, delta `+0.00287`, with 95% CI
`[-0.00702, +0.00859]`. The interval includes zero, so this is inconclusive rather than a proven
degradation, but it explicitly fails the historical settlement pass. Cockpit shows the two
truth targets separately. The challenger remains shadow-only and future outcomes cannot be
backfilled into its prospective `0/60` gate.

#### Resolution geometry and conservative settlement shrink - 2026-07-13

The next investigation rejected a bucket-geometry bug before changing any probabilities. The
101 active resolution contracts comprise 83 Wunderground contracts published and rounded in
whole degrees Celsius and 18 in whole degrees Fahrenheit. The rules parser already converts the
published integer buckets into the correct latent-temperature intervals by expanding each finite
boundary by one half of the source unit (`0.5 C` or `0.5 F`, converted to Celsius). The event
contract remains `next_day_first_point`. No production bucket boundary was changed.

The weighted ensemble's residuals were then evaluated as an empirical kernel distribution instead
of a Gaussian. Empirical-only Brier was `0.060852` versus production `0.058577`; its paired
block-bootstrap interval `[-0.007675, +0.008669]` crossed zero. Replacing the Gaussian alone is
therefore not supported.

A separate conservative challenger was specified with a fixed, non-optimized blend: 75%
timestamp-matched production probability and 25% empirical weighted-ensemble probability. Across
the same 45 independent settlement blocks it scored Brier `0.056611` versus production `0.058577`,
delta `-0.001966`, with a 95% block-bootstrap interval `[-0.004536, -0.000502]`. This is the first
statistically positive settlement-aligned historical result in this sequence. Because the blend
was proposed after inspecting the broader family of weighted-ensemble experiments, it remains
prior evidence rather than confirmatory production evidence.

The implementation is isolated as
`historical_weighted_shrunk/convex-affine-empirical-shrink25-v1`. It writes a complete normalized
shadow distribution alongside the raw weighted expert, records the exact production baseline and
training provenance, and has no automatic activation path. Promotion still requires the fixed
first 60 new prospective Polymarket resolution blocks to pass the preregistered
`fixed-range-brier-v3` mean-improvement bound. Historical rows cannot populate that gate, and the
effective production fingerprint remains unchanged.

The first container restart also exposed an operational interaction: three concurrent
`asyncio.to_thread` fits remained GIL-bound long enough for six minute-critical scheduler jobs to
exceed their misfire grace window. The fit is now executed in one isolated spawned process. On the
verification restart the 37-second weighted run completed successfully while price/signals and
execution both ran successfully inside the same interval; no new scheduler miss was recorded.

#### Sealed prospective accuracy evidence - 2026-07-14

The first prospective implementation stored a versioned prediction but allowed a later run to
upsert the same logical key. That made a challenger reproducible in code, but did not prove that
the row being scored was the exact row published before its outcome. The weighted challengers
therefore start a clean confirmatory cohort under new versions:

- `historical_weighted/convex-affine-temperature-sealed-v2`;
- `historical_weighted_shrunk/convex-affine-empirical-shrink25-sealed-v2`.

All earlier v1 rows remain available for diagnosis and historical comparison, but are explicitly
`evidence_eligible=false`. They cannot populate a prospective block, survive a version filter as
v2 evidence, or contribute to the 60-block promotion gate. The confirmatory gate starts from zero resolved
blocks even where a v1 prediction exists for the same market.

Migration `20260714_0027` turns `accuracy_predictions` into an append-only evidence ledger. A
sealed row records its first `generated_at`, strict `training_cutoff`, exact
`baseline_model_prob_id`, deterministic `input_hash`, versioned `experiment_fingerprint`, and a
full `record_hash`. PostgreSQL rejects `UPDATE` and `DELETE`; an identical insert retry is
idempotent, while a replay of the same evidence key with different content is an integrity error.
The audit accepts only rows whose stored baseline foreign key resolves to the same market and
timestamp and whose provenance hashes are present.

Point-in-time eligibility is also stricter than the version label alone:

- calibration cases must have `valid_date < training_cutoff`; the outcome date being predicted
  cannot enter its own fit;
- every selected forecast must have been available by the frozen input snapshot;
- the linked production baseline must already exist, precede generation, carry a probability
  pipeline version, and be no more than five minutes old;
- `training_cutoff <= generated_at < resolves_at`; future-dated or post-resolution records fail
  closed and are excluded rather than repaired.

Regional forecasts remain shadow diagnostics until upstream availability is genuinely verified.
A regional slice is eligible only when every contributing run records
`upstream_availability_verified=true` and `init_time_semantics=upstream_run`. Locally inferred or
retrieval-time proxy metadata does not qualify, irrespective of apparent retrospective skill.
This prevents a fast-looking regional model from earning promotion through unknown publication
latency or a reconstructed run that would not have been observable at decision time.

Deployment verification tightened the protocol further. PostgreSQL now replaces any
client-supplied `generated_at` on a sealed insert with `clock_timestamp()`, verifies that the
linked baseline has the same market and timestamp, and checks that the sealed baseline
probability and pipeline version match that row. A sealed insert must also satisfy the SHA-256
length/provenance constraint before it can enter the ledger. Formal scoring recomputes each
`record_hash`, separates every `experiment_fingerprint`, and accepts only an atomic event vector:
one timestamp and pipeline, every resolved bucket present, probabilities summing to one, and
exactly one winning outcome. Coverage is measured against independently stored
`forecast_evaluations`; it is no longer hard-coded to 100%. The integration fixture proves that a
30/31 cohort remains ineligible even with positive skill and a negative upper confidence bound.

The production migration completed on 2026-07-14 with a verified pre-migration dump. The first
clean cohort contains 308 active markets for each weighted expert (616 sealed rows total), one
fingerprint per expert, 616/616 valid record hashes, zero provenance violations, and zero resolved
v2 blocks. Legacy diagnostic inventory remains 2,442 raw rows and 924 shrunk rows, all
`evidence_eligible=false`; the Cockpit reports 528 legacy shrunk markets excluded. Runtime remains
`paper`, kill switch remains off, and both challengers remain `applied=false`. Scheduler jobs now
have a 30-second misfire grace window so a short local CPU/DB spike delays an operational poll
instead of dropping it.

#### Sealed-v2 coverage for all probability challengers - 2026-07-14

The append-only protocol is no longer specific to the historical weighted ensemble. A shared
`sealed_accuracy` path converts the challenger distributions produced by `recompute_probs` and
the interval-censored job into new `*-sealed-v2` cohorts. Each conversion is fail-closed at the
whole-event boundary: all active buckets must share one event, timestamp and effective production
cohort;
the candidate vector and the timestamp-matched baseline vector must both be finite and
normalizable; and every bucket must have one exact raw `model_probs` baseline row. The
interval-censored and shared recompute paths supply the complete persisted market universe for
each candidate event, including blocked or unverified sibling buckets, so a filtered query result
cannot redefine an event as a smaller apparently complete cohort.

Normalization does not replace or obscure the production evidence. Every sealed bucket stores
the exact raw baseline probability and foreign-key ID attested by PostgreSQL, while the normalized
candidate and baseline vectors are recorded separately for event-level scoring. The event input
hash binds the market geometry, raw challenger payload, raw baseline rows, normalized cohort and
probability-pipeline fingerprint. Legacy expert versions remain visible for diagnosis but are
excluded from prospective evidence; every new expert/fingerprint pair therefore restarts at
`0/60` independent resolution blocks and cannot inherit an earlier result.

Formal audit does not trust the normalized baseline copied into JSON. It loads the immutable
foreign-key `ModelProb` rows, reconstructs and deterministically quantizes the complete normalized
baseline vector, and uses the JSON value only as an attestation that must match the reconstruction.
A mismatch excludes the cohort. The weighted challenger additionally requires the raw production
baseline total to lie within one percent of unity before normalization; a total outside
`[0.99, 1.01]` fails closed instead of concealing a missing or duplicated bucket.

Evidence identity uses `model_probs.components.pipeline.cohort`, which is the semantic pipeline
version plus its material code/config fingerprint. The plain `pipeline.version` is accepted only
as a compatibility fallback for rows created before effective cohorts existed. Event grouping,
input hashes, experiment fingerprints, baseline metadata and accuracy reports all use this same
effective identity; a rebuild with changed material inputs therefore cannot inherit evidence from
an older fingerprint that happens to share its semantic version.

The material pipeline fingerprint includes the shared sealer and both independent challenger
entry points: `services/sealed_accuracy.py`, `jobs/generate_censored_challenger.py`, and
`jobs/generate_weighted_ensemble.py`. A change to normalization, event-universe selection or
weighted evidence construction therefore creates a new effective cohort even if the semantic
pipeline label is not manually changed.

Migration `20260714_0028` closes the remaining baseline-mutation path. Once a `model_probs` row is
referenced by eligible sealed evidence, PostgreSQL rejects both its deletion and any material
update. This complements the append-only prediction ledger: neither side of a paired prospective
comparison can be rewritten after publication.

Migration `20260714_0029` moves the same cohort rule into PostgreSQL. On every eligible evidence
insert, the database derives the baseline identity as `pipeline.cohort` with fallback to
`pipeline.version` and rejects a different value supplied in the sealed distribution. Cohort
attestation therefore does not rely on application code alone.

Shadow publication is now an explicit runtime stage rather than an incidental minute-boundary
side effect. The schedule publishes complete shadow probability cohorts at 00:18, 05:18, 06:18,
12:18 and 18:18 UTC. The 05:18 snapshot additionally precedes the interval-censored challenger at
05:20 and the weighted ensemble at 05:22. Ordinary minute-driven forecast, ensemble and price
cycles continue to refresh production probabilities and signals, but write
`evidence_eligible=0`; only the explicitly scheduled shadow snapshots can start confirmatory
cohorts. All challenger jobs remain shadow-only. They have no automatic application path, do not
change the production probability fingerprint, and do not relax PAPER-mode or live-gate
requirements.

With candidate completeness and baseline reconstruction specified, the immutable Gamma outcome
ledger is now production-verified under migration `20260714_0030`. The remaining P0 evidence task
is the synthetic market side of evaluation:

1. add a dual-token market benchmark that records and validates both YES and NO executable books,
   rather than deriving one side algebraically, so model skill and execution opportunity are
   compared against prices that could actually have been traded. Each benchmark must retain token
   identity, timestamp, bid/ask, depth and source snapshot hash.

Only after this executable-book ledger is point-in-time and immutable should another model family
or promotion rule be added to the confirmatory program. Completion of the Gamma ledger establishes
outcome provenance; it does not establish predictive skill.

#### Production verification - 2026-07-14

Migration `20260714_0029` is applied in production after stopping the worker and creating the
verified pre-migration dump
`backups/stratus_pre_0029_20260714T061615Z.dump` (215,985,692 bytes). The deployed all-station
cycle processed 837 currently active markets. Its challenger stages wrote 7,964 eligible rows
across 825 markets and 13 diagnostic/shadow experts; every expert has one fingerprint in that
snapshot, the baseline effective-cohort mismatch count is zero, and no row is marked `applied`.
Ordinary minute cycles were observed repeatedly with `sealed_predictions=0`; the explicit cycle
wrote the sealed evidence, followed by 825 interval-censored and 1,650 weighted predictions.

The complete production eligible ledger contained 43,923 rows after the final restart, and all
43,923 record hashes verified. API `/healthz`, `/readyz`, and `/api/v1/accuracy-overview` returned
HTTP 200; PostgreSQL, API, worker and backup services were running; runtime remained `paper` with
the kill switch off. Verification before deployment passed 311 unit tests, 87 real-PostgreSQL
integration tests, full Ruff, and strict mypy. These checks validate provenance and operation,
not predictive skill: the new effective cohort still needs future independent resolutions before
any promotion decision.

#### P0 acceptance contract: immutable Gamma outcome evidence

Status: **implemented, migrated under `20260714_0030`, backfilled and production-verified**. The
outcome side of a confirmatory comparison must no longer be reconstructed from the mutable
`markets.resolved_outcome` column. A Gamma resolution is admissible formal evidence only after an
event-level ledger row satisfies all of the following conditions:

- the exact decoded HTTP entity body returned by `httpx` (after HTTP content decoding, before JSON
  projection) is stored as immutable `BYTEA` and hashed before parsing. PostgreSQL independently
  checks its byte length and SHA-256; the hash must never be derived from a later
  re-serialization;
- PostgreSQL assigns `first_seen_at` with its own clock. A client-supplied or replay timestamp
  cannot backdate when STRATUS first observed the resolution;
- the source event identity and the persisted STRATUS event identity agree, every persisted
  bucket appears exactly once, no unknown bucket is introduced, all members are final binary
  resolutions, and the complete mutually-exclusive vector contains exactly one winner;
- each bucket projection binds its internal market ID, external Gamma market ID, immutable bucket
  geometry and rules identity. Changing a market row later cannot silently change the meaning of
  the sealed winner vector;
- an identical replay is an idempotent success. A later Gamma response with the same canonical
  resolution may return the retained first witness but cannot replace its raw hash or timestamp;
  a different canonical outcome vector under the same event identity is an integrity conflict,
  never an update;
- PostgreSQL rejects `UPDATE` and `DELETE`, validates the raw-body size/hash plus the complete
  semantic projection against persisted markets, and uses a deferred constraint trigger to reject
  any commit where the full market projection is not atomically linked to the ledger. Formal
  consumers reparse the retained raw body and independently recompute the vector, universe,
  rules-manifest and record hashes before admitting a row;
- settlement writes and validates the ledger before applying the compatibility projection to
  `markets.resolved_outcome`. Formal forecast evaluation and accuracy audit read the ledger winner
  vector exclusively; the mutable market field remains operational compatibility data only;
- event-level training accepts only an exact, duplicate-free set of every bucket in the sealed
  Gamma vector at one decision timestamp. Partial cohorts never enter censored, weighted or
  settlement-calibration fitting;
- accounting records actual settlement time separately from Gamma market close time: the former
  drives loss limits and realised-P&L reports, while the latter is only the non-leaking CLV cutoff.

Verification covered strict parser failures (missing, duplicate, zero or multiple winners;
malformed final prices or timestamps; and outcome-label order), exact raw-byte hashing,
server-attested first-seen time, retry idempotency, conflicting replay rejection, append-only
database enforcement and direct-SQL attempts to store an invalid universe/projection. Production
counts, migration status and post-deployment audit coverage are recorded below.

Local acceptance completed with 29 focused strict Gamma parser/witness unit tests, 14 focused
immutable-ledger PostgreSQL tests, 340 total unit tests and 105 total real-PostgreSQL integration
tests. The database set includes raw-body tamper rejection, server time, replay/conflict,
append-only and deferred atomic-projection enforcement, one-way legacy `ForecastEvaluation`
attestation, complete-cohort training guards, multi-event bankroll serialization and delayed
settlement accounting. Full Ruff and strict mypy are clean. These are implementation checks, not
production counts or evidence of predictive skill.

#### Production acceptance evidence - 2026-07-14

Migration `20260714_0030` was applied with API and worker stopped after a binary-safe PostgreSQL
custom dump, `backups/stratus_pre_0030_20260714T080911Z.dump` (224,223,502 bytes; SHA-256
`12bea72018b05780ce01c811d199cdf52133dabd5edef163e54229ff2d743363`), was fully read and
decompressed by `pg_restore`. The previous application image remains tagged
`stratus:rollback-pre-0030` at
`sha256:8b18d9a144db39ecc1105892ec7d4f342f32b62ad0d93ead10a298e89d81904e`. API and worker were
recreated from the same final image,
`sha256:7f8f957710618037be18e3e2f67f97f954849f5023401f7fe081eaa2545875e7`.

The one-off Gamma backfill inspected 72 events and sealed 71 complete event vectors covering 781
markets and 3,397,855 exact decoded source bytes. Independent raw-body reparsing and database
diagnostics found zero raw hash/shape failures, universe-count mismatches, projection mismatches or
resolved-but-unattested markets. Event `693387` remained deliberately unsealed because Gamma
market `2887997` was still not closed after restart; this is fail-closed pending source finality,
not repaired evidence.

Formal evaluation contains 1,111 Gamma-attested rows. The remaining 585 legacy rows stay
diagnostic-only; partial provenance and attested outcome mismatches are zero. The formal audit
produced 4,132 skill reports and 128 drift events, with zero promotion-eligible models. The operator
API reports 101 independent samples and returns HTTP 200 from `/healthz`, `/readyz` and the
authenticated `/api/v1/accuracy-overview` endpoint.

After restart PostgreSQL, backup, API and worker were running; all observed scheduler jobs,
including settlement, completed successfully. Runtime remained `paper` with the kill switch off.
The post-restart health audit remained non-critical `warning`: shadow drift, recovery-window
collector uptime, stale near-market prices and pre-existing exposure breaches. It reported no
Gamma evidence hash or projection-integrity issue. This closes outcome-truth provenance only; it
does not validate predictive skill or authorize live trading.

#### P0 acceptance: independent dual-token executable benchmark - 2026-07-14

Status: **implemented, migrated under `20260714_0031` and `20260714_0032`, and
production-verified**. This closes the remaining synthetic market-benchmark gap. Formal evidence
no longer derives a NO quote, spread, depth or close as `1 - YES`.

The accepted protocol is `clob-dual-v1`:

- each collection request contains complete event cohorts and both token identities for every
  bucket; an event is never split across source responses;
- the exact decoded `/books` response body, requested token universe, source metadata, byte count,
  SHA-256, PostgreSQL first-seen time and deterministic record hash are retained in
  `clob_book_batches`;
- `clob_market_snapshots` projects the independently observed YES and NO bid, ask, midpoint,
  near-touch executable depth, source timestamp/hash, tick size and token identity. One-sided,
  stale, skewed or inconsistent pairs remain immutable diagnostic rows but are ineligible;
- the event-normalized market benchmark is formed only from eligible sibling snapshots in the
  exact same raw batch. Partial, cross-batch or stale event vectors produce model-only evaluation,
  never a repaired market score;
- prospective `ModelProb`, `Signal`, `Order`, `Position`, CLV and `ForecastEvaluation` rows retain
  the exact snapshot/protocol/token provenance. BUY_NO execution and marking use the real NO book;
- PostgreSQL independently validates raw hashes, atomic batch projection, side/token/price
  identity, event arithmetic and Gamma outcome linkage. The raw ledger is append-only. Migration
  `0032` additionally seals dual model probabilities and all decision/trade identity after use;
- operator analytics, the validation gate and settlement calibrator accept only prospective
  `clob-dual-v1` evidence. Legacy YES-only rows remain visible as historical diagnostics and P&L,
  but cannot fill any new gate counter.

Local acceptance passed full Ruff, strict mypy over 129 source files, 371 unit tests, 120
real-PostgreSQL integration tests, Compose validation, and the WinUI x64 build with zero warnings
and zero errors. The integration set includes raw-byte tamper rejection, deferred whole-batch
projection, same-batch event arithmetic, one-sided books, live side-token execution, partial fill
lifecycle, legacy fail-closed behavior, calibrator reset, immutable open-position identity and
formal legacy exclusion.

#### Dual-token production acceptance evidence - 2026-07-14

The deploy candidate was frozen without committing the pre-existing dirty worktree as
`backups/stratus_source_pre_0031_20260714T093753Z.tar.gz` (839,766 bytes; SHA-256
`f50939611e5780948dc32689dfafb0342108fb74339abbf2f7ecfb1bb8a8b26a`) and copied with an
identical hash to `F:/StratusBackups`. Its Git base is
`f607d99dcbb517bcb5f2deb56e1f4fc60dfb63fd`.

After the acceptance notes, credential rotation, atomic-backup fix and regression test were added,
the final non-secret source tree was frozen again as
`backups/stratus_source_post_0032_20260714T100825Z.tar.gz` (845,380 bytes; SHA-256
`b355eb6827748d4822eab17613359f158e15cf0021c68c86f55925f8f030c310`) and copied with the same
hash to `F:/StratusBackups`. The archive explicitly excludes gitignored `.env` and therefore
contains no rotated database credential.

API and worker were stopped at schema `20260714_0030`, runtime `paper`, kill switch off, with 54
open and 46 closed paper positions. The custom pre-migration dump
`backups/stratus_pre_0031_20260714T093938Z.dump` is 230,634,808 bytes with SHA-256
`3cb04e4ece52f6fa335b8cec978249ccfbeff6f78f8026b1f0e94565276a55bb`. It was fully decompressed,
restored into a scratch database, verified at revision `0030` with the same 54/46 position counts,
removed, and copied with the same hash to `F:/StratusBackups`.

The previous application image remains tagged
`stratus:rollback-pre-0031-20260714t093753z` at
`sha256:7f8f957710618037be18e3e2f67f97f954849f5023401f7fe081eaa2545875e7`. Migration reached
`20260714_0032`; all 16 expected evidence/trade triggers were present and enabled. API, worker and
the retained migrate container were then recreated from the same image
`sha256:53c9359572204c45a4f486bea6beb795ec8998fc1fc14c160ecf1b6cf29f7b19`.

The first strict production collection wrote five atomic batches for 110 near-resolution markets:
37 pairs were complete and eligible; 73 had independently empty/one-sided YES and NO books and
were correctly retained as ineligible. A wider startup refresh brought the acceptance snapshot to
26 batches, 561 paired snapshots and 2,263,374 decoded source bytes; 287 snapshot rows were
eligible across 250 distinct markets. Independent database diagnostics found zero invalid raw
hashes, token universes, batch projections, dual ModelProb anchors, Signal side/token/ask links or
Order identity links. Initial physical relation sizes were 824 KiB for batches and 584 KiB for
snapshots versus 2,210 KiB of decoded raw payload, showing effective TOAST compression at this
sample size.

The pre-existing mixed-benchmark settlement calibrator was reset before the first dual probability
calculation. It now records protocol `clob-dual-v1`, alpha 1, beta 0, zero training blocks and
`eligible=false`; it cannot apply historical mixed evidence to the new cohort. The startup and
first scheduled minute cycles completed successfully for price/signals, execution and settlement.
No worker/API traceback or critical log was observed.

Authenticated API acceptance returned `healthz=ok` and `readyz=ready`. Accuracy, Edge, Brier and
CLV responses all report `clob-dual-v1 / prospective`; total P&L separately reports
`all-protocol-history`. At the cohort reset the formal counters are deliberately zero: zero
forecast evaluations, independent samples, benchmark positions, resolved trades, Brier rows and
CLV rows; the live gate is blocked at 0/10. The 100 historical paper positions, including 46
closed, remain visible but contribute to none of those counters.

The post-restart health audit is non-critical `warning` with zero CLOB/Gamma integrity issues.
Warnings are expected recovery-window collector uptime, shadow drift, stale or one-sided
near-resolution books, active markets still awaiting their first dual anchor, and legacy exposure
overages. Runtime remains `paper`, kill switch remains off, and PostgreSQL, backup, API and worker
are running.

The same acceptance audit found that the local PostgreSQL role still used the Compose fallback
password `stratus`. Although the database port is loopback-only, the credential was rotated to a
cryptographically random 256-bit value without printing it, and the matching PostgreSQL and host
DSNs were written to gitignored `.env`. PostgreSQL, migrate, backup, API and worker were recreated;
the database returned at revision `0032`, API returned `healthz=ok` and `readyz=ready`, and the
post-rotation startup plus scheduled price, execution and settlement jobs all completed
successfully. The fallback is no longer present in container configuration.

Stopping the old backup sidecar during that rotation exposed a separate durability defect: it
wrote directly to the final `.dump` name, so an interrupted 156,632,406-byte archive retained a
readable TOC but failed a complete `pg_restore` with EOF. The artifact was preserved as
`stratus_20260714T095815Z.dump.partial.invalid`. The sidecar now writes only to
`.dump.partial`, validates the TOC, and atomically renames to `.dump`; stale partials are never
eligible for off-site sync and are removed after one day. A regression test fixes the required
write/verify/publish ordering. The first backup under the new protocol remained partial while
`pg_dump` ran, then atomically published as `stratus_20260714T100357Z.dump` (237,068,209 bytes,
SHA-256 `dbec6c87402d2cb8553f7a622bbc0fdd280f4c6aec2584ba6319085d8232ca78`) and passed a full
decompression read; no active `.dump.partial` remained.

This acceptance proves point-in-time market and outcome provenance, not predictive skill. Any
claim that the model beats the market must now accumulate from the zeroed prospective cohort.

#### Immutable confirmatory promotion authority

Revision `20260714_0033` replaces rolling-report activation with the source-bound protocol
`fixed-range-brier-v3` (`predictable-range-hoeffding-v1`). The complete global family is
preregistered only when its nine always-on experts have at least one exact full-vector scope;
conditional `regime_emos` joins when present. Each scope freezes code fingerprint, sealed
version, production cohort, station, horizon and kind. Only the first 60 post-registration
distinct Gamma-resolved blocks enter its single fixed look.

For every sealed probability vector, PostgreSQL enumerates all possible outcomes before using the
actual resolution and derives a predictable range for `baseline Brier - candidate Brier`. At the
look it subtracts
`sqrt(log(1/alpha_i) * sum(range_i^2) / (2 * 60^2))` from observed mean improvement. The trial
passes only when this conservative LCB is strictly above its preregistered minimum effect.
Family-wide alpha is controlled by Bonferroni; wins/losses and bootstrap intervals are diagnostic,
not activation authority. The conditional martingale formulation avoids asserting that adjacent
station-days are i.i.d.

PostgreSQL reconstructs every candidate/baseline/outcome vector, computes range statistics and
overwrites client-provided definition and decision hashes from authoritative normalized values.
Trial, evidence, look and activation ledgers are append-only. Registration itself is an immutable
prospective contract, so downgrade is refused after the first trial exists. Deferred constraint
triggers require that, once the whole family is terminal, exactly all passing scopes activate in
one transaction; partial, early or extra activation sets cannot commit. This prevents one
activation from changing the cohort while sibling trials still collect against the preregistered
baseline.

`model_skill_reports` remain diagnostic and can no longer apply hierarchical, regime, conformal,
trajectory, nonlinear, stacking or settlement-calibration challengers. Recompute requires an
exact effective activation and writes activation/definition/decision hashes into probability
provenance. Health treats an applied component without that exact binding, or a probability row
with the wrong activation-manifest hash, as critical. Cockpit reports rolling slices separately
from formal `n/60` confirmation progress. Full details and acceptance criteria are in
`docs/ACCURACY_PROMOTION_PROTOCOL.md`.

#### Implemented operational guardrail after P0 — revision 0034

The dual raw ledger remains intentionally append-only and has no automatic deletion. Revision
`20260714_0034` adds an append-only hourly capacity ledger and the explicit policy
`immutable-evidence-preserve-v1`: a 20,000,000,000-byte local evidence budget, warning at 75%, a
30-day projection horizon, and no extrapolation until at least 20 observed hours exist. Reaching
the budget is `blocking` and makes new CLOB collection fail closed. Before the measurement window
is complete, the API reports measurement progress rather than inventing a growth rate or runway.

A future archival policy may move unreferenced old batches to content-addressed off-host storage,
but it must never remove a snapshot referenced by a probability, signal, order, position, CLV or
evaluation, and its restore path must re-attest the original hashes and referential integrity.
Until such an archive exists, station promotion remains capacity-gated as well as accuracy-gated.

An early 22.48-minute production sample contained 85 batches, 1,826 paired snapshots and 7,375,796
decoded source bytes. The two physical relations occupied 4,072 KiB; the observed rates were 18.77
MiB/hour decoded and approximately 10.6 MiB/hour physical. A linear physical extrapolation is about
0.25 GiB/day, but this is not yet a daily capacity claim because due-event mix changes with horizon
and station stage. The required one-hour/day measurements remain the basis for the retention and
promotion budget.

The completed first-hour checkpoint covered 67.45 minutes: 254 batches, 5,489 paired snapshots,
22,305,465 decoded source bytes and 12,124,160 physical relation bytes. Of those snapshots, 2,592
were executable dual-token pairs. The measured rates were 18.92 MiB/hour decoded and 10.28
MiB/hour physical, consistent with the early estimate and still approximately 0.24 GiB/day under
a linear extrapolation. This closes the historical one-hour checkpoint only. The policy is now
explicit, but its forward projection remains empirically pending until the new append-only sampler
has accumulated at least 20 hours under the deployed workload. It does not authorize station
promotion by itself.

#### Operational evidence hardening — code complete in block 0034

The Phase 1 engineering gaps assigned to block 0034 are now closed without weakening PAPER or
live gates:

- `resolution-reconciliation` runs at startup and hourly. It persists one hash-attested record per
  resolved market, binds the Gamma event vector and rules hash, and compares the venue outcome
  with METAR-derived truth and, when available, final settlement-native truth. Results are
  classified as `match`, `boundary_sensitive`, `clear_mismatch`, `exact_truth_gap`,
  `exact_source_mismatch`, or `truth_unavailable`; exact-source conflicts are fail-closed.
  PostgreSQL validates the event/vector/outcome link against the immutable Gamma ledger, rejects
  deletion of current evidence, and archives material corrections in append-only
  `evidence_version_history`; polling-only heartbeats do not create false versions.
- settlement now owns a durable per-event lifecycle: `venue_pending`, `source_missing`,
  `ambiguous`, `manual_review`, or `resolved`. Partial Gamma vectors never settle individual
  buckets, source/database failures are isolated by event, evidence hashes and transitions are
  retained, and nonterminal cases escalate to manual review after 24 hours.
- signal generation applies an event-level probability-coherence guard before acceptance. It
  requires a complete contiguous/exhaustive bucket partition, one timestamp/horizon/pipeline
  cohort, and eligible `clob-dual-v1` snapshots from one CLOB batch. It normalises only a complete
  finite vector within the `0.001` mass tolerance and otherwise persists a stable `event_*` skip
  reason; missing buckets and synthetic complements are never imputed.
- health uses `info`, `warning`, `critical`, and `blocking`. `critical` and `blocking` make
  readiness false; only explicit `blocking` engages the kill switch. Collector SLOs distinguish
  six-hour startup grace, evidence collection, bounded planned maintenance, passing, and failing
  across the 48-hour window. Planned maintenance is visible, reasoned, and capped at two hours.
- immutable CLOB capacity is measured hourly in append-only samples under the policy above; no
  application path automatically deletes evidence. Collection is additionally fail-closed on a
  missing/stale sample, logical budget, or physical free-space reserve. Responses are bounded
  before buffering, and a second live measurement plus an advisory transaction lock makes the
  final capacity check and evidence write atomic. A capacity block engages the authoritative kill
  switch before paper/live execution can claim new work.

Operator visibility is implemented through `GET /api/v1/resolution-reconciliation`,
`GET /api/v1/settlement-cases`, and `GET /api/v1/storage-capacity`. Cockpit's **Resolution &
evidence operations** card combines Resolution QA, settlement lifecycle and immutable-ledger
capacity/runway into `HEALTHY`, `WATCH`, `REVIEW`, or `BLOCKING` operator states.
Optional evidence endpoints are isolated from core Cockpit data: version skew or one diagnostic
timeout produces a local `DEGRADED` state while last-known runtime, bankroll, exposure, and risk
remain visible.

The remaining work is both empirical and operational. STRATUS must accumulate the minimum
20-hour capacity window and independently resolved prospective dual-token evidence, but it also
needs licensed/storable official exact-source coverage, a durable validation-ledger summary,
cost-decomposed CLV, reliable alert delivery, and explicit source/provider provenance. No METAR
reconciliation, UI status, historical backfill, or capacity estimate is evidence that a model
beats the market or permission to relax the live gate.

#### Production deployment acceptance — 2026-07-14 17:27 UTC

Revision `20260714_0034` is deployed in the local production stack. API and worker run the same
image `sha256:c5f411a79d42a2d462b39c7e1de4ac71fc1c837b2ce2e1b5852eb65f149c9f41`;
the previous image remains available as `stratus:rollback-pre-0034-20260714t162700z` at
`sha256:45ac312a9795195168da61e5220387c8358d6aefeae299226c39fff61da2665b`.
The release gate passed 468 non-PostgreSQL tests, 135 clean-database PostgreSQL integration tests,
Ruff, strict mypy over 142 source files, Compose validation, a single Alembic head, diff validation,
and an x64 WinUI build with zero warnings and zero errors.

The exact pre-migration database is stored at
`backups/stratus_final_pre_0034_20260714T171723Z.dump` (303,286,490 bytes; SHA-256
`2CA5A8ADDA8F33E7B563377DE2751256B45C40B4E4F75B9048D7630956DDAB11`). It was restored into a
separate database before migration and reproduced revision `0033` plus the frozen counts: 1,617
markets, 122 positions, 1,002 orders, 84 Gamma outcome events, 1,423 CLOB batches, and 30,613 CLOB
snapshots. The matching final source archive is
`backups/stratus_source_final_0034_20260714T172513Z.tar.gz` (950,588 bytes; SHA-256
`D46631F4B82955462088F62EBBE64586FF1D893399AB041BE4C40A7F3D6BCCEB`). It explicitly excludes
`.env`, the local Compose override, logs, caches, build output, and the backup directory.

The first migration attempt safely stopped before DDL because Compose supplied empty optional
maintenance timestamps. Settings now normalise empty optional maintenance values to `None`; a
regression test and an in-container empty-environment smoke test cover the actual Compose path.
The second attempt upgraded transactionally. All four new tables exist and 14 database triggers
protect cross-ledger consistency, terminal evidence, append-only capacity samples, and material
version history.

Runtime acceptance returned `healthz=200`, `readyz=ready`, and 200 from overview, resolution
reconciliation, settlement lifecycle, and storage-capacity endpoints. Startup recorded capacity
success before discovery and price collection. The acceptance snapshot contains 924/924
METAR-consistent reconciliation rows, 0/924 attested official exact-source rows, 15 honest
`venue_pending` settlement cases, and zero material evidence history rows. The latest capacity
sample is fresh: 64,774,144 evidence-ledger bytes, approximately
1,003,134,504,960 free bytes against a 5,000,000,000-byte reserve, with automatic deletion still
disabled. Its severity is `info / measurement_window_pending`, because the required 20-hour
projection window has intentionally not yet elapsed.

The post-deployment health audit is non-blocking `warning`, and runtime remains `paper` with the
kill switch off. Current warnings identify restart-window collector coverage, prospective shadow
drift, stale or one-sided near-resolution books, 15 venue-pending events, and legacy active-market
diagnostic anchors; no new API/worker `ERROR`, `CRITICAL`, or traceback was observed. Cockpit was
launched through the packaged WinUI workflow and verified through UI Automation: the real
`STRATUS Operator Console` window rendered the then-current reconciliation classification and
capacity fields without a local `DEGRADED` state. Revision 0035 supersedes the ambiguous
`924/924 match` label: Cockpit must show METAR consistency and attested official exact-source
coverage separately, and incomplete exact coverage can never render `HEALTHY`.

This closes the engineering and deployment portion of block `0034`. The roadmap does not loosen:
the next decision points remain empirical — complete the 20-hour capacity window, resolve or
classify pending venue cases as their official vectors arrive, and accumulate prospective
dual-token calibration/CLV evidence until every live gate passes.

#### Empirical Ops P0 deployment acceptance — operational block 0035

Block 0035 was deployed on 2026-07-14 at 18:17 UTC as a code-only release; the database schema
remains at the single Alembic head `20260714_0034`. API and worker run the same image
`sha256:faffffee2741ebf8c14bb33d3f2df8219fdc351885a34e0e080069162572f395`.
The exact previous image remains tagged as
`stratus:rollback-pre-0035-20260714t181645z` at
`sha256:c5f411a79d42a2d462b39c7e1de4ac71fc1c837b2ce2e1b5852eb65f149c9f41`.

The release gate passed 470 non-PostgreSQL tests, 140 clean-database PostgreSQL integration tests,
Ruff, strict mypy over 143 source files, Compose validation, one Alembic head/current revision,
diff validation, candidate-image imports, and an x64 WinUI build with zero warnings and zero
errors. Three benchmark fixtures found by the full PG run were corrected to use one PostgreSQL
clock with strict `batch < decision < outcome < witness` ordering; the production trigger was not
weakened.

The reconciliation API now exposes full-ledger METAR availability/consistency, raw exact values,
attested exact-source coverage and reconciliation freshness independently of item filters. The
settlement API similarly exposes full-ledger open/stale/freshness aggregates. Health reports
`exact_truth_backlog` as `info` and stale reconciliation/polling as non-kill-switch `warning`.
Cockpit uses required JSON fields so an old API creates a local diagnostic `DEGRADED` state rather
than false zeros. With the current API it renders `924/924 METAR-consistent`, `Official exact truth
0/924`, `0.0% attested coverage`, fresh reconciliation and settlement polling, and `WATCH`; it can
never render `HEALTHY` while attested exact coverage is incomplete.

Expired markets are now excluded from both initial CLOB due selection and same-event sibling
expansion using the strict boundary `resolves_at > now`. Post-deployment acceptance retained nine
new raw batches and 198 snapshots with zero snapshots for a market already expired at batch
first-seen time, despite 165 legacy rows still marked `active` past their resolution timestamp.
This removes avoidable collection cost without rewriting or deleting any historical evidence.

Runtime acceptance returned `healthz=200`, `readyz=ready`, API `healthy`, worker `running`, PAPER,
kill switch off, and a non-blocking `warning` audit. The new API reported 924 reconciliation rows,
0 stale rows, 15 open venue-pending settlement cases, 0 stale cases, and formal live gate `0/10`.
No API/worker error, critical log line, or traceback appeared after deployment. The final packaged
`STRATUS Operator Console` window was launched, responded to UI Automation, and was left running.

This block improves evidence honesty and operational efficiency; it does not prove accuracy.
Revisions `0035` and `0036` subsequently delivered the prospective-validation ledger and durable
local alert inbox described below. None of this relaxes the ten-part live gate.

#### Prospective validation and local alerts deployment acceptance — revisions 0035/0036

Migrations `20260714_0035` and `20260714_0036` were deployed on 2026-07-14. Production is at one
Alembic head, `20260714_0036`. API and worker run candidate image
`sha256:414a9fbd827c0b1e8d02143190657e6e527263ff1eed03d514a71eba29f40b5c`. The exact pre-release
application image remains available as `stratus:rollback-pre-0036-20260714t195534z` at
`sha256:faffffee2741ebf8c14bb33d3f2df8219fdc351885a34e0e080069162572f395`.

Release verification passed Ruff, strict mypy, 475 non-PostgreSQL tests, and 157 clean-database
PostgreSQL tests through the full Alembic chain. The latter includes direct-writer tamper cases,
zero backfill, deferred enrollment completeness, event-lock races, idempotency, close-window
enforcement, notification concurrency/lease ownership/retry/dead-letter behavior, API contracts,
and both guarded downgrades.

Before migration, a full database backup was taken at
`backups/stratus_pre_0036_20260714T202226Z.dump` (318,697,098 bytes; SHA-256
`18CCB8FE674BF339F5B9DF55ECDBED278EDBB2B13D2E656437C54F0941C1BC31`). A complete restore drill
matched the recorded schema and evidence-count vector exactly before production upgrade.

Revision `0035` opened one immutable prospective runtime with zero enrollments and zero outcomes.
The API reports scope `prospective` and integrity label
`database-attested-order-fill+gamma+clob-v1`. That zero is the required no-backfill boundary, not
missing historical data and not an accuracy result. The first real qualifying post-boundary fill
will provide the first empirical enrollment; no synthetic trade will be created for acceptance.

Revision `0036` retained all 577 legacy alert rows as unacknowledged, pre-activation local inbox
history. No Telegram runtime was inserted and no external attempt, retry, lease, or dead-letter
state was created. The API therefore reports Telegram `not_activated`, zero external pending
rows, and zero dead letters. Starting Telegram remains a separate explicit operator decision;
legacy rows will remain local-only rather than being replayed.

Runtime acceptance is PAPER with the kill switch off and the formal gate blocked at `0/10`. The
manual health audit remains a non-blocking `warning`. These are deployment facts only: no
prospective outcome exists yet, and neither the successful tests nor the operational runtime
proves forecast accuracy, positive CLV, profitability, or live readiness.

The remaining P0 work is now empirical and source-specific:

1. Acquire legally storable official exact-resolution evidence and document the exact resolution
   source and rounding rules for every traded city/station.
2. Accumulate genuine prospective order fills and Gamma outcomes until accuracy, CLV,
   concentration, drawdown, and all eleven gate checks can be evaluated on the frozen cohort.
3. Decompose CLV into model edge, spread, latency, expected slippage, and adverse-selection costs
   without reconstructing unavailable venue evidence.
4. Forecast-provider, model-run, first-seen availability, raw-response, and model-input provenance
   is implemented prospectively in revision `20260715_0037`. Continue accumulating verified
   upstream-run cohorts; Open-Meteo publication time remains explicitly unverified, and structural
   breaks cannot be evaluated until multiple real cohorts resolve.

Until these empirical requirements pass, live mode remains blocked regardless of legacy P&L or
the completeness of the operator tooling.

#### Forecast source and model-input provenance — revision 0037

Revision `20260715_0037` closes the engineering portion of P0 provider/run provenance with the
zero-backfill protocol in `docs/FORECAST_PROVENANCE_PROTOCOL.md`. Exact successful HTTP bytes,
URLs, request timing, optional HTTP metadata, parsed daily/trajectory manifests, database
first-seen availability, run bindings, and probability input hashes are retained prospectively.
NWS stores its discovery and hourly responses as one ordered evidence chain. Multi-member ensemble
requests share one ingest without pretending that 31/51 members are one model run.

PostgreSQL independently validates hashes, raw byte sizes, response-set completeness, exact
materialized temperatures and trajectories, append-only evidence, and cited-run immutability.
Legacy inputs are never backfilled: they remain visible and force probability provenance to
`complete=false`. Health reports incomplete post-boundary active-market lineage without stopping
paper collection, while the formal live gate now requires complete current-pipeline provenance as
its eleventh check.

Operator API `GET /api/v1/forecast-provenance` and Cockpit's **Forecast source evidence** card show
collection volume, raw storage, model/run coverage, latency, and active-probability completeness.
This proves lineage, not predictive skill. Revision 0038 subsequently completed the separate
zero-backfill execution-cost/markout engineering P0; the remaining evidence P0 is official
exact-resolution source coverage and genuine prospective outcomes and fills.

Production acceptance on 2026-07-15 is recorded in
`docs/FORECAST_PROVENANCE_PROTOCOL.md`. Revision 0037 runs on
`sha256:db629090a014751148a3b68fe2dd800cda446e6c77201805bf51133754dfadec` with 205 immutable
ingests, 206 independently verified HTTP bodies, 34,894 materialized daily extrema, and 1,041
trajectories. The accepted regression baseline is 489 non-PostgreSQL plus 166 real-PostgreSQL
tests. Current prospective lineage is complete for 297/319 probabilities; the 22 deliberate
exceptions are still-open KLGA 2026-07-14 markets whose inputs predate the zero-backfill boundary
and therefore remain visible, unbackfilled, and live-blocking.

#### Execution cost and post-fill markouts — revision 0038 deployed

Revision `20260715_0038`, specified in `docs/EXECUTION_COST_PROTOCOL.md`, is implemented and
production-deployed. It opened a new database-clock cohort with zero backfill, commits an immutable
order intent before execution, persists the exact PAPER execution book, records atomic
maker/taker fill legs, and seals fixed 5m and 30m markouts. Legacy fills remain `unknown`; live SDK
observations are not mislabelled as raw HTTP; live payloads remain cost-ineligible until trade ids,
role, fee, and venue time are verified.

Production acceptance on 2026-07-15 recorded:

- schema at the single head `20260715_0038`;
- API and worker on image
  `sha256:f64718d1c503acd9db7eeb4b8dfd7d82464e9a3ec977c7e9c99d82d504cd30f5`;
- pre-migration backup `backups/stratus_pre_0038_20260715t0615z.dump`, SHA-256
  `3F9106F9D1FFC511CE941C55E6A79EDA164E21B1E31F952079AD337C54230AF5`;
- 711 tests plus Ruff, strict mypy, and Docker Compose validation passing;
- two genuine post-boundary PAPER attempts, both terminal no-fills and both immutably sealed.

Because neither attempt filled, the execution report correctly says `evidence_incomplete`: the
release proves protocol plumbing and fail-closed evidence semantics, not eligible cost/markout
coverage, predictive edge, profitability, or live readiness. The formal eleven-part gate is
unchanged. A possible twelfth execution-quality gate remains a future, separately reviewed change
after at least 100 eligible PAPER seals and prospectively frozen coverage/cost thresholds.

Revision 0038 also preserves the accuracy/execution boundary around event probabilities. Exact
materialization proves that the immutable final event vector and its per-bucket probabilities were
persisted exactly; event coherence separately applies the stricter executable-market and event
geometry rules used by signal generation. Accuracy evidence may accept exact materialization while
trading remains coherence-blocked. Reports and future promotion logic must continue to expose both
results rather than treating either as a proxy for the other.

The next work is empirical: collect genuine filled PAPER attempts and their sealed 5m/30m marks,
monitor eligible coverage and cost slices, and accumulate the independent resolved outcomes needed
by the unchanged live gate. No synthetic acceptance trade and no threshold relaxation is allowed.

#### Official resolution-source evidence — revision 0039 deployed

Revision `20260715_0039`, specified in
`docs/OFFICIAL_RESOLUTION_SOURCE_PROTOCOL.md`, is production-deployed. It closes the engineering
part of exact settlement-truth provenance with a prospective, zero-backfill chain from an exact
resolution contract and reviewed contract-scoped policy to immutable raw JSON, deterministic
extraction, an exact actual, and its strict `settlement_actuals` projection. Database triggers
independently validate content hashes, raw byte lengths, policy/parser seals, identities, decimal
conversion, timestamps, and the one-to-one projection; every evidence table is append-only.

The operator can no longer type a settlement temperature. The legacy mutation returns HTTP 410.
The only enabled ingestion shape is a server-root, hash-addressed artifact whose bytes, digest,
length, finality witness, publication field, and exact numeric token are independently checked.
Policy approval is a two-step canonical-hash attestation, and a changed `rules_hash` always needs a
new external review. Read APIs never return raw bytes.

Production acceptance on 2026-07-15 recorded:

- one Alembic head, `20260715_0039`, and identical API/worker/migration image
  `sha256:e4edd3c4e35228d2f8bea93d058c963bc6e92eb2b35986c6694b862fd79adec8`;
- rollback tag `stratus:rollback-pre-0039` at the exact 0038 digest
  `sha256:f64718d1c503acd9db7eeb4b8dfd7d82464e9a3ec977c7e9c99d82d504cd30f5`;
- backup `backups/stratus_pre_0039_20260715T093831Z.dump` (541,389,370 bytes; SHA-256
  `CB2F7D46B12D2BDFD726167FDA84BF6377BF1AF39E0C87B03292D1AE5DC37FF5`) proven by a full restore;
- a restored-production 0038 -> 0039 -> 0038 -> 0039 drill with one boundary row, zero evidence
  backfill, all 155 contracts preserved, and guarded downgrade behavior;
- Ruff, strict mypy, 575 non-PG tests, 195 real-PG tests, Compose validation, API smoke, and an x64
  Cockpit build with zero warnings/errors;
- PAPER and kill switch off, 42/42 fresh jobs successful, API/worker logs clean, source integrity
  valid, and a non-blocking informational authorization issue.

Cockpit now shows **Official resolution truth** independently from METAR consistency. The deployed
baseline is honestly `EXTERNAL AUTH BLOCKED`, licensed contracts `0/155`, exact coverage `0/155`,
artifact import disabled, and no collected raw response. This is a correct safety state, not a
failed collector and not an accuracy result.

The remaining official-truth work is external and empirical: obtain written contract-specific
permission for commercial automated use and immutable raw/value retention, prove exact product
equivalence to the market's named Wunderground source, then attest and collect the first real
post-boundary artifacts. The documented TWC 07:00-to-07:00 daily summary is not accepted as a
local-calendar substitute. Until a product clears both gates, no network collector is enabled,
no historical truth is backfilled, and live remains blocked.

#### Evidence continuity production acceptance — revision 0040

Revision `20260715_0040`, specified in `docs/EVIDENCE_CONTINUITY_PROTOCOL.md`, was deployed and
accepted on 2026-07-15. Production has one Alembic head, and API, worker, and migration services
run the same final image
`sha256:9bc680bd35943738e5c30df065cc5404ac5eb7e33d4dc55b5054b87dbf0d101f`. The retained
pre-migration backup is `backups/stratus_pre_0040_20260715T124901Z.dump` (690,770,617 bytes;
SHA-256 `435cba086e45afe26d97d257f7803730918d3ac666f85e483a0aab032c746145`); full restore and
`0039 -> 0040 -> 0039 -> 0040` drills preserved every source count and proved zero backfill.

The exact reviewed plan
`ad0bde63223755c9dc02ca05ad48c4a1b73b3ddc3a3cc62d3d652f40d4b66e32` opened epoch 2 at
`2026-07-15 13:55:49.043719 UTC`, with `predictive_validation` forked and the other five lanes
continued. Production now has 2 immutable plans, 2 epochs, and 12 lane rows. All 20 genuine
epoch-2 PAPER intents carry all seven release/lane attestations and became resting maker orders;
no synthetic fill or backfilled evidence was created.

Candidate-1 acceptance correctly failed closed on a Python/PostgreSQL float-canonicalization
mismatch: the invalid intent and order did not commit, and the kill switch engaged. The final
release uses PostgreSQL-compatible finite-float canonical JSON and rejects non-finite values. It
also replaces a 20.600831-second full probability scan with an indexed active-market lookup
(0.390154 seconds, about 52.8 times faster) and makes the Cockpit continuity read non-authoritative
with a 30-second single-flight cache. Ten concurrent cold API reads completed in 1.278 seconds,
with zero advisory locks or lock waiters during Cockpit auto-refresh.

Final verification passed Ruff, strict mypy over 162 source files, 637 non-PostgreSQL tests, 216
fresh-PostgreSQL integration tests in 422.06 seconds, Compose validation, one-head Alembic
validation, and an x64 WinUI build with zero warnings/errors. Runtime is PAPER, kill switch off,
`readyz=200`; final health audit `id=540` has 15 non-blocking issues and zero blocking/critical
issues. The operator console is running, and the initial window recorded 51/51 successful jobs
without an API/worker error, critical, traceback, or source-hash failure.

The evidence state is still honestly `collecting`: continuity matches, liveness is
`waiting_for_due`, the bottleneck is `maker_orders_resting`, complete collection days are 0, and
ETA remains unavailable until seven complete days. Coverage remains 5 active PAPER and 15 active
SHADOW stations, with 6 enrollments, 0 outcomes, and 0 exact official actuals. Revision 0040
therefore proves continuity, integrity, and operations — not accuracy, edge, profit, or LIVE
readiness. The empirical and external-source live gates remain unchanged and blocked.

#### Emergency execution-fill projection — revision 0041 production acceptance

Revision `20260715_0041`, specified in
`docs/EXECUTION_FILL_PROJECTION_PROTOCOL.md`, is implemented, reviewed, and production-accepted for
migration, continuity activation, and the initial PAPER cutover. It responds to two fail-closed
execution defects: a natural PAPER writer attempted to replace immutable `orders.price` with actual
execution VWAP, and the finalizer derived
VWAP from independently rounded cash notional rather than from atomic `shares * price` legs. The
database rejected the invalid mutation/projection; 0041 fixes the writers and consumers without
weakening those guards.

The candidate establishes these quality controls:

- requested limit remains immutable in `Order.price` and
  `ExecutionOrderIntent.requested_limit_price`; actual fill VWAP is the exact share-weighted atomic
  leg price and is exposed separately in API, Cockpit, Telegram, positions, seals, and validation;
- terminal obligation/enrollment/seal projection freezes the leg set under one deterministic intent
  lock, with acceptance tests covering both possible leg-versus-projection race winners;
- current formal performance, skill/luck, ROI, live gate, Edge, calibration/Brier, CLV,
  root-cause actions, and drill-downs use one lane-scoped immutable order snapshot and fail closed
  rather than substituting legacy position history;
- predictive/execution `continue` preserves compatible evidence and either lane's `fork` resets the
  full formal denominator; selection projection independently requires the snapshot's formal order
  ids plus the active selection identity/boundary, so selection `fork` resets selection reporting;
- the reviewed release must fork `predictive_validation`, `execution_evidence`, and
  `selection_policy`; the other three lanes may continue only on exact identity equality;
- release-manifest construction verifies the connected database has exactly one Alembic head equal
  to 0041 and hashes material migration SQL into lane identity;
- fee backfill is limited to no-intent PAPER fills whose actual `filled_at` is before the execution
  boundary; ambiguous post-boundary rows are not laundered into legacy evidence; and
- Telegram classifies a proven no-fill as warning and a claimed fill without atomic evidence as
  critical, preserving non-zero cancelled-fill semantics.

Observed production acceptance records:

- retained backup `backups/stratus_pre_0041_final_20260715T190832Z.dump`, 802,838,676 bytes,
  SHA-256 `526ac9d4554f252e2a2388596658b6bc41fad674d420aff23ff5c703d010a93d`;
- one Alembic head, `20260715_0041`, and a passing migration drill;
- API and worker on image
  `sha256:ab2b627d77faef9d7f2114fdbf208d83947163fd81565d0f0197cec682d6b181`;
- reviewed plan `7eb012e66704fb70cf30f20fce0a6a344e369089fe2770616956b6054faea5d5`,
  opening epoch 3 at `2026-07-15T19:14:03.860671Z`;
- forks for `predictive_validation`, `execution_evidence`, and `selection_policy`, with
  `official_resolution_truth`, `station_lifecycle`, and `operational_legacy` continued;
- matching post-activation continuity, a `warning` audit with zero critical/blocking findings, and
  audited command 5 resuming PAPER in the initial cutover window; and
- Ruff, strict mypy over 162 source files, 661 non-PostgreSQL tests, 227 fresh-PostgreSQL tests, and
  WinUI x64 Debug and Release builds passing.

One observational 0041 P0 is historically inconclusive. Through `2026-07-15T19:29:36Z`, the new epoch had 15
genuine intents/resting maker orders and zero fill legs. No natural order had crossed. Therefore the
production requested-limit-versus-exact-VWAP projection had neither failed nor passed. The later
kill switch expired the backing signals while leaving the maker orders resting; those historical
orders can no longer fill because the fill path requires a pending signal. They must be sealed as
zero-fill quarantine outcomes, not used as synthetic execution evidence. A future 0042 natural fill
validates only the current cohort and cannot be relabelled as 0041 evidence.

A later sleep/wake checkpoint prevents treating the initial resume as a permanent steady state.
After approximately 15 hours asleep, the runtime correctly auto-engaged the kill switch at
`2026-07-16T10:38:35Z` because scheduler and capacity evidence were stale, with 25 jobs recorded as
missed. Startup refresh restored capacity to `within_budget`; discovery observed 199 events, 75
targets, and 825 markets, while observations reported 20/210, forecasts 20/2,118, probabilities
374, and CLOB 38 written of 110 due.

Audit 572 isolated one critical condition: `official_resolution_truth` identity no longer matched
after contract inventory grew from 155 to 182. Reviewed plan 5
(`cba642b35b31aa17428b5c13148daef19c0e85256235873d6093151a3a80829a`; release manifest
`dcfc5302e0a4786d8017254555743d307a1d208134269047e5950f714b353e27`) opened epoch 4 at
`2026-07-16T10:43:00.712204Z`. It forked only official truth and continued the other five lanes,
preserving the epoch-3 predictive/execution/selection evidence boundaries.

After CLOB, settlement, and 1,000-item reconciliation refreshes, audit 573 is `warning` with 14
findings and zero critical/blocking; continuity matches and 49 post-epoch-4 jobs succeeded with zero
errors. The kill switch nevertheless remains ON because daily P&L is `-$96.31`; execution waits
for the next `America/New_York` daily-risk boundary, whose instant is reported in UTC. This is an
intentional risk hold, not an 0041 failure. Natural
observation still has 15 stranded epoch-3 intents/orders, zero fill legs, and zero prospective
enrollments.

This emergency revision changes evidence integrity, not model skill. It does not relax any accuracy,
CLV, official-truth, legal, risk, or LIVE criterion. Read-only accuracy/station-reliability work may
continue while execution remains risk-held. The stranded 0041 orders require formal zero-fill
quarantine. New natural-fill collection resumes only prospectively under the accepted 0042 cohort
after an audited safe resume.

#### Station reliability and selection trust — revision 0042 production acceptance

Revision `20260717_0042` implements and production-accepts the Phase-3 station reliability and
cooldown work. It does not close the separate 0041 natural-fill projection observation; that
historical P0 remains inconclusive because its backing signals were expired, and a later 0042
crossing cannot rewrite it.

The accepted revision collapses reconciliation evidence to the independent unit
`station × local date × weather kind`. If one unit has several bucket rows, the most conservative
classification wins. The score uses only decisive matches and conflicts:

- `m = match units`;
- `c = exact_source_mismatch + clear_mismatch + exact_truth_gap`;
- `n = m + c` and observed reliability `p_hat = m / n`; and
- the selection statistic is the two-sided 95% Wilson lower bound
  `LB = (p_hat + z²/(2n) - z*sqrt(p_hat*(1-p_hat)/n + z²/(4n²))) / (1 + z²/n)`,
  where `z = 1.959963984540054`.

`boundary_sensitive` and `truth_unavailable` units are counted and displayed but excluded from
`n`. They are non-punitive because neither proves a station/source conflict. Unavailable official
truth is never silently converted into a match. The accepted defaults are a 180-day window,
minimum 20 comparable units, Wilson lower bound at least 0.80, 48-hour resolution-conflict block,
90-minute current-observation freshness, and 45-minute snapshot freshness; all material values are
included in the station-reliability policy hash.

The resulting status and authority are deliberately asymmetric:

- `COLLECTING` may participate in PAPER collection when its current-date observation is fresh and
  no conflict/policy/integrity block exists; this is how evidence can mature without granting live
  authority;
- `RELIABLE` means the sample threshold and Wilson threshold pass, but it still does not bypass
  station lifecycle, global live gate, legal, wallet, risk, or continuity controls;
- `WATCH` and `BLOCKED` are selection-blocking; and
- LIVE requires both an `approved` lifecycle stage and a current, valid `RELIABLE` snapshot, in
  addition to every existing global gate.

Snapshots are append-only and bind evaluated date, observation scope, exact conflict counters,
cooldown expiries, stable reasons, policy hash, source hash, and record hash. New decision
provenance binds a signal to the exact station-reliability snapshot used at selection, and execution
revalidates current policy/date/freshness rather than trusting a previously pending signal. A
missing, stale, tampered, wrong-policy, wrong-local-date, or newly blocked snapshot fails closed.
Because that provenance and recheck alter both decision and claim semantics, epoch 5 forked
`selection_policy`, `station_lifecycle`, and `execution_evidence`; it also forked
`official_resolution_truth` after the database-bound manifest detected independent inventory
growth. Epoch 6 then forked only `operational_legacy` for the reporting-window hotfix. The
database-bound plans remained authoritative; no mismatch was forced into an undocumented continue.

The Calibration page now separates **Forecast calibration matrix** (bias, sigma, Brier and sample
maturity, scoped by the page filter) from **Station trust** (global station-selection evidence) in
two bounded native tabs with independent scroll ownership.
The trust table shows Wilson lower bound and `n`, exact blocking reasons, observation/evaluation
dates, conflict and observation block expiries, boundary/unavailable exclusions, and shortened
policy/source/record hashes. A `/stations` diagnostic failure degrades only the trust table; it no
longer erases otherwise available forecast-calibration rows.

The 0042 cutover satisfied the following acceptance controls; PAPER resume under the new code is
still a separate audited operation:

1. one clean `0041 -> 0042` migration drill and downgrade refusal after evidence exists;
2. full non-PostgreSQL and fresh-PostgreSQL suites, strict mypy, Ruff, and WinUI x64 builds;
3. retained pre-cutover backup plus identical API/worker image digest;
4. kill-switch-only quarantine of stranded PAPER orders with formal no-fill seals for every
   immutable intent, plus a hard zero count of nonterminal LIVE orders;
5. reviewed continuity actions, including expected forks of `selection_policy`,
   `station_lifecycle`, and `execution_evidence`, with no backfill;
6. fresh settlement -> reconciliation -> reliability assessment ordering before signals/execution;
7. a post-activation audit with zero critical/blocking findings and matching evidence identities;
8. an operator-visible check that inactive stations do not inflate active selection-block counts;
   and
9. the 0041 natural-fill observation retained as historically inconclusive, never rewritten by
   quarantine, backfill, or post-0042 evidence.

Observed acceptance on 2026-07-17:

- retained backup `backups/stratus_pre_0042_20260717T061253Z.dump`, 886,676,148 bytes, SHA-256
  `2934FC7038279D4B0241633ED77C488DFDE16C573C4E4DC57EA4ECA3182661C4`, with both catalog check and
  full isolated restore drill passing;
- one production Alembic head `20260717_0042`, zero signal-provenance backfill, and 67 stranded
  PAPER orders quarantined as 30 formally sealed immutable intents plus 37 legacy orders without
  fabricated intent; nonterminal LIVE count remained zero;
- epoch 5 opened by plan
  `6ec51d341119e5203ada6e2dec35bc662b2fc655c8d1643385f1f81de20442c3`, forking execution,
  selection, station lifecycle, and official truth, with the latter independently required by
  contract inventory growth from 182 to 209;
- a reporting-boundary defect found by the first audit was corrected without rewriting evidence;
  hotfix image `sha256:8ea86c06caeba861a61e015d4b6d0a2e6e107526e21e399eed3b45d8de20b32d` and plan
  `666b265b892b21306dd8a5f0425883ec15f907ca804853a13f5a28a168da7c16` opened epoch 6, forking
  only `operational_legacy`;
- the prescribed settlement -> reconciliation -> station-assessment -> signal -> execution order
  completed, 20/20 active stations received current-local-date snapshots under one policy hash,
  and post-boundary signals have immutable snapshot provenance;
- formal audit 621 was `warning` with zero critical/blocking findings; continuity, cohort, image,
  and every lane identity match, with no frozen lanes;
- Ruff, strict mypy over 166 modules, 689 non-PostgreSQL tests, 239 PostgreSQL tests across clean
  sequential shards plus focused hotfix continuity regressions, and WinUI x64 Debug/Release builds
  with zero warnings/errors passed; and
- the live operator API returns 20 active trust rows, zero active selection blocks, two inactive
  archived stations excluded from active totals, and all 480 forecast-calibration rows.

Audited operator command 6 resumed only Polymarket PAPER at
`2026-07-17T07:10:31.783545Z`. Kill switch is OFF, `/readyz=200`, continuity allows execution, and
the post-resume health audit remains non-critical `warning` with zero critical/blocking findings.
The first natural cycle produced two `paper_resting` orders whose signals bind current-date station
snapshot 63, release epoch 6, and every continuity-lane attestation. This closes the
engineering/deployment milestone, not the empirical live gate: no statement here proves model
skill, positive CLV, profitability, official-source licensing, legal readiness, or LIVE authority.

##### Deterministic scheduler hotfix — production acceptance, 2026-07-17

Runtime image `sha256:061e73111d8ac0464c3bc2b2cffe92ff8370984789a9283fc734403d0859f843`
was accepted through reviewed plan 9
`661b47e1f4244f3cad6ce68090537a25c913cc955fc69f2508cd2a9fb8999a66` as epoch 7, boundary
`2026-07-17T08:26:03.767299Z`, with epoch 6 as predecessor. The runtime-only change retained
Alembic head `20260717_0042`, forked `execution_evidence`, `operational_legacy`,
`selection_policy`, and `station_lifecycle`, and continued `predictive_validation` and
`official_resolution_truth`.

The production scheduler now serializes observation -> station assessment, settlement ->
reconciliation -> station assessment, and price/probability/signal -> execution through a shared
station barrier. Price cohorts run at second 30; settlement polls run at minutes 00, 10, 20, 30,
40, and 50, with the minute-00 poll owning the hourly reconciliation composite. Startup job-run IDs
31656-31661 and recurring composite records 31693/31694 plus later cycles verified the order.
Standalone settlement 31738 fired exactly at `08:40:00Z`; recurring observations 31787 completed
only after 20-station/214-report collection and child station assessment 31786 succeeded.
Audited stop command 7, a `0/0/0` quarantine, and PAPER-only resume command 8 bracketed activation.
Audit 634 was `warning` with 6 informational, 6 warning, 0 critical, and 0 blocking findings;
readiness was 200 with matching image/cohort/lane identities and no mismatched or frozen lane.

All 205 first post-resume signals were skipped with present, fresh, current-local-date station
snapshots; no executable signal or order existed yet. Ruff, strict mypy, 695 non-PostgreSQL tests,
and 61 PostgreSQL tests passed. Canonical release evidence and parent/child job semantics are in
`EVIDENCE_CONTINUITY_PROTOCOL.md` section 20.2. This is scheduler-integrity acceptance only: it
does not prove skill, CLV, profitability, LIVE readiness, or revision 0041 natural-fill success.

##### Candidate-anchor v2 — selection bottleneck correction, 2026-07-17

The next read-only production diagnosis separated a genuine absence of edge from a selection
implementation defect. Full recomputes published all 308 current rows and all 28 active event
vectors were exactly materialized, while 204--206 candidate rows in 27/28 events had their own
eligible dual-token snapshot. Revision-v1 admission nevertheless passed only 4--5 events because
it required eligible books for all 11 siblings in one batch, then selected latest *anchored* rows
per bucket and mixed calculation cycles. The dominant `event_probability_time_mismatch` and
`event_probability_vector_incomplete` skips therefore described overcoupled admission, not a
failed model vector.

`candidate-anchor-v2` now loads the latest row for every event bucket without an anchor filter,
proves one complete same-time/horizon/cohort materialized distribution, and separately validates
the exact complete/eligible `clob-dual-v1` snapshot of the candidate market. It cannot borrow old
sibling rows; publication clocks are per event; condition and YES/NO token identity plus causal
batch timing are fail-closed. Edge, spread, cost, depth, Kelly, exposure, station-reliability, and
loss gates are unchanged. The strict all-sibling/same-batch assessment is preserved for the
whole-market Brier benchmark and is not relabelled as candidate admission.

The read-only counterfactual at `08:48Z` produced 17 selection-eligible markets in 10 events versus
zero under revision v1 with all downstream thresholds unchanged. Unit tests and a real-PostgreSQL
regression prove incomplete/mixed/tampered vectors remain blocked, a newer vector never borrows an
older fully anchored cohort, and the one admitted signal binds the exact current model and candidate
snapshot while creating no sibling signal. Canonical semantics and release requirements are in
`CANDIDATE_ANCHOR_ADMISSION_PROTOCOL.md`. Production activation must fork only
`selection_policy`; this engineering correction does not prove fills, CLV, skill, profitability,
or LIVE readiness.

Production acceptance completed on image
`sha256:12b1b2ca4874dbe13bd4a89ab0e63f13a41781a8fc7a9014e8c2282aa606b8d3`, plan 10
`c519506a00adf95ae3370e91f27fae9eab2276435119cf787b186b0049ea71cc`, epoch 8, boundary
`2026-07-17T09:36:34.871545Z`. The reviewed plan forked only `selection_policy`; the other five
lanes continued. Audited commands 9 and 10 bracketed the cutover and resumed only PAPER. The first
natural cycle produced resting maker orders, and by `09:51:03.718696Z` the epoch held 24 immutable
intents: 23 resting and one naturally filled/eligible/enrolled order. A full checked sample had
24/24 exact model, candidate CLOB, station, and selection-lane provenance with zero LIVE rows; an
11-event/121-bucket deep audit found zero vector, partition, foreign-anchor, or sibling-borrowing
violation. Audit 643 remained non-critical/non-blocking and continuity advanced to
`maker_orders_resting`.

The next accuracy item is now explicit: recover missing historical observations before finalizing
station-day extrema after downtime. The 2026-07-17 startup had only partial 2026-07-16 coverage,
and `daily-actuals` correctly failed closed for all 20 stations. Implement recovery from an
authorized immutable observation source, preserve point-in-time provenance and completeness
checks, and never convert a partial day into calibration truth. This recovery is independent of
candidate-anchor admission and must receive its own manifest/continuity treatment if it changes
predictive or official-truth evidence.

##### Observation recovery 0043 — implementation decision

The production diagnosis found a concrete cause rather than a weak completeness threshold. No
`daily_actuals` row exists for any of the 20 active stations on local date 2026-07-16. Two worker
outages of roughly 15.4 and 17.5 hours exceeded the six-hour live METAR lookback; resulting maximum
station-day gaps were 10–13 hours. The existing ≥20 sample, ≤90-minute boundary, and ≤2-hour gap
gate correctly rejected all partial days and will not be relaxed.

Revision 0043 uses a bounded IEM ASOS archive recovery path. Exact response bytes and normalized
points live in new append-only evidence tables and never enter live `observations`. A recovered
HIGH/LOW pair is derived atomically from one complete artifact, carries its true database
`first_seen_at`, and is quarantined rather than overwritten when the remote archive changes. EMOS,
weighted-ensemble, and recompute truth reads capture a database cutoff and exclude later-arriving
actuals. The full contract, source/retention review, continuity action, and acceptance gates are in
`OBSERVATION_RECOVERY_PROTOCOL.md`.

Hardening extends the same temporal-truth rule to all prospective ordinary `DailyActual` inserts and
previous-run `HistoricalForecast` rows: PostgreSQL assigns first-seen time, historical forecasts are
immutable insert-or-compare evidence, and caller-supplied/backdated availability cannot enter a fit.
The exact IEM endpoint is hash-bound, response size is enforced while streaming, and one global HTTP
budget includes retries. Reconciliation now preserves recovered-label provenance for station
reliability instead of erasing it.

The reviewed candidate must therefore fork `predictive_validation`, `selection_policy`,
`station_lifecycle`, and `operational_legacy`; the operational fork is required because its manifest
hashes the changed reconciliation record/value-hash semantics. It must not increment or relabel
official-resolution truth. `execution_evidence` and `official_resolution_truth` may continue only
when the generated manifest proves exact identity. At the time of this implementation decision,
revision 0043 remained a release candidate. Its later observed production acceptance is recorded
below; PAPER remained the only permitted trading mode throughout.

##### Observation recovery 0043 — production acceptance, 2026-07-17

Revision `20260717_0043` reached one production head and opened its prospective recovery boundary
at `2026-07-17T12:12:22.739436Z`. Audited command 11 had stopped PAPER at
`2026-07-17T12:11:24.463534Z`; recovery collection did not begin before continuity activation.

Image `sha256:bd5545a5db0d965bc18871b934b70f09a725ca565a6574bad9e68b46bb3b9b78` was accepted through
reviewed plan 13
`479f0438ed4a0932df649155b8be99b8dfdd2cce1fae4dec115af717d1e6e19a` as epoch 9, parent epoch 8,
opened at `2026-07-17T12:43:49.447950Z`. The plan forked `predictive_validation`,
`selection_policy`, `station_lifecycle`, and `operational_legacy`; it continued
`execution_evidence` and `official_resolution_truth`. Release-, lane-, and activation-manifest
hashes were `ae61538a05eb3056f874cb2dbe63ca92305ec28fe60ab19511bdc1f52ea8c106`,
`c6c55b7364d2cd6b7ade0eff17807998e50741a4fd0280f5d702ec608d1344ac`, and
`c75ad808b3d6655cf944d43c0be1c20366e208d9389c6e63b318ea892d099290`.

The migration was unintentionally started by a compose dependency before the planned dump. The
retained archive is therefore truthfully classified as post-migration/pre-activation:
`stratus_post_0043_pre_activation_20260717T121343Z.dump`, 1,132,357,356 bytes, SHA-256
`1FB73D945E0825763765176E40B09A93EC5ACE3F1CF74C752C52FDCA1A6A4151`. Its catalog and full
isolated restore passed, and zero-backfill was independently proven; this record does not relabel
the archive as pre-migration.

After activation, 21/21 artifacts were accepted: 20 stations for local date `2026-07-16` and NZWN
for `2026-07-17`. They contain 878 immutable normalized points and exactly 42 recovered
`daily_actuals` (atomic HIGH/LOW pairs). Audits 658 and 659 were `warning`, with 24 and 14 findings
respectively and zero critical/blocking findings. There was no separate PAPER resume command and no
LIVE command/order/position or authority. This closes the recovery engineering milestone only; it
does not prove predictive skill, CLV, profitability, official-source equivalence, or LIVE readiness.

##### Resolution-contract integrity and bounded audit — revision 0044

Revision `20260719_0044` removes daily false continuity forks without weakening event-specific
settlement evidence. Every database row retains its exact `rules_hash` and raw rules. Continuity
groups rows only through a validated semantic profile whose rules template replaces exactly one
`D Mon 'YY` resolution date while preserving every other byte; the parsed rules date must equal the
market date. HTTPS locators are canonicalized fail-closed, and every stored field is reparsed and
compared before manifest admission. Repository writes are insert-or-compare, while PostgreSQL
rejects content UPDATE/DELETE and permits only one irreversible `retired_at` transition.

The same revision makes runtime database access least-privileged, hashes compose deployment state
into `operational_legacy`, treats `/healthz` as container liveness, and gives PostgreSQL a restart
policy. The accuracy audit no longer materializes the full trajectory ledger: the failed baseline
exceeded 4.66 GiB RSS, whereas the bounded production benchmark completed in 201.641 seconds at
approximately 306–410 MiB RSS and produced 33,104 skill reports plus 123 drift events.

Preflight observed 263 active contracts collapsing to 27 semantic profiles with zero current
rules-date or locator violations. The database-bound 0044 plan forked all six continuity lanes. The
four direct semantic forks were joined by `selection_policy` and `execution_evidence` because the
release expanded every lane identity to bind `pyproject.toml` and `uv.lock`. No lane action was
forced from documentation; reviewed plan 16 independently produced this complete six-fork set and
activated epoch 10 under the kill switch. Operational acceptance then exposed a least-privilege
capacity-read defect; the worker was stopped before execution resumed. Corrected plan 18 opened
successor epoch 11 with only `operational_legacy` forked and the five unchanged evidence lanes
continued. The immutable observed cutover record is maintained in
`docs/EVIDENCE_CONTINUITY_PROTOCOL.md` section 20.7.

##### Observation-recovery missed-hour correction — epoch 15

The recovery admission bug was narrower than the label-quality contract: automatic recovery ran
only in one station-local hour, so downtime during that hour could strand an otherwise eligible
day. Epoch 15 changes the automatic condition to allow bounded processing at or after the local
threshold. It does not relax the minimum observation count, boundary coverage, maximum gap,
lookback, request cap, cooldown, immutable raw-artifact, or atomic HIGH/LOW requirements.

The change is covered by before/at/after-threshold unit cases and clean-PostgreSQL regressions for
missed-hour recovery, idempotency, request-cap drainage, and cooldown expiry. Ruff, strict mypy,
847 non-PostgreSQL tests (3 skipped), and 8 focused recovery integration tests passed. No migration
was introduced.

Reviewed plan 22 activated exact image
`sha256:c8813973ad897e9b7e0cc12de77eca4a58a636e901ae8e391f53843ce6b38a2c`
as epoch 15. The label-consuming `predictive_validation`, `selection_policy`,
`station_lifecycle`, and `operational_legacy` lanes forked; `execution_evidence` and
`official_resolution_truth` continued exact parent identity. The catch-up admitted 38 additional
station-days after the new boundary, taking accepted artifacts 42 -> 80 and recovered actual rows
84 -> 160. Final integrity checks found zero broken artifact pairs and zero missing active
station-days in the three-day recovery window.

This closes the automatic label-availability defect. It deliberately does **not** close the
accuracy workstream. The next evidence-bound steps are:

1. keep epoch-15 PAPER collection prospective until the preregistered independent-block and
   station denominators mature; do not manufacture samples by replaying or repeatedly scoring the
   same market;
2. keep weighted and censored challengers shadow-only until their preregistered prospective support
   and confidence gates pass; the current weighted challenger has a promising retrospective
   settlement delta but zero prospective promotion blocks;
3. acquire and validate legally retainable exact official-resolution sources for each supported
   contract family; the current ledger still has zero licensed exact settlement values, so METAR
   recovery must not be relabelled as official truth;
4. reassess station/model/horizon slices only at fixed review boundaries, preserving negative
   results and structural breaks rather than tuning filters after every score movement;
5. keep LIVE prohibited until every quantitative, evidence-continuity, legal, operational, and
   executor gate passes independently.

At the epoch-15 acceptance snapshot, 290 contracts existed, 275 forecast evaluations produced 30
independent samples, and no station was promotion-qualified. Model Brier was 0.07275 versus market
Brier 0.06641. The score did not change merely because missing future labels became recoverable;
that is expected and prevents a maintenance fix from being misreported as predictive improvement.
