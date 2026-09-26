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

        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "created",
            "from_status": None,
            "to_status": "created",
            "actor": fields.get("owner_command", "system"),
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
    ) -> Task:
        """Assign a task to an agent (CREATED → ASSIGNED)."""
        task = await self.get(task_id, tenant_id)
        self._validate_transition(task.status, "assigned")

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "assigned",
            "from_status": task.status,
            "to_status": "assigned",
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
    ) -> Task:
        """Cancel a task (→ CANCELLED)."""
        task = await self.get(task_id, tenant_id)
        if task.status in _TERMINAL_STATES:
            raise TaskTransitionNotAllowed(
                f"Cannot cancel task in terminal state '{task.status}'"
            )

        now = datetime.now(timezone.utc)
        audit_entry = {
            "timestamp": now.isoformat(),
            "action": "cancelled",
            "from_status": task.status,
            "to_status": "cancelled",
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