"""
SOP recommendations for one incident, with the operator's answers.

Scoring is `SOPRecommendationEngine`'s (approved, active SOPs only; nothing
is re-scored here). This service adds the per-incident decisions an operator
records - accepted / dismissed - and hides dismissed SOPs unless asked.

Recording a decision re-runs the engine to confirm the SOP is still
recommended for the incident and to keep the confidence the operator saw.
Accepting executes nothing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident
from app.models.sop_recommendation_decision import SOPRecommendationDecision
from app.repositories.incident_repository import IncidentRepository
from app.schemas.sop import IncidentSOPRecommendationOut, SOPRecommendationDecisionOut
from app.services.sop.recommendation_engine import (
    SOPRecommendation,
    SOPRecommendationEngine,
    build_sop_recommendation_engine,
)

#: Upper bound on candidates scored when a decision is checked; the tenant's
#: approved catalogue is small (tens of SOPs), so this is every match.
_ALL_MATCHES = 200


class RecommendationIncidentNotFound(Exception):
    pass


class SOPNotRecommended(Exception):
    """The SOP is not (or no longer) a recommendation for this incident."""


class IncidentSOPRecommendations:
    def __init__(self, db: AsyncSession, engine: SOPRecommendationEngine | None = None):
        self.db = db
        self.engine = engine or build_sop_recommendation_engine(db)

    async def _incident(self, tenant_id: uuid.UUID, incident_id: uuid.UUID) -> Incident:
        incident = await IncidentRepository(self.db).get(incident_id, tenant_id)
        if incident is None:
            raise RecommendationIncidentNotFound(str(incident_id))
        return incident

    async def _decisions(self, tenant_id: uuid.UUID, incident_id: uuid.UUID) -> dict[uuid.UUID, SOPRecommendationDecision]:
        rows = await self.db.execute(
            select(SOPRecommendationDecision).where(
                SOPRecommendationDecision.tenant_id == tenant_id,
                SOPRecommendationDecision.incident_id == incident_id,
            )
        )
        return {d.sop_id: d for d in rows.scalars()}

    async def list(
        self,
        tenant_id: uuid.UUID,
        incident_id: uuid.UUID,
        *,
        limit: int = 3,
        min_confidence: float = 30.0,
        include_dismissed: bool = False,
    ) -> list[IncidentSOPRecommendationOut]:
        """Top `limit` recommendations, best first. Dismissed SOPs are
        dropped before the limit is applied, so dismissing one surfaces the
        next best rather than shrinking the list."""
        incident = await self._incident(tenant_id, incident_id)
        decisions = await self._decisions(tenant_id, incident_id)
        hidden = 0 if include_dismissed else sum(1 for d in decisions.values() if d.decision == "dismissed")
        recs = await self.engine.recommend(incident, limit=limit + hidden, min_confidence=min_confidence)

        out: list[IncidentSOPRecommendationOut] = []
        for rec in recs:
            decision = decisions.get(rec.sop_id)
            if decision is not None and decision.decision == "dismissed" and not include_dismissed:
                continue
            out.append(self._out(rec, decision))
            if len(out) == limit:
                break
        return out

    async def decide(
        self,
        tenant_id: uuid.UUID,
        incident_id: uuid.UUID,
        sop_id: uuid.UUID,
        *,
        decision: str,
        reason: str | None,
        actor_id: uuid.UUID | None,
    ) -> tuple[SOPRecommendationDecision, dict, dict]:
        """Record (or change) the answer. Returns (row, old values, new
        values); both dicts are empty when the same answer is repeated."""
        incident = await self._incident(tenant_id, incident_id)
        recs = await self.engine.recommend(incident, limit=_ALL_MATCHES, min_confidence=0.0)
        rec = next((r for r in recs if r.sop_id == sop_id), None)
        if rec is None:
            raise SOPNotRecommended(str(sop_id))

        reason = (reason or "").strip() or None
        row = (await self._decisions(tenant_id, incident_id)).get(sop_id)
        if row is not None and row.decision == decision and row.reason == reason:
            return row, {}, {}

        old = {"decision": row.decision, "reason": row.reason} if row is not None else {}
        now = datetime.now(timezone.utc)
        if row is None:
            row = SOPRecommendationDecision(tenant_id=tenant_id, incident_id=incident_id, sop_id=sop_id)
            self.db.add(row)
        row.decision = decision
        row.reason = reason
        row.sop_version_id = rec.current_version_id
        row.confidence_score = rec.confidence_score
        row.decided_by = actor_id
        row.decided_at = now
        await self.db.flush()
        new = {"decision": decision, "reason": reason, "confidence_score": rec.confidence_score}
        return row, old, new

    @staticmethod
    def _out(rec: SOPRecommendation, decision: SOPRecommendationDecision | None) -> IncidentSOPRecommendationOut:
        return IncidentSOPRecommendationOut(
            sop_id=rec.sop_id,
            sop_number=rec.sop_number,
            name=rec.name,
            category=rec.category,
            confidence_score=rec.confidence_score,
            matched_criteria=rec.matched_criteria,
            reasoning=rec.reasoning,
            business_impact_score=rec.business_impact_score,
            estimated_resolution_minutes=rec.estimated_resolution_minutes,
            required_skills=rec.required_skills,
            requires_approval=rec.requires_approval,
            rollback_procedure=rec.rollback_procedure,
            current_version_id=rec.current_version_id,
            decision=SOPRecommendationDecisionOut.model_validate(decision) if decision is not None else None,
        )
