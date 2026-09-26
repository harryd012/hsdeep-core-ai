"""Deterministic network-intelligence correlation.

Pure computation layer evaluating ONE flow through BOTH engines
(SD-WAN path decision + SASE security decision), then deriving the
HSDEEP-native artefacts the rest of the stack already understands:

  * audit events   - timestamped flow_id/device/engine/stage/decision/reason
                     rows, ready for the existing AuditService / AuditEvent
                     table (no second audit framework).
  * recommendation - an advisory payload ("use WAN2 as preferred path",
                     "blocked session"). Advisory-only: status is always
                     PENDING_HUMAN_DECISION. The Action Plane remains the
                     only execution surface.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.network_intelligence.sase import evaluate_sase
from app.services.network_intelligence.sdwan import (
    DECISION_FORWARD,
    evaluate_paths,
)

ENGINE_SDWAN = "SD-WAN"
ENGINE_SASE = "SASE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_recommendation(
    *,
    flow_id: str,
    sdwan: dict[str, Any],
    sase: dict[str, Any],
) -> dict[str, Any] | None:
    """Advisory recommendation, or None when nothing needs doing."""
    sdwan_selected = sdwan.get("selected_path")
    degraded = [p for p in sdwan.get("paths", []) if p.get("health") == "Degraded"]
    sase_blocked = sase.get("decision") == "BLOCK"

    if sase_blocked and sdwan_selected:
        return {
            "title": f"Security block with WAN guidance for flow {flow_id}",
            "status": "PENDING_HUMAN_DECISION",
            "actions": [],
            "reason": (
                f"{sase.get('reason', 'Session blocked by policy')}; "
                f"path {sdwan_selected} remains the best available WAN path"
            ),
        }
    if sase_blocked:
        return {
            "title": f"Security block recommended for flow {flow_id}",
            "status": "PENDING_HUMAN_DECISION",
            "actions": [],
            "reason": sase.get("reason", "Session blocked by policy"),
        }
    if sdwan.get("failover") and sdwan_selected:
        degraded_names = ", ".join(p["name"] for p in degraded) or "the current path"
        return {
            "title": f"WAN degradation detected for flow {flow_id}",
            "status": "PENDING_HUMAN_DECISION",
            "actions": [],
            "reason": (
                f"WAN degradation detected. Recommended action: use "
                f"{sdwan_selected} as preferred path. Reason: "
                f"{degraded_names} exceeds configured thresholds."
            ),
        }
    return None


def build_audit_events(
    *,
    flow_id: str,
    device: str,
    sdwan: dict[str, Any],
    sase: dict[str, Any],
) -> list[dict[str, Any]]:
    """Audit rows matching the AuditService vocabulary."""
    events: list[dict[str, Any]] = []

    def _row(engine: str, stage: str, decision: str, reason: str) -> dict[str, Any]:
        return {
            "timestamp": _now_iso(),
            "flow_id": flow_id,
            "device": device,
            "engine": engine,
            "stage": stage,
            "decision": decision,
            "reason": reason,
        }

    if sdwan.get("selected_path"):
        events.append(_row(
            ENGINE_SDWAN, "PATH_EVALUATION",
            f"{sdwan['selected_path']}_SELECTED", sdwan.get("reason", ""),
        ))
    elif sdwan.get("decision"):
        events.append(_row(
            ENGINE_SDWAN, "PATH_EVALUATION",
            sdwan["decision"], sdwan.get("reason", ""),
        ))
    for stage in sase.get("pipeline", []):
        events.append(_row(
            ENGINE_SASE, str(stage.get("stage", "")).upper().replace(" ", "_"),
            str(stage.get("decision", "")), str(stage.get("reason", "")),
        ))
    return events


def correlate_flow(
    flow: dict[str, Any],
    *,
    paths: list[dict[str, Any]] | None = None,
    weights: dict[str, Any] | None = None,
    require_compliant_device: bool = True,
    block_malicious_urls: bool = True,
    enforce_application_policy: bool = True,
    incident_id: str | None = None,
    incident_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one flow through SD-WAN + SASE and derive HSDEEP artefacts."""
    flow = dict(flow or {})
    flow_id = str(flow.get("flow_id") or incident_id or f"FLOW-{uuid.uuid4().hex[:8]}")
    device = str(flow.get("device_identity") or flow.get("device") or "")

    sdwan = evaluate_paths(paths or flow.get("paths") or [], weights)
    sase = evaluate_sase(
        flow,
        require_compliant_device=require_compliant_device,
        block_malicious_urls=block_malicious_urls,
        enforce_application_policy=enforce_application_policy,
    )
    return {
        "flow_id": flow_id,
        "flow": flow,
        "sdwan": sdwan,
        "sase": sase,
        "incident_id": incident_id,
        "incident_context": incident_context or {},
        "recommendation": build_recommendation(flow_id=flow_id, sdwan=sdwan, sase=sase),
        "audit": build_audit_events(flow_id=flow_id, device=device, sdwan=sdwan, sase=sase),
    }
