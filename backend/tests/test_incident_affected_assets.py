"""
Regression test for Bug 1: GET /api/incidents returns 500 (ResponseValidationError).

Root cause: the Pydantic schema declared ``affected_assets: dict`` while the
DB column (and the correlation engine's intent) stores a *list* of asset
dicts.  Every incident row defaulted to ``[]`` (a list), which Pydantic
rejected against the ``dict`` type, producing:

    {'type': 'dict_type', 'loc': ('response', N, 'affected_assets'),
     'msg': 'Input should be a valid dictionary', 'input': []}

Fix: schema and model now declare ``affected_assets: list[dict]``, the
correlation engine writes a flat list (not ``{"assets": [...]}``), and an
Alembic data migration normalizes existing rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.models.incident import Incident


@pytest.mark.asyncio
class TestIncidentAffectedAssets:
    """Regression tests ensuring affected_assets is a list of dicts, not a
    dict, so FastAPI's response validation no longer rejects incident rows."""

    async def test_incident_with_list_affected_assets_returns_200(self, tenant):
        """An incident whose affected_assets is a list of asset dicts must
        pass FastAPI's response validation (no ResponseValidationError).

        This is the exact scenario from the bug report: an incident with
        ``affected_assets = []`` (the server_default) must serialize
        through ``IncidentOut`` (which now declares ``list[dict]``) and
        return HTTP 200.
        """
        from app.main import app

        now = datetime.now(timezone.utc)
        assets = [
            {"id": str(uuid.uuid4()), "type": "device", "name": "Web Server 1"},
            {"id": str(uuid.uuid4()), "type": "site", "name": "NYC Data Center"},
        ]

        async with AsyncSessionLocal() as db:
            incident = Incident(
                tenant_id=tenant.id,
                title="Test Incident",
                description="Regression test for affected_assets type",
                severity="critical",
                status="open",
                opened_at=now,
                last_alert_at=now,
                alert_count=1,
                affected_assets=assets,
            )
            db.add(incident)
            await db.commit()
            await db.refresh(incident)
            incident_id = incident.id

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/incidents?tenant_id={tenant.id}",
                headers={"Authorization": "Bearer " + create_access_token(
                    user_id=uuid.uuid4(), tenant_id=tenant.id,
                    email="t@t.io", is_superuser=True, permissions=frozenset())},
            )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        incident_data = next(i for i in data if i["id"] == str(incident_id))
        assert isinstance(incident_data["affected_assets"], list)
        assert len(incident_data["affected_assets"]) == 2
        assert incident_data["affected_assets"][0]["type"] == "device"
        assert incident_data["affected_assets"][1]["type"] == "site"

    async def test_incident_with_empty_affected_assets_returns_200(self, tenant):
        """An incident with the server_default ``affected_assets = []`` (no
        member alerts that carry a device/site) must also pass validation."""
        from app.main import app

        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            incident = Incident(
                tenant_id=tenant.id,
                title="Empty Assets Incident",
                description="Regression test - empty affected_assets",
                severity="warning",
                status="open",
                opened_at=now,
                last_alert_at=now,
                alert_count=1,
                affected_assets=[],
            )
            db.add(incident)
            await db.commit()
            await db.refresh(incident)
            incident_id = incident.id

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/incidents?tenant_id={tenant.id}",
                headers={"Authorization": "Bearer " + create_access_token(
                    user_id=uuid.uuid4(), tenant_id=tenant.id,
                    email="t@t.io", is_superuser=True, permissions=frozenset())},
            )

        assert response.status_code == 200
        data = response.json()
        incident_data = next(i for i in data if i["id"] == str(incident_id))
        assert incident_data["affected_assets"] == []
