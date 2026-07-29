# STRATUS evidence continuity protocol

Status: the 0040 continuity foundation, revision 0041 emergency fill projection, revision
`20260717_0042` station-reliability release, and revision `20260717_0043` observation recovery are
production-accepted. Revision `20260719_0044` remains the production schema head. The current
release is epoch 18 through reviewed plan 28, exact image
`sha256:de7d033f14ee5527473525ac8b31c5e8235edcf2ce7d667196fe0efa2d41942a`.
It continues all six exact boundaries inherited from epoch 17. Epoch 17 intentionally forked
`operational_legacy` for a new collection/liveness window and `selection_policy` for the deployed
`SIGNAL_MAX_EDGE=0.20` admission rule; it admitted no worker execution or formal evidence before
being superseded. Polymarket is authorized only in PAPER mode, Binance remains
`backtest + kill`, and LIVE remains prohibited. The 0041 natural-fill
observation remains historically inconclusive and cannot be collected from its stranded orders;
the completed 0042 quarantine does not rewrite that result. Sections 1–17 retain the planning
baseline and implementation contract, Sections 18–19 are dated acceptance/addendum records, and
Section 20 records revision 0042 and subsequent continuity releases.

Continuity foundation migration: `20260715_0040`. Current production schema head:
`20260719_0044`.

## 1. Purpose and authority

Revision 0040 prevents an application release from silently doing either of two unsafe things:

1. mixing evidence produced by materially different probability, selection, execution, station,
   or truth definitions; or
2. resetting a genuinely unchanged evidence denominator merely because unrelated application
   code was rebuilt.

It introduces immutable **release epochs** and explicit **continuity lanes**. A release may
continue a lane only when a reviewed, hash-bound plan proves that the lane's material identity is
unchanged. A material or unclassified change must fork that lane at a new PostgreSQL-clock
zero-backfill boundary. Old evidence remains queryable under its original identity; it is never
relabelled as current.

The protocol is an identity and continuity control plane. It does not claim predictive skill,
improve a model, create more observations, or make incompatible samples comparable. It adds one
fail-closed precondition to new execution: the running release and every execution-relevant lane
must exactly match an activated epoch before a PAPER or LIVE order can be claimed.

Existing settlement, reconciliation, evidence sealing, position monitoring, cancellation, and
risk-reduction work must remain available while new execution is blocked. Continuity is not a
reason to abandon or rewrite already-open state.

## 2. Planning baseline and denominator warning

The following values are the supplied pre-activation planning baseline for 0040. They are retained
unchanged as historical planning input and must not be read as the final release-acceptance
snapshot; observed acceptance values are recorded in Section 18:

| Surface | Planning value | Unit and authority |
|---|---:|---|
| current confirmatory lane | `0` | current-release formal outcome/block denominator |
| earlier prospective evidence | `5 eligible` | eligible enrollments in an older lane; not current outcomes |
| execution evidence | `2 attempts`, both no-fill | execution-attempt denominator; zero filled-cost observations |
| legacy open inventory | `51` | operationally open legacy rows; not a prospective sample |
| station lifecycle | `5 paper / 15 shadow` | station-stage inventory; not trades or independent outcomes |
| official exact truth | `0/155` | contracts with 0039 exact-source evidence / active contracts |

These denominators are intentionally incompatible. In particular:

- `0 current + 5 old eligible` is not `5 current`;
- two no-fill execution attempts are not two validation outcomes, two fills, or two markouts;
- 51 legacy open rows are exposure/operations, not 51 prospective observations;
- 5 paper and 15 shadow stations are deployment coverage, not 20 independent samples; and
- `0/155` official-source coverage is a contract-coverage ratio, not Brier, CLV, ROI, execution,
  station-day, or trade evidence.

No API, health summary, ETA, Cockpit progress bar, Telegram message, or live-gate adapter may add,
average, or otherwise collapse these values into one numerator or denominator. A response must
name the lane, epoch, unit, eligibility rule, and denominator alongside every aggregate.

The production procedure required the implementation to query the authoritative tables again and
record a dated activation snapshot. This section is intentionally not edited to masquerade later
values as the original planning input.

## 3. Zero-backfill boundary

Migration 0040 must create its runtime boundary using PostgreSQL `clock_timestamp()` and must not
copy, replay, enrol, or synthesize any existing order, outcome, execution attempt, position,
station, forecast, probability, CLOB snapshot, Gamma event, reconciliation row, or official-source
row into a new 0040 epoch.

The existing 0035 prospective ledger, 0037 forecast provenance, 0038 execution-cost ledger, and
0039 official-resolution evidence remain the authorities for their own rows. Revision 0040 stores
release/lane identity and cutover references; it does not duplicate their measurements.

An activation may point to an existing immutable lane only through an exact compatibility
identity. That reference is continuity, not backfill. If compatibility cannot be proven, the lane
forks and its new formal denominator begins at zero. A row predating that fork boundary can never
be promoted into it later.

Migration success must therefore initially mean:

- one immutable 0040 runtime boundary;
- zero release plans, zero activated epochs, and zero lane activations;
- no change to any pre-0040 evidence count; and
- new execution fail-closed until an exact plan is activated.

## 4. Immutable release epochs

A release epoch is the database-attested identity of one executable STRATUS release. A process
restart of the same release is not a new epoch. Any material build, configuration, schema, or lane
identity change requires a new plan and activation.

The implementation is expected to freeze at least:

- application image digest and build/source identity;
- Python lock/dependency identity;
- Alembic head;
- probability pipeline semantic version, effective cohort, and source/config fingerprint;
- active selection-policy manifest and hashes;
- execution-cost definition and paper/live execution semantics;
- station-registry/stage manifest relevant to execution;
- official-resolution evidence definition identity;
- parent epoch id/hash and per-lane continuity action;
- an allowlisted configuration manifest containing only evidence-relevant, non-secret values; and
- the complete canonical release-plan hash.

Secrets, credentials, raw licensed bytes, private keys, and bearer tokens must never enter a
release manifest, record hash, log, API response, or Cockpit field.

Proposed 0040 relations are:

- `evidence_continuity_runtime`: immutable singleton boundary, definition version/hash, and
  protocol identities;
- `evidence_release_plans`: append-only canonical plans and reviewer-visible plan hashes;
- `evidence_release_epochs`: append-only activations, database-clock cutover, release identity,
  parent identity, and activation record hash;
- `evidence_continuity_lanes`: one immutable, complete set of lane decisions per epoch; and
- `evidence_release_halts`: append-only emergency/runtime mismatch events, if a separate halt
  relation is used instead of deriving the halt from integrity state.

Final names and the definition hash must be frozen by the implementation and migration together.
This in-progress specification does not invent a final SHA-256 value.

Runtime, plan, epoch, lane, and halt evidence must reject `UPDATE` and `DELETE`. Foreign keys use
`ON DELETE RESTRICT`; canonical hashes are recomputed in PostgreSQL; timestamps that define the
boundary or activation use the database clock; uniqueness prevents duplicate activation of one
plan. Downgrade must be refused after the first plan, epoch, lane, or halt row exists.

## 5. Two-step plan and activate workflow

Planning and activation are separate authenticated operations.

### 5.1 Plan

The plan operation receives or derives the exact candidate release identity and a decision for
every required continuity lane. It performs no activation and cannot enable execution. It returns:

- canonical plan payload;
- release manifest hash;
- per-lane identity, parent, action, reason, and compatibility hash;
- all detected material and unclassified changes;
- proposed zero-backfill boundaries for forked lanes; and
- one plan SHA-256 for external review.

Allowed lane actions are:

- `continue`: exact material identity matches the parent lane;
- `fork`: a material change opens a new zero-denominator lane;
- `freeze`: the release may run non-execution collection/diagnostics but cannot enrol new formal
  evidence or execute under that lane; and
- `not_applicable`: allowed only for a lane the release demonstrably does not consume.

An unknown file/config/dependency change that could affect a lane is not compatible by default.
It must cause `fork` or `freeze`; a reviewer comment cannot override a mismatched computed hash.

Planning must be idempotent for identical canonical content. Reading the plan API, opening
Cockpit, starting a process, running a migration, or restarting Docker cannot activate a plan.

### 5.2 Activate

Activation requires the exact previously returned plan hash plus an authenticated reviewer,
review time, and non-empty rationale. It must atomically verify that:

1. the currently running candidate reports the exact image/build/schema/config identity in the
   plan;
2. the selected parent epoch is still the active parent and has not been superseded or halted;
3. all required lanes are present exactly once and their computed identities still match;
4. cutover source counts/cursors have not crossed an unhandled race;
5. no unresolved integrity issue exists; and
6. the approved plan hash is byte-for-byte the current canonical plan hash.

The database then appends one epoch and its complete lane set at one database-clock cutover. A
partial lane activation must be rejected with a deferred completeness constraint. Concurrent
activation attempts for the same plan or parent produce one epoch or fail; they never create two
current epochs.

Activation never changes an old epoch or moves an old row across a boundary. Reusing an old image
after rollback requires a new reviewed activation event; the database clock is not rewound.

## 6. Continuity lanes

Each lane has its own semantic identity and denominator. Compatibility is decided independently;
one continued lane does not imply that another may continue.

### 6.1 Predictive validation lane

Identity includes the effective probability-pipeline cohort/fingerprint, final probability
semantics, source-provenance requirement, event-vector/materialization rules, benchmark protocol,
independent block definition, and validation arithmetic.

Changing model inputs, ensemble/calibration/application weights, probability transformations,
benchmark eligibility, target construction, event geometry, block unit, or outcome arithmetic
requires a fork. A build-only or UI-only release may continue only when the complete computed
identity remains exact.

The earlier `5 eligible` planning rows remain in their original lane. They are not added to a new
current denominator merely because both lanes are labelled PAPER.

### 6.2 Selection-policy lane

Identity includes every evaluated policy hash, edge/cost threshold, side/horizon/station scope,
decision timestamp semantics, candidate opportunity definition, and retained-trade/block
arithmetic. Adding, deleting, reordering, or modifying a policy is a fork for that policy family,
not a historical rescore of old opportunities.

### 6.3 Execution-evidence lane

Identity includes revision-0038 intent/fill/seal definitions, purchased-token coordinates, paper
maker/taker simulation, book eligibility, fee treatment, fill clocks, mark horizons/grace, and
cost arithmetic.

The supplied two terminal no-fill attempts remain valid attempt evidence in their original 0038
lane. They contribute zero filled-cost and zero post-fill-markout observations. A continued 0038
lane may retain its attempt denominator only if all material execution semantics are unchanged;
otherwise the new execution lane starts at zero.

### 6.4 Operational legacy lane

Legacy open orders/positions remain visible and must continue through safe monitoring,
cancellation, settlement, and reconciliation. Their supplied count of 51 is operational
inventory, not confirmatory evidence. Closing one later does not retroactively make its pre-epoch
decision or fill eligible for a new lane.

### 6.5 Station lifecycle lane

The supplied `5 paper / 15 shadow` station counts describe stage inventory. The lane binds station
identity, source/station mappings, stage, and any execution eligibility derived from stage. A
station transition is prospective and cannot relabel earlier shadow observations or trades.

Station counts may be shown as coverage but cannot be used as accuracy, trade, independent-day,
or official-source denominators.

### 6.6 Official-resolution truth lane

Identity references revision 0039's definition, contract/rules/source identity, and current
contract-scoped licence/equivalence policy. It does not collect a source or change 0039 evidence.

The supplied planning state is `0/155` official exact coverage. Missing external authorization is
an expected fail-closed state, not 155 overdue collections and not a zero-valued accuracy metric.
Exact-source coverage remains separate from METAR consistency, Gamma outcomes, predictive
validation, and station coverage.

## 7. Runtime fail-closed execution gate

Before a PAPER or LIVE order claim, the execution transaction must obtain an authoritative 0040
decision proving:

- one active, non-halted release epoch exists;
- the running image/build/schema/config identity equals that epoch;
- the signal/model probability belongs to the active predictive lane;
- the active selection and execution identities equal those used by the order intent;
- the station and market scope are eligible under the active station lane; and
- every execution-required lane is `continue` or an activated post-boundary `fork`, never
  `freeze`, missing, incompatible, or unclassified.

The decision and durable order intent must be committed before any new paper execution book
request or live submission. A process cannot cache an activation indefinitely; every claim uses
the database-authoritative epoch or a transactionally safe bounded token tied to it.

Stable blocking reasons must distinguish at least:

- `evidence_continuity_unactivated`;
- `evidence_release_identity_mismatch`;
- `evidence_lane_identity_mismatch`;
- `evidence_lane_frozen`;
- `evidence_epoch_halted`; and
- `evidence_continuity_integrity_error`.

The continuity gate is additive. It does not alter Kelly sizing, exposure caps, station limits,
legal/tax checks, kill-switch rules, or any existing live-gate threshold. A matching epoch is
necessary but never sufficient for LIVE. Conversely, lack of official-source authorization does
not newly stop PAPER unless an existing rule already requires it; 0040 only prevents identity
ambiguity.

When the gate blocks new execution, collectors, shadow forecasts, read APIs, Cockpit, health,
settlement, reconciliation, cancellation, markout completion, and risk-reducing work may continue
provided they cannot create a new order. The gate must not strand the supplied legacy-open
inventory.

## 8. Due-aware liveness and ETA semantics

Evidence grows only when a natural eligible event becomes due. Wall-clock time since activation
is therefore not a liveness denominator.

Each lane summary must distinguish:

- `waiting_for_due`: no eligible event has reached its natural due/finality window;
- `due_pending`: at least one event is due but remains within its protocol grace period;
- `collecting_on_schedule`: due events are being enrolled/sealed within grace;
- `overdue`: a due event crossed its grace deadline without the required row;
- `stalled`: repeated due events are overdue or the lane has made no progress despite eligible
  due opportunities;
- `complete`: the lane-specific preregistered target is satisfied; and
- `not_applicable` or `blocked_external`: no valid due schedule exists, for example official truth
  without an authorized product.

Due time must come from the lane's frozen source semantics. Market close, source publication,
markout target, settlement recognition, and local weather-day finality are different clocks and
must not be substituted for one another.

An ETA to a sample threshold may be shown only after at least **seven complete collection days**.
A complete collection day is a closed UTC `[00:00, 24:00)` interval after epoch activation for
which every obligation due by the interval end plus its protocol grace has been classified. The
partial activation day, current day, maintenance-excluded day, or day with unresolved scheduler
coverage is not complete.

Before seven complete days, ETA fields are JSON `null` with reason
`insufficient_full_days_7_required`; Cockpit displays `Collecting baseline — ETA unavailable`.
It must not extrapolate from hours, partial days, two attempts, five enrollments, station count,
legacy inventory, or scheduled-but-not-due markets.

After seven complete days, every ETA must expose:

- lane and target denominator;
- complete-day count and observation window;
- due, eligible, excluded, and sealed throughput separately;
- method and uncertainty/range;
- data freshness and maintenance exclusions; and
- the fact that ETA is an operational projection, not a skill or profitability forecast.

If throughput is zero, due coverage is incomplete, identities change, or a lane forks, ETA returns
to unavailable. A new fork starts its own seven-full-day history; it cannot inherit throughput
from an incompatible epoch.

## 9. Operator API

Planned read endpoints:

- `GET /api/v1/evidence-continuity` — current runtime identity, active/planned epoch, execution
  gate result/reasons, lane identities, separate counters, due/liveness state, and ETA metadata;
- `GET /api/v1/evidence-continuity/epochs` — bounded immutable epoch history;
- `GET /api/v1/evidence-continuity/lanes` — bounded lane/cutover history and compatibility
  decisions; and
- optional contract/order drill-down through existing evidence endpoints, preserving their own
  units and identities.

Planned authenticated mutations:

- `POST /api/v1/evidence-continuity/plan-hash` — canonical preview only;
- `POST /api/v1/evidence-continuity/activate` — exact approved plan hash, reviewer, and rationale;
  and
- `POST /api/v1/evidence-continuity/halt` — append-only emergency halt if implemented.

No endpoint may accept an evidence value, overwrite a denominator, mark an incompatible lane as
compatible, activate by source-family wildcard, rewrite a pipeline cohort, or bypass the runtime
gate. Read endpoints never activate a plan.

JSON null is distinct from zero. Every metric includes `epoch_id`, `lane`, `unit`, `eligible_n`,
the relevant denominator, and identity hash. Old/current, PAPER/LIVE, filled/no-fill,
paper/shadow-station, and official/unofficial evidence stay separate by default.

## 10. Cockpit

Cockpit should add an independent **Evidence continuity** surface with:

- current release epoch, image/build identity, activation time, parent, and plan hash;
- a prominent `ACTIVE`, `PLANNED`, `EXECUTION BLOCKED`, `IDENTITY MISMATCH`, or `HALTED` state;
- one row/card per continuity lane with action, current identity, boundary, liveness, numerator,
  denominator, and due status;
- an explicit execution-gate verdict and stable blocking reasons;
- separate presentation of the planning categories: current formal `0`, older `5 eligible`,
  execution `2 attempts / 2 no-fill / 0 fills`, legacy open `51`, stations `5 paper / 15 shadow`,
  and official exact `0/155`; and
- ETA as unavailable until seven complete days, including the missing-history reason.

There must be no combined completion percentage or common progress bar for incompatible lanes.
Historical/reference evidence uses a neutral style; expected external blocks and waiting-for-due
states are not red. Red is reserved for identity/integrity violations, unauthorized activation,
or genuinely overdue obligations. API failure degrades only this surface and never replaces the
last-known core runtime/risk view with fabricated zeros.

If Cockpit exposes activation controls, preview and activate remain two distinct actions. The
activation confirmation must display the exact plan hash, release identity, per-lane actions, all
forked zero boundaries, reviewer, and rationale. A generic `Continue anyway` control is forbidden.

## 11. Health semantics

Planned health issue classes include:

- informational: plan present but not activated; waiting for naturally due evidence; ETA history
  below seven complete days; expected official-source external block;
- warning: candidate plan becoming stale, activation expiry if used, lane due backlog within an
  operational tolerance, or incomplete full-day coverage;
- critical/source-local halt: repeated overdue due events, unresolved activation race, or a
  required lane unexpectedly frozen; and
- blocking for new execution: no active epoch, running-release mismatch, lane identity mismatch,
  incomplete epoch lane set, record/hash/cutover inconsistency, mixed denominator, or attempted
  execution without a committed continuity decision.

Expected zero baselines, no-fill attempts, legacy inventory, shadow stations, no naturally due
event, and `0/155` externally blocked official coverage are not integrity failures.

Health must report the continuity execution gate independently from the existing trading kill
switch and live gate. Revision 0040 must not silently introduce a new profitability/risk threshold
or turn an expected collecting state into a global trading halt. An integrity defect that can
contaminate evidence or permit unidentified execution remains fail-closed.

## 12. Threat model and security properties

The protocol addresses:

- **silent semantic reuse**: changed model/execution code cannot inherit an old lane without an
  exact compatibility hash;
- **release reset abuse**: unchanged semantic lanes need not lose their denominator because of an
  unrelated build;
- **operator substitution**: activation requires the exact reviewed plan hash, not editable UI
  fields;
- **configuration drift**: runtime identity is checked at activation and again before execution;
- **partial activation**: deferred completeness requires every lane exactly once;
- **concurrent cutover**: database locking and uniqueness permit one parent-to-child activation;
- **backfill/replay**: timestamps, boundaries, cited source identities, and append-only triggers
  reject pre-boundary enrolment;
- **denominator laundering**: type/unit/lane/epoch fields prevent old eligible, no-fill, legacy,
  station, and official coverage counts from being summed;
- **false liveness and ETA**: only naturally due obligations and seven complete days permit an
  operational projection;
- **rollback bypass**: an older binary cannot resume execution without a new matching activated
  epoch; and
- **read-side activation**: monitoring, health, and Cockpit have no implicit mutation path.

The protocol does not prove that an upstream forecast, CLOB, Gamma, METAR, or official source is
correct. It does not protect against a PostgreSQL superuser who drops triggers, a compromised host
or build pipeline, or theft of external credentials. Build signing, host security, database audit
anchoring, secret management, and network egress controls remain separate controls.

## 13. Required tests

### 13.1 Unit tests

- canonical plan and release/lane hashes are stable; every material field changes the hash;
- unclassified changes fail to `fork` or `freeze`, never implicit `continue`;
- exact no-change manifests continue a lane across unrelated releases;
- each listed material model, selection, execution, station, and truth change forks its lane;
- incompatible denominator types cannot be combined;
- due/liveness state transitions respect each protocol clock and grace;
- no ETA before seven complete days, including activation/current/maintenance partial days;
- a fork resets only its own full-day and sample history; and
- runtime identity mismatch returns stable fail-closed reasons.

### 13.2 Clean PostgreSQL integration tests

- 0039 -> 0040 is the sole migration path, opens one database-clock runtime, and performs zero
  backfill;
- existing 0035/0037/0038/0039 rows and supplied-category counts are unchanged by migration;
- plan/epoch/lane rows are append-only and hashes are independently recomputed;
- exact plan hash, current runtime identity, parent identity, lane completeness, and cutover are
  enforced by direct SQL as well as repositories;
- concurrent plan retries are idempotent and concurrent activation creates one epoch;
- a changed or stale plan cannot activate;
- missing/frozen/mismatched lanes prevent new PAPER and LIVE claims in the same transaction;
- settlement, cancellation, reconciliation, markout completion, and safe handling of legacy-open
  rows remain available while execution is blocked;
- old eligible, current outcomes, no-fill attempts, station inventory, legacy open, and official
  coverage remain separate in repository and API aggregates;
- due-aware states do not mark no-due/external-blocked lanes overdue;
- downgrade is allowed only while no 0040 plan/epoch/lane/halt evidence exists and is refused
  afterwards; and
- API responses preserve nulls, identities, units, denominators, and pagination.

### 13.3 Runtime and UI verification

- candidate startup reports its exact image/build/schema/config identity;
- before activation, collectors/reads/settlement remain available but new execution is blocked;
- plan preview has no side effect; exact activation enables only matching execution;
- process restart under the same activated release does not create a new epoch;
- config/image mismatch after activation blocks new claims;
- API totals reconcile to direct SQL without cross-lane sums;
- health distinguishes waiting, overdue, external block, and integrity failure;
- WinUI x64 build passes and the packaged Cockpit renders each lane and the unavailable ETA
  honestly; and
- no synthetic trade, fill, outcome, official actual, or station promotion is created for
  acceptance.

## 14. Deployment and acceptance procedure

The checklist below was the controlling release procedure. Its observed completion evidence is in
Section 18; the checklist itself remains the implementation contract rather than a retrospective
rewrite.

Before migration:

1. Freeze the final definition/version/hash and exact lane manifests.
2. Pass Ruff, strict mypy, the full non-PostgreSQL suite, complete clean-PostgreSQL chain, Compose
   validation, and one-head Alembic validation.
3. Create a pre-0040 production backup, record its size/SHA-256, verify its TOC, and complete an
   isolated restore drill.
4. Record the exact pre-release image digest and prepare a **0040-aware** rollback image.
5. Prove on a restored production snapshot that migration creates only the runtime boundary and
   changes none of the pre-existing evidence counts.

Production rollout:

1. Stop or gate new execution while retaining settlement/reconciliation/risk-reducing work.
2. Deploy migration, API, and worker from one immutable candidate image.
3. Upgrade to 0040 and verify zero plans/epochs/lanes and a closed continuity execution gate.
4. Re-query the planning-baseline categories from authoritative tables; record them as a dated
   pre-activation snapshot without combining them.
5. Generate the canonical plan, externally review the full lane decision set, and record the plan
   hash.
6. Activate that exact hash; verify one epoch, a complete lane set, matching runtime identity, and
   the intended continued/forked boundaries.
7. Run API, health, scheduler, log, direct-SQL, and Cockpit smoke checks.
8. Record real test totals, image/backup hashes, schema head, counts, health, gate state, and UI
   evidence in a dated acceptance section. Do not copy planning values as if observed.

Deployment does not need a naturally occurring trade. If none is due, the honest new denominator
remains zero. Tests and operators must never force a production trade to make the release look
active.

## 15. Rollback

Schema downgrade may be tested only while all 0040 plan/epoch/lane/halt tables are empty. Once a
plan or activation exists, downgrade must refuse because removing the boundary would allow later
software to misidentify evidence.

Application rollback after activation must retain the 0040 schema and gate. The pre-0040 0039
worker does not know the continuity gate and therefore must not be restarted as an executing
worker. A safe rollback is either:

- a prebuilt 0040-aware rollback image plus a newly reviewed rollback release epoch; or
- services held with new execution disabled while a compatible image is produced.

Rollback never deletes epochs, rewinds activation time, restores an old active pointer, relabels
new evidence as old, or merges forked denominators. Any evidence created before the rollback stays
under the epoch that admitted it.

## 16. Explicit exclusions

Revision 0040 does **not**:

- implement or activate an official-resolution HTTP collector, credentials, canary, retry/SLA
  scheduler, or Wunderground/HKO/national-source workflow;
- change revision 0039 licence, artifact, exact-actual, or settlement-projection semantics;
- add, remove, retrain, promote, weight, or otherwise change a forecast/model/calibrator/expert;
- change probabilities, source forecasts, event geometry, station mappings, or temperature
  targets;
- change signal filters, minimum edge, cost buffer, side/horizon rules, maker simulation, or
  market selection;
- change Kelly sizing, bankroll, exposure, loss limits, kill-switch authority, legal/tax checks,
  live confirmation, or any live-gate threshold;
- add stations or promote the supplied 15 shadow stations to paper;
- settle or close the supplied 51 legacy-open rows, create fills/outcomes, or backfill official
  truth;
- combine incompatible denominators or claim an ETA before seven complete days; or
- claim accuracy, edge, profitability, source correctness, or live readiness.

Any such change requires its own reviewed protocol and, when material to evidence, a forked
continuity lane with a new prospective boundary.

## 17. Completion definition

Revision 0040 is complete only when schema, repositories, runtime gate, API, health, Cockpit,
tests, backup/restore, rollback-aware deployment, zero-backfill proof, two-step activation, and
post-activation identity smoke all pass together and a dated acceptance record contains only
observed facts.

The completion evidence in Section 18 satisfies this engineering definition. The supplied `0`,
`5 eligible`, `2 no-fill attempts`, `51 legacy open`, `5 paper / 15 shadow`, and `0/155 official`
values remain separate planning facts, not a combined progress score and not evidence that the
strategy is ready for LIVE.

## 18. Production acceptance — 2026-07-15

### 18.1 Backup, restore, and zero-backfill proof

The pre-migration backup is
`backups/stratus_pre_0040_20260715T124901Z.dump` (690,770,617 bytes; SHA-256
`435cba086e45afe26d97d257f7803730918d3ac666f85e483a0aab032c746145`). Its `pg_restore`
listing was valid, and a complete isolated restore matched production exactly:

- execution evidence: 2 intents, 0 fill legs, 2 obligations, 2 unfilled-maker outcomes, and 2
  seals;
- positions: 126 total, 51 open, and all 126 PAPER;
- stations: 22 total — 5 active PAPER, 15 active SHADOW, and 2 inactive SHADOW; and
- prospective/source evidence: 6 enrollments, 0 outcomes, 0 exact actuals, 155 contracts, and 0
  official-source responses.

On that restored production snapshot, the complete `0039 -> 0040 -> 0039 -> 0040` drill preserved
every count. Before activation, migration 0040 opened zero release plans, zero epochs, and zero
lanes, proving the required zero-backfill boundary. The temporary restore and test databases were
cleaned after verification; the backup is retained.

### 18.2 Immutable release and activation record

Production has one Alembic head, `20260715_0040`. API, worker, and migration services run the same
final image digest
`sha256:9bc680bd35943738e5c30df065cc5404ac5eb7e33d4dc55b5054b87dbf0d101f`. Two rollback
artifacts remain identified: tag `stratus:rollback-0040-aware` points to the 0040-aware
candidate-1 image at
`sha256:6c4f87f3cb97a33c7dd2e7c5622046b4c8a12c9772e550fb8fb01c70e138b8f6`, and tag
`stratus:rollback-pre-0040` points to the pre-0040 image at
`sha256:e4edd3c4e35228d2f8bea93d058c963bc6e92eb2b35986c6694b862fd79adec8`.

The accepted second plan has:

- plan hash `ad0bde63223755c9dc02ca05ad48c4a1b73b3ddc3a3cc62d3d652f40d4b66e32`;
- release-manifest hash `f59d4eef3249faa5cfe54eaceac5a23c1fdbdb7704e3947d3a64bb609c1115af`;
- lane-manifest hash `7093185cfef7f7e7fdde5e7a49293d2702bbdf3e1210ea16f6a0f6e7686f8e3c`.

Epoch 2 opened at `2026-07-15 13:55:49.043719 UTC` with epoch 1 as parent and immutable record hash
`e88ef25d8e36fac3219cba812b5b1f5cd9fb2c867338f58508d837670bf13abf`. The
`predictive_validation` lane forked at the epoch-2 boundary; `selection_policy`,
`execution_evidence`, `operational_legacy`, `station_lifecycle`, and
`official_resolution_truth` continued their parent boundaries. The final database contains 2
plans, 2 epochs, and 12 lane rows. Both pre-0040 legacy intents remain explicitly all-null for the
seven 0040 attestations, and epoch 1 admitted no intent.

### 18.3 Candidate-1 defects and fail-closed correction

Candidate 1 exposed two production acceptance defects without admitting corrupt evidence. First,
the Cockpit read path used a full `model_probs` `DISTINCT ON` scan taking 20.600831 seconds and
also requested the authoritative advisory release lock. Second, the first natural intent was
rejected because Python's scientific-notation float rendering did not match PostgreSQL JSONB's
canonical decimal rendering. The database trigger rejected that model-source hash, the kill
switch engaged, and no invalid intent or order committed.

The final image contains three corresponding controls:

1. active-market `LATERAL` latest-row lookups use `uq_model_probs_market_time`; the same read-only
   production query fell from 20.600831 seconds to 0.390154 seconds, about 52.8 times faster;
2. `GET /api/v1/evidence-continuity` is non-authoritative and uses a per-process 30-second
   single-flight cache that is invalidated immediately after activation; and
3. recursive canonical JSON emits every finite float as an unquoted plain decimal compatible
   with PostgreSQL, preserves the other typed-value semantics, and rejects non-finite values.

For the exact production model row `id=852182`, Python and PostgreSQL now independently produce
the same source hash:
`747ae7fd5d4c22c4f42b6123fb018e37eae4835f88053773bf65616996c55599`.

### 18.4 Verification and runtime smoke

The final candidate passed Ruff, strict mypy over 162 source files, 637 non-PostgreSQL tests, and
216 fresh-PostgreSQL integration tests in 422.06 seconds. Docker Compose configuration is valid,
Alembic has one head, and the WinUI x64 build completed with zero warnings and zero errors.

The final post-natural-cycle health audit (`id=540`, `2026-07-15 14:08:44.209119 UTC`) was a
non-blocking `warning` with 15 informational/warning issues and zero blocking or critical issues.
Daily loss-limit state was `+$70.6620` on `$1,056.6545`; weekly was
`+$5.6307` on `$1,121.6858`; neither limit was breached. Auditable resume command `id=4`
succeeded. Runtime is PAPER with the kill switch off, and `readyz` returns HTTP 200.

Natural, unsynthesized acceptance produced 20 genuine epoch-2 PAPER intents. All 20/20 carry all
seven release/lane foreign-key attestations, their source hashes were accepted by the database
trigger, and execution completed successfully. They created 20 resting PAPER maker orders; no
synthetic fill was introduced. The initial observation window recorded 51/51 successful job runs
and no API or worker `ERROR`, `CRITICAL`, traceback, or source-hash failure line.

The continuity endpoint also passed its operational load check: 10 concurrent cold requests were
coalesced and all returned HTTP 200 in 1.278 seconds with one `generated_at`; a later cold request
took 0.525 seconds and a warm request 0.005 seconds. PostgreSQL showed zero advisory locks and zero
lock waiters while Cockpit auto-refreshed. The x64 **STRATUS Operator Console** is running and
responding through the package-aware `dotnet run` path, with evidence-continuity calls returning
HTTP 200.

### 18.5 Honest evidence state and scope of acceptance

The accepted continuity state is `collecting`, with release and lane identities matching. Its
liveness is `waiting_for_due`; the current operational bottleneck is `maker_orders_resting`.
There are zero complete collection days, so ETA is correctly absent with reason
`insufficient_full_days_7_required`. Current station coverage is 5 active PAPER and 15 active
SHADOW stations. Prospective evidence remains 6 enrollments, 0 outcomes, and 0 exact official
actuals.

This acceptance proves release continuity, database integrity, fail-closed execution, and stable
operations only. It does **not** prove forecast accuracy, predictive edge, profit, or LIVE
readiness. LIVE remains blocked until the empirical and external-source gates pass.

## 19. Revision 0041 emergency continuity addendum — release accepted, fill observation inconclusive

Revision `20260715_0041` corrects a material execution-fill projection defect without rewriting
revision-0040 evidence. `Order.price` and `ExecutionOrderIntent.requested_limit_price` remain the
immutable requested decision/limit price; actual execution VWAP is derived exactly from immutable
atomic legs as `sum(shares * price) / sum(shares)`. Independently rounded stored notionals are cash
accounting values and are never divided by shares to reconstruct VWAP.

The implementation extends continuity enforcement as follows:

- the current formal validation snapshot admits only orders attested to the active
  `predictive_validation` and `execution_evidence` identity hashes and preserved boundaries;
- live-gate, performance/edge, Brier/calibration, CLV, root-cause actions, and Brier/CLV drill-down
  formal fields all consume that same snapshot, with no legacy-position fallback;
- a compatible `continue` retains the sample, while a predictive or execution `fork` resets its
  current denominator without deleting historical audit rows;
- selection reporting is the intersection of the snapshot's exact formal order ids and intents
  attested to the active `selection_policy` identity and boundary; selection `continue` retains
  compatible decisions and selection `fork` resets only the active selection projection;
- legacy fee reconstruction requires a no-intent PAPER fill whose `filled_at` precedes the frozen
  execution-cost boundary; ambiguous post-boundary no-intent rows fail closed; and
- Telegram distinguishes a proven zero-fill warning from a critical claimed-fill-without-atomic-
  evidence condition and never labels a cancelled non-zero fill as `no_fill`.

Revision 0041 therefore requires reviewed `fork` actions for `predictive_validation`,
`execution_evidence`, and `selection_policy`. `operational_legacy`, `station_lifecycle`, and
`official_resolution_truth` may continue only if their computed identities remain exactly
unchanged. This semantic requirement is not itself an activation record.

The revision-0041 release manifest is database-bound: manifest generation reads `alembic_version`,
requires exactly the single head `20260715_0041`, records it, and incorporates the material
predictive/execution migration SQL sources into the corresponding lane identities. Manifest
generation fails closed on a missing, multiple, stale, or unexpected database head.

The PostgreSQL acceptance contract includes deterministic lock races in both serial orders. A fill
leg that wins the shared intent lock before obligation/enrollment projection is included in the
immutable aggregate; once terminal projection wins that lock, a competing or later fill leg is
rejected. Neither interleaving may produce a partially projected enrollment or mutable seal.

### 19.1 Observed release and continuity acceptance

The retained pre-0041 backup is
`backups/stratus_pre_0041_final_20260715T190832Z.dump` (802,838,676 bytes; SHA-256
`526ac9d4554f252e2a2388596658b6bc41fad674d420aff23ff5c703d010a93d`). The migration drill passed
and production has the single Alembic head `20260715_0041`.

API and worker run the same immutable image,
`sha256:ab2b627d77faef9d7f2114fdbf208d83947163fd81565d0f0197cec682d6b181`. Reviewed plan
`7eb012e66704fb70cf30f20fce0a6a344e369089fe2770616956b6054faea5d5` opened epoch 3 at
`2026-07-15T19:14:03.860671Z`. Its immutable lane actions match the semantic requirements above:

- `predictive_validation`, `execution_evidence`, and `selection_policy` forked;
- `official_resolution_truth`, `station_lifecycle`, and `operational_legacy` continued.

In the initial cutover window, continuity matched the running release and all active lane identities.
The cutover health audit was `warning` with zero critical and zero blocking findings, after which
audited command 5 resumed PAPER. These are dated acceptance facts, not a permanent current-runtime
claim. Verification passed Ruff, strict mypy over 162 source files, 661 non-PostgreSQL tests, 227
fresh-PostgreSQL tests, the migration drill, and WinUI x64 Debug and Release builds.

These observed facts accept revision 0041 for schema, immutable release identity, reviewed lane
continuity, and the initial PAPER cutover. They do not convert old evidence into epoch-3 evidence,
authorize LIVE, or establish a permanent ready state. The revision-0040 acceptance above remains
historical evidence under its own epoch/image and is not reused as 0041 acceptance.

### 19.2 Natural fill-projection observation is historically inconclusive

Through `2026-07-15T19:29:36Z`, epoch 3 had 15 genuine intents/resting maker orders and zero fill
legs. No natural order had crossed, so no production fill existed on which to observe requested
limit versus exact leg-derived VWAP. This is a waiting-for-market state, not a failed control and
not a successful fill-projection claim.

The later kill-switch transition expired the signals behind those resting orders without
terminalizing their PAPER orders. The maker fill projector requires a pending signal, so those
epoch-3 orders can no longer cross naturally even after resume. The observation is therefore
historically inconclusive: it neither passed nor failed, and it is no longer collectible from the
0041 cohort. It must not be closed with a synthetic fill, historical backfill, or by relabelling a
future 0042 fill as 0041 evidence. A genuine post-0042 fill must validate the current projection
path prospectively under its own release/lane identities.

### 19.3 Historical pre-0042 sleep/wake refresh, epoch 4, and safe stop

After an approximately 15-hour sleep/wake interval, the runtime correctly auto-engaged the kill
switch at `2026-07-16T10:38:35Z` because scheduler and capacity evidence were stale. Twenty-five
jobs were recorded as missed. This was the expected fail-closed response to stale operational
evidence, not a continuity mismatch and not a fill-projection failure.

Startup refresh restored capacity to `within_budget`. Discovery observed 199 events, 75 targets,
and 825 markets; observation collection reported 20/210, forecast collection 20/2,118,
probability generation 374, and the CLOB refresh wrote 38 of 110 due items. Audit 572 found exactly
one critical condition: the `official_resolution_truth` manifest mismatch created when contract
inventory changed from 155 to 182.

Reviewed plan 5 has plan hash
`cba642b35b31aa17428b5c13148daef19c0e85256235873d6093151a3a80829a` and release-manifest hash
`dcfc5302e0a4786d8017254555743d307a1d208134269047e5950f714b353e27`. It opened epoch 4 at
`2026-07-16T10:43:00.712204Z`, forked only `official_resolution_truth`, and continued
`predictive_validation`, `execution_evidence`, `selection_policy`, `station_lifecycle`, and
`operational_legacy`. Those continues preserve the accepted epoch-3 boundaries rather than reset or
backfill their denominators.

Following CLOB, settlement, and 1,000-item reconciliation refreshes, audit 573 is `warning` with 14
findings, zero critical, and zero blocking. Continuity match is true. The post-epoch-4 window has 49
successful jobs and zero errors.

The cutover command-5 resume above is not the current state: the kill switch remains ON because the
daily-loss warning reports P&L `-$96.31`, and the system is waiting for the next
`America/New_York` daily-risk boundary, whose instant is reported in UTC. This is an intentional
risk hold, not a failed release acceptance. The natural maker sample remains
15 epoch-3 intents/orders, zero fill legs, and zero prospective enrollments. Their signals were
expired by the kill switch, so they are stranded rather than a waiting natural-fill sample.

## 20. Revision 0042 and subsequent continuity releases

Revision `20260717_0042` adds prospective station-selection semantics. It does not rewrite
reconciliation rows, station stages, signals, orders, outcomes, or any 0041 denominator. Its new
append-only reliability snapshot is derived from independently collapsed
`station × local date × weather kind` units and carries its own policy, source, and record hashes.

The policy identity includes at least the protocol version, reconciliation window, minimum
comparable sample count, Wilson lower-bound threshold, resolution-conflict duration,
current-observation freshness, and snapshot maximum age. The source identity binds the exact
reconciliation record hashes and current observation evidence consumed by one assessment. The
record hash binds both identities plus counts, status, local evaluation date, conflict anchor,
block expiries, and stable reasons. Snapshot rows reject mutation and deletion.

The score denominator is `matches + conflicts`, where conflicts are
`exact_source_mismatch`, `clear_mismatch`, and `exact_truth_gap`. `boundary_sensitive` and
`truth_unavailable` are retained as separately counted evidence but excluded from the denominator;
they are non-punitive and cannot be reclassified as matches merely to preserve continuity. The
selection statistic is the two-sided 95% Wilson lower bound over that comparable denominator.

This is a material change to the `selection_policy`, `station_lifecycle`, and
`execution_evidence` identities:

- selection now depends on a current, hash-valid station snapshot and stable block reasons;
- PAPER may collect under `COLLECTING` when no current conflict/observation/integrity block exists;
- LIVE requires a current `RELIABLE` snapshot in addition to an `approved` lifecycle stage and all
  other global gates; and
- a pending signal cannot preserve authority after the station policy, local date, observation
  freshness, conflict state, or snapshot integrity changes.

`execution_evidence` is material because both PAPER and LIVE claim paths now recheck the bound
snapshot before fill/submission. This is not a reporting-only foreign key: it changes which pending
intent may execute and therefore requires a new execution-evidence boundary.

Every new selection decision records the exact snapshot identity it used. Execution revalidates
that identity and the current station gate before a PAPER claim or LIVE submission. Missing legacy
provenance is tolerated only as historical/queryable state; it cannot authorize a new post-0042
execution. The snapshot foreign key and immutable reference preserve the chain from station
assessment to signal and then through the existing signal/order intent evidence.

Continuity planning must hash all material assessment, reconciliation, repository, runtime,
migration, and configuration sources into the affected lanes. A reviewed candidate plan must fork
or freeze every mismatched lane; documentation cannot pre-approve a `continue`. The accepted epoch-5
activation forked `selection_policy`, `station_lifecycle`, `execution_evidence`, and the independently
mismatched `official_resolution_truth` lane. The actual plan output and database-bound manifest
remain authoritative and must freeze/fork any additional mismatch instead of forcing a continue.

Activation remains zero-backfill. Migration creates schema and integrity guards; it does not
synthesize a trusted snapshot for an old decision, move an old signal into a current lane, promote
a station, or count boundary/unavailable rows as success. Startup must refresh settlement, then
reconciliation, then station assessment before signal generation/execution. Scheduled assessment
may refresh observation freshness more often, but a reconciliation update cannot be followed by a
decision from an older assessment.

The pre-cutover execution-state quarantine is also zero-backfill. With the kill switch ON,
`stratus-quarantine-execution-state` must terminalize stranded PAPER orders. Orders with immutable
intents are closed only through `record_terminal_no_fill`, which appends the required obligation
and ineligible seal; pre-ledger orders are terminalized without fabricating an intent. The command
refuses the entire transaction when any LIVE order is `pending`, `submitting`, `open`,
`partially_filled`, `cancel_requested`, or `submit_unconfirmed`, or when PAPER fill evidence is
ambiguous. Such LIVE state requires venue reconciliation, never an inferred cleanup.

PAPER resume under 0042 remains a separate audited act. The historical 0041 natural-fill
observation remains inconclusive and must not be claimed by the 0042 migration, quarantine,
synthetic fill, historical backfill, or a later 0042 fill.

### 20.1 Observed 0042 cutover and hotfix acceptance — 2026-07-17

The retained pre-0042 backup is `backups/stratus_pre_0042_20260717T061253Z.dump`
(886,676,148 bytes; SHA-256
`2934FC7038279D4B0241633ED77C488DFDE16C573C4E4DC57EA4ECA3182661C4`). `pg_restore --list`
passed, and a full restore drill reproduced the 0041 head plus the frozen production counts in a
separate database before that drill database was removed. The production migration then reached
the single head `20260717_0042` with zero backfill of station-snapshot provenance into old signals.

With the kill switch ON and zero nonterminal LIVE orders, the quarantine classified 67 stranded
PAPER orders: 30 immutable intents received formal terminal no-fill obligations/seals, while 37
pre-ledger orders were terminalized without inventing an intent. A second run is idempotent. The
command refuses nonterminal LIVE state or ambiguous PAPER fill evidence rather than inferring an
outcome.

Candidate image
`sha256:a8cab1ec043a6a446cbbce9f8ee3b6620b4003515aeb1c2f5be14160c3f01312` and reviewed plan
`6ec51d341119e5203ada6e2dec35bc662b2fc655c8d1643385f1f81de20442c3` opened epoch 5 at
`2026-07-17T06:35:37.363729Z`. It forked `execution_evidence`, `selection_policy`,
`station_lifecycle`, and `official_resolution_truth`; the additional official-truth fork was caused
by the independently observed contract inventory growing from 182 to 209. `predictive_validation`
and `operational_legacy` continued. The migration/startup sequence was settlement, reconciliation,
station assessment, signal generation, then execution. All 20 active stations received a current
local-date snapshot under one policy hash, and every post-boundary signal carried snapshot
provenance.

The first audit exposed a reporting-only boundary defect: the unified current-evidence window used
only the continued predictive boundary and therefore relabelled pre-fork execution/selection days
as current. The fix makes that window start at the maximum boundary of all five prospective
evidence lanes and fails closed when any required boundary is absent. No evidence row was rewritten.
Hotfix image
`sha256:8ea86c06caeba861a61e015d4b6d0a2e6e107526e21e399eed3b45d8de20b32d` and reviewed plan
`666b265b892b21306dd8a5f0425883ec15f907ca804853a13f5a28a168da7c16` opened epoch 6 at
`2026-07-17T06:50:53.340688Z`, forking only `operational_legacy` and continuing the other five
already-correct boundaries.

Final acceptance has matching cohort/image/lane identities, no mismatched or frozen lane, and
continuity gate `evidence_continuity_match`. Formal audit 621 completed as `warning` with zero
critical and zero blocking findings; later refreshes preserve that zero-critical/zero-blocking
state. API and worker run the same hotfix digest. Verification passed Ruff, strict mypy over 166
source modules, 689 non-PostgreSQL tests, 239 PostgreSQL tests across clean sequential shards plus
the focused hotfix continuity regressions, and WinUI x64 Debug and Release builds with zero
warnings/errors. The operator API exposes 20/20 active stations with current-date trust snapshots,
one policy hash, zero active selection blocks, and 480 calibration rows.

During cutover the local operator API credential was rotated after it appeared in diagnostic
output. Compose now resolves it from `.env`, and Windows Credential Locker was synchronized to the
same new value; neither credential value is part of this document or release evidence.

After acceptance, audited operator command 6 resumed only the Polymarket PAPER venue at
`2026-07-17T07:10:31.783545Z`. The runtime remains strictly PAPER, the kill switch is OFF, and
`/readyz=200`. The next natural cycle completed successfully and created two `paper_resting` orders;
both bind a current-date station snapshot, release epoch 6, and all six lane attestations. The
post-resume health audit remains `warning` with zero critical and zero blocking findings. This is
authorization for prospective PAPER evidence collection only, not authorization for LIVE and not a
claim of predictive skill, positive CLV, or profitability.

### 20.2 Deterministic scheduler hotfix acceptance — 2026-07-17

The scheduler-ordering hotfix is runtime-only; production remains at Alembic head
`20260717_0042`. Image
`sha256:061e73111d8ac0464c3bc2b2cffe92ff8370984789a9283fc734403d0859f843` was activated through
reviewed plan 9,
`661b47e1f4244f3cad6ce68090537a25c913cc955fc69f2508cd2a9fb8999a66`, as epoch 7 at
`2026-07-17T08:26:03.767299Z`, with epoch 6 as its predecessor. Because `runtime.py` is part of the
database-bound manifest for four material lanes, the plan forked `execution_evidence`,
`operational_legacy`, `selection_policy`, and `station_lifecycle`. `predictive_validation` and
`official_resolution_truth` continued. Image, cohort, and every lane identity match the activated
release; no lane is mismatched or frozen.

Audited command 7 stopped execution at `2026-07-17T08:22:51Z` before activation. The execution-state
quarantine then returned zero classified, zero terminalized, and zero ambiguous orders (`0/0/0`),
with no nonterminal LIVE order. Audited command 8 resumed only Polymarket PAPER at
`2026-07-17T08:33:06Z`; it did not enable LIVE.

The accepted runtime makes the recurring dependency order explicit instead of relying on
independently dispatched APScheduler jobs. The `observations` parent commits observations before
publishing station assessment. The hourly `resolution-reconciliation` parent performs settlement,
then reconciliation, then station assessment. The `prices-and-signals` parent holds the same
station barrier across price/probability/signal publication and execution, so it cannot enter
between reconciliation and the assessment derived from that reconciliation. Price cohorts start at
second 30 of every minute. Settlement is polled at minutes 00, 10, 20, 30, 40, and 50; the minute-00
poll is the first phase of the reconciliation composite, while the other five are settlement-only
polls. Startup job-run IDs 31656 through 31661 recorded the prescribed settlement -> reconciliation
-> station assessment -> price/signal -> execution order. Recurring composite parent/child records
31693/31694 and later runs completed under the same ordering contract. The first standalone
settlement on the new grid was job 31738 at exactly `08:40:00Z`. The first recurring observation
composite then committed all 20 stations/214 METAR reports before child station assessment 31786;
parent observation job 31787 completed only after that child succeeded.

Parent and child job names deliberately remain separately visible for operator liveness. Scheduled
`observations` records child `station-reliability`; scheduled `prices-and-signals` records child
`execution`; scheduled `resolution-reconciliation` records its child `settlement`. There are no
independently scheduled `station-reliability` or `execution` jobs. Additional `settlement` records at
minutes 10, 20, 30, 40, and 50 are the independent settlement-only polls, not duplicate execution of
the hourly composite.

Formal audit 634 completed as `warning` with six informational and six warning findings, zero
critical findings, and zero blocking findings. `/readyz=200`, the operational state is
`collecting`, evidence continuity matches, and no nonterminal LIVE order exists. The first 205
post-resume signals were conservatively skipped; every one carried a present, fresh, current-local-
date station snapshot. No executable signal or order had yet been created, so this observation
verifies fail-closed station provenance and scheduler ordering but is not fill or profitability
evidence. Verification passed Ruff, strict mypy, 695 non-PostgreSQL tests, and 61 PostgreSQL tests.

This hotfix acceptance changes timing and evidence integrity only. It does not close revision
0041's historically inconclusive natural-fill observation, prove predictive skill, positive CLV,
or profitability, relax any empirical/legal/risk gate, or authorize LIVE.

### 20.3 Candidate-anchor v2 selection-only acceptance — 2026-07-17

Image `sha256:12b1b2ca4874dbe13bd4a89ab0e63f13a41781a8fc7a9014e8c2282aa606b8d3`
was accepted through reviewed plan 10,
`c519506a00adf95ae3370e91f27fae9eab2276435119cf787b186b0049ea71cc`, as epoch 8 at
`2026-07-17T09:36:34.871545Z`. The database-bound manifest forked only `selection_policy`.
`predictive_validation`, `execution_evidence`, `official_resolution_truth`, `station_lifecycle`,
and `operational_legacy` continued with exact parent identity. Image/cohort/lane identities match;
there are no mismatched or frozen lanes.

Audited command 9 stopped Polymarket under PAPER with zero pending signals and zero nonterminal
orders; no LIVE order or position existed. Audited command 10 resumed only PAPER at
`2026-07-17T09:40:53.339919Z` after startup completion, matching continuity, and a health audit
with zero critical/blocking findings. The first natural price/signal cycle completed successfully
at `09:41:56.867223Z` and created real resting maker evidence instead of the former stable
`event_probability_time_mismatch` bottleneck.

At the `09:51:03.718696Z` acceptance snapshot, epoch 8 had 24 intents: 23 resting PAPER orders and
one eligible natural maker fill with one eligible prospective enrollment. All 24/24 checked orders
bound the current model cohort, exact candidate CLOB snapshot, current station snapshot, and the
epoch-8 selection lane; LIVE count was zero. A deeper 22-order proof checked 11 complete event
vectors and 121 ordered bucket rows with zero model/vector/partition/anchor/lane violations and
zero sibling borrowing. Post-resume audit 643 was `warning` with 5 info, 6 warning, 0 critical, and
0 blocking findings; continuity bottleneck became `maker_orders_resting`.

This boundary is selection-only. Historical v1 skips retain their original interpretation, and
the continued predictive and execution lanes retain their evidence. The natural fill starts a new
execution observation but is not called complete until its required post-fill marks and immutable
cost seal mature. No part of this release authorizes LIVE or establishes model skill, CLV, or
profitability.

### 20.4 Observation-recovery boundary — revision 0043

Revision 0043 changes the availability and provenance of METAR-derived training labels and previous-
run forecasts. PostgreSQL attests prospective `DailyActual` and `HistoricalForecast` first-seen
times; historical forecasts become immutable insert-or-compare evidence; all predictive truth reads
enforce their as-of cutoffs. Its required semantic continuity action includes a
`predictive_validation` fork.

Recovered daily-actual provenance is also retained in resolution reconciliation instead of being
collapsed into indistinguishable legacy METAR truth. Because that reconciliation feeds station
reliability and selection claims, the reviewed candidate is expected to fork `selection_policy` and
`station_lifecycle` as well. Because the operational lane hashes the same reconciliation job and its
record/value-hash semantics changed, `operational_legacy` is also expected to fork. Recovery
artifacts and points remain calibration evidence, not official settlement truth;
`official_resolution_truth` and `execution_evidence` may continue only when the generated
database-bound plan proves exact parent identity. The plan remains authoritative and reviewed,
never forced by documentation.

The material identity includes the exact IEM endpoint digest, streaming response byte cap, global
per-run HTTP budget (including retries), recovery/parser/completeness policy, immutable historical-
forecast path, truth consumers, station mapping, and reconciliation dependency closure. The
candidate cannot collect recovery evidence before migration and continuity activation because doing
so would attach the new label semantics to the old evidence epoch.

Canonical requirements and the eventual production hashes/counts are maintained in
`OBSERVATION_RECOVERY_PROTOCOL.md`. This section records the required lane meaning; it does not claim
that revision 0043 had already been deployed or accepted at the time it was written. The observed
acceptance follows.

### 20.5 Observation-recovery production acceptance — 2026-07-17

Revision `20260717_0043` opened its prospective recovery runtime boundary at
`2026-07-17T12:12:22.739436Z`. Audited command 11 stopped PAPER immediately before the cutover;
there was no separate resume command and no LIVE action. Image
`sha256:bd5545a5db0d965bc18871b934b70f09a725ca565a6574bad9e68b46bb3b9b78`, reviewed plan 13 hash
`479f0438ed4a0932df649155b8be99b8dfdd2cce1fae4dec115af717d1e6e19a`, and epoch 9 (parent 8,
opened `2026-07-17T12:43:49.447950Z`) were activated with the database-bound actions required by
the candidate: forks of `predictive_validation`, `selection_policy`, `station_lifecycle`, and
`operational_legacy`, and exact continues of `execution_evidence` and
`official_resolution_truth`.

The release-, lane-, and activation-manifest hashes were
`ae61538a05eb3056f874cb2dbe63ca92305ec28fe60ab19511bdc1f52ea8c106`,
`c6c55b7364d2cd6b7ade0eff17807998e50741a4fd0280f5d702ec608d1344ac`, and
`c75ad808b3d6655cf944d43c0be1c20366e208d9389c6e63b318ea892d099290`. Because compose started the
migration before the planned dump, the retained archive is explicitly a post-migration,
pre-activation witness: `stratus_post_0043_pre_activation_20260717T121343Z.dump`, 1,132,357,356
bytes, SHA-256 `1FB73D945E0825763765176E40B09A93EC5ACE3F1CF74C752C52FDCA1A6A4151`. Catalog inspection, full
isolated restore, and independent zero-backfill checks passed; the history is not relabelled as a
pre-migration backup.

Collection began only after epoch activation. It accepted 21 immutable artifacts containing 878
normalized points and produced exactly 42 atomic recovered HIGH/LOW actual rows. Audits 658 and 659
had zero critical/blocking findings. No execution or official-resolution evidence was relabelled,
and no LIVE order or position existed. Canonical source, count, and retention details remain in
`OBSERVATION_RECOVERY_PROTOCOL.md` section 11.

### 20.6 Resolution-contract semantic identity — revision 0044

Revision `20260719_0044` replaces volatile daily contract-inventory identity with semantic profiles
without discarding exact event evidence. One and only one rules date is parsed, normalized in the
profile template, and required to equal `market_date`; every other rules byte remains material.
HTTPS source locators reject ambiguous whitespace/control, invalid escaping, dot-segment, encoded
authority, IP, and numeric-host forms. Manifest construction reparses each database contract and
compares every persisted field. Repository writes are insert-or-compare; PostgreSQL permits only an
irreversible retirement timestamp and rejects content UPDATE/DELETE. Downgrade must refuse while
any immutable contract exists.

The runtime revision also removes owner/superuser credentials from application services through a
restricted `stratus_app` role, includes compose in `operational_legacy`, uses `/healthz` for
container liveness, restarts PostgreSQL after transient Docker startup failures, and bounds the
accuracy audit's trajectory scan. Preflight contains 263 active event contracts and 27 semantic
profiles with zero current rules-date/locator violation. The prior audit path exceeded 4.66 GiB RSS
and was terminated; the bounded run completed in 201.641 seconds at approximately 306–410 MiB RSS,
writing 33,104 skill reports and 123 drift events.

Because `services/resolution_contracts.py` is material to station interpretation, the contract
change directly requires forks of `predictive_validation`, `official_resolution_truth`,
`station_lifecycle`, and `operational_legacy`. The final hardening also binds `pyproject.toml` and
`uv.lock` into every lane identity instead of leaving dependency drift only in the global payload.
That source-closure expansion is itself a material identity change, so reviewed database-bound plan
16 forked all six lanes, including `selection_policy` and `execution_evidence`. The actual plan
remained authoritative and was not forced to match documentation. The observed backup, migration,
initial epoch, operational-smoke failure, corrected image, successor epoch, health state, and
PAPER-only disposition are recorded in section 20.7. LIVE remains prohibited.

#### Mandatory 0044 cutover order

Revision 0044 must not be migrated underneath a legacy owner-connected runtime. With the global
and venue kill switches already engaged, stop every non-Postgres Compose service, including
`worker`, `api`, `telegram`, Binance, and the owner-connected `backup` sidecar, plus any host
`stratus-api`; verify through `pg_stat_activity` that no other database session remains (excluding
the owner session performing the check). Only the migration and tightly scoped role provisioner may then use
the owner credential. Apply 0044, provision the database-qualified application login, and start
new non-root runtime containers that receive only the runtime password file, never the owner DSN,
owner password, or Docker socket. After startup, prove owner sessions are absent from the accepted
application runtime. A deliberately started migration, role provisioner, or backup process is a
time-bounded owner exception and must be attributable to that service. Prove runtime sessions use
`session_user = current_user = stratus_app_<db-md5>`, TEMP is denied,
`session_replication_role = origin`, and integrity triggers are enabled. Production acceptance
proves the remaining boundary through catalog ACL/ownership inspection, runtime identity checks,
enabled-trigger inspection, and safe denied authentication/cross-database probes. Destructive DDL,
TRUNCATE, or trigger-disable probes run only against an isolated restored or integration-test
database under the restricted role; they are never attempted against production data.

This order is a blocking release prerequisite. Container dependencies do not revoke already-open
owner sessions. Host development mode is explicitly PAPER-only and is not an OS isolation boundary;
it cannot be used as the accepted production runtime or for LIVE.

### 20.7 Observed 0044 production cutover and capacity hotfix — 2026-07-20

This cutover was performed with the global and Polymarket kill switches engaged, runtime mode
`paper`, Binance mode `backtest` with its venue kill engaged, and zero open LIVE positions or
nonterminal LIVE orders. Nothing in this record authorizes LIVE.

#### Backup, restore, and schema preservation

The accepted pre-migration archive is
`backups/stratus_pre_0044_20260719T222139Z.dump`, 1,572,434,322 bytes, SHA-256
`EBE624E8BB16CF557E25A0CB7FFC15EFF6C7815F8E6FAD958B115A7BECB9C8F2`. A full isolated restore
proved head 0043, 263 contracts of which all 263 were active, 144 positions, 5,764 immutable CLOB
batches, and observation-recovery state of 21 artifacts, 878 normalized points, and 42 recovered
actuals. A prior interrupted archive was renamed with the suffix `.rejected-incomplete`: its table
of contents was readable, but it contained no contracts or positions and was never accepted as a
backup.

Before and after migration, canonical contract inventory remained 263/263 and its comparison hash
remained `37a630b53282b061cdbaee71dbde0263`; positions remained 144 and LIVE remained zero. Production
then had the single Alembic head `20260719_0044`. The mounted HBA parsed as nine rules with zero
errors. It admits exact role `stratus_app_752875213a111f12d876fef68bbf8259` only to database
`stratus` and rejects the runtime-role family elsewhere.

The application containers run as uid/gid 10001 and expose no owner password, owner/migration DSN,
generic `DATABASE_URL`, or `PGPASSWORD`. Runtime sessions proved
`session_user = current_user = stratus_app_752875213a111f12d876fef68bbf8259`, database `stratus`,
`session_replication_role = origin`, LOGIN+INHERIT only, CRUD without ownership, and denial of TEMP,
schema CREATE, TRUNCATE, owner login, and cross-database login. All three 0044 integrity triggers
were enabled. Owner sessions were absent after the one-shot migration/provisioning checks.

#### Initial activation and fail-closed operational rejection

Initial image `sha256:35d42a6a5d059c60133cda430045d989ac2b65f438525949b98d332a50b7e922`
(191,501,862 bytes) was reviewed as plan 16, plan hash
`33e20cf0cc8d4567f70bad735ba79ac469c47494e5c7f0de2bfff197de91c705`, plan record hash
`afbb4549f53942b1e0b586adee8024326d8f8dc51367f5d165c1628f8682b230`, release-manifest hash
`e6943ea260b2569e1a31043f68ffc5753696807120a84b8ce9251e78acb3b552`, and lane-manifest hash
`8d8a61773a33feb8ef5a7e7168ff795c0cc1f8eb1fe76cb2fa628a31ef8449dc`. It opened epoch 10
(parent 9) at `2026-07-20T01:58:56.151365Z`, epoch record hash
`5ebf161ae655cceeef075e32372ed4d965cf30cf08d0e85d58a38a306b9de177`, with all six lanes
forked. Stale plan 14 still referenced the former epoch-9 image and was never activated.

The first worker smoke correctly remained unable to execute under the kill switch, but its
storage-capacity startup job failed: even with an explicit mounted capacity path, SQL evaluated
`current_setting('data_directory')`, which the restricted role may not read. Price collection then
failed closed on stale capacity. The worker was stopped immediately; the kill switches did not
change, no new PAPER execution was admitted, and LIVE remained zero.

The correction binds an explicit capacity path as a SQL value and evaluates
`current_setting('data_directory')` only when no override is configured. No
`pg_read_all_settings` or other privilege was granted. Capacity audit, price collection, and their
policy/repository dependencies are now part of the `operational_legacy` source closure.

Intermediate image `sha256:9be3e644ed4199d5b4193115c1e5d208ddbc050d291606c417cd7233829d6390`
produced plan 17, hash `97d6305ca639b03bae98eb19129799758bfcfba1429e42dd445e1aad1130dc5c`,
whose six lane actions were all `continue`. That preview exposed the missing capacity source
closure and was deliberately never activated. This is a fail-closed negative acceptance result,
not an epoch.

#### Corrected image and successor epoch

Final image `sha256:7105bfc30649ceab40e3f61df69c021a6aa6724b92b46bd32fc61d40f75b83ed`
was created at `2026-07-20T03:05:29.198555438Z` and is 191,502,785 bytes. Rendered Compose for
every profile bound migrate, API, worker, Telegram, and Binance runtime to exactly this digest.
Reviewed plan 18 has plan hash
`fa37901163780f1d3c458325a10934c5205afe621ada0506aa1c50271db3c0b2`, plan record hash
`de01e01a4772a5640a5e487b23abc0666083a137ad4547391efc5479abcefa30`, release-manifest hash
`2ff1ae6d105119ec9295eaa45973151296bea0e12a6cc5d9fbe8ff8af8bb04b8`, and lane-manifest hash
`5e21fde2400cc7c5667feb0b52df46cf8e8112ef4a436df7176fe4cce0de6f54`.

Plan 18 opened epoch 11 (parent 10) at `2026-07-20T03:07:26.404201Z`, epoch record hash
`0b8e0dbdedc77cc37ca36b7531cfb379f32e41cbd477bee00fa45c45aa11fbf2`. Only
`operational_legacy` forked, to identity
`b3a8f577d40b2773156461ef1b5f3fe7353aace1fabe3c864b6c7086c040ff0a`; the other five lanes
continued their exact epoch-10 identities and boundaries. All requirements were complete, with no
missing, mismatched, or frozen lane. Cohort and image match, and the continuity gate reason is
`evidence_continuity_match`.

The final worker ran as uid/gid 10001. Explicit and natural capacity jobs succeeded; the fresh
sample at `2026-07-20T03:08:41.634594Z` was complete, `capacity_within_budget`, with
983,648,919,552 free bytes against a 5,000,000,000-byte reserve. The startup price job 36095 wrote
453 of 594 due market snapshots and skipped 141 conservatively; probability recomputation wrote
242 rows, signal generation classified 168 candidates as skipped, and execution job 36097
succeeded by remaining blocked under runtime control. Scheduler startup completed with 27 jobs,
and the first scheduled killed cycle also completed successfully. No `data_directory` permission
error recurred.

Bounded accuracy audit completed in 216 seconds with 33,392 skill reports and 118 drift events;
observed worker memory was approximately 1.18 GiB rather than the pre-fix multi-GiB unbounded
failure. Verification was Ruff-clean, strict mypy-clean over 170 source files, and 839
non-PostgreSQL tests passed. The complete 275-case PostgreSQL set was covered as 273 passes against
the production-HBA server plus the two intentionally HBA-rejected direct-login cases passing 2/2
against an isolated CI-equivalent PostgreSQL; after the source-closure correction, all 23 focused
continuity/capacity PostgreSQL tests passed. WinUI Release x64 compiled with zero errors (one
network-only NU1900 warning), all Compose profiles validated, and the final Docker build completed.

Pre-reset health audit 697 at `2026-07-20T03:16:37.232906Z` contained five info, twelve warning,
zero critical, and one blocking issue: only `weekly_loss_limit`, P&L `-$232.56`. Continuity and
capacity were healthy; readiness was 503 only for the blocking audit and deliberate kill switch.
The final weekly-reset audit, venue-scoped PAPER resume, first post-resume natural cycle, and
post-resume invariants are appended below only after they are observed.

### 20.8 Backup hardening, final operational epoch, and PAPER resume — 2026-07-20

The pre-reset review did not treat a readable custom archive as the whole unattended-backup
contract. It found that two sidecars could overlap, transient failures could drive restart churn,
publication after the dump was not fully fail-closed, schema identity was sampled only after the
archive snapshot, age-only retention could eventually erase every good local copy, and remote
copying exposed an incomplete prefix as though it were accepted. No backup was started from that
candidate.

The corrected contract uses a shared-filesystem `flock`, a six-hour dump timeout, bounded
five-minute-to-one-hour exponential retry, and an immutable tool-image digest. Each restore point
is assembled in a hidden unique directory and published by one directory rename only after the
custom archive, readable non-empty catalog, SHA-256/byte/catalog counts, and equal pre/post Alembic
heads exist in `manifest.txt`. Failures remove only their unpublished partial. Retention runs only
after successful publication, keeps at least the three newest directory restore points, and never
automatically removes accepted legacy `.dump` files. The optional remote path accepts only an
rclone remote named `crypt`, download-verifies the uploaded files, and writes `COMPLETE` last;
consumers must reject a remote prefix without that exact marker.

An intermediate implementation was made explicit rather than silently inheriting epoch 11.
Reviewed plan 19 had plan hash
`93582cf748ddd0b75ef90af4284fea9ab73fbc07b78a1063e598661c59fca874`, plan-record hash
`226c245f0702498c6442ab74ab91ed911be65213cfd62418a6d042814029fcb8`, release-manifest hash
`80345367a4a97c44b45d2239f684a64db26b352627d35178348c17b91f508abb`, and lane-manifest hash
`f8848759b83c79da32db9bf5965d983ab23a81f7681aabc3ee18f2d408dd915e`. It opened epoch 12
(parent 11) at `2026-07-20T03:46:00.963739Z`, record hash
`b32316cf1be7f95ff2577e4c77515b5ba5958d4647e49cb0fc2465a8dc42436d`. Only
`operational_legacy` forked, to lane 69 identity
`9035b7a5133fa794069b1b7038725f7a46e96780f87d14b18caa812ce4295403`; review then found the
remaining fail-open/retention/off-site defects before the sidecar was started.

Final image `sha256:c5076ba953fe44dca9b885c1c9823e2bca82f84f5ecc90fe70ddc89c17bcf2f5`
was created at `2026-07-20T04:03:12.146490679Z` and is 191,507,614 bytes. Reviewed plan 20 has
plan hash `871695387dee29f458373920cbe1b50dfedae8a4e43bae18160ff15f55446038`, plan-record hash
`af6a1ac0221b6adb2707cd80fbc2cd0c52a6cf8df023e05cf852169157cdcce2`, release-manifest hash
`b1241540090360a410db93920d74d8d718d6b662f2b7150c0afd3d7dac244df3`, and lane-manifest hash
`1c8c60b4cb8467eabc7f5d1b64c018638f33ed57aa4d2acadb5803b7cae6d6d9`. It opened epoch 13
(parent 12) at `2026-07-20T04:04:54.381320Z`, epoch record hash
`63c4debb386ccdf8208e213a0745c8888bac6c036b2c617573bfa27c8d752b6b`. Again only
`operational_legacy` forked, to lane 75 identity
`ffbb97c5a9a2e339b2b63490df291dfa9b95281c50adae52041d8222b4d64818`; the other five lanes
continued the same boundaries and identities through epochs 11, 12, and 13. Image/cohort match,
the lane set is complete, no lane is mismatched or frozen, and the gate reason is
`evidence_continuity_match`.

The `America/New_York` week reset occurred at `2026-07-20T04:00:00Z`. Audit 702 at
`04:05:06.539929Z` was `warning` with 18 issues, zero critical/blocking, and no daily/weekly loss
finding. Before resume, runtime and Polymarket were both killed PAPER, Binance was killed
`backtest`, capacity was complete/fresh/within budget, `/healthz` was 200, `/readyz` was 503 for
exactly `kill_switch`, and the database contained zero open LIVE positions and zero nonterminal
LIVE orders. Audited command 12 (`paper-resume-0044-20260720t0408z`) succeeded at
`2026-07-20T04:09:39.767520Z` with detail `Kill switch off for eligible venues in polymarket.`

After the command, global runtime and Polymarket were PAPER with kill off; Binance remained
`backtest` with kill on; `/healthz` and `/readyz` returned 200. The first natural cycle after the
command was job 36479 (`prices-and-signals`, scheduled `04:10:30Z`) with its execution job 36478;
both completed `success`. It produced no new signal or order, which is a valid conservative result,
and produced no LIVE state. Audit 703 at `04:10:58.811269Z` and periodic audit 704 at
`04:14:19.744572Z` were both `warning` with 17 issues and zero critical/blocking findings. Full
non-PostgreSQL regression after the final backup contract was 843 passed and 275 intentionally
skipped PostgreSQL tests; Ruff was clean, rendered backup/off-site shell syntax passed in the
digest-pinned images, and all Compose profiles validated. These facts authorize prospective PAPER
collection only; they do not authorize LIVE.

The owner-only sidecar was first started from epoch 13 at `2026-07-20T04:12:56Z`. A second
nonblocking acquisition of `/backups/.stratus-backup.lock` was refused while the dump ran. The
hidden work directory was not accepted until it atomically published as
`backups/stratus_20260720T041256Z_9379ef5bda23_1.backup` at `04:31:35Z`. Its archive is
1,265,297,862 bytes with SHA-256
`f422dd32866a697f12d2d3077901512fadec929b21c9d05ffb0c87c538e9b7b2`; the 89,396-byte catalog
has SHA-256 `b49641682e3b4f384ab21893724fec02796b09b7e2989c9a7d2b7baab52030f7` and 1,074 entries.
The manifest records head `20260719_0044` both before and after the snapshot, a readable catalog,
and deliberately says `full_restore_verified=false` because the producer may not self-attest the
separate restore drill.

That exact published archive then restored completely into an isolated, unexposed PostgreSQL 16
container. The restored copy had head `20260719_0044`, 79 public tables, zero invalid indexes,
zero unvalidated constraints, zero disabled user triggers, 269 resolution contracts, 144
positions, 1,301 orders, 130,997 signals, all 13 release epochs and 78 continuity lanes, command 12,
and zero open LIVE positions/nonterminal LIVE orders; database size was 6,749,789,207 bytes. The
temporary restore container and volume were deleted only after those queries passed. The retained
backup sidecar is `healthy`; no owner client backend remains connected while it sleeps. Audit 709
at `2026-07-20T05:04:19.761296Z` is `warning` with 17 issues, zero critical/blocking findings, and
no loss-limit finding. By the `2026-07-20T05:14:19Z` acceptance check, 60 additional orders had
been created since the pre-resume snapshot and all 60 were PAPER; LIVE remained zero.

### 20.9 Docker restart, false-publication containment, and epoch 14 — 2026-07-20

#### Incident boundary and false publication

Section 20.8 remains the historical epoch-13 acceptance record. During later console/API work, the
Docker Desktop backend was stopped inadvertently and the database port became unavailable. Docker
and PostgreSQL were recovered, but review of the restarted backup sidecar found a more important
contract defect: a failed dump had produced a published zero-byte restore-point directory and a
false health marker. They were quarantined, not deleted, as
`backups/.rejected-zero-byte-stratus_20260720T071344Z_9379ef5bda23_1.backup` and
`backups/.stratus-backup-health.rejected-20260720T071344Z`. They are forensic artifacts and must
never be treated as accepted backups.

The failure was caused by POSIX `set -e` semantics: commands inside `create_backup` did not abort
the shell because the function itself was called in an `if` condition. The dump failure therefore
fell through into publication and health reporting. The incident did not authorize execution;
runtime was held behind the kill switch while evidence was preserved and the contract was fixed.

#### Corrective backup contract and owner rotation

Every critical producer command now has an explicit `|| exit 1`, `create_backup` runs as a simple
command with its status captured afterward, and a published restore point is revalidated before
success is declared. The validator rejects noncanonical basenames, symlinked directories or files,
zero-byte archives, unreadable or empty catalogs, checksum/count mismatches, and unequal schema
heads. Hidden abandoned partials are cleaned without touching accepted evidence. Retention counts
only accepted restore points, and the atomic health marker is bound to the accepted directory and
manifest hash.

The optional off-site contract is also fail-closed: an empty remote store remains unhealthy; a
matching `COMPLETE` marker receives an hourly lightweight `rclone check --size-only`; one accepted
backup per day receives a rotating full `--download` scrub; scrub state persists in the named
volume `stratus-offsite-state`; and a fresh upload still receives full download verification before
`COMPLETE` is published.

An owner credential appeared in diagnostic output and was therefore treated as exposed. It was
rotated without recording either secret here, PostgreSQL accepted the replacement and rejected the
retired value, stopped containers carrying the retired credential were removed, and the distinct
least-privilege runtime role was reprovisioned. No owner client backend remained connected when the
release work completed.

#### Image, reviewed plan 21, and epoch 14

Corrective image `sha256:cdcd9d48f6e2aa56c5f54519db88a228e0652f44dc53f4cf209ae5f6e3d1131a`
was created at `2026-07-20T08:25:07.307215542Z` and is 191,513,285 bytes. The embedded
`/app/docker-compose.yml` SHA-256 is
`574bac6c8d1f42413df47460b59e6647b46e1cbcfec0b092ae72e8a62fdbc910`, matching the reviewed
local file. Reviewed plan 21 has plan hash
`b1102b7392e277a58ef6dec3e669bd79c54344ca5f70be84171217d0c4a780b0`.

Epoch 14 (parent 13) opened at `2026-07-20T08:57:06.446500Z`. Only
`operational_legacy` forked, to identity
`1d6fc35e8464917f0fe83946ac356d56697c100ee94d779c7386f77b6d1f8884`. The
`execution_evidence`, `official_resolution_truth`, `predictive_validation`, `selection_policy`,
and `station_lifecycle` lanes continued their exact parent identities and boundaries. Image/cohort
match, the lane set is complete, no lane is mismatched or frozen, and the gate reason is
`evidence_continuity_match`.

#### Post-fix backup, isolated restore, and runtime disposition

The first accepted post-fix backup started at `2026-07-20T08:58:27Z` and atomically published as
`backups/stratus_20260720T085827Z_0d352caf5b71_1.backup` at `09:10:59Z`. Its archive is
1,276,357,110 bytes with SHA-256
`cb286806568a688870e20ece21d6711f5bfea740769cbbb87c1432cb9a5a432c`; its 89,396-byte catalog
contains 1,074 entries and has SHA-256
`bba08542e0c364e949b14d6aabc0a711a66b5aef7b4ef77efeed1b9c4dd03611`. Manifest SHA-256 is
`9b8cf4f8dd4a3c2159b17c1fc37ba651239f0b1eb9e7ad11cafcf6648829ca62`, with schema head
`20260719_0044` before and after the snapshot. The format-v1 health marker names this exact restore
point and manifest hash; the sidecar is healthy and sleeping on its daily schedule.

An independent host checksum and catalog/manifest validation preceded a full restore into an
isolated PostgreSQL 16.13 container with `--network none` and no host port. The restored database
was 6,635,944,983 bytes and contained schema head `20260719_0044`, 79 public tables, 290 resolution
contracts, 147 positions, 1,336 orders, 132,048 signals, 14 release epochs, 17 reviewed plans, 84
continuity lanes, 13 commands, and audit 709 as its latest snapshot audit. It had zero invalid
indexes, unvalidated constraints, disabled user triggers, LIVE positions, LIVE orders, or LIVE
intents. Epoch 14/plan 21 and successful command 13 were present. Runtime in the snapshot was PAPER
with the kill switch on. The temporary restore container and volume were independently confirmed
removed after verification.

Audit 710 after recovery was blocking only because capacity was stale and one execution-evidence
obligation/mark was overdue. Under the kill switch, bounded capacity refresh and evidence
finalization sealed that obligation; audit 711 then had zero critical or blocking findings.
Audited command 14 (`paper-resume-epoch14-20260720t1006z`) succeeded at
`2026-07-20T10:10:07.974026Z` with detail `Kill switch off for eligible venues in polymarket.`

At the final observed state, API, PostgreSQL, backup, and worker services were healthy with zero
restarts; Polymarket and global runtime were PAPER with kill off, while Binance remained
`backtest + kill`. `/healthz` and `/readyz` returned 200. Audit 712 at
`2026-07-20T10:17:05.460667Z` was `warning` with 15 issues and zero critical, blocking, or loss
findings. Since command 14, all 21 orders were PAPER, three PAPER positions were open, and LIVE
positions/nonterminal LIVE orders remained zero. The one error among 53 post-command job runs was
the fail-closed `daily-actuals` rejection of 14 station-days with fewer than 20 observations; it
was a data-completeness finding, not an execution or integrity failure. The first natural scheduled
trading cycle completed successfully, and maker simulation correctly produced no fill without a
price crossing. These facts authorize continued prospective PAPER collection only and grant no
LIVE authority.

### 20.10 Observation-recovery missed-hour correction and epoch 15 — 2026-07-20

#### Scope and reviewed plan

The production scheduler could miss a recoverable station-day when the worker did not run during
the one configured station-local recovery hour. The correction changes only the automatic
admission boundary from exact-hour to at-or-after-hour; every evidence, completeness, cooldown,
lookback, pacing, and request-cap rule remains fail-closed and bounded. The source closure now
explicitly attributes that recovery job to every lane that can consume the resulting label or its
availability: `predictive_validation`, `selection_policy`, `station_lifecycle`, and
`operational_legacy`. It does not attribute the job to execution evidence or official truth.

Image `sha256:c8813973ad897e9b7e0cc12de77eca4a58a636e901ae8e391f53843ce6b38a2c`
was retained alongside rollback tag `stratus:rollback-e14-cdcd9d48f6e2`. Reviewed plan 22 has plan
hash `552954843fce5ff3e4db4b09fd350043e00d4b08360e26940316df4dd88137b8`, plan-record hash
`bfff9a2cef6e99bf3798721bdf165308547aa768ed9198c2b3522c28bd11c1f3`, release-manifest hash
`62d4fe24082b3fa8ab312ed4f2f0d5ee79b42ef6c157e5f3ac1f033cc0f7430b`, and lane-manifest hash
`50e9d28267aadc204566a34689732d790cb5717491e80409a23c704c5c41aea4`.

The reviewed actions were exact:

- lane 85, `execution_evidence`: continue parent lane 79;
- lane 86, `official_resolution_truth`: continue parent lane 80;
- lane 87, `operational_legacy`: fork parent lane 81;
- lane 88, `predictive_validation`: fork parent lane 82;
- lane 89, `selection_policy`: fork parent lane 83;
- lane 90, `station_lifecycle`: fork parent lane 84.

Epoch 15 opened from epoch 14 at `2026-07-20T11:22:49.768643Z`, with epoch-record hash
`1f23ab488f0717ef0b8c9f7e981a0b708538de3ed1203362988ab5148aa629f4`. Its gate reason is
`evidence_continuity_match`; image and cohort match, and no lane is mismatched or frozen.

#### Fail-closed cutover and recovered evidence

Audited command 15 (`recovery-release-stop-20260720T111140Z`) succeeded at
`2026-07-20T11:12:23.411485Z`, engaging the all-venue kill switch and expiring 19 Polymarket
signals. Before replacement, the accepted epoch-14 worker sealed the nonterminal execution state:
50 PAPER records quarantined, 50 sealed, zero legacy. The candidate API was admitted first and the
candidate worker remained stopped until the plan had been reviewed and activated.

After the epoch-15 boundary, bounded recovery pass 1 materialized 20 station-days and pass 2
materialized 18; pass 3 made zero HTTP requests and exited successfully. Accepted artifacts grew
from 42 to 80 and recovered actual rows from 84 to 160. The final integrity query found zero
accepted artifacts without exactly two derived kinds, zero derived actuals attached to a
non-accepted artifact, and zero missing station-days in the active 20-station three-day lookback.
This is prospective evidence admitted after the new predictive boundary, not a relabelling of old
evidence.

The candidate worker then completed its startup pipeline under the kill switch. It collected 212
observations for 20 stations, produced 2,120 forecast rows and 1,055 trajectories, built 32,800
ensemble rows, refreshed 580 price rows, and recomputed 308/308 probabilities. Three shadow-model
gaps were isolated rather than fatal. Signal evaluation inserted 218 skipped records and execution
remained blocked by runtime control. Six execution-evidence obligations were claimed and sealed.

#### PAPER-only disposition

The final pre-resume checks had zero critical or blocking findings and zero LIVE state. Audited
command 16 (`recovery-release-resume-20260720T115730Z`) succeeded at
`2026-07-20T11:57:53.355292Z` and released only Polymarket PAPER. Binance remained
`backtest + kill`. The first natural price/signal cycle evaluated 220 markets, created 21 pending
PAPER signals, and posted 21 maker-simulated PAPER orders; one later filled naturally. At the
acceptance snapshot there were 22 post-resume orders, all PAPER, and zero LIVE positions, orders,
nonterminal orders, or intents.

Audit 721 at `2026-07-20T11:59:13.936335Z` was `warning` with 16 findings and zero critical or
blocking findings. API, worker, PostgreSQL, and backup were healthy; `/healthz` and `/readyz`
returned 200. Global and Polymarket mode remained PAPER with kill off, while Binance stayed killed.
This acceptance authorizes continued prospective PAPER evidence collection only. It does not
authorize LIVE or claim that the model beats the market.

### 20.11 Canonical settlement-truth quarantine and epoch 16 - 2026-07-20

#### Truth boundary and non-rewrite contract

Review of the current reconciliation state found that a METAR-derived `daily_actuals` cell can
disagree with the value recorded by the venue resolution. Such a cell must not remain eligible as
an ordinary calibration or validation label merely because it is internally complete. The epoch-16
candidate therefore adds a fail-closed training-truth boundary:

- an agreed exact settlement value is canonical for its station/date/kind cell;
- identical exact duplicates collapse to one training unit;
- conflicting exact values block the cell instead of being averaged, selected, or allowed to fall
  back to METAR;
- a resolved or attested market cell without current reconciliation is ineligible for fallback;
  and
- current reconciliation mismatches quarantine the corresponding `daily_actuals` cell from every
  truth consumer.

This is explicitly a **current-remediated truth view**, not a point-in-time replay of historical
reconciliation revisions. Reconciliation rows do not yet provide an append-only as-of revision
ledger from which an earlier remediation state can be reconstructed. Consequently, this release
does not rewrite sealed prospective predictions, formal Gamma outcome records, or their existing
evaluation hashes. The immutable Gamma outcome ledger remains the authority for the outcomes it
already sealed; the new truth view governs current and future calibration, challenger generation,
probability recomputation, accuracy diagnostics, and PIT health diagnostics.

Health now verifies reconciliation record hashes and blocks on any integrity failure. A current
clear mismatch is critical until a valid station-lifecycle state absorbs it; once the station is
disabled, actively cooling down, or otherwise selection-blocked by the valid lifecycle snapshot,
the issue is reported as the warning `resolution_clear_mismatch_contained`. Repeated polling does
not erase containment. Conflicting canonical exact cells are blocking and cannot silently re-enter
through the daily-actual fallback.

#### Reviewed plan, negative control, and activation

Candidate image
`sha256:d081e2e640eb5ad713954dbd87d0a3b3d8b1d05b1581017e2e48ff9941531843`
was admitted by reviewed plan 25, exact plan hash
`05bede4926514ba9248cbba549681c42b8c8e864eb2fad89f103f1edff4814ec`.
The lane actions were:

- `execution_evidence`: continue;
- `official_resolution_truth`: continue;
- `operational_legacy`: fork;
- `predictive_validation`: fork;
- `selection_policy`: continue; and
- `station_lifecycle`: continue.

The earlier preview plan 24 was a negative control: it exposed the stale image binding before
activation and was never activated. The Compose override and release environment were then bound
to the exact reviewed image, a new preview produced plan 25, and only that exact hash was activated
as epoch 16 at `2026-07-20T16:27:06.935419Z` under the kill switch. The current continuity state is
`collecting`: cohort and image match, and the evidence execution gate is allowed. This is the
intended fail-closed behavior: discovery of a stale candidate identity must create no epoch and
must grant no execution authority.

#### Cutover quarantine and verification

Audited command 17 (`truth-quarantine-stop-20260720T161940Z`) engaged the all-venue stop before the
candidate worker ran. The nonterminal PAPER execution state at this boundary contained one record;
the quarantine result was `paper=1`, `sealed=1`, `legacy=0`. The candidate API and worker were then
verified against the exact plan-25 digest while execution remained killed. No LIVE authority or
state was introduced.

Static and regression verification passed with 860 non-PostgreSQL tests passed and 3 skipped,
plus all 280 unique PostgreSQL integration tests. The production-HBA boundary check also passed;
the runtime role remained unable to cross the owner-only boundary. Ruff, strict mypy, and diff
checks were clean. These checks establish the implementation and evidence-integrity contract;
they do not claim an official exact-source value where none was collected, do not prove predictive
skill, and do not authorize LIVE.

### 20.12 Operational liveness recovery, E3 selection ceiling, and epoch 18 - 2026-07-28

#### Incident record and boundary semantics

Health audit 799 at `2026-07-28T04:35:43.882664Z` correctly reported
`evidence_collection_stalled`: seven full UTC days from July 21 through July 28 lacked the required
combined coverage from `execution`, `execution-markouts`, `execution-cost-finalization`, and
`settlement`. The outage remains in the append-only audit and job history. The recovery does not
delete those days, synthesize job runs, backfill evidence, or classify an operator stop as planned
maintenance. An operator stop blocks new entries; it does not suspend the required evidence jobs,
so using stop/resume intervals as denominator exclusions was explicitly rejected.

The reporting contract now separates two boundaries:

- the **formal prospective boundary** is the newest of the five prospective evidence-lane
  boundaries and continues to govern probability, signal, execution, enrollment, outcome, and
  formal denominator queries; and
- the **operational collection boundary** is the newer of that formal boundary and
  `operational_legacy.boundary_at`, and governs only scheduler liveness, full-day coverage, and
  ETA.

If either required boundary is missing, collection remains unopened fail-closed. An operational
fork can therefore start a truthful new observation window without laundering old stalled days or
resetting otherwise compatible formal evidence. A selection fork remains material and opens a new
selection-compatible formal boundary.

#### Reviewed plans 26-28

Plan 26, exact hash
`949d329b4d917b4586470e7aa0ec518b7d12b5627f6667f70f5a972ee43bbba1`, was a
negative-control preview and was never activated. Review found that the already implemented E3
ceiling was not explicitly bound into selection configuration identity. The manifest was corrected
to include `signal_max_edge`; `.env.example` and Compose now bind the production default
`SIGNAL_MAX_EDGE=0.20`.

Reviewed plan 27, exact hash
`86cc54e62ae8336a78d04a88567ec2bdc8f9453121d1bcdb65567e1651a0edca`, forked only
`operational_legacy` and `selection_policy` and continued the other four lanes. It activated image
`sha256:8c3b375e9fd45bf78371ba8e1c4ce3697285516cc267052275767883e06959b8` as
epoch 17 at `2026-07-28T05:09:09.930256Z`. The operational fork reset only collection/ETA; the
E3 selection fork intentionally reset the selection-compatible formal boundary. Because the
immutable epoch-17 approval text could be read as denying that latter consequence, epoch 17 was
superseded before worker execution. It admitted zero job runs, model probabilities, signals,
execution intents, enrollments, or outcomes.

Reviewed plan 28, exact hash
`babb63b834ef39f8d9090642a1c527fc11424daf0d814a77035e2d8bdf547c21`, continued
all six epoch-17 boundaries and activated the documentation-complete image
`sha256:de7d033f14ee5527473525ac8b31c5e8235edcf2ce7d667196fe0efa2d41942a` as
epoch 18 at `2026-07-28T05:11:57.435240Z`. Its epoch record hash is
`6df338cdb77a42ab82930690acb0ec7bb255874897818c9971124bac7d1fbf16`. The
selection and operational boundaries remain the PostgreSQL boundary opened by epoch 17 at
`2026-07-28T05:09:09.930256Z`; the other compatible formal boundaries retain their earlier
immutable cutovers.

#### Verification under kill and PAPER-only resume

The candidate worker completed startup under the Polymarket kill switch. It recovered all 13
attempted bounded observation gaps without quarantine, ran settlement and reconciliation, and
started all 27 scheduled jobs. Four incomplete station-days were isolated as data-quality
warnings. Before resume, required natural jobs had already completed repeatedly under kill:
`execution`, `execution-markouts`, and `execution-cost-finalization` each had at least two
successful recurring cycles, and settlement completed its natural `05:20 UTC` cron run.

Health audit 800 at `2026-07-28T05:22:26.350202Z` completed as `warning` with no critical finding.
Continuity matched epoch 18 exactly, the execution gate reason was
`evidence_continuity_match`, collection had zero eligible full days, zero stalled days, and no
missing required scheduler job. Audit 799 and its seven stalled historical days remain unchanged.
The new liveness status is honestly `waiting_for_due` with
`insufficient_full_days_7_required`; it is not a claim of recovered uptime or model quality.

Audited command 21 (`codex-polymarket-paper-20260728T052714Z`) first reaffirmed mode `paper` while
leaving the kill switch on. Audited command 22
(`codex-resume-polymarket-paper-20260728T052736Z`) then resumed only
`venue=polymarket` at `2026-07-28T05:27:39.862797Z`. Binance remained
`enabled + backtest + kill`. The first natural post-resume cycle evaluated 218 markets, admitted
15 pending signals, and posted 15 maker-simulated PAPER orders; it recorded zero fills. The next
cycle continued those 15 resting simulations without manufacturing a fill. At the acceptance
snapshot:

- `/healthz=200` and `/readyz=200`;
- API and worker use the exact epoch-18 image with restart count zero;
- Polymarket is `paper + kill=false`;
- Binance is `backtest + kill=true`;
- LIVE positions and nonterminal LIVE orders are zero;
- continuity is `collecting`, image/cohort match, and the execution gate is allowed; and
- the current lane has 15 execution intents, zero fills, zero cost seals, zero enrollments, and
  zero outcomes.

Fifteen earlier `paper_resting` rows are retained as immutable audit attempts whose corresponding
signals were expired with `skip_reason=kill_switch`; they are not current pending signals and
cannot be filled by the executor.

The startup shadow `censored-challenger` completed once and sealed 825 unapplied predictions. Its
scheduled `05:20 UTC` replay then failed closed because the replay used a new training cutoff for
the same sealed unique keys. The transaction rolled back and production probabilities were
unchanged. This shadow-only idempotency defect does not authorize or block PAPER execution, but it
remains a remediation item: a complete, hash-valid existing cohort should be recognized as an
idempotent replay, while partial or invalid cohorts must continue to fail closed.

Verification for the liveness/identity change included 864 non-PostgreSQL tests passed and 3
skipped, the full five-test evidence-continuity PostgreSQL file, the focused new PostgreSQL
boundary test, 117 targeted manifest/continuity/health tests after the config-identity correction,
clean scoped Ruff, strict mypy over 172 source files, and valid Compose configuration. These checks
prove release integrity and PAPER runtime behavior only. They do not prove accuracy, edge,
profitability, exact official-source coverage, or LIVE readiness.

### 20.13 Bounded evaluation and audit recovery, revision 0045 and epochs 20–21 — 2026-07-28

#### Fail-closed incident boundary

PAPER remained stopped while three production defects were investigated. First, a risk cap could
reduce a signal to a value that rounded to zero only after admission, allowing a zero-size pending
signal to reach execution. Second, point-in-time forecast evaluation could select and materialize
an unbounded eligible history. Third, the health audit called `.all()` over 430,628 eligible
accuracy-prediction rows from a table of approximately 2.3 GiB. The two unbounded reads could exceed
the worker's 3 GiB memory boundary. No LIVE state was introduced. Thirty-three old nonterminal
PAPER maker attempts were quarantined under the kill switch before the corrected worker received
execution authority.

The remediation is fail-closed at every writer boundary:

- signal caps use exact decimal arithmetic and round down to the `$0.0001` execution quantum before
  status assignment; a value below that quantum becomes
  `risk_capped_size_below_quantum`;
- the executor expires any legacy non-positive pending signal as
  `paper_order_size_nonpositive`;
- the execution-cost ledger rejects non-positive price, notional, or shares before persistence;
- forecast evaluation selects at most four point-in-time snapshots per market, excludes existing
  rows in SQL, limits each run to 1,000 rows, streams batches of 100, and never loads probability
  components it does not need;
- a complete and hash-valid sealed shadow cohort is an idempotent no-op, while a partial or invalid
  cohort remains a hard failure; and
- the health audit uses a bounded 180-day/watermark window, server-side streaming in batches of
  500, and row expunging. A current mismatch forces a bounded re-scan rather than an unbounded
  historical load.

Revision `20260728_0045` also makes the close-probability precision contract explicit. The source
snapshot remains 8-decimal evidence; `forecast_evaluations.pre_resolution_close` remains a
6-decimal value and must equal `round(source_close, 6)`. Upgrade, downgrade, re-upgrade, and a
PostgreSQL behavior check using `0.51234567 -> 0.512346` passed; an incorrectly rounded value was
rejected. This evaluation-semantic change belongs only to `predictive_validation`.

#### Epoch 20 predictive cutover

Reviewed plan 32, exact hash
`f189707f150007491c6c315c4fc9aa31cd3294efbe9472dfe95eadc7953c754f`,
activated exact image
`sha256:1cd483159137f7ec70a9929eaa733ba5d981d88800001cbb346ab41dfe189db8`
as epoch 20 at `2026-07-28T11:03:17.983841Z`. It forked only
`predictive_validation`; the other five lanes continued. The release-manifest, lane-manifest, and
epoch-record hashes were respectively:

- `69dd16fd7089eb5867fd8efc8e20d73e54c2e2ae0a8429abe32da3d335da9f6a`;
- `4ba1500768053f62c8f188ad3e28fa76b4011d8abfa0d82805f5735e85c36e33`;
- `fa88de73b9995cf268af26f32cf181eb31cecc130eadd5c43c45cbabd0b6e7d0`.

Under the kill switch, the bounded forecast-evaluation smoke selected and wrote 1,000 rows and
exited successfully without OOM. Repeating the already complete censored-challenger cohort wrote
zero predictions and exited successfully. Neither check changed production execution authority.

#### Epoch 21 operational cutover and PAPER-only resume

The final health-audit streaming change was reviewed as operational only. Plan 33, exact hash
`908e8b064e9a95b70d544e4477716fea76a76402054c69c7309b5d0937e9cd96`,
activated exact image
`sha256:f487b5a8bfa21d70e264032ba28e248f79e92928acd2158f544f0bc465f0fad4`
as epoch 21 at `2026-07-28T11:22:41.119811Z`. Only `operational_legacy` forked.
The release-manifest, lane-manifest, and epoch-record hashes are:

- `014f10c81bcd7060fb6e6901d374816e101c089d641f743cb981b49d2678043b`;
- `8c65b8786d809a9c9efc3e96dbf5efd897a81f9ce2a9249660c2f5ae4317a076`;
- `c12fa85a9b3e6598700d5fc31d0ae40c24cabb85b2e67dbd369a1112784734ac`.

The pre-resume audit scanned 14,553 rows with zero invalid records and no OOM. The post-resume audit
scanned 14,564 rows with zero invalid records at approximately 107–131 MiB observed memory. API
and worker used the exact epoch-21 image; the worker had a 3 GiB enforced limit, zero restarts, and
no OOM flag. Static verification was clean with Ruff, strict mypy over 172 source files, 869 unit
tests passed and 3 skipped, 283 unique PostgreSQL integration tests covered, valid Compose
configuration, and the focused precision-trigger regression.

Audited command 23 reaffirmed global `paper` mode. Audited command 24 resumed only
`venue=polymarket`; Binance remained `backtest + kill`. At acceptance, `/readyz=200`, continuity
matched epoch 21, the evidence execution gate reason was `evidence_continuity_match`, and LIVE
positions and orders were both zero.

The first natural post-resume signal cycle evaluated 212 markets, inserted 212 decisions, admitted
12 positive-size pending signals, and skipped 200. The executor posted 12 maker-simulated PAPER
orders. Their minimum signal size was `$1.0697`; all 12 intents had positive notional and shares,
valid epoch-21 continuity, and no fill was manufactured. Thirty-three zero-size rejected decisions
created no order and no intent. The following cycle admitted no new order and maintained the 12
resting simulations. By `2026-07-28T11:51:07Z`, later natural cycles had produced 15 total
epoch-21 intents: one eligible PAPER fill and 14 resting maker orders. Order 1549 filled exactly at
its requested `$0.05500000` price and `$1.069700` notional. Its immutable 5-minute markout used an
eligible snapshot observed 61 seconds after the target; its 30-minute mark remained prospectively
due. This is execution-evidence progress, not evidence of predictive skill.

That mark also exposed a scheduler race: `execution-markouts` and
`execution-cost-finalization` start at the same instant and both claim the same due obligation with
`FOR UPDATE SKIP LOCKED`. The finalizer won the row lock at `11:44`, `11:49`, and `11:50 UTC`;
the markout job won at `11:51 UTC` and then stored the correct pre-existing eligible snapshot.
Evidence was neither rewritten nor lost, but theoretical delay is unbounded. This is retained as a
P2 operational remediation: replace the competing claims with one ordered composite operation
that seals markouts before finalization. It does not require or authorize a trading-mode change.

A read-only 30-minute monitor now verifies API/PostgreSQL/worker health, exact image and epoch
identity, continuity gate state, Polymarket PAPER and Binance fail-closed modes, LIVE-row
invariants, job freshness, worker restarts/OOM, and non-positive execution values. It may alert or
report; it has no authority to switch mode, resume a venue, write evidence, or enable LIVE.

This acceptance proves bounded runtime behavior and continuity integrity only. It does not prove
accuracy, positive edge, profitability, exact official-source equivalence, or LIVE readiness.

### 20.14 P0 execution-markout starvation boundary — 2026-07-29

#### Append-only correction of the earlier severity assessment

Section 20.13 remains the immutable historical account of the epoch-21 observation, including its
then-current P2 classification. The 2026-07-29 root-cause review supersedes only that severity
assessment: the incident is P0 because the independent recurring `execution-markouts` and
`execution-cost-finalization` jobs had overlapping claim predicates and no finite fairness
guarantee.

Both paths could select the same `pending` or `waiting_for_marks` obligation when
`next_due_at <= observed_now` and then use `FOR UPDATE SKIP LOCKED`. The finalizer won the observed
row at `11:44`, `11:49`, and `11:50 UTC`; the markout path won at `11:51 UTC` and cited the correct
pre-existing eligible snapshot. That row was not rewritten or lost, but one eventual success does
not prove bounded liveness for the topology. In addition, absence of an eligible snapshot at the
first target poll advanced the next useful poll to grace expiry instead of polling new eligible
snapshots during grace.

No old epoch, lane, mark, seal, job run, or acceptance record is changed by this correction. Tests,
deployment, canary, and a successor epoch are not claimed here.

#### Material remediation identity

The required production scheduler has exactly one recurring
`execution-cost-maintenance` job at 15-second cadence. Each invocation captures one
PostgreSQL-authoritative clock value and uses it for two sequential short transactions:

1. Phase A claims only unresolved due mark work, appends observed or terminally missing marks, and
   commits.
2. Phase B starts only after that commit, claims only already-finalizable obligations, appends
   immutable cost seals, and commits independently.

The phases deliberately do not share one transaction. A Phase-B failure must not roll back a
valid Phase-A mark. Repository predicates must be disjoint in the same database state; row locking
remains a concurrency mechanism, not the ownership rule. The former standalone markout and
finalization entry points remain manual-only and must not be registered as recurring or startup
jobs.

During every pass inside a due mark's frozen grace window, Phase A searches the immutable CLOB
ledger for the deterministic first eligible snapshot in
`[target_at, target_at + mark_grace]`. Before grace expiry, absence leaves the mark unresolved and
pollable on the next 15-second pass. At or after expiry, continued absence appends the immutable
`missing` result. This changes evidence-collection behavior prospectively; it does not change the
historical snapshot ledger or authorize a synthetic/backfilled mark.

#### Required successor plan and activation order

The corrected claim ownership and grace polling are material to `execution_evidence`; that lane
must fork at a new PostgreSQL-clock zero-backfill boundary. The action for every other lane,
including `operational_legacy` and `selection_policy`, must be derived strictly from the complete
computed release-plan hash diff; this addendum does not predeclare fork or continue for them. The
reviewer rationale must separate the material execution-evidence semantic change from an
incidental hash change caused by a shared source file such as `runtime.py`. Any mismatched or
unclassified lane fails closed.

The required order is:

1. complete implementation review, static checks, focused tests, full non-PostgreSQL tests, and
   clean-PostgreSQL verification;
2. reconcile existing execution-cost obligations and preserve a hash-bound, read-only incident
   artifact;
3. preserve and restore-verify the pre-cutover database, and record the exact rollback image;
4. deploy or stage the exact candidate artifact under fail-closed controls while candidate
   scheduler writes remain blocked under the predecessor execution-evidence identity;
5. verify the deployed/staged artifact identity, generate and independently review the complete
   continuity plan, then activate exactly one successor epoch;
6. verify image/cohort/lane match before the candidate scheduler writes execution evidence; and
7. run a bounded PAPER-only canary. LIVE remains prohibited.

Candidate code running before activation must not write marks or seals under the epoch-21
execution-evidence identity. Existing evidence remains in its original lane; the new lane begins
at zero and receives only post-boundary candidate behavior.

At the time this section was appended, every release fact below remained pending:

| Release fact | Status |
|---|---|
| reviewed source revision and image digest | pending |
| test and clean-PostgreSQL evidence | pending |
| incident reconciliation artifact | pending |
| backup and isolated restore proof | pending |
| production deployment identity | pending |
| reviewed plan and successor epoch id/hash | pending |
| PAPER canary | pending |
| P0 closure | pending |

The detailed gate is `docs/PRODUCTION_READINESS_CHECKLIST.md`, and the incident record is
`docs/incidents/2026-07-29_EXECUTION_MARKOUT_STARVATION.md`. Neither document grants execution or
LIVE authority.
