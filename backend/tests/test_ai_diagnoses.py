"""AI Diagnosis service tests (Phase 6 - AI diagnostic context)."""
from __future__ import annotations

import uuid

import pytest

from app.core.db import AsyncSessionLocal
from app.models.tenant import Tenant
from app.repositories.ai_diagnosis_repository import AiDiagnosisRepository
from app.repositories.noc_event_repository import NocEventRepository
from app.repositories.agent_repository import AgentRepository
from app.services.ai_diagnosis_service import AiDiagnosisNotFound, AiDiagnosisService
from app.services.noc_service import NocEventService

pytestmark = pytest.mark.asyncio


async def _make_noc_event(tenant_id: uuid.UUID, db: AsyncSessionLocal):
    service = NocEventService(
        noc_repo=NocEventRepository(db),
        agent_repo=AgentRepository(db),
    )
    return await service.create(
        tenant_id,
        title="Correlated outage",
        severity="critical",
        source="correlation",
        category="availability",
    )


async def test_create_diagnosis(tenant: Tenant, db: AsyncSessionLocal):
    event = await _make_noc_event(tenant.id, db)
    service = AiDiagnosisService(AiDiagnosisRepository(db))
    diagnosis = await service.create(
        tenant_id=tenant.id,
        noc_event_id=event.id,
        requested_by=None,
        context_snapshot={"severity": "critical", "evidence_count": 3},
    )
    assert diagnosis.tenant_id == tenant.id
    assert diagnosis.noc_event_id == event.id
    assert diagnosis.status == "pending"
    assert diagnosis.evidence_sufficiency == "insufficient"


async def test_get_diagnosis_not_found(tenant: Tenant, db: AsyncSessionLocal):
    service = AiDiagnosisService(AiDiagnosisRepository(db))
    with pytest.raises(AiDiagnosisNotFound):
        await service.get(uuid.uuid4(), tenant.id)


async def test_diagnosis_tenant_isolation(tenant: Tenant, db: AsyncSessionLocal):
    event = await _make_noc_event(tenant.id, db)
    service = AiDiagnosisService(AiDiagnosisRepository(db))
    diagnosis = await service.create(tenant_id=tenant.id, noc_event_id=event.id)
    other_tenant_id = uuid.uuid4()
    with pytest.raises(AiDiagnosisNotFound):
        await service.get(diagnosis.id, other_tenant_id)


async def test_get_by_noc_event(tenant: Tenant, db: AsyncSessionLocal):
    event = await _make_noc_event(tenant.id, db)
    service = AiDiagnosisService(AiDiagnosisRepository(db))
    diagnosis = await service.create(tenant_id=tenant.id, noc_event_id=event.id)
    found = await service.get_by_noc_event(event.id, tenant.id)
    assert found is not None
    assert found.id == diagnosis.id


async def test_update_diagnosis(tenant: Tenant, db: AsyncSessionLocal):
    event = await _make_noc_event(tenant.id, db)
    service = AiDiagnosisService(AiDiagnosisRepository(db))
    diagnosis = await service.create(tenant_id=tenant.id, noc_event_id=event.id)
    updated = await service.update(
        diagnosis.id, tenant.id,
        status="validated",
        analyst_approved=True,
        evidence_sufficiency="sufficient",
        overall_confidence=85,
        diagnosis_summary="Root cause is upstream firewall path degradation",
    )
    assert updated.status == "validated"
    assert updated.analyst_approved is True
    assert updated.overall_confidence == 85
    assert "firewall" in updated.diagnosis_summary


async def test_delete_diagnosis(tenant: Tenant, db: AsyncSessionLocal):
    event = await _make_noc_event(tenant.id, db)
    service = AiDiagnosisService(AiDiagnosisRepository(db))
    diagnosis = await service.create(tenant_id=tenant.id, noc_event_id=event.id)
    await service.delete(diagnosis.id, tenant.id)
    with pytest.raises(AiDiagnosisNotFound):
        await service.get(diagnosis.id, tenant.id)