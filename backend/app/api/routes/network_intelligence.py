"""Network-intelligence evaluate route (SD-WAN + SASE, Phase: network intel).

Reuses existing FastAPI conventions: tenant_id from Depends(get_tenant_id),
read-only POST (no DB writes), RBAC `devices.read`-equivalent visibility.
Uses `monitoring.read` — network health visibility, matching the
intelligence/health-aggregate routes which carry no separate guard.

Single endpoint:
  POST /api/network-intelligence/evaluate  - evaluate one flow

The response contains the flow, sdwan, sase, incident_context,
recommendation and audit sections. Recommendations are ADVISORY ONLY
(status PENDING_HUMAN_DECISION); nothing here executes a network change —
execution stays exclusively behind the existing device-actions plane.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends

from app.core.auth import get_tenant_id, require_permission
from app.services.network_intelligence import correlate_flow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/network-intelligence", tags=["network-intelligence"])


# ---------------------------------------------------------------------------
# Schemas (inline — the evaluate payload is a simulation/intelligence shape,
# not a persisted model, so it lives with the route like the incidents.py
# intelligence aggregates do).
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field  # noqa: E402


class WanPathIn(BaseModel):
    name: str
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    packet_loss_pct: float = 0.0
    bandwidth_mbps: float = 0.0
    available: bool = True


class FlowIn(BaseModel):
    flow_id: str | None = None
    user_identity: str = ""
    device_identity: str = ""
    device_compliance: bool = True
    destination: str = ""
    url_category: str = ""
    application: str = ""
    risk_level: str = "low"


class EvaluateIn(BaseModel):
    flow: FlowIn = Field(default_factory=FlowIn)
    paths: list[WanPathIn] = Field(default_factory=list)
    weights: dict[str, float] | None = None
    require_compliant_device: bool = True
    block_malicious_urls: bool = True
    enforce_application_policy: bool = True
    incident_id: str | None = None
    incident_context: dict = Field(default_factory=dict)


@router.post("/evaluate", dependencies=[Depends(require_permission("devices.read"))])
async def evaluate_flow(
    payload: EvaluateIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Evaluate one flow through SD-WAN + SASE intelligence.

    Stateless and read-only: no device is touched, no incident is created,
    no action is executed. Returns the flow, sdwan, sase, incident_context,
    recommendation and audit sections.
    """
    result = correlate_flow(
        {**payload.flow.model_dump(), "flow_id": payload.flow.flow_id},
        paths=[p.model_dump() for p in payload.paths],
        weights=payload.weights,
        require_compliant_device=payload.require_compliant_device,
        block_malicious_urls=payload.block_malicious_urls,
        enforce_application_policy=payload.enforce_application_policy,
        incident_id=payload.incident_id,
        incident_context=payload.incident_context,
    )
    result["tenant_id"] = str(tenant_id)
    logger.info(
        "network-intelligence evaluate tenant=%s flow=%s sdwan=%s sase=%s",
        tenant_id, result["flow_id"],
        result["sdwan"].get("decision"), result["sase"].get("decision"),
    )
    return result


@router.get("/scenarios")
async def list_scenarios(
    tenant_id: uuid.UUID = Depends(get_tenant_id),  # noqa: ARG001
) -> dict:
    """Deterministic demo/simulation scenarios for the dashboard panel."""
    return {"scenarios": list(SCENARIOS.keys())}


@router.get("/scenarios/{name}")
async def get_scenario(
    name: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),  # noqa: ARG001
) -> dict:
    """One deterministic scenario payload (flow + paths); evaluate via POST."""
    from fastapi import HTTPException

    scenario = SCENARIOS.get(name)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario {name!r}")
    return {"name": name, **scenario()}


def _scenario_healthy() -> dict:
    return {
        "flow": {
            "flow_id": "DEMO-HEALTHY",
            "user_identity": "employee@company.com",
            "device_identity": "Windows-Laptop-01",
            "device_compliance": True,
            "destination": "intranet.company.com",
            "url_category": "business",
            "application": "HTTPS",
            "risk_level": "low",
        },
        "paths": [
            {"name": "WAN1", "latency_ms": 45, "jitter_ms": 8,
             "packet_loss_pct": 0.4, "bandwidth_mbps": 200, "available": True},
            {"name": "WAN2", "latency_ms": 35, "jitter_ms": 7,
             "packet_loss_pct": 0.5, "bandwidth_mbps": 500, "available": True},
        ],
    }


def _scenario_wan_degraded() -> dict:
    scenario = _scenario_healthy()
    scenario["flow"] = {**scenario["flow"], "flow_id": "DEMO-WAN-DEGRADED"}
    scenario["paths"] = [
        {"name": "WAN1", "latency_ms": 180, "jitter_ms": 42,
         "packet_loss_pct": 8, "bandwidth_mbps": 200, "available": True},
        {"name": "WAN2", "latency_ms": 35, "jitter_ms": 7,
         "packet_loss_pct": 0.5, "bandwidth_mbps": 500, "available": True},
    ]
    return scenario


def _scenario_security_violation() -> dict:
    scenario = _scenario_healthy()
    scenario["flow"] = {
        **scenario["flow"],
        "flow_id": "DEMO-SECURITY-VIOLATION",
        "device_compliance": False,
        "destination": "malicious-site.example",
        "url_category": "malware",
        "risk_level": "high",
    }
    return scenario


def _scenario_combined() -> dict:
    scenario = _scenario_wan_degraded()
    scenario["flow"] = {
        **scenario["flow"],
        "flow_id": "DEMO-COMBINED",
        "device_compliance": False,
        "destination": "malicious-site.example",
        "url_category": "malware",
        "risk_level": "high",
    }
    return scenario


SCENARIOS = {
    "healthy": _scenario_healthy,
    "wan-degraded": _scenario_wan_degraded,
    "security-violation": _scenario_security_violation,
    "combined": _scenario_combined,
}

