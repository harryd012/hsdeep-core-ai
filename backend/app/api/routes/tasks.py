"""
Task API routes — task lifecycle management.
"""
from __future__ import annotations
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal
from app.core.auth import get_tenant_id as get_current_tenant
from app.core.auth import require_permission
from app.repositories.agent_repository import AgentRepository
from app.core.db import get_db
from app.schemas.task import (
    TaskAssign,
    TaskCreate,
    TaskExecutionSubmit,
    TaskRead,
    TaskStatusChange,
    TaskUpdate,
    TaskVerification,
    TaskWorkerExecution,
)
from app.services.agent_service import AgentService
from app.services.task_routing import NoAgentAvailable, RoutingEngine
from app.services.task_service import (
    AgentTaskPermissionError,
    ApprovalNotRequiredError,
    ApprovalRequiredError,
    RISK_ORDER,
    TaskNotFound,
    TaskService,
    TaskTransitionNotAllowed,
)
from app.repositories.agent_repository import AgentRepository
from app.repositories.task_repository import TaskRepository

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_agent_service(db: AsyncSession = Depends(get_db)) -> AgentService:
    return AgentService(agent_repo=AgentRepository(db))


def get_task_service(db: AsyncSession = Depends(get_db)) -> TaskService:
    return TaskService(task_repo=TaskRepository(db))


def get_task_routing_engine(db: AsyncSession = Depends(get_db)) -> RoutingEngine:
    return RoutingEngine(agent_repo=AgentRepository(db))


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    status_filter: str | None = Query(None, alias="status"),
    task_type: str | None = None,
    priority: str | None = None,
    assigned_agent_id: uuid.UUID | None = None,
    customer_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """List tasks with optional filters."""
    return await service.list_tasks(
        tenant_id,
        status=status_filter,
        task_type=task_type,
        priority=priority,
        assigned_agent_id=assigned_agent_id,
        customer_id=customer_id,
        project_id=project_id,
        correlation_id=correlation_id,
        limit=limit,
        offset=offset,
    )


@router.get("/by-agent/{agent_id}", response_model=list[TaskRead])
async def list_tasks_by_agent(
    agent_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """List tasks assigned to a specific agent."""
    return await service.list_by_agent(agent_id, tenant_id, status=status_filter)


@router.get("/by-incident/{incident_id}", response_model=list[TaskRead])
async def list_tasks_by_incident(
    incident_id: uuid.UUID,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """List tasks linked to a specific incident."""
    return await service.list_by_incident(incident_id, tenant_id)


@router.get("/counts", response_model=dict[str, int])
async def count_tasks_by_status(
    assigned_agent_id: uuid.UUID | None = None,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Count tasks by status for dashboard views."""
    return await service.count_by_status(tenant_id, assigned_agent_id=assigned_agent_id)


def _request_actor(request: Request) -> str | None:
    """The authenticated human behind a request, for audit attribution.

    The principal is resolved once by the auth layer and published on
    ``request.state`` (see ``_publish_principal`` in ``app.core.auth``), so this
    reads the already-resolved identity instead of parsing credentials itself:
    no second identity system, no token/password handling, and nothing
    sensitive is ever stored. Only the human-readable identity already persisted
    for the user (their email) or, failing that, their stable id is recorded.

    Returns ``None`` when the request carries no principal, so the service keeps
    its existing fallback rather than inventing a user.
    """
    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is None:
        return None
    email = getattr(principal, "email", None)
    if email:
        return str(email)
    user_id = getattr(principal, "user_id", None)
    return f"user:{user_id}" if user_id else None


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("tasks.write"))])
async def create_task(
    body: TaskCreate,
    request: Request,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Create a new task.

    Actor attribution (M9.12b): when the request was authenticated, the human
    who created the task is recorded in the audit trail. A human API operation
    is never silently attributed to ``SYSTEM``.
    """
    task = await service.create_task(tenant_id, actor=_request_actor(request), **body.model_dump())
    await db.commit()
    return task


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: uuid.UUID,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Get a specific task by ID."""
    try:
        return await service.get(task_id, tenant_id)
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
async def _assert_assignable_agent(
    db: AsyncSession,
    service: TaskService,
    task_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> None:
    """Shared server-side authorization for assignment AND reassignment.

    Kept in one place on purpose: duplicating these checks is how a security
    rule drifts between two call sites. Enforces, before any write:

    * tenant ownership - the tenant-scoped AgentRepository lookup makes another
      tenant's agent id indistinguishable from an unknown one (404), leaking no
      cross-tenant existence;
    * capability - when the agent declares supported_task_types, the task's type
      must be among them;
    * eligibility - the Agent model's own can_accept_task predicate (active,
      available/busy, under max workload) is reused, never re-derived.

    The task is fetched tenant-scoped, so a foreign task id is a 404 here too.
    """
    agent = await AgentRepository(db).get(agent_id, tenant_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found in this tenant",
        )
    task = await service.get(task_id, tenant_id)
    if agent.supported_task_types and task.task_type not in agent.supported_task_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Agent {agent.name!r} does not support task type {task.task_type!r}",
        )
    if not agent.can_accept_task:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Agent {agent.name!r} cannot accept work "
                f"(status={agent.status}, availability={agent.availability}, "
                f"workload={agent.current_workload}/{agent.max_workload})"
            ),
        )


@router.post("/{task_id}/assign", response_model=TaskRead)
async def assign_task(
    task_id: uuid.UUID,
    request: Request,
    body: TaskAssign | None = None,
    assigned_agent_id: uuid.UUID | None = Query(default=None),
    assigned_by_agent_id: uuid.UUID | None = Query(default=None),
    manager_agent_id: uuid.UUID | None = Query(default=None),
    service: TaskService = Depends(get_task_service),
    agent_service: AgentService = Depends(get_agent_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_permission("tickets.write")),
):
    """Assign a task to an agent (human-controlled assignment).

    Server-side authorization (M9.12b). A frontend that hides agents is never
    the control, so this route enforces, before any write:

    * tenant ownership — ``AgentRepository.get`` is tenant-scoped, so another
      tenant's agent id is indistinguishable from an unknown one (404) and no
      cross-tenant existence is leaked;
    * capability — when the agent declares ``supported_task_types``, the task's
      type must be among them;
    * eligibility — the Agent model's own ``can_accept_task`` predicate
      (active, available/busy, under max workload) is reused rather than
      re-deriving a second rule;
    * lifecycle — the service's existing ``_validate_transition`` still owns
      CREATED -> ASSIGNED.

    The authenticated principal is recorded in the audit trail so a human
    assignment is never attributed to SYSTEM.
    """
    try:
        payload = body or TaskAssign(
            assigned_agent_id=assigned_agent_id,
            assigned_by_agent_id=assigned_by_agent_id,
            manager_agent_id=manager_agent_id,
        )
        if payload.assigned_agent_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="assigned_agent_id is required",
            )
        await _assert_assignable_agent(
            db, service, task_id, tenant_id, payload.assigned_agent_id
        )
        task = await service.assign_task(
            task_id,
            tenant_id,
            payload.assigned_agent_id,
            payload.assigned_by_agent_id,
            payload.manager_agent_id,
            actor=_request_actor(request),
        )
        await agent_service.assign_task(payload.assigned_agent_id, tenant_id)
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/route", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def auto_route_task(
    task_id: uuid.UUID,
    department: str | None = None,
    level: str | None = None,
    service: TaskService = Depends(get_task_service),
    agent_service: AgentService = Depends(get_agent_service),
    routing: RoutingEngine = Depends(get_task_routing_engine),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Auto-route a task to the best available agent."""
    try:
        task = await service.get(task_id, tenant_id)
        agent = await routing.find_candidates(
            tenant_id, task, department=department, level=level, limit=1
        )
        if not agent:
            raise NoAgentAvailable("No suitable agent found")
        assigned = await service.assign_task(
            task_id, tenant_id, agent[0].id, manager_agent_id=agent[0].manager_id
        )
        await agent_service.assign_task(agent[0].id, tenant_id)
        return assigned
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (TaskTransitionNotAllowed, NoAgentAvailable) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/accept", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def accept_task(
    task_id: uuid.UUID,
    agent_id: uuid.UUID = Query(...),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Agent accepts the assigned task."""
    try:
        task = await service.accept_task(task_id, tenant_id, agent_id)
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/investigate", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def start_investigation(
    task_id: uuid.UUID,
    agent_id: uuid.UUID = Query(...),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Agent starts investigation."""
    try:
        task = await service.start_investigation(task_id, tenant_id, agent_id)
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/submit", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
@router.post("/{task_id}/submit-execution", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def submit_for_verification(
    task_id: uuid.UUID,
    body: TaskExecutionSubmit,
    agent_id: uuid.UUID = Query(...),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Agent submits work for verification."""
    try:
        task = await service.submit_execution(
            task_id,
            tenant_id,
            agent_id,
            body.execution_result,
            evidence=body.evidence,
        )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/verify", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def verify_task(
    task_id: uuid.UUID,
    body: TaskVerification,
    verified_by_agent_id: uuid.UUID | None = Query(default=None),
    agent_id: uuid.UUID | None = Query(default=None),
    service: TaskService = Depends(get_task_service),
    agent_service: AgentService = Depends(get_agent_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Verify task completion."""
    try:
        verifier_id = verified_by_agent_id or agent_id
        if verifier_id is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="agent_id is required")
        task = await service.verify_task(
            task_id,
            tenant_id,
            verifier_id,
            body.verification_result,
            passed=body.passed,
        )
        if task.assigned_agent_id:
            await agent_service.complete_task(
                task.assigned_agent_id, tenant_id, success=body.passed
            )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/record", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def record_task(
    task_id: uuid.UUID,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Record verified task (final state)."""
    try:
        task = await service.record_task(task_id, tenant_id)
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/fail", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def fail_task(
    task_id: uuid.UUID,
    body: TaskStatusChange,
    agent_id: uuid.UUID = Query(...),
    service: TaskService = Depends(get_task_service),
    agent_service: AgentService = Depends(get_agent_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Mark task as failed."""
    try:
        task = await service.fail_task(
            task_id, tenant_id, agent_id, body.reason or "Unknown failure"
        )
        if task.assigned_agent_id:
            await agent_service.complete_task(
                task.assigned_agent_id, tenant_id, success=False
            )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/retry", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def retry_task(
    task_id: uuid.UUID,
    agent_id: uuid.UUID = Query(...),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed task."""
    try:
        task = await service.retry_task(task_id, tenant_id, agent_id)
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/escalate", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def escalate_task(
    task_id: uuid.UUID,
    body: TaskStatusChange,
    escalated_by_agent_id: uuid.UUID = Query(...),
    target_agent_id: uuid.UUID | None = None,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Escalate a task to a higher authority."""
    try:
        task = await service.escalate_task(
            task_id,
            tenant_id,
            escalated_by_agent_id,
            target_agent_id=target_agent_id,
            reason=body.reason,
        )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


async def _assert_approver_authorized(
    db: AsyncSession,
    service: TaskService,
    task_id: uuid.UUID,
    tenant_id: uuid.UUID,
    approver_agent_id: uuid.UUID | None,
) -> None:
    """Reuse the existing Agent authority fields - no new RBAC is invented.

    When a call names an approving agent, that agent must belong to this tenant
    (tenant-scoped lookup: a foreign id is reported exactly like an unknown
    one), must have ``can_approve`` set, and its ``approval_limit`` must be at
    or above the task's risk level. Human approval is authorized by the
    existing authenticated principal plus the tenant context.
    """
    if approver_agent_id is None:
        return
    agent = await AgentRepository(db).get(approver_agent_id, tenant_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approver agent not found in this tenant",
        )
    if not agent.can_approve:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent {agent.name!r} is not authorized to approve",
        )
    task = await service.get(task_id, tenant_id)
    task_risk = RISK_ORDER.get((task.risk_level or "low").lower(), 0)
    limit = RISK_ORDER.get((agent.approval_limit or "low").lower(), 0)
    if task_risk > limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Agent {agent.name!r} cannot approve a {task.risk_level!r} risk "
                f"task (approval_limit={agent.approval_limit!r})"
            ),
        )


@router.post("/{task_id}/reassign", response_model=TaskRead)
async def reassign_task(
    task_id: uuid.UUID,
    request: Request,
    body: TaskAssign | None = None,
    assigned_agent_id: uuid.UUID | None = Query(default=None),
    assigned_by_agent_id: uuid.UUID | None = Query(default=None),
    manager_agent_id: uuid.UUID | None = Query(default=None),
    reason: str | None = Query(default=None),
    service: TaskService = Depends(get_task_service),
    agent_service: AgentService = Depends(get_agent_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_permission("tickets.write")),
):
    """Reassign an already-assigned task to a different agent.

    Reuses the SAME server-side authorization as assignment (shared
    ``_assert_assignable_agent``: tenant-scoped target agent, capability,
    eligibility) and the existing lifecycle authority - only a state the
    declared matrix allows to reach ``reassigned`` may be moved, so a completed
    or cancelled task cannot be resurrected by reassigning it.

    The approval fields are never touched: reassignment changes who executes the
    work, not what was approved, so it can neither grant nor revoke approval and
    the protected-action gate applies exactly as before.

    Workload follows the work: the new agent is incremented and the previous one
    released through the existing counters. The actor is the authenticated
    principal - a client cannot supply audit identity.
    """
    try:
        payload = body or TaskAssign(
            assigned_agent_id=assigned_agent_id,
            assigned_by_agent_id=assigned_by_agent_id,
            manager_agent_id=manager_agent_id,
        )
        if payload.assigned_agent_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="assigned_agent_id is required",
            )
        await _assert_assignable_agent(
            db, service, task_id, tenant_id, payload.assigned_agent_id
        )
        previous = await service.get(task_id, tenant_id)
        previous_agent_id = previous.assigned_agent_id

        task = await service.reassign_task(
            task_id,
            tenant_id,
            payload.assigned_agent_id,
            payload.assigned_by_agent_id,
            payload.manager_agent_id,
            actor=_request_actor(request),
            reason=reason,
        )
        await agent_service.assign_task(payload.assigned_agent_id, tenant_id)
        if previous_agent_id and previous_agent_id != payload.assigned_agent_id:
            await agent_service.release_task(previous_agent_id, tenant_id)
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/request-approval", response_model=TaskRead)
async def request_task_approval(
    task_id: uuid.UUID,
    request: Request,
    body: TaskStatusChange | None = None,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_permission("tickets.write")),
):
    """Request approval for a task that requires it.

    The requester is the authenticated principal resolved by the auth layer -
    never a client-supplied actor string.
    """
    try:
        task = await service.request_approval(
            task_id,
            tenant_id,
            actor=_request_actor(request),
            reason=body.reason if body else None,
        )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ApprovalNotRequiredError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/approve", response_model=TaskRead)
async def approve_task(
    task_id: uuid.UUID,
    request: Request,
    body: TaskStatusChange | None = None,
    approver_agent_id: uuid.UUID | None = Query(default=None),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_permission("tickets.execute")),
):
    """Approve a task awaiting approval, so protected execution may proceed.

    Only a task that is genuinely WAITING_FOR_APPROVAL can be approved, and the
    deciding actor recorded in the audit trail is the authenticated principal.
    """
    try:
        await _assert_approver_authorized(
            db, service, task_id, tenant_id, approver_agent_id
        )
        task = await service.approve_task(
            task_id,
            tenant_id,
            actor=_request_actor(request),
            reason=body.reason if body else None,
        )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ApprovalNotRequiredError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/reject", response_model=TaskRead)
async def reject_task(
    task_id: uuid.UUID,
    request: Request,
    body: TaskStatusChange | None = None,
    approver_agent_id: uuid.UUID | None = Query(default=None),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_permission("tickets.execute")),
):
    """Reject a task awaiting approval. The protected action is never executed."""
    try:
        await _assert_approver_authorized(
            db, service, task_id, tenant_id, approver_agent_id
        )
        task = await service.reject_task(
            task_id,
            tenant_id,
            actor=_request_actor(request),
            reason=body.reason if body else None,
        )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ApprovalNotRequiredError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task(
    task_id: uuid.UUID,
    request: Request,
    body: TaskStatusChange | None = None,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_permission("tickets.write")),
):
    """Cancel a task.

    Cancellation is audited against the authenticated principal, and the
    existing transition matrix remains the authority on which states may be
    cancelled - so a task that is already executing, verifying or verified is
    refused rather than silently killed mid-flight.
    """
    try:
        task = await service.cancel_task(
            task_id,
            tenant_id,
            reason=body.reason if body else None,
            actor=_request_actor(request),
        )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{task_id}/worker-execute", response_model=TaskRead)
async def worker_execute_task(
    task_id: uuid.UUID,
    body: TaskWorkerExecution,
    agent_id: uuid.UUID = Query(...),
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_permission("tickets.execute")),
):
    """Execute a real assigned task through the worker gate."""
    try:
        task = await service.run_worker_execution(
            task_id,
            tenant_id,
            agent_id=agent_id,
            tool_name=body.tool_name,
            execution_input=body.execution_input,
            allowed_tools=set(body.allowed_tools or []),
        )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except AgentTaskPermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ApprovalRequiredError as e:
        # The P0 gate refused protected execution: an authorization failure,
        # not a server error. Nothing external was attempted.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/{task_id}/execute", response_model=TaskRead)
async def start_execution(
    task_id: uuid.UUID,
    agent_id: uuid.UUID = Query(...),
    execution_plan: dict | None = None,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_permission("tickets.execute")),
):
    """Agent starts execution."""
    try:
        task = await service.start_execution(
            task_id, tenant_id, agent_id, execution_plan=execution_plan
        )
        await db.commit()
        return task
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TaskTransitionNotAllowed as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ApprovalRequiredError as e:
        # The gate now also guards the transition INTO executing, so starting
        # execution on an unapproved task is an authorization refusal (403),
        # not a state conflict.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.patch("/{task_id}", response_model=TaskRead, dependencies=[Depends(require_permission("tasks.write"))])
async def update_task(
    task_id: uuid.UUID,
    body: TaskUpdate,
    service: TaskService = Depends(get_task_service),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """Update task details."""
    try:
        task = await service.get(task_id, tenant_id)
        return await service.task_repo.update(
            task, **body.model_dump(exclude_unset=True)
        )
    except TaskNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))