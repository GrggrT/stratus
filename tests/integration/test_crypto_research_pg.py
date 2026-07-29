from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.backtesting.research import (
    MovingAverageCandidateSpec,
    ResearchExecutionConfig,
    ResearchExperimentSpec,
    ResearchSelectionPolicy,
)
from stratus.db.models import CryptoResearchResult, CryptoSymbol, TradingVenue
from stratus.repositories.crypto_research import (
    ResearchLedgerError,
    ResearchResultValue,
    append_research_result,
    complete_research_holdout,
    complete_research_oos,
    list_research_results,
    register_research_experiment,
    start_research_holdout,
    start_research_oos,
    unlock_research_holdout,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
START = datetime(2024, 1, 1, tzinfo=UTC)
END = START + timedelta(days=60)
SYMBOL = "BTCUSDT"


def _spec() -> ResearchExperimentSpec:
    return ResearchExperimentSpec(
        name="binance-research-ledger-test",
        symbols=(SYMBOL,),
        interval="5m",
        dataset_start=START,
        dataset_end=END,
        minimum_train=timedelta(days=30),
        validation=timedelta(days=10),
        test=timedelta(days=10),
        step=timedelta(days=10),
        final_holdout=timedelta(days=10),
        execution=ResearchExecutionConfig(
            initial_quote_per_symbol=Decimal("1000"),
            order_notional=Decimal("100"),
            maker_fee_bps=Decimal("1"),
            taker_fee_bps=Decimal("10"),
            entry_slippage_bps=Decimal("5"),
            exit_slippage_bps=Decimal("5"),
            latency_ms=250,
            order_ttl_seconds=20,
            maker_participation_rate=Decimal("0.05"),
        ),
        selection=ResearchSelectionPolicy(
            bootstrap_samples=100,
            minimum_oos_cycles=1,
            minimum_symbol_cycles=1,
            minimum_positive_neighbors=0,
        ),
        candidates=(MovingAverageCandidateSpec(12, 48),),
    )


def _result(
    *,
    phase: str,
    fold_number: int,
    start: datetime,
    end: datetime,
    stress: str,
) -> ResearchResultValue:
    suffix = "1" if stress == "baseline" else "2"
    return ResearchResultValue(
        phase=phase,  # type: ignore[arg-type]
        fold_number=fold_number,
        stress_case=stress,  # type: ignore[arg-type]
        window_start=start,
        window_end=end,
        run_hash=suffix * 64,
        data_hash="a" * 64,
        config_hash=("b" if stress == "baseline" else "c") * 64,
        event_count=100,
        metrics={
            "completed_cycles": 1,
            "profitable_cycles": 1,
            "net_pnl": "1",
            "gross_profit": "1",
            "gross_loss": "0",
            "profit_factor": None,
            "win_rate": "1",
            "mean_net_expectancy": "1",
            "bootstrap_lcb_95_expectancy": "1",
            "maximum_drawdown": "0.01",
            "annualized_daily_sharpe": "2",
            "calmar_ratio": "2",
            "pnl_without_top_five_cycles": "0",
            "top_five_profit_contribution": "1",
        },
        cycle_net_pnls=(Decimal("1"),),
    )


async def _register_symbol(session: AsyncSession) -> None:
    session.add(
        CryptoSymbol(
            venue=TradingVenue.BINANCE_SPOT,
            symbol=SYMBOL,
            base_asset="BTC",
            quote_asset="USDT",
            status="TRADING",
            enabled=True,
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.00001"),
            minimum_quantity=Decimal("0.00001"),
            minimum_notional=Decimal("5"),
            maximum_notional=Decimal("9000000"),
            supports_oco=True,
            supports_oto=True,
            filters={},
            filter_hash="f" * 64,
            fetched_at=datetime.now(UTC),
        )
    )


async def test_research_ledger_is_idempotent_append_only_and_holdout_locked(
    db_sessions: SessionFactory,
) -> None:
    spec = _spec()
    candidate_key = spec.candidates[0].candidate_key
    fold = spec.plan.folds[0]
    holdout = spec.plan.final_holdout

    async with db_sessions.begin() as session:
        await _register_symbol(session)
        registered = await register_research_experiment(session, spec)
        repeated = await register_research_experiment(session, spec)
        assert repeated.id == registered.id

        conflicting = replace(
            spec,
            execution=replace(spec.execution, order_notional=Decimal("200")),
        )
        with pytest.raises(ResearchLedgerError, match="different experiment_key"):
            await register_research_experiment(session, conflicting)

        await start_research_oos(session, spec.experiment_key)
        with pytest.raises(ResearchLedgerError, match="selected candidate"):
            await append_research_result(
                session,
                reference=spec.experiment_key,
                candidate_key=candidate_key,
                symbol=SYMBOL,
                value=_result(
                    phase="holdout",
                    fold_number=0,
                    start=holdout.start,
                    end=holdout.end,
                    stress="baseline",
                ),
            )

        baseline = _result(
            phase="oos",
            fold_number=1,
            start=fold.test.start,
            end=fold.test.end,
            stress="baseline",
        )
        first = await append_research_result(
            session,
            reference=spec.experiment_key,
            candidate_key=candidate_key,
            symbol=SYMBOL,
            value=baseline,
        )
        repeated_result = await append_research_result(
            session,
            reference=spec.experiment_key,
            candidate_key=candidate_key,
            symbol=SYMBOL,
            value=baseline,
        )
        assert repeated_result.id == first.id
        with pytest.raises(ResearchLedgerError, match="conflicting event_count"):
            await append_research_result(
                session,
                reference=spec.experiment_key,
                candidate_key=candidate_key,
                symbol=SYMBOL,
                value=replace(baseline, event_count=101),
            )
        with pytest.raises(ResearchLedgerError, match="incomplete"):
            await complete_research_oos(
                session,
                spec.experiment_key,
                selected_candidate_key=candidate_key,
            )

        await append_research_result(
            session,
            reference=spec.experiment_key,
            candidate_key=candidate_key,
            symbol=SYMBOL,
            value=_result(
                phase="oos",
                fold_number=1,
                start=fold.test.start,
                end=fold.test.end,
                stress="double_slippage",
            ),
        )
        selected = await complete_research_oos(
            session,
            spec.experiment_key,
            selected_candidate_key=candidate_key,
        )
        assert selected.status == "oos_complete"

        with pytest.raises(ResearchLedgerError, match="hashes do not match"):
            await unlock_research_holdout(
                session,
                spec.experiment_key,
                plan_hash="0" * 64,
                candidate_set_hash=spec.candidate_set_hash,
            )
        await unlock_research_holdout(
            session,
            spec.experiment_key,
            plan_hash=spec.plan.plan_hash,
            candidate_set_hash=spec.candidate_set_hash,
        )
        await start_research_holdout(session, spec.experiment_key)
        for stress in ("baseline", "double_slippage"):
            await append_research_result(
                session,
                reference=spec.experiment_key,
                candidate_key=candidate_key,
                symbol=SYMBOL,
                value=_result(
                    phase="holdout",
                    fold_number=0,
                    start=holdout.start,
                    end=holdout.end,
                    stress=stress,
                ),
            )
        completed = await complete_research_holdout(
            session,
            spec.experiment_key,
            decision="NO_GO",
            reasons=("test_gate_failure",),
        )
        assert completed.status == "complete"
        assert completed.decision == "NO_GO"
        assert len(await list_research_results(session, registered.id)) == 4

    with pytest.raises(DBAPIError, match="append-only"):
        async with db_sessions.begin() as session:
            await session.execute(update(CryptoResearchResult).values(event_count=999))
