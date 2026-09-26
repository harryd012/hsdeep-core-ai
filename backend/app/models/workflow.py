"""
Workflow model - Enterprise Workflow Automation Engine sprint.

Single-table design (revised from an initial Workflow/WorkflowVersion
split): the CURRENT graph definition lives directly on this row
(`definition`, `version_number`, `status`, `approved_by`, `approved_at`),
and every prior version is appended to `version_history` (JSONB array) on
edit rather than living in a separate versions table. This trades away
cross-workflow SQL querying of past versions (acceptable - version history
is always looked up scoped to one already-loaded Workflow, never queried
across workflows) for a simpler schema, per explicit sprint scoping
decision.

`trigger_type`/`trigger_filter` let a Workflow optionally auto-queue a
`WorkflowExecution` when a real pipeline event occurs (e.g. an Incident
gets a recommended SOP) - see `services/workflow/workflow_selector.py`.
`trigger_filter` is free-form JSONB (category/min_priority/vendor/
platform/device_type/site_id/business_service_id, all optional) rather
than dedicated columns, mirroring `SOP`'s matching-criteria philosophy
without duplicating those columns onto a second table.

WorkflowTemplate/WorkflowVariable from the brief's "potential models"
list are deliberately NOT separate tables: a "template" IS this row's
`definition` (a versioned graph), and a "variable" has no independent
lifecycle - it only exists as a key inside `WorkflowExecution.variables`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class Workflow(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workflow_number", name="uq_workflows_tenant_id_workflow_number"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Human-readable stable identifier (e.g. "WF-INC-001"), same rationale
    # as SOP.sop_number - operators reference workflows by this, not the
    # UUID PK.
    workflow_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # incident | alert | runbook | approval | maintenance | notification |
    # servicenow | jira | teams | slack | webhook | email | self_healing |
    # ai - free-form string, not an enum, so a future workflow type never
    # requires a migration (same rationale as MonitoringSource.source_type
    # and SOP.category).
    workflow_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # manual | incident_created | priority_changed | sop_recommended |
    # alert_opened | schedule - None means "manual execution only", never
    # auto-queued.
    trigger_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    # Optional matching criteria (category/min_priority/vendor/platform/
    # device_type/site_id/business_service_id, all optional keys) -
    # WorkflowSelector scores real Incident/Alert fields against this in
    # Python, same "nullable/free-form, not FK-required" philosophy as
    # SOP's matching columns. Empty dict means "trigger_type fires
    # regardless of context".
    trigger_filter: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), nullable=False, default=list, server_default="{}")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)

    # -- Current version (inline; no separate WorkflowVersion table) ------
    # The graph itself:
    #   {"nodes": [{"id": "n1", "type": "approval", "config": {...}}, ...],
    #    "edges": [{"from": "n1", "to": "n2", "condition": null}, ...],
    #    "variables": {"escalation_target": {"type": "string", "default": null}}}
    # WorkflowEngine (services/workflow/workflow_engine.py) is the only
    # code that interprets this structure.
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # draft | approved | deprecated - a version only becomes executable
    # once "approved", same manual-approval-applied-to-content pattern as
    # SOPVersion.status.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft", index=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Every PRIOR version, appended to on each edit (see
    # WorkflowService.create_new_version()) - each entry:
    #   {"version_number": 1, "definition": {...}, "status": "deprecated",
    #    "change_summary": "...", "created_at": "...", "approved_by": "...",
    #    "approved_at": "..."}
    # Never queried across workflows - always read scoped to one already-
    # loaded Workflow row (list_versions()), so JSONB is sufficient; this
    # is the explicit "fewer tables" tradeoff accepted for this sprint.
    version_history: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<Workflow id={self.id} workflow_number={self.workflow_number!r} type={self.workflow_type!r} v{self.version_number}>"
