# HSDEEP CORE AI - backend production image (Phase 9A)
#
# ONE image, several roles. The role is chosen at deploy time with RUNTIME_MODE:
#   api | worker | scheduler | all   (see deployment/README.md)
#
# Build (from the repository root):
#   docker build -t hsdeep-core-ai-backend:9a backend
# Run (configuration is injected - no .env, no secrets baked in):
#   docker run --rm -p 8000:8000 --env-file deployment/.env.production \
#     -e RUNTIME_MODE=api hsdeep-core-ai-backend:9a

# --- Stage 1: isolated virtualenv with the RUNTIME deps only ---------------
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# Runtime requirements only: requirements-dev.txt (pytest, hypothesis, respx) is
# deliberately NOT installed, so no test tooling can ship to production.
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# --- Stage 2: runtime (no compilers, no build tools) ----------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
# libpq5 = runtime library for the Postgres driver; curl = container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# First-login bootstrap for a fresh database (deployment/RENDER.md). Run it once
# from the platform shell: python scripts/seed_admin.py
COPY scripts/__init__.py scripts/seed_admin.py ./scripts/
# No .env and no credentials are copied: production configuration is injected by
# the platform (secret manager / orchestrator), never baked into an image layer.
USER app
EXPOSE 8000
# Liveness only (dependency-free), so a Postgres/Redis blip can never trigger a
# restart storm. Readiness (/health/ready, 503 when a required dependency is
# down) is the orchestrator's readinessProbe, configured in the deployment.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1
# --no-access-log: the app already emits one structured api_request line per
# request (app/core/request_logging.py); uvicorn's access log would duplicate it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
