"""
Universal infrastructure API (spec sections 31, 35, 38, 39).

Uses the REAL app + database (project convention). Every case checks one of the
four things the milestone must not get wrong:

  * authentication is required (401 without a token, not 404),
  * authorization is enforced (403 without `devices.read`),
  * tenant isolation holds (another tenant's resource is a 404, never data),
  * an unsupported/absent capability is reported as such, with a reason.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.repositories.device_repository import DeviceRepository

pytestmark = pytest.mark.asyncio

BASE = "/api/infrastructure/intelligence"

READ_PERMISSIONS = frozenset(
    {"devices.read", "sites.read", "dashboard.read"}
)


def _token(tenant_id: uuid.UUID, permissions=READ_PERMISSIONS) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="universal-test@hsdeep.local",
        is_superuser=False,
        permissions=frozenset(permissions),
    )


def _client(tenant_id: uuid.UUID | None, permissions=READ_PERMISSIONS):
    headers = (
        {"Authorization": f"Bearer {_token(tenant_id, permissions)}"}
        if tenant_id is not None else {}
    )
    return httpx.AsyncClient(app=app, base_url="http://test", headers=headers)


async def _seed_device(tenant_id: uuid.UUID, source_id: uuid.UUID):
    async with AsyncSessionLocal() as db:
        device, _created = await DeviceRepository(db).upsert(
            tenant_id=tenant_id,
            monitoring_source_id=source_id,
            external_id="api-dev-1",
            name="branch-fw-01",
            device_type="firewall",
            vendor="Fortinet",
            status="up",
            provider="fortigate",
        )
        await db.commit()
        return device.id


@pytest.mark.parametrize(
    "path",
    [
        f"{BASE}/catalog",
        f"{BASE}/providers",
        f"{BASE}/tenant/overview",
        f"{BASE}/resources",
        f"{BASE}/firewalls",
    ],
)
async def test_unauthenticated_is_401_not_404(path):
    async with _client(None) as client:
        response = await client.get(path)
    assert response.status_code == 401


async def test_missing_permission_is_403(tenant):
    async with _client(tenant.id, permissions=frozenset()) as client:
        response = await client.get(f"{BASE}/resources")
    assert response.status_code == 403


async def test_catalog_reports_the_canonical_vocabulary(tenant):
    async with _client(tenant.id) as client:
        response = await client.get(f"{BASE}/catalog")
    assert response.status_code == 200
    body = response.json()
    assert "interfaces" in [entry["capability"] for entry in body["capabilities"]]
    assert body["collection_states"]
    assert body["reasons"]
    assert body["resource_types"]


async def test_providers_report_wired_state_honestly(tenant):
    async with _client(tenant.id) as client:
        response = await client.get(f"{BASE}/providers")
    assert response.status_code == 200
    body = response.json()
    assert "prtg" in body["wired"]
    rows = {row["provider"]: row for row in body["providers"]}
    assert rows["aruba_central"]["wired"] is False


async def test_empty_tenant_overview_is_honest_not_flattering(tenant):
    async with _client(tenant.id) as client:
        response = await client.get(f"{BASE}/tenant/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["resources"]["total"] == 0
    assert body["interfaces"]["total"] == 0
    # No resources must NOT be reported as perfect health.
    assert body["health"]["health"] == "unknown"
    assert body["telemetry"]["newest_telemetry_at"] is None
    assert body["telemetry"]["quality"] == "unknown"


async def test_resource_list_validation_and_pagination(tenant):
    async with _client(tenant.id) as client:
        ok = await client.get(f"{BASE}/resources", params={"limit": 1})
        bad_family = await client.get(f"{BASE}/resources", params={"family": "nope"})
        bad_type = await client.get(
            f"{BASE}/resources", params={"resource_type": "not_a_type"}
        )
        too_big = await client.get(f"{BASE}/resources", params={"limit": 5000})
    assert ok.status_code == 200
    page = ok.json()
    assert page["limit"] == 1 and page["items"] == []
    assert bad_family.status_code == 422
    assert bad_type.status_code == 422
    # The ceiling is enforced by validation rather than silently clamped.
    assert too_big.status_code == 422


async def test_unknown_resource_is_404(tenant):
    async with _client(tenant.id) as client:
        response = await client.get(f"{BASE}/resources/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_other_tenant_resource_is_404_not_data(tenant, monitoring_source_factory):
    """The strongest isolation check: a REAL resource owned by another tenant."""
    from app.models.tenant import Tenant

    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    async with AsyncSessionLocal() as db:
        db.add(other)
        await db.commit()
        await db.refresh(other)

    other_source = await monitoring_source_factory(
        tenant_id=other.id, source_type="fortigate"
    )
    other_device_id = await _seed_device(other.id, other_source.id)

    async with _client(tenant.id) as client:
        detail = await client.get(f"{BASE}/resources/{other_device_id}")
        caps = await client.get(f"{BASE}/resources/{other_device_id}/capabilities")
        ifaces = await client.get(f"{BASE}/resources/{other_device_id}/interfaces")
        telemetry = await client.get(f"{BASE}/resources/{other_device_id}/telemetry")
        network = await client.get(f"{BASE}/resources/{other_device_id}/network")
        listing = await client.get(f"{BASE}/resources")

    for response in (detail, caps, ifaces, telemetry, network):
        assert response.status_code == 404, response.text
    assert all(
        item["resource_id"] != str(other_device_id) for item in listing.json()["items"]
    )

    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete

        await db.execute(delete(Tenant).where(Tenant.id == other.id))
        await db.commit()


async def test_resource_detail_capabilities_and_network(
    tenant, monitoring_source_factory
):
    source = await monitoring_source_factory(
        tenant_id=tenant.id, source_type="fortigate"
    )
    device_id = await _seed_device(tenant.id, source.id)

    async with _client(tenant.id) as client:
        detail = await client.get(f"{BASE}/resources/{device_id}")
        caps = await client.get(f"{BASE}/resources/{device_id}/capabilities")
        ifaces = await client.get(f"{BASE}/resources/{device_id}/interfaces")
        telemetry = await client.get(f"{BASE}/resources/{device_id}/telemetry")
        network = await client.get(f"{BASE}/resources/{device_id}/network")
        dependencies = await client.get(f"{BASE}/resources/{device_id}/dependencies")
        firewalls = await client.get(f"{BASE}/firewalls")

    assert detail.status_code == 200
    body = detail.json()
    assert body["resource"]["resource_type"] == "firewall"
    assert body["resource"]["provider"] == "fortigate"
    assert body["health"]["health"] in {
        "healthy", "warning", "critical", "unknown", "maintenance"
    }

    assert caps.status_code == 200
    capabilities = caps.json()
    assert capabilities["provider"] == "fortigate"
    assert all(entry["reason"] for entry in capabilities.get("unsupported", []))

    assert ifaces.status_code == 200
    interfaces = ifaces.json()
    assert interfaces["supported"] is False
    assert interfaces["reason"] == "primary_inventory_not_collected"

    assert telemetry.status_code == 200
    assert telemetry.json()["current"] == []
    assert telemetry.json()["quality"] == "unknown"

    assert network.status_code == 200
    network_body = network.json()
    assert network_body["wan"]["supported"] is False
    assert network_body["wan"]["reason"] == "primary_inventory_not_collected"
    assert network_body["sdwan"]["supported"] is False
    assert network_body["sdwan"]["reason"] == "provider_capability_not_supported"

    assert dependencies.json()["count"] == 0
    assert firewalls.status_code == 200
    assert firewalls.json()["resource_type"] == "firewall"
    assert any(
        item["resource_id"] == str(device_id) for item in firewalls.json()["items"]
    )


async def test_telemetry_rejects_an_unknown_capability(
    tenant, monitoring_source_factory
):
    source = await monitoring_source_factory(tenant_id=tenant.id, source_type="snmp")
    device_id = await _seed_device(tenant.id, source.id)
    async with _client(tenant.id) as client:
        response = await client.get(
            f"{BASE}/resources/{device_id}/telemetry",
            params={"capability": "not_a_capability"},
        )
    assert response.status_code == 422


async def test_correlation_context_requires_ids_and_stays_scoped(
    tenant, monitoring_source_factory
):
    source = await monitoring_source_factory(tenant_id=tenant.id, source_type="snmp")
    device_id = await _seed_device(tenant.id, source.id)

    async with _client(tenant.id) as client:
        empty = await client.post(
            f"{BASE}/correlation/context", json={"resource_ids": []}
        )
        result = await client.post(
            f"{BASE}/correlation/context", json={"resource_ids": [str(device_id)]}
        )

    assert empty.status_code == 422
    assert result.status_code == 200
    body = result.json()
    assert body["tenant_id"] == str(tenant.id)
    assert len(body["groups"]) == 1
    assert body["groups"][0]["root_cause_candidates"][0]["name"] == "branch-fw-01"
    # A single resource has no edges, and the API says so rather than inventing one.
    assert body["limitations"]


async def test_site_overview_for_an_unknown_site_is_404(tenant):
    async with _client(tenant.id) as client:
        missing = await client.get(f"{BASE}/sites/{uuid.uuid4()}/overview")
    assert missing.status_code == 404


async def test_correlation_context_includes_open_incident_and_stays_scoped(
    tenant, monitoring_source_factory
):
    """Regression: HsignceContextBuilder must call get_open_for_device() WITH
    tenant_id.

    Before the fix, POST /correlation/context for a device that has an OPEN
    incident raised ``TypeError: get_open_for_device() missing 1 required
    positional argument: 'tenant_id'`` (a 500). It now returns 200, includes the
    open incident as evidence, and never leaks another tenant's incident.
    """
    from datetime import datetime, timezone

    from app.models.incident import Incident
    from app.models.tenant import Tenant

    source = await monitoring_source_factory(tenant_id=tenant.id, source_type="snmp")
    device_id = await _seed_device(tenant.id, source.id)

    # Another tenant with a device + open incident, to prove isolation.
    async with AsyncSessionLocal() as db:
        other = Tenant(name=f"Other {uuid.uuid4().hex[:6]}", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
        await db.refresh(other)
    other_source = await monitoring_source_factory(tenant_id=other.id, source_type="snmp")
    other_device_id = await _seed_device(other.id, other_source.id)

    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        db.add(Incident(
            tenant_id=tenant.id,
            device_id=device_id,
            title="branch-fw-01 unreachable",
            severity="critical",
            status="open",
            opened_at=now,
            last_alert_at=now,
        ))
        db.add(Incident(
            tenant_id=other.id,
            device_id=other_device_id,
            title="other-tenant incident",
            severity="critical",
            status="open",
            opened_at=now,
            last_alert_at=now,
        ))
        await db.commit()

    try:
        async with _client(tenant.id) as client:
            result = await client.post(
                f"{BASE}/correlation/context", json={"resource_ids": [str(device_id)]}
            )

        assert result.status_code == 200
        body = result.json()
        evidence = [
            e
            for group in body.get("groups", [])
            for e in group.get("evidence", [])
        ]
        incident_evidence = [e for e in evidence if e.get("kind") == "incident"]
        assert incident_evidence, "open incident must appear as correlation evidence"
        # Isolation: only the caller's incident is returned, never the other's.
        assert all(e.get("resource_id") == str(device_id) for e in incident_evidence)
    finally:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import delete
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()

