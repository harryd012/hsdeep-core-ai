"""Acknowledgements are attributed to the signed-in user, never the body.

Before: alert acknowledge required a client-supplied `acknowledged_by`, and
the Incidents page sent the nil UUID, which violates the users foreign key.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.core import auth
from app.core.auth import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.user import User
from app.repositories.incident_repository import IncidentRepository

pytestmark = pytest.mark.usefixtures("seeded_subscription")


def _client(tenant_id, user_id, permissions):
    token = create_access_token(
        user_id=user_id, tenant_id=tenant_id, email="actor@hsdeep.local",
        is_superuser=False, permissions=frozenset(permissions),
    )
    return httpx.AsyncClient(app=app, base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _operator(db, tenant) -> User:
    user = User(tenant_id=tenant.id, email=f"op-{uuid.uuid4().hex[:6]}@hsdeep.local")
    db.add(user)
    await db.commit()
    return user


async def test_alert_acknowledge_uses_the_principal(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    user = await _operator(db, tenant)
    now = datetime.now(timezone.utc)
    alert = Alert(
        tenant_id=tenant.id, monitoring_source_id=source.id, severity="warning",
        status="open", title="CPU high", triggered_at=now, last_occurred_at=now,
    )
    db.add(alert)
    await db.commit()

    async with _client(tenant.id, user.id, {"alerts.acknowledge"}) as client:
        # No body at all, and a spoofed id: both end up attributed to `user`.
        resp = await client.post(f"/api/alerts/{alert.id}/acknowledge", json={"acknowledged_by": str(uuid.uuid4())})
    assert resp.status_code == 200, resp.text
    assert resp.json()["acknowledged_by"] == str(user.id)


async def test_incident_acknowledge_without_body_uses_the_principal(tenant, db):
    user = await _operator(db, tenant)
    now = datetime.now(timezone.utc)
    incident = await IncidentRepository(db).create(
        tenant_id=tenant.id, title="Site down", status="open", severity="critical",
        opened_at=now, last_alert_at=now,
    )
    await db.commit()

    async with _client(tenant.id, user.id, {"incidents.write"}) as client:
        resp = await client.post(f"/api/incidents/{incident.id}/acknowledge", json={})
    assert resp.status_code == 200, resp.text
    await db.refresh(incident)
    assert incident.acknowledged_by == user.id


def test_api_key_actor_is_none():
    principal = auth.Principal(
        user_id=uuid.UUID(int=0), tenant_id=uuid.uuid4(), email="api-key",
        is_superuser=False, permissions=frozenset(),
    )
    assert auth.get_actor_user_id(principal) is None
