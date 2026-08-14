"""Fixtures for integration tests against a real Postgres.

Activated only when STRATUS_TEST_DATABASE_URL is set, e.g.:

    docker compose up -d postgres
    docker compose exec -T postgres createdb -U stratus stratus_test
    STRATUS_TEST_DATABASE_URL=postgresql+asyncpg://stratus:stratus@localhost:5432/stratus_test \
        uv run pytest tests/integration

The schema is created by running the real Alembic migration chain, so these
tests also validate that the migrations apply cleanly and that every
ON CONFLICT constraint name the repositories target actually exists.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import date
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from stratus.config import (
    Settings,
    get_migration_settings,
    runtime_database_role_name,
    safety_database_group_role_name,
    safety_database_role_name,
)
from stratus.db.models import ProbabilityPipelineReleaseEpoch, WeatherKind
from stratus.db.session import create_engine as create_runtime_engine
from stratus.jobs.process_operator_commands import process_operator_commands
from stratus.repositories.accuracy import upsert_resolution_contract
from stratus.repositories.evidence_continuity import (
    EvidenceLanePlan,
    acquire_probability_pipeline_release_lock,
    append_probability_pipeline_release_epoch,
    build_probability_pipeline_release_plan,
    current_evidence_continuity_lanes,
    current_probability_pipeline_release_epoch,
    persist_probability_pipeline_release_plan,
)
from stratus.repositories.operator_commands import enqueue_command
from stratus.services.evidence_release_manifest import (
    build_evidence_release_manifest,
    decide_evidence_lane_actions,
)
from stratus.services.resolution_contracts import parse_resolution_contract
from stratus.services.safety_control import (
    acquire_paper_canary_lease_atomically,
    run_paper_canary_watchdog_once,
)

_TEST_SETTINGS = Settings(_env_file=None)
TEST_IMAGE_DIGEST = "b" * 64


@pytest.fixture(autouse=True)
def _install_explicit_test_api_engine(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database_url: str,
) -> None:
    """Give API integration tests their explicit owner-only test engine.

    Production ``stratus.db.session.create_engine`` never accepts an owner URL.
    API tests need administrative access because they deliberately exercise
    database triggers and immutable evidence, so the bypass lives only in this
    pytest fixture and cannot be activated by a runtime environment variable.
    """
    import stratus.api

    def create_test_admin_engine(
        settings: Settings,
        *,
        pool_size: int | None = None,
        max_overflow: int | None = None,
        read_only: bool = False,
    ) -> AsyncEngine:
        resolved_url = make_url(settings.resolved_database_url())
        database_name = resolved_url.database
        if (
            database_name is not None
            and resolved_url.username == runtime_database_role_name(database_name)
        ):
            return create_runtime_engine(
                settings,
                pool_size=pool_size,
                max_overflow=max_overflow,
                read_only=read_only,
            )

        resolved_pool_size = (
            settings.stratus_database_pool_size if pool_size is None else pool_size
        )
        resolved_max_overflow = (
            settings.stratus_database_max_overflow
            if max_overflow is None
            else max_overflow
        )
        return create_async_engine(
            migrated_database_url,
            pool_pre_ping=True,
            pool_size=resolved_pool_size,
            max_overflow=resolved_max_overflow,
        )

    monkeypatch.setattr(stratus.api, "create_engine", create_test_admin_engine)

# Cleaned before every test. runtime_control is intentionally NOT truncated:
# migration 0009 seeds it and the fail-closed logic depends on that row.
_TRUNCATE_TABLES = (
    "observation_recovery_points",
    "observation_recovery_artifacts",
    "evidence_continuity_lanes",
    "probability_pipeline_release_epochs",
    "probability_pipeline_release_plans",
    "official_resolution_exact_actuals",
    "official_resolution_source_responses",
    "official_resolution_license_attestations",
    "execution_cost_seals",
    "execution_postfill_marks",
    "execution_cost_obligations",
    "execution_fill_legs",
    "execution_reconciliation_observations",
    "execution_order_intents",
    "prospective_validation_outcomes",
    "prospective_validation_enrollments",
    "storage_capacity_samples",
    "evidence_version_history",
    "station_reliability_snapshots",
    "resolution_reconciliations",
    "settlement_cases",
    "clob_market_snapshots",
    "clob_book_batches",
    "gamma_outcome_events",
    "forecast_source_responses",
    "forecast_ingests",
    "forecast_trajectories",
    "settlement_probability_calibrators",
    "model_drift_events",
    "model_skill_reports",
    "accuracy_stack_weights",
    "accuracy_predictions",
    "forecast_evaluations",
    "settlement_actuals",
    "resolution_contracts",
    "forecast_runs",
    "forecast_model_registry",
    "crypto_research_results",
    "crypto_research_candidates",
    "crypto_research_experiments",
    "crypto_data_gaps",
    "crypto_market_snapshots",
    "crypto_archive_imports",
    "crypto_aggregate_trades",
    "crypto_candles",
    "crypto_symbols",
    "selection_policy_decisions",
    "orders",
    "positions",
    "signals",
    "model_probs",
    "market_prices",
    "markets",
    "forecasts",
    "historical_forecasts",
    "observations",
    "daily_actuals",
    "calibration",
    "stations",
    "bankroll_log",
    "telegram_deliveries",
    "notification_delivery_runtime",
    "job_runs",
    "validation_checkpoints",
    "health_audits",
    "paper_canary_safety_leases",
    "operator_commands",
)


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    url = os.environ["STRATUS_TEST_DATABASE_URL"]
    previous = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["MIGRATION_DATABASE_URL"] = url
    get_migration_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous
        get_migration_settings.cache_clear()


@pytest.fixture
async def db_sessions(
    migrated_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        await session.execute(
            text("TRUNCATE TABLE " + ", ".join(_TRUNCATE_TABLES) + " RESTART IDENTITY CASCADE")
        )
        # Migration 0009 seeds the legacy Polymarket control row. It is not
        # truncated because fail-closed reads require it to exist, but every
        # test must start from migration 0047's fail-closed PAPER baseline.
        await session.execute(
            text(
                "UPDATE runtime_control SET kill_switch = true, "
                "mode = 'paper'::execution_mode, live_confirmation_until = NULL, "
                "halted_reason = NULL, updated_at = now() WHERE id = 1"
            )
        )
        # Migration 0047 seals the venue baseline: Polymarket PAPER is stopped
        # until an observed lease resumes it; Binance is backtest-only.
        await session.execute(
            text(
                "UPDATE venue_runtime_control SET enabled = true, "
                "mode = 'paper'::venue_mode, kill_switch = true, "
                "halted_reason = NULL, live_confirmation_until = NULL, "
                "strategy_version = NULL, risk_config_version = 'legacy', "
                "last_reconciled_at = NULL WHERE venue = 'polymarket'"
            )
        )
        await session.execute(
            text(
                "UPDATE venue_runtime_control SET enabled = true, "
                "mode = 'backtest'::venue_mode, kill_switch = true, "
                "halted_reason = NULL, live_confirmation_until = NULL, "
                "strategy_version = NULL, "
                "risk_config_version = 'v1-conservative', "
                "last_reconciled_at = NULL WHERE venue = 'binance_spot'"
            )
        )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def canary_role_sessions(
    db_sessions: async_sessionmaker[AsyncSession],
    migrated_database_url: str,
) -> AsyncIterator[SimpleNamespace]:
    """Expose the actual runtime and safety logins, never SET ROLE stand-ins."""
    owner_url = make_url(migrated_database_url)
    runtime_password = "runtime_canary_role_test_password_0047"
    safety_password = "safety_canary_role_test_password_0047"
    async with db_sessions.begin() as owner:
        database_name = await owner.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        runtime_role = runtime_database_role_name(database_name)
        safety_group_role = safety_database_group_role_name(database_name)
        safety_role = safety_database_role_name(database_name)
        await owner.execute(
            text(f'ALTER ROLE "{runtime_role}" LOGIN PASSWORD \'{runtime_password}\'')
        )
        await owner.execute(
            text(f'ALTER ROLE "{safety_role}" LOGIN PASSWORD \'{safety_password}\'')
        )

    runtime_engine = create_async_engine(
        owner_url.set(username=runtime_role, password=runtime_password),
        pool_pre_ping=True,
    )
    safety_engine = create_async_engine(
        owner_url.set(username=safety_role, password=safety_password),
        pool_pre_ping=True,
    )
    safety_emergency_engine = create_async_engine(
        owner_url.set(username=safety_role, password=safety_password),
        pool_pre_ping=True,
    )
    roles = SimpleNamespace(
        runtime=async_sessionmaker(runtime_engine, expire_on_commit=False),
        safety=async_sessionmaker(safety_engine, expire_on_commit=False),
        safety_emergency=async_sessionmaker(
            safety_emergency_engine,
            expire_on_commit=False,
        ),
        runtime_role=runtime_role,
        safety_group_role=safety_group_role,
        safety_role=safety_role,
    )
    try:
        async with roles.runtime() as session:
            assert await session.scalar(
                text("SELECT public.assert_stratus_runtime_boundary(true)")
            ) is True
        async with roles.safety() as session:
            assert await session.scalar(
                text("SELECT public.assert_stratus_safety_boundary(true)")
            ) is True
        async with roles.safety_emergency() as session:
            assert await session.scalar(
                text("SELECT public.assert_stratus_safety_boundary(true)")
            ) is True
        yield roles
    finally:
        await runtime_engine.dispose()
        await safety_engine.dispose()
        await safety_emergency_engine.dispose()
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(f'ALTER ROLE "{runtime_role}" NOLOGIN PASSWORD NULL')
            )
            await owner.execute(
                text(f'ALTER ROLE "{safety_role}" NOLOGIN PASSWORD NULL')
            )


@pytest.fixture
async def active_paper_canary(
    db_sessions: async_sessionmaker[AsyncSession],
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> SimpleNamespace:
    """Open one genuine, watchdog-observed PAPER execution lease.

    Migration 0047 deliberately makes ``kill_switch=false`` impossible to
    manufacture with a direct fixture UPDATE.  Execution tests therefore use
    the same split safety/runtime control path as the release canary: the
    safety login reserves a lease, the watchdog observes it, and the runtime
    login consumes the exact reserved resume command.
    """
    roles = canary_role_sessions
    nonce = "integration-paper-canary-nonce-" + ("n" * 32)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        lease_id="integration-paper-canary-lease",
        run_id="integration-paper-canary-run",
        acquire_request_id="integration-paper-canary-acquire",
        resume_request_id="integration-paper-canary-resume",
        direct_stop_request_id="integration-paper-canary-direct-stop",
        release_request_id="integration-paper-canary-release",
        resume_actor="integration-suite",
        resume_reason="Exercise PAPER execution under migration 0047.",
        nonce=nonce,
        ttl_seconds=30,
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    async with roles.runtime.begin() as session:
        await enqueue_command(
            session,
            request_id="integration-paper-canary-resume",
            command="resume",
            payload={"venue": "polymarket"},
            actor="integration-suite",
            reason="Exercise PAPER execution under migration 0047.",
        )
    assert await process_operator_commands(
        Settings(_env_file=None, database_url=migrated_database_url),
        roles.runtime,
    ) == (1, 1)
    async with db_sessions() as session:
        assert await session.scalar(
            text("SELECT public.paper_canary_execution_authorized()")
        ) is True
    return roles


@pytest.fixture
async def canonical_probability_release_inputs(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Seed the stable inputs required to build the canonical release manifest."""
    async with db_sessions.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO stations (
                    icao, name, city, lat, lon, tz, active, stage
                ) VALUES
                    ('KLGA', 'LaGuardia', 'New York City', 40.77, -73.87,
                     'America/New_York', true, 'approved'),
                    ('KCOH', 'Coherence Test', 'Coherence', 40, -73,
                     'UTC', true, 'approved')
                """
            )
        )
        source_url = "https://www.wunderground.com/history/daily/us/ny/KLGA"
        rules = (
            "This market resolves to the highest temperature on 12 Jul '26. "
            f"The resolution source is available here: {source_url}. "
            "This market cannot resolve until the first data point for the following date. "
            "The source measures temperatures to whole degrees Fahrenheit."
        )
        await upsert_resolution_contract(
            session,
            parse_resolution_contract(
                event_id="live-execution-fixture",
                rules_raw=rules,
                resolution_source=source_url,
                station_code="KLGA",
                local_timezone="America/New_York",
                statistic=WeatherKind.HIGH,
            ),
            expected_market_date=date(2026, 7, 12),
        )
        await session.execute(
            text(
                """
                INSERT INTO markets (
                    polymarket_id, event_id, market_date, kind, rules_raw,
                    resolves_at
                ) VALUES (
                    'live-execution-fixture-market', 'live-execution-fixture',
                    DATE '2026-07-12', 'high', :rules,
                    TIMESTAMPTZ '2026-07-13 00:00:00+00'
                )
                """
            ),
            {"rules": rules},
        )


@pytest.fixture
async def activated_probability_release_epoch(
    db_sessions: async_sessionmaker[AsyncSession],
    canonical_probability_release_inputs: None,
) -> Callable[[Settings], Awaitable[ProbabilityPipelineReleaseEpoch]]:
    """Activate the real canonical manifest and expose a reviewed successor helper."""

    async def activate(settings: Settings) -> ProbabilityPipelineReleaseEpoch:
        async with db_sessions.begin() as session:
            await acquire_probability_pipeline_release_lock(session)
            candidate = await build_evidence_release_manifest(settings, session)
            pipeline = candidate.manifest.get("pipeline")
            assert isinstance(pipeline, dict)
            material_fingerprint = pipeline.get("material_fingerprint")
            activation_manifest_hash = pipeline.get("activation_manifest_hash")
            assert isinstance(material_fingerprint, str)
            assert isinstance(activation_manifest_hash, str)
            active = await current_probability_pipeline_release_epoch(session)
            active_lanes = await current_evidence_continuity_lanes(session)
            decisions = decide_evidence_lane_actions(
                candidate,
                (
                    {lane.lane_name: lane.identity_hash for lane in active_lanes}
                    if active is not None
                    else None
                ),
            )
            plan = build_probability_pipeline_release_plan(
                pipeline_version=settings.probability_pipeline_version,
                material_fingerprint=material_fingerprint,
                activation_manifest_hash=activation_manifest_hash,
                image_digest=settings.release_image_digest,
                previous_epoch_id=active.id if active is not None else None,
                release_manifest=candidate.manifest,
                lane_plans=tuple(
                    EvidenceLanePlan(
                        lane_name=decision.lane_name,
                        action=decision.action,
                        identity_hash=decision.identity_hash,
                        manifest=decision.manifest,
                        rationale=decision.rationale,
                    )
                    for decision in decisions
                ),
                reviewed_by="pytest-integration",
                approval_reason=(
                    "Activate canonical integration evidence for reviewed runtime settings."
                ),
            )
            await persist_probability_pipeline_release_plan(session, plan)
            return await append_probability_pipeline_release_epoch(
                session,
                plan,
                expected_plan_hash=plan.plan_hash,
            )

    await activate(_TEST_SETTINGS.model_copy(update={"release_image_digest": TEST_IMAGE_DIGEST}))
    return activate
