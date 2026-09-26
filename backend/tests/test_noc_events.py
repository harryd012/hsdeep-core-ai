
"""
NOC Event service + API tests.

Covers:
  1. Create NOC event (service layer)
  2. Create NOC event with invalid analyst assignment
  3. Update NOC event (service layer)
  4. Status transition validation
  5. Tenant isolation (cross-tenant access blocked)
  6. API: POST /noc/events
  7. API: PATCH /noc/events/{id}
  8. API: status transition validation via HTTP

Runs against the real Postgres DB per project convention (conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.agent import Agent
from app.models.noc_event import NocEvent
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.repositories.agent_repository import AgentRepository
from app.repositories.noc_event_repository import NocEventRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.noc_service import NocEventInvalidTransition, NocEventNotFound, NocEventService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_sub(db, tenant_id: uuid.UUID, *, status: str = "active") -> Subscription:
    plan = await PlanRepository(db).create(
        name=f"NOC-{uuid.uuid4().hex[:8]}",
        tier=f"noc-{uuid.uuid4().hex[:8]}",
        description="NOC test plan",
        price_monthly=0.0,
        price_annual=0.0,
        device_limit=100,
        sensor_limit=1000,
        api_rate_limit=100000,
        collector_limit=10,
        seats=25,
        features_json={},
        is_public=False,
    )
    now = datetime.now(timezone.utc)
    sub = await SubscriptionRepository(db).create(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status=status,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        billing_interval="month",
    )
    await db.commit()
    return sub


def _jwt(tenant_id: uuid.UUID, permissions: set[str], actor_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=actor_id,
        tenant_id=tenant_id,
        email=f"test-{actor_id}@test.local",
        is_superuser=False,
        permissions=frozenset(permissions),
    )


async def _make_soc_analyst(db, tenant_id: uuid.UUID, **overrides) -> Agent:
    fields = {
        "tenant_id": tenant_id,
        "agent_id": f"soc-{uuid.uuid4().hex[:6]}",
        "name": "Test SOC Analyst",
        "role": "soc_analyst",
        "department": "soc",
        "level": "L2",
        "is_active": True,
    }
    fields.update(overrides)
    agent = Agent(**fields)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent
# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def noc_service(db: AsyncSessionLocal) -> NocEventService:
    return NocEventService(
        noc_repo=NocEventRepository(db),
        agent_repo=AgentRepository(db),
    )


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


class TestNocEventServiceCreate:
    async def test_create_event_success(self, tenant: Tenant, db: AsyncSessionLocal):
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        event = await service.create(
            tenant.id,
            title="Test Event",
            severity="critical",
            source="test",
            category="availability",
        )
        assert event.id is not None
        assert event.tenant_id == tenant.id
        assert event.title == "Test Event"
        assert event.severity == "critical"
        assert event.status == "open"
        assert event.occurrence_count == 1

    async def test_create_event_with_analyst(self, tenant: Tenant, db: AsyncSessionLocal):
        analyst = await _make_soc_analyst(db, tenant.id)
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        event = await service.create(
            tenant.id,
            title="Assigned Event",
            severity="warning",
            source="test",
            assigned_analyst_id=analyst.id,
        )
        assert event.assigned_analyst_id == analyst.id

    async def test_create_event_invalid_analyst(self, tenant: Tenant, db: AsyncSessionLocal):
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        fake_id = uuid.uuid4()
        with pytest.raises(NocEventNotFound, match="not found"):
            await service.create(
                tenant.id,
                title="Test",
                severity="info",
                source="test",
                assigned_analyst_id=fake_id,
            )

    async def test_create_event_non_soc_analyst(self, tenant: Tenant, db: AsyncSessionLocal):
        # Create an agent that is NOT a SOC analyst
        non_soc = await _make_soc_analyst(db, tenant.id, department="noc", role="noc_engineer")
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        with pytest.raises(ValueError, match="not a SOC analyst"):
            await service.create(
                tenant.id,
                title="Test",
                severity="info",
                source="test",
                assigned_analyst_id=non_soc.id,
            )


class TestNocEventServiceUpdate:
    async def test_update_title(self, tenant: Tenant, db: AsyncSessionLocal):
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        event = await service.create(
            tenant.id,
            title="Original",
            severity="info",
            source="test",
        )
        updated = await service.update(event.id, tenant.id, title="Updated")
        assert updated.title == "Updated"

    async def test_update_status_transition(self, tenant: Tenant, db: AsyncSessionLocal):
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        event = await service.create(
            tenant.id,
            title="Test",
            severity="warning",
            source="test",
        )
        updated = await service.update(event.id, tenant.id, status="acknowledged")
        assert updated.status == "acknowledged"
        assert updated.acknowledged_at is not None

    async def test_invalid_status_transition(self, tenant: Tenant, db: AsyncSessionLocal):
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        event = await service.create(
            tenant.id,
            title="Test",
            severity="info",
            source="test",
        )
        await service.update(event.id, tenant.id, status="resolved")
        with pytest.raises(NocEventInvalidTransition):
            await service.update(event.id, tenant.id, status="open")

    async def test_update_not_found(self, tenant: Tenant, db: AsyncSessionLocal):
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        with pytest.raises(NocEventNotFound):
            await service.update(uuid.uuid4(), tenant.id, title="X")


class TestNocEventTenantIsolation:
    async def test_cross_tenant_read_blocked(self, tenant: Tenant, db: AsyncSessionLocal):
        service = NocEventService(
            noc_repo=NocEventRepository(db),
            agent_repo=AgentRepository(db),
        )
        event = await service.create(
            tenant.id,
            title="Private Event",
            severity="critical",
            source="test",
        )
        other_tenant_id = uuid.uuid4()
        with pytest.raises(NocEventNotFound):
            await service.get(event.id, other_tenant_id)
# ---------------------------------------------------------------------------
# API-layer tests
# ---------------------------------------------------------------------------


class TestNocEventApi:
    async def test_create_event_api(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            await _seed_sub(db, tenant.id)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/noc/events",
                headers={
                    "Authorization": "Bearer " + _jwt(tenant.id, {"noc.write"}, uuid.uuid4())
                },
                json={
                    "title": "API Test Event",
                    "severity": "warning",
                    "source": "api-test",
                    "category": "performance",
                    "triggered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["title"] == "API Test Event"
        assert data["status"] == "open"

    async def test_update_event_api(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            await _seed_sub(db, tenant.id)
            service = NocEventService(
                noc_repo=NocEventRepository(db),
                agent_repo=AgentRepository(db),
            )
            event = await service.create(
                tenant.id,
                title="To Update",
                severity="info",
                source="test",
            )
            await db.commit()
            await db.refresh(event)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/noc/events/{event.id}",
                headers={
                    "Authorization": "Bearer " + _jwt(tenant.id, {"noc.write"}, uuid.uuid4())
                },
                json={"status": "acknowledged"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "acknowledged"

    async def test_create_requires_subscription(self, tenant: Tenant):
        """Without an active subscription, POST should return 402."""
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/noc/events",
                headers={
                    "Authorization": "Bearer " + _jwt(tenant.id, {"noc.write"}, uuid.uuid4())
                },
                json={
                    "title": "Should Fail",
                    "severity": "info",
                    "source": "test",
                },
            )
        assert resp.status_code == 402

    async def test_invalid_transition_api(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            await _seed_sub(db, tenant.id)
            service = NocEventService(
                noc_repo=NocEventRepository(db),
                agent_repo=AgentRepository(db),
            )
            event = await service.create(
                tenant.id,
                title="Test",
                severity="info",
                source="test",
            )
            # Resolve it first
            await service.update(event.id, tenant.id, status="resolved")
            await db.commit()
            await db.refresh(event)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/noc/events/{event.id}",
                headers={
                    "Authorization": "Bearer " + _jwt(tenant.id, {"noc.write"}, uuid.uuid4())
                },
                json={"status": "open"},  # invalid: resolved -> open
            )
        assert resp.status_code == 409, resp.text
