"""Change Request API routes."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.change_request_repository import ChangeRequestRepository
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestRead,
    ChangeRequestSummary,
    ChangeRequestUpdate,
)
from app.services.change_request_service import (
    ChangeRequestNotFound,
    ChangeRequestService,
    InvalidChangeTransition,
)

router = APIRouter(prefix="/change-requests", tags=["change-requests"])


def get_change_service(session: AsyncSession = Depends(get_db)) -> ChangeRequestService:
    return ChangeRequestService(ChangeRequestRepository(session))


@router.post("/", response_model=ChangeRequestRead, status_code=status.HTTP_201_CREATED)
async def create_change_request(
    change_data: ChangeRequestCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: None = Depends(require_active_subscription),
    service: ChangeRequestService = Depends(get_change_service),
):
    return await service.create(
        tenant_id=tenant_id,
        title=change_data.title,
        description=change_data.description,
        change_type=change_data.change_type,
        risk_level=change_data.risk_level,
        priority=change_data.priority,
        business_justification=change_data.business_justification,
        implementation_plan=change_data.implementation_plan,
        backout_plan=change_data.backout_plan,
        test_plan=change_data.test_plan,
        validation_plan=change_data.validation_plan,
        security_impact=change_data.security_impact,
        affected_devices=change_data.affected_devices,
        affected_services=change_data.affected_services,
        downtime_expected=change_data.downtime_expected,
        downtime_duration_minutes=change_data.downtime_duration_minutes,
        maintenance_window_start=change_data.maintenance_window_start,
        maintenance_window_end=change_data.maintenance_window_end,
        requested_by=change_data.requested_by,
    )


@router.get("/", response_model=list[ChangeRequestRead])
async def list_change_requests(
    status: Optional[str] = Query(None),
    change_type: Optional[str] = Query(None),
    risk_level: Optional[str] = Query(None),
    requested_by: Optional[uuid.UUID] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ChangeRequestService = Depends(get_change_service),
):
    return await service.list(tenant_id, status=status, change_type=change_type,
                              risk_level=risk_level, requested_by=requested_by,
                              limit=limit, offset=offset)


@router.get("/summary", response_model=ChangeRequestSummary)
async def change_request_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ChangeRequestService = Depends(get_change_service),
):
    return await service.summary(tenant_id)


@router.get("/{change_id}", response_model=ChangeRequestRead)
async def get_change_request(
    change_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ChangeRequestService = Depends(get_change_service),
):
    try:
        return await service.get(change_id, tenant_id)
    except ChangeRequestNotFound:
        raise HTTPException(status_code=404, detail="Change request not found")


@router.patch("/{change_id}", response_model=ChangeRequestRead)
async def update_change_request(
    change_id: uuid.UUID,
    change_data: ChangeRequestUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: None = Depends(require_active_subscription),
    service: ChangeRequestService = Depends(get_change_service),
):
    try:
        return await service.update(change_id, tenant_id, **change_data.model_dump(exclude_unset=True))
    except ChangeRequestNotFound:
        raise HTTPException(status_code=404, detail="Change request not found")


@router.post("/{change_id}/status")
async def update_change_status(
    change_id: uuid.UUID,
    new_status: str = Query(...),
    approved_by: Optional[uuid.UUID] = Query(None),
    approval_notes: Optional[str] = Query(None),
    implemented_by: Optional[uuid.UUID] = Query(None),
    implementation_result: Optional[str] = Query(None),
    verified_by: Optional[uuid.UUID] = Query(None),
    verification_result: Optional[str] = Query(None),
    closure_notes: Optional[str] = Query(None),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ChangeRequestService = Depends(get_change_service),
):
    try:
        change = await service.update_status(
            change_id, tenant_id, new_status,
            approved_by=approved_by, approval_notes=approval_notes,
            implemented_by=implemented_by, implementation_result=implementation_result,
            verified_by=verified_by, verification_result=verification_result,
            closure_notes=closure_notes,
        )
        return ChangeRequestRead.model_validate(change)
    except ChangeRequestNotFound:
        raise HTTPException(status_code=404, detail="Change request not found")
    except InvalidChangeTransition as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{change_id}")
async def delete_change_request(
    change_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ChangeRequestService = Depends(get_change_service),
):
    try:
        await service.delete(change_id, tenant_id)
        return {"status": "deleted"}
    except ChangeRequestNotFound:
        raise HTTPException(status_code=404, detail="Change request not found")
