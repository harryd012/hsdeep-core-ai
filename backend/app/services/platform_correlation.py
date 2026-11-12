"""
Metrics, logs and traces correlation for one trace (Phase 22).

Given a trace id, this collects what is LINKED BY IDENTIFIER to it:

  * the trace itself, from Tempo;
  * the log lines that carry the same ``trace_id``, from Loki;
  * the alerts, incidents and collection runs whose ids appear on the trace's
    spans or on those log lines, from PostgreSQL (the existing tables).

Each link reports how it was established (``state``):

  linked              found through a shared identifier
  none                the identifier exists, nothing matched it
  missing_identifier  neither the trace nor its logs carry the identifier, so
                      no link is claimed
  unavailable         the backend that would answer is not configured or did
                      not answer
  time_aligned        metrics only: Prometheus series carry no trace id (by
                      design, to keep labels bounded), so the charts are the
                      same time window, not an identifier match

Nothing is inferred from timing alone and presented as a link.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redaction import redact
from app.models.alert import Alert
from app.models.collector_run import CollectorRun
from app.models.incident import Incident
from app.services import platform_logs, platform_traces

LOG_LIMIT = 200
MAX_IDS = 20
#: Without a stored trace, logs are searched over this many hours back.
FALLBACK_LOOKBACK_HOURS = 24


def _uuids(values: list[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for value in values[:MAX_IDS]:
        try:
            out.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def _alerts(db: AsyncSession, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = (await db.execute(select(Alert).where(Alert.id.in_(ids)).limit(MAX_IDS))).scalars().all()
    return [{"id": str(a.id), "tenant_id": str(a.tenant_id), "title": a.title, "severity": a.severity,
             "status": a.status, "source": a.source, "triggered_at": _iso(a.triggered_at),
             "incident_id": str(a.incident_id) if a.incident_id else None} for a in rows]


async def _incidents(db: AsyncSession, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = (await db.execute(select(Incident).where(Incident.id.in_(ids)).limit(MAX_IDS))).scalars().all()
    return [{"id": str(i.id), "tenant_id": str(i.tenant_id), "title": i.title, "severity": i.severity,
             "status": i.status, "priority": i.priority, "opened_at": _iso(i.opened_at),
             "recommended_sop": i.recommended_sop,
             # The existing incident page holds the SOP recommendations and RCA.
             "route": f"/incidents/{i.id}"} for i in rows]


async def _runs(db: AsyncSession, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = (await db.execute(select(CollectorRun).where(CollectorRun.id.in_(ids)).limit(MAX_IDS))).scalars().all()
    return [{"id": str(r.id), "tenant_id": str(r.tenant_id), "status": r.status,
             "trigger": getattr(r, "trigger", None), "started_at": _iso(getattr(r, "started_at", None)),
             "finished_at": _iso(getattr(r, "finished_at", None)),
             "error": redact(r.error)[:500] if getattr(r, "error", None) else None} for r in rows]


def _link(state: str, items: list[Any], by: str | None, note: str) -> dict[str, Any]:
    return {"state": state, "by": by, "count": len(items), "items": items, "note": note}


async def correlate(trace_id: str, *, tempo_url: str, loki_url: str, db: AsyncSession, max_window_hours: int,
                    timeout: float = 8.0, client: httpx.AsyncClient | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    trace_id = platform_traces.valid_trace_id(trace_id)
    now = now or datetime.now(timezone.utc)
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        trace = await platform_traces.get_trace(tempo_url, trace_id, timeout=timeout, client=client)
        if trace["found"]:
            since = datetime.fromisoformat(trace["start"]) - timedelta(minutes=1)
            until = min(datetime.fromisoformat(trace["end"]) + timedelta(minutes=5), now + timedelta(minutes=1))
            if until <= since:
                until = since + timedelta(minutes=6)
            window_basis = "trace"
        else:
            until = now
            since = now - timedelta(hours=min(FALLBACK_LOOKBACK_HOURS, max_window_hours))
            window_basis = "lookback"
        filters = platform_logs.make_filters(since=since, until=until, trace_id=trace_id)
        logs = await platform_logs.search(loki_url, filters, limit=LOG_LIMIT, timeout=timeout, client=client)
    finally:
        if own:
            await client.aclose()

    # Identifiers present on the spans and on the trace's own log lines.
    ids: dict[str, list[str]] = {k: list(v) for k, v in (trace.get("correlation") or {}).items()}
    for line in logs.get("items") or []:
        for key in ("tenant_id", "request_id", "incident_id", "collection_run_id", "provider_id"):
            value = line.get(key)
            if value and value not in ids.setdefault(key, []) and len(ids[key]) < MAX_IDS:
                ids[key].append(value)

    # -- trace ---------------------------------------------------------------------
    if not trace.get("configured"):
        trace_link = _link("unavailable", [], "trace_id", "TEMPO_URL is not set on the backend.")
    elif trace.get("error"):
        trace_link = _link("unavailable", [], "trace_id", f"Tempo did not answer ({trace['error']}).")
    elif not trace["found"]:
        trace_link = _link("none", [], "trace_id",
                           "Tempo has no trace with this id. It may not have been sampled or exported, "
                           "or it is older than the trace retention.")
    else:
        trace_link = _link("linked", [{"trace_id": trace_id, "start": trace["start"], "duration_ms": trace["duration_ms"],
                                       "spans": len(trace["spans"]), "errors": trace["errors"],
                                       "services": trace["services"]}], "trace_id", "Found in Tempo by trace id.")

    # -- logs ----------------------------------------------------------------------
    if not logs.get("configured"):
        logs_link = _link("unavailable", [], "trace_id", "LOKI_URL is not set on the backend.")
    elif logs.get("error"):
        logs_link = _link("unavailable", [], "trace_id", f"Loki did not answer ({logs['error']}).")
    elif not logs["items"]:
        logs_link = _link("none", [], "trace_id",
                          "No log line in the searched window carries this trace id.")
    else:
        logs_link = _link("linked", logs["items"], "trace_id",
                          f"Log lines whose trace_id equals this trace ({'trace window' if window_basis == 'trace' else 'recent lookback'})."
                          + (" More exist than are shown." if logs.get("has_more") else ""))

    # -- alerts, incidents, collection runs (PostgreSQL) --------------------------
    alert_ids = _uuids(ids.get("alert_id", []))
    try:
        alerts = await _alerts(db, alert_ids)
        incident_ids = _uuids(ids.get("incident_id", []) + [a["incident_id"] for a in alerts if a["incident_id"]])
        incidents = await _incidents(db, incident_ids)
        run_ids = _uuids(ids.get("collection_run_id", []))
        runs = await _runs(db, run_ids)
        db_error = None
    except Exception:  # noqa: BLE001 - report as unavailable, never as "no alerts"
        alerts, incidents, runs, incident_ids, run_ids, db_error = [], [], [], [], [], "database"

    def db_link(found: list[Any], asked: list[Any], by: str, what: str) -> dict[str, Any]:
        if db_error:
            return _link("unavailable", [], by, "The database did not answer.")
        if not asked:
            return _link("missing_identifier", [], by,
                         f"Neither the trace's spans nor its log lines carry {by}, so no {what} is claimed for it.")
        if not found:
            return _link("none", [], by, f"The trace names {len(asked)} {by} value(s); no matching {what} exists.")
        return _link("linked", found, by, f"Matched by {by} recorded on the trace.")

    # -- metrics -------------------------------------------------------------------
    services = trace.get("services") or sorted({line["service"] for line in logs.get("items") or [] if line.get("service")})
    metrics_link = {
        "state": "time_aligned", "by": None, "count": 0, "items": [],
        "since": since.isoformat(), "until": until.isoformat(), "services": services,
        "note": "Prometheus series carry no trace id (labels stay bounded), so these charts show the same time window. "
                "They are aligned by time, not linked by identifier.",
    }

    return {
        "checked_at": now.isoformat(), "trace_id": trace_id, "window": {"since": since.isoformat(),
                                                                         "until": until.isoformat(), "basis": window_basis},
        "identifiers": ids,
        "links": {
            "trace": trace_link, "logs": logs_link, "metrics": metrics_link,
            "alerts": db_link(alerts, alert_ids, "alert_id", "alert"),
            "incidents": db_link(incidents, incident_ids, "incident_id", "incident"),
            "collection_runs": db_link(runs, run_ids, "collection_run_id", "collection run"),
        },
    }
