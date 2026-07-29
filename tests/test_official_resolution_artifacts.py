from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from stratus.services.official_resolution_artifacts import (
    AuthorizedArtifactError,
    read_authorized_artifact,
)


def _artifact(root: Path, body: bytes, *, contract_id: int = 7) -> str:
    digest = hashlib.sha256(body).hexdigest()
    relative = f"imports/wunderground/{contract_id}/{digest}.bin"
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True)
    target.write_bytes(body)
    return relative


def test_reads_hash_addressed_artifact_beneath_configured_root(tmp_path: Path) -> None:
    relative = _artifact(tmp_path, b'{"temperatureMax":["80"]}')
    artifact = read_authorized_artifact(
        tmp_path,
        relative,
        expected_source_family="wunderground",
        expected_contract_id=7,
        max_bytes=4096,
    )
    assert artifact.relative_path == relative
    assert artifact.sha256 == hashlib.sha256(artifact.body).hexdigest()


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.bin",
        "/absolute.bin",
        "imports\\wunderground\\7\\dead.bin",
        "imports/wunderground/7/C:evil.bin",
        "imports/wunderground/0/" + "a" * 64 + ".bin",
    ],
)
def test_rejects_unsafe_or_noncanonical_references(tmp_path: Path, relative: str) -> None:
    with pytest.raises(AuthorizedArtifactError):
        read_authorized_artifact(
            tmp_path,
            relative,
            expected_source_family="wunderground",
            expected_contract_id=7,
            max_bytes=4096,
        )


def test_rejects_wrong_scope_hash_and_disabled_root(tmp_path: Path) -> None:
    relative = _artifact(tmp_path, b"licensed exact bytes")
    with pytest.raises(AuthorizedArtifactError, match="source family"):
        read_authorized_artifact(
            tmp_path,
            relative,
            expected_source_family="other",
            expected_contract_id=7,
            max_bytes=4096,
        )
    with pytest.raises(AuthorizedArtifactError, match="contract id"):
        read_authorized_artifact(
            tmp_path,
            relative,
            expected_source_family="wunderground",
            expected_contract_id=8,
            max_bytes=4096,
        )
    path = tmp_path.joinpath(*relative.split("/"))
    path.write_bytes(b"tampered")
    with pytest.raises(AuthorizedArtifactError, match="hash-addressed"):
        read_authorized_artifact(
            tmp_path,
            relative,
            expected_source_family="wunderground",
            expected_contract_id=7,
            max_bytes=4096,
        )
    with pytest.raises(AuthorizedArtifactError, match="disabled"):
        read_authorized_artifact(
            None,
            relative,
            expected_source_family="wunderground",
            expected_contract_id=7,
            max_bytes=4096,
        )


def test_rejects_symlink_escape_when_platform_supports_it(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    body = b"outside evidence"
    digest = hashlib.sha256(body).hexdigest()
    target = outside / f"{digest}.bin"
    target.write_bytes(body)
    link_parent = tmp_path / "imports" / "wunderground" / "7"
    link_parent.parent.mkdir(parents=True)
    try:
        link_parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")
    with pytest.raises(AuthorizedArtifactError):
        read_authorized_artifact(
            tmp_path,
            f"imports/wunderground/7/{digest}.bin",
            expected_source_family="wunderground",
            expected_contract_id=7,
            max_bytes=4096,
        )
