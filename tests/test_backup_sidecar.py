import os
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest


def _backup_service() -> str:
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    return compose.split("\n  backup:\n", 1)[1].split("\n  backup-offsite:\n", 1)[0]


def _offsite_service() -> str:
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    return compose.split("\n  backup-offsite:\n", 1)[1].split("\n  telegram:\n", 1)[0]


def test_backup_sidecar_serializes_and_publishes_atomically() -> None:
    service = _backup_service()

    assert "flock -n 9" in service
    assert ".stratus_$${backup_id}.backup.partial" in service
    assert "stratus_$${backup_id}.backup" in service
    assert 'mv -T "$$partial_dir" "$$target_dir"' in service
    assert "timeout -s TERM 21600 pg_dump" in service
    assert 'backup_id="$${stamp}_$${instance}_$$$$"' in service
    rendered = service.replace("$$", "$")
    assert 'backup_id="${stamp}_${instance}_$$"' in rendered


def test_backup_sidecar_publishes_integrity_manifest_and_backs_off() -> None:
    service = _backup_service()

    assert "pg_restore --list" in service
    assert "stat -c '%s'" in service
    assert "wc -c" not in service
    assert "archive_sha256=" in service
    assert "catalog_sha256=" in service
    assert "schema_head=" in service
    assert "schema_head_before=" in service
    assert "schema_head_after=" in service
    assert 'test "$$schema_before" = "$$schema_after"' in service
    assert "catalog_verified=true" in service
    assert "schema_stable_during_dump=true" in service
    assert "full_restore_verified=false" in service
    assert "retry_seconds=$$((retry_seconds * 2))" in service
    assert 'retry_seconds="$$max_retry_seconds"' in service
    assert 'rm -rf -- "$$partial_dir"' in service
    assert "restart: \"on-failure:5\"" in service


def test_backup_sidecar_does_not_suppress_fail_fast_inside_if_condition() -> None:
    service = _backup_service()

    assert 'if create_backup "$$partial_dir"' not in service
    assert (
        'create_backup "$$partial_dir" "$$target_dir" "$$started_at"\n'
        "          backup_status=$$?"
    ) in service
    assert 'if [ "$$backup_status" -eq 0 ]' in service
    assert '--file="$$partial_dir/stratus.dump" || exit 1' in service
    assert '>"$$partial_dir/catalog.list" || exit 1' in service
    assert 'test -s "$$partial_dir/catalog.list" || exit 1' in service


def test_backup_sidecar_revalidates_and_quarantines_before_acceptance() -> None:
    service = _backup_service()

    publish_at = service.index('echo "published $$target_dir"')
    dump_guard_at = service.index('[ -s "$$target_dir/stratus.dump" ]')
    catalog_guard_at = service.index('[ -s "$$target_dir/catalog.list" ]')
    manifest_guard_at = service.index("'^archive_bytes=[1-9][0-9]*$$'")
    assert dump_guard_at < publish_at
    assert catalog_guard_at < publish_at
    assert manifest_guard_at < publish_at
    assert 'accepted_restore_point "$$target_dir"' in service[:publish_at]
    assert 'rejected_dir="$${target_dir}.rejected-incomplete"' in service
    assert 'mv -T "$$target_dir" "$$rejected_dir" || exit 1' in service


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is unavailable")
def test_backup_function_fails_inside_a_conditional_caller(tmp_path: Path) -> None:
    service = _backup_service()
    start = service.index("        create_backup() (")
    end = service.index("\n        prune_published()", start)
    function = textwrap.dedent(service[start:end]).replace("$$", "$")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    psql = bin_dir / "psql"
    psql.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    psql.chmod(0o755)
    timeout = bin_dir / "timeout"
    timeout.write_text(
        "#!/bin/sh\n"
        'for argument in "$@"; do\n'
        '  case "$argument" in\n'
        '    --file=*) : > "${argument#--file=}" ;;\n'
        "  esac\n"
        "done\n"
        "exit 42\n",
        encoding="utf-8",
    )
    timeout.chmod(0o755)
    pg_restore = bin_dir / "pg_restore"
    pg_restore.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    pg_restore.chmod(0o755)
    partial = tmp_path / ".candidate.backup.partial"
    target = tmp_path / "candidate.backup"
    script = (
        "set -u\n"
        f"{function}\n"
        'if create_backup "$1" "$2" "2026-07-20T00:00:00Z"; then\n'
        "  exit 90\n"
        "fi\n"
        'test ! -e "$1"\n'
        'test ! -e "$2"\n'
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PGDATABASE"] = "stratus"

    completed = subprocess.run(
        [
            shutil.which("sh") or "sh",
            "-c",
            script,
            "backup-regression",
            str(partial),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert not partial.exists()
    assert not target.exists()


def test_backup_retention_is_success_gated_and_keeps_three_restore_points() -> None:
    service = _backup_service()

    assert 'if [ "$$success" = true ] && ! prune_published' in service
    assert "delete_budget=$$((published_count - 3))" in service
    assert 'if accepted_restore_point "$$candidate"' in service
    assert 'if accepted_restore_point "$$old"' in service
    assert 'test -s "$$archive" || return 1' in service
    assert 'test "$$expected_archive_sha" = "$$actual_archive_sha" || return 1' in service
    assert "find /backups -type f -name 'stratus_*.dump' -mtime +14 -delete" not in service
    assert '*[!A-Za-z0-9_.-]*|\'\') return 1 ;;' in service
    assert 'test ! -L "$$directory" || return 1' in service
    assert 'test ! -L "$$archive" || return 1' in service


def test_backup_restart_cleans_partials_and_health_is_bound_to_manifest() -> None:
    service = _backup_service()

    cleanup_at = service.index("if ! cleanup_abandoned_partials")
    attempt_at = service.index('stamp="$$(date -u +%Y%m%dT%H%M%SZ)"')
    accepted_at = service.index("success=true", attempt_at)
    health_call_at = service.index("if ! write_health_marker", accepted_at)
    assert cleanup_at < attempt_at
    assert accepted_at < health_call_at
    assert 'rm -rf -- "$$abandoned" || return 1' in service
    assert r"manifest_sha256=%s\n" in service
    assert "backup published but health marker update failed; sidecar remains unhealthy" in service

    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert "test -s \"$$directory/stratus.dump\"" in compose
    assert 'test "$$actual_sha" = "$$expected_sha"' in compose


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is unavailable")
def test_retention_floor_ignores_bogus_glob_matches(tmp_path: Path) -> None:
    service = _backup_service()
    start = service.index("        prune_published() {")
    end = service.index("\n\n        while true;", start)
    backup_root = tmp_path.as_posix()
    assert " " not in backup_root
    function = (
        textwrap.dedent(service[start:end])
        .replace("$$", "$")
        .replace("/backups", backup_root)
    )

    old = time.time() - (16 * 24 * 60 * 60)
    valid_directories: list[Path] = []
    for index in range(3):
        directory = tmp_path / f"stratus_valid_{index}.backup"
        directory.mkdir()
        (directory / "VALID").touch()
        os.utime(directory, (old, old))
        valid_directories.append(directory)
    for index in range(3):
        bogus = tmp_path / f"stratus_bogus_{index}.backup"
        bogus.touch()
        os.utime(bogus, (old, old))

    script = (
        'accepted_restore_point() { test -d "$1" && test -f "$1/VALID"; }\n'
        f"{function}\n"
        "prune_published\n"
    )
    completed = subprocess.run(
        [shutil.which("sh") or "sh", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert all(directory.is_dir() for directory in valid_directories)


def test_offsite_requires_crypt_and_publishes_verified_completion_marker() -> None:
    service = _offsite_service()

    assert "rclone/rclone:1.68@sha256:" in service
    assert "crypt:*)" in service
    assert "rclone config show crypt" in service
    upload_at = service.index('rclone copy "$$source_dir" "$$remote_dir"')
    verify_at = service.index(
        'rclone check "$$source_dir" "$$remote_dir"', upload_at
    )
    marker_at = service.index('rclone copyto "$$marker" "$$remote_dir/COMPLETE"')
    assert upload_at < verify_at < marker_at
    assert "--one-way --download" in service
    assert "verified_count=0" in service
    assert 'verified_count=$$((verified_count + 1))' in service
    assert 'if [ "$$failed" = false ] && [ "$$verified_count" -gt 0 ]' in service
    assert 'rm -f /tmp/stratus-offsite-health' in service
    marker_match_at = service.index('if [ "$$remote_marker" = "$$marker_body" ]')
    reverify_at = service.index('rclone check "$$source_dir" "$$remote_dir"', marker_match_at)
    continue_at = service.index("continue", marker_match_at)
    assert marker_match_at < reverify_at < continue_at
    assert "--one-way --size-only --exclude COMPLETE" in service
    assert "find /state/full-scrub-state -mmin -1440" in service
    assert 'record_full_scrub "$$scrub_name"' in service
    assert "completed rotating full off-site scrub" in service
    assert service.count("--one-way --download --exclude COMPLETE") == 2
    assert "stratus-offsite-state:/state" in service


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is unavailable")
def test_offsite_empty_directory_remains_unhealthy(tmp_path: Path) -> None:
    service = _offsite_service()
    start = service.index('        while true; do')
    end = service.index('\n    environment:', start)
    backup_root = (tmp_path / "backups").as_posix()
    health_marker = (tmp_path / "offsite-health").as_posix()
    Path(backup_root).mkdir()
    loop = (
        textwrap.dedent(service[start:end])
        .replace("$$", "$")
        .replace("while true; do", "for _offsite_once in once; do", 1)
        .replace("/backups", backup_root)
        .replace("/tmp/stratus-offsite-health", health_marker)
        .replace("sleep 3600", ":")
        .replace("sleep 900", ":")
    )
    script = f'remote_root="crypt:test"\n{loop}\n'
    completed = subprocess.run(
        [shutil.which("sh") or "sh", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert not Path(health_marker).exists()
    assert "no verified off-site restore point" in completed.stderr
