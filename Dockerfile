FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91
COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependency layer: resolved strictly from the lock file so the image
# contains exactly the versions that were reviewed and tested.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md alembic.ini Dockerfile docker-compose.yml .dockerignore ./
COPY migrations ./migrations
COPY ops/postgres/stratus-pg_hba.conf ./ops/postgres/stratus-pg_hba.conf
COPY .github/workflows/ci.yml ./.github/workflows/ci.yml
COPY docs/RELEASE_CUTOVER_RUNBOOK.md ./docs/RELEASE_CUTOVER_RUNBOOK.md
COPY src ./src
COPY scripts ./scripts
COPY scripts/stratus-runtime-entrypoint.sh /usr/local/bin/stratus-runtime-entrypoint
RUN uv sync --frozen --no-dev \
    && chmod 0555 /usr/local/bin/stratus-runtime-entrypoint \
    && useradd --create-home --uid 10001 stratus \
    && chown -R stratus:stratus /app

ENV PATH="/app/.venv/bin:$PATH"
USER stratus

CMD ["stratus-run"]
