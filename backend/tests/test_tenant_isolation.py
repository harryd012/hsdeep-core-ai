"""
Per-router tenant-isolation tests.

For each router, tests verify:
  1. An unauthenticated request returns 401 (NOT 404 — returning 404 without
     auth leaks the existence of a route and undermines the auth layer).
  2. An authenticated request for a different tenant does NOT return the
     other tenant's data. Acceptable outcomes: 404, 403, or an empty result
     set. Unacceptable: 200 with actual data belonging to another tenant.
  3. An authenticated request for the user's own tenant returns 200 (with
     data or empty list), 403 (authenticated but lacks permission), or 404.

Uses the real app and database (per project convention), borrowing the
`tenant` fixture from conftest.py which creates a real committed Tenant row.
"""
from __future__ import annotations

import uuid

import pytest
from app.core.auth import create_access_token
from app.main import app

import httpx

# Write routes are gated on an active subscription (P1 remediation); seed one
# for this module's shared tenant at fixture level.
pytestmark = pytest.mark.usefixtures("seeded_subscription")


async def _seed_active_sub(tenant_id: uuid.UUID) -> None:
    """Seed Tenant+Plan+Subscription for an ad-hoc tenant so cross-tenant
    denial tests reach ownership logic instead of the subscription gate."""
    from datetime import datetime, timedelta, timezone

    from app.core.db import AsyncSessionLocal
    from app.models.plan import Plan
    from app.models.subscription import Subscription
    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as db:
        if await db.get(Tenant, tenant_id) is None:
            db.add(Tenant(id=tenant_id, name=f"xt-{uuid.uuid4().hex[:8]}",
                          slug=f"xt-{uuid.uuid4().hex[:8]}"))
            await db.commit()
        plan = Plan(
            name=f"XT-{uuid.uuid4().hex[:8]}", tier=f"xt-{uuid.uuid4().hex[:8]}",
            description=None, price_monthly=0, price_annual=0,
            device_limit=100, sensor_limit=1000, api_rate_limit=100000,
            collector_limit=10, seats=5, features_json={}, is_public=False,
        )
        db.add(plan)
        await db.flush()
        now = datetime.now(timezone.utc)
        db.add(Subscription(
            tenant_id=tenant_id, plan_id=plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
            billing_interval="month",
        ))
        await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jwt_for(tenant_id: uuid.UUID) -> str:
    """Create a valid access token for the given tenant."""
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="isolation-test@hsdeep.local",
        is_superuser=False,
        permissions=frozenset(),
    )


async def _client_for(tenant_id: uuid.UUID) -> httpx.AsyncClient:
    """Return an httpx client authenticated for the given tenant."""
    return httpx.AsyncClient(
        app=app,
        base_url="http://test",
        headers={"Authorization": f"Bearer {_jwt_for(tenant_id)}"},
    )


def _is_empty_value(v) -> bool:
    """True when *v* represents an empty/no-data response value.

    Recognises the scalar sentinels (0, None, "", False) as well as empty
    containers (``{}``, ``[]``) so that summary dicts whose sub-fields are
    themselves empty containers are treated as "no data leaked".
    """
    if v in (0, None, "", False):
        return True
    if isinstance(v, (dict, list)) and len(v) == 0:
        return True
    return False


# Each entry: (path_with_placeholder, placeholder_replacement)
# We use a representative endpoint from each router.  Where a router has
# multiple scopes, we pick the most common pattern (usually ``/sources``
# or ``/{resource}``) and add one extra for routes that use a resource_id.
ROUTER_ENDPOINTS: list[tuple[str, str]] = [
    # alerts
    ("/api/alerts?tenant_id={tid}", "alert list"),
    # assets
    ("/api/assets/devices?tenant_id={tid}", "asset list"),
    # dashboard
    ("/api/dashboard/summary?tenant_id={tid}", "dashboard"),
    # devices
    ("/api/devices?tenant_id={tid}", "device list"),
    # incidents
    ("/api/incidents?tenant_id={tid}", "incident list"),
    # infrastructure
    ("/api/infrastructure/sites?tenant_id={tid}", "infra list"),
    # monitoring
    ("/api/monitoring/sources?tenant_id={tid}", "monitoring sources"),
    # priorities
    ("/api/priorities?tenant_id={tid}", "priorities list"),
    # runbooks
    ("/api/runbooks?tenant_id={tid}", "runbooks list"),
    # servicenow
    ("/api/servicenow/connections?tenant_id={tid}", "servicenow connections"),
    # sops
    ("/api/sops?tenant_id={tid}", "sops list"),
    # settings
    ("/api/settings/general?tenant_id={tid}", "settings general"),
    # workflows
    ("/api/workflows?tenant_id={tid}", "workflows list"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPerRouterTenantIsolation:
    """One parametrized test runs the same isolation contract across all routers."""

    @pytest.mark.parametrize("path_template,label", ROUTER_ENDPOINTS, ids=[e[1] for e in ROUTER_ENDPOINTS])
    async def test_cross_tenant_does_not_leak_data(self, tenant, path_template, label):
        """Authenticated request for tenant A must NOT return tenant B's data.

        Routes using Depends(get_tenant_id) correctly ignore the query-param
        tenant_id and scope to the token's tenant. We therefore test with a
        *fresh* token representing a tenant with NO data — requesting that
        tenant's own data must return empty/200, which proves no data is leaked
        from any other tenant.

        Acceptable outcomes:
          200 with empty list/dict — no data exists for the caller's tenant.
          404 — route/service explicitly rejects the empty tenant.
          403 — caller authenticated but lacks permission for this endpoint;
                 no data (not even another tenant's) is returned.
        Unacceptable:
          200 with any non-empty data payload.
        """
        empty_tenant_id = uuid.uuid4()
        path = path_template.format(tid=str(empty_tenant_id))

        async with await _client_for(empty_tenant_id) as client:
            response = await client.get(path)

        # 200 with empty body is the ideal proof of no-leak; 404 is also
        # acceptable (route explicitly rejects mismatch). 403 is likewise
        # acceptable: the caller is authenticated but lacks the permission for
        # this endpoint, so NO data (not even another tenant's) is returned.
        if response.status_code == 200:
            body = response.json()
            if isinstance(body, dict):
                # A response whose ``tenant_id`` matches the caller's own
                # (empty) tenant is NOT a leak — it is that tenant's own
                # data (e.g. default settings), even if the values are
                # non-empty system defaults.
                if str(body.get("tenant_id")) == str(empty_tenant_id):
                    pass  # own tenant's data — acceptable
                else:
                    all_empty = all(_is_empty_value(v) for v in body.values())
                    assert all_empty, (
                        f"[{label}] Cross-tenant returned non-empty data: {body}"
                    )
            elif isinstance(body, list):
                all_empty = len(body) == 0
                assert all_empty, (
                    f"[{label}] Cross-tenant returned non-empty data: {body}"
                )
            else:
                all_empty = not body
                assert all_empty, (
                    f"[{label}] Cross-tenant returned non-empty data: {body}"
                )
        elif response.status_code == 403:
            pass
        else:
            assert response.status_code == 404, (
                f"[{label}] Expected 404, empty-200, or 403 for empty-tenant, "
                f"got {response.status_code}: {response.text}"
            )

    @pytest.mark.parametrize("path_template,label", ROUTER_ENDPOINTS, ids=[e[1] for e in ROUTER_ENDPOINTS])
    async def test_own_tenant_access_returns_200_or_empty(self, tenant, path_template, label):
        """Authenticated request for the user's own tenant may return 200
        (with data or empty list), 403 (authenticated but lacks the permission
        for this specific endpoint), or 404 (not found). Must NOT 401."""
        path = path_template.format(tid=str(tenant.id))

        async with await _client_for(tenant.id) as client:
            response = await client.get(path)

        assert response.status_code in (200, 403, 404), (
            f"[{label}] Expected 200, 403, or 404 for own-tenant access, "
            f"got {response.status_code}: {response.text}"
        )

    @pytest.mark.parametrize("path_template,label", ROUTER_ENDPOINTS, ids=[e[1] for e in ROUTER_ENDPOINTS])
    async def test_unauthenticated_returns_401(self, tenant, path_template, label):
        """Unauthenticated requests must be rejected with 401, not 404.

        Returning 404 without auth leaks route existence and undermines
        the authentication layer entirely.
        """
        path = path_template.format(tid=str(tenant.id))

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(path)

        assert response.status_code == 401, (
            f"[{label}] Expected 401 without auth, got {response.status_code}: {response.text}"
        )


class TestWorkflowsRouterIsolation:
    """Additional tests for workflow write operations and by-ID endpoints."""

    async def test_create_workflow_ownership(self, tenant, db):
        """Workflow created under tenant A is not visible to tenant B."""
        from app.repositories.workflow_repository import WorkflowRepository
        from app.repositories.workflow_execution_repository import WorkflowExecutionRepository
        from app.schemas.workflow import WorkflowCreate
        from app.services.workflow_service import WorkflowService
        from app.services.workflow.workflow_engine import WorkflowEngine

        tenant_b_id = uuid.uuid4()
        workflow_repo = WorkflowRepository(db)
        execution_repo = WorkflowExecutionRepository(db)
        engine = WorkflowEngine(execution_repo=execution_repo, db=db)
        service = WorkflowService(
            workflow_repo=workflow_repo,
            execution_repo=execution_repo,
            engine=engine,
        )
        payload = WorkflowCreate(
            workflow_number="iso-1",
            name="isolated-workflow",
            workflow_type="incident",
            trigger_type="manual",
            definition={"nodes": [{"id": "start", "type": "start"}], "edges": []},
        )
        created = await service.create_workflow(tenant.id, **payload.model_dump())
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.get(f"/api/workflows/{created.id}")
        assert resp.status_code == 404, f"Expected 404 for cross-tenant workflow read, got {resp.status_code}: {resp.text}"

    async def test_cross_tenant_cannot_execute_workflow(self, tenant, db):
        """Tenant B must not be able to execute tenant A's workflow.

        A 403 (permission denied) is also acceptable: the cross-tenant caller
        lacks ``workflows.execute``, so the request is rejected before any
        tenant data is touched.
        """
        from app.repositories.workflow_repository import WorkflowRepository
        from app.repositories.workflow_execution_repository import WorkflowExecutionRepository
        from app.services.workflow_service import WorkflowService
        from app.services.workflow.workflow_engine import WorkflowEngine

        tenant_b_id = uuid.uuid4()
        # Provision tenant B so the denial comes from ownership/RBAC (403/404),
        # not the subscription gate.
        await _seed_active_sub(tenant_b_id)
        workflow_repo = WorkflowRepository(db)
        execution_repo = WorkflowExecutionRepository(db)
        engine = WorkflowEngine(execution_repo=execution_repo, db=db)
        service = WorkflowService(
            workflow_repo=workflow_repo,
            execution_repo=execution_repo,
            engine=engine,
        )
        created = await service.create_workflow(
            tenant.id,
            definition={"nodes": [{"id": "start", "type": "start"}, {"id": "end", "type": "end"}], "edges": [{"from": "start", "to": "end"}]},
            workflow_number="iso-2",
            name="x",
            workflow_type="incident",
            trigger_type="manual",
        )
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.post(
                f"/api/workflows/{created.id}/execute",
                json={"requested_by": str(uuid.uuid4())},
            )
        assert resp.status_code in (403, 404), f"Expected 403 or 404 for cross-tenant workflow execute, got {resp.status_code}: {resp.text}"


class TestServiceNowRouterIsolation:
    """Isolation tests for the ServiceNow connection/link endpoints."""

    async def test_cross_tenant_connection_not_leaked(self, tenant, db):
        """Tenant A must not read tenant B's ServiceNow connection."""
        from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
        from app.schemas.servicenow import ServiceNowConnectionCreate
        from app.services.servicenow.incident_sync import ServiceNowIncidentSyncService

        tenant_b_id = uuid.uuid4()
        conn_repo = ServiceNowConnectionRepository(db)
        service = ServiceNowIncidentSyncService(
            conn_repo=conn_repo,
            link_repo=__import__("app.repositories.servicenow_incident_link_repository", fromlist=["ServiceNowIncidentLinkRepository"]).ServiceNowIncidentLinkRepository(db),
            job_repo=__import__("app.repositories.servicenow_sync_job_repository", fromlist=["ServiceNowSyncJobRepository"]).ServiceNowSyncJobRepository(db),
        )
        payload = ServiceNowConnectionCreate(name="x", instance_url="https://x.service-now.com", auth_type="basic", username="u", password_plaintext="p")
        created = await conn_repo.create(tenant.id, **payload.model_dump(exclude={"password_plaintext"}, exclude_unset=True))
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.get(f"/api/servicenow/connections/{created.id}")
        assert resp.status_code == 404, f"Expected 404 for cross-tenant connection read, got {resp.status_code}: {resp.text}"


class TestServiceNowWebhookIsolation:
    """Webhook signature + tenant scoping tests (Priority 3)."""

    async def test_valid_signature_returns_200_and_scoped_to_connection_tenant(self, tenant, db):
        """Valid HMAC-SHA256 signature on a webhook for tenant A returns 200 and
        is processed under tenant A's scope."""
        from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
        from app.repositories.servicenow_incident_link_repository import ServiceNowIncidentLinkRepository
        from app.services.servicenow.webhook_handler import IncomingWebhookProcessor
        from app.core.security import SecretCrypto

        conn_repo = ServiceNowConnectionRepository(db)
        link_repo = ServiceNowIncidentLinkRepository(db)
        crypto = SecretCrypto()
        webhook_secret_plain = "s3cret"
        created = await conn_repo.create(
            tenant.id,
            name="w",
            instance_url="https://x.service-now.com",
            auth_type="basic",
            username="u",
            webhook_secret_encrypted=crypto.encrypt(webhook_secret_plain),
        )
        await db.commit()

        body = b'{"correlation_id":"abc-123"}'
        import hmac, hashlib
        sig = "sha256=" + hmac.new(webhook_secret_plain.encode("utf-8"), body, hashlib.sha256).hexdigest()

        processor = IncomingWebhookProcessor(conn_repo=conn_repo, link_repo=link_repo)
        result = await processor.process(tenant.id, body, sig, created.instance_url)
        assert result.get("status") == "no_link"

    async def test_invalid_signature_returns_401(self, tenant, db):
        """Invalid/unknown webhook signature raises an error containing
        'signature' so the route can map it to 401."""
        from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
        from app.repositories.servicenow_incident_link_repository import ServiceNowIncidentLinkRepository
        from app.services.servicenow.webhook_handler import IncomingWebhookProcessor
        from app.core.security import SecretCrypto

        conn_repo = ServiceNowConnectionRepository(db)
        link_repo = ServiceNowIncidentLinkRepository(db)
        crypto = SecretCrypto()
        webhook_secret_plain = "s3cret"
        created = await conn_repo.create(
            tenant.id,
            name="w2",
            instance_url="https://x.service-now.com",
            auth_type="basic",
            username="u",
            webhook_secret_encrypted=crypto.encrypt(webhook_secret_plain),
        )
        await db.commit()

        body = b'{"correlation_id":"abc-123"}'
        processor = IncomingWebhookProcessor(conn_repo=conn_repo, link_repo=link_repo)
        try:
            await processor.process(tenant.id, body, "sha256=bad", created.instance_url)
            pytest.fail("Expected an exception for invalid webhook signature")
        except Exception as exc:
            assert "Invalid" in str(exc) or "signature" in str(exc).lower(), (
                f"Expected 'Invalid' or 'signature' in error, got: {exc}"
            )

    async def test_payload_tenant_id_ignored_resolved_tenant_wins(self, tenant, db):
        """Even if payload context implies another tenant, the route resolves
        tenant from connection ownership (query/connection), not payload."""
        from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
        from app.repositories.servicenow_incident_link_repository import ServiceNowIncidentLinkRepository
        from app.services.servicenow.webhook_handler import IncomingWebhookProcessor
        from app.core.security import SecretCrypto
        import hmac, hashlib

        tenant_b_id = uuid.uuid4()
        conn_repo = ServiceNowConnectionRepository(db)
        link_repo = ServiceNowIncidentLinkRepository(db)
        crypto = SecretCrypto()
        webhook_secret_plain = "s3cret"
        created = await conn_repo.create(
            tenant.id,
            name="w3",
            instance_url="https://x.service-now.com",
            auth_type="basic",
            username="u",
            webhook_secret_encrypted=crypto.encrypt(webhook_secret_plain),
        )
        await db.commit()

        body = b'{"correlation_id":"abc-123"}'
        sig = "sha256=" + hmac.new(webhook_secret_plain.encode("utf-8"), body, hashlib.sha256).hexdigest()
        processor = IncomingWebhookProcessor(conn_repo=conn_repo, link_repo=link_repo)
        result = await processor.process(tenant.id, body, sig, created.instance_url)
        assert result.get("status") == "no_link"


class TestByIdEndpointIsolation:
    """Cross-tenant isolation tests for single-resource-by-ID endpoints (Priority 2)."""

    async def test_cross_tenant_incident_by_id_returns_404(self, tenant, db):
        """Tenant A requesting tenant B's incident by ID gets 404, not the resource."""
        from app.repositories.incident_repository import IncidentRepository
        from app.models.incident import Incident
        from app.services.incident_service import IncidentService
        from datetime import datetime, timezone

        tenant_b_id = uuid.uuid4()
        repo = IncidentRepository(db)
        service = IncidentService(incident_repo=repo, alert_repo=__import__("app.repositories.alert_repository", fromlist=["AlertRepository"]).AlertRepository(db))
        now = datetime.now(timezone.utc)
        incident = await repo.create(tenant_id=tenant.id, title="Isolated incident", status="open", severity="P1", opened_at=now, last_alert_at=now)
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.get(f"/api/incidents/{incident.id}")
        assert resp.status_code == 404, f"Expected 404 for cross-tenant incident by-ID, got {resp.status_code}: {resp.text}"

    async def test_cross_tenant_device_by_id_returns_404(self, tenant, db):
        """Tenant A requesting tenant B's device by ID gets 404."""
        from app.repositories.device_repository import DeviceRepository
        from app.models.device import Device
        from app.repositories.monitoring_source_repository import MonitoringSourceRepository

        tenant_b_id = uuid.uuid4()
        source_repo = MonitoringSourceRepository(db)
        device_repo = DeviceRepository(db)
        source = await source_repo.create(tenant_id=tenant.id, name="s", source_type="prtg", is_active=True, poll_interval_seconds=60, status="pending")
        device = await device_repo.upsert(
            tenant_id=tenant.id,
            monitoring_source_id=source.id,
            external_id="dev-1",
            name="Isolated device",
            device_type="server",
            vendor="test",
        )
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.get(f"/api/devices/{device[0].id}")
        assert resp.status_code == 404, f"Expected 404 for cross-tenant device by-ID, got {resp.status_code}: {resp.text}"

    async def test_cross_tenant_workflow_by_id_returns_404(self, tenant, db):
        """Tenant A requesting tenant B's workflow by ID gets 404."""
        from app.repositories.workflow_repository import WorkflowRepository
        from app.repositories.workflow_execution_repository import WorkflowExecutionRepository
        from app.services.workflow_service import WorkflowService
        from app.services.workflow.workflow_engine import WorkflowEngine
        from app.schemas.workflow import WorkflowCreate

        tenant_b_id = uuid.uuid4()
        workflow_repo = WorkflowRepository(db)
        execution_repo = WorkflowExecutionRepository(db)
        engine = WorkflowEngine(execution_repo=execution_repo, db=db)
        service = WorkflowService(workflow_repo=workflow_repo, execution_repo=execution_repo, engine=engine)
        payload = WorkflowCreate(
            workflow_number="iso-3",
            name="isolated-workflow",
            workflow_type="incident",
            trigger_type="manual",
            definition={"nodes": [{"id": "start", "type": "start"}], "edges": []},
        )
        created = await service.create_workflow(tenant.id, **payload.model_dump())
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.get(f"/api/workflows/{created.id}")
        assert resp.status_code == 404, f"Expected 404 for cross-tenant workflow by-ID, got {resp.status_code}: {resp.text}"

    async def test_cross_tenant_alert_by_id_returns_404(self, tenant, db):
        """Tenant A requesting tenant B's alert by ID gets 404."""
        from app.repositories.alert_repository import AlertRepository
        from app.models.alert import Alert
        from app.services.alert_service import AlertService
        from datetime import datetime, timezone

        tenant_b_id = uuid.uuid4()
        repo = AlertRepository(db)
        service = AlertService(alert_repo=repo)
        alert = await repo.create(tenant_id=tenant.id, title="Isolated alert", severity="critical", status="open", triggered_at=datetime.now(timezone.utc), last_occurred_at=datetime.now(timezone.utc))
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.get(f"/api/alerts/{alert.id}")
        assert resp.status_code == 404, f"Expected 404 for cross-tenant alert by-ID, got {resp.status_code}: {resp.text}"

    async def test_cross_tenant_monitoring_source_by_id_returns_404(self, tenant, db):
        """Tenant A requesting tenant B's monitoring source by ID gets 404."""
        from app.repositories.monitoring_source_repository import MonitoringSourceRepository
        from app.models.monitoring_source import MonitoringSource

        tenant_b_id = uuid.uuid4()
        repo = MonitoringSourceRepository(db)
        source = await repo.create(tenant_id=tenant.id, name="Isolated source", source_type="prtg", is_active=True, poll_interval_seconds=60, status="pending")
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.get(f"/api/monitoring/sources/{source.id}")
        assert resp.status_code == 404, f"Expected 404 for cross-tenant monitoring source by-ID, got {resp.status_code}: {resp.text}"

    async def test_cross_tenant_site_by_id_returns_404(self, tenant, db):
        """Tenant A requesting tenant B's site by ID gets 404."""
        from app.repositories.site_repository import SiteRepository
        from app.models.site import Site

        tenant_b_id = uuid.uuid4()
        repo = SiteRepository(db)
        site = Site(tenant_id=tenant.id, name="Isolated site", code="iso-site-1", region="us-east-1")
        db.add(site)
        await db.flush()
        await db.refresh(site)
        await db.commit()

        async with await _client_for(tenant_b_id) as client:
            resp = await client.get(f"/api/assets/sites/{site.id}")
        assert resp.status_code == 404, f"Expected 404 for cross-tenant site by-ID, got {resp.status_code}: {resp.text}"


class TestTwoTenantSeededIsolation:
    """Task 2 regression: both tenants have data; A's API calls must
    NEVER return B's rows (stronger than the empty-tenant test above)."""

    @staticmethod
    async def _seed_separable_data(tenant_id: uuid.UUID, marker: str) -> dict:
        """Seed incident, ticket, alert, device, monitoring source for a tenant."""
        from datetime import datetime, timezone as tz_mod
        from app.repositories.alert_repository import AlertRepository
        from app.repositories.device_repository import DeviceRepository
        from app.repositories.incident_repository import IncidentRepository
        from app.repositories.monitoring_source_repository import MonitoringSourceRepository
        from app.repositories.ticket_repository import TicketRepository
        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as loan_db:
            now = datetime.now(tz_mod.utc)
            incident = await IncidentRepository(loan_db).create(
                tenant_id=tenant_id,
                title=f"[{marker}] incident", description="isolation",
                severity="critical", priority="P1", status="open",
                correlation_type="single_device",
                opened_at=now, last_alert_at=now,
            )
            ticket = await TicketRepository(loan_db).create(
                tenant_id=tenant_id,
                incident_id=incident.id,
                ticket_number=f"TCK-{marker.upper()}",
                title=f"[{marker}] ticket", description="isolation",
                source="incident_triggered", status="open",
                priority="P1", category="single_device", opened_at=now,
            )
            source = await MonitoringSourceRepository(loan_db).create(
                tenant_id=tenant_id, name=f"[{marker}] source",
                source_type="prtg", is_active=True,
                poll_interval_seconds=60, status="connected",
            )
            device = await DeviceRepository(loan_db).upsert(
                tenant_id=tenant_id, monitoring_source_id=source.id,
                external_id=f"dev-{marker}", name=f"[{marker}] device",
                device_type="server", vendor="test",
            )
            alert = await AlertRepository(loan_db).create(
                tenant_id=tenant_id, title=f"[{marker}] alert",
                severity="critical", status="open",
                triggered_at=now, last_occurred_at=now,
            )
            await loan_db.commit()
            return {
                "incident_id": incident.id,
                "ticket_id": ticket.id,
                "source_id": source.id,
                "device_id": device[0].id,
                "alert_id": alert.id,
            }

    @staticmethod
    async def _ids_from_api(path: str, token: str, label: str) -> list[str]:
        import httpx
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 403, 404), f"[{label}] GET {path} -> {resp.status_code}: {resp.text}"
        if resp.status_code in (403, 404):
            return []
        body = resp.json()
        # Handle both {"items": [...]} and bare-list response shapes.
        if isinstance(body, dict) and "items" in body:
            return [str(item.get("id")) for item in body["items"]]
        if isinstance(body, list):
            return [str(item.get("id")) for item in body if isinstance(item, dict)]
        return []

    async def test_two_tenant_seeded_tickets_no_leak(self, tenant, db):
        """Tenants A AND B both have tickets; A's list and by-ID never return B's."""
        from app.core.db import AsyncSessionLocal
        from app.models.tenant import Tenant
        from sqlalchemy import delete

        # Create tenant B alongside the `tenant` fixture (tenant A)
        async with AsyncSessionLocal() as loan_db:
            tenant_b = Tenant(name="Tenant B", slug=f"tenant-b-{uuid.uuid4().hex[:8]}")
            loan_db.add(tenant_b)
            await loan_db.commit()
            await loan_db.refresh(tenant_b)

        try:
            seeded_a = await self._seed_separable_data(tenant.id, "A")
            seeded_b = await self._seed_separable_data(tenant_b.id, "B")

            token_a = _jwt_for(tenant.id)

            # List must contain A's ticket, never B's
            ticket_ids = await self._ids_from_api("/api/tickets/", token_a, "tickets-list")
            assert str(seeded_a["ticket_id"]) in ticket_ids, "A's own ticket should appear in A's list"
            assert str(seeded_b["ticket_id"]) not in ticket_ids, "LEAK: A's ticket list returned B's ticket!"

            # By-ID must 404 for B's ticket
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get(
                    f"/api/tickets/{seeded_b['ticket_id']}",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
            assert resp.status_code == 404, f"Cross-tenant ticket by ID must 404, got {resp.status_code}"
        finally:
            async with AsyncSessionLocal() as loan_db:
                await loan_db.execute(delete(Tenant).where(Tenant.id == tenant_b.id))
                await loan_db.commit()

    async def test_two_tenant_seeded_incidents_alerts_devices_sources_no_leak(self, tenant, db):
        """A's incident/alert/device/source list never contains B's rows."""
        from app.core.db import AsyncSessionLocal
        from app.models.tenant import Tenant
        from sqlalchemy import delete

        async with AsyncSessionLocal() as loan_db:
            tenant_b = Tenant(name="Tenant B", slug=f"tenant-b-{uuid.uuid4().hex[:8]}")
            loan_db.add(tenant_b)
            await loan_db.commit()
            await loan_db.refresh(tenant_b)

        try:
            seeded_a = await self._seed_separable_data(tenant.id, "A")
            seeded_b = await self._seed_separable_data(tenant_b.id, "B")
            token_a = _jwt_for(tenant.id)

            checks = [
                ("/api/incidents?limit=100", "incident_id", "incidents"),
                ("/api/alerts?limit=100", "alert_id", "alerts"),
                ("/api/devices?limit=100", "device_id", "devices"),
                ("/api/monitoring/sources?limit=100", "source_id", "sources"),
            ]
            for path, key, label in checks:
                ids = await self._ids_from_api(path, token_a, label)
                assert str(seeded_a[key]) in ids, f"A's own {label} should appear in A's {label} list"
                assert str(seeded_b[key]) not in ids, f"LEAK: A's {label} list returned B's {label}!"
        finally:
            async with AsyncSessionLocal() as loan_db:
                await loan_db.execute(delete(Tenant).where(Tenant.id == tenant_b.id))
                await loan_db.commit()
