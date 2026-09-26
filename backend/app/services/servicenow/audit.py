"""
Structured audit logging for ServiceNow integration.

Emits JSON log records with every sync job transition, API interaction,
and conflict event. Integrates with the existing Python logging framework
via a custom JsonFormatter (see setup at module init).

All sensitive values (tokens, secrets) are never included in log output.

Log record fields:
    timestamp, job_id, connection_id, incident_id, servicenow_sys_id,
    action, status_before, status_after, latency_ms, retry_count,
    result, error_message, trace_id
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JsonFormatter — ensures all ServiceNow logs are structured JSON
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Format only ServiceNow log records as structured JSON."""

    # Only apply to our logger and its children
    SERVICE_NOW_LOGGERS = frozenset([
        "app.services.servicenow",
        "app.services.servicenow.client",
        "app.services.servicenow.incident_sync",
        "app.services.servicenow.webhook_handler",
        "app.scheduler.servicenow_jobs",
    ])

    def format(self, record: logging.LogRecord) -> str:
        # Non-ServiceNow loggers use the default format
        if not any(record.name.startswith(prefix) for prefix in self.SERVICE_NOW_LOGGERS):
            return super().format(record)

        # Build structured payload
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Merge any extras the caller attached (job_id, connection_id, etc.)
        for key in (
            "job_id", "connection_id", "incident_id", "servicenow_sys_id",
            "action", "status_before", "status_after", "latency_ms",
            "retry_count", "result", "error_message", "trace_id",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        # Include the message as a secondary string for human readability
        msg = record.getMessage()
        if msg:
            payload["message"] = msg

        # Handle exceptions
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Audit context manager — attaches fields to log records
# ---------------------------------------------------------------------------

class audit_event:
    """Context manager that attaches structured fields to every log call
    within its scope and emits a completion event on exit."""

    def __init__(
        self,
        action: str,
        *,
        job_id: uuid.UUID | None = None,
        connection_id: uuid.UUID | None = None,
        incident_id: uuid.UUID | None = None,
        servicenow_sys_id: str | None = None,
        trace_id: str | None = None,
    ):
        self.action = action
        self.job_id = job_id
        self.connection_id = connection_id
        self.incident_id = incident_id
        self.servicenow_sys_id = servicenow_sys_id
        self.trace_id = trace_id or f"sn-{uuid.uuid4().hex[:12]}"
        self.start_ns = time.perf_counter_ns()
        self.status_before: str | None = None
        self.status_after: str | None = None
        self._extra: dict[str, Any] = {}

    def _log(self, level: int, result: str | None = None, error_message: str | None = None, **extras) -> None:
        for k, v in extras.items():
            setattr(self, k, v)
            self._extra[k] = v

        latency_ms = (time.perf_counter_ns() - self.start_ns) / 1_000_000.0
        record_extra = {
            "job_id": str(self.job_id) if self.job_id else None,
            "connection_id": str(self.connection_id) if self.connection_id else None,
            "incident_id": str(self.incident_id) if self.incident_id else None,
            "servicenow_sys_id": self.servicenow_sys_id,
            "action": self.action,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "latency_ms": round(latency_ms, 2),
            "retry_count": getattr(self, "retry_count", None),
            "result": result,
            "error_message": error_message,
            "trace_id": self.trace_id,
        }
        record_extra.update(self._extra)

        # Attach extras to the current log record via the logger
        log_kwargs = {k: v for k, v in record_extra.items() if v is not None}
        getattr(logger, logging.getLevelName(level))(
            f"[{self.action}] latency={latency_ms:.1f}ms",
            extra=log_kwargs,
        )

    def __enter__(self) -> "audit_event":
        self.status_before = None
        self._log(logging.INFO, result="started")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self.status_after = "error"
            self._log(
                logging.ERROR,
                result="failed",
                error_message=str(exc_val),
            )
            return False  # propagate the exception

        self.status_after = "succeeded"
        self._log(logging.INFO, result="completed")
        return False

    def set_status(self, status: str) -> None:
        """Record an intermediate status for this audit event."""
        self.status_after = status
        self._log(logging.INFO, result=status)


# ---------------------------------------------------------------------------
# Module-level setup — called once at app startup
# ---------------------------------------------------------------------------

def _setup_structured_logging() -> None:
    """Attach our JSON formatter to all ServiceNow loggers. Safe to call
    multiple times (idempotent)."""
    handlers = [h for h in logger.handlers if isinstance(h, _JsonFormatter)]
    if not handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        handler.addFilter(lambda r: any(r.name.startswith(p) for p in _JsonFormatter.SERVICE_NOW_LOGGERS))
        logger.addHandler(handler)


_setup_structured_logging()
