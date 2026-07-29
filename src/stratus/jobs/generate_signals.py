from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    BankrollLog,
    ClobBookBatch,
    ClobMarketSnapshot,
    ExecutionMode,
    Market,
    MarketStatus,
    ModelProb,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    Station,
    StationStage,
    TradeSide,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.execution import in_flight_live_exposure
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import get_runtime_control
from stratus.repositories.signals import SignalValue, insert_signals
from stratus.repositories.station_reliability import (
    acquire_station_reliability_gate_lock,
    latest_station_reliability_by_station,
    station_reliability_record_is_valid,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL, traded_token_id
from stratus.services.event_probability_coherence import (
    EVENT_PROBABILITY_COHERENCE_PROTOCOL,
    EVENT_PROBABILITY_MASS_TOLERANCE,
    EventBucketProbability,
    EventCoherenceReason,
    EventCoherenceResult,
    assess_event_probability_coherence,
    assess_event_probability_materialization,
    event_probability_distribution_sha256,
    pipeline_cohort_from_components,
)
from stratus.services.signals import (
    SignalInputs,
    SignalPolicy,
    ensemble_agreement,
    evaluate_signal,
)
from stratus.services.station_reliability import (
    station_reliability_policy_hash,
    station_signal_gate_reasons,
)

logger = logging.getLogger(__name__)
CANDIDATE_ANCHOR_ADMISSION_PROTOCOL = "candidate-anchor-v2"
_ORDER_NOTIONAL_QUANTUM = Decimal("0.0001")


def _decimal(value: float, precision: str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(precision))


def _risk_capped_order_size(
    decision_size_usd: float,
    market_remaining_usd: Decimal,
    station_remaining_usd: Decimal,
) -> Decimal:
    """Return a persisted-size-safe order notional without exceeding a risk cap."""

    uncapped = Decimal(str(decision_size_usd))
    bounded = max(
        Decimal(0),
        min(uncapped, market_remaining_usd, station_remaining_usd),
    )
    return bounded.quantize(_ORDER_NOTIONAL_QUANTUM, rounding=ROUND_DOWN)


def signal_policy(settings: Settings) -> SignalPolicy:
    return SignalPolicy(
        min_edge=settings.signal_min_edge,
        max_edge=settings.signal_max_edge,
        max_spread=settings.max_spread,
        edge_cost_buffer=settings.edge_cost_buffer,
        kelly_multiplier=settings.kelly_multiplier,
        max_win_fraction=settings.max_win_fraction,
        tail_price_low=settings.tail_price_low,
        tail_price_high=settings.tail_price_high,
        min_calibration_samples=settings.min_traded_calibration_samples,
    )


def _calibration_samples(components: dict[str, object]) -> int:
    """Calibration depth recorded by recompute_probs alongside shrinkage.
    Missing metadata (pre-upgrade rows) reads as uncalibrated — fail closed."""
    shrinkage = components.get("shrinkage")
    if isinstance(shrinkage, dict):
        n = shrinkage.get("n")
        if isinstance(n, int) and n >= 0:
            return n
    return 0


def _event_coherence_by_id(
    event_markets: list[Market],
    *,
    eligible_market_ids: set[int],
    latest_probs: Mapping[int, ModelProb],
    latest_prices: Mapping[int, tuple[ClobMarketSnapshot, datetime]],
) -> dict[str, EventCoherenceResult]:
    """Adapt ORM rows into the pure, fail-closed event guard."""

    grouped: dict[str, list[EventBucketProbability]] = defaultdict(list)
    for market in event_markets:
        probability = latest_probs.get(market.id)
        price_row = (
            latest_prices.get(probability.market_snapshot_id)
            if probability is not None and probability.market_snapshot_id is not None
            else None
        )
        anchor = price_row[0] if price_row is not None else None
        grouped[market.event_id].append(
            EventBucketProbability(
                market_id=market.id,
                event_id=market.event_id,
                station_id=market.station_id,
                market_date=market.market_date,
                kind=market.kind.value,
                resolves_at=market.resolves_at,
                resolution_source=market.resolution_source,
                bucket_low_c=market.bucket_low_c,
                bucket_high_c=market.bucket_high_c,
                eligible=(
                    market.id in eligible_market_ids
                    and market.status is MarketStatus.ACTIVE
                    and market.station_verified
                ),
                probability=probability.prob if probability is not None else None,
                probability_ts=probability.ts if probability is not None else None,
                horizon_h=probability.horizon_h if probability is not None else None,
                pipeline_cohort=(
                    pipeline_cohort_from_components(probability.components)
                    if probability is not None
                    else None
                ),
                anchor_snapshot_id=(
                    probability.market_snapshot_id if probability is not None else None
                ),
                anchor_market_id=anchor.market_id if anchor is not None else None,
                anchor_batch_id=anchor.batch_id if anchor is not None else None,
                anchor_eligible=bool(
                    probability is not None
                    and probability.market_anchor_protocol == CLOB_DUAL_PROTOCOL
                    and anchor is not None
                    and anchor.id == probability.market_snapshot_id
                    and anchor.eligible
                ),
            )
        )
    return {
        event_id: assess_event_probability_coherence(rows) for event_id, rows in grouped.items()
    }


def _event_materialization_by_id(
    event_markets: list[Market],
    *,
    latest_probs: Mapping[int, ModelProb],
) -> dict[str, EventCoherenceResult]:
    """Prove each latest event vector without borrowing execution anchors.

    The model vector is an event-level object: every known bucket must share
    one timestamp, horizon, pipeline cohort, exhaustive partition, and exact
    unit mass.  CLOB availability is deliberately checked later for the one
    candidate token being considered; an empty sibling book cannot make an
    otherwise complete probability vector stale.
    """

    grouped: dict[str, list[EventBucketProbability]] = defaultdict(list)
    for market in event_markets:
        probability = latest_probs.get(market.id)
        grouped[market.event_id].append(
            EventBucketProbability(
                market_id=market.id,
                event_id=market.event_id,
                station_id=market.station_id,
                market_date=market.market_date,
                kind=market.kind.value,
                resolves_at=market.resolves_at,
                resolution_source=market.resolution_source,
                bucket_low_c=market.bucket_low_c,
                bucket_high_c=market.bucket_high_c,
                eligible=(market.status is MarketStatus.ACTIVE and market.station_verified),
                probability=probability.prob if probability is not None else None,
                probability_ts=probability.ts if probability is not None else None,
                horizon_h=probability.horizon_h if probability is not None else None,
                pipeline_cohort=(
                    pipeline_cohort_from_components(probability.components)
                    if probability is not None
                    else None
                ),
                anchor_snapshot_id=None,
                anchor_market_id=None,
                anchor_batch_id=None,
                anchor_eligible=False,
            )
        )
    return {
        event_id: assess_event_probability_materialization(rows)
        for event_id, rows in grouped.items()
    }


def _candidate_anchor_reason(
    probability: ModelProb | None,
    price_row: tuple[ClobMarketSnapshot, datetime] | None,
    *,
    market: Market,
) -> str | None:
    """Validate the exact dual-token book bound to one candidate probability.

    This is intentionally candidate-local.  It never searches older model
    rows or sibling snapshots, so a current signal cannot inherit stale CLOB
    evidence merely because that bucket had a usable book in an earlier run.
    """

    if probability is None or price_row is None:
        return EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value
    snapshot, first_seen_at = price_row
    if (
        probability.market_anchor_protocol != CLOB_DUAL_PROTOCOL
        or probability.market_snapshot_id is None
        or snapshot.id != probability.market_snapshot_id
        or snapshot.market_id != market.id
        or not snapshot.complete
        or not snapshot.eligible
        or not (market.resolution_source or "").strip()
        or market.condition_id is None
        or snapshot.condition_id != market.condition_id
        or market.yes_token_id is None
        or market.no_token_id is None
        or snapshot.yes_token_id != market.yes_token_id
        or snapshot.no_token_id != market.no_token_id
    ):
        return EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value
    if (
        probability.ts.tzinfo is None
        or probability.ts.utcoffset() is None
        or first_seen_at.tzinfo is None
        or first_seen_at.utcoffset() is None
        or first_seen_at.astimezone(UTC) > probability.ts.astimezone(UTC)
    ):
        return EventCoherenceReason.PROBABILITY_TIME_MISMATCH.value
    return None


def _materialized_coherence_reason(
    coherence: EventCoherenceResult | None,
    *,
    market_id: int,
    stored_probability: Decimal,
    probability_ts: datetime,
    publication_ts: datetime | None,
    components: Mapping[str, object],
) -> str | None:
    if coherence is None:
        return EventCoherenceReason.PROBABILITY_VECTOR_INCOMPLETE.value
    if not coherence.passed:
        return (
            coherence.reason.value
            if coherence.reason is not None
            else EventCoherenceReason.PROBABILITY_VECTOR_INCOMPLETE.value
        )
    if (
        publication_ts is None
        or coherence.probability_ts is None
        or probability_ts.tzinfo is None
        or probability_ts.utcoffset() is None
        or coherence.probability_ts != publication_ts.astimezone(UTC)
        or coherence.probability_ts != probability_ts.astimezone(UTC)
    ):
        return EventCoherenceReason.PROBABILITY_COHORT_STALE.value
    coherent_probability = coherence.probability_by_market.get(market_id)
    if (
        coherent_probability is None
        or coherence.normalized
        or coherent_probability != stored_probability
    ):
        return EventCoherenceReason.PROBABILITY_NOT_MATERIALIZED.value
    proof = components.get("event_coherence")
    pipeline_cohort = pipeline_cohort_from_components(components)
    if not isinstance(proof, dict):
        return EventCoherenceReason.PROBABILITY_NOT_MATERIALIZED.value
    final_probability = proof.get("final_probability")
    event_total_final = proof.get("event_total_final")
    raw_probability = proof.get("raw_probability")
    event_total_before = proof.get("event_total_before")
    normalization_scale = proof.get("normalization_scale")
    try:
        final_decimal = Decimal(final_probability) if isinstance(final_probability, str) else None
        total_final_decimal = (
            Decimal(event_total_final) if isinstance(event_total_final, str) else None
        )
        raw_decimal = Decimal(raw_probability) if isinstance(raw_probability, str) else None
        total_before_decimal = (
            Decimal(event_total_before) if isinstance(event_total_before, str) else None
        )
        scale_decimal = (
            Decimal(normalization_scale) if isinstance(normalization_scale, str) else None
        )
        distribution_hash = event_probability_distribution_sha256(coherence.probability_by_market)
    except (ArithmeticError, ValueError):
        return EventCoherenceReason.PROBABILITY_NOT_MATERIALIZED.value
    proof_anchor_batch_id = proof.get("anchor_batch_id")
    applied = proof.get("applied")
    trade_context_passed = proof.get("passed")
    trade_context_reason = proof.get("reason")
    expected_scale = (
        Decimal("1") / total_before_decimal
        if total_before_decimal is not None and total_before_decimal > 0
        else None
    )
    if (
        proof.get("protocol") != EVENT_PROBABILITY_COHERENCE_PROTOCOL
        or proof.get("event_id") != coherence.event_id
        or proof.get("materialized") is not True
        or proof.get("materialization_reason") is not None
        or not isinstance(trade_context_passed, bool)
        or (trade_context_passed and trade_context_reason is not None)
        or (not trade_context_passed and not isinstance(trade_context_reason, str))
        or not isinstance(applied, bool)
        or proof.get("probability_ts") != coherence.probability_ts.isoformat()
        or proof.get("distribution_sha256") != distribution_hash
        or proof.get("pipeline_cohort") != pipeline_cohort
        or pipeline_cohort != coherence.pipeline_cohort
        or (
            trade_context_passed
            and (
                not isinstance(proof_anchor_batch_id, int)
                or isinstance(proof_anchor_batch_id, bool)
            )
        )
        or proof.get("bucket_count") != coherence.bucket_count
        or proof.get("probability_count") != coherence.probability_count
        or final_decimal != stored_probability
        or total_final_decimal != Decimal("1")
        or raw_decimal is None
        or not raw_decimal.is_finite()
        or raw_decimal < 0
        or raw_decimal > 1
        or total_before_decimal is None
        or not total_before_decimal.is_finite()
        or total_before_decimal <= 0
        or scale_decimal is None
        or not scale_decimal.is_finite()
        or scale_decimal <= 0
        or abs(total_before_decimal - Decimal("1")) > EVENT_PROBABILITY_MASS_TOLERANCE
        or applied != (total_before_decimal != Decimal("1"))
        or expected_scale is None
        or scale_decimal != expected_scale
    ):
        return EventCoherenceReason.PROBABILITY_NOT_MATERIALIZED.value
    return None


async def generate_signals(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory.begin() as session:
            await acquire_station_reliability_gate_lock(session)
            control = await get_runtime_control(session)
            mode = control.mode
            eligible_stages = (
                (StationStage.APPROVED,)
                if mode is ExecutionMode.LIVE
                else (StationStage.PAPER, StationStage.APPROVED)
            )
            loss_limits, _expired = await enforce_loss_limits(
                session,
                mode=mode,
                initial_bankroll_usd=settings.initial_bankroll_usd,
                timezone=settings.risk_timezone,
                now=now,
            )
            control = await get_runtime_control(session)
            market_rows = (
                await session.execute(
                    select(Market, Station)
                    .join(Station, Market.station_id == Station.id)
                    .where(
                        Market.status == MarketStatus.ACTIVE,
                        Market.station_verified.is_(True),
                        Station.active.is_(True),
                        Station.stage.in_(eligible_stages),
                    )
                )
            ).all()
            station_ids = sorted({station.id for _market, station in market_rows})
            station_reliability = await latest_station_reliability_by_station(
                session,
                station_ids,
            )
            event_ids = sorted({market.event_id for market, _station in market_rows})
            event_markets = (
                list(
                    (await session.execute(select(Market).where(Market.event_id.in_(event_ids))))
                    .scalars()
                    .all()
                )
                if event_ids
                else []
            )
            event_market_ids = [market.id for market in event_markets]
            probabilities = (
                (
                    await session.execute(
                        select(ModelProb)
                        .where(ModelProb.market_id.in_(event_market_ids))
                        .distinct(ModelProb.market_id)
                        .order_by(ModelProb.market_id, ModelProb.ts.desc())
                    )
                )
                .scalars()
                .all()
            )
            snapshot_ids = [
                probability.market_snapshot_id
                for probability in probabilities
                if probability.market_snapshot_id is not None
            ]
            prices = (
                await session.execute(
                    select(ClobMarketSnapshot, ClobBookBatch.first_seen_at)
                    .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
                    .where(
                        ClobMarketSnapshot.id.in_(snapshot_ids),
                        ClobMarketSnapshot.eligible.is_(True),
                    )
                )
            ).all()
            bankroll = (
                await session.execute(
                    select(BankrollLog.balance_usd)
                    .where(BankrollLog.mode == mode)
                    .order_by(BankrollLog.ts.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            open_positions = (
                await session.execute(
                    select(Position, Market)
                    .join(Market, Position.market_id == Market.id)
                    .where(Position.mode == mode, Position.status == PositionStatus.OPEN)
                )
            ).all()
            pending_signals = (
                await session.execute(
                    select(Signal, Market)
                    .join(Market, Signal.market_id == Market.id)
                    .where(Signal.status == SignalStatus.PENDING)
                )
            ).all()
            # Live mode only: orders placed but not yet turned into a position
            # by the fill poll. Paper positions are created atomically, so
            # there is no such window there.
            in_flight = await in_flight_live_exposure(session) if mode is ExecutionMode.LIVE else []

        bankroll_amount = (
            Decimal(bankroll)
            if bankroll is not None
            else Decimal(str(settings.initial_bankroll_usd))
        )
        bankroll_usd = float(bankroll_amount)
        latest_probs = {probability.market_id: probability for probability in probabilities}
        latest_prices = {price.id: (price, first_seen_at) for price, first_seen_at in prices}
        event_coherence = _event_materialization_by_id(
            event_markets,
            latest_probs=latest_probs,
        )
        failed_coherence = [result for result in event_coherence.values() if not result.passed]
        normalized_coherence = [
            result for result in event_coherence.values() if result.passed and result.normalized
        ]
        if failed_coherence:
            reason_counts = Counter(
                result.reason.value for result in failed_coherence if result.reason is not None
            )
            logger.info(
                "event probability materialization blocked %d/%d events: %s",
                len(failed_coherence),
                len(event_coherence),
                dict(sorted(reason_counts.items())),
            )
        if normalized_coherence:
            logger.info(
                "event probability coherence normalized %d/%d events within tolerance",
                len(normalized_coherence),
                len(event_coherence),
            )
        if failed_coherence or normalized_coherence:
            logger.debug(
                "event probability coherence diagnostics: %s",
                [result.metadata() for result in (*failed_coherence, *normalized_coherence)],
            )

        market_exposure: dict[int, Decimal] = defaultdict(Decimal)
        station_day_exposure: dict[tuple[int | None, object], Decimal] = defaultdict(Decimal)
        pending_market_ids = set()
        for position, market in open_positions:
            exposure = position.size_usd
            market_exposure[market.id] += exposure
            station_day_exposure[(market.station_id, market.market_date)] += exposure
        for signal, market in pending_signals:
            exposure = signal.size_usd
            pending_market_ids.add(market.id)
            market_exposure[market.id] += exposure
            station_day_exposure[(market.station_id, market.market_date)] += exposure
        for order in in_flight:
            exposure = order.size_usd
            pending_market_ids.add(order.market_id)
            market_exposure[order.market_id] += exposure
            station_day_exposure[(order.station_id, order.market_date)] += exposure

        global_reasons = []
        if control.kill_switch:
            global_reasons.append("kill_switch")
        if control.halted_reason:
            global_reasons.append("project_halted")
        if loss_limits.daily_breached:
            global_reasons.append("daily_loss_limit")
        if loss_limits.weekly_breached:
            global_reasons.append("weekly_loss_limit")

        policy = signal_policy(settings)
        reliability_policy_hash = station_reliability_policy_hash(settings)
        candidates = []
        for market, station in market_rows:
            probability = latest_probs.get(market.id)
            coherence = event_coherence.get(market.event_id)
            price_row = (
                latest_prices.get(probability.market_snapshot_id)
                if probability is not None and probability.market_snapshot_id is not None
                else None
            )
            anchor_reason = _candidate_anchor_reason(
                probability,
                price_row,
                market=market,
            )
            if probability is None or price_row is None:
                continue
            price, price_seen_at = price_row
            if (
                price.yes_best_bid is None
                or price.yes_best_ask is None
                or price.yes_bid_depth_usd is None
                or price.yes_ask_depth_usd is None
                or price.no_best_bid is None
                or price.no_best_ask is None
                or price.no_bid_depth_usd is None
                or price.no_ask_depth_usd is None
            ):
                continue
            # A signal must quote the exact final ModelProb. Event normalization
            # is a model-publication concern; silently changing p here breaks
            # immutable intent provenance and can turn a legacy row tradable.
            effective_probability = probability.prob
            coherence_reason = _materialized_coherence_reason(
                coherence,
                market_id=market.id,
                stored_probability=probability.prob,
                probability_ts=probability.ts,
                publication_ts=(coherence.probability_ts if coherence is not None else None),
                components=probability.components,
            )
            if coherence_reason is None:
                coherence_reason = anchor_reason
            agreement_yes = ensemble_agreement(probability.components, TradeSide.BUY_YES)
            decision = evaluate_signal(
                SignalInputs(
                    model_probability=float(effective_probability),
                    yes_best_bid=float(price.yes_best_bid),
                    yes_best_ask=float(price.yes_best_ask),
                    yes_bid_depth_usd=float(price.yes_bid_depth_usd),
                    yes_ask_depth_usd=float(price.yes_ask_depth_usd),
                    no_best_bid=float(price.no_best_bid),
                    no_best_ask=float(price.no_best_ask),
                    no_bid_depth_usd=float(price.no_bid_depth_usd),
                    no_ask_depth_usd=float(price.no_ask_depth_usd),
                    horizon_h=float(probability.horizon_h),
                    market_created_at=market.created_at,
                    price_ts=price_seen_at,
                    probability_ts=probability.ts,
                    station_verified=market.station_verified,
                    ensemble_yes_agreement=agreement_yes,
                    bankroll_usd=bankroll_usd,
                    fee_rate=float(market.fee_rate) if market.fees_enabled else 0.0,
                    calibration_samples=_calibration_samples(probability.components),
                ),
                now,
                policy,
            )
            candidates.append(
                (
                    decision.edge,
                    market,
                    probability,
                    effective_probability,
                    price,
                    price_seen_at,
                    decision,
                    coherence_reason,
                    station,
                )
            )

        values = []
        accepted = 0
        for (
            _,
            market,
            probability,
            effective_probability,
            price,
            price_seen_at,
            decision,
            coherence_reason,
            station,
        ) in sorted(candidates, key=lambda item: item[0], reverse=True):
            reasons = list(decision.reasons) + global_reasons
            reliability_snapshot = station_reliability.get(station.id)
            reliability_evidence_valid = bool(
                reliability_snapshot is not None
                and station_reliability_record_is_valid(reliability_snapshot)
            )
            reasons.extend(
                ("station_reliability_evidence_invalid",)
                if reliability_snapshot is not None and not reliability_evidence_valid
                else station_signal_gate_reasons(
                    reliability_snapshot,
                    market_date=market.market_date,
                    now=now,
                    expected_policy_hash=reliability_policy_hash,
                    max_age=timedelta(
                        minutes=settings.station_reliability_snapshot_max_age_minutes
                    ),
                    observation_stale_after=timedelta(
                        minutes=settings.station_observation_stale_minutes
                    ),
                    station_timezone=station.tz,
                    require_reliable=mode is ExecutionMode.LIVE,
                )
            )
            if coherence_reason is not None:
                reasons.append(coherence_reason)
            if market.id in pending_market_ids:
                reasons.append("pending_signal_exists")
            market_remaining = (
                Decimal(str(settings.market_exposure_fraction)) * bankroll_amount
                - market_exposure[market.id]
            )
            station_key = (market.station_id, market.market_date)
            station_remaining = (
                Decimal(str(settings.station_day_exposure_fraction)) * bankroll_amount
                - station_day_exposure[station_key]
            )
            final_size = _risk_capped_order_size(
                decision.size_usd,
                market_remaining,
                station_remaining,
            )
            if market_remaining <= 0:
                reasons.append("market_exposure_limit")
            if station_remaining <= 0:
                reasons.append("station_day_exposure_limit")
            if final_size <= 0:
                reasons.append("risk_capped_size_below_quantum")

            # Liquidity must be checked against the final risk-capped order size.
            reasons = [reason for reason in reasons if reason != "insufficient_depth_3x"]
            selected_depth = (
                price.yes_ask_depth_usd
                if decision.side is TradeSide.BUY_YES
                else price.no_ask_depth_usd
            )
            if selected_depth < 3 * final_size:
                reasons.append("insufficient_depth_3x")

            status = SignalStatus.SKIPPED if reasons else SignalStatus.PENDING
            if status is SignalStatus.PENDING:
                accepted += 1
                market_exposure[market.id] += final_size
                station_day_exposure[station_key] += final_size
                pending_market_ids.add(market.id)
            values.append(
                SignalValue(
                    market_id=market.id,
                    ts=max(probability.ts, price_seen_at),
                    side=decision.side,
                    model_prob=effective_probability,
                    market_price=_decimal(decision.entry_price, "0.000001"),
                    edge=_decimal(decision.edge, "0.000001"),
                    kelly_frac=_decimal(float(final_size) / bankroll_usd, "0.000001"),
                    size_usd=final_size,
                    confidence=decision.confidence,
                    status=status,
                    skip_reason=";".join(dict.fromkeys(reasons)) if reasons else None,
                    clob_snapshot_id=price.id,
                    station_reliability_snapshot_id=(
                        reliability_snapshot.id
                        if reliability_snapshot is not None
                        and reliability_evidence_valid
                        else None
                    ),
                    token_id=traded_token_id(price, decision.side),
                    benchmark_protocol=CLOB_DUAL_PROTOCOL,
                )
            )

        async with session_factory.begin() as session:
            written = await insert_signals(
                session,
                values,
                skipped_heartbeat=timedelta(minutes=settings.skipped_signal_heartbeat_minutes),
            )
        logger.info(
            "signal generation complete: protocol=%s evaluated=%d inserted=%d "
            "pending=%d skipped=%d",
            CANDIDATE_ANCHOR_ADMISSION_PROTOCOL,
            len(candidates),
            written,
            accepted,
            len(candidates) - accepted,
        )
        return accepted, len(candidates) - accepted


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(generate_signals(settings))


if __name__ == "__main__":
    main()
