from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from py_clob_client_v2 import (
    AssetType,
    BalanceAllowanceParams,
    ClobClient,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
    TradeParams,
)


class ClobTradeError(RuntimeError):
    pass


class ClobVenueRejected(ClobTradeError):
    """The venue explicitly proved that it did not accept the order."""


class ClobPlacementOutcomeUnknown(ClobTradeError):
    """The placement response cannot prove whether an order was accepted."""


@dataclass(frozen=True, slots=True)
class PlacedOrder:
    order_id: str
    status: str


def parse_place_response(payload: object) -> PlacedOrder:
    """Fail closed: only an explicit success with an order id counts."""
    if not isinstance(payload, dict):
        raise ClobPlacementOutcomeUnknown("CLOB returned an invalid order response")
    success = payload.get("success")
    error_message = payload.get("errorMsg")
    order_id = payload.get("orderID")
    usable_order_id = isinstance(order_id, str) and bool(order_id.strip())
    if success is False and not usable_order_id:
        raise ClobVenueRejected(f"order rejected: {error_message or payload}")
    if success is not True or error_message:
        raise ClobPlacementOutcomeUnknown(
            "CLOB response does not contain an unambiguous placement result"
        )
    if not usable_order_id:
        raise ClobPlacementOutcomeUnknown("CLOB response is missing orderID")
    status = payload.get("status")
    assert isinstance(order_id, str)
    return PlacedOrder(order_id=order_id, status=status if isinstance(status, str) else "unknown")


class ClobTrader:
    """Async facade over the official (synchronous) Polymarket CLOB client.

    Every network call runs in a worker thread. connect() derives the L2 API
    credentials once; trading methods refuse to run before it.
    """

    def __init__(
        self,
        *,
        host: str,
        chain_id: int,
        private_key: str,
        signature_type: int | None = None,
        funder: str | None = None,
    ) -> None:
        self._client = ClobClient(
            host,
            chain_id=chain_id,
            key=private_key,
            signature_type=signature_type,
            funder=funder,
        )
        self._connected = False

    async def connect(self) -> None:
        def derive() -> None:
            creds = self._client.create_or_derive_api_key()
            self._client.set_api_creds(creds)

        await asyncio.to_thread(derive)
        self._connected = True

    def _require_connected(self) -> None:
        if not self._connected:
            raise ClobTradeError("ClobTrader.connect() must be called before trading")

    async def tick_size(self, token_id: str) -> Decimal:
        value = await asyncio.to_thread(self._client.get_tick_size, token_id)
        return Decimal(str(value))

    async def neg_risk(self, token_id: str) -> bool:
        return bool(await asyncio.to_thread(self._client.get_neg_risk, token_id))

    async def place_gtc_buy(
        self,
        *,
        token_id: str,
        price: Decimal,
        size_shares: Decimal,
        tick_size: Decimal,
        neg_risk: bool,
    ) -> PlacedOrder:
        self._require_connected()

        def post() -> Any:
            return self._client.create_and_post_order(
                order_args=OrderArgs(
                    token_id=token_id,
                    price=float(price),
                    size=float(size_shares),
                    side=Side.BUY,
                ),
                options=PartialCreateOrderOptions(
                    tick_size=str(tick_size), neg_risk=neg_risk
                ),
                order_type=OrderType.GTC,
            )

        return parse_place_response(await asyncio.to_thread(post))

    async def cancel(self, order_ids: list[str]) -> None:
        self._require_connected()
        if order_ids:
            await asyncio.to_thread(self._client.cancel_orders, list(order_ids))

    async def order(self, order_id: str) -> dict[str, Any]:
        self._require_connected()
        payload = await asyncio.to_thread(self._client.get_order, order_id)
        if not isinstance(payload, dict):
            raise ClobTradeError("CLOB returned an invalid order payload")
        return payload

    async def trades(self, asset_id: str) -> list[dict[str, Any]]:
        self._require_connected()

        def fetch() -> Any:
            return self._client.get_trades(TradeParams(asset_id=asset_id))

        payload = await asyncio.to_thread(fetch)
        if not isinstance(payload, list):
            raise ClobTradeError("CLOB returned an invalid trades payload")
        return [item for item in payload if isinstance(item, dict)]

    async def collateral_balance(self) -> Decimal:
        """Available USDC collateral, in dollars."""
        self._require_connected()

        def fetch() -> Any:
            return self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )

        payload = await asyncio.to_thread(fetch)
        if not isinstance(payload, dict) or "balance" not in payload:
            raise ClobTradeError("CLOB returned an invalid balance payload")
        try:
            # Balances are reported in USDC base units (6 decimals).
            return (Decimal(str(payload["balance"])) / Decimal(10**6)).quantize(
                Decimal("0.0001")
            )
        except (TypeError, ValueError) as exc:
            raise ClobTradeError("CLOB balance is not a number") from exc
