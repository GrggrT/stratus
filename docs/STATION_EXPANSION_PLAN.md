# STRATUS Station Expansion Plan

Status: 20-station pool implemented and bootstrapped on 2026-07-12; revision 0042 station
reliability was production-accepted and PAPER-only collection resumed on 2026-07-17.

## Decision

STRATUS expands from three stations to a 20-station research pool without
weakening signal, execution, or risk filters. Five stations are eligible for
paper signals; fifteen begin in shadow mode.

The purpose is to collect independent station-day evidence faster. Repeated
orders in the same market are not treated as independent validation samples.

## Lifecycle

- `shadow`: collect weather history, forecasts, observations, market metadata,
  hourly prices, and hourly probabilities; never create orders.
- `paper`: run the normal probability and signal pipeline and permit simulated
  maker orders.
- `approved`: station is eligible for live orders, but only after the global
  live gate and operator confirmation also pass.
- `disabled`: reserved registry entry; excluded from operational use.

Lifecycle is necessary but not sufficient. Live execution filters stations fail-closed: only
`approved` can be considered, and revision 0042 additionally requires a current `RELIABLE`
station-trust snapshot. Paper execution considers `paper` and `approved`; a `COLLECTING` trust
status may collect PAPER evidence only when no current conflict, observation, integrity, date, or
policy block exists.

## Initial cohort

| Stage | City | ICAO | Historical source |
|---|---|---:|---|
| paper | New York City | KLGA | NY_ASOS / LGA |
| paper | London | EGLC | GB__ASOS / EGLC |
| paper | Paris | LFPB | FR__ASOS / LFPB |
| paper | Tokyo | RJTT | JP__ASOS / RJTT |
| paper | Seoul | RKSI | KR__ASOS / RKSI |
| shadow | Shanghai | ZSPD | CN__ASOS / ZSPD |
| shadow | Miami | KMIA | FL_ASOS / MIA |
| shadow | Amsterdam | EHAM | NL__ASOS / EHAM |
| shadow | Busan | RKPK | KR__ASOS / RKPK |
| shadow | Munich | EDDM | DE__ASOS / EDDM |
| shadow | Madrid | LEMD | ES__ASOS / LEMD |
| shadow | Helsinki | EFHK | FI__ASOS / EFHK |
| shadow | Warsaw | EPWA | PL__ASOS / EPWA |
| shadow | Singapore | WSSS | SG__ASOS / WSSS |
| shadow | Taipei | RCSS | TW__ASOS / RCSS |
| shadow | Kuala Lumpur | WMKK | MY__ASOS / WMKK |
| shadow | Manila | RPLL | PH__ASOS / RPLL |
| shadow | Wellington | NZWN | NF__ASOS / NZWN |
| shadow | Chicago | KORD | IL_ASOS / ORD |
| shadow | Los Angeles | KLAX | CA_ASOS / LAX |

Station IDs were read from active Polymarket temperature-market resolution
rules. IEM identifiers and time zones were cross-checked against the IEM ASOS
network registry before seeding.

## Promotion policy

A shadow station may be considered for paper only when all conditions hold:

1. Resolution rules identify exactly one configured ICAO station and the city
   matches.
2. IEM actuals and fixed-lead forecast bootstrap complete without coverage
   gaps material to daily high/low calculation.
3. Every traded calibration bucket has at least the configured minimum sample
   count.
4. Current observations are fresh and active markets exist.
5. Order-book depth and spread are viable under the unchanged execution-cost
   filters.
6. The current station-trust snapshot is hash-valid, evaluated for the current local date, and has
   no active selection block. A station may remain `COLLECTING` for PAPER while its independent
   comparable sample grows.

Promotion should happen in small cohorts. Operate five paper stations for at
least three resolved local days before promoting another cohort.

Paper-to-approved promotion additionally requires at least 100 resolved probability samples for
that station, Brier below 0.18, and a current `RELIABLE` station-trust result. Approval does not
bypass the global live gate. A lifecycle stage change never converts earlier SHADOW or COLLECTING
records into live-eligible evidence.

## Gate semantics

The old gate required exactly three scored stations. The expandable gate now
requires at least three qualified paper/approved stations. Additional shadow
or immature paper stations cannot make an otherwise valid gate fail merely by
existing.

Global requirements remain unchanged: 200 closed paper positions, 100 CLV
samples, average CLV above 1.5 cents, CLV beat rate at least 52%, positive ROI
and skill confidence intervals, and model Brier better than the market.

## Load controls

- Paper/approved price collection keeps adaptive one/ten-minute polling.
- Shadow prices and probabilities update hourly.
- Identical skipped-signal states are persisted once per hour rather than once
  per minute.
- Forecast, ensemble, and METAR jobs isolate partial station failures; they
  fail the whole job only when every request or batch fails.
- Existing market and station-day exposure caps remain in force.

## Monitoring

`GET /api/v1/stations` reports each station's stage, calibration coverage,
active market count, closed paper positions, latest observation, and whether
the data prerequisites for paper review are present. The 0042 candidate extends it with Wilson
score/lower bound and sample count, exact conflicts, excluded boundary/unavailable counts, stable
reasons, conflict and observation block expiries, evaluated local date, selection reasons, and
policy/source/record hashes.

The principal validation unit for expansion analysis is
`station × local date × weather kind`. Nominal position count must always be
reported alongside this independent-unit count before a live decision.

## Deployment verification

The production bootstrap completed on 2026-07-12 with:

- 916,824 historical METAR observations;
- 43,400 daily high/low rows from the bootstrap (43,402 after the next
  scheduled finalization);
- 451,380 fixed-lead historical forecast rows;
- 84,396 matched forecast/actual samples used by EMOS;
- 480 calibration rows (24 per active station), with no skipped fit groups;
- all 20 active stations meeting the data prerequisite for paper review.

The operational pool remains deliberately limited to five paper stations and
fifteen shadow stations. Two legacy registry rows are inactive and shown as
archived in Cockpit. No shadow station was automatically promoted after the
bootstrap; cohort promotion still requires the review policy above.

## Revision 0042 station-trust policy (production accepted)

Reconciliation rows are first collapsed to one independent
`station × local date × weather kind` unit. The most conservative classification wins within the
unit, so a bucket-level match cannot hide a boundary or conflict in another bucket for the same
station/day/kind.

Let `m` be decisive matches and let `c` be the sum of `exact_source_mismatch`, `clear_mismatch`,
and `exact_truth_gap`. With `n = m + c` and `p_hat = m/n`, station trust uses the two-sided 95%
Wilson lower bound:

`LB = (p_hat + z²/(2n) - z*sqrt(p_hat*(1-p_hat)/n + z²/(4n²))) / (1 + z²/n)`,
where `z = 1.959963984540054`.

`boundary_sensitive` and `truth_unavailable` remain visible audit counts but are excluded from
`n`. They are non-punitive because neither establishes a source conflict; unavailable truth is not
treated as a success. Accepted defaults are a 180-day window, 20 comparable units, `LB >= 0.80`,
a 48-hour resolution-conflict block, 90-minute observation freshness, and 45-minute snapshot
freshness. These values are hashed as selection policy, not hidden UI thresholds.

Status authority is:

- `COLLECTING`: fewer than the minimum comparable units; PAPER collection can continue if every
  current gate reason is clear;
- `RELIABLE`: enough samples and Wilson lower bound at/above policy, with no active data/conflict
  block;
- `WATCH`: mature but Wilson lower bound below policy, therefore selection-blocked; and
- `BLOCKED`: an active conflict block or current-observation problem, also selection-blocked.

LIVE requires `approved + RELIABLE` and still requires the global validation/legal/risk/continuity
gates. PAPER `COLLECTING` is an evidence-growth state, not a quality claim. Inactive registry rows
remain visible but do not inflate active blocked counts.

The accepted revision stores append-only snapshots with exact policy/source/record identity and binds new
signals to the snapshot used at selection. A stale pending signal is rechecked before execution;
missing, tampered, wrong-policy, wrong-local-date, or newly blocked evidence fails closed.

Deployment acceptance is complete. Epoch 5 forked `selection_policy`, `station_lifecycle`,
`execution_evidence`, and independently changed official truth; epoch 6 forked only
`operational_legacy` for the reporting-window hotfix. All 20 active stations have current-date
snapshots under one policy hash, the operator API excludes two inactive archived rows from active
totals, and the post-resume audit has zero critical/blocking findings. Audited command 6 resumed
only Polymarket PAPER; the first cycle created two fully attested resting orders. No station is
automatically promoted by this document. Revision 0041's natural fill-projection observation
remains separately and historically inconclusive.

### Deterministic recurring station-trust refresh — production accepted

Epoch 7 accepted runtime image
`sha256:061e73111d8ac0464c3bc2b2cffe92ff8370984789a9283fc734403d0859f843` under reviewed plan 9.
Observation refresh now commits before station reassessment, and the hourly settlement ->
reconciliation -> assessment composite shares one station barrier with the price/signal/execution
cycle. A trading cycle therefore cannot select or execute against the old assessment after a newer
reconciliation has committed. Price cohorts start at second 30; settlement polls run at minutes 00,
10, 20, 30, 40, and 50, with the minute-00 poll owning reconciliation and reassessment.

After PAPER-only resume, 205 signals were skipped and every signal had a present, fresh,
current-local-date station snapshot. No executable signal or order had yet been produced. The
runtime remained `collecting`; audit 634 had zero critical/blocking findings, and continuity had no
mismatched or frozen lane. This confirms selection-evidence freshness and fail-closed behavior, not
station promotion, predictive skill, profitability, or LIVE authority. Full release evidence is in
`EVIDENCE_CONTINUITY_PROTOCOL.md` section 20.2.

The first live recurring refresh confirmed the implementation rather than only its unit contract:
20 stations and 214 METAR reports committed before child assessment job 31786, and observation
parent 31787 did not report success until the assessment had succeeded.
