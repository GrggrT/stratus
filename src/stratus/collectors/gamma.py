from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


class GammaError(RuntimeError):
    pass


class GammaResolutionPending(GammaError):
    """The event is structurally valid but not all buckets are final yet."""


@dataclass(frozen=True, slots=True)
class ResolvedMarket:
    polymarket_id: str
    outcome_yes: bool
    closed_at: datetime
    yes_price: str
    no_price: str
    condition_id: str | None
    question_id: str | None
    yes_token_id: str
    no_token_id: str
    uma_resolution_status: str
    resolved_by: str | None


@dataclass(frozen=True, slots=True)
class ExpectedGammaMarket:
    """Immutable local identity expected inside one Gamma event response."""

    market_id: int
    polymarket_id: str
    condition_id: str | None
    yes_token_id: str | None
    no_token_id: str | None
    rules_hash: str


@dataclass(frozen=True, slots=True)
class CompleteGammaResolution:
    """A complete, mutually exclusive binary event resolution."""

    event_id: str
    outcomes: tuple[ResolvedMarket, ...]
    first_outcome_at: datetime
    fully_resolved_at: datetime
    event_closed_at: datetime | None
    event_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class GammaEventSnapshot:
    """Exact HTTP witness plus the parsed Gamma event payload.

    ``response_sha256`` is calculated over the exact response bytes before
    JSON parsing. Gamma responses are mutable after resolution, so callers
    must treat this as a witnessed observation, not a stable event identity.
    """

    event_id: str
    payload: dict[str, Any]
    source_url: str
    response_body: bytes
    response_sha256: str
    response_bytes: int
    fetched_at: datetime
    http_date: datetime | None
    last_modified: datetime | None
    etag: str | None


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
        events: list[dict[str, Any]] = []
        for event_id in dict.fromkeys(event_ids):
            events.append((await self.fetch_event_snapshot(event_id)).payload)
        return events

    async def fetch_event_snapshot(self, event_id: str) -> GammaEventSnapshot:
        if self._client is None:
            raise RuntimeError("GammaClient must be used as an async context manager")
        return await self._get_event_snapshot(event_id)

    async def _get_event_snapshot(self, event_id: str) -> GammaEventSnapshot:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(f"{self.events_url}/{event_id}")
                response.raise_for_status()
                raw_body = response.content
                payload = response.json()
                if not isinstance(payload, dict):
                    raise GammaError("Gamma returned an invalid event payload")
                payload_event_id = payload.get("id")
                if str(payload_event_id) != event_id:
                    raise GammaError(
                        f"Gamma event identity mismatch: requested {event_id}, "
                        f"received {payload_event_id}"
                    )
                return GammaEventSnapshot(
                    event_id=event_id,
                    payload=payload,
                    source_url=str(response.request.url),
                    response_body=raw_body,
                    response_sha256=hashlib.sha256(raw_body).hexdigest(),
                    response_bytes=len(raw_body),
                    fetched_at=datetime.now(UTC),
                    http_date=_parse_http_date(response.headers.get("Date")),
                    last_modified=_parse_http_date(response.headers.get("Last-Modified")),
                    etag=response.headers.get("ETag"),
                )
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
        try:
            resolution = _parse_resolved_market(market, event)
        except GammaError:
            continue
        resolutions[resolution.polymarket_id] = resolution
    return resolutions


def parse_complete_event_resolution(
    event: dict[str, Any],
    *,
    expected_event_id: str,
    expected_markets: list[ExpectedGammaMarket],
) -> CompleteGammaResolution:
    """Parse an all-or-nothing binary winner vector for one Gamma event.

    Unlike the legacy permissive parser, this rejects the *entire* event when
    one bucket is missing, duplicated, non-terminal, malformed, or does not
    match the local pre-discovered market universe. This is the only parser
    suitable for settlement and formal accuracy evidence.
    """

    if str(event.get("id")) != expected_event_id:
        raise GammaError(
            f"Gamma event identity mismatch: expected {expected_event_id}, "
            f"received {event.get('id')}"
        )
    if len(expected_markets) < 2:
        raise GammaError("local event universe must contain at least two markets")
    expected_by_external = {market.polymarket_id: market for market in expected_markets}
    if len(expected_by_external) != len(expected_markets):
        raise GammaError("local event universe contains duplicate market ids")

    raw_markets = event.get("markets")
    if not isinstance(raw_markets, list):
        raise GammaError("Gamma event is missing markets")
    raw_ids: list[str] = []
    raw_by_id: dict[str, dict[str, Any]] = {}
    for raw_market in raw_markets:
        if not isinstance(raw_market, dict) or not isinstance(raw_market.get("id"), str):
            raise GammaError("Gamma event contains a market without a string id")
        raw_id = raw_market["id"]
        raw_ids.append(raw_id)
        raw_by_id[raw_id] = raw_market
    if len(set(raw_ids)) != len(raw_ids):
        raise GammaError("Gamma event contains duplicate market ids")
    if set(raw_by_id) != set(expected_by_external):
        missing = sorted(set(expected_by_external) - set(raw_by_id))
        unknown = sorted(set(raw_by_id) - set(expected_by_external))
        raise GammaError(
            f"Gamma market universe mismatch: missing={missing} unknown={unknown}"
        )

    outcomes: list[ResolvedMarket] = []
    for expected in sorted(expected_markets, key=lambda item: item.market_id):
        resolution = _parse_resolved_market(
            raw_by_id[expected.polymarket_id],
            event,
            require_market_closed_time=True,
        )
        if expected.condition_id is not None and resolution.condition_id != expected.condition_id:
            raise GammaError(
                f"Gamma condition id mismatch for market {expected.polymarket_id}"
            )
        if (
            expected.yes_token_id is not None
            and resolution.yes_token_id != expected.yes_token_id
        ) or (
            expected.no_token_id is not None
            and resolution.no_token_id != expected.no_token_id
        ):
            raise GammaError(f"Gamma token identity mismatch for {expected.polymarket_id}")
        outcomes.append(resolution)
    if sum(outcome.outcome_yes for outcome in outcomes) != 1:
        raise GammaError("Gamma event does not contain exactly one YES winner")

    closed_times = [outcome.closed_at for outcome in outcomes]
    return CompleteGammaResolution(
        event_id=expected_event_id,
        outcomes=tuple(outcomes),
        first_outcome_at=min(closed_times),
        fully_resolved_at=max(closed_times),
        event_closed_at=_optional_iso_datetime(event.get("closedTime"), "closedTime"),
        event_updated_at=_optional_iso_datetime(event.get("updatedAt"), "updatedAt"),
    )


def _parse_resolved_market(
    market: dict[str, Any],
    event: dict[str, Any],
    *,
    require_market_closed_time: bool = False,
) -> ResolvedMarket:
    market_id = market.get("id")
    if not isinstance(market_id, str):
        raise GammaError("Gamma market is missing id")
    if market.get("closed") is not True:
        raise GammaResolutionPending(f"Gamma market {market_id} is not closed")
    status = market.get("umaResolutionStatus")
    if status != "resolved":
        raise GammaResolutionPending(f"Gamma market {market_id} is not resolved")

    labels = _json_list(market.get("outcomes"), "outcomes", market_id)
    prices = _json_list(market.get("outcomePrices"), "outcomePrices", market_id)
    token_ids = _json_list(market.get("clobTokenIds"), "clobTokenIds", market_id)
    if len(labels) != 2 or len(prices) != 2 or len(token_ids) != 2:
        raise GammaError(f"Gamma market {market_id} is not a binary token pair")
    normalized_labels = [str(label).strip().casefold() for label in labels]
    if sorted(normalized_labels) != ["no", "yes"]:
        raise GammaError(f"Gamma market {market_id} has invalid outcome labels")
    yes_index = normalized_labels.index("yes")
    no_index = normalized_labels.index("no")
    yes_token_id = token_ids[yes_index]
    no_token_id = token_ids[no_index]
    if (
        not isinstance(yes_token_id, str)
        or not yes_token_id.strip()
        or not isinstance(no_token_id, str)
        or not no_token_id.strip()
    ):
        raise GammaError(f"Gamma market {market_id} has invalid token ids")
    try:
        yes_price = Decimal(str(prices[yes_index]))
        no_price = Decimal(str(prices[no_index]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GammaError(f"Gamma market {market_id} has invalid outcome prices") from exc
    if (yes_price, no_price) not in {
        (Decimal("1"), Decimal("0")),
        (Decimal("0"), Decimal("1")),
    }:
        raise GammaError(
            f"Gamma market {market_id} has non-binary terminal payout "
            f"{yes_price}/{no_price}"
        )

    if require_market_closed_time:
        raw_closed_at = market.get("closedTime")
    else:
        raw_closed_at = market.get("closedTime") or event.get("closedTime")
    closed_at = _required_iso_datetime(raw_closed_at, "closedTime", market_id)
    condition_id = market.get("conditionId")
    question_id = market.get("questionID") or market.get("questionId")
    resolved_by = market.get("resolvedBy")
    return ResolvedMarket(
        polymarket_id=market_id,
        outcome_yes=yes_price == 1,
        closed_at=closed_at,
        yes_price=format(yes_price, "f"),
        no_price=format(no_price, "f"),
        condition_id=str(condition_id) if condition_id is not None else None,
        question_id=str(question_id) if question_id is not None else None,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        uma_resolution_status=status,
        resolved_by=str(resolved_by) if resolved_by is not None else None,
    )


def _json_list(value: Any, field: str, market_id: str) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise GammaError(f"Gamma market {market_id} has invalid {field}") from exc
    if not isinstance(value, list):
        raise GammaError(f"Gamma market {market_id} is missing {field}")
    return value


def _required_iso_datetime(value: Any, field: str, market_id: str) -> datetime:
    if not isinstance(value, str):
        raise GammaError(f"Gamma market {market_id} is missing {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GammaError(f"Gamma market {market_id} has invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GammaError(
            f"Gamma market {market_id} {field} must include a timezone"
        )
    return parsed.astimezone(UTC)


def _optional_iso_datetime(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    return _required_iso_datetime(value, field, "event")


def _parse_http_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
