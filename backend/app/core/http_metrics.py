"""
Prometheus instrumentation for HTTP API requests.

A plain ASGI middleware (not ``BaseHTTPMiddleware``) installed outermost, so
it times the whole stack and also counts responses produced before routing,
such as rate-limit 429s and CORS preflights. It runs alongside
``RequestObservabilityMiddleware`` (the per-request log line), which it
does not replace.

The ``route`` label is the matched route template, read from
``scope["route"]`` after the app has run. When nothing set it (a response
returned by a middleware before routing), the template is resolved by
matching the path against the app's routes. A path that matches no route is
labelled ``__unmatched__``. The raw path never becomes a label.
"""
from __future__ import annotations

import time

from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import UNMATCHED_ROUTE, http_requests_in_progress, observe_http_request

# Same exclusions as the request log (app/core/request_logging.py): probes,
# the scrape itself and the API docs would dominate the series otherwise.
EXCLUDED_PREFIXES = ("/health", "/metrics", "/docs", "/openapi.json", "/redoc")

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def _method_label(method: str) -> str:
    return method if method in _METHODS else "OTHER"


def route_template(scope: Scope) -> str:
    """The route template for this request, or ``__unmatched__``."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    app = scope.get("app")
    routes = getattr(getattr(app, "router", None), "routes", None) or ()
    # A wrong method is PARTIAL: still the same template.
    probe = {**scope, "method": scope.get("method", "GET")}
    for candidate in routes:
        try:
            match, _ = candidate.matches(probe)
        except Exception:  # noqa: BLE001 - a broken matcher must never fail a request
            continue
        if match in (Match.FULL, Match.PARTIAL):
            candidate_path = getattr(candidate, "path", None)
            if candidate_path:
                return candidate_path
    return UNMATCHED_ROUTE


class HttpMetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(EXCLUDED_PREFIXES):
            await self.app(scope, receive, send)
            return

        method = _method_label(scope.get("method", ""))
        status = 500  # stays 500 if the app raises before sending a response

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        in_progress = http_requests_in_progress.labels(method=method)
        in_progress.inc()
        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            in_progress.dec()
            observe_http_request(method, route_template(scope), status, time.perf_counter() - started)
