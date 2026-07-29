from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ClobBookBatch,
    ClobMarketSnapshot,
    ExecutionCostRuntime,
    ExecutionFillLeg,
    ExecutionMode,
    ExecutionOrderIntent,
    ExecutionReconciliationObservation,
    TradeSide,
)
from stratus.repositories.clob_snapshots import clob_batch_record_is_valid
from stratus.services.execution_cost import (
    ExecutionCostIntegrityError,
    execution_record_hash,
    source_payload_hash,
)

_TERMINAL_STATUSES = (
    "filled",
    "partially_filled",
    "canceled",
    "unfilled_maker",
    "submit_failed",
    "rejected",
    "expired",
)
_SCHEDULER_SLACK = timedelta(minutes=2)
_SOURCE_REATTEST_LEG_LIMIT = 128


@dataclass(frozen=True, slots=True)
class ExecutionCostModeCoverage:
    mode: ExecutionMode
    attempts: int
    sealed: int
    eligible: int
    filled_seals: int
    mark_5m_complete: int
    mark_30m_complete: int


@dataclass(frozen=True, slots=True)
class ExecutionCostHealthSummary:
    opened_at: datetime
    post_boundary_orders: int
    orders_without_intent: int
    terminal_without_obligation: int
    overdue_obligations: int
    overdue_marks: int
    invalid_eligible_source_legs: int
    reconciliation_mismatches: int
    duplicate_identities: int
    live_unverified_legs: int
    live_unverified_reasons: tuple[tuple[str, int], ...]
    coverage: tuple[ExecutionCostModeCoverage, ...]


def _observation_record_is_valid(row: ExecutionReconciliationObservation) -> bool:
    try:
        return (
            source_payload_hash(row.normalized_payload) == row.payload_sha256
            and execution_record_hash(
                "observation",
                {
                    "definition_version": row.definition_version,
                    "first_seen_at": row.first_seen_at,
                    "payload_sha256": row.payload_sha256,
                    "observation_kind": row.observation_kind,
                    "order_id": row.order_id,
                    "parser_version": row.parser_version,
                    "request_started_at": row.request_started_at,
                    "response_received_at": row.response_received_at,
                    "sdk_method": row.sdk_method,
                },
                definition_hash=row.definition_hash,
            )
            == row.record_hash
        )
    except (ExecutionCostIntegrityError, TypeError, ValueError):
        return False


def _fill_record_is_valid(row: ExecutionFillLeg) -> bool:
    try:
        return (
            execution_record_hash(
                "fill_leg",
                {
                    "definition_version": row.definition_version,
                    "eligibility_reason": row.eligibility_reason,
                    "eligible": row.eligible,
                    "external_leg_key": row.external_leg_key,
                    "external_trade_id": row.external_trade_id,
                    "fee_semantics": row.fee_semantics,
                    "fee_usd": row.fee_usd,
                    "fill_best_ask": row.fill_best_ask,
                    "fill_best_bid": row.fill_best_bid,
                    "fill_clob_snapshot_hash": row.fill_clob_snapshot_hash,
                    "fill_clob_snapshot_id": row.fill_clob_snapshot_id,
                    "fill_effective_at": row.fill_effective_at,
                    "fill_midpoint": row.fill_midpoint,
                    "first_seen_at": row.first_seen_at,
                    "intent_id": row.intent_id,
                    "leg_sequence": row.leg_sequence,
                    "liquidity_role": row.liquidity_role,
                    "market_id": row.market_id,
                    "mode": row.mode.value,
                    "notional_usd": row.notional_usd,
                    "order_id": row.order_id,
                    "price": row.price,
                    "reconciliation_observation_id": row.reconciliation_observation_id,
                    "shares": row.shares,
                    "side": row.side.value,
                    "signal_id": row.signal_id,
                    "source_hash": row.source_hash,
                    "source_kind": row.source_kind,
                    "timestamp_semantics": row.timestamp_semantics,
                    "token_id": row.token_id,
                    "venue_filled_at": row.venue_filled_at,
                },
                definition_hash=row.definition_hash,
            )
            == row.record_hash
        )
    except (ExecutionCostIntegrityError, TypeError, ValueError):
        return False


def _eligible_leg_projection_is_valid(
    leg: ExecutionFillLeg,
    intent: ExecutionOrderIntent,
    observation: ExecutionReconciliationObservation | None,
    snapshot: ClobMarketSnapshot | None,
    batch: ClobBookBatch | None,
    runtime: ExecutionCostRuntime,
) -> bool:
    if (
        not leg.eligible
        or not _fill_record_is_valid(leg)
        or leg.order_id != intent.order_id
        or leg.signal_id != intent.signal_id
        or leg.market_id != intent.market_id
        or leg.mode != intent.mode
        or leg.side != intent.side
        or leg.token_id != intent.token_id
        or leg.first_seen_at < intent.intent_at
        or leg.definition_version != runtime.definition_version
        or leg.definition_hash != runtime.definition_hash
        or snapshot is None
        or batch is None
        or snapshot.batch_id != batch.id
        or snapshot.market_id != leg.market_id
        or snapshot.record_hash != leg.fill_clob_snapshot_hash
        or not snapshot.complete
        or not snapshot.eligible
    ):
        return False
    if leg.side == TradeSide.BUY_YES:
        projection = (
            snapshot.yes_token_id,
            snapshot.yes_best_bid,
            snapshot.yes_best_ask,
            snapshot.yes_midpoint,
        )
    else:
        projection = (
            snapshot.no_token_id,
            snapshot.no_best_bid,
            snapshot.no_best_ask,
            snapshot.no_midpoint,
        )
    if projection != (
        leg.token_id,
        leg.fill_best_bid,
        leg.fill_best_ask,
        leg.fill_midpoint,
    ):
        return False
    if leg.source_kind == "paper_simulation":
        return (
            leg.mode == ExecutionMode.PAPER
            and observation is None
            and leg.source_hash == batch.record_hash
            and leg.timestamp_semantics == "paper_clob_first_seen"
            and leg.venue_filled_at is None
            and leg.fill_effective_at == batch.first_seen_at
            and batch.first_seen_at >= intent.intent_at
        )
    if leg.source_kind != "live_sdk" or observation is None:
        return False
    return (
        leg.mode == ExecutionMode.LIVE
        and observation.order_id == leg.order_id
        and _observation_record_is_valid(observation)
        and leg.source_hash == observation.record_hash
        and leg.external_trade_id is not None
        and bool(leg.external_trade_id.strip())
        and leg.liquidity_role in {"maker", "taker"}
        and leg.fee_usd is not None
        and leg.fee_semantics == "venue_reported"
        and leg.timestamp_semantics == "verified_venue_fill"
        and leg.venue_filled_at is not None
        and leg.fill_effective_at == leg.venue_filled_at
        and batch.first_seen_at <= leg.venue_filled_at
        and batch.first_seen_at
        >= leg.venue_filled_at - timedelta(seconds=runtime.live_fill_snapshot_max_age_seconds)
    )


async def _invalid_eligible_source_leg_count(
    session: AsyncSession,
    runtime: ExecutionCostRuntime,
) -> int:
    # Reconcile cheap relational/source-hash projections over the full cohort.
    # The rows and cited CLOB evidence are append-only, so expensive raw-byte
    # replay is bounded to the newest legs on each cycle without weakening a
    # defect once it has been observed.
    invalid_leg_ids = set(
        (
            await session.scalars(
                text(
                    """
                    SELECT leg.id
                    FROM execution_fill_legs leg
                    JOIN execution_order_intents intent ON intent.id = leg.intent_id
                    LEFT JOIN execution_reconciliation_observations observation
                      ON observation.id = leg.reconciliation_observation_id
                    LEFT JOIN clob_market_snapshots snapshot
                      ON snapshot.id = leg.fill_clob_snapshot_id
                    LEFT JOIN clob_book_batches batch ON batch.id = snapshot.batch_id
                    WHERE leg.eligible AND (
                        leg.order_id IS DISTINCT FROM intent.order_id
                        OR leg.signal_id IS DISTINCT FROM intent.signal_id
                        OR leg.market_id IS DISTINCT FROM intent.market_id
                        OR leg.mode IS DISTINCT FROM intent.mode
                        OR leg.side IS DISTINCT FROM intent.side
                        OR leg.token_id IS DISTINCT FROM intent.token_id
                        OR leg.first_seen_at < intent.intent_at
                        OR leg.definition_version IS DISTINCT FROM :definition_version
                        OR leg.definition_hash IS DISTINCT FROM :definition_hash
                        OR snapshot.id IS NULL
                        OR snapshot.market_id IS DISTINCT FROM leg.market_id
                        OR snapshot.record_hash IS DISTINCT FROM leg.fill_clob_snapshot_hash
                        OR NOT coalesce(snapshot.complete, false)
                        OR NOT coalesce(snapshot.eligible, false)
                        OR leg.token_id IS DISTINCT FROM CASE
                            WHEN leg.side = 'buy_yes'::trade_side
                            THEN snapshot.yes_token_id ELSE snapshot.no_token_id END
                        OR leg.fill_best_bid IS DISTINCT FROM CASE
                            WHEN leg.side = 'buy_yes'::trade_side
                            THEN snapshot.yes_best_bid ELSE snapshot.no_best_bid END
                        OR leg.fill_best_ask IS DISTINCT FROM CASE
                            WHEN leg.side = 'buy_yes'::trade_side
                            THEN snapshot.yes_best_ask ELSE snapshot.no_best_ask END
                        OR leg.fill_midpoint IS DISTINCT FROM CASE
                            WHEN leg.side = 'buy_yes'::trade_side
                            THEN snapshot.yes_midpoint ELSE snapshot.no_midpoint END
                        OR leg.source_kind NOT IN ('paper_simulation', 'live_sdk')
                        OR (leg.source_kind = 'paper_simulation' AND (
                            leg.mode <> 'paper'::execution_mode
                            OR observation.id IS NOT NULL
                            OR leg.source_hash IS DISTINCT FROM batch.record_hash
                            OR leg.timestamp_semantics <> 'paper_clob_first_seen'
                            OR leg.venue_filled_at IS NOT NULL
                            OR leg.fill_effective_at IS DISTINCT FROM batch.first_seen_at
                            OR batch.first_seen_at < intent.intent_at))
                        OR (leg.source_kind = 'live_sdk' AND (
                            leg.mode <> 'live'::execution_mode
                            OR observation.id IS NULL
                            OR observation.order_id IS DISTINCT FROM leg.order_id
                            OR leg.source_hash IS DISTINCT FROM observation.record_hash
                            OR nullif(btrim(leg.external_trade_id), '') IS NULL
                            OR leg.liquidity_role NOT IN ('maker', 'taker')
                            OR leg.fee_usd IS NULL
                            OR leg.fee_semantics <> 'venue_reported'
                            OR leg.timestamp_semantics <> 'verified_venue_fill'
                            OR leg.venue_filled_at IS NULL
                            OR leg.fill_effective_at IS DISTINCT FROM leg.venue_filled_at
                            OR batch.first_seen_at > leg.venue_filled_at
                            OR batch.first_seen_at < leg.venue_filled_at
                                - CAST(:live_snapshot_age AS integer)
                                  * interval '1 second'))
                    )
                    """
                ),
                {
                    "definition_version": runtime.definition_version,
                    "definition_hash": runtime.definition_hash,
                    "live_snapshot_age": runtime.live_fill_snapshot_max_age_seconds,
                },
            )
        ).all()
    )
    rows = (
        await session.execute(
            select(
                ExecutionFillLeg,
                ExecutionOrderIntent,
                ExecutionReconciliationObservation,
                ClobMarketSnapshot,
                ClobBookBatch,
            )
            .join(ExecutionOrderIntent, ExecutionOrderIntent.id == ExecutionFillLeg.intent_id)
            .outerjoin(
                ExecutionReconciliationObservation,
                ExecutionReconciliationObservation.id
                == ExecutionFillLeg.reconciliation_observation_id,
            )
            .outerjoin(
                ClobMarketSnapshot,
                ClobMarketSnapshot.id == ExecutionFillLeg.fill_clob_snapshot_id,
            )
            .outerjoin(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
            .where(ExecutionFillLeg.eligible.is_(True))
            .order_by(ExecutionFillLeg.first_seen_at.desc(), ExecutionFillLeg.id.desc())
            .limit(_SOURCE_REATTEST_LEG_LIMIT)
        )
    ).all()
    snapshots_by_batch: dict[int, list[ClobMarketSnapshot]] = defaultdict(list)
    batches: dict[int, ClobBookBatch] = {}
    for _leg, _intent, _observation, snapshot, batch in rows:
        if snapshot is not None and batch is not None:
            batches[batch.id] = batch
    if batches:
        sibling_rows = (
            await session.execute(
                select(ClobMarketSnapshot).where(ClobMarketSnapshot.batch_id.in_(tuple(batches)))
            )
        ).scalars()
        for sibling in sibling_rows:
            snapshots_by_batch[sibling.batch_id].append(sibling)
    invalid_batches = {
        batch_id
        for batch_id, batch in batches.items()
        if not clob_batch_record_is_valid(batch, snapshots_by_batch[batch_id])
    }
    for leg, intent, observation, snapshot, batch in rows:
        if (
            not _eligible_leg_projection_is_valid(
                leg,
                intent,
                observation,
                snapshot,
                batch,
                runtime,
            )
            or batch is None
            or batch.id in invalid_batches
        ):
            invalid_leg_ids.add(leg.id)
    return len(invalid_leg_ids)


async def execution_cost_health_summary(
    session: AsyncSession,
    *,
    now: datetime,
) -> ExecutionCostHealthSummary:
    """Independently reconcile the immutable 0038 execution-evidence cohort."""

    runtime = await session.get(ExecutionCostRuntime, 1)
    if runtime is None:
        raise RuntimeError("execution cost runtime boundary is missing")
    overdue_before = now - _SCHEDULER_SLACK
    terminal_statuses = list(_TERMINAL_STATUSES)
    core = (
        (
            await session.execute(
                text(
                    """
                SELECT
                    (SELECT count(*) FROM orders o
                     WHERE o.created_at >= :opened_at)::bigint AS post_boundary_orders,
                    (SELECT count(*) FROM orders o
                     WHERE o.created_at >= :opened_at
                       AND NOT EXISTS (
                           SELECT 1 FROM execution_order_intents i
                           WHERE i.order_id = o.id
                       ))::bigint AS orders_without_intent,
                    (SELECT count(*) FROM execution_order_intents i
                     JOIN orders o ON o.id = i.order_id
                     WHERE o.status = ANY(CAST(:terminal_statuses AS text[]))
                       AND NOT EXISTS (
                           SELECT 1 FROM execution_cost_obligations obligation
                           WHERE obligation.intent_id = i.id
                             AND obligation.order_id = o.id
                       ))::bigint AS terminal_without_obligation,
                    (SELECT count(*) FROM execution_cost_obligations obligation
                     WHERE obligation.state <> 'sealed'
                       AND obligation.next_due_at < :overdue_before
                    )::bigint AS overdue_obligations,
                    (SELECT count(*)
                     FROM execution_fill_legs leg
                     CROSS JOIN unnest(CAST(:mark_horizons AS integer[]))
                                AS horizon(seconds)
                     WHERE leg.fill_effective_at IS NOT NULL
                       AND leg.fill_effective_at
                           + (horizon.seconds + CAST(:mark_grace AS integer))
                             * interval '1 second'
                           < :overdue_before
                       AND NOT EXISTS (
                           SELECT 1 FROM execution_postfill_marks mark
                           WHERE mark.fill_leg_id = leg.id
                             AND mark.horizon_seconds = horizon.seconds
                       ))::bigint AS overdue_marks,
                    (SELECT count(*) FROM execution_fill_legs leg
                     WHERE leg.mode = 'live'::execution_mode
                       AND leg.source_kind = 'live_sdk'
                       AND (
                           nullif(btrim(leg.external_trade_id), '') IS NULL
                           OR leg.liquidity_role = 'unknown'
                           OR leg.fee_usd IS NULL
                           OR leg.fee_semantics <> 'venue_reported'
                           OR leg.venue_filled_at IS NULL
                           OR leg.fill_effective_at IS NULL
                           OR leg.timestamp_semantics <> 'verified_venue_fill'
                       ))::bigint AS live_unverified_legs
                """
                ),
                {
                    "opened_at": runtime.opened_at,
                    "terminal_statuses": terminal_statuses,
                    "overdue_before": overdue_before,
                    "mark_horizons": runtime.mark_horizons_seconds,
                    "mark_grace": runtime.mark_grace_seconds,
                },
            )
        )
        .mappings()
        .one()
    )
    reconciliation_mismatches = int(
        await session.scalar(
            text(
                """
                WITH leg_aggregate AS (
                    SELECT intent_id,
                           count(*)::integer AS leg_count,
                           sum(shares) AS total_shares,
                           sum(notional_usd) AS total_notional,
                           count(*) FILTER (WHERE fee_usd IS NULL)::integer
                               AS missing_fees,
                           sum(fee_usd) AS total_fee,
                           bool_and(eligible) AS all_eligible,
                           CASE WHEN bool_or(liquidity_role = 'unknown') THEN 'unknown'
                                WHEN count(DISTINCT liquidity_role) = 1
                                    THEN min(liquidity_role)
                                ELSE 'mixed' END AS aggregate_role
                    FROM execution_fill_legs
                    GROUP BY intent_id
                )
                SELECT count(*)::bigint
                FROM execution_cost_seals seal
                JOIN execution_cost_obligations obligation
                  ON obligation.id = seal.obligation_id
                JOIN execution_order_intents intent ON intent.id = seal.intent_id
                JOIN orders order_row ON order_row.id = seal.order_id
                LEFT JOIN leg_aggregate aggregate ON aggregate.intent_id = intent.id
                LEFT JOIN positions position_row ON position_row.order_id = seal.order_id
                WHERE obligation.intent_id IS DISTINCT FROM seal.intent_id
                   OR obligation.order_id IS DISTINCT FROM seal.order_id
                   OR obligation.terminal_status IS DISTINCT FROM order_row.status
                   OR intent.order_id IS DISTINCT FROM seal.order_id
                   OR seal.leg_count IS DISTINCT FROM coalesce(aggregate.leg_count, 0)
                   OR seal.aggregate_role IS DISTINCT FROM
                      coalesce(aggregate.aggregate_role, 'unknown')
                   OR seal.total_shares IS DISTINCT FROM aggregate.total_shares
                   OR seal.filled_notional_usd IS DISTINCT FROM aggregate.total_notional
                   OR seal.total_fee_usd IS DISTINCT FROM CASE
                      WHEN coalesce(aggregate.missing_fees, 0) = 0
                        THEN aggregate.total_fee ELSE NULL END
                   OR seal.eligible IS DISTINCT FROM (
                      coalesce(aggregate.leg_count, 0) > 0
                      AND coalesce(aggregate.all_eligible, false)
                      AND coalesce(aggregate.missing_fees, 0) = 0)
                   OR (coalesce(aggregate.leg_count, 0) = 0
                       AND order_row.filled_usd <> 0)
                   OR (coalesce(aggregate.leg_count, 0) > 0 AND (
                       abs(aggregate.total_notional - order_row.filled_usd) > 0.0001
                       OR order_row.fee_usd IS DISTINCT FROM aggregate.total_fee))
                   OR (intent.mode = 'live'::execution_mode
                       AND coalesce(aggregate.leg_count, 0) > 0 AND (
                           position_row.id IS NULL
                           OR abs(position_row.size_shares - aggregate.total_shares)
                              > 0.0000005
                           OR abs(position_row.size_usd - aggregate.total_notional) > 0.0001
                           OR position_row.fee_usd IS DISTINCT FROM aggregate.total_fee))
                """
            )
        )
        or 0
    )
    identity_mismatches = int(
        await session.scalar(
            text(
                """
                SELECT count(*)::bigint
                FROM execution_fill_legs leg
                JOIN execution_order_intents intent ON intent.id = leg.intent_id
                LEFT JOIN execution_reconciliation_observations observation
                  ON observation.id = leg.reconciliation_observation_id
                WHERE leg.order_id IS DISTINCT FROM intent.order_id
                   OR leg.signal_id IS DISTINCT FROM intent.signal_id
                   OR leg.market_id IS DISTINCT FROM intent.market_id
                   OR leg.mode IS DISTINCT FROM intent.mode
                   OR leg.side IS DISTINCT FROM intent.side
                   OR leg.token_id IS DISTINCT FROM intent.token_id
                   OR (leg.reconciliation_observation_id IS NOT NULL
                       AND observation.order_id IS DISTINCT FROM leg.order_id)
                """
            )
        )
        or 0
    )
    duplicate_identities = int(
        await session.scalar(
            text(
                """
                SELECT coalesce(sum(duplicates), 0)::bigint
                FROM (
                    SELECT count(*) - 1 AS duplicates
                    FROM execution_order_intents GROUP BY order_id HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_fill_legs
                      GROUP BY order_id, external_leg_key HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_fill_legs
                      GROUP BY intent_id, leg_sequence HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_fill_legs
                      WHERE external_trade_id IS NOT NULL
                      GROUP BY order_id, external_trade_id HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1
                      FROM execution_reconciliation_observations
                      GROUP BY order_id, observation_kind, sdk_method, payload_sha256
                      HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_cost_obligations
                      GROUP BY order_id HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_cost_obligations
                      GROUP BY intent_id HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_postfill_marks
                      GROUP BY fill_leg_id, horizon_seconds HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_cost_seals
                      GROUP BY order_id HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_cost_seals
                      GROUP BY intent_id HAVING count(*) > 1
                    UNION ALL SELECT count(*) - 1 FROM execution_cost_seals
                      GROUP BY obligation_id HAVING count(*) > 1
                ) duplicate_groups
                """
            )
        )
        or 0
    )
    reason_rows = (
        await session.execute(
            text(
                """
                SELECT eligibility_reason, count(*)::bigint AS legs
                FROM execution_fill_legs
                WHERE mode = 'live'::execution_mode
                  AND source_kind = 'live_sdk'
                  AND (
                      nullif(btrim(external_trade_id), '') IS NULL
                      OR liquidity_role = 'unknown'
                      OR fee_usd IS NULL
                      OR fee_semantics <> 'venue_reported'
                      OR venue_filled_at IS NULL
                      OR fill_effective_at IS NULL
                      OR timestamp_semantics <> 'verified_venue_fill'
                  )
                GROUP BY eligibility_reason
                ORDER BY count(*) DESC, eligibility_reason
                """
            )
        )
    ).all()
    coverage_rows = (
        await session.execute(
            text(
                """
                SELECT mode_value.mode,
                       count(DISTINCT intent.id)::bigint AS attempts,
                       count(DISTINCT seal.id)::bigint AS sealed,
                       count(DISTINCT seal.id) FILTER (WHERE seal.eligible)::bigint
                           AS eligible,
                       count(DISTINCT seal.id) FILTER (WHERE seal.leg_count > 0)::bigint
                           AS filled_seals,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.leg_count > 0
                             AND seal.markout_300s_coverage = 'complete'
                       )::bigint AS mark_5m_complete,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.leg_count > 0
                             AND seal.markout_1800s_coverage = 'complete'
                       )::bigint AS mark_30m_complete
                FROM (VALUES ('paper'::execution_mode), ('live'::execution_mode))
                     AS mode_value(mode)
                LEFT JOIN execution_order_intents intent ON intent.mode = mode_value.mode
                LEFT JOIN execution_cost_seals seal ON seal.intent_id = intent.id
                GROUP BY mode_value.mode
                ORDER BY mode_value.mode::text
                """
            )
        )
    ).all()
    invalid_sources = await _invalid_eligible_source_leg_count(session, runtime)
    return ExecutionCostHealthSummary(
        opened_at=runtime.opened_at,
        post_boundary_orders=int(core["post_boundary_orders"]),
        orders_without_intent=int(core["orders_without_intent"]),
        terminal_without_obligation=int(core["terminal_without_obligation"]),
        overdue_obligations=int(core["overdue_obligations"]),
        overdue_marks=int(core["overdue_marks"]),
        invalid_eligible_source_legs=invalid_sources,
        reconciliation_mismatches=reconciliation_mismatches + identity_mismatches,
        duplicate_identities=duplicate_identities,
        live_unverified_legs=int(core["live_unverified_legs"]),
        live_unverified_reasons=tuple((str(reason), int(count)) for reason, count in reason_rows),
        coverage=tuple(
            ExecutionCostModeCoverage(
                mode=ExecutionMode(str(row.mode)),
                attempts=int(row.attempts),
                sealed=int(row.sealed),
                eligible=int(row.eligible),
                filled_seals=int(row.filled_seals),
                mark_5m_complete=int(row.mark_5m_complete),
                mark_30m_complete=int(row.mark_30m_complete),
            )
            for row in coverage_rows
        ),
    )


__all__ = [
    "ExecutionCostHealthSummary",
    "ExecutionCostModeCoverage",
    "execution_cost_health_summary",
]
