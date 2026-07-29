# STRATUS prospective validation protocol

Status: **deployed and release-verified on 2026-07-14; currently collecting prospective
evidence**. The operator-database snapshot recorded on 2026-07-15 contains **6 enrollments,
5 eligible enrollments, and 0 sealed outcomes**. The formal live gate now has eleven checks.
These counts are an operational snapshot, not evidence of predictive skill.

Implementation revision: `20260714_0035` (`20260714_0034 -> 20260714_0035`). This
revision is applied to the operator database; production subsequently advanced through revisions
`20260714_0036`, `20260715_0037`, and `20260715_0038`. Verification covers the zero-baseline
protocol, database attestation, deployment, and API/UI projection. The later enrollments show that
collection has started; no outcome has yet been sealed and no predictive-skill claim follows.

## Purpose and authority

This protocol creates a zero-backfill, order-level ledger for evaluating realised performance of
one immutable probability-pipeline cohort. It is diagnostic evidence only: it does not change
probabilities, signal eligibility, sizing, execution, settlement, risk limits, or the live gate.

The ledger uses one terminal order fill as its atomic trade unit. That is intentional: a paper
`Position` can aggregate fills from different pipeline cohorts, so assigning its complete P&L to
one cohort after resolution would be invalid. Operational position reports remain useful, but
formal prospective counts and returns come only from the tables described below.

The source chain is:

`Order -> Signal -> ModelProb -> ClobMarketSnapshot -> GammaOutcomeEvent`.

Eligible evidence requires the side-explicit `clob-dual-v1` benchmark and an immutable Gamma
outcome vector. No NO price is reconstructed as `1 - YES`, and no historical position is copied
into the prospective ledger.

## Actual schema

### `prospective_validation_runtime`

A singleton row (`id = 1`) is inserted by migration `0035` with:

- `opened_at`: PostgreSQL `clock_timestamp()`; the prospective registration boundary;
- `definition_version`: `prospective-validation-v1`;
- `definition_hash`: SHA-256 of the frozen protocol definition.

There is no separate protocol-registry table. The runtime singleton is the authoritative
boundary and definition record.

### `prospective_validation_enrollments`

One append-only row is admitted per post-boundary terminal order (`order_id` is unique). The row
freezes:

- order, signal, model probability, market, and CLOB snapshot identifiers;
- execution mode, exact pipeline cohort, station ICAO, local market date, weather kind, side,
  and token;
- fill price, filled notional, fee, derived shares, `filled_at`, and database-clock
  `enrolled_at`;
- benchmark and definition identity;
- canonical hashes of the order, signal, model, snapshot, and enrollment record;
- `eligible` plus one stable `eligibility_reason`.

Ineligible attempts are retained as immutable completeness evidence when they have enough source
identity to be recorded. Current reasons include `outcome_already_known`, `non_dual_protocol`,
`snapshot_ineligible`, `model_probability_missing`, `pipeline_cohort_missing`,
`model_anchor_ambiguous`, `station_missing`, and `token_mismatch`. Excluded rows never enter
formal metrics.

### `prospective_validation_outcomes`

One append-only row is admitted per eligible enrollment (`enrollment_id` is unique). It freezes:

- the immutable Gamma outcome event, Gamma record hash, and outcome-vector hash;
- the resolved YES value, side-adjusted win flag, market close time, and recognition time;
- realised order P&L;
- when an eligible close snapshot exists: snapshot identity/hash, side-specific close midpoint,
  CLV, skill P&L, and luck P&L;
- a canonical outcome record hash.

Realised P&L remains valid when no eligible close snapshot exists. In that case the close, CLV,
skill, and luck fields are all null rather than fabricated.

## Zero-backfill and enrollment lifecycle

Migration `0035` creates the runtime and empty ledger tables. It does not select from or replay
existing orders, positions, signals, or Gamma outcomes. A fill with `filled_at < opened_at` is
outside the protocol permanently.

Enrollment is an **application hook**, not a database trigger that invents the row. The paper and
live execution repositories call `enroll_terminal_order(...)` in the same transaction after an
order reaches `filled` or `partially_filled` with positive filled notional. The hook:

1. locks the order;
2. obtains the same event-scoped PostgreSQL advisory lock used by Gamma outcome projection;
3. refreshes resolution state after the lock, making the fill-vs-outcome race deterministic;
4. selects the latest anchored model row while requiring all matching candidates to identify one
   cohort;
5. derives live fill economics from its one-to-one `Position`, or paper economics from the
   `Order`;
6. computes source and record hashes and inserts with conflict protection on `order_id`.

A deferred constraint trigger on `orders` is the completeness guard. At transaction commit it
rejects every post-boundary positive terminal fill that lacks an enrollment. This lets the
application persist the order and enrollment in either statement order while preventing a caller
from bypassing the hook.

If Gamma evidence wins the event lock first, the enrollment is permanently marked
`outcome_already_known`; it cannot be retrospectively promoted into the sample.

## Outcome sealing and close benchmark

Settlement calls `seal_prospective_validation_outcomes(...)` after a valid Gamma outcome event
has been projected onto its markets. The function verifies the immutable Gamma record, selects
eligible unresolved enrollments for that event, derives the side-adjusted result and payout, and
inserts each outcome idempotently.

For CLV and skill/luck decomposition, the close benchmark is the latest eligible, side-specific
CLOB midpoint whose batch satisfies the closed interval:

`enrollment.filled_at <= batch.first_seen_at <= market_closed_at`.

Snapshots observed before the fill are never used as a close benchmark. This prevents a
pre-entry quote from masquerading as closing-line evidence. If the interval contains no eligible
snapshot, decomposition is explicitly unavailable for that order.

## Database attestation and immutability

The application constructs the rows; PostgreSQL independently validates them before insert.

The enrollment validator re-reads the referenced order/signal/market/station/snapshot/model chain
and rejects mismatched identity, cohort, terminal status, fill arithmetic, boundary timestamps,
source hashes, eligibility classification, or record hash. It enforces
`opened_at <= filled_at <= enrolled_at`.

The outcome validator re-reads the eligible enrollment, exact Gamma vector item, market
projection, and eligible close snapshot. It independently checks:

- Gamma/vector/market identity and the resolved YES value;
- win and P&L arithmetic;
- `recognized_at` not preceding Gamma first-seen time or market close;
- the close window bounded by fill and market close;
- close price, CLV, skill/luck arithmetic, and the outcome record hash.

Runtime, enrollment, and outcome rows reject every `UPDATE` and `DELETE`. Foreign keys use
`ON DELETE RESTRICT`, hashes have a lowercase SHA-256 shape, and uniqueness prevents duplicate
enrollments or outcomes. Downgrade is refused after the first enrollment or outcome exists.

These checks attest consistency with the referenced database evidence. They do not prove that an
external venue or weather source was itself correct.

## Cohort and sample semantics

Every formal request selects exactly one execution mode and one pipeline cohort. The default
cohort is the current `clob-dual-v1` probability-pipeline cohort. Supplying `cohort=<exact value>`
selects that frozen cohort explicitly; no fallback relabels old evidence as current.

Excluded rows without a cohort are reported as completeness diagnostics for the selected mode,
but they never enter return or block metrics.

The independent block is:

`pipeline cohort / station ICAO / local market date / weather kind`.

Multiple markets, buckets, sides, and fills in the same station-day-kind block are not counted as
independent observations. If any eligible enrollment in a block is unresolved, the entire block
is pending and absent from the realised trajectory. Completed blocks are ordered by their latest
`recognized_at`, then stable block dimensions and enrollment identity.

The summary returns:

- enrolled, eligible, excluded, pending, closed-order, and independent-block counts;
- total cost, P&L, ROI, skill/luck decomposition, and decomposed-order count;
- leave-largest-order robust P&L and ROI;
- maximum drawdown from configured initial bankroll;
- largest absolute order contribution and result excluding that order;
- a deterministic cumulative trajectory by complete independent block.

The formal sample starts at zero after migration. Historical `/equity`,
`/performance-summary`, and `/edge-report` data are not substituted into it.

## Operator API

Read endpoint:

`GET /api/v1/validation-ledger?mode=paper|live&cohort=<optional exact cohort>`

The response exposes:

- boundary, definition version/hash, benchmark protocol, sample scope, and source-integrity label;
- requested/current/selected cohort identities and whether the selection is current;
- enrollment, exclusion, pending outcome, pending block, closed order, and independent block
  counts;
- realised performance, decomposition, robust-result, drawdown, and concentration diagnostics;
- ordered block points with cumulative P&L, skill, luck, equity, and drawdown.

An absent current cohort yields an explicit empty current selection. The client must not replace
missing ledger data with legacy statistics or zeros.

## Cockpit

Cockpit loads `/api/v1/validation-ledger?mode=paper` on the main cockpit page. The card displays
the immutable boundary and cohort, enrollment and outcome counts, P&L/ROI, skill/luck,
leave-largest diagnostics, and an interactive cumulative trajectory. Unavailable API data is
rendered as unavailable; the UI does not fall back to historical performance. The card is
diagnostic and does not unlock live mode.

## Verification and deployment acceptance

Release acceptance was recorded on 2026-07-14:

- Ruff and strict mypy passed; 475 non-PostgreSQL tests and 157 clean-database PostgreSQL tests
  passed through the complete Alembic chain. The PostgreSQL suite covered direct-SQL tamper
  rejection, enrollment completeness, race/idempotency, zero backfill, close-window enforcement,
  API integration, and guarded downgrade.
- The pre-migration backup
  `backups/stratus_pre_0036_20260714T202226Z.dump` is 318,697,098 bytes with SHA-256
  `18CCB8FE674BF339F5B9DF55ECDBED278EDBB2B13D2E656437C54F0941C1BC31`. A full restore drill
  reproduced the recorded schema and evidence-count vector exactly.
- Production upgraded through `20260714_0035` to the single current head `20260714_0036`.
  Migration `0035` opened exactly one runtime and exactly zero enrollments and zero outcomes;
  historical trades were not backfilled.
- API and worker were recreated from candidate image
  `sha256:414a9fbd827c0b1e8d02143190657e6e527263ff1eed03d514a71eba29f40b5c` and passed runtime
  acceptance. The rollback tag is `stratus:rollback-pre-0036-20260714t195534z`, pointing to image
  `sha256:faffffee2741ebf8c14bb33d3f2df8219fdc351885a34e0e080069162572f395`.
- At the 2026-07-14 release acceptance, the operator API reported sample scope `prospective`,
  source integrity `database-attested-order-fill+gamma+clob-v1`, and the correct zero baseline.
  PAPER remains
  active, the kill switch is off, and the then-current formal live gate remained blocked at
  `0/10`. Revision 0037 later expanded the formal gate to eleven checks; this dated result is not
  a current gate-count claim.
- The post-deployment manual health audit is a non-blocking `warning`; it is not presented as a
  healthy-strategy or live-readiness result.

At the 2026-07-14 release acceptance, the first genuine qualifying post-boundary fill had not yet
arrived. That historical zero baseline was correct and was not an unfinished migration step.
Genuine fills have since created 6 enrollments, of which 5 are eligible; outcome sealing remains
at zero. Deployment and tests must never manufacture a trade or an outcome to advance those
counts.

This release verifies the integrity mechanism and its zero boundary. It does **not** verify
forecast accuracy, positive CLV, profitability, official resolution-source correctness, or a
live-trading edge.

## Non-goals

This protocol does not claim predictive skill, legal authority, source correctness, or trade
profitability. It does not alter the existing live gate, import historical evidence, calculate
unrealised P&L, or treat correlated station-day buckets as independent samples. A future change to
the definition, hashes, block unit, or arithmetic requires a new version and a new prospective
boundary; existing evidence must remain immutable.
