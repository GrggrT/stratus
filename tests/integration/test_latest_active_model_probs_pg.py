from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.db.models import (
    Market,
    MarketStatus,
    ModelProb,
    Station,
    StationStage,
    WeatherKind,
)
from stratus.repositories.model_probs import (
    LATEST_ACTIVE_MARKET_PROBABILITY_COMPONENTS_SQL,
    latest_active_market_probability_components,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


def _market(
    *,
    suffix: str,
    station_id: int,
    status: MarketStatus = MarketStatus.ACTIVE,
    verified: bool = True,
) -> Market:
    return Market(
        polymarket_id=f"latest-prob-{suffix}",
        event_id=f"latest-prob-event-{suffix}",
        station_id=station_id,
        market_date=date(2026, 7, 31),
        kind=WeatherKind.HIGH,
        rules_raw=f"Latest probability fixture {suffix}",
        station_verified=verified,
        status=status,
        resolves_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def _probability(
    market_id: int,
    *,
    timestamp: datetime,
    version: str,
) -> ModelProb:
    return ModelProb(
        market_id=market_id,
        ts=timestamp,
        prob=Decimal("0.500000"),
        sigma=Decimal("1.0000"),
        horizon_h=Decimal("12.00"),
        components={"version": version},
    )


def _plan_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    descendants = [node]
    for child in node.get("Plans", []):
        descendants.extend(_plan_nodes(child))
    return descendants


async def test_latest_active_probability_lookup_is_filtered_latest_and_indexable(
    db_sessions: SessionFactory,
) -> None:
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    async with db_sessions.begin() as session:
        paper = Station(
            icao="KPAP",
            name="Paper station",
            city="Paper",
            lat=Decimal("40"),
            lon=Decimal("-73"),
            tz="UTC",
            active=True,
            stage=StationStage.PAPER,
        )
        approved = Station(
            icao="KAPP",
            name="Approved station",
            city="Approved",
            lat=Decimal("41"),
            lon=Decimal("-72"),
            tz="UTC",
            active=True,
            stage=StationStage.APPROVED,
        )
        shadow = Station(
            icao="KSHD",
            name="Shadow station",
            city="Shadow",
            lat=Decimal("42"),
            lon=Decimal("-71"),
            tz="UTC",
            active=True,
            stage=StationStage.SHADOW,
        )
        inactive = Station(
            icao="KOFF",
            name="Inactive station",
            city="Inactive",
            lat=Decimal("43"),
            lon=Decimal("-70"),
            tz="UTC",
            active=False,
            stage=StationStage.PAPER,
        )
        session.add_all((paper, approved, shadow, inactive))
        await session.flush()

        paper_market = _market(suffix="paper", station_id=paper.id)
        approved_market = _market(suffix="approved", station_id=approved.id)
        no_probability = _market(suffix="empty", station_id=paper.id)
        blocked_market = _market(
            suffix="blocked",
            station_id=paper.id,
            status=MarketStatus.BLOCKED,
        )
        unverified_market = _market(
            suffix="unverified",
            station_id=paper.id,
            verified=False,
        )
        shadow_market = _market(suffix="shadow", station_id=shadow.id)
        inactive_market = _market(suffix="inactive", station_id=inactive.id)
        session.add_all(
            (
                paper_market,
                approved_market,
                no_probability,
                blocked_market,
                unverified_market,
                shadow_market,
                inactive_market,
            )
        )
        await session.flush()

        session.add_all(
            (
                _probability(
                    paper_market.id,
                    timestamp=now - timedelta(hours=2),
                    version="paper-old",
                ),
                _probability(
                    paper_market.id,
                    timestamp=now,
                    version="paper-latest",
                ),
                _probability(
                    approved_market.id,
                    timestamp=now,
                    version="approved-latest",
                ),
                _probability(
                    blocked_market.id,
                    timestamp=now,
                    version="blocked",
                ),
                _probability(
                    unverified_market.id,
                    timestamp=now,
                    version="unverified",
                ),
                _probability(
                    shadow_market.id,
                    timestamp=now,
                    version="shadow",
                ),
                _probability(
                    inactive_market.id,
                    timestamp=now,
                    version="inactive",
                ),
            )
        )
        await session.flush()

        components = await latest_active_market_probability_components(session)

        assert components == (
            {"version": "paper-latest"},
            {"version": "approved-latest"},
        )

        await session.execute(text("SET LOCAL enable_seqscan = off"))
        raw_plan = await session.scalar(
            text(
                "EXPLAIN (FORMAT JSON) "
                + LATEST_ACTIVE_MARKET_PROBABILITY_COMPONENTS_SQL
            )
        )
        plan_document = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
        nodes = _plan_nodes(plan_document[0]["Plan"])
        probability_nodes = [
            node for node in nodes if node.get("Relation Name") == "model_probs"
        ]

        assert probability_nodes
        assert all(node["Node Type"] != "Seq Scan" for node in probability_nodes)
        assert any(
            node.get("Index Name") == "uq_model_probs_market_time"
            for node in probability_nodes
        )
