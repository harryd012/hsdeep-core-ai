"""
Task Service — manages the task lifecycle in the virtual company.
Follows the same service pattern as IncidentService.

Task lifecycle:
    CREATED → ASSIGNED → ACCEPTED → INVESTIGATING → EXECUTING →
    VERIFYING → VERIFIED → RECORDED

Failure path:
    EXECUTING → FAILED → RETRY / ESCALATE / REASSIGN
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.task import Task
from app.repositories.task_repository import TaskRepository
from app.services import tool_gateway


class TaskNotFound(Exception):
    pass


class TaskTransitionNotAllowed(Exception):
    pass


class AgentTaskPermissionError(Exception):
    pass


class ApprovalNotRequiredError(Exception):
    """Approval was requested for a task that does not require it."""


class ApprovalRequiredError(Exception):
    """Protected execution was refused because a required approval is missing.

    Raised by the centralized gate at the execution boundary. It is an
    authorization refusal, not a transient failure: nothing external has been
    attempted at the point it is raised.
    """


# -- Approval states (P0 security boundary) --------------------------------
APPROVAL_NOT_REQUIRED = "not_required"
APPROVAL_WAITING = "waiting_for_approval"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"

#: Riskiest level an approver may sign off, keyed by the Task model's existing
#: risk_level vocabulary. Used to compare a task's risk against an agent's
#: ``approval_limit`` (an existing Agent field).
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# Allowed task lifecycle transitions
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"assigned", "cancelled"},
    "assigned": {"accepted", "reassigned", "cancelled"},
    "accepted": {"investigating", "cancelled"},
    "investigating": {"executing", "escalated", "failed"},
    "executing": {"verifying", "failed"},
    "verifying": {"verified", "failed"},
    "verified": {"recorded"},
    "recorded": set(),
    "failed": {"retrying", "escalated", "reassigned", "cancelled"},
    "retrying": {"executing", "failed"},
    "escalated": {"investigating", "executing", "cancelled"},
    "reassigned": {"assigned", "accepted"},
    "cancelled": set(),
}

_TERMINAL_STATES = {"recorded", "cancelled"}


class TaskService:
    def __init__(self, task_repo: TaskRepository):
        self.task_repo = task_repo

    async def get(self, task_id: uuid.UUID, tenant_id: uuid.UUID) -> Task:
        task = await self.task_repo.get(task_id, tenant_id)
        if task is None:
            raise TaskNotFound(f"Task {task_id} not found")
        return task

    async def get_by_task_number(self, task_number: str, tenant_id: uuid.UUID) -> Task:
        task = await self.task_repo.get_by_task_number(task_number, tenant_id)
        if task is None:
            raise TaskNotFound(f"Task '{task_number}' not found")
        return task

    async def list_tasks(self, tenant_id: uuid.UUID, **filters) -> list[Task]:
        return await self.task_repo.list(tenant_id, **filters)

    async def list_by_agent(
        self, agent_id: uuid.UUID, tenant_id: uuid.UUID, **filters
    ) -> list[Task]:
        return await self.task_repo.list_by_agent(agent_id, tenant_id, **filters)

    async def list_by_incident(
        self, incident_id: uuid.UUID, tenant_id: uuid.UUID, **filters
    ) -> list[Task]:
        return await self.task_repo.list_by_incident(incident_id, tenant_id, **filters)

    async def count_by_status(self, tenant_id: uuid.UUID, **filters) -> dict[str, int]:
        return await self.task_repo.count_by_status(tenant_id, **filters)

    async def create_task(self, tenant_id: uuid.UUID, **fields) -> Task:
        """Create a new task in CREATED state."""
        task_number = await self.task_repo.next_task_number(tenant_id)
        now = datetime.now(timezone.utc)
        # POPPED, not forwarded: `**fields` below goes straight to the ORM and
        # Task has no `actor` column, so a caller-supplied actor must not leak
        # into the insert.
        actor = fields.pop("actor", None)

        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "created",
            "from_status": None,
            "to_status": "created",
            # Actor attribution (Phase 9.12): a task created BY A HUMAN is never
            # recorded as "system". The caller that knows the authenticated
            # principal passes `actor`; only genuinely platform-generated work
            # falls back to the SYSTEM marker.
            "actor": actor or fields.get("owner_command") or "SYSTEM",
        }

        return await self.task_repo.create(
            tenant_id=tenant_id,
            task_number=task_number,
            status="created",
            audit_log=[audit_entry],
            **fields,
        )

    async def assign_task(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        assigned_agent_id: uuid.UUID,
        assigned_by_agent_id: uuid.UUID | None = None,
        manager_agent_id: uuid.UUID | None = None,
        actor: str | None = None,
    ) -> Task:
        """Assign a task to an agent (CREATED → ASSIGNED).

        Actor attribution (M9.12b): a human assignment records who ordered it.
        ``actor`` is audit metadata only and is never written to a Task column.
        """
        task = await self.get(task_id, tenant_id)
        self._validate_transition(task.status, "assigned")

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "assigned",
            "from_status": task.status,
            "to_status": "assigned",
            # Who ordered the assignment: the authenticated human. When an
            # agent-driven flow (explicit opt-in) assigns work, the assigning
            # agent is named; only genuinely platform-generated work says
            # SYSTEM. A human action is never recorded as SYSTEM.
            "actor": actor
            or (f"agent:{assigned_by_agent_id}" if assigned_by_agent_id else "SYSTEM"),
            "assigned_to": str(assigned_agent_id),
            "assigned_agent_id": str(assigned_agent_id),
            "assigned_by_agent_id": str(assigned_by_agent_id) if assigned_by_agent_id else None,
        }

        task.audit_log.append(audit_entry)
        return await self.task_repo.update(
            task,
            status="assigned",
            assigned_agent_id=assigned_agent_id,
            assigned_by_agent_id=assigned_by_agent_id,
            manager_agent_id=manager_agent_id,
            assigned_at=now,
        )

    async def reassign_task(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        new_agent_id: uuid.UUID,
        assigned_by_agent_id: uuid.UUID | None = None,
        manager_agent_id: uuid.UUID | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> Task:
        """Move an already-assigned task to a different agent.

        Uses the EXISTING state machine (``assigned|failed → reassigned`` then
        ``reassigned → assigned``), recorded as one auditable pair and persisted
        in a single update, so the task ends where the operator expects without
        inventing a second lifecycle.

        Deliberately does NOT touch the approval fields: reassignment changes
        WHO executes the work, not WHAT was approved, so it can neither grant
        approval to a task that has none nor revoke one that exists. The
        protected-action gate therefore applies to a reassigned task exactly as
        before (invariants E and F).
        """
        task = await self.get(task_id, tenant_id)

        # Deterministic no-op handling: repeating the same reassignment is
        # refused as a conflict rather than silently succeeding or churning
        # audit history with an identical event.
        if task.assigned_agent_id == new_agent_id and task.status == "assigned":
            raise TaskTransitionNotAllowed(
                f"Task {task_id} is already assigned to agent {new_agent_id}"
            )

        self._validate_transition(task.status, "reassigned")
        previous_agent_id = task.assigned_agent_id
        now = datetime.now(timezone.utc)
        audit_actor = actor or (
            f"agent:{assigned_by_agent_id}" if assigned_by_agent_id else "SYSTEM"
        )

        task.audit_log.append(
            {
                "timestamp": now.isoformat(),
                "action": "reassigned",
                "from_status": task.status,
                "to_status": "reassigned",
                "actor": audit_actor,
                "previous_agent_id": str(previous_agent_id) if previous_agent_id else None,
                "new_agent_id": str(new_agent_id),
                "reason": reason,
            }
        )

        # The existing machine models the second half of the move explicitly.
        self._validate_transition("reassigned", "assigned")
        task.audit_log.append(
            {
                "timestamp": now.isoformat(),
                "action": "assigned",
                "from_status": "reassigned",
                "to_status": "assigned",
                "actor": audit_actor,
                "assigned_to": str(new_agent_id),
                "assigned_agent_id": str(new_agent_id),
                "assigned_by_agent_id": (
                    str(assigned_by_agent_id) if assigned_by_agent_id else None
                ),
                "previous_agent_id": str(previous_agent_id) if previous_agent_id else None,
                "reason": reason,
            }
        )

        return await self.task_repo.update(
            task,
            status="assigned",
            assigned_agent_id=new_agent_id,
            assigned_by_agent_id=assigned_by_agent_id,
            manager_agent_id=manager_agent_id,
            assigned_at=now,
        )

    @staticmethod
    def _require_assigned_agent(task: Task, agent_id: uuid.UUID) -> None:
        if task.assigned_agent_id is not None and task.assigned_agent_id != agent_id:
            raise AgentTaskPermissionError(
                f"Agent {agent_id} is not the assigned worker for task {task.id}"
            )

    async def accept_task(
        self, task_id: uuid.UUID, tenant_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Task:
        """Agent accepts the task (ASSIGNED → ACCEPTED)."""
        task = await self.get(task_id, tenant_id)
        self._validate_transition(task.status, "accepted")
        self._require_assigned_agent(task, agent_id)

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "accepted",
            "from_status": task.status,
            "to_status": "accepted",
            "agent_id": str(agent_id),
        }

        task.audit_log.append(audit_entry)
        return await self.task_repo.update(
            task,
            status="accepted",
            accepted_at=now,
        )

    async def start_execution(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        execution_plan: dict | None = None,
    ) -> Task:
        """Agent starts execution (INVESTIGATING → EXECUTING)."""
        task = await self.get(task_id, tenant_id)
        # M9.13: the same centralized gate guards the transition INTO executing,
        # not just the adapter call, so a task that requires approval cannot be
        # advanced along the lifecycle as if it had been executed (which would
        # otherwise let an agent reach submit_for_verification from a task that
        # was never approved). The gate itself is unchanged.
        self._assert_approval_gate(task)
        self._validate_transition(task.status, "executing")
        self._require_assigned_agent(task, agent_id)

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "execution_started",
            "from_status": task.status,
            "to_status": "executing",
            "agent_id": str(agent_id),
        }

        task.audit_log.append(audit_entry)
        updates = {"status": "executing", "started_at": task.started_at or now}
        if execution_plan:
            updates["execution_plan"] = execution_plan
        return await self.task_repo.update(task, **updates)

    async def submit_execution(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        execution_result: dict,
        *,
        evidence: dict | None = None,
    ) -> Task:
        """Compatibility alias for the execution submission lifecycle."""
        return await self.submit_for_verification(
            task_id,
            tenant_id,
            agent_id,
            execution_result,
            evidence=evidence,
        )

    async def submit_for_verification(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        execution_result: dict,
        evidence: dict | None = None,
    ) -> Task:
        """Agent submits work for verification (EXECUTING → VERIFYING)."""
        task = await self.get(task_id, tenant_id)
        self._validate_transition(task.status, "verifying")
        self._require_assigned_agent(task, agent_id)

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "submitted_for_verification",
            "from_status": task.status,
            "to_status": "verifying",
            "agent_id": str(agent_id),
        }

        task.audit_log.append(audit_entry)
        updates = {
            "status": "verifying",
            "execution_result": execution_result,
        }
        if evidence:
            updates["evidence"] = evidence
        return await self.task_repo.update(task, **updates)

    async def verify_task(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        verified_by_agent_id: uuid.UUID,
        verification_result: dict,
        *,
        passed: bool = True,
    ) -> Task:
        """Verify task completion (VERIFYING → VERIFIED or FAILED)."""
        task = await self.get(task_id, tenant_id)
        self._validate_transition(task.status, "verified" if passed else "failed")

        now = datetime.now(timezone.utc)
        new_status = "verified" if passed else "failed"
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "verified" if passed else "verification_failed",
            "from_status": task.status,
            "to_status": new_status,
            "agent_id": str(verified_by_agent_id),
            "result": verification_result,
        }

        task.audit_log.append(audit_entry)
        updates = {
            "status": new_status,
            "verification_result": verification_result,
            "verified_by_agent_id": verified_by_agent_id,
        }
        if passed:
            updates["verified"] = True
            updates["verified_at"] = now
        return await self.task_repo.update(task, **updates)

    # -- Approval workflow (P0 security boundary) --------------------------
    #
    # Approval is persisted state on the existing Task, decided only by an
    # authenticated principal supplied by the route (never by a client-provided
    # actor string). The lifecycle validator keeps owning Task.status; these
    # methods own approval_status and append to the existing audit_log.
    async def request_approval(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> Task:
        """Move a task that genuinely requires approval into WAITING_FOR_APPROVAL."""
        task = await self.get(task_id, tenant_id)
        if not task.approval_required:
            raise ApprovalNotRequiredError(
                f"Task {task_id} does not require approval"
            )
        if task.approval_status == APPROVAL_WAITING:
            raise TaskTransitionNotAllowed(
                f"Task {task_id} is already waiting for approval"
            )
        if task.approval_status == APPROVAL_APPROVED:
            raise TaskTransitionNotAllowed(f"Task {task_id} is already approved")
        # Fidelity to the documented lifecycle: approval is requested for work
        # that has been accepted (an execution-eligible status), not while the
        # task is still being routed. Narrowing only - the execution gate that
        # protects the Action Plane is unchanged and independent of this.
        if task.status not in {"accepted", "investigating"}:
            raise TaskTransitionNotAllowed(
                f"Task {task_id} cannot request approval while {task.status}"
            )

        now = datetime.now(timezone.utc)
        task.audit_log.append(
            {
                "timestamp": now.isoformat(),
                "action": "approval_requested",
                "from_status": task.status,
                "to_status": task.status,
                "approval_status": APPROVAL_WAITING,
                "actor": actor or "SYSTEM",
                "reason": reason,
            }
        )
        return await self.task_repo.update(
            task,
            approval_status=APPROVAL_WAITING,
            approval_requested_by=actor,
            approval_requested_at=now,
        )

    async def approve_task(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> Task:
        """Grant approval so protected execution may proceed."""
        task = await self.get(task_id, tenant_id)
        if not task.approval_required:
            raise ApprovalNotRequiredError(
                f"Task {task_id} does not require approval"
            )
        if task.approval_status != APPROVAL_WAITING:
            raise TaskTransitionNotAllowed(
                f"Task {task_id} is not awaiting approval "
                f"(approval_status={task.approval_status})"
            )

        now = datetime.now(timezone.utc)
        task.audit_log.append(
            {
                "timestamp": now.isoformat(),
                "action": "approval_granted",
                "from_status": task.status,
                "to_status": task.status,
                "approval_status": APPROVAL_APPROVED,
                "actor": actor or "SYSTEM",
                "reason": reason,
            }
        )
        return await self.task_repo.update(
            task,
            approval_status=APPROVAL_APPROVED,
            approval_decided_by=actor,
            approval_decided_at=now,
            approval_result=reason or "approved",
        )

    async def reject_task(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> Task:
        """Refuse approval. The protected action is never executed."""
        task = await self.get(task_id, tenant_id)
        if not task.approval_required:
            raise ApprovalNotRequiredError(
                f"Task {task_id} does not require approval"
            )
        if task.approval_status != APPROVAL_WAITING:
            raise TaskTransitionNotAllowed(
                f"Task {task_id} is not awaiting approval "
                f"(approval_status={task.approval_status})"
            )

        now = datetime.now(timezone.utc)
        task.audit_log.append(
            {
                "timestamp": now.isoformat(),
                "action": "approval_rejected",
                "from_status": task.status,
                "to_status": task.status,
                "approval_status": APPROVAL_REJECTED,
                "actor": actor or "SYSTEM",
                "reason": reason,
            }
        )
        return await self.task_repo.update(
            task,
            approval_status=APPROVAL_REJECTED,
            approval_decided_by=actor,
            approval_decided_at=now,
            approval_result=reason or "rejected",
        )

    @staticmethod
    def _assert_approval_gate(task: Task) -> None:
        """Centralized protected-execution gate (P0).

        Invariant: when a task requires approval, protected infrastructure
        execution MUST NOT START unless the persisted approval state says
        APPROVED.

        The decision reads database state only. It cannot be influenced by
        frontend state, browser behaviour, client-provided approval flags,
        local storage or UI controls, and it runs BEFORE any tool adapter is
        resolved or invoked, so no external command is issued when it refuses.
        """
        if not task.approval_required:
            return
        if task.approval_status == APPROVAL_APPROVED:
            return
        raise ApprovalRequiredError(
            f"Task {task.id} requires approval before protected execution "
            f"(approval_status={task.approval_status})"
        )

    async def run_worker_execution(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        agent_id: uuid.UUID,
        tool_name: str | None = None,
        execution_input: dict | None = None,
        allowed_tools: set[str] | None = None,
    ) -> Task:
        """Execute a real assigned task through the worker gate.

        This enforces the minimum real backend contract expected by Phase 6:
        - the task must be assigned to the caller agent
        - the agent must be authorized for the requested tool (when provided)
        - execution is recorded in the task audit log and result payload
        - the task is advanced to verifying so the command center reflects the
          real backend state instead of fake synthetic lifecycle transitions.
        """
        task = await self.get(task_id, tenant_id)
        # P0 PROTECTED-ACTION GATE: refuse before any tool adapter is resolved
        # or invoked. Reads persisted approval state only.
        self._assert_approval_gate(task)
        if task.assigned_agent_id is not None and task.assigned_agent_id != agent_id:
            raise AgentTaskPermissionError(
                f"Agent {agent_id} is not the assigned worker for task {task_id}"
            )

        tool_allowlist = allowed_tools or set()
        if tool_name and tool_name not in tool_allowlist:
            raise AgentTaskPermissionError(
                f"Tool '{tool_name}' is not authorized for agent {agent_id}"
            )

        if task.status not in {"accepted", "investigating", "executing"}:
            self._validate_transition(task.status, "executing")

        now = datetime.now(timezone.utc)

        # If a tool is requested, dispatch to the registered adapter via
        # the central tool gateway. If no adapter exists, record an honest
        # 'tool_unavailable' audit entry and raise an explicit exception so
        # callers (API/routes) can surface the capability-unavailable state.
        execution_result: dict = {}
        evidence: dict = {}

        if tool_name:
            adapter = tool_gateway.get_adapter(tool_name)
            if adapter is None:
                task.audit_log.append(
                    {
                        "timestamp": now.isoformat(),
                        "action": "tool_unavailable",
                        "from_status": task.status,
                        "to_status": task.status,
                        "agent_id": str(agent_id),
                        "tool": tool_name,
                        "reason": "adapter_unavailable",
                    }
                )
                # Persist the audit entry and return an honest failure to the
                # caller instead of fabricating execution results.
                await self.task_repo.update(task, audit_log=task.audit_log)
                raise AgentTaskPermissionError(f"Tool '{tool_name}' is unavailable for execution")

            # Call the adapter. Adapters return a StepResult-like dict with
            # keys: success(bool), output(dict), error(str|None).
            try:
                result = await adapter.execute(execution_input or {}, context={
                    "task_id": str(task_id),
                    "agent_id": str(agent_id),
                    "tenant_id": str(tenant_id),
                })
            except Exception as exc:
                task.audit_log.append(
                    {
                        "timestamp": now.isoformat(),
                        "action": "worker_execution_error",
                        "from_status": task.status,
                        "to_status": "failed",
                        "agent_id": str(agent_id),
                        "tool": tool_name,
                        "reason": str(exc),
                    }
                )
                await self.task_repo.update(task, audit_log=task.audit_log, status="failed", failure_reason=str(exc))
                raise

            execution_result = {
                "success": bool(result.get("success")),
                "message": result.get("output") or result.get("error") or "",
                "timestamp": now.isoformat(),
            }
            evidence = {"tool": tool_name, "agent_id": str(agent_id), "records": result.get("output")}

        # Record worker_execution audit and update state to executing
        task.audit_log.append(
            {
                "timestamp": now.isoformat(),
                "action": "worker_execution",
                "from_status": task.status,
                "to_status": "executing",
                "agent_id": str(agent_id),
                "tool": tool_name,
            }
        )

        await self.task_repo.update(
            task,
            status="executing",
            execution_result=execution_result,
            execution_plan={"tool": tool_name, "input": execution_input or {}, "approved_by": str(agent_id)},
            started_at=task.started_at or now,
            audit_log=task.audit_log,
        )

        # If no tool was requested, proceed to submit_for_verification with
        # the recorded execution_result (may be empty). Otherwise submit
        # the adapter-produced result and evidence.
        return await self.submit_for_verification(
            task_id,
            tenant_id,
            agent_id,
            {
                "status": "completed" if execution_result.get("success") else "failed",
                "result": execution_result,
                "tool": tool_name,
                "input": execution_input or {},
            },
            evidence=evidence or None,
        )

    async def fail_task(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        failure_reason: str,
    ) -> Task:
        """Mark task as failed (→ FAILED)."""
        task = await self.get(task_id, tenant_id)
        allowed_from = {"investigating", "executing", "verifying", "retrying"}
        if task.status not in allowed_from:
            raise TaskTransitionNotAllowed(
                f"Cannot fail task from state '{task.status}'"
            )

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "failed",
            "from_status": task.status,
            "to_status": "failed",
            "agent_id": str(agent_id),
            "reason": failure_reason,
        }

        task.audit_log.append(audit_entry)
        return await self.task_repo.update(
            task,
            status="failed",
            failure_reason=failure_reason,
        )

    async def retry_task(
        self, task_id: uuid.UUID, tenant_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Task:
        """Retry a failed task (FAILED → RETRYING)."""
        task = await self.get(task_id, tenant_id)
        self._validate_transition(task.status, "retrying")

        if task.retry_count >= task.max_retries:
            raise TaskTransitionNotAllowed(
                f"Task has reached max retries ({task.max_retries})"
            )

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "retrying",
            "from_status": task.status,
            "to_status": "retrying",
            "agent_id": str(agent_id),
            "retry_count": task.retry_count + 1,
        }

        task.audit_log.append(audit_entry)
        return await self.task_repo.update(
            task,
            status="retrying",
            retry_count=task.retry_count + 1,
            failure_reason=None,
        )

    async def escalate_task(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        escalated_by_agent_id: uuid.UUID,
        target_agent_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> Task:
        """Escalate a task to a higher authority."""
        task = await self.get(task_id, tenant_id)
        allowed_from = {"investigating", "failed"}
        if task.status not in allowed_from:
            raise TaskTransitionNotAllowed(
                f"Cannot escalate task from state '{task.status}'"
            )

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "escalated",
            "from_status": task.status,
            "to_status": "escalated",
            "escalated_by_agent_id": str(escalated_by_agent_id),
            "target_agent_id": str(target_agent_id) if target_agent_id else None,
            "reason": reason,
        }

        task.audit_log.append(audit_entry)
        updates = {"status": "escalated"}
        if target_agent_id:
            updates["assigned_agent_id"] = target_agent_id
        return await self.task_repo.update(task, **updates)

    async def cancel_task(
        self,
        task_id: uuid.UUID,
        tenant_id: uuid.UUID,
        reason: str | None = None,
        actor: str | None = None,
    ) -> Task:
        """Cancel a task (→ CANCELLED).

        Actor attribution (M9.13): a human cancellation records who ordered it,
        so the audit trail never reports a human action as SYSTEM. ``actor`` is
        audit metadata only and is never written to a Task column.
        """
        task = await self.get(task_id, tenant_id)
        if task.status in _TERMINAL_STATES:
            raise TaskTransitionNotAllowed(
                f"Cannot cancel task in terminal state '{task.status}'"
            )
        # The declared transition matrix stays the authority: states with no
        # `cancelled` edge (investigating / executing / verifying / verified)
        # must not be cancellable either, so cancellation cannot quietly bypass
        # the lifecycle every other operation is validated against.
        self._validate_transition(task.status, "cancelled")

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "cancelled",
            "from_status": task.status,
            "to_status": "cancelled",
            "actor": actor or "SYSTEM",
            "reason": reason,
        }

        task.audit_log.append(audit_entry)
        return await self.task_repo.update(
            task,
            status="cancelled",
            completed_at=now,
        )

    @staticmethod
    def _validate_transition(current: str, target: str) -> None:
        """Validate a task state transition."""
        allowed = _ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise TaskTransitionNotAllowed(
                f"Task transition not allowed: '{current}' -> '{target}'"
            )

    async def record_task(
        self, task_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Task:
        """Record verified task (VERIFIED → RECORDED)."""
        task = await self.get(task_id, tenant_id)
        self._validate_transition(task.status, "recorded")

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "recorded",
            "from_status": task.status,
            "to_status": "recorded",
        }

        task.audit_log.append(audit_entry)

        duration = None
        if task.started_at:
            duration = int((now - task.started_at).total_seconds())

        return await self.task_repo.update(
            task,
            status="recorded",
            completed_at=now,
            duration_seconds=duration,
        )
    async def start_investigation(
        self, task_id: uuid.UUID, tenant_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Task:
        """Agent starts investigation (ACCEPTED → INVESTIGATING)."""
        task = await self.get(task_id, tenant_id)
        self._validate_transition(task.status, "investigating")
        self._require_assigned_agent(task, agent_id)

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "investigation_started",
            "from_status": task.status,
            "to_status": "investigating",
            "agent_id": str(agent_id),
        }

        task.audit_log.append(audit_entry)
        return await self.task_repo.update(
            task,
            status="investigating",
            started_at=now,
        )