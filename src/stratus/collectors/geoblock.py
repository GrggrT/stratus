from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class GeoblockStatus:
    blocked: bool
    country: str | None
    detail: str


def parse_geoblock(payload: object) -> GeoblockStatus:
    """Fail closed: anything other than an explicit blocked=false is blocked."""
    if not isinstance(payload, dict) or not isinstance(payload.get("blocked"), bool):
        return GeoblockStatus(True, None, "invalid geoblock response")
    country = payload.get("country")
    return GeoblockStatus(
        blocked=bool(payload["blocked"]),
        country=country if isinstance(country, str) else None,
        detail="ok",
    )


async def fetch_geoblock(
    url: str, http_client: httpx.AsyncClient | None = None
) -> GeoblockStatus:
    """Polymarket requires the check before every live order; a transport
    failure therefore reads as blocked, never as permission."""
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await client.get(url)
        response.raise_for_status()
        return parse_geoblock(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        return GeoblockStatus(True, None, f"geoblock check failed: {exc}")
    finally:
        if owns_client:
            await client.aclose()
