FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependency layer: resolved strictly from the lock file so the image
# contains exactly the versions that were reviewed and tested.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 stratus \
    && chown -R stratus:stratus /app

ENV PATH="/app/.venv/bin:$PATH"
USER stratus

CMD ["stratus-run"]
