from datetime import UTC, datetime, timedelta

import pytest

from stratus.config import Settings
from stratus.repositories.storage_capacity import _capacity_path_projection
from stratus.services.storage_capacity import (
    CAPACITY_POLICY_VERSION,
    CapacityPoint,
    assess_capacity_collection_gate,
    assess_clob_ledger_capacity,
)


def _point(
    *,
    at: datetime,
    ledger_bytes: int,
    free_disk_bytes: int | None = 10_000,
) -> CapacityPoint:
    return CapacityPoint(
        captured_at=at,
        database_bytes=ledger_bytes * 2,
        clob_book_batches_bytes=ledger_bytes // 2,
        clob_market_snapshots_bytes=ledger_bytes - ledger_bytes // 2,
        free_disk_bytes=free_disk_bytes,
    )


def test_capacity_path_override_avoids_privileged_postgres_setting() -> None:
    projection, parameters = _capacity_path_projection(" /capacity-probe ")

    assert "current_setting" not in projection
    assert parameters == {"storage_path_override": "/capacity-probe"}

    fallback_projection, fallback_parameters = _capacity_path_projection(None)
    assert fallback_projection == "current_setting('data_directory') AS data_directory"
    assert fallback_parameters == {}


def test_capacity_waits_for_full_measurement_window() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    assessment = assess_clob_ledger_capacity(
        _point(at=now, ledger_bytes=100),
        _point(at=now - timedelta(hours=6), ledger_bytes=20),
        budget_bytes=1_000,
        warning_fraction=0.75,
        projection_days=30,
        minimum_window_hours=20,
        minimum_free_bytes=1_000,
    )

    assert assessment.severity == "info"
    assert assessment.reason == "measurement_window_pending"
    assert assessment.growth_bytes_per_day is None
    assert assessment.policy_version == CAPACITY_POLICY_VERSION


def test_capacity_warns_when_projection_reaches_budget() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    assessment = assess_clob_ledger_capacity(
        _point(at=now, ledger_bytes=400),
        _point(at=now - timedelta(hours=24), ledger_bytes=300),
        budget_bytes=1_000,
        warning_fraction=0.75,
        projection_days=7,
        minimum_window_hours=20,
        minimum_free_bytes=1_000,
    )

    assert assessment.severity == "warning"
    assert assessment.reason == "capacity_projection_at_risk"
    assert assessment.growth_bytes_per_day == 100
    assert assessment.projected_ledger_bytes == 1_100
    assert assessment.days_to_budget == 6


def test_capacity_blocks_only_after_hard_budget_is_exhausted() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    assessment = assess_clob_ledger_capacity(
        _point(at=now, ledger_bytes=1_001),
        None,
        budget_bytes=1_000,
        warning_fraction=0.75,
        projection_days=30,
        minimum_window_hours=20,
        minimum_free_bytes=1_000,
    )

    assert assessment.severity == "blocking"
    assert assessment.reason == "capacity_budget_exhausted"
    assert assessment.days_to_budget == 0


def test_capacity_blocks_when_free_disk_cannot_be_measured() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)

    assessment = assess_clob_ledger_capacity(
        _point(at=now, ledger_bytes=100, free_disk_bytes=None),
        None,
        budget_bytes=1_000,
        warning_fraction=0.75,
        projection_days=30,
        minimum_window_hours=20,
        minimum_free_bytes=250,
    )

    assert assessment.severity == "blocking"
    assert assessment.reason == "free_disk_measurement_unavailable"


def test_capacity_blocks_at_exact_free_disk_reserve() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)

    assessment = assess_clob_ledger_capacity(
        _point(at=now, ledger_bytes=100, free_disk_bytes=250),
        None,
        budget_bytes=1_000,
        warning_fraction=0.75,
        projection_days=30,
        minimum_window_hours=20,
        minimum_free_bytes=250,
    )

    assert assessment.severity == "blocking"
    assert assessment.reason == "free_disk_reserve_breached"


def test_collection_gate_requires_fresh_complete_sample() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    ttl = timedelta(minutes=90)

    assert not assess_capacity_collection_gate(
        None,
        now=now,
        budget_bytes=1_000,
        minimum_free_bytes=250,
        sample_ttl=ttl,
    ).allowed
    stale = assess_capacity_collection_gate(
        _point(at=now - ttl - timedelta(seconds=1), ledger_bytes=100),
        now=now,
        budget_bytes=1_000,
        minimum_free_bytes=250,
        sample_ttl=ttl,
    )
    assert stale.allowed is False
    assert stale.reason == "capacity_sample_stale"
    unavailable = assess_capacity_collection_gate(
        _point(at=now, ledger_bytes=100, free_disk_bytes=None),
        now=now,
        budget_bytes=1_000,
        minimum_free_bytes=250,
        sample_ttl=ttl,
    )
    assert unavailable.allowed is False
    assert unavailable.reason == "free_disk_measurement_unavailable"

    accepted = assess_capacity_collection_gate(
        _point(at=now - timedelta(minutes=89), ledger_bytes=999),
        now=now,
        budget_bytes=1_000,
        minimum_free_bytes=250,
        sample_ttl=ttl,
    )
    assert accepted.allowed is True


def test_collection_gate_blocks_budget_free_space_and_future_samples() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    common = {
        "now": now,
        "budget_bytes": 1_000,
        "minimum_free_bytes": 250,
        "sample_ttl": timedelta(minutes=90),
    }

    assert (
        assess_capacity_collection_gate(_point(at=now, ledger_bytes=1_000), **common).reason
        == "capacity_budget_exhausted"
    )
    assert (
        assess_capacity_collection_gate(
            _point(at=now, ledger_bytes=100, free_disk_bytes=250), **common
        ).reason
        == "free_disk_reserve_breached"
    )
    assert (
        assess_capacity_collection_gate(
            _point(at=now + timedelta(seconds=1), ledger_bytes=100), **common
        ).reason
        == "capacity_sample_from_future"
    )
    assert (
        assess_capacity_collection_gate(
            _point(at=now, ledger_bytes=700),
            pending_bytes=300,
            **common,
        ).reason
        == "capacity_write_would_exhaust_budget"
    )
    assert (
        assess_capacity_collection_gate(
            _point(at=now, ledger_bytes=100, free_disk_bytes=550),
            pending_bytes=300,
            **common,
        ).reason
        == "free_disk_write_would_breach_reserve"
    )


def test_capacity_config_requires_nested_response_and_write_reserves() -> None:
    with pytest.raises(ValueError, match="response limit"):
        Settings(
            _env_file=None,
            clob_response_max_bytes=250_000_000,
            clob_ledger_capacity_write_reserve_bytes=250_000_000,
        )
    with pytest.raises(ValueError, match="ledger budget"):
        Settings(
            _env_file=None,
            clob_ledger_capacity_budget_bytes=1_000_000_000,
            clob_ledger_capacity_write_reserve_bytes=1_000_000_000,
        )
    with pytest.raises(ValueError, match="free-disk reserve"):
        Settings(
            _env_file=None,
            clob_ledger_capacity_min_free_bytes=500_000_000,
            clob_ledger_capacity_write_reserve_bytes=500_000_000,
        )
