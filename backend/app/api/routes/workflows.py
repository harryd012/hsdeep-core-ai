"""
Workflow Definition / Execution API (Enterprise Workflow Automation
Engine sprint).

Approval/pause/resume/cancel act on a WorkflowEXECUTION, not a workflow
DEFINITION (a definition doesn't "run") - so those live under
/workflows/executions/{execution_id}/..., mirroring the exact same
resolution runbooks.py already applied to Runbook/RunbookExecution last
sprint.

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
from app.repositories.workflow_execution_repository import WorkflowExecutionRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.workflow import (
    WorkflowApproveRequest,
    WorkflowCreate,
    WorkflowDetailOut,
    WorkflowExecutionActionRequest,
    WorkflowExecutionApproveRequest,
    WorkflowExecutionOut,
    WorkflowExecutionRejectRequest,
    WorkflowExecutionRequest,
    WorkflowExecutionSummaryOut,
    WorkflowNewVersionRequest,
    WorkflowOut,
    WorkflowStatisticsOut,
)
from app.services.workflow.execution_tracker import InvalidWorkflowExecutionTransition, WorkflowExecutionTracker
from app.services.workflow.workflow_engine import WorkflowDefinitionError, WorkflowEngine
from app.services.workflow_service import WorkflowNotExecutable, WorkflowNotFound, WorkflowService

router = APIRouter(prefix="/workflows", tags=["workflows"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_workflow_service(db: AsyncSession = Depends(get_db)) -> WorkflowService:
    execution_repo = WorkflowExecutionRepository(db)
    return WorkflowService(
        workflow_repo=WorkflowRepository(db),
        execution_repo=execution_repo,
        engine=WorkflowEngine(execution_repo=execution_repo, db=db),
    )


def get_execution_tracker(db: AsyncSession = Depends(get_db)) -> WorkflowExecutionTracker:
    return WorkflowExecutionTracker(execution_repo=WorkflowExecutionRepository(db))


# -- Workflow definitions ----------------------------------------------------
@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    workflow_type: str | None = None,
    trigger_type: str | None = None,
    is_active: bool | None = True,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: WorkflowService = Depends(get_workflow_service),
):
    return await service.list_workflows(
        tenant_id, workflow_type=workflow_type, trigger_type=trigger_type, is_active=is_active, search=search, limit=limit, offset=offset
    )


@router.get("/history", response_model=list[WorkflowExecutionSummaryOut])
async def list_execution_history(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    workflow_id: uuid.UUID | None = None,
    incident_id: uuid.UUID | None = None,
    alert_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Execution History deliverable - most recent first, across all
    workflows."""
    return await service.list_executions(
        tenant_id, workflow_id=workflow_id, incident_id=incident_id, alert_id=alert_id, status=status, limit=limit, offset=offset
    )


@router.get("/statistics", response_model=WorkflowStatisticsOut)
async def get_statistics(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Workflow Metrics deliverable - real aggregates only."""
    return await service.statistics(tenant_id)


@router.get("/{workflow_id}", response_model=WorkflowDetailOut)
async def get_workflow(
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return await service.get(workflow_id, tenant_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", dependencies=[_active_sub_guard], response_model=WorkflowOut, status_code=201)
async def create_workflow(
    payload: WorkflowCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        workflow = await service.create_workflow(tenant_id, **payload.model_dump())
    except WorkflowDefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return workflow


@router.put("/{workflow_id}", dependencies=[_active_sub_guard], response_model=WorkflowOut)
async def create_new_version(
    workflow_id: uuid.UUID,
    payload: WorkflowNewVersionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Creates a new DRAFT version (appends the current one to
    version_history) - does not overwrite live executions already running
    against the prior version's definition_snapshot."""
    try:
        workflow = await service.create_new_version(
            workflow_id, tenant_id, definition=payload.definition, change_summary=payload.change_summary
        )
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowDefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return workflow


@router.post("/{workflow_id}/approve", dependencies=[_active_sub_guard], response_model=WorkflowOut)
async def approve_workflow_version(
    workflow_id: uuid.UUID,
    payload: WorkflowApproveRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Manual approval workflow applied to the Workflow DEFINITION itself
    (distinct from an execution's approval nodes) - a version only
    becomes executable once approved."""
    try:
        workflow = await service.approve_current_version(workflow_id, tenant_id, approved_by=payload.approved_by)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowDefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return workflow


@router.delete("/{workflow_id}", dependencies=[_active_sub_guard], status_code=204)
async def delete_workflow(
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        await service.delete_workflow(workflow_id, tenant_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()


@router.post("/{workflow_id}/execute", dependencies=[_active_sub_guard], response_model=WorkflowExecutionOut, status_code=201)
async def execute_workflow(
    workflow_id: uuid.UUID,
    payload: WorkflowExecutionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
):
    """Queues a new WorkflowExecution and immediately drives it forward
    until it parks (waiting_approval/paused) or reaches a terminal state -
    same "execute means start now, park at the first human gate" contract
    as POST /runbooks/{id}/execute already established, generalized to a
    graph."""
    try:
        execution = await service.execute(
            workflow_id, tenant_id, trigger_source="manual",
            incident_id=payload.incident_id, alert_id=payload.alert_id,
            requested_by=payload.requested_by, initial_variables=payload.initial_variables,
        )
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowNotExecutable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    execution = await service.advance(execution.id, tenant_id)
    await db.commit()
    return execution


# -- Execution lifecycle ------------------------------------------------------
@router.get("/executions/{execution_id}", response_model=WorkflowExecutionOut)
async def get_execution(
    execution_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: WorkflowService = Depends(get_workflow_service),
):
    try:
        return await service.get_execution(execution_id, tenant_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/executions/{execution_id}/approve", dependencies=[_active_sub_guard], response_model=WorkflowExecutionOut)
async def approve_execution(
    execution_id: uuid.UUID,
    payload: WorkflowExecutionApproveRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
    tracker: WorkflowExecutionTracker = Depends(get_execution_tracker),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        execution = await tracker.approve(execution, node_id=payload.node_id, approved_by=payload.approved_by, comment=payload.comment)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidWorkflowExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    execution = await service.advance(execution.id, tenant_id)
    await db.commit()
    return execution


@router.post("/executions/{execution_id}/reject", dependencies=[_active_sub_guard], response_model=WorkflowExecutionOut)
async def reject_execution(
    execution_id: uuid.UUID,
    payload: WorkflowExecutionRejectRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
    tracker: WorkflowExecutionTracker = Depends(get_execution_tracker),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        execution = await tracker.reject(execution, node_id=payload.node_id, rejected_by=payload.rejected_by, reason=payload.reason)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidWorkflowExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # A rejection sends the graph down the approval node's rejection path
    # (the ApprovalNodeExecutor returns status="failed" on next run_step()
    # unless the definition routes a rejection edge - see node_executors.py)
    execution = await service.advance(execution.id, tenant_id)
    await db.commit()
    return execution


@router.post("/executions/{execution_id}/pause", dependencies=[_active_sub_guard], response_model=WorkflowExecutionOut)
async def pause_execution(
    execution_id: uuid.UUID,
    payload: WorkflowExecutionActionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
    tracker: WorkflowExecutionTracker = Depends(get_execution_tracker),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        execution = await tracker.pause(execution, actor_id=payload.actor_id, reason=payload.reason)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidWorkflowExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return execution


@router.post("/executions/{execution_id}/resume", dependencies=[_active_sub_guard], response_model=WorkflowExecutionOut)
async def resume_execution(
    execution_id: uuid.UUID,
    payload: WorkflowExecutionActionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
    tracker: WorkflowExecutionTracker = Depends(get_execution_tracker),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        execution = await tracker.resume(execution, actor_id=payload.actor_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidWorkflowExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    execution = await service.advance(execution.id, tenant_id)
    await db.commit()
    return execution


@router.post("/executions/{execution_id}/cancel", dependencies=[_active_sub_guard], response_model=WorkflowExecutionOut)
async def cancel_execution(
    execution_id: uuid.UUID,
    payload: WorkflowExecutionActionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
    tracker: WorkflowExecutionTracker = Depends(get_execution_tracker),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        execution = await tracker.cancel(execution, actor_id=payload.actor_id, reason=payload.reason)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidWorkflowExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return execution


@router.post("/executions/{execution_id}/rollback", dependencies=[_active_sub_guard], response_model=WorkflowExecutionOut)
async def rollback_execution(
    execution_id: uuid.UUID,
    payload: WorkflowExecutionActionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: WorkflowService = Depends(get_workflow_service),
    tracker: WorkflowExecutionTracker = Depends(get_execution_tracker),
):
    try:
        execution = await service.get_execution(execution_id, tenant_id)
        execution = await tracker.rollback(execution, actor_id=payload.actor_id, notes=payload.reason)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidWorkflowExecutionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return execution
