"""
Distributed tracing and OTLP log export (Phase 22).

OpenTelemetry SDK, W3C Trace Context. One tracer provider per process:

* Spans are always created, so every log line and response carries a real
  ``trace_id``. They are exported only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is
  set (the OpenTelemetry Collector, which forwards to Tempo). Unset means
  nothing leaves the process and no network call is made.
* Logs are exported over OTLP to the same collector (which forwards to Loki)
  when the endpoint is set and ``OTEL_LOGS_EXPORT`` is true. stdout logging
  (app/core/logging.py) is unchanged and remains the fallback.

Nothing here invents telemetry: a span exists because code ran inside it.

SECURITY
* httpx client spans record the URL without its query string or user info.
  Some providers authenticate in the query string (PRTG ``passhash``).
* SQL spans record the statement text with bind placeholders, never the
  bound values (the instrumentation's default).
* Exported log records pass the same redaction as stdout, including the
  exception text and ``extra`` fields.
* The collector applies a second redaction pass
  (deployment/observability/otel-collector/config.yml).
"""
from __future__ import annotations

import copy
import logging
import socket
import traceback
from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.core import telemetry_context
from app.core.redaction import redact

logger = logging.getLogger(__name__)

TRACER_NAME = "hsdeep"
_STANDARD_LOG_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
_state: dict[str, Any] = {"configured": False, "provider": None, "log_provider": None, "exporting": False}


class _RootSampler(TraceIdRatioBased):
    """Sampler for spans that have no parent. A CLIENT span with no parent is
    a SQL statement or an outbound HTTP call made outside any request, job or
    event (pool pre-pings, health checks): on its own it is a one-span trace
    that explains nothing, so it is not recorded. Inside a request or a job
    the same call has a parent and is recorded as usual."""

    def should_sample(self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None):
        if kind == SpanKind.CLIENT:
            from opentelemetry.sdk.trace.sampling import Decision, SamplingResult

            return SamplingResult(Decision.DROP)
        return super().should_sample(parent_context, trace_id, name, kind, attributes, links, trace_state)

    def get_description(self) -> str:
        return f"HsdeepRootSampler{{{self.rate}}}"


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def current_ids() -> tuple[str | None, str | None]:
    """(trace_id, span_id) of the active span as lower-case hex, or (None, None)."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


def _clean(attrs: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        out[key] = value if isinstance(value, (bool, int, float)) else redact(str(value))[:1024]
    return out


@contextmanager
def span(name: str, *, kind: SpanKind = SpanKind.INTERNAL, parent: Any = None,
         expected: tuple[type[BaseException], ...] = (), **attributes: Any) -> Iterator[trace.Span]:
    """Run a block inside a span. An exception marks the span failed, records
    the (redacted) exception event and is re-raised unchanged.

    ``expected`` exception types are re-raised without marking the span failed
    (a 401 from the auth layer is an outcome, not a fault).
    Attribute names are given with ``__`` for ``.`` (``hsdeep__tenant_id``).
    """
    attrs = _clean({k.replace("__", "."): v for k, v in attributes.items()})
    with tracer().start_as_current_span(
        name, kind=kind, context=parent, attributes=attrs, record_exception=False, set_status_on_exception=False
    ) as current:
        try:
            yield current
        except BaseException as exc:
            if isinstance(exc, Exception) and not isinstance(exc, expected):
                record_error(current, exc)
            raise


def record_error(current: trace.Span, exc: BaseException) -> None:
    """Mark a span failed with a redacted exception event."""
    if not current.is_recording():
        return
    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    current.add_event("exception", {
        "exception.type": type(exc).__name__,
        "exception.message": redact(str(exc))[:2000],
        "exception.stacktrace": redact(stack)[:16000],
    })
    current.set_status(Status(StatusCode.ERROR, redact(str(exc))[:500] or type(exc).__name__))


def set_attributes(**attributes: Any) -> None:
    """Add attributes to the active span (no-op without one)."""
    current = trace.get_current_span()
    if current.is_recording():
        current.set_attributes(_clean({k.replace("__", "."): v for k, v in attributes.items()}))


# -- W3C Trace Context propagation ------------------------------------------------

def inject(carrier: dict[str, str] | None = None) -> dict[str, str]:
    """Write ``traceparent`` / ``tracestate`` for the active span into a dict
    (HTTP headers, an event envelope, a job payload)."""
    carrier = {} if carrier is None else carrier
    propagate.inject(carrier)
    return carrier


def extract(carrier: Mapping[str, str] | None) -> Any:
    """An OpenTelemetry context from a carrier. A missing or malformed
    ``traceparent`` yields the current context, so a new trace starts."""
    return propagate.extract(dict(carrier or {}))


@contextmanager
def attached(carrier: Mapping[str, str] | None) -> Iterator[None]:
    """Make the trace context in ``carrier`` current for the block. Used where
    work crosses a queue or a process and contextvars do not follow."""
    if not carrier:
        yield
        return
    token = otel_context.attach(extract(carrier))
    try:
        yield
    finally:
        otel_context.detach(token)


# -- Setup --------------------------------------------------------------------------

def _endpoint(base: str, signal: str) -> str:
    return f"{base.rstrip('/')}/v1/{signal}"


def _valid_endpoint(url: str) -> bool:
    parts = urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc) and not parts.username and not parts.password


def safe_url(url: str) -> str:
    """URL without query string, fragment or user info."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[unparseable url]"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _httpx_request_hook(current: trace.Span, request: Any) -> None:
    if not current or not current.is_recording():
        return
    try:
        url = safe_url(str(request.url))
    except Exception:  # noqa: BLE001 - a hook must never fail the request
        url = "[unavailable]"
    # Both the stable and the legacy semantic-convention names.
    current.set_attribute("url.full", url)
    current.set_attribute("http.url", url)
    for key, value in telemetry_context.snapshot().items():
        if key in ("tenant_id", "provider_id", "collection_run_id", "incident_id", "request_id"):
            current.set_attribute(f"hsdeep.{key}", value)


async def _httpx_async_request_hook(current: trace.Span, request: Any) -> None:
    _httpx_request_hook(current, request)


class _RedactingOtlpLogHandler(logging.Handler):
    """Forwards records to the OpenTelemetry log handler with the exception
    text redacted (the wrapped handler would otherwise export it raw)."""

    def __init__(self, inner: logging.Handler) -> None:
        super().__init__(level=inner.level)
        self._inner = inner

    def emit(self, record: logging.LogRecord) -> None:
        try:
            clone = copy.copy(record)
            if clone.exc_info and clone.exc_info[0] is not None:
                exc_type, exc, tb = clone.exc_info
                clone.__dict__["exception.type"] = exc_type.__name__
                clone.__dict__["exception.message"] = redact(str(exc))[:2000]
                clone.__dict__["exception.stacktrace"] = redact("".join(traceback.format_exception(exc_type, exc, tb)))[:16000]
            clone.exc_info = None
            clone.exc_text = None
            clone.__dict__["logger_name"] = record.name
            # An unset field is absent, not an empty string.
            for key in [k for k, v in clone.__dict__.items() if v is None and k not in _STANDARD_LOG_ATTRS]:
                del clone.__dict__[key]
            self._inner.emit(clone)
        except Exception:  # noqa: BLE001 - log export must never fail the caller
            self.handleError(record)

    def flush(self) -> None:
        self._inner.flush()


def configure_telemetry(settings: Any, *, span_exporter: SpanExporter | None = None) -> logging.Handler | None:
    """Install the tracer provider (once per process) and return the OTLP log
    handler to attach to the root logger, or ``None`` when log export is off.

    ``span_exporter`` is for tests (an in-memory exporter).
    """
    if _state["configured"]:
        return _state.get("log_handler")
    service_name = settings.otel_service_name or "hsdeep-backend"
    telemetry_context.set_static(
        service_name=service_name, environment=settings.app_env, runtime_mode=settings.runtime_mode
    )
    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "hsdeep",
        "service.instance.id": f"{socket.gethostname()}:{settings.runtime_mode}",
        "deployment.environment": settings.app_env,
        "deployment.environment.name": settings.app_env,
        "hsdeep.runtime_mode": settings.runtime_mode,
    })
    ratio = min(max(float(settings.otel_traces_sampler_ratio), 0.0), 1.0)
    provider = TracerProvider(resource=resource, sampler=ParentBased(_RootSampler(ratio)))

    endpoint = (settings.otel_exporter_otlp_endpoint or "").strip()
    exporting = False
    if span_exporter is not None:
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif endpoint and not _valid_endpoint(endpoint):
        logger.error("OTEL_EXPORTER_OTLP_ENDPOINT is not a valid http(s) URL without credentials; telemetry export is off.")
    elif endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=_endpoint(endpoint, "traces"), timeout=5),
            max_queue_size=4096, max_export_batch_size=512, schedule_delay_millis=2000,
        ))
        exporting = True

    trace.set_tracer_provider(provider)
    propagate.set_global_textmap(CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()]))
    _instrument_libraries()

    handler: logging.Handler | None = None
    if exporting and settings.otel_logs_export:
        handler = _otlp_log_handler(resource, endpoint)
    _state.update(configured=True, provider=provider, exporting=exporting, log_handler=handler)
    logger.info(
        "Telemetry configured: service=%s traces_export=%s logs_export=%s sample_ratio=%s",
        service_name, exporting, handler is not None, ratio,
    )
    return handler


def _otlp_log_handler(resource: Resource, endpoint: str) -> logging.Handler | None:
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    except ImportError:  # the logs SDK is still marked experimental upstream
        logger.error("OpenTelemetry logs SDK is not importable; OTLP log export is off (stdout logging continues).")
        return None
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(BatchLogRecordProcessor(
        OTLPLogExporter(endpoint=_endpoint(endpoint, "logs"), timeout=5), schedule_delay_millis=2000,
    ))
    _state["log_provider"] = log_provider
    return _RedactingOtlpLogHandler(LoggingHandler(level=logging.NOTSET, logger_provider=log_provider))


def _instrument_libraries() -> None:
    """Database and outbound-HTTP spans. Each is best-effort: a missing or
    incompatible instrumentation leaves the application working, untraced."""
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        from app.core.db import engine

        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SQLAlchemy tracing is off: %s", type(exc).__name__)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        instrumentor = HTTPXClientInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(
                request_hook=_httpx_request_hook, async_request_hook=_httpx_async_request_hook
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("httpx tracing is off: %s", type(exc).__name__)


def exporting() -> bool:
    return bool(_state["exporting"])


def shutdown() -> None:
    """Flush pending spans and log records (called from the app lifespan)."""
    for key in ("provider", "log_provider"):
        provider = _state.get(key)
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:  # noqa: BLE001
                pass
