# STRATUS Operator Console

Status: Phase A, Phase B monitoring, and Phase C controlled operations are implemented.
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

## Navigation

1. **Dashboard** — API readiness, trading mode, kill switch, bankroll, exposure, open
   positions, validation gate, collector uptime, and latest health issues.
2. **Markets** — active and blocked markets, station lineage, rules-change reason, and manual
   verification workflow.
3. **Signals** — recent accepted/rejected signals and structured skip reasons.
4. **Positions** — open/closed positions, fees, P&L, CLV, and settlement state.
5. **Calibration** — Brier by station/horizon, reliability diagram, CLV, ROI, and gate status.
6. **Jobs** — last/next run, duration, failures, source freshness, and allowlisted manual jobs.
7. **Operations** — kill switch, resume, backup status, retention, and operator audit log.
8. **Settings** — loopback API address, credential storage, theme, refresh cadence.

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

The **Live** page renders live orders and positions from `/orders` and
`/positions` and flags winning closed live positions as awaiting a manual
on-chain redemption (the bot never redeems).

All collection limits are bounded to 1–200 records. The desktop refreshes through loopback
HTTP every 10 seconds and clears stale collection data after a failed refresh.

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

## Safety invariants

- The app treats unreachable API, stale audit, missing runtime control, and unknown response
  versions as not ready.
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
