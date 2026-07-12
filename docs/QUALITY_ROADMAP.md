# STRATUS quality roadmap

Status: roadmap for moving STRATUS from a working paper-trading prototype to a
validated, operator-grade trading system.

Last updated: 2026-07-12.

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

## Current baseline

Snapshot from the 2026-07-12 paper run after health/analytics hardening:

- Mode: `paper`.
- Kill switch: `false`.
- Health audit: `warning`.
- Positions: 26 total, 24 closed, 2 open.
- Closed PnL: `+$102.97`.
- ROI: `+15.89%`.
- Robust PnL excluding largest absolute-PnL trade: `-$101.65`.
- Robust ROI excluding largest absolute-PnL trade: `-16.66%`.
- Average CLV: `+0.0024`.
- CLV beat rate: `37.5%`.

Interpretation: the system is operational, but edge is not proven. The ordinary
ROI is positive, while robust ROI is negative and average CLV is far below the
live gate target. This is a paper hypothesis, not a live-ready strategy.
The realised profit is currently dominated by one outlier winner; until robust
ROI and CLV improve on a larger resolved sample, positive PnL must be treated
as variance rather than evidence of edge.

## Live gate

Live trading must remain blocked until all of the following are true:

1. At least 100 resolved paper positions.
2. Brier score `< 0.18`.
3. Average CLV `> +0.015`.
4. Realised ROI `> 0`.
5. Robust ROI `> 0` after excluding the largest absolute-PnL trade. Ordinary
   ROI alone is not sufficient.
6. Largest single trade contribution is less than 25-30% of total realised PnL;
   otherwise live remains blocked even if headline ROI is positive.
7. Health audit has no unresolved `critical` or `blocking` issues.
8. Austria/KYC/tax readiness is explicitly confirmed.
9. Wallet, funder, signature type, cancellation path, and emergency stop are
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

- At least 100 resolved paper positions.
- Brier `< 0.18`.
- Average CLV `> +0.015`.
- ROI and robust ROI are both positive.
- Largest trade contribution is below 25-30%.

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

UI polish should not outrun the validation layer. A professional-looking
console is useful only if it exposes correct edge and risk information.
