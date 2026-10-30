"""Repro + configurable-window + concurrency tests for incident recurrence.

These use in-memory fake repositories (no DB) so they run under the test-isolation
guard without needing a live Postgres. The DB-level dedup backstop (unique
partial index ``uq_incidents_open_signature``) is covered by migration
``e5f7a2c9d4b1``; the concurrency test here pins the check-then-insert race it
is meant to close.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.alert import Alert
from app.models.incident import Incident
from app.services.alerting import correlation_engine
from app.services.alerting.correlation_engine import CorrelationEngine

pytestmark = pytest.mark.asyncio


def _make_alert(
    now: datetime,
    *,
    tenant_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    title: str = "SSL Security Check (Port 993)",
    metric_type: str | None = "ssl_security",
) -> Alert:
    return Alert(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        device_id=device_id or uuid.uuid4(),
        site_id=None,
        sensor_id=None,
        signal_external_id=None,
        title=title,
        severity="critical",
        status="open",
        metric_type=metric_type,
        source="monitoring_sync",
        category=None,
        triggered_at=now,
        last_occurred_at=now,
        occurrence_count=1,
        is_root_cause_candidate=False,
        is_suppressed=False,
        incident_id=None,
        correlation_id=None,
    )


def _make_incident(tenant_id: uuid.UUID, last_alert_at: datetime) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        site_id=None,
        device_id=None,
        title="Incident: SSL Security Check (Port 993)",
        severity="critical",
        status="open",
        opened_at=last_alert_at,
        last_alert_at=last_alert_at,
        alert_count=1,
    )


class _FakeAlertRepo:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.last_alert: Alert | None = None

    async def set_alert_signature(self, alert: Alert, signature: str) -> None:
        self.last_alert = alert
        alert.alert_signature = signature
        self.calls.append(("set_alert_signature", signature))

    async def set_correlation_fields(self, alert: Alert, **kwargs) -> None:
        self.calls.append(("set_correlation_fields", kwargs))
        for k, v in kwargs.items():
            setattr(alert, k, v)

    async def list(self, tenant_id, incident_id=None, status=None, limit=1000):
        self.calls.append(("list", incident_id))
        return [self.last_alert] if self.last_alert is not None else []


class _FakeIncidentRepo:
    def __init__(self, open_for_signature: Incident | None = None) -> None:
        self.open_for_signature = open_for_signature
        self.created: list[Incident] = []
        self.calls: list[tuple] = []

    async def get_open_for_signature(self, signature, tenant_id):
        self.calls.append(("get_open_for_signature", signature))
        return self.open_for_signature

    async def get_open_for_device_and_sensor(self, device_id, sensor_id, tenant_id):
        return None

    async def get_open_for_device(self, device_id, tenant_id):
        return None

    async def get_open_for_site(self, site_id, tenant_id):
        return None

    async def create(self, **kwargs) -> Incident:
        self.calls.append(("create", kwargs["title"]))
        incident = Incident(
            id=uuid.uuid4(),
            tenant_id=kwargs["tenant_id"],
            site_id=kwargs.get("site_id"),
            device_id=kwargs.get("device_id"),
            title=kwargs.get("title"),
            severity=kwargs.get("severity"),
            status=kwargs.get("status"),
            opened_at=kwargs.get("opened_at"),
            last_alert_at=kwargs.get("last_alert_at"),
            alert_count=kwargs.get("alert_count", 1),
        )
        self.created.append(incident)
        return incident

    async def add_alert(self, incident, occurred_at=None) -> None:
        self.calls.append(("add_alert", incident.id))
        incident.last_alert_at = occurred_at
        incident.alert_count += 1

    async def touch_occurrence(self, incident, occurred_at=None) -> None:
        self.calls.append(("touch_occurrence", incident.id))

    async def update_severity(self, incident, severity) -> None:
        self.calls.append(("update_severity", incident.id, severity))
        incident.severity = severity

    async def set_root_cause(self, incident, root_cause_id) -> None:
        self.calls.append(("set_root_cause", incident.id, root_cause_id))
        incident.root_cause_alert_id = root_cause_id

async def test_31min_gap_creates_second_incident_with_default_window():
    """Repro: with the default 30-min window a 31-min-old open incident is
    treated as stale, so a fresh incident is created for the next poll."""
    now = datetime.now(timezone.utc)
    alert = _make_alert(now)
    stale = _make_incident(alert.tenant_id, last_alert_at=now - timedelta(minutes=31))
    incident_repo = _FakeIncidentRepo(open_for_signature=stale)
    alert_repo = _FakeAlertRepo()
    engine = CorrelationEngine(alert_repo, incident_repo)

    result = await engine.correlate(alert)

    assert result.is_new_incident is True
    assert len(incident_repo.created) == 1
    assert not any(c[0] == "add_alert" for c in incident_repo.calls)


async def test_configurable_window_reuses_31min_old_incident(monkeypatch):
    """Fix: a wider inactivity window reuses the still-relevant open incident."""
    monkeypatch.setattr(
        correlation_engine, "_correlation_window", lambda: timedelta(minutes=60)
    )
    now = datetime.now(timezone.utc)
    alert = _make_alert(now)
    existing = _make_incident(alert.tenant_id, last_alert_at=now - timedelta(minutes=31))
    incident_repo = _FakeIncidentRepo(open_for_signature=existing)
    alert_repo = _FakeAlertRepo()
    engine = CorrelationEngine(alert_repo, incident_repo)

    result = await engine.correlate(alert)

    assert result.is_new_incident is False
    assert len(incident_repo.created) == 0
    assert any(c[0] == "add_alert" for c in incident_repo.calls)


async def test_concurrent_correlate_race_produces_two_incidents_without_db_guard():
    """Demonstrates the check-then-insert race: two concurrent correlate() calls
    both read "no open incident" and both create one. Migration e5f7a2c9d4b1's
    unique partial index is the DB backstop that turns this into one incident."""
    incident_repo = _FakeIncidentRepo(open_for_signature=None)
    alert_repo = _FakeAlertRepo()
    engine = CorrelationEngine(alert_repo, incident_repo)
    now = datetime.now(timezone.utc)
    a1 = _make_alert(now)
    a2 = _make_alert(now, device_id=a1.device_id)  # same probe -> same signature

    await asyncio.gather(engine.correlate(a1), engine.correlate(a2))

    # Without the DB unique index, both workers create an incident.
    assert len(incident_repo.created) == 2

async def test_same_probe_creates_single_incident_and_stamps_signature():
    """Correlating the same probe must yield exactly ONE incident that carries
    the probe signature, so the unique partial index can enforce
    idempotence on the database.
    """
    repo = _SignatureReplyingRepo()
    alert_repo = _FakeAlertRepo()
    engine = CorrelationEngine(alert_repo, repo)
    now = datetime.now(timezone.utc)
    a1 = _make_alert(now)
    # Same device, sensor_id None -> same probe signature as a1.
    a2 = _make_alert(now, device_id=a1.device_id)

    first = await engine.correlate(a1)
    second = await engine.correlate(a2)

    # a2 must reuse the incident opened by a1, not create a duplicate.
    assert first.is_new_incident is True
    assert second.is_new_incident is False
    assert len(repo.created) == 1
    assert repo.created[0].incident_signature == a1.alert_signature
    # The repository query honoured the stored signature.
    assert repo.last_get_signature == a1.alert_signature


class _SignatureReplyingRepo:
    """Fake incident repo whose ``get_open_for_signature`` honours stored
    ``incident_signature``, mirroring the corrected repository query so that
    this deterministic, in-memory regression test actually passes through the
    new signature look-up path (the real Postgres unique index is checked by
    the DB-level migration, not this in-memory test).
    """

    def __init__(self) -> None:
        self.created: list[Incident] = []
        self.last_get_signature: str | None = None

    async def get_open_for_signature(self, signature: str, tenant_id: uuid.UUID) -> Incident | None:
        self.last_get_signature = signature
        for inc in self.created:
            if inc.incident_signature == signature:
                return inc
        return None

    async def get_open_for_device_and_sensor(self, device_id, sensor_id, tenant_id) -> Incident | None:
        return None

    async def get_open_for_device(self, device_id, tenant_id) -> Incident | None:
        return None

    async def get_open_for_site(self, site_id, tenant_id) -> Incident | None:
        return None

    async def create(self, **kwargs) -> Incident:
        incident = Incident(
            id=uuid.uuid4(),
            tenant_id=kwargs["tenant_id"],
            site_id=kwargs.get("site_id"),
            device_id=kwargs.get("device_id"),
            title=kwargs.get("title"),
            severity=kwargs.get("severity"),
            status=kwargs.get("status"),
            opened_at=kwargs.get("opened_at"),
            last_alert_at=kwargs.get("last_alert_at"),
            alert_count=kwargs.get("alert_count", 1),
            incident_signature=kwargs.get("incident_signature"),
        )
        self.created.append(incident)
        return incident

    async def add_alert(self, incident, occurred_at=None) -> None:
        incident.last_alert_at = occurred_at
        incident.alert_count += 1

    async def touch_occurrence(self, incident, occurred_at=None) -> None:
        pass

    async def update_severity(self, incident, severity) -> None:
        pass

    async def set_root_cause(self, incident, root_cause_id) -> None:
        pass


