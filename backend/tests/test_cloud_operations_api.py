"""Cloud Operations KPI endpoint tests (integration, DB-free).

Exercises ``GET /api/dashboard/cloud-operations`` over the real ASGI app with a
minted JWT (same convention as ``test_agent_api.py``). The service dependency
is swapped with fake repositories through FastAPI's built-in
``app.dependency_overrides`` so the endpoint's contract is verified without a
live database:

* the route is registered and reachable at /api/dashboard/cloud-operations;
* tenant identity comes from the authenticated principal (JWT), never a query
  param - the fake repos record the tenant_id they receive;
* the response serializes through the ``CloudOperations`` response_model;
* genuine zeros and not-configured cards survive the wire;
* a repository failure surfaces as a 5xx, never a fabricated card.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.routes.dashboard import get_cloud_operations_service
from app.core.auth import create_access_token
from app.main import app
from app.services.cloud_operations_service import CloudOperationsService

pytestmark = pytest.mark.asyncio

TENANT_ID = uuid.uuid4()


def _auth_headers(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="test@hsdeep.local",
        is_superuser=True,
        permissions=frozenset(),
    )
    return {"Authorization": f"Bearer {token}"}


class _RecordingDeviceRepo:
    def __init__(self, cloud_resource_count: int = 0, states: dict[str, int] | None = None):
        self.cloud_resource_count = cloud_resource_count
        self.states = states or {
            "up": 0,
            "down": 0,
            "stale": 0,
            "unknown": 0,
            "total": 0,
        }
        self.seen_tenant_ids: list[uuid.UUID] = []

    async def count(self, tenant_id, *, device_type=None, **kwargs) -> int:
        self.seen_tenant_ids.append(tenant_id)
        return self.cloud_resource_count if device_type == "cloud_resource" else 0

    async def counts_by_state(self, tenant_id, *, now, freshness_seconds) -> dict[str, int]:
        self.seen_tenant_ids.append(tenant_id)
        return dict(self.states)


class _RecordingIncidentRepo:
    def __init__(self, active_total: int = 0, critical_active: int = 0):
        self.active_total = active_total
        self.critical_active = critical_active
        self.seen_tenant_ids: list[uuid.UUID] = []
        self.seen_statuses: list[str] | None = None

    async def active_counts(self, tenant_id, *, statuses) -> tuple[int, int]:
        self.seen_tenant_ids.append(tenant_id)
        self.seen_statuses = list(statuses)
        return (self.active_total, self.critical_active)


def _client(raise_app_exceptions: bool = True) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


@pytest_asyncio.fixture
async def client():
    async with _client() as ac:
        yield ac


def _override_service(svc: CloudOperationsService) -> None:
    app.dependency_overrides[get_cloud_operations_service] = lambda: svc


def _clear_override() -> None:
    app.dependency_overrides.pop(get_cloud_operations_service, None)


class TestCloudOperationsEndpoint:
    async def test_returns_200_with_six_cards_and_real_serialization(self, client):
        device_repo = _RecordingDeviceRepo(
            cloud_resource_count=2,
            states={"up": 5, "down": 1, "stale": 3, "unknown": 1, "total": 10},
        )
        incident_repo = _RecordingIncidentRepo(active_total=3, critical_active=1)
        _override_service(
            CloudOperationsService(device_repo=device_repo, incident_repo=incident_repo)
        )
        try:
            resp = await client.get(
                "/api/dashboard/cloud-operations", headers=_auth_headers(TENANT_ID)
            )
        finally:
            _clear_override()

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "cloud_resources",
            "resource_health",
            "active_incidents",
            "cloud_spend",
            "policy_compliance",
            "backup_readiness",
        }
        assert body["cloud_resources"]["value"] == 2
        assert body["cloud_resources"]["unavailable"] is False
        assert body["resource_health"]["value"] == {
            "online": 5,
            "offline": 1,
            "stale": 3,
            "unknown": 1,
            "total": 10,
        }
        assert body["active_incidents"]["value"] == 3
        assert body["active_incidents"]["detail"] == {"total": 3, "critical": 1}
        for key in ("cloud_spend", "policy_compliance", "backup_readiness"):
            assert body[key]["unavailable"] is True
            assert body[key]["value"] is None
            assert body[key]["unavailable_reason"]

    async def test_tenant_identity_comes_from_principal_not_query(self, client):
        device_repo = _RecordingDeviceRepo(cloud_resource_count=1)
        incident_repo = _RecordingIncidentRepo(active_total=1)
        _override_service(
            CloudOperationsService(device_repo=device_repo, incident_repo=incident_repo)
        )
        try:
            # No ?tenant_id= query param is sent; the tenant comes from the JWT.
            resp = await client.get(
                "/api/dashboard/cloud-operations", headers=_auth_headers(TENANT_ID)
            )
        finally:
            _clear_override()

        assert resp.status_code == 200
        assert device_repo.seen_tenant_ids == [TENANT_ID, TENANT_ID]  # count + counts_by_state
        assert incident_repo.seen_tenant_ids == [TENANT_ID]
        assert incident_repo.seen_statuses == ["open", "acknowledged", "investigating"]

    async def test_genuine_zero_is_serialized_as_zero(self, client):
        _override_service(
            CloudOperationsService(
                device_repo=_RecordingDeviceRepo(cloud_resource_count=0),
                incident_repo=_RecordingIncidentRepo(active_total=0),
            )
        )
        try:
            resp = await client.get(
                "/api/dashboard/cloud-operations", headers=_auth_headers(TENANT_ID)
            )
        finally:
            _clear_override()

        assert resp.status_code == 200
        body = resp.json()
        assert body["cloud_resources"]["value"] == 0
        assert body["cloud_resources"]["unavailable"] is False
        assert body["active_incidents"]["value"] == 0
        assert body["active_incidents"]["unavailable"] is False

    async def test_repository_failure_surfaces_as_500(self):
        class _FailingDeviceRepo(_RecordingDeviceRepo):
            async def count(self, tenant_id, **kwargs):
                raise RuntimeError("db connection lost")

        _override_service(
            CloudOperationsService(
                device_repo=_FailingDeviceRepo(),
                incident_repo=_RecordingIncidentRepo(),
            )
        )
        try:
            async with _client(raise_app_exceptions=False) as client:
                resp = await client.get(
                    "/api/dashboard/cloud-operations", headers=_auth_headers(TENANT_ID)
                )
        finally:
            _clear_override()

        assert resp.status_code == 500
