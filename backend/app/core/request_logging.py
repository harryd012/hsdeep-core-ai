"""
Structured request observability (Phase 8B).

Reuses the standard ``logging`` module - the same architecture the rest of the
application uses (including the existing ``_JsonFormatter`` precedent in
``app/services/servicenow/audit.py``). No second logging framework.

Emits ONE structured line per API request carrying: timestamp (the handler's own
formatter), method, route, status, duration, correlation id, tenant id and
authenticated user id - the last two only when the auth layer actually resolved
them for that request, never invented.

``X-Correlation-ID`` is accepted from the caller or generated, echoed back on the
response, and stored on ``request.state.correlation_id`` so it can be carried
into downstream work (the event bus already has its own ``correlation_id``).
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("app.request")

CORRELATION_HEADER = "X-Correlation-ID"

# High-frequency probes are not logged per request so the log stays operationally
# useful; they are still served normally.
_SILENT_PREFIXES = ("/health", "/metrics", "/docs", "/openapi.json", "/redoc")

# Names the auth layer might use when publishing the resolved principal on the
# request. Only real values are read - a missing one is logged as null.
_PRINCIPAL_ATTRS = ("principal", "user", "current_user", "auth")


def _principal_ids(request: Request) -> tuple[str | None, str | None]:
    """Return (tenant_id, user_id) only if the auth layer resolved them."""
    for attr in _PRINCIPAL_ATTRS:
        candidate = getattr(request.state, attr, None)
        if candidate is None:
            continue
        tenant_id = getattr(candidate, "tenant_id", None)
        user_id = getattr(candidate, "user_id", None) or getattr(candidate, "id", None)
        if tenant_id is not None or user_id is not None:
            return (
                str(tenant_id) if tenant_id is not None else None,
                str(user_id) if user_id is not None else None,
            )
    tenant_id = getattr(request.state, "tenant_id", None)
    return (str(tenant_id) if tenant_id is not None else None, None)


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    """Logs every API request with its outcome, duration and tenant context."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_SILENT_PREFIXES):
            return await call_next(request)

        correlation_id = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # Never swallow the error: log it with context, then re-raise so the
            # existing exception handling still applies.
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "api_request_failed method=%s route=%s duration_ms=%.1f correlation_id=%s",
                request.method,
                request.url.path,
                duration_ms,
                correlation_id,
                extra={
                    "method": request.method,
                    "route": request.url.path,
                    "duration_ms": round(duration_ms, 1),
                    "correlation_id": correlation_id,
                },
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        tenant_id, user_id = _principal_ids(request)
        status = response.status_code
        # 5xx = server error, 4xx = client/authz failure: log at the matching level.
        log = logger.info
        if status >= 500:
            log = logger.error
        elif status >= 400:
            log = logger.warning
        log(
            "api_request method=%s route=%s status=%s duration_ms=%.1f "
            "correlation_id=%s tenant_id=%s user_id=%s",
            request.method,
            request.url.path,
            status,
            duration_ms,
            correlation_id,
            tenant_id,
            user_id,
            # Same seven fields as first-class attributes, so LOG_FORMAT=json
            # emits them as queryable JSON keys (any log platform can filter on
            # tenant_id/correlation_id without parsing the message).
            extra={
                "method": request.method,
                "route": request.url.path,
                "status": status,
                "duration_ms": round(duration_ms, 1),
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
        )
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


#: Probe endpoints exempt from FORCE_HTTPS. Container health checks, Kubernetes
#: probes and platform health checks (Render, load balancers) call the process
#: directly over plain HTTP; refusing them would mark every replica unhealthy.
#: They return no tenant data.
HTTPS_EXEMPT_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/ready"})


class ForceHttpsMiddleware(BaseHTTPMiddleware):
    """Refuses plaintext requests when FORCE_HTTPS is enabled.

    Honours ``X-Forwarded-Proto`` so it behaves correctly behind a TLS-
    terminating proxy. Off by default, so local development is unaffected; the
    response is a generic 400 and never leaks internals. Health probes
    (``HTTPS_EXEMPT_PATHS``) are always answered.
    """

    def __init__(self, app, *, enabled: bool = False) -> None:
        super().__init__(app)
        self._enabled = enabled

    async def dispatch(self, request: Request, call_next):
        if self._enabled and request.url.path not in HTTPS_EXEMPT_PATHS:
            proto = (
                request.headers.get("x-forwarded-proto") or request.url.scheme or ""
            ).lower()
            if proto != "https":
                from starlette.responses import JSONResponse

                return JSONResponse({"detail": "HTTPS is required."}, status_code=400)
        return await call_next(request)
