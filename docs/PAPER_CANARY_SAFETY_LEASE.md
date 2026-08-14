# Polymarket PAPER canary safety lease

Status: implemented, fail-closed, not a LIVE authorization mechanism.

## Purpose

The release canary may turn off the global and Polymarket kill switches only
for one reviewed PAPER run. Authority is represented by one short PostgreSQL
DB-clock lease. It is independent of the worker and expires unless the
collector heartbeats it while the independent safety-control watchdog remains
fresh.

The lease never authorizes LIVE, Binance, a mode change, or a global resume.

## Safety invariants

- Readiness, acquisition, and exact resume require zero pending Signals in
  addition to exact fail-closed PAPER controls and zero LIVE inventory. A
  pending cleanup survivor can never be admitted into a new execution window.
- PostgreSQL `clock_timestamp()` is authoritative. TTL is 1-30 seconds;
  production uses 15 seconds and a 5-second collector heartbeat. The collector
  also heartbeats between every bounded Docker inspect, SQL/HTTP coherence
  call, safety HTTP call, resource sample, and worker-log read; a failed
  checkpoint aborts collection rather than continuing on expired authority.
- A PostgreSQL transaction advisory lock serializes acquire, heartbeat,
  resume, release, watchdog expiry, and every Polymarket emergency stop,
  including the empty-active-table race.
- Acquisition binds the exact resume `request_id`, actor, reason, terminal
  release `request_id`, and the exact resume payload
  `{"venue":"polymarket"}`.
- The first heartbeat and resume require `watchdog_at > acquired_at` and a
  fresh watchdog observation.
- The nonce exists only in request memory. Only its SHA-256 digest is
  persisted or returned.
- Watchdog and collector direct-stop request IDs are cryptographically random,
  persisted, hidden from API responses, and protected by a database reservation
  trigger. Every lease request ID remains reserved after terminalization and
  cannot be reused across request-ID columns. Release and watchdog STOPs require
  their exact precommitted IDs.
- Applying migration 0047 forms a monotonic PAPER-only latch immediately,
  including when the lease table is empty: database triggers reject every
  unleased kill-off and every new non-PAPER mode or live confirmation.
  Polymarket disablement is rejected only while a canary lease is active;
  terminal-state disablement, kill-on, confirmation clearing, and recovery
  back to PAPER remain available.
- Execution reads always require an active, unexpired, independently observed
  lease whose watchdog observation is at most seven seconds old. An empty,
  expired, unobserved, or watchdog-stale lease set is killed. Global and
  Polymarket venue snapshots apply the same defence.
- Runtime Signal reservations and exposure-bearing PAPER Order/Intent,
  FillLeg, and Position mutations also pass a `DEFERRABLE INITIALLY DEFERRED`
  commit fence. At commit it takes the shared side of the emergency STOP
  advisory lock, then revalidates the exact DB-clock lease, fresh watchdog,
  global/Polymarket PAPER kill-off controls, and Binance
  `enabled+backtest+kill`. If STOP/expiry wins, the execution transaction
  aborts; if execution obtains the shared lock first, STOP waits for that
  commit and then seals the system. Exposure commits require PostgreSQL
  `READ COMMITTED`; repeatable-read/serializable transactions fail closed so
  a stale MVCC snapshot cannot survive STOP. Runtime-originated LIVE exposure is
  rejected by the same trigger; owner bootstrap/test data and risk-reducing
  reconciliation of pre-existing LIVE inventory remain available.
- Runtime/global and runtime/Polymarket authority mutations take the exclusive
  side with `pg_try_advisory_xact_lock` and fail immediately with SQLSTATE
  `55P03` while an exposure commit holds the shared side. Non-null global or
  venue halt reasons cannot be cleared or rewritten by the runtime login.
- Runtime cannot delete Order/Position inventory, including legacy-synthetic
  rows, rewrite Signal decisions/provenance or Order/Position identity, shrink
  cumulative fill/cost fields, fabricate terminal lifecycle state, or append a
  PAPER bankroll value that disagrees with immutable settlement projection.
  PAPER Order inserts require an exact intent at commit, PAPER Positions must
  equal the aggregate immutable FillLeg projection, and resolved-position PnL
  and clocks must match the attested Gamma outcome vector. Owner-only bootstrap
  and maintenance retain the trusted bypass.
- Any authenticated emergency stop with a fresh request ID, or a generic
  legacy/direct stop, atomically sets global and Polymarket kill switches,
  clears live confirmations, expires pending signals, requests cancellation of
  open/pending LIVE orders, rejects the exact pending resume, and terminalizes
  the active lease as `revoked_stopped`.
- A pending `stop` audit can reach `succeeded`, `rejected`, or `failed` from
  the exact runtime login only while executing the owner `SECURITY DEFINER`
  STOP primitive. Direct runtime DML cannot forge a terminal STOP audit. The
  worker and safety-control paths remain atomic and exact replay remains
  idempotent.
- Watchdog expiry terminalizes as `expired_stopped`, or `expired_safe` when
  controls were already fail-closed.
- Successful completion is one authenticated transaction: reject an
  outstanding bound resume, audited Polymarket STOP, then terminal state
  `released` linked to that stop command. There is no unsafe
  stop-then-release crash window.
- Binance rows are outside the stop lock/update set and must remain byte-for-
  byte unchanged.

## Control-plane API

The independent `safety-control` service is host-loopback only and requires
the exact `X-API-Key`. Its API key and database login/password are distinct
from the operator API and runtime database credentials; the worker/API never
receive the safety secrets, and safety-control never receives the runtime
database secret.

The safety database login has direct `SELECT` only on the lease audit table
and no control-table, command-table, sequence, or generic DML authority. Its
six allowed mutations are narrow `SECURITY DEFINER` functions that verify the
exact safety session login. Conversely, the safety login cannot create,
rewrite, or delete an operator resume command.

Production opens two independently boundary-verified, one-connection pools for
that same least-privilege login. Watchdog/acquire/heartbeat/release use only the
control pool; emergency STOP has a reserved pool that those operations cannot
borrow. This keeps an authenticated control request queue from consuming STOP
capacity.

- `POST /api/v1/canary-leases/acquire`
  - body: `lease_id`, `run_id`, `acquire_request_id`, `resume_request_id`,
    `direct_stop_request_id`, `release_request_id`, `resume_actor`,
    `resume_reason`, `nonce`
- `POST /api/v1/canary-leases/{lease_id}/heartbeat`
  - body: `run_id`, `nonce`
- `POST /api/v1/canary-leases/{lease_id}/release`
  - body: `run_id`, `nonce`, `release_request_id`, `successful: true`
  - this endpoint is the atomic terminal STOP + release operation
- `POST /api/v1/emergency-stop`
  - remains the independent, idempotent Polymarket-only emergency path;
    a fresh `request_id` need not equal the lease's hidden collector
    `direct_stop_request_id`, but it must not match another current or
    historical lease request ID;
  - the exact precommitted `direct_stop_request_id` remains usable by the
    authenticated canary barrier after a matching lease is already
    `released`, `expired_stopped`, `expired_safe`, or `revoked_stopped`.
    PostgreSQL first proves the historical terminal shape/linkage, appends or
    replays the canonical direct-STOP audit, and never rewrites the immutable
    historical lease state or its original release/watchdog/runtime stop link;
  - critical controls, audit, and lease terminalization never wait on Signal
    or Order row locks. Cleanup applies `FOR UPDATE SKIP LOCKED` before its
    batch `LIMIT`, so locked low IDs cannot starve later lockable rows;
    `expired_signals` and `cancel_requested_live_orders` are the rows claimed
    by that invocation, not lifetime totals. Replaying the exact succeeded
    request is audit-idempotent and advances skipped/remaining cleanup.

The ordinary watchdog transaction also reads the DB-owned control-state
readiness bit. After that transaction commits, the watchdog atomically publishes
a freshness-bounded in-process readiness snapshot.
Unauthenticated `/readyz` reads only that snapshot plus watchdog task state; it
never borrows either safety database pool. Snapshot age starts before the
watchdog attempts its checkout and is capped at six seconds, below the
seven-second database authority. It is red before the first successful tick,
after any failed tick, while controls are unsafe, when the snapshot is stale,
when the task is absent, or after repeated failures. Acquisition revalidates
that same state after its transaction; if freshness changed, safety-control
uses the reserved direct-STOP identity through the emergency pool and returns
503 even when the STOP response is ambiguous. `/healthz` only proves the process
is alive.

## Canary integration contract

The collector must:

1. verify fail-closed preflight, zero pending commands, zero pending Signals,
   and zero LIVE rows in Order, Position, and ExecutionOrderIntent history;
2. acquire with the exact future resume identity, including actor and reason;
3. wait until the independent watchdog advances `watchdog_at`, then recheck
   zero pending Signals before publishing collector admission evidence;
4. heartbeat every 5 seconds;
5. let the already reviewed main operator command with the reserved identity
   resume only Polymarket PAPER;
6. on divergence, use the independent emergency-stop endpoint with the
   precommitted collector `direct_stop_request_id` for the normal canary path;
   an independently authenticated operator may use any fresh request ID;
7. on success, call the lease release endpoint as the single terminal
   STOP + release mutation;
8. verify global/Polymarket fail-closed, Binance unchanged, no active lease,
   no pending bound resume, and the linked successful stop command before
   sealing the final manifest. A committed STOP is confirmed separately from
   cleanup convergence: while pending Signals or nonterminal LIVE Orders
   remain, replay the exact direct STOP identity within the bounded
   confirmation window and fail closed if they do not reach zero. Historical
   LIVE inventory remains forbidden and is never deleted as "cleanup".

A response-loss retry must reuse the exact acquire/release identity and nonce.
Different identities are rejected rather than merged.

Every composite execution-evidence log row also carries exact `mark_ids` and
`finalize_ids`. A count/identity mismatch or the same finalize obligation
appearing in more than one maintenance event while `mark_claimed=0` is treated
as `scheduler_lock_contention` and triggers the independent Polymarket STOP.

## Verification evidence

Focused unit and PostgreSQL 16 integration coverage includes:

- API authentication/RBAC and nonce non-disclosure;
- real Alembic migration and runtime-role boundary;
- fresh-head fail-closed bootstrap, migration-latched PAPER-only state, and
  actual runtime/safety-login negative DML tests;
- real runtime-login rejection of forged `succeeded`, `rejected`, and `failed`
  STOP terminal states, plus legitimate queued worker STOP and exact replay;
- DB-clock acquisition, watchdog observation, and heartbeat extension;
- absent, unobserved, expired, wrong-actor/reason, wrong-nonce, and collision
  rejection;
- independent expiry stop without worker or collector;
- execution read-side fail-close without the watchdog;
- seven-second watchdog-staleness read and commit fail-close;
- deterministic Signal, Order/Intent, and FillLeg/Position commit races versus
  STOP/lease expiry, plus the reverse execution-before-STOP serialization;
- exact runtime-login rejection of new LIVE exposure and catalog proof that
  all five commit triggers are enabled, constraint-backed, deferrable, and
  initially deferred;
- real runtime-login rejection of Signal decision rewrites, legacy
  Order/Position deletion or identity rewrites, lifecycle/amount fabrication,
  unprojected PAPER Orders/Positions, and settlement-derived bankroll drift,
  with owner maintenance bypass proved separately;
- empty-set acquire versus generic STOP serialization;
- locked-oldest STOP cleanup selecting later unlocked candidates, pending
  survivor rejection by readiness/acquire/resume, and unlock-plus-replay
  convergence for both runtime and safety STOP primitives;
- queued resume versus release/stop races with no deadlock;
- late reserved stop-ID poisoning rejection;
- exact pending-resume terminalization;
- atomic success release and response-loss idempotency;
- Binance isolation;
- proof that a long maintenance `run_job` does not hold the safety lock; and
- proof that a blocked expensive LIVE-gate calculation does not delay
  emergency STOP and cannot reopen state after that STOP;
- post-terminal raw SQL rejection for kill-off, non-PAPER mode, and live
  confirmation, while safe disable/STOP/PAPER recovery remain possible; and
- a second exact observed lease can resume a later PAPER canary without
  weakening the history latch.

## Operational limits

- The safety-control service and database remain in the same local failure
  domain. Read-side expiry blocks new execution work, but cannot undo an
  already committed PAPER fill.
- PostgreSQL availability is required for all lease transitions. Failure is
  intentionally fail-closed; it is not automatically repaired.
- Revision 0047 has no permissive pre-canary state. Migration atomically
  forces and audits global/Polymarket kills, seals Binance in killed backtest,
  and execution remains unauthorized until an observed lease exists.
- Applying revision 0047 permanently fuses this schema revision to Polymarket
  PAPER.
  A future LIVE release requires an explicit audited fence-retirement
  migration after every legal, quantitative, operational, and operator gate;
  the PAPER lease can never authorize it.
- Alembic downgrade is supported only for empty disposable development/test
  schemas. It removes the bootstrap audit and managed safety roles and is not
  an audit-preserving production rollback. Production recovery is
  forward-only, as required by the release runbook.
- This control plane does not prove strategy edge and must never be interpreted
  as permission to enable LIVE.
