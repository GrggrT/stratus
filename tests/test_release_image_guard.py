from pathlib import Path


def _guard() -> str:
    return (
        Path(__file__).parents[1] / "scripts" / "Assert-StratusReleaseImage.ps1"
    ).read_text(encoding="utf-8")


def test_release_image_guard_binds_image_revision_environment_and_services() -> None:
    guard = _guard()

    assert "ValidatePattern('^stratus:candidate-[0-9a-f]{12}$')" in guard
    assert "ValidatePattern('^[0-9a-f]{64}$')" in guard
    assert "ValidatePattern('^[0-9a-f]{40}$')" in guard
    assert "sha256:$ExpectedDigest" in guard
    assert "expected linux/amd64" in guard
    assert "$inspectJson | ConvertFrom-Json" in guard
    assert "Labels.'org.opencontainers.image.revision'" in guard
    assert 'Read-EnvValue -Path $EnvFile -Name \'STRATUS_IMAGE\'' in guard
    assert (
        "Read-EnvValue -Path $EnvFile -Name "
        "'STRATUS_RELEASE_IMAGE_DIGEST'"
    ) in guard
    for service_name in (
        "migrate",
        "worker",
        "binance-market-stream",
        "api",
        "telegram",
    ):
        assert f"'{service_name}'" in guard
    assert "$service.image -ne $Image" in guard
    assert "environment.RELEASE_IMAGE_DIGEST" in guard
