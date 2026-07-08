from datetime import UTC, datetime
from io import BytesIO

import pytest
from PIL import Image

from stratus.services.live_analytics import EquityPoint
from stratus.services.telegram_reports import (
    reliability_bins,
    render_equity_chart,
    render_reliability_chart,
)


def test_render_equity_chart_produces_valid_png() -> None:
    points = [
        EquityPoint(ts=datetime(2026, 7, d, tzinfo=UTC), cumulative_pnl_usd=v)
        for d, v in [(1, 10.0), (2, -5.0), (3, 8.0), (4, 22.0)]
    ]
    chart = render_equity_chart(points)
    assert chart is not None
    with Image.open(BytesIO(chart)) as image:
        assert image.format == "PNG"


def test_render_equity_chart_needs_two_points() -> None:
    assert render_equity_chart([]) is None
    assert render_equity_chart(
        [EquityPoint(ts=datetime(2026, 7, 1, tzinfo=UTC), cumulative_pnl_usd=1.0)]
    ) is None


def test_reliability_bins_are_per_station_and_probability_decile() -> None:
    rows = [
        ("KLGA", False, 0.05),
        ("KLGA", True, 0.15),
        ("KLGA", True, 0.19),
        ("EGLC", True, 1.0),
    ]

    bins = reliability_bins(rows)

    assert len(bins["KLGA"]) == 2
    assert bins["KLGA"][1].forecast == pytest.approx(0.17)
    assert bins["KLGA"][1].actual == 1.0
    assert bins["KLGA"][1].count == 2
    assert bins["EGLC"][0].lower == 0.9


def test_reliability_chart_is_valid_png() -> None:
    chart = render_reliability_chart(
        [("KLGA", False, 0.1), ("KLGA", True, 0.8), ("EGLC", True, 0.7)]
    )

    assert chart is not None
    with Image.open(BytesIO(chart)) as image:
        assert image.format == "PNG"
        assert image.size == (900, 650)


def test_reliability_chart_is_absent_without_resolutions() -> None:
    assert render_reliability_chart([]) is None
