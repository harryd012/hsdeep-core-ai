"""
Repository Pattern: direct SQLAlchemy access for global Plan rows.
Plans are NOT tenant-scoped - they are the shared catalog of subscription tiers.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan


class PlanRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, plan_id: uuid.UUID) -> Plan | None:
        stmt = select(Plan).where(Plan.id == plan_id, Plan.is_deleted.is_(False))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_tier(self, tier: str) -> Plan | None:
        stmt = select(Plan).where(Plan.tier == tier, Plan.is_deleted.is_(False))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_public(self) -> list[Plan]:
        stmt = (
            select(Plan)
            .where(Plan.is_public.is_(True), Plan.is_deleted.is_(False))
            .order_by(Plan.price_monthly)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[Plan]:
        stmt = select(Plan).where(Plan.is_deleted.is_(False)).order_by(Plan.price_monthly)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
        tier: str,
        description: str | None,
        price_monthly: float,
        price_annual: float,
        device_limit: int,
        sensor_limit: int,
        api_rate_limit: int,
        collector_limit: int,
        seats: int,
        features_json: dict,
        is_public: bool = True,
        alert_limit: int = 0,
        collector_run_limit: int = 0,
        display_order: int = 0,
    ) -> Plan:
        plan = Plan(
            name=name,
            tier=tier,
            description=description,
            price_monthly=price_monthly,
            price_annual=price_annual,
            device_limit=device_limit,
            sensor_limit=sensor_limit,
            api_rate_limit=api_rate_limit,
            collector_limit=collector_limit,
            seats=seats,
            features_json=features_json,
            is_public=is_public,
            alert_limit=alert_limit,
            collector_run_limit=collector_run_limit,
            display_order=display_order,
        )
        self.db.add(plan)
        await self.db.flush()
        await self.db.refresh(plan)
        return plan