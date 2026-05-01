# syntax=docker/dockerfile:1.7
# Phase 6 — multi-stage build for the FastAPI annotation tool.
# Builder uses uv to resolve + install deps; runtime is a slim image with non-root user.

# ────────────────────────────────────────────────────────────
# Stage 1: builder
# ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# librosa needs libsndfile1 at build time so its C-extension probes succeed.
# build-essential covers any wheel that needs to compile (scipy/llvmlite fallback paths).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libsndfile1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Astral's fast Python package manager) into /usr/local/bin.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    VIRTUAL_ENV=/opt/venv

# Create the venv that we'll later copy into the runtime image.
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build

# Copy only dep manifests first so the layer caches when source changes.
COPY pyproject.toml requirements.txt ./

# Install runtime dependencies into the venv.
# Use requirements.txt because pyproject's hatch build expects src/ to exist —
# we don't need to install the package itself, just its deps.
RUN uv pip install --python "$VIRTUAL_ENV/bin/python" -r requirements.txt \
    && uv pip install --python "$VIRTUAL_ENV/bin/python" \
        authlib \
        itsdangerous \
        python-multipart \
        python-dotenv \
        "sentry-sdk[fastapi]"

# ────────────────────────────────────────────────────────────
# Stage 2: runtime
# ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime needs libsndfile1 for librosa to load audio at request time.
# curl is kept so the HEALTHCHECK below works without extra deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsndfile1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 app \
    && useradd  --system --uid 1000 --gid app --home-dir /app --shell /usr/sbin/nologin app

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_PORT=8000

# Copy the prepared venv from the builder.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Copy only what the running app needs. data/ is a volume — never bake it in.
COPY --chown=app:app src/        ./src/
COPY --chown=app:app static/     ./static/
COPY --chown=app:app scripts/    ./scripts/
COPY --chown=app:app pyproject.toml ./

# data/ is mounted by docker-compose; ensure the mount point exists and is writable.
RUN mkdir -p /app/data && chown -R app:app /app

USER app

EXPOSE 8000

# /api/dimensions returns JSON once lifespan startup completes — a good readiness probe.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8000/api/dimensions || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
