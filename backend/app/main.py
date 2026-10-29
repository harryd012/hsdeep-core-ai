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
import hmac
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
async def metrics(request: Request):
    cfg = get_settings()
    expected = cfg.metrics_bearer_token
    if expected:
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            return JSONResponse(
                {"detail": "Invalid or missing metrics token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
    elif cfg.app_env == "production":
        # No token configured: do not publish platform internals publicly.
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------
# Rate Limiter (tenant-aware sliding window)
# ---------------------------------------------------

class _RateLimiter:
    """Sliding-window rate limiter keyed by an arbitrary scope key
    (``tenant:<uuid>`` for authenticated callers, ``ip:<addr>`` otherwise).
    The limit itself is supplied per acquire() so authenticated tenants get
    their plan's api_rate_limit instead of the flat default."""

    def __init__(self, requests_per_minute: int = 60) -> None:
        self._default_rpm = requests_per_minute
        self._lock = asyncio.Lock()
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        # tenant_id -> (expires_at_monotonic, rpm) - avoids a DB hit per request.
        self._plan_cache: dict[str, tuple[float, int]] = {}
        self._plan_ttl = 60.0

    def key_for_request(self, request) -> str:
        """Tenant-scoped key when a valid JWT bearer token is present,
        IP-scoped otherwise (login/refresh/anonymous traffic)."""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                from app.core.auth import _decode_access_token

                payload = _decode_access_token(auth[7:].strip())
                tid = payload.get("tid")
                if tid:
                    return f"tenant:{tid}"
            except Exception:  # noqa: BLE001 - invalid/expired token falls back to IP
                pass
        client_ip = request.client.host if request.client else "unknown"
        return f"ip:{client_ip}"

    async def rpm_for(self, key: str) -> int:
        """Requests-per-minute for this key: the tenant plan's
        api_rate_limit for tenant keys (cached 60s), the flat default
        otherwise. A missing/nonpositive plan value falls back to the
        default rather than producing an unbounded or zero limit."""
        self._default_rpm = max(self._default_rpm, 1)
        if not key.startswith("tenant:"):
            return self._default_rpm
        now = time.monotonic()
        cached = self._plan_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        rpm: int | None = None
        try:
            import uuid as _uuid

            from app.core.db import AsyncSessionLocal
            from app.repositories.subscription_repository import SubscriptionRepository

            async with AsyncSessionLocal() as db:
                sub = await SubscriptionRepository(db).get_active(_uuid.UUID(key.split(":", 1)[1]))
                if sub is not None and sub.plan is not None:
                    rpm = int(sub.plan.api_rate_limit)
        except Exception:  # noqa: BLE001 - never let limiter config break requests
            rpm = None
        if not rpm or rpm <= 0:
            rpm = self._default_rpm
        self._plan_cache[key] = (now + self._plan_ttl, rpm)
        return rpm

    async def acquire(self, key: str, rpm: int) -> None:
        now = time.monotonic()
        cutoff = now - 60.0
        async with self._lock:
            window = self._windows[key]
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= rpm:
                wait = window[0] + 60.0 - now
                if wait > 0:
                    await asyncio.sleep(wait)
                while window and window[0] < now:
                    window.popleft()
            window.append(now)


class _RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware enforcing a sliding-window rate limit scoped per tenant
    (plan.api_rate_limit) for authenticated traffic and per IP otherwise.

    It additionally meters and enforces the tenant's PER-BILLING-PERIOD API
    quota (``Plan.api_rate_limit``), which is what the plan actually sells and
    what the Usage UI displays. See ``app/services/metering/api_quota.py``: the
    same plan field used to be applied only as a per-minute burst ceiling, so a
    50_000/month allowance never blocked anything, and the ``api.call`` meter
    had no callers so usage always read 0.

    The burst window below is deliberately left as-is: it is generous (it uses
    the period allowance as a per-minute ceiling) and changing it would alter
    existing behaviour for every tenant. The period quota is the real limit.
    """

    def __init__(self, app, requests_per_minute: int = 60) -> None:
        super().__init__(app)
        self._limiter = _RateLimiter(requests_per_minute)

    async def dispatch(self, request: Request, call_next):
        key = self._limiter.key_for_request(request)
        rpm = await self._limiter.rpm_for(key)
        await self._limiter.acquire(key, rpm)

        if key.startswith("tenant:"):
            import uuid as _uuid

            from app.services.metering.api_quota import (
                api_allowance,
                pending_for,
                record_api_call,
            )

            try:
                tenant_id = _uuid.UUID(key.split(":", 1)[1])
            except ValueError:
                return await call_next(request)

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
