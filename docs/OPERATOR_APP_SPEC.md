# STRATUS Operator Console

Status: Phase A, Phase B monitoring, Phase C controlled operations, revision 0038 execution evidence
operations, revisions 0035/0036 validation and alert observability, revision 0037 forecast-source
evidence, and revision 0038 execution-quality evidence are implemented and release-verified.
Revision 0041 requested-limit/fill-VWAP and lane-scoped formal reporting changes are implemented,
release-verified, and production-accepted for the initial PAPER cutover. Observation of the first
natural fill projection is historically inconclusive because its backing signals expired before a
crossing. Revision 0042 station trust UI/backend contracts and their continuity cutover are
production-accepted; they do not rewrite that 0041 result. Audited command 6 subsequently resumed
only Polymarket PAPER; LIVE remains blocked by its independent gates.
The desktop client is Windows-only and local-only; mutations remain fail-closed until the
backend and Credential Locker contain the same operator API key.

## Purpose

`StratusConsole` is the primary local operating surface for the paper-trading system.
Telegram remains the remote emergency interface. The desktop app must not contain trading,
risk, calibration, or settlement logic; PostgreSQL and the Python backend remain the source
of truth.

## Technology and deployment

- C# WinUI 3 on Windows App SDK.
- Packaged single-project application using the official Microsoft WinUI template and its
  supported CLI registration/run loop.
- One main window with a native `NavigationView` and page-level `CommandBar` actions.
- Light, dark, high-contrast, keyboard, mouse, and touch support through stock WinUI controls.
- Backend access only through loopback HTTP. No direct database connection and no invocation
  of arbitrary shell commands from the UI.

### Verified local build and launch loop

`dotnet build` verifies compilation but does not by itself refresh the registered packaged
application under `bin/.../win-x64/AppX`. Starting the adjacent unpackaged executable can also
fail because it lacks Windows App SDK package identity. Use the supported WinApp run target:

```powershell
dotnet run --project apps/StratusConsole/StratusConsole.csproj `
  -c Debug -p:Platform=x64
```

That command builds, synchronizes the `AppX` payload, registers the debug identity, and launches
the exact current DLL. For a compile-only CI check, continue using:

```powershell
dotnet build apps/StratusConsole/StratusConsole.csproj -c Debug -p:Platform=x64
```

The acceptance check compares the hashes of the root and `AppX` `StratusConsole.dll` files
before visual verification, preventing an older registered UI from being mistaken for the
latest build.

## Navigation

1. **Cockpit** (destination tag `Dashboard`) — API readiness, trading mode, kill switch, bankroll, exposure, open
   positions, validation gate, collector uptime, resolution/settlement evidence operations,
   immutable-ledger capacity, execution quality, and latest health issues.
2. **Markets** — active and blocked markets, station lineage, rules-change reason, and manual
   verification workflow.
3. **Signals** — recent accepted/rejected signals and structured skip reasons.
4. **Positions** — open/closed positions, fees, P&L, CLV, and settlement state.
5. **Calibration** — filter-scoped forecast calibration/Brier views plus a separate global Station
   trust table with Wilson reliability, exact selection blocks, freshness, cooldown expiry, and
   evidence hashes.
6. **Jobs** — last/next run, duration, failures, source freshness, and allowlisted manual jobs.
7. **Alerts** — durable local inbox, unacknowledged badge and filters, acknowledgement controls,
   and optional Telegram delivery state.
8. **Live** — live orders and positions, execution state, manual-redeem reminders, retrospective
   CLV/P&L metrics, and realised-equity curve.
9. **Operations** — paper/live mode requests, kill switch, resume, allowlisted job controls,
   backup/retention state, and the operator audit log.

Loopback API address, Credential Locker setup, theme, and refresh cadence are application-level
configuration; the current shell does not expose a separate **Settings** navigation destination.

## Delivery phases

### Phase A — read-only shell

- Dashboard polls `GET /healthz` and `GET /readyz`.
- Connection failures and non-ready reasons are explicit; stale data is never shown as live.
- No credentials or mutation endpoints are required.

### Phase B — monitoring API

- Add versioned `GET /api/v1/*` resources for overview, markets, signals, positions,
  calibration, jobs, and backups.
- Use bounded queries, pagination, UTC timestamps, stable DTOs, and a contract test suite.
- Polling is the initial transport; server-sent events are optional after profiling.

Implemented resources:

- `GET /api/v1/overview`
- `GET /api/v1/markets?limit=...`
- `GET /api/v1/signals?limit=...`
- `GET /api/v1/positions?limit=...`
- `GET /api/v1/calibration?limit=...`
- `GET /api/v1/orders?limit=...`
- `GET /api/v1/jobs?limit=...`
- `GET /api/v1/resolution-reconciliation?classification=...&limit=...`
- `GET /api/v1/settlement-cases?classification=...&limit=...`
- `GET /api/v1/storage-capacity`
- `GET /api/v1/forecast-provenance`
- `GET /api/v1/execution-cost-report?mode=paper|live`
- `GET /api/v1/execution-cost-report/orders?mode=paper|live&...`

The **Live** page renders live orders and positions from `/orders` and
`/positions`, flags winning closed live positions as awaiting a manual
on-chain redemption (the bot never redeems), shows the CLV/PnL retrospective
cards from `/live-summary`, and draws a realised-equity curve (a WinUI
`Polyline` on a `Canvas`) from `/live-equity`.

Legacy list collection limits are bounded to 1–200 records except calibration, which is bounded to
1–1000 and requested at 500 so all 480 current station/model/horizon rows are available;
reconciliation and settlement-case queries are bounded to 1–1000. The desktop refreshes through
loopback HTTP every 10 seconds.
Dashboard core data and diagnostic evidence are fetched independently: a missing/version-skewed
diagnostic endpoint marks only that surface `DEGRADED` and retains last-known values, while
runtime, bankroll, exposure, and risk remain visible. A full API failure marks retained dashboard
data stale instead of replacing it with an empty success-looking state.

### Revision 0034 — evidence operations

The Dashboard contains one adaptive **Resolution & evidence operations** card. It remains a
read-only projection of backend evidence and has three metrics:

- **Resolution QA** shows METAR-consistent resolved markets separately from attested official
  exact-source coverage. Raw exact values without a source hash are labelled separately and do not
  increase official coverage. The metric also shows reconciliation freshness/SLA and counts
  `exact_source_mismatch`, `clear_mismatch`, `exact_truth_gap`, `boundary_sensitive`, and
  `truth_unavailable`. Exact-source conflicts are displayed as blocking; clear mismatches require
  review; boundary-sensitive, unavailable, stale, and incomplete exact truth remain at least
  `WATCH` without pretending that the official value is known.
- **Settlement lifecycle** shows open versus resolved event cases, poll freshness/SLA and the
  oldest open case, and separates
  `venue_pending`, `source_missing`, `ambiguous`, and `manual_review`. A venue-pending event is not
  rendered as a failed settlement, while ambiguity and overdue manual review are escalated.
- **Immutable CLOB ledger** shows measured bytes against the configured budget, an accessible
  progress bar, paired-snapshot count, observed-window progress, measured growth/day and runway
  when available, plus measured free space against the physical reserve and capacity-sample
  freshness. Before the minimum 20-hour window it says that measurement/runway is pending; it
  never extrapolates from an incomplete window.

The card derives an operator state of `HEALTHY`, `WATCH`, `REVIEW`, or `BLOCKING`. It also shows
the capacity policy version, whether automatic deletion is enabled (normally `disabled`), and the
age of the latest capacity sample. This state is a compact operational summary, not an accuracy
or live-trading gate; the detailed server classifications and four-level health audit remain
authoritative.

API contracts:

- `/api/v1/resolution-reconciliation` returns full-ledger METAR/exact-source coverage and
  freshness aggregates plus classification totals and bounded per-market records with
  venue/METAR/exact implications, boundary/truth gaps, rules and evidence hashes, provenance time,
  and record hash. Item filters never alter the global aggregates.
- `/api/v1/settlement-cases` returns full-ledger open/stale/freshness aggregates plus
  classification totals and bounded per-event lifecycle records with age, attempts, transitions,
  source status, resolution-vector evidence, and record hash.
- `/api/v1/storage-capacity` returns sample age/freshness, measurement completeness, observed
  hours, database/ledger sizes, batch/snapshot counts, free bytes and the mandatory physical
  reserve, growth and 30-day projection when valid, days to budget, policy version, and the
  immutable retention rule.

Deployment acceptance on 2026-07-14 verified the packaged x64 application against revision
`20260714_0034` through the real loopback API and UI Automation. The opened
`STRATUS Operator Console` rendered the revision-0034 evidence fields, `15 open`, a fresh
immutable-ledger capacity sample, and the physical free-space reserve without a diagnostic
`DEGRADED` state. Revision 0035 replaces the ambiguous match label with separate
`METAR-consistent` and `Official exact truth` values. Optional API
isolation had already been exercised against the prior `0033` server: the dashboard retained
runtime and portfolio data while only the unavailable evidence card degraded.

### Operational block 0035 — exact coverage and freshness honesty

The revision-0035 API contract makes the distinction machine-readable rather than relying on UI
wording:

- resolution aggregates include `metar_available`, `metar_consistent`, `exact_available`,
  `exact_source_hash_available`, `exact_coverage`, oldest/latest check times, stale count, freshness
  and SLA;
- `exact_coverage` counts only exact settlement values with an attested source hash; a raw exact
  value without that hash remains visible but does not count as official coverage;
- settlement aggregates include total/open counts, oldest open case, latest poll, stale open count,
  freshness and SLA;
- aggregate values always cover the full durable ledger, while classification and limit parameters
  constrain only returned detail items.

These fields are JSON-required in the WinUI DTOs. A version-skewed API therefore degrades only the
evidence card and retains last-known core data instead of presenting missing fields as successful
zeroes. The card uses accessible help text and a polite live status. Incomplete official exact
coverage, unavailable truth, boundary-sensitive evidence, or stale polling always produces at
least `WATCH`; exact-source conflict remains `BLOCKING` and clear/ambiguous evidence remains
`REVIEW`.

Production UI Automation on 2026-07-14 confirmed the final x64 window rendered `924/924
METAR-consistent`, `Official exact truth 0/924`, `0.0% attested coverage`, both freshness/SLA
lines, `WATCH`, PAPER and gate `0/10`, with no local `DEGRADED` state. The verified application
instance was left running for the operator.

### Phase C — controlled operations

- Add a local operator token stored with Windows Credential Locker or DPAPI.
- Mutations use allowlisted commands only: stop, resume, verify market, and enqueue a named
  maintenance job.
- Every mutation requires a request id, actor, reason, confirmation dialog, and immutable
  database audit record.
- Live mode remains unavailable until all existing quantitative, legal, tax, and executor
  gates pass.

Implemented (backend):

- `POST /api/v1/commands` — enqueue an allowlisted command
  (`stop`, `resume`, `verify_market {market_id}`,
  `run_job {job: discover-markets|backfill-fees|prune-signals}`) with a
  client-generated `request_id` (idempotent), `actor`, and `reason`. Requires
  `OPERATOR_API_KEY`; returns 403 when no key is configured, so mutations can
  never be an unauthenticated call.
- `GET /api/v1/commands` — the immutable audit log (status, detail,
  executed_at). Rows are never deleted, only status-transitioned
  (`pending -> succeeded | rejected | failed`).
- The worker polls the queue every 20 seconds (`operator-commands` job),
  executes one command per transaction (a crashing command is marked failed
  instead of wedging the queue), and shares the `/verify` logic with the
  Telegram bot.
- `StratusApiClient` gained `PostCommandAsync`/`GetCommandsAsync`.
- `set_mode {mode: paper|live}` joined the command allowlist. Paper switches
  immediately; live replays the full Telegram gating server-side (operator
  legal/tax flags, configured wallet, the day-30/60 validation gate, and a
  two-command confirmation within 5 minutes) — the console can request live
  mode but can never bypass a gate.
- `/api/v1/positions` items now carry `market_date`, `kind`, and — for open
  positions — `mark_price` (side-adjusted latest midpoint) and
  `unrealized_pnl_usd`. `/api/v1/equity?mode=paper|live` generalises the
  equity series to both modes.

Implemented (desktop):

- The operator key is stored per Windows user in Credential Locker; the
  `STRATUS_OPERATOR_API_KEY` environment variable remains a development fallback.
- Command bar: `Stop` is always visible; `Resume` appears on Operations,
  `Verify market` on Markets, `Run job` on Jobs. Every action opens a
  confirmation dialog whose default button is Cancel and requires a
  free-text reason (>= 4 characters); the request id is a client-generated
  GUID and the actor is `console:<username>`.
- The Operations page appends the command audit log (status, reason,
  detail, executed time) under the runtime/audit/gate cards.
- Markets list items show the internal `#id` used by verify_market.
- A 401/403 response surfaces an explicit hint to configure
  `OPERATOR_API_KEY` / `STRATUS_OPERATOR_API_KEY`; commands are never
  retried automatically and a submit guard prevents double sends.

### Revisions 0035/0036 — prospective validation and local alert observability

Revision `20260714_0035` adds a Cockpit projection of the immutable prospective-validation
ledger. It loads `/api/v1/validation-ledger?mode=paper` independently from legacy portfolio and
edge reports and shows:

- the immutable registration boundary, exact selected cohort, protocol identity, and source
  integrity label;
- eligible/excluded/pending/closed order and independent-block counts;
- realised P&L/ROI, skill/luck, drawdown, largest-order concentration, and leave-largest result;
- an interactive cumulative trajectory whose point selection shows the contributing
  station/date/kind block.

The production baseline is deliberately empty: zero enrollments and zero outcomes under source
integrity `database-attested-order-fill+gamma+clob-v1`. The card must not substitute legacy
performance or imply that a zero sample is a passing result.

Revision `20260714_0036` adds an **Alerts** destination, an unacknowledged-count navigation badge,
local-inbox and optional-channel summaries, `unacknowledged`/`all` filters, per-row external state,
individual acknowledgement, and bounded acknowledgement-through. `Not activated` is rendered as
an expected Telegram state. It is not replaced with a green delivery-success claim.

At deployment, the API exposed 577 unacknowledged legacy alerts as `local_only`, no Telegram
runtime, zero external pending rows, and zero dead letters. This is intentional activation
isolation: opening Cockpit or reading the API cannot activate or replay Telegram.

The Cockpit summary and validation header use adaptive layouts so the five runtime/portfolio/gate
tiles and edge controls reflow rather than requiring a fixed wide canvas. Diagnostic API failures
remain local to their cards; core PAPER/kill-switch state stays visible.

Release acceptance on 2026-07-14 recorded:

- Ruff and strict mypy passing, plus 475 non-PostgreSQL and 157 clean-database PostgreSQL tests;
- production schema at the single head `20260714_0036`;
- API and worker on image
  `sha256:414a9fbd827c0b1e8d02143190657e6e527263ff1eed03d514a71eba29f40b5c`;
- a packaged x64 build and package-aware launch of a real top-level
  `STRATUS Operator Console` window with the Alerts badge;
- runtime in PAPER, kill switch off, manual health at non-blocking `warning`, and the formal live
  gate still blocked at `0/10`.

The console verifies operator visibility and control boundaries. It does not establish model
accuracy, official truth-source coverage, profitable execution, or permission to trade live.

### Revision 0037 — forecast source evidence

Cockpit loads `GET /api/v1/forecast-provenance` as an independently isolated diagnostic and
renders a dedicated **Forecast source evidence** card. It shows immutable ingest/HTTP-response/raw
byte counts, materialized daily/trajectory inputs, active probability lineage coverage, p95 HTTP
and database-seal latency, verified upstream initialization coverage, and per-model collector
activity. The badge states `SEALED`, `COLLECTING`, `STALE`, or `UNAVAILABLE`.

The card must never translate missing legacy evidence into zero cost or complete provenance.
`complete/active` coverage reaches 100% only when every current probability binds exact run,
ingest, manifest, and record hashes. Endpoint failure degrades this card and leaves the new live
gate criterion fail-closed; it does not erase the last-known core runtime/risk state.

### Revision 0038 — execution quality evidence

Cockpit loads `GET /api/v1/execution-cost-report?mode=paper|live` independently and renders the
adaptive **Execution quality** card. It shows attempts, filled/no-fill/sealed counts, maker fill
rate, eligible-seal coverage, the model-edge-to-realised-net-edge waterfall, exact fee/slippage
coverage, and 5m/30m adverse-selection coverage. Drill-down uses
`GET /api/v1/execution-cost-report/orders` and preserves source references, eligibility reasons,
null metrics, mode separation, and bounded pagination. Failure of either diagnostic degrades this
card without hiding the last-known runtime, kill-switch, bankroll, or exposure state.

The deployed first cohort contains two genuine PAPER attempts. Both are terminal no-fills and both
are immutably sealed, so the badge correctly reads `EVIDENCE INCOMPLETE`; maker/cost/markout values
remain unavailable rather than being displayed as zero. This verifies the operator/API/ledger
plumbing, not execution quality or live permission.

Probability labels must retain three separate meanings throughout Cockpit. **Exact
materialization** means the final immutable event vector and per-bucket probabilities match what
the probability job persisted. **Candidate anchor** means the particular candidate cites its own
current complete/eligible dual-token snapshot and passes `candidate-anchor-v2`; sibling liquidity
is not inferred. **Full-market coherence** means every sibling has an eligible anchor in one batch
and is required for whole-vector market benchmarks. The UI must not turn materialization or
candidate admission into a green full-market-coherence claim, or hide valid model/candidate
evidence merely because a sibling book is unavailable.

Production release acceptance on 2026-07-15 recorded:

- production schema `20260715_0038` at the single Alembic head;
- API and worker image
  `sha256:f64718d1c503acd9db7eeb4b8dfd7d82464e9a3ec977c7e9c99d82d504cd30f5`;
- guarded backup `backups/stratus_pre_0038_20260715t0615z.dump`, SHA-256
  `3F9106F9D1FFC511CE941C55E6A79EDA164E21B1E31F952079AD337C54230AF5`;
- 711 tests, Ruff, strict mypy, and Docker Compose validation passing;
- runtime PAPER evidence at two sealed no-fill attempts with state `evidence_incomplete`.

The eleven-part live gate remains unchanged and fail-closed. Revision 0038 is an evidence surface;
deployment is not proof of model edge, profitable execution, or live readiness.

### Revision 0039 — official resolution truth

Cockpit loads `GET /api/v1/official-resolution-sources` independently and renders the adaptive
**Official resolution truth** card. It shows current licensed/active/blocked/expired contracts,
attestation count, immutable raw-response count and bytes, artifact-import availability, exact
actual coverage, settlement projection count, evidence freshness, source integrity, the database
boundary, and every frozen protocol/hash identity. Its states are `EXTERNAL AUTH BLOCKED`,
`COLLECTING`, `INTEGRITY ERROR`, and isolated `UNAVAILABLE`.

The card explicitly says **METAR is not official truth**. METAR consistency remains a separate QA
diagnostic and can never satisfy exact-source coverage. A missing endpoint degrades only this card;
stored runtime, risk, and kill-switch state remain visible. The console exposes no raw response,
license bypass, manual settlement value, or source-activation toggle.

Production release acceptance on 2026-07-15 recorded:

- schema `20260715_0039` and API/worker/migration image
  `sha256:e4edd3c4e35228d2f8bea93d058c963bc6e92eb2b35986c6694b862fd79adec8`;
- source API `200`, integrity `valid`, state `disabled_external_authorization`, import disabled,
  licensed `0/155`, exact `0/155`, and zero response/projection rows;
- packaged x64 build with zero warnings/errors and a real top-level
  `STRATUS Operator Console` window left running;
- passive UI Automation confirmation of `EXTERNAL AUTH BLOCKED`, `0/155`, full protocol/hash
  detail, and the METAR warning. Freshness grammar was also normalized to `<1m ago`.

This UI proves that the evidence boundary is observable. It does not grant source permission,
prove source-product equivalence, create an official actual, establish model accuracy, or permit
live trading.

### Revision 0041 — immutable fill projection and formal lane scope

The orders surface keeps `price` as a compatibility alias for the immutable requested limit and
also exposes `requested_limit_price` and nullable `actual_fill_vwap`. Every order, position, seal,
and execution-detail view must label **Requested limit** separately from **Actual fill VWAP**. The
actual value is the share-weighted price of immutable atomic legs; the UI must not derive it from
stored notional, substitute the requested limit, or present a missing actual fill as zero.

All formal validation surfaces use the same current lane-scoped order dataset returned by the
backend. This includes the Validation gate tile/checklist, skill/luck and ROI benchmark fields,
Edge, calibration/Brier, CLV, root-cause actions, and Brier/CLV drill-downs. Each surface must expose
or preserve the effective sample boundary and predictive/execution lane identity. Missing lanes or
a forked empty sample render an explicit zero/empty current formal cohort; the console must not
recover visual continuity by loading legacy portfolio statistics.

Selection-policy reporting further intersects those exact formal order ids with intents attested to
the active selection identity and boundary. A selection `continue` retains compatible decisions; a
selection `fork` renders a new empty current selection projection without erasing the underlying
formal outcomes. A predictive or execution `fork` resets all current formal benchmark surfaces.

Operator/API execution alerts preserve evidence severity: a terminal attempt proven to have zero
filled quantity is `WARNING no_fill`; a claimed fill lacking atomic evidence is
`CRITICAL execution_evidence_unavailable`; and a cancelled order with non-zero filled quantity is
never rendered as a no-fill. Backfill diagnostics must identify ambiguous post-boundary no-intent
fills as excluded evidence rather than silently presenting reconstructed fees.

The 0041 release panel may display a candidate only when its manifest records the actual single
database head `20260715_0041`. The reviewed activation must show forks for
`predictive_validation`, `execution_evidence`, and `selection_policy`; no UI action may infer or
approve those actions from an older 0040 manifest.

Production acceptance records one Alembic head `20260715_0041`, API and worker on image
`sha256:ab2b627d77faef9d7f2114fdbf208d83947163fd81565d0f0197cec682d6b181`, and reviewed plan
`7eb012e66704fb70cf30f20fce0a6a344e369089fe2770616956b6054faea5d5`. Epoch 3 opened at
`2026-07-15T19:14:03.860671Z`; predictive, execution, and selection lanes forked, while official
truth, station lifecycle, and operational legacy continued. Continuity matched the running release
in the cutover window. Its audit was `warning` with zero critical/blocking findings, and audited
command 5 resumed PAPER at that time; this is not a claim about the later permanent steady state.

Release verification passed Ruff, strict mypy over 162 source files, 661 non-PostgreSQL tests, 227
fresh-PostgreSQL tests, the migration drill, and WinUI x64 Debug and Release builds. The retained
backup is `backups/stratus_pre_0041_final_20260715T190832Z.dump` (802,838,676 bytes; SHA-256
`526ac9d4554f252e2a2388596658b6bc41fad674d420aff23ff5c703d010a93d`).

The 0041 natural fill-projection observation is visibly historical and inconclusive. Through
`2026-07-15T19:29:36Z`, the corrected runtime showed 15 genuine intents/resting maker orders and
zero fill legs. No natural crossing was available to render Requested limit versus Actual fill
VWAP end to end. The later kill switch expired those signals, making that cohort uncollectible; the
UI must show neither pass nor failure, and a future 0042 fill cannot close or relabel it.

The following is a historical pre-0042 checkpoint. After an approximately 15-hour sleep/wake
interval, the console/backend safety state correctly
returned to stopped: the kill switch auto-engaged at `2026-07-16T10:38:35Z` because scheduler and
capacity evidence were stale, and 25 jobs were recorded as missed. Startup refresh restored
capacity to `within_budget` and refreshed discovery, observations, forecasts, probabilities, CLOB,
settlement, and reconciliation.

Audit 572 isolated an `official_resolution_truth` manifest mismatch after contract inventory grew
from 155 to 182. Reviewed plan 5
(`cba642b35b31aa17428b5c13148daef19c0e85256235873d6093151a3a80829a`; release manifest
`dcfc5302e0a4786d8017254555743d307a1d208134269047e5950f714b353e27`) opened epoch 4 at
`2026-07-16T10:43:00.712204Z`, forked only official truth, and continued the other five lanes.
After refresh, audit 573 shows `warning` with 14 findings and zero critical/blocking; continuity
matches, and 49 post-epoch-4 jobs succeeded with zero errors.

At that checkpoint the console correctly showed stopped because daily P&L was `-$96.31`, pending
the normal UTC daily-risk reset. That was a risk hold, not a continuity or fill-projection failure.
The sample had 15 resting intents/orders, zero fill legs, and zero prospective enrollments. The
subsequent 0042 quarantine closed the stranded state while preserving the historical observation
as inconclusive.

### Revision 0042 — Station trust and calibration separation (production accepted)

The Calibration destination must not use the word "reliability" for two different statistics.
The **Forecast calibration matrix** is model evidence: station/horizon sample count, bias, sigma,
and Brier. The page filter (`All`, `Mature`, `Needs data`, or `Review risk`) scopes that matrix, its
quality mix, and its forecast outlier summary. It must render every matching station; the former
six-station display cap is removed and the matrix owns a bounded vertical scroll region.

The separate **Station trust** table is selection evidence and is always global. Its header says
that the calibration filter does not change it. Forecast calibration and Station trust are separate
native `TabView` tabs inside one bounded lab surface, so the 20-row trust table owns its own scroll
instead of being clipped below the collection viewport. Each active or archived station row shows:

- lifecycle stage and active/archive state;
- observed reliability, two-sided 95% Wilson lower bound, and comparable independent-unit `n`;
- conflict count, boundary-sensitive count, unavailable-truth count, latest conflict date/kind,
  and stable reliability reasons;
- exact selection-block reason codes rather than a generic cooldown label;
- selection-evaluated local date, reliability evaluation time, latest observation time, and the
  resolution/observation block expiry when present; and
- shortened policy, source, and immutable-record hashes for operator correlation.

Boundary-sensitive and unavailable official-truth units are visibly labelled **excluded** and
non-punitive; neither is shown as a match or conflict. The explanatory InfoBar states that a proven
conflict or stale current-day observation can still block selection.

Status wording preserves authority boundaries. `COLLECTING` can be eligible for PAPER evidence
collection if the backend reports no current selection block. LIVE still requires `RELIABLE`, an
`approved` lifecycle stage, the global empirical gate, legal readiness, continuity, and risk
checks. `RELIABLE` in this table is therefore not a LIVE-ready badge.

Inactive registry rows are shown as archived and excluded from active selection-block totals.
The UI never tries to infer station trust from calibration rows. Forecast calibration and station
trust requests are isolated: if `/api/v1/stations` fails or is version-skewed, the Station trust
surface renders `DEGRADED` with the endpoint error while `/api/v1/calibration` rows remain visible.

The expected `/api/v1/stations` item contract extends the lifecycle fields with
`reliability_reasons`, `latest_conflict_at`, `latest_conflict_date`, `latest_conflict_kind`,
`reliability_policy_hash`, `reliability_source_hash`, `reliability_record_hash`, and
`selection_evaluated_for_date`. Existing score, lower-bound, sample, excluded-unit, evaluation,
cooldown, `selection_blocked`, and `selection_reasons` fields remain authoritative.

Production acceptance passed with x64 Debug and Release builds at zero warnings/errors, a packaged
process launched against the migrated 0042 API, and live API verification of all 20 active trust
rows plus 480 calibration rows. Every active row has a current-date selection evaluation, a single
shared reliability policy hash, and no active selection block; two inactive rows remain archived
and excluded from active totals. The isolated failure path keeps `/calibration` data visible when
`/stations` is unavailable. The operator API key was rotated during cutover, moved to `.env`
interpolation, and synchronized with Windows Credential Locker without recording either secret in
release evidence. Reviewed epochs 5 and 6 bind the station-selection release and its reporting
hotfix to the production image/lane identities. The natural-fill projection required by revision
0041 remains independently and historically inconclusive. Pixel-level desktop capture could not be
repeated because the Windows helper returned access denied; that UX observation limitation does
not replace or weaken the build, process, API-contract, continuity, and backend safety checks.

After production acceptance, audited command 6 completed successfully at
`2026-07-17T07:10:31.783545Z`. The Cockpit contract now reports PAPER, kill switch OFF, readiness
200, matching evidence continuity, and a non-critical `warning` audit with zero critical/blocking
findings. The first post-resume execution cycle created two resting PAPER orders with current-date
station provenance and complete epoch-6 lane attestations. No console action enabled LIVE.

The epoch-7 deterministic scheduler hotfix preserves operator liveness without presenting dependent
phases as independently runnable jobs. `observations` is the scheduled parent for observation
refresh plus child `station-reliability`; `prices-and-signals` owns child `execution`; and the
hourly `resolution-reconciliation` parent owns its minute-00 child `settlement`. There are no
independently scheduled `station-reliability` or `execution` jobs. Separate `settlement` rows at
minutes 10, 20, 30, 40, and 50 are normal settlement-only polls. The Jobs page may therefore show
both a composite parent and its child liveness record; this is evidence of ordered completion, not
duplicate scheduling.

Production acceptance used image
`sha256:061e73111d8ac0464c3bc2b2cffe92ff8370984789a9283fc734403d0859f843`, plan 9, and epoch 7.
Audited command 8 resumed PAPER only. `/readyz=200`, audit 634 had zero critical/blocking findings,
and image/cohort/lane identities matched with no frozen lane. The runtime remained `collecting`;
205 post-resume skipped signals all had current station provenance, while no executable signal or
order yet existed. The UI must present that state as healthy fail-closed collection, not as a fill,
skill, profitability, or LIVE-readiness claim.

## Safety invariants

- The app treats unreachable API, stale audit, missing runtime control, and unknown response
  versions as not ready.
- Health severity is preserved end to end: `info` and `warning` do not engage the global stop;
  `critical` and `blocking` make readiness false; only explicit `blocking` may engage the kill
  switch. Unknown malformed severity fails conservatively as `critical`.
- Destructive or financial actions never execute on page load, retry, or double click.
- Operator commands are idempotent and are handled by the worker through a command queue;
  the desktop process never starts Python jobs directly.
- `/stop` is always prominent. `/resume` requires a confirmation dialog and displays the
  current halt reason and loss-limit state.
- The desktop app cannot weaken backend risk limits or station verification.

## Phase A acceptance criteria

- The packaged x64 project builds and launches from the command line.
- A real top-level window titled `STRATUS Operator Console` launches successfully.
- Navigation remains usable at wide and narrow window widths.
- Dashboard distinguishes connecting, ready, not-ready, and unreachable states.
- Refresh is available through a native `CommandBar` and does not block the UI thread.
- The final verified app instance remains open for operator inspection.
