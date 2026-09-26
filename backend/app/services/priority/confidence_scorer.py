"""
Confidence Scoring.

Two distinct confidence scores, both 0-100, both computed from real
completeness/consistency signals in already-stored data - never a
simulated or random value:

  Alert Confidence    - how much to trust ONE alert's classification
                        (device resolved? category matched a real rule
                        instead of falling back to "other"? severity
                        derived from a real threshold vs. a vendor-native
                        signal with no threshold at all?). Computed at
                        alert-creation time by AlertService, stored on
                        Alert.confidence_score.
  Incident Confidence  - how much to trust the INCIDENT-level priority
                        score that's about to be computed: average member
                        alert confidence, whether a root cause was
                        identified, and whether the affected device(s)
                        have a resolvable BusinessService/criticality
                        (an incident scored using only "medium" defaults
                        everywhere is real but less trustworthy than one
                        with fully-classified inputs).
"""
from __future__ import annotations

from app.models.alert import Alert


def alert_confidence(
    *,
    device_resolved: bool,
    category_matched: bool,
    severity_source: str,  # "threshold" | "vendor_native"
) -> float:
    """Called once per alert at creation time (AlertService), before
    CorrelationEngine/PriorityEngine ever see it. Base score with additive
    boosts/penalties for each completeness signal - deliberately simple
    arithmetic, not a weighted model, so it stays trivially explainable."""
    score = 50.0
    score += 20.0 if device_resolved else -10.0
    score += 15.0 if category_matched else -15.0
    score += 15.0 if severity_source == "threshold" else 0.0
    return max(0.0, min(100.0, score))


def incident_confidence(
    active_alerts: list[Alert],
    *,
    root_cause_identified: bool,
    inputs_fully_classified: bool,
) -> tuple[float, dict]:
    if not active_alerts:
        return 0.0, {"reason": "no active member alerts"}

    scored_alerts = [a.confidence_score for a in active_alerts if a.confidence_score is not None]
    avg_alert_confidence = sum(scored_alerts) / len(scored_alerts) if scored_alerts else 50.0

    score = avg_alert_confidence
    score += 10.0 if root_cause_identified else -10.0
    score += 10.0 if inputs_fully_classified else -5.0
    score = max(0.0, min(100.0, score))

    reasoning = {
        "member_alerts_with_confidence": len(scored_alerts),
        "member_alerts_total": len(active_alerts),
        "avg_alert_confidence": round(avg_alert_confidence, 1),
        "root_cause_identified": root_cause_identified,
        "inputs_fully_classified": inputs_fully_classified,
    }
    return round(score, 1), reasoning
