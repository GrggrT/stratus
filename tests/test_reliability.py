from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from stratus.services.live_analytics import EquityPoint
from stratus.services.telegram_reports import (
    StationScore,
    qualified_station_scores,
    reliability_bins,
    render_equity_chart,
    render_reliability_chart,
)


def test_station_gate_accepts_three_qualified_inside_larger_pool() -> None:
    scores = (
        StationScore("EGLC", 120, 0.12),
        StationScore("KLGA", 110, 0.17),
        StationScore("LFPB", 100, 0.10),
        StationScore("RJTT", 40, 0.09),
        StationScore("RKSI", 130, 0.22),
    )

    qualified = qualified_station_scores(
        scores,
        sample_minimum=100,
        brier_maximum=0.18,
    )

    assert [score.icao for score in qualified] == ["EGLC", "KLGA", "LFPB"]


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
    assert (
        render_equity_chart(
            [EquityPoint(ts=datetime(2026, 7, 1, tzinfo=UTC), cumulative_pnl_usd=1.0)]
        )
        is None
    )


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


def test_backup_sidecar_publishes_only_verified_archives() -> None:
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    write_at = compose.index("timeout -s TERM 21600 pg_dump")
    verify_at = compose.index('pg_restore --list "$$partial_dir/stratus.dump"')
    manifest_at = compose.index("format=stratus-backup-manifest-v2")
    stable_schema_at = compose.index('test "$$schema_before" = "$$schema_after"')
    publish_at = compose.index('mv -T "$$partial_dir" "$$target_dir"')

    assert write_at < verify_at < stable_schema_at < manifest_at < publish_at
    assert ".stratus_$${backup_id}.backup.partial" in compose
    assert "catalog_verified=true" in compose
    assert "full_restore_verified=false" in compose
