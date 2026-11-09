"""SOP recommendations on an incident (docs/SOP_RECOMMENDATIONS.md).

The real SOPRecommendationEngine scores real rows: only active SOPs with an
approved current version and a matching category are recommended, best first.
Decisions are recorded per (incident, SOP), are idempotent, audited, hide a
dismissed SOP from the default list, and stay inside the tenant.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.alert import Alert
from app.models.incident import Incident
from app.models.knowledge_audit import AuditEvent
from app.models.sop import SOP, SOPVersion
from app.models.sop_recommendation_decision import SOPRecommendationDecision
from app.models.tenant import Tenant

NOW = datetime.now(timezone.utc)
READER = frozenset({"incidents.read", "sops.read"})
WRITER = READER | {"incidents.write"}


def _client(tenant_id, permissions=WRITER):
    token = create_access_token(
        user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local", is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


async def _sop(db, tenant_id, number, *, category="availability", vendor=None, status="approved", **fields) -> SOP:
    sop = SOP(tenant_id=tenant_id, sop_number=number, name=f"{number} procedure", category=category, vendor=vendor,
              estimated_resolution_minutes=20, required_skills=["network"], rollback_procedure="Undo step 1", **fields)
    db.add(sop)
    await db.flush()
    version = SOPVersion(tenant_id=tenant_id, sop_id=sop.id, version_number=1, steps=[{"order": 1, "action": "check"}],
                         possible_causes=[], status=status, created_at=NOW)
    db.add(version)
    await db.flush()
    sop.current_version_id = version.id
    return sop


@pytest_asyncio.fixture
async def seeded(tenant, seeded_subscription):
    """An availability incident (Cisco) and four SOPs: one vendor-specific
    match, one generic match, one in another category, one only drafted."""
    async with AsyncSessionLocal() as db:
        incident = Incident(tenant_id=tenant.id, title="Core switch down", severity="critical", status="open",
                            opened_at=NOW, last_alert_at=NOW)
        db.add(incident)
        await db.flush()
        db.add(Alert(tenant_id=tenant.id, incident_id=incident.id, title="Switch unreachable", severity="critical",
                     status="open", category="availability", vendor="cisco", is_root_cause_candidate=True,
                     triggered_at=NOW, last_occurred_at=NOW))
        cisco = await _sop(db, tenant.id, "SOP-901", vendor="cisco")
        generic = await _sop(db, tenant.id, "SOP-902")
        await _sop(db, tenant.id, "SOP-903", category="security")
        draft = await _sop(db, tenant.id, "SOP-904", status="draft")
        await db.commit()
        ids = {"incident": incident.id, "cisco": cisco.id, "generic": generic.id, "draft": draft.id}
    yield ids
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tenant.id))
        await db.commit()


def _url(incident_id, sop_id=None) -> str:
    base = f"/api/incidents/{incident_id}/sop-recommendations"
    return f"{base}/{sop_id}/decision" if sop_id else base


async def test_recommendations_are_scored_filtered_and_ordered(tenant, seeded):
    async with _client(tenant.id, READER) as client:
        r = await client.get(_url(seeded["incident"]), params={"min_confidence": 0})
        assert r.status_code == 200, r.text
        recs = r.json()
        ids = [rec["sop_id"] for rec in recs]
        # Vendor match outranks the generic one; other category and drafts never appear.
        assert ids == [str(seeded["cisco"]), str(seeded["generic"])]
        assert recs[0]["confidence_score"] > recs[1]["confidence_score"]
        first = recs[0]
        assert first["sop_number"] == "SOP-901"
        assert first["matched_criteria"]["category"] is True
        assert first["estimated_resolution_minutes"] == 20
        assert first["required_skills"] == ["network"]
        assert first["rollback_procedure"] == "Undo step 1"
        assert first["decision"] is None

        assert len((await client.get(_url(seeded["incident"]), params={"min_confidence": 0, "limit": 1})).json()) == 1
        above = (await client.get(_url(seeded["incident"]), params={"min_confidence": 100})).json()
        assert above == []


async def test_decisions_are_recorded_idempotent_and_audited(tenant, seeded):
    incident, cisco, generic = seeded["incident"], seeded["cisco"], seeded["generic"]
    async with _client(tenant.id) as client:
        r = await client.post(_url(incident, cisco), json={"decision": "accepted"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["decision"] == "accepted" and body["confidence_score"] > 0
        assert body["decided_by"] is None  # test principal is not a user row
        assert body["sop_version_id"] is not None

        # Same answer again: no new row, no new audit event.
        assert (await client.post(_url(incident, cisco), json={"decision": "accepted"})).status_code == 200

        # Dismissing hides the SOP from the default list, not from include_dismissed.
        r = await client.post(_url(incident, generic), json={"decision": "dismissed", "reason": "  not this site  "})
        assert r.json()["reason"] == "not this site"
        listed = (await client.get(_url(incident), params={"min_confidence": 0})).json()
        assert [x["sop_id"] for x in listed] == [str(cisco)]
        assert listed[0]["decision"]["decision"] == "accepted"
        everything = (await client.get(_url(incident), params={"min_confidence": 0, "include_dismissed": True})).json()
        assert {x["sop_id"] for x in everything} == {str(cisco), str(generic)}

        # Changing an answer replaces it.
        assert (await client.post(_url(incident, cisco), json={"decision": "dismissed"})).json()["decision"] == "dismissed"

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(SOPRecommendationDecision).where(
            SOPRecommendationDecision.incident_id == incident))).scalars().all()
        assert {(r.sop_id, r.decision) for r in rows} == {(cisco, "dismissed"), (generic, "dismissed")}
        events = (await db.execute(select(AuditEvent).where(
            AuditEvent.tenant_id == tenant.id, AuditEvent.action.like("sop_recommendation.%")))).scalars().all()
        assert sorted(e.action for e in events) == [
            "sop_recommendation.accepted", "sop_recommendation.dismissed", "sop_recommendation.dismissed",
        ]


async def test_validation_permissions_and_not_recommended(tenant, seeded):
    incident = seeded["incident"]
    async with _client(tenant.id) as client:
        assert (await client.post(_url(incident, seeded["cisco"]), json={"decision": "maybe"})).status_code == 422
        # A drafted SOP is not a recommendation, so it cannot be decided.
        assert (await client.post(_url(incident, seeded["draft"]), json={"decision": "accepted"})).status_code == 404
        assert (await client.get(_url(uuid.uuid4()))).status_code == 404
    async with _client(tenant.id, READER) as client:
        assert (await client.post(_url(incident, seeded["cisco"]), json={"decision": "accepted"})).status_code == 403
    async with _client(tenant.id, frozenset({"incidents.read"})) as client:
        assert (await client.get(_url(incident))).status_code == 403


async def test_other_tenant_sees_nothing(tenant, seeded):
    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
    try:
        async with _client(other.id) as client:
            assert (await client.get(_url(seeded["incident"]))).status_code == 404
            r = await client.post(_url(seeded["incident"], seeded["cisco"]), json={"decision": "accepted"})
            assert r.status_code in (402, 403, 404)  # no subscription or not found - never a write
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(SOPRecommendationDecision).where(
                SOPRecommendationDecision.incident_id == seeded["incident"]))).scalars().all() == []
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()
