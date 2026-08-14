import re
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
    assert 'Read-EnvValue -Path $absoluteEnvFile -Name \'STRATUS_IMAGE\'' in guard
    assert (
        "Read-EnvValue -Path $absoluteEnvFile -Name "
        "'STRATUS_RELEASE_IMAGE_DIGEST'"
    ) in guard
    for service_name in (
        "migrate",
        "worker",
        "binance-market-stream",
        "api",
        "safety-control",
        "telegram",
    ):
        assert f"'{service_name}'" in guard
    assert "$service.image -ne $Image" in guard
    assert "environment.RELEASE_IMAGE_DIGEST" in guard
    assert "worker = 3221225472L" in guard
    assert "api = 1073741824L" in guard
    assert "'safety-control' = 268435456L" in guard
    assert "$memoryLimit -isnot [long]" in guard
    assert "[int64]$memoryLimit -ne [int64]$expectedMemoryLimits[$serviceName]" in guard
    assert "$expectedOperatorApiKeyServices = @('api', 'validate-safety-credentials')" in guard
    assert "Candidate image bakes OPERATOR_API_KEY" in guard
    assert "same non-empty credential" in guard
    assert "$defaultComposeJson = & docker @composeArguments config --format json" in guard
    assert "'--project-directory'" in guard
    assert "'--env-file'" in guard
    assert "'--project-name'" in guard
    assert "'COMPOSE_PROFILES'" in guard
    assert "reserved Compose override variable" in guard
    assert "Rendered migrate command/entrypoint/user/mount/dependency provenance" in guard
    assert "--profile release" in guard
    assert "Default Compose model must contain no services" in guard
    assert "exact reviewed release service inventory" in guard
    assert "exact reviewed isolated " in guard
    assert "service inventory." in guard
    assert "complete reviewed service inventory" in guard


def test_compose_bounds_worker_api_and_safety_memory() -> None:
    compose = (
        Path(__file__).parents[1] / "docker-compose.yml"
    ).read_text(encoding="utf-8")

    assert "mem_limit: 3g" in compose
    assert "mem_limit: 1g" in compose
    assert "mem_limit: 256m" in compose
    assert "STRATUS_WORKER_MEMORY_LIMIT" not in compose
    assert "STRATUS_API_MEMORY_LIMIT" not in compose
    assert "STRATUS_SAFETY_MEMORY_LIMIT" not in compose


def test_compose_confines_operator_api_key_to_api_and_validator() -> None:
    compose = (
        Path(__file__).parents[1] / "docker-compose.yml"
    ).read_text(encoding="utf-8")
    boundaries = [
        (match.start(), match.group(1))
        for match in re.finditer(r"(?m)^  ([a-zA-Z0-9_-]+):$", compose)
    ]
    service_blocks = {
        name: compose[start : boundaries[index + 1][0]]
        if index + 1 < len(boundaries)
        else compose[start:]
        for index, (start, name) in enumerate(boundaries)
    }
    exposed = {
        name
        for name, block in service_blocks.items()
        if re.search(r"(?m)^      OPERATOR_API_KEY:", block)
    }

    assert exposed == {"api", "validate-safety-credentials"}
    for name in (
        "migrate",
        "worker",
        "binance-market-stream",
        "safety-control",
        "telegram",
        "provision-runtime-role",
        "provision-safety-role",
    ):
        assert "OPERATOR_API_KEY:" not in service_blocks[name]


def test_compose_default_model_profiles_every_release_writer() -> None:
    compose = (
        Path(__file__).parents[1] / "docker-compose.yml"
    ).read_text(encoding="utf-8")

    expected_profiles = {
        "validate-owner-password": (
            'profiles: ["release", "infrastructure", "binance", '
            '"telegram", "offsite"]'
        ),
        "validate-safety-credentials": 'profiles: ["release"]',
        "postgres": (
            'profiles: ["release", "infrastructure", "binance", '
            '"telegram", "offsite"]'
        ),
        "migrate": 'profiles: ["release", "binance", "telegram"]',
        "provision-runtime-role": (
            'profiles: ["release", "binance", "telegram"]'
        ),
        "provision-safety-role": 'profiles: ["release"]',
        "worker": 'profiles: ["release"]',
        "api": 'profiles: ["release"]',
        "safety-control": 'profiles: ["release"]',
        "backup": 'profiles: ["release", "infrastructure", "offsite"]',
        "binance-market-stream": 'profiles: ["binance"]',
        "backup-offsite": 'profiles: ["offsite"]',
        "telegram": 'profiles: ["telegram"]',
    }
    for service_name, profile_line in expected_profiles.items():
        service = re.search(
            rf"(?ms)^  {re.escape(service_name)}:\n.*?(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
            compose,
        )
        assert service is not None
        assert profile_line in service.group(0)

    services_block = compose.split("\nvolumes:\n", maxsplit=1)[0]
    service_names = set(re.findall(r"(?m)^  ([a-zA-Z0-9_-]+):$", services_block))
    assert service_names == set(expected_profiles)


def test_ci_executes_release_image_guard_after_candidate_build() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    build = workflow.index("      - name: Docker build")
    assert 'candidate="stratus:candidate-${GITHUB_SHA:0:12}"' in workflow[build:]
    assert "./scripts/Assert-StratusReleaseImage.ps1" in workflow[build:]
    assert '-ExpectedRevision "$GITHUB_SHA"' in workflow[build:]
    assert '-Root "$GITHUB_WORKSPACE"' in workflow[build:]
    assert "STRATUS_RELEASE_IMAGE_DIGEST=%s" in workflow[build:]
    assert 'test -z "$default_services"' in workflow
    assert '"${compose_args[@]}" --profile release config --services' in workflow
    assert '--project-directory "$GITHUB_WORKSPACE"' in workflow
    assert '--env-file "$compose_env"' in workflow
    assert "Test-StratusPreStartTopology.ps1" in workflow
