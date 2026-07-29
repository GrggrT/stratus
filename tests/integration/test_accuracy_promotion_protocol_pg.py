from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.services.promotion_protocol import (
    PairedBrierBlock,
    PromotionDefinition,
    evaluate_fixed_promotion,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]

PROTOCOL = "fixed-range-brier-v3"
EPOCH = "fixed-range-brier-v3-20260714"
TEST_METHOD = "predictable-range-hoeffding-v1"
EXPERT = "promotion-test-expert"
LEGACY_VERSION = "promotion-test-v1"
SEALED_VERSION = f"{LEGACY_VERSION}-sealed-v2"
FINGERPRINT = "a" * 64
PIPELINE_COHORT = "promotion-test-pipeline+dual"
HORIZON = "24-48h"


async def _clear_protocol(db_sessions: SessionFactory) -> None:
    async with db_sessions.begin() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE accuracy_promotion_trials "
                "RESTART IDENTITY CASCADE"
            )
        )


async def _insert_trial(
    session: AsyncSession,
    *,
    station_id: int,
    definition: PromotionDefinition,
    family_member_index: int,
    family_size: int,
) -> tuple[int, datetime, str]:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO accuracy_promotion_trials (
                    protocol_version, registration_epoch, family_id,
                    experiment_id, candidate_id, baseline_id, pipeline_cohort,
                    registered_at, starts_at, target_blocks, allocated_alpha,
                    minimum_brier_improvement, test_method, definition_hash, expert,
                    legacy_expert_version, expert_version,
                    experiment_fingerprint, station_id, horizon_bucket, kind,
                    scope_key, family_member_index, family_size, family_alpha,
                    record_hash
                ) VALUES (
                    :protocol_version, :registration_epoch, :family_id,
                    :experiment_id, :candidate_id, :baseline_id, :pipeline_cohort,
                    :registered_at, :starts_at, :target_blocks, :allocated_alpha,
                    :minimum_brier_improvement, :test_method, :definition_hash, :expert,
                    :legacy_expert_version, :expert_version,
                    :experiment_fingerprint, :station_id, :horizon_bucket,
                    'high'::weather_kind, :scope_key, :family_member_index,
                    :family_size, 0.050000000000000000, :record_hash
                )
                RETURNING id, created_at, definition_hash
                """
            ),
            {
                "protocol_version": PROTOCOL,
                "registration_epoch": EPOCH,
                "family_id": definition.family_id,
                "experiment_id": definition.experiment_id,
                "candidate_id": definition.candidate_id,
                "baseline_id": definition.baseline_id,
                "pipeline_cohort": definition.pipeline_cohort,
                "registered_at": definition.registered_at,
                "starts_at": definition.starts_at,
                "target_blocks": definition.target_blocks,
                "allocated_alpha": Decimal(str(definition.alpha)),
                "minimum_brier_improvement": Decimal(
                    str(definition.minimum_brier_improvement)
                ),
                "test_method": TEST_METHOD,
                # Deliberately bogus: the database must overwrite this with
                # the content address derived from normalized definition data.
                "definition_hash": "f" * 64,
                "expert": EXPERT,
                "legacy_expert_version": LEGACY_VERSION,
                "expert_version": SEALED_VERSION,
                "experiment_fingerprint": FINGERPRINT,
                "station_id": station_id,
                "horizon_bucket": HORIZON,
                "scope_key": f"{station_id}:{HORIZON}:high",
                "family_member_index": family_member_index,
                "family_size": family_size,
                "record_hash": "0" * 64,
            },
        )
    ).one()
    return int(row.id), row.created_at, str(row.definition_hash)


async def _seed_vectors(
    session: AsyncSession,
    *,
    station_id: int,
    generated_at: datetime,
    blocks: int,
) -> list[tuple[int, int]]:
    prediction_vectors: list[tuple[int, int]] = []
    await session.execute(text("SET LOCAL session_replication_role = replica"))
    for block_index in range(blocks):
        event_id = f"promotion-event-{block_index:03d}"
        market_date = date(2027, 1, 1) + timedelta(days=block_index)
        prediction_ts = generated_at + timedelta(seconds=block_index)
        published_at = prediction_ts + timedelta(milliseconds=100)
        first_outcome_at = generated_at + timedelta(hours=2, days=block_index)
        market_ids: list[int] = []
        baseline_ids: list[int] = []
        prediction_ids: list[int] = []
        external_ids: list[str] = []
        for bucket_index, (outcome, candidate_probability) in enumerate(
            ((True, Decimal("0.800000")), (False, Decimal("0.200000")))
        ):
            external_id = f"promotion-market-{block_index:03d}-{bucket_index}"
            market_id = await session.scalar(
                text(
                    """
                    INSERT INTO markets (
                        polymarket_id, event_id, station_id, market_date, kind,
                        bucket_low_c, bucket_high_c, rules_raw, resolves_at,
                        status, station_verified, resolved_outcome
                    ) VALUES (
                        :external_id, :event_id, :station_id, :market_date,
                        'high'::weather_kind, :bucket_low, :bucket_high,
                        'promotion protocol integration rules', :resolves_at,
                        'resolved'::market_status, true, :outcome
                    ) RETURNING id
                    """
                ),
                {
                    "external_id": external_id,
                    "event_id": event_id,
                    "station_id": station_id,
                    "market_date": market_date,
                    "bucket_low": Decimal("20") + bucket_index,
                    "bucket_high": Decimal("21") + bucket_index,
                    "resolves_at": first_outcome_at,
                    "outcome": outcome,
                },
            )
            assert market_id is not None
            baseline_id = await session.scalar(
                text(
                    """
                    INSERT INTO model_probs (
                        market_id, ts, prob, sigma, horizon_h, components,
                        market_snapshot_id, market_anchor_protocol
                    ) VALUES (
                        :market_id, :ts, 0.500000, 1.0, 36,
                        CAST(:components AS jsonb), :snapshot_id,
                        'clob-dual-v1'
                    ) RETURNING id
                    """
                ),
                {
                    "market_id": market_id,
                    "ts": prediction_ts,
                    "components": json.dumps(
                        {
                            "pipeline": {
                                "version": "promotion-test-pipeline",
                                "cohort": PIPELINE_COHORT,
                            }
                        }
                    ),
                    # FK enforcement is intentionally disabled only while the
                    # upstream fixture is built. The promotion trigger still
                    # requires the prospective dual-snapshot marker.
                    "snapshot_id": 10_000_000 + block_index * 2 + bucket_index,
                },
            )
            assert baseline_id is not None
            prediction_id = await session.scalar(
                text(
                    """
                    INSERT INTO accuracy_predictions (
                        market_id, ts, expert, expert_version, probability,
                        mean_c, sigma_c, training_sample_n,
                        point_in_time_valid, distribution, generated_at,
                        training_cutoff, baseline_model_prob_id, input_hash,
                        experiment_fingerprint, record_hash, evidence_eligible
                    ) VALUES (
                        :market_id, :ts, :expert, :expert_version, :probability,
                        21.0, 1.0, 400, true, CAST(:distribution AS jsonb),
                        :generated_at, :training_cutoff, :baseline_id,
                        :input_hash, :fingerprint, :record_hash, true
                    ) RETURNING id
                    """
                ),
                {
                    "market_id": market_id,
                    "ts": prediction_ts,
                    "expert": EXPERT,
                    "expert_version": SEALED_VERSION,
                    "probability": candidate_probability,
                    "distribution": json.dumps(
                        {
                            "evidence_protocol": "sealed-v2",
                            "legacy_expert_version": LEGACY_VERSION,
                            "baseline_pipeline_version": PIPELINE_COHORT,
                            "horizon_bucket": HORIZON,
                            "baseline_probability": 0.5,
                            "normalized_baseline_probability": 0.5,
                        }
                    ),
                    "generated_at": published_at,
                    "training_cutoff": prediction_ts,
                    "baseline_id": baseline_id,
                    "input_hash": hashlib.sha256(event_id.encode()).hexdigest(),
                    "fingerprint": FINGERPRINT,
                    "record_hash": hashlib.sha256(
                        f"{event_id}:{bucket_index}".encode()
                    ).hexdigest(),
                },
            )
            assert prediction_id is not None
            market_ids.append(int(market_id))
            baseline_ids.append(int(baseline_id))
            prediction_ids.append(int(prediction_id))
            external_ids.append(external_id)

        outcome_vector: list[dict[str, Any]] = [
            {
                "market_id": market_id,
                "polymarket_id": external_id,
                "outcome_yes": bucket_index == 0,
            }
            for bucket_index, (market_id, external_id) in enumerate(
                zip(market_ids, external_ids, strict=True)
            )
        ]
        response = b"{}"
        resolution_event_id = await session.scalar(
            text(
                """
                INSERT INTO gamma_outcome_events (
                    event_id, source_url, source_response_body,
                    source_response_sha256, source_response_bytes,
                    first_outcome_at, fully_resolved_at, market_count,
                    market_universe_hash, rules_manifest_hash,
                    outcome_vector_hash, outcome_vector, record_hash
                ) VALUES (
                    :event_id, 'https://gamma.example.test/events', :response,
                    :response_hash, :response_bytes, :first_outcome_at,
                    :fully_resolved_at, 2, :market_universe_hash,
                    :rules_manifest_hash, :outcome_vector_hash,
                    CAST(:outcome_vector AS jsonb), :record_hash
                ) RETURNING id
                """
            ),
            {
                "event_id": event_id,
                "response": response,
                "response_hash": hashlib.sha256(response).hexdigest(),
                "response_bytes": len(response),
                "first_outcome_at": first_outcome_at,
                "fully_resolved_at": first_outcome_at + timedelta(seconds=30),
                "market_universe_hash": hashlib.sha256(
                    f"markets:{event_id}".encode()
                ).hexdigest(),
                "rules_manifest_hash": hashlib.sha256(
                    f"rules:{event_id}".encode()
                ).hexdigest(),
                "outcome_vector_hash": hashlib.sha256(
                    json.dumps(outcome_vector, sort_keys=True).encode()
                ).hexdigest(),
                "outcome_vector": json.dumps(outcome_vector),
                "record_hash": hashlib.sha256(f"gamma:{event_id}".encode()).hexdigest(),
            },
        )
        assert resolution_event_id is not None
        await session.execute(
            text(
                "UPDATE markets SET resolution_event_id = :resolution_event_id "
                "WHERE id = ANY(:market_ids)"
            ),
            {
                "resolution_event_id": resolution_event_id,
                "market_ids": market_ids,
            },
        )
        prediction_vectors.append((prediction_ids[0], prediction_ids[1]))
    return prediction_vectors


async def _insert_members(
    session: AsyncSession,
    *,
    trial_id: int,
    vectors: list[tuple[int, int]],
) -> None:
    for prediction_ids in vectors:
        await session.execute(
            text(
                """
                INSERT INTO accuracy_promotion_evidence_members (
                    trial_id, accuracy_prediction_ids
                ) VALUES (:trial_id, :prediction_ids)
                """
            ),
            {"trial_id": trial_id, "prediction_ids": list(prediction_ids)},
        )


async def test_fixed_promotion_ledger_is_fail_closed_and_append_only(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    await _clear_protocol(db_sessions)
    now = datetime.now(UTC)
    registered_at = now - timedelta(minutes=1)
    starts_at = now + timedelta(seconds=2)
    async with db_sessions.begin() as session:
        station_id = await session.scalar(
            text(
                """
                INSERT INTO stations (icao, name, city, lat, lon, tz)
                VALUES ('KPRM', 'Promotion Test', 'Test City', 40, -73, 'UTC')
                RETURNING id
                """
            )
        )
        assert station_id is not None
        definitions = [
            PromotionDefinition(
                family_id=EPOCH,
                experiment_id=f"promotion-integration-{label}",
                candidate_id=f"candidate-{label}",
                baseline_id="production-baseline",
                pipeline_cohort=PIPELINE_COHORT,
                registered_at=registered_at,
                starts_at=starts_at,
                target_blocks=60,
                alpha=0.025,
                expert=EXPERT,
                legacy_expert_version=LEGACY_VERSION,
                expert_version=SEALED_VERSION,
                experiment_fingerprint=FINGERPRINT,
                station_id=int(station_id),
                horizon_bucket=HORIZON,
                kind="high",
                scope_key=f"{station_id}:{HORIZON}:high",
                family_member_index=index,
                family_size=2,
                family_alpha=0.05,
                minimum_brier_improvement=0.001 if label == "pass" else 0.30,
            )
            for index, label in enumerate(("pass", "fail"), 1)
        ]
        pass_trial_id, pass_created_at, pass_definition_hash = await _insert_trial(
            session,
            station_id=int(station_id),
            definition=definitions[0],
            family_member_index=1,
            family_size=2,
        )
        fail_trial_id, fail_created_at, fail_definition_hash = await _insert_trial(
            session,
            station_id=int(station_id),
            definition=definitions[1],
            family_member_index=2,
            family_size=2,
        )
        assert pass_definition_hash == definitions[0].definition_hash
        assert fail_definition_hash == definitions[1].definition_hash

    generated_at = max(pass_created_at, fail_created_at, starts_at) + timedelta(seconds=1)
    async with db_sessions.begin() as session:
        vectors = await _seed_vectors(
            session,
            station_id=int(station_id),
            generated_at=generated_at,
            blocks=61,
        )

    async with db_sessions.begin() as session:
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        foreign_prediction_id = await session.scalar(
            text(
                """
                INSERT INTO accuracy_predictions (
                    market_id, ts, expert, expert_version, probability,
                    mean_c, sigma_c, training_sample_n, point_in_time_valid,
                    distribution, generated_at, training_cutoff,
                    baseline_model_prob_id, input_hash,
                    experiment_fingerprint, record_hash, evidence_eligible
                )
                SELECT market_id, ts, expert, 'foreign-v1-sealed-v2', probability,
                       mean_c, sigma_c, training_sample_n, point_in_time_valid,
                       jsonb_set(
                           distribution,
                           '{legacy_expert_version}',
                           '"foreign-v1"'::jsonb
                       ),
                       generated_at, training_cutoff, baseline_model_prob_id,
                       input_hash, :fingerprint, :record_hash, evidence_eligible
                FROM accuracy_predictions
                WHERE id = :source_id
                RETURNING id
                """
            ),
            {
                "source_id": vectors[0][0],
                "fingerprint": "f" * 64,
                "record_hash": "e" * 64,
            },
        )
        assert foreign_prediction_id is not None

    async with db_sessions() as session:
        with pytest.raises(
            DBAPIError,
            match="crosses cohort/fingerprint/scope or is incomplete",
        ):
            await _insert_members(
                session,
                trial_id=pass_trial_id,
                vectors=[(int(foreign_prediction_id), vectors[0][1])],
            )
        await session.rollback()

    async with db_sessions.begin() as session:
        await _insert_members(session, trial_id=pass_trial_id, vectors=vectors[:59])

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="requires exactly 60 blocks"):
            await session.execute(
                text(
                    """
                    INSERT INTO accuracy_promotion_looks (
                        trial_id, definition_hash, decision_hash
                    ) VALUES (:trial_id, :definition_hash, :decision_hash)
                    """
                ),
                {
                    "trial_id": pass_trial_id,
                    "definition_hash": definitions[0].definition_hash,
                    "decision_hash": "b" * 64,
                },
            )
        await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="trial_block|trial_event"):
            await _insert_members(session, trial_id=pass_trial_id, vectors=vectors[:1])
        await session.rollback()

    async with db_sessions.begin() as session:
        await _insert_members(session, trial_id=pass_trial_id, vectors=vectors[59:60])
        await _insert_members(session, trial_id=fail_trial_id, vectors=vectors[:60])

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="already has its fixed 60 blocks"):
            await _insert_members(session, trial_id=pass_trial_id, vectors=vectors[60:61])
        await session.rollback()

    async with db_sessions.begin() as session:
        fail_look = (
            await session.execute(
                text(
                    """
                    INSERT INTO accuracy_promotion_looks (
                        trial_id, definition_hash, decision_hash
                    ) VALUES (:trial_id, :definition_hash, :decision_hash)
                    RETURNING id, decision_hash
                    """
                ),
                {
                    "trial_id": fail_trial_id,
                    "definition_hash": fail_definition_hash,
                    "decision_hash": "c" * 64,
                },
            )
        ).one()
        fail_look_id = int(fail_look.id)
        fail_decision_hash = str(fail_look.decision_hash)
        assert fail_decision_hash != "c" * 64

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="passed complete fixed look"):
            await session.execute(
                text(
                    """
                    INSERT INTO accuracy_promotion_activations (
                        activation_id, trial_id, look_id, definition_hash,
                        decision_hash, family_id, experiment_id, candidate_id,
                        baseline_id, pipeline_cohort, activated_at, activated_by
                    ) VALUES (
                        'failed-activation', :trial_id, :look_id,
                        :definition_hash, :decision_hash, :family_id,
                        :experiment_id, :candidate_id, :baseline_id,
                        :pipeline_cohort, clock_timestamp(), 'integration-test'
                    )
                    """
                ),
                {
                    "trial_id": fail_trial_id,
                    "look_id": fail_look_id,
                    "definition_hash": fail_definition_hash,
                    "decision_hash": fail_decision_hash,
                    "family_id": EPOCH,
                    "experiment_id": definitions[1].experiment_id,
                    "candidate_id": definitions[1].candidate_id,
                    "baseline_id": definitions[1].baseline_id,
                    "pipeline_cohort": PIPELINE_COHORT,
                },
            )
        await session.rollback()

    # The deferred constraint must reject a terminal family if the passing
    # look is committed without its complete activation set.
    async with db_sessions() as session:
        incomplete_activation_look = (
            await session.execute(
                text(
                    """
                    INSERT INTO accuracy_promotion_looks (
                        trial_id, definition_hash, decision_hash
                    ) VALUES (:trial_id, :definition_hash, :decision_hash)
                    RETURNING id, decision_hash
                    """
                ),
                {
                    "trial_id": pass_trial_id,
                    "definition_hash": pass_definition_hash,
                    "decision_hash": "d" * 64,
                },
            )
        ).one()
        assert str(incomplete_activation_look.decision_hash) != "d" * 64
        with pytest.raises(DBAPIError, match="must activate atomically"):
            await session.commit()
        await session.rollback()

    async with db_sessions.begin() as session:
        pass_look = (
            await session.execute(
                text(
                    """
                    INSERT INTO accuracy_promotion_looks (
                        trial_id, definition_hash, decision_hash
                    ) VALUES (:trial_id, :definition_hash, :decision_hash)
                    RETURNING id, decision_hash
                    """
                ),
                {
                    "trial_id": pass_trial_id,
                    "definition_hash": pass_definition_hash,
                    "decision_hash": "d" * 64,
                },
            )
        ).one()
        pass_look_id = int(pass_look.id)
        pass_decision_hash = str(pass_look.decision_hash)
        assert pass_decision_hash != "d" * 64
        activation_id = await session.scalar(
            text(
                """
                INSERT INTO accuracy_promotion_activations (
                    activation_id, trial_id, look_id, definition_hash,
                    decision_hash, family_id, experiment_id, candidate_id,
                    baseline_id, pipeline_cohort, activated_at, activated_by
                ) VALUES (
                    'passed-activation', :trial_id, :look_id,
                    :definition_hash, :decision_hash, :family_id,
                    :experiment_id, :candidate_id, :baseline_id,
                    :pipeline_cohort, clock_timestamp(), 'integration-test'
                )
                RETURNING id
                """
            ),
            {
                "trial_id": pass_trial_id,
                "look_id": pass_look_id,
                "definition_hash": pass_definition_hash,
                "decision_hash": pass_decision_hash,
                "family_id": EPOCH,
                "experiment_id": definitions[0].experiment_id,
                "candidate_id": definitions[0].candidate_id,
                "baseline_id": definitions[0].baseline_id,
                "pipeline_cohort": PIPELINE_COHORT,
            },
        )
        assert activation_id is not None

    async with db_sessions() as session:
        summary = (
            await session.execute(
                text(
                    """
                        SELECT trial.status::text AS trial_status,
                               look.status::text AS look_status,
                               look.independent_block_count,
                               look.mean_delta_brier,
                               look.wins,
                               look.nonwins,
                               look.sum_predictable_range_squared,
                               look.confidence_radius,
                               look.mean_improvement_lcb,
                               look.one_sided_p_bound,
                               look.decision_hash,
                               activation.record_hash
                    FROM accuracy_promotion_trials AS trial
                    JOIN accuracy_promotion_looks AS look ON look.trial_id = trial.id
                    LEFT JOIN accuracy_promotion_activations AS activation
                      ON activation.trial_id = trial.id
                    WHERE trial.id = :trial_id
                    """
                ),
                {"trial_id": pass_trial_id},
            )
        ).one()
    assert summary.trial_status == "activated"
    assert summary.look_status == "passed"
    assert summary.independent_block_count == 60
    assert summary.mean_delta_brier < Decimal("-0.20")
    assert summary.wins == 60
    assert summary.nonwins == 0
    assert summary.sum_predictable_range_squared == Decimal("21.600000000000000000000000000000")
    assert summary.confidence_radius > 0
    assert summary.mean_improvement_lcb > definitions[0].minimum_brier_improvement
    assert summary.one_sided_p_bound <= Decimal(str(definitions[0].alpha))
    assert len(summary.record_hash) == 64

    async with db_sessions() as session:
        member_rows = (
            await session.execute(
                text(
                    """
                    SELECT independent_block_key, resolved_cutoff,
                           baseline_brier, candidate_brier,
                           predictable_improvement_lower,
                           predictable_improvement_upper, evidence_hash
                    FROM accuracy_promotion_evidence_members
                    WHERE trial_id = :trial_id
                    ORDER BY resolved_cutoff, independent_block_key
                    """
                ),
                {"trial_id": pass_trial_id},
            )
        ).all()
    expected_decision = evaluate_fixed_promotion(
        definitions[0],
        [
            PairedBrierBlock(
                block_key=row.independent_block_key,
                resolved_cutoff=row.resolved_cutoff,
                baseline_brier=row.baseline_brier,
                candidate_brier=row.candidate_brier,
                predictable_improvement_lower=row.predictable_improvement_lower,
                predictable_improvement_upper=row.predictable_improvement_upper,
                evidence_hash=row.evidence_hash,
            )
            for row in member_rows
        ],
    )
    assert summary.decision_hash == expected_decision.decision_hash
    assert summary.confidence_radius == expected_decision.confidence_radius
    assert summary.mean_improvement_lcb == expected_decision.mean_improvement_lcb
    assert summary.one_sided_p_bound == expected_decision.one_sided_p_bound

    mutation_statements = (
        "UPDATE accuracy_promotion_evidence_members SET delta_brier = 0 "
        "WHERE trial_id = :trial_id",
        "DELETE FROM accuracy_promotion_looks WHERE trial_id = :trial_id",
        "UPDATE accuracy_promotion_activations SET activated_by = 'other' "
        "WHERE trial_id = :trial_id",
    )
    for statement in mutation_statements:
        async with db_sessions() as session:
            with pytest.raises(DBAPIError, match="append-only accuracy promotion ledger"):
                await session.execute(text(statement), {"trial_id": pass_trial_id})
            await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="trial definition is immutable"):
            await session.execute(
                text(
                    "UPDATE accuracy_promotion_trials SET candidate_id = 'changed' "
                    "WHERE id = :trial_id"
                ),
                {"trial_id": pass_trial_id},
            )
        await session.rollback()

    config = Config("alembic.ini")
    with pytest.raises(DBAPIError, match="cannot downgrade accuracy promotion protocol"):
        await asyncio.to_thread(command.downgrade, config, "20260714_0032")

    await _clear_protocol(db_sessions)
