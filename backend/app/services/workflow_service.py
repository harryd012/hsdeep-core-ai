"""
Workflow service layer.

Thin, mirrors SOPService's split: graph traversal is owned by
WorkflowEngine, execution-level lifecycle actions by
WorkflowExecutionTracker, trigger matching by WorkflowSelector - this
class exposes only the CRUD/lookup/versioning paths the API needs, plus
the create-execution and evaluate-triggers convenience methods.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.workflow import Workflow
from app.models.workflow_execution import WorkflowExecution
from app.repositories.workflow_execution_repository import WorkflowExecutionRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.sop.sop_matcher import IncidentContext
from app.services.workflow.workflow_engine import WorkflowEngine
from app.services.workflow.workflow_selector import WorkflowSelector


class WorkflowNotFound(Exception):
    pass


class WorkflowNotExecutable(Exception):
    """Raised when execute() is requested against a Workflow whose
    current version has not been approved yet - mirrors SOPMatcher's
    "only an approved SOPVersion is a candidate" rule, applied to
    preventing execution of unreviewed definitions."""


class WorkflowService:
    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        execution_repo: WorkflowExecutionRepository,
        engine: WorkflowEngine,
        selector: WorkflowSelector | None = None,
    ):
        self.workflow_repo = workflow_repo
        self.execution_repo = execution_repo
        self.engine = engine
        self.selector = selector or WorkflowSelector()

    # -- Workflow CRUD / versioning ------------------------------------------
    async def get(self, workflow_id: uuid.UUID, tenant_id: uuid.UUID) -> Workflow:
        workflow = await self.workflow_repo.get(workflow_id, tenant_id)
        if workflow is None:
            raise WorkflowNotFound(f"Workflow {workflow_id} not found")
        return workflow

    async def list_workflows(self, tenant_id: uuid.UUID, **filters) -> list[Workflow]:
        return await self.workflow_repo.list(tenant_id, **filters)

    async def create_workflow(self, tenant_id: uuid.UUID, *, definition: dict, **fields) -> Workflow:
        WorkflowEngine.validate_definition(definition)
        return await self.workflow_repo.create(tenant_id=tenant_id, definition=definition, **fields)

    async def create_new_version(
        self, workflow_id: uuid.UUID, tenant_id: uuid.UUID, *, definition: dict, change_summary: str | None = None
    ) -> Workflow:
        WorkflowEngine.validate_definition(definition)
        workflow = await self.get(workflow_id, tenant_id)
        return await self.workflow_repo.create_new_version(workflow, definition=definition, change_summary=change_summary)

    async def approve_current_version(self, workflow_id: uuid.UUID, tenant_id: uuid.UUID, *, approved_by: uuid.UUID | None) -> Workflow:
        workflow = await self.get(workflow_id, tenant_id)
        WorkflowEngine.validate_definition(workflow.definition)
        return await self.workflow_repo.approve_current_version(workflow, approved_by=approved_by)

    async def delete_workflow(self, workflow_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        workflow = await self.get(workflow_id, tenant_id)
        await self.workflow_repo.soft_delete(workflow)

    # -- Execution ------------------------------------------------------------
    async def execute(
        self,
        workflow_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        trigger_source: str = "manual",
        incident_id: uuid.UUID | None = None,
        alert_id: uuid.UUID | None = None,
        requested_by: uuid.UUID | None = None,
        initial_variables: dict | None = None,
    ) -> WorkflowExecution:
        workflow = await self.get(workflow_id, tenant_id)
        if workflow.status != "approved":
            raise WorkflowNotExecutable(
                f"Workflow {workflow_id} current version is {workflow.status!r}, not 'approved' - cannot execute."
            )
        variables = {**self._default_variables(workflow.definition), **(initial_variables or {})}
        execution = await self.execution_repo.create(
            tenant_id=tenant_id,
            workflow_id=workflow.id,
            workflow_version_number=workflow.version_number,
            definition_snapshot=workflow.definition,
            trigger_source=trigger_source,
            incident_id=incident_id,
            alert_id=alert_id,
            requested_by=requested_by,
            variables=variables,
            status="queued",
        )
        return await self.execution_repo.append_audit(
            execution, event_type="queued", message=f"Execution queued (trigger_source={trigger_source}).", actor_id=requested_by
        )

    async def advance(self, execution_id: uuid.UUID, tenant_id: uuid.UUID, *, max_steps: int = 25) -> WorkflowExecution:
        """Drives run_step() repeatedly until the execution parks
        (waiting_approval/paused) or reaches a terminal state, bounded by
        max_steps so a malformed graph (e.g. an infinite decision loop)
        can never hang the calling request indefinitely."""
        execution = await self.get_execution(execution_id, tenant_id)
        for _ in range(max_steps):
            if execution.status not in ("queued", "running"):
                break
            execution = await self.engine.run_step(execution)
        return execution

    async def get_execution(self, execution_id: uuid.UUID, tenant_id: uuid.UUID) -> WorkflowExecution:
        execution = await self.execution_repo.get(execution_id, tenant_id)
        if execution is None:
            raise WorkflowNotFound(f"WorkflowExecution {execution_id} not found")
        return execution

    async def list_executions(self, tenant_id: uuid.UUID, **filters) -> list[WorkflowExecution]:
        return await self.execution_repo.list(tenant_id, **filters)

    async def statistics(self, tenant_id: uuid.UUID) -> dict:
        return await self.execution_repo.statistics(tenant_id)

    # -- Trigger evaluation ---------------------------------------------------
    async def evaluate_triggers(
        self, tenant_id: uuid.UUID, *, trigger_type: str, context: IncidentContext, incident_id: uuid.UUID | None = None
    ) -> list[WorkflowExecution]:
        """Called from the real pipeline (see alert_service.py's optional
        integration point) when a real event occurs (e.g. an Incident just
        got a recommended SOP). Auto-queues a new WorkflowExecution for
        every matching, active, approved Workflow registered for
        `trigger_type` - never auto-approves/auto-runs past the first
        approval gate; queuing an execution is not the same as executing
        past a human-required node."""
        candidates = await self.workflow_repo.list_for_trigger(tenant_id, trigger_type)
        queued: list[WorkflowExecution] = []
        for workflow in candidates:
            match = self.selector.score(workflow, context)
            if match is None:
                continue
            execution = await self.execute(
                workflow.id, tenant_id, trigger_source=trigger_type, incident_id=incident_id,
                initial_variables={"_trigger_match": {"confidence_score": match.confidence_score, "matched_criteria": match.matched_criteria}},
            )
            queued.append(execution)
        return queued

    @staticmethod
    def _default_variables(definition: dict) -> dict:
        schema = definition.get("variables", {})
        return {name: spec.get("default") for name, spec in schema.items()}
