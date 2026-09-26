"""
SOP (Standard Operating Procedure) model - SOP & Runbook Intelligence
Engine sprint.

`SOP` is the versioned CATALOG ENTRY: identity, matching criteria
(category/tags/vendor/platform/device_type/site/business_service/region),
and approval/skill/rollback metadata. The actual step-by-step procedure
content lives on `SOPVersion` (one-to-many) so a procedure can evolve
without losing its history - matching the sprint's explicit "SOP Version
Management" deliverable. `Runbook` (see runbook.py) is a distinct,
EXECUTABLE artifact that references one `SOPVersion` - the brief lists
"SOP Repository" and "Runbook Repository" as separate items, and this
mirrors that: an SOP describes WHAT should happen and why; a Runbook is
HOW it gets carried out (manual/semi-automated/automated steps) and is
what `RunbookExecution` actually runs.

Matching criteria columns are all nullable/free-form (not enums, not
FK-required) so a broad SOP ("any critical availability incident") and a
narrow one ("Meraki AP alert at Site X") coexist in the same table without
a schema change - `SOPMatcher` (see services/sop/sop_matcher.py) scores
each candidate SOP against an Incident's real fields rather than requiring
an exact criteria match.
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
    from app.models.business_service import BusinessService
    from app.models.runbook import Runbook
    from app.models.site import Site
    from app.models.tenant import Tenant


class SOP(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sops"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sop_number", name="uq_sops_tenant_id_sop_number"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Human-readable stable identifier (e.g. "SOP-NET-001"), distinct from
    # the UUID primary key - operators reference SOPs by this in tickets/
    # chat, same convention the sprint brief's own YAML examples use
    # (see sop/device_down.yaml's `id: SOP-001`).
    sop_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # availability | performance | capacity | configuration | security |
    # connectivity | other - same vocabulary as Alert.category, so an
    # incident's root-cause category can be matched directly against this
    # column with no translation table.
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Free-form tag list for keyword/tag search (Knowledge Base
    # requirement) - Postgres ARRAY rather than a join table, since tags
    # here are simple labels with no attributes of their own.
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), nullable=False, default=list, server_default="{}")

    # -- Matching criteria (all optional - narrows the match, doesn't
    # require an exact hit; see SOPMatcher) ---------------------------------
    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)  # source_type, e.g. "prtg"
    device_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    business_service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_services.id", ondelete="SET NULL"), nullable=True, index=True
    )
    region: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # Minimum incident priority this SOP applies to (P1-P4) - nullable
    # means "applies regardless of priority".
    min_priority: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # -- Recommendation metadata (surfaced on every recommendation per the
    # sprint's mandatory-fields requirement) --------------------------------
    required_skills: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    estimated_resolution_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rollback_procedure: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)

    # Denormalized pointer to the version currently recommended/executed by
    # default - SOPVersion rows are never deleted (audit trail), this just
    # tracks "which one is current". Nullable until at least one version
    # exists.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sop_versions.id", ondelete="SET NULL"), nullable=True
    )

    tenant: Mapped["Tenant"] = relationship()
    site: Mapped["Site | None"] = relationship()
    business_service: Mapped["BusinessService | None"] = relationship()
    versions: Mapped[list["SOPVersion"]] = relationship(
        back_populates="sop", foreign_keys="SOPVersion.sop_id", cascade="all, delete-orphan"
    )
    current_version: Mapped["SOPVersion | None"] = relationship(foreign_keys=[current_version_id])
    runbooks: Mapped[list["Runbook"]] = relationship(back_populates="sop")

    def __repr__(self) -> str:
        return f"<SOP id={self.id} sop_number={self.sop_number!r} category={self.category!r}>"


class SOPVersion(Base, UUIDPKMixin):
    __tablename__ = "sop_versions"
    __table_args__ = (
        UniqueConstraint("sop_id", "version_number", name="uq_sop_versions_sop_id_version_number"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sops.id", ondelete="CASCADE"), nullable=False, index=True
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ordered list of investigation/remediation steps for this version -
    # e.g. [{"order": 1, "action": "Check device reachability via ping"}],
    # same shape as the existing sop/*.yaml `steps:` lists, just versioned
    # and queryable instead of a flat file. JSONB (not a child table) since
    # steps have no independent lifecycle - they only ever change by
    # creating a new SOPVersion.
    steps: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    possible_causes: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )

    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # draft | approved | deprecated - a version only becomes eligible for
    # SOPMatcher recommendation once "approved" (manual approval workflow
    # requirement extends to the SOP content itself, not just individual
    # runbook executions).
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft", index=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    sop: Mapped["SOP"] = relationship(back_populates="versions", foreign_keys=[sop_id])

    def __repr__(self) -> str:
        return f"<SOPVersion id={self.id} sop_id={self.sop_id} version_number={self.version_number} status={self.status!r}>"
