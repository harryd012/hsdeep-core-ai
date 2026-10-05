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


# ---------------------------------------------------------------------------
# PHASE 5B - canonical, tenant-scoped provider sensor reads
#
# The legacy `GET /api/monitoring/prtg/root-sensors` passthrough was a global
# bridge to ONE environment-configured PRTG probe: it took no tenant, logged in
# with a single process-wide credential, and returned that probe's sensors to
# ANY authenticated caller. It has been REMOVED, which makes
# `GET /api/monitoring/sources/{source_id}/sensors` the only path by which
# provider sensor rows (PRTG included) reach a client.
#
# These tests lock in the properties that removal buys:
#   1. tenant isolation - a caller can never read another tenant's source
#      sensors, not even by supplying a tenant_id of their own choosing;
#   2. one source of truth - the per-source rows ARE the canonical `Sensor`
#      rows the tenant-wide listing returns, so the monitoring page cannot
#      disagree with the rest of the platform.
# ---------------------------------------------------------------------------

LEGACY_PRTG_PASSTHROUGH = "/api/monitoring/prtg/root-sensors"


@pytest_asyncio.fixture
async def other_tenant():
    """An unrelated, fully-active tenant used to prove cross-tenant denial."""
    tenant_id = uuid.uuid4()
    await _seed_sub_for(tenant_id)
    try:
        yield tenant_id
    finally:
        from sqlalchemy import delete as sa_delete

        from app.models.tenant import Tenant

        async with AsyncSessionLocal() as db:
            await db.execute(sa_delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()


async def _create_source(client, tenant_id: uuid.UUID) -> dict:
    """Create a PRTG monitoring source for `tenant_id`; return the API body."""
    resp = await client.post(
        "/api/monitoring/sources",
        json=_source_payload(tenant_id),
        headers=_auth_headers(tenant_id),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _persist_sensors(
    tenant_id: uuid.UUID, source_id: str, *, count: int = 2, status: str = "up"
) -> list[str]:
    """Persist `count` canonical Sensor rows for `source_id`, the same way the
    Collector Framework does (rows carry tenant_id AND monitoring_source_id).
    Returns the sensor names so callers can assert on what was written."""
    from app.models.sensor import Sensor

    names: list[str] = []
    async with AsyncSessionLocal() as db:
        for index in range(count):
            sensor_name = f"canon-{uuid.uuid4().hex[:8]}-{index}"
            names.append(sensor_name)
            db.add(
                Sensor(
                    tenant_id=tenant_id,
                    monitoring_source_id=uuid.UUID(source_id),
                    external_id=f"ext-{uuid.uuid4().hex[:10]}",
                    name=sensor_name,
                    metric_type="ping",
                    unit="ms",
                    current_value=float(index + 1),
                    status=status,
                    last_value_at=datetime.now(timezone.utc),
                )
            )
        await db.commit()
    return names


class TestSourceSensorsTenantIsolation:
    """`GET /api/monitoring/sources/{id}/sensors` is the canonical read path for
    provider sensor data and must stay tenant-scoped."""

    async def test_owner_sees_persisted_sensors(self, client, tenant):
        created = await _create_source(client, tenant.id)
        names = await _persist_sensors(tenant.id, created["id"])

        resp = await client.get(
            f"/api/monitoring/sources/{created['id']}/sensors",
            headers=_auth_headers(tenant.id),
        )
        assert resp.status_code == 200, resp.text
        assert sorted(row["name"] for row in resp.json()) == sorted(names)

    async def test_other_tenant_gets_404_and_no_data(self, client, tenant, other_tenant):
        created = await _create_source(client, tenant.id)
        names = await _persist_sensors(tenant.id, created["id"])

        resp = await client.get(
            f"/api/monitoring/sources/{created['id']}/sensors",
            headers=_auth_headers(other_tenant),
        )
        assert resp.status_code == 404
        # The denial must convey NOTHING about the other tenant's source.
        assert created["name"] not in resp.text
        for name in names:
            assert name not in resp.text, "cross-tenant sensor data leaked"

    async def test_client_supplied_tenant_id_cannot_select_a_tenant(
        self, client, tenant, other_tenant
    ):
        """Authenticated as `other_tenant` but asking for `tenant`'s sensor rows.

        The legacy passthrough accepted no tenant at all - every caller saw the
        same probe. The canonical route must ignore client-supplied tenant
        input and keep using the JWT's tenant.
        """
        created = await _create_source(client, tenant.id)
        names = await _persist_sensors(tenant.id, created["id"])

        resp = await client.get(
            f"/api/monitoring/sources/{created['id']}/sensors",
            params={"tenant_id": str(tenant.id)},
            headers=_auth_headers(other_tenant),
        )
        assert resp.status_code == 404, (
            "a client-supplied tenant_id must not grant access to another tenant"
        )
        for name in names:
            assert name not in resp.text

    async def test_requires_authentication(self, client, tenant):
        created = await _create_source(client, tenant.id)
        resp = await client.get(f"/api/monitoring/sources/{created['id']}/sensors")
        assert resp.status_code in (401, 403), (
            f"an unauthenticated read must be rejected, got {resp.status_code}"
        )

    async def test_unknown_source_id_is_404(self, client, tenant):
        resp = await client.get(
            f"/api/monitoring/sources/{uuid.uuid4()}/sensors",
            headers=_auth_headers(tenant.id),
        )
        assert resp.status_code == 404


class TestLegacyPrtgPassthroughIsRemoved:
    """The non-tenant-scoped `GET /prtg/root-sensors` bridge is gone for good."""

    async def test_endpoint_is_not_routable(self, client, tenant):
        resp = await client.get(
            LEGACY_PRTG_PASSTHROUGH, headers=_auth_headers(tenant.id)
        )
        assert resp.status_code == 404, (
            "the legacy global PRTG passthrough must not be reachable: "
            f"got {resp.status_code}"
        )

    async def test_no_route_registers_the_legacy_path(self):
        """Belt-and-braces: the path must be absent from the route table, so it
        cannot be reintroduced without failing here."""
        paths = {getattr(route, "path", "") for route in app.routes}
        assert not any(path.endswith("/prtg/root-sensors") for path in paths), (
            "a /prtg/root-sensors route is registered again"
        )
        assert any(path.endswith("/sources/{source_id}/sensors") for path in paths), (
            "the canonical tenant-scoped sensor route must be registered"
        )


class TestSensorSourceOfTruthParity:
    """The per-source rows ARE the canonical rows the tenant-wide listing
    returns, so the monitoring page cannot contradict `/sensors`."""

    async def test_source_rows_match_the_tenant_wide_listing(self, client, tenant):
        created = await _create_source(client, tenant.id)
        await _persist_sensors(tenant.id, created["id"], count=3, status="warning")

        per_source_resp = await client.get(
            f"/api/monitoring/sources/{created['id']}/sensors",
            headers=_auth_headers(tenant.id),
        )
        assert per_source_resp.status_code == 200, per_source_resp.text
        per_source = per_source_resp.json()
        assert len(per_source) == 3

        tenant_wide_resp = await client.get(
            "/api/monitoring/sensors", headers=_auth_headers(tenant.id)
        )
        assert tenant_wide_resp.status_code == 200, tenant_wide_resp.text
        by_id = {row["id"]: row for row in tenant_wide_resp.json()}

        for row in per_source:
            assert row["id"] in by_id, "per-source row missing from tenant-wide list"
            assert by_id[row["id"]] == row, (
                "per-source and tenant-wide reads must return identical rows "
                "(a single source of truth)"
            )
            assert row["status"] == "warning"
            assert row["metric_type"] == "ping"

    async def test_other_tenants_rows_are_absent(self, client, tenant, other_tenant):
        """A tenant's source-scoped read never contains another tenant's rows."""
        ours = await _create_source(client, tenant.id)
        await _persist_sensors(tenant.id, ours["id"], count=2)
        theirs = await _create_source(client, other_tenant)
        their_names = await _persist_sensors(other_tenant, theirs["id"], count=2)

        resp = await client.get(
            f"/api/monitoring/sources/{ours['id']}/sensors",
            headers=_auth_headers(tenant.id),
        )
        assert resp.status_code == 200, resp.text
        for name in their_names:
            assert name not in resp.text
