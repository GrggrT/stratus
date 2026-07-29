from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta, tzinfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import stratus.operator_api as operator_api_module
import stratus.repositories.evidence_continuity_gate as gate_module
import stratus.repositories.evidence_continuity_reporting as reporting_module
from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import ProbabilityPipelineReleaseEpoch, WeatherKind
from stratus.repositories.accuracy import upsert_resolution_contract
from stratus.repositories.evidence_continuity import (
    EvidenceLanePlan,
    ProbabilityPipelineReleasePlan,
    append_probability_pipeline_release_epoch,
    build_probability_pipeline_release_plan,
    persist_probability_pipeline_release_plan,
    probability_pipeline_release_cohort,
)
from stratus.repositories.evidence_continuity_gate import probability_release_gate
from stratus.repositories.evidence_continuity_reporting import evidence_continuity_summary
from stratus.services.evidence_continuity import EvidenceDueCounts
from stratus.services.evidence_release_manifest import (
    EvidenceReleaseLaneManifest,
    EvidenceReleaseManifest,
)
from stratus.services.execution_cost import source_payload_hash
from stratus.services.resolution_contracts import parse_resolution_contract

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
REVIEWER = "pytest-api-reviewer"
LANE_NAMES = (
    "predictive_validation",
    "selection_policy",
    "execution_evidence",
    "operational_legacy",
    "station_lifecycle",
    "official_resolution_truth",
)


def _lane_plans(
    *,
    action: str,
    variants: dict[str, str] | None = None,
    actions: dict[str, str] | None = None,
) -> tuple[EvidenceLanePlan, ...]:
    variants = variants or {}
    actions = actions or {}
    plans: list[EvidenceLanePlan] = []
    for lane_name in LANE_NAMES:
        manifest = {
            "protocol": "evidence-api-lane-test-v1",
            "lane": lane_name,
            "identity": variants.get(lane_name, "baseline"),
        }
        plans.append(
            EvidenceLanePlan(
                lane_name=lane_name,
                action=actions.get(lane_name, action),
                identity_hash=source_payload_hash(manifest),
                manifest=manifest,
                rationale=f"Activate API integration evidence for {lane_name}.",
            )
        )
    return tuple(plans)


def _candidate_manifest(
    *,
    cohort: str,
    lane_plans: tuple[EvidenceLanePlan, ...],
    variant: str = "baseline",
) -> EvidenceReleaseManifest:
    manifest = {
        "protocol": "evidence-api-release-test-v1",
        "pipeline": {"cohort": cohort},
        "variant": variant,
    }
    return EvidenceReleaseManifest(
        manifest=manifest,
        manifest_hash=source_payload_hash(manifest),
        lanes=tuple(
            EvidenceReleaseLaneManifest(
                lane_name=lane.lane_name,  # type: ignore[arg-type]
                manifest=lane.manifest,
                identity_hash=lane.identity_hash,
                missing_requirements=(),
            )
            for lane in lane_plans
        ),
    )


async def _seed_manifest_requirements(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO stations (icao, name, city, lat, lon, tz, active, stage)
            VALUES ('TAPI', 'API Test Station', 'API Test City', 40, -73,
                    'UTC', true, 'paper')
            """
        )
    )
    source_url = "https://www.wunderground.com/history/daily/us/test/TAPI"
    rules = (
        "This market resolves to the highest temperature on 12 Jul '26. "
        f"The resolution source is available here: {source_url}. "
        "This market cannot resolve until the first data point for the following date. "
        "The source measures temperatures to whole degrees Fahrenheit."
    )
    await upsert_resolution_contract(
        session,
        parse_resolution_contract(
            event_id="api-evidence-event",
            rules_raw=rules,
            resolution_source=source_url,
            station_code="TAPI",
            local_timezone="UTC",
            statistic=WeatherKind.HIGH,
        ),
        expected_market_date=date(2026, 7, 12),
    )
    await session.execute(
        text(
            """
            INSERT INTO markets (
                polymarket_id, event_id, market_date, kind, rules_raw, resolves_at
            ) VALUES (
                'api-evidence-market', 'api-evidence-event', DATE '2026-07-12',
                'high', :rules, TIMESTAMPTZ '2026-07-13 00:00:00+00'
            )
            """
        ),
        {"rules": rules},
    )


async def _activate_plan(
    session: AsyncSession,
    plan: ProbabilityPipelineReleasePlan,
) -> ProbabilityPipelineReleaseEpoch:
    await persist_probability_pipeline_release_plan(session, plan)
    return await append_probability_pipeline_release_epoch(
        session,
        plan,
        expected_plan_hash=plan.plan_hash,
    )


@pytest.mark.asyncio
async def test_continuity_summary_restarts_at_newest_cross_lane_fork(
    db_sessions: SessionFactory,
) -> None:
    settings = Settings(_env_file=None, release_image_digest="91" * 32)
    fingerprint = "7" * 16
    activation_hash = "8" * 64
    cohort = probability_pipeline_release_cohort(
        settings.probability_pipeline_version,
        fingerprint,
        activation_hash,
    )
    first_lanes = _lane_plans(action="fork")
    first_manifest = _candidate_manifest(cohort=cohort, lane_plans=first_lanes)
    first_plan = build_probability_pipeline_release_plan(
        pipeline_version=settings.probability_pipeline_version,
        material_fingerprint=fingerprint,
        activation_manifest_hash=activation_hash,
        image_digest=settings.release_image_digest,
        previous_epoch_id=None,
        release_manifest=first_manifest.manifest,
        lane_plans=first_lanes,
        reviewed_by=REVIEWER,
        approval_reason="Open the baseline cross-lane reporting integration cohort.",
    )
    async with db_sessions.begin() as session:
        first = await _activate_plan(session, first_plan)

    successor_lanes = _lane_plans(
        action="continue",
        variants={"execution_evidence": "execution-v2"},
        actions={"execution_evidence": "fork"},
    )
    successor_manifest = _candidate_manifest(
        cohort=cohort,
        lane_plans=successor_lanes,
        variant="successor",
    )
    successor_plan = build_probability_pipeline_release_plan(
        pipeline_version=settings.probability_pipeline_version,
        material_fingerprint=fingerprint,
        activation_manifest_hash=activation_hash,
        image_digest=settings.release_image_digest,
        previous_epoch_id=first.id,
        release_manifest=successor_manifest.manifest,
        lane_plans=successor_lanes,
        reviewed_by=REVIEWER,
        approval_reason="Fork execution evidence without relabelling the older shared sample.",
    )
    async with db_sessions.begin() as session:
        successor = await _activate_plan(session, successor_plan)
    assert successor.opened_at > first.opened_at

    async with db_sessions() as session:
        summary = await evidence_continuity_summary(
            session,
            candidate_cohort=cohort,
            candidate_image_digest=settings.release_image_digest,
            now=successor.opened_at + timedelta(hours=1),
        )

    current = next(lane for lane in summary.lanes if lane.scope == "current")
    assert current.opened_at == successor.opened_at
    assert summary.collection_window.candidate_full_days == 0
    assert summary.liveness.value == "stalled"
    assert summary.collection_window.missing_scheduler_jobs == (
        "execution",
        "execution-cost-maintenance",
        "settlement",
    )


@pytest.mark.asyncio
async def test_operational_fork_resets_collection_not_formal_denominators(
    db_sessions: SessionFactory,
) -> None:
    settings = Settings(_env_file=None, release_image_digest="92" * 32)
    fingerprint = "9" * 16
    activation_hash = "a" * 64
    cohort = probability_pipeline_release_cohort(
        settings.probability_pipeline_version,
        fingerprint,
        activation_hash,
    )
    first_lanes = _lane_plans(action="fork")
    first_manifest = _candidate_manifest(cohort=cohort, lane_plans=first_lanes)
    first_plan = build_probability_pipeline_release_plan(
        pipeline_version=settings.probability_pipeline_version,
        material_fingerprint=fingerprint,
        activation_manifest_hash=activation_hash,
        image_digest=settings.release_image_digest,
        previous_epoch_id=None,
        release_manifest=first_manifest.manifest,
        lane_plans=first_lanes,
        reviewed_by=REVIEWER,
        approval_reason="Open the baseline operational reporting integration cohort.",
    )
    async with db_sessions.begin() as session:
        await _seed_manifest_requirements(session)
        first = await _activate_plan(session, first_plan)
        await session.execute(
            text(
                """
                INSERT INTO signals (
                    market_id, ts, side, model_prob, market_price, edge,
                    kelly_frac, size_usd, confidence, status,
                    token_id, benchmark_protocol
                )
                SELECT id, :ts, 'buy_yes', 0.60, 0.50, 0.10,
                       0.01, 5.00, 'A', 'pending',
                       'operational-boundary-yes', 'legacy-synthetic-yes-v0'
                FROM markets
                WHERE polymarket_id = 'api-evidence-market'
                """
            ),
            {"ts": first.opened_at},
        )

    successor_lanes = _lane_plans(
        action="continue",
        variants={"operational_legacy": "reporting-v2"},
        actions={"operational_legacy": "fork"},
    )
    successor_manifest = _candidate_manifest(
        cohort=cohort,
        lane_plans=successor_lanes,
        variant="operational-successor",
    )
    successor_plan = build_probability_pipeline_release_plan(
        pipeline_version=settings.probability_pipeline_version,
        material_fingerprint=fingerprint,
        activation_manifest_hash=activation_hash,
        image_digest=settings.release_image_digest,
        previous_epoch_id=first.id,
        release_manifest=successor_manifest.manifest,
        lane_plans=successor_lanes,
        reviewed_by=REVIEWER,
        approval_reason="Fork operational reporting without resetting formal evidence.",
    )
    async with db_sessions.begin() as session:
        successor = await _activate_plan(session, successor_plan)
    assert successor.opened_at > first.opened_at

    async with db_sessions() as session:
        summary = await evidence_continuity_summary(
            session,
            candidate_cohort=cohort,
            candidate_image_digest=settings.release_image_digest,
            now=successor.opened_at + timedelta(hours=1),
        )

    current = next(lane for lane in summary.lanes if lane.scope == "current")
    assert current.opened_at == first.opened_at
    assert summary.signal_rows_since_epoch == 1
    assert summary.pending_signals == 1
    assert summary.collection_window.candidate_full_days == 0
    assert summary.collection_window.evaluated_days == 0
    assert summary.velocity.outcomes_per_day is None


@pytest.mark.asyncio
async def test_evidence_continuity_api_requires_reviewed_epoch_activation(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    settings = Settings(
        database_url=migrated_database_url,
        operator_api_key="continuity-secret",
        release_image_digest="ab" * 32,
    )
    app = create_app(settings)
    app.state.sessions = db_sessions
    reason = "Freeze the first reviewed prospective evidence cohort"

    async with db_sessions.begin() as session:
        await _seed_manifest_requirements(session)
        gate = await probability_release_gate(session, settings)
        assert gate.allowed is False
        assert gate.reason == "evidence_continuity_unactivated"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "continuity-secret"},
    ) as client:
        before = await client.get("/api/v1/evidence-continuity")
        assert before.status_code == 200
        before_payload = before.json()
        assert before_payload["state"] == "evidence_continuity_unactivated"
        assert before_payload["execution_gate_reason"] == (
            "evidence_continuity_unactivated"
        )
        assert before_payload["active_epoch"] is None
        assert before_payload["cohort_match"] is False
        assert before_payload["current_bottleneck"] == "release_activation_required"
        assert before_payload["outcome_velocity_per_day"] is None
        assert before_payload["eta_200_at"] is None
        assert before_payload["eta_reason"] == "insufficient_full_days_7_required"
        assert before_payload["liveness"] == "waiting_for_due"
        assert before_payload["collection_window"]["candidate_full_days"] == 0
        assert before_payload["collection_window"]["due_counts"]["total"] == 0

        plan = await client.post(
            "/api/v1/evidence-continuity/release-plan",
            json={"reviewed_by": REVIEWER, "approval_reason": reason},
        )
        assert plan.status_code == 200
        plan_payload = plan.json()
        assert plan_payload["requires_activation"] is True
        assert plan_payload["active_cohort"] is None
        assert plan_payload["image_digest"] == "ab" * 32
        assert plan_payload["reviewed_by"] == REVIEWER
        assert len(plan_payload["lane_plans"]) == 6
        assert plan_payload["persisted_plan_id"] is not None
        assert plan_payload["persisted_record_hash"] != "0" * 64
        assert plan_payload["planned_at"] is not None
        assert plan_payload["release_manifest_hash"]
        assert plan_payload["lane_manifest_hash"]

        stale = await client.post(
            "/api/v1/evidence-continuity/release-epochs",
            json={
                "reviewed_by": REVIEWER,
                "approval_reason": reason,
                "expected_plan_hash": "cd" * 32,
            },
        )
        assert stale.status_code == 409

        activated = await client.post(
            "/api/v1/evidence-continuity/release-epochs",
            json={
                "reviewed_by": REVIEWER,
                "approval_reason": reason,
                "expected_plan_hash": plan_payload["plan_hash"],
            },
        )
        assert activated.status_code == 200
        activated_payload = activated.json()["activated"]
        assert activated_payload["cohort"] == plan_payload["cohort"]
        assert activated_payload["approval_reason"] == reason
        assert activated_payload["record_hash"] != "0" * 64

        repeated = await client.post(
            "/api/v1/evidence-continuity/release-epochs",
            json={
                "reviewed_by": REVIEWER,
                "approval_reason": reason,
                "expected_plan_hash": plan_payload["plan_hash"],
            },
        )
        assert repeated.status_code == 409

        after = await client.get("/api/v1/evidence-continuity")
        assert after.status_code == 200
        after_payload = after.json()
        assert after_payload["state"] == "collecting"
        assert after_payload["execution_gate_allowed"] is True
        assert after_payload["execution_gate_reason"] == "evidence_continuity_match"
        assert after_payload["cohort_match"] is True
        assert after_payload["epoch_count"] == 1
        assert after_payload["active_epoch"]["cohort"] == plan_payload["cohort"]
        assert len(after_payload["continuity_lanes"]) == 6
        assert after_payload["lanes"][0]["scope"] == "current"
        assert after_payload["complete_days"] == 0
        assert after_payload["outcome_velocity_per_day"] is None
        assert after_payload["eta_reason"] == "insufficient_full_days_7_required"
        assert after_payload["liveness"] == "waiting_for_due"
        assert after_payload["collection_window"]["evaluated_days"] == 0

        epoch_history = await client.get("/api/v1/evidence-continuity/epochs")
        assert epoch_history.status_code == 200
        assert epoch_history.json()["count"] == 1
        lane_history = await client.get("/api/v1/evidence-continuity/lanes")
        assert lane_history.status_code == 200
        assert lane_history.json()["count"] == 6

    async with db_sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProbabilityPipelineReleaseEpoch))
            == 1
        )
        gate = await probability_release_gate(session, settings)
        assert gate.allowed is True
        assert gate.active_cohort == gate.candidate_cohort


@pytest.mark.asyncio
async def test_probability_release_gate_has_stable_multilane_reasons(
    db_sessions: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, release_image_digest="12" * 32)
    fingerprint = "1" * 16
    activation_hash = "a" * 64
    cohort = probability_pipeline_release_cohort(
        settings.probability_pipeline_version,
        fingerprint,
        activation_hash,
    )
    baseline_lanes = _lane_plans(action="fork")
    candidate = _candidate_manifest(cohort=cohort, lane_plans=baseline_lanes)

    async def candidate_manifest(
        _settings: Settings,
        _session: AsyncSession,
    ) -> EvidenceReleaseManifest:
        return candidate

    monkeypatch.setattr(
        gate_module,
        "build_evidence_release_manifest",
        candidate_manifest,
    )
    async with db_sessions.begin() as session:
        unactivated = await probability_release_gate(session, settings)
        assert unactivated.reason == "evidence_continuity_unactivated"

        first_plan = build_probability_pipeline_release_plan(
            pipeline_version=settings.probability_pipeline_version,
            material_fingerprint=fingerprint,
            activation_manifest_hash=activation_hash,
            image_digest=settings.release_image_digest,
            previous_epoch_id=None,
            release_manifest=candidate.manifest,
            lane_plans=baseline_lanes,
            reviewed_by=REVIEWER,
            approval_reason="Activate the stable release gate reason integration cohort.",
        )
        first = await _activate_plan(session, first_plan)
        exact = await probability_release_gate(session, settings)
        assert exact.allowed is True
        assert exact.reason == "evidence_continuity_match"

        changed_lanes = _lane_plans(
            action="fork",
            variants={"selection_policy": "changed-v2"},
        )
        candidate = _candidate_manifest(cohort=cohort, lane_plans=changed_lanes)
        changed = await probability_release_gate(session, settings)
        assert changed.reason == "evidence_lane_identity_mismatch"
        assert changed.lane_identity_mismatches == ("selection_policy",)

        candidate = _candidate_manifest(cohort=cohort, lane_plans=baseline_lanes)
        image_mismatch = await probability_release_gate(
            session,
            Settings(_env_file=None, release_image_digest="34" * 32),
        )
        assert image_mismatch.reason == "evidence_release_identity_mismatch"

        candidate = _candidate_manifest(
            cohort=cohort,
            lane_plans=baseline_lanes,
            variant="global-v2",
        )
        global_mismatch = await probability_release_gate(session, settings)
        assert global_mismatch.reason == "evidence_release_identity_mismatch"

        frozen_lanes = _lane_plans(
            action="continue",
            variants={"execution_evidence": "frozen-v2"},
            actions={"execution_evidence": "freeze"},
        )
        candidate = _candidate_manifest(cohort=cohort, lane_plans=baseline_lanes)
        frozen_plan = build_probability_pipeline_release_plan(
            pipeline_version=settings.probability_pipeline_version,
            material_fingerprint=fingerprint,
            activation_manifest_hash=activation_hash,
            image_digest=settings.release_image_digest,
            previous_epoch_id=first.id,
            release_manifest=candidate.manifest,
            lane_plans=frozen_lanes,
            reviewed_by=REVIEWER,
            approval_reason="Freeze changed execution evidence for stable gate testing.",
        )
        await _activate_plan(session, frozen_plan)
        candidate = _candidate_manifest(cohort=cohort, lane_plans=frozen_lanes)
        frozen = await probability_release_gate(session, settings)
        assert frozen.reason == "evidence_lane_frozen"
        assert frozen.frozen_lanes == ("execution_evidence",)


@pytest.mark.asyncio
async def test_evidence_continuity_api_uses_only_due_complete_scheduler_days(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=migrated_database_url,
        operator_api_key="continuity-liveness-secret",
        release_image_digest="ef" * 32,
    )
    async with db_sessions.begin() as session:
        await _seed_manifest_requirements(session)
    setup_app = create_app(settings)
    setup_app.state.sessions = db_sessions
    setup_reason = "Activate the scheduler liveness integration cohort."
    async with AsyncClient(
        transport=ASGITransport(app=setup_app),
        base_url="http://test",
        headers={"X-API-Key": "continuity-liveness-secret"},
    ) as client:
        preview = await client.post(
            "/api/v1/evidence-continuity/plan-hash",
            json={"reviewed_by": REVIEWER, "approval_reason": setup_reason},
        )
        assert preview.status_code == 200
        activated = await client.post(
            "/api/v1/evidence-continuity/activate",
            json={
                "reviewed_by": REVIEWER,
                "approval_reason": setup_reason,
                "expected_plan_hash": preview.json()["plan_hash"],
            },
        )
        assert activated.status_code == 200
    async with db_sessions() as session:
        epoch = await session.scalar(
            select(ProbabilityPipelineReleaseEpoch)
            .order_by(ProbabilityPipelineReleaseEpoch.id.desc())
            .limit(1)
        )
    assert epoch is not None
    opened = epoch.opened_at.astimezone(UTC)
    opened_midnight = opened.replace(hour=0, minute=0, second=0, microsecond=0)
    first_full_day = (
        opened_midnight
        if opened == opened_midnight
        else opened_midnight + timedelta(days=1)
    )
    collection_end = first_full_day + timedelta(days=7)
    frozen_now = collection_end + timedelta(minutes=45)

    async with db_sessions.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO job_runs (job_id, scheduled_at, finished_at, status, error)
                SELECT 'execution', tick, tick, 'success', NULL
                FROM generate_series(
                    CAST(:started_at AS timestamptz) + interval '1 minute',
                    CAST(:ended_at AS timestamptz),
                    interval '1 minute'
                ) AS tick
                """
            ),
            {
                "started_at": first_full_day,
                "ended_at": frozen_now,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO job_runs (job_id, scheduled_at, finished_at, status, error)
                SELECT 'execution-cost-maintenance', tick, tick, 'success', NULL
                FROM generate_series(
                    CAST(:started_at AS timestamptz) + interval '15 seconds',
                    CAST(:ended_at AS timestamptz),
                    interval '15 seconds'
                ) AS tick
                """
            ),
            {"started_at": first_full_day, "ended_at": frozen_now},
        )
        await session.execute(
            text(
                """
                INSERT INTO job_runs (job_id, scheduled_at, finished_at, status, error)
                SELECT 'settlement', tick, tick, 'success', NULL
                FROM generate_series(
                    CAST(:started_at AS timestamptz) + interval '10 minutes',
                    CAST(:ended_at AS timestamptz),
                    interval '10 minutes'
                ) AS tick
                """
            ),
            {"started_at": first_full_day, "ended_at": frozen_now},
        )

    async with db_sessions() as session:
        scheduler_windows = await reporting_module._scheduler_coverage_windows(
            session,
            historical_started_at=first_full_day,
            historical_ended_at=collection_end,
            current_started_at=collection_end,
            now=frozen_now,
        )
    assert len(scheduler_windows) == 8
    assert all(not window.missing_scheduler_jobs for window in scheduler_windows)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return frozen_now if tz is not None else frozen_now.replace(tzinfo=None)

    monkeypatch.setattr(operator_api_module, "datetime", FrozenDateTime)
    app = create_app(settings)
    app.state.sessions = db_sessions
    headers = {"X-API-Key": "continuity-liveness-secret"}
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    ) as client:
        complete = await client.get("/api/v1/evidence-continuity")
    assert complete.status_code == 200
    payload = complete.json()
    window = payload["collection_window"]
    assert payload["liveness"] == "complete"
    assert payload["complete_days"] == 7
    assert payload["outcome_velocity_per_day"] == 0.0
    assert payload["eta_reason"] == "no_outcomes_in_complete_days"
    assert window["candidate_full_days"] == 7
    assert window["evaluated_days"] == 7
    assert window["complete_days"] == 7
    assert window["maintenance_excluded_days"] == 0
    assert window["missing_scheduler_jobs"] == []
    assert window["due_counts"]["total"] == 0

    async with db_sessions.begin() as session:
        await session.execute(
            text(
                """
                DELETE FROM job_runs
                WHERE job_id = 'execution-cost-maintenance'
                  AND scheduled_at > :gap_started_at
                  AND scheduled_at <= :ended_at
                """
            ),
            {
                "gap_started_at": frozen_now - timedelta(minutes=1),
                "ended_at": frozen_now,
            },
        )
    app.state.evidence_continuity_response_cache.invalidate()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    ) as client:
        current_stalled = await client.get("/api/v1/evidence-continuity")
    assert current_stalled.status_code == 200
    current_stalled_payload = current_stalled.json()
    assert current_stalled_payload["liveness"] == "stalled"
    assert current_stalled_payload["collection_window"]["stalled_days"] == 0
    assert current_stalled_payload["collection_window"]["missing_scheduler_jobs"] == [
        "execution-cost-maintenance"
    ]

    original_due_counts_by_day = reporting_module._due_counts_by_day

    async def current_overdue_due_counts(
        *args: object,
        **kwargs: object,
    ) -> tuple[dict[datetime, EvidenceDueCounts], dict[datetime, int]]:
        due_by_day, outcomes_by_day = await original_due_counts_by_day(*args, **kwargs)  # type: ignore[arg-type]
        due_by_day[current_day] = EvidenceDueCounts(mark_overdue=1)
        return due_by_day, outcomes_by_day

    current_day = frozen_now.replace(hour=0, minute=0, second=0, microsecond=0)
    with monkeypatch.context() as context:
        context.setattr(
            reporting_module,
            "_due_counts_by_day",
            current_overdue_due_counts,
        )
        app.state.evidence_continuity_response_cache.invalidate()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
        ) as client:
            overdue_and_stalled = await client.get("/api/v1/evidence-continuity")
    assert overdue_and_stalled.status_code == 200
    overdue_payload = overdue_and_stalled.json()
    assert overdue_payload["liveness"] == "overdue"
    assert overdue_payload["collection_window"]["due_counts"]["mark_overdue"] == 1
    assert overdue_payload["collection_window"]["missing_scheduler_jobs"] == [
        "execution-cost-maintenance"
    ]

    async with db_sessions.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO job_runs (job_id, scheduled_at, finished_at, status, error)
                SELECT 'execution-cost-maintenance', tick, tick, 'success', NULL
                FROM generate_series(
                    CAST(:started_at AS timestamptz) + interval '15 seconds',
                    CAST(:ended_at AS timestamptz),
                    interval '15 seconds'
                ) AS tick
                ON CONFLICT (job_id, scheduled_at) DO NOTHING
                """
            ),
            {
                "started_at": frozen_now - timedelta(minutes=1),
                "ended_at": frozen_now,
            },
        )

    maintenance_settings = Settings(
        _env_file=None,
        database_url=migrated_database_url,
        operator_api_key="continuity-liveness-secret",
        release_image_digest="ef" * 32,
        health_maintenance_starts_at=first_full_day + timedelta(hours=12),
        health_maintenance_ends_at=first_full_day + timedelta(hours=13),
        health_maintenance_reason="Planned evidence host maintenance",
    )
    maintenance_app = create_app(maintenance_settings)
    maintenance_app.state.sessions = db_sessions
    async with AsyncClient(
        transport=ASGITransport(app=maintenance_app),
        base_url="http://test",
        headers=headers,
    ) as client:
        maintenance = await client.get("/api/v1/evidence-continuity")
    assert maintenance.status_code == 200
    maintenance_payload = maintenance.json()
    assert maintenance_payload["collection_window"]["maintenance_excluded_days"] == 1
    assert maintenance_payload["complete_days"] == 6
    assert maintenance_payload["eta_reason"] == "insufficient_full_days_7_required"

    latest_day = collection_end - timedelta(days=1)
    async with db_sessions.begin() as session:
        await session.execute(
            text(
                """
                DELETE FROM job_runs
                WHERE job_id = 'settlement'
                  AND scheduled_at > :started_at
                  AND scheduled_at <= :ended_at
                """
            ),
            {"started_at": latest_day, "ended_at": collection_end},
        )
    # This test mutates the reporting source synchronously and then expects an
    # immediate read. Production readers accept the bounded 30-second TTL;
    # explicitly invalidate here to test the newly committed source state.
    app.state.evidence_continuity_response_cache.invalidate()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    ) as client:
        stalled = await client.get("/api/v1/evidence-continuity")
    assert stalled.status_code == 200
    stalled_payload = stalled.json()
    assert stalled_payload["liveness"] == "stalled"
    assert stalled_payload["complete_days"] == 6
    assert stalled_payload["collection_window"]["stalled_days"] == 1
    assert stalled_payload["collection_window"]["missing_scheduler_jobs"] == [
        "settlement"
    ]
