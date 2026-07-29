from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import ExecutionMode


@dataclass(frozen=True, slots=True)
class ExecutionCostReasonCount:
    reason: str
    orders: int


@dataclass(frozen=True, slots=True)
class ExecutionCostMetrics:
    eligible_orders: int
    eligible_shares: Decimal
    gross_model_edge: Decimal | None
    latency_movement: Decimal | None
    taker_spread: Decimal | None
    taker_slippage: Decimal | None
    maker_improvement: Decimal | None
    fee_drag: Decimal | None
    realized_net_edge: Decimal | None
    adverse_selection_5m: Decimal | None
    adverse_selection_30m: Decimal | None


@dataclass(frozen=True, slots=True)
class ExecutionCostSlice:
    dimension: str
    value: str
    attempts: int
    filled: int
    sealed: int
    eligible: int
    metrics: ExecutionCostMetrics


@dataclass(frozen=True, slots=True)
class ExecutionCostSummary:
    mode: ExecutionMode
    opened_at: datetime
    definition_version: str
    definition_hash: str
    attempts: int
    filled_attempts: int
    no_fill_attempts: int
    pending_attempts: int
    sealed_attempts: int
    eligible_seals: int
    ineligible_seals: int
    maker_attempts: int
    maker_fills: int
    maker_fill_rate: Decimal | None
    maker_seals: int
    taker_seals: int
    mixed_seals: int
    unknown_role_seals: int
    expected_slippage_known: int
    expected_slippage_total: int
    exact_fee_known: int
    exact_fee_total: int
    mark_5m_known: int
    mark_5m_total: int
    mark_30m_known: int
    mark_30m_total: int
    metrics: ExecutionCostMetrics
    reasons: tuple[ExecutionCostReasonCount, ...]
    slices: tuple[ExecutionCostSlice, ...]


@dataclass(frozen=True, slots=True)
class ExecutionCostOrderSummary:
    order_id: int
    intent_id: int
    signal_id: int
    market_id: int
    station_icao: str | None
    market_date: date
    kind: str
    confidence: str
    horizon_h: Decimal | None
    mode: ExecutionMode
    side: str
    order_style: str
    requested_price: Decimal
    requested_notional_usd: Decimal
    intent_at: datetime
    terminal_status: str | None
    obligation_state: str | None
    eligible: bool | None
    eligibility_reason: str | None
    aggregate_role: str | None
    leg_count: int
    total_shares: Decimal | None
    vwap_fill_price: Decimal | None
    total_fee_usd: Decimal | None
    gross_model_edge: Decimal | None
    latency_movement: Decimal | None
    taker_spread: Decimal | None
    taker_slippage: Decimal | None
    maker_improvement: Decimal | None
    fee_drag: Decimal | None
    realized_net_edge: Decimal | None
    adverse_selection_5m: Decimal | None
    adverse_selection_30m: Decimal | None
    sealed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExecutionCostOrderPage:
    mode: ExecutionMode
    total: int
    limit: int
    offset: int
    orders: tuple[ExecutionCostOrderSummary, ...]


_WEIGHTED_METRIC_SQL = r"""
    count(*) FILTER (WHERE seal.eligible)::bigint AS eligible_orders,
    coalesce(sum(seal.total_shares) FILTER (WHERE seal.eligible), 0)
        AS eligible_shares,
    sum(seal.gross_model_edge * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.gross_model_edge IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.gross_model_edge IS NOT NULL), 0)
        AS gross_model_edge,
    sum(seal.latency_movement * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.latency_movement IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.latency_movement IS NOT NULL), 0)
        AS latency_movement,
    sum(seal.taker_spread * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.taker_spread IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.taker_spread IS NOT NULL), 0)
        AS taker_spread,
    sum(seal.taker_slippage * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.taker_slippage IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.taker_slippage IS NOT NULL), 0)
        AS taker_slippage,
    sum(seal.maker_improvement * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.maker_improvement IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.maker_improvement IS NOT NULL), 0)
        AS maker_improvement,
    sum(seal.fee_drag * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.fee_drag IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.fee_drag IS NOT NULL), 0)
        AS fee_drag,
    sum(seal.realized_net_edge * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.realized_net_edge IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.realized_net_edge IS NOT NULL), 0)
        AS realized_net_edge,
    sum(seal.adverse_selection_300s * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.adverse_selection_300s IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.adverse_selection_300s IS NOT NULL), 0)
        AS adverse_selection_5m,
    sum(seal.adverse_selection_1800s * seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.adverse_selection_1800s IS NOT NULL)
        / nullif(sum(seal.total_shares)
        FILTER (WHERE seal.eligible AND seal.adverse_selection_1800s IS NOT NULL), 0)
        AS adverse_selection_30m
"""


def _decimal(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _metrics(row: RowMapping) -> ExecutionCostMetrics:
    return ExecutionCostMetrics(
        eligible_orders=int(row["eligible_orders"]),
        eligible_shares=Decimal(row["eligible_shares"]),
        gross_model_edge=_decimal(row["gross_model_edge"]),
        latency_movement=_decimal(row["latency_movement"]),
        taker_spread=_decimal(row["taker_spread"]),
        taker_slippage=_decimal(row["taker_slippage"]),
        maker_improvement=_decimal(row["maker_improvement"]),
        fee_drag=_decimal(row["fee_drag"]),
        realized_net_edge=_decimal(row["realized_net_edge"]),
        adverse_selection_5m=_decimal(row["adverse_selection_5m"]),
        adverse_selection_30m=_decimal(row["adverse_selection_30m"]),
    )


async def execution_cost_summary(
    session: AsyncSession,
    *,
    mode: ExecutionMode,
) -> ExecutionCostSummary:
    """Return the frozen 0038 cohort without pooling paper and live evidence."""

    row = (
        await session.execute(
            text(
                rf"""
                SELECT runtime.opened_at,
                       runtime.definition_version,
                       runtime.definition_hash,
                       count(DISTINCT intent.id)::bigint AS attempts,
                       count(DISTINCT intent.id) FILTER (
                           WHERE EXISTS (
                               SELECT 1 FROM execution_fill_legs leg
                               WHERE leg.intent_id = intent.id
                           )
                       )::bigint AS filled_attempts,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.eligibility_reason = 'no_fill'
                       )::bigint AS no_fill_attempts,
                       count(DISTINCT seal.id)::bigint AS sealed_attempts,
                       count(DISTINCT seal.id) FILTER (WHERE seal.eligible)::bigint
                           AS eligible_seals,
                       count(DISTINCT seal.id) FILTER (WHERE NOT seal.eligible)::bigint
                           AS ineligible_seals,
                       count(DISTINCT intent.id) FILTER (
                           WHERE intent.order_style = 'maker'
                       )::bigint AS maker_attempts,
                       count(DISTINCT intent.id) FILTER (
                           WHERE intent.order_style = 'maker'
                             AND EXISTS (
                                 SELECT 1 FROM execution_fill_legs leg
                                 WHERE leg.intent_id = intent.id
                             )
                       )::bigint AS maker_fills,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.aggregate_role = 'maker'
                       )::bigint AS maker_seals,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.aggregate_role = 'taker'
                       )::bigint AS taker_seals,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.aggregate_role = 'mixed'
                       )::bigint AS mixed_seals,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.aggregate_role = 'unknown'
                       )::bigint AS unknown_role_seals,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.expected_slippage_coverage = 'modeled'
                       )::bigint AS expected_slippage_known,
                       count(DISTINCT seal.id)::bigint AS expected_slippage_total,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.total_fee_usd IS NOT NULL
                       )::bigint AS exact_fee_known,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.leg_count > 0
                       )::bigint AS exact_fee_total,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.markout_300s_coverage = 'complete'
                       )::bigint AS mark_5m_known,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.markout_300s_coverage <> 'not_applicable'
                       )::bigint AS mark_5m_total,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.markout_1800s_coverage = 'complete'
                       )::bigint AS mark_30m_known,
                       count(DISTINCT seal.id) FILTER (
                           WHERE seal.markout_1800s_coverage <> 'not_applicable'
                       )::bigint AS mark_30m_total,
                       {_WEIGHTED_METRIC_SQL}
                FROM execution_cost_runtime runtime
                LEFT JOIN execution_order_intents intent ON intent.mode = :mode
                LEFT JOIN execution_cost_seals seal ON seal.intent_id = intent.id
                WHERE runtime.id = 1
                GROUP BY runtime.opened_at, runtime.definition_version,
                         runtime.definition_hash
                """
            ),
            {"mode": mode.value},
        )
    ).mappings().one()
    reasons = tuple(
        ExecutionCostReasonCount(reason=cast(str, item["reason"]), orders=int(item["orders"]))
        for item in (
            await session.execute(
                text(
                    """
                    SELECT seal.eligibility_reason AS reason,
                           count(*)::bigint AS orders
                    FROM execution_cost_seals seal
                    JOIN execution_order_intents intent ON intent.id = seal.intent_id
                    WHERE intent.mode = :mode AND NOT seal.eligible
                    GROUP BY seal.eligibility_reason
                    ORDER BY count(*) DESC, seal.eligibility_reason
                    """
                ),
                {"mode": mode.value},
            )
        ).mappings()
    )
    slices = await _execution_cost_slices(session, mode=mode)
    maker_attempts = int(row["maker_attempts"])
    maker_fills = int(row["maker_fills"])
    return ExecutionCostSummary(
        mode=mode,
        opened_at=cast(datetime, row["opened_at"]),
        definition_version=cast(str, row["definition_version"]),
        definition_hash=cast(str, row["definition_hash"]),
        attempts=int(row["attempts"]),
        filled_attempts=int(row["filled_attempts"]),
        no_fill_attempts=int(row["no_fill_attempts"]),
        pending_attempts=max(0, int(row["attempts"]) - int(row["sealed_attempts"])),
        sealed_attempts=int(row["sealed_attempts"]),
        eligible_seals=int(row["eligible_seals"]),
        ineligible_seals=int(row["ineligible_seals"]),
        maker_attempts=maker_attempts,
        maker_fills=maker_fills,
        maker_fill_rate=(
            Decimal(maker_fills) / Decimal(maker_attempts)
            if maker_attempts
            else None
        ),
        maker_seals=int(row["maker_seals"]),
        taker_seals=int(row["taker_seals"]),
        mixed_seals=int(row["mixed_seals"]),
        unknown_role_seals=int(row["unknown_role_seals"]),
        expected_slippage_known=int(row["expected_slippage_known"]),
        expected_slippage_total=int(row["expected_slippage_total"]),
        exact_fee_known=int(row["exact_fee_known"]),
        exact_fee_total=int(row["exact_fee_total"]),
        mark_5m_known=int(row["mark_5m_known"]),
        mark_5m_total=int(row["mark_5m_total"]),
        mark_30m_known=int(row["mark_30m_known"]),
        mark_30m_total=int(row["mark_30m_total"]),
        metrics=_metrics(row),
        reasons=reasons,
        slices=slices,
    )


async def _execution_cost_slices(
    session: AsyncSession,
    *,
    mode: ExecutionMode,
) -> tuple[ExecutionCostSlice, ...]:
    dimension_sql = {
        "station": "coalesce(station.icao, 'unknown')",
        "kind": "market.kind::text",
        "horizon": (
            "CASE WHEN model.horizon_h IS NULL THEN 'unknown' "
            "WHEN model.horizon_h <= 6 THEN '0-6h' "
            "WHEN model.horizon_h <= 24 THEN '6-24h' ELSE '24h+' END"
        ),
        "confidence": "signal.confidence::text",
        "side": "intent.side::text",
        "liquidity_role": "coalesce(seal.aggregate_role, 'unsealed')",
        "entry_price_band": (
            "CASE WHEN seal.vwap_fill_price IS NULL THEN 'no-fill' "
            "WHEN seal.vwap_fill_price < 0.10 THEN '0.00-0.10' "
            "WHEN seal.vwap_fill_price < 0.25 THEN '0.10-0.25' "
            "WHEN seal.vwap_fill_price < 0.50 THEN '0.25-0.50' "
            "WHEN seal.vwap_fill_price < 0.75 THEN '0.50-0.75' "
            "WHEN seal.vwap_fill_price < 0.90 THEN '0.75-0.90' "
            "ELSE '0.90-1.00' END"
        ),
    }
    result: list[ExecutionCostSlice] = []
    for dimension, expression in dimension_sql.items():
        rows = (
            await session.execute(
                text(
                    rf"""
                    SELECT {expression} AS value,
                           count(DISTINCT intent.id)::bigint AS attempts,
                           count(DISTINCT intent.id) FILTER (
                               WHERE EXISTS (
                                   SELECT 1 FROM execution_fill_legs leg
                                   WHERE leg.intent_id = intent.id
                               )
                           )::bigint AS filled,
                           count(DISTINCT seal.id)::bigint AS sealed,
                           count(DISTINCT seal.id) FILTER (WHERE seal.eligible)::bigint
                               AS eligible,
                           {_WEIGHTED_METRIC_SQL}
                    FROM execution_order_intents intent
                    JOIN signals signal ON signal.id = intent.signal_id
                    JOIN markets market ON market.id = intent.market_id
                    LEFT JOIN stations station ON station.id = market.station_id
                    LEFT JOIN model_probs model ON model.id = intent.model_prob_id
                    LEFT JOIN execution_cost_seals seal ON seal.intent_id = intent.id
                    WHERE intent.mode = :mode
                    GROUP BY {expression}
                    ORDER BY {expression}
                    """
                ),
                {"mode": mode.value},
            )
        ).mappings()
        result.extend(
            ExecutionCostSlice(
                dimension=dimension,
                value=cast(str, item["value"]),
                attempts=int(item["attempts"]),
                filled=int(item["filled"]),
                sealed=int(item["sealed"]),
                eligible=int(item["eligible"]),
                metrics=_metrics(item),
            )
            for item in rows
        )
    return tuple(result)


async def execution_cost_orders(
    session: AsyncSession,
    *,
    mode: ExecutionMode,
    limit: int = 50,
    offset: int = 0,
    eligible: bool | None = None,
    role: str | None = None,
    station: str | None = None,
    kind: str | None = None,
    horizon: str | None = None,
    side: str | None = None,
    reason: str | None = None,
) -> ExecutionCostOrderPage:
    """Paginated order drill-down with explicit, parameterized filters."""

    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("execution-cost pagination is outside the supported range")
    conditions = ["intent.mode = :mode"]
    params: dict[str, object] = {"mode": mode.value, "limit": limit, "offset": offset}
    optional = {
        "eligible": ("seal.eligible = :eligible", eligible),
        "role": ("seal.aggregate_role = :role", role),
        "station": ("station.icao = :station", station),
        "kind": ("market.kind::text = :kind", kind),
        "side": ("intent.side::text = :side", side),
        "reason": ("seal.eligibility_reason = :reason", reason),
    }
    for name, (condition, value) in optional.items():
        if value is not None:
            conditions.append(condition)
            params[name] = value
    if horizon is not None:
        horizon_conditions = {
            "0-6h": "model.horizon_h <= 6",
            "6-24h": "model.horizon_h > 6 AND model.horizon_h <= 24",
            "24h+": "model.horizon_h > 24",
            "unknown": "model.horizon_h IS NULL",
        }
        if horizon not in horizon_conditions:
            raise ValueError("unsupported execution-cost horizon filter")
        conditions.append(horizon_conditions[horizon])
    where = " AND ".join(conditions)
    from_sql = r"""
        FROM execution_order_intents intent
        JOIN signals signal ON signal.id = intent.signal_id
        JOIN markets market ON market.id = intent.market_id
        LEFT JOIN stations station ON station.id = market.station_id
        LEFT JOIN model_probs model ON model.id = intent.model_prob_id
        LEFT JOIN execution_cost_obligations obligation ON obligation.intent_id = intent.id
        LEFT JOIN execution_cost_seals seal ON seal.intent_id = intent.id
    """
    total = int(
        (
            await session.execute(
                text(f"SELECT count(*) {from_sql} WHERE {where}"), params
            )
        ).scalar_one()
    )
    rows = (
        await session.execute(
            text(
                f"""
                SELECT intent.order_id, intent.id AS intent_id, intent.signal_id,
                       intent.market_id, station.icao AS station_icao,
                       market.market_date, market.kind::text AS kind,
                       signal.confidence::text AS confidence, model.horizon_h,
                       intent.mode::text AS mode, intent.side::text AS side,
                       intent.order_style, intent.requested_limit_price AS requested_price,
                       intent.requested_notional_usd, intent.intent_at,
                       obligation.terminal_status, obligation.state AS obligation_state,
                       seal.eligible, seal.eligibility_reason, seal.aggregate_role,
                       coalesce(seal.leg_count, 0) AS leg_count, seal.total_shares,
                       seal.vwap_fill_price, seal.total_fee_usd,
                       seal.gross_model_edge, seal.latency_movement,
                       seal.taker_spread, seal.taker_slippage,
                       seal.maker_improvement, seal.fee_drag,
                       seal.realized_net_edge,
                       seal.adverse_selection_300s AS adverse_selection_5m,
                       seal.adverse_selection_1800s AS adverse_selection_30m,
                       seal.sealed_at
                {from_sql}
                WHERE {where}
                ORDER BY intent.intent_at DESC, intent.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).mappings()
    return ExecutionCostOrderPage(
        mode=mode,
        total=total,
        limit=limit,
        offset=offset,
        orders=tuple(_order_summary(row) for row in rows),
    )


async def execution_cost_report(
    session: AsyncSession,
    mode: ExecutionMode,
) -> ExecutionCostSummary:
    """Operator-facing spelling retained as the stable public repository API."""

    return await execution_cost_summary(session, mode=mode)


def _order_summary(row: RowMapping) -> ExecutionCostOrderSummary:
    return ExecutionCostOrderSummary(
        order_id=int(row["order_id"]),
        intent_id=int(row["intent_id"]),
        signal_id=int(row["signal_id"]),
        market_id=int(row["market_id"]),
        station_icao=cast(str | None, row["station_icao"]),
        market_date=cast(date, row["market_date"]),
        kind=cast(str, row["kind"]),
        confidence=cast(str, row["confidence"]),
        horizon_h=_decimal(row["horizon_h"]),
        mode=ExecutionMode(cast(str, row["mode"])),
        side=cast(str, row["side"]),
        order_style=cast(str, row["order_style"]),
        requested_price=Decimal(row["requested_price"]),
        requested_notional_usd=Decimal(row["requested_notional_usd"]),
        intent_at=cast(datetime, row["intent_at"]),
        terminal_status=cast(str | None, row["terminal_status"]),
        obligation_state=cast(str | None, row["obligation_state"]),
        eligible=cast(bool | None, row["eligible"]),
        eligibility_reason=cast(str | None, row["eligibility_reason"]),
        aggregate_role=cast(str | None, row["aggregate_role"]),
        leg_count=int(row["leg_count"]),
        total_shares=_decimal(row["total_shares"]),
        vwap_fill_price=_decimal(row["vwap_fill_price"]),
        total_fee_usd=_decimal(row["total_fee_usd"]),
        gross_model_edge=_decimal(row["gross_model_edge"]),
        latency_movement=_decimal(row["latency_movement"]),
        taker_spread=_decimal(row["taker_spread"]),
        taker_slippage=_decimal(row["taker_slippage"]),
        maker_improvement=_decimal(row["maker_improvement"]),
        fee_drag=_decimal(row["fee_drag"]),
        realized_net_edge=_decimal(row["realized_net_edge"]),
        adverse_selection_5m=_decimal(row["adverse_selection_5m"]),
        adverse_selection_30m=_decimal(row["adverse_selection_30m"]),
        sealed_at=cast(datetime | None, row["sealed_at"]),
    )


__all__ = [
    "ExecutionCostMetrics",
    "ExecutionCostOrderPage",
    "ExecutionCostOrderSummary",
    "ExecutionCostReasonCount",
    "ExecutionCostSlice",
    "ExecutionCostSummary",
    "execution_cost_orders",
    "execution_cost_report",
    "execution_cost_summary",
]
