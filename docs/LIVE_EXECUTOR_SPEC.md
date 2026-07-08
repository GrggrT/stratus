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

## Phase L2 — order placement (implemented)

- `jobs/execute_live_signals.py::execute_live_signals`: readiness check ->
  geoblock -> tick size / neg-risk lookup -> `plan_live_buy` ->
  `place_gtc_buy`. The order row is committed as `submitting` BEFORE the
  network call (`repositories/execution.py::claim_live_order`, ON CONFLICT
  DO NOTHING on `uq_orders_signal_mode`), so a signal that already has an
  order is never re-submitted.
- Outcome handling: an explicit exchange rejection -> `submit_failed` (no
  order on the book, no halt); an unknown outcome (timeout) or an order
  found stuck in `submitting` from a prior crash -> `submit_unconfirmed`
  plus kill switch plus a critical alert, because the order MAY be live.
  The executor never re-sends an order whose outcome it cannot confirm.
- `cancel_live_orders`: cancels orders the kill switch marked
  `cancel_requested`.
- Order planning only ever adjusts DOWN and the `LIVE_MAX_ORDER_USD`
  ceiling is enforced independently of Kelly/exposure.

Tested end to end against a real Postgres with a fake trader (no wallet,
no network): placement, geoblock gating, timeout-halts-trading,
rejection-does-not-halt, orphaned-`submitting`-escalated, and cancellation.

**Deliberately not wired yet.** Neither placement nor cancellation is
registered in the scheduler, and `/mode live` still reports the executor as
not deployed. Reaching LIVE mode would let the bot place orders with no
position/PnL accounting, so the dispatch (paper vs live in
`execute_signals`), the cancellation job, and the `/mode live` unlock are
all deferred to the end of L3, where the full place -> fills -> positions ->
settle loop lands together. `stratus-execute-live` exists as a
readiness-gated manual entry point in the meantime.

## Phase L3 — fills, positions, settlement

### L3a — fills → positions, live bankroll (implemented)

- `poll_live_fills` reads each open live order's cumulative fill state
  (`services/live_execution.py::parse_order_fill`, fail-closed) and
  recomputes its position (`repositories/execution.py::
  upsert_live_position`, one position per order via the partial-unique
  `uq_positions_order` index from migration 0016). Recompute-not-append
  makes repeated or missed polls self-healing. A fully matched order flips
  to `filled`; a canceled one to `canceled`.
- `avg_price` uses the order's limit price — a conservative upper bound on
  a BUY's cost, since limit BUYs fill at or below their price. (Per-trade
  reconciliation via `get_trades` is a later refinement.)
- Live bankroll: `record_live_bankroll` appends a `get_balance_allowance`
  snapshot only when the collateral balance changed.
- `stratus-poll-live-fills` is a manual entry point; not scheduled yet.

### L3b — live settlement (implemented)

- `repositories/settlement.py::settle_resolved_markets` replaces the
  paper-only function: it resolves each market once and closes ALL open
  positions (both modes) in one transaction. Paper positions still
  synthesise a bankroll balance from realised PnL; live positions record
  PnL and CLV only — the real USDC balance does not move until the operator
  redeems on-chain, so no synthetic live bankroll row is written.
- Winning live positions enqueue one `live-redeem:<ts>` reminder listing
  the shares to redeem manually via the Polymarket UI. Losing live
  positions settle silently.

### L3c — go live (implemented)

- `execute_signals` now dispatches by runtime mode: LIVE ->
  `execute_live_signals`, else `execute_paper_signals`. The scheduler runs
  the single `execution` job plus `live-fills` and `live-cancellations`
  every minute (both no-op without a configured wallet).
- `/mode live` is unlocked: after the legal/tax flags, the paper gate, and
  the two-message confirmation, a configured `POLYGON_PRIVATE_KEY` flips
  the runtime to LIVE; without a wallet it stays blocked.
- Health audit flags `live_orders_unconfirmed` as critical (engaging the
  kill switch) so an order the executor could not confirm cannot be
  silently forgotten.

### Operator runbook: going live

1. Set `LIVE_LEGAL_APPROVED=true`, `LIVE_TAX_APPROVED=true`,
   `POLYGON_PRIVATE_KEY`, and (proxy wallets) `POLYMARKET_FUNDER` /
   `POLYMARKET_SIGNATURE_TYPE` in `.env`. Keep `LIVE_MAX_ORDER_USD` small.
2. **Preflight against the real server:** `uv run stratus-live-preflight`.
   It authenticates the wallet, checks geoblock, reads the collateral
   balance, and looks up a tick size — all WITHOUT placing an order — and
   exits non-zero unless everything passed. This validates that the live
   response shapes match the fail-closed parsers before any money moves.
3. Confirm the day-30/60 validation gate has passed (`/calib`).
4. `/mode live`, then `/mode live` again within the confirmation window.
5. First real trade should be a single small order (`LIVE_MAX_ORDER_USD=2`):
   verify it fills into a position, `/stop` cancels a resting order, and a
   `live-redeem` reminder arrives on resolution.
6. Watch `/status`; the executor places GTC BUYs capped at
   `LIVE_MAX_ORDER_USD`. `/stop` halts immediately and marks resting
   orders `cancel_requested` for the cancellation job.
7. **Redemption is manual.** On resolution STRATUS records live PnL and
   sends a `live-redeem` reminder; redeem winning tokens yourself in the
   Polymarket UI. The bot never moves funds on-chain.

The place-order response (`success`, `errorMsg`, `orderID`, `status`) and
the get-order object (`size_matched`, `price`, `status`) were verified
against the official CLOB docs; the parsers fail closed on any deviation.

## Evidence retention (per the readiness memo)

Orders, fills, fees, geoblock responses, and wallet transaction hashes are
kept in Postgres and never pruned; `prune_signals` touches only orderless
SKIPPED/EXPIRED signals.
