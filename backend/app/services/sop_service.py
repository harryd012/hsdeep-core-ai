"""
SOP service layer.

Thin, mirrors `IncidentService`'s split: SOP MATCHING/RECOMMENDATION is
owned by `SOPRecommendationEngine`, so this class exposes only the CRUD/
lookup/knowledge-base-search paths the API needs, plus version management
(create/approve a new SOPVersion, promote it to current).
"""
from __future__ import annotations

import uuid

from app.models.sop import SOP, SOPVersion
from app.repositories.sop_repository import SOPRepository


class SOPNotFound(Exception):
    pass


class SOPVersionNotFound(Exception):
    pass


class SOPService:
    def __init__(self, sop_repo: SOPRepository):
        self.sop_repo = sop_repo

    async def get(self, sop_id: uuid.UUID, tenant_id: uuid.UUID) -> SOP:
        sop = await self.sop_repo.get(sop_id, tenant_id)
        if sop is None:
            raise SOPNotFound(f"SOP {sop_id} not found")
        return sop

    async def list_sops(self, tenant_id: uuid.UUID, **filters) -> list[SOP]:
        return await self.sop_repo.list(tenant_id, **filters)

    async def search(self, tenant_id: uuid.UUID, query: str, *, limit: int = 25) -> list[SOP]:
        """Knowledge Base search deliverable."""
        return await self.sop_repo.search_knowledge_base(tenant_id, query, limit=limit)

    async def create_sop(self, tenant_id: uuid.UUID, **fields) -> SOP:
        return await self.sop_repo.create(tenant_id=tenant_id, **fields)

    async def create_version(
        self,
        sop_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        steps: list[dict],
        possible_causes: list[str] | None = None,
        change_summary: str | None = None,
    ) -> SOPVersion:
        """SOP Version Management deliverable: appends a new draft version.
        Does NOT promote it to current - call approve_and_promote() once an
        approver signs off, so `current_version_id` never points at
        unreviewed content."""
        sop = await self.get(sop_id, tenant_id)
        version_number = await self.sop_repo.next_version_number(sop.id)
        return await self.sop_repo.create_version(
            tenant_id=tenant_id,
            sop_id=sop.id,
            version_number=version_number,
            steps=steps,
            possible_causes=possible_causes or [],
            change_summary=change_summary,
            status="draft",
        )

    async def list_versions(self, sop_id: uuid.UUID, tenant_id: uuid.UUID) -> list[SOPVersion]:
        await self.get(sop_id, tenant_id)  # tenant-scope check
        return await self.sop_repo.list_versions(sop_id)

    async def approve_and_promote(
        self, sop_id: uuid.UUID, version_id: uuid.UUID, tenant_id: uuid.UUID, *, approved_by: uuid.UUID | None
    ) -> SOP:
        """Manual approval workflow applied to SOP content itself (distinct
        from RunbookExecution's approval workflow) - a version only becomes
        eligible for SOPMatcher recommendation once approved AND set as the
        SOP's current_version_id."""
        sop = await self.get(sop_id, tenant_id)
        version = await self.sop_repo.get_version(version_id, tenant_id)
        if version is None or version.sop_id != sop.id:
            raise SOPVersionNotFound(f"SOPVersion {version_id} not found for SOP {sop_id}")
        approved = await self.sop_repo.approve_version(version, approved_by=approved_by)
        return await self.sop_repo.set_current_version(sop, approved)
