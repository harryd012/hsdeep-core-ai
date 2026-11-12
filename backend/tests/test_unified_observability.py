"""
Phase 22 - unified observability: metrics, logs, traces and their correlation.

No running stack is needed: Prometheus, Loki and Tempo are answered by
``httpx.MockTransport`` with responses in the shapes the real services return
(captured from Prometheus 3.5, Loki 3.5 and Tempo 2.8).

Pins:
  * metric instrumentation: SQL statements are timed with a bounded label;
  * structured logging: correlation ids on every JSON line, never invented,
    secrets redacted in the message, the ``extra`` fields and the exception;
  * trace context propagation: W3C traceparent in and out of a request,
    across the event bus and the Redis envelope;
  * tenant isolation and RBAC: every new endpoint is superuser-only, a tenant
    filter always reaches the query, cross-tenant lookups are audited;
  * query security: the browser's text never becomes LogQL / TraceQL / PromQL;
    windows, limits and cursors are bounded;
  * missing and stale telemetry: NOT AVAILABLE / NO DATA / STALE are reported
    as such, NaN is never 0, nothing is healthy without evidence;
  * correlation: a link is only claimed through a shared identifier.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from app.core import telemetry_context, tracing
from app.core.auth import create_access_token
from app.core.logging import _JsonFormatter
from app.core.redaction import MASK, RedactingFilter, redact_value
from app.services import platform_correlation, platform_logs, platform_traces
from app.services.platform_infrastructure import (
    EXPORTER_METRICS, FRESHNESS_SECONDS, SIGNALS, SOURCES, STATES, classify, fetch_infrastructure, metric_names,
)

BASE = "/api/platform/observability"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TENANT = "11111111-2222-3333-4444-555555555555"


def _token(is_superuser: bool, tenant_id: uuid.UUID | None = None) -> str:
    return create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id or uuid.uuid4(), email="obs@example.test",
                               is_superuser=is_superuser, permissions=frozenset({"monitoring.read"}))


@pytest.fixture(scope="module", autouse=True)
def _telemetry_configured():
    """Importing the app configures logging and the tracer provider once."""
    import app.main  # noqa: F401


@pytest.fixture(scope="module")
def spans() -> InMemorySpanExporter:
    """Spans recorded by the process tracer provider, readable in memory."""
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


# ---------------------------------------------------------------------------
# Metric instrumentation
# ---------------------------------------------------------------------------

class TestDatabaseQueryMetrics:
    def test_operation_label_is_bounded(self):
        from app.core.metrics import db_operation

        assert db_operation("SELECT 1") == "select"
        assert db_operation("  insert into t values (1)") == "insert"
        assert db_operation("WITH x AS (SELECT 1) SELECT * FROM x") == "select"
        assert db_operation("UPDATE t SET a = 1") == "update"
        assert db_operation("DELETE FROM t") == "delete"
        # Anything else, including text built to look like a label value, is "other".
        for statement in ("VACUUM", "", "select_from_secret", 'x"} 1\n', "SET search_path = tenant_42"):
            assert db_operation(statement) == "other"

    def test_slow_query_is_counted_only_past_the_threshold(self):
        from prometheus_client import REGISTRY

        from app.core.metrics import DB_SLOW_QUERY_SECONDS, observe_db_query

        def slow():
            return REGISTRY.get_sample_value("hsdeep_db_slow_queries_total", {"operation": "update"}) or 0

        def count():
            return REGISTRY.get_sample_value("hsdeep_db_query_duration_seconds_count", {"operation": "update"}) or 0

        before_slow, before_count = slow(), count()
        observe_db_query("update", DB_SLOW_QUERY_SECONDS / 10)
        assert slow() == before_slow and count() == before_count + 1
        observe_db_query("update", DB_SLOW_QUERY_SECONDS + 0.5)
        assert slow() == before_slow + 1 and count() == before_count + 2

    async def test_a_real_statement_is_timed(self):
        from prometheus_client import REGISTRY
        from sqlalchemy import text

        import app.main  # noqa: F401 - installs the listeners
        from app.core.db import AsyncSessionLocal

        def count():
            return REGISTRY.get_sample_value("hsdeep_db_query_duration_seconds_count", {"operation": "select"}) or 0

        before = count()
        async with AsyncSessionLocal() as db:
            assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
        assert count() >= before + 1

    def test_no_high_cardinality_label_on_any_platform_metric(self):
        from prometheus_client import REGISTRY

        import app.core.metrics  # noqa: F401

        forbidden = {"tenant", "tenant_id", "user", "user_id", "email", "request_id", "trace_id", "span_id",
                     "incident_id", "collection_run_id", "provider_id", "ip", "path", "url"}
        for family in REGISTRY.collect():
            if not family.name.startswith("hsdeep_"):
                continue
            for sample in family.samples:
                assert not (set(sample.labels) & forbidden), (family.name, sample.labels)


class TestCatalogueReadsOnlyRealMetrics:
    def test_every_metric_has_a_declared_exporter_or_is_emitted_by_the_backend(self):
        from prometheus_client import REGISTRY

        import app.main  # noqa: F401
        from app.services.platform_timeseries import INFRA_SERIES

        emitted = set()
        for family in REGISTRY.collect():
            emitted.add(family.name)
            emitted.update(s.name for s in family.samples)
        declared = {m for metrics in EXPORTER_METRICS.values() for m in metrics}
        used = set()
        for spec in list(SIGNALS) + list(INFRA_SERIES):
            used |= metric_names(spec["query"])
        assert used, "the parser found no metric names"
        unknown = {m for m in used if m not in declared and m not in emitted}
        assert not unknown, f"queries read metrics nothing emits: {sorted(unknown)}"
        # And nothing is declared that no query reads (the list stays honest).
        assert not (declared - used), sorted(declared - used)

    def test_every_signal_selects_its_sources_job(self):
        for spec in SIGNALS:
            job = SOURCES[spec["source"]]["job"]
            assert f'job="{job}"' in spec["query"] or "hsdeep_" in spec["query"], spec["key"]

    def test_infra_series_are_requested_only_by_group(self):
        from app.services.platform_timeseries import ALL_GROUPS, INFRA_GROUPS, INFRA_SERIES, PLATFORM_GROUPS, SERIES

        assert {s["group"] for s in SERIES} == set(PLATFORM_GROUPS)
        assert {s["group"] for s in INFRA_SERIES} == set(INFRA_GROUPS)
        assert len({s["key"] for s in SERIES + INFRA_SERIES}) == len(SERIES) + len(INFRA_SERIES)
        assert set(ALL_GROUPS) == set(PLATFORM_GROUPS) | set(INFRA_GROUPS)

    async def test_timeseries_group_selection(self):
        from app.services.platform_timeseries import INFRA_SERIES, fetch_timeseries

        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params["query"])
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        out = await fetch_timeseries("http://prom:9090", "1h", client, groups=("host", "redis"))
        want = [s for s in INFRA_SERIES if s["group"] in ("host", "redis")]
        assert len(seen) == len(want) and {s["key"] for s in out["series"]} == {s["key"] for s in want}
        # An unknown group cannot widen the query set.
        seen.clear()
        out = await fetch_timeseries("http://prom:9090", "1h", client, groups=("nope; drop",))
        assert {s["group"] for s in out["series"]} == {"http", "collection", "scheduler", "alerts"}


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

def _record(message: str = "hello", *, name: str = "app.services.rca_service", exc_info=None, **extra) -> logging.LogRecord:
    record = logging.LogRecord(name, logging.INFO, __file__, 1, message, (), exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _emit(record: logging.LogRecord) -> dict:
    telemetry_context.CorrelationFilter().filter(record)
    RedactingFilter().filter(record)
    return json.loads(_JsonFormatter().format(record))


class TestStructuredLogging:
    def test_correlation_ids_are_added_inside_a_span(self):
        with tracing.span("test.op"), telemetry_context.bind(
            request_id="req-1", incident_id=uuid.UUID(int=7), collection_run_id=uuid.UUID(int=8), provider_id=uuid.UUID(int=9),
        ):
            line = _emit(_record())
            trace_id, span_id = tracing.current_ids()
        assert line["trace_id"] == trace_id and re.fullmatch(r"[0-9a-f]{32}", trace_id)
        assert line["span_id"] == span_id and re.fullmatch(r"[0-9a-f]{16}", span_id)
        assert line["request_id"] == "req-1"
        assert line["incident_id"] == str(uuid.UUID(int=7))
        assert line["collection_run_id"] == str(uuid.UUID(int=8))
        assert line["provider_id"] == str(uuid.UUID(int=9))
        assert line["category"] == "application" and line["component"] == "services.rca_service"
        assert line["service_name"] and line["environment"]

    def test_absent_identifiers_are_absent_not_invented(self):
        line = _emit(_record())
        for key in ("trace_id", "span_id", "request_id", "incident_id", "collection_run_id", "provider_id", "tenant_id"):
            assert key not in line, key

    def test_bind_restores_previous_values_and_rejects_unknown_fields(self):
        with telemetry_context.bind(request_id="outer"):
            with telemetry_context.bind(request_id="inner"):
                telemetry_context.rebind(collection_run_id="run-1")
                assert telemetry_context.snapshot()["request_id"] == "inner"
                assert telemetry_context.snapshot()["collection_run_id"] == "run-1"
            snap = telemetry_context.snapshot()
            assert snap["request_id"] == "outer" and "collection_run_id" not in snap
        assert "request_id" not in telemetry_context.snapshot()
        with pytest.raises(KeyError):
            with telemetry_context.bind(user_email="x@example.test"):
                pass

    def test_tenant_comes_from_the_existing_tenant_context(self):
        from app.core.tenant_context import current_tenant_id

        token = current_tenant_id.set(uuid.UUID(TENANT))
        try:
            assert _emit(_record())["tenant_id"] == TENANT
        finally:
            current_tenant_id.reset(token)

    @pytest.mark.parametrize("logger_name,category", [
        ("app.request", "api"), ("app.api.routes.devices", "api"), ("app.core.auth", "authentication"),
        ("app.api.routes.auth", "authentication"), ("app.services.audit_service", "security_audit"),
        ("app.services.audit.platform_observability", "security_audit"), ("app.collectors.prtg", "collector"),
        ("app.services.monitoring.collection_orchestrator", "collector"), ("app.scheduler.jobs", "scheduler"),
        ("app.services.servicenow.client", "integration"), ("sqlalchemy.engine", "database"),
        ("app.services.rca_service", "application"),
    ])
    def test_categories(self, logger_name, category):
        assert telemetry_context.category_for(logger_name) == category

    def test_secrets_are_redacted_in_message_extras_and_exception(self):
        try:
            raise RuntimeError("connect failed: postgresql://app:hunter2secret@db:5432/hsdeep")
        except RuntimeError:
            import sys

            record = _record(
                "login failed password=hunter2 for user", exc_info=sys.exc_info(),
                api_key="sk-live-123456", authorization="Bearer abcdefghijklmnop", note="token=abc123def456",
                connection_info={"password": "pw"}, duration_ms=12.5, status=500,
            )
        line = _emit(record)
        blob = json.dumps(line)
        for secret in ("hunter2", "sk-live-123456", "abcdefghijklmnop", "abc123def456", '"pw"'):
            assert secret not in blob, secret
        assert line["api_key"] == MASK and line["authorization"] == MASK and line["connection_info"] == MASK
        # Non-secret structured fields keep their type and value.
        assert line["duration_ms"] == 12.5 and line["status"] == 500
        assert "RuntimeError" in line["exception"]

    def test_redact_value_rules(self):
        assert redact_value("password", "x") == MASK
        assert redact_value("db_password", 12345) == MASK
        assert redact_value("route", "/api/x") == "/api/x"
        assert redact_value("retries", 3) == 3 and redact_value("ok", True) is True
        assert redact_value("note", None) is None
        assert "eyJ" not in redact_value("detail", "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl")

    def test_otlp_log_handler_redacts_the_exception_and_drops_unset_fields(self):
        captured: list[logging.LogRecord] = []

        class Sink(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = tracing._RedactingOtlpLogHandler(Sink())
        try:
            raise ValueError("bad token=supersecretvalue123")
        except ValueError:
            import sys

            record = _record("boom", exc_info=sys.exc_info(), tenant_id=None, route="/x")
        handler.emit(record)
        (out,) = captured
        assert out.exc_info is None and "supersecretvalue123" not in out.__dict__["exception.stacktrace"]
        assert out.__dict__["exception.type"] == "ValueError"
        assert "tenant_id" not in out.__dict__ and out.__dict__["route"] == "/x"
        # The caller's record is untouched (stdout still formats its own exception).
        assert record.exc_info is not None


# ---------------------------------------------------------------------------
# Trace context propagation
# ---------------------------------------------------------------------------

class TestTracePropagation:
    async def test_request_continues_the_callers_w3c_trace(self, spans):
        from app.main import app

        spans.clear()
        parent_span = "00f067aa0ba902b7"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"{BASE}/summary", headers={
                "traceparent": f"00-{TRACE_ID}-{parent_span}-01", "X-Request-ID": "req-abc-1"})
        assert resp.status_code == 401
        assert resp.headers["X-Trace-ID"] == TRACE_ID
        assert resp.headers["X-Request-ID"] == "req-abc-1" and resp.headers["X-Correlation-ID"] == "req-abc-1"
        finished = {s.name: s for s in spans.get_finished_spans() if format(s.context.trace_id, "032x") == TRACE_ID}
        server = finished[f"GET {BASE}/summary"]
        assert format(server.parent.span_id, "016x") == parent_span
        assert server.attributes["http.route"] == f"{BASE}/summary"
        assert server.attributes["http.response.status_code"] == 401
        assert server.attributes["hsdeep.request_id"] == "req-abc-1"
        # Authentication is a child span; a denial is an outcome, not a fault.
        auth = finished["auth.authenticate"]
        assert auth.parent.span_id == server.context.span_id
        assert auth.attributes["hsdeep.auth.outcome"] == "denied" and not auth.events

    async def test_authenticated_request_records_tenant_resolution(self, spans):
        from app.main import app

        spans.clear()
        tenant = uuid.uuid4()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"{BASE}/summary", headers={"Authorization": f"Bearer {_token(True, tenant)}"})
        assert resp.status_code == 200
        trace_id = resp.headers["X-Trace-ID"]
        mine = {s.name: s for s in spans.get_finished_spans() if format(s.context.trace_id, "032x") == trace_id}
        assert mine["tenant.resolve"].attributes["hsdeep.tenant_id"] == str(tenant)
        assert mine["auth.authenticate"].attributes["hsdeep.auth.outcome"] == "allowed"
        assert mine[f"GET {BASE}/summary"].attributes["hsdeep.tenant_id"] == str(tenant)
        # No credential or email on any span.
        blob = json.dumps([dict(s.attributes) for s in mine.values()], default=str)
        assert "obs@example.test" not in blob and "Bearer" not in blob

    async def test_malformed_traceparent_and_unsafe_request_id_start_clean(self):
        from app.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"{BASE}/summary", headers={
                "traceparent": "garbage", "X-Request-ID": 'x" | drop\n'})
        assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["X-Trace-ID"])
        assert resp.headers["X-Trace-ID"] != TRACE_ID
        # The unsafe id is replaced by a UUID, never echoed.
        uuid.UUID(resp.headers["X-Request-ID"])

    def test_inject_and_attached_round_trip(self):
        with tracing.span("publisher"):
            carrier = tracing.inject({})
            parent_trace, parent_span = tracing.current_ids()
        assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", carrier["traceparent"])
        assert tracing.current_ids() == (None, None)
        with tracing.attached(carrier), tracing.span("consumer") as child:
            assert tracing.current_ids()[0] == parent_trace
            assert format(child.parent.span_id, "016x") == parent_span
        assert tracing.current_ids() == (None, None)

    async def test_event_bus_handlers_continue_the_publishers_trace(self):
        from app.services.events.event_bus import DomainEvent, EventBus, EventType

        bus = EventBus()
        seen: dict[str, object] = {}
        incident = uuid.uuid4()

        async def handler(event: DomainEvent) -> None:
            seen["ids"] = tracing.current_ids()
            seen["snapshot"] = telemetry_context.snapshot()

        bus.subscribe(EventType.AI_RCA_STARTED)(handler)
        with tracing.span("request"):
            event = DomainEvent(event_type=EventType.AI_RCA_STARTED, tenant_id=uuid.uuid4(),
                                payload={"incident_id": str(incident)})
            publisher_trace, _ = tracing.current_ids()
        assert event.trace_context["traceparent"].split("-")[1] == publisher_trace
        # Processed later, outside the publisher's context (the queue worker).
        await bus._process_event_traced(event)
        assert seen["ids"][0] == publisher_trace
        assert seen["snapshot"]["incident_id"] == str(incident)
        assert tracing.current_ids() == (None, None)

    def test_redis_envelope_carries_only_a_well_formed_carrier(self):
        from app.services.events.event_bus import DomainEvent, EventType
        from app.services.events.redis_transport import decode, encode

        with tracing.span("request"):
            event = DomainEvent(event_type=EventType.AI_RCA_STARTED, tenant_id=uuid.uuid4(), payload={"a": 1})
            trace_id, _ = tracing.current_ids()
        envelope = json.loads(encode(event, source="api"))
        assert envelope["trace_id"] == trace_id
        assert decode(json.dumps(envelope)).trace_context == event.trace_context
        # A forged or malformed carrier is dropped, never trusted.
        for bad in ({"traceparent": "00-zz-yy-01"}, {"traceparent": "x", "evil": "1"}, "str", None,
                    {"tracestate": "a=b"}):
            envelope["trace_context"] = bad
            assert decode(json.dumps(envelope)).trace_context == {}
        # An envelope written before Phase 22 (no carrier) still decodes.
        del envelope["trace_context"]
        assert decode(json.dumps(envelope)).trace_context == {}

    def test_parentless_client_spans_are_not_recorded(self, spans):
        from opentelemetry.trace import SpanKind

        spans.clear()
        with tracing.span("SELECT orphan", kind=SpanKind.CLIENT) as orphan:
            assert not orphan.is_recording()
        with tracing.span("job"), tracing.span("SELECT in job", kind=SpanKind.CLIENT) as child:
            assert child.is_recording()
        names = [s.name for s in spans.get_finished_spans()]
        assert "SELECT orphan" not in names and "SELECT in job" in names

    def test_span_errors_are_redacted_and_expected_ones_are_not_faults(self, spans):
        spans.clear()
        with pytest.raises(RuntimeError):
            with tracing.span("failing", hsdeep__note="password=topsecret99"):
                raise RuntimeError("upstream said api_key=AKIAABCDEFGHIJKLMNOP")
        with pytest.raises(KeyError):
            with tracing.span("expected", expected=(KeyError,)):
                raise KeyError("x")
        done = {s.name: s for s in spans.get_finished_spans()}
        failing = done["failing"]
        blob = json.dumps([dict(failing.attributes)] + [dict(e.attributes) for e in failing.events], default=str)
        assert "topsecret99" not in blob and "AKIAABCDEFGHIJKLMNOP" not in blob
        assert failing.status.status_code.name == "ERROR" and failing.events[0].name == "exception"
        assert done["expected"].status.status_code.name == "UNSET" and not done["expected"].events

    def test_outbound_urls_lose_query_string_and_credentials(self):
        assert tracing.safe_url("https://user:pw@prtg.example/api/table.json?username=a&passhash=123#f") == \
            "https://prtg.example/api/table.json"
        assert tracing.safe_url("http://10.0.0.5:8443/x?token=abc") == "http://10.0.0.5:8443/x"

    async def test_outbound_http_span_never_records_the_query_string(self, spans):
        import asyncio

        spans.clear()
        received: dict[str, str] = {}

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            for line in head.split("\r\n")[1:]:
                name, _, value = line.partition(":")
                received[name.strip().lower()] = value.strip()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            await writer.drain()
            writer.close()

        # A real socket: the instrumentation wraps httpx's own transports.
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with tracing.span("collector.collect"):
                trace_id, _ = tracing.current_ids()
                async with httpx.AsyncClient() as client:
                    await client.get(f"http://127.0.0.1:{port}/api/table.json",
                                     params={"username": "svc", "passhash": "99887766"})
        finally:
            server.close()
            await server.wait_closed()
        # W3C context crosses the service boundary.
        assert received["traceparent"].split("-")[1] == trace_id
        client_spans = [s for s in spans.get_finished_spans() if s.kind.name == "CLIENT"]
        assert client_spans, "the outbound call produced no client span"
        blob = json.dumps([dict(s.attributes) for s in client_spans], default=str)
        assert "99887766" not in blob and "passhash" not in blob
        assert f"http://127.0.0.1:{port}/api/table.json" in blob


# ---------------------------------------------------------------------------
# Query security: LogQL / TraceQL are built, never accepted
# ---------------------------------------------------------------------------

def _filters(**kw) -> platform_logs.LogFilters:
    return platform_logs.make_filters(since=NOW - timedelta(hours=1), until=NOW, **kw)


class TestLogQueryBuilding:
    def test_plain_query_is_scoped_to_the_platform_namespace(self):
        assert platform_logs.build_logql(_filters()) == '{service_namespace="hsdeep"}'

    def test_all_filters(self):
        q = platform_logs.build_logql(_filters(
            severities=["error", "WARNING"], service="hsdeep-backend", component="scheduler.jobs", category="scheduler",
            tenant_id=TENANT, environment="production", trace_id=TRACE_ID.upper(), request_id="req-1",
            incident_id=str(uuid.UUID(int=1)), collection_run_id=str(uuid.UUID(int=2)), provider_id=str(uuid.UUID(int=3)),
            q="timeout",
        ))
        assert q.startswith('{service_namespace="hsdeep", service_name="hsdeep-backend", deployment_environment="production"}')
        assert '|~ "(?i)timeout"' in q
        assert '| detected_level=~"ERROR|WARN"' in q
        assert f'| tenant_id="{TENANT}"' in q and f'| trace_id="{TRACE_ID}"' in q
        assert '| component="scheduler.jobs"' in q and '| category="scheduler"' in q and '| request_id="req-1"' in q
        assert f'| incident_id="{uuid.UUID(int=1)}"' in q and f'| collection_run_id="{uuid.UUID(int=2)}"' in q

    @pytest.mark.parametrize("keyword", [
        '"} | drop', '" or 1=1', "a|b", ".*", "(?s).+", "\\", "x` | line_format `{{.secret}}", "}{", "[a-z]+$",
    ])
    def test_keyword_is_always_one_escaped_literal(self, keyword):
        q = platform_logs.build_logql(_filters(q=keyword))
        selector, _, rest = q.partition("} ")
        assert selector == '{service_namespace="hsdeep"'
        assert rest.startswith('|~ "(?i)')
        literal = json.loads(rest[len("|~ "):])  # exactly one string literal, nothing after it
        pattern = literal[len("(?i)"):]
        # The pattern matches the keyword itself and nothing broader.
        assert re.fullmatch(pattern, keyword)
        assert not re.fullmatch(pattern, keyword + "x") and not re.fullmatch(pattern, "zzz")

    @pytest.mark.parametrize("field,value", [
        ("service", 'x"} | drop'), ("service", "a b"), ("component", "a|b"), ("environment", "prod;"),
        ("tenant_id", "not-a-uuid"), ("tenant_id", f'{TENANT}" or "1'), ("trace_id", "xyz"), ("trace_id", TRACE_ID + "0"),
        ("request_id", "a b"), ("request_id", 'a"b'), ("incident_id", "1"), ("collection_run_id", "{}"),
        ("provider_id", "*"), ("category", "everything"), ("severities", ["LOUD"]), ("q", "x" * 201), ("q", "a\x00b"),
    ])
    def test_invalid_filter_values_are_rejected_not_repaired(self, field, value):
        with pytest.raises(platform_logs.FilterInvalid):
            _filters(**{field: value})

    def test_window_is_bounded(self):
        since, until = platform_logs.window(None, None, max_hours=168, now=NOW)
        assert until == NOW and until - since == timedelta(hours=1)
        with pytest.raises(platform_logs.FilterInvalid):
            platform_logs.window(NOW, NOW - timedelta(minutes=1), max_hours=168)
        with pytest.raises(platform_logs.FilterInvalid):
            platform_logs.window(NOW - timedelta(hours=169), NOW, max_hours=168)
        # Naive datetimes are read as UTC.
        s, u = platform_logs.window(datetime(2026, 11, 12, 11, 0), datetime(2026, 11, 12, 12, 0), max_hours=168)
        assert s.tzinfo is not None and u - s == timedelta(hours=1)


class TestTraceQueryBuilding:
    def _f(self, **kw):
        return platform_traces.make_filters(since=NOW - timedelta(hours=1), until=NOW, **kw)

    def test_queries(self):
        assert platform_traces.build_traceql(self._f()) == '{ resource.service.namespace = "hsdeep" }'
        q = platform_traces.build_traceql(self._f(
            service="hsdeep-backend", operation="GET /api/incidents/{incident_id}", tenant_id=TENANT,
            incident_id=str(uuid.UUID(int=1)), min_duration_ms=500, errors_only=True, environment="production"))
        assert 'resource.service.name = "hsdeep-backend"' in q and 'name = "GET /api/incidents/{incident_id}"' in q
        assert f'span."hsdeep.tenant_id" = "{TENANT}"' in q and "status = error" in q and "duration >= 500ms" in q
        assert 'resource.deployment.environment = "production"' in q

    @pytest.mark.parametrize("kw", [
        {"service": 'a" } || { true'}, {"operation": 'x" }'}, {"operation": "a\nb"}, {"tenant_id": "x"},
        {"min_duration_ms": -1}, {"min_duration_ms": 10**9}, {"environment": "a b"},
    ])
    def test_rejects(self, kw):
        with pytest.raises(platform_logs.FilterInvalid):
            self._f(**kw)

    @pytest.mark.parametrize("bad", ["", "abc", "0" * 32, TRACE_ID + "ff", "../../etc/passwd", TRACE_ID[:-1] + "g"])
    def test_trace_id_validation(self, bad):
        with pytest.raises(platform_logs.FilterInvalid):
            platform_traces.valid_trace_id(bad)

    def test_trace_id_is_normalised(self):
        assert platform_traces.valid_trace_id(f"  {TRACE_ID.upper()} ") == TRACE_ID


# ---------------------------------------------------------------------------
# Loki
# ---------------------------------------------------------------------------

def _loki_stream(ts_ns: int, line: str, **meta) -> dict:
    base = {"service_name": "hsdeep-backend", "service_namespace": "hsdeep", "deployment_environment": "development",
            "detected_level": "INFO", "severity_text": "INFO", "component": "request", "category": "api",
            "logger_name": "app.request"}
    return {"stream": {**base, **meta}, "values": [[str(ts_ns), line]]}


def _loki(streams_for, *, seen: list | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/loki/api/v1/query_range":
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams",
                                                                          "result": streams_for(request)}})
        if request.url.path.startswith("/loki/api/v1/label/"):
            name = request.url.path.split("/")[-2]
            return httpx.Response(200, json={"status": "success", "data": {
                "service_name": ["hsdeep-backend", "container", 'bad"name'], "deployment_environment": ["development"]}[name]})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestLokiSearch:
    #: One minute before the end of the test window.
    T0 = int(NOW.timestamp()) * 1_000_000_000 - 60_000_000_000

    async def test_lines_are_parsed_ordered_and_redacted(self):
        seen: list[httpx.Request] = []

        def streams(_):
            return [
                _loki_stream(self.T0, "api_request method=GET route=/x status=200", trace_id=TRACE_ID, span_id="6316de4e4eb757c7",
                             request_id="req-1", tenant_id=TENANT, method="GET", route="/x", status="200",
                             code_file_path="/app/x.py", telemetry_sdk_name="opentelemetry"),
                _loki_stream(self.T0 + 5_000_000_000, "provider said password=hunter2 and Bearer abcdefghijklmnop",
                             detected_level="ERROR", exception_type="RuntimeError",
                             exception_message="dsn postgresql://u:pw123456@db/x",
                             exception_stacktrace="Traceback...\nRuntimeError: token=zzzzzzzzzzzz", authorization="Bearer x"),
            ]

        out = await platform_logs.search("http://loki:3100", _filters(tenant_id=TENANT), limit=50,
                                         client=_loki(streams, seen=seen))
        assert out["configured"] and out["reachable"] and out["error"] is None and not out["has_more"]
        newest, oldest = out["items"]
        assert newest["severity"] == "ERROR" and oldest["severity"] == "INFO"
        assert oldest["trace_id"] == TRACE_ID and oldest["tenant_id"] == TENANT and oldest["request_id"] == "req-1"
        assert oldest["service"] == "hsdeep-backend" and oldest["component"] == "request" and oldest["category"] == "api"
        assert oldest["environment"] == "development"
        assert oldest["attributes"] == {"method": "GET", "route": "/x", "status": "200", "logger_name": "app.request",
                                        "code_file_path": "/app/x.py"}
        assert newest["tenant_id"] is None  # a line without a tenant has none
        blob = json.dumps(out)
        for secret in ("hunter2", "abcdefghijklmnop", "pw123456", "zzzzzzzzzzzz"):
            assert secret not in blob, secret
        assert newest["exception"]["type"] == "RuntimeError"
        # The tenant filter reached Loki, the URL did not reach the caller.
        params = seen[0].url.params
        assert f'| tenant_id="{TENANT}"' in params["query"] and params["direction"] == "backward"
        assert params["limit"] == "51" and "loki:3100" not in blob

    async def test_pagination_cursor(self):
        seen: list[httpx.Request] = []

        def streams(request):
            return [_loki_stream(self.T0 + i, f"line {i}") for i in range(4)]

        out = await platform_logs.search("http://loki:3100", _filters(), limit=3, client=_loki(streams, seen=seen))
        assert [i["message"] for i in out["items"]] == ["line 3", "line 2", "line 1"]
        assert out["has_more"] and out["next_cursor"] == str(self.T0 + 1)
        await platform_logs.search("http://loki:3100", _filters(), limit=3, cursor=out["next_cursor"],
                                   client=_loki(streams, seen=seen))
        assert seen[-1].url.params["end"] == str(self.T0 + 1)
        with pytest.raises(platform_logs.FilterInvalid):
            await platform_logs.search("http://loki:3100", _filters(), cursor="1; drop", client=_loki(streams))

    async def test_limit_is_clamped(self):
        seen: list[httpx.Request] = []
        await platform_logs.search("http://loki:3100", _filters(), limit=10**6, client=_loki(lambda r: [], seen=seen))
        assert seen[0].url.params["limit"] == str(platform_logs.MAX_LIMIT + 1)

    async def test_not_configured_unreachable_timeout_and_bad_payload(self):
        assert (await platform_logs.search("", _filters()))["configured"] is False
        bad = await platform_logs.search("ftp://loki", _filters())
        assert bad["error"] == "invalid_url" and not bad["reachable"]
        creds = await platform_logs.search("http://user:pw@loki:3100", _filters())
        assert creds["error"] == "invalid_url"

        def refuse(request):
            raise httpx.ConnectError("connect to http://secret-loki:3100 refused")

        out = await platform_logs.search("http://loki:3100", _filters(),
                                         client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
        assert out["error"] == "unreachable" and out["items"] == [] and "secret-loki" not in json.dumps(out)

        def slow(request):
            raise httpx.ReadTimeout("slow")

        out = await platform_logs.search("http://loki:3100", _filters(),
                                         client=httpx.AsyncClient(transport=httpx.MockTransport(slow)))
        assert out["error"] == "timeout"
        out = await platform_logs.search("http://loki:3100", _filters(), client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "success"}))))
        assert out["error"] == "bad_response" and not out["reachable"]
        out = await platform_logs.search("http://loki:3100", _filters(), client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(400, text="parse error at line 1"))))
        assert out["error"] == "rejected" and "parse error" not in json.dumps(out)

    async def test_volume_buckets_by_severity(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["query"].startswith("sum by (detected_level) (count_over_time({service_namespace=")
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": [
                {"metric": {"detected_level": "INFO"}, "values": [[1000, "9"], [1060, "41"]]},
                {"metric": {"detected_level": "WARN"}, "values": [[1060, "1"]]},
                {"metric": {}, "values": [[1060, "2"]]},
            ]}})

        out = await platform_logs.volume("http://loki:3100", _filters(),
                                         client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        assert out["buckets"] == [{"t": 1000, "counts": {"INFO": 9}},
                                  {"t": 1060, "counts": {"INFO": 41, "WARN": 1, "UNKNOWN": 2}}]

    async def test_facets_drop_values_that_could_not_be_filtered_on(self):
        out = await platform_logs.facets("http://loki:3100", _filters(), client=_loki(lambda r: []))
        assert out["services"] == ["container", "hsdeep-backend"] and out["environments"] == ["development"]

    async def test_export_pages_until_the_cap(self):
        calls = {"n": 0}

        def streams(request):
            calls["n"] += 1
            limit = int(request.url.params["limit"])
            end = int(request.url.params["end"])
            return [_loki_stream(end - 1 - i, f"l{end - 1 - i}") for i in range(limit)]

        lines, error, truncated = await platform_logs.export_lines("http://loki:3100", _filters(), max_lines=1200,
                                                                   client=_loki(streams))
        assert error is None and truncated and len(lines) == 1200 and calls["n"] == 3
        assert len({line["ts_ns"] for line in lines}) == 1200  # no line twice across pages


# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------

def _b64(hex_id: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_id)).decode()


def _tempo_trace(extra_attrs: list | None = None) -> dict:
    """A trace in the shape Tempo 2.8 returns from /api/v2/traces/{id}."""
    t0 = 1_794_518_177_000_000_000
    root, child, grandchild = "6316de4e4eb757c7", "ca921050d3be91a4", "1111111111111111"
    return {"trace": {"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "hsdeep-backend"}},
                                    {"key": "service.namespace", "value": {"stringValue": "hsdeep"}}]},
        "scopeSpans": [{"scope": {"name": "hsdeep"}, "spans": [
            {"traceId": _b64(TRACE_ID), "spanId": _b64(root), "name": "GET /api/x", "kind": "SPAN_KIND_SERVER",
             "startTimeUnixNano": str(t0), "endTimeUnixNano": str(t0 + 150_000_000),
             "attributes": [{"key": "hsdeep.tenant_id", "value": {"stringValue": TENANT}},
                            {"key": "hsdeep.request_id", "value": {"stringValue": "req-1"}},
                            {"key": "http.response.status_code", "value": {"intValue": "500"}}] + (extra_attrs or []),
             "status": {"code": "STATUS_CODE_ERROR", "message": "HTTP 500"}},
            {"traceId": _b64(TRACE_ID), "spanId": _b64(child), "parentSpanId": _b64(root), "name": "collector.collect",
             "kind": "SPAN_KIND_INTERNAL", "startTimeUnixNano": str(t0 + 10_000_000),
             "endTimeUnixNano": str(t0 + 140_000_000),
             "attributes": [{"key": "db.password", "value": {"stringValue": "hunter2"}},
                            {"key": "note", "value": {"stringValue": "token=abcdef123456"}}],
             "events": [{"timeUnixNano": str(t0 + 100_000_000), "name": "exception", "attributes": [
                 {"key": "exception.type", "value": {"stringValue": "TimeoutError"}},
                 {"key": "exception.message", "value": {"stringValue": "provider timed out"}}]}],
             "status": {"code": "STATUS_CODE_ERROR"}},
            {"traceId": _b64(TRACE_ID), "spanId": _b64(grandchild), "parentSpanId": _b64("2222222222222222"),
             "name": "SELECT", "kind": "SPAN_KIND_CLIENT", "startTimeUnixNano": str(t0 + 20_000_000),
             "endTimeUnixNano": str(t0 + 21_000_000)},
        ]}]}]}}


def _tempo(payload_for) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(payload_for))


class TestTempo:
    async def test_trace_is_decoded_into_the_waterfall_shape(self):
        out = await platform_traces.get_trace("http://tempo:3200", TRACE_ID, client=_tempo(
            lambda r: httpx.Response(200, json=_tempo_trace())))
        assert out["found"] and out["error"] is None and out["trace_id"] == TRACE_ID
        assert [s["name"] for s in out["spans"]] == ["GET /api/x", "collector.collect", "SELECT"]
        root, child, orphan = out["spans"]
        assert root["span_id"] == "6316de4e4eb757c7" and root["parent_span_id"] is None and root["kind"] == "server"
        assert child["parent_span_id"] == root["span_id"] and child["status_code"] == "error"
        assert root["duration_ms"] == 150.0 and out["duration_ms"] == 150.0
        assert out["errors"] == 2 and out["orphans"] == 1 and out["services"] == ["hsdeep-backend"]
        assert child["events"][0]["attributes"]["exception.type"] == "TimeoutError"
        assert out["correlation"] == {"tenant_id": [TENANT], "request_id": ["req-1"]}
        blob = json.dumps(out)
        assert "hunter2" not in blob and "abcdef123456" not in blob and "tempo:3200" not in blob

    async def test_hex_ids_are_accepted_too(self):
        payload = _tempo_trace()
        for span in payload["trace"]["resourceSpans"][0]["scopeSpans"][0]["spans"]:
            for key in ("traceId", "spanId", "parentSpanId"):
                if key in span:
                    span[key] = base64.b64decode(span[key]).hex()
        out = await platform_traces.get_trace("http://tempo:3200", TRACE_ID, client=_tempo(
            lambda r: httpx.Response(200, json={"batches": payload["trace"]["resourceSpans"]})))
        assert out["found"] and len(out["spans"]) == 3

    async def test_missing_trace_is_an_answer_and_failures_are_categories(self):
        out = await platform_traces.get_trace("http://tempo:3200", TRACE_ID, client=_tempo(lambda r: httpx.Response(404)))
        assert out["reachable"] and out["found"] is False and out["error"] is None and out["spans"] == []
        assert (await platform_traces.get_trace("", TRACE_ID))["configured"] is False

        def refuse(request):
            raise httpx.ConnectError("no route to http://secret-tempo")

        out = await platform_traces.get_trace("http://tempo:3200", TRACE_ID, client=_tempo(refuse))
        assert out["error"] == "unreachable" and not out["found"] and "secret-tempo" not in json.dumps(out)
        # Spans of another trace in the payload are never shown under this id.
        other = _tempo_trace()
        for span in other["trace"]["resourceSpans"][0]["scopeSpans"][0]["spans"]:
            span["traceId"] = _b64("a" * 32)
        out = await platform_traces.get_trace("http://tempo:3200", TRACE_ID, client=_tempo(
            lambda r: httpx.Response(200, json=other)))
        assert out["found"] is False

    async def test_search(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"traces": [
                {"traceID": "d48eb78aed7c513a49725057885dbf8", "rootServiceName": "hsdeep-backend", "rootTraceName": "GET /api/x",
                 "startTimeUnixNano": "1794518288905146000", "durationMs": 6,
                 "serviceStats": {"hsdeep-backend": {"spanCount": 4, "errorCount": 1}}},
                {"traceID": TRACE_ID, "rootServiceName": "hsdeep-backend", "rootTraceName": "scheduler.tick",
                 "startTimeUnixNano": "1794518388905146000"},
                {"traceID": "not hex"},
            ]})

        f = platform_traces.make_filters(since=NOW - timedelta(hours=1), until=NOW, min_duration_ms=100, tenant_id=TENANT)
        out = await platform_traces.search("http://tempo:3200", f, limit=10**6, client=_tempo(handler))
        params = seen[0].url.params
        assert params["limit"] == str(platform_traces.MAX_LIMIT) and "duration >= 100ms" in params["q"]
        assert f'span."hsdeep.tenant_id" = "{TENANT}"' in params["q"]
        newest, older = out["items"]
        assert newest["trace_id"] == TRACE_ID and newest["root_name"] == "scheduler.tick"
        # What Tempo did not report is None, never 0.
        assert newest["duration_ms"] is None and newest["spans"] is None and newest["errors"] is None
        assert older["trace_id"] == "0d48eb78aed7c513a49725057885dbf8" and older["spans"] == 4 and older["errors"] == 1


# ---------------------------------------------------------------------------
# Missing and stale telemetry (host / infrastructure)
# ---------------------------------------------------------------------------

def _prom(table: dict, *, down: bool = False) -> httpx.AsyncClient:
    """Answers each PromQL query from ``table`` (expr -> [(labels, value)])."""
    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            raise httpx.ConnectError("refused")
        expr = request.url.params["query"]
        if expr == "vector(1)":
            rows = [({}, 1)]
        else:
            rows = table.get(expr)
            if rows is None:
                rows = next((v for k, v in table.items() if k.endswith("*") and expr.startswith(k[:-1])), [])
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": [
            {"metric": m, "value": [0, str(v)]} for m, v in rows]}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _q(key: str) -> str:
    return next(s["query"] for s in SIGNALS if s["key"] == key)


def _by_key(out: dict, kind: str = "signals") -> dict:
    return {row["key"]: row for row in out[kind]}


HOST = {"instance": "host-a:9100"}


class TestInfrastructureStates:
    async def test_no_exporters_means_not_available_never_zero_or_healthy(self):
        out = await fetch_infrastructure("http://prom:9090", client=_prom({}))
        assert out["configured"] and out["reachable"]
        assert {s["state"] for s in out["sources"]} == {"NOT AVAILABLE"}
        assert {s["state"] for s in out["signals"]} == {"NOT AVAILABLE"}
        assert all(s["rows"] == [] for s in out["signals"])
        host = _by_key(out, "sources")["host"]
        assert 'job "node"' in host["reason"] and "node_exporter" in host["reason"]
        assert {u["key"] for u in out["unsupported"]} == {"gpu_intel", "npu_intel"}
        assert out["states"] == list(STATES)

    async def test_healthy_host_with_evidence(self):
        table = {
            'up{job="node"}': [(HOST, 1)], 'time() - max(max_over_time((timestamp(up{job="node"})*': [({}, 4)],
            _q("host_cpu_util"): [(HOST, 31.5)], _q("host_memory_util"): [(HOST, 40)],
            _q("host_disk_used_pct"): [({**HOST, "mountpoint": "/"}, 55), ({**HOST, "mountpoint": "/data"}, 10)],
            _q("host_cpu_cores"): [(HOST, 8)], _q("host_uptime"): [(HOST, 86400)],
        }
        out = await fetch_infrastructure("http://prom:9090", client=_prom(table))
        sources, signals = _by_key(out, "sources"), _by_key(out)
        assert sources["host"]["state"] == "HEALTHY" and sources["host"]["targets_up"] == 1
        assert sources["host"]["last_success_age_seconds"] == 4
        assert signals["host_cpu_util"]["state"] == "HEALTHY"
        assert signals["host_cpu_util"]["rows"] == [{"labels": {"instance": "host-a:9100"}, "value": 31.5, "state": "HEALTHY"}]
        assert len(signals["host_disk_used_pct"]["rows"]) == 2
        # Measured but not judged: no threshold, so no health state at all.
        assert signals["host_cpu_cores"]["state"] is None and signals["host_cpu_cores"]["thresholds"] is None
        assert signals["host_uptime"]["rows"][0]["state"] is None
        # Scraped, but the kernel gave no cpufreq: NO DATA, with the reason, not 0.
        freq = signals["host_cpu_frequency"]
        assert freq["state"] == "NO DATA" and freq["rows"] == [] and "cpufreq" in freq["reason"]
        assert out["instances"] == ["host-a:9100"]

    async def test_thresholds_raise_the_source(self):
        table = {'up{job="node"}': [(HOST, 1)], 'time() - max(max_over_time((timestamp(up{job="node"})*': [({}, 4)],
                 _q("host_cpu_util"): [(HOST, 90)], _q("host_memory_util"): [(HOST, 97)]}
        out = await fetch_infrastructure("http://prom:9090", client=_prom(table))
        signals = _by_key(out)
        assert signals["host_cpu_util"]["state"] == "WARNING" and signals["host_memory_util"]["state"] == "CRITICAL"
        host = _by_key(out, "sources")["host"]
        assert host["state"] == "CRITICAL" and "Memory utilisation" in host["reason"]

    async def test_nan_and_infinity_are_no_data(self):
        table = {'up{job="node"}': [(HOST, 1)], 'time() - max(max_over_time((timestamp(up{job="node"})*': [({}, 4)],
                 _q("host_cpu_util"): [(HOST, "NaN")], _q("host_disk_latency"): [(HOST, "+Inf")]}
        out = await fetch_infrastructure("http://prom:9090", client=_prom(table))
        signals = _by_key(out)
        assert signals["host_cpu_util"]["state"] == "NO DATA" and signals["host_cpu_util"]["rows"] == []
        assert signals["host_disk_latency"]["state"] == "NO DATA"
        # Scraped but nothing measured: the source is NO DATA, not healthy.
        assert _by_key(out, "sources")["host"]["state"] == "NO DATA"

    async def test_stale_when_the_exporter_stopped_answering(self):
        table = {'up{job="redis"}': [({"instance": "r:9121"}, 0)],
                 'time() - max(max_over_time((timestamp(up{job="redis"})*': [({}, 640)]}
        out = await fetch_infrastructure("http://prom:9090", client=_prom(table))
        redis = _by_key(out, "sources")["redis"]
        assert redis["state"] == "STALE" and "640" in redis["reason"] and redis["targets_up"] == 0
        assert _by_key(out)["redis_memory"]["state"] == "STALE"

    async def test_stale_when_the_last_scrape_is_older_than_the_window(self):
        table = {'up{job="postgres"}': [({"instance": "p:9187"}, 1)],
                 'time() - max(max_over_time((timestamp(up{job="postgres"})*': [({}, FRESHNESS_SECONDS + 30)]}
        out = await fetch_infrastructure("http://prom:9090", client=_prom(table))
        assert _by_key(out, "sources")["postgres"]["state"] == "STALE"

    async def test_no_data_when_a_target_never_answered(self):
        table = {'up{job="cadvisor"}': [({"instance": "c:8080"}, 0)]}
        out = await fetch_infrastructure("http://prom:9090", client=_prom(table))
        cadvisor = _by_key(out, "sources")["containers"]
        assert cadvisor["state"] == "NO DATA" and cadvisor["last_success_age_seconds"] is None
        assert _by_key(out)["container_cpu"]["state"] == "NO DATA"

    async def test_degraded_when_some_targets_are_down(self):
        table = {'up{job="node"}': [(HOST, 1), ({"instance": "host-b:9100"}, 0)],
                 'time() - max(max_over_time((timestamp(up{job="node"})*': [({}, 3)],
                 _q("host_cpu_util"): [(HOST, 10)]}
        out = await fetch_infrastructure("http://prom:9090", client=_prom(table))
        host = _by_key(out, "sources")["host"]
        assert host["state"] == "DEGRADED" and host["targets_up"] == 1 and host["targets_total"] == 2

    async def test_instance_filter_and_rejection_of_unsafe_values(self):
        table = {'up{job="node"}': [(HOST, 1), ({"instance": "host-b:9100"}, 1)],
                 'time() - max(max_over_time((timestamp(up{job="node"})*': [({}, 3)],
                 _q("host_cpu_util"): [(HOST, 10), ({"instance": "host-b:9100"}, 20)]}
        out = await fetch_infrastructure("http://prom:9090", "host-b:9100", client=_prom(table))
        assert [r["labels"]["instance"] for r in _by_key(out)["host_cpu_util"]["rows"]] == ["host-b:9100"]
        out = await fetch_infrastructure("http://prom:9090", 'x"} or up', client=_prom(table))
        assert out["instance"] is None and len(_by_key(out)["host_cpu_util"]["rows"]) == 2

    async def test_prometheus_missing_or_down(self):
        out = await fetch_infrastructure("")
        assert out["configured"] is False and out["sources"] == [] and out["signals"] == []
        out = await fetch_infrastructure("http://prom:9090", client=_prom({}, down=True))
        assert out["configured"] and not out["reachable"] and out["error"] == "unreachable" and out["signals"] == []
        assert (await fetch_infrastructure("not a url"))["error"] == "invalid_url"

    def test_classify(self):
        spec = {"warn": 80, "crit": 90, "lower_is_worse": False}
        assert [classify(spec, v) for v in (0, 79.9, 80, 89.9, 90, 100)] == \
            ["HEALTHY", "HEALTHY", "WARNING", "WARNING", "CRITICAL", "CRITICAL"]
        assert classify({"warn": None, "crit": None, "lower_is_worse": False}, 5) is None
        low = {"warn": 0.95, "crit": 0.8, "lower_is_worse": True}
        assert [classify(low, v) for v in (0.99, 0.9, 0.5)] == ["HEALTHY", "WARNING", "CRITICAL"]


class TestPipelineAndHealth:
    async def test_pipeline_states(self):
        from app.services.platform_health import fetch_pipeline

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200 if request.url.host != "tempo" else 503)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        out = await fetch_pipeline("http://otel:13133", "http://loki:3100", "http://tempo:3200", exporting=True, client=client)
        by = {c["key"]: c for c in out["components"]}
        assert by["otel_collector"]["status"] == "HEALTHY" and by["loki"]["status"] == "HEALTHY"
        assert by["tempo"]["status"] == "CRITICAL" and by["backend_export"]["status"] == "HEALTHY"
        out = await fetch_pipeline("", "", "", exporting=False, client=client)
        assert {c["status"] for c in out["components"]} == {"NOT AVAILABLE"}
        # Exporting, but nothing confirms the collector: UNKNOWN, not healthy.
        out = await fetch_pipeline("", "", "", exporting=True, client=client)
        assert {c["key"]: c["status"] for c in out["components"]}["backend_export"] == "UNKNOWN"
        assert "otel:13133" not in json.dumps(out) and "loki:3100" not in json.dumps(out)

    async def test_health_stops_listing_a_signal_once_its_exporter_is_scraped(self):
        from app.services.platform_health import evaluate

        def client(table):
            def handler(request: httpx.Request) -> httpx.Response:
                rows = table.get(request.url.params.get("query", ""), [])
                return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": [
                    {"metric": m, "value": [0, str(v)]} for m, v in rows]}})
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        base = {'max(up{job="prometheus"})': [({}, 1)]}
        keys = lambda out: {item["key"] for item in out["not_available"]}  # noqa: E731
        assert keys(await evaluate("http://prom:9090", client=client(base))) == {"frontend", "host", "containers", "db_queries"}
        with_node = {**base, 'up{job="node"}': [({"instance": "n"}, 0)],
                     "count(hsdeep_db_query_duration_seconds_count)": [({}, 5)]}
        assert keys(await evaluate("http://prom:9090", client=client(with_node))) == {"frontend", "containers"}
        assert keys(await evaluate("")) == {"frontend", "host", "containers", "db_queries"}


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

class _FakeDb:
    """Answers the three lookups in order: alerts, incidents, collection runs."""

    def __init__(self, *results, fail: bool = False):
        self._results, self.fail, self.statements = list(results), fail, []

    async def execute(self, statement):
        if self.fail:
            raise RuntimeError("database is down")
        self.statements.append(str(statement))
        rows = self._results.pop(0)

        class _Scalars:
            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return rows

        return _Scalars()


def _both(trace_payload, streams) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "tempo":
            return trace_payload(request)
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "streams", "result": streams}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestCorrelation:
    async def test_links_only_through_shared_identifiers(self):
        from types import SimpleNamespace as NS

        alert_id, incident_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        extra = [{"key": "hsdeep.alert_id", "value": {"stringValue": str(alert_id)}}]
        streams = [_loki_stream(1_794_518_177_050_000_000, "collection failed", detected_level="ERROR", trace_id=TRACE_ID,
                                collection_run_id=str(run_id), tenant_id=TENANT)]
        when = datetime(2026, 11, 12, 21, 0, tzinfo=timezone.utc)
        db = _FakeDb(
            [NS(id=alert_id, tenant_id=uuid.UUID(TENANT), title="CPU high", severity="critical", status="open",
                source="prtg", triggered_at=when, incident_id=incident_id)],
            [NS(id=incident_id, tenant_id=uuid.UUID(TENANT), title="Core switch down", severity="critical", status="open",
                priority="P1", opened_at=when, recommended_sop="SOP-042")],
            [NS(id=run_id, tenant_id=uuid.UUID(TENANT), status="error", trigger="scheduler", started_at=when,
                finished_at=when, error="login failed password=hunter2")],
        )
        out = await platform_correlation.correlate(
            TRACE_ID, tempo_url="http://tempo:3200", loki_url="http://loki:3100", db=db, max_window_hours=168,
            client=_both(lambda r: httpx.Response(200, json=_tempo_trace(extra)), streams),
            now=datetime(2026, 11, 13, tzinfo=timezone.utc))
        links = out["links"]
        assert links["trace"]["state"] == "linked" and links["trace"]["items"][0]["errors"] == 2
        assert links["logs"]["state"] == "linked" and links["logs"]["by"] == "trace_id" and links["logs"]["count"] == 1
        assert links["alerts"]["state"] == "linked" and links["alerts"]["items"][0]["title"] == "CPU high"
        # The incident came through the alert's incident_id; its page holds SOP and RCA.
        incident = links["incidents"]["items"][0]
        assert links["incidents"]["state"] == "linked" and incident["route"] == f"/incidents/{incident_id}"
        assert incident["recommended_sop"] == "SOP-042"
        run = links["collection_runs"]["items"][0]
        assert links["collection_runs"]["state"] == "linked" and "hunter2" not in run["error"]
        # Metrics are never presented as linked.
        assert links["metrics"]["state"] == "time_aligned" and links["metrics"]["by"] is None
        assert "not linked by identifier" in links["metrics"]["note"]
        assert out["window"]["basis"] == "trace"
        assert out["identifiers"]["collection_run_id"] == [str(run_id)] and out["identifiers"]["alert_id"] == [str(alert_id)]

    async def test_no_identifier_means_no_claim(self):
        db = _FakeDb()
        out = await platform_correlation.correlate(
            TRACE_ID, tempo_url="http://tempo:3200", loki_url="http://loki:3100", db=db, max_window_hours=168,
            client=_both(lambda r: httpx.Response(200, json=_tempo_trace()), []))
        links = out["links"]
        assert links["trace"]["state"] == "linked" and links["logs"]["state"] == "none"
        for key in ("alerts", "incidents", "collection_runs"):
            assert links[key]["state"] == "missing_identifier" and links[key]["items"] == []
        assert db.statements == []  # nothing was looked up by time or by guess

    async def test_identifier_without_a_match_is_none_not_linked(self):
        extra = [{"key": "hsdeep.incident_id", "value": {"stringValue": str(uuid.uuid4())}}]
        out = await platform_correlation.correlate(
            TRACE_ID, tempo_url="http://tempo:3200", loki_url="http://loki:3100", db=_FakeDb([]), max_window_hours=168,
            client=_both(lambda r: httpx.Response(200, json=_tempo_trace(extra)), []))
        assert out["links"]["incidents"]["state"] == "none"

    async def test_unavailable_backends_are_reported_as_such(self):
        out = await platform_correlation.correlate(
            TRACE_ID, tempo_url="", loki_url="", db=_FakeDb(fail=True), max_window_hours=168)
        links = out["links"]
        assert links["trace"]["state"] == "unavailable" and links["logs"]["state"] == "unavailable"
        assert out["window"]["basis"] == "lookback"
        # With no identifiers there is nothing to ask the database for.
        assert links["alerts"]["state"] == "missing_identifier"

    async def test_trace_absent_from_tempo_but_logs_exist(self):
        streams = [_loki_stream(1_794_518_177_050_000_000, "request done", trace_id=TRACE_ID,
                                incident_id=str(uuid.uuid4()))]
        out = await platform_correlation.correlate(
            TRACE_ID, tempo_url="http://tempo:3200", loki_url="http://loki:3100", db=_FakeDb(fail=True),
            max_window_hours=6, client=_both(lambda r: httpx.Response(404), streams),
            now=datetime(2026, 11, 13, tzinfo=timezone.utc))
        links = out["links"]
        assert links["trace"]["state"] == "none" and links["logs"]["state"] == "linked"
        assert links["incidents"]["state"] == "unavailable"  # the database failed: not "no incident"
        since, until = (datetime.fromisoformat(out["window"][k]) for k in ("since", "until"))
        assert until - since == timedelta(hours=6)  # the lookback respects the configured maximum


# ---------------------------------------------------------------------------
# Endpoints: RBAC, tenant isolation, validation, rate limit, audit
# ---------------------------------------------------------------------------

ENDPOINTS = ["/infrastructure", "/pipeline", "/logs", "/logs/volume", "/logs/facets", "/logs/export", "/traces",
             f"/traces/{TRACE_ID}", f"/correlation/{TRACE_ID}"]


@pytest.fixture
def api(monkeypatch):
    """The app with Loki / Tempo / Prometheus unset and a fresh rate limiter."""
    from app.api.routes import platform_observability as routes
    from app.core.config import get_settings

    settings = get_settings()
    for name in ("loki_url", "tempo_url", "prometheus_url", "otel_collector_health_url"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "observability_queries_per_minute", 1000)
    monkeypatch.setattr(routes, "_query_limiter", None)
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestEndpoints:
    @pytest.mark.parametrize("path", ENDPOINTS)
    async def test_superuser_only(self, api, path):
        async with api as client:
            assert (await client.get(BASE + path)).status_code == 401
            resp = await client.get(BASE + path, headers={"Authorization": f"Bearer {_token(False)}"})
            # A tenant user, even with monitoring.read, never sees platform-wide telemetry.
            assert resp.status_code == 403, path

    async def test_unconfigured_backends_say_so(self, api):
        headers = {"Authorization": f"Bearer {_token(True)}"}
        async with api as client:
            logs = (await client.get(f"{BASE}/logs", headers=headers)).json()
            assert logs["configured"] is False and logs["items"] == []
            traces = (await client.get(f"{BASE}/traces", headers=headers)).json()
            assert traces["configured"] is False and traces["items"] == []
            trace_ = (await client.get(f"{BASE}/traces/{TRACE_ID}", headers=headers)).json()
            assert trace_["configured"] is False and trace_["found"] is False
            infra = (await client.get(f"{BASE}/infrastructure", headers=headers)).json()
            assert infra["configured"] is False and infra["signals"] == []
            pipeline = (await client.get(f"{BASE}/pipeline", headers=headers)).json()
            assert {c["status"] for c in pipeline["components"]} <= {"NOT AVAILABLE", "UNKNOWN"}
            assert (await client.get(f"{BASE}/logs/export", headers=headers)).status_code == 503

    @pytest.mark.parametrize("path,params", [
        ("/logs", {"tenant_id": "x"}), ("/logs", {"trace_id": "zz"}), ("/logs", {"service": 'a"}'}),
        ("/logs", {"severity": "LOUD"}), ("/logs", {"limit": 501}), ("/logs", {"limit": 0}),
        ("/logs", {"since": "2026-11-01T00:00:00Z", "until": "2026-11-12T00:00:00Z"}),
        ("/logs", {"since": "2026-11-12T00:00:00Z", "until": "2026-11-11T00:00:00Z"}),
        ("/logs", {"q": "x" * 201}), ("/logs/export", {"format": "xlsx"}), ("/logs/volume", {"category": "all"}),
        ("/traces", {"operation": 'x" }'}), ("/traces", {"min_duration_ms": -5}), ("/traces", {"limit": 101}),
        ("/traces/abc", {}), ("/correlation/" + "0" * 32, {}), ("/timeseries", {"group": "host; drop"}),
        ("/timeseries", {"range": "5y"}),
    ])
    async def test_invalid_input_is_422(self, api, path, params):
        async with api as client:
            resp = await client.get(BASE + path, params=params, headers={"Authorization": f"Bearer {_token(True)}"})
            assert resp.status_code == 422, (path, params, resp.text[:200])

    async def test_timeseries_keeps_its_phase_21_contract(self, api):
        async with api as client:
            headers = {"Authorization": f"Bearer {_token(True)}"}
            default = (await client.get(f"{BASE}/timeseries", headers=headers)).json()
            assert set(default) == {"range", "step_seconds", "checked_at", "series", "configured", "reachable", "error"}
            assert (await client.get(f"{BASE}/timeseries", params={"group": ["host", "redis"]}, headers=headers)).status_code == 200

    async def test_rate_limit(self, api, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "observability_queries_per_minute", 3)
        headers = {"Authorization": f"Bearer {_token(True)}"}
        async with api as client:
            codes = [(await client.get(f"{BASE}/traces", headers=headers)).status_code for _ in range(5)]
            assert codes == [200, 200, 200, 429, 429]
            # Another user has their own budget.
            other = {"Authorization": f"Bearer {_token(True)}"}
            assert (await client.get(f"{BASE}/traces", headers=other)).status_code == 200

    async def test_tenant_filter_reaches_loki_and_is_audited(self, api, monkeypatch, caplog):
        from app.core.config import get_settings
        from app.services import audit_service

        monkeypatch.setattr(get_settings(), "loki_url", "http://loki:3100")
        queries: list[str] = []

        async def fake_search(loki_url, filters, **kw):
            queries.append(platform_logs.build_logql(filters))
            return {"configured": True, "reachable": True, "error": None, "items": [], "has_more": False,
                    "next_cursor": None}

        audited: list[dict] = []

        async def fake_record(self, **kw):
            audited.append(kw)

        monkeypatch.setattr(platform_logs, "search", fake_search)
        monkeypatch.setattr(audit_service.AuditService, "record", fake_record)
        own_tenant = uuid.uuid4()
        headers = {"Authorization": f"Bearer {_token(True, own_tenant)}"}
        with caplog.at_level(logging.INFO, logger="app.services.audit.platform_observability"):
            async with api as client:
                assert (await client.get(f"{BASE}/logs", params={"tenant_id": TENANT, "q": "secret words"},
                                         headers=headers)).status_code == 200
                assert (await client.get(f"{BASE}/logs", params={"tenant_id": str(own_tenant)}, headers=headers)).status_code == 200
                assert (await client.get(f"{BASE}/logs", headers=headers)).status_code == 200
        assert f'| tenant_id="{TENANT}"' in queries[0] and f'| tenant_id="{own_tenant}"' in queries[1]
        assert "tenant_id" not in queries[2]
        # Only the look into ANOTHER tenant is written to the audit trail.
        assert [a["action"] for a in audited] == ["platform_observability.logs.tenant_search"]
        assert audited[0]["context"]["tenant_filter"] == TENANT and audited[0]["tenant_id"] == own_tenant
        # Every query is in the security-audit log; the keyword text is not.
        records = [r for r in caplog.records if r.name == "app.services.audit.platform_observability"]
        assert len(records) == 3 and all(r.audit_action == "logs.search" for r in records)
        assert "secret words" not in json.dumps([r.__dict__ for r in records], default=str)
        assert records[0].keyword is True

    async def test_export_is_capped_redacted_safe_for_spreadsheets_and_audited(self, api, monkeypatch):
        from app.core.config import get_settings
        from app.services import audit_service

        monkeypatch.setattr(get_settings(), "loki_url", "http://loki:3100")
        line = platform_logs._line({"service_name": "hsdeep-backend", "detected_level": "ERROR", "trace_id": TRACE_ID},
                                   "1794518177000000000", "=HYPERLINK(\"http://evil\") password=hunter2")

        async def fake_export(loki_url, filters, **kw):
            return [line], None, True

        audited: list[dict] = []

        async def fake_record(self, **kw):
            audited.append(kw)

        monkeypatch.setattr(platform_logs, "export_lines", fake_export)
        monkeypatch.setattr(audit_service.AuditService, "record", fake_record)
        headers = {"Authorization": f"Bearer {_token(True)}"}
        async with api as client:
            resp = await client.get(f"{BASE}/logs/export", headers=headers)
            assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
            assert resp.headers["X-Export-Truncated"] == "true" and "attachment" in resp.headers["content-disposition"]
            header, row = resp.text.strip().splitlines()
            assert header.split(",")[:3] == ["timestamp", "severity", "service"]
            assert "hunter2" not in row and "'=HYPERLINK" in row  # formula neutralised
            ndjson = await client.get(f"{BASE}/logs/export", params={"format": "ndjson"}, headers=headers)
            assert json.loads(ndjson.text.splitlines()[0])["trace_id"] == TRACE_ID
        assert [a["action"] for a in audited] == ["platform_observability.logs.export"] * 2
        assert audited[0]["context"]["lines"] == 1 and audited[0]["context"]["truncated"] is True


# ---------------------------------------------------------------------------
# Deployment (static checks; no Docker needed)
# ---------------------------------------------------------------------------

import yaml  # noqa: E402
from pathlib import Path  # noqa: E402

OBS = Path(__file__).resolve().parents[2] / "deployment" / "observability"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestDeployment:
    compose = _yaml(OBS / "docker-compose.yml")

    def test_phase_21_services_are_unchanged_and_new_ones_added(self):
        services = self.compose["services"]
        assert {"prometheus", "grafana"} <= set(services)
        assert {"otel-collector", "loki", "tempo", "node-exporter", "cadvisor", "postgres-exporter",
                "redis-exporter", "dcgm-exporter", "volume-init"} <= set(services)
        # No second Prometheus, Grafana or log system.
        images = [svc["image"].split(":")[0] for svc in services.values()]
        assert len(images) == len(set(images))
        assert services["dcgm-exporter"]["profiles"] == ["gpu"]  # never started by default

    def test_only_what_the_backend_needs_is_published_and_only_on_loopback(self):
        published = {}
        for name, svc in self.compose["services"].items():
            for port in svc.get("ports", []):
                assert port.startswith("127.0.0.1:"), (name, port)
                published.setdefault(name, []).append(port.rsplit(":", 1)[-1])
        assert published["otel-collector"] == ["4318", "13133"]
        assert published["loki"] == ["3100"] and published["tempo"] == ["3200"]
        for exporter in ("node-exporter", "cadvisor", "postgres-exporter", "redis-exporter", "dcgm-exporter"):
            assert exporter not in published, exporter

    def test_no_secret_is_an_environment_value(self):
        services = self.compose["services"]
        pg, redis = services["postgres-exporter"]["environment"], services["redis-exporter"]["environment"]
        assert pg["DATA_SOURCE_PASS_FILE"] == "/run/secrets/postgres_exporter_password"
        assert "DATA_SOURCE_PASS" not in pg and "DATA_SOURCE_NAME" not in pg
        assert redis["REDIS_PASSWORD_FILE"].startswith("/run/secrets/") and "REDIS_PASSWORD" not in redis
        assert "./secrets/postgres_exporter_password:/run/secrets/postgres_exporter_password:ro" in services["postgres-exporter"]["volumes"]
        text = (OBS / ".env.example").read_text(encoding="utf-8")
        assert not re.search(r"(?im)^[A-Z_]*(PASSWORD|SECRET|TOKEN)[A-Z_]*=\S", text)
        for line in text.splitlines():
            if "URI=" in line or "ADDR=" in line:
                assert "@" not in line, line  # no user:password@ in a connection setting

    def test_retention_and_persistence(self):
        services = self.compose["services"]
        assert "loki-data:/loki" in services["loki"]["volumes"]
        assert "tempo-data:/var/tempo" in services["tempo"]["volumes"]
        assert services["loki"]["environment"]["LOKI_RETENTION"] == "${LOKI_RETENTION:-168h}"
        assert services["tempo"]["environment"]["TEMPO_RETENTION"] == "${TEMPO_RETENTION:-168h}"
        loki = (OBS / "loki" / "loki.yml").read_text(encoding="utf-8")
        assert "retention_period: ${LOKI_RETENTION:-168h}" in loki and "retention_enabled: true" in loki
        assert "reporting_enabled: false" in loki
        tempo = (OBS / "tempo" / "tempo.yml").read_text(encoding="utf-8")
        assert "block_retention: ${TEMPO_RETENTION:-168h}" in tempo and "reporting_enabled: false" in tempo
        env = (OBS / ".env.example").read_text(encoding="utf-8")
        assert "LOKI_RETENTION=168h" in env and "TEMPO_RETENTION=168h" in env

    def test_loki_indexes_only_bounded_labels(self):
        loki = (OBS / "loki" / "loki.yml").read_text(encoding="utf-8")
        block = loki[loki.index("otlp_config"):loki.index("compactor:")]
        labels = re.findall(r"^\s+- ([a-z.]+)$", block, flags=re.M)
        assert labels == ["service.name", "service.namespace", "deployment.environment"]
        for forbidden in ("tenant", "trace", "request", "incident", "user"):
            assert forbidden not in block

    def test_collector_pipelines_redact_and_export_to_loki_and_tempo(self):
        raw = (OBS / "otel-collector" / "config.yml").read_text(encoding="utf-8")
        cfg = yaml.safe_load(raw)
        pipelines = cfg["service"]["pipelines"]
        assert pipelines["traces"]["exporters"] == ["otlphttp/tempo"]
        assert pipelines["logs"]["exporters"] == ["otlphttp/loki"]
        for name, pipeline in pipelines.items():
            assert "redaction" in pipeline["processors"] and "memory_limiter" in pipeline["processors"], name
            assert pipeline["processors"].index("redaction") < pipeline["processors"].index("batch"), name
        # Metrics are scraped by Prometheus, not pushed through the collector.
        assert "metrics" not in pipelines
        redaction = cfg["processors"]["redaction"]
        key_pattern = re.compile(redaction["blocked_key_patterns"][0])
        for key in ("password", "db.password", "api_key", "Authorization", "http.request.header.cookie", "passhash"):
            assert key_pattern.fullmatch(key), key
        for key in ("http.route", "hsdeep.tenant_id", "trace_id", "service.name"):
            assert not key_pattern.fullmatch(key), key
        value_patterns = [re.compile(p) for p in redaction["blocked_values"]]
        for secret in ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "Bearer abcdefghijkl", "AKIAABCDEFGHIJKLMNOP"):
            assert any(p.search(secret) for p in value_patterns), secret
        assert "http://tempo:4318" in raw and "http://loki:3100/otlp" in raw
        assert cfg["extensions"]["health_check"]["endpoint"].endswith(":13133")

    def test_container_logs_are_off_unless_a_directory_is_given(self):
        collector = self.compose["services"]["otel-collector"]
        mount = next(v for v in collector["volumes"] if v.endswith(":/var/lib/docker/containers:ro"))
        assert mount.startswith("${DOCKER_CONTAINERS_DIR:-./otel-collector/no-container-logs}")
        assert (OBS / "otel-collector" / "no-container-logs").is_dir()
        assert collector["user"] == "${OTEL_COLLECTOR_USER:-10001}"  # not root by default

    def test_prometheus_scrapes_every_source_the_catalogue_names(self):
        cfg = _yaml(OBS / "prometheus" / "prometheus.yml")
        jobs = {j["job_name"]: j for j in cfg["scrape_configs"]}
        for source in SOURCES.values():
            assert source["job"] in jobs, source["job"]
        # The Phase 21 jobs are still there, unchanged.
        assert jobs["hsdeep-backend"]["authorization"]["credentials_file"] == "/run/secrets/metrics_token"
        assert "prometheus" in jobs
        # Platform series are marked as such; a GPU target exists only if a file is added.
        for name in ("node", "cadvisor", "postgres", "redis"):
            assert jobs[name]["static_configs"][0]["labels"] == {"scope": "platform"}, name
        assert jobs["nvidia-gpu"]["file_sd_configs"][0]["files"] == ["/etc/prometheus/targets/optional/gpu-*.yml"]
        assert not list((OBS / "prometheus" / "targets" / "optional").glob("gpu-*.yml"))
        # cAdvisor keeps only the families the catalogue reads.
        keep = re.compile(jobs["cadvisor"]["metric_relabel_configs"][0]["regex"])
        for metric in EXPORTER_METRICS["cAdvisor"]:
            assert keep.fullmatch(metric), metric

    def test_grafana_gets_read_only_loki_and_tempo_datasources(self):
        sources = {d["uid"]: d for d in _yaml(OBS / "grafana" / "provisioning" / "datasources" / "loki-tempo.yml")["datasources"]}
        assert sources["hsdeep-loki"]["url"] == "http://loki:3100" and sources["hsdeep-tempo"]["url"] == "http://tempo:3200"
        assert all(d["editable"] is False and d["access"] == "proxy" for d in sources.values())
        assert sources["hsdeep-loki"]["jsonData"]["derivedFields"][0]["datasourceUid"] == "hsdeep-tempo"
        assert sources["hsdeep-tempo"]["jsonData"]["tracesToLogsV2"]["datasourceUid"] == "hsdeep-loki"

    def test_backend_settings_are_documented_in_the_env_templates(self):
        root = OBS.parents[1]
        for template in (root / "backend" / ".env.example", root / "deployment" / ".env.production.example"):
            text = template.read_text(encoding="utf-8")
            for name in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_SERVICE_NAME", "OTEL_TRACES_SAMPLER_RATIO", "OTEL_LOGS_EXPORT",
                         "LOKI_URL", "TEMPO_URL", "OTEL_COLLECTOR_HEALTH_URL", "OBSERVABILITY_QUERY_TIMEOUT_SECONDS",
                         "OBSERVABILITY_MAX_WINDOW_HOURS", "OBSERVABILITY_QUERIES_PER_MINUTE"):
                assert re.search(rf"(?m)^#? ?{name}=", text), (template.name, name)

    def test_settings_defaults_export_nothing(self):
        from app.core.config import Settings

        fields = Settings.model_fields
        assert fields["otel_exporter_otlp_endpoint"].default == "" and fields["loki_url"].default == ""
        assert fields["tempo_url"].default == "" and fields["otel_collector_health_url"].default == ""

    def test_requirements_pin_the_opentelemetry_packages(self):
        text = (OBS.parents[1] / "backend" / "requirements.txt").read_text(encoding="utf-8")
        for package in ("opentelemetry-api", "opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-http",
                        "opentelemetry-instrumentation-sqlalchemy", "opentelemetry-instrumentation-httpx"):
            assert re.search(rf"(?m)^{package}==\d", text), package
