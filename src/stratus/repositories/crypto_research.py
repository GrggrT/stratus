from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.backtesting.engine import BACKTEST_ENGINE_VERSION
from stratus.backtesting.research import (
    RESEARCH_PROTOCOL_VERSION,
    MovingAverageCandidateSpec,
    ResearchExecutionConfig,
    ResearchExperimentSpec,
    ResearchSelectionPolicy,
    canonical_payload_hash,
)
from stratus.backtesting.windows import (
    WalkForwardPlan,
    walk_forward_plan_from_payload,
)
from stratus.db.models import (
    CryptoResearchCandidate,
    CryptoResearchExperiment,
    CryptoResearchResult,
    CryptoSymbol,
    TradingVenue,
)

ResearchPhase = Literal["oos", "holdout"]
ResearchStressCase = Literal["baseline", "double_slippage"]


class ResearchLedgerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchExperimentRecord:
    id: int
    experiment_key: str
    name: str
    status: str
    symbols: tuple[str, ...]
    interval: str
    dataset_start: datetime
    dataset_end: datetime
    plan: WalkForwardPlan
    candidate_set_hash: str
    symbol_filters: dict[str, dict[str, str | None]]
    symbol_filters_hash: str
    execution: ResearchExecutionConfig
    selection: ResearchSelectionPolicy
    engine_version: str
    protocol_version: str
    selected_candidate_key: str | None
    holdout_unlocked_at: datetime | None
    decision: str | None
    decision_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchCandidateRecord:
    id: int
    experiment_id: int
    candidate_key: str
    strategy_name: str
    strategy_version: str
    spec: MovingAverageCandidateSpec


@dataclass(frozen=True, slots=True)
class ResearchResultValue:
    phase: ResearchPhase
    fold_number: int
    stress_case: ResearchStressCase
    window_start: datetime
    window_end: datetime
    run_hash: str
    data_hash: str
    config_hash: str
    event_count: int
    metrics: dict[str, Any]
    cycle_net_pnls: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class ResearchResultRecord:
    id: int
    experiment_id: int
    candidate_id: int
    candidate_key: str
    symbol_id: int
    symbol: str
    phase: ResearchPhase
    fold_number: int
    stress_case: ResearchStressCase
    window_start: datetime
    window_end: datetime
    run_hash: str
    data_hash: str
    config_hash: str
    event_count: int
    metrics: dict[str, Any]
    cycle_net_pnls: tuple[Decimal, ...]


async def register_research_experiment(
    session: AsyncSession,
    spec: ResearchExperimentSpec,
) -> ResearchExperimentRecord:
    existing = await session.scalar(
        select(CryptoResearchExperiment)
        .where(
            or_(
                CryptoResearchExperiment.experiment_key == spec.experiment_key,
                CryptoResearchExperiment.name == spec.name,
            )
        )
        .with_for_update()
    )
    if existing is not None:
        _require_registered_contract(existing, spec)
        current_filters = await research_symbol_filter_payloads(session, spec.symbols)
        if existing.symbol_filters != current_filters:
            raise ResearchLedgerError(
                "registered research symbol filters differ from current metadata"
            )
        await _require_registered_candidates(session, existing.id, spec.candidates)
        return _experiment_record(existing)

    symbol_filters = await research_symbol_filter_payloads(session, spec.symbols)
    plan = spec.plan
    row = CryptoResearchExperiment(
        experiment_key=spec.experiment_key,
        name=spec.name,
        status="registered",
        symbols=list(spec.symbols),
        interval=spec.interval,
        dataset_start=spec.dataset_start,
        dataset_end=spec.dataset_end,
        holdout_start=plan.final_holdout.start,
        holdout_end=plan.final_holdout.end,
        plan=spec.plan_payload(),
        plan_hash=plan.plan_hash,
        candidate_set_hash=spec.candidate_set_hash,
        symbol_filters=symbol_filters,
        symbol_filters_hash=canonical_payload_hash(symbol_filters),
        execution_config=spec.execution.to_payload(),
        execution_config_hash=spec.execution.canonical_hash(),
        selection_policy=spec.selection.to_payload(),
        engine_version=BACKTEST_ENGINE_VERSION,
        protocol_version=RESEARCH_PROTOCOL_VERSION,
        selected_candidate_key=None,
        decision=None,
        decision_reasons=[],
    )
    session.add(row)
    await session.flush()
    for candidate in sorted(spec.candidates, key=lambda item: item.candidate_key):
        session.add(
            CryptoResearchCandidate(
                experiment_id=row.id,
                candidate_key=candidate.candidate_key,
                strategy_name="moving_average_cross",
                strategy_version=candidate.strategy_version,
                parameters=candidate.to_payload(),
            )
        )
    await session.flush()
    return _experiment_record(row)


async def get_research_experiment(
    session: AsyncSession,
    reference: str,
    *,
    for_update: bool = False,
) -> ResearchExperimentRecord:
    statement = select(CryptoResearchExperiment).where(
        or_(
            CryptoResearchExperiment.experiment_key == reference,
            CryptoResearchExperiment.name == reference,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise ResearchLedgerError(f"research experiment not found: {reference}")
    return _experiment_record(row)


async def list_research_candidates(
    session: AsyncSession,
    experiment_id: int,
) -> tuple[ResearchCandidateRecord, ...]:
    rows = (
        await session.scalars(
            select(CryptoResearchCandidate)
            .where(CryptoResearchCandidate.experiment_id == experiment_id)
            .order_by(CryptoResearchCandidate.candidate_key)
        )
    ).all()
    return tuple(_candidate_record(row) for row in rows)


async def list_research_results(
    session: AsyncSession,
    experiment_id: int,
    *,
    phase: ResearchPhase | None = None,
) -> tuple[ResearchResultRecord, ...]:
    statement = (
        select(CryptoResearchResult, CryptoResearchCandidate.candidate_key, CryptoSymbol.symbol)
        .join(
            CryptoResearchCandidate,
            CryptoResearchCandidate.id == CryptoResearchResult.candidate_id,
        )
        .join(CryptoSymbol, CryptoSymbol.id == CryptoResearchResult.symbol_id)
        .where(CryptoResearchResult.experiment_id == experiment_id)
    )
    if phase is not None:
        statement = statement.where(CryptoResearchResult.phase == phase)
    statement = statement.order_by(
        CryptoResearchResult.phase,
        CryptoResearchResult.fold_number,
        CryptoResearchCandidate.candidate_key,
        CryptoSymbol.symbol,
        CryptoResearchResult.stress_case,
    )
    rows = (await session.execute(statement)).all()
    return tuple(_result_record(row[0], candidate_key=row[1], symbol=row[2]) for row in rows)


async def start_research_oos(
    session: AsyncSession,
    reference: str,
) -> ResearchExperimentRecord:
    row = await _locked_experiment(session, reference)
    if row.status == "registered":
        row.status = "oos_running"
        row.updated_at = datetime.now(UTC)
        await session.flush()
    elif row.status != "oos_running":
        raise ResearchLedgerError(f"cannot start OOS from research status {row.status}")
    return _experiment_record(row)


async def append_research_result(
    session: AsyncSession,
    *,
    reference: str,
    candidate_key: str,
    symbol: str,
    value: ResearchResultValue,
) -> ResearchResultRecord:
    _validate_result_value(value)
    experiment = await _locked_experiment(session, reference)
    plan = _verified_plan(experiment)
    candidate = await session.scalar(
        select(CryptoResearchCandidate).where(
            CryptoResearchCandidate.experiment_id == experiment.id,
            CryptoResearchCandidate.candidate_key == candidate_key,
        )
    )
    if candidate is None:
        raise ResearchLedgerError("research result candidate is not registered")
    if symbol not in experiment.symbols:
        raise ResearchLedgerError("research result symbol is outside the contract")
    symbol_row = await session.scalar(
        select(CryptoSymbol).where(
            CryptoSymbol.venue == TradingVenue.BINANCE_SPOT,
            CryptoSymbol.symbol == symbol,
        )
    )
    if symbol_row is None:
        raise ResearchLedgerError("research result symbol is not registered")

    _require_result_window(experiment, plan, candidate_key, value)
    existing = await session.scalar(
        select(CryptoResearchResult).where(
            CryptoResearchResult.experiment_id == experiment.id,
            CryptoResearchResult.candidate_id == candidate.id,
            CryptoResearchResult.symbol_id == symbol_row.id,
            CryptoResearchResult.phase == value.phase,
            CryptoResearchResult.fold_number == value.fold_number,
            CryptoResearchResult.stress_case == value.stress_case,
        )
    )
    if existing is not None:
        _require_result_matches(existing, value)
        return _result_record(existing, candidate_key=candidate_key, symbol=symbol)

    allowed_status = "oos_running" if value.phase == "oos" else "holdout_running"
    if experiment.status != allowed_status:
        raise ResearchLedgerError(
            f"cannot append {value.phase} result from research status {experiment.status}"
        )
    row = CryptoResearchResult(
        experiment_id=experiment.id,
        candidate_id=candidate.id,
        symbol_id=symbol_row.id,
        phase=value.phase,
        fold_number=value.fold_number,
        stress_case=value.stress_case,
        window_start=value.window_start,
        window_end=value.window_end,
        run_hash=value.run_hash,
        data_hash=value.data_hash,
        config_hash=value.config_hash,
        event_count=value.event_count,
        metrics=value.metrics,
        cycle_net_pnls=[str(item) for item in value.cycle_net_pnls],
    )
    session.add(row)
    await session.flush()
    return _result_record(row, candidate_key=candidate_key, symbol=symbol)


async def complete_research_oos(
    session: AsyncSession,
    reference: str,
    *,
    selected_candidate_key: str | None,
    no_go_reasons: tuple[str, ...] = (),
) -> ResearchExperimentRecord:
    row = await _locked_experiment(session, reference)
    if row.status == "oos_complete" and row.selected_candidate_key == selected_candidate_key:
        return _experiment_record(row)
    if row.status == "complete" and selected_candidate_key is None and row.decision == "NO_GO":
        return _experiment_record(row)
    if row.status != "oos_running":
        raise ResearchLedgerError(f"cannot complete OOS from research status {row.status}")
    await _require_complete_result_set(session, row, phase="oos")
    now = datetime.now(UTC)
    if selected_candidate_key is None:
        if not no_go_reasons:
            raise ResearchLedgerError("OOS NO_GO requires at least one reason")
        row.status = "complete"
        row.decision = "NO_GO"
        row.decision_reasons = list(no_go_reasons)
        row.completed_at = now
    else:
        await _require_candidate_key(session, row.id, selected_candidate_key)
        if no_go_reasons:
            raise ResearchLedgerError("selected OOS candidate cannot include NO_GO reasons")
        row.selected_candidate_key = selected_candidate_key
        row.status = "oos_complete"
    row.updated_at = now
    await session.flush()
    return _experiment_record(row)


async def unlock_research_holdout(
    session: AsyncSession,
    reference: str,
    *,
    plan_hash: str,
    candidate_set_hash: str,
) -> ResearchExperimentRecord:
    row = await _locked_experiment(session, reference)
    if plan_hash != row.plan_hash or candidate_set_hash != row.candidate_set_hash:
        raise ResearchLedgerError("holdout unlock hashes do not match registered contract")
    if row.status == "holdout_unlocked":
        return _experiment_record(row)
    if row.status != "oos_complete" or row.selected_candidate_key is None:
        raise ResearchLedgerError(f"cannot unlock holdout from research status {row.status}")
    holdout_count = await session.scalar(
        select(func.count(CryptoResearchResult.id)).where(
            CryptoResearchResult.experiment_id == row.id,
            CryptoResearchResult.phase == "holdout",
        )
    )
    if holdout_count:
        raise ResearchLedgerError("holdout results exist before explicit unlock")
    now = datetime.now(UTC)
    row.status = "holdout_unlocked"
    row.holdout_unlocked_at = now
    row.updated_at = now
    await session.flush()
    return _experiment_record(row)


async def start_research_holdout(
    session: AsyncSession,
    reference: str,
) -> ResearchExperimentRecord:
    row = await _locked_experiment(session, reference)
    if row.status == "holdout_unlocked":
        row.status = "holdout_running"
        row.updated_at = datetime.now(UTC)
        await session.flush()
    elif row.status != "holdout_running":
        raise ResearchLedgerError(f"cannot start holdout from research status {row.status}")
    return _experiment_record(row)


async def complete_research_holdout(
    session: AsyncSession,
    reference: str,
    *,
    decision: Literal["GO", "NO_GO"],
    reasons: tuple[str, ...],
) -> ResearchExperimentRecord:
    row = await _locked_experiment(session, reference)
    if row.status == "complete":
        if row.decision != decision or tuple(row.decision_reasons) != reasons:
            raise ResearchLedgerError("completed holdout decision is immutable")
        return _experiment_record(row)
    if row.status != "holdout_running":
        raise ResearchLedgerError(f"cannot complete holdout from research status {row.status}")
    if decision == "GO" and reasons:
        raise ResearchLedgerError("GO decision cannot include failure reasons")
    if decision == "NO_GO" and not reasons:
        raise ResearchLedgerError("NO_GO decision requires failure reasons")
    await _require_complete_result_set(session, row, phase="holdout")
    now = datetime.now(UTC)
    row.status = "complete"
    row.decision = decision
    row.decision_reasons = list(reasons)
    row.completed_at = now
    row.updated_at = now
    await session.flush()
    return _experiment_record(row)


async def _locked_experiment(
    session: AsyncSession,
    reference: str,
) -> CryptoResearchExperiment:
    row = await session.scalar(
        select(CryptoResearchExperiment)
        .where(
            or_(
                CryptoResearchExperiment.experiment_key == reference,
                CryptoResearchExperiment.name == reference,
            )
        )
        .with_for_update()
    )
    if row is None:
        raise ResearchLedgerError(f"research experiment not found: {reference}")
    return row


async def research_symbol_filter_payloads(
    session: AsyncSession,
    symbols: tuple[str, ...],
) -> dict[str, dict[str, str | None]]:
    rows = (
        await session.scalars(
            select(CryptoSymbol).where(
                CryptoSymbol.venue == TradingVenue.BINANCE_SPOT,
                CryptoSymbol.symbol.in_(symbols),
            )
        )
    ).all()
    found = {row.symbol: row for row in rows}
    missing = sorted(set(symbols) - set(found))
    if missing:
        raise ResearchLedgerError("research symbols are not registered: " + ", ".join(missing))
    disabled = sorted(
        symbol
        for symbol in symbols
        if found[symbol].status != "TRADING" or not found[symbol].enabled
    )
    if disabled:
        raise ResearchLedgerError("research symbols are not enabled: " + ", ".join(disabled))
    payloads: dict[str, dict[str, str | None]] = {}
    for symbol in symbols:
        row = found[symbol]
        required = (
            row.price_tick,
            row.quantity_step,
            row.minimum_quantity,
            row.minimum_notional,
        )
        if any(value is None for value in required):
            raise ResearchLedgerError(f"research symbol filters are incomplete: {symbol}")
        payloads[symbol] = {
            "filter_hash": row.filter_hash,
            "price_tick": _decimal_text(row.price_tick),
            "quantity_step": _decimal_text(row.quantity_step),
            "minimum_quantity": _decimal_text(row.minimum_quantity),
            "minimum_notional": _decimal_text(row.minimum_notional),
            "maximum_notional": _decimal_text(row.maximum_notional),
        }
    return payloads


async def _require_registered_candidates(
    session: AsyncSession,
    experiment_id: int,
    expected: tuple[MovingAverageCandidateSpec, ...],
) -> None:
    actual = await list_research_candidates(session, experiment_id)
    expected_by_key = {item.candidate_key: item for item in expected}
    if set(item.candidate_key for item in actual) != set(expected_by_key):
        raise ResearchLedgerError("registered research candidate set is inconsistent")
    for item in actual:
        expected_item = expected_by_key[item.candidate_key]
        if item.spec != expected_item or item.strategy_version != expected_item.strategy_version:
            raise ResearchLedgerError("registered research candidate payload is inconsistent")


def _require_registered_contract(
    row: CryptoResearchExperiment,
    spec: ResearchExperimentSpec,
) -> None:
    plan = spec.plan
    expected: dict[str, object] = {
        "experiment_key": spec.experiment_key,
        "name": spec.name,
        "symbols": list(spec.symbols),
        "interval": spec.interval,
        "dataset_start": _utc(spec.dataset_start),
        "dataset_end": _utc(spec.dataset_end),
        "holdout_start": _utc(plan.final_holdout.start),
        "holdout_end": _utc(plan.final_holdout.end),
        "plan": spec.plan_payload(),
        "plan_hash": plan.plan_hash,
        "candidate_set_hash": spec.candidate_set_hash,
        "execution_config": spec.execution.to_payload(),
        "execution_config_hash": spec.execution.canonical_hash(),
        "selection_policy": spec.selection.to_payload(),
        "engine_version": BACKTEST_ENGINE_VERSION,
        "protocol_version": RESEARCH_PROTOCOL_VERSION,
    }
    for field, expected_value in expected.items():
        actual = getattr(row, field)
        if isinstance(actual, datetime):
            actual = _utc(actual)
        if actual != expected_value:
            raise ResearchLedgerError(f"research name/key is bound to a different {field}")


def _verified_plan(row: CryptoResearchExperiment) -> WalkForwardPlan:
    plan = walk_forward_plan_from_payload(row.plan)
    if plan.plan_hash != row.plan_hash:
        raise ResearchLedgerError("stored research plan hash is inconsistent")
    if _utc(plan.final_holdout.start) != _utc(row.holdout_start):
        raise ResearchLedgerError("stored research holdout start is inconsistent")
    if _utc(plan.final_holdout.end) != _utc(row.holdout_end):
        raise ResearchLedgerError("stored research holdout end is inconsistent")
    if _utc(plan.folds[0].train.start) != _utc(row.dataset_start):
        raise ResearchLedgerError("stored research dataset start is inconsistent")
    if _utc(plan.final_holdout.end) != _utc(row.dataset_end):
        raise ResearchLedgerError("stored research dataset end is inconsistent")
    return plan


def _experiment_record(row: CryptoResearchExperiment) -> ResearchExperimentRecord:
    plan = _verified_plan(row)
    execution = ResearchExecutionConfig.from_payload(row.execution_config)
    if execution.canonical_hash() != row.execution_config_hash:
        raise ResearchLedgerError("stored research execution hash is inconsistent")
    selection = ResearchSelectionPolicy.from_payload(row.selection_policy)
    if row.engine_version != BACKTEST_ENGINE_VERSION:
        raise ResearchLedgerError("stored research engine version is unsupported")
    if row.protocol_version != RESEARCH_PROTOCOL_VERSION:
        raise ResearchLedgerError("stored research protocol version is unsupported")
    symbol_filters = _validated_symbol_filters(row.symbol_filters, row.symbols)
    if canonical_payload_hash(symbol_filters) != row.symbol_filters_hash:
        raise ResearchLedgerError("stored research symbol filter hash is inconsistent")
    if not isinstance(row.symbols, list) or not all(
        isinstance(symbol, str) for symbol in row.symbols
    ):
        raise ResearchLedgerError("stored research symbols are invalid")
    if not isinstance(row.decision_reasons, list) or not all(
        isinstance(reason, str) for reason in row.decision_reasons
    ):
        raise ResearchLedgerError("stored research decision reasons are invalid")
    return ResearchExperimentRecord(
        id=row.id,
        experiment_key=row.experiment_key,
        name=row.name,
        status=row.status,
        symbols=tuple(row.symbols),
        interval=row.interval,
        dataset_start=row.dataset_start,
        dataset_end=row.dataset_end,
        plan=plan,
        candidate_set_hash=row.candidate_set_hash,
        symbol_filters=symbol_filters,
        symbol_filters_hash=row.symbol_filters_hash,
        execution=execution,
        selection=selection,
        engine_version=row.engine_version,
        protocol_version=row.protocol_version,
        selected_candidate_key=row.selected_candidate_key,
        holdout_unlocked_at=row.holdout_unlocked_at,
        decision=row.decision,
        decision_reasons=tuple(row.decision_reasons),
    )


def _candidate_record(row: CryptoResearchCandidate) -> ResearchCandidateRecord:
    spec = MovingAverageCandidateSpec.from_payload(row.parameters)
    if row.candidate_key != spec.candidate_key:
        raise ResearchLedgerError("stored research candidate hash is inconsistent")
    if row.strategy_name != "moving_average_cross":
        raise ResearchLedgerError("stored research strategy name is unsupported")
    if row.strategy_version != spec.strategy_version:
        raise ResearchLedgerError("stored research strategy version is inconsistent")
    return ResearchCandidateRecord(
        id=row.id,
        experiment_id=row.experiment_id,
        candidate_key=row.candidate_key,
        strategy_name=row.strategy_name,
        strategy_version=row.strategy_version,
        spec=spec,
    )


def _result_record(
    row: CryptoResearchResult,
    *,
    candidate_key: str,
    symbol: str,
) -> ResearchResultRecord:
    if row.phase not in ("oos", "holdout"):
        raise ResearchLedgerError("stored research result phase is invalid")
    if row.stress_case not in ("baseline", "double_slippage"):
        raise ResearchLedgerError("stored research result stress case is invalid")
    try:
        cycle_pnls = tuple(Decimal(value) for value in row.cycle_net_pnls)
    except (InvalidOperation, TypeError) as exc:
        raise ResearchLedgerError("stored research cycle PnLs are invalid") from exc
    if any(not value.is_finite() for value in cycle_pnls):
        raise ResearchLedgerError("stored research cycle PnLs must be finite")
    return ResearchResultRecord(
        id=row.id,
        experiment_id=row.experiment_id,
        candidate_id=row.candidate_id,
        candidate_key=candidate_key,
        symbol_id=row.symbol_id,
        symbol=symbol,
        phase=cast(ResearchPhase, row.phase),
        fold_number=row.fold_number,
        stress_case=cast(ResearchStressCase, row.stress_case),
        window_start=row.window_start,
        window_end=row.window_end,
        run_hash=row.run_hash,
        data_hash=row.data_hash,
        config_hash=row.config_hash,
        event_count=row.event_count,
        metrics=row.metrics,
        cycle_net_pnls=cycle_pnls,
    )


def _validate_result_value(value: ResearchResultValue) -> None:
    if value.phase not in ("oos", "holdout"):
        raise ResearchLedgerError("research result phase is invalid")
    if value.stress_case not in ("baseline", "double_slippage"):
        raise ResearchLedgerError("research result stress case is invalid")
    if value.fold_number < 0 or value.event_count <= 0:
        raise ResearchLedgerError("research result counters are invalid")
    if value.window_start.tzinfo is None or value.window_start.utcoffset() is None:
        raise ResearchLedgerError("research result window start must be timezone-aware")
    if value.window_end.tzinfo is None or value.window_end.utcoffset() is None:
        raise ResearchLedgerError("research result window end must be timezone-aware")
    if value.window_start >= value.window_end:
        raise ResearchLedgerError("research result window is invalid")
    for name, digest in (
        ("run_hash", value.run_hash),
        ("data_hash", value.data_hash),
        ("config_hash", value.config_hash),
    ):
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ResearchLedgerError(f"research result {name} is invalid")
    if any(not item.is_finite() for item in value.cycle_net_pnls):
        raise ResearchLedgerError("research result cycle PnLs must be finite")
    try:
        canonical_payload_hash(value.metrics)
    except (TypeError, UnicodeEncodeError) as exc:
        raise ResearchLedgerError("research result metrics are not canonical JSON") from exc


def _require_result_window(
    experiment: CryptoResearchExperiment,
    plan: WalkForwardPlan,
    candidate_key: str,
    value: ResearchResultValue,
) -> None:
    if value.phase == "holdout":
        if value.fold_number != 0:
            raise ResearchLedgerError("holdout research result must use fold zero")
        if candidate_key != experiment.selected_candidate_key:
            raise ResearchLedgerError("holdout research result must use selected candidate")
        expected = plan.final_holdout
    else:
        if value.fold_number < 1 or value.fold_number > len(plan.folds):
            raise ResearchLedgerError("OOS research fold is outside the contract")
        expected = plan.folds[value.fold_number - 1].test
    if _utc(value.window_start) != _utc(expected.start):
        raise ResearchLedgerError("research result window start differs from contract")
    if _utc(value.window_end) != _utc(expected.end):
        raise ResearchLedgerError("research result window end differs from contract")


def _require_result_matches(row: CryptoResearchResult, value: ResearchResultValue) -> None:
    expected: dict[str, object] = {
        "phase": value.phase,
        "fold_number": value.fold_number,
        "stress_case": value.stress_case,
        "window_start": _utc(value.window_start),
        "window_end": _utc(value.window_end),
        "run_hash": value.run_hash,
        "data_hash": value.data_hash,
        "config_hash": value.config_hash,
        "event_count": value.event_count,
        "metrics": value.metrics,
        "cycle_net_pnls": [str(item) for item in value.cycle_net_pnls],
    }
    for field, expected_value in expected.items():
        actual = getattr(row, field)
        if isinstance(actual, datetime):
            actual = _utc(actual)
        if actual != expected_value:
            raise ResearchLedgerError(f"research result identity has conflicting {field}")


async def _require_candidate_key(
    session: AsyncSession,
    experiment_id: int,
    candidate_key: str,
) -> None:
    candidate_id = await session.scalar(
        select(CryptoResearchCandidate.id).where(
            CryptoResearchCandidate.experiment_id == experiment_id,
            CryptoResearchCandidate.candidate_key == candidate_key,
        )
    )
    if candidate_id is None:
        raise ResearchLedgerError("selected research candidate is not registered")


async def _require_complete_result_set(
    session: AsyncSession,
    row: CryptoResearchExperiment,
    *,
    phase: ResearchPhase,
) -> None:
    plan = _verified_plan(row)
    candidate_count = await session.scalar(
        select(func.count(CryptoResearchCandidate.id)).where(
            CryptoResearchCandidate.experiment_id == row.id
        )
    )
    if not candidate_count:
        raise ResearchLedgerError("research candidate set is empty")
    candidate_multiplier = candidate_count if phase == "oos" else 1
    fold_count = len(plan.folds) if phase == "oos" else 1
    expected = candidate_multiplier * len(row.symbols) * fold_count * 2
    actual = await session.scalar(
        select(func.count(CryptoResearchResult.id)).where(
            CryptoResearchResult.experiment_id == row.id,
            CryptoResearchResult.phase == phase,
        )
    )
    if actual != expected:
        raise ResearchLedgerError(
            f"research {phase} result set is incomplete: expected={expected} actual={actual}"
        )


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not value.is_finite():
        raise ResearchLedgerError("research symbol filter must be finite")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _validated_symbol_filters(
    payload: dict[str, Any],
    symbols: list[str],
) -> dict[str, dict[str, str | None]]:
    if set(payload) != set(symbols):
        raise ResearchLedgerError("stored research symbol filter set is inconsistent")
    expected_fields = {
        "filter_hash",
        "price_tick",
        "quantity_step",
        "minimum_quantity",
        "minimum_notional",
        "maximum_notional",
    }
    validated: dict[str, dict[str, str | None]] = {}
    for symbol in symbols:
        values = payload[symbol]
        if not isinstance(values, dict) or set(values) != expected_fields:
            raise ResearchLedgerError("stored research symbol filter payload is invalid")
        filter_hash = values["filter_hash"]
        if not isinstance(filter_hash, str) or len(filter_hash) != 64:
            raise ResearchLedgerError("stored research exchange filter hash is invalid")
        item: dict[str, str | None] = {"filter_hash": filter_hash}
        for field in expected_fields - {"filter_hash"}:
            value = values[field]
            if value is None and field == "maximum_notional":
                item[field] = None
                continue
            if not isinstance(value, str):
                raise ResearchLedgerError("stored research numeric filter is invalid")
            try:
                decimal_value = Decimal(value)
            except InvalidOperation as exc:
                raise ResearchLedgerError("stored research numeric filter is invalid") from exc
            if not decimal_value.is_finite() or decimal_value <= 0:
                raise ResearchLedgerError("stored research numeric filter must be positive")
            if _decimal_text(decimal_value) != value:
                raise ResearchLedgerError("stored research numeric filter is not canonical")
            item[field] = value
        validated[symbol] = item
    return validated
