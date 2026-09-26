"""Phase 9.9 - SNMP monitoring-source onboarding: credential + readiness contract.

`tests/test_monitoring_crud.py` already covers generic source CRUD, tenant
isolation and credential non-exposure. This module adds the SNMP-specific
onboarding contract that the operator workflow depends on:

* a created SNMP source really stores its community as Fernet ciphertext, and
  the stored bytes decrypt back to exactly what was sent (not merely "a field
  named encrypted exists");
* the community never appears in a create/list/get/patch response body, in a
  readiness result, or in a log line;
* an update that omits the credential PRESERVES the stored ciphertext, and an
  update that supplies one REPLACES it;
* `credential_configured` tells the UI whether a secret exists without ever
  returning it;
* the readiness taxonomy keeps a transport failure (unreachable/timeout) apart
  from an unsupported configuration, and readiness/sync are tenant-scoped and
  authenticated.

Real app, real database, real JWT (minted by the same helper the CRUD module
uses). No mocking of the security path.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.core.security import SecretCrypto
from app.main import app
from app.models.monitoring_source import MonitoringSource
from app.services.monitoring import readiness as R

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("seeded_subscription")]

#: A distinctive value that cannot occur by accident, so a leak is unambiguous.
COMMUNITY = "s3cret-community-9f2a7c"
ROTATED = "rotated-community-4b8d1e"


def _auth_headers(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="snmp-onboarding@example.com",
        is_superuser=True,
        permissions=frozenset(),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def _seed_sub_for(tenant_id: uuid.UUID) -> None:
    """Active plan+subscription for an ad-hoc tenant, so a cross-tenant request
    reaches ownership logic instead of the subscription gate."""
    from app.models.plan import Plan
    from app.models.subscription import Subscription
    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as db:
        if await db.get(Tenant, tenant_id) is None:
            db.add(
                Tenant(
                    id=tenant_id,
                    name=f"snmp-xt-{uuid.uuid4().hex[:8]}",
                    slug=f"snmp-xt-{uuid.uuid4().hex[:8]}",
                )
            )
            await db.commit()

    async with AsyncSessionLocal() as db:
        plan = Plan(
            name=f"SX-{uuid.uuid4().hex[:8]}",
            tier=f"sx-{uuid.uuid4().hex[:8]}",
            description=None,
            price_monthly=0,
            price_annual=0,
            device_limit=100,
            sensor_limit=1000,
            api_rate_limit=100000,
            collector_limit=10,
            seats=5,
            features_json={},
            is_public=False,
        )
        db.add(plan)
        await db.flush()
        now = datetime.now(timezone.utc)
        db.add(
            Subscription(
                tenant_id=tenant_id,
                plan_id=plan.id,
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                billing_interval="month",
            )
        )
        await db.commit()


def _snmp_payload(tenant_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "tenant_id": str(tenant_id),
        "name": f"snmp-sw-{uuid.uuid4().hex[:8]}",
        "source_type": "snmp",
        "hostname": "127.0.0.1",
        "port": 161,
        "protocol": "snmp",
        "snmp_version": "v2c",
        "api_key": COMMUNITY,       # the write-only community input
        "is_active": True,
        "poll_interval_seconds": 60,
    }
    payload.update(overrides)
    return payload


async def _stored(source_id: str) -> MonitoringSource | None:
    """The committed row, read straight from the database."""
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(MonitoringSource).where(MonitoringSource.id == uuid.UUID(source_id))
            )
        ).scalar_one_or_none()


class TestSnmpCredentialAtRest:
    async def test_create_stores_the_community_as_ciphertext(self, client, tenant):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        assert created.status_code == 201, created.text
        source_id = created.json()["id"]

        row = await _stored(source_id)
        assert row is not None
        stored = row.api_key_encrypted
        # The stored value is real ciphertext, not the community itself.
        assert stored is not None
        assert stored != COMMUNITY
        assert COMMUNITY not in stored
        # Fernet tokens are recognisable, and the value round-trips.
        assert stored.startswith("gAAAAA")
        crypto = SecretCrypto()
        assert crypto.decrypt(stored) == COMMUNITY

    async def test_credential_configured_reports_storage_without_the_value(
        self, client, tenant
    ):
        with_credential = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        without = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id, api_key=None),
            headers=_auth_headers(tenant.id),
        )
        assert with_credential.status_code == 201 and without.status_code == 201

        body_with = with_credential.json()
        body_without = without.json()
        assert body_with["credential_configured"] is True
        assert body_without["credential_configured"] is False
        # A boolean is all the API says about the secret.
        assert "api_key" not in body_with and "api_key_encrypted" not in body_with
        assert COMMUNITY not in with_credential.text

    async def test_community_never_appears_in_a_response_body(self, client, tenant):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        source_id = created.json()["id"]
        headers = _auth_headers(tenant.id)

        for response in (
            created,
            await client.get(f"/api/monitoring/sources/{source_id}", headers=headers),
            await client.get("/api/monitoring/sources", headers=headers),
            await client.patch(
                f"/api/monitoring/sources/{source_id}",
                json={"name": f"renamed-{uuid.uuid4().hex[:6]}"},
                headers=headers,
            ),
        ):
            assert response.status_code in (200, 201), response.text
            assert COMMUNITY not in response.text
            assert "api_key_encrypted" not in response.text
            assert "password_encrypted" not in response.text

    async def test_update_without_a_credential_preserves_the_stored_secret(
        self, client, tenant
    ):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        source_id = created.json()["id"]
        before = (await _stored(source_id)).api_key_encrypted

        patched = await client.patch(
            f"/api/monitoring/sources/{source_id}",
            json={"hostname": "127.0.0.2", "port": 1161},
            headers=_auth_headers(tenant.id),
        )
        assert patched.status_code == 200, patched.text

        row = await _stored(source_id)
        assert row.hostname == "127.0.0.2" and row.port == 1161
        # Untouched credential material: byte-identical ciphertext.
        assert row.api_key_encrypted == before
        assert SecretCrypto().decrypt(row.api_key_encrypted) == COMMUNITY
        assert patched.json()["credential_configured"] is True

    async def test_update_with_a_credential_replaces_the_stored_secret(self, client, tenant):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        source_id = created.json()["id"]
        before = (await _stored(source_id)).api_key_encrypted

        patched = await client.patch(
            f"/api/monitoring/sources/{source_id}",
            json={"api_key": ROTATED},
            headers=_auth_headers(tenant.id),
        )
        assert patched.status_code == 200, patched.text
        assert ROTATED not in patched.text

        row = await _stored(source_id)
        assert row.api_key_encrypted != before
        assert SecretCrypto().decrypt(row.api_key_encrypted) == ROTATED

    async def test_the_collector_reads_the_community_it_was_given(self, client, tenant):
        """The stored secret is the one the existing SNMPCollector will use."""
        from app.collectors.snmp import SNMPCollector
        from app.core.security import decrypt_connection_info

        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        row = await _stored(created.json()["id"])
        connection_info = decrypt_connection_info(row)

        collector = SNMPCollector(connection_info)
        assert collector._snmp_version() == "v2c"
        assert collector._community() == COMMUNITY


class TestSnmpReadinessTaxonomy:
    async def test_unreachable_target_is_not_reported_as_unsupported(self, client, tenant):
        """A closed port is a TRANSPORT fact, never a capability statement."""
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id, port=1161),   # nothing listens here
            headers=_auth_headers(tenant.id),
        )
        source_id = created.json()["id"]

        result = await client.post(
            f"/api/monitoring/sources/{source_id}/readiness",
            headers=_auth_headers(tenant.id),
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["status"] != R.READINESS_UNSUPPORTED
        assert body["status"] in {R.READINESS_UNREACHABLE, R.READINESS_TIMEOUT}
        assert body["status"] != R.READINESS_READY
        assert body["protocol"].lower() == "snmp"

    async def test_a_bad_snmp_version_is_a_config_error(self, client, tenant):
        """The one case where 'unsupported' IS right: a version the collector
        does not implement - reported at the config stage, not as a network
        failure."""
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id, snmp_version="v9"),
            headers=_auth_headers(tenant.id),
        )
        assert created.status_code == 201, created.text

        result = await client.post(
            f"/api/monitoring/sources/{created.json()['id']}/readiness",
            headers=_auth_headers(tenant.id),
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["status"] == R.READINESS_UNSUPPORTED
        assert "v9" in body["message"]

    async def test_readiness_never_returns_the_community(self, client, tenant, caplog):
        caplog.set_level(logging.DEBUG)
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id, port=1161),
            headers=_auth_headers(tenant.id),
        )
        result = await client.post(
            f"/api/monitoring/sources/{created.json()['id']}/readiness",
            headers=_auth_headers(tenant.id),
        )
        assert result.status_code == 200
        assert COMMUNITY not in result.text
        assert COMMUNITY not in caplog.text
        # The safe details an operator sees carry no credential material either.
        assert "s3cret" not in result.text.lower()

    async def test_readiness_requires_authentication(self, client, tenant):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        response = await client.post(
            f"/api/monitoring/sources/{created.json()['id']}/readiness"
        )
        assert response.status_code == 401

    async def test_readiness_is_tenant_scoped(self, client, tenant):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        source_id = created.json()["id"]

        other_tenant = uuid.uuid4()
        await _seed_sub_for(other_tenant)
        response = await client.post(
            f"/api/monitoring/sources/{source_id}/readiness",
            headers=_auth_headers(other_tenant),
        )
        assert response.status_code == 404, response.text


class TestSnmpSync:
    async def test_sync_uses_the_existing_orchestrator_and_reports_honestly(
        self, client, tenant
    ):
        """Sync on an unreachable source must fail HONESTLY (no fabricated
        inventory) while the endpoint itself stays a normal 200 response."""
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id, port=1162),
            headers=_auth_headers(tenant.id),
        )
        source_id = created.json()["id"]

        result = await client.post(
            f"/api/monitoring/sources/{source_id}/sync",
            headers=_auth_headers(tenant.id),
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert set(body) >= {
            "source_id", "status", "sensors_created", "sensors_updated",
            "sensors_total", "synced_at", "error",
        }
        assert body["source_id"] == source_id
        assert COMMUNITY not in result.text

        # The source records what really happened, so the list view is truthful.
        row = await _stored(source_id)
        assert row.status in {"error", "pending", "connected"}
        assert row.last_sync_at is not None or row.last_error is not None

    async def test_sync_requires_authentication(self, client, tenant):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        response = await client.post(
            f"/api/monitoring/sources/{created.json()['id']}/sync"
        )
        assert response.status_code == 401

    async def test_sync_is_tenant_scoped(self, client, tenant):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        source_id = created.json()["id"]

        other_tenant = uuid.uuid4()
        await _seed_sub_for(other_tenant)
        response = await client.post(
            f"/api/monitoring/sources/{source_id}/sync",
            headers=_auth_headers(other_tenant),
        )
        assert response.status_code == 404, response.text

    async def test_cross_tenant_read_cannot_reveal_the_credential_state(
        self, client, tenant
    ):
        created = await client.post(
            "/api/monitoring/sources",
            json=_snmp_payload(tenant.id),
            headers=_auth_headers(tenant.id),
        )
        source_id = created.json()["id"]

        other_tenant = uuid.uuid4()
        await _seed_sub_for(other_tenant)
        response = await client.get(
            f"/api/monitoring/sources/{source_id}", headers=_auth_headers(other_tenant)
        )
        assert response.status_code == 404, response.text
        assert "credential_configured" not in response.text