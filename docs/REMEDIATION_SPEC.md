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

## Roadmap (recommended, not implemented here)

1. **Shared engine in the scheduler process.** Every job invocation builds
   and disposes its own `AsyncEngine` (~3-4 pools/minute at steady state).
   Refactor jobs to accept an optional `session_factory`, defaulting to a
   process-wide engine in `runtime.py`; CLI entry points keep building
   their own. Pure plumbing, touches all 15 jobs — do it in one dedicated
   change.
2. **Depth checks against the executed book.** BUY_NO signals are priced
   and depth-checked from the YES book (`1 - best_bid`, `bid_depth`) but
   executed against the NO token's ask book. Fine for paper; before live,
   fetch the executed token's book for the 3x-depth check.
3. **Loss-limit day boundaries.** Daily/weekly loss limits reset at 00:00
   UTC, which splits a bad New York evening across two "days". Decide
   whether the accounting day should follow the venue day (ET) and encode
   the decision in `services/risk.py`.
4. **Signals table retention.** ~1 row/market/minute (mostly `SKIPPED`).
   Add a retention job (e.g. delete `SKIPPED` older than 30 days) or
   partition by month before the table gets expensive.
5. **Settlement escalation.** A market that Gamma stops returning, or that
   resolves without exact 1/0 outcome prices, is retried every 10 minutes
   forever without an alert. Alert on markets more than N hours past
   `resolves_at` that are still unresolved.
6. **ICAO extraction tightening.** `_STATION_URL_PATTERN` is
   case-insensitive and matches any 4-char path segment (`/data` ->
   candidate `DATA`), producing avoidable `station_conflict` blocks and
   `/verify` toil. Drop `IGNORECASE` and/or validate candidates against the
   known station table before conflict counting.
7. **Fail-closed runtime control.** `get_runtime_control` returns
   permissive defaults when the `runtime_control` row is missing. Paper-safe
   today; before live, make a missing row equivalent to kill switch ON.
8. **Type checking.** The codebase is fully typed; adding `mypy --strict`
   (or pyright) to CI is cheap now and expensive later.

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
