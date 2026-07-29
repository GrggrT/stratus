from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from stratus.services.live_analytics import (
    PositionStat,
    ProspectiveValidationTrade,
    cumulative_equity,
    prospective_clv_outcomes,
    prospective_position_stats,
    prospective_trade_outcomes,
    summarize_live_performance,
    summarize_prospective_validation,
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


def test_prospective_validation_groups_blocks_and_tracks_drawdown() -> None:
    times = [datetime(2026, 7, day, tzinfo=UTC) for day in (1, 2, 3)]
    trades = [
        ProspectiveValidationTrade(
            enrollment_id=2,
            order_id=102,
            pipeline_cohort="cohort-a",
            station_icao="KLGA",
            market_date=date(2026, 7, 1),
            kind="high",
            recognized_at=times[0],
            pnl_usd=20.0,
            cost_usd=20.0,
            skill_pnl_usd=2.0,
            luck_pnl_usd=18.0,
        ),
        # Same independent block: counted as a second order, not a sample.
        ProspectiveValidationTrade(
            enrollment_id=1,
            order_id=101,
            pipeline_cohort="cohort-a",
            station_icao="KLGA",
            market_date=date(2026, 7, 1),
            kind="high",
            recognized_at=times[0],
            pnl_usd=5.0,
            cost_usd=10.0,
            skill_pnl_usd=1.0,
            luck_pnl_usd=4.0,
        ),
        ProspectiveValidationTrade(
            enrollment_id=3,
            order_id=103,
            pipeline_cohort="cohort-a",
            station_icao="EGLC",
            market_date=date(2026, 7, 2),
            kind="low",
            recognized_at=times[1],
            pnl_usd=-40.0,
            cost_usd=30.0,
            skill_pnl_usd=-3.0,
            luck_pnl_usd=-37.0,
        ),
        ProspectiveValidationTrade(
            enrollment_id=4,
            order_id=104,
            pipeline_cohort="cohort-a",
            station_icao="LFPB",
            market_date=date(2026, 7, 3),
            kind="high",
            recognized_at=times[2],
            pnl_usd=10.0,
            cost_usd=20.0,
            skill_pnl_usd=None,
            luck_pnl_usd=None,
        ),
    ]

    summary = summarize_prospective_validation(trades, initial_bankroll_usd=100.0)

    assert summary.closed_orders == 4
    assert summary.independent_blocks == 3
    assert summary.total_pnl_usd == -5.0
    assert summary.roi == pytest.approx(-5 / 80)
    assert summary.skill_pnl_usd == 0.0
    assert summary.luck_pnl_usd == -15.0
    assert summary.decomposed_orders == 3
    assert summary.max_drawdown_usd == 40.0
    assert summary.max_drawdown_fraction == 0.4
    assert summary.largest_order_id == 103
    assert summary.robust_pnl_usd == 35.0
    assert summary.robust_roi == pytest.approx(0.7)
    assert [point.station_icao for point in summary.points] == ["KLGA", "EGLC", "LFPB"]
    assert [point.orders for point in summary.points] == [2, 1, 1]
    assert [point.cumulative_pnl_usd for point in summary.points] == [25.0, -15.0, -5.0]


def test_prospective_validation_empty_is_explicit() -> None:
    summary = summarize_prospective_validation([], initial_bankroll_usd=1080.0)

    assert summary.closed_orders == 0
    assert summary.independent_blocks == 0
    assert summary.roi is None
    assert summary.largest_order_id is None
    assert summary.points == ()


def _formal_prospective_trade() -> ProspectiveValidationTrade:
    signal_ts = datetime(2026, 7, 1, 8, tzinfo=UTC)
    return ProspectiveValidationTrade(
        enrollment_id=1,
        order_id=101,
        pipeline_cohort="accuracy-v4",
        station_icao="KLGA",
        market_date=date(2026, 7, 1),
        kind="high",
        recognized_at=datetime(2026, 7, 2, tzinfo=UTC),
        pnl_usd=4.0,
        cost_usd=6.0,
        skill_pnl_usd=0.5,
        luck_pnl_usd=3.5,
        signal_id=11,
        market_id=12,
        polymarket_id="formal-market",
        side="buy_yes",
        signal_ts=signal_ts,
        resolves_at=datetime(2026, 7, 1, 18, tzinfo=UTC),
        confidence="A",
        horizon_bucket="6-24h",
        requested_limit_price=0.61,
        fill_vwap=0.59,
        filled_notional_usd=5.9,
        fee_usd=0.1,
        fee_complete=True,
        size_shares=10.0,
        yes_model_prob=0.72,
        model_prob=0.72,
        yes_market_prob=0.60,
        market_prob=0.60,
        raw_model_prob=0.80,
        predicted_edge=0.12,
        resolved_yes=True,
        won=True,
        market_closed_at=datetime(2026, 7, 1, 18, tzinfo=UTC),
        close_price=0.64,
        clv=0.05,
        predictive_lane_id=21,
        predictive_lane_identity_hash="a" * 64,
        predictive_lane_boundary_at=signal_ts,
        execution_lane_id=22,
        execution_lane_identity_hash="b" * 64,
        execution_lane_boundary_at=signal_ts,
    )


def test_prospective_adapters_share_one_formal_projection() -> None:
    trade = _formal_prospective_trade()

    positions = prospective_position_stats([trade])
    brier = prospective_trade_outcomes([trade])
    clv = prospective_clv_outcomes([trade])

    assert positions == [
        PositionStat(
            closed=True,
            pnl_usd=4.0,
            clv=0.05,
            cost_usd=6.0,
            fee_usd=0.1,
            size_shares=10.0,
            avg_price=0.59,
        )
    ]
    assert len(brier) == 1
    assert brier[0].model_prob == 0.72
    assert brier[0].market_prob == 0.60
    assert brier[0].pipeline_version == "accuracy-v4"
    assert brier[0].order_id == 101
    assert len(clv) == 1
    assert clv[0].entry_price == 0.59
    assert clv[0].clv == 0.05
    assert clv[0].fee_usd == 0.1


def test_prospective_adapters_fail_closed_on_unknown_fee_or_lineage() -> None:
    trade = _formal_prospective_trade()
    unknown_fee = replace(trade, fee_usd=None, fee_complete=False)
    missing_lineage = replace(trade, execution_lane_id=None)
    inconsistent_side_probability = replace(trade, model_prob=0.28)
    inconsistent_binary_outcome = replace(trade, won=False)

    for invalid in (
        unknown_fee,
        missing_lineage,
        inconsistent_side_probability,
        inconsistent_binary_outcome,
    ):
        assert prospective_position_stats([invalid]) == []
        assert prospective_trade_outcomes([invalid]) == []
        assert prospective_clv_outcomes([invalid]) == []


def test_prospective_clv_adapter_excludes_only_missing_close_line() -> None:
    trade = replace(_formal_prospective_trade(), close_price=None, clv=None)

    assert len(prospective_position_stats([trade])) == 1
    assert len(prospective_trade_outcomes([trade])) == 1
    assert prospective_clv_outcomes([trade]) == []


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
            PositionStat(closed=True, pnl_usd=100.0, clv=0.02, cost_usd=50.0, fee_usd=0.05),
            PositionStat(closed=True, pnl_usd=-10.0, clv=-0.01, cost_usd=25.0, fee_usd=0.02),
            PositionStat(closed=True, pnl_usd=-10.0, clv=-0.01, cost_usd=25.0, fee_usd=0.02),
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
    assert (
        decompose_pnl(
            PositionStat(closed=False, pnl_usd=None, clv=None, cost_usd=1.0, fee_usd=None)
        )
        is None
    )
    assert (
        decompose_pnl(PositionStat(closed=True, pnl_usd=5.0, clv=None, cost_usd=1.0, fee_usd=None))
        is None
    )
    assert (
        decompose_pnl(
            PositionStat(
                closed=True,
                pnl_usd=5.0,
                clv=0.01,
                cost_usd=1.0,
                fee_usd=None,
                size_shares=0.0,
            )
        )
        is None
    )


def test_summary_reports_skill_luck_and_deterministic_ci() -> None:
    positions = [
        PositionStat(
            closed=True,
            pnl_usd=8.0,
            clv=0.04,
            cost_usd=20.0,
            fee_usd=None,
            size_shares=40.0,
            avg_price=0.5,
        ),
        PositionStat(
            closed=True,
            pnl_usd=-20.0,
            clv=0.01,
            cost_usd=20.0,
            fee_usd=None,
            size_shares=40.0,
            avg_price=0.5,
        ),
        PositionStat(
            closed=True,
            pnl_usd=12.0,
            clv=-0.02,
            cost_usd=12.0,
            fee_usd=None,
            size_shares=30.0,
            avg_price=0.4,
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
        brier_root_cause,
        edge_calibration,
        edge_slope,
        raw_model_brier,
    )

    trades = [
        # Model was sharper than the market on both.
        TradeOutcome(
            model_prob=0.8,
            market_prob=0.6,
            won=True,
            pnl_usd=8.0,
            cost_usd=12.0,
            predicted_edge=0.20,
            raw_model_prob=0.9,
            station_icao="KLGA",
            kind="high",
            horizon_bucket="0-6h",
            confidence="A",
            side="buy_yes",
            pipeline_version="accuracy-v4",
        ),
        TradeOutcome(
            model_prob=0.2,
            market_prob=0.4,
            won=False,
            pnl_usd=-4.0,
            cost_usd=8.0,
            predicted_edge=0.02,
            raw_model_prob=0.1,
            station_icao="KLGA",
            kind="high",
            horizon_bucket="0-6h",
            confidence="C",
            side="buy_no",
            pipeline_version="accuracy-v4",
        ),
        TradeOutcome(
            model_prob=0.7,
            market_prob=0.55,
            won=True,
            pnl_usd=4.5,
            cost_usd=11.0,
            predicted_edge=0.08,
            raw_model_prob=None,
            station_icao="EGLC",
            kind="low",
            horizon_bucket="6-24h",
            confidence="B",
            side="buy_yes",
            pipeline_version=None,
        ),
    ]
    briers = brier_comparison(trades)
    assert briers is not None
    brier_model, brier_market = briers
    assert brier_model < brier_market
    assert brier_comparison([]) is None
    raw = raw_model_brier(trades)
    assert raw is not None
    assert raw[1] == 2

    buckets = edge_calibration(trades)
    assert [b.count for b in buckets] == [1, 0, 1, 1]
    assert buckets[0].avg_realized_return == pytest.approx(-0.5)
    assert buckets[1].avg_realized_return is None

    slope = edge_slope(trades)
    assert slope is not None and slope > 0
    assert edge_slope(trades[:2]) is None

    slices = brier_root_cause(trades)
    station_klga = next(s for s in slices if s.dimension == "station" and s.value == "KLGA")
    assert station_klga.count == 2
    assert station_klga.raw_count == 2
    assert station_klga.model_minus_market < 0
    confidence_a = next(s for s in slices if s.dimension == "confidence" and s.value == "A")
    assert confidence_a.avg_predicted_edge == 0.2
    version_v4 = next(
        s for s in slices if s.dimension == "pipeline_version" and s.value == "accuracy-v4"
    )
    assert version_v4.count == 2
    legacy = next(s for s in slices if s.dimension == "pipeline_version" and s.value == "unknown")
    assert legacy.count == 1


def test_price_band_labels() -> None:
    from stratus.services.live_analytics import price_band

    assert price_band(0.05) == "0.00-0.10"
    assert price_band(0.10) == "0.10-0.25"
    assert price_band(0.30) == "0.25-0.50"
    assert price_band(0.95) == "0.90-1.00"
    assert price_band(1.0) == "0.90-1.00"


def _clv(clv: float, **kw: object):
    from stratus.services.live_analytics import ClvOutcome

    base: dict[str, object] = {
        "clv": clv,
        "entry_price": 0.5,
        "pnl_usd": 1.0,
        "cost_usd": 20.0,
        "fee_usd": 0.04,
        "won": clv > 0,
        "station_icao": "KLGA",
        "kind": "high",
        "side": "buy_yes",
        "confidence": "B",
        "horizon_bucket": "6-24h",
    }
    base.update(kw)
    return ClvOutcome(**base)  # type: ignore[arg-type]


def test_clv_root_cause_slices_and_worst_first_order() -> None:
    from stratus.services.live_analytics import clv_root_cause, clv_summary

    outcomes = [
        _clv(0.05, station_icao="KLGA"),
        _clv(-0.03, station_icao="LFPB"),
        _clv(-0.01, station_icao="LFPB"),
        _clv(0.02, station_icao="EGLC", entry_price=0.95),
    ]
    summary = clv_summary(outcomes)
    assert summary is not None
    avg, beat = summary
    assert avg == pytest.approx((0.05 - 0.03 - 0.01 + 0.02) / 4, abs=1e-6)
    assert beat == pytest.approx(0.5)

    slices = clv_root_cause(outcomes)
    stations = [s for s in slices if s.dimension == "station"]
    # Worst CLV first within the dimension: LFPB (avg -0.02) before EGLC/KLGA.
    assert stations[0].value == "LFPB"
    assert stations[0].count == 2
    assert stations[0].avg_clv == pytest.approx(-0.02)
    assert stations[0].clv_beat_rate == 0.0

    # The tail entry (0.95) lands in the 0.90-1.00 price band.
    bands = {s.value for s in slices if s.dimension == "price_band"}
    assert "0.90-1.00" in bands
    assert "0.50-0.75" in bands  # the 0.5 entries ([0.50, 0.75))


def test_clv_summary_empty() -> None:
    from stratus.services.live_analytics import clv_root_cause, clv_summary

    assert clv_summary([]) is None
    assert clv_root_cause([]) == ()
