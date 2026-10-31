"""Palo Alto evidence-backed RCA engine.

Generates ranked root-cause hypotheses from collected evidence using
deterministic rules (never ML, never fabricated data). Each hypothesis carries
supporting/contradicting evidence, an honest confidence score with a stated
basis, the missing evidence that would raise confidence, and a suggested next
diagnostic action.

The configured AI provider may be used only to interpret/summarize; it is
never allowed to assert a verified fact that evidence does not support.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.schemas.paloalto import (
    DiagnosticRisk,
    EvidenceKind,
    EvidenceRef,
    RCAAssessment,
    RCAHypothesis,
    RCAScoreBasis,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rule_policy_deny(ev: EvidenceRef) -> bool:
    a = ev.attributes.get("rule_action")
    return ev.kind == EvidenceKind.TRAFFIC_LOG and bool(a) and a.lower() == "deny"


def _rule_threat_block(ev: EvidenceRef) -> bool:
    return ev.kind == EvidenceKind.THREAT_LOG and bool(
        ev.attributes.get("rule_name") or ev.summary
    )


def _rule_url_block(ev: EvidenceRef) -> bool:
    return ev.kind == EvidenceKind.URL_LOG and "block" in ev.summary.lower()


def _rule_decrypt_error(ev: EvidenceRef) -> bool:
    return ev.kind == EvidenceKind.DECRYPT_LOG and (
        "error" in ev.summary.lower() or "fail" in ev.summary.lower()
    )


class RCAEngine:
    """Deterministic hypothesis generator over collected evidence."""

    def assess(self, incident_id, evidence: Iterable[EvidenceRef]) -> RCAAssessment:
        evs = list(evidence)
        hypotheses: list[RCAHypothesis] = []

        deny = [e for e in evs if _rule_policy_deny(e)]
        if deny:
            hypotheses.append(
                self._build(
                    id="security_policy_deny",
                    title="Security policy denial or incorrect rule order",
                    probable_cause="A security policy matched the flow and denied it.",
                    supporting=deny,
                    contradicting=[],
                    base=70.0 if len(deny) > 1 else 55.0,
                    basis=RCAScoreBasis.CORROBORATED if len(deny) > 1 else RCAScoreBasis.SINGLE_SOURCE,
                    missing=["matching security rule config", "rule shadowing analysis"],
                    next_action="Run security_policy_analysis to inspect the matching rule and order.",
                    remediation="Adjust or add a security policy rule (requires approval).",
                    risk=DiagnosticRisk.REQUIRES_APPROVAL,
                )
            )

        threat = [e for e in evs if _rule_threat_block(e)]
        if threat:
            hypotheses.append(
                self._build(
                    id="threat_prevention_block",
                    title="Threat Prevention blocked the flow",
                    probable_cause="A threat signature matched and the action blocked the session.",
                    supporting=threat,
                    contradicting=[],
                    base=65.0,
                    basis=RCAScoreBasis.SINGLE_SOURCE,
                    missing=["threat log action field", "signature id"],
                    next_action="Run threat_url_filtering to confirm signature and action.",
                    remediation="Tune or suppress the threat profile entry (requires approval).",
                    risk=DiagnosticRisk.REQUIRES_APPROVAL,
                )
            )

        urlb = [e for e in evs if _rule_url_block(e)]
        if urlb:
            hypotheses.append(
                self._build(
                    id="url_filtering_block",
                    title="URL Filtering blocked the destination",
                    probable_cause="The destination URL matched a blocked category or custom list.",
                    supporting=urlb,
                    contradicting=[],
                    base=60.0,
                    basis=RCAScoreBasis.SINGLE_SOURCE,
                    missing=["url category", "custom url entries"],
                    next_action="Run threat_url_filtering to confirm category and override.",
                    remediation="Add a URL filtering override (requires approval).",
                    risk=DiagnosticRisk.REQUIRES_APPROVAL,
                )
            )

        dec = [e for e in evs if _rule_decrypt_error(e)]
        if dec:
            hypotheses.append(
                self._build(
                    id="ssl_decryption_failure",
                    title="TLS decryption or certificate failure",
                    probable_cause="Decryption failed, breaking an otherwise-permitted flow.",
                    supporting=dec,
                    contradicting=[],
                    base=58.0,
                    basis=RCAScoreBasis.SINGLE_SOURCE,
                    missing=["decrypt policy", "certificate chain"],
                    next_action="Run ssl_decryption to inspect decrypt policy and cert errors.",
                    remediation="Exclude the application from decryption or fix trust chain.",
                    risk=DiagnosticRisk.REQUIRES_APPROVAL,
                )
            )

        if not hypotheses:
            hypotheses.append(
                RCAHypothesis(
                    id="insufficient_evidence",
                    title="Insufficient evidence to assert a root cause",
                    probable_cause="Collected evidence does not yet support any specific hypothesis.",
                    supporting_evidence=[],
                    contradicting_evidence=[],
                    confidence_score=0.0,
                    score_basis=RCAScoreBasis.INSUFFICIENT,
                    missing_evidence=["traffic logs for the affected flow"],
                    next_diagnostic_action="Run traffic_log_investigation to gather flow evidence.",
                    suggested_remediation=None,
                    remediation_risk=DiagnosticRisk.READ_ONLY,
                )
            )

        hypotheses.sort(key=lambda h: h.confidence_score, reverse=True)
        return RCAAssessment(
            incident_id=incident_id,
            generated_at=_now(),
            hypotheses=hypotheses,
            ai_interpretation=None,
        )

    @staticmethod
    def _build(*, id, title, probable_cause, supporting, contradicting,
               base, basis, missing, next_action, remediation, risk) -> RCAHypothesis:
        # Confidence scales with corroborating evidence but is capped so a
        # single source never reads as near-certain.
        corroboration = min(len(supporting) - 1, 2) * 10.0
        score = min(base + corroboration, 90.0) if supporting else base
        return RCAHypothesis(
            id=id,
            title=title,
            probable_cause=probable_cause,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            confidence_score=round(score, 1),
            score_basis=basis,
            missing_evidence=missing,
            next_diagnostic_action=next_action,
            suggested_remediation=remediation,
            remediation_risk=risk,
        )
