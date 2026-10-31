"""IncidentRepository.intelligence_counts / _correlated_count semantics.

Pins the exact count definitions behind ``GET /api/incidents/intelligence``:
  * active     = open + acknowledged + investigating (excluding soft-deleted)
  * correlated = open AND alert_count > 1             (excluding soft-deleted)

Also proves ``intelligence_counts``'s active count equals ``active_counts()`` for
the same status set, so the two aggregates cannot drift.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.incident import Incident
from app.models.tenant import Tenant
from app.repositories.incident_repository import IncidentRepository

ACTIVE_STATUSES = ["open", "acknowledged", "investigating"]


def _make_incident(
    t: Tenant,
    title: str,
    *,
    status: str = "open",
    alert_count: int = 1,
    deleted: bool = False,
    now: datetime | None = None,
) -> Incident:
    now = now or datetime.now(timezone.utc)
    return Incident(
        tenant_id=t.id,
        title=title,
        severity="critical",
        status=status,
        alert_count=alert_count,
        opened_at=now,
        last_alert_at=now,
        is_deleted=deleted,
    )


@pytest.mark.asyncio
async def test_intelligence_counts_semantics(tenant, db):
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            _make_incident(tenant, "open-1", status="open", alert_count=1, now=now),
            _make_incident(tenant, "open-2", status="open", alert_count=2, now=now),
            _make_incident(tenant, "open-5", status="open", alert_count=5, now=now),
            _make_incident(tenant, "ack-1", status="acknowledged", alert_count=1, now=now),
            _make_incident(tenant, "inv-3", status="investigating", alert_count=3, now=now),
            _make_incident(tenant, "resolved-9", status="resolved", alert_count=9, now=now),
            _make_incident(tenant, "deleted-open-2", status="open", alert_count=2, deleted=True, now=now),
        ]
    )
    await db.flush()

    repo = IncidentRepository(db)
    active, correlated = await repo.intelligence_counts(tenant.id)

    # open-1, open-2, open-5, ack-1, inv-3 = 5 (resolved + soft-deleted excluded)
    assert active == 5
    # open-2, open-5 = 2 (status must be 'open' AND alert_count > 1)
    assert correlated == 2


@pytest.mark.asyncio
async def test_intelligence_active_equals_active_counts(tenant, db):
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            _make_incident(tenant, "a", status="open", alert_count=1, now=now),
            _make_incident(tenant, "b", status="acknowledged", alert_count=4, now=now),
            _make_incident(tenant, "c", status="investigating", alert_count=2, now=now),
            _make_incident(tenant, "d", status="resolved", alert_count=7, now=now),
        ]
    )
    await db.flush()

    repo = IncidentRepository(db)
    active, _ = await repo.intelligence_counts(tenant.id)
    active_direct, _ = await repo.active_counts(tenant.id, statuses=ACTIVE_STATUSES)
    assert active == active_direct == 3


@pytest.mark.asyncio
async def test_intelligence_counts_tenant_isolated(tenant, db):
    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(other)
    await db.flush()

    now = datetime.now(timezone.utc)
    db.add_all(
        [
            _make_incident(tenant, "mine-open-2", status="open", alert_count=2, now=now),
            _make_incident(other, "theirs-open-2", status="open", alert_count=2, now=now),
            _make_incident(other, "theirs-open-9", status="open", alert_count=9, now=now),
        ]
    )
    await db.flush()

    repo = IncidentRepository(db)
    active, correlated = await repo.intelligence_counts(tenant.id)
    assert active == 1
    assert correlated == 1


@pytest.mark.asyncio
async def test_intelligence_counts_empty_tenant(tenant, db):
    repo = IncidentRepository(db)
    active, correlated = await repo.intelligence_counts(tenant.id)
    assert (active, correlated) == (0, 0)
