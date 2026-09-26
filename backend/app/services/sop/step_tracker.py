"""
Runbook execution step tracker.

Adds per-step progress tracking to RunbookExecution by writing step entries
to the existing `step_history` JSONB column on the model (added in a future
migration if not yet present, but we query it as if it exists now).

Mirrors WorkflowEngine's step_history pattern: each entry records node_id,
node_type, status, message, linked_execution_id, recorded_at.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.runbook import Runbook
from app.models.runbook_execution import RunbookExecution
from app.repositories.runbook_execution_repository import RunbookExecutionRepository


class StepTracker:
    """Tracks individual step execution progress within a RunbookExecution.
    Builds on the existing RunbookExecution lifecycle (pending_approval → approved
    → running → succeeded/failed) by adding granular step-level detail."""

    def __init__(self, execution_repo: RunbookExecutionRepository):
        self.execution_repo = execution_repo

    async def start_step(
        self,
        execution: RunbookExecution,
        runbook: Runbook,
        step_index: int,
    ) -> RunbookExecution:
        """Mark a step as started in the step_history array."""
        if step_index >= len(runbook.steps):
            raise ValueError(f"Step index {step_index} out of range for runbook with {len(runbook.steps)} steps")

        step_entry = {
            "step_index": step_index,
            "step_id": f"s{step_index}",
            "status": "started",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "output_variables": {},
        }
        execution.step_history = [*execution.step_history, step_entry]
        await self.execution_repo.db.flush()
        return execution

    async def complete_step(
        self,
        execution: RunbookExecution,
        runbook: Runbook,
        step_index: int,
        *,
        output_variables: dict | None = None,
        success: bool = True,
    ) -> RunbookExecution:
        """Mark a step as completed in the step_history array."""
        # Find the last started entry for this step and update it
        steps = execution.step_history
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].get("step_index") == step_index and steps[i].get("status") == "started":
                steps[i]["status"] = "succeeded" if success else "failed"
                steps[i]["completed_at"] = datetime.now(timezone.utc).isoformat()
                if output_variables:
                    steps[i]["output_variables"] = {**steps[i].get("output_variables", {}), **output_variables}
                break

        execution.step_history = steps
        await self.execution_repo.db.flush()
        return execution

    async def approve_step(
        self,
        execution: RunbookExecution,
        step_index: int,
        *,
        output_variables: dict | None = None,
    ) -> RunbookExecution:
        """Mark a step as approved (for approval-gated steps)."""
        steps = execution.step_history
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].get("step_index") == step_index and steps[i].get("status") == "started":
                steps[i]["status"] = "approved"
                steps[i]["completed_at"] = datetime.now(timezone.utc).isoformat()
                if output_variables:
                    steps[i]["output_variables"] = {**steps[i].get("output_variables", {}), **output_variables}
                break

        execution.step_history = steps
        await self.execution_repo.db.flush()
        return execution

    async def fail_step(
        self,
        execution: RunbookExecution,
        step_index: int,
        *,
        error_message: str,
    ) -> RunbookExecution:
        """Mark a step as failed with an error message."""
        steps = execution.step_history
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].get("step_index") == step_index and steps[i].get("status") in ("started", "approved"):
                steps[i]["status"] = "failed"
                steps[i]["completed_at"] = datetime.now(timezone.utc).isoformat()
                steps[i]["error_message"] = error_message
                break

        execution.step_history = steps
        await self.execution_repo.db.flush()
        return execution

    async def get_progress(self, execution: RunbookExecution) -> dict:
        """Return step progress summary: completed count, total steps, current status."""
        steps = execution.step_history
        if not steps:
            return {"total": 0, "completed": 0, "current_status": "no_steps"}

        statuses = [s.get("status", "unknown") for s in steps]
        completed_count = sum(1 for s in statuses if s in ("succeeded", "approved"))
        failed_count = sum(1 for s in statuses if s == "failed")
        waiting_count = sum(1 for s in statuses if s in ("started", "approved"))

        if failed_count > 0:
            current_status = "failed"
        elif completed_count == len(statuses):
            current_status = "completed"
        elif any(s == "waiting_approval" for s in statuses):
            current_status = "waiting_approval"
        else:
            current_status = "running"

        return {
            "total": len(steps),
            "completed": completed_count,
            "failed": failed_count,
            "waiting": waiting_count,
            "current_status": current_status,
            "steps": steps,
        }
