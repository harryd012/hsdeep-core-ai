"""
Platform trace search over Grafana Tempo, for the superuser-only Trace
Explorer (Phase 22).

These are traces of the HSDEEP platform itself (API requests, scheduler
ticks, collection runs, RCA), exported by the backend through the
OpenTelemetry Collector. Traces that customers send from their own
applications are a different thing: they stay in PostgreSQL behind the
tenant-scoped ``/api/traces``.

Safety rules (as for logs, app/services/platform_logs.py): the browser never
sends TraceQL; a query is built from validated filters; window, result count
and time are bounded; Tempo's URL never reaches the browser; errors are
categories; span attributes are redacted again on the way out.

Tempo returns a trace as OTLP JSON with base64 ids. It is converted to hex
and decoded by the SAME decoder the OTLP ingest uses
(``app.services.tracing.otlp``), so a span has one shape everywhere and the
existing waterfall can draw it.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.redaction import redact, redact_value
from app.services.platform_health import _valid
from app.services.platform_logs import FilterInvalid, _match, _uuid
from app.services.tracing import otlp

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_SPANS = 5000
_NAME = re.compile(r"^[A-Za-z0-9._/\-]{1,128}$")
_OPERATION = re.compile(r"^[A-Za-z0-9._/\-{}: ]{1,200}$")
_ENV = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")

#: Span attributes that tie a trace to logs, alerts and incidents.
CORRELATION_ATTRIBUTES = ("hsdeep.tenant_id", "hsdeep.request_id", "hsdeep.incident_id", "hsdeep.collection_run_id",
                          "hsdeep.provider_id", "hsdeep.alert_id")


@dataclass(frozen=True)
class TraceFilters:
    since: datetime
    until: datetime
    service: str | None = None
    operation: str | None = None
    environment: str | None = None
    tenant_id: str | None = None
    incident_id: str | None = None
    collection_run_id: str | None = None
    min_duration_ms: int | None = None
    errors_only: bool = False


def make_filters(*, since: datetime, until: datetime, service: str | None = None, operation: str | None = None,
                 environment: str | None = None, tenant_id: str | None = None, incident_id: str | None = None,
                 collection_run_id: str | None = None, min_duration_ms: int | None = None,
                 errors_only: bool = False) -> TraceFilters:
    if min_duration_ms is not None and not (0 <= int(min_duration_ms) <= 3_600_000):
        raise FilterInvalid("min_duration_ms must be between 0 and 3600000")
    return TraceFilters(
        since=since, until=until, service=_match("service", service, _NAME),
        operation=_match("operation", operation, _OPERATION), environment=_match("environment", environment, _ENV),
        tenant_id=_uuid("tenant_id", tenant_id), incident_id=_uuid("incident_id", incident_id),
        collection_run_id=_uuid("collection_run_id", collection_run_id),
        min_duration_ms=int(min_duration_ms) if min_duration_ms else None, errors_only=bool(errors_only),
    )


def valid_trace_id(trace_id: str) -> str:
    value = (trace_id or "").strip().lower()
    if not _TRACE_ID.match(value) or value == "0" * 32:
        raise FilterInvalid("trace id must be 32 hex characters")
    return value


def _lit(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_traceql(f: TraceFilters) -> str:
    """The TraceQL for a set of validated filters. Pure; unit-tested."""
    conditions = ['resource.service.namespace = "hsdeep"']
    if f.service:
        conditions.append(f"resource.service.name = {_lit(f.service)}")
    if f.environment:
        conditions.append(f"resource.deployment.environment = {_lit(f.environment)}")
    if f.operation:
        conditions.append(f"name = {_lit(f.operation)}")
    if f.tenant_id:
        conditions.append(f'span."hsdeep.tenant_id" = {_lit(f.tenant_id)}')
    if f.incident_id:
        conditions.append(f'span."hsdeep.incident_id" = {_lit(f.incident_id)}')
    if f.collection_run_id:
        conditions.append(f'span."hsdeep.collection_run_id" = {_lit(f.collection_run_id)}')
    if f.errors_only:
        conditions.append("status = error")
    if f.min_duration_ms:
        conditions.append(f"duration >= {f.min_duration_ms}ms")
    return "{ " + " && ".join(conditions) + " }"


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None) -> tuple[Any, str | None]:
    try:
        resp = await client.get(url, params=params)
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPError:
        return None, "unreachable"
    if resp.status_code == 404:
        return None, "not_found"
    if resp.status_code in (400, 422):
        return None, "rejected"
    if resp.status_code >= 400:
        return None, "unreachable"
    try:
        return resp.json(), None
    except ValueError:
        return None, "bad_response"


def _summary(row: dict[str, Any]) -> dict[str, Any] | None:
    trace_id = str(row.get("traceID") or "").lower().rjust(32, "0")
    if not _TRACE_ID.match(trace_id):
        return None
    try:
        start = datetime.fromtimestamp(int(row["startTimeUnixNano"]) / 1e9, tz=timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        start = None
    services: dict[str, dict[str, int]] = {}
    span_count = error_count = None
    stats = row.get("serviceStats")
    if isinstance(stats, dict):
        span_count = error_count = 0
        for name, s in stats.items():
            spans, errors = int((s or {}).get("spanCount") or 0), int((s or {}).get("errorCount") or 0)
            services[str(name)[:255]] = {"spans": spans, "errors": errors}
            span_count += spans
            error_count += errors
    return {
        "trace_id": trace_id, "start": start,
        "duration_ms": float(row["durationMs"]) if row.get("durationMs") is not None else None,
        "root_service": row.get("rootServiceName") or None, "root_name": row.get("rootTraceName") or None,
        # None = Tempo did not report it for this trace; never shown as 0.
        "spans": span_count, "errors": error_count, "services": sorted(services),
    }


async def search(tempo_url: str, f: TraceFilters, *, limit: int = DEFAULT_LIMIT, timeout: float = 8.0,
                 client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    out = {"checked_at": datetime.now(timezone.utc).isoformat(), "since": f.since.isoformat(), "until": f.until.isoformat(),
           "limit": limit, "items": []}
    if not tempo_url:
        return {**out, "configured": False, "reachable": False, "error": None}
    base = _valid(tempo_url)
    if base is None:
        return {**out, "configured": True, "reachable": False, "error": "invalid_url"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        payload, error = await _get(client, f"{base}/api/search", {
            "q": build_traceql(f), "start": int(f.since.timestamp()), "end": int(f.until.timestamp()) + 1,
            "limit": limit, "spss": 3,
        })
    finally:
        if own:
            await client.aclose()
    if error:
        return {**out, "configured": True, "reachable": False, "error": error}
    try:
        items = [s for s in (_summary(row) for row in payload.get("traces") or []) if s]
    except (AttributeError, TypeError, ValueError):
        return {**out, "configured": True, "reachable": False, "error": "bad_response"}
    items.sort(key=lambda t: t["start"] or "", reverse=True)
    return {**out, "configured": True, "reachable": True, "error": None, "items": items[:limit]}


def _hex_id(value: Any, length: int) -> str:
    """Tempo's JSON carries ids as base64 (protobuf JSON); OTLP JSON wants hex."""
    text = str(value or "")
    if len(text) == length and _HEX.match(text):
        return text.lower()
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return text.lower()
    return raw.hex()


def _to_otlp_json(payload: Any) -> dict[str, Any]:
    trace = payload.get("trace") if isinstance(payload, dict) and isinstance(payload.get("trace"), dict) else payload
    if not isinstance(trace, dict):
        return {"resourceSpans": []}
    resource_spans = trace.get("resourceSpans") or trace.get("batches") or []
    out = []
    for rs in resource_spans:
        if not isinstance(rs, dict):
            continue
        scopes = []
        for ss in rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []:
            spans = []
            for span in (ss or {}).get("spans") or []:
                if not isinstance(span, dict):
                    continue
                fixed = dict(span)
                fixed["traceId"] = _hex_id(span.get("traceId"), 32)
                fixed["spanId"] = _hex_id(span.get("spanId"), 16)
                if span.get("parentSpanId"):
                    fixed["parentSpanId"] = _hex_id(span.get("parentSpanId"), 16)
                spans.append(fixed)
            scopes.append({"spans": spans})
        out.append({"resource": rs.get("resource") or {}, "scopeSpans": scopes})
    return {"resourceSpans": out}


def _clean_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: redact_value(k, v) for k, v in attrs.items()}


def _span_out(span: dict[str, Any]) -> dict[str, Any]:
    return {
        "span_id": span["span_id"], "parent_span_id": span["parent_span_id"], "service_name": span["service_name"],
        "name": span["name"], "kind": span["kind"],
        "start_time": span["start_time"].isoformat(), "end_time": span["end_time"].isoformat(),
        "duration_ms": span["duration_ms"], "status_code": span["status_code"],
        "status_message": redact(span["status_message"]) if span["status_message"] else None,
        "attributes": _clean_attributes(span["attributes"]),
        "resource_attributes": _clean_attributes(span["resource_attributes"]),
        "events": [{"name": e["name"], "time": e["time"], "attributes": _clean_attributes(e["attributes"])}
                   for e in span["events"]],
    }


def correlation_ids(spans: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Distinct values of each correlation attribute found on the spans."""
    found: dict[str, list[str]] = {}
    for span in spans:
        for key in CORRELATION_ATTRIBUTES:
            value = span["attributes"].get(key)
            if value in (None, ""):
                continue
            short = key.split(".", 1)[1]
            bucket = found.setdefault(short, [])
            if str(value) not in bucket and len(bucket) < 20:
                bucket.append(str(value))
    return found


async def get_trace(tempo_url: str, trace_id: str, *, timeout: float = 8.0,
                    client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Every span of one trace, in the shape the existing waterfall draws."""
    trace_id = valid_trace_id(trace_id)
    out: dict[str, Any] = {"checked_at": datetime.now(timezone.utc).isoformat(), "trace_id": trace_id, "found": False,
                           "spans": [], "orphans": 0, "truncated": False, "rejected_spans": 0,
                           "start": None, "end": None, "duration_ms": None, "services": [], "errors": 0,
                           "correlation": {}}
    if not tempo_url:
        return {**out, "configured": False, "reachable": False, "error": None}
    base = _valid(tempo_url)
    if base is None:
        return {**out, "configured": True, "reachable": False, "error": "invalid_url"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        payload, error = await _get(client, f"{base}/api/v2/traces/{trace_id}")
    finally:
        if own:
            await client.aclose()
    if error == "not_found":
        return {**out, "configured": True, "reachable": True, "error": None}
    if error:
        return {**out, "configured": True, "reachable": False, "error": error}

    decoded = otlp.decode(_to_otlp_json(payload))
    spans = sorted((s for s in decoded.spans if s["trace_id"] == trace_id), key=lambda s: s["start_time"])
    truncated = len(spans) > MAX_SPANS
    spans = spans[:MAX_SPANS]
    if not spans:
        return {**out, "configured": True, "reachable": True, "error": None, "rejected_spans": decoded.rejected}
    ids = {s["span_id"] for s in spans}
    start = min(s["start_time"] for s in spans)
    end = max(s["end_time"] for s in spans)
    return {
        **out, "configured": True, "reachable": True, "error": None, "found": True,
        "spans": [_span_out(s) for s in spans],
        "orphans": sum(1 for s in spans if s["parent_span_id"] and s["parent_span_id"] not in ids),
        "truncated": truncated, "rejected_spans": decoded.rejected,
        "start": start.isoformat(), "end": end.isoformat(),
        "duration_ms": round((end - start).total_seconds() * 1000, 3),
        "services": sorted({s["service_name"] for s in spans}),
        "errors": sum(1 for s in spans if s["status_code"] == "error"),
        "correlation": correlation_ids(spans),
    }
