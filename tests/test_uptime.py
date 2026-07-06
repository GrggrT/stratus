from datetime import UTC, datetime, timedelta

from stratus.services.uptime import JobOutcome, calculate_collector_uptime


def test_collector_uptime_passes_complete_mature_window() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    interval = timedelta(minutes=10)
    window = timedelta(hours=1)
    outcomes = [
        JobOutcome("collector", now - window + interval * index, "success")
        for index in range(1, 7)
    ]

    result = calculate_collector_uptime(
        outcomes,
        now=now,
        monitoring_started=now - window - timedelta(minutes=1),
        window=window,
        intervals={"collector": interval},
    )

    assert result.mature is True
    assert result.passed is True
    assert result.metrics[0].uptime == 1.0
    assert result.metrics[0].max_gap == interval


def test_collector_uptime_detects_missed_run_and_gap() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    interval = timedelta(minutes=10)
    window = timedelta(hours=1)
    outcomes = [
        JobOutcome("collector", now - window + interval * index, "success")
        for index in (1, 2, 5, 6)
    ]

    result = calculate_collector_uptime(
        outcomes,
        now=now,
        monitoring_started=now - window,
        window=window,
        intervals={"collector": interval},
    )

    assert result.passed is False
    assert result.metrics[0].uptime == 4 / 6
    assert result.metrics[0].max_gap == timedelta(minutes=30)


def test_collector_uptime_requires_full_window_before_pass() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    result = calculate_collector_uptime(
        [],
        now=now,
        monitoring_started=now - timedelta(hours=12),
        intervals={"collector": timedelta(hours=1)},
    )

    assert result.mature is False
    assert result.passed is False
    assert result.coverage == timedelta(hours=12)
