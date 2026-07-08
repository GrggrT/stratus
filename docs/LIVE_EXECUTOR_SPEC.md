# Live executor spec

Status: Phase L1 implemented. The live path in `execute_signals` stays
disabled until Phase L2 lands; `/mode live` still reports the executor as
not deployed.

## Context

The paper pipeline is complete and validated by the day-30/60 gate. Going
live replaces `walk_ask_book` simulation with signed GTC limit orders on the
Polymarket CLOB. The operator accepted the legal/tax risk personally
(dated note in `AUSTRIA_LIVE_READINESS.md`); every technical gate remains.

## Invariants (all phases)

1. **No order without all gates.** `services/live_execution.py::
   evaluate_live_readiness` requires: `LIVE_LEGAL_APPROVED`,
   `LIVE_TAX_APPROVED` (operator-set, never flipped by code), a configured
   `POLYGON_PRIVATE_KEY`, runtime mode LIVE (reached only through the paper
   gate plus double `/mode live` confirmation), kill switch off, no project
   halt, and a fresh geoblock check.
2. **Geoblock before every order**, not per session
   (`collectors/geoblock.py`). Unknown or failed check = blocked.
3. **Adjust down, never up.** Order planning
   (`services/live_execution.py::plan_live_buy`) snaps the limit price down
   to the market tick, rounds shares down, clamps notional to
   `LIVE_MAX_ORDER_USD`, and rejects anything below the market minimum —
   it never corrects an order upward to make it fit.
4. **Idempotency and audit parity with paper**: one order per signal
   (`uq_orders_signal_mode` covers LIVE separately from PAPER),
   `FOR UPDATE SKIP LOCKED` + status re-check before submission, and the
   kill switch marks open live orders `cancel_requested`.
5. **The console and Telegram never sign anything**; only the worker holds
   the key.

## Phase L1 — foundation (implemented)

- `py-clob-client-v2` dependency: EIP-712 signing, L1/L2 auth, GTC/FOK
  orders (API verified against the client source, not from memory).
- Settings: `CLOB_API_URL`, `GEOBLOCK_URL`, `POLYGON_CHAIN_ID`,
  `POLYGON_PRIVATE_KEY`, `POLYMARKET_FUNDER`, `POLYMARKET_SIGNATURE_TYPE`
  (proxy wallets), `LIVE_MAX_ORDER_USD`.
- `collectors/clob_trader.py`: async facade over the synchronous official
  client (worker threads); `connect()` derives L2 creds once; strict
  fail-closed parsing of the order-placement response.
- `collectors/geoblock.py`: fail-closed geoblock status.
- `services/live_execution.py`: pure, unit-tested readiness and
  order-planning logic.

## Phase L2 — order placement (next)

- Live branch in `jobs/execute_signals.py`: readiness check -> geoblock ->
  tick size/neg-risk lookup -> `plan_live_buy` -> `place_gtc_buy`;
  Order row written with `clob_order_id` before any retry logic may run.
  A timeout after submission is treated as possibly-placed: reconcile via
  open-orders lookup before ever re-submitting a signal.
- Cancellation worker for `cancel_requested` live orders
  (kill switch / TTL expiry).
- Unlock `/mode live`: replace the hardcoded "executor is not deployed"
  response with a switch on executor configuration.

## Phase L3 — fills, positions, settlement

- Poll open orders; build live positions from fills (partial fills
  accumulate into one position per signal).
- Live bankroll from `get_balance_allowance` snapshots.
- Settlement: resolution detection reuses the Gamma path; on-chain
  redemption of winning tokens stays MANUAL via the Polymarket UI in this
  phase (documented operator runbook step); PnL is recorded at resolution.
- Health audit: live-specific checks (unfilled order age, orphan orders on
  the book with no matching signal, balance drift vs recorded bankroll).

## Evidence retention (per the readiness memo)

Orders, fills, fees, geoblock responses, and wallet transaction hashes are
kept in Postgres and never pruned; `prune_signals` touches only orderless
SKIPPED/EXPIRED signals.
