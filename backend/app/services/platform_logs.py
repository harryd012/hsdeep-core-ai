"""
Platform log search over Grafana Loki, for the superuser-only Logs Explorer
(Phase 22).

These are the HSDEEP platform's own logs (API, authentication, security
audit, integrations, collectors, scheduler, database, containers), shipped by
the OpenTelemetry Collector. Customer device logs are a different thing: they
stay in PostgreSQL behind the tenant-scoped ``/api/logs``.

Safety rules, all enforced here on the server:

  * The browser never sends LogQL. A query is BUILT from validated filters:
    every value must match a strict pattern before it is placed in the query,
    and is then written as a quoted string literal.
  * The time window is bounded (``max_window_hours``), the page size is
    bounded (``MAX_LIMIT``) and every call has a timeout.
  * Loki's URL never reaches the browser; failures are categories
    (``timeout`` / ``unreachable`` / ``bad_response`` / ``rejected``).
  * Every returned line is redacted again (``app.core.redaction``), so a line
    written by a component that does not redact is still masked on the way out.
  * A tenant filter narrows to one tenant's lines. Lines with no tenant id
    (startup, scheduler housekeeping) are platform lines and are only returned
    when no tenant filter is set.

Field names are the ones Loki stores for OTLP logs (verified against Loki
3.5): index labels ``service_name``, ``service_namespace``,
``deployment_environment``; everything else is structured metadata
(``detected_level``, ``trace_id``, ``tenant_id`` ...).
"""
from __future__ import annotations

import json
import re
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.redaction import redact, redact_value
from app.services.platform_health import _valid

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
EXPORT_MAX_LINES = 5000
MAX_KEYWORD_CHARS = 200
MAX_LINE_CHARS = 16_000
NAMESPACE = "hsdeep"

#: Loki's normalised levels (``detected_level``), least to most severe.
SEVERITIES: tuple[str, ...] = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")
_SEVERITY_ALIASES = {"WARNING": "WARN", "CRITICAL": "FATAL", "ERR": "ERROR", "INFORMATION": "INFO"}
CATEGORIES: tuple[str, ...] = (
    "application", "api", "authentication", "security_audit", "integration", "collector", "scheduler",
    "database", "container",
)

_NAME = re.compile(r"^[A-Za-z0-9._/\-]{1,128}$")
_ENV = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
_CURSOR = re.compile(r"^[0-9]{16,20}$")

#: Structured-metadata keys shown as their own fields.
_ID_FIELDS = ("tenant_id", "trace_id", "span_id", "request_id", "incident_id", "collection_run_id", "provider_id")
#: Other metadata worth showing in the detail view. Anything else is dropped.
_DETAIL_FIELDS = ("method", "route", "status", "duration_ms", "user_id", "correlation_id", "logger_name",
                  "code_file_path", "code_function_name", "code_line_number", "service_instance_id",
                  "hsdeep_runtime_mode", "container_id", "log_file_path")


class FilterInvalid(ValueError):
    """A filter value that may not be placed in a query."""


@dataclass(frozen=True)
class LogFilters:
    since: datetime
    until: datetime
    severities: tuple[str, ...] = ()
    service: str | None = None
    component: str | None = None
    category: str | None = None
    tenant_id: str | None = None
    environment: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    incident_id: str | None = None
    collection_run_id: str | None = None
    provider_id: str | None = None
    q: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def window(since: datetime | None, until: datetime | None, *, max_hours: int,
           now: datetime | None = None) -> tuple[datetime, datetime]:
    """A bounded [since, until) window; default the last hour."""
    def utc(moment: datetime) -> datetime:
        return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

    until = utc(until) if until is not None else (now or datetime.now(timezone.utc))
    since = utc(since) if since is not None else until - timedelta(hours=1)
    if since >= until:
        raise FilterInvalid("since must be before until")
    if until - since > timedelta(hours=max_hours):
        raise FilterInvalid(f"the window may not exceed {max_hours} hours")
    return since, until


def _uuid(name: str, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise FilterInvalid(f"{name} must be a UUID") from exc


def _match(name: str, value: str | None, pattern: re.Pattern[str]) -> str | None:
    if value is None or value == "":
        return None
    if not pattern.match(value):
        raise FilterInvalid(f"{name} has characters that are not allowed")
    return value


def make_filters(*, since: datetime, until: datetime, severities: list[str] | None = None, service: str | None = None,
                 component: str | None = None, category: str | None = None, tenant_id: str | None = None,
                 environment: str | None = None, trace_id: str | None = None, request_id: str | None = None,
                 incident_id: str | None = None, collection_run_id: str | None = None, provider_id: str | None = None,
                 q: str | None = None) -> LogFilters:
    """Validate every filter. Raises ``FilterInvalid`` rather than dropping or
    repairing a value, so a rejected filter is never silently ignored."""
    levels: list[str] = []
    for raw in severities or []:
        level = _SEVERITY_ALIASES.get(raw.strip().upper(), raw.strip().upper())
        if level not in SEVERITIES:
            raise FilterInvalid(f"unknown severity: {raw[:20]}")
        if level not in levels:
            levels.append(level)
    if category and category not in CATEGORIES:
        raise FilterInvalid("unknown category")
    trace = (trace_id or "").strip().lower() or None
    if trace and not _TRACE_ID.match(trace):
        raise FilterInvalid("trace_id must be 32 hex characters")
    keyword = (q or "").strip() or None
    if keyword and len(keyword) > MAX_KEYWORD_CHARS:
        raise FilterInvalid(f"the keyword may not exceed {MAX_KEYWORD_CHARS} characters")
    if keyword and any(ord(ch) < 32 for ch in keyword):
        raise FilterInvalid("the keyword may not contain control characters")
    return LogFilters(
        since=since, until=until, severities=tuple(levels),
        service=_match("service", service, _NAME), component=_match("component", component, _NAME),
        category=category or None, tenant_id=_uuid("tenant_id", tenant_id),
        environment=_match("environment", environment, _ENV), trace_id=trace,
        request_id=_match("request_id", request_id, _REQUEST_ID),
        incident_id=_uuid("incident_id", incident_id), collection_run_id=_uuid("collection_run_id", collection_run_id),
        provider_id=_uuid("provider_id", provider_id), q=keyword,
    )


def _lit(value: str) -> str:
    """A LogQL (Go) double-quoted string literal."""
    return json.dumps(value, ensure_ascii=False)


_RE2_SPECIAL = re.compile(r"([\\.+*?()|\[\]{}^$])")


def build_logql(f: LogFilters) -> str:
    """The LogQL for a set of validated filters. Pure; unit-tested."""
    selector = [f"service_namespace={_lit(NAMESPACE)}"]
    if f.service:
        selector.append(f"service_name={_lit(f.service)}")
    if f.environment:
        selector.append(f"deployment_environment={_lit(f.environment)}")
    stages: list[str] = []
    if f.q:
        # Case-insensitive literal match: the keyword is escaped, never a regex.
        stages.append("|~ " + _lit("(?i)" + _RE2_SPECIAL.sub(r"\\\1", f.q)))
    if f.severities:
        stages.append("| detected_level=~" + _lit("|".join(f.severities)))
    for key in ("component", "category", "tenant_id", "trace_id", "request_id", "incident_id",
                "collection_run_id", "provider_id"):
        value = getattr(f, key)
        if value:
            stages.append(f"| {key}={_lit(value)}")
    return "{" + ", ".join(selector) + "}" + (" " + " ".join(stages) if stages else "")


def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def _severity(meta: dict[str, str]) -> str:
    level = (meta.get("detected_level") or meta.get("severity_text") or "").upper()
    level = _SEVERITY_ALIASES.get(level, level)
    return level if level in SEVERITIES else "UNKNOWN"


def _line(meta: dict[str, str], ts_ns: str, text: str) -> dict[str, Any]:
    exception = None
    if meta.get("exception_type") or meta.get("exception_stacktrace"):
        exception = {
            "type": meta.get("exception_type") or None,
            "message": redact(meta.get("exception_message") or "") or None,
            "stacktrace": redact(meta.get("exception_stacktrace") or "")[:MAX_LINE_CHARS] or None,
        }
    seconds, nanos = divmod(int(ts_ns), 1_000_000_000)
    out: dict[str, Any] = {
        # Stable across requests: timestamp plus a checksum of the line.
        "id": f"{ts_ns}-{zlib.crc32((text + '|' + (meta.get('span_id') or '')).encode('utf-8', 'replace')):08x}",
        "ts_ns": ts_ns,
        "timestamp": datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=nanos // 1000).isoformat(),
        "severity": _severity(meta),
        "service": meta.get("service_name") or None,
        "component": meta.get("component") or None,
        "category": meta.get("category") or None,
        "environment": meta.get("deployment_environment") or meta.get("environment") or None,
        "message": redact(text)[:MAX_LINE_CHARS],
        "exception": exception,
        "attributes": {k: redact_value(k, meta[k]) for k in _DETAIL_FIELDS if meta.get(k)},
    }
    for key in _ID_FIELDS:
        out[key] = meta.get(key) or None
    return out


def _base_result(f: LogFilters, limit: int) -> dict[str, Any]:
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "since": f.since.isoformat(),
            "until": f.until.isoformat(), "limit": limit, "items": [], "next_cursor": None, "has_more": False}


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> tuple[Any, str | None]:
    try:
        resp = await client.get(url, params=params)
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPError:
        return None, "unreachable"
    if resp.status_code in (400, 422):
        return None, "rejected"
    if resp.status_code >= 400:
        return None, "unreachable"
    try:
        return resp.json(), None
    except ValueError:
        return None, "bad_response"


async def search(loki_url: str, f: LogFilters, *, limit: int = DEFAULT_LIMIT, cursor: str | None = None,
                 timeout: float = 8.0, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """One page of log lines, newest first. ``cursor`` is the ``next_cursor``
    of the previous page (the oldest timestamp already shown)."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    out = _base_result(f, limit)
    if not loki_url:
        return {**out, "configured": False, "reachable": False, "error": None}
    base = _valid(loki_url)
    if base is None:
        return {**out, "configured": True, "reachable": False, "error": "invalid_url"}
    end_ns = _ns(f.until)
    if cursor:
        if not _CURSOR.match(cursor):
            raise FilterInvalid("cursor is not valid")
        end_ns = min(end_ns, int(cursor))
    start_ns = _ns(f.since)
    if end_ns <= start_ns:
        return {**out, "configured": True, "reachable": True, "error": None}

    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        # One extra line tells us whether another page exists.
        payload, error = await _get(client, f"{base}/loki/api/v1/query_range", {
            "query": build_logql(f), "start": start_ns, "end": end_ns, "limit": limit + 1, "direction": "backward",
        })
    finally:
        if own:
            await client.aclose()
    if error:
        return {**out, "configured": True, "reachable": False, "error": error}
    try:
        streams = payload["data"]["result"]
        rows = [(stream.get("stream") or {}, ts, text) for stream in streams for ts, text in stream.get("values") or []]
    except (KeyError, TypeError, ValueError):
        return {**out, "configured": True, "reachable": False, "error": "bad_response"}
    rows.sort(key=lambda r: int(r[1]), reverse=True)
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_line(meta, ts, text) for meta, ts, text in rows]
    return {**out, "configured": True, "reachable": True, "error": None, "items": items, "has_more": has_more,
            # Exclusive upper bound for the next (older) page.
            "next_cursor": rows[-1][1] if (has_more and rows) else None}


async def volume(loki_url: str, f: LogFilters, *, buckets: int = 60, timeout: float = 8.0,
                 client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Lines per time bucket by severity, for the stacked volume chart."""
    span_seconds = max(1, int((f.until - f.since).total_seconds()))
    step = max(15, span_seconds // max(1, min(buckets, 120)))
    out = {"checked_at": datetime.now(timezone.utc).isoformat(), "since": f.since.isoformat(), "until": f.until.isoformat(),
           "step_seconds": step, "buckets": []}
    if not loki_url:
        return {**out, "configured": False, "reachable": False, "error": None}
    base = _valid(loki_url)
    if base is None:
        return {**out, "configured": True, "reachable": False, "error": "invalid_url"}
    query = f"sum by (detected_level) (count_over_time({build_logql(f)} [{step}s]))"
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        payload, error = await _get(client, f"{base}/loki/api/v1/query_range", {
            "query": query, "start": _ns(f.since), "end": _ns(f.until), "step": step,
        })
    finally:
        if own:
            await client.aclose()
    if error:
        return {**out, "configured": True, "reachable": False, "error": error}
    by_time: dict[int, dict[str, int]] = {}
    try:
        for series in payload["data"]["result"]:
            level = _severity({"detected_level": (series.get("metric") or {}).get("detected_level", "")})
            for ts, raw in series.get("values") or []:
                bucket = by_time.setdefault(int(float(ts)), {})
                bucket[level] = bucket.get(level, 0) + int(float(raw))
    except (KeyError, TypeError, ValueError):
        return {**out, "configured": True, "reachable": False, "error": "bad_response"}
    return {**out, "configured": True, "reachable": True, "error": None,
            "buckets": [{"t": t, "counts": counts} for t, counts in sorted(by_time.items())]}


async def facets(loki_url: str, f: LogFilters, *, timeout: float = 8.0,
                 client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Services and environments that logged in the window (Loki index labels)."""
    out = {"checked_at": datetime.now(timezone.utc).isoformat(), "services": [], "environments": [],
           "severities": list(SEVERITIES), "categories": list(CATEGORIES)}
    if not loki_url:
        return {**out, "configured": False, "reachable": False, "error": None}
    base = _valid(loki_url)
    if base is None:
        return {**out, "configured": True, "reachable": False, "error": "invalid_url"}
    params = {"start": _ns(f.since), "end": _ns(f.until), "query": "{service_namespace=" + _lit(NAMESPACE) + "}"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        services, e1 = await _get(client, f"{base}/loki/api/v1/label/service_name/values", params)
        envs, e2 = await _get(client, f"{base}/loki/api/v1/label/deployment_environment/values", params)
    finally:
        if own:
            await client.aclose()
    if e1 and e2:
        return {**out, "configured": True, "reachable": False, "error": e1}

    def values(payload: Any) -> list[str]:
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        return sorted(v for v in (data or []) if isinstance(v, str) and _NAME.match(v))[:200]

    return {**out, "configured": True, "reachable": True, "error": None,
            "services": values(services), "environments": values(envs)}


async def export_lines(loki_url: str, f: LogFilters, *, max_lines: int = EXPORT_MAX_LINES, timeout: float = 8.0,
                       client: httpx.AsyncClient | None = None) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Up to ``max_lines`` lines for an export, paged server-side.
    Returns (lines, error category, truncated)."""
    lines: list[dict[str, Any]] = []
    cursor: str | None = None
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        while len(lines) < max_lines:
            page = await search(loki_url, f, limit=min(MAX_LIMIT, max_lines - len(lines)), cursor=cursor,
                                timeout=timeout, client=client)
            if not page.get("configured"):
                return lines, "not_configured", False
            if page["error"]:
                return lines, page["error"], False
            lines.extend(page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                return lines, None, False
    finally:
        if own:
            await client.aclose()
    return lines, None, True


EXPORT_COLUMNS = ("timestamp", "severity", "service", "component", "category", "environment", "tenant_id", "trace_id",
                  "span_id", "request_id", "incident_id", "collection_run_id", "provider_id", "message")
