"""
WorkflowExecution model - Enterprise Workflow Automation Engine sprint.
The runtime record of one run through a Workflow's graph.

Single-table design (revised from an initial WorkflowExecution/
WorkflowStepExecution/WorkflowAuditLog split): per-node execution history
lives in `step_history` (JSONB array) and the event/audit trail lives in
`audit_log` (JSONB array), both appended to by `WorkflowEngine`/
`WorkflowExecutionTracker` rather than in separate tables. Same explicit
"fewer tables" tradeoff as workflow.py's `version_history` - step/audit
history is always read scoped to one already-loaded WorkflowExecution
(the execution detail view), never queried standalone across executions
in this sprint. If a future sprint needs "every failed node across all
executions this month"-style cross-execution querying, that is the
trigger to split these back into real tables - not a redesign, an
additive follow-up.

State machine (all 11 states from the sprint brief):
    Draft -> Ready -> Queued -> WaitingApproval -> Running -> Succeeded
                                                 \\-> Paused -> Running
                                                 \\-> Retrying -> Running
                                                 \\-> Failed -> RolledBack
                     \\-> Cancelled (from any non-terminal state)
`Draft`/`Ready` describe the Workflow definition's own readiness (mirrors
SOPVersion.status), not typically persisted as a WorkflowExecution state
- an execution always starts at `Queued` or `WaitingApproval`. Both are
included in the Literal for schema completeness per the brief's explicit
state list.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.incident import Incident
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.workflow import Workflow


class WorkflowExecution(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "workflow_executions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The Workflow.version_number this execution ran (or is running) -
    # NOT a FK (there's no separate versions table) - a plain snapshot
    # int, so an execution's recorded version never goes stale/ambiguous
    # even after the Workflow itself is edited to a new version.
    workflow_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshot of the definition this execution is actually running -
    # copied from Workflow.definition (or the matching version_history
    # entry) at queue time. WorkflowEngine reads/advances against THIS
    # copy, never the live Workflow.definition, so an in-flight execution
    # is never affected by someone editing the workflow mid-run.
    definition_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # manual | incident_created | priority_changed | sop_recommended |
    # alert_opened | schedule - mirrors Workflow.trigger_type; recorded
    # per-execution since a manually-triggered run and an auto-triggered
    # run of the same Workflow are both possible.
    trigger_source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual", server_default="manual")

    # Optional real context this execution is acting on - nullable since
    # a Notification/Maintenance workflow may have neither.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # draft | ready | queued | waiting_approval | running | paused |
    # retrying | succeeded | failed | cancelled | rolled_back - the exact
    # 11-state machine from the sprint brief. Free-form string (not a DB
    # enum) so a future state addition never requires a migration, same
    # convention as every other lifecycle status column in this codebase.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", server_default="queued", index=True
    )

    # Runtime variable bag - seeded from Workflow.definition["variables"]
    # defaults at queue time, then read/written by node executors as the
    # graph advances (e.g. a Decision node's branch result, an Approval
    # node's approver comment). Free-form JSONB since the variable SCHEMA
    # itself is workflow-specific and already declared in
    # definition_snapshot["variables"] - this column holds only values.
    variables: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # The node currently executing/awaiting action, or the last node that
    # ran before a terminal state. Null only before the first node starts.
    current_node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Append-only per-node execution history - each entry:
    #   {"node_id": "n1", "node_type": "approval", "status": "succeeded",
    #    "sequence_order": 1, "parallel_group": null,
    #    "input_variables": {...}, "output_variables": {...},
    #    "retry_count": 0, "error_message": null,
    #    "runbook_execution_id": "...", "started_at": "...", "completed_at": "..."}
    # `runbook_execution_id` links a task node that delegated to the
    # existing Runbook engine (see services/workflow/node_executor_registry.py)
    # - never a duplicated execution path, always a real
    # RunbookExecution.id when present.
    step_history: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    # Append-only event/audit trail - each entry:
    #   {"event_type": "queued", "message": "...", "details": {...},
    #    "actor_id": "...", "occurred_at": "..."}
    # Same fields AlertHistory/RunbookExecution's implicit audit trail
    # already uses, just embedded here per this sprint's "fewer tables"
    # scoping decision rather than a separate WorkflowAuditLog table.
    audit_log: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    # -- policy/observability fields (Retry/Timeout/Rollback + Metrics) ----
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    rollback_performed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Denormalized for cheap reporting queries (avg duration, success
    # rate), same rationale as RunbookExecution.duration_seconds.
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    workflow: Mapped["Workflow"] = relationship()
    incident: Mapped["Incident | None"] = relationship()
    alert: Mapped["Alert | None"] = relationship()

    def __repr__(self) -> str:
        return f"<WorkflowExecution id={self.id} workflow_id={self.workflow_id} status={self.status!r}>"
