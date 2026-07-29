from pathlib import Path

import pytest

from stratus.config import Settings
from stratus.repositories.analytics import probability_pipeline_version
from stratus.services.pipeline_identity import (
    pipeline_cohort_matches_base,
    probability_pipeline_base_cohort,
    probability_pipeline_fingerprint,
    probability_pipeline_metadata,
)


def test_pipeline_fingerprint_is_deterministic_and_material_config_sensitive() -> None:
    baseline = Settings()
    assert probability_pipeline_fingerprint(baseline) == probability_pipeline_fingerprint(
        baseline
    )

    changed = Settings(accuracy_stack_l2=baseline.accuracy_stack_l2 + 0.01)
    assert probability_pipeline_fingerprint(changed) != probability_pipeline_fingerprint(
        baseline
    )

    next_version = Settings(
        probability_pipeline_version=f"{baseline.probability_pipeline_version}-next"
    )
    assert probability_pipeline_fingerprint(next_version) != probability_pipeline_fingerprint(
        baseline
    )

    recovery_policy = Settings(
        daily_actual_recovery_lookback_days=(
            baseline.daily_actual_recovery_lookback_days + 1
        )
    )
    assert probability_pipeline_fingerprint(
        recovery_policy
    ) != probability_pipeline_fingerprint(baseline)

    recovery_endpoint = Settings(
        iem_asos_url="https://example.invalid/reviewed-source-change"
    )
    assert probability_pipeline_fingerprint(
        recovery_endpoint
    ) != probability_pipeline_fingerprint(baseline)


def test_release_image_witness_is_normalized_but_not_a_probability_input() -> None:
    baseline = Settings(_env_file=None)
    digest = "ab" * 32
    witnessed = Settings(_env_file=None, release_image_digest=f"sha256:{digest.upper()}")

    assert witnessed.release_image_digest == digest
    assert probability_pipeline_fingerprint(witnessed) == probability_pipeline_fingerprint(
        baseline
    )


@pytest.mark.parametrize("digest", ["a" * 63, "a" * 65, "g" * 64, ""])
def test_release_image_witness_rejects_invalid_digest(digest: str) -> None:
    with pytest.raises(ValueError, match="release image digest"):
        Settings(_env_file=None, release_image_digest=digest)


@pytest.mark.parametrize(
    "relative",
    [
        "collectors/clob.py",
        "collectors/gamma.py",
        "collectors/iem.py",
        "collectors/open_meteo_previous_runs.py",
        "db/models.py",
        "jobs/bootstrap_iem.py",
        "jobs/bootstrap_previous_runs.py",
        "jobs/recover_observations.py",
        "repositories/accuracy.py",
        "repositories/calibration.py",
        "repositories/historical_forecasts.py",
        "repositories/model_probs.py",
        "repositories/observation_recovery.py",
        "repositories/official_resolution.py",
        "repositories/clob_snapshots.py",
        "repositories/gamma_outcomes.py",
        "services/clob_evidence.py",
        "services/observation_recovery.py",
    ],
)
def test_pipeline_fingerprint_covers_market_and_resolution_semantics(
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    settings = Settings()
    baseline = probability_pipeline_fingerprint(settings)
    package_root = Path(__file__).resolve().parents[1] / "src" / "stratus"
    target = (package_root / relative).resolve()
    original_read_bytes = Path.read_bytes

    def changed_read_bytes(path: Path) -> bytes:
        content = original_read_bytes(path)
        return content + b"\n# identity-test" if path.resolve() == target else content

    monkeypatch.setattr(Path, "read_bytes", changed_read_bytes)

    assert probability_pipeline_fingerprint(settings) != baseline


def test_pipeline_base_cohort_match_is_exact_and_allows_only_manifest_suffix() -> None:
    settings = Settings()
    fingerprint = probability_pipeline_fingerprint(settings)
    current_base = probability_pipeline_base_cohort(
        settings,
        fingerprint=fingerprint,
    )

    assert pipeline_cohort_matches_base(stored=current_base, current_base=current_base)
    assert pipeline_cohort_matches_base(
        stored=f"{current_base}+promotions-{'a' * 16}",
        current_base=current_base,
    )
    assert not pipeline_cohort_matches_base(
        stored=f"{current_base}-other",
        current_base=current_base,
    )
    assert not pipeline_cohort_matches_base(
        stored=f"{current_base}+promotions-{'a' * 15}",
        current_base=current_base,
    )


def test_pipeline_metadata_exposes_semantic_version_and_hashed_cohort() -> None:
    metadata = probability_pipeline_metadata(Settings())

    assert metadata["version"] == "stratus-accuracy-v4-20260713"
    assert len(metadata["fingerprint"]) == 16
    assert metadata["cohort"] == f"{metadata['version']}+{metadata['fingerprint']}"
    assert probability_pipeline_version({"pipeline": metadata}) == metadata["cohort"]
    assert (
        probability_pipeline_version({"pipeline": {"version": "legacy-v3"}})
        == "legacy-v3"
    )


def test_pipeline_metadata_binds_a_valid_activation_manifest_to_the_cohort() -> None:
    manifest_hash = "AB" * 32

    metadata = probability_pipeline_metadata(
        Settings(),
        activation_manifest_hash=manifest_hash,
    )

    assert metadata["activation_manifest_hash"] == manifest_hash.lower()
    assert metadata["cohort"].endswith(f"+promotions-{manifest_hash[:16].lower()}")
    assert probability_pipeline_version({"pipeline": metadata}) == metadata["cohort"]


@pytest.mark.parametrize("manifest_hash", ["a" * 63, "a" * 65, "z" * 64, ""])
def test_pipeline_metadata_rejects_invalid_activation_manifest_hash(
    manifest_hash: str,
) -> None:
    with pytest.raises(ValueError, match="64-character SHA-256"):
        probability_pipeline_metadata(
            Settings(),
            activation_manifest_hash=manifest_hash,
        )
