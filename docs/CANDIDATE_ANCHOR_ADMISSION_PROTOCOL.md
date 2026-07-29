# Candidate-anchor signal admission protocol

Status: candidate-anchor-v2 accepted for prospective PAPER collection, 2026-07-17.

## 1. Purpose

Signal admission must prove two different facts without coupling them more tightly than the
decision requires:

1. the model published one complete, immutable probability distribution for the whole event; and
2. the particular YES/NO token STRATUS may buy has one current, executable, dual-token CLOB
   witness.

Revision `candidate-anchor-v2` keeps both checks fail-closed while removing the revision-v1 rule
that every sibling bucket also had to expose an executable book in the same CLOB batch. Sibling
liquidity is not used to price, size, execute, mark, or settle the candidate order, so making it a
trade prerequisite suppressed valid PAPER evidence without strengthening the candidate's own
provenance.

This protocol changes selection only. It does not change model probabilities, event
normalisation, calibration, edge thresholds, cost buffers, depth requirements, Kelly sizing,
exposure caps, station reliability, settlement, or LIVE authority.

## 2. Production diagnosis

The read-only diagnosis before the change found:

- every full recompute persisted all 308 current `ModelProb` rows;
- all 28 active event vectors passed exact model materialisation;
- 204--206 current candidate rows had their own eligible dual-token anchor in 27/28 events;
- only 4--5 events passed revision-v1 full-sibling trade coherence because all 11 sibling books
  had to be eligible in one batch; and
- signal generation selected the latest *anchored* row independently per bucket, thereby mixing
  old calculation cycles and repeatedly producing `event_probability_time_mismatch` and
  `event_probability_vector_incomplete`.

A read-only counterfactual at 2026-07-17 08:48 UTC retained every economic, station, liquidity,
and risk gate but separated event materialisation from candidate execution evidence. It produced
17 selection-eligible markets in 10 events versus zero under revision v1. This is evidence of a
selection bottleneck, not evidence of model skill or a guarantee of maker fills.

## 3. Mandatory model-vector proof

For every active event, signal generation loads the latest `ModelProb` row for every known bucket
without filtering on anchor presence. `assess_event_probability_materialization` must then prove:

- a unique and exhaustive contiguous bucket partition;
- one event identity, station/day/kind/resolution time, horizon, calculation timestamp, and
  pipeline cohort;
- one finite probability for every bucket;
- total probability within the existing `0.001` tolerance and an exact six-decimal final vector
  whose mass is one; and
- a valid per-row `event_coherence` materialisation attestation whose event id, timestamp,
  pipeline cohort, bucket counts, distribution hash, final probability, raw total, scale, and
  applied-normalisation flag reproduce the stored vector.

Selection never normalises or repairs probabilities. It accepts only the already materialised
values written by the probability job. Missing, mixed-time, mixed-horizon, mixed-cohort, malformed,
or hash-inconsistent vectors retain their stable `event_*` blocking reason.

The publication clock is per event. A newer calculation for another city cannot make a complete
event stale, and a partial newer calculation inside the same event cannot borrow older sibling
rows because the equal-timestamp proof fails.

## 4. Mandatory candidate execution proof

After the whole vector passes, each candidate is independently required to cite the exact
`ClobMarketSnapshot` named by its latest `ModelProb` row. Admission fails unless all of the
following hold:

- `market_anchor_protocol == clob-dual-v1`;
- snapshot id and market id match the candidate row and market;
- the snapshot is both complete and eligible;
- condition id and both YES/NO token ids exactly match the market contract;
- the market has a non-empty resolution source; and
- the immutable CLOB batch was first seen no later than the probability calculation.

No lookup falls back to an older model row or an older snapshot. Only the selected side's real
token, bid/ask, spread, and executable depth enter the subsequent economic decision. The existing
`evaluate_signal`, half-spread-plus-buffer cost gate, three-times-depth gate, station evidence,
position/pending exposure accounting, and portfolio caps remain unchanged.

## 5. Strict full-sibling benchmark remains

`assess_event_probability_coherence` and the persisted `event_coherence.passed/reason/
anchor_batch_id` fields remain unchanged. They continue to require eligible anchors for every
sibling in one exact CLOB batch and therefore remain the correct standard for an event-normalised
market Brier benchmark or any analysis that actually consumes the whole market vector.

Signal admission consumes the independently attested `materialized` half plus the candidate-local
anchor proof. It must not relabel a candidate admission as a passed full-market benchmark, and it
must not relabel a failed full-market benchmark as an invalid model vector.

## 6. Evidence continuity

The source change is confined to `jobs/generate_signals.py`, which is material only to the
`selection_policy` lane. A production activation must therefore:

- fork `selection_policy` at the exact image boundary;
- continue `predictive_validation`, `execution_evidence`, `official_resolution_truth`,
  `station_lifecycle`, and `operational_legacy` when their manifests are unchanged;
- quarantine no historical evidence and backfill no signal; and
- resume only PAPER after an audited stop, zero nonterminal LIVE check, reviewed release plan,
  post-activation health audit, and provenance inspection of the first new signals/orders.

Historical revision-v1 skips remain immutable and interpretable under their original epoch.

## 7. Acceptance tests

The release is not acceptable unless tests prove all of the following:

1. one eligible candidate plus ineligible/unanchored siblings can pass only when the latest whole
   model vector is complete and same-time;
2. incomplete, mixed-time, mixed-horizon, mixed-cohort, malformed, and proof-tampered vectors fail;
3. wrong snapshot id, market id, condition id, token identity, protocol, completeness, eligibility,
   or causal clock fails the candidate;
4. an older fully anchored sibling cohort is never borrowed after a newer materialised vector;
5. independent events use independent publication clocks;
6. the strict full-sibling `assess_event_probability_coherence` contract is unchanged; and
7. a real-PostgreSQL path preserves the exact current model probability and candidate snapshot in
   `Signal`, creates no sibling signal without a current anchor, and reaches the immutable
   execution-intent path without fabricated evidence.

## 8. What this does not prove

More admitted PAPER candidates improve the rate at which execution and outcome evidence can be
collected. They do not prove positive CLV, model superiority, profitable fills, sufficient
independent resolution units, legal/tax/KYC readiness, or permission to trade LIVE. All formal
promotion and live gates remain authoritative.

## 9. Production acceptance — epoch 8

The accepted image is
`sha256:12b1b2ca4874dbe13bd4a89ab0e63f13a41781a8fc7a9014e8c2282aa606b8d3`.
Reviewed plan 10 has plan hash
`c519506a00adf95ae3370e91f27fae9eab2276435119cf787b186b0049ea71cc`,
release-manifest hash
`611560cda47fdc0477933ebe673ed96d11238051903a66810d14a7362e3f11cf`, and
lane-manifest hash
`27f1de7415660dbda0a2dc446626738a3e78e3aa4271036d36436432c8d18564`.
It opened epoch 8 at `2026-07-17T09:36:34.871545Z`, with epoch 7 as its parent and record
hash `c6d3e23f2f930ac32b01334fa01940fe37daf715a5f95627f08c357c831c329e`.

The server-generated plan was accepted only after it returned exactly these actions:

- `selection_policy`: `fork`;
- `predictive_validation`: `continue`;
- `execution_evidence`: `continue`;
- `official_resolution_truth`: `continue`;
- `station_lifecycle`: `continue`; and
- `operational_legacy`: `continue`.

Audited command 9 enabled the Polymarket kill switch at
`2026-07-17T09:34:46.581988Z` and expired zero signals. Before the old worker stopped, the
database contained zero pending signals, zero nonterminal orders, zero LIVE orders, and zero LIVE
positions. The two existing open PAPER positions were preserved; no synthetic closure or backfill
was performed. API and worker were then recreated on the accepted image while the kill switch
remained on. Continuity matched the new image, cohort, and all six lanes before audited command 10
resumed only Polymarket PAPER at `2026-07-17T09:40:53.339919Z`. `/healthz` and `/readyz` then both
returned 200.

The first natural post-resume `prices-and-signals` cycle was scheduled at
`2026-07-17T09:41:30Z` and completed successfully at `09:41:56.867223Z`; its child execution job
also completed successfully. It evaluated 206 candidates and initially admitted 21 PAPER maker
orders. Continued natural cycles increased the timestamped acceptance sample at
`2026-07-17T09:51:03.718696Z` to 24 immutable epoch-8 intents: 23 `paper_resting` orders and one
natural eligible maker fill. The fill is order 1166 for LFPB, `buy_yes`, 25.438333 shares at
0.06000000, notional USD 1.526300, zero simulated fee. It has an eligible immutable fill leg,
execution-cost obligation 72, and eligible prospective enrollment 9. Its five- and thirty-minute
post-fill evidence was not yet due at this acceptance snapshot, so the obligation was correctly
pending and no premature cost seal was claimed.

A database-wide provenance audit covered every then-current accepted order. All 24/24 bound the
epoch-8 cohort, exact stored `ModelProb`, exact candidate `clob-dual-v1` snapshot, current station
snapshot, and epoch-8 `selection_policy` fork; LIVE count was zero. A deeper snapshot covered 22
orders across 11 event vectors and 121 bucket rows: candidate violations 0, vector violations 0,
bucket-partition violations 0, foreign sibling anchors 0, and candidate-snapshot reuse by a sibling
0. Twenty-one of those 22 decisions had `event_coherence.passed=false` because full-sibling market
context was incomplete while `materialized=true`; this directly demonstrates the intended
candidate-only admission path without relabelling the strict full-market benchmark.

Post-resume health audit 643 at `2026-07-17T09:48:38.570539Z` was `warning` with five
informational and six warning findings, zero critical findings, and zero blocking findings.
Continuity state was `collecting`, its bottleneck advanced from `awaiting_executable_signal` to
`maker_orders_resting`, and no lane was mismatched or frozen.

Verification before cutover passed Ruff, strict mypy over 166 source modules, 702 non-PostgreSQL
tests, 47 focused candidate-admission unit tests, the candidate PostgreSQL regression, 39 isolated
live-execution PostgreSQL tests, selection-policy, dual-trade-immutability, and station-reliability
PostgreSQL suites, and all 31 execution-cost-schema PostgreSQL tests. Two initially failing
execution-cost tests were proven to be non-hermetic fixtures: one dated a purported legacy fill
after the prospective watermark and one inherited the local API key from `.env`. Only those test
fixtures were corrected; production code and migrations were unchanged.

One independent accuracy-recovery issue remains visible and fail-closed. Startup could fetch only
a partial observation window for 2026-07-16, so `daily-actuals` refused to finalize all 20 station
days rather than inventing extrema from incomplete coverage. Candidate admission did not cause
this, and no incomplete actual entered calibration, but an authorized historical-observation
recovery path is required before those missing station-days can become training truth.
