"""
BusinessService model (AI Priority Engine sprint). Represents a business-
facing service (e.g. "Customer Portal", "Payment Processing", "Email") that
one or more Devices support. Distinct from MonitoringSource/Device
(technical inventory) - this is the business-context layer the Priority
Engine needs to answer "how many users/how much revenue does this incident
actually affect", which nothing in the existing schema could answer before
this sprint.

Kept deliberately minimal (name, criticality, estimated_users) rather than
a full CMDB/service-catalog model - broadening this later (SLAs, owners,
dependency graphs) is additive, not a redesign.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.tenant import Tenant


class BusinessService(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "business_services"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_business_services_tenant_id_name"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # critical | high | medium | low - the primary business-criticality
    # input to BusinessImpactEngine/RiskEngine. Not nullable (unlike
    # Site.criticality/Device.criticality) since a BusinessService row is
    # only ever created deliberately by an operator classifying a real
    # service - there's no "legacy unclassified" case to accommodate here.
    criticality: Mapped[str] = mapped_column(String(20), nullable=False, default="medium", server_default="medium", index=True)

    # Best-effort estimate of how many end users depend on this service -
    # used by BusinessImpactEngine's affected-user-count calculation.
    # Nullable/0 is a legitimate "unknown" state, not an error.
    estimated_users: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    devices: Mapped[list["Device"]] = relationship(back_populates="business_service")

    def __repr__(self) -> str:
        return f"<BusinessService id={self.id} name={self.name!r} criticality={self.criticality!r}>"
