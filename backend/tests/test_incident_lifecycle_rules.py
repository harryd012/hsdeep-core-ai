"""lifecycle.resolve_on_clear + close_stale_incidents — mocked repos, no DB."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.incident import Incident
from app.services.alerting.lifecycle import close_stale_incidents, resolve_on_clear

pytestmark = pytest.mark.asyncio


class _FakeAlertRepo:
    def __init__(self, active: int = 0) -> None:
        self.active = active

    async def count_active_for_incident(self, incident_id) -> int:
        return self.active


class _FakeIncidentRepo:
    def __init__(self, stale: list[Incident] | None = None) -> None:
        self.stale = stale or []
        self.resolved: list[Incident] = []
        self.closed: list[tuple[Incident, str]] = []

    async def resolve(self, incident) -> Incident:
        self.resolved.append(incident)
        incident.status = "resolved"
        return incident

    async def list_stale_open(self, before, *, limit=500):
        return self.stale

    async def close_stale(self, incident, *, reason) -> Incident:
        self.closed.append((incident, reason))
        incident.status = "closed"
        incident.closure_reason = reason
        return incident


def _incident(status: str = "open") -> Incident:
    now = datetime.now(timezone.utc)
    return Incident(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Incident: SSL Security Check (Port 993)",
        severity="critical",
        status=status,
        opened_at=now,
        last_alert_at=now,
        alert_count=1,
    )


async def test_resolve_on_clear_resolves_when_no_active_members():
    incident = _incident()
    incident_repo = _FakeIncidentRepo()
    alert_repo = _FakeAlertRepo(active=0)

    result = await resolve_on_clear(alert_repo, incident_repo, incident)

    assert result is incident
    assert incident.status == "resolved"
    assert len(incident_repo.resolved) == 1


async def test_resolve_on_clear_noop_when_active_members_remain():
    incident = _incident()
    incident_repo = _FakeIncidentRepo()
    alert_repo = _FakeAlertRepo(active=1)

    result = await resolve_on_clear(alert_repo, incident_repo, incident)

    assert result is None
    assert incident.status == "open"
    assert len(incident_repo.resolved) == 0


async def test_resolve_on_clear_noop_when_already_terminal():
    incident = _incident(status="closed")
    incident_repo = _FakeIncidentRepo()
    alert_repo = _FakeAlertRepo(active=0)

    result = await resolve_on_clear(alert_repo, incident_repo, incident)

    assert result is None
    assert len(incident_repo.resolved) == 0


async def test_close_stale_incidents_closes_and_records_reason():
    stale = [_incident() for _ in range(3)]
    incident_repo = _FakeIncidentRepo(stale=stale)

    closed = await close_stale_incidents(incident_repo, stale_minutes=1440)

    assert closed == 3
    assert all(inc.status == "closed" for inc in stale)
    assert all("1440" in reason for _, reason in incident_repo.closed)
    assert all(inc.closure_reason is not None for inc in stale)


async def test_close_stale_incidents_noop_when_none():
    incident_repo = _FakeIncidentRepo(stale=[])

    closed = await close_stale_incidents(incident_repo, stale_minutes=1440)

    assert closed == 0
