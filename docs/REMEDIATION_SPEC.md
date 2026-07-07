# STRATUS remediation and modernization spec

Status: items in "Implemented" landed together with this document; items in
"Roadmap" are recommended follow-ups that need an owner decision or a
migration and are intentionally not bundled here.

Source: full-codebase review of 2026-07-06 (all money paths read in full;
88 tests green, ruff clean at the time of writing).

## Goals

1. Close the one externally reachable attack surface (Postgres).
2. Remove the two failure modes that get worse silently over time
   (unbounded latest-row scans, all-or-nothing market discovery).
3. Make station verification honest: a verification must not outlive the
   rules text it was granted for.
4. Make builds and CI reproducible and boring.

Non-goals: any change to trading semantics (sizing, edge thresholds,
gate criteria), and anything that only matters for the live executor,
which remains intentionally undeployed.

## Implemented

### 1. Network exposure of Postgres (security, high)

`docker-compose.yml` published `5432:5432` on all host interfaces with the
static password `stratus`. The database stores the kill switch, execution
mode, and calibrations — DB access equals trade control.

- Postgres is now bound to `127.0.0.1` only.
- `POSTGRES_PASSWORD` is parameterized via the compose `.env`
  (default remains `stratus` for local dev; set a real value on any
  shared host). `DATABASE_URL` for app containers and `PGPASSWORD` for the
  backup sidecar derive from the same variable.
- `alembic.ini` no longer carries a hardcoded DSN; `migrations/env.py`
  always injects the URL from application settings.

### 2. Unbounded latest-row scans (performance, high)

Three call sites loaded every `model_probs` / `market_prices` row for the
relevant markets and reduced to "latest per market" in Python. Prices are
written every minute, so these scans grow linearly with market lifetime and
run inside the per-minute signal loop.

Replaced with Postgres `DISTINCT ON (market_id) ... ORDER BY market_id,
ts DESC` (the pattern already used by `recompute_probs`):

- `jobs/generate_signals.py` — latest probability and price per market.
- `services/telegram_reports.py::_resolved_probability_rows` — latest
  pre-resolution probability per resolved market; the `ts <= resolves_at`
  filter moved from Python into SQL.
- `jobs/audit_health.py` — latest probability per active market.

### 3. All-or-nothing market discovery (availability, high)

One unparseable market (a new bucket-title format, a missing `endDate`)
raised out of the per-market loop in `jobs/discover_markets.py`, rolled
back the whole transaction, and stopped discovery of *all* markets,
including updates to already-tracked ones.

- Market-level parse failures are now isolated per market: logged with the
  event/market id, counted as `unparseable`, the run continues.
- An event with a malformed `markets` array is skipped with a warning
  instead of aborting the run.
- Fail-closed behaviour per market is unchanged: an unparseable market is
  simply not created/updated, never traded.

### 4. Sticky station verification (data integrity, high)

`repositories/markets.py::upsert_market` kept `station_verified=True`
forever once granted (unless the event-level station id changed). If
Polymarket edits a market's rules mid-life — which happens during disputes —
the bot kept trading against the old resolution source.

New invariant: **a verification applies to the exact rules text it was
granted on.**

- If an existing verified market arrives unverified from the parser *and*
  its `rules_raw` or `resolution_source` changed, verification is revoked:
  `station_verified=False`, status `BLOCKED`, reason preserved
  (fallback `rules_changed`).
- If the rules text is unchanged, verification stays sticky — this keeps
  the manual `/verify` workflow working (re-discovery does not undo an
  admin approval).
- A revocation enqueues a Telegram alert keyed by a digest of the new
  rules text, so each distinct rules edit alerts exactly once. This also
  covers the pre-existing gap where re-blocked old markets never alerted
  (the alert query filters on `created_at`, which is Polymarket's
  creation time).

### 5. Telegram daily-report busy loop (efficiency, medium)

After 08:00 Europe/Berlin the notification loop built the full daily report
(heavy `ModelProb x DailyActual` join) every 5 seconds and threw it away
because the enqueue is idempotent. The loop now checks for the delivery key
first (`repositories/telegram.py::has_delivery`) and builds the report only
when it is actually going to be enqueued.

### 6. Fire-and-forget scheduler listener tasks (correctness, medium)

`runtime.py` created `persist_job_event` tasks without holding a reference;
asyncio keeps only weak references, so a task could be garbage-collected
mid-flight and job-run records / failure notifications silently lost.
Tasks are now held in a set (discarded on completion) and drained on
shutdown before the engine is disposed.

### 7. Reproducible container builds (supply chain, medium)

`pip install .` resolved dependencies at image build time, ignoring
`uv.lock`. The Dockerfile now installs with `uv sync --frozen --no-dev`
(uv binary pinned; dependency layer cached separately from source), so the
image contains exactly the locked versions that were reviewed and tested.

### 8. CI (process)

`.github/workflows/ci.yml` runs `ruff check` and `pytest` with uv on every
push and pull request.

## Roadmap follow-up (implemented 2026-07-06)

All eight roadmap items landed in the follow-up pass:

1. **Shared engine in the scheduler process.** All 13 scheduled jobs accept
   an optional `session_factory`; `runtime.py` creates one engine and hands
   its factory to every job (and to the event listener). CLI entry points
   keep the engine-per-invocation behaviour via
   `db/session.py::session_factory_scope`.
2. **Depth checks against the executed book.** `execute_signals` re-checks
   the 3x-depth rule against the book the order actually walks
   (`services/execution.py::usable_ask_depth_usd`); a thin executed book
   leaves the signal pending until its TTL.
3. **Loss-limit day boundaries.** The accounting timezone is now the
   `RISK_TIMEZONE` setting (`services/risk.py::period_starts`). Per the
   operator's decision (2026-07-06) the default is `America/New_York`,
   aligning the daily reset with the venue day.
4. **Signals table retention.** `stratus-prune-signals` (scheduled daily at
   03:30 UTC) deletes SKIPPED/EXPIRED signals older than
   `SIGNAL_RETENTION_DAYS` (default 30) that never produced an order.
5. **Settlement escalation.** The health audit raises `settlement_stale`
   for markets more than 12 hours past `resolves_at` that are still
   unresolved.
6. **ICAO extraction tightening.** Station patterns are case-sensitive for
   the code itself; lowercase path segments (`/data`, `/wrh1`) no longer
   become candidates.
7. **Fail-closed runtime control.** A missing `runtime_control` row (seeded
   by migration 0009, so always abnormal) now reads as kill switch ON with
   `halted_reason="runtime_control row missing"`.
8. **Type checking.** `mypy --strict` is clean over the whole tree and
   runs in CI. The findings it surfaced on the way — including two real
   `str | None` argument bugs in `bootstrap_iem` — were fixed.

## Hardening follow-up, part 2 (implemented 2026-07-06)

1. **Postgres integration tests.** `tests/integration/` runs the real
   Alembic chain against a live Postgres and round-trips every
   ON CONFLICT constraint target, DISTINCT ON, the model-probs advisory
   lock, and the retention delete — the SQL the mock-based unit tests can
   only compile, never execute. Activated by `STRATUS_TEST_DATABASE_URL`;
   CI provides a `postgres:16` service container, locally use
   `docker compose up -d postgres` plus a `stratus_test` database.
2. **Actuals-vs-resolution reconciliation.** The health audit joins
   resolved markets to our METAR-derived `daily_actuals` and raises
   `actuals_resolution_mismatch` when a market resolved against what our
   own observations imply by more than 0.25°C from the nearest bucket
   boundary (`services/reconciliation.py`). This watches the key epistemic
   risk: calibrating on METAR while Polymarket resolves on official
   climate reports.
3. **Off-site backups.** Opt-in `backup-offsite` compose service
   (`--profile offsite`) copies verified dumps to any rclone remote
   (`OFFSITE_REMOTE`, config in gitignored `rclone.conf`), so a dead host
   disk no longer takes the backups with it.

## Remaining recommendations

- Before live trading: re-read the live-readiness checklist in
  `AUSTRIA_LIVE_READINESS.md`.

## Acceptance criteria

- `docker compose up` on a fresh host exposes no database port beyond
  loopback; changing `POSTGRES_PASSWORD` in `.env` propagates to app,
  migrate, and backup containers.
- Signal generation latency does not grow with `market_prices` table size
  (verify with `EXPLAIN`: index scan behind `DISTINCT ON`).
- Discovery run with one poisoned market fixture still upserts all other
  markets and logs exactly one `unparseable` warning.
- A rules edit on a verified market flips it to `BLOCKED` within one
  discovery cycle and produces exactly one Telegram alert; `/verify`
  re-approval then survives subsequent unchanged re-discoveries.
- `pytest` and `ruff check` pass locally and in CI; the Docker image builds
  from `uv.lock` only.
