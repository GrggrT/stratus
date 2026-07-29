from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from datetime import datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.db.models import ProbabilityPipelineReleaseEpoch
from stratus.repositories.evidence_continuity import (
    EvidenceLanePlan,
    ProbabilityPipelineReleaseIntegrityError,
    ProbabilityPipelineReleasePlan,
    append_probability_pipeline_release_epoch,
    build_probability_pipeline_release_plan,
    current_evidence_continuity_lanes,
    current_probability_pipeline_release_epoch,
    list_probability_pipeline_release_epochs,
    persist_probability_pipeline_release_plan,
)
from stratus.repositories.evidence_continuity_reporting import _probability_counts
from stratus.services.evidence_release_manifest import ALEMBIC_HEAD
from stratus.services.execution_cost import source_payload_hash

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
PIPELINE_VERSION = "stratus-accuracy-v4-20260715"
FINGERPRINT_A = "1" * 16
FINGERPRINT_B = "2" * 16
ACTIVATION_A = "a" * 64
ACTIVATION_B = "b" * 64
IMAGE_A = "c" * 64
IMAGE_B = "d" * 64
REVIEWER = "pytest-release-reviewer"
REASON_A = "Approve the initial immutable confirmatory evidence epoch."
REASON_B = "Approve the reviewed material pipeline successor epoch."
LANE_NAMES = (
    "predictive_validation",
    "selection_policy",
    "execution_evidence",
    "operational_legacy",
    "station_lifecycle",
    "official_resolution_truth",
)


def _release_manifest(*, variant: str = "baseline") -> dict[str, object]:
    return {
        "protocol": "evidence-release-test-v1",
        "revision": "0040",
        "variant": variant,
    }


def _lane_plans(
    *,
    default_action: str,
    variants: Mapping[str, str] | None = None,
    actions: Mapping[str, str] | None = None,
) -> tuple[EvidenceLanePlan, ...]:
    variants = variants or {}
    actions = actions or {}
    plans: list[EvidenceLanePlan] = []
    for lane_name in LANE_NAMES:
        manifest = {
            "protocol": "evidence-lane-test-v1",
            "lane": lane_name,
            "identity": variants.get(lane_name, "baseline"),
        }
        plans.append(
            EvidenceLanePlan(
                lane_name=lane_name,
                action=actions.get(lane_name, default_action),
                identity_hash=source_payload_hash(manifest),
                manifest=manifest,
                rationale=f"Preserve reviewed integration evidence for {lane_name}.",
            )
        )
    return tuple(plans)


def _plan(
    *,
    previous_epoch_id: int | None,
    fingerprint: str = FINGERPRINT_A,
    activation_hash: str = ACTIVATION_A,
    image_digest: str = IMAGE_A,
    reason: str = REASON_A,
    release_variant: str = "baseline",
    lane_plans: tuple[EvidenceLanePlan, ...] | None = None,
) -> ProbabilityPipelineReleasePlan:
    return build_probability_pipeline_release_plan(
        pipeline_version=PIPELINE_VERSION,
        material_fingerprint=fingerprint,
        activation_manifest_hash=activation_hash,
        image_digest=image_digest,
        previous_epoch_id=previous_epoch_id,
        release_manifest=_release_manifest(variant=release_variant),
        lane_plans=lane_plans or _lane_plans(default_action="fork"),
        reviewed_by=REVIEWER,
        approval_reason=reason,
    )


async def _activate(
    session: AsyncSession,
    plan: ProbabilityPipelineReleasePlan,
) -> ProbabilityPipelineReleaseEpoch:
    await persist_probability_pipeline_release_plan(session, plan)
    return await append_probability_pipeline_release_epoch(
        session,
        plan,
        expected_plan_hash=plan.plan_hash,
    )


def _epoch_insert_values(
    plan: ProbabilityPipelineReleasePlan,
    *,
    plan_id: int,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "pipeline_version": plan.pipeline_version,
        "material_fingerprint": plan.material_fingerprint,
        "activation_manifest_hash": plan.activation_manifest_hash,
        "cohort": plan.cohort,
        "image_digest": plan.image_digest,
        "plan_id": plan_id,
        "previous_epoch_id": plan.previous_epoch_id,
        "approval_reason": plan.approval_reason,
        "plan_hash": plan.plan_hash,
        "record_hash": "0" * 64,
    }
    if overrides:
        values.update(overrides)
    return values


async def _direct_epoch_insert(
    session: AsyncSession,
    values: Mapping[str, object],
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO probability_pipeline_release_epochs (
                pipeline_version, material_fingerprint, activation_manifest_hash,
                cohort, image_digest, plan_id, previous_epoch_id, approval_reason,
                plan_hash, record_hash
            ) VALUES (
                :pipeline_version, :material_fingerprint, :activation_manifest_hash,
                :cohort, :image_digest, :plan_id, :previous_epoch_id, :approval_reason,
                :plan_hash, :record_hash
            )
            """
        ),
        values,
    )


async def test_migration_is_zero_backfill_and_has_one_runtime_row(
    db_sessions: SessionFactory,
) -> None:
    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "20260715_0039")
    await asyncio.to_thread(command.upgrade, config, "head")

    async with db_sessions() as session:
        assert await session.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == ALEMBIC_HEAD
        counts = {
            table: await session.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in (
                "evidence_continuity_runtime",
                "probability_pipeline_release_plans",
                "probability_pipeline_release_epochs",
                "evidence_continuity_lanes",
            )
        }
        triggers = set(
            (
                await session.execute(
                    text(
                        """
                        SELECT tgname
                        FROM pg_trigger
                        WHERE NOT tgisinternal
                          AND tgrelid IN (
                              'probability_pipeline_release_plans'::regclass,
                              'probability_pipeline_release_epochs'::regclass,
                              'evidence_continuity_lanes'::regclass,
                              'execution_order_intents'::regclass
                          )
                        """
                    )
                )
            ).scalars()
        )
    assert counts == {
        "evidence_continuity_runtime": 1,
        "probability_pipeline_release_plans": 0,
        "probability_pipeline_release_epochs": 0,
        "evidence_continuity_lanes": 0,
    }
    assert "trg_probability_pipeline_release_plans_validate_insert" in triggers
    assert "trg_probability_pipeline_release_epochs_validate_insert" in triggers
    assert "trg_evidence_continuity_lanes_validate_insert" in triggers
    assert "trg_evidence_release_epoch_complete_lanes" in triggers
    assert "trg_0040_execution_intent_release_epoch" in triggers


async def test_reviewed_plan_is_idempotent_and_first_epoch_has_six_lanes(
    db_sessions: SessionFactory,
) -> None:
    plan = _plan(previous_epoch_id=None)
    async with db_sessions.begin() as session:
        database_plan_hash = await session.scalar(
            text(
                """
                SELECT evidence_release_plan_hash(
                    :pipeline_version, :material_fingerprint,
                    :activation_manifest_hash, :cohort, :image_digest,
                    :previous_epoch_id, :release_manifest_hash,
                    :lane_manifest_hash, :reviewed_by, :approval_reason
                )
                """
            ),
            {
                "pipeline_version": plan.pipeline_version,
                "material_fingerprint": plan.material_fingerprint,
                "activation_manifest_hash": plan.activation_manifest_hash,
                "cohort": plan.cohort,
                "image_digest": plan.image_digest,
                "previous_epoch_id": plan.previous_epoch_id,
                "release_manifest_hash": plan.release_manifest_hash,
                "lane_manifest_hash": plan.lane_manifest_hash,
                "reviewed_by": plan.reviewed_by,
                "approval_reason": plan.approval_reason,
            },
        )
        assert database_plan_hash == plan.plan_hash
        persisted = await persist_probability_pipeline_release_plan(session, plan)
        repeated = await persist_probability_pipeline_release_plan(session, plan)
        assert repeated.id == persisted.id
        assert persisted.record_hash != "0" * 64

        before = await session.scalar(text("SELECT clock_timestamp()"))
        stored = await append_probability_pipeline_release_epoch(
            session,
            plan,
            expected_plan_hash=plan.plan_hash,
        )
        after = await session.scalar(text("SELECT clock_timestamp()"))
        lanes = await current_evidence_continuity_lanes(session)

        assert before is not None and after is not None
        assert before <= stored.opened_at <= after
        assert stored.plan_id == persisted.id
        assert stored.record_hash != "0" * 64
        assert len(lanes) == 6
        assert {lane.lane_name for lane in lanes} == set(LANE_NAMES)
        assert {lane.action for lane in lanes} == {"fork"}
        assert all(lane.parent_lane_id is None for lane in lanes)
        assert all(lane.boundary_at == stored.opened_at for lane in lanes)
        assert all(lane.record_hash != "0" * 64 for lane in lanes)
        assert await current_probability_pipeline_release_epoch(session) is stored
        assert await list_probability_pipeline_release_epochs(session) == (stored,)


async def test_probability_counts_use_latest_eligible_row_per_active_market(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        epoch = await _activate(session, _plan(previous_epoch_id=None))
        predictive_lane = next(
            lane
            for lane in await current_evidence_continuity_lanes(session)
            if lane.lane_name == "predictive_validation"
        )
        boundary = predictive_lane.boundary_at
        assert boundary == epoch.opened_at

        await session.execute(
            text(
                """
                INSERT INTO markets (
                    polymarket_id, event_id, market_date, kind, rules_raw,
                    status, resolves_at
                ) VALUES
                    ('report-current-a1', 'event-a', current_date, 'high', 'rules',
                     'active', now() + interval '1 day'),
                    ('report-current-a2', 'event-a', current_date, 'high', 'rules',
                     'active', now() + interval '1 day'),
                    ('report-current-c', 'event-c', current_date, 'high', 'rules',
                     'active', now() + interval '1 day'),
                    ('report-prior-b', 'event-b', current_date, 'high', 'rules',
                     'active', now() + interval '1 day'),
                    ('report-inactive', 'event-inactive', current_date, 'high', 'rules',
                     'blocked', now() + interval '1 day')
                """
            )
        )
        market_rows = await session.execute(
            text(
                "SELECT polymarket_id, id FROM markets "
                "WHERE polymarket_id LIKE 'report-%'"
            )
        )
        market_ids: dict[str, int] = {
            str(row["polymarket_id"]): int(row["id"])
            for row in market_rows.mappings()
        }

        async def add_probability(
            market_key: str,
            *,
            ts: datetime,
            cohort: str,
            materialized: bool,
            coherent: bool,
        ) -> None:
            await session.execute(
                text(
                    """
                    INSERT INTO model_probs (
                        market_id, ts, prob, sigma, horizon_h, components
                    ) VALUES (
                        :market_id, :ts, 0.6, 1.0, 12.0,
                        jsonb_build_object(
                            'pipeline', jsonb_build_object(
                                'cohort', CAST(:cohort AS text)
                            ),
                            'event_coherence', jsonb_build_object(
                                'materialized', CAST(:materialized AS boolean),
                                'passed', CAST(:coherent AS boolean)
                            )
                        )
                    )
                    """
                ),
                {
                    "market_id": market_ids[market_key],
                    "ts": ts,
                    "cohort": cohort,
                    "materialized": materialized,
                    "coherent": coherent,
                },
            )

        prior_cohort = "prior-audit-cohort"
        await add_probability(
            "report-current-a1",
            ts=boundary - timedelta(minutes=1),
            cohort=epoch.cohort,
            materialized=False,
            coherent=False,
        )
        await add_probability(
            "report-current-a1",
            ts=boundary + timedelta(minutes=1),
            cohort=epoch.cohort,
            materialized=True,
            coherent=True,
        )
        await add_probability(
            "report-current-a2",
            ts=boundary + timedelta(minutes=2),
            cohort=epoch.cohort,
            materialized=True,
            coherent=False,
        )
        await add_probability(
            "report-current-c",
            ts=boundary + timedelta(minutes=3),
            cohort=epoch.cohort,
            materialized=False,
            coherent=False,
        )
        await add_probability(
            "report-prior-b",
            ts=boundary - timedelta(minutes=2),
            cohort=prior_cohort,
            materialized=True,
            coherent=False,
        )
        # This is the physical latest row for the market, but it is an active-
        # cohort row before the reviewed boundary. Filtering must happen inside
        # the lateral lookup so the eligible prior-cohort row remains visible.
        await add_probability(
            "report-prior-b",
            ts=boundary - timedelta(minutes=1),
            cohort=epoch.cohort,
            materialized=True,
            coherent=True,
        )
        await add_probability(
            "report-inactive",
            ts=boundary + timedelta(minutes=4),
            cohort=epoch.cohort,
            materialized=True,
            coherent=True,
        )

        counts = await _probability_counts(
            session,
            active_cohort=epoch.cohort,
            active_opened_at=boundary,
        )

    assert counts == {
        epoch.cohort: (3, 1, 1),
        prior_cohort: (1, 1, 0),
    }


async def test_plan_epoch_and_lane_records_are_append_only(
    db_sessions: SessionFactory,
) -> None:
    plan = _plan(previous_epoch_id=None)
    async with db_sessions.begin() as session:
        persisted = await persist_probability_pipeline_release_plan(session, plan)
        epoch = await append_probability_pipeline_release_epoch(
            session,
            plan,
            expected_plan_hash=plan.plan_hash,
        )
        lane = (await current_evidence_continuity_lanes(session))[0]

        for statement, params in (
            (
                "UPDATE probability_pipeline_release_plans "
                "SET reviewed_by = 'changed' WHERE id = :id",
                {"id": persisted.id},
            ),
            (
                "DELETE FROM probability_pipeline_release_epochs WHERE id = :id",
                {"id": epoch.id},
            ),
            (
                "UPDATE evidence_continuity_lanes "
                "SET rationale = 'changed rationale' WHERE id = :id",
                {"id": lane.id},
            ),
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                async with session.begin_nested():
                    await session.execute(text(statement), params)


async def test_same_cohort_new_image_continues_all_lane_boundaries(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        first = await _activate(session, _plan(previous_epoch_id=None))
        first_lanes = {
            lane.lane_name: lane for lane in await current_evidence_continuity_lanes(session)
        }
        successor_plan = _plan(
            previous_epoch_id=first.id,
            image_digest=IMAGE_B,
            reason="Approve a rebuilt image while continuing compatible evidence lanes.",
            lane_plans=_lane_plans(default_action="continue"),
        )
        successor = await _activate(session, successor_plan)
        successor_lanes = {
            lane.lane_name: lane for lane in await current_evidence_continuity_lanes(session)
        }

        assert successor.cohort == first.cohort
        assert successor.image_digest == IMAGE_B
        assert all(lane.action == "continue" for lane in successor_lanes.values())
        assert all(
            successor_lanes[name].parent_lane_id == first_lanes[name].id
            and successor_lanes[name].boundary_at == first_lanes[name].boundary_at
            for name in LANE_NAMES
        )


async def test_same_image_material_lane_fork_resets_only_changed_lane(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        first = await _activate(session, _plan(previous_epoch_id=None))
        first_lanes = {
            lane.lane_name: lane for lane in await current_evidence_continuity_lanes(session)
        }
        changed_lane = "selection_policy"
        successor_plan = _plan(
            previous_epoch_id=first.id,
            image_digest=IMAGE_A,
            reason="Fork only the reviewed material selection policy evidence lane.",
            lane_plans=_lane_plans(
                default_action="continue",
                variants={changed_lane: "material-v2"},
                actions={changed_lane: "fork"},
            ),
        )
        successor = await _activate(session, successor_plan)
        successor_lanes = {
            lane.lane_name: lane for lane in await current_evidence_continuity_lanes(session)
        }

        assert successor.image_digest == first.image_digest
        assert successor.cohort == first.cohort
        assert successor_lanes[changed_lane].action == "fork"
        assert successor_lanes[changed_lane].boundary_at == successor.opened_at
        for lane_name in set(LANE_NAMES) - {changed_lane}:
            assert successor_lanes[lane_name].action == "continue"
            assert (
                successor_lanes[lane_name].boundary_at
                == first_lanes[lane_name].boundary_at
            )


async def test_chain_rejects_unpersisted_stale_and_exact_noop_plans(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        first_plan = _plan(previous_epoch_id=None)
        first = await _activate(session, first_plan)
        successor_plan = _plan(
            previous_epoch_id=first.id,
            image_digest=IMAGE_B,
            reason=REASON_B,
            lane_plans=_lane_plans(default_action="continue"),
        )
        with pytest.raises(
            ProbabilityPipelineReleaseIntegrityError,
            match="stale or does not match",
        ):
            await append_probability_pipeline_release_epoch(
                session,
                successor_plan,
                expected_plan_hash="e" * 64,
            )
        with pytest.raises(
            ProbabilityPipelineReleaseIntegrityError,
            match="must be persisted",
        ):
            await append_probability_pipeline_release_epoch(
                session,
                successor_plan,
                expected_plan_hash=successor_plan.plan_hash,
            )

        duplicate_plan = _plan(
            previous_epoch_id=first.id,
            reason="Reject this exact no-op release manifest successor plan.",
        )
        persisted_duplicate = await persist_probability_pipeline_release_plan(
            session, duplicate_plan
        )
        with pytest.raises(
            ProbabilityPipelineReleaseIntegrityError,
            match="exact no-op manifest",
        ):
            await append_probability_pipeline_release_epoch(
                session,
                duplicate_plan,
                expected_plan_hash=duplicate_plan.plan_hash,
            )
        with pytest.raises(DBAPIError, match="exact no-op manifest"):
            async with session.begin_nested():
                await _direct_epoch_insert(
                    session,
                    _epoch_insert_values(
                        duplicate_plan,
                        plan_id=persisted_duplicate.id,
                    ),
                )


async def test_freeze_lane_blocks_new_execution_intents(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        first = await _activate(session, _plan(previous_epoch_id=None))
        frozen_plan = _plan(
            previous_epoch_id=first.id,
            release_variant="execution-freeze",
            reason="Freeze execution evidence while its material identity is reviewed.",
            lane_plans=_lane_plans(
                default_action="continue",
                variants={"execution_evidence": "frozen-v2"},
                actions={"execution_evidence": "freeze"},
            ),
        )
        frozen = await _activate(session, frozen_plan)
        await session.execute(
            text("SELECT set_config('stratus.release_image_digest', :digest, true)"),
            {"digest": frozen.image_digest},
        )
        await session.execute(
            text(
                "CREATE TEMP TABLE execution_epoch_probe ("
                "pipeline_cohort text NOT NULL, signal_id bigint, model_prob_id bigint)"
            )
        )
        await session.execute(
            text(
                """
                CREATE TRIGGER trg_execution_epoch_probe
                BEFORE INSERT ON execution_epoch_probe FOR EACH ROW
                EXECUTE FUNCTION guard_execution_intent_release_epoch()
                """
            )
        )
        with pytest.raises(DBAPIError, match="lane set is incomplete or frozen"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO execution_epoch_probe (pipeline_cohort) "
                        "VALUES (:cohort)"
                    ),
                    {"cohort": frozen.cohort},
                )


async def test_changed_lane_cannot_claim_continuation(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        first = await _activate(session, _plan(previous_epoch_id=None))
        invalid = _plan(
            previous_epoch_id=first.id,
            release_variant="invalid-continuation",
            reason="Attempt an invalid changed identity continuation for testing.",
            lane_plans=_lane_plans(
                default_action="continue",
                variants={"predictive_validation": "changed-v2"},
            ),
        )
        await persist_probability_pipeline_release_plan(session, invalid)
        with pytest.raises(
            ProbabilityPipelineReleaseIntegrityError,
            match="database rejected",
        ):
            await append_probability_pipeline_release_epoch(
                session,
                invalid,
                expected_plan_hash=invalid.plan_hash,
            )


async def test_execution_intent_guard_requires_active_image_and_post_boundary_data(
    db_sessions: SessionFactory,
) -> None:
    first_plan = _plan(previous_epoch_id=None)
    async with db_sessions.begin() as session:
        await session.execute(
            text(
                "CREATE TEMP TABLE execution_epoch_probe ("
                "pipeline_cohort text NOT NULL, signal_id bigint, model_prob_id bigint)"
            )
        )
        await session.execute(
            text(
                """
                CREATE TRIGGER trg_execution_epoch_probe
                BEFORE INSERT ON execution_epoch_probe FOR EACH ROW
                EXECUTE FUNCTION guard_execution_intent_release_epoch()
                """
            )
        )
        with pytest.raises(DBAPIError, match="is not activated"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO execution_epoch_probe (pipeline_cohort) "
                        "VALUES (:cohort)"
                    ),
                    {"cohort": first_plan.cohort},
                )

        first = await _activate(session, first_plan)
        with pytest.raises(DBAPIError, match="runtime image"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO execution_epoch_probe (pipeline_cohort) "
                        "VALUES (:cohort)"
                    ),
                    {"cohort": first.cohort},
                )
        await session.execute(
            text("SELECT set_config('stratus.release_image_digest', :digest, true)"),
            {"digest": IMAGE_A},
        )
        with pytest.raises(DBAPIError, match="decision evidence predates release cohort"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO execution_epoch_probe (pipeline_cohort) "
                        "VALUES (:cohort)"
                    ),
                    {"cohort": first.cohort},
                )


async def test_downgrade_refuses_after_reviewed_plan_exists(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        await persist_probability_pipeline_release_plan(
            session,
            _plan(previous_epoch_id=None),
        )

    with pytest.raises(Exception, match="refusing 0040 downgrade"):
        await asyncio.to_thread(
            command.downgrade,
            Config("alembic.ini"),
            "20260715_0039",
        )
