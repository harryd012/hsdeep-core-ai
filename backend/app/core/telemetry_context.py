"""
Correlation identifiers shared by logs and traces (Phase 22).

One place that knows which identifiers tie a log line to a trace, a request,
a tenant, an incident, a collection run and a provider. They live in
``contextvars``, so they follow the request (or the scheduler job) through
``await`` and into tasks created from it, and never leak between requests.

These identifiers are high-cardinality on purpose. They go into log records
and span attributes only, never into Prometheus labels (app/core/metrics.py).

``trace_id`` / ``span_id`` are read from the active OpenTelemetry span and
``tenant_id`` from the existing ``app.core.tenant_context`` variable, so there
is one source for each. A value that was never set is absent, not invented.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

#: Identifiers bound explicitly with ``bind()``.
BOUND_FIELDS: tuple[str, ...] = ("request_id", "incident_id", "collection_run_id", "provider_id")
#: Every correlation field a log record or span may carry.
CORRELATION_FIELDS: tuple[str, ...] = (
    "trace_id", "span_id", "request_id", "tenant_id", "service_name", "environment",
    "incident_id", "collection_run_id", "provider_id",
)

_VARS: dict[str, ContextVar[str | None]] = {
    name: ContextVar(f"hsdeep_{name}", default=None) for name in BOUND_FIELDS
}

# Set once at startup by app.core.tracing.configure_telemetry().
_static: dict[str, str] = {}


def set_static(*, service_name: str, environment: str, runtime_mode: str) -> None:
    _static.update(service_name=service_name, environment=environment, runtime_mode=runtime_mode)


@contextmanager
def bind(**ids: Any) -> Iterator[None]:
    """Bind identifiers for the enclosed block. ``None`` values are ignored;
    unknown names raise, so a typo cannot silently drop a correlation key."""
    before = {name: var.get() for name, var in _VARS.items()}
    rebind(**ids)
    try:
        yield
    finally:
        # Restores every field, including any set with rebind() inside the block.
        for name, value in before.items():
            _VARS[name].set(value)


def rebind(**ids: Any) -> None:
    """Set identifiers learnt part-way through a ``bind()`` block (a collection
    run id exists only once the run row is created). The enclosing ``bind()``
    restores the previous values on exit."""
    for name, value in ids.items():
        if name not in _VARS:
            raise KeyError(f"unknown correlation field: {name}")
        if value is not None:
            _VARS[name].set(str(value))


def snapshot() -> dict[str, str]:
    """The identifiers known right now. Absent keys were never set."""
    out: dict[str, str] = {}
    from app.core.tracing import current_ids  # local: tracing imports this module

    trace_id, span_id = current_ids()
    if trace_id:
        out["trace_id"] = trace_id
        out["span_id"] = span_id or ""
    for name, var in _VARS.items():
        value = var.get()
        if value:
            out[name] = value
    from app.core.tenant_context import current_tenant_id

    tenant = current_tenant_id.get()
    if tenant is not None:
        out["tenant_id"] = str(tenant)
    for key in ("service_name", "environment"):
        if _static.get(key):
            out[key] = _static[key]
    return out


#: Logger-name prefix -> log category (first match wins; order matters).
_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("app.request", "api"),
    ("app.api.routes.auth", "authentication"),
    ("app.api.routes.mfa", "authentication"),
    ("app.api.routes.identity", "authentication"),
    ("app.core.auth", "authentication"),
    ("app.services.auth", "authentication"),
    ("app.services.identity", "authentication"),
    ("app.services.mfa", "authentication"),
    ("app.services.audit", "security_audit"),
    ("app.services.security", "security_audit"),
    ("app.core.security", "security_audit"),
    ("app.api.routes.admin_security", "security_audit"),
    ("app.collectors", "collector"),
    ("app.services.monitoring", "collector"),
    ("app.edge", "collector"),
    ("app.scheduler", "scheduler"),
    ("apscheduler", "scheduler"),
    ("app.services.itsm", "integration"),
    ("app.services.servicenow", "integration"),
    ("app.services.integration", "integration"),
    ("app.services.notifications", "integration"),
    ("app.services.adapters", "integration"),
    ("app.ai", "integration"),
    ("app.core.db", "database"),
    ("sqlalchemy", "database"),
    ("alembic", "database"),
    ("asyncpg", "database"),
    ("app.api", "api"),
    ("uvicorn", "api"),
)


def category_for(logger_name: str) -> str:
    for prefix, category in _CATEGORIES:
        if logger_name == prefix or logger_name.startswith(prefix + ".") or logger_name.startswith(prefix + "_"):
            return category
    return "application"


def component_for(logger_name: str) -> str:
    """``app.services.rca_service`` -> ``services.rca_service``; bounded depth."""
    parts = logger_name.split(".")
    if parts and parts[0] == "app":
        parts = parts[1:]
    return ".".join(parts[:2]) or "root"


class CorrelationFilter(logging.Filter):
    """Adds the correlation identifiers, category and component to every log
    record. A value the caller passed in ``extra={...}`` is kept as is."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API name
        try:
            for key, value in snapshot().items():
                if getattr(record, key, None) is None:
                    setattr(record, key, value)
            if getattr(record, "category", None) is None:
                record.category = category_for(record.name)
            if getattr(record, "component", None) is None:
                record.component = component_for(record.name)
        except Exception:  # noqa: BLE001 - logging must never fail the caller
            pass
        return True
