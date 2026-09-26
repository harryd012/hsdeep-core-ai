"""Change Request service tests (Phase 8 - Change/CAB)."""
from __future__ import annotations

import uuid

import pytest

from app.core.db import AsyncSessionLocal
from app.models.tenant import Tenant
from app.repositories.change_request_repository import ChangeRequestRepository
from app.services.change_request_service import (
    ChangeRequestNotFound,
    ChangeRequestService,
    InvalidChangeTransition,
)

pytestmark = pytest.mark.asyncio


async def _make_change(tenant_id: uuid.UUID, db: AsyncSessionLocal, *, title: str = "Test Change"):
    return await ChangeRequestService(ChangeRequestRepository(db)).create(
        tenant_id=tenant_id,
        title=title,
        description="Firmware upgrade for core firewall",
        change_type="firmware",
        risk_level="high",
        priority="high",
        business_justification="Security patch for known CVE",
        implementation_plan="Backup config, stage firmware, apply during maintenance window",
        backout_plan="Reboot to previous firmware image",
    )


async def test_create_change_request(tenant: Tenant, db: AsyncSessionLocal):
    service = ChangeRequestService(ChangeRequestRepository(db))
    change = await _make_change(tenant.id, db)
    assert change.title == "Test Change"
    assert change.change_type == "firmware"
    assert change.status == "draft"
    assert change.risk_level == "high"
    assert change.change_number.startswith("CHG-")


async def test_get_change_not_found(tenant: Tenant, db: AsyncSessionLocal):
    service = ChangeRequestService(ChangeRequestRepository(db))
    with pytest.raises(ChangeRequestNotFound):
        await service.get(uuid.uuid4(), tenant.id)


async def test_change_tenant_isolation(tenant: Tenant, db: AsyncSessionLocal):
    change = await _make_change(tenant.id, db)
    other_tenant_id = uuid.uuid4()
    with pytest.raises(ChangeRequestNotFound):
        await ChangeRequestService(ChangeRequestRepository(db)).get(change.id, other_tenant_id)


async def test_validate_transition(tenant: Tenant, db: AsyncSessionLocal):
    service = ChangeRequestService(ChangeRequestRepository(db))
    # draft -> pending_approval
    assert await service.validate_transition("draft", "pending_approval") is True
    # draft -> approved is invalid
    assert await service.validate_transition("draft", "approved") is False


async def test_status_lifecycle(tenant: Tenant, db: AsyncSessionLocal):
    service = ChangeRequestService(ChangeRequestRepository(db))
    change = await _make_change(tenant.id, db)

    change = await service.update_status(change.id, tenant.id, "pending_approval")
    assert change.status == "pending_approval"

    change = await service.update_status(
        change.id, tenant.id, "approved", approved_by=None, approval_notes="OK to proceed"
    )
    assert change.status == "approved"
    assert change.approved_at is not None

    change = await service.update_status(change.id, tenant.id, "scheduled")
    assert change.status == "scheduled"

    change = await service.update_status(change.id, tenant.id, "in_progress")
    assert change.status == "in_progress"

    change = await service.update_status(
        change.id, tenant.id, "implemented",
        implemented_by=None,
        implementation_result="Firmware applied successfully",
    )
    assert change.status == "implemented"
    assert change.implemented_at is not None

    change = await service.update_status(change.id, tenant.id, "verifying")
    assert change.status == "verifying"

    change = await service.update_status(
        change.id, tenant.id, "completed", verification_result="Verified healthy"
    )
    assert change.status == "completed"
    assert change.verified_at is not None
    assert change.closed_at is not None


async def test_invalid_transition_raises(tenant: Tenant, db: AsyncSessionLocal):
    service = ChangeRequestService(ChangeRequestRepository(db))
    change = await _make_change(tenant.id, db)
    # draft -> completed is invalid
    with pytest.raises(InvalidChangeTransition):
        await service.update_status(change.id, tenant.id, "completed")


async def test_delete_change(tenant: Tenant, db: AsyncSessionLocal):
    service = ChangeRequestService(ChangeRequestRepository(db))
    change = await _make_change(tenant.id, db)
    await service.delete(change.id, tenant.id)
    with pytest.raises(ChangeRequestNotFound):
        await service.get(change.id, tenant.id)