"""
Runbook Repository / Execution API (SOP & Runbook Intelligence Engine
sprint). Manual approval workflow + execution history deliverables.

NOTE(auth): same interim pattern as every other router in this codebase -
tenant_id is an explicit query parameter because there is no
authenticated-principal dependency yet.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.auth import get_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.runbook_execution_repository import RunbookExecutionRepository
from app.repositories.runbook_repository import RunbookRepository
from app.schemas.runbook import (
    RunbookCreate,
    RunbookExecutionApproveRequest,
    RunbookExecutionCompleteRequest,
    RunbookExecutionOut,
    RunbookExecutionRejectRequest,
    RunbookExecutionRequest,
    RunbookExecutionRollbackRequest,
    RunbookExecutionStartRequest,
    RunbookOut,
)
from app.services.runbook_service import RunbookNotFound, RunbookService
from app.services.sop.execution_tracker import ExecutionTracker, InvalidExecutionTransition

router = APIRouter(prefix="/runbooks", tags=["runbooks"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_runbook_service(db: AsyncSession = Depends(get_db)) -> RunbookService:
    execution_repo = RunbookExecutionRepository(db)
    runbook_repo = RunbookRepository(db)
    return RunbookService(
        runbook_repo=runbook_repo,
        execution_tracker=ExecutionTracker(execution_repo=execution_repo, runbook_repo=runbook_repo),
        execution_repo=execution_repo,
    )


@router.get("", response_model=list[RunbookOut])
async def list_runbooks(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    execution_mode: str | None = None,
    is_active: bool | None = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: RunbookService = Depends(get_runbook_service),
):
    return await service.list_runbooks(tenant_id, execution_mode=execution_mode, is_active=is_active, limit=limit, offset=offset)


@router.get("/history", response_model=list[RunbookExecutionOut])
async def list_execution_history(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    runbook_id: uuid.UUID | None = None,
    incident_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: RunbookService = Depends(get_runbook_service),
):
    """Execution History deliverable - most recent first."""
    return await service.list_execution_history(
        tenant_id, runbook_id=runbook_id, incident_id=incident_id, status=status, limit=limit, offset=offset
    )


@router.get("/{runbook_id}", response_model=RunbookOut)
async def get_runbook(
    runbook_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: RunbookService = Depends(get_runbook_service),
):
    try:
        return await service.get(runbook_id, tenant_id)
    except RunbookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", dependencies=[_active_sub_guard], response_model=RunbookOut, status_code=201)
async def create_runbook(
    payload: RunbookCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: RunbookService = Depends(get_runbook_service),
):
    runbook = await service.create_runbook(tenant_id, **payload.model_dump())
    await db.commit()
    return runbook


@router.post("/{runbook_id}/execute", dependencies=[_active_sub_guard], response_model=RunbookExecutionOut, status_code=201)
async def execute_runbook(
    runbook_id: uuid.UUID,
    payload: RunbookExecutionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: RunbookService = Depends(get_runbook_service),
):
    """Requests a new execution (manual approval workflow entry point) -
    always starts at pending_approval, never runs immediately."""
    try:
        execution = await service.request_execution(
            runbook_id, tenant_id, incident_id=payload.incident_id, operator_id=payload.operator_id
        )
    except RunbookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return execution


@router.get("/executions/{execution_id}", response_model=RunbookExecutionOut)
async def get_execution(
    execution_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: RunbookService = Depends(get_runbook_service),
):
    try:
        return await service.get_execution(execution_id, tenant_id)
    except RunbookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/executions/{execution_id}/approve", dependencies=[_active_sub_guard], response_model=RunbookExecutionOut)
async def approve_execution(
    execution_id: uuid.UUID,
    payload: RunbookExecutionApproveRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: RunbookService = Depends(get_runbook_service),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        approved = await service.execution_tracker.approve(execution, approved_by=payload.approved_by)
    except RunbookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return approved


@router.post("/executions/{execution_id}/reject", dependencies=[_active_sub_guard], response_model=RunbookExecutionOut)
async def reject_execution(
    execution_id: uuid.UUID,
    payload: RunbookExecutionRejectRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: RunbookService = Depends(get_runbook_service),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        rejected = await service.execution_tracker.reject(execution, rejected_by=payload.rejected_by, reason=payload.reason)
    except RunbookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return rejected


@router.post("/executions/{execution_id}/start", dependencies=[_active_sub_guard], response_model=RunbookExecutionOut)
async def start_execution(
    execution_id: uuid.UUID,
    payload: RunbookExecutionStartRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: RunbookService = Depends(get_runbook_service),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        started = await service.execution_tracker.start(execution, operator_id=payload.operator_id)
    except RunbookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return started


@router.post("/executions/{execution_id}/complete", dependencies=[_active_sub_guard], response_model=RunbookExecutionOut)
async def complete_execution(
    execution_id: uuid.UUID,
    payload: RunbookExecutionCompleteRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: RunbookService = Depends(get_runbook_service),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        completed = await service.execution_tracker.complete(
            execution,
            success=payload.success,
            result_notes=payload.result_notes,
            rollback_required=payload.rollback_required,
        )
    except RunbookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return completed


@router.post("/executions/{execution_id}/rollback", dependencies=[_active_sub_guard], response_model=RunbookExecutionOut)
async def rollback_execution(
    execution_id: uuid.UUID,
    payload: RunbookExecutionRollbackRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: RunbookService = Depends(get_runbook_service),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        rolled_back = await service.execution_tracker.rollback(execution, result_notes=payload.result_notes)
    except RunbookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return rolled_back
