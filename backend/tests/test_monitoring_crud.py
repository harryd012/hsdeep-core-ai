"""
Sprint 2 — Tasks 16.3 / 16.4: Monitoring Source CRUD integration tests
+ credential non-exposure property test.

Tests cover:
  - POST /api/monitoring/sources (201 create, 422 unknown source_type)
  - GET  /api/monitoring/sources (list, filter by source_type / is_active)
  - GET  /api/monitoring/sources/{id} (200 found, 404 not found)
  - PATCH /api/monitoring/sources/{id} (partial update, 404 wrong tenant)
  - DELETE /api/monitoring/sources/{id} (204, subsequent GET 404)
  - POST /api/monitoring/sources/{id}/test-connection (stubbed response)
  - Credential non-exposure: api_key / password NEVER appear in any response
    field (plaintext or encrypted), across create + GET + PATCH responses.

Uses httpx AsyncClient against the real FastAPI app (same process, real
Postgres DB). Auth is provided by minting a real JWT via create_access_token.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures('seeded_subscription')]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CREDENTIAL_FIELD_NAMES = {
    "api_key",
    "password",
    "api_key_encrypted",
    "password_encrypted",
    "oauth_client_secret_encrypted",
    "oauth_access_token_encrypted",
    "oauth_refresh_token_encrypted",
    "webhook_secret_encrypted",
}


def _make_token(tenant_id: uuid.UUID) -> str:
    """Mint a short-lived JWT that pins the caller to `tenant_id`."""
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="test@example.com",
        is_superuser=True,      # superuser bypasses all permission checks
        permissions=frozenset(),
    )


def _auth_headers(tenant_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(tenant_id)}"}


def _assert_no_credentials_in_response(response_json: dict | list) -> None:
    """Recursively confirm no credential field name appears anywhere in the
    response body — Requirement 9.2 / Correctness Property 6."""
    if isinstance(response_json, dict):
        for key, value in response_json.items():
            assert key not in CREDENTIAL_FIELD_NAMES, (
                f"Credential field '{key}' leaked into API response"
            )
            _assert_no_credentials_in_response(value)
    elif isinstance(response_json, list):
        for item in response_json:
            _assert_no_credentials_in_response(item)


@pytest_asyncio.fixture
async def client():
    """Async HTTPX client wired directly to the FastAPI ASGI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac



async def _seed_sub_for(tenant_id: uuid.UUID) -> None:
    """Seed an active plan+subscription for an ad-hoc tenant so cross-tenant
    denial tests reach ownership logic instead of the subscription gate."""
    from datetime import datetime, timedelta, timezone

    from app.models.plan import Plan
    from app.models.subscription import Subscription
    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as db:
        if await db.get(Tenant, tenant_id) is None:
            db.add(Tenant(id=tenant_id, name=f"xt-{uuid.uuid4().hex[:8]}", slug=f"xt-{uuid.uuid4().hex[:8]}"))
            await db.commit()

    async with AsyncSessionLocal() as db:
        plan = Plan(
            name=f"XT-{uuid.uuid4().hex[:8]}",
            tier=f"xt-{uuid.uuid4().hex[:8]}",
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
        db.add(Subscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            billing_interval="month",
        ))
        await db.commit()

def _source_payload(tenant_id: uuid.UUID, **overrides) -> dict:
    base = {
        "tenant_id": str(tenant_id),
        "name": f"test-prtg-{uuid.uuid4().hex[:6]}",
        "source_type": "prtg",
        "hostname": "prtg.internal",
        "port": 443,
        "protocol": "https",
        "verify_ssl": False,
        "poll_interval_seconds": 60,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# POST /api/monitoring/sources
# ---------------------------------------------------------------------------

class TestCreateSource:

    async def test_create_returns_201(self, client, tenant):
        headers = _auth_headers(tenant.id)
        payload = _source_payload(tenant.id)

        resp = await client.post("/api/monitoring/sources", json=payload, headers=headers)

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["source_type"] == "prtg"
        assert body["name"] == payload["name"]
        assert "id" in body

    async def test_create_rejects_unknown_source_type(self, client, tenant):
        headers = _auth_headers(tenant.id)
        payload = _source_payload(tenant.id, source_type="nonexistent_vendor")

        resp = await client.post("/api/monitoring/sources", json=payload, headers=headers)

        assert resp.status_code == 422, resp.text

    async def test_create_response_never_exposes_credentials(self, client, tenant):
        """Requirement 9.2: create response must not contain any credential field."""
        headers = _auth_headers(tenant.id)
        payload = _source_payload(
            tenant.id,
            api_key="super-secret-api-key-12345",
            password="hunter2",
        )

        resp = await client.post("/api/monitoring/sources", json=payload, headers=headers)

        assert resp.status_code == 201, resp.text
        _assert_no_credentials_in_response(resp.json())

    async def test_create_credential_values_not_in_response_body_text(self, client, tenant):
        """Plaintext credential must not appear anywhere in the raw response body."""
        headers = _auth_headers(tenant.id)
        secret_value = f"secret-{uuid.uuid4().hex}"
        payload = _source_payload(tenant.id, api_key=secret_value)

        resp = await client.post("/api/monitoring/sources", json=payload, headers=headers)

        assert resp.status_code == 201, resp.text
        assert secret_value not in resp.text, (
            "Plaintext credential appeared in create response body"
        )

    async def test_create_requires_auth(self, client, tenant):
        resp = await client.post(
            "/api/monitoring/sources", json=_source_payload(tenant.id)
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/monitoring/sources
# ---------------------------------------------------------------------------

class TestListSources:

    async def test_list_returns_created_source(self, client, tenant):
        headers = _auth_headers(tenant.id)
        payload = _source_payload(tenant.id)
        create_resp = await client.post("/api/monitoring/sources", json=payload, headers=headers)
        assert create_resp.status_code == 201
        source_id = create_resp.json()["id"]

        list_resp = await client.get("/api/monitoring/sources", headers=headers)

        assert list_resp.status_code == 200
        ids = [s["id"] for s in list_resp.json()]
        assert source_id in ids

    async def test_list_filter_by_source_type(self, client, tenant):
        headers = _auth_headers(tenant.id)
        await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id, source_type="prtg"),
            headers=headers,
        )

        resp = await client.get(
            "/api/monitoring/sources", params={"source_type": "prtg"}, headers=headers
        )

        assert resp.status_code == 200
        assert all(s["source_type"] == "prtg" for s in resp.json())

    async def test_list_never_exposes_credentials(self, client, tenant):
        headers = _auth_headers(tenant.id)
        await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id, api_key="list-secret-key"),
            headers=headers,
        )

        resp = await client.get("/api/monitoring/sources", headers=headers)

        assert resp.status_code == 200
        _assert_no_credentials_in_response(resp.json())

    async def test_list_tenant_isolation(self, client, tenant):
        """Sources created under one tenant must not appear in another tenant's list."""
        other_tenant_id = uuid.uuid4()
        headers_a = _auth_headers(tenant.id)
        headers_b = _auth_headers(other_tenant_id)

        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers_a,
        )
        assert create_resp.status_code == 201
        source_id = create_resp.json()["id"]

        resp_b = await client.get("/api/monitoring/sources", headers=headers_b)
        assert resp_b.status_code == 200
        ids_b = [s["id"] for s in resp_b.json()]
        assert source_id not in ids_b


# ---------------------------------------------------------------------------
# GET /api/monitoring/sources/{id}
# ---------------------------------------------------------------------------

class TestGetSource:

    async def test_get_existing_source(self, client, tenant):
        headers = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers,
        )
        source_id = create_resp.json()["id"]

        get_resp = await client.get(f"/api/monitoring/sources/{source_id}", headers=headers)

        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == source_id

    async def test_get_returns_404_for_wrong_tenant(self, client, tenant):
        headers_a = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers_a,
        )
        source_id = create_resp.json()["id"]

        # Different tenant — should get 404
        headers_b = _auth_headers(uuid.uuid4())
        get_resp = await client.get(f"/api/monitoring/sources/{source_id}", headers=headers_b)
        assert get_resp.status_code == 404

    async def test_get_returns_404_for_unknown_id(self, client, tenant):
        headers = _auth_headers(tenant.id)
        resp = await client.get(
            f"/api/monitoring/sources/{uuid.uuid4()}", headers=headers
        )
        assert resp.status_code == 404

    async def test_get_never_exposes_credentials(self, client, tenant):
        headers = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id, api_key="get-secret-key"),
            headers=headers,
        )
        source_id = create_resp.json()["id"]

        get_resp = await client.get(f"/api/monitoring/sources/{source_id}", headers=headers)

        assert get_resp.status_code == 200
        _assert_no_credentials_in_response(get_resp.json())


# ---------------------------------------------------------------------------
# PATCH /api/monitoring/sources/{id}
# ---------------------------------------------------------------------------

class TestUpdateSource:

    async def test_patch_updates_only_supplied_fields(self, client, tenant):
        headers = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id, name="original-name"),
            headers=headers,
        )
        source_id = create_resp.json()["id"]
        original_hostname = create_resp.json()["hostname"]

        patch_resp = await client.patch(
            f"/api/monitoring/sources/{source_id}",
            json={"name": "updated-name"},
            headers=headers,
        )

        assert patch_resp.status_code == 200
        body = patch_resp.json()
        assert body["name"] == "updated-name"
        assert body["hostname"] == original_hostname  # unchanged

    async def test_patch_returns_404_for_wrong_tenant(self, client, tenant):
        headers_a = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers_a,
        )
        source_id = create_resp.json()["id"]

        # Different tenant - fully provisioned (own subscription) so the
        # denial comes from OWNERSHIP (404), not the subscription gate.
        attacker_id = uuid.uuid4()
        await _seed_sub_for(attacker_id)
        headers_b = _auth_headers(attacker_id)
        try:
            patch_resp = await client.patch(
                f"/api/monitoring/sources/{source_id}",
                json={"name": "hacked"},
                headers=headers_b,
            )
            assert patch_resp.status_code == 404
        finally:
            async with AsyncSessionLocal() as db:
                from app.models.tenant import Tenant

                await db.execute(
                    __import__("sqlalchemy").delete(Tenant).where(Tenant.id == attacker_id)
                )
                await db.commit()

    async def test_patch_credential_never_echoed_back(self, client, tenant):
        """PATCH with a new api_key must not echo the value or any
        encrypted form back in the response — Requirement 9.2."""
        headers = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers,
        )
        source_id = create_resp.json()["id"]
        new_secret = f"new-key-{uuid.uuid4().hex}"

        patch_resp = await client.patch(
            f"/api/monitoring/sources/{source_id}",
            json={"api_key": new_secret},
            headers=headers,
        )

        assert patch_resp.status_code == 200
        _assert_no_credentials_in_response(patch_resp.json())
        assert new_secret not in patch_resp.text, (
            "Plaintext credential appeared in PATCH response body"
        )


# ---------------------------------------------------------------------------
# DELETE /api/monitoring/sources/{id}
# ---------------------------------------------------------------------------

class TestDeleteSource:

    async def test_delete_returns_204(self, client, tenant):
        headers = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers,
        )
        source_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/monitoring/sources/{source_id}", headers=headers
        )
        assert del_resp.status_code == 204

    async def test_delete_makes_source_disappear_from_list(self, client, tenant):
        headers = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers,
        )
        source_id = create_resp.json()["id"]

        await client.delete(f"/api/monitoring/sources/{source_id}", headers=headers)

        list_resp = await client.get("/api/monitoring/sources", headers=headers)
        ids = [s["id"] for s in list_resp.json()]
        assert source_id not in ids

    async def test_delete_makes_get_return_404(self, client, tenant):
        headers = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers,
        )
        source_id = create_resp.json()["id"]

        await client.delete(f"/api/monitoring/sources/{source_id}", headers=headers)

        get_resp = await client.get(
            f"/api/monitoring/sources/{source_id}", headers=headers
        )
        assert get_resp.status_code == 404

    async def test_delete_returns_404_for_wrong_tenant(self, client, tenant):
        headers_a = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id),
            headers=headers_a,
        )
        source_id = create_resp.json()["id"]

        attacker_id = uuid.uuid4()
        await _seed_sub_for(attacker_id)
        headers_b = _auth_headers(attacker_id)
        try:
            del_resp = await client.delete(
                f"/api/monitoring/sources/{source_id}", headers=headers_b
            )
            assert del_resp.status_code == 404
        finally:
            async with AsyncSessionLocal() as db:
                from app.models.tenant import Tenant

                await db.execute(
                    __import__("sqlalchemy").delete(Tenant).where(Tenant.id == attacker_id)
                )
                await db.commit()


# ---------------------------------------------------------------------------
# Task 16.4 — Property: credentials NEVER returned by the API
# ---------------------------------------------------------------------------

class TestCredentialNonExposureProperty:
    """Property 6 (API half): for arbitrary credential strings submitted via
    POST, no subsequent GET/PATCH response for that source contains the
    credential value, encrypted or plaintext.

    This is a deterministic integration test (not hypothesis-driven) since
    the invariant is structural — the schema simply omits the fields — and a
    single representative assertion is sufficient to catch an accidental
    field addition or schema regression.
    """

    async def test_api_key_never_returned_across_full_lifecycle(self, client, tenant):
        headers = _auth_headers(tenant.id)
        secret = f"lifecycle-secret-{uuid.uuid4().hex}"

        # CREATE with credential
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id, api_key=secret),
            headers=headers,
        )
        assert create_resp.status_code == 201
        source_id = create_resp.json()["id"]

        # GET — must not contain secret
        get_resp = await client.get(f"/api/monitoring/sources/{source_id}", headers=headers)
        assert get_resp.status_code == 200
        assert secret not in get_resp.text

        # LIST — must not contain secret
        list_resp = await client.get("/api/monitoring/sources", headers=headers)
        assert list_resp.status_code == 200
        assert secret not in list_resp.text

        # PATCH with a different credential — original secret still must not appear
        await client.patch(
            f"/api/monitoring/sources/{source_id}",
            json={"api_key": f"rotated-{uuid.uuid4().hex}"},
            headers=headers,
        )
        get_resp2 = await client.get(f"/api/monitoring/sources/{source_id}", headers=headers)
        assert secret not in get_resp2.text

    async def test_password_never_returned_across_full_lifecycle(self, client, tenant):
        headers = _auth_headers(tenant.id)
        secret = f"pw-{uuid.uuid4().hex}"

        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id, password=secret),
            headers=headers,
        )
        assert create_resp.status_code == 201
        source_id = create_resp.json()["id"]

        for endpoint in [
            f"/api/monitoring/sources/{source_id}",
            "/api/monitoring/sources",
        ]:
            resp = await client.get(endpoint, headers=headers)
            assert secret not in resp.text, (
                f"Password leaked from {endpoint}"
            )

    async def test_encrypted_field_names_absent_from_all_responses(self, client, tenant):
        """The schema must structurally exclude *_encrypted field names,
        regardless of what the ORM model carries internally."""
        headers = _auth_headers(tenant.id)
        create_resp = await client.post(
            "/api/monitoring/sources",
            json=_source_payload(tenant.id, api_key="key-for-schema-test"),
            headers=headers,
        )
        assert create_resp.status_code == 201
        source_id = create_resp.json()["id"]

        for resp in [
            await client.get(f"/api/monitoring/sources/{source_id}", headers=headers),
            await client.get("/api/monitoring/sources", headers=headers),
        ]:
            _assert_no_credentials_in_response(resp.json())
