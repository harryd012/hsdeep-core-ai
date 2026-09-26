"""
Repository Pattern: all direct SQLAlchemy access for SOP/SOPVersion lives
here, same convention as IncidentRepository/DeviceRepository. New
repository for the SOP & Runbook Intelligence Engine sprint - authorized
per the sprint brief since SOP/SOPVersion are new domain entities (same
precedent as BusinessService/MaintenanceWindow repositories added in the
AI Priority Engine sprint).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.sop import SOP, SOPVersion


class SOPRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- SOP -----------------------------------------------------------------
    async def create(self, **fields) -> SOP:
        sop = SOP(**fields)
        self.db.add(sop)
        await self.db.flush()
        await self.db.refresh(sop)
        return sop

    async def get(self, sop_id: uuid.UUID, tenant_id: uuid.UUID) -> SOP | None:
        stmt = select(SOP).where(
            SOP.id == sop_id, SOP.tenant_id == tenant_id, SOP.is_deleted.is_(False)
        ).options(selectinload(SOP.versions), selectinload(SOP.current_version))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_number(self, tenant_id: uuid.UUID, sop_number: str) -> SOP | None:
        """Used for idempotent seeding/import - keyed on the human-readable
        (tenant_id, sop_number) identity rather than the UUID PK."""
        stmt = select(SOP).where(
            SOP.tenant_id == tenant_id, SOP.sop_number == sop_number, SOP.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        category: str | None = None,
        vendor: str | None = None,
        platform: str | None = None,
        device_type: str | None = None,
        is_active: bool | None = True,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SOP]:
        stmt = select(SOP).where(SOP.tenant_id == tenant_id, SOP.is_deleted.is_(False))
        if category is not None:
            stmt = stmt.where(SOP.category == category)
        if vendor is not None:
            stmt = stmt.where(SOP.vendor == vendor)
        if platform is not None:
            stmt = stmt.where(SOP.platform == platform)
        if device_type is not None:
            stmt = stmt.where(SOP.device_type == device_type)
        if is_active is not None:
            stmt = stmt.where(SOP.is_active == is_active)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                (SOP.name.ilike(like))
                | (SOP.description.ilike(like))
                | (SOP.sop_number.ilike(like))
            )
        stmt = stmt.order_by(SOP.sop_number).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def search_knowledge_base(self, tenant_id: uuid.UUID, query: str, *, limit: int = 25) -> list[SOP]:
        """Knowledge Base search deliverable - keyword/tag search across
        name, description, sop_number, and the tags array. Case-insensitive
        substring match (no external search engine dependency), consistent
        with every other "search" implemented in this codebase so far
        (e.g. DeviceRepository.list(search=...))."""
        like = f"%{query}%"
        stmt = (
            select(SOP)
            .where(
                SOP.tenant_id == tenant_id,
                SOP.is_deleted.is_(False),
                SOP.is_active.is_(True),
                or_(
                    SOP.name.ilike(like),
                    SOP.description.ilike(like),
                    SOP.sop_number.ilike(like),
                    SOP.tags.any(query),
                ),
            )
            .order_by(SOP.sop_number)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_matching(self, tenant_id: uuid.UUID) -> list[SOP]:
        """Candidate pool for SOPMatcher: active SOPs whose current version
        has actually been approved. A SOP with no approved current_version
        is excluded from recommendations - "a version only becomes eligible
        for SOPMatcher recommendation once approved" per sop.py's docstring.
        Matching criteria (vendor/platform/device_type/site/etc.) are all
        nullable/optional-narrowing, so this is deliberately NOT filtered
        further in SQL - SOPMatcher scores every candidate in Python since
        an unset criterion means "applies to anything", which isn't
        expressible as a simple equality filter."""
        stmt = (
            select(SOP)
            .join(SOPVersion, SOP.current_version_id == SOPVersion.id)
            .where(
                SOP.tenant_id == tenant_id,
                SOP.is_deleted.is_(False),
                SOP.is_active.is_(True),
                SOPVersion.status == "approved",
            )
            .options(selectinload(SOP.current_version))
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(self, tenant_id: uuid.UUID, *, is_active: bool | None = True) -> int:
        stmt = select(func.count()).select_from(SOP).where(SOP.tenant_id == tenant_id, SOP.is_deleted.is_(False))
        if is_active is not None:
            stmt = stmt.where(SOP.is_active == is_active)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def set_current_version(self, sop: SOP, version: SOPVersion) -> SOP:
        sop.current_version_id = version.id
        await self.db.flush()
        await self.db.refresh(sop)
        return sop

    # -- SOPVersion ------------------------------------------------------------
    async def create_version(self, **fields) -> SOPVersion:
        fields.setdefault("created_at", datetime.now(timezone.utc))
        version = SOPVersion(**fields)
        self.db.add(version)
        await self.db.flush()
        await self.db.refresh(version)
        return version

    async def get_version(self, version_id: uuid.UUID, tenant_id: uuid.UUID) -> SOPVersion | None:
        stmt = select(SOPVersion).where(SOPVersion.id == version_id, SOPVersion.tenant_id == tenant_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_versions(self, sop_id: uuid.UUID) -> list[SOPVersion]:
        stmt = select(SOPVersion).where(SOPVersion.sop_id == sop_id).order_by(SOPVersion.version_number.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def next_version_number(self, sop_id: uuid.UUID) -> int:
        stmt = select(func.max(SOPVersion.version_number)).where(SOPVersion.sop_id == sop_id)
        result = await self.db.execute(stmt)
        current_max = result.scalar_one_or_none()
        return (current_max or 0) + 1

    async def approve_version(self, version: SOPVersion, *, approved_by: uuid.UUID | None) -> SOPVersion:
        version.status = "approved"
        version.approved_by = approved_by
        version.approved_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(version)
        return version
