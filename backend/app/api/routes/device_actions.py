"""
Device Action & Infrastructure Control Plane API (Phase 1).

The ONLY surface through which the dashboard can trigger device actions.
Every write is RBAC-guarded (device_actions.execute / .approve) and
subscription-gated; reads use device_actions.read. Tenant isolation is
enforced by the service/repository layer (tenant-scoped queries only).

Routes:
  GET  /api/device-actions/catalog        - action catalog + classifications
  GET  /api/devices/{id}/capabilities    - resolved capability model
  POST /api/device-actions               - request an action (policy decides
                                           auto-execute vs approval)
  GET  /api/device-actions               - list requests (tenant-scoped)
  GET  /api/device-actions/{id}          - one request (full audit spine)
  POST /api/device-actions/{id}/approve  - human decision ALLOW/DENY/MODIFY
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.schemas.device_action import (
    ActionCatalogEntry,
    ApprovalDecisionIn,
    DeviceActionRequestIn,
    DeviceActionRequestOut,
    DeviceCapabilitiesOut,
)
from app.services.automation_runner import resume_after_device_action_decision
from app.services.device_action_service import (
    DeviceActionError,
    DeviceActionNotFound,
    DeviceActionService,
)

router = APIRouter(tags=["device-actions"])

logger = logging.getLogger(__name__)

_active_sub_guard = Depends(require_active_subscription)


def get_service(db: AsyncSession = Depends(get_db)) -> DeviceActionService:
    return DeviceActionService(db)


@router.get("/device-actions/catalog", response_model=list[ActionCatalogEntry])
async def get_action_catalog(
    _principal: Principal = Depends(require_permission("device_actions.read")),
):
    """The action catalog (single source of truth) so clients never
    hard-code actions, classifications, or capability requirements."""
    return DeviceActionService.catalog()


@router.get(
    "/devices/{device_id}/capabilities",
    response_model=DeviceCapabilitiesOut,
)
async def get_device_capabilities(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("device_actions.read")),
    service: DeviceActionService = Depends(get_service),
):
    try:
        return await service.get_capabilities(tenant_id, device_id)
    except DeviceActionNotFound as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post(
    "/device-actions",
    response_model=DeviceActionRequestOut,
    status_code=202,
    dependencies=[_active_sub_guard],
)
async def request_device_action(
    payload: DeviceActionRequestIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("device_actions.execute")),
    service: DeviceActionService = Depends(get_service),
):
    """Request a device action. The policy engine decides: auto-execute
    (read-only), await human approval (state-changing), or policy-deny."""
    try:
        request = await service.request_action(
            tenant_id=tenant_id,
            device_id=payload.device_id,
            action=payload.action,
            params=payload.params,
            requested_by=principal.user_id,
            incident_id=payload.incident_id,
            motivation=payload.motivation,
        )
        await service.db.commit()
        await service.db.refresh(request)
        return request
    except DeviceActionError as exc:
        await service.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/device-actions", response_model=list[DeviceActionRequestOut])
async def list_device_actions(
    device_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("device_actions.read")),
    service: DeviceActionService = Depends(get_service),
):
    return await service.list_requests(
        tenant_id, device_id=device_id, status=status, limit=limit, offset=offset
    )


@router.get("/device-actions/{request_id}", response_model=DeviceActionRequestOut)
async def get_device_action(
    request_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _principal: Principal = Depends(require_permission("device_actions.read")),
    service: DeviceActionService = Depends(get_service),
):
    try:
        return await service.get_request(tenant_id, request_id)
    except DeviceActionNotFound as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post(
    "/device-actions/{request_id}/approve",
    response_model=DeviceActionRequestOut,
    dependencies=[_active_sub_guard],
)
async def approve_device_action(
    request_id: uuid.UUID,
    payload: ApprovalDecisionIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("device_actions.approve")),
    service: DeviceActionService = Depends(get_service),
):
    """Human decision on a state-changing action: ALLOW / DENY / MODIFY."""
    try:
        request = await service.resolve_approval(
            tenant_id=tenant_id,
            request_id=request_id,
            decision=payload.decision,
            approver_id=principal.user_id,
            approver_name=principal.email,
            note=payload.note,
            modified_params=payload.modified_params,
        )
        await service.db.commit()
        await service.db.refresh(request)
    except DeviceActionError as exc:
        await service.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    # SOP bridge: when this action came from a workflow auto-step that paused
    # on this approval, resume the paused SOPExecution. Best-effort — an
    # automation-resume hiccup must never fail the approval decision itself.
    try:
        await resume_after_device_action_decision(
            request.id,
            tenant_id,
            approved=(payload.decision != "DENY"),
            approver_id=principal.user_id,
            note=payload.note,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "SOP resume after device-action decision failed for request %s",
            request.id, exc_info=True,
        )
    return request
