from datetime import UTC, datetime

from stratus.jobs.backfill_single_runs import _run_times


def test_run_times_align_to_model_cycle() -> None:
    values = _run_times(
        start=datetime(2026, 7, 1, 1, 10, tzinfo=UTC),
        end=datetime(2026, 7, 1, 19, tzinfo=UTC),
        frequency_hours=6,
    )
    assert [value.hour for value in values] == [6, 12, 18]
