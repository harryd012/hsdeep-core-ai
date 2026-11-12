"""
Centralized logging configuration.

Two output formats (Phase 9A), selected by ``LOG_FORMAT``:

* ``text`` (default) - the human-readable single line used since Phase 1,
  unchanged, so existing log parsing/evidence stays valid.
* ``json`` - one JSON object per line for centralized log shipping. Any
  non-standard field passed via ``logger.info(..., extra={...})`` (the request
  logger's ``method``, ``route``, ``status``, ``duration_ms``,
  ``correlation_id``, ``tenant_id``, ``user_id``) becomes a first-class JSON key
  so a log platform can query/filter on it directly.
"""
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.core.redaction import RedactingFilter, redact
from app.core.telemetry_context import CorrelationFilter


#: LogRecord attributes that must never be emitted as their own JSON key.
#: Computed from a throwaway record so it stays correct across Python versions
#: (e.g. `taskName` only exists on 3.12+). Everything else on a record came from
#: an explicit `extra={...}` and IS emitted.
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


class _JsonFormatter(logging.Formatter):
    """One JSON object per line (Phase 9A, for centralized log platforms).

    Dependency-free (``json`` + ``datetime`` only), matching the existing
    ``_JsonFormatter`` precedent in ``app/services/servicenow/audit.py``.
    Values are printed exactly as the caller passed them: no application code
    passes credentials, and request logs carry only the seven documented fields.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = (
                value
                if isinstance(value, (str, int, float, bool)) or value is None
                else str(value)
            )
        if record.exc_info:
            # Exception text can quote a URL or a connection string.
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    settings = get_settings()

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    formatter: logging.Formatter
    if settings.log_format == "json":
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    # Correlation identifiers first (trace_id, span_id, request_id, tenant_id,
    # service_name, environment, incident_id, collection_run_id, provider_id,
    # plus category / component), then redaction over the message and every
    # structured field (security programme S5, core/redaction.py).
    handler.addFilter(CorrelationFilter())
    handler.addFilter(RedactingFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = [handler]

    # Tracing, and OTLP log export to the OpenTelemetry Collector when
    # OTEL_EXPORTER_OTLP_ENDPOINT is set (Phase 22). The same two filters run
    # on the exported records. A telemetry failure never stops the process.
    try:
        from app.core.tracing import configure_telemetry

        otlp_handler = configure_telemetry(settings)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("Telemetry setup failed; continuing with stdout logging only.")
        otlp_handler = None
    if otlp_handler is not None:
        otlp_handler.addFilter(CorrelationFilter())
        otlp_handler.addFilter(RedactingFilter())
        root_logger.addHandler(otlp_handler)
    # The exporter's own transport must not log through itself.
    for noisy in ("opentelemetry", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Tame noisy third-party loggers by default
    logging.getLogger("uvicorn.access").setLevel(log_level)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.WARNING if not settings.debug else logging.INFO
    )

    # SECURITY: httpx's own request logger prints the full request URL
    # (including query string) at INFO level. Collectors that authenticate
    # via query params - e.g. PRTGCollector's username/passhash - would
    # otherwise leak credentials into logs even though app.collectors.prtg
    # and app.core.security both explicitly document a no-log rule for
    # them. Force this to WARNING unconditionally (even in debug), since
    # there is no safe "verbose" mode for this particular logger.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
