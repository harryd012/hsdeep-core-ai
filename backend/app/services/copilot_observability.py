"""
Copilot observability context (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P10 slice 1).

Gives the Copilot's query path the observability data built in P1-P9, the
same way it already gets alerts, incidents and tickets: a bounded, read-only,
tenant-scoped context assembled from the existing services, included only
when the question is about it, and only for callers who may read monitoring
data. The language model is only ever given these real rows.

  * by topic: sensors, network, logs, traces, unusual readings (baselines),
    monitoring coverage;
  * by host: a question naming a device (by name, hostname or address) gets
    that device's failing sensors, enabled-but-down interfaces, open alerts
    and recent warning / critical log lines.

Every provider degrades to omission (with a note) instead of failing the turn.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.device import Device
from app.models.interface import Interface
from app.models.sensor import Sensor
from app.models.sensor_baseline import SensorBaseline

logger = logging.getLogger(__name__)

MAX_ITEMS = 10
MAX_HOSTS = 3
MAX_MESSAGE_CHARS = 200

#: Whole words (regex); a trailing \w* marks a stem ("anomal" -> anomaly, anomalous).
TOPICS: dict[str, tuple[str, ...]] = {
    "sensors": (r"sensors?", r"thresholds?", r"failing"),
    "network": (r"network\w*", r"interfaces?", r"links?", r"bandwidth", r"utili[sz]\w*", r"vpn", r"tunnels?"),
    "logs": (r"logs?", r"syslog", r"log lines?"),
    "traces": (r"traces?", r"tracing", r"latency", r"slow\w*", r"spans?", r"apm", r"service map"),
    "anomalies": (r"anomal\w*", r"unusual", r"baselines?", r"abnormal", r"spikes?"),
    "coverage": (r"coverage", r"unmonitored", r"not monitored", r"monitoring gaps?", r"missing monitoring"),
}
_TOPIC_RE = {t: re.compile(r"\b(?:" + "|".join(words) + r")\b", re.I) for t, words in TOPICS.items()}

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{2,}")


def topics_in(message: str) -> list[str]:
    return [t for t, pattern in _TOPIC_RE.items() if pattern.search(message)]


def host_tokens(message: str) -> set[str]:
    """Words that could name a host: 3+ characters, letters, digits, dots, dashes."""
    return {t.lower().rstrip(".:") for t in _TOKEN.findall(message)}


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "…"


async def _sensors(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    counts = dict(
        (await db.execute(
            select(Sensor.status, func.count())
            .where(Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False))
            .group_by(Sensor.status)
        )).all()
    )
    failing = (await db.execute(
        select(Sensor.name, Sensor.status, Sensor.current_value, Sensor.unit, Device.name)
        .outerjoin(Device, Device.id == Sensor.device_id)
        .where(Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False), Sensor.status.in_(("down", "warning")))
        .order_by((Sensor.status == "down").desc(), Sensor.name)
        .limit(MAX_ITEMS)
    )).all()
    return {
        "by_status": counts,
        "failing": [{"sensor": n, "status": s, "value": v, "unit": u, "device": d} for n, s, v, u, d in failing],
    }


async def _network(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    from app.services.monitoring.network_center import NetworkCenterService

    o = await NetworkCenterService(db).overview(tenant_id)
    return {
        "interfaces": {k: v for k, v in o["interfaces"].items() if k != "last_telemetry_at"},
        "last_counter_sample": o["interfaces"]["last_telemetry_at"],
        "busiest": [{"device": i["device_name"], "interface": i["name"], "utilization_pct": i["utilization_pct"]}
                    for i in o["top_utilization"][:5]],
        "down_while_enabled": [{"device": i["device_name"], "interface": i["name"], "since": i["last_status_change_at"]}
                               for i in o["unexpected_down"][:MAX_ITEMS]],
        "vpn_tunnels": {"total": o["tunnels"]["total"], "down": [
            {"firewall": t["device_name"], "tunnel": t["name"]} for t in o["tunnels"]["items"] if t["status"] == "down"
        ][:MAX_ITEMS]},
    }


async def _logs(db: AsyncSession, tenant_id: uuid.UUID, now: datetime, *, device_id: uuid.UUID | None = None,
                limit: int = MAX_ITEMS) -> dict:
    from app.services.logs.explorer import LogExplorer, LogQuery

    q = LogQuery(since=now - timedelta(hours=1), until=now, severities=("critical", "warning"), device_id=device_id)
    items, total = await LogExplorer(db).search(tenant_id, q, limit=limit, offset=0)
    return {
        "window": "last hour", "warning_or_critical_lines": total,
        "latest": [{"time": i["timestamp"], "severity": i["severity"], "host": i["device_name"] or i["host"],
                    "app": i["app"], "message": _clip(i["message"])} for i in items],
    }


async def _traces(db: AsyncSession, tenant_id: uuid.UUID, now: datetime) -> dict:
    from app.services.tracing.query import TraceQuery

    services = await TraceQuery(db).services(tenant_id, now - timedelta(hours=1), now)
    return {
        "window": "last hour",
        "services": [{"service": s["service"], "spans": s["spans"], "error_rate_pct": s["error_rate_pct"],
                      "p95_ms": s["p95_ms"]} for s in services[:MAX_ITEMS]],
    }


async def _anomalies(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    rows = (await db.execute(
        select(Sensor.name, Sensor.unit, Device.name, SensorBaseline.last_value, SensorBaseline.last_expected,
               SensorBaseline.last_z, SensorBaseline.last_evaluated_at)
        .join(Sensor, Sensor.id == SensorBaseline.sensor_id)
        .outerjoin(Device, Device.id == Sensor.device_id)
        .where(SensorBaseline.tenant_id == tenant_id, SensorBaseline.state == "anomalous")
        .order_by(func.abs(SensorBaseline.last_z).desc().nulls_last())
        .limit(MAX_ITEMS)
    )).all()
    watched = (await db.execute(
        select(func.count()).select_from(SensorBaseline)
        .where(SensorBaseline.tenant_id == tenant_id, SensorBaseline.enabled.is_(True))
    )).scalar_one()
    return {
        "sensors_with_baseline": watched,
        "unusual_now": [{"sensor": n, "device": d, "value": v, "usual": e, "std_from_usual": z, "unit": u, "checked": t}
                        for n, u, d, v, e, z, t in rows],
    }


async def _coverage(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    from app.services.monitoring.sensor_library import RULES, SensorLibraryService

    hosts = await SensorLibraryService(db).recommendations(tenant_id)
    return {r.title: sum(1 for h in hosts if r.key in h["rules"]) for r in RULES.values()}


async def _hosts(db: AsyncSession, tenant_id: uuid.UUID, message: str, now: datetime) -> list[dict]:
    tokens = host_tokens(message)
    if not tokens:
        return []
    devices = (await db.execute(
        select(Device)
        .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False), or_(
            func.lower(Device.name).in_(tokens), func.lower(Device.hostname).in_(tokens), Device.ip_address.in_(tokens),
        ))
        .order_by(Device.name)
        .limit(MAX_HOSTS)
    )).scalars().all()
    out = []
    for d in devices:
        failing = (await db.execute(
            select(Sensor.name, Sensor.status, Sensor.current_value, Sensor.unit)
            .where(Sensor.tenant_id == tenant_id, Sensor.device_id == d.id, Sensor.is_deleted.is_(False),
                   Sensor.status.in_(("down", "warning")))
            .limit(MAX_ITEMS)
        )).all()
        down = (await db.execute(
            select(Interface.name, Interface.last_status_change_at)
            .where(Interface.device_id == d.id, Interface.is_deleted.is_(False),
                   and_(Interface.admin_status == "up", Interface.oper_status == "down"))
            .limit(MAX_ITEMS)
        )).all()
        alerts = (await db.execute(
            select(Alert.title, Alert.severity, Alert.status, Alert.triggered_at)
            .where(Alert.tenant_id == tenant_id, Alert.device_id == d.id, Alert.status != "resolved",
                   Alert.is_deleted.is_(False))
            .order_by(Alert.triggered_at.desc())
            .limit(MAX_ITEMS)
        )).all()
        entry = {
            "device": d.name, "type": d.device_type, "status": d.status, "address": d.ip_address or d.hostname,
            "failing_sensors": [{"sensor": n, "status": s, "value": v, "unit": u} for n, s, v, u in failing],
            "interfaces_down_while_enabled": [{"interface": n, "since": t} for n, t in down],
            "open_alerts": [{"title": t, "severity": s, "status": st, "since": at} for t, s, st, at in alerts],
        }
        try:
            entry["recent_logs"] = (await _logs(db, tenant_id, now, device_id=d.id, limit=5))["latest"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Copilot: logs for %s unavailable: %s", d.id, exc)
        out.append(entry)
    return out


async def observability_context(db: AsyncSession, tenant_id: uuid.UUID, message: str,
                                now: datetime | None = None) -> tuple[dict, list[str]]:
    """The observability part of a Copilot query context, and notes on anything omitted."""
    now = now or datetime.now(timezone.utc)
    providers = {
        "sensors": lambda: _sensors(db, tenant_id),
        "network": lambda: _network(db, tenant_id),
        "logs": lambda: _logs(db, tenant_id, now),
        "traces": lambda: _traces(db, tenant_id, now),
        "anomalies": lambda: _anomalies(db, tenant_id),
        "coverage": lambda: _coverage(db, tenant_id),
    }
    context: dict = {}
    notes: list[str] = []
    # Each provider in its own savepoint: a failed query drops only its own
    # part, never the rest of the turn's session.
    for topic in topics_in(message):
        try:
            async with db.begin_nested():
                context[f"observability_{topic}"] = await providers[topic]()
        except Exception as exc:  # noqa: BLE001 - degrade, don't fail the turn
            logger.warning("Copilot: %s context unavailable: %s", topic, exc)
            notes.append(f"{topic} unavailable")
    try:
        async with db.begin_nested():
            hosts = await _hosts(db, tenant_id, message, now)
        if hosts:
            context["observability_hosts"] = hosts
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copilot: host context unavailable: %s", exc)
        notes.append("host context unavailable")
    return context, notes


__all__ = ["TOPICS", "host_tokens", "observability_context", "topics_in"]
