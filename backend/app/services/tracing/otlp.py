"""
OTLP/JSON trace decoding (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P8 slice 1).

Decodes an `ExportTraceServiceRequest` in the OTLP/HTTP JSON encoding
(exporters: OTEL_EXPORTER_OTLP_PROTOCOL=http/json) into span rows. Pure: no
I/O. Follows the OTLP JSON mapping: trace and span ids are hex strings, enum
values may be integers or their names, 64-bit integers may be strings.

A span is rejected (and counted, with the first reason) rather than repaired
when its ids are missing, malformed or all zero, or it ends before it starts.
A resource without `service.name` gets OpenTelemetry's own default,
`unknown_service`. Attributes and events are bounded so one service cannot
grow a row without limit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAX_SPANS = 10_000
MAX_ATTRIBUTES = 128
MAX_VALUE_CHARS = 1024
MAX_EVENTS = 32
UNKNOWN_SERVICE = "unknown_service"

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID = re.compile(r"^[0-9a-f]{16}$")
_KINDS = {0: "unspecified", 1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}
_STATUS = {0: "unset", 1: "ok", 2: "error"}


@dataclass
class Decoded:
    spans: list[dict] = field(default_factory=list)
    rejected: int = 0
    first_error: str | None = None

    def reject(self, reason: str) -> None:
        self.rejected += 1
        self.first_error = self.first_error or reason


def _enum(value, names: dict[int, str], prefix: str) -> str | None:
    if isinstance(value, int) and value in names:
        return names[value]
    if isinstance(value, str):
        if value.isdigit() and int(value) in names:
            return names[int(value)]
        name = value.upper().removeprefix(prefix).lower()
        if name in names.values():
            return name
    return None


def _any_value(v: dict):
    """OTLP AnyValue -> a JSON value (strings bounded, nesting flattened to strings past one level)."""
    if not isinstance(v, dict):
        return None
    if "stringValue" in v:
        return str(v["stringValue"])[:MAX_VALUE_CHARS]
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return None
    if "doubleValue" in v:
        try:
            return float(v["doubleValue"])
        except (TypeError, ValueError):
            return None
    if "arrayValue" in v:
        values = (v["arrayValue"] or {}).get("values") or []
        return [str(_any_value(x))[:MAX_VALUE_CHARS] for x in values[:32]]
    if "kvlistValue" in v:
        return {str(kv.get("key"))[:128]: str(_any_value(kv.get("value")))[:MAX_VALUE_CHARS]
                for kv in ((v["kvlistValue"] or {}).get("values") or [])[:32]}
    if "bytesValue" in v:
        return "(bytes)"
    return None


def attributes(kvs) -> dict:
    out: dict = {}
    for kv in (kvs or [])[:MAX_ATTRIBUTES]:
        if isinstance(kv, dict) and kv.get("key"):
            out[str(kv["key"])[:128]] = _any_value(kv.get("value") or {})
    return out


def _nanos(value) -> datetime | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n / 1e9, tz=timezone.utc)


def decode(payload: dict) -> Decoded:
    out = Decoded()
    if not isinstance(payload, dict):
        out.reject("body is not an ExportTraceServiceRequest")
        return out
    for rs in payload.get("resourceSpans") or []:
        resource = attributes(((rs or {}).get("resource") or {}).get("attributes"))
        service = str(resource.get("service.name") or UNKNOWN_SERVICE)[:255]
        for ss in (rs or {}).get("scopeSpans") or []:
            for span in (ss or {}).get("spans") or []:
                if len(out.spans) + out.rejected >= MAX_SPANS:
                    out.reject(f"more than {MAX_SPANS} spans in one request")
                    continue
                trace_id = str(span.get("traceId") or "").lower()
                span_id = str(span.get("spanId") or "").lower()
                parent = str(span.get("parentSpanId") or "").lower() or None
                if not _TRACE_ID.match(trace_id) or trace_id == "0" * 32:
                    out.reject("invalid traceId (32 hex characters, not all zero)")
                    continue
                if not _SPAN_ID.match(span_id) or span_id == "0" * 16:
                    out.reject("invalid spanId (16 hex characters, not all zero)")
                    continue
                if parent is not None and (not _SPAN_ID.match(parent) or parent == "0" * 16):
                    out.reject("invalid parentSpanId")
                    continue
                start, end = _nanos(span.get("startTimeUnixNano")), _nanos(span.get("endTimeUnixNano"))
                if start is None or end is None or end < start:
                    out.reject("missing times, or the span ends before it starts")
                    continue
                status = span.get("status") or {}
                events = [
                    {"name": str(e.get("name") or "")[:255], "time": (_nanos(e.get("timeUnixNano")) or start).isoformat(),
                     "attributes": attributes(e.get("attributes"))}
                    for e in (span.get("events") or [])[:MAX_EVENTS] if isinstance(e, dict)
                ]
                out.spans.append({
                    "trace_id": trace_id, "span_id": span_id, "parent_span_id": parent, "service_name": service,
                    "name": str(span.get("name") or "(unnamed)")[:500],
                    "kind": _enum(span.get("kind", 0), _KINDS, "SPAN_KIND_") or "unspecified",
                    "start_time": start, "end_time": end,
                    "duration_ms": round((end - start).total_seconds() * 1000, 3),
                    "status_code": _enum(status.get("code", 0), _STATUS, "STATUS_CODE_") or "unset",
                    "status_message": (str(status["message"])[:2000] if status.get("message") else None),
                    "attributes": attributes(span.get("attributes")),
                    "resource_attributes": resource,
                    "events": events,
                })
    return out


__all__ = ["Decoded", "MAX_SPANS", "UNKNOWN_SERVICE", "attributes", "decode"]
