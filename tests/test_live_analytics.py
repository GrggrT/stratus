from datetime import UTC, datetime

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
