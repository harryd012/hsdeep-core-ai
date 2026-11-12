"""
Trace queries (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P8 slice 1).

Tenant-scoped reads over `trace_spans`, every one bounded to a window of at
most MAX_WINDOW:

  * services     - per service: spans, error spans, traces, and latency
                   (p50 / p95 / max) of its entry spans (server / consumer
                   spans, or spans without a parent);
  * traces       - one row per trace that has spans in the window: start, total
                   duration, spans, errors, services, and its root span (the
                   span without a parent; the earliest span when the root has
                   not arrived);
  * trace        - every span of one trace, for the waterfall;
  * service map  - caller -> callee edges from parent / child spans whose
                   services differ, with calls, errors and callee latency;

and per service (APM):

  * timeseries   - requests, errors and p50 / p95 / p99 latency of its entry
                   spans per time bucket (at most MAX_POINTS buckets; empty
                   buckets are measured zeros, latency None);
  * operations   - per span name: calls, errors, p50 / p95 and share of time;
  * dependencies - the service-map edges into and out of the service;

and errors: failed spans grouped by service, span name and exception
(OpenTelemetry's `exception` span event, else the span status message), with
the most recent trace as an example.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, String, and_, cast, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.trace_span import TraceSpan

MAX_WINDOW = timedelta(days=7)
DEFAULT_WINDOW = timedelta(hours=1)
MAX_TRACE_SPANS = 5000
MAX_POINTS = 120
#: Bucket sizes (seconds) for the time series: the smallest that keeps the
#: window within MAX_POINTS buckets, so 1 h -> 1 min, 24 h -> 15 min, 7 d -> 3 h.
BUCKET_STEPS = (60, 300, 900, 1800, 3600, 10800, 21600)
MAX_OPERATIONS = 100
MAX_ERROR_GROUPS = 100


class WindowInvalid(ValueError):
    pass


class TraceNotFound(Exception):
    pass


def window(since: datetime | None, until: datetime | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    until = until or now
    since = since or until - DEFAULT_WINDOW
    if since.tzinfo is None or until.tzinfo is None:
        raise WindowInvalid("since and until need a time zone")
    if since >= until:
        raise WindowInvalid("since must be before until")
    if until - since > MAX_WINDOW:
        raise WindowInvalid("the window can be at most 7 days")
    return since, until


def _in_window(tenant_id: uuid.UUID, since: datetime, until: datetime, span=TraceSpan):
    return and_(span.tenant_id == tenant_id, span.start_time >= since, span.start_time < until)


def _entry(span=TraceSpan):
    return or_(span.kind.in_(("server", "consumer")), span.parent_span_id.is_(None))


def _pct(p: float, col):
    return func.percentile_cont(p).within_group(col)


def bucket_seconds(since: datetime, until: datetime) -> int:
    span = (until - since).total_seconds()
    for step in BUCKET_STEPS:
        if span / step <= MAX_POINTS:
            return step
    return BUCKET_STEPS[-1]


def _ms(value) -> float | None:
    return round(float(value), 3) if value is not None else None


#: The first `exception` event of a span (OpenTelemetry semantic conventions).
#: The JSON path is a module constant, never user input.
_EXCEPTION = func.jsonb_path_query_first(
    TraceSpan.events, literal_column("""'$[*] ? (@.name == "exception")'::jsonpath""")
)


class TraceQuery:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def services(self, tenant_id: uuid.UUID, since: datetime, until: datetime) -> list[dict]:
        is_error = TraceSpan.status_code == "error"
        rows = (
            await self.db.execute(
                select(
                    TraceSpan.service_name,
                    func.count(),
                    func.count().filter(is_error),
                    func.count(func.distinct(TraceSpan.trace_id)),
                    func.count().filter(_entry()),
                    _pct(0.5, TraceSpan.duration_ms).filter(_entry()),
                    _pct(0.95, TraceSpan.duration_ms).filter(_entry()),
                    func.max(TraceSpan.duration_ms).filter(_entry()),
                    func.max(TraceSpan.start_time),
                )
                .where(_in_window(tenant_id, since, until))
                .group_by(TraceSpan.service_name)
                .order_by(func.count().filter(is_error).desc(), TraceSpan.service_name)
            )
        ).all()
        return [
            {
                "service": s, "spans": n, "error_spans": e, "traces": t, "entry_spans": entries,
                "error_rate_pct": round(e / n * 100, 2) if n else 0.0,
                "p50_ms": float(p50) if p50 is not None else None, "p95_ms": float(p95) if p95 is not None else None,
                "max_ms": float(mx) if mx is not None else None, "last_seen": last,
            }
            for s, n, e, t, entries, p50, p95, mx, last in rows
        ]

    async def traces(
        self, tenant_id: uuid.UUID, since: datetime, until: datetime, *, service: str | None, errors_only: bool,
        min_duration_ms: float | None, q: str | None, limit: int, offset: int,
    ) -> tuple[list[dict], int]:
        is_error = TraceSpan.status_code == "error"
        start = func.min(TraceSpan.start_time)
        duration = cast(func.extract("epoch", func.max(TraceSpan.end_time) - func.min(TraceSpan.start_time)) * 1000, Float)
        agg = (
            select(
                TraceSpan.trace_id.label("trace_id"),
                start.label("start"),
                duration.label("duration_ms"),
                func.count().label("spans"),
                func.count().filter(is_error).label("errors"),
                func.array_agg(func.distinct(TraceSpan.service_name)).label("services"),
            )
            .where(_in_window(tenant_id, since, until))
            .group_by(TraceSpan.trace_id)
        )
        if service:
            agg = agg.having(func.bool_or(TraceSpan.service_name == service))
        if errors_only:
            agg = agg.having(func.count().filter(is_error) > 0)
        if min_duration_ms is not None:
            agg = agg.having(duration >= min_duration_ms)
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            agg = agg.having(func.bool_or(TraceSpan.name.ilike(like, escape="\\")))
        sub = agg.subquery()
        total = (await self.db.execute(select(func.count()).select_from(sub))).scalar_one()
        page = (
            await self.db.execute(select(sub).order_by(sub.c.start.desc(), sub.c.trace_id).limit(limit).offset(offset))
        ).all()
        if not page:
            return [], total
        ids = [r.trace_id for r in page]
        # The root span (no parent), else the earliest span of the trace.
        roots = (
            await self.db.execute(
                select(TraceSpan.trace_id, TraceSpan.service_name, TraceSpan.name, TraceSpan.parent_span_id)
                .where(TraceSpan.tenant_id == tenant_id, TraceSpan.trace_id.in_(ids))
                .distinct(TraceSpan.trace_id)
                .order_by(TraceSpan.trace_id, TraceSpan.parent_span_id.is_(None).desc(), TraceSpan.start_time)
            )
        ).all()
        root_of = {r.trace_id: r for r in roots}
        return [
            {
                "trace_id": r.trace_id, "start": r.start, "duration_ms": round(float(r.duration_ms), 3), "spans": r.spans,
                "errors": r.errors, "services": sorted(r.services or []),
                "root_service": root_of[r.trace_id].service_name if r.trace_id in root_of else None,
                "root_name": root_of[r.trace_id].name if r.trace_id in root_of else None,
                "root_complete": r.trace_id in root_of and root_of[r.trace_id].parent_span_id is None,
            }
            for r in page
        ], total

    async def trace(self, tenant_id: uuid.UUID, trace_id: str) -> list[TraceSpan]:
        spans = (
            await self.db.execute(
                select(TraceSpan)
                .where(TraceSpan.tenant_id == tenant_id, TraceSpan.trace_id == trace_id.lower())
                .order_by(TraceSpan.start_time, TraceSpan.span_id)
                .limit(MAX_TRACE_SPANS)
            )
        ).scalars().all()
        if not spans:
            raise TraceNotFound(trace_id)
        return list(spans)

    async def _edges(self, tenant_id: uuid.UUID, since: datetime, until: datetime, service: str | None = None) -> list[dict]:
        parent, child = aliased(TraceSpan), aliased(TraceSpan)
        stmt = (
            select(
                parent.service_name, child.service_name, func.count(),
                func.count().filter(child.status_code == "error"),
                func.avg(child.duration_ms), _pct(0.95, child.duration_ms),
            )
            .join(child, and_(
                child.tenant_id == parent.tenant_id, child.trace_id == parent.trace_id,
                child.parent_span_id == parent.span_id,
            ))
            .where(_in_window(tenant_id, since, until, parent), parent.service_name != child.service_name)
            .group_by(parent.service_name, child.service_name)
            .order_by(func.count().desc())
        )
        if service is not None:
            stmt = stmt.where(or_(parent.service_name == service, child.service_name == service))
        rows = (await self.db.execute(stmt)).all()
        return [
            {"source": s, "target": t, "calls": n, "errors": e, "avg_ms": round(float(avg), 3), "p95_ms": _ms(p95)}
            for s, t, n, e, avg, p95 in rows
        ]

    async def service_map(self, tenant_id: uuid.UUID, since: datetime, until: datetime) -> dict:
        return {"edges": await self._edges(tenant_id, since, until), "services": await self.services(tenant_id, since, until)}

    async def timeseries(self, tenant_id: uuid.UUID, service: str, since: datetime, until: datetime) -> dict:
        step = bucket_seconds(since, until)
        bucket = (func.floor(func.extract("epoch", TraceSpan.start_time) / step) * step).label("bucket")
        is_error = TraceSpan.status_code == "error"
        rows = (
            await self.db.execute(
                select(
                    bucket, func.count(), func.count().filter(is_error),
                    _pct(0.5, TraceSpan.duration_ms), _pct(0.95, TraceSpan.duration_ms), _pct(0.99, TraceSpan.duration_ms),
                )
                .where(_in_window(tenant_id, since, until), TraceSpan.service_name == service, _entry())
                .group_by(bucket)
            )
        ).all()
        by_bucket = {int(r[0]): r for r in rows}
        points = []
        t = int(since.timestamp()) // step * step
        while t < until.timestamp():
            r = by_bucket.get(t)
            n, e = (r[1], r[2]) if r is not None else (0, 0)
            points.append({
                "t": datetime.fromtimestamp(t, tz=timezone.utc), "requests": n, "errors": e,
                "error_rate_pct": round(e / n * 100, 2) if n else 0.0,
                "p50_ms": _ms(r[3]) if r is not None else None,
                "p95_ms": _ms(r[4]) if r is not None else None,
                "p99_ms": _ms(r[5]) if r is not None else None,
            })
            t += step
        return {"bucket_seconds": step, "points": points}

    async def operations(self, tenant_id: uuid.UUID, service: str, since: datetime, until: datetime) -> list[dict]:
        is_error = TraceSpan.status_code == "error"
        total = func.sum(TraceSpan.duration_ms)
        rows = (
            await self.db.execute(
                select(
                    TraceSpan.name, func.bool_or(_entry()), func.count(), func.count().filter(is_error),
                    _pct(0.5, TraceSpan.duration_ms), _pct(0.95, TraceSpan.duration_ms), total,
                )
                .where(_in_window(tenant_id, since, until), TraceSpan.service_name == service)
                .group_by(TraceSpan.name)
                .order_by(total.desc(), TraceSpan.name)
                .limit(MAX_OPERATIONS)
            )
        ).all()
        grand = sum(float(r[6]) for r in rows)
        return [
            {
                "name": name, "entry": bool(entry), "calls": n, "errors": e,
                "error_rate_pct": round(e / n * 100, 2) if n else 0.0,
                "p50_ms": _ms(p50), "p95_ms": _ms(p95), "total_ms": round(float(t), 3),
                "time_share_pct": round(float(t) / grand * 100, 2) if grand else 0.0,
            }
            for name, entry, n, e, p50, p95, t in rows
        ]

    async def dependencies(self, tenant_id: uuid.UUID, service: str, since: datetime, until: datetime) -> dict:
        edges = await self._edges(tenant_id, since, until, service)
        return {
            "callers": [e for e in edges if e["target"] == service],
            "callees": [e for e in edges if e["source"] == service],
        }

    async def errors(self, tenant_id: uuid.UUID, since: datetime, until: datetime, *, service: str | None = None) -> list[dict]:
        attrs = _EXCEPTION.op("->")("attributes")
        etype = attrs.op("->>")("exception.type").label("etype")
        emsg = func.left(
            func.coalesce(attrs.op("->>")("exception.message"), TraceSpan.status_message, ""), 300
        ).label("emsg")
        latest = func.array_agg(aggregate_order_by(TraceSpan.trace_id, TraceSpan.start_time.desc()), type_=ARRAY(String))
        stmt = (
            select(
                TraceSpan.service_name, TraceSpan.name, etype, emsg,
                func.count(), func.count(func.distinct(TraceSpan.trace_id)),
                func.min(TraceSpan.start_time), func.max(TraceSpan.start_time), latest[1],
            )
            .where(_in_window(tenant_id, since, until), TraceSpan.status_code == "error")
            .group_by(TraceSpan.service_name, TraceSpan.name, etype, emsg)
            .order_by(func.count().desc(), func.max(TraceSpan.start_time).desc())
            .limit(MAX_ERROR_GROUPS)
        )
        if service is not None:
            stmt = stmt.where(TraceSpan.service_name == service)
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "service": svc, "operation": name, "exception_type": et, "message": msg or None, "count": n,
                "traces": traces, "first_seen": first, "last_seen": last, "sample_trace_id": sample,
            }
            for svc, name, et, msg, n, traces, first, last, sample in rows
        ]


__all__ = ["MAX_POINTS", "MAX_TRACE_SPANS", "MAX_WINDOW", "bucket_seconds", "TraceNotFound", "TraceQuery", "WindowInvalid", "window"]
