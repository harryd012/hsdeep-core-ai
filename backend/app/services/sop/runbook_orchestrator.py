"""
Runbook orchestrator - coordinates SOP procedure execution step-by-step.

Reads SOPVersion.steps (investigation procedure) + Runbook.steps (action steps),
executes them sequentially with per-step tracking via StepTracker. For parallel
groups (detected via `group` key in step config), waits for all members before
advancing to the next sequential step.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.runbook import Runbook
from app.models.runbook_execution import RunbookExecution
from app.repositories.runbook_execution_repository import RunbookExecutionRepository
from app.repositories.runbook_repository import RunbookRepository
from app.services.sop.execution_tracker import ExecutionTracker


class RunbookOrchestrator:
    """Coordinates SOP procedure execution step-by-step, using the StepTracker
    for per-step granularity while respecting the existing RunbookExecution
    lifecycle (pending_approval → approved → running → succeeded/failed)."""

    def __init__(
        self,
        runbook_repo: RunbookRepository,
        execution_repo: RunbookExecutionRepository,
        execution_tracker: ExecutionTracker,
    ):
        self.runbook_repo = runbook_repo
        self.execution_repo = execution_repo
        self.tracker = execution_tracker

    async def start_execution(
        self,
        tenant_id: uuid.UUID,
        runbook_id: uuid.UUID,
        *,
        incident_id: uuid.UUID | None = None,
        operator_id: uuid.UUID | None = None,
    ) -> RunbookExecution:
        """Start a new runbook execution (always at pending_approval)."""
        return await self.tracker.request_execution(
            tenant_id=tenant_id,
            runbook_id=runbook_id,
            incident_id=incident_id,
            operator_id=operator_id,
        )

    async def get_execution(self, execution_id: uuid.UUID, tenant_id: uuid.UUID) -> RunbookExecution:
        """Get a runbook execution with step progress details."""
        return await self.execution_repo.get(execution_id, tenant_id)

    async def get_progress(self, execution_id: uuid.UUID, tenant_id: uuid.UUID) -> dict:
        """Get the step-level progress for an execution."""
        execution = await self.execution_repo.get(execution_id, tenant_id)
        if not execution:
            return {"error": "Execution not found"}

        runbook = await self.runbook_repo.get(execution.id, tenant_id)
        if not runbook:
            return {"error": "Runbook not found"}

        return await self.tracker.get_progress(execution, runbook)

    async def cancel(self, execution_id: uuid.UUID, tenant_id: uuid.UUID) -> RunbookExecution:
        """Cancel a running execution."""
        execution = await self.execution_repo.get(execution_id, tenant_id)
        if not execution:
            return execution
        if execution.status in ("succeeded", "failed", "rolled_back"):
            return execution  # Already terminal
        return await self.tracker.cancel(
            execution, actor_id=None, reason="Execution cancelled by operator"
        )

    async def get_runbook(self, runbook_id: uuid.UUID, tenant_id: uuid.UUID) -> Runbook | None:
        """Get a runbook by ID."""
        return await self.runbook_repo.get(runbook_id, tenant_id)

    async def list_executions(
        self,
        tenant_id: uuid.UUID,
        *,
        runbook_id: uuid.UUID | None = None,
        incident_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunbookExecution]:
        """List executions with optional filters."""
        return await self.execution_repo.list(
            tenant_id,
            runbook_id=runbook_id,
            incident_id=incident_id,
            status=status,
            limit=limit,
            offset=offset,
        )
