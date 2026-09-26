"""
SOP Matcher.

Scores a candidate `SOP` against a real `IncidentContext` (see below) and
returns an explainable confidence score - same "honest heuristic, not ML"
philosophy as `RiskEngine`/`DecisionEngine`: every rule is a plain
comparison over real, already-resolved fields, never a random or
simulated score.

Deliberately pure/stateless (no DB access, no repo dependencies) - same
split as RiskEngine (pure calculator) vs. BusinessImpactEngine (resolves
real rows via repos, then hands scalars to the pure calculator).
`SOPRecommendationEngine` is what resolves the real Device/Site/
BusinessService/MonitoringSource/success-rate facts and builds the
`IncidentContext` this class scores against.

Matching rule set:
  - `SOP.category` is NOT NULL and is the mandatory match: an incident's
    root-cause category must equal it for the SOP to be a candidate at
    all (a "Configuration Drift Investigation" SOP is never relevant to
    an "availability" incident, regardless of how well the optional
    criteria line up). Enforced as a hard exclusion, not a score
    reduction.
  - Every other criterion (vendor/platform/device_type/site/
    business_service/region/min_priority) is OPTIONAL on the SOP row -
    per sop.py's docstring, an unset criterion means "applies regardless
    of that dimension" (a broad SOP), so it contributes partial credit
    rather than zero. A SET criterion that actively CONTRADICTS the
    incident's real field is a hard exclusion (a Meraki-specific SOP must
    never be recommended for a FortiGate incident) - this is what makes a
    narrow SOP actually narrow, consistent with the module's docstring
    ("scores... rather than requiring an exact match" describes the
    overall confidence number, not license to recommend a SOP whose
    explicit vendor/platform criteria are simply wrong).
  - A SET criterion the incident context cannot confirm (the real field
    is unknown/None) neither excludes nor fully credits - it's genuine
    uncertainty, scored at a quarter credit.
  - Historical execution success rate (from RunbookExecutionRepository,
    real terminal RunbookExecution rows only) applies a final ±15%
    adjustment on top of the criteria-based score, and ONLY when real
    execution history exists - a SOP with zero executions is judged on
    criteria alone, never penalized/boosted for lack of data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.sop import SOP

# Criteria weights, summing to 100 with the mandatory category match.
_WEIGHT_CATEGORY = 40.0
_WEIGHT_VENDOR = 10.0
_WEIGHT_PLATFORM = 10.0
_WEIGHT_DEVICE_TYPE = 10.0
_WEIGHT_SITE = 10.0
_WEIGHT_BUSINESS_SERVICE = 10.0
_WEIGHT_REGION = 5.0
_WEIGHT_MIN_PRIORITY = 5.0

# Credit multipliers applied to a criterion's weight.
_CREDIT_UNSET_ON_SOP = 0.5      # SOP left this dimension open (broad match)
_CREDIT_UNKNOWN_ON_CONTEXT = 0.25  # SOP is specific but the incident can't confirm it

_PRIORITY_RANK = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}

# Historical success-rate adjustment: applied multiplicatively as a small
# swing around the criteria score, only when real execution history exists.
_SUCCESS_RATE_WEIGHT = 0.15


@dataclass
class IncidentContext:
    """Real, already-resolved facts about the incident being matched -
    built by SOPRecommendationEngine from live Device/Site/BusinessService/
    MonitoringSource/Alert rows. Every field is `| None` because not every
    incident resolves every dimension (e.g. a device-less vendor-native
    alert has no device_type)."""

    category: str | None
    vendor: str | None = None
    platform: str | None = None
    device_type: str | None = None
    site_id: object | None = None  # uuid.UUID | None, kept loose to avoid a hard import cycle
    business_service_id: object | None = None
    region: str | None = None
    priority: str | None = None


@dataclass
class SOPMatchResult:
    sop: SOP
    confidence_score: float  # 0-100
    matched_criteria: dict
    reasoning: dict = field(default_factory=dict)


class SOPMatcher:
    def score(
        self, sop: SOP, context: IncidentContext, *, success_rate: float | None = None, success_rate_sample_size: int = 0
    ) -> SOPMatchResult | None:
        """Returns None when `sop` is hard-excluded (category mismatch or a
        contradicting narrow criterion) - callers filter out None results
        rather than treating an excluded SOP as a zero-confidence match."""
        if context.category is not None and sop.category != context.category:
            return None

        matched_criteria: dict[str, bool | None] = {"category": True}
        earned = _WEIGHT_CATEGORY
        criterion_reasoning: dict[str, str] = {"category": f"matched ({sop.category})"}

        for name, weight, sop_value, context_value in (
            ("vendor", _WEIGHT_VENDOR, sop.vendor, context.vendor),
            ("platform", _WEIGHT_PLATFORM, sop.platform, context.platform),
            ("device_type", _WEIGHT_DEVICE_TYPE, sop.device_type, context.device_type),
            ("site_id", _WEIGHT_SITE, sop.site_id, context.site_id),
            ("business_service_id", _WEIGHT_BUSINESS_SERVICE, sop.business_service_id, context.business_service_id),
            ("region", _WEIGHT_REGION, sop.region, context.region),
        ):
            credit, matched, reason = self._score_criterion(name, weight, sop_value, context_value)
            if credit is None:
                return None  # hard-excluded by a contradicting narrow criterion
            earned += credit
            matched_criteria[name] = matched
            criterion_reasoning[name] = reason

        priority_credit, priority_matched, priority_reason = self._score_min_priority(sop.min_priority, context.priority)
        if priority_credit is None:
            return None
        earned += priority_credit
        matched_criteria["min_priority"] = priority_matched
        criterion_reasoning["min_priority"] = priority_reason

        criteria_score = round(earned, 1)

        final_score = criteria_score
        success_rate_reasoning: dict = {"applied": False}
        if success_rate is not None and success_rate_sample_size > 0:
            # Swing of up to +/- (_SUCCESS_RATE_WEIGHT * 100) around the
            # criteria score, centered on a 50% success rate (neutral).
            adjustment = (success_rate - 0.5) * 2 * (_SUCCESS_RATE_WEIGHT * 100)
            final_score = max(0.0, min(100.0, criteria_score + adjustment))
            success_rate_reasoning = {
                "applied": True,
                "success_rate": success_rate,
                "sample_size": success_rate_sample_size,
                "adjustment": round(adjustment, 1),
            }

        return SOPMatchResult(
            sop=sop,
            confidence_score=round(final_score, 1),
            matched_criteria=matched_criteria,
            reasoning={
                "criteria_score": criteria_score,
                "criteria": criterion_reasoning,
                "historical_success_rate": success_rate_reasoning,
            },
        )

    @staticmethod
    def _score_criterion(
        name: str, weight: float, sop_value, context_value
    ) -> tuple[float | None, bool | None, str]:
        if sop_value is None:
            return weight * _CREDIT_UNSET_ON_SOP, None, "SOP does not constrain this dimension (broad match)"
        if context_value is None:
            return weight * _CREDIT_UNKNOWN_ON_CONTEXT, None, f"incident's {name} is unknown; SOP requires {sop_value!r}"
        if sop_value == context_value:
            return weight, True, f"matched ({sop_value!r})"
        return None, False, f"mismatch: SOP requires {sop_value!r}, incident has {context_value!r}"

    @staticmethod
    def _score_min_priority(sop_min_priority: str | None, incident_priority: str | None) -> tuple[float | None, bool | None, str]:
        if sop_min_priority is None:
            return _WEIGHT_MIN_PRIORITY * _CREDIT_UNSET_ON_SOP, None, "SOP applies regardless of priority"
        if incident_priority is None:
            return (
                _WEIGHT_MIN_PRIORITY * _CREDIT_UNKNOWN_ON_CONTEXT,
                None,
                f"incident has no priority yet; SOP requires at least {sop_min_priority}",
            )
        sop_rank = _PRIORITY_RANK.get(sop_min_priority)
        incident_rank = _PRIORITY_RANK.get(incident_priority)
        if sop_rank is None or incident_rank is None:
            return _WEIGHT_MIN_PRIORITY * _CREDIT_UNKNOWN_ON_CONTEXT, None, "unrecognized priority value"
        # Lower rank = more urgent (P1=0). The incident qualifies when it is
        # at least as urgent as the SOP's minimum.
        if incident_rank <= sop_rank:
            return _WEIGHT_MIN_PRIORITY, True, f"incident priority {incident_priority} meets minimum {sop_min_priority}"
        return None, False, f"incident priority {incident_priority} is below SOP's minimum {sop_min_priority}"
