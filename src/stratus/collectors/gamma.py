from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


class GammaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedMarket:
    outcome_yes: bool
    closed_at: datetime


class GammaClient:
    def __init__(
        self,
        *,
        events_url: str,
        weather_tag_id: int,
        page_size: int = 100,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.events_url = events_url
        self.weather_tag_id = weather_tag_id
        self.page_size = page_size
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> GammaClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch_active_weather_events(self) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("GammaClient must be used as an async context manager")
        events: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self._get_page(offset)
            events.extend(page)
            if len(page) < self.page_size:
                return events
            offset += self.page_size

    async def fetch_events_by_id(self, event_ids: list[str]) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("GammaClient must be used as an async context manager")
        events = []
        for event_id in dict.fromkeys(event_ids):
            events.append(await self._get_event(event_id))
        return events

    async def _get_event(self, event_id: str) -> dict[str, Any]:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(f"{self.events_url}/{event_id}")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise GammaError("Gamma returned an invalid event payload")
                return payload
            except (httpx.HTTPError, ValueError, GammaError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise GammaError(
            f"Gamma event request failed after 3 attempts: {last_error}"
        ) from last_error

    async def _get_page(self, offset: int) -> list[dict[str, Any]]:
        assert self._client is not None
        params: dict[str, str | int] = {
            "tag_id": self.weather_tag_id,
            "active": "true",
            "closed": "false",
            "limit": self.page_size,
            "offset": offset,
        }
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(self.events_url, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or not all(
                    isinstance(item, dict) for item in payload
                ):
                    raise GammaError("Gamma returned an invalid events payload")
                return payload
            except (httpx.HTTPError, ValueError, GammaError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise GammaError(f"Gamma request failed after 3 attempts: {last_error}") from last_error


def parse_resolved_markets(event: dict[str, Any]) -> dict[str, bool]:
    return {
        market_id: resolution.outcome_yes
        for market_id, resolution in parse_market_resolutions(event).items()
    }


def parse_market_resolutions(event: dict[str, Any]) -> dict[str, ResolvedMarket]:
    raw_markets = event.get("markets")
    if not isinstance(raw_markets, list):
        raise GammaError("Gamma event is missing markets")
    resolutions: dict[str, ResolvedMarket] = {}
    for market in raw_markets:
        if not isinstance(market, dict) or market.get("closed") is not True:
            continue
        if market.get("umaResolutionStatus") != "resolved":
            continue
        market_id = market.get("id")
        raw_prices = market.get("outcomePrices")
        raw_closed_at = market.get("closedTime") or event.get("closedTime")
        if (
            not isinstance(market_id, str)
            or not isinstance(raw_prices, str)
            or not isinstance(raw_closed_at, str)
        ):
            continue
        try:
            prices = json.loads(raw_prices)
            if not isinstance(prices, list) or len(prices) != 2:
                continue
            yes_price, no_price = (Decimal(str(value)) for value in prices)
            closed_at = datetime.fromisoformat(raw_closed_at.replace("Z", "+00:00"))
            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=UTC)
            closed_at = closed_at.astimezone(UTC)
        except (json.JSONDecodeError, InvalidOperation, TypeError, ValueError):
            continue
        if yes_price == 1 and no_price == 0:
            resolutions[market_id] = ResolvedMarket(True, closed_at)
        elif yes_price == 0 and no_price == 1:
            resolutions[market_id] = ResolvedMarket(False, closed_at)
    return resolutions
