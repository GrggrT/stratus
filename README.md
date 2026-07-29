# STRATUS

Phase 0 foundation for the Polymarket weather system. The current scope contains the
PostgreSQL schema, the initial Alembic migration, and the deterministic Open-Meteo forecast
collector and weather-market discovery for three active stations: KLGA, EGLC, and LFPB.

## Run locally

For console/API development in **PAPER mode only**, use the guarded host-API workflow in
[`docs/LOCAL_DEV_MODE.md`](docs/LOCAL_DEV_MODE.md). It keeps Postgres in Docker, runs
`stratus-api` from the current checkout, and stops stale container `api`/`worker` services.
Host mode is not isolated from same-user code execution and must never be used for LIVE;
the production least-privilege boundary requires the containerized runtime.

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force .secrets | Out-Null
$runtimePassword = [Convert]::ToHexString(
  [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).ToLowerInvariant()
Set-Content .secrets\stratus_runtime_password $runtimePassword -NoNewline
docker compose up -d postgres
uv sync --extra dev
uv run alembic upgrade head
docker compose run --rm --no-deps provision-runtime-role
uv run stratus-fetch-forecasts
uv run stratus-fetch-ensemble
uv run stratus-discover-markets
uv run stratus-fetch-observations
uv run stratus-bootstrap-iem
uv run stratus-recover-observations
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

Alembic and backup operations read the owner-only `MIGRATION_DATABASE_URL`.
Runtime processes ignore generic `DATABASE_URL`; they derive a database-qualified
`stratus_app_<md5(database)>` login from the mounted password file, or accept the
explicit `STRATUS_RUNTIME_DATABASE_URL` override. Every physical runtime connection
revalidates the least-privilege boundary before serving work.

`stratus-recover-observations` is the bounded, provenance-preserving path for missed closed local
days. It stores immutable IEM response evidence and never writes archive points into live
`observations`. See [`docs/OBSERVATION_RECOVERY_PROTOCOL.md`](docs/OBSERVATION_RECOVERY_PROTOCOL.md).

Evidence continuity keeps two boundaries distinct. Formal trade/model denominators use the newest
boundary of the five prospective evidence lanes; scheduler liveness and ETA additionally respect
the `operational_legacy` boundary. An operational-only fork can therefore start a new monitored
uptime window without deleting or relabelling formal samples. A selection-policy fork, such as
activating a reviewed adverse-selection edge ceiling, prospectively resets only the compatible
selection/formal window and remains visible in immutable epoch history.

## Binance Spot research data

The Binance venue is disabled and kill-switched by default. The implemented scope is public market
data and exchange-disconnected research: official Spot SDK REST data, `bookTicker` WebSocket
snapshots, REST depth-20 reconciliation, SHA-256-verified kline/`aggTrades` archives, and a
deterministic event-driven simulator. No Binance order-placement method is implemented, and
operator requests for Binance `testnet` or `live` mode are rejected.

After explicitly setting `BINANCE_ENABLED=true` and changing the persisted Binance venue mode to
`shadow`, the public-data commands are:

```powershell
uv run stratus-collect-binance-market-data
docker compose --profile binance up -d binance-market-stream
uv run stratus-backfill-binance-klines --start-date 2026-06-01 --end-date 2026-06-30
uv run stratus-backfill-binance-agg-trades --start-date 2026-06-01 --end-date 2026-06-30
```

The standalone single-run backtester still reads imported PostgreSQL rows and requires verified
kline and `aggTrades` import manifests. For example, after importing the selected `5m` interval:

```powershell
uv run stratus-backtest-binance --symbol BTCUSDT --interval 5m `
  --start 2026-06-01T00:00:00Z --end 2026-07-01T00:00:00Z `
  --initial-quote 1000 --order-notional 25 `
  --maker-fee-bps $env:BINANCE_RESEARCH_MAKER_FEE_BPS `
  --taker-fee-bps $env:BINANCE_RESEARCH_TAKER_FEE_BPS `
  --entry-slippage-bps $env:BINANCE_RESEARCH_ENTRY_SLIPPAGE_BPS `
  --exit-slippage-bps $env:BINANCE_RESEARCH_EXIT_SLIPPAGE_BPS
```

The command also runs a double-slippage stress. Its gate remains `NO_GO` until the required
out-of-sample cycle count, walk-forward/untouched-holdout, diversification, and parameter-stability
evidence all pass. A profitable simulation does not enable live trading.

The immutable multi-symbol research orchestrator uses a bounded archive stream by default. It
downloads one official UTC day and symbol at a time, verifies the Binance SHA-256 checksum and
archive structure, feeds every `aggTrades` and closed `5m` candle to the current candidate batch
and its 2x-slippage stress, then deletes the temporary ZIP files. Canonical event bytes are built
once and shared by the matrix engines. There is no event sampling or precision reduction.
PostgreSQL stores only archive provenance and append-only research results.

The fail-closed defaults cap the temporary cache at 10 GB, preserve at least 70 GB of free disk,
reject an individual archive above 500 MB or 5 GB uncompressed, and stop before network access
when available physical memory or available system commit is below 1 GB. Resources are rechecked
every 10,000 events; transient memory pressure pauses processing for up to five minutes before a
fail-closed stop. These limits can make research slower or stop a run; they do not silently produce
a partial result. Validate the pre-registered 24-month candidate grid and
estimate the conventional import size without downloading market data:

```powershell
uv run stratus-research-binance validate-spec `
  --spec docs/binance_research_24m.example.json
uv run stratus-estimate-binance-dataset `
  --start-month 2024-07 --end-month 2026-06 --storage-path .
```

On 2026-07-12 the official monthly Binance files for BTCUSDT/ETHUSDT contained 24.429 GiB of
compressed `aggTrades` and `5m` klines. The PostgreSQL heap/index planning range is 314.8-524.6
GB, while this workstation had 93.4 GB free, so the preflight result was
`INSUFFICIENT_FOR_POSTGRESQL_PLAN`. The 24-month import must not be started on the current disk.
The bounded stream is the selected mode for this workstation and avoids that PostgreSQL expansion.

Migration `0023` and the example contract are deployed on this workstation. `run-oos` processes
one candidate/fold/symbol window by default and reports `completed_result_rows`; repeat the same
idempotent command until `complete=true`:

```powershell
uv run stratus-research-binance run-oos `
  --experiment e229d60ad8c201a57fc0485dccce20d2a4cb959997e261ed5c73ad9888458c34 `
  --candidate-batch-size 1 --max-batches 1
```

The first production OOS batch completed on 2026-07-12: BTCUSDT fold 1, MA 12/24,
24,098,556 exact events, 60 verified daily manifests, and two result rows. Baseline net PnL was
`-179.86765501752270000` USDT and the 2x-slippage result was `-231.2963157934710000` USDT.
The batch took 1,758.6 seconds.

The next measured run processed three more candidates on the same partition in 3,416.7 seconds.
A third run processed the five remaining candidates in 5,536.5 seconds, with observed Python
private memory of about 97-119 MiB. A fourth bounded run processed all nine ETHUSDT fold-1
candidates in 15,865.5 seconds, with about 97-136 MiB private memory. It streamed 50,294,060 exact
events and safely resumed after two transient resource-pressure pauses. A fifth run completed all
nine BTCUSDT fold-2 candidates in 6,427.1 seconds, streaming 19,391,847 exact events without a
resource pause. That brought progress to `54/288`: three partitions were complete, each with one shared data
hash, and all 180 daily manifests are verified without duplication. A sixth run completed all nine
ETHUSDT fold-2 candidates in 4,335.4 seconds, streaming 28,003,074 exact events. It used an
independent hidden process plus earlier resource guards after two interactive process-tree cleanups
ended before producing results. Progress is now `72/288`: four partitions are complete and all 240
daily manifests are verified without duplication. All nine candidates were negative in both cost
scenarios on every completed partition. A seventh run completed all nine BTCUSDT fold-3 candidates
in 13,763.2 seconds including resource-guard pauses, streaming 37,765,604 exact events with about
99-137 MiB private memory. Progress is now `90/288`: five partitions and all 300 daily manifests
are complete without duplication. The fold-3 data hash is shared by all 18 result rows, and every
candidate is again negative in both cost scenarios. An eighth bounded run completed all nine
ETHUSDT fold-3 candidates on a guarded retry in 8,890.2 seconds, streaming 47,786,016 exact events.
The first attempt stopped fail-closed at the disk reserve before writing any result row; pruning
reproducible Docker build/image caches and compacting its VHD restored the reserve without deleting
volumes. Progress is now `108/288`: six partitions and all 360 daily manifests are complete without
duplication, and every ETH fold-3 baseline and double-slippage result is negative. That is not a
complete OOS decision and must not be used to prune the pre-registered candidate set. The next
bounded run may use batch size 9 for BTCUSDT fold 4; the remaining 10 partitions are roughly 25-35
compute-hours under the measured resource contention.

Only after complete OOS selection may an operator issue explicit `unlock-holdout` with both
registered hashes, followed by `run-holdout`. The orchestrator never unlocks holdout automatically
and remains unable to place Binance orders. The fee, slippage, latency, and notional values in the
example are conservative research inputs; changing them requires a new experiment.

Runtime isolation and current block reasons are exposed at `GET /api/v1/venues`. The detailed
design, rollout gates, and remaining work are in
[`docs/BINANCE_SPOT_TRADING_SPEC.md`](docs/BINANCE_SPOT_TRADING_SPEC.md).

The collector requests hourly `temperature_2m` values separately for ECMWF, GFS, ICON, and
GEM, then stores the local-calendar daily high and low. Re-running it in the same UTC hour
updates the same rows instead of creating duplicates.

For KLGA the hourly collector also resolves the current NWS `forecastHourly` link through the
official `/points` endpoint and stores only complete local-calendar days. NWS replaces GEM as
the 10% deterministic anchor when available for a market date; GEM remains the fallback.

Open-Meteo's live forecast endpoint does not expose the upstream model initialization timestamp.
Live snapshots are therefore labelled `collector_snapshot`; `available_at` is stored and every
probability query is point-in-time filtered. The manual `uv run stratus-backfill-single-runs`
command preserves exact initialization times. Its availability is explicitly labelled as a
documented latency proxy, never as an observed publication time.

Ensemble collection stores every member independently: GEFS control plus 30 perturbed members
as `gefs_m01..m31`, and ECMWF control plus 50 perturbed members as
`ecmwf_ens_m01..m51`. Member count is validated on every response. As with deterministic
forecasts, only fully covered local-calendar days are stored.

The station registry contains a 20-station research pool. Lifecycle stages keep new stations
in shadow or paper until their station-level evidence qualifies; stage, not registry presence,
controls trade eligibility. Market discovery cross-checks the station in both rules and resolution URL;
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

The probability pipeline kernel-dresses finite ECMWF/GEFS member clouds, applies EMOS plus an
AR(1) residual correction, and enforces intraday high/low constraints from observations. QRF,
analog, and two-regime Gaussian-mixture challengers run beside the baseline. Once enough
independent resolved cases exist, a non-negative regularized stack replaces fixed blending;
until then market shrinkage remains fail-closed. Every prediction stores expert, sample, and
point-in-time diagnostics.

Deterministic Open-Meteo runs also retain compact local-day hourly trajectories. The
`trajectory_ar1` challenger forms a multi-model median path, conditions elapsed hours on observed
temperatures, samples serially correlated AR(1) residual paths, and converts their daily maxima or
minima into bucket probabilities. It is prospective and shadow-only: a station/horizon/kind slice
cannot enter the learned stack until at least 60 independent day blocks show a Brier improvement
whose 95% block-bootstrap CI is strictly below zero.

For the current local day, `trajectory_nowcast` compares the latest observed hours with the
multi-model median path. A robust, recency-weighted bias (outlier-clipped and capped at ±4°C) is
propagated into future hours with a six-hour half-life. Hours already observed remain fixed. The
resulting high/low shift is sampled as a separate challenger and never overwrites the baseline;
it requires its own 60-block prospective Brier/CI gate before the regularized stack may use it.

An online split-conformal guardrail independently checks whether EMOS sigma is too narrow or too
wide. Standardized historical residuals produce a finite-sample 90% scale factor, bounded to avoid
unstable tails. The resulting `conformal_gaussian` probabilities are recorded prospectively but
remain excluded from the trading stack until 60 independent resolved day blocks prove a Brier
improvement with a strictly negative 95% CI. METAR-derived actuals may initialize the shadow scale;
only settlement outcomes can authorize its trading use.

Accuracy v2 parses a versioned resolution contract for every verified event. Source precision
drives bucket boundaries; settlement-native values override METAR-derived actuals for training.
Licensed final values enter only through authenticated `POST /api/v1/settlement-actuals` with
rules hash, response hash, actor, reason, and provenance. Wunderground storage is refused until
a suitable licence exists. Same-horizon market probability, executable bid/ask, and
pre-resolution close are stored as separate benchmarks.

Regional HRRR/NBM, UK 2 km, AROME, ICON-EU, HARMONIE-AROME, JMA MSM, KMA LDPS, CMA GRAPES,
BOM ACCESS-G, and ECMWF AIFS run shadow-only where applicable. A daily 30/60/90/180-day bake-off
uses deterministic CRPS, coverage, latency, and station-day block-bootstrap CI. Structural
drift and model eligibility are exposed through `GET /api/v1/accuracy-overview` and the Cockpit
Accuracy Laboratory.

Resolved market partitions also train a global settlement-outcome beta calibrator. Training and
validation are blocked by independent station/day/kind/horizon samples, so mutually exclusive
buckets from one event are never counted as independent evidence. The fitted probabilities remain
shadow-only until at least 60 blocks exist, out-of-fold Brier improves with a 95% block-bootstrap
CI strictly below zero, and ECE does not regress beyond its guardrail. Cockpit exposes the fitted
alpha/beta, sample count, Brier/ECE before and after, CI, and whether the calibrator is merely
observed or actually applied to trading probabilities.

Signal generation evaluates both YES and NO edges, assigns A/B/C confidence, applies
quarter-Kelly sizing, spread/age/horizon/price/freshness/liquidity filters, and hard exposure
limits. Candidates are allocated by descending edge so the 12% station/day budget goes to the
best opportunities first. Rejected snapshots are persisted with semicolon-separated reasons.

CLOB evidence independently captures the executable YES and NO books for every complete event
cohort. The exact decoded `/books` response, request token universe, server-attested first-seen
time, source hashes, token identities, bid/ask, near-touch depth and both book timestamps are kept
in an append-only ledger. A snapshot is trading-eligible only when both token books are complete,
fresh and internally consistent; no NO price or depth is inferred as `1 - YES`. The legacy
`market_prices` table is only an operational YES-side projection and is not admitted as formal
benchmark evidence. Markets inside six hours of `resolves_at` become due every minute, other
paper/approved markets every ten minutes, and shadow stations hourly. Every formal probability,
signal, order, position, CLV and forecast evaluation retains the exact dual-token snapshot and
protocol provenance used for its decision.

Revision `20260714_0034` adds the operational evidence guard around that ledger. Resolved markets
are reconciled hourly against METAR-derived and, when available, final settlement-native truth;
Gamma settlements retain a per-event `venue_pending | source_missing | ambiguous | manual_review |
resolved` lifecycle and never accept a partial outcome vector. Signal generation accepts an event
only when its complete bucket vector is coherent and bound to one eligible `clob-dual-v1` batch;
missing buckets and synthetic complements fail closed. PostgreSQL validates reconciliation and
terminal settlement identities against the immutable Gamma vector, rejects evidence deletion, and
retains material corrections in append-only `evidence_version_history`.

Immutable CLOB evidence is never deleted automatically. Hourly append-only capacity samples are
evaluated under `immutable-evidence-preserve-v1` with a 20 GB budget, warning at 75%, and a 30-day
projection only after at least 20 observed hours. Collection and execution fail closed when the
sample is missing/stale, the logical write reserve would cross the budget, or measured free space
would cross the physical reserve. CLOB response bytes are bounded before buffering; the final live
measurement and evidence write are serialized by a PostgreSQL advisory transaction lock. Operator
state is available from
`GET /api/v1/resolution-reconciliation`, `GET /api/v1/settlement-cases`, and
`GET /api/v1/storage-capacity`, and is summarized by Cockpit's **Resolution & evidence
operations** card. Diagnostic endpoint failures degrade only that card and retain last-known core
Cockpit data. Until the 20-hour measurement window and prospective dual-token resolution evidence
accumulate, capacity runway and predictive skill remain unproven.

Telegram is the operational interface. Configure `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_ADMIN_USER_ID`, and optionally `TELEGRAM_CHAT_ID`, then run
`uv run stratus-telegram` alongside `uv run stratus-run`. Only the configured admin can use
commands. `/stop` is persisted in PostgreSQL and blocks both signal acceptance and execution;
`/resume` clears it. A/B signals, fills, settlements, and the 08:00 Europe/Berlin daily digest
use a deduplicated delivery queue. The live CLOB executor exists but is fail-closed: LIVE remains
unavailable until the formal 11-part paper evidence gate and every separate legal, tax, credential,
risk, and reconciliation gate pass. Neither Telegram nor Cockpit can bypass those checks.
The `/daily` report includes the five largest pre-resolution model temperature errors against
finalized station observations for the reporting day.

Austria-specific platform, legal, KYC, and tax findings are recorded in
`docs/AUSTRIA_LIVE_READINESS.md`. Live additionally requires explicit legal and tax approval
flags; technical geoblock availability alone is not approval.

Validation checkpoints run at 08:00 Europe/Berlin on paper days 30 and 60. Each checkpoint
persists Brier by station, resolved-position count, average CLV, and ROI. Two failed
checkpoints set a terminal halt reason and enable the kill switch; `/resume` cannot override
that state.
`/calib` returns a per-station reliability diagram and gate summary over a rolling 30-day window.
The formal gate has 11 conjunctive checks. Its main sample floors are 200 closed prospective PAPER
order fills, 100 independent station-day-kind samples, 100 lane-scoped resolved trades on the
current probability pipeline, 100 captured CLV observations, and at least three calibrated
stations with 100 samples each and Brier below 0.18. It additionally requires average CLV above
0.015, CLV beat rate at least 52%, positive lower 95% bounds for ROI and skill P&L, model Brier
below the market benchmark, and complete immutable forecast provenance. Missing closing snapshots
never count toward the CLV threshold.

The ten-minute data-quality audit checks source freshness, event probability coherence, pending
signal age, resolution/settlement evidence, immutable-ledger capacity, and both exposure limits.
Issues are classified as `info`, `warning`, `critical`, or `blocking`: `critical` and `blocking`
make readiness false, but only explicit `blocking` engages the kill switch. Alerts are
deduplicated into the Telegram queue.
`/status` independently proves the Phase 0 collection DoD over a trailing 48-hour window:
at least 99% of expected runs for every critical collector and no excessive schedule gaps.
The SLO distinguishes six-hour startup grace, collecting, bounded visible planned maintenance,
passing, and failing; planned maintenance is reasoned and capped at two hours per SLO window.
Probability recomputation and signal generation are serialized across scheduler jobs. Model
probability writes also take a PostgreSQL transaction advisory lock so manual jobs cannot
deadlock with the worker.

Paper execution applies each market's Gamma `feeSchedule`. Weather taker fees are included in
net edge, Kelly sizing, fill budget, and position cost basis. `size_usd` remains total capital
at risk; `fee_usd` records the fee component separately.

Loss limits are calculated against bankroll at the start of each local day/week in
`RISK_TIMEZONE` (default `America/New_York`); stored and reported timestamps remain UTC. A 6%
daily realized loss expires pending signals and blocks new trades until the next local-day
boundary. A 12% weekly realized loss additionally enables the kill switch and requests
cancellation of live orders. Enforcement runs before both signal generation and execution.

For continuous paper operation, first create the ignored runtime-password file shown above.
Build a reviewed commit from a clean worktree under a unique immutable tag, set `STRATUS_IMAGE`
to that tag, and set `STRATUS_RELEASE_IMAGE_DIGEST` to the exact 64-hex Docker image ID without
the `sha256:` prefix. Validate the rendered Compose configuration before starting any service.
The default `stratus:local` remains convenient for development but is not a production release
identity. The migration and one-shot runtime-role provisioner run first; a release worker starts
only after both succeed and its reviewed evidence epoch is active. After Telegram credentials are
present in `.env`, start the bot with `docker compose --profile telegram up -d telegram`.

Before any release service starts, run
`scripts/Assert-StratusReleaseImage.ps1 -Image <candidate-tag> -ExpectedDigest <64-hex-image-id>
-ExpectedRevision <40-hex-commit>`. The guard fails unless the inspected local image is
`linux/amd64`, carries the exact Git revision label, matches both release values in `.env`, and is
the image rendered for every application service. Do not use `docker compose --build` during a
release cutover; build the candidate once and promote that exact image ID.

The unauthenticated health surface exposes only `GET /healthz` (process liveness) and
`GET /readyz` (database, latest health audit, and runtime-control readiness). Docker binds the
operator API to `127.0.0.1:8000`; no web UI or OpenAPI page is enabled, and mutations remain
authenticated, allowlisted operator commands.

Start daily database backups with `docker compose up -d backup`. A shared filesystem `flock`
serializes all writers. Each accepted restore point is atomically published as a
`backups/stratus_<UTC>_<instance>_<pid>.backup/` directory containing `stratus.dump`,
`catalog.list`, and `manifest.txt`; the manifest records archive/catalog SHA-256, byte and catalog
counts, and matching Alembic heads before and after the dump. Failed work remains unpublished and
is removed before bounded exponential retry. Automatic retention applies only after a successful
publication, keeps at least the three newest directory restore points, and never auto-deletes
legacy accepted `.dump` files.

The complete disaster-recovery procedure is
[`docs/BACKUP_RESTORE_RUNBOOK.md`](docs/BACKUP_RESTORE_RUNBOOK.md). In particular, `pg_dump`
does not archive cluster roles or the target database ACL. A restore is not accepted until the
database-scoped roles are recreated, PostgreSQL's default `TEMPORARY` privilege is revoked from
`PUBLIC`, both owner and restricted-login runtime-boundary assertions pass, and the immutable
execution ledger reconciles cleanly.

Off-site copy is opt-in with the `offsite` profile and is deliberately restricted to the rclone
remote named `crypt` (`OFFSITE_REMOTE=crypt:<path>`). It rechecks local hashes, performs a remote
download verification, and uploads `COMPLETE` last; a remote directory without that exact marker
is not an accepted restore point.
