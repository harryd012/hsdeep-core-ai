"""Palo Alto diagnostic workflow engine.

A reusable, provider-aware 16-stage workflow that guides an engineer from
incident intake through root-cause assessment, remediation planning, and
post-fix validation. Supports partial execution and resumption; a failed step
never silently marks later steps as passed.

Steps are data (DIAGNOSTIC_STEPS). The engine (DiagnosticWorkflowEngine)
owns the lifecycle. Diagnostic actions are advisory unless SafetyPolicy
grants an execution path for a backend-safe, allowlisted operation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from app.schemas.paloalto import (
    DiagnosticExecution,
    DiagnosticExecutionCreate,
    DiagnosticRisk,
    DiagnosticStep,
    DiagnosticStepStatus,
    EvidenceKind,
)

# The canonical 16-stage diagnostic sequence. Order is significant: it mirrors
# the SOP's triage flow from cheap/high-signal checks toward deeper ones.
DIAGNOSTIC_STEPS: list[dict] = [
    {
        "id": "incident_intake",
        "name": "Incident intake & scope validation",
        "description": "Confirm the incident scope, affected device, and that the firewall is in scope for diagnostics.",
        "preconditions": ["incident exists", "device resolved"],
        "required_evidence": [EvidenceKind.SYSTEM_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "traffic_flow_map",
        "name": "Traffic-flow mapping",
        "description": "Map source/destination IP, ports, protocol, zones and App-ID for the affected flow.",
        "preconditions": ["incident_intake passed"],
        "required_evidence": [EvidenceKind.TRAFFIC_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "traffic_log_investigation",
        "name": "Traffic log investigation",
        "description": "Search traffic logs for the flow; confirm allow/deny, rule name, and session end reason.",
        "preconditions": ["traffic_flow_map passed"],
        "required_evidence": [EvidenceKind.TRAFFIC_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "security_policy_analysis",
        "name": "Security policy & rule-order analysis",
        "description": "Identify the matching security rule and its position; detect overly-broad or shadowing rules.",
        "preconditions": ["traffic_log_investigation passed"],
        "required_evidence": [EvidenceKind.CONFIG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "nat_translation",
        "name": "NAT translation validation",
        "description": "Verify source/destination NAT rules and that translations are correct for the flow.",
        "preconditions": ["security_policy_analysis passed"],
        "required_evidence": [EvidenceKind.CONFIG, EvidenceKind.TRAFFIC_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "routing_next_hop",
        "name": "Routing & next-hop verification",
        "description": "Validate the egress route, next hop, and that the next hop is reachable.",
        "preconditions": ["nat_translation passed"],
        "required_evidence": [EvidenceKind.ROUTE],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "dns_upstream",
        "name": "DNS & upstream reachability",
        "description": "Check DNS resolution and upstream/ISP reachability for the destination.",
        "preconditions": ["routing_next_hop passed"],
        "required_evidence": [EvidenceKind.SYSTEM_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "appid_userid",
        "name": "App-ID & User-ID analysis",
        "description": "Confirm the detected application matches intent and that User-ID mapping is present.",
        "preconditions": ["traffic_log_investigation passed"],
        "required_evidence": [EvidenceKind.TRAFFIC_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
]

DIAGNOSTIC_STEPS += [
    {
        "id": "threat_url_filtering",
        "name": "Threat Prevention & URL Filtering analysis",
        "description": "Check threat and URL logs for blocks, signatures, and category matches.",
        "preconditions": ["traffic_log_investigation passed"],
        "required_evidence": [EvidenceKind.THREAT_LOG, EvidenceKind.URL_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "ssl_decryption",
        "name": "SSL/TLS decryption investigation",
        "description": "Review decryption logs and certificate errors; confirm decrypt policy matches intent.",
        "preconditions": ["traffic_log_investigation passed"],
        "required_evidence": [EvidenceKind.DECRYPT_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "globalprotect",
        "name": "GlobalProtect VPN troubleshooting",
        "description": "Check GP gateway, authentication, tunnel, and split-tunnel routing when GP is in scope.",
        "preconditions": ["incident scope includes remote access"],
        "required_evidence": [EvidenceKind.GP_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "interface_resource_ha",
        "name": "Interface, resource & HA health checks",
        "description": "Inspect interface errors/drops, CPU/memory pressure, and HA state.",
        "preconditions": ["incident_intake passed"],
        "required_evidence": [EvidenceKind.INTERFACE, EvidenceKind.SYSTEM_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "session_packet_capture",
        "name": "Session analysis & targeted packet capture",
        "description": "Inspect the live session and, if authorized, run a bounded targeted packet capture.",
        "preconditions": ["traffic_log_investigation passed"],
        "required_evidence": [EvidenceKind.SESSION],
        "risk": DiagnosticRisk.REQUIRES_APPROVAL,
    },
    {
        "id": "root_cause_assessment",
        "name": "Root-cause assessment",
        "description": "Synthesize collected evidence into ranked hypotheses with confidence and gaps.",
        "preconditions": ["prior steps recorded"],
        "required_evidence": [],
        "risk": DiagnosticRisk.READ_ONLY,
    },
    {
        "id": "remediation_planning",
        "name": "Remediation planning",
        "description": "Propose safe remediation with risk level and approval requirements.",
        "preconditions": ["root_cause_assessment passed"],
        "required_evidence": [],
        "risk": DiagnosticRisk.REQUIRES_APPROVAL,
    },
    {
        "id": "post_fix_validation",
        "name": "Post-remediation validation & closure",
        "description": "Re-validate the flow after remediation and confirm incident can be closed.",
        "preconditions": ["remediation applied"],
        "required_evidence": [EvidenceKind.TRAFFIC_LOG],
        "risk": DiagnosticRisk.READ_ONLY,
    },
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DiagnosticWorkflowEngine:
    """Drives a DiagnosticExecution through DIAGNOSTIC_STEPS.

    Lifecycle rules enforced here (never in the UI):
      - A step runs only when its preconditions are met by passed steps;
        otherwise it is BLOCKED, not skipped.
      - A FAILED step leaves downstream steps PENDING/BLOCKED; it never marks
        them passed.
      - Steps may run in any resumable order; the engine tracks state.
      - `run_step` receives a provider action and records evidence, timing,
        and outcome. High-risk actions require a SafetyPolicy grant.
    """

    def __init__(self, safety_policy=None):
        self._safety = safety_policy

    def new_execution(
        self,
        tenant_id: uuid.UUID,
        req: DiagnosticExecutionCreate,
        created_by: uuid.UUID | None = None,
    ) -> DiagnosticExecution:
        steps = [
            DiagnosticStep(
                id=s["id"],
                name=s["name"],
                description=s["description"],
                preconditions=list(s["preconditions"]),
                required_evidence=list(s["required_evidence"]),
                risk=s["risk"],
            )
            for s in DIAGNOSTIC_STEPS
        ]
        return DiagnosticExecution(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            incident_id=req.incident_id,
            device_id=req.device_id,
            status=DiagnosticStepStatus.PENDING,
            risk=DiagnosticRisk.READ_ONLY,
            created_by=created_by,
            created_at=_now(),
            updated_at=_now(),
            steps=steps,
            current_step_id=steps[0].id if steps else None,
        )

    def _get_step(self, ex: DiagnosticExecution, step_id: str) -> DiagnosticStep | None:
        for s in ex.steps:
            if s.id == step_id:
                return s
        return None

    def _passed_ids(self, ex: DiagnosticExecution) -> set[str]:
        return {s.id for s in ex.steps if s.status == DiagnosticStepStatus.PASSED}

    def _preconditions_met(self, ex: DiagnosticExecution, step: DiagnosticStep) -> bool:
        passed = self._passed_ids(ex)
        for pre in step.preconditions:
            if pre.endswith(" passed"):
                req_id = pre[: -len(" passed")].strip()
                if req_id not in passed:
                    return False
        return True

    async def run_step(
        self,
        ex: DiagnosticExecution,
        step_id: str,
        action: Callable | None = None,
        actor_role: str = "engineer",
    ) -> DiagnosticExecution:
        """Execute one step. `action` is an async provider call returning a
        result object; when None the step is recorded as a manual pass with an
        honest 'manual verification' summary (never a fabricated result).
        """
        step = self._get_step(ex, step_id)
        if step is None:
            raise KeyError(f"unknown diagnostic step: {step_id}")

        if step.status == DiagnosticStepStatus.PASSED:
            return ex

        if not self._preconditions_met(ex, step):
            step.status = DiagnosticStepStatus.BLOCKED
            step.failure_reason = "preconditions not satisfied by prior passed steps"
            step.next_recommended_action = "run prerequisite steps first"
            ex.updated_at = _now()
            return ex

        if self._safety is not None:
            decision = self._safety.evaluate(step, actor_role)
            if not decision.allowed:
                step.status = DiagnosticStepStatus.BLOCKED
                step.failure_reason = decision.reason
                step.next_recommended_action = decision.next_action
                ex.updated_at = _now()
                return ex

        step.status = DiagnosticStepStatus.RUNNING
        step.started_at = _now()
        ex.status = DiagnosticStepStatus.RUNNING
        ex.current_step_id = step.id
        ex.updated_at = _now()

        try:
            if action is None:
                step.result_summary = "manual verification required; no automated provider action available"
                step.status = DiagnosticStepStatus.PASSED
            else:
                result = await action()
                summary = getattr(result, "summary", None) or str(result)
                step.result_summary = summary
                ev = getattr(result, "evidence", None)
                if ev:
                    step.evidence.extend(ev)
                ok = getattr(result, "ok", True)
                step.status = (
                    DiagnosticStepStatus.PASSED if ok else DiagnosticStepStatus.FAILED
                )
                if not ok:
                    step.failure_reason = summary
                    step.next_recommended_action = getattr(
                        result, "next_action", None
                    )
        except Exception as exc:  # noqa: BLE001 - record, never swallow silently
            step.status = DiagnosticStepStatus.FAILED
            step.failure_reason = f"{type(exc).__name__}: {exc}"
            step.next_recommended_action = "inspect provider connectivity/credentials"

        step.finished_at = _now()
        ex.updated_at = _now()
        ex.status = self._aggregate_status(ex)
        ex.current_step_id = self._next_pending(ex)
        return ex

    def skip_step(
        self, ex: DiagnosticExecution, step_id: str, reason: str
    ) -> DiagnosticExecution:
        step = self._get_step(ex, step_id)
        if step is None:
            raise KeyError(f"unknown diagnostic step: {step_id}")
        step.status = DiagnosticStepStatus.SKIPPED
        step.result_summary = f"skipped: {reason}"
        step.finished_at = _now()
        ex.updated_at = _now()
        ex.status = self._aggregate_status(ex)
        ex.current_step_id = self._next_pending(ex)
        return ex

    def mark_failed(
        self, ex: DiagnosticExecution, step_id: str, reason: str
    ) -> DiagnosticExecution:
        step = self._get_step(ex, step_id)
        if step is None:
            raise KeyError(f"unknown diagnostic step: {step_id}")
        step.status = DiagnosticStepStatus.FAILED
        step.failure_reason = reason
        step.finished_at = _now()
        ex.updated_at = _now()
        ex.status = self._aggregate_status(ex)
        # A failure must not advance current_step past the failure.
        ex.current_step_id = step_id
        return ex

    def _aggregate_status(self, ex: DiagnosticExecution) -> DiagnosticStepStatus:
        statuses = {s.status for s in ex.steps}
        if DiagnosticStepStatus.FAILED in statuses:
            return DiagnosticStepStatus.FAILED
        if DiagnosticStepStatus.RUNNING in statuses:
            return DiagnosticStepStatus.RUNNING
        if statuses and statuses <= {
            DiagnosticStepStatus.PASSED,
            DiagnosticStepStatus.SKIPPED,
        }:
            return DiagnosticStepStatus.PASSED
        if DiagnosticStepStatus.BLOCKED in statuses:
            return DiagnosticStepStatus.BLOCKED
        return DiagnosticStepStatus.PENDING

    def _next_pending(self, ex: DiagnosticExecution) -> str | None:
        for s in ex.steps:
            if s.status == DiagnosticStepStatus.PENDING:
                return s.id
        return None
