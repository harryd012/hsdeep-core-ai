"""Deterministic SASE policy intelligence (HSDEEP network intelligence).

Pure computation layer: no I/O, no DB, no gateways. It evaluates one network
flow through the fixed policy pipeline — Identity → Device Posture → ZTNA →
SWG → CASB → FWaaS — and produces the final security decision, one verdict
per stage, and a human-readable reason.

All stage rules are deterministic data (see module constants below), so
identical flows always yield identical decisions. Stages that no longer
apply after a terminal deny/block are reported as NOT_EVALUATED rather than
being silently skipped, which keeps the pipeline shape stable for tests,
audit, and the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STAGE_IDENTITY = "Identity"
STAGE_DEVICE_POSTURE = "Device Posture"
STAGE_ZTNA = "ZTNA"
STAGE_SWG = "SWG"
STAGE_CASB = "CASB"
STAGE_FWAAS = "FWaaS"
STAGE_FINAL = "Final Decision"

PIPELINE_STAGES = (
    STAGE_IDENTITY,
    STAGE_DEVICE_POSTURE,
    STAGE_ZTNA,
    STAGE_SWG,
    STAGE_CASB,
    STAGE_FWAAS,
)

DECISION_ALLOW = "ALLOW"
DECISION_DENY = "DENY"
DECISION_BLOCK = "BLOCK"
DECISION_NOT_EVALUATED = "NOT_EVALUATED"

MALICIOUS_URL_CATEGORIES = frozenset({
    "malware", "phishing", "command-and-control", "c2", "botnet",
    "ransomware", "spyware", "cryptomining", "malicious",
})

HIGH_RISK_LEVELS = frozenset({"high", "critical"})

# Applications denied by default policy regardless of destination.
DENIED_APPLICATIONS = frozenset({
    "tor", "torrent", "p2p", "cryptominer",
})

# URL categories treated as sanctioned/unsanctioned-SaaS gates for CASB.
CASB_BLOCKED_CATEGORIES = frozenset({
    "unsanctioned-saas", "file-sharing", "personal-storage",
})
CASB_REVIEW_CATEGORIES = frozenset({"saas", "cloud-storage", "webmail"})


@dataclass(frozen=True)
class SaseFlow:
    """One normalised flow in for policy evaluation."""

    user_identity: str = ""
    device_identity: str = ""
    device_compliance: bool = True
    destination: str = ""
    url_category: str = ""
    application: str = ""
    risk_level: str = "low"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SaseFlow":
        return cls(
            user_identity=str(payload.get("user_identity", "") or ""),
            device_identity=str(payload.get("device_identity", "") or ""),
            device_compliance=bool(payload.get("device_compliance", True)),
            destination=str(payload.get("destination", "") or ""),
            url_category=str(payload.get("url_category", "") or "").lower(),
            application=str(payload.get("application", "") or "").lower(),
            risk_level=str(payload.get("risk_level", "") or "low").lower(),
        )


@dataclass
class StageVerdict:
    """One stage's deterministic verdict."""

    stage: str
    decision: str
    reason: str = ""


def _not_evaluated(stage: str, reason: str = "Superseded by earlier decision") -> StageVerdict:
    return StageVerdict(stage=stage, decision=DECISION_NOT_EVALUATED, reason=reason)


def evaluate_sase(
    flow: SaseFlow | dict[str, Any],
    *,
    require_compliant_device: bool = True,
    block_malicious_urls: bool = True,
    enforce_application_policy: bool = True,
) -> dict[str, Any]:
    """Evaluate one flow through the fixed policy pipeline.

    Returns {"decision", "pipeline": [...], "reason"}. Terminal denies short-
    circuit the remaining stages (reported as NOT_EVALUATED) so a single call
    always yields the full stable pipeline shape for audit and UI.
    """
    f = flow if isinstance(flow, SaseFlow) else SaseFlow.from_dict(flow or {})
    verdicts: list[StageVerdict] = []
    blocked = False
    blocked_reason = ""

    # -- Identity ---------------------------------------------------------
    if not f.user_identity:
        verdicts.append(StageVerdict(STAGE_IDENTITY, DECISION_DENY, "Unknown user identity"))
        blocked, blocked_reason = True, "Unknown user identity"
    else:
        verdicts.append(StageVerdict(STAGE_IDENTITY, DECISION_ALLOW, f"Identity {f.user_identity} recognised"))

    # -- Device Posture ----------------------------------------------------
    if not blocked:
        if not f.device_compliance and require_compliant_device:
            verdicts.append(StageVerdict(STAGE_DEVICE_POSTURE, DECISION_DENY, "Device is not compliant"))
            blocked, blocked_reason = True, "Device is not compliant"
        else:
            verdicts.append(StageVerdict(
                STAGE_DEVICE_POSTURE, DECISION_ALLOW,
                "Device posture compliant" if f.device_compliance else "Posture check waived by policy",
            ))

    # -- ZTNA --------------------------------------------------------------
    if blocked:
        verdicts.append(_not_evaluated(STAGE_ZTNA))
    elif not f.device_compliance and require_compliant_device:
        verdicts.append(StageVerdict(STAGE_ZTNA, DECISION_DENY, "Device is not compliant"))
        blocked, blocked_reason = True, "Device is not compliant"
    elif f.risk_level in HIGH_RISK_LEVELS:
        verdicts.append(StageVerdict(STAGE_ZTNA, DECISION_DENY, f"Session risk is {f.risk_level}"))
        blocked, blocked_reason = True, f"Session risk is {f.risk_level}"
    else:
        verdicts.append(StageVerdict(STAGE_ZTNA, DECISION_ALLOW, "ZTNA session authorised"))

    # -- SWG ----------------------------------------------------------------
    if blocked:
        verdicts.append(_not_evaluated(STAGE_SWG))
    elif block_malicious_urls and f.url_category in MALICIOUS_URL_CATEGORIES:
        verdicts.append(StageVerdict(STAGE_SWG, DECISION_BLOCK, "Malicious destination"))
        blocked, blocked_reason = True, "Malicious destination"
    else:
        verdicts.append(StageVerdict(STAGE_SWG, DECISION_ALLOW, "Destination permitted by web policy"))

    # -- CASB ---------------------------------------------------------------
    if blocked:
        verdicts.append(_not_evaluated(STAGE_CASB))
    elif f.url_category in CASB_BLOCKED_CATEGORIES:
        verdicts.append(StageVerdict(STAGE_CASB, DECISION_BLOCK, f"Unsanctioned SaaS category: {f.url_category}"))
        blocked, blocked_reason = True, f"Unsanctioned SaaS category: {f.url_category}"
    else:
        verdicts.append(StageVerdict(STAGE_CASB, DECISION_ALLOW, "No CASB restriction applies"))

    # -- FWaaS ---------------------------------------------------------------
    if blocked:
        verdicts.append(_not_evaluated(STAGE_FWAAS))
    elif enforce_application_policy and f.application in DENIED_APPLICATIONS:
        verdicts.append(StageVerdict(STAGE_FWAAS, DECISION_BLOCK, f"Application denied by policy: {f.application}"))
        blocked, blocked_reason = True, f"Application denied by policy: {f.application}"
    else:
        verdicts.append(StageVerdict(STAGE_FWAAS, DECISION_ALLOW, "Egress permitted by firewall policy"))

    parts: list[str] = []
    if not f.device_compliance and require_compliant_device:
        parts.append("Non-compliant device")
    if block_malicious_urls and f.url_category in MALICIOUS_URL_CATEGORIES:
        parts.append("malicious destination")
    if blocked and not parts:
        parts.append(blocked_reason)
    final = DECISION_BLOCK if blocked else DECISION_ALLOW
    reason = (
        "Non-compliant device attempting access to malicious destination"
        if (not f.device_compliance and f.url_category in MALICIOUS_URL_CATEGORIES)
        else ("; ".join(parts) if parts else "Flow permitted by policy")
    )
    pipeline = [
        {"stage": v.stage, "decision": v.decision, "reason": v.reason}
        for v in verdicts
    ]
    pipeline.append({"stage": STAGE_FINAL, "decision": final, "reason": reason})
    return {"decision": final, "pipeline": pipeline, "reason": reason}

