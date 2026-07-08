from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from py_clob_client_v2 import (
    ClobClient,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
)


class ClobTradeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlacedOrder:
    order_id: str
    status: str


def parse_place_response(payload: object) -> PlacedOrder:
    """Fail closed: only an explicit success with an order id counts."""
    if not isinstance(payload, dict):
        raise ClobTradeError("CLOB returned an invalid order response")
    if payload.get("success") is not True or payload.get("errorMsg"):
        raise ClobTradeError(f"order rejected: {payload.get('errorMsg') or payload}")
    order_id = payload.get("orderID")
    if not isinstance(order_id, str) or not order_id:
        raise ClobTradeError("CLOB response is missing orderID")
    status = payload.get("status")
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
