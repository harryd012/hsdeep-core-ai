"""
Runbook model - SOP & Runbook Intelligence Engine sprint.

A Runbook is the EXECUTABLE artifact tied to one `SOPVersion`: concrete
steps an operator (or, in the future, an automation agent) actually runs,
with an execution mode. Distinct from `SOPVersion.steps` (the procedure's
own recorded content) - a single SOPVersion can have multiple Runbooks
(e.g. a "manual" runbook for operators without CLI access and a
"semi_automated" runbook that runs a script for the same procedure).

`execution_mode` is the sprint's explicit "Manual / Semi-Automated /
Future Fully-Automated" requirement. `automation_ref` is a free-form
pointer (e.g. a script path or job name) for the semi/fully-automated
modes - never executed by this model or its repository; actual execution
side effects belong to a future automation-runner service, out of scope
here per the sprint brief ("Future Fully Automated Runbooks").
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.runbook_execution import RunbookExecution
    from app.models.sop import SOP, SOPVersion
    from app.models.tenant import Tenant


class Runbook(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "runbooks"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sops.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sop_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sop_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # manual | semi_automated | automated
    execution_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default="manual", index=True
    )

    # Ordered list of concrete execution steps - e.g.
    # [{"order": 1, "action": "ping_gateway", "timeout_seconds": 10}],
    # same shape as sop/device_down.yaml's `steps:` but scoped to what an
    # operator/script actually DOES, as opposed to SOPVersion.steps which
    # describes the investigation procedure itself. For a "manual" runbook
    # these are often identical to the SOPVersion steps; they diverge once
    # semi/fully-automated tooling steps are added.
    steps: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    # Free-form pointer to the automation artifact for semi/fully-automated
    # modes (e.g. a script path, job name, or webhook URL) - never invoked
    # by this codebase; a future automation-runner service is the intended
    # consumer, out of scope for this sprint.
    automation_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)

    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)

    tenant: Mapped["Tenant"] = relationship()
    sop: Mapped["SOP"] = relationship(back_populates="runbooks")
    sop_version: Mapped["SOPVersion"] = relationship()
    executions: Mapped[list["RunbookExecution"]] = relationship(back_populates="runbook")

    def __repr__(self) -> str:
        return f"<Runbook id={self.id} name={self.name!r} execution_mode={self.execution_mode!r}>"
