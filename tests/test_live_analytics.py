from datetime import UTC, datetime

import pytest

from stratus.services.live_analytics import (
    PositionStat,
    cumulative_equity,
    summarize_live_performance,
)


def test_cumulative_equity_runs_in_time_order() -> None:
    t1 = datetime(2026, 7, 1, tzinfo=UTC)
    t2 = datetime(2026, 7, 2, tzinfo=UTC)
    t3 = datetime(2026, 7, 3, tzinfo=UTC)
    # Deliberately out of order to prove it sorts by time.
    points = cumulative_equity([(t2, -5.0), (t1, 20.0), (t3, 10.0)])

    assert [(p.ts, p.cumulative_pnl_usd) for p in points] == [
        (t1, 20.0),
        (t2, 15.0),
        (t3, 25.0),
    ]


def test_cumulative_equity_empty() -> None:
    assert cumulative_equity([]) == []


def test_summary_over_mixed_open_and_closed_positions() -> None:
    positions = [
        # Closed winner, entered well above the close (positive CLV).
        PositionStat(closed=True, pnl_usd=20.0, clv=0.03, cost_usd=20.0, fee_usd=0.04),
        # Closed loser, but still beat the closing line.
        PositionStat(closed=True, pnl_usd=-20.0, clv=0.01, cost_usd=20.0, fee_usd=0.04),
        # Closed loser that entered worse than the close.
        PositionStat(closed=True, pnl_usd=-10.0, clv=-0.02, cost_usd=10.0, fee_usd=0.02),
        # Still open: excluded from realised stats but counted in totals.
        PositionStat(closed=False, pnl_usd=None, clv=None, cost_usd=15.0, fee_usd=0.03),
    ]

    perf = summarize_live_performance(positions)

    assert perf.positions == 4
    assert perf.closed == 3
    assert perf.open == 1
    assert perf.wins == 1
    assert perf.total_pnl_usd == -10.0
    assert perf.robust_pnl_usd == -30.0
    assert perf.total_cost_usd == 65.0
    assert perf.total_fees_usd == 0.13
    # ROI over closed cost only: -10 / 50.
    assert perf.roi == -0.2
    # Robust ROI removes the largest absolute PnL position (+20).
    assert perf.robust_roi == -1.0
    assert perf.win_rate == 1 / 3
    # avg CLV over the three closed positions with a value: (0.03+0.01-0.02)/3.
    assert perf.avg_clv is not None and abs(perf.avg_clv - 0.006667) < 1e-6
    # Two of three beat the closing line.
    assert perf.clv_beat_rate is not None and abs(perf.clv_beat_rate - 0.6667) < 1e-3


def test_summary_is_defined_with_no_data() -> None:
    perf = summarize_live_performance([])
    assert perf.positions == 0
    assert perf.roi is None
    assert perf.robust_roi is None
    assert perf.avg_clv is None
    assert perf.clv_beat_rate is None
    assert perf.win_rate is None


def test_open_only_positions_have_no_realised_stats() -> None:
    perf = summarize_live_performance(
        [PositionStat(closed=False, pnl_usd=None, clv=None, cost_usd=10.0, fee_usd=None)]
    )
    assert perf.open == 1
    assert perf.roi is None
    assert perf.robust_roi is None
    assert perf.total_cost_usd == 10.0


def test_robust_summary_exposes_single_trade_dependency() -> None:
    perf = summarize_live_performance(
        [
            PositionStat(
                closed=True, pnl_usd=100.0, clv=0.02, cost_usd=50.0, fee_usd=0.05
            ),
            PositionStat(
                closed=True, pnl_usd=-10.0, clv=-0.01, cost_usd=25.0, fee_usd=0.02
            ),
            PositionStat(
                closed=True, pnl_usd=-10.0, clv=-0.01, cost_usd=25.0, fee_usd=0.02
            ),
        ]
    )

    assert perf.total_pnl_usd == 80.0
    assert perf.roi == 0.8
    assert perf.robust_pnl_usd == -20.0
    assert perf.robust_roi == -0.4


def test_decompose_pnl_invariant_and_semantics() -> None:
    from stratus.services.live_analytics import decompose_pnl

    # 40 shares at 0.50 ($20 cost incl $0.10 fee -> avg covers $19.90),
    # close at 0.55 (clv +0.05), market resolved YES: pnl = 40 - 20 = 20.
    stat = PositionStat(
        closed=True,
        pnl_usd=20.0,
        clv=0.05,
        cost_usd=20.0,
        fee_usd=0.10,
        size_shares=40.0,
        avg_price=0.4975,
    )
    split = decompose_pnl(stat)
    assert split is not None
    skill, luck = split
    # skill = 40 * 0.05 - 0.10 = 1.90; luck = pnl - skill.
    assert skill == pytest.approx(1.90)
    assert luck == pytest.approx(18.10)
    assert skill + luck == pytest.approx(stat.pnl_usd)

    # Open, missing CLV or missing shares -> no decomposition.
    assert decompose_pnl(
        PositionStat(closed=False, pnl_usd=None, clv=None, cost_usd=1.0, fee_usd=None)
    ) is None
    assert decompose_pnl(
        PositionStat(closed=True, pnl_usd=5.0, clv=None, cost_usd=1.0, fee_usd=None)
    ) is None
    assert decompose_pnl(
        PositionStat(
            closed=True, pnl_usd=5.0, clv=0.01, cost_usd=1.0, fee_usd=None,
            size_shares=0.0,
        )
    ) is None


def test_summary_reports_skill_luck_and_deterministic_ci() -> None:
    positions = [
        PositionStat(
            closed=True, pnl_usd=8.0, clv=0.04, cost_usd=20.0, fee_usd=None,
            size_shares=40.0, avg_price=0.5,
        ),
        PositionStat(
            closed=True, pnl_usd=-20.0, clv=0.01, cost_usd=20.0, fee_usd=None,
            size_shares=40.0, avg_price=0.5,
        ),
        PositionStat(
            closed=True, pnl_usd=12.0, clv=-0.02, cost_usd=12.0, fee_usd=None,
            size_shares=30.0, avg_price=0.4,
        ),
    ]
    first = summarize_live_performance(positions)
    second = summarize_live_performance(positions)

    assert first.decomposed == 3
    # skill = 40*0.04 + 40*0.01 + 30*(-0.02) = 1.6 + 0.4 - 0.6 = 1.4
    assert first.skill_pnl_usd == pytest.approx(1.4)
    # Total pnl is 0, so luck = pnl - skill = -1.4 and the invariant holds.
    assert first.luck_pnl_usd == pytest.approx(-1.4)
    assert first.skill_pnl_usd + first.luck_pnl_usd == pytest.approx(0.0, abs=1e-6)
    # Bootstrap must be deterministic: identical inputs, identical CIs.
    assert (first.roi_ci_low, first.roi_ci_high) == (
        second.roi_ci_low,
        second.roi_ci_high,
    )
    assert first.roi_ci_low is not None and first.roi_ci_high is not None
    assert first.roi_ci_low <= first.roi_ci_high
    # Mixed-sign sample: zero stays inside the interval, so not significant.
    assert first.statistically_positive is False


def test_bootstrap_cis_are_none_for_tiny_samples() -> None:
    from stratus.services.live_analytics import bootstrap_ratio_ci, bootstrap_sum_ci

    assert bootstrap_sum_ci([]) is None
    assert bootstrap_sum_ci([1.0]) is None
    assert bootstrap_ratio_ci([(1.0, 2.0)]) is None
    ci = bootstrap_sum_ci([1.0, 1.0, 1.0])
    assert ci == (3.0, 3.0)


def test_brier_comparison_and_edge_calibration() -> None:
    from stratus.services.live_analytics import (
        TradeOutcome,
        brier_comparison,
        edge_calibration,
        edge_slope,
    )

    trades = [
        # Model was sharper than the market on both.
        TradeOutcome(
            model_prob=0.8, market_prob=0.6, won=True,
            pnl_usd=8.0, cost_usd=12.0, predicted_edge=0.20,
        ),
        TradeOutcome(
            model_prob=0.2, market_prob=0.4, won=False,
            pnl_usd=-4.0, cost_usd=8.0, predicted_edge=0.02,
        ),
        TradeOutcome(
            model_prob=0.7, market_prob=0.55, won=True,
            pnl_usd=4.5, cost_usd=11.0, predicted_edge=0.08,
        ),
    ]
    briers = brier_comparison(trades)
    assert briers is not None
    brier_model, brier_market = briers
    assert brier_model < brier_market
    assert brier_comparison([]) is None

    buckets = edge_calibration(trades)
    assert [b.count for b in buckets] == [1, 0, 1, 1]
    assert buckets[0].avg_realized_return == pytest.approx(-0.5)
    assert buckets[1].avg_realized_return is None

    slope = edge_slope(trades)
    assert slope is not None and slope > 0
    assert edge_slope(trades[:2]) is None
