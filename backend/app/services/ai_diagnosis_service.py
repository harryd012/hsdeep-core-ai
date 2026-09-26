"""AI Diagnosis service for business logic."""
from __future__ import annotations

import uuid

from app.models.ai_diagnosis import AIDiagnosis
from app.repositories.ai_diagnosis_repository import AiDiagnosisRepository


class AiDiagnosisNotFound(Exception):
    pass


class AiDiagnosisService:
    def __init__(self, repo: AiDiagnosisRepository):
        self.repo = repo

    async def create(self, *, tenant_id: uuid.UUID, noc_event_id: uuid.UUID,
                     requested_by: uuid.UUID | None = None,
                     status: str = "pending",
                     evidence_sufficiency: str = "insufficient",
                     model_used: str = "ollama-local",
                     context_snapshot: dict | None = None) -> AIDiagnosis:
        return await self.repo.create(
            tenant_id=tenant_id,
            noc_event_id=noc_event_id,
            requested_by=requested_by,
            status=status,
            evidence_sufficiency=evidence_sufficiency,
            model_used=model_used,
            context_snapshot=context_snapshot,
        )

    async def get(self, diagnosis_id: uuid.UUID, tenant_id: uuid.UUID) -> AIDiagnosis:
        diagnosis = await self.repo.get(diagnosis_id, tenant_id)
        if diagnosis is None:
            raise AiDiagnosisNotFound(f"AI diagnosis {diagnosis_id} not found")
        return diagnosis

    async def get_by_noc_event(self, noc_event_id: uuid.UUID, tenant_id: uuid.UUID) -> AIDiagnosis | None:
        return await self.repo.get_by_noc_event(noc_event_id, tenant_id)

    async def list(self, tenant_id: uuid.UUID, *, status: str | None = None,
                   limit: int = 100, offset: int = 0) -> list[AIDiagnosis]:
        return await self.repo.list(tenant_id, status=status, limit=limit, offset=offset)

    async def count(self, tenant_id: uuid.UUID, *, status: str | None = None) -> int:
        return await self.repo.count(tenant_id, status=status)

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        return await self.repo.summary(tenant_id)

    async def update(self, diagnosis_id: uuid.UUID, tenant_id: uuid.UUID,
                                          **fields) -> AIDiagnosis:
        diagnosis = await self.get(diagnosis_id, tenant_id)
        return await self.repo.update(diagnosis, **fields)

    async def delete(self, diagnosis_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        diagnosis = await self.get(diagnosis_id, tenant_id)
        await self.repo.delete(diagnosis)
