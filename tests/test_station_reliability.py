from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from stratus.config import Settings
from stratus.db.models import (
    ResolutionReconciliationClassification,
    StationReliabilitySnapshot,
    StationReliabilityStatus,
    WeatherKind,
)
from stratus.jobs import assess_station_reliability as reliability_job
from stratus.repositories.resolution_reconciliation import (
    ResolutionReconciliationIntegrityError,
)
from stratus.repositories.station_reliability import (
    StationReliabilitySnapshotValue,
    station_reliability_record_hash,
    station_reliability_record_is_valid,
)
from stratus.services.station_reliability import (
    StationReconciliationEvidence,
    assess_station_reliability,
    reduce_station_reconciliation_units,
    station_reliability_policy_hash,
    station_signal_gate_reasons,
    wilson_lower_bound,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


class _ScalarRows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, scalar_results: list[list[Any]]) -> None:
        self._scalar_results = iter(scalar_results)

    async def scalars(self, _statement: Any) -> _ScalarRows:
        return _ScalarRows(next(self._scalar_results))

    async def execute(self, _statement: Any) -> None:
        return None


class _FakeBegin:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, scalar_results: list[list[Any]]) -> None:
        self._session = _FakeSession(scalar_results)

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self._session)


def _evidence(
    market_date: date,
    classification: ResolutionReconciliationClassification,
    *,
    kind: WeatherKind = WeatherKind.HIGH,
    suffix: str = "0",
) -> StationReconciliationEvidence:
    return StationReconciliationEvidence(
        station_id=1,
        market_date=market_date,
        kind=kind,
        classification=classification,
        checked_at=NOW,
        record_hash=suffix * 64,
    )


def _snapshot(
    *,
    status: StationReliabilityStatus = StationReliabilityStatus.RELIABLE,
    evaluated_at: datetime = NOW,
    observation_local_date: date = date(2026, 7, 17),
    latest_observation_at: datetime | None = NOW,
    observation_cooldown_until: datetime | None = None,
    resolution_cooldown_until: datetime | None = None,
    reasons: list[str] | None = None,
    policy_hash: str = "a" * 64,
) -> StationReliabilitySnapshot:
    return StationReliabilitySnapshot(
        id=1,
        station_id=1,
        evaluated_at=evaluated_at,
        window_days=180,
        comparable_units=20,
        match_units=20,
        conflict_units=0,
        exact_source_mismatch_units=0,
        clear_mismatch_units=0,
        exact_truth_gap_units=0,
        boundary_units=0,
        unavailable_units=4,
        reliability_score=Decimal("1"),
        reliability_lower_bound=Decimal("0.838870"),
        status=status,
        latest_conflict_at=None,
        latest_conflict_date=None,
        latest_conflict_kind=None,
        resolution_cooldown_until=resolution_cooldown_until,
        latest_observation_at=latest_observation_at,
        observation_local_date=observation_local_date,
        observation_cooldown_until=observation_cooldown_until,
        reasons=reasons or [],
        policy_hash=policy_hash,
        source_hash="b" * 64,
        record_hash="c" * 64,
    )


def test_reducer_counts_one_independent_unit_and_uses_conflict_priority() -> None:
    rows = (
        _evidence(
            date(2026, 7, 16),
            ResolutionReconciliationClassification.MATCH,
            suffix="1",
        ),
        _evidence(
            date(2026, 7, 16),
            ResolutionReconciliationClassification.EXACT_TRUTH_GAP,
            suffix="2",
        ),
        _evidence(
            date(2026, 7, 16),
            ResolutionReconciliationClassification.TRUTH_UNAVAILABLE,
            suffix="3",
        ),
    )

    units = reduce_station_reconciliation_units(rows)

    assert len(units) == 1
    assert units[0].classification is ResolutionReconciliationClassification.EXACT_TRUTH_GAP
    assert units[0].record_hashes == ("1" * 64, "2" * 64, "3" * 64)


def test_reducer_boundary_outweighs_match_for_the_same_independent_unit() -> None:
    units = reduce_station_reconciliation_units(
        (
            _evidence(
                date(2026, 7, 16),
                ResolutionReconciliationClassification.MATCH,
                suffix="1",
            ),
            _evidence(
                date(2026, 7, 16),
                ResolutionReconciliationClassification.BOUNDARY_SENSITIVE,
                suffix="2",
            ),
        )
    )

    assert len(units) == 1
    assert units[0].classification is ResolutionReconciliationClassification.BOUNDARY_SENSITIVE


def test_reducer_match_outweighs_unavailable_for_the_same_independent_unit() -> None:
    units = reduce_station_reconciliation_units(
        (
            _evidence(
                date(2026, 7, 16),
                ResolutionReconciliationClassification.TRUTH_UNAVAILABLE,
                suffix="1",
            ),
            _evidence(
                date(2026, 7, 16),
                ResolutionReconciliationClassification.MATCH,
                suffix="2",
            ),
        )
    )

    assert len(units) == 1
    assert units[0].classification is ResolutionReconciliationClassification.MATCH


def test_reducer_keeps_station_day_kinds_as_independent_units() -> None:
    units = reduce_station_reconciliation_units(
        (
            _evidence(
                date(2026, 7, 16),
                ResolutionReconciliationClassification.MATCH,
                kind=WeatherKind.HIGH,
                suffix="1",
            ),
            _evidence(
                date(2026, 7, 16),
                ResolutionReconciliationClassification.MATCH,
                kind=WeatherKind.LOW,
                suffix="2",
            ),
            _evidence(
                date(2026, 7, 17),
                ResolutionReconciliationClassification.MATCH,
                kind=WeatherKind.HIGH,
                suffix="3",
            ),
        )
    )

    assert [(unit.market_date, unit.kind) for unit in units] == [
        (date(2026, 7, 16), WeatherKind.HIGH),
        (date(2026, 7, 16), WeatherKind.LOW),
        (date(2026, 7, 17), WeatherKind.HIGH),
    ]


def test_unavailable_truth_is_excluded_and_never_starts_a_cooldown() -> None:
    rows = [
        _evidence(
            date(2026, 7, day),
            ResolutionReconciliationClassification.MATCH,
            suffix="4",
        )
        for day in range(1, 21)
    ]
    rows.extend(
        _evidence(
            date(2026, 6, day),
            ResolutionReconciliationClassification.TRUTH_UNAVAILABLE,
            suffix="5",
        )
        for day in range(1, 6)
    )
    assessment = assess_station_reliability(
        reduce_station_reconciliation_units(rows),
        now=NOW,
        station_timezone="UTC",
        latest_observation_at=NOW - timedelta(minutes=5),
        min_samples=20,
        min_lower_bound=0.80,
        resolution_cooldown=timedelta(hours=48),
        observation_stale_after=timedelta(minutes=90),
    )

    assert assessment.comparable_units == 20
    assert assessment.unavailable_units == 5
    assert assessment.reliability_score == Decimal("1.000000")
    assert assessment.status is StationReliabilityStatus.RELIABLE
    assert assessment.resolution_cooldown_until is None
    assert "station_resolution_conflict" not in assessment.reasons


@pytest.mark.parametrize(
    "classification",
    [
        ResolutionReconciliationClassification.EXACT_SOURCE_MISMATCH,
        ResolutionReconciliationClassification.CLEAR_MISMATCH,
        ResolutionReconciliationClassification.EXACT_TRUTH_GAP,
    ],
)
def test_real_source_conflicts_start_stable_station_cooldown(
    classification: ResolutionReconciliationClassification,
) -> None:
    assessment = assess_station_reliability(
        reduce_station_reconciliation_units(
            [_evidence(date(2026, 7, 16), classification, suffix="6")]
        ),
        now=NOW,
        station_timezone="UTC",
        latest_observation_at=NOW,
        min_samples=20,
        min_lower_bound=0.80,
        resolution_cooldown=timedelta(hours=48),
        observation_stale_after=timedelta(minutes=90),
    )

    assert assessment.status is StationReliabilityStatus.BLOCKED
    assert assessment.latest_conflict_at == NOW
    assert assessment.resolution_cooldown_until == NOW + timedelta(hours=48)
    assert "station_resolution_conflict" in assessment.reasons


def test_delayed_conflict_starts_at_discovery_and_keeps_that_anchor_on_reassessment() -> None:
    delayed = StationReconciliationEvidence(
        station_id=1,
        market_date=date(2026, 7, 15),
        kind=WeatherKind.HIGH,
        classification=ResolutionReconciliationClassification.CLEAR_MISMATCH,
        checked_at=NOW,
        record_hash="7" * 64,
    )

    assessment = assess_station_reliability(
        reduce_station_reconciliation_units((delayed,)),
        now=NOW,
        station_timezone="Europe/Vienna",
        latest_observation_at=NOW,
        min_samples=20,
        min_lower_bound=0.80,
        resolution_cooldown=timedelta(hours=48),
        observation_stale_after=timedelta(minutes=90),
    )

    assert assessment.latest_conflict_at == NOW
    assert assessment.resolution_cooldown_until == NOW + timedelta(hours=48)
    assert assessment.status is StationReliabilityStatus.BLOCKED

    previous = _snapshot(
        status=StationReliabilityStatus.BLOCKED,
        resolution_cooldown_until=assessment.resolution_cooldown_until,
        reasons=["station_resolution_conflict", "station_reliability_collecting"],
    )
    previous.latest_conflict_at = assessment.latest_conflict_at
    previous.latest_conflict_date = date(2026, 7, 15)
    previous.latest_conflict_kind = WeatherKind.HIGH
    previous.conflict_units = 1
    rechecked = StationReconciliationEvidence(
        station_id=delayed.station_id,
        market_date=delayed.market_date,
        kind=delayed.kind,
        classification=delayed.classification,
        checked_at=NOW + timedelta(hours=6),
        record_hash=delayed.record_hash,
    )

    reassessment = assess_station_reliability(
        reduce_station_reconciliation_units((rechecked,)),
        now=NOW + timedelta(hours=6),
        station_timezone="Europe/Vienna",
        latest_observation_at=NOW + timedelta(hours=6),
        min_samples=20,
        min_lower_bound=0.80,
        resolution_cooldown=timedelta(hours=48),
        observation_stale_after=timedelta(minutes=90),
        previous_snapshot=previous,
    )

    assert reassessment.latest_conflict_at == NOW
    assert reassessment.resolution_cooldown_until == NOW + timedelta(hours=48)


def test_observation_cooldown_is_limited_to_the_current_local_date() -> None:
    snapshot = _snapshot(
        status=StationReliabilityStatus.BLOCKED,
        observation_cooldown_until=NOW + timedelta(minutes=90),
        reasons=["station_observation_stale"],
    )

    assert station_signal_gate_reasons(
        snapshot,
        market_date=date(2026, 7, 17),
        now=NOW,
        station_timezone="UTC",
        expected_policy_hash="a" * 64,
        max_age=timedelta(minutes=45),
        observation_stale_after=timedelta(minutes=90),
    ) == ("station_observation_stale",)
    assert (
        station_signal_gate_reasons(
            snapshot,
            market_date=date(2026, 7, 18),
            now=NOW,
            station_timezone="UTC",
            expected_policy_hash="a" * 64,
            max_age=timedelta(minutes=45),
            observation_stale_after=timedelta(minutes=90),
        )
        == ()
    )


def test_assessment_rejects_recent_observation_from_previous_local_day() -> None:
    local_midnight = datetime(2026, 7, 17, 22, 30, tzinfo=UTC)
    previous_local_day_observation = local_midnight - timedelta(hours=1)

    assessment = assess_station_reliability(
        (),
        now=local_midnight,
        station_timezone="Europe/Vienna",
        latest_observation_at=previous_local_day_observation,
        min_samples=20,
        min_lower_bound=0.80,
        resolution_cooldown=timedelta(hours=48),
        observation_stale_after=timedelta(minutes=90),
    )

    assert assessment.observation_local_date == date(2026, 7, 18)
    assert assessment.status is StationReliabilityStatus.BLOCKED
    assert "station_observation_previous_local_date" in assessment.reasons


@pytest.mark.parametrize(
    ("now", "latest_observation_at", "expected_local_date"),
    [
        (
            datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
            datetime(2026, 3, 29, 0, 30, tzinfo=UTC),
            date(2026, 3, 29),
        ),
        (
            datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
            date(2026, 10, 25),
        ),
    ],
)
def test_assessment_accepts_same_local_day_observation_across_dst_transition(
    now: datetime,
    latest_observation_at: datetime,
    expected_local_date: date,
) -> None:
    assessment = assess_station_reliability(
        (),
        now=now,
        station_timezone="Europe/Vienna",
        latest_observation_at=latest_observation_at,
        min_samples=20,
        min_lower_bound=0.80,
        resolution_cooldown=timedelta(hours=48),
        observation_stale_after=timedelta(minutes=90),
    )

    assert assessment.observation_local_date == expected_local_date
    assert "station_observation_previous_local_date" not in assessment.reasons
    assert "station_observation_stale" not in assessment.reasons


def test_signal_gate_fails_closed_on_missing_stale_or_wrong_policy_snapshot() -> None:
    assert station_signal_gate_reasons(
        None,
        market_date=NOW.date(),
        now=NOW,
        station_timezone="UTC",
        expected_policy_hash="a" * 64,
        max_age=timedelta(minutes=45),
        observation_stale_after=timedelta(minutes=90),
    ) == ("station_reliability_snapshot_missing",)
    assert station_signal_gate_reasons(
        _snapshot(evaluated_at=NOW - timedelta(hours=2)),
        market_date=NOW.date(),
        now=NOW,
        station_timezone="UTC",
        expected_policy_hash="a" * 64,
        max_age=timedelta(minutes=45),
        observation_stale_after=timedelta(minutes=90),
    ) == ("station_reliability_snapshot_stale",)
    assert station_signal_gate_reasons(
        _snapshot(policy_hash="d" * 64),
        market_date=NOW.date(),
        now=NOW,
        station_timezone="UTC",
        expected_policy_hash="a" * 64,
        max_age=timedelta(minutes=45),
        observation_stale_after=timedelta(minutes=90),
    ) == ("station_reliability_policy_mismatch",)


def test_signal_gate_preserves_low_reliability_reason_while_station_is_blocked() -> None:
    snapshot = _snapshot(
        status=StationReliabilityStatus.BLOCKED,
        resolution_cooldown_until=NOW + timedelta(hours=1),
        reasons=[
            "station_resolution_conflict",
            "station_reliability_below_threshold",
        ],
    )

    assert station_signal_gate_reasons(
        snapshot,
        market_date=NOW.date(),
        now=NOW,
        station_timezone="UTC",
        expected_policy_hash="a" * 64,
        max_age=timedelta(minutes=45),
        observation_stale_after=timedelta(minutes=90),
    ) == (
        "station_resolution_conflict",
        "station_reliability_below_threshold",
    )


def test_signal_gate_recomputes_observation_staleness_at_decision_time() -> None:
    observed_at = NOW - timedelta(minutes=89)
    snapshot = _snapshot(latest_observation_at=observed_at)

    assert station_signal_gate_reasons(
        snapshot,
        market_date=NOW.date(),
        now=NOW,
        station_timezone="UTC",
        expected_policy_hash="a" * 64,
        max_age=timedelta(hours=3),
        observation_stale_after=timedelta(minutes=90),
    ) == ()
    assert station_signal_gate_reasons(
        snapshot,
        market_date=NOW.date(),
        now=NOW + timedelta(minutes=2),
        station_timezone="UTC",
        expected_policy_hash="a" * 64,
        max_age=timedelta(hours=3),
        observation_stale_after=timedelta(minutes=90),
    ) == ("station_observation_stale",)


@pytest.mark.parametrize(
    ("now", "station_timezone", "snapshot_local_date"),
    [
        (
            datetime(2026, 3, 28, 23, 30, tzinfo=UTC),
            "Europe/Vienna",
            date(2026, 3, 28),
        ),
        (
            datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
            "Europe/Vienna",
            date(2026, 10, 24),
        ),
    ],
)
def test_signal_gate_detects_previous_local_day_snapshot_across_dst_boundaries(
    now: datetime,
    station_timezone: str,
    snapshot_local_date: date,
) -> None:
    snapshot = _snapshot(
        evaluated_at=now - timedelta(minutes=5),
        observation_local_date=snapshot_local_date,
        latest_observation_at=now - timedelta(minutes=5),
    )

    assert station_signal_gate_reasons(
        snapshot,
        market_date=now.astimezone(ZoneInfo(station_timezone)).date(),
        now=now,
        station_timezone=station_timezone,
        expected_policy_hash="a" * 64,
        max_age=timedelta(minutes=45),
        observation_stale_after=timedelta(minutes=90),
    ) == ("station_reliability_snapshot_local_date_stale",)


def test_collecting_station_is_allowed_in_paper_but_live_requires_reliable() -> None:
    snapshot = _snapshot(
        status=StationReliabilityStatus.COLLECTING,
        reasons=["station_reliability_collecting"],
    )
    kwargs = {
        "market_date": NOW.date(),
        "now": NOW,
        "station_timezone": "UTC",
        "expected_policy_hash": "a" * 64,
        "max_age": timedelta(minutes=45),
        "observation_stale_after": timedelta(minutes=90),
    }

    assert station_signal_gate_reasons(snapshot, **kwargs) == ()
    assert station_signal_gate_reasons(
        snapshot,
        require_reliable=True,
        **kwargs,
    ) == ("station_reliability_collecting",)


def test_snapshot_record_hash_attests_every_selection_field() -> None:
    settings = Settings(_env_file=None)
    value = StationReliabilitySnapshotValue(
        station_id=1,
        evaluated_at=NOW,
        window_days=180,
        comparable_units=20,
        match_units=20,
        conflict_units=0,
        exact_source_mismatch_units=0,
        clear_mismatch_units=0,
        exact_truth_gap_units=0,
        boundary_units=0,
        unavailable_units=4,
        reliability_score=Decimal("1.000000"),
        reliability_lower_bound=Decimal("0.838870"),
        status=StationReliabilityStatus.RELIABLE,
        latest_conflict_at=None,
        latest_conflict_date=None,
        latest_conflict_kind=None,
        resolution_cooldown_until=None,
        latest_observation_at=NOW,
        observation_local_date=NOW.date(),
        observation_cooldown_until=None,
        reasons=(),
        policy_hash=station_reliability_policy_hash(settings),
        source_hash="b" * 64,
    )
    row = StationReliabilitySnapshot(
        **{field: getattr(value, field) for field in value.__dataclass_fields__},
        record_hash=station_reliability_record_hash(value),
    )

    assert station_reliability_record_is_valid(row)
    row.match_units = 19
    assert not station_reliability_record_is_valid(row)


def test_wilson_bound_requires_real_depth() -> None:
    assert wilson_lower_bound(0, 0) is None
    assert wilson_lower_bound(20, 20) == pytest.approx(0.83887484)
    with pytest.raises(ValueError, match="successes"):
        wilson_lower_bound(2, 1)


async def test_assessment_job_fails_closed_on_invalid_reconciliation_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    station = SimpleNamespace(id=1, tz="UTC")
    invalid = SimpleNamespace(
        id=91,
        station_id=1,
        market_date=NOW.date() - timedelta(days=1),
        kind=WeatherKind.HIGH,
        classification=ResolutionReconciliationClassification.MATCH,
        checked_at=NOW,
        record_hash="0" * 64,
    )
    sessions = _FakeSessionFactory([[station], [invalid], []])

    async def no_previous(_session: Any, _station_ids: list[int]) -> dict[int, Any]:
        return {}

    monkeypatch.setattr(reliability_job, "latest_station_reliability_by_station", no_previous)
    monkeypatch.setattr(
        reliability_job,
        "resolution_reconciliation_record_is_valid",
        lambda _row: False,
    )

    with pytest.raises(
        ResolutionReconciliationIntegrityError,
        match="refused invalid reconciliation evidence: 91",
    ):
        await reliability_job.assess_station_reliability_job(
            Settings(_env_file=None),
            sessions,  # type: ignore[arg-type]
        )


async def test_assessment_job_uses_half_open_local_date_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    station = SimpleNamespace(id=1, tz="UTC")

    def reconciliation(row_id: int, market_date: date) -> SimpleNamespace:
        return SimpleNamespace(
            id=row_id,
            station_id=1,
            market_date=market_date,
            kind=WeatherKind.HIGH,
            classification=ResolutionReconciliationClassification.MATCH,
            checked_at=NOW,
            record_hash=f"{row_id:064x}",
        )

    rows = [
        reconciliation(1, NOW.date() - timedelta(days=31)),
        reconciliation(2, NOW.date() - timedelta(days=30)),
        reconciliation(3, NOW.date() - timedelta(days=1)),
        reconciliation(4, NOW.date()),
    ]
    sessions = _FakeSessionFactory([[station], rows, []])
    inserted: list[StationReliabilitySnapshotValue] = []

    async def no_previous(_session: Any, _station_ids: list[int]) -> dict[int, Any]:
        return {}

    async def capture_snapshot(
        _session: Any,
        value: StationReliabilitySnapshotValue,
    ) -> None:
        inserted.append(value)

    monkeypatch.setattr(reliability_job, "datetime", FrozenDateTime)
    monkeypatch.setattr(reliability_job, "latest_station_reliability_by_station", no_previous)
    monkeypatch.setattr(
        reliability_job,
        "resolution_reconciliation_record_is_valid",
        lambda _row: True,
    )
    monkeypatch.setattr(reliability_job, "insert_station_reliability_snapshot", capture_snapshot)

    count = await reliability_job.assess_station_reliability_job(
        Settings(
            _env_file=None,
            station_reliability_window_days=30,
            station_reliability_min_samples=1,
        ),
        sessions,  # type: ignore[arg-type]
    )

    assert count == 1
    assert len(inserted) == 1
    assert inserted[0].comparable_units == 2
    assert inserted[0].match_units == 2
