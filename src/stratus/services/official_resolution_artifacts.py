from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

_IMPORT_PATH_RE = re.compile(
    r"^imports/(?P<source_family>[a-z0-9][a-z0-9_-]{0,31})/"
    r"(?P<contract_id>[1-9][0-9]*)/(?P<sha256>[0-9a-f]{64})\.bin$"
)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class AuthorizedArtifactError(ValueError):
    """An offline official-source artifact is outside its authorized root or identity."""


@dataclass(frozen=True, slots=True)
class AuthorizedArtifact:
    relative_path: str
    body: bytes
    sha256: str


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", None)
    if attributes is None:
        return path.is_symlink()
    return bool(attributes & _REPARSE_POINT)


def _validate_no_link_components(root: Path, candidate: Path) -> None:
    relative = candidate.relative_to(root)
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink() or _is_reparse_point(current):
            raise AuthorizedArtifactError("artifact path may not traverse a link or reparse point")


def read_authorized_artifact(
    root: Path | None,
    relative_path: str,
    *,
    expected_source_family: str,
    expected_contract_id: int,
    max_bytes: int,
) -> AuthorizedArtifact:
    """Read one hash-addressed artifact beneath the configured import root.

    The filename is an input identity, never evidence authority: the bytes are
    read by the server and hashed again before they may be sealed in Postgres.
    """

    if root is None:
        raise AuthorizedArtifactError(
            "official-resolution artifact import is disabled until its root is configured"
        )
    if not 1_024 <= max_bytes <= 10_000_000:
        raise AuthorizedArtifactError("artifact byte limit is outside [1024, 10000000]")
    if "\\" in relative_path or ":" in relative_path:
        raise AuthorizedArtifactError("artifact reference must use a normalized relative path")
    match = _IMPORT_PATH_RE.fullmatch(relative_path)
    if match is None:
        raise AuthorizedArtifactError("artifact reference does not match the authorized layout")
    if match.group("source_family") != expected_source_family:
        raise AuthorizedArtifactError("artifact source family does not match the contract")
    if int(match.group("contract_id")) != expected_contract_id:
        raise AuthorizedArtifactError("artifact contract id does not match the request")

    try:
        resolved_root = root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise AuthorizedArtifactError("configured artifact root is unavailable") from exc
    if not resolved_root.is_dir() or _is_reparse_point(resolved_root):
        raise AuthorizedArtifactError("configured artifact root must be a real directory")

    candidate = resolved_root.joinpath(*relative_path.split("/"))
    try:
        resolved_candidate = candidate.resolve(strict=True)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise AuthorizedArtifactError("artifact path escapes or is absent from its root") from exc
    _validate_no_link_components(resolved_root, candidate)
    if not resolved_candidate.is_file():
        raise AuthorizedArtifactError("artifact reference is not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved_candidate, flags)
    except OSError as exc:
        raise AuthorizedArtifactError("artifact could not be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > max_bytes:
            raise AuthorizedArtifactError(
                "artifact is empty, non-regular, or exceeds its byte limit"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            body = handle.read(max_bytes + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(body) > max_bytes or len(body) != before.st_size:
        raise AuthorizedArtifactError("artifact changed or exceeded its limit while being read")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise AuthorizedArtifactError("artifact changed while being read")
    digest = hashlib.sha256(body).hexdigest()
    if digest != match.group("sha256"):
        raise AuthorizedArtifactError("artifact bytes do not match the hash-addressed filename")
    return AuthorizedArtifact(relative_path=relative_path, body=body, sha256=digest)


__all__ = [
    "AuthorizedArtifact",
    "AuthorizedArtifactError",
    "read_authorized_artifact",
]
