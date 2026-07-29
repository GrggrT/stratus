# STRATUS alert delivery protocol

Status: **deployed and release-verified on 2026-07-14; Telegram intentionally not activated**.

Implementation revision: `20260714_0036` (`20260714_0035 -> 20260714_0036`). This
revision is the single current Alembic head in the operator database. The local inbox and
acknowledgement state are running; optional external Telegram delivery remains outside the
activated boundary by explicit operator choice.

## Purpose

STRATUS separates its durable local alert inbox from optional Telegram delivery. Local
observability and acknowledgement must work without Telegram credentials, a network connection,
or a running Telegram process. Starting Telegram must not replay the legacy backlog.

`telegram_deliveries` remains the canonical inbox. The migration extends that table rather than
creating a second queue.

## Actual schema

Migration `0036` adds these nullable fields to `telegram_deliveries`:

- `acknowledged_at`: local operator acknowledgement time;
- `last_attempt_at`: database time of the most recent external claim;
- `next_attempt_at`: durable retry schedule;
- `lease_until`: current external-delivery ownership lease;
- `dead_lettered_at`: terminal retry-exhaustion time.

It also creates `notification_delivery_runtime`, keyed by channel, with:

- `activated_at`: first activation watermark for that row;
- `heartbeat_at`;
- `last_success_at`;
- `last_failure_at`;
- bounded `last_error`.

Migration `0036` inserts no runtime row. Absence of the `telegram` row is the durable
`not_activated` state. Existing inbox rows keep all new fields null and remain local-only.

Partial indexes support unacknowledged rows, externally due rows, and dead letters. Downgrade is
guarded: it is refused if any channel runtime exists or any delivery has acknowledgement,
attempt, retry, lease, or dead-letter state. This avoids silently destroying activation or
operator-delivery evidence.

## Local inbox and acknowledgement

Producers continue to enqueue idempotently by unique `event_key`. Every row is immediately
available to the local API whether or not Telegram is activated.

Acknowledgement is independent of external delivery:

- acknowledging a row does not mark it sent and does not remove it from Telegram eligibility;
- sending a row does not acknowledge it locally;
- one-row acknowledgement is idempotent and uses PostgreSQL `clock_timestamp()`;
- bulk acknowledgement requires a caller-supplied `through_id` and changes only rows whose
  `id <= through_id`, so concurrently arriving alerts are not hidden.

## Telegram activation watermark

The explicitly configured Telegram process is the activation action. On startup it calls
`activate_notification_channel(...)` in a committed transaction before producing or claiming
alerts. `INSERT ... ON CONFLICT DO NOTHING` preserves the first database-clock `activated_at`
watermark across restarts.

Only rows with `created_at >= activated_at` are eligible for Telegram claims. Pre-activation rows
remain visible and acknowledgeable in the local inbox but are permanently `local_only` for
external delivery. Neither migration, API reads, Cockpit, nor health audit activates Telegram.

## Claim, lease, retry, and dead-letter lifecycle

The consumer claims due post-activation rows atomically with `FOR UPDATE SKIP LOCKED`. A row is
eligible only when it is unsent, not dead-lettered, due by `next_attempt_at`, and unleased or past
lease expiry. Claiming:

- increments `attempts`;
- writes `last_attempt_at` from the database clock;
- clears the prior retry time;
- writes a finite `lease_until` ownership token.

The current notification loop claims at most five rows with a 120-second lease before network
I/O. Send/failure completion must present the exact claimed `lease_until`; a stale consumer cannot
complete work after ownership has changed.

On confirmed send, the row receives database-clock `sent_at`; lease, retry, and row error are
cleared; channel success and heartbeat are updated. On failure, the lease clears, bounded error
text and channel failure state are written, and attempts 1-4 schedule retries after 10 seconds,
30 seconds, 2 minutes, and 10 minutes. Failure on attempt 5 or later writes
`dead_lettered_at` and no next attempt.

The guarantee is at-least-once. A process can send externally and crash before committing
`sent_at`; lease expiry can then cause a duplicate Telegram message. The implementation does not
claim exactly-once delivery across the network/database boundary.

## Database-clock state and health

Claim due-times, leases, activation, acknowledgement, attempts, retry scheduling, sent/failure
timestamps, and API age/in-flight classification use PostgreSQL time. This avoids state changes
caused by application-host clock skew.

Channel states are:

- `not_activated`: no runtime row; expected and non-error;
- `healthy`: activated, fresh heartbeat, no current failure, no overdue post-activation backlog,
  and no dead letters;
- `stale`: activated but heartbeat missing or older than two minutes;
- `degraded`: current failure, post-activation backlog older than two minutes, or dead letters.

Health audit reports stale consumer, overdue backlog, dead-letter, and current-failure issues only
after activation. These diagnostics are non-blocking: they do not change readiness, the kill
switch, risk limits, or the live gate. The optional channel's `not_activated` state is not a
health warning.

## Operator API

Read endpoint:

`GET /api/v1/alerts?state=unacknowledged|all&limit=1..500`

The response is based on a database-clock `generated_at` and includes:

- total and unacknowledged counts;
- oldest unacknowledged timestamp and age;
- channel state, activation/heartbeat/success/failure timestamps, and bounded error;
- post-activation pending/backlog and dead-letter counts;
- newest-first items with local acknowledgement, attempt, retry, lease, dead-letter, and error
  fields;
- derived external state: `local_only`, `pending`, `in_flight`, `retry_scheduled`, `sent`, or
  `dead_letter`.

Authenticated mutation endpoints:

- `POST /api/v1/alerts/{delivery_id}/ack`;
- `POST /api/v1/alerts/ack-through` with JSON `{"through_id": <positive id>}`.

Operator mutations remain disabled until `OPERATOR_API_KEY` is configured.

## Cockpit

Cockpit has an Alerts destination and an unacknowledged-count navigation badge. It shows separate
local inbox, optional Telegram channel, and external queue cards; supports
`unacknowledged`/`all` filtering; renders per-item external state and retry information; and
offers individual plus bounded bulk acknowledgement.

`Not activated` is displayed as an expected optional state, not an error. API incompatibility or
failure is rendered as degraded/unavailable rather than fabricated zero counts.

## Verification and deployment acceptance

Release acceptance was recorded on 2026-07-14:

- Ruff and strict mypy passed; 475 non-PostgreSQL tests and 157 clean-database PostgreSQL tests
  passed through the full Alembic chain. Coverage includes legacy isolation, concurrent claims,
  exact lease ownership, retry/dead-letter transitions, acknowledgement independence,
  database-clock summaries, API responses, and the guarded downgrade.
- The pre-migration backup
  `backups/stratus_pre_0036_20260714T202226Z.dump` is 318,697,098 bytes with SHA-256
  `18CCB8FE674BF339F5B9DF55ECDBED278EDBB2B13D2E656437C54F0941C1BC31`; its full restore vector
  matched the source database exactly.
- Production is at `20260714_0036`. Immediately after migration, all 577 legacy inbox rows were
  still unsent and unacknowledged with null attempt, retry, lease, and dead-letter state. No
  `notification_delivery_runtime` row was created, so every legacy row is durably `local_only`.
- API and worker run candidate image
  `sha256:414a9fbd827c0b1e8d02143190657e6e527263ff1eed03d514a71eba29f40b5c`. The local alerts API is
  readable and reports 577 total/unacknowledged legacy rows, zero post-activation pending rows,
  zero dead letters, and channel state `not_activated`.
- The rollback tag is `stratus:rollback-pre-0036-20260714t195534z`, pointing to image
  `sha256:faffffee2741ebf8c14bb33d3f2df8219fdc351885a34e0e080069162572f395`.
- The packaged x64 console build passed and a real top-level `STRATUS Operator Console` window
  rendered the local Alerts navigation badge. Optional Telegram was not started.

The post-deployment manual health result is a non-blocking `warning`; the unactivated optional
Telegram channel is not itself an error. Alert delivery is operational observability, not
evidence of forecasting accuracy, profitability, or live readiness.

## Rollout and rollback

Deploy in this order:

1. take and restore-test a backup;
2. keep the optional Telegram profile stopped;
3. apply migration `0036` and attest that no runtime row was created;
4. deploy API, worker, and Cockpit and verify local visibility/acknowledgement;
5. activate Telegram only as a separate, explicit operator decision with valid credentials.

Steps 1-4 were completed for the 2026-07-14 release. Step 5 remains intentionally unperformed;
the 577 pre-activation rows are local-only and will not be replayed if Telegram is activated in
the future.

An application rollback may retain schema `0036`; older code simply lacks the new inbox and
delivery-state behavior. A schema downgrade is allowed only while there is no runtime and no new
acknowledgement/delivery state. Once that evidence exists, roll forward or restore a verified
pre-migration backup.

Alert delivery is observability infrastructure. It never authorizes trading and never modifies
model, signal, execution, or risk decisions.
