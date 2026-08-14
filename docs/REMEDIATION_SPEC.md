# STRATUS remediation and modernization spec

Status: the original remediation items and the eight roadmap follow-ups are
implemented. Later least-privilege and evidence-continuity hardening through
revision 0044 is recorded in the acceptance addendum below.

Source: full-codebase review of 2026-07-06 (all money paths read in full;
88 tests green and ruff clean at the time of writing). This is a historical
baseline, not the current release-acceptance total.

Related follow-up: `docs/QUALITY_ROADMAP.md` defines the post-remediation
quality plan: data reconciliation, model validation, signal hardening,
operator console, and live-readiness gates.

## Goals

1. Close the one externally reachable attack surface (Postgres).
2. Remove the two failure modes that get worse silently over time
   (unbounded latest-row scans, all-or-nothing market discovery).
3. Make station verification honest: a verification must not outlive the
   rules text it was granted for.
4. Make builds and CI reproducible and boring.

Non-goals of the original remediation: changes to trading semantics (sizing,
edge thresholds, gate criteria) and LIVE execution semantics. A live executor
now exists, but it remains fail-closed behind continuity, quantitative, legal,
credential, risk, and explicit operator gates; this document grants no LIVE
authority.

## Implemented

### 1. Network exposure of Postgres (security, high)

`docker-compose.yml` published `5432:5432` on all host interfaces with the
static password `stratus`. The database stores the kill switch, execution
mode, and calibrations — DB access equals trade control.

- Postgres is now bound to `127.0.0.1` only.
- `POSTGRES_PASSWORD` has no default. Compose refuses an absent, short,
  non-URL-safe, or known-default owner credential. The owner secret is exposed
  only to PostgreSQL, one-shot migration/provisioning, and the backup service;
  application runtimes receive a separate file-mounted database-qualified
  credential.
- Runtime services use deterministic database-qualified roles and an ignored
  password file. Generic `DATABASE_URL` is not a settings source, preventing a
  legacy owner DSN from reaching API, worker, Telegram, or job entrypoints.
  Every physical runtime connection rechecks the database ACL assertion.
- Production HBA admits the exact database-qualified runtime login only to
  `stratus` and rejects the complete runtime-role family for every other
  database over local sockets, IPv4, and IPv6.
- `alembic.ini` no longer carries a hardcoded DSN; `migrations/env.py`
  accepts only `MIGRATION_DATABASE_URL`.

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

`.github/workflows/ci.yml` runs Ruff, strict mypy, pytest, Compose validation,
and a reproducible Docker build with uv on every push and pull request. A
separate workflow compiles the WinUI operator console.

## Roadmap follow-up (implemented 2026-07-06)

All eight roadmap items landed in the follow-up pass:

1. **Shared engine in the scheduler process.** All scheduled jobs use the
   shared scheduler engine/session factory; composite parent/child ordering is
   documented in the evidence-continuity protocol. CLI entry points keep the
   engine-per-invocation behaviour via `db/session.py::session_factory_scope`.
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
   `docker compose --profile infrastructure up -d postgres` plus a
   `stratus_test` database.
2. **Actuals-vs-resolution reconciliation.** The health audit joins
   resolved markets to our METAR-derived `daily_actuals` and raises
   `actuals_resolution_mismatch` when a market resolved against what our
   own observations imply by more than 0.25°C from the nearest bucket
   boundary (`services/reconciliation.py`). This watches the key epistemic
   risk: calibrating on METAR while Polymarket resolves on official
   climate reports.
3. **Off-site backups.** Opt-in `backup-offsite` compose service
   (`--profile offsite`) accepts only the rclone remote named `crypt`
   (`OFFSITE_REMOTE=crypt:<path>`, config in gitignored `rclone.conf`). It
   validates the local manifest hashes, download-verifies the encrypted remote
   copy, and publishes `COMPLETE` last. A partial remote prefix is never an
   accepted restore point, so a dead host disk does not silently turn an
   interrupted plaintext upload into the only backup.

## Remaining recommendation in this original remediation scope

- Before live trading: re-read the live-readiness checklist in
  `AUSTRIA_LIVE_READINESS.md`.

## Acceptance criteria

- `docker compose up` on a fresh host exposes no database port beyond
  loopback. Rotating `POSTGRES_PASSWORD` affects PostgreSQL,
  migration/provisioning, and backup only; application services continue to
  use the separately rotated runtime-password file.
- API, worker, Telegram, and Binance containers run non-root and expose no
  owner DSN/password, generic `DATABASE_URL`, migration DSN, or Docker socket.
- Signal generation latency does not grow with `market_prices` table size
  (verify with `EXPLAIN`: index scan behind `DISTINCT ON`).
- Discovery run with one poisoned market fixture still upserts all other
  markets and logs exactly one `unparseable` warning.
- A rules edit on a verified market flips it to `BLOCKED` within one
  discovery cycle and produces exactly one Telegram alert; `/verify`
  re-approval then survives subsequent unchanged re-discoveries.
- `pytest` and `ruff check` pass locally and in CI; the Docker image builds
  from `uv.lock` only.

## Revision 0044 acceptance addendum — 2026-07-20

Revision 0044 was migrated only after a 1,572,434,322-byte pre-migration custom-format backup had
passed catalog inspection and full isolated restore. The database retained 263/263 contracts, 144
positions, and the same canonical contract hash across migration. Production HBA contains nine
valid rules and limits every `stratus_app_[0-9a-f]{32}` runtime role to database `stratus`; app containers run as
uid/gid 10001 with no owner secret or generic DSN. Safe catalog/authentication checks and isolated
PostgreSQL tests prove the boundary without destructive probes against production data.

The first restricted worker exposed an operational defect: the capacity query evaluated privileged
`current_setting('data_directory')` despite an explicit path override. It failed closed under the
kill switch and was stopped before execution resumed. The fix binds the explicit path without
granting `pg_read_all_settings`; capacity enforcement and its callers are now hashed into the
`operational_legacy` continuity lane. A six-continue preview that revealed the missing source
closure was never activated.

The capacity-corrected image first passed reviewed plan 18 and epoch 11. Subsequent backup review
found that the owner-only sidecar still lacked cross-process serialization, bounded retry,
retention-floor, and atomic verified off-site semantics. No backup was started from that candidate.
The epoch-13 accepted image is
`sha256:c5076ba953fe44dca9b885c1c9823e2bca82f84f5ecc90fe70ddc89c17bcf2f5`,
accepted through reviewed plan 20 and epoch 13. Only `operational_legacy` forked; all five evidence
lanes continued exact parent identities. Capacity is fresh and within budget, continuity exactly
matches, and the bounded accuracy audit completed with 33,392 reports and 118 drift events.

Final verification is 843 unit/static tests, Ruff, strict mypy over 170 source files, the complete
275-case PostgreSQL matrix across its production-HBA and isolated-login contours, 23 focused
post-closure continuity/capacity PostgreSQL tests, rendered shell syntax in the pinned tool images,
Compose validation, Docker build, and a zero-error WinUI Release x64 build. After the weekly reset,
fresh pre- and post-cycle audits had zero critical/blocking/loss findings. Audited command 12
resumed only Polymarket PAPER; Binance stayed `backtest + kill`, both readiness probes return 200,
and the first natural price/execution cycle succeeded without creating any LIVE state. This
addendum grants no LIVE authority.

The first hardened daily backup was then atomically published as
`backups/stratus_20260720T041256Z_9379ef5bda23_1.backup` (1,265,297,862 bytes; SHA-256
`f422dd32866a697f12d2d3077901512fadec929b21c9d05ffb0c87c538e9b7b2`; 1,074 TOC entries) and
fully restored into an isolated PostgreSQL 16 instance. Schema head, all 79 public tables,
index/constraint/trigger validity, epoch 13/78 lanes, and zero LIVE state were verified before the
temporary restore volume was removed. The recurring sidecar is healthy and no owner client session
remains connected while it sleeps.

### Post-acceptance backup incident and epoch 14 corrective release — 2026-07-20

The epoch-13 record above is a valid historical acceptance snapshot, not the current release. A
later Docker restart exposed a backup publication defect: a zero-byte archive directory and a
health marker had been published even though the dump had failed. Both artifacts were retained for
forensics and quarantined as
`backups/.rejected-zero-byte-stratus_20260720T071344Z_9379ef5bda23_1.backup` and
`backups/.stratus-backup-health.rejected-20260720T071344Z`; neither is an accepted restore point.
The root cause was POSIX `errexit` suppression inside a function invoked as the condition of an
`if` statement.

The corrected backup contract gives every critical producer command an explicit failure exit,
captures the function status outside conditional context, and revalidates the complete restore
point before success is reported. Publication and its manifest-bound health marker are atomic;
noncanonical names, symbolic links, empty archives, unreadable catalogs, mismatched hashes or
schema heads, and abandoned partials are rejected. Retention counts accepted restore points only.
Off-site copies remain unhealthy when the remote store is empty, receive a lightweight hourly
size check when their `COMPLETE` marker still matches, and receive one rotating full download scrub
per day from state retained in the `stratus-offsite-state` volume; every fresh upload still receives
a full download verification.

The PostgreSQL owner credential was treated as exposed during diagnostics and rotated without
printing either the replacement or the retired value. The new credential was accepted, the old
one was rejected, stopped containers carrying the retired secret were removed, and the separate
least-privilege runtime role was reprovisioned. No owner client backend remained connected after
the release work.

The corrective application image is
`sha256:cdcd9d48f6e2aa56c5f54519db88a228e0652f44dc53f4cf209ae5f6e3d1131a`, created at
`2026-07-20T08:25:07.307215542Z` (191,513,285 bytes). Reviewed plan 21 has plan hash
`b1102b7392e277a58ef6dec3e669bd79c54344ca5f70be84171217d0c4a780b0` and opened epoch 14
(parent 13) at `2026-07-20T08:57:06.446500Z`. Only `operational_legacy` forked, to identity
`1d6fc35e8464917f0fe83946ac356d56697c100ee94d779c7386f77b6d1f8884`; the
`execution_evidence`, `official_resolution_truth`, `predictive_validation`, `selection_policy`,
and `station_lifecycle` lanes continued their exact parent identities and boundaries. The lane set
is complete, image/cohort match, no lane is mismatched or frozen, and the gate reason is
`evidence_continuity_match`.

The first post-fix accepted restore point is
`backups/stratus_20260720T085827Z_0d352caf5b71_1.backup`. It completed at
`2026-07-20T09:10:59Z`; its 1,276,357,110-byte archive has SHA-256
`cb286806568a688870e20ece21d6711f5bfea740769cbbb87c1432cb9a5a432c`, and its 89,396-byte,
1,074-entry catalog has SHA-256
`bba08542e0c364e949b14d6aabc0a711a66b5aef7b4ef77efeed1b9c4dd03611`. The manifest has
SHA-256 `9b8cf4f8dd4a3c2159b17c1fc37ba651239f0b1eb9e7ad11cafcf6648829ca62` and records schema
head `20260719_0044` before and after the snapshot. The format-v1 health marker points to this exact
directory and manifest.

An independent full restore into an isolated PostgreSQL 16.13 container with no network and no
host port verified head `20260719_0044`, all 79 public tables, 14 epochs, 17 plans, 84 lanes, 290
resolution contracts, 147 positions, 1,336 orders, 132,048 signals, zero invalid indexes, zero
unvalidated constraints, zero disabled user triggers, and zero LIVE positions, orders, or intents.
The temporary restore container and volume were removed after verification.

After bounded capacity and overdue-execution-evidence remediation under the kill switch, audit 711
had zero critical or blocking findings. Audited command 14
(`paper-resume-epoch14-20260720t1006z`) resumed only eligible Polymarket PAPER at
`2026-07-20T10:10:07.974026Z`; Binance remained `backtest + kill`. At the final observed runtime,
API, PostgreSQL, backup, and worker services were healthy, `/healthz` and `/readyz` returned 200,
audit 712 had zero critical/blocking/loss findings, and all 21 post-resume orders were PAPER. LIVE
positions and nonterminal LIVE orders remained zero. The one post-resume job error was a
fail-closed `daily-actuals` rejection for 14 station-days with fewer than 20 observations, not an
execution or integrity failure. This corrective release still grants no LIVE authority.
