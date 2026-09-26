"""
Health and readiness endpoints.

Single source of truth for system health states consumed by the
Command Center, the frontend Sidebar/TopBar, and the AiStatusCard.
Every field is derived from a *real* dependency probe — nothing is hardcoded.

Health states (authoritative, consumed by frontend aiStatus.ts):
    UNKNOWN   — no response received yet (initial loading)
    ONLINE    — backend healthy, all critical dependencies up
    DEGRADED  — backend healthy but a non-critical dependency (e.g. Ollama) is down
    OFFLINE   — backend unreachable, or a critical dependency (database) is down

AI CTO status (separate from AI inference status):
    HEALTHY   — all control-plane dependencies (database, redis) are UP
    DEGRADED  — at least one control-plane dependency is DOWN; reason field explains which

AI inference status:
    READY     — Ollama is UP and the inference path is operational
    UNAVAILABLE — Ollama is DOWN or not configured
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.core.db import check_db_connection
from app.core.redis import redis_health

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# Probe timeout for Ollama reachability check (milliseconds).
_OLLAMA_PROBE_TIMEOUT_MS = 3000


async def _check_ollama() -> str:
    """Probe the Ollama host (if configured). Returns 'up' or 'down'.

    Ollama is non-critical for platform operation — it powers AI Copilot
    intent classification and summarisation only. Its absence yields DEGRADED,
    never OFFLINE.
    """
    settings = __import__("app.core.config", fromlist=["get_settings"]).get_settings()
    ollama_host = settings.ollama_host
    if not ollama_host:
        return "unavailable"
    try:
        import asyncio
        import httpx

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(_OLLAMA_PROBE_TIMEOUT_MS / 1000)) as client:
                resp = await client.get(ollama_host.rstrip("/"))
                return "up" if resp.status_code < 500 else "down"
        except (httpx.RequestError, asyncio.TimeoutError):
            return "down"
    except ImportError:
        return "unavailable"


@router.get("/health/live")
async def health_live():
    """Pure liveness probe: returns 200 as long as this process can handle
    an HTTP request, with NO dependency checks (no DB, no Redis, no Ollama).

    This is deliberately dumb. A container orchestrator's liveness probe
    should only ever answer "is the process alive / should it be restarted",
    never "are my dependencies healthy" - conflating the two causes a
    transient Postgres blip to trigger a pointless container restart storm
    instead of Kubernetes just waiting for readiness to recover. Use
    /health/ready (or the existing /ready) for the dependency-aware check.
    """
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


def _readiness_payload(h: dict, response: Response) -> dict:
    """Single readiness implementation shared by /ready and /health/ready.

    Readiness must reflect the dependencies REQUIRED to serve production
    traffic, so it is stricter than liveness:

      * PostgreSQL is always required - without it no request can be served.
      * Redis is required only when ``Settings.readiness_require_redis`` is true
        (the default, preserving the pre-9A ``ready = database AND redis``).
        A deployment that deliberately runs without Redis sets it false so its
        instances still become ready.
      * Ollama is NEVER required: AI features fall back to deterministic
        behaviour, so it can only ever affect /health's informational status.

    Returns HTTP 503 (not 200) when a required dependency is unavailable - which
    is what an orchestrator or load balancer needs in order to stop routing
    traffic to this instance. The historical keys ``status``, ``ready`` and
    ``checks`` keep their previous meaning and shape; ``required`` and
    ``not_ready`` are additive.
    """
    settings = get_settings()
    deps = h["dependencies"]
    required = ["database"]
    if settings.readiness_require_redis:
        required.append("redis")
    not_ready = [name for name in required if deps.get(name) != "up"]
    ready = not not_ready
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": h["status"],
        "ready": ready,
        "required": required,
        "not_ready": not_ready,
        "checks": deps,
    }


@router.get("/health/ready")
async def health_ready(response: Response):
    """Readiness probe under the /health/* namespace some orchestrators expect
    (a single health.livenessProbe/readinessProbe path prefix).

    Dependency-aware and HTTP-status-correct: 200 only when every required
    dependency is reachable, 503 otherwise. Delegates to /health so there is
    exactly one dependency-check implementation.
    """
    h = await health()
    return _readiness_payload(h, response)


@router.get("/health")
async def health():
    """Comprehensive liveness + dependency probe.

    Returns structured health data so the frontend can derive the
    authoritative system state (ONLINE / DEGRADED / OFFLINE) from real
    dependency reachability, not hardcoded labels.

    The ``system_online`` boolean mirrors the Command Center's own
    ``overview.system_online`` (DB + Redis).  ``ai_ready`` reflects Ollama.
    """
    db_ok = await check_db_connection()
    redis_state = await redis_health()
    redis_ok = redis_state == "up"
    ollama_status = await _check_ollama()

    deps: dict[str, str] = {
        "database": "up" if db_ok else "down",
        # "up" / "down" / "unreachable" - never a URL or credential.
        "redis": redis_state,
    }
    if ollama_status != "unavailable":
        deps["ollama"] = ollama_status

    # Database is the critical dependency.  Its absence makes the platform
    # OFFLINE.  Redis down means the cache/scheduler layer is degraded but
    # the API can still serve stale data, so OFFLINE as well (the platform
    # relies on Redis for task routing and the scheduler).  Ollama down is
    # only DEGRADED — AI features fall back to deterministic behaviour.
    if not db_ok:
        status = "unavailable"
    elif not redis_ok:
        status = "degraded"
    elif ollama_status == "down":
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "ready": db_ok and redis_ok,
        "ai_ready": ollama_status == "up",
        "system_online": db_ok and redis_ok,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dependencies": deps,
    }


@router.get("/ready")
async def ready(response: Response):
    """Readiness probe: delegates to ``/health`` so there is exactly one
    authoritative dependency-check implementation.

    Container orchestrators (Kubernetes, Docker Compose) and load balancers
    should call this endpoint. Returns 503 while a required dependency is
    unavailable and 200 once it recovers - the instance is never removed from
    service because of a *liveness* problem, and /health/live stays
    dependency-free.
    """
    h = await health()
    return _readiness_payload(h, response)

# Control-plane dependencies that determine AI CTO health.
# Ordered by priority: database first, then redis.
# Ollama is the AI inference backend, NOT a control-plane dependency,
# so it is deliberately excluded from AI CTO status calculation.
_CONTROL_PLANE_DEPS: list[tuple[str, str]] = [
    ("database", "Database"),
    ("redis", "Redis"),
]


def _derive_ai_cto_status(deps: dict[str, str]) -> tuple[str, str | None]:
    """Return (status, reason) for the AI CTO based on dependency states.

    If all control-plane deps listed in ``_CONTROL_PLANE_DEPS`` that are
    present in ``deps`` are ``"up"``, the CTO is HEALTHY with no reason.
    Otherwise the first DOWN dependency (in priority order) becomes the
    degradation reason.
    """
    for key, label in _CONTROL_PLANE_DEPS:
        state = deps.get(key, "up")
        if state != "up":
            return "degraded", f"{label} dependency is DOWN"
    return "healthy", None


@router.get("/ai-cto")
async def ai_cto_status():
    """AI CTO health + AI inference status.

    Returns the AI CTO orchestration-layer status (control plane) and AI
    inference readiness (Ollama) as two *independent* signals, plus the raw
    dependency states.  The reason for any degradation is derived from the
    real probe results, never hardcoded.

    Differentiated states:
      - AI CTO HEALTHY   — database + redis both UP
      - AI CTO DEGRADED  — database or redis DOWN; ``reason`` names the failing dependency
      - AI READY         — Ollama UP, inference path operational
      - AI UNAVAILABLE   — Ollama DOWN or not configured
    """
    db_ok = await check_db_connection()
    redis_state = await redis_health()
    redis_ok = redis_state == "up"
    ollama_status = await _check_ollama()

    deps: dict[str, str] = {
        "database": "up" if db_ok else "down",
        # "up" / "down" / "unreachable" - never a URL or credential.
        "redis": redis_state,
    }
    if ollama_status != "unavailable":
        deps["ollama"] = ollama_status

    # AI CTO status — only database + redis (control plane)
    if db_ok and redis_ok:
        ai_cto = "healthy"
        ai_cto_reason: str | None = None
    else:
        ai_cto = "degraded"
        # Identify the specific failing dependency
        failing: list[str] = []
        if not db_ok:
            failing.append("database")
        if not redis_ok:
            failing.append("redis")
        ai_cto_reason = f"{' and '.join(failing)} dependency is DOWN"

    # AI inference status: only depends on Ollama
    ai_status = "ready" if ollama_status == "up" else "unavailable"
    ai_message = "Inference path operational" if ollama_status == "up" else (
        "Ollama is DOWN" if ollama_status == "down"
        else "Ollama is not configured"
    )

    return {
        "ai_cto": {
            "status": ai_cto,
            "reason": ai_cto_reason,
        },
        "services": deps,
        "ai": {
            "status": ai_status,
            "message": ai_message,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
