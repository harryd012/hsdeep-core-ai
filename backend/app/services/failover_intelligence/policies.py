"""
Operational SLA threshold evaluation — pure, deterministic, no I/O.

The failover detector needs exactly one answer from a policy: "given this
observed value and this policy's history, is the metric critical, warning,
clearing, or none of those?" Keeping that decision in a pure module (instead of
inline in the detector) means it can be table-tested directly and reused by the
dry-run API, the simulation engine and the recovery verifier without any of them
touching the ORM.

Two rules matter for correctness and are easy to get wrong:

1. **Missing telemetry is not a verdict.** ``observed is None`` produces no
   level at all. Treating a missing sample as 0 would report a perfect path;
   treating it as critical would open a failover episode on silence. Neither is
   defensible, so the detector simply waits for data.

2. **Hysteresis is explicit.** ``clear_threshold`` exists so that a metric
   hovering exactly on the critical boundary does not oscillate between
   breached and clear on every collection cycle. Without it, a link sitting at
   99.9 ms against a 100 ms critical level would flap the FSM indefinitely.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.operational_sla_policy import (
    HIGHER_IS_WORSE,
    LOWER_IS_WORSE,
    SCOPE_ALL_WAN,
    SCOPE_LINK,
    SCOPE_PRECEDENCE,
    SCOPE_SITE,
)

LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"
LEVEL_CLEAR = "clear"

#: Levels that mean "currently breached".
BREACH_LEVELS: frozenset[str] = frozenset({LEVEL_WARNING, LEVEL_CRITICAL})


@dataclass(frozen=True)
class ThresholdHit:
    """One policy's verdict for one observed value."""

    policy_id: str | None
    policy_name: str | None
    metric: str
    level: str
    observed: float
    threshold: float | None
    scope: str | None
    scope_id: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_name": self.policy_name,
            "metric": self.metric,
            "level": self.level,
            "observed": self.observed,
            "threshold": self.threshold,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "reason": self.reason,
        }


def to_number(value: Any) -> float | None:
    """Coerce telemetry to a float, or None when it is genuinely absent.

    Booleans are rejected on purpose (``True`` is not a measurement), as are
    NaN and infinities, which some vendor APIs emit for "no data".
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def is_lower_worse(metric: str) -> bool:
    if metric in LOWER_IS_WORSE:
        return True
    if metric in HIGHER_IS_WORSE:
        return False
    # Unknown metric: assume "bigger is worse", the common case for WAN
    # impairment metrics. Callers only ever see configured metrics in practice.
    return False


def _crosses(observed: float, threshold: float, *, lower_is_worse: bool) -> bool:
    return observed <= threshold if lower_is_worse else observed >= threshold


def threshold_for(policy: Any, level: str) -> float | None:
    """Read one threshold level off a policy-like object."""
    field = {
        LEVEL_WARNING: "warning_threshold",
        LEVEL_CRITICAL: "critical_threshold",
        LEVEL_CLEAR: "clear_threshold",
    }.get(level)
    if field is None:
        return None
    return to_number(getattr(policy, field, None))


def classify(policy: Any, observed: float | None) -> str | None:
    """Level implied by the raw thresholds (no hysteresis)."""
    value = to_number(observed)
    if value is None:
        return None
    lower_is_worse = is_lower_worse(getattr(policy, "metric", ""))
    critical = threshold_for(policy, LEVEL_CRITICAL)
    warning = threshold_for(policy, LEVEL_WARNING)
    if critical is not None and _crosses(value, critical, lower_is_worse=lower_is_worse):
        return LEVEL_CRITICAL
    if warning is not None and _crosses(value, warning, lower_is_worse=lower_is_worse):
        return LEVEL_WARNING
    return None


def evaluate_policy(
    policy: Any, observed: float | None, *, previous_level: str | None = None,
) -> ThresholdHit | None:
    """Full verdict for one policy, applying the clear level when breached.

    Returns ``None`` when the metric is unmeasured or comfortably inside the
    thresholds and was not previously breached. Returns a ``clear`` hit when a
    previously breached metric has recovered past ``clear_threshold``, which is
    what lets the detector reset its consecutive-breach counter.
    """
    value = to_number(observed)
    if value is None:
        return None
    metric = getattr(policy, "metric", "")
    lower_is_worse = is_lower_worse(metric)
    level = classify(policy, value)
    policy_id = str(getattr(policy, "id", "") or "") or None
    scope_id = str(getattr(policy, "scope_id", "") or "") or None
    scope = getattr(policy, "scope", None)
    if level is not None:
        threshold = threshold_for(policy, level)
        return ThresholdHit(
            policy_id=policy_id,
            policy_name=getattr(policy, "name", None),
            metric=metric,
            level=level,
            observed=value,
            threshold=threshold,
            scope=scope,
            scope_id=scope_id,
            reason=f"{metric}={value} crossed the {level} level {threshold}",
        )
    if previous_level in BREACH_LEVELS:
        clear_level = threshold_for(policy, LEVEL_CLEAR)
        if clear_level is None:
            # No explicit clear level: fall back to the warning level, which is
            # a strictly weaker requirement than the critical one that opened
            # the episode.
            clear_level = threshold_for(policy, LEVEL_WARNING)
        if clear_level is not None and not _crosses(
            value, clear_level, lower_is_worse=lower_is_worse
        ):
            return ThresholdHit(
                policy_id=policy_id,
                policy_name=getattr(policy, "name", None),
                metric=metric,
                level=LEVEL_CLEAR,
                observed=value,
                threshold=clear_level,
                scope=scope,
                scope_id=scope_id,
                reason=f"{metric}={value} is clear of the {clear_level} level",
            )
    return None


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------
def scope_rank(policy: Any, *, link_id: Any = None, site_id: Any = None) -> int | None:
    """Precedence of a policy for one link: 0 = most specific, or None.

    ``None`` means the policy does not apply to this link at all (a different
    site's policy, or a link-scoped policy for another link). Comparing
    stringified ids keeps this usable with ORM rows, plain dataclasses and raw
    UUIDs alike.
    """
    scope = getattr(policy, "scope", SCOPE_ALL_WAN)
    scope_id = getattr(policy, "scope_id", None)
    if scope == SCOPE_ALL_WAN:
        return SCOPE_PRECEDENCE.index(SCOPE_ALL_WAN)
    if scope == SCOPE_LINK:
        if link_id is None or scope_id is None:
            return None
        return 0 if str(scope_id) == str(link_id) else None
    if scope == SCOPE_SITE:
        if site_id is None or scope_id is None:
            return None
        if str(scope_id) == str(site_id):
            return SCOPE_PRECEDENCE.index(SCOPE_SITE)
        return None
    return None


def resolve_policies(
    policies: Any, *, link_id: Any = None, site_id: Any = None,
) -> dict[str, Any]:
    """Most specific active policy per metric for one candidate link.

    A link-scoped policy beats a site-scoped policy beats the tenant-wide
    ``all_wan`` default, independently per metric — so a tenant can run a
    conservative global packet-loss policy while a specific MPLS circuit gets a
    tighter latency one.
    """
    best: dict[str, tuple[int, Any]] = {}
    for policy in policies or ():
        if getattr(policy, "is_active", True) is False:
            continue
        rank = scope_rank(policy, link_id=link_id, site_id=site_id)
        if rank is None:
            continue
        metric = getattr(policy, "metric", None)
        if not metric:
            continue
        current = best.get(metric)
        if current is None or rank < current[0]:
            best[metric] = (rank, policy)
    return {metric: policy for metric, (_, policy) in best.items()}


def metric_value(source: Any, metric: str) -> float | None:
    """Read a metric off a link row or a path payload.

    Canonical columns first, then the free-form ``attributes`` map, because
    collectors disagree about whether jitter arrives as a column or as an extra
    attribute. Returns None when genuinely absent - never a fabricated 0.
    """
    if source is None:
        return None
    if isinstance(source, dict):
        direct = source.get(metric)
        if direct is not None:
            return to_number(direct)
        attributes = source.get("attributes") or {}
        return to_number(attributes.get(metric)) if isinstance(attributes, dict) else None
    direct = getattr(source, metric, None)
    if direct is not None:
        return to_number(direct)
    attributes = getattr(source, "attributes", None) or {}
    return to_number(attributes.get(metric)) if isinstance(attributes, dict) else None
