"""Enterprise SLA Engine models.

SLAPolicy - per-tenant, per-priority SLA definitions with response and
resolution time targets. BusinessHours and HolidayCalendar enable
business-hours-only SLA calculation (excluding nights/weekends/holidays).

Tenant-scoped like every other model in this codebase.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class SLAPolicy(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """Defines SLA targets for response and resolution times.

    Matched to tickets by priority (P1-P4) and optionally by
    business_service_id for service-specific SLAs.
    """

    __tablename__ = "sla_policies"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # P1 | P2 | P3 | P4 - matches to ticket priority
    priority: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    # Optional: scope to a specific business service
    business_service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_services.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # SLA targets in minutes
    response_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Warning threshold (percentage of SLA time elapsed before warning, e.g. 80)
    warning_threshold_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=80, server_default="80"
    )

    # Whether to use business hours for SLA calculation
    use_business_hours: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Optional FK to a BusinessHours configuration
    business_hours_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_hours.id", ondelete="SET NULL"), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<SLAPolicy id={self.id} name={self.name!r} priority={self.priority!r}>"


class BusinessHours(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """Operating hours for SLA calculation.

    Stores per-day start/end times. Days where both start and end are
    NULL are considered non-working days.
    """

    __tablename__ = "business_hours"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC", server_default="UTC")

    # Per-day working hours (NULL = non-working day)
    monday_start: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    monday_end: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    tuesday_start: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    tuesday_end: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    wednesday_start: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    wednesday_end: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    thursday_start: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    thursday_end: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    friday_start: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    friday_end: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    saturday_start: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    saturday_end: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    sunday_start: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    sunday_end: Mapped[datetime | None] = mapped_column(Time, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<BusinessHours id={self.id} name={self.name!r} tz={self.timezone!r}>"


class HolidayCalendar(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """Holiday definitions for SLA calculation.

    Each row is a single holiday. `is_recurring=True` means the holiday
    repeats every year on the same month/day (the year part is ignored).
    """

    __tablename__ = "holiday_calendars"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The holiday date
    holiday_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # If True, this holiday repeats every year (month/day only)
    is_recurring: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<HolidayCalendar id={self.id} name={self.name!r} date={self.holiday_date}>"