from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings
from stratus.db.models import WeatherKind
from stratus.repositories.accuracy import upsert_resolution_contract
from stratus.services.evidence_release_manifest import (
    ALEMBIC_HEAD,
    EvidenceReleaseManifestError,
    build_evidence_release_manifest,
)
from stratus.services.resolution_contracts import parse_resolution_contract

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
SOURCE_URL = "https://www.wunderground.com/history/daily/us/ny/KLGA"


def _rules(day: str, *, decimal: bool = False) -> str:
    precision = (
        "Fahrenheit to one decimal place"
        if decimal
        else "whole degrees Fahrenheit"
    )
    return (
        f"This market resolves to the highest temperature on {day}. "
        f"The resolution source is available here: {SOURCE_URL}. "
        "This market cannot resolve until the first data point for the following date. "
        f"The source measures temperatures to {precision}."
    )


async def test_release_manifest_reads_database_head_and_rejects_mismatch(
    db_sessions: SessionFactory,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        release_image_digest="c" * 64,
    )
    async with db_sessions() as session:
        transaction = await session.begin()
        try:
            candidate = await build_evidence_release_manifest(settings, session)
            assert candidate.manifest["alembic_head"] == ALEMBIC_HEAD

            await session.execute(
                text("UPDATE alembic_version SET version_num = '20260715_0040'")
            )
            with pytest.raises(EvidenceReleaseManifestError, match="Alembic head mismatch"):
                await build_evidence_release_manifest(settings, session)
        finally:
            await transaction.rollback()


async def test_daily_contract_discovery_preserves_semantic_release_identity(
    db_sessions: SessionFactory,
    canonical_probability_release_inputs: None,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        release_image_digest="c" * 64,
    )
    async with db_sessions.begin() as session:
        before = await build_evidence_release_manifest(settings, session)
        await upsert_resolution_contract(
            session,
            parse_resolution_contract(
                event_id="next-daily-event",
                rules_raw=_rules("13 Jul '26"),
                resolution_source=SOURCE_URL,
                station_code="KLGA",
                local_timezone="America/New_York",
                statistic=WeatherKind.HIGH,
            ),
            expected_market_date=date(2026, 7, 13),
        )
        await session.execute(
            text(
                """
                INSERT INTO markets (
                    polymarket_id, event_id, market_date, kind, rules_raw,
                    resolves_at
                ) VALUES (
                    'next-daily-market', 'next-daily-event', DATE '2026-07-13',
                    'high', :rules, TIMESTAMPTZ '2026-07-14 00:00:00+00'
                )
                """
            ),
            {"rules": _rules("13 Jul '26")},
        )
        after_daily_event = await build_evidence_release_manifest(settings, session)

        assert before.manifest_hash == after_daily_event.manifest_hash
        assert before.lane_identity_hashes == after_daily_event.lane_identity_hashes

        await upsert_resolution_contract(
            session,
            parse_resolution_contract(
                event_id="materially-changed-event",
                rules_raw=_rules("14 Jul '26", decimal=True),
                resolution_source=SOURCE_URL,
                station_code="KLGA",
                local_timezone="America/New_York",
                statistic=WeatherKind.HIGH,
            ),
            expected_market_date=date(2026, 7, 14),
        )
        await session.execute(
            text(
                """
                INSERT INTO markets (
                    polymarket_id, event_id, market_date, kind, rules_raw,
                    resolves_at
                ) VALUES (
                    'materially-changed-market', 'materially-changed-event',
                    DATE '2026-07-14', 'high', :rules,
                    TIMESTAMPTZ '2026-07-15 00:00:00+00'
                )
                """
            ),
            {"rules": _rules("14 Jul '26", decimal=True)},
        )
        after_material_change = await build_evidence_release_manifest(settings, session)

    changed = {
        lane_name
        for lane_name, identity_hash in before.lane_identity_hashes.items()
        if after_material_change.lane_identity_hashes[lane_name] != identity_hash
    }
    assert changed == {
        "official_resolution_truth",
        "predictive_validation",
        "station_lifecycle",
    }


async def test_release_manifest_rejects_contract_without_any_database_market(
    db_sessions: SessionFactory,
    canonical_probability_release_inputs: None,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        release_image_digest="c" * 64,
    )
    async with db_sessions.begin() as session:
        await upsert_resolution_contract(
            session,
            parse_resolution_contract(
                event_id="contract-without-market",
                rules_raw=_rules("15 Jul '26"),
                resolution_source=SOURCE_URL,
                station_code="KLGA",
                local_timezone="America/New_York",
                statistic=WeatherKind.HIGH,
            ),
            expected_market_date=date(2026, 7, 15),
        )

        with pytest.raises(
            EvidenceReleaseManifestError,
            match="has no database markets",
        ):
            await build_evidence_release_manifest(settings, session)
