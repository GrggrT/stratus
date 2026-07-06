# STRATUS

Phase 0 foundation for the Polymarket weather system. The current scope contains the
PostgreSQL schema, the initial Alembic migration, and the deterministic Open-Meteo forecast
collector and weather-market discovery for three active stations: KLGA, EGLC, and LFPB.

## Run locally

```powershell
Copy-Item .env.example .env
docker compose up -d postgres
uv sync --extra dev
uv run alembic upgrade head
uv run stratus-fetch-forecasts
uv run stratus-fetch-ensemble
uv run stratus-discover-markets
uv run stratus-fetch-observations
uv run stratus-bootstrap-iem
uv run stratus-bootstrap-previous-runs
uv run stratus-recalibrate-emos
uv run stratus-recompute-probs
uv run stratus-generate-signals
uv run stratus-execute-signals
uv run stratus-settle-markets
uv run stratus-audit-gate
uv run stratus-audit-health
uv run stratus-backfill-fees
uv run stratus-prune-signals
uv run stratus-api
uv run stratus-run
uv run stratus-telegram
uv run stratus-finalize-daily-actuals
uv run stratus-fetch-prices
uv run pytest
```

The collector requests hourly `temperature_2m` values separately for ECMWF, GFS, ICON, and
GEM, then stores the local-calendar daily high and low. Re-running it in the same UTC hour
updates the same rows instead of creating duplicates.

For KLGA the hourly collector also resolves the current NWS `forecastHourly` link through the
official `/points` endpoint and stores only complete local-calendar days. NWS replaces GEM as
the 10% deterministic anchor when available for a market date; GEM remains the fallback.

Open-Meteo's forecast endpoint does not expose the upstream model initialization timestamp.
Accordingly, `forecasts.run_time` is the UTC hour of the observed API snapshot. It must not be
interpreted as the native model cycle time.

Ensemble collection stores every member independently: GEFS control plus 30 perturbed members
as `gefs_m01..m31`, and ECMWF control plus 50 perturbed members as
`ecmwf_ens_m01..m51`. Member count is validated on every response. As with deterministic
forecasts, only fully covered local-calendar days are stored.

The active station set is based on current market rules: KLGA (NYC), EGLC (London), and LFPB
(Paris). EGLL and LFPG remain inactive in the database so previously collected forecasts are
not relabelled. Market discovery cross-checks the station in both rules and resolution URL;
unknown, inactive, conflicting, or changed stations are stored as blocked.
Station lineage only trusts previously verified markets, so an unconfirmed station change
cannot silently become the baseline on the following day. The block reason is persisted and
included in the Telegram verification alert.

`bucket_low_c` and `bucket_high_c` are continuous Celsius decision boundaries. For example,
an exact `25°C` resolution bucket is stored as `[24.5, 25.5)`, while an open lower tail has a
null lower bound. This matches whole-degree resolution semantics and can be used directly by
the later Gaussian-CDF model.

METAR collection requests all active stations in one AviationWeather call with a six-hour
overlap and upserts by `(station_id, obs_time)`. Daily actuals are calculated in each station's
IANA timezone. A day is not finalized unless it has at least 20 observations, observations
near both day boundaries, and no gap longer than two hours.

The IEM bootstrap downloads three years in station/year-sized chunks and commits every chunk,
so an interrupted run can be resumed safely through observation upserts. IEM source identifiers
are explicit station metadata: `NY_ASOS/LGA`, `GB__ASOS/EGLC`, and `FR__ASOS/LFPB`. Historical
daily actuals use the same completeness gate as live finalization.

Previous Runs bootstrap stores fixed-lead daily extremes separately from live forecasts.
Archived 24, 48, and 72-hour forecasts map to calibration buckets `6-24h`, `24-48h`, and
`48h+`. The `0-6h` bucket is intentionally left unbootstrapped. EMOS coefficients are fitted
with OLS over the trailing 180 days with at least 60 matched forecast/actual samples; residual
sigma has a 0.5°C numerical floor. A weighted regression over the 24/48/72-hour residual
sigmas fits `sigma = c + d * horizon`; negative uncertainty slopes are constrained to zero.

The probability pipeline uses ECMWF and GEFS member fractions plus EMOS-corrected Gaussian
components for ICON, GFS, and GEM. Default weights are 0.35/0.30/0.15/0.10/0.10. Available
components are renormalized. After 30 resolved samples exist for every available component,
weights become proportional to `exp(-5 * Brier)` per station and horizon using a trailing
180-day scoring window. Deterministic outliers beyond 1.5 ensemble sigma are halved, and
the result is shrunk toward three-year empirical station climatology in a ±15-day seasonal
window. Every persisted probability includes component-level diagnostics in JSON.

Signal generation evaluates both YES and NO edges, assigns A/B/C confidence, applies
quarter-Kelly sizing, spread/age/horizon/price/freshness/liquidity filters, and hard exposure
limits. Candidates are allocated by descending edge so the 12% station/day budget goes to the
best opportunities first. Rejected snapshots are persisted with semicolon-separated reasons.

CLOB price snapshots use the YES outcome book. Best prices are calculated independently of
the API level ordering. `bid_depth_usd` and `ask_depth_usd` contain executable notional within
one cent of the respective best price. One-sided books are skipped because they have no valid
spread or midpoint. `market_prices.ts` is collection time; the CLOB book timestamp is only
validated because it can reflect an older book update. `last_price_fetch_at` advances for
two-sided and one-sided books alike. Markets inside six hours of `resolves_at` become due every
minute; all other markets become due every ten minutes.

Telegram is the operational interface. Configure `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_ADMIN_USER_ID`, and optionally `TELEGRAM_CHAT_ID`, then run
`uv run stratus-telegram` alongside `uv run stratus-run`. Only the configured admin can use
commands. `/stop` is persisted in PostgreSQL and blocks both signal acceptance and execution;
`/resume` clears it. A/B signals, fills, settlements, and the 08:00 Europe/Berlin daily digest
use a deduplicated delivery queue. Live mode remains unavailable until the validation gate and
the separate live CLOB executor both exist.
The `/daily` report includes the five largest pre-resolution model temperature errors against
finalized station observations for the reporting day.

Austria-specific platform, legal, KYC, and tax findings are recorded in
`docs/AUSTRIA_LIVE_READINESS.md`. Live additionally requires explicit legal and tax approval
flags; technical geoblock availability alone is not approval.

Validation checkpoints run at 08:00 Europe/Berlin on paper days 30 and 60. Each checkpoint
persists Brier by station, resolved-position count, average CLV, and ROI. Two failed
checkpoints set a terminal halt reason and enable the kill switch; `/resume` cannot override
that state.
`/calib` returns a per-station reliability diagram and gate summary over a rolling 30-day
window. The gate requires at least 100 resolved paper positions and 100 captured CLV observations;
missing closing snapshots never count toward the CLV threshold.

The ten-minute data-quality audit checks source freshness, event probability sums, pending
signal age, and both exposure limits. Warnings are deduplicated into the Telegram queue;
an exposure breach enables the kill switch immediately.
`/status` independently proves the Phase 0 collection DoD over a trailing 48-hour window:
at least 99% of expected runs for every critical collector and no excessive schedule gaps.
The health audit reports an SLO warning only after the full window has matured.
Probability recomputation and signal generation are serialized across scheduler jobs. Model
probability writes also take a PostgreSQL transaction advisory lock so manual jobs cannot
deadlock with the worker.

Paper execution applies each market's Gamma `feeSchedule`. Weather taker fees are included in
net edge, Kelly sizing, fill budget, and position cost basis. `size_usd` remains total capital
at risk; `fee_usd` records the fee component separately.

Loss limits are calculated against bankroll at the start of each UTC day/week. A 6% daily
realized loss expires pending signals and blocks new trades until the next UTC day. A 12%
weekly realized loss additionally enables the kill switch and requests cancellation of live
orders. Enforcement runs before both signal generation and execution.

For continuous paper operation, run `docker compose up -d --build worker`. The migration job
runs first and the worker restarts automatically. After Telegram credentials are present in
`.env`, start the bot with `docker compose --profile telegram up -d telegram`.

The health API exposes only `GET /healthz` (process liveness) and `GET /readyz` (database,
latest health audit, and runtime-control readiness). Docker binds it to `127.0.0.1:8000`; no
web UI, OpenAPI page, or mutation endpoint is enabled.

Start daily database backups with `docker compose up -d backup`. Backups are written in custom
PostgreSQL format under `backups/`, validated immediately with `pg_restore --list`, and retained
for 14 days.
