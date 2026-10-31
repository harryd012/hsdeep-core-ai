"""AI Diagnosis repository for database access."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_diagnosis import AIDiagnosis


class AiDiagnosisRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        noc_event_id: uuid.UUID,
        requested_by: uuid.UUID | None = None,
        status: str = "pending",
        evidence_sufficiency: str = "insufficient",
        model_used: str = "ollama-local",
        context_snapshot: dict | None = None,
    ) -> AIDiagnosis:
        diagnosis = AIDiagnosis(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            noc_event_id=noc_event_id,
            requested_by=requested_by,
            status=status,
            evidence_sufficiency=evidence_sufficiency,
            model_used=model_used,
            context_snapshot=context_snapshot or {},
        )
        self.session.add(diagnosis)
        await self.session.flush()
        return diagnosis

    async def get(self, diagnosis_id: uuid.UUID, tenant_id: uuid.UUID) -> AIDiagnosis | None:
        result = await self.session.execute(
            select(AIDiagnosis).where(AIDiagnosis.id == diagnosis_id, AIDiagnosis.tenant_id == tenant_id, AIDiagnosis.is_deleted.is_(False))
        )
        return result.scalar_one_or_none()

    async def get_by_noc_event(self, noc_event_id: uuid.UUID, tenant_id: uuid.UUID) -> AIDiagnosis | None:
        result = await self.session.execute(
            select(AIDiagnosis).where(AIDiagnosis.noc_event_id == noc_event_id, AIDiagnosis.tenant_id == tenant_id, AIDiagnosis.is_deleted.is_(False))
        )
        return result.scalar_one_or_none()

    async def list(self, tenant_id: uuid.UUID, *, status: str | None = None,
                   limit: int = 100, offset: int = 0) -> list[AIDiagnosis]:
        stmt = select(AIDiagnosis).where(AIDiagnosis.tenant_id == tenant_id, AIDiagnosis.is_deleted.is_(False))
        if status:
            stmt = stmt.where(AIDiagnosis.status == status)
        stmt = stmt.order_by(AIDiagnosis.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, tenant_id: uuid.UUID, *, status: str | None = None) -> int:
        stmt = select(func.count()).select_from(AIDiagnosis).where(AIDiagnosis.tenant_id == tenant_id, AIDiagnosis.is_deleted.is_(False))
        if status:
            stmt = stmt.where(AIDiagnosis.status == status)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        total = await self.count(tenant_id)
        by_status = {}
        for status in ["pending", "analyzing", "completed", "failed", "insufficient_evidence"]:
            by_status[status] = await self.count(tenant_id, status=status)
        # Average confidence
        stmt = select(func.avg(AIDiagnosis.overall_confidence)).select_from(AIDiagnosis).where(
            AIDiagnosis.tenant_id == tenant_id, AIDiagnosis.is_deleted.is_(False)
        )
        result = await self.session.execute(stmt)
        avg_confidence = result.scalar_one() or 0.0
        # Approval required count
        stmt = select(func.count()).select_from(AIDiagnosis).where(
            AIDiagnosis.tenant_id == tenant_id, AIDiagnosis.is_deleted.is_(False), AIDiagnosis.approval_required.is_(True)
        )
        result = await self.session.execute(stmt)
        approval_required_count = result.scalar_one()
        return {
            "total_diagnoses": total,
            "by_status": by_status,
            "avg_confidence": round(float(avg_confidence), 2),
            "approval_required_count": approval_required_count,
        }

    async def update(self, diagnosis: AIDiagnosis, **fields) -> AIDiagnosis:
        for key, value in fields.items():
            setattr(diagnosis, key, value)
        diagnosis.updated_at = datetime.utcnow()
        await self.session.flush()
        return diagnosis

    async def delete(self, diagnosis: AIDiagnosis) -> None:
        diagnosis.is_deleted = True
        diagnosis.deleted_at = datetime.utcnow()
        diagnosis.updated_at = datetime.utcnow()
        await self.session.flush()
