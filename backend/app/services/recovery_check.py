"""
Incident recovery check: did the affected resources actually recover?

Answers "what evidence supports closure?" from persisted telemetry, never
from an action's exit status. For one incident it evaluates:

  - every member alert: resolved -> pass, otherwise fail;
  - every sensor those alerts came from: a reading newer than ``since`` with
    status up -> pass; down/warning -> fail; no reading after ``since`` ->
    inconclusive (stale);
  - every affected device (the incident's own and the alerts'): seen after
    ``since`` with status up -> pass; down/warning -> fail; otherwise
    inconclusive.

``since`` defaults to the incident's last alert: only observations made after
the problem was last signalled count as evidence of recovery.

Verdict: any failing check -> ``not_recovered``; otherwise any inconclusive
check, or no checks at all -> ``inconclusive``; else ``recovered``. Read-only:
this module never changes incident state.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.device import Device
from app.models.incident import Incident
from app.models.sensor import Sensor

_UP = {"up", "online", "ok"}
_PROBLEM = {"down", "warning", "warn", "critical", "offline", "error"}


@dataclass(frozen=True)
class RecoveryCheckItem:
    kind: str  # alert | sensor | device
    id: uuid.UUID
    name: str
    status: str
    observed_at: datetime | None
    result: str  # pass | fail | inconclusive
    detail: str


@dataclass
class RecoveryCheckResult:
    incident_id: uuid.UUID
    since: datetime
    evaluated_at: datetime
    verdict: str  # recovered | not_recovered | inconclusive
    checks: list[RecoveryCheckItem] = field(default_factory=list)


class RecoveryIncidentNotFound(LookupError):
    pass


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _telemetry_check(kind, rid, name, status, observed_at, since) -> RecoveryCheckItem:
    state = (status or "unknown").strip().lower()
    observed_at = _as_utc(observed_at)
    fresh = observed_at is not None and observed_at > since
    if state in _PROBLEM:
        result, detail = "fail", f"{kind} is {state}"
    elif not fresh:
        result = "inconclusive"
        detail = (
            f"no {kind} observation since {since.isoformat()}"
            if observed_at is not None
            else f"{kind} has never reported"
        )
    elif state in _UP:
        result, detail = "pass", f"{kind} is {state} as of {observed_at.isoformat()}"
    else:
        result, detail = "inconclusive", f"{kind} status is {state}"
    return RecoveryCheckItem(kind, rid, name, state, observed_at, result, detail)


def verdict_of(checks: list[RecoveryCheckItem]) -> str:
    results = {c.result for c in checks}
    if "fail" in results:
        return "not_recovered"
    if not checks or "inconclusive" in results:
        return "inconclusive"
    return "recovered"


async def check_incident_recovery(
    db: AsyncSession,
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    since: datetime | None = None,
) -> RecoveryCheckResult:
    incident = (
        await db.execute(
            select(Incident).where(
                Incident.id == incident_id,
                Incident.tenant_id == tenant_id,
                Incident.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if incident is None:
        raise RecoveryIncidentNotFound(str(incident_id))

    since = _as_utc(since) or _as_utc(incident.last_alert_at) or _as_utc(incident.opened_at)
    alerts = (
        await db.execute(
            select(Alert).where(
                Alert.tenant_id == tenant_id,
                Alert.incident_id == incident_id,
                Alert.is_deleted.is_(False),
            )
        )
    ).scalars().all()

    checks: list[RecoveryCheckItem] = []
    for alert in alerts:
        resolved = alert.status == "resolved"
        checks.append(RecoveryCheckItem(
            kind="alert",
            id=alert.id,
            name=alert.title,
            status=alert.status,
            observed_at=_as_utc(alert.resolved_at if resolved else alert.last_occurred_at),
            result="pass" if resolved else "fail",
            detail="alert resolved" if resolved else f"alert still {alert.status}",
        ))

    sensor_ids = {a.sensor_id for a in alerts if a.sensor_id}
    if sensor_ids:
        sensors = (
            await db.execute(
                select(Sensor).where(
                    Sensor.tenant_id == tenant_id,
                    Sensor.id.in_(sensor_ids),
                    Sensor.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        for s in sorted(sensors, key=lambda s: s.name):
            checks.append(_telemetry_check("sensor", s.id, s.name, s.status, s.last_value_at, since))

    device_ids = {a.device_id for a in alerts if a.device_id}
    if incident.device_id:
        device_ids.add(incident.device_id)
    if device_ids:
        devices = (
            await db.execute(
                select(Device).where(
                    Device.tenant_id == tenant_id,
                    Device.id.in_(device_ids),
                    Device.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        for d in sorted(devices, key=lambda d: d.name):
            checks.append(_telemetry_check("device", d.id, d.name, d.status, d.last_seen_at, since))

    return RecoveryCheckResult(
        incident_id=incident.id,
        since=since,
        evaluated_at=datetime.now(timezone.utc),
        verdict=verdict_of(checks),
        checks=checks,
    )
