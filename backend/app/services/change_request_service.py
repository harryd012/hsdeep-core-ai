"""Change Request service for business logic."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.models.change_request import ChangeRequest
from app.repositories.change_request_repository import ChangeRequestRepository


class ChangeRequestNotFound(Exception):
    pass


class InvalidChangeTransition(Exception):
    pass


VALID_STATUS_TRANSITIONS = {
    "draft": {"pending_approval"},
    "pending_approval": {"approved", "rejected", "cancelled"},
    "approved": {"scheduled"},
    "scheduled": {"in_progress", "cancelled"},
    "in_progress": {"implemented", "rolled_back", "failed"},
    "implemented": {"verifying"},
    "verifying": {"completed", "rolled_back"},
    "completed": set(),
    "rejected": {"cancelled"},
    "rolled_back": {"completed"},
    "failed": {"in_progress"},
    "cancelled": set(),
}


class ChangeRequestService:
    def __init__(self, repo: ChangeRequestRepository):
        self.repo = repo

    async def _generate_change_number(self, tenant_id: uuid.UUID) -> str:
        """Generate a unique change number for the tenant."""
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M")
        counter = 1
        while True:
            change_number = f"CHG-{timestamp}-{counter:04d}"
            stmt = select(ChangeRequest).where(
                ChangeRequest.tenant_id == tenant_id,
                ChangeRequest.change_number == change_number,
                ChangeRequest.is_deleted.is_(False)
            )
            result = await self.repo.session.execute(stmt)
            if result.scalar_one_or_none() is None:
                return change_number
            counter += 1

    async def create(self, *, tenant_id: uuid.UUID, title: str, description: str,
                     change_type: str = "config", risk_level: str = "medium",
                     priority: str = "normal", business_justification: str | None = None,
                     implementation_plan: str | None = None, backout_plan: str | None = None,
                     test_plan: str | None = None, validation_plan: str | None = None,
                     security_impact: str | None = None, affected_devices: list | None = None,
                     affected_services: list | None = None, downtime_expected: bool = False,
                     downtime_duration_minutes: int = 0,
                     maintenance_window_start: datetime | None = None,
                     maintenance_window_end: datetime | None = None,
                                         requested_by: uuid.UUID | None = None) -> ChangeRequest:
        change_number = await self._generate_change_number(tenant_id)
        return await self.repo.create(
            tenant_id=tenant_id, change_number=change_number, title=title,
            description=description, change_type=change_type, risk_level=risk_level,
            priority=priority, business_justification=business_justification,
            implementation_plan=implementation_plan, backout_plan=backout_plan,
            test_plan=test_plan, validation_plan=validation_plan,
            security_impact=security_impact, affected_devices=affected_devices,
            affected_services=affected_services, downtime_expected=downtime_expected,
            downtime_duration_minutes=downtime_duration_minutes,
            maintenance_window_start=maintenance_window_start,
            maintenance_window_end=maintenance_window_end, requested_by=requested_by,
        )

    async def get(self, change_id: uuid.UUID, tenant_id: uuid.UUID) -> ChangeRequest:
        change = await self.repo.get(change_id, tenant_id)
        if change is None:
            raise ChangeRequestNotFound(f"Change request {change_id} not found")
        return change

    async def list(self, tenant_id: uuid.UUID, *, status: str | None = None,
                   change_type: str | None = None, risk_level: str | None = None,
                   requested_by: uuid.UUID | None = None,
                   limit: int = 100, offset: int = 0) -> list[ChangeRequest]:
        return await self.repo.list(tenant_id, status=status, change_type=change_type,
                                    risk_level=risk_level, requested_by=requested_by,
                                    limit=limit, offset=offset)

    async def count(self, tenant_id: uuid.UUID, *, status: str | None = None) -> int:
        return await self.repo.count(tenant_id, status=status)

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        return await self.repo.summary(tenant_id)

    async def validate_transition(self, current_status: str, new_status: str) -> bool:
        valid_next = VALID_STATUS_TRANSITIONS.get(current_status, set())
        return new_status in valid_next

    async def update_status(self, change_id: uuid.UUID, tenant_id: uuid.UUID,
                            new_status: str, *, approved_by: uuid.UUID | None = None,
                            approval_notes: str | None = None, implemented_by: uuid.UUID | None = None,
                            implementation_result: str | None = None, rollback_executed: bool | None = None,
                            verified_by: uuid.UUID | None = None, verification_result: str | None = None,
                                closure_notes: str | None = None) -> ChangeRequest:
        change = await self.get(change_id, tenant_id)
        if not await self.validate_transition(change.status, new_status):
            raise InvalidChangeTransition(f"Cannot transition from {change.status} to {new_status}")
        fields = {"status": new_status}
        if new_status == "approved":
            fields["approved_by"] = approved_by
            fields["approved_at"] = datetime.utcnow()
            if approval_notes:
                fields["approval_notes"] = approval_notes
        elif new_status == "implemented":
            fields["implemented_by"] = implemented_by
            fields["implemented_at"] = datetime.utcnow()
            if implementation_result:
                fields["implementation_result"] = implementation_result
            if rollback_executed is not None:
                fields["rollback_executed"] = rollback_executed
        elif new_status == "verifying":
            fields["verified_by"] = verified_by
        elif new_status == "completed":
            fields["verified_at"] = datetime.utcnow()
            if verification_result:
                fields["verification_result"] = verification_result
            fields["closed_at"] = datetime.utcnow()
            if closure_notes:
                fields["closure_notes"] = closure_notes
        return await self.repo.update(change, **fields)

    async def update(self, change_id: uuid.UUID, tenant_id: uuid.UUID, **fields) -> ChangeRequest:
        change = await self.get(change_id, tenant_id)
        return await self.repo.update(change, **fields)

    async def delete(self, change_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        change = await self.get(change_id, tenant_id)
        await self.repo.delete(change)