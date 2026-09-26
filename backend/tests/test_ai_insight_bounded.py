"""Tests for the AI-insight context-bounding + sustained_critical
idempotency fixes.

Covers (per the work brief):
  B. sustained_critical condition -> generated deterministically, repeated
     computation is idempotent (identical output, zero side effects/rows),
     and it never triggers duplicate correlation/task work.
  C. Huge historical event/alert volume -> Copilot/AI context stays bounded:
        * AIInsightService uses small bounded alert queries (never
          limit=100000 over a tenant's whole alerts table), and
        * the Copilot command-center projection trims the full agent/task
          aggregate to a compact summary before it reaches the LLM.

Runs against the real configured Postgres where a tenant is needed;
pure-python unit checks (fake repos / plain dicts) run anywhere.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import AsyncSessionLocal
from app.models.alert import Alert
from app.models.tenant import Tenant
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.sensor_repository import SensorRepository
from app.services.ai_insight_service import AIInsightService
from app.services.copilot_router import _compact_command_center

pytestmark = pytest.mark.asyncio

_ALERT_LIMIT_CAP = 200


# --------------------------------------------------------------------------- #
# Pure-python units (no DB): bounded query usage + compact command-center     #
# --------------------------------------------------------------------------- #
class _FakeAlertRepo:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_active_for_insights(self, tenant_id, **kw):
        self.calls.append(kw)
        return []


class _FakeSensorRepo:
    async def list_for_tenant(self, tenant_id):
        return []


class _FakeRunRepo:
    def __init__(self) -> None:
        self.list_calls: list[dict] = []

    async def list(self, tenant_id, **kw):
        self.list_calls.append(kw)
        return []


class TestBoundedAiInsightQueries:
    async def test_insight_alert_queries_are_bounded(self):
        """The AI-insight path must query active alerts with a SMALL limit,
        never scan the tenant's whole alerts table (100k+)."""
        alert_repo = _FakeAlertRepo()
        run_repo = _FakeRunRepo()
        svc = AIInsightService(
            alert_repo=alert_repo,  # type: ignore[arg-type]
            sensor_repo=_FakeSensorRepo(),  # type: ignore[arg-type]
            run_repo=run_repo,  # type: ignore[arg-type]
        )
        await svc.insights(uuid.uuid4())

        assert alert_repo.calls, "expected at least one bounded alert query"
        for call in alert_repo.calls:
            limit = call.get("limit")
            assert limit is not None and 0 < limit <= _ALERT_LIMIT_CAP, (
                f"insight alert query must be bounded, got limit={limit}"
            )

        assert run_repo.list_calls, "expected a collector-run query"
        for call in run_repo.list_calls:
            limit = call.get("limit")
            assert limit is not None and limit <= 1000, (
                f"collector-run query must be bounded, got limit={limit}"
            )

    async def test_command_center_projection_is_bounded(self):
        """The Copilot command-center context must be a compact summary, not
        the full up-to-1000-row dashboard aggregate."""
        now = datetime.now(timezone.utc).isoformat()
        many_tasks = [
            {"task_number": f"T-{i}", "title": f"task {i}", "status": "open"}
            for i in range(1000)
        ]
        many_agents = [
            {"name": f"agent-{i}", "role": "L1", "department": "noc", "status": "active"}
            for i in range(1000)
        ]
        many_activity = [
            {"task_number": f"T-{i}", "timestamp": now, "action": "update"}
            for i in range(300)
        ]
        overview = {
            "generated_at": now,
            "metrics": {"total_agents": 1000, "total_tasks": 1000},
            "workload": [{"department": "NOC", "active_tasks": 10}],
            "tasks": many_tasks,
            "agents": many_agents,
            "activity": many_activity,
        }

        compact = _compact_command_center(overview)

        assert len(compact["tasks"]) <= 10
        assert len(compact["agents"]) <= 10
        assert len(compact["activity"]) <= 20
        assert compact["metrics"]["total_tasks"] == 1000  # aggregate preserved
        assert compact["metrics"]["total_agents"] == 1000
# --------------------------------------------------------------------------- #
# DB-backed: sustained_critical is deterministic + idempotent, no side effects #
# --------------------------------------------------------------------------- #
class TestSustainedCriticalIdempotent:
    async def test_repeated_computation_is_idempotent_and_read_only(self):
        tenant = Tenant(name=f"Sustained Test {uuid.uuid4().hex[:8]}",
                        slug=f"sustained-{uuid.uuid4().hex[:8]}")
        try:
            # A sustained critical alert (triggered > 30 min ago) + a healthy
            # sensor (so the no_data rule doesn't inject unrelated insights).
            async with AsyncSessionLocal() as db:
                db.add(tenant)
                await db.flush()
                from app.models.monitoring_source import MonitoringSource
                from app.models.sensor import Sensor

                source = MonitoringSource(tenant_id=tenant.id, name="src",
                                          source_type="prtg", is_active=True)
                db.add(source)
                await db.flush()
                sensor = Sensor(
                    tenant_id=tenant.id,
                    monitoring_source_id=source.id,
                    external_id="sens-up",
                    name="Healthy Sensor",
                    metric_type="uptime",
                    status="up",
                    current_value=1.0,
                    last_value_at=datetime.now(timezone.utc),
                )
                db.add(sensor)
                now = datetime.now(timezone.utc)
                alert = Alert(
                    tenant_id=tenant.id,
                    title="SSL Security Check - mail.example.com:993",
                    severity="critical",
                    status="open",
                    metric_type="ssl_security",
                    triggered_at=now - timedelta(minutes=45),
                    last_occurred_at=now,
                    occurrence_count=2000,
                )
                db.add(alert)
                await db.commit()
                await db.refresh(tenant)

            # Build the service against a fresh session per call (the repos
            # bind to a session, and we don't want to hold a transaction open).
            first = None
            second = None
            async with AsyncSessionLocal() as db:
                svc = AIInsightService(
                    alert_repo=AlertRepository(db),
                    sensor_repo=SensorRepository(db),
                    run_repo=CollectorRunRepository(db),
                )
                first = await svc.insights(tenant.id)
            async with AsyncSessionLocal() as db:
                svc = AIInsightService(
                    alert_repo=AlertRepository(db),
                    sensor_repo=SensorRepository(db),
                    run_repo=CollectorRunRepository(db),
                )
                second = await svc.insights(tenant.id)

            sustained_first = [i for i in first.insights if i.kind == "sustained_critical"]
            sustained_second = [i for i in second.insights if i.kind == "sustained_critical"]

            # The sustained-critical condition is detected once...
            assert len(sustained_first) >= 1
            assert any("SSL Security Check" in i.message for i in sustained_first)
            # ...and repeated computation is idempotent (identical output).
            assert [i.message for i in sustained_first] == [
                i.message for i in sustained_second
            ]

            # Zero side effects - computing insights creates no incidents and
            # no tasks (insights are read-only over existing rows).
            async with AsyncSessionLocal() as db:
                open_incidents = await IncidentRepository(db).list(
                    tenant.id, status="open"
                )
                assert len(open_incidents) == 0
        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
                await db.commit()