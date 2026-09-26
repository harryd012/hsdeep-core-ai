"""Repository for SLA Engine data access (SLAPolicy, BusinessHours, HolidayCalendar)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sla import BusinessHours, HolidayCalendar, SLAPolicy


class SLARepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- SLAPolicy -----------------------------------------------------------

    async def create_policy(self, **fields) -> SLAPolicy:
        policy = SLAPolicy(**fields)
        self.db.add(policy)
        await self.db.flush()
        await self.db.refresh(policy)
        return policy

    async def get_policy(self, policy_id: uuid.UUID, tenant_id: uuid.UUID) -> SLAPolicy | None:
        stmt = select(SLAPolicy).where(
            SLAPolicy.id == policy_id,
            SLAPolicy.tenant_id == tenant_id,
            SLAPolicy.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_policies(
        self, tenant_id: uuid.UUID, *, is_active: bool | None = None
    ) -> list[SLAPolicy]:
        stmt = select(SLAPolicy).where(
            SLAPolicy.tenant_id == tenant_id, SLAPolicy.is_deleted.is_(False)
        )
        if is_active is not None:
            stmt = stmt.where(SLAPolicy.is_active.is_(is_active))
        stmt = stmt.order_by(SLAPolicy.priority.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_policy_for_priority(
        self, tenant_id: uuid.UUID, priority: str, business_service_id: uuid.UUID | None = None
    ) -> SLAPolicy | None:
        """Find the best-matching active SLA policy for a given priority.

        Prefers a service-specific policy over a tenant-wide one.
        """
        # Try service-specific first
        if business_service_id:
            stmt = select(SLAPolicy).where(
                SLAPolicy.tenant_id == tenant_id,
                SLAPolicy.priority == priority,
                SLAPolicy.business_service_id == business_service_id,
                SLAPolicy.is_active.is_(True),
                SLAPolicy.is_deleted.is_(False),
            )
            result = (await self.db.execute(stmt)).scalar_one_or_none()
            if result:
                return result

        # Fall back to tenant-wide (no business_service_id)
        stmt = select(SLAPolicy).where(
            SLAPolicy.tenant_id == tenant_id,
            SLAPolicy.priority == priority,
            SLAPolicy.business_service_id.is_(None),
            SLAPolicy.is_active.is_(True),
            SLAPolicy.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def update_policy(self, policy: SLAPolicy, **fields) -> SLAPolicy:
        for key, value in fields.items():
            if hasattr(policy, key):
                setattr(policy, key, value)
        await self.db.flush()
        await self.db.refresh(policy)
        return policy

    async def soft_delete_policy(self, policy: SLAPolicy) -> None:
        policy.is_deleted = True
        await self.db.flush()

    # -- BusinessHours -------------------------------------------------------

    async def create_business_hours(self, **fields) -> BusinessHours:
        bh = BusinessHours(**fields)
        self.db.add(bh)
        await self.db.flush()
        await self.db.refresh(bh)
        return bh

    async def get_business_hours(self, bh_id: uuid.UUID, tenant_id: uuid.UUID) -> BusinessHours | None:
        stmt = select(BusinessHours).where(
            BusinessHours.id == bh_id,
            BusinessHours.tenant_id == tenant_id,
            BusinessHours.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_business_hours(self, tenant_id: uuid.UUID) -> list[BusinessHours]:
        stmt = select(BusinessHours).where(
            BusinessHours.tenant_id == tenant_id, BusinessHours.is_deleted.is_(False)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_business_hours(self, bh: BusinessHours, **fields) -> BusinessHours:
        for key, value in fields.items():
            if hasattr(bh, key):
                setattr(bh, key, value)
        await self.db.flush()
        await self.db.refresh(bh)
        return bh

    async def soft_delete_business_hours(self, bh: BusinessHours) -> None:
        bh.is_deleted = True
        await self.db.flush()

    # -- HolidayCalendar -----------------------------------------------------

    async def create_holiday(self, **fields) -> HolidayCalendar:
        holiday = HolidayCalendar(**fields)
        self.db.add(holiday)
        await self.db.flush()
        await self.db.refresh(holiday)
        return holiday

    async def list_holidays(self, tenant_id: uuid.UUID) -> list[HolidayCalendar]:
        stmt = select(HolidayCalendar).where(
            HolidayCalendar.tenant_id == tenant_id, HolidayCalendar.is_deleted.is_(False)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_holidays_between(
        self, tenant_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[HolidayCalendar]:
        """Get all holidays (including recurring) that fall between start and end."""
        stmt = select(HolidayCalendar).where(
            HolidayCalendar.tenant_id == tenant_id,
            HolidayCalendar.is_deleted.is_(False),
        )
        all_holidays = list((await self.db.execute(stmt)).scalars().all())
        result = []
        for h in all_holidays:
            if h.is_recurring:
                # Check if month/day matches any date in the range
                h_month = h.holiday_date.month
                h_day = h.holiday_date.day
                current = start
                while current <= end:
                    if current.month == h_month and current.day == h_day:
                        result.append(h)
                        break
                    current = current.replace(day=current.day + 1) if current.day < 28 else current
            else:
                if start <= h.holiday_date <= end:
                    result.append(h)
        return result

    async def soft_delete_holiday(self, holiday: HolidayCalendar) -> None:
        holiday.is_deleted = True
        await self.db.flush()