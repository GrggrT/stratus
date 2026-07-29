from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from stratus.config import (
    runtime_database_group_role_name,
    runtime_database_role_name,
)
from stratus.db.models import Market, MarketStatus, ResolutionContract, WeatherKind
from stratus.repositories.accuracy import (
    ResolutionContractIntegrityError,
    upsert_resolution_contract,
)
from stratus.services.evidence_release_manifest import ALEMBIC_HEAD
from stratus.services.resolution_contracts import (
    ParsedResolutionContract,
    parse_resolution_contract,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


def _contract() -> ParsedResolutionContract:
    rules = (
        "Resolve 17 Jul '26 from "
        "https://www.wunderground.com/history/daily/test/KAAA using whole degrees "
        "Celsius after the first data point for the following date."
    )
    return parse_resolution_contract(
        event_id="immutable-resolution-event",
        rules_raw=rules,
        resolution_source="https://www.wunderground.com/history/daily/test/KAAA",
        station_code="KAAA",
        local_timezone="UTC",
        statistic=WeatherKind.HIGH,
    )


def _contract_row(
    value: ParsedResolutionContract,
    *,
    rules_raw: str | None = None,
) -> ResolutionContract:
    stored_rules = value.rules_raw if rules_raw is None else rules_raw
    return ResolutionContract(
        event_id=value.event_id,
        rules_hash=hashlib.sha256(stored_rules.encode("utf-8")).hexdigest(),
        source_family=value.source_family,
        source_url=value.source_url,
        station_code=value.station_code,
        local_timezone=value.local_timezone,
        statistic=value.statistic.value,
        source_unit=value.source_unit,
        decimal_places=value.decimal_places,
        finalization_rule=value.finalization_rule,
        license_status=value.license_status,
        storage_permitted=value.storage_permitted,
        license_url=value.license_url,
        rules_raw=stored_rules,
    )


def _market(*, polymarket_id: str, event_id: str, market_date: date) -> Market:
    return Market(
        polymarket_id=polymarket_id,
        event_id=event_id,
        market_date=market_date,
        kind=WeatherKind.HIGH,
        rules_raw="date-integrity fixture",
        resolves_at=datetime(2026, 7, 18, tzinfo=UTC),
        status=MarketStatus.BLOCKED,
    )


async def test_repository_requires_expected_market_date(
    db_sessions: SessionFactory,
) -> None:
    value = _contract()
    async with db_sessions.begin() as session:
        with pytest.raises(
            ResolutionContractIntegrityError,
            match="rules date does not match the expected market date",
        ):
            await upsert_resolution_contract(
                session,
                value,
                expected_market_date=date(2026, 7, 18),
            )
        assert await session.scalar(
            select(func.count()).select_from(ResolutionContract)
        ) == 0


async def test_database_date_invariant_supports_contract_first_discovery_order(
    db_sessions: SessionFactory,
) -> None:
    value = _contract()
    async with db_sessions.begin() as session:
        await upsert_resolution_contract(
            session,
            value,
            expected_market_date=date(2026, 7, 17),
        )
        market = _market(
            polymarket_id="contract-first-matching",
            event_id=value.event_id,
            market_date=date(2026, 7, 17),
        )
        session.add(market)
        await session.flush()

        with pytest.raises(DBAPIError, match="market date does not match"):
            async with session.begin_nested():
                session.add(
                    _market(
                        polymarket_id="contract-first-mismatch",
                        event_id=value.event_id,
                        market_date=date(2026, 7, 18),
                    )
                )
                await session.flush()

        with pytest.raises(DBAPIError, match="market date does not match"):
            async with session.begin_nested():
                await session.execute(
                    update(Market)
                    .where(Market.id == market.id)
                    .values(market_date=date(2026, 7, 18))
                )

        assert await session.scalar(
            select(func.count()).select_from(Market).where(
                Market.event_id == value.event_id
            )
        ) == 1


async def test_contract_trigger_rejects_existing_market_mismatch_and_bad_dates(
    db_sessions: SessionFactory,
) -> None:
    value = _contract()
    async with db_sessions.begin() as session:
        session.add(
            _market(
                polymarket_id="market-before-contract",
                event_id=value.event_id,
                market_date=date(2026, 7, 18),
            )
        )
    with pytest.raises(DBAPIError, match="does not match every market date"):
        async with db_sessions.begin() as session:
            session.add(_contract_row(value))
            await session.flush()

    for rules in (
        value.rules_raw.replace("17 Jul '26", "the target date"),
        value.rules_raw + " Correction references 18 Jul '26.",
        value.rules_raw.replace("17 Jul '26", "31 Feb '26"),
    ):
        with pytest.raises(
            DBAPIError,
            match="resolution rules (must contain exactly one|contain an invalid)",
        ):
            async with db_sessions.begin() as session:
                session.add(_contract_row(value, rules_raw=rules))
                await session.flush()


async def test_cross_table_date_invariant_serializes_concurrent_app_inserts(
    db_sessions: SessionFactory,
) -> None:
    owner_url = make_url(os.environ["STRATUS_TEST_DATABASE_URL"])
    password = "concurrent_contract_test_password_0044"
    async with db_sessions.begin() as owner:
        database_name = await owner.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        app_role = runtime_database_role_name(database_name)
        await owner.execute(
            text(f'ALTER ROLE "{app_role}" LOGIN PASSWORD \'{password}\'')
        )

    app_engine = create_async_engine(
        owner_url.set(username=app_role, password=password),
        pool_size=2,
        max_overflow=0,
    )
    app_sessions = async_sessionmaker(app_engine, expire_on_commit=False)
    try:
        for first_kind in ("contract", "market"):
            event_id = f"concurrent-{first_kind}-first"
            value = replace(_contract(), event_id=event_id)
            first_session = app_sessions()
            second_session = app_sessions()
            first_transaction = await first_session.begin()
            second_transaction = await second_session.begin()
            second_flush: asyncio.Task[None] | None = None
            try:
                assert await first_session.scalar(
                    text("SELECT public.assert_stratus_runtime_boundary(true)")
                ) is True
                assert await second_session.scalar(
                    text("SELECT public.assert_stratus_runtime_boundary(true)")
                ) is True
                second_pid = await second_session.scalar(
                    text("SELECT pg_backend_pid()")
                )
                assert isinstance(second_pid, int)

                if first_kind == "contract":
                    first_session.add(_contract_row(value))
                    second_session.add(
                        _market(
                            polymarket_id="concurrent-contract-first-market",
                            event_id=event_id,
                            market_date=date(2026, 7, 18),
                        )
                    )
                    expected_error = "market date does not match"
                else:
                    first_session.add(
                        _market(
                            polymarket_id="concurrent-market-first-market",
                            event_id=event_id,
                            market_date=date(2026, 7, 18),
                        )
                    )
                    second_session.add(_contract_row(value))
                    expected_error = "does not match every market date"

                await first_session.flush()
                second_flush = asyncio.create_task(second_session.flush())
                advisory_waiting = False
                for _ in range(50):
                    async with db_sessions() as observer:
                        advisory_waiting = bool(
                            await observer.scalar(
                                text(
                                    """
                                    SELECT EXISTS (
                                        SELECT 1
                                        FROM pg_locks
                                        WHERE pid = :pid
                                          AND locktype = 'advisory'
                                          AND NOT granted
                                    )
                                    """
                                ),
                                {"pid": second_pid},
                            )
                        )
                    if advisory_waiting:
                        break
                    await asyncio.sleep(0.1)

                assert advisory_waiting
                assert not second_flush.done()

                await first_transaction.commit()
                with pytest.raises(DBAPIError, match=expected_error):
                    await asyncio.wait_for(second_flush, timeout=5)
            finally:
                if first_transaction.is_active:
                    await first_transaction.rollback()
                if second_transaction.is_active:
                    await second_transaction.rollback()
                if second_flush is not None and not second_flush.done():
                    second_flush.cancel()
                    await asyncio.gather(second_flush, return_exceptions=True)
                await first_session.close()
                await second_session.close()

            async with db_sessions.begin() as owner:
                await owner.execute(
                    text(
                        "TRUNCATE TABLE public.resolution_contracts, "
                        "public.markets RESTART IDENTITY CASCADE"
                    )
                )
    finally:
        await app_engine.dispose()
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(f'ALTER ROLE "{app_role}" NOLOGIN PASSWORD NULL')
            )


async def test_0043_to_0044_upgrade_serializes_preflight_with_writers(
    db_sessions: SessionFactory,
) -> None:
    def alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    downgraded = await asyncio.to_thread(
        alembic, "downgrade", "20260717_0043"
    )
    assert downgraded.returncode == 0, downgraded.stderr

    event_id = "migration-preflight-concurrent-writer"
    value = replace(_contract(), event_id=event_id)
    upgrade_task: asyncio.Task[subprocess.CompletedProcess[str]] | None = None
    try:
        async with db_sessions.begin() as writer:
            writer.add(_contract_row(value))
            writer.add(
                _market(
                    polymarket_id="migration-preflight-concurrent-market",
                    event_id=event_id,
                    market_date=date(2026, 7, 18),
                )
            )
            await writer.flush()

            upgrade_task = asyncio.create_task(
                asyncio.to_thread(alembic, "upgrade", "20260719_0044")
            )
            migration_waiting = False
            for _ in range(50):
                async with db_sessions() as observer:
                    migration_waiting = bool(
                        await observer.scalar(
                            text(
                                """
                                SELECT EXISTS (
                                    SELECT 1
                                    FROM pg_stat_activity
                                    WHERE datname = current_database()
                                      AND pid <> pg_backend_pid()
                                      AND wait_event_type = 'Lock'
                                      AND query LIKE
                                          '%LOCK TABLE public.markets, public.resolution_contracts%'
                                )
                                """
                            )
                        )
                    )
                if migration_waiting:
                    break
                await asyncio.sleep(0.1)

            assert migration_waiting
            assert not upgrade_task.done()

        assert upgrade_task is not None
        raced_upgrade = await asyncio.wait_for(upgrade_task, timeout=60)
        assert raced_upgrade.returncode != 0
        assert "has mismatched market dates" in (
            raced_upgrade.stdout + raced_upgrade.stderr
        )
    finally:
        if upgrade_task is not None and not upgrade_task.done():
            upgrade_task.cancel()
            await asyncio.gather(upgrade_task, return_exceptions=True)
        async with db_sessions.begin() as owner:
            await owner.execute(
                delete(ResolutionContract).where(
                    ResolutionContract.event_id == event_id
                )
            )
            await owner.execute(delete(Market).where(Market.event_id == event_id))
        restored = await asyncio.to_thread(alembic, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr


async def test_0043_to_0044_upgrade_rejects_preexisting_invalid_contract_dates(
    db_sessions: SessionFactory,
) -> None:
    def alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    value = _contract()
    downgraded = await asyncio.to_thread(
        alembic, "downgrade", "20260717_0043"
    )
    assert downgraded.returncode == 0, downgraded.stderr
    try:
        malformed_rules = value.rules_raw.replace("17 Jul '26", "the target date")
        async with db_sessions.begin() as session:
            session.add(_contract_row(value, rules_raw=malformed_rules))
            session.add(
                _market(
                    polymarket_id="pre-0044-malformed-contract",
                    event_id=value.event_id,
                    market_date=date(2026, 7, 17),
                )
            )

        malformed_upgrade = await asyncio.to_thread(
            alembic, "upgrade", "20260719_0044"
        )
        assert malformed_upgrade.returncode != 0
        assert "has malformed rules" in (
            malformed_upgrade.stdout + malformed_upgrade.stderr
        )

        async with db_sessions.begin() as session:
            assert await session.scalar(
                text("SELECT version_num FROM public.alembic_version")
            ) == ("20260717_0043")
            await session.execute(
                delete(ResolutionContract).where(
                    ResolutionContract.event_id == value.event_id
                )
            )
            await session.execute(
                delete(Market).where(Market.event_id == value.event_id)
            )
            session.add(_contract_row(value))
            session.add(
                _market(
                    polymarket_id="pre-0044-mismatched-contract",
                    event_id=value.event_id,
                    market_date=date(2026, 7, 18),
                )
            )

        mismatched_upgrade = await asyncio.to_thread(
            alembic, "upgrade", "20260719_0044"
        )
        assert mismatched_upgrade.returncode != 0
        assert "has mismatched market dates" in (
            mismatched_upgrade.stdout + mismatched_upgrade.stderr
        )
    finally:
        async with db_sessions.begin() as session:
            await session.execute(
                delete(ResolutionContract).where(
                    ResolutionContract.event_id == value.event_id
                )
            )
            await session.execute(
                delete(Market).where(Market.event_id == value.event_id)
            )
        restored = await asyncio.to_thread(alembic, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr


async def test_resolution_contract_exact_retry_is_idempotent_and_mismatch_fails(
    db_sessions: SessionFactory,
) -> None:
    value = _contract()
    async with db_sessions.begin() as session:
        first = await upsert_resolution_contract(
            session, value, expected_market_date=value.resolution_date
        )
        await session.flush()
        discovered_at = first.discovered_at

        retry = await upsert_resolution_contract(
            session, value, expected_market_date=value.resolution_date
        )
        assert retry.id == first.id
        assert retry.discovered_at == discovered_at
        assert await session.scalar(select(func.count()).select_from(ResolutionContract)) == 1

        mismatches = (
            replace(value, source_family="national"),
            replace(value, source_url=f"{value.source_url}?revision=2"),
            replace(value, station_code="KBBB"),
            replace(value, local_timezone="Europe/Berlin"),
            replace(value, statistic=WeatherKind.LOW),
            replace(value, source_unit="F"),
            replace(value, decimal_places=1),
            replace(value, finalization_rule="source_finalized"),
            replace(value, license_status="reviewed"),
            replace(value, storage_permitted=True),
            replace(value, license_url="https://example.test/license"),
            replace(value, rules_raw=f"{value.rules_raw} Added clause."),
        )
        for mismatch in mismatches:
            with pytest.raises(
                ResolutionContractIntegrityError,
                match="differs from immutable stored content",
            ):
                await upsert_resolution_contract(
                    session,
                    mismatch,
                    expected_market_date=value.resolution_date,
                )

        await session.refresh(first)
        assert first.source_url == value.source_url
        assert first.rules_raw == value.rules_raw
        assert await session.scalar(select(func.count()).select_from(ResolutionContract)) == 1


async def test_resolution_contract_retry_never_reactivates_retired_row(
    db_sessions: SessionFactory,
) -> None:
    value = _contract()
    async with db_sessions.begin() as session:
        stored = await upsert_resolution_contract(
            session, value, expected_market_date=value.resolution_date
        )
        assert stored.discovered_at is not None
        with pytest.raises(
            DBAPIError,
            match="resolution contract retirement predates discovery",
        ):
            async with session.begin_nested():
                await session.execute(
                    update(ResolutionContract)
                    .where(ResolutionContract.id == stored.id)
                    .values(retired_at=stored.discovered_at - timedelta(microseconds=1))
                )

        retired_at = datetime.now(UTC)
        stored.retired_at = retired_at
        await session.flush()

        with pytest.raises(
            ResolutionContractIntegrityError,
            match="retired resolution contract cannot be reactivated",
        ):
            await upsert_resolution_contract(
                session, value, expected_market_date=value.resolution_date
            )

        await session.refresh(stored)
        assert stored.retired_at == retired_at
        assert await session.scalar(select(func.count()).select_from(ResolutionContract)) == 1


async def test_database_trigger_enforces_content_and_retirement_immutability(
    db_sessions: SessionFactory,
) -> None:
    value = _contract()
    async with db_sessions.begin() as session:
        stored = await upsert_resolution_contract(
            session, value, expected_market_date=value.resolution_date
        )
        await session.flush()

        with pytest.raises(DBAPIError, match="resolution contract content is immutable"):
            async with session.begin_nested():
                await session.execute(
                    update(ResolutionContract)
                    .where(ResolutionContract.id == stored.id)
                    .values(source_url=f"{value.source_url}?revision=2")
                )

        with pytest.raises(DBAPIError, match="resolution contract content is immutable"):
            async with session.begin_nested():
                await session.execute(
                    delete(ResolutionContract).where(ResolutionContract.id == stored.id)
                )

        retired_at = datetime.now(UTC)
        await session.execute(
            update(ResolutionContract)
            .where(ResolutionContract.id == stored.id)
            .values(retired_at=retired_at)
        )
        await session.refresh(stored)
        assert stored.retired_at == retired_at

        with pytest.raises(
            DBAPIError,
            match="resolution contract retirement is irreversible",
        ):
            async with session.begin_nested():
                await session.execute(
                    update(ResolutionContract)
                    .where(ResolutionContract.id == stored.id)
                    .values(retired_at=None)
                )

        with pytest.raises(
            DBAPIError,
            match="resolution contract retirement is irreversible",
        ):
            async with session.begin_nested():
                await session.execute(
                    update(ResolutionContract)
                    .where(ResolutionContract.id == stored.id)
                    .values(retired_at=retired_at)
                )


async def test_runtime_role_has_crud_but_not_boundary_bypass(
    db_sessions: SessionFactory,
) -> None:
    value = _contract()
    async with db_sessions.begin() as session:
        database_name = await session.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        runtime_role = runtime_database_group_role_name(database_name)
        app_role = runtime_database_role_name(database_name)
        assert runtime_role != runtime_database_group_role_name(
            f"{database_name}_other"
        )
        assert app_role != runtime_database_role_name(f"{database_name}_other")
        role_boundary = (
            await session.execute(
                text(
                    """
                    SELECT runtime.rolcanlogin AS runtime_login,
                           runtime.rolsuper AS runtime_super,
                           runtime.rolreplication AS runtime_replication,
                           runtime.rolbypassrls AS runtime_bypassrls,
                           app.rolsuper AS app_super,
                           app.rolreplication AS app_replication,
                           app.rolbypassrls AS app_bypassrls,
                           membership.admin_option,
                           membership.inherit_option,
                           membership.set_option
                    FROM pg_roles runtime
                    JOIN pg_auth_members membership
                      ON membership.roleid = runtime.oid
                    JOIN pg_roles app ON app.oid = membership.member
                    WHERE runtime.rolname = :runtime_role
                      AND app.rolname = :app_role
                    """
                ),
                {"runtime_role": runtime_role, "app_role": app_role},
            )
        ).one()
        assert tuple(role_boundary) == (
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            False,
        )

        with pytest.raises(
            DBAPIError,
            match="runtime boundary must execute as the database-scoped app login",
        ):
            async with session.begin_nested():
                await session.scalar(
                    text("SELECT public.assert_stratus_runtime_boundary(true)")
                )

        await session.execute(text(f'SET LOCAL ROLE "{app_role}"'))
        assert await session.scalar(text("SELECT current_user")) == app_role
        assert await session.scalar(
            text(
                "SELECT has_database_privilege("
                ":app_role, current_database(), 'TEMPORARY')"
            ),
            {"app_role": app_role},
        ) is False
        # SET ROLE proves inherited CRUD, but is deliberately not accepted as
        # the real runtime login: session_user remains the database owner.
        with pytest.raises(
            DBAPIError,
            match="runtime boundary must execute as the database-scoped app login",
        ):
            async with session.begin_nested():
                await session.scalar(
                    text("SELECT public.assert_stratus_runtime_boundary(true)")
                )

        station_id = await session.scalar(
            text(
                """
                INSERT INTO public.stations (
                    icao, name, city, lat, lon, tz, active, stage
                ) VALUES (
                    'RLP1', 'Runtime least privilege', 'Boundary',
                    1.0, 2.0, 'UTC', true, 'paper'
                )
                RETURNING id
                """
            )
        )
        assert station_id is not None
        assert (
            await session.scalar(
                text("SELECT name FROM public.stations WHERE id = :id"),
                {"id": station_id},
            )
            == "Runtime least privilege"
        )
        await session.execute(
            text("UPDATE public.stations SET city = 'Updated' WHERE id = :id"),
            {"id": station_id},
        )
        assert (
            await session.scalar(
                text("SELECT city FROM public.stations WHERE id = :id"),
                {"id": station_id},
            )
            == "Updated"
        )
        assert await session.scalar(
            text("SELECT version_num FROM public.alembic_version")
        ) == ALEMBIC_HEAD

        stored = await upsert_resolution_contract(
            session, value, expected_market_date=value.resolution_date
        )
        await session.flush()
        with pytest.raises(DBAPIError, match="resolution contract content is immutable"):
            async with session.begin_nested():
                await session.execute(
                    update(ResolutionContract)
                    .where(ResolutionContract.id == stored.id)
                    .values(source_url=f"{value.source_url}?runtime-bypass=1")
                )

        denied_statements = (
            "CREATE TEMP TABLE runtime_shadow_probe (id integer)",
            "TRUNCATE TABLE public.stations",
            "ALTER TABLE public.stations ADD COLUMN runtime_escape integer",
            "SET LOCAL session_replication_role = replica",
        )
        for statement in denied_statements:
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(text(statement))

        deleted = await session.execute(
            text("DELETE FROM public.stations WHERE id = :id"),
            {"id": station_id},
        )
        assert deleted.rowcount == 1


async def test_runtime_boundary_rejects_disabled_integrity_trigger(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        await session.execute(
            text(
                "ALTER TABLE public.resolution_contracts "
                "DISABLE TRIGGER trg_resolution_contract_market_date"
            )
        )
        with pytest.raises(DBAPIError, match="integrity trigger boundary drifted"):
            async with session.begin_nested():
                await session.scalar(
                    text("SELECT public.assert_stratus_runtime_boundary(false)")
                )
        await session.execute(
            text(
                "ALTER TABLE public.resolution_contracts "
                "ENABLE TRIGGER trg_resolution_contract_market_date"
            )
        )
        assert await session.scalar(
            text("SELECT public.assert_stratus_runtime_boundary(false)")
        ) is True


async def test_runtime_boundary_rejects_database_replication_role_default(
    db_sessions: SessionFactory,
) -> None:
    owner_url = make_url(os.environ["STRATUS_TEST_DATABASE_URL"])
    password = "continuity_test_password_0044"
    async with db_sessions.begin() as session:
        database_name = await session.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        app_role = runtime_database_role_name(database_name)
        await session.execute(
            text(f'ALTER ROLE "{app_role}" LOGIN PASSWORD \'{password}\'')
        )
        await session.execute(
            text(
                f'ALTER DATABASE "{database_name}" '
                "SET session_replication_role TO replica"
            )
        )

    app_url = owner_url.set(username=app_role, password=password)
    rejected_engine = create_async_engine(app_url)
    try:
        with pytest.raises(DBAPIError, match="session_replication_role is not origin"):
            async with rejected_engine.connect() as connection:
                await connection.scalar(
                    text("SELECT public.assert_stratus_runtime_boundary(true)")
                )
    finally:
        await rejected_engine.dispose()
        async with db_sessions.begin() as session:
            await session.execute(
                text(
                    f'ALTER DATABASE "{database_name}" '
                    "RESET session_replication_role"
                )
            )

    accepted_engine = create_async_engine(app_url)
    try:
        async with accepted_engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT public.assert_stratus_runtime_boundary(true)")
            ) is True
    finally:
        await accepted_engine.dispose()
        async with db_sessions.begin() as session:
            await session.execute(
                text(f'ALTER ROLE "{app_role}" NOLOGIN PASSWORD NULL')
            )


async def test_downgrade_refuses_to_remove_used_contract_boundary(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        await upsert_resolution_contract(
            session,
            _contract(),
            expected_market_date=date(2026, 7, 17),
        )

    with pytest.raises(
        DBAPIError,
        match="cannot downgrade 20260719_0044 with resolution contracts present",
    ):
        await asyncio.to_thread(
            command.downgrade,
            Config("alembic.ini"),
            "20260717_0043",
        )

    async with db_sessions() as session:
        assert await session.scalar(
            text("SELECT version_num FROM public.alembic_version")
        ) == ALEMBIC_HEAD
