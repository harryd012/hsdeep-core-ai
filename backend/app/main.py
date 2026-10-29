"""
HSDEEP CORE AI
Application Entry Point
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.core.request_logging import ForceHttpsMiddleware, RequestObservabilityMiddleware
from fastapi.middleware.cors import CORSMiddleware

import asyncio
import math
import time
from collections import defaultdict
from collections import deque

from app.api.routes import auth, alerts, dashboard, devices, health, incidents, infrastructure, monitoring, priorities, sops
from app.api.routes import ingest
from app.api.routes import agents, tasks
from app.api.routes import command_center
from app.api.routes import discovery_scopes
from app.api.routes import admin_tenants
from app.api.routes import copilot
from app.api.routes import billing, onboarding, stripe_webhook
from app.api.routes import tickets, workflows, runbooks, servicenow, assets, assignments, itsm, sla
from app.api.routes import settings as settings_routes
from app.api.routes import noc, soc
from app.api.routes import topology
from app.api.routes import integrations, normalized_events
from app.api.routes import zones, change_requests, ai_diagnoses
from app.api.routes import device_actions
from app.api.routes import network_intelligence
from app.api.routes import discovery
from app.api.routes import infrastructure_intelligence
from app.api.routes import failover_intelligence
from app.core.config import get_settings
from app.core.di import init_di
from app.core.logging import configure_logging
from app.scheduler.scheduler import _reconcile_stale_runs, start_scheduler, stop_scheduler
from app.services.metering.router import router as metering_router
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.requests import Request
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

# Register monitoring collectors
import app.services.monitoring  # noqa: F401

# ---------------------------------------------------
# Logging
# ---------------------------------------------------

configure_logging()
logger = logging.getLogger(__name__)

settings = get_settings()


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated on_event startup/shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup logic runs before the first request;
    shutdown logic runs after the last response. Replaces the deprecated
    @app.on_event('startup') / @app.on_event('shutdown') pattern."""
    logger.info("=" * 70)
    logger.info("Starting %s", settings.app_name)
    logger.info("Environment : %s", settings.app_env)
    logger.info("Debug       : %s", settings.debug)
    logger.info("Swagger     : /docs")
    logger.info("OpenAPI     : /openapi.json")
    logger.info("=" * 70)

    # Fail loudly (not silently-insecure) if APP_ENV=production but
    # security-critical config still looks like a dev/placeholder value.
    # See PRODUCTION_READINESS.md P0-2. No-op outside production.
    _prod_problems = settings.validate_for_production()
    if _prod_problems:
        for _p in _prod_problems:
            logger.error("PRODUCTION CONFIG FAILED: %s", _p)
        raise RuntimeError(
            f"Refusing to start with APP_ENV=production: {len(_prod_problems)} "
            "unsafe configuration issue(s) found (see error log above)."
        )

    # Initialise DI singletons (usage collector, etc.)
    init_di()

    # Reconcile stale CollectorRun rows BEFORE the scheduler starts firing
    # ticks, so a leftover status="running" row from a previous process
    # never blocks the concurrent-run-skip guard for that source.
    await _reconcile_stale_runs()

    # --- Phase 9A runtime role -------------------------------------------
    # One image, several roles. 'api' replicas must never run scheduler jobs:
    # the tick's own Postgres advisory lock already guarantees a single owner,
    # but having every API pod race for it is waste and hides misconfiguration.
    # Local development keeps the pre-9A behaviour via the default 'all'.
    _runs_scheduler = settings.runtime_mode in ("all", "worker", "scheduler")
    _runs_consumers = settings.runtime_mode in ("all", "worker")
    logger.info(
        "Runtime mode : %s (scheduler=%s event_consumers=%s)",
        settings.runtime_mode,
        _runs_scheduler,
        _runs_consumers,
    )
    if _runs_scheduler:
        start_scheduler(app)
    else:
        logger.info(
            "Runtime mode %s: scheduler jobs NOT started in this process.",
            settings.runtime_mode,
        )

    # Subscribe the RCA service + the narrow INCIDENT_CREATED -> AI_RCA_STARTED
    # bridge (see _start_rca_consumers). Skipped by runtime roles that must not
    # consume events ('api', 'scheduler'). Role 'all' (local development) keeps
    # the exact pre-9A behaviour.
    if _runs_consumers:
        await _start_rca_consumers(app)
    else:
        logger.info(
            "Runtime mode %s: RCA/EventBus consumers NOT started in this process.",
            settings.runtime_mode,
        )

    yield  # ----- application runs -----

    logger.info("Stopping %s", settings.app_name)
    stop_scheduler(app)
    # Phase 9A: unregister RCA handlers and drain/stop the event bus inside a
    # bounded budget. Idempotent, and safe when RCA was never wired.
    await _stop_rca_consumers(app)


async def _start_rca_consumers(app: FastAPI) -> None:
    """Wire the production RCA half (Phase 9A).

    Extracted verbatim from the lifespan so that runtime roles which must NOT
    consume events ('api', 'scheduler') can skip it, while 'all' (local dev) and
    'worker' keep exactly the pre-9A behaviour.

    Registers RCALifecycle (AI_RCA_STARTED) and the narrow INCIDENT_CREATED ->
    AI_RCA_STARTED bridge. AutonomousLifecycleOrchestrator is deliberately NOT
    started, so no ticket/SOP/automation/closure behaviour is activated.
    Non-fatal throughout: a failure here must never stop the API from serving.
    """
    life = None
    try:
        from app.services.rca_lifecycle import start_rca_lifecycle

        life = start_rca_lifecycle()
        await life.start()
        app.state.rca_lifecycle = life
    except Exception as _exc:  # noqa: BLE001
        logger.error("RCA lifecycle wiring failed: %s", _exc, exc_info=True)

    try:
        from app.services.rca_trigger import RCAIncidentCreatedBridge

        bridge = RCAIncidentCreatedBridge(
            event_bus=life.event_bus if life is not None else None
        )
        bridge.register()
        app.state.rca_trigger_bridge = bridge
    except Exception as _exc:  # noqa: BLE001
        logger.error("RCA trigger bridge wiring failed: %s", _exc, exc_info=True)


async def _stop_rca_consumers(app: FastAPI) -> None:
    """Graceful, bounded, idempotent shutdown of the RCA half (Phase 9A).

    Unregisters the RCA handlers first so nothing new is dispatched to them,
    then drains/stops the existing event bus within
    ``Settings.shutdown_drain_seconds``.

    Safe when the lifecycle was never initialised (failed wiring, or a runtime
    role that does not consume events) and safe to run more than once. Never
    raises - shutdown must always complete.
    """
    bridge = getattr(app.state, "rca_trigger_bridge", None)
    if bridge is not None:
        try:
            bridge.unregister()
        except Exception:  # noqa: BLE001
            logger.warning(
                "RCA bridge unregister failed during shutdown (ignored)",
                exc_info=True,
            )

    life = getattr(app.state, "rca_lifecycle", None)
    if life is not None:
        await life.stop()
    else:
        # No lifecycle object (never wired, or already stopped): stop the bus
        # worker directly so a started worker is never left running.
        try:
            from app.services.events.event_bus import get_event_bus

            await asyncio.wait_for(
                get_event_bus().stop(), timeout=settings.shutdown_drain_seconds
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Event bus stop during shutdown failed (ignored)", exc_info=True
            )

    app.state.rca_lifecycle = None
    app.state.rca_trigger_bridge = None


# ---------------------------------------------------
# FastAPI App
# ---------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="HSDEEP CORE AI - Autonomous Infrastructure Intelligence Platform",
    debug=settings.debug,
    lifespan=lifespan,
)

# ---------------------------------------------------
# CORS (environment-aware — see PRODUCTION_READINESS.md P0-3)
# ---------------------------------------------------
#
# allow_credentials=True + a wildcard origin is a real cross-tenant
# credential-leak vector (any site can call this API using a logged-in
# user's cookies/bearer context via CORS). Production must declare an
# explicit comma-separated allow-list via CORS_ORIGINS; anything else
# (unset or "*") fails startup loudly instead of silently degrading to
# unrestricted access. Development keeps a narrow localhost default so the
# app still boots out of the box, but that default is never "*" either.
_cors_origins_raw = settings.cors_origins.strip()

if settings.app_env == "production":
    if not _cors_origins_raw or _cors_origins_raw == "*":
        raise RuntimeError(
            "CORS_ORIGINS must be set to an explicit comma-separated allow-list "
            "when APP_ENV=production (e.g. 'https://app.hsdeep.com,https://hsdeep.com'). "
            "A wildcard or unset origin combined with allow_credentials=True is a "
            "cross-tenant security risk and is refused at startup rather than "
            "silently permitted. Set CORS_ORIGINS explicitly to start this process."
        )
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
else:
    _cors_origins = (
        [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
        if _cors_origins_raw
        else ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

# Observability + HTTPS enforcement. Registered before CORS so CORS remains the
# outermost layer; the request logger therefore covers every routed request.
app.add_middleware(RequestObservabilityMiddleware)
app.add_middleware(ForceHttpsMiddleware, enabled=settings.force_https)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------
# Root Endpoint
# ---------------------------------------------------

@app.get("/", tags=["Root"])
async def root():
    return {
        "application": settings.app_name,
        "version": "0.1.0",
        "environment": settings.app_env,
        "status": "Running",
        "documentation": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
        "ready": "/ready",
        "modules": {
            "Auth & RBAC": "/api/auth",
            "Monitoring": "/api/monitoring",
            "Alert Engine": "/api/alerts",
            "RCA Engine": "Coming Soon",
            "SOP Engine": "/api/sops",
            "AI Copilot": "Coming Soon",
        },
    }

# ---------------------------------------------------
# Prometheus /metrics Endpoint
# ---------------------------------------------------

@app.get("/metrics", tags=["Metrics"])
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------
# Rate Limiter (tenant-aware sliding window)
# ---------------------------------------------------

class _RateLimiter:
    """Sliding-window rate limiter keyed by an arbitrary scope key:
    ``user:<tid>:<sub>`` for session-JWT (web UI) callers, ``ip:<addr>``
    otherwise (anonymous, login/refresh, API-key traffic).

    Session keys get the per-user fair-use cap (``session_rpm_per_user``) and
    are rejected with 429 when over it (``try_acquire``). IP keys keep the flat
    default and the historical behaviour of waiting for a free slot
    (``acquire``). The lock is never held across a sleep, so one throttled key
    cannot stall every other caller."""

    # Measured 2026-10-29: a page load is ~12 calls, steady state 13-30 rpm per
    # tab (the 5s NOC/SOC pollers are the heaviest, ~50 rpm worst case), so a
    # 6-tab NOC wall is ~180-300 rpm. 600 leaves ~2x headroom for one human
    # while capping a script driving its own session token.
    DEFAULT_SESSION_RPM = 600

    def __init__(self, requests_per_minute: int = 60, session_rpm_per_user: int | None = None) -> None:
        self._default_rpm = max(requests_per_minute, 1)
        if session_rpm_per_user is None:
            session_rpm_per_user = settings.session_rpm_per_user
        # A missing/nonpositive value falls back to the built-in cap rather
        # than producing an unbounded or zero limit.
        self._session_rpm = session_rpm_per_user if session_rpm_per_user > 0 else self.DEFAULT_SESSION_RPM
        self._lock = asyncio.Lock()
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    def key_for_request(self, request) -> str:
        """User-scoped key when a valid session JWT bearer token is present,
        IP-scoped otherwise (login/refresh/anonymous/API-key traffic)."""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                from app.core.auth import _decode_access_token

                payload = _decode_access_token(auth[7:].strip())
                tid = payload.get("tid")
                sub = payload.get("sub")
                if tid and sub:
                    return f"user:{tid}:{sub}"
            except Exception:  # noqa: BLE001 - invalid/expired token falls back to IP
                pass
        client_ip = request.client.host if request.client else "unknown"
        return f"ip:{client_ip}"

    async def rpm_for(self, key: str) -> int:
        """Requests-per-minute for this key: the per-user session cap for
        ``user:`` keys, the flat default otherwise."""
        if key.startswith("user:"):
            return self._session_rpm
        return self._default_rpm

    async def try_acquire(self, key: str, rpm: int) -> float | None:
        """Record a request if the key is under ``rpm`` in the last 60s and
        return None; otherwise record nothing and return the seconds until a
        slot frees up."""
        now = time.monotonic()
        cutoff = now - 60.0
        async with self._lock:
            window = self._windows[key]
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= rpm:
                return max(window[0] + 60.0 - now, 0.001)
            window.append(now)
            return None

    async def acquire(self, key: str, rpm: int) -> None:
        """Wait (outside the lock) until the key has a free slot, then record."""
        while (wait := await self.try_acquire(key, rpm)) is not None:
            await asyncio.sleep(wait)


class _RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware enforcing a sliding-window rate limit: a per-user fair-use
    cap for session-JWT (web UI) traffic, rejected with 429 + Retry-After when
    exceeded, and a flat per-IP limit (waits for a slot) otherwise.

    It additionally meters and enforces the tenant's PER-BILLING-PERIOD API
    quota (``Plan.api_rate_limit``) for API-key requests only - session JWT
    (web UI) traffic and control-plane paths are never counted. That quota is
    what the plan actually sells and what the Usage UI displays. See ``app/services/metering/api_quota.py``: the
    same plan field used to be applied only as a per-minute burst ceiling, so a
    50_000/month allowance never blocked anything, and the ``api.call`` meter
    had no callers so usage always read 0.

    Session traffic is never metered against that quota, so the per-user cap
    is what stops a user scripting against their own session token. It no
    longer uses ``Plan.api_rate_limit`` as a per-minute ceiling (~50_000 rpm on
    Starter, which never limited anything).
    """

    def __init__(self, app, requests_per_minute: int = 60, session_rpm_per_user: int | None = None) -> None:
        super().__init__(app)
        self._limiter = _RateLimiter(requests_per_minute, session_rpm_per_user)

    async def dispatch(self, request: Request, call_next):
        key = self._limiter.key_for_request(request)
        rpm = await self._limiter.rpm_for(key)
        if key.startswith("user:"):
            retry_after = await self._limiter.try_acquire(key, rpm)
            if retry_after is not None:
                seconds = max(1, math.ceil(retry_after))
                return JSONResponse(
                    status_code=HTTP_429_TOO_MANY_REQUESTS,
                    headers={"Retry-After": str(seconds)},
                    content={
                        "error": "rate_limited",
                        "limit": rpm,
                        "window_seconds": 60,
                        "retry_after": seconds,
                        "message": (
                            f"Too many requests: the limit is {rpm} per minute per "
                            f"user. Retry in {seconds}s."
                        ),
                    },
                )
        else:
            await self._limiter.acquire(key, rpm)

        from app.services.metering.api_quota import (
            api_allowance,
            api_key_tenant,
            is_control_plane_path,
            pending_for,
            record_api_call,
        )

        # Only API-key traffic is metered (see api_quota "Who is metered"):
        # a session JWT - HSDEEP's own web UI - already got a ``user:`` key
        # above and is never counted. Session endpoints stay reachable for
        # everyone so a locked-out tenant can still log in to /usage.
        if key.startswith("user:") or is_control_plane_path(request.url.path):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            return await call_next(request)
        tenant_id = await api_key_tenant(auth[7:].strip())
        if tenant_id is not None:
            # Meter the request, then enforce the period allowance. Metering
            # failures are swallowed inside the meter and never break a request.
            await record_api_call(tenant_id)
            limit, used, over = await api_allowance(tenant_id)
            if limit is not None and (over or used + pending_for(tenant_id) > limit):
                from app.core.entitlements import _audit_entitlement_denial

                await _audit_entitlement_denial(
                    tenant_id,
                    "api_calls",
                    "plan_limit_exceeded",
                    f"API request quota {limit} reached (used {used})",
                )
                # No credentials, connection strings or tenant secrets here.
                return JSONResponse(
                    status_code=HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "error": "plan_limit_exceeded",
                        "resource": "api_calls",
                        "limit": limit,
                        "used": used,
                        "message": (
                            f"Your plan allows {limit} API requests per billing "
                            f"period. You have used {used}. Upgrade to add more."
                        ),
                        "upgrade_url": "/usage",
                    },
                )

        return await call_next(request)


# ---------------------------------------------------
# Security headers (PRODUCTION_READINESS.md P1-SEC-HEADERS)
# ---------------------------------------------------


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline security headers on every response.

    This backend serves JSON (+ Swagger/ReDoc docs), never HTML application
    content, so a strict default-src 'none' CSP is safe for API responses.
    /docs and /redoc are excluded from the strict CSP because Swagger UI /
    ReDoc load their own JS/CSS assets and would otherwise fail to render.
    HSTS is only ever added when the deployment explicitly confirms it is
    served over HTTPS (FORCE_HTTPS=true) - emitting HSTS over a plain-HTTP
    deployment can lock users out of the site for the max-age duration.
    """

    _DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), interest-cohort=()",
        )
        if not any(request.url.path.startswith(p) for p in self._DOCS_PATHS):
            response.headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
        if settings.app_env == "production" and os.getenv("FORCE_HTTPS", "false").lower() == "true":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


# ---------------------------------------------------
# Middleware (order matters – rate limit runs first, security headers wrap
# every response including rate-limited 429s since it's added last/outermost)
# ---------------------------------------------------

app.add_middleware(_RateLimitMiddleware, requests_per_minute=60)
app.add_middleware(_SecurityHeadersMiddleware)

# ---------------------------------------------------
# Routers
# ---------------------------------------------------

app.include_router(health.router)
app.include_router(auth.router, prefix="/api")
app.include_router(monitoring.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(incidents.router, prefix="/api")
app.include_router(priorities.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(devices.router, prefix="/api")
app.include_router(infrastructure.router, prefix="/api")
app.include_router(sops.router, prefix="/api")
app.include_router(metering_router)
app.include_router(billing.router, prefix="/api")
app.include_router(onboarding.router, prefix="/api")
app.include_router(stripe_webhook.router, prefix="/api")
app.include_router(tickets.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(runbooks.router, prefix="/api")
app.include_router(servicenow.router, prefix="/api")
app.include_router(settings_routes.router, prefix="/api")
app.include_router(sla.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(command_center.router, prefix="/api")
app.include_router(copilot.router, prefix="/api")
app.include_router(assets.router, prefix="/api")
app.include_router(assignments.router, prefix="/api")
app.include_router(itsm.router, prefix="/api")
app.include_router(noc.router, prefix="/api")
app.include_router(noc.correlation_router, prefix="/api")
app.include_router(soc.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
app.include_router(normalized_events.router, prefix="/api")
app.include_router(zones.router, prefix="/api")
app.include_router(change_requests.router, prefix="/api")
app.include_router(ai_diagnoses.router, prefix="/api")
app.include_router(topology.router, prefix="/api")
app.include_router(device_actions.router, prefix="/api")
app.include_router(network_intelligence.router, prefix="/api")
app.include_router(infrastructure_intelligence.router, prefix="/api")
app.include_router(failover_intelligence.router, prefix="/api")
app.include_router(discovery.router, prefix="/api")
app.include_router(discovery_scopes.router, prefix="/api")
app.include_router(admin_tenants.router, prefix="/api")
app.include_router(ingest.router, prefix="/api/v1")
