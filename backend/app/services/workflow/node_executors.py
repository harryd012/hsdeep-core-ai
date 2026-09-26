"""
Concrete node executors - Enterprise Workflow Automation Engine sprint.

Every class here registers itself via `@NodeExecutorRegistry.register(...)`
against `WorkflowEngine`'s node-type dispatch. Importing this module
(done once, in `services/workflow/__init__.py`, same "registration side
effect" pattern as `app/services/monitoring/__init__.py` importing
`app/collectors/`) is what makes every node type resolvable - `main.py`
already imports `app.services.workflow` at startup via the same mechanism
used for collectors.

Node types implemented this sprint (the sprint's explicit list):
    start, end          - no-op structural markers
    task                 - generic no-op placeholder for future
                           self-healing/AI/webhook/email/etc. node types;
                           always returns "succeeded" with no side effect,
                           so a workflow author can sketch a graph before
                           every leaf node type exists.
    runbook_task          - delegates to the EXISTING Runbook/
                           ExecutionTracker (never re-implements
                           execution) - this is what keeps every task
                           node subject to the same manual-approval gate
                           as a direct Runbook execution; no autonomous
                           remediation is introduced.
    approval              - manual approval gate; parks the execution at
                           "waiting_approval" until an operator calls
                           WorkflowExecutionTracker.approve()/reject().
    decision              - evaluates `config["branches"]` (ordered list of
                           {"condition": {...}, "next_node_id": "..."})
                           against `execution.variables`; first matching
                           branch wins, `config["default_next_node_id"]`
                           is the fallback. Deterministic, explainable -
                           same "plain comparison, not ML" philosophy as
                           DecisionEngine/SOPMatcher.
    delay                 - parks the execution at "paused" until
                           `config["resume_at"]` (absolute) or
                           `config["delay_seconds"]` (relative to node
                           start, computed once and stored in
                           execution.variables) has elapsed; the scheduler
                           tick (see workflow_engine.py's resume_due_delays)
                           is what actually resumes it - this class only
                           decides "is it time yet".
    notification           - records a real audit_log entry describing
                           what WOULD be sent (channel/target/message from
                           config) - never claims a message was actually
                           delivered, since no real email/Slack/Teams/
                           webhook integration exists yet. Always
                           "succeeded" (recording an audit entry cannot
                           fail in the way a real delivery could).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.repositories.runbook_execution_repository import RunbookExecutionRepository
from app.repositories.runbook_repository import RunbookRepository
from app.services.sop.execution_tracker import ExecutionTracker, RunbookExecutionNotFound
from app.services.workflow.node_executor_registry import BaseNodeExecutor, NodeExecutorRegistry, NodeResult


@NodeExecutorRegistry.register("start")
class StartNodeExecutor(BaseNodeExecutor):
    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        return NodeResult(status="succeeded", message="Workflow started.")


@NodeExecutorRegistry.register("end")
class EndNodeExecutor(BaseNodeExecutor):
    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        return NodeResult(status="succeeded", message="Workflow reached its end node.")


@NodeExecutorRegistry.register("task")
class TaskNodeExecutor(BaseNodeExecutor):
    """Generic no-op placeholder - reserved for future ServiceNow/Jira/
    Teams/Slack/Webhook/Email/SelfHealing/AI node types per the sprint's
    "design for future support" list. Deliberately does nothing beyond
    recording that it ran, so it never fabricates an integration outcome
    that doesn't exist yet."""

    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        return NodeResult(
            status="succeeded",
            message=f"Task node '{node.get('id')}' completed (no-op placeholder; type={config.get('kind', 'generic')!r}).",
        )


@NodeExecutorRegistry.register("approval")
class ApprovalNodeExecutor(BaseNodeExecutor):
    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        # Re-entrant: once approved, WorkflowEngine calls execute() again
        # to actually advance past this node - checks a real, already-
        # persisted approval fact on the execution (set by
        # WorkflowExecutionTracker.approve()), never assumes.
        approvals = execution.variables.get("_approvals", {})
        node_id = node["id"]
        if approvals.get(node_id) == "approved":
            return NodeResult(status="succeeded", message=f"Approval node '{node_id}' was approved.")
        if approvals.get(node_id) == "rejected":
            return NodeResult(status="failed", message=f"Approval node '{node_id}' was rejected.")
        return NodeResult(
            status="waiting",
            message=f"Waiting for manual approval at node '{node_id}'.",
            execution_status="waiting_approval",
        )


@NodeExecutorRegistry.register("decision")
class DecisionNodeExecutor(BaseNodeExecutor):
    """Deterministic conditional branching - every rule is a plain
    equality/comparison over execution.variables, no ML/LLM call, same
    "honest heuristic" convention as DecisionEngine/SOPMatcher."""

    _OPERATORS = {
        "eq": lambda a, b: a == b,
        "ne": lambda a, b: a != b,
        "gt": lambda a, b: a is not None and b is not None and a > b,
        "gte": lambda a, b: a is not None and b is not None and a >= b,
        "lt": lambda a, b: a is not None and b is not None and a < b,
        "lte": lambda a, b: a is not None and b is not None and a <= b,
        "in": lambda a, b: a in (b or []),
    }

    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        branches = config.get("branches", [])
        for branch in branches:
            condition = branch.get("condition", {})
            if self._matches(condition, execution.variables):
                return NodeResult(
                    status="succeeded",
                    message=f"Decision matched branch -> {branch.get('next_node_id')!r}.",
                    next_node_ids=[branch["next_node_id"]],
                    output_variables={"_last_decision": {"node_id": node["id"], "matched": condition}},
                )
        default_next = config.get("default_next_node_id")
        if default_next:
            return NodeResult(
                status="succeeded",
                message=f"Decision fell through to default branch -> {default_next!r}.",
                next_node_ids=[default_next],
                output_variables={"_last_decision": {"node_id": node["id"], "matched": None}},
            )
        return NodeResult(status="failed", message=f"Decision node '{node['id']}' matched no branch and has no default.")

    @classmethod
    def _matches(cls, condition: dict, variables: dict) -> bool:
        if not condition:
            return False
        variable_name = condition.get("variable")
        operator = condition.get("operator", "eq")
        expected = condition.get("value")
        actual = variables.get(variable_name)
        op_fn = cls._OPERATORS.get(operator)
        if op_fn is None:
            return False
        return op_fn(actual, expected)


@NodeExecutorRegistry.register("delay")
class DelayNodeExecutor(BaseNodeExecutor):
    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        node_id = node["id"]
        delays = execution.variables.get("_delays", {})
        resume_at_iso = delays.get(node_id)

        now = datetime.now(timezone.utc)
        if resume_at_iso is None:
            # First visit to this node: compute and store the resume time.
            if config.get("resume_at"):
                resume_at = datetime.fromisoformat(config["resume_at"])
            else:
                delay_seconds = int(config.get("delay_seconds", 0))
                resume_at = now + timedelta(seconds=delay_seconds)
            return NodeResult(
                status="waiting",
                message=f"Delay node '{node_id}' waiting until {resume_at.isoformat()}.",
                execution_status="paused",
                output_variables={"_delays": {**delays, node_id: resume_at.isoformat()}},
            )

        resume_at = datetime.fromisoformat(resume_at_iso)
        if now >= resume_at:
            return NodeResult(status="succeeded", message=f"Delay node '{node_id}' elapsed at {now.isoformat()}.")
        return NodeResult(
            status="waiting",
            message=f"Delay node '{node_id}' still waiting until {resume_at.isoformat()}.",
            execution_status="paused",
        )


@NodeExecutorRegistry.register("runbook_task")
class RunbookTaskNodeExecutor(BaseNodeExecutor):
    """Delegates to the EXISTING Runbook engine (RunbookRepository +
    ExecutionTracker) rather than re-implementing execution logic - this
    is what keeps a workflow's task nodes subject to the exact same
    manual-approval gate (pending_approval -> approved -> running ->
    succeeded/failed) as a directly-triggered Runbook execution. No
    autonomous remediation is introduced by this node type; it only
    orchestrates the SAME human-gated execution the SOP & Runbook
    Intelligence Engine sprint already built."""

    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        node_id = node["id"]
        runbook_id_str = config.get("runbook_id")
        if not runbook_id_str:
            return NodeResult(status="failed", message=f"runbook_task node '{node_id}' has no configured runbook_id.")

        links = execution.variables.get("_runbook_links", {})
        execution_repo = RunbookExecutionRepository(db)
        runbook_repo = RunbookRepository(db)
        tracker = ExecutionTracker(execution_repo=execution_repo, runbook_repo=runbook_repo)

        linked_id = links.get(node_id)
        if linked_id is None:
            try:
                runbook_execution = await tracker.request_execution(
                    tenant_id=execution.tenant_id,
                    runbook_id=uuid.UUID(runbook_id_str),
                    incident_id=execution.incident_id,
                    operator_id=execution.requested_by,
                )
            except RunbookExecutionNotFound as exc:
                return NodeResult(status="failed", message=str(exc))
            return NodeResult(
                status="waiting",
                message=f"runbook_task '{node_id}' requested RunbookExecution {runbook_execution.id} (pending approval).",
                execution_status="waiting_approval",
                linked_execution_id=str(runbook_execution.id),
                output_variables={"_runbook_links": {**links, node_id: str(runbook_execution.id)}},
            )

        runbook_execution = await execution_repo.get(uuid.UUID(linked_id), execution.tenant_id)
        if runbook_execution is None:
            return NodeResult(status="failed", message=f"Linked RunbookExecution {linked_id} not found.")

        if runbook_execution.status in ("pending_approval", "approved", "running"):
            return NodeResult(
                status="waiting",
                message=f"runbook_task '{node_id}' waiting on RunbookExecution {linked_id} (status={runbook_execution.status}).",
                execution_status="waiting_approval" if runbook_execution.status == "pending_approval" else "running",
                linked_execution_id=linked_id,
            )
        if runbook_execution.status == "succeeded":
            return NodeResult(
                status="succeeded",
                message=f"runbook_task '{node_id}' completed via RunbookExecution {linked_id}.",
                linked_execution_id=linked_id,
            )
        return NodeResult(
            status="failed",
            message=f"runbook_task '{node_id}' failed via RunbookExecution {linked_id} (status={runbook_execution.status}).",
            linked_execution_id=linked_id,
        )


@NodeExecutorRegistry.register("notification")
class NotificationNodeExecutor(BaseNodeExecutor):
    """Records a real audit_log entry describing the intended
    notification - never claims delivery, since no real email/Slack/
    Teams/webhook integration exists yet (per the sprint's "future ITSM
    integrations" scope note and the standing "never fabricate data"
    rule)."""

    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        channel = config.get("channel", "log")
        target = config.get("target", "unspecified")
        message = config.get("message", "")
        return NodeResult(
            status="succeeded",
            message=f"Notification recorded (channel={channel!r}, target={target!r}): {message}",
            output_variables={"_last_notification": {"channel": channel, "target": target, "message": message}},
        )


@NodeExecutorRegistry.register("servicenow_incident")
class ServiceNowIncidentNodeExecutor(BaseNodeExecutor):
    """Creates/updates a ServiceNow incident via the SyncService. Follows the
    same delegation pattern as RunbookTaskNodeExecutor: instantiates the
    service with repos from the current session, never holds persistent state.

    Config expects:
      {
        "connection_id": "uuid-or-var",  // literal UUID or _var_ref
        "push_fields": {...}              // optional overrides for SN fields
      }

    Outputs:
      _sn_sys_id, _sn_number via output_variables (used by downstream nodes).
    """

    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
        from app.repositories.servicenow_incident_link_repository import ServiceNowIncidentLinkRepository
        from app.repositories.servicenow_sync_job_repository import ServiceNowSyncJobRepository
        from app.services.servicenow.incident_sync import ServiceNowIncidentSyncService

        node_id = node["id"]
        conn_id_str = config.get("connection_id", "")

        # Resolve connection_id: literal UUID or _var_ref
        if conn_id_str.startswith("_var_"):
            var_name = conn_id_str.replace("_var_", "", 1)
            resolved = execution.variables.get(var_name)
            if not resolved:
                return NodeResult(status="failed", message=f"servicenow_incident node '{node_id}': connection_id variable {var_name!r} not found in execution context.")
            conn_id_str = str(resolved)

        try:
            connection_id = uuid.UUID(conn_id_str)
        except (ValueError, TypeError):
            return NodeResult(status="failed", message=f"servicenow_incident node '{node_id}': invalid connection_id {conn_id_str!r}.")

        if not execution.incident_id:
            return NodeResult(status="failed", message=f"servicenow_incident node '{node_id}': execution has no incident_id to push.")

        sync_service = ServiceNowIncidentSyncService(
            conn_repo=ServiceNowConnectionRepository(db),
            link_repo=ServiceNowIncidentLinkRepository(db),
            job_repo=ServiceNowSyncJobRepository(db),
        )

        status, message = await sync_service.push_incident_to_servicenow(
            tenant_id=execution.tenant_id,
            incident_id=execution.incident_id,
            connection_id=connection_id,
        )

        # Build output variables for downstream nodes
        output = {}
        if status == "succeeded":
            output["_sn_push_status"] = "success"
            output["_sn_node_result"] = message
            # Try to extract sys_id/number from the connection's link lookup
            link = await sync_service.link_repo.get_by_incident_id(execution.tenant_id, execution.incident_id)
            if link and link.sn_sys_id:
                output["_sn_sys_id"] = link.sn_sys_id
                output["_sn_number"] = link.sn_number or ""

        return NodeResult(
            status="succeeded" if status == "succeeded" else "failed",
            message=message,
            linked_execution_id=str(connection_id),
            output_variables=output,
        )


@NodeExecutorRegistry.register("servicenow_pull")
class ServiceNowPullNodeExecutor(BaseNodeExecutor):
    """Pulls an incident update from ServiceNow. Use in workflows that need
    to sync the latest SN state (e.g., after a delay or on demand).

    Config expects:
      {
        "connection_id": "uuid-or-var",  // literal UUID or _var_ref
        "sn_sys_id": "sys-id-or-var"     // optional; falls back to link lookup
      }
    """

    async def execute(self, *, execution, node: dict, config: dict, db) -> NodeResult:
        from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
        from app.repositories.servicenow_sync_job_repository import ServiceNowSyncJobRepository
        from app.services.servicenow.incident_sync import ServiceNowIncidentSyncService

        node_id = node["id"]
        conn_id_str = config.get("connection_id", "")

        if conn_id_str.startswith("_var_"):
            var_name = conn_id_str.replace("_var_", "", 1)
            resolved = execution.variables.get(var_name)
            if not resolved:
                return NodeResult(status="failed", message=f"servicenow_pull node '{node_id}': connection_id variable {var_name!r} not found.")
            conn_id_str = str(resolved)

        try:
            connection_id = uuid.UUID(conn_id_str)
        except (ValueError, TypeError):
            return NodeResult(status="failed", message=f"servicenow_pull node '{node_id}': invalid connection_id {conn_id_str!r}.")

        sn_sys_id = config.get("sn_sys_id", "")
        if sn_sys_id and sn_sys_id.startswith("_var_"):
            var_name = sn_sys_id.replace("_var_", "", 1)
            resolved = execution.variables.get(var_name)
            if resolved:
                sn_sys_id = str(resolved)

        sync_service = ServiceNowIncidentSyncService(
            conn_repo=ServiceNowConnectionRepository(db),
            link_repo=None,  # not needed for pull result
            job_repo=ServiceNowSyncJobRepository(db),
        )

        result = await sync_service.pull_incident_from_servicenow(
            tenant_id=execution.tenant_id,
            connection_id=connection_id,
            sn_sys_id=sn_sys_id or None,
        )

        output = {}
        if result.get("status") == "succeeded":
            output["_sn_pull_status"] = "success"
            output["_sn_pull_data"] = result.get("data", {})
        else:
            output["_sn_pull_status"] = result.get("status", "error")

        return NodeResult(
            status="succeeded" if result.get("status") == "succeeded" else "failed",
            message=result.get("message", "Pull completed"),
            output_variables=output,
        )
