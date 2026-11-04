"""ServiceNowSyncJobRepository queries run (regression: they filtered on a
soft-delete column the model does not have, so the worker's ServiceNow sync,
the job list and retry all raised AttributeError)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.servicenow import ServiceNowConnection
from app.repositories.servicenow_sync_job_repository import ServiceNowSyncJobRepository

pytestmark = pytest.mark.asyncio


async def test_due_get_and_dead_letter_queries_work_and_stay_in_tenant(db, tenant):
    conn = ServiceNowConnection(tenant_id=tenant.id, name="snow", instance_url="https://dev.example.test")
    db.add(conn)
    await db.flush()
    repo = ServiceNowSyncJobRepository(db)
    now = datetime.now(timezone.utc)
    due = await repo.create(tenant_id=tenant.id, connection_id=conn.id, job_type="create_incident",
                            next_attempt_at=now - timedelta(minutes=1))
    later = await repo.create(tenant_id=tenant.id, connection_id=conn.id, job_type="create_incident",
                              next_attempt_at=now + timedelta(hours=1))
    dead = await repo.create(tenant_id=tenant.id, connection_id=conn.id, job_type="create_incident",
                             status="dead_letter", next_attempt_at=now)
    await db.commit()  # committed so the tenant fixture's teardown can cascade

    assert [j.id for j in await repo.list_due(tenant.id)] == [due.id]
    assert [j.id for j in await repo.list_dead_letter(tenant.id)] == [dead.id]
    assert (await repo.get(later.id, tenant.id)).id == later.id
    assert await repo.get(later.id, uuid.uuid4()) is None
    assert await repo.list_due(uuid.uuid4()) == []
