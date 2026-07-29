from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from stratus.config import Settings
from stratus.services.evidence_release_manifest import (
    _LANE_REPOSITORY_SOURCE_FILES,
    _LANE_SOURCE_FILES,
    _PREDICTIVE_SOURCE_FILES,
    ALEMBIC_HEAD,
    LANE_NAMES,
    EvidenceReleaseDatabaseSnapshot,
    EvidenceReleaseManifest,
    EvidenceReleaseManifestError,
    _source_file_manifests,
    compose_evidence_release_manifest,
    decide_evidence_lane_actions,
)

FINGERPRINT = "1" * 16
PROMOTION_HASH = "2" * 64
LOCK_HASH = "3" * 64


def test_predictive_lane_tracks_the_trade_scoring_repository() -> None:
    """Scoring changes must fork predictive evidence instead of mixing cohorts."""

    assert "repositories/analytics.py" in _PREDICTIVE_SOURCE_FILES
    assert "jobs/audit_accuracy.py" in _PREDICTIVE_SOURCE_FILES
    assert "jobs/evaluate_forecasts.py" in _PREDICTIVE_SOURCE_FILES


def test_daily_actual_truth_quarantine_forks_predictive_and_operational_evidence(
    tmp_path: Path,
) -> None:
    quarantine_source = "repositories/daily_actual_fallback.py"
    affected_lanes = {"predictive_validation", "operational_legacy"}
    assert all(
        quarantine_source in _LANE_SOURCE_FILES[lane]
        for lane in affected_lanes
    )
    assert all(
        quarantine_source not in paths
        for lane, paths in _LANE_SOURCE_FILES.items()
        if lane not in affected_lanes
    )

    package_root = tmp_path / "src" / "stratus"
    source_path = package_root / quarantine_source
    source_path.parent.mkdir(parents=True)
    source_path.write_text("first training-truth policy", encoding="utf-8")
    first, _ = _source_file_manifests(package_root, tmp_path)
    source_path.write_text("second training-truth policy", encoding="utf-8")
    second, _ = _source_file_manifests(package_root, tmp_path)

    for lane in affected_lanes:
        first_row = next(
            row for row in first[lane] if row["path"] == quarantine_source
        )
        second_row = next(
            row for row in second[lane] if row["path"] == quarantine_source
        )
        assert first_row["sha256"] != second_row["sha256"]


def test_truth_quarantine_operational_lane_closes_transitive_dependencies() -> None:
    required_sources = {
        "db/models.py",
        "jobs/audit_health.py",
        "repositories/daily_actual_fallback.py",
        "repositories/resolution_reconciliation.py",
        "repositories/station_reliability.py",
        "repositories/training_truth.py",
        "services/station_reliability.py",
    }
    assert required_sources.issubset(_LANE_SOURCE_FILES["operational_legacy"])
    required_schema = {
        "migrations/versions/20260714_0034_resolution_reconciliation_lifecycle.py",
        "migrations/versions/20260715_0039_official_resolution_source_evidence.py",
        "migrations/versions/20260717_0042_station_reliability.py",
    }
    assert required_schema.issubset(
        _LANE_REPOSITORY_SOURCE_FILES["operational_legacy"]
    )


def test_contract_creation_and_binding_sources_are_in_both_truth_lanes() -> None:
    dependencies = {
        "collectors/gamma.py",
        "db/models.py",
        "operator_api.py",
        "jobs/discover_markets.py",
        "parsers/market_rules.py",
        "repositories/accuracy.py",
        "repositories/markets.py",
        "repositories/official_resolution.py",
        "services/official_resolution.py",
        "services/official_resolution_artifacts.py",
        "services/resolution_contracts.py",
    }

    assert dependencies <= set(_LANE_SOURCE_FILES["predictive_validation"])
    assert dependencies <= set(_LANE_SOURCE_FILES["official_resolution_truth"])


def test_0041_sql_is_hashed_into_predictive_and_execution_lanes(
    tmp_path: Path,
) -> None:
    migration = "migrations/versions/20260715_0041_execution_fill_projection.py"
    assert migration in _LANE_REPOSITORY_SOURCE_FILES["predictive_validation"]
    assert migration in _LANE_REPOSITORY_SOURCE_FILES["execution_evidence"]

    package_root = tmp_path / "src" / "stratus"
    package_root.mkdir(parents=True)
    migration_path = tmp_path / migration
    migration_path.parent.mkdir(parents=True)
    migration_path.write_text("first SQL semantics", encoding="utf-8")
    first, _ = _source_file_manifests(package_root, tmp_path)
    migration_path.write_text("second SQL semantics", encoding="utf-8")
    second, _ = _source_file_manifests(package_root, tmp_path)

    for lane in ("predictive_validation", "execution_evidence"):
        first_row = next(row for row in first[lane] if row["path"] == migration)
        second_row = next(row for row in second[lane] if row["path"] == migration)
        assert first_row["sha256"] != second_row["sha256"]


def test_0043_recovery_semantics_fork_prediction_and_station_decisions() -> None:
    migration = "migrations/versions/20260717_0043_observation_recovery.py"
    for lane in ("predictive_validation", "selection_policy", "station_lifecycle"):
        assert migration in _LANE_REPOSITORY_SOURCE_FILES[lane]
    assert all(
        migration not in paths
        for lane, paths in _LANE_REPOSITORY_SOURCE_FILES.items()
        if lane not in {"predictive_validation", "selection_policy", "station_lifecycle"}
    )
    assert {
        "collectors/iem.py",
        "collectors/open_meteo_previous_runs.py",
        "db/models.py",
        "jobs/bootstrap_iem.py",
        "jobs/bootstrap_previous_runs.py",
        "jobs/finalize_daily_actuals.py",
        "jobs/recover_observations.py",
        "jobs/recalibrate_emos.py",
        "repositories/accuracy.py",
        "repositories/calibration.py",
        "repositories/daily_actuals.py",
        "repositories/historical_forecasts.py",
        "repositories/model_probs.py",
        "repositories/observation_recovery.py",
        "repositories/official_resolution.py",
        "services/daily_actuals.py",
        "services/observation_recovery.py",
        "services/official_resolution.py",
        "services/official_resolution_artifacts.py",
    } <= set(_PREDICTIVE_SOURCE_FILES)


def test_recovery_job_change_forks_every_label_consuming_lane(
    tmp_path: Path,
) -> None:
    recovery_job = "jobs/recover_observations.py"
    label_consuming_lanes = {
        "predictive_validation",
        "selection_policy",
        "station_lifecycle",
        "operational_legacy",
    }

    for lane in label_consuming_lanes:
        assert recovery_job in _LANE_SOURCE_FILES[lane]
    for lane in set(LANE_NAMES) - label_consuming_lanes:
        assert recovery_job not in _LANE_SOURCE_FILES[lane]

    package_root = tmp_path / "src" / "stratus"
    recovery_path = package_root / recovery_job
    recovery_path.parent.mkdir(parents=True)
    recovery_path.write_text("first recovery schedule", encoding="utf-8")
    first, _ = _source_file_manifests(package_root, tmp_path)
    recovery_path.write_text("second recovery schedule", encoding="utf-8")
    second, _ = _source_file_manifests(package_root, tmp_path)

    for lane in label_consuming_lanes:
        first_row = next(row for row in first[lane] if row["path"] == recovery_job)
        second_row = next(row for row in second[lane] if row["path"] == recovery_job)
        assert first_row["sha256"] != second_row["sha256"]


def test_station_selection_lanes_track_reconciliation_runtime_and_schema() -> None:
    runtime_dependencies = {
        "jobs/reconcile_resolutions.py",
        "repositories/resolution_reconciliation.py",
        "runtime.py",
        "services/reconciliation.py",
    }
    reconciliation_schema = (
        "migrations/versions/20260714_0034_resolution_reconciliation_lifecycle.py"
    )

    for lane in ("selection_policy", "station_lifecycle"):
        assert runtime_dependencies <= set(_LANE_SOURCE_FILES[lane])
        assert reconciliation_schema in _LANE_REPOSITORY_SOURCE_FILES[lane]


def test_operational_lane_tracks_health_and_continuity_reporting_semantics() -> None:
    assert {
        "config.py",
        "db/session.py",
        "jobs/audit_health.py",
        "repositories/evidence_continuity_reporting.py",
        "runtime.py",
    } <= set(_LANE_SOURCE_FILES["operational_legacy"])
    assert "runtime_db_boundary.py" in _LANE_SOURCE_FILES["operational_legacy"]
    assert {
        ".dockerignore",
        "Dockerfile",
        "alembic.ini",
        "docker-compose.yml",
        "migrations/env.py",
        "migrations/versions/20260719_0044_resolution_contract_integrity.py",
        "ops/postgres/stratus-pg_hba.conf",
        "pyproject.toml",
        "scripts/Start-StratusDevApi.ps1",
        "scripts/stratus-runtime-entrypoint.sh",
        "uv.lock",
    } <= set(_LANE_REPOSITORY_SOURCE_FILES["operational_legacy"])


def test_operational_lane_tracks_storage_capacity_safety_semantics() -> None:
    assert {
        "jobs/audit_storage_capacity.py",
        "jobs/fetch_prices.py",
        "repositories/storage_capacity.py",
        "services/storage_capacity.py",
    } <= set(_LANE_SOURCE_FILES["operational_legacy"])


def test_0044_sql_is_hashed_into_every_affected_continuity_lane() -> None:
    migration = "migrations/versions/20260719_0044_resolution_contract_integrity.py"

    for lane in (
        "predictive_validation",
        "official_resolution_truth",
        "station_lifecycle",
        "operational_legacy",
    ):
        assert migration in _LANE_REPOSITORY_SOURCE_FILES[lane]


def test_0045_close_precision_semantics_fork_only_predictive_validation() -> None:
    migration = (
        "migrations/versions/"
        "20260728_0045_forecast_evaluation_close_precision.py"
    )

    assert migration in _LANE_REPOSITORY_SOURCE_FILES["predictive_validation"]
    assert all(
        migration not in paths
        for lane, paths in _LANE_REPOSITORY_SOURCE_FILES.items()
        if lane != "predictive_validation"
    )


def test_0046_accuracy_overview_indexes_fork_only_operational_lane() -> None:
    migration = "migrations/versions/20260730_0046_accuracy_overview_indexes.py"

    assert migration in _LANE_REPOSITORY_SOURCE_FILES["operational_legacy"]
    assert all(
        migration not in paths
        for lane, paths in _LANE_REPOSITORY_SOURCE_FILES.items()
        if lane != "operational_legacy"
    )


def test_locked_dependency_graph_is_hashed_into_every_lane(tmp_path: Path) -> None:
    for lane in LANE_NAMES:
        assert {"pyproject.toml", "uv.lock"} <= set(
            _LANE_REPOSITORY_SOURCE_FILES[lane]
        )

    package_root = tmp_path / "src" / "stratus"
    package_root.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text("version = 1\n", encoding="utf-8")
    first, _ = _source_file_manifests(package_root, tmp_path)
    lock_path.write_text("version = 2\n", encoding="utf-8")
    second, _ = _source_file_manifests(package_root, tmp_path)

    for lane in LANE_NAMES:
        first_lock = next(row for row in first[lane] if row["path"] == "uv.lock")
        second_lock = next(row for row in second[lane] if row["path"] == "uv.lock")
        assert first_lock["sha256"] != second_lock["sha256"]

    parent_sources = {
        lane: (*rows, {"path": "uv.lock", "sha256": "d" * 64})
        for lane, rows in _sources().items()
    }
    candidate_sources = {
        lane: (*rows, {"path": "uv.lock", "sha256": "e" * 64})
        for lane, rows in _sources().items()
    }
    parent = _build(sources=parent_sources)
    candidate = _build(sources=candidate_sources)
    decisions = decide_evidence_lane_actions(candidate, parent.lane_identity_hashes)
    assert {decision.action for decision in decisions} == {"fork"}


def test_execution_lane_tracks_scheduler_admission_semantics() -> None:
    assert "runtime.py" in _LANE_SOURCE_FILES["execution_evidence"]


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {"release_image_digest": "4" * 64, **updates}
    return Settings(**values)  # type: ignore[arg-type]


def _runtime(version: str, marker: str) -> dict[str, object]:
    return {"definition_version": version, "definition_hash": marker * 64}


def _snapshot() -> EvidenceReleaseDatabaseSnapshot:
    return EvidenceReleaseDatabaseSnapshot(
        alembic_heads=(ALEMBIC_HEAD,),
        evidence_continuity_runtime=_runtime("evidence-continuity-v1", "5"),
        prospective_validation_runtime=_runtime("prospective-validation-v1", "6"),
        forecast_provenance_runtime=_runtime("forecast-provenance-v1", "7"),
        observation_recovery_runtime={
            **_runtime("observation-recovery-v1", "d"),
            "source_protocol": "iem-asos-recovery-v1",
            "source_endpoint_sha256": "f" * 64,
            "parser_version": "iem-csv-evidence-v2",
            "completeness_version": "daily-extremes-v1",
            "retention_policy_version": "iem-retention-review-v1",
            "retention_policy_sha256": "e" * 64,
        },
        execution_cost_runtime={
            **_runtime("execution-cost-v1", "8"),
            "paper_execution_evidence": "paper-clob-cross-v1",
            "live_reconciliation_evidence": "live-sdk-reconciliation-v1",
            "postfill_mark_protocol": "clob-dual-postfill-v1",
            "mark_horizons_seconds": (1800, 300),
            "mark_grace_seconds": 300,
            "live_fill_snapshot_max_age_seconds": 120,
        },
        official_resolution_runtime={
            **_runtime("official-resolution-evidence-v1", "9"),
            "license_protocol": "official-resolution-license-v1",
            "response_protocol": "official-resolution-source-v1",
            "extraction_protocol": "official-resolution-extraction-v1",
        },
        active_stations=(
            {
                "icao": "KLGA",
                "city": "New York City",
                "latitude": Decimal("40.777000"),
                "longitude": Decimal("-73.872600"),
                "timezone": "America/New_York",
                "iem_network": "NY_ASOS",
                "iem_station_id": "LGA",
                "stage": "paper",
            },
            {
                "icao": "EGLC",
                "city": "London",
                "latitude": Decimal("51.505300"),
                "longitude": Decimal("0.055300"),
                "timezone": "Europe/London",
                "iem_network": None,
                "iem_station_id": None,
                "stage": "shadow",
            },
        ),
        active_resolution_contracts=(
            {
                "event_id": "event-b",
                "resolution_date": "2026-07-17",
                "rules_hash": "b" * 64,
                "rules_template_sha256": "1" * 64,
                "source_family": "wunderground",
                "source_locator_sha256": "c" * 64,
                "station_code": "KLGA",
                "local_timezone": "America/New_York",
                "statistic": "high",
                "source_unit": "F",
                "decimal_places": 0,
                "finalization_rule": "next_day_first_point",
                "license_status": "restricted_unlicensed",
                "license_locator_sha256": "d" * 64,
                "storage_permitted": False,
            },
            {
                "event_id": "event-a",
                "resolution_date": "2026-07-17",
                "rules_hash": "a" * 64,
                "rules_template_sha256": "2" * 64,
                "source_family": "national",
                "source_locator_sha256": "e" * 64,
                "station_code": "EGLC",
                "local_timezone": "Europe/London",
                "statistic": "low",
                "source_unit": "C",
                "decimal_places": 1,
                "finalization_rule": "source_published",
                "license_status": "unreviewed",
                "license_locator_sha256": None,
                "storage_permitted": False,
            },
        ),
        effective_promotions=(),
    )


def _sources() -> dict[str, tuple[dict[str, object], ...]]:
    return {
        name: (
            {"path": f"services/{name}-b.py", "sha256": "b" * 64},
            {"path": f"services/{name}-a.py", "sha256": "a" * 64},
        )
        for name in LANE_NAMES
    }


def _build(
    *,
    settings: Settings | None = None,
    snapshot: EvidenceReleaseDatabaseSnapshot | None = None,
    sources: dict[str, tuple[dict[str, object], ...]] | None = None,
) -> EvidenceReleaseManifest:
    return compose_evidence_release_manifest(
        settings or _settings(),
        snapshot or _snapshot(),
        material_fingerprint=FINGERPRINT,
        activation_manifest_hash=PROMOTION_HASH,
        uv_lock_sha256=LOCK_HASH,
        lane_source_files=sources or _sources(),
    )


def _changed_lanes(
    before: EvidenceReleaseManifest,
    after: EvidenceReleaseManifest,
) -> set[str]:
    left = before.lane_identity_hashes
    right = after.lane_identity_hashes
    return {name for name in LANE_NAMES if left[name] != right[name]}


@pytest.mark.parametrize(
    "observed_heads",
    [(), ("20260715_0040",), (ALEMBIC_HEAD, "unexpected_branch")],
)
def test_release_manifest_fails_closed_on_database_head_mismatch(
    observed_heads: tuple[str, ...],
) -> None:
    snapshot = replace(_snapshot(), alembic_heads=observed_heads)

    with pytest.raises(EvidenceReleaseManifestError, match="Alembic head mismatch"):
        _build(snapshot=snapshot)


def test_image_digest_changes_only_the_global_release_manifest() -> None:
    first = _build()
    second = _build(settings=_settings(release_image_digest="a" * 64))

    assert first.manifest_hash != second.manifest_hash
    assert first.lane_identity_hashes == second.lane_identity_hashes


@pytest.mark.parametrize(
    ("field", "value", "expected_lane"),
    [
        ("calibration_window_days", 181, "predictive_validation"),
        (
            "iem_asos_url",
            "https://example.invalid/reviewed-source-change",
            "predictive_validation",
        ),
        ("signal_max_edge", 0.15, "selection_policy"),
        ("signal_min_edge", 0.06, "selection_policy"),
        ("paper_order_ttl_minutes", 31, "execution_evidence"),
        ("kelly_multiplier", 0.2, "operational_legacy"),
        ("target_cities", ("London",), "station_lifecycle"),
        ("official_resolution_artifact_max_bytes", 1_000_001, "official_resolution_truth"),
    ],
)
def test_material_config_changes_only_its_allowlisted_lane(
    field: str,
    value: object,
    expected_lane: str,
) -> None:
    before = _build()
    after = _build(settings=_settings(**{field: value}))

    assert _changed_lanes(before, after) == {expected_lane}


@pytest.mark.parametrize("lane_name", LANE_NAMES)
def test_scoped_source_change_forks_only_that_lane(lane_name: str) -> None:
    sources = _sources()
    row = dict(sources[lane_name][0])
    row["sha256"] = "c" * 64
    sources[lane_name] = (row, *sources[lane_name][1:])

    assert _changed_lanes(_build(), _build(sources=sources)) == {lane_name}


def test_database_material_is_lane_scoped() -> None:
    baseline = _build()
    station = dict(_snapshot().active_stations[0])
    station["stage"] = "approved"
    station_changed = replace(
        _snapshot(),
        active_stations=(station, *_snapshot().active_stations[1:]),
    )
    contract = dict(_snapshot().active_resolution_contracts[0])
    contract["decimal_places"] = 1
    contract_changed = replace(_snapshot(), active_resolution_contracts=(contract,))
    execution = dict(_snapshot().execution_cost_runtime or {})
    execution["mark_grace_seconds"] = 301
    execution_changed = replace(_snapshot(), execution_cost_runtime=execution)

    assert _changed_lanes(baseline, _build(snapshot=station_changed)) == {
        "predictive_validation",
        "station_lifecycle",
    }
    assert _changed_lanes(baseline, _build(snapshot=contract_changed)) == {
        "official_resolution_truth",
        "predictive_validation",
        "station_lifecycle",
    }
    assert _changed_lanes(baseline, _build(snapshot=execution_changed)) == {
        "execution_evidence"
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rules_template_sha256", "9" * 64),
        ("source_locator_sha256", "1" * 64),
        ("station_code", "KJFK"),
        ("local_timezone", "UTC"),
        ("statistic", "low"),
        ("source_unit", "C"),
        ("decimal_places", 1),
        ("finalization_rule", "source_finalized"),
        ("license_locator_sha256", "4" * 64),
    ],
)
def test_each_contract_semantic_change_forks_truth_and_prediction(
    field: str,
    value: object,
) -> None:
    snapshot = _snapshot()
    contract = dict(snapshot.active_resolution_contracts[0])
    contract[field] = value
    active_stations = snapshot.active_stations
    if field == "station_code":
        remapped_station = dict(snapshot.active_stations[0])
        remapped_station["icao"] = value
        active_stations = (remapped_station, *snapshot.active_stations)
    elif field == "local_timezone":
        remapped_station = dict(snapshot.active_stations[0])
        remapped_station["timezone"] = value
        active_stations = (remapped_station, *snapshot.active_stations[1:])
    changed = replace(
        snapshot,
        active_stations=active_stations,
        active_resolution_contracts=(
            contract,
            *snapshot.active_resolution_contracts[1:],
        ),
    )

    assert _changed_lanes(_build(snapshot=snapshot), _build(snapshot=changed)) == {
        "official_resolution_truth",
        "predictive_validation",
        "station_lifecycle",
    }


def test_valid_source_and_license_policy_change_forks_truth_and_prediction() -> None:
    snapshot = _snapshot()
    contract = dict(snapshot.active_resolution_contracts[0])
    contract.update(
        source_family="hko",
        license_status="open_data_attribution",
        storage_permitted=True,
        license_locator_sha256="8" * 64,
    )
    changed = replace(snapshot, active_resolution_contracts=(contract,))

    assert _changed_lanes(_build(snapshot=snapshot), _build(snapshot=changed)) == {
        "official_resolution_truth",
        "predictive_validation",
        "station_lifecycle",
    }


def test_daily_contract_inventory_growth_does_not_fork_evidence_lanes() -> None:
    snapshot = _snapshot()
    next_daily_event = dict(snapshot.active_resolution_contracts[0])
    next_daily_event["event_id"] = "event-next-day"
    next_daily_event["resolution_date"] = "2026-07-18"
    next_daily_event["rules_hash"] = "f" * 64
    grown = replace(
        snapshot,
        active_resolution_contracts=(
            *snapshot.active_resolution_contracts,
            next_daily_event,
        ),
    )

    before = _build(snapshot=snapshot)
    after = _build(snapshot=grown)

    assert before.manifest_hash == after.manifest_hash
    assert before.lane_identity_hashes == after.lane_identity_hashes
    for lane_name in (
        "official_resolution_truth",
        "predictive_validation",
        "station_lifecycle",
    ):
        manifest = next(
            lane.manifest for lane in after.lanes if lane.lane_name == lane_name
        )
        rendered = repr(manifest)
        lane_data = manifest["lane_data"]
        assert isinstance(lane_data, dict)
        assert "active_resolution_contracts" not in lane_data
        assert "resolution_contract_semantics" in lane_data
        assert "event-next-day" not in rendered
        assert "rules_hash" not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [("station_code", "EGLC"), ("local_timezone", "Europe/London")],
)
def test_contract_station_mapping_change_forks_station_lane(
    field: str,
    value: str,
) -> None:
    snapshot = _snapshot()
    contract = dict(snapshot.active_resolution_contracts[0])
    contract[field] = value
    # Keep the station/timezone pair internally valid while changing either
    # input under test.
    if field == "station_code":
        contract["local_timezone"] = "Europe/London"
        active_stations = snapshot.active_stations
    else:
        remapped_station = dict(snapshot.active_stations[0])
        remapped_station["timezone"] = value
        active_stations = (remapped_station, *snapshot.active_stations[1:])
    changed = replace(
        snapshot,
        active_stations=active_stations,
        active_resolution_contracts=(
            contract,
            *snapshot.active_resolution_contracts[1:],
        ),
    )

    assert "station_lifecycle" in _changed_lanes(
        _build(snapshot=snapshot),
        _build(snapshot=changed),
    )


def test_operational_build_boundary_change_produces_a_fork_decision() -> None:
    parent = _build()
    sources = _sources()
    changed_row = dict(sources["operational_legacy"][0])
    changed_row["sha256"] = "c" * 64
    sources["operational_legacy"] = (
        changed_row,
        *sources["operational_legacy"][1:],
    )
    candidate = _build(sources=sources)

    decisions = decide_evidence_lane_actions(candidate, parent.lane_identity_hashes)
    actions = {decision.lane_name: decision.action for decision in decisions}
    assert actions["operational_legacy"] == "fork"
    assert {action for lane, action in actions.items() if lane != "operational_legacy"} == {
        "continue"
    }


def test_unclassified_contract_manifest_field_fails_closed() -> None:
    snapshot = _snapshot()
    contract = dict(snapshot.active_resolution_contracts[0])
    contract["new_unclassified_semantic"] = "material"

    with pytest.raises(EvidenceReleaseManifestError, match="unclassified"):
        _build(snapshot=replace(snapshot, active_resolution_contracts=(contract,)))


def test_invalid_exact_rules_hash_fails_closed_even_though_it_is_not_lane_identity() -> None:
    snapshot = _snapshot()
    contract = dict(snapshot.active_resolution_contracts[0])
    contract["rules_hash"] = "not-a-sha256"

    with pytest.raises(EvidenceReleaseManifestError, match="rules hash is invalid"):
        _build(snapshot=replace(snapshot, active_resolution_contracts=(contract,)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rules_template_sha256", "not-a-sha256"),
        ("resolution_date", "2026-02-30"),
        ("source_family", "unsupported"),
        ("source_locator_sha256", "not-a-sha256"),
        ("license_locator_sha256", "not-a-sha256"),
        ("statistic", "average"),
        ("source_unit", "K"),
        ("decimal_places", 4),
        ("finalization_rule", "daily_final"),
        ("local_timezone", "Not/A_Zone"),
        ("storage_permitted", "false"),
    ],
)
def test_malformed_contract_semantics_fail_closed(field: str, value: object) -> None:
    snapshot = _snapshot()
    contract = dict(snapshot.active_resolution_contracts[0])
    contract[field] = value

    with pytest.raises(EvidenceReleaseManifestError, match=f"{field} is invalid"):
        _build(snapshot=replace(snapshot, active_resolution_contracts=(contract,)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("license_status", "approved"),
        ("storage_permitted", True),
        ("license_locator_sha256", None),
    ],
)
def test_inconsistent_contract_license_policy_fails_closed(
    field: str,
    value: object,
) -> None:
    snapshot = _snapshot()
    contract = dict(snapshot.active_resolution_contracts[0])
    contract[field] = value

    with pytest.raises(EvidenceReleaseManifestError, match="license policy is inconsistent"):
        _build(snapshot=replace(snapshot, active_resolution_contracts=(contract,)))


def test_input_order_is_canonical_and_numbers_are_strings() -> None:
    snapshot = _snapshot()
    reversed_snapshot = replace(
        snapshot,
        active_stations=tuple(reversed(snapshot.active_stations)),
        active_resolution_contracts=tuple(reversed(snapshot.active_resolution_contracts)),
    )
    sources = {
        name: tuple(reversed(rows)) for name, rows in _sources().items()
    }
    first = _build()
    second = _build(snapshot=reversed_snapshot, sources=sources)

    assert first == second
    station_lane = next(
        lane for lane in first.lanes if lane.lane_name == "station_lifecycle"
    )
    station_text = str(station_lane.manifest)
    assert "40.777" in station_text
    assert "Decimal" not in station_text


def test_manifests_never_contain_secrets_raw_rules_or_source_urls() -> None:
    candidate = _build(
        settings=_settings(
            polygon_private_key="0x-private",
            operator_api_key="operator-secret",
            telegram_bot_token="telegram-secret",
        )
    )
    rendered = repr(candidate).lower()

    assert "0x-private" not in rendered
    assert "operator-secret" not in rendered
    assert "telegram-secret" not in rendered
    assert "rules_raw" not in rendered
    assert "source_url" not in rendered
    assert "://" not in rendered


def test_forbidden_key_scan_fails_closed() -> None:
    bad_station = {**_snapshot().active_stations[0], "api_key": "leak"}
    snapshot = replace(_snapshot(), active_stations=(bad_station,))

    with pytest.raises(EvidenceReleaseManifestError, match="forbidden"):
        _build(snapshot=snapshot)


def test_lane_decisions_are_first_continue_fork_and_freeze() -> None:
    candidate = _build()
    first = decide_evidence_lane_actions(candidate, None)
    continued = decide_evidence_lane_actions(candidate, candidate.lane_identity_hashes)
    changed_parent = dict(candidate.lane_identity_hashes)
    changed_parent["selection_policy"] = "f" * 64
    changed = decide_evidence_lane_actions(candidate, changed_parent)
    incomplete_snapshot = replace(_snapshot(), execution_cost_runtime=None)
    incomplete = decide_evidence_lane_actions(_build(snapshot=incomplete_snapshot), None)

    assert {item.action for item in first} == {"fork"}
    assert {item.action for item in continued} == {"continue"}
    assert next(item for item in changed if item.lane_name == "selection_policy").action == "fork"
    frozen = next(item for item in incomplete if item.lane_name == "execution_evidence")
    assert frozen.action == "freeze"
    assert "execution_cost_runtime" in frozen.rationale


def test_zero_image_and_unknown_parent_fail_closed() -> None:
    with pytest.raises(EvidenceReleaseManifestError, match="nonzero"):
        _build(settings=_settings(release_image_digest="0" * 64))

    candidate = _build()
    decisions = decide_evidence_lane_actions(candidate, {})
    assert {decision.action for decision in decisions} == {"freeze"}
