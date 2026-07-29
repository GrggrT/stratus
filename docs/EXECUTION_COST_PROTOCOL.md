# Execution Cost and Post-Fill Markout Protocol

Status: implemented and production-deployed under revision `20260715_0038`. The protocol remains
prospective, zero-backfill evidence infrastructure; deployment does not establish trading edge or
live readiness. The 2026-07-29 P0 scheduler remediation is specified but not production-accepted:
verification, deployment, canary, and successor evidence epoch are pending. See
`docs/incidents/2026-07-29_EXECUTION_MARKOUT_STARVATION.md`.

### Production implementation record — 2026-07-15

- Production schema is at the single Alembic head `20260715_0038`.
- API and worker run image
  `sha256:f64718d1c503acd9db7eeb4b8dfd7d82464e9a3ec977c7e9c99d82d504cd30f5`.
- The guarded pre-migration backup is
  `backups/stratus_pre_0038_20260715t0615z.dump`, SHA-256
  `3F9106F9D1FFC511CE941C55E6A79EDA164E21B1E31F952079AD337C54230AF5`.
- Release validation passed 711 tests, Ruff, strict mypy, and Docker Compose validation.
- The first genuine post-boundary PAPER sample contains two attempts. Both terminated without a
  fill and both received immutable seals; the report therefore correctly returns
  `evidence_incomplete`. These rows prove intent, obligation, terminal no-fill, and sealing
  plumbing, but provide zero eligible fill-cost or post-fill-markout observations.

## 1. Objective

Revision 0038 must let STRATUS answer, for every new execution attempt:

- what the model and independently observed purchased-token book showed when the order intent was
  committed;
- which immutable book evidence was used to simulate or reconcile every fill leg;
- whether each leg was maker, taker, or genuinely unknown;
- how much edge was consumed by book movement, spread, slippage, and fees;
- whether the purchased token moved against the fill after 5 and 30 minutes;
- which claims are exact, which are simulated, and which remain unavailable.

The ledger is evidence infrastructure. It does not retroactively make the paper simulator a venue,
does not turn an SDK object into raw HTTP evidence, and does not prove trading edge.

## 2. Frozen cohort and protocol identities

Migration `20260715_0038` has `20260715_0037` as its sole parent. It created a singleton
`execution_cost_runtime` row using PostgreSQL `clock_timestamp()` and performed zero backfill.

The frozen definition is:

| Field | Value |
|---|---|
| definition version | `execution-cost-v1` |
| paper execution evidence | `paper-clob-cross-v1` |
| live reconciliation evidence | `live-sdk-reconciliation-v1` |
| post-fill mark protocol | `clob-dual-postfill-v1` |
| mark horizons | `300`, `1800` seconds |
| mark grace | `300` seconds after each target |
| live fill-snapshot maximum age | `120` seconds |

The runtime row stores the values above plus their immutable definition SHA-256. Application
constants and the migration fixture must produce the same hash.

Only an order whose intent is database-witnessed at or after `execution_cost_runtime.opened_at`
belongs to this cohort. Existing orders, fills, positions, CLV, and revision-0035 validation rows
remain cost decomposition `unknown`. No migration, job, API, or report may synthesize 0038 rows from
them.

Revision 0038 is deliberately separate from `prospective_validation_*`. Revision 0035 freezes
trade/outcome eligibility; revision 0038 freezes execution evidence. Neither ledger rewrites or
reinterprets the other.

## 3. Side and time semantics

### 3.1 Purchased-token coordinates

All prices and costs are expressed in the token actually bought:

- for `buy_yes`, `p = signal.model_prob` and the YES bid/ask/midpoint are used;
- for `buy_no`, `p = 1 - signal.model_prob` and the independently observed NO bid/ask/midpoint are
  used;
- a YES complement is never substituted for a missing NO book;
- all probabilities and token prices must be strictly between zero and one.

Every intent stores both the original YES probability and the transformed `side_model_probability`
so later code cannot silently change the convention.

Exact event-probability materialization, candidate admission, and full-market coherence are
separate upstream assertions. Materialization proves that the cited immutable event vector,
timestamp, pipeline identity, bucket geometry, and per-bucket final probabilities are exactly the
values persisted by the probability job. `candidate-anchor-v2` then requires the exact complete,
eligible dual-token snapshot bound to the one token being considered; it never borrows an older
candidate or sibling anchor. Full-market coherence remains stricter still: it requires executable
anchors for every sibling in one CLOB batch and is used where the whole market vector is consumed,
including the market Brier benchmark. A materialized vector can therefore be valid accuracy
evidence, and a candidate can be valid execution evidence, while the full-market benchmark remains
unavailable. None of these assertions may be substituted for another in an intent, report, health
result, or live-readiness decision. The canonical admission contract is
`CANDIDATE_ANCHOR_ADMISSION_PROTOCOL.md`.

### 3.2 Clocks

The following clocks are distinct and must never be aliased:

1. `decision_at` is the immutable signal/model decision time.
2. `intent_at` is the PostgreSQL witness that the executable intent was committed before a new
   execution request or live submission.
3. CLOB batch `request_started_at`, `response_received_at`, and `first_seen_at` retain their existing
   revision-0031 meanings.
4. `venue_filled_at` is populated only from a verified live provider field. A polling time, local
   loop `now`, order terminal time, or placement acknowledgement is not a venue fill time.
5. `fill_effective_at` is the time from which formal mark horizons are measured. For paper it is
   the cited execution batch's database `first_seen_at`. For live it equals verified
   `venue_filled_at`; otherwise it is null and the leg is ineligible.
6. `first_seen_at` on 0038 evidence is always PostgreSQL-authoritative.

Paper code must stop assigning `filled_at` before the book request. A maker paper fill occurs when
the first persisted crossing book is observed, not when the polling loop began.

## 4. Data contract

All 0038 evidence hashes use SHA-256 lower-case hexadecimal, include the definition hash, and are
recomputed by PostgreSQL. JSON hashes use the repository's frozen PostgreSQL-canonical JSONB text
contract. Evidence rows are append-only; operational obligations have only the explicit state
transitions described below. Hash-bearing source rows cannot be updated or deleted.

### 4.1 `execution_cost_runtime`

One immutable row contains:

- `id = 1`, `opened_at`, `definition_version`, and `definition_hash`;
- ordered `mark_horizons_seconds = [300, 1800]`;
- `mark_grace_seconds = 300`;
- `live_fill_snapshot_max_age_seconds = 120`.

Downgrade must refuse once any 0038 intent, observation, fill leg, mark, obligation, or seal exists.

### 4.2 `execution_order_intents`

Exactly one immutable intent is required per post-boundary order. It is committed before paper
execution-book collection or live signing/submission and records:

- unique `order_id`, plus `signal_id`, `model_prob_id`, `market_id`, mode, side, and token id;
- pipeline cohort and the signal/model record hashes calculated with the frozen validation-ledger
  hash helpers;
- decision CLOB snapshot id and record hash;
- original YES model probability and purchased-side model probability `p`;
- purchased-side bid, ask, and midpoint `m0` copied exactly from the decision snapshot;
- `order_style` (`maker` or `taker`), requested limit price, shares, and notional;
- expected fee USD, or null plus an explicit unavailable reason;
- expected slippage per share, or null plus an explicit unavailable reason;
- `decision_at`, database `intent_at`, definition identity, and record hash.

The database validates order/signal/market/side/token consistency, exact side projection from the
decision snapshot, snapshot hash, `decision_at <= intent_at`, and positive requested size. A
post-boundary order must not become executable unless its intent has committed.

Expected slippage is a pre-trade model prediction, not observed slippage. Until a versioned model
exists it must be null with `expected_slippage_not_modeled`; zero is forbidden as a placeholder.

### 4.3 `execution_reconciliation_observations`

This append-only table preserves each live SDK observation used by reconciliation:

- order id and observation kind (`placement`, `order`, `trades`, or `cancel`);
- SDK method/parser version;
- request start and response receive times;
- normalized provider payload as JSONB, payload SHA-256, database first-seen, and record hash.

This table must be labelled **normalized SDK evidence**, not raw HTTP. If the SDK does not expose
exact response bytes, URL, or HTTP headers, STRATUS must not claim to possess them. Repeated polling
payloads may be deduplicated only by an explicit unique content identity; a changed cumulative fill
is a new observation.

### 4.4 `execution_fill_legs`

One row represents one atomic purchased-token fill leg. Required fields are:

- intent, order, signal, market, mode, side, and token identity;
- `source_kind` (`paper_simulation` or `live_sdk`);
- nullable reconciliation-observation id and nullable fill CLOB snapshot id/hash;
- stable `external_trade_id` when provided and a unique per-order `external_leg_key`;
- deterministic leg sequence;
- liquidity role (`maker`, `taker`, or `unknown`);
- shares, price, notional, nullable fee USD, and fee semantics;
- nullable `venue_filled_at`, database `first_seen_at`, nullable `fill_effective_at`, and explicit
  timestamp semantics;
- purchased-side fill bid, ask `a_j`, and midpoint `m_j` when exact book evidence exists;
- `eligible`, one stable `eligibility_reason`, definition identity, source hash, and record hash.

For paper taker execution, each consumed ask level is a separate leg; an aggregate VWAP alone is
insufficient. The leg cites the newly persisted raw CLOB batch/snapshot used by the book walk. For
paper maker execution, one conservative synthetic leg is recorded at the resting limit only when a
new persisted book offers at or below that limit with the required full depth. Its role is `maker`
and its source remains explicitly `paper_simulation`.

For live execution, one order may contain both maker and taker legs. Role therefore belongs to the
leg, never only to the order. A stable provider trade/leg identity is required to make cumulative
polls idempotent. The current fallback from matched shares to the order limit price is not eligible
evidence and must not produce an eligible fill leg.

Fee absence means null, not zero. A leg cannot be eligible when its fee, role, exact price/size,
verified venue time, stable external identity, or sufficiently close independent fill snapshot is
unavailable. Initial live rows remain ineligible with a precise reason such as
`live_schema_unverified`, `venue_fill_time_missing`, `liquidity_role_unknown`,
`fee_unknown`, or `fill_snapshot_missing` until real provider payloads and parser fixtures prove
the contract.

For a verified live leg, the fill snapshot is selected without hindsight: choose the latest
eligible immutable snapshot for the same market whose batch `first_seen_at` is in
`[venue_filled_at - 120 seconds, venue_filled_at]`, ordered by batch first-seen descending and id
descending. It must contain the exact purchased token and a two-sided book. A snapshot first seen
after the venue fill cannot be used for fill-cost decomposition; it belongs only to the post-fill
mark stream. No qualifying snapshot makes the leg ineligible with `fill_snapshot_missing`.

### 4.5 `execution_cost_obligations`

Every post-boundary order receives exactly one obligation when it first becomes terminal. The
terminal order transition and obligation creation occur in the same database transaction. The row
contains order/intent identity, terminal status, creation time, next due time, state, finalized
time, and stable failure reason.

Allowed state transitions are:

```text
pending -> waiting_for_marks -> sealed
pending -> sealed
waiting_for_marks -> sealed
```

`sealed` is terminal. An unfilled or expired order still receives an obligation and an ineligible
seal with `no_fill`; this is necessary for honest attempt and maker fill-rate reporting. A terminal
post-boundary order without an obligation is an integrity failure.

### 4.6 `execution_postfill_marks`

There is exactly one row per fill leg and frozen horizon. It records:

- leg and horizon, `target_at = fill_effective_at + horizon`;
- status `observed` or `missing`;
- selected CLOB snapshot id/hash or null plus a stable missing reason;
- purchased-side bid, ask, midpoint, actual observation time, offset from target, sealed time, and
  record hash.

The selector uses the first eligible immutable snapshot for the same market whose batch
`first_seen_at` is in `[target_at, target_at + 300 seconds]`, ordered by batch first-seen and id. It
must use the leg's exact purchased token. Once the grace interval closes without such a snapshot,
the mark is sealed `missing`; a later price cannot replace it. Empty, one-sided, wrong-token, or
ineligible snapshots do not qualify.

### 4.7 `execution_cost_seals`

Exactly one immutable seal summarizes every terminal post-boundary order after all required marks
are observed or explicitly missing. It contains:

- order/intent identity, eligibility, and one stable primary reason plus optional reason detail;
- aggregate role (`maker`, `taker`, `mixed`, or `unknown`) and leg count;
- total shares `Q`, filled notional, VWAP fill price `F`, total fee, and fee per share;
- intent midpoint `m0`, side model probability `p`, and weighted fill midpoint;
- gross model edge, latency movement, taker spread, taker slippage, maker improvement, fee drag,
  and realized net edge per share;
- expected-slippage value and coverage state, without substituting zero;
- 5m/30m markout values and coverage states;
- definition identity, sealed time, and record hash.

An eligible seal requires all filled legs to be eligible, exact aggregate reconciliation, and a
known fee for every leg. Mark absence does not rewrite execution eligibility, but the corresponding
markout metric remains null and its coverage is `missing`. Ineligible seals retain counts and
reasons while all unsupported cost metrics remain null.

## 5. Frozen cost algebra

For eligible legs `j`, let:

- `q_j` be shares and `Q = sum(q_j)`;
- `f_j` be fill price and `F = sum(q_j * f_j) / Q`;
- `m0` be the purchased-side intent midpoint;
- `m_j` and `a_j` be purchased-side midpoint and best ask at that leg's fill snapshot;
- `fee` be the sum of exact leg fees;
- `T` be taker legs and `M` be maker legs.

All values below are per share:

```text
gross_model_edge = p - m0
latency_movement = sum(q_j * (m_j - m0)) / Q
taker_spread = sum(j in T, q_j * (a_j - m_j)) / Q
taker_slippage = sum(j in T, q_j * (f_j - a_j)) / Q
maker_improvement = sum(j in M, q_j * (m_j - f_j)) / Q
fee_drag = fee / Q

realized_net_edge = gross_model_edge
                  - latency_movement
                  - taker_spread
                  - taker_slippage
                  + maker_improvement
                  - fee_drag
                  = p - F - fee_drag
```

The equality is a database- and unit-tested invariant within the fixed numeric tolerance. No term
is clipped to zero: negative maker improvement or favorable negative latency/slippage is retained
as observed. Mixed-role orders use the same leg-weighted identity.

If `c` is an immutable purchased-side pre-resolution close midpoint from the prospective
validation ledger, reports may derive:

```text
CLV = c - F
net_CLV = c - F - fee_drag
```

These values are joined at report time after resolution; the execution seal is not mutated.

For post-fill horizon `h`, let `post_mid_j,h` be the purchased-side mark midpoint. When every leg
has a valid mark:

```text
adverse_selection_h = sum(q_j * (m_j - post_mid_j,h)) / Q
```

A positive value means the purchased token moved against the fill after execution. A missing leg
mark makes the order-level horizon value null; reports expose coverage rather than averaging an
unlabelled partial subset.

Expected slippage is reported separately from observed `taker_slippage` and is never inserted into
the realized identity.

## 6. Paper execution flow

Paper execution must be refactored into the following order:

1. Re-read runtime control and risk capacity under the existing authoritative lock.
2. Create the `orders` row and immutable intent from the signal's cited dual-token decision
   snapshot; commit before any new execution-book request.
3. Fetch through the existing raw CLOB evidence path, persisting `clob_book_batches` and
   `clob_market_snapshots`; direct non-persisted `fetch_books` output is not execution evidence.
4. Taker mode walks exact purchased-token ask levels and stores one leg per consumed level. Maker
   mode posts the resting order; each later poll also persists a new batch, and only the first
   qualifying crossing batch may create the fill leg.
5. Terminal order state, position effects, and cost obligation are committed atomically. A retry
   finds the same intent/leg identities and cannot duplicate a fill.

`filled_at` on the legacy order/position remains a compatibility field, but for new paper rows it
must equal the authoritative paper `fill_effective_at`, never the pre-request loop time.

This ledger measures the behavior of the declared paper simulator. It must be labelled simulated
execution and must not be pooled with eligible live venue fills without a mode dimension.

## 7. Live reconciliation flow

Before signing/submission, live execution commits an intent. Every placement, order, trade, and
cancel SDK result subsequently used by reconciliation is preserved as a normalized observation.

The reconciler must:

- parse atomic trade legs and deduplicate them by stable provider identity;
- preserve maker/taker role per leg;
- accept monotonically increasing cumulative fills but derive totals from unique legs;
- never infer a fee of zero, venue timestamp from polling time, or fill price from the limit;
- create an explicit ineligible leg/seal when a terminal matched order lacks verifiable trade
  evidence;
- keep unknown evidence visible for operator review rather than silently dropping the order.

Live eligibility stays closed until captured provider examples prove field names, units, trade id,
role, fee, and venue timestamp, and those examples become sanitized regression fixtures. This
engineering work does not authorize live mode or bypass legal, tax, KYC, wallet, or quantitative
gates.

## 8. Scheduled jobs and health

The production scheduler must register one idempotent composite job,
`execution-cost-maintenance`, every 15 seconds. It replaces the competing recurring
`execution-markouts` and `execution-cost-finalization` topology identified in the 2026-07-29 P0
incident.

### 8.1 One clock and two committed phases

Each composite invocation captures one PostgreSQL-authoritative clock value. Both phases receive
that exact value, but they use separate short transactions:

1. **Phase A — markouts:** claim unresolved applicable mark work, append observed or terminally
   missing marks, and commit.
2. **Phase B — finalization:** after Phase A commits, open a new transaction, claim only
   finalizable obligations, append immutable cost seals, and commit.

The phase boundary is intentional. A Phase-B exception rolls back finalization without rolling
back valid Phase-A marks. Retrying either phase remains safe through repository idempotency,
append-only triggers, and the unique mark/seal identities.

### 8.2 Disjoint claim ownership

The markout and finalizer claim predicates must be disjoint in the same database state:

- Phase A may claim only an obligation with at least one unresolved applicable
  `(fill_leg_id, horizon_seconds)` mark whose target is due at the captured clock.
- Phase B may claim only an obligation with no unresolved applicable marks, or one whose
  execution evidence makes post-fill marks not applicable.

`FOR UPDATE SKIP LOCKED` prevents competing writes but is not a fairness or ownership guarantee.
Correctness comes from the disjoint predicates and ordered phase commits. The same predicates
apply to scheduled, retry, concurrent, and manual paths.

### 8.3 Markout polling within grace

For each due unresolved mark, Phase A chooses the first eligible persisted snapshot in the
inclusive frozen interval `[target_at, target_at + mark_grace]`.

- If the first eligible snapshot exists, seal it deterministically.
- If no eligible snapshot exists and the captured clock is before grace expiry, leave the mark
  unresolved and set its next-due clock to the captured clock. This makes it immediately eligible
  for the next composite invocation instead of adding another polling interval to the stored due
  time.
- If no eligible snapshot exists at or after grace expiry, append one immutable `missing` mark.

The scheduler cadence is nominally 15 seconds. While a mark is inside grace, the operational SLO is
that the next successful composite scan begins no more than 20 seconds after the preceding scan.
The polling cadence and SLO do not change the target, grace, source eligibility, or first-snapshot
selection rules. They remove both the former wait until grace expiry and the accidental near-30
second delay caused by storing `observed_now + 15 seconds` just after a scheduler tick.

### 8.4 Manual-only legacy entry points

`seal_execution_markouts` and `finalize_execution_cost_obligations` remain available for explicit
operator recovery and focused tests. They are manual-only and must not appear in recurring or
startup scheduler registration. Manual use does not bypass disjoint claim ownership, immutable
uniqueness, continuity, or audit requirements.

### 8.5 Health and observability

Health must report separately:

- composite-job freshness and the captured database clock;
- every scheduler invocation skipped because of `EVENT_JOB_MAX_INSTANCES`, durably recorded under
  its authoritative scheduled time with status `max_instances`;
- Phase-A claimed, observed, missing, and deferred counts;
- Phase-B claimed, sealed, and deferred counts;
- oldest unresolved target age and whether it remains within grace;
- post-boundary executable order without intent;
- terminal order without obligation;
- overdue obligation or mark;
- eligible leg with missing/invalid source evidence;
- reconciliation mismatch or duplicate external leg key;
- live schema/fee/role/time evidence still unverified;
- eligible-seal rate and 5m/30m mark coverage.

Paper collection continues for ordinary missing markouts, but invariant violations are
`critical`. Any live execution-evidence invariant is fail-closed.

Health must require `execution-cost-maintenance`; absence of a recurring manual-only legacy job is
not a freshness failure. A recently successful composite run is not sufficient when an
unexplained mark remains unresolved after target plus grace.

`EVENT_JOB_MAX_INSTANCES` is not evidence that a job ran. Every scheduled time carried by that
APScheduler event must produce one idempotent `job_runs` row with status `max_instances`, must
enqueue a deduplicated operator alert, and must count as non-success for scheduler uptime and
evidence continuity. No listener may replace it with a synthetic success or phase outcome.

No retention job may delete 0038 evidence or the cited raw CLOB batches/snapshots. Storage-capacity
reporting must include all new relations and their dependent raw evidence.

## 9. Operator API and Cockpit

`GET /api/v1/execution-cost-report?mode=paper|live` returns:

- cohort boundary and frozen definition;
- attempt, filled, no-fill, sealed, eligible, and ineligible counts;
- maker/taker/mixed counts and maker fill rate;
- expected-slippage and exact-fee coverage;
- share-weighted gross edge, latency movement, spread, slippage, maker improvement, fee drag, and
  realized net edge;
- 5m/30m adverse-selection averages and coverage;
- the same metrics by station, kind, forecast horizon, confidence, side, liquidity role, and entry
  price band;
- stable ineligibility-reason counts.

`GET /api/v1/execution-cost-report/orders` provides paginated drill-down with filters for mode,
eligibility, role, station, kind, horizon, side, and reason. A single-order response includes intent,
legs, source references, marks, seal, and the arithmetic identity.

Unsupported metrics are JSON `null`, never `0`. Every aggregate includes its eligible numerator
and relevant denominator. Paper and live are separate cohorts by default; an explicit `mode` is
required.

Cockpit should render an **Execution quality** card with cost waterfall, maker fill rate, evidence
coverage, and 5m/30m adverse selection. Drill-down must link source row ids and explain every null or
ineligible result in plain language.

## 10. Relationship to live readiness

Revision 0038 must not silently add or relax a formal live-gate criterion. The current eleven
checks remain unchanged while the ledger accumulates its first genuine sample.

The API may expose a shadow execution-evidence readiness result. Promotion to a twelfth gate check
requires a separate reviewed protocol after at least 100 eligible paper seals, sufficient markout
coverage, and frozen thresholds chosen prospectively. Historical or ineligible rows cannot satisfy
that future check.

## 11. Required tests

### Unit tests

- YES/NO purchased-side projection uses independent token books;
- intent, fill, expected-slippage, and mark time semantics;
- taker level walking returns deterministic atomic legs;
- maker crossing, depth, expiration, and retry identity;
- maker, taker, and mixed-role algebra, including the net-edge equality;
- fee null is distinct from zero;
- post-fill mark selection, grace boundary, missing mark, and adverse-selection sign;
- live payload normalization, leg deduplication, partial fills, and unknown-field fail-closed paths;
- stable hashes and definition identity.

### Clean PostgreSQL integration tests

- migration has one parent/head, opens one database-clock boundary, and performs zero backfill;
- direct writers cannot violate side/token/snapshot/time/hash contracts;
- source evidence, legs, marks, and seals are append-only;
- intent commits before execution request evidence;
- one intent and obligation per order, one external leg identity, one mark per leg/horizon, and one
  seal per order under concurrent retries;
- paper taker and maker flows cite raw immutable batches and exact snapshots;
- terminal/no-fill, partial, mixed-role, missing-fee, missing-time, and unverified-live cases;
- first-after-target mark selection and irreversible missing marks;
- aggregate totals reconcile to order/position values and satisfy the algebraic identity;
- existing revision-0035 enrollments remain unchanged;
- guarded downgrade refuses when any 0038 state exists;
- report/drill-down contracts preserve nulls, denominators, and mode separation.

### End-to-end verification

- exactly one recurring `execution-cost-maintenance` job is registered at 15-second cadence,
  remains idempotent across restart, and the two legacy jobs are manual-only;
- repeated `EVENT_JOB_MAX_INSTANCES` delivery creates one durable non-success row and one
  deduplicated alert per authoritative scheduled time;
- one database clock is shared by two sequential transactions, and forced Phase-B failure cannot
  roll back Phase-A marks;
- concurrent repository tests prove the markout and finalizer claim predicates are disjoint;
- a snapshot arriving after target but before grace is selected after the obligation is made
  immediately eligible for the next composite poll; consecutive successful within-grace scans
  satisfy the nominal 15-second cadence and the maximum 20-second operational SLO, while `missing`
  is sealed only after the full grace interval;
- a genuine post-boundary paper maker or taker attempt creates intent, persisted execution book,
  terminal obligation, legs/no-fill seal, and eventual 5m/30m marks;
- API totals match direct SQL counts and Cockpit explains incomplete evidence;
- API and worker produce no new error, critical, or traceback logs;
- WinUI x64 build succeeds and the packaged `STRATUS Operator Console` opens a real responsive
  window if the Cockpit surface changes.

## 12. Release acceptance

Before production migration:

1. Ruff, strict mypy, all non-PostgreSQL tests, and the complete clean-PostgreSQL suite pass.
2. Alembic has one head and clean upgrade through 0038 succeeds.
3. A full production backup, verified restore drill, exact rollback image, and guarded downgrade
   result are recorded.
4. Candidate image imports and runtime startup jobs pass before retagging production.
5. Production remains PAPER with kill switch/risk authority unchanged.

After migration:

1. The new cohort begins at zero; this is required and is not treated as missing data.
2. API/worker run the same candidate image and schema reports `20260715_0038`.
3. The first genuine post-boundary attempt proves the paper source chain without synthetic
   production rows. If no trade is naturally due during acceptance, zero remains the honest result.
4. Every new terminal order has one obligation/seal, and every supported metric reconciles exactly.
5. Live rows remain explicitly ineligible until verified payload evidence exists.
6. The eleven-part live gate remains fail-closed; execution-cost deployment is not evidence of
   profitability or permission to trade live.

Production acceptance completed on 2026-07-15 with all pre- and post-migration conditions above.
The two observed PAPER attempts were sealed as `no_fill`, so the runtime state
`evidence_incomplete` is the only honest result: the protocol plumbing is deployed and auditable,
while eligible execution-cost coverage, markout coverage, predictive edge, and live readiness all
remain unproven.

## 13. 2026-07-29 P0 release boundary

The composite scheduler changes execution-evidence claim ownership and grace-window polling
semantics. It therefore requires a reviewed zero-backfill fork of `execution_evidence`. The
action for every other continuity lane, including `operational_legacy` and `selection_policy`,
must be derived from the complete computed release-plan hash diff; this protocol does not
predeclare fork or continue for them. Reviewer rationale must distinguish the material
execution-evidence semantic change from an incidental hash change caused by a shared source file
such as `runtime.py`. Any mismatch or unclassified change fails closed.

The successor epoch must be activated after required test verification and exact candidate
deployment/staging identity verification, but before the candidate scheduler writes execution
evidence or PAPER execution resumes. Existing rows remain in their original epochs and must not be
rewritten, copied, or backfilled.

Production readiness is governed by `docs/PRODUCTION_READINESS_CHECKLIST.md`. At the time of this
protocol update, implementation review, tests, production reconciliation, backup/restore,
deployment, epoch activation, and PAPER canary are all pending. This section records a required
cutover contract; it is not evidence that the cutover occurred.
