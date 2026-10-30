"""Palo Alto SOP / runbook intelligence.

A searchable, versioned registry of Palo Alto troubleshooting runbooks derived
from the NOC SOP and troubleshooting cheat sheet. Each runbook defines
applicable symptoms, prerequisites, diagnostic steps with expected vs abnormal
outcomes, safe remediation, approval requirements, rollback, post-fix
verification, and escalation criteria.

This extends the existing SOP engine's philosophy (explainable, deterministic
matching) rather than building a competing subsystem. `match()` scores a
runbook against observed symptoms so the workspace can recommend the right
procedure.
"""
from __future__ import annotations

from app.schemas.paloalto import DiagnosticRisk, Runbook, RunbookMatch, RunbookStep


def _rb(**kw) -> Runbook:
    return Runbook(**kw)


RUNBOOKS: list[Runbook] = [
    _rb(
        id="internet_connectivity",
        title="Internet connectivity failure",
        topic="internet",
        version="1.0.0",
        applicable_symptoms=["no internet", "site unreachable", "browsing fails", "upstream down"],
        prerequisites=["management access to the firewall", "known affected source host"],
        steps=[
            RunbookStep(id="confirm_scope", instruction="Confirm which sources/subnets are affected and whether it is total or partial.", expected_outcome="Affected scope identified (single host, subnet, or all).", abnormal_outcome="Scope unclear; gather more data.", evidence_requirement="traffic logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_default_route", instruction="Verify the default route and next-hop reachability.", expected_outcome="A default route exists and next hop responds to ping.", abnormal_outcome="No default route or next hop unreachable.", evidence_requirement="routing table", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_upstream", instruction="Test upstream/ISP reachability and DNS resolution.", expected_outcome="Upstream reachable, DNS resolves.", abnormal_outcome="Upstream or DNS failure points to ISP/core issue.", evidence_requirement="system logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_security_rule", instruction="Confirm no security policy denies the flow.", expected_outcome="Flow permitted or denied by a specific rule.", abnormal_outcome="Deny indicates policy issue.", evidence_requirement="traffic logs", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["If ISP/core outage: engage upstream provider.", "If policy deny: adjust the matching rule (requires approval)."],
        approval_required=True,
        rollback_instructions="Revert any policy change to the prior committed config.",
        post_fix_verification=["Confirm internet access restored for affected sources.", "Verify no new denies in traffic logs."],
        escalation_criteria=["Upstream outage confirmed beyond firewall control.", "No route and no remediation path."],
    ),
    _rb(
        id="application_connectivity",
        title="Application connectivity failure",
        topic="application",
        version="1.0.0",
        applicable_symptoms=["app unreachable", "app timeout", "application not working"],
        prerequisites=["application name or URL", "affected user or host"],
        steps=[
            RunbookStep(id="identify_flow", instruction="Identify source/dest/ports/App-ID for the application flow.", expected_outcome="Flow and App-ID identified.", abnormal_outcome="App-ID unknown; inspect traffic logs.", evidence_requirement="traffic logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_traffic_log", instruction="Search traffic logs for allow/deny and rule name.", expected_outcome="Session established with an allow rule.", abnormal_outcome="Deny or no session indicates a block.", evidence_requirement="traffic logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_appid", instruction="Confirm detected App-ID matches the intended application.", expected_outcome="App-ID matches intent.", abnormal_outcome="App-ID mismatch may mis-route or mis-control the flow.", evidence_requirement="traffic logs", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["If App-ID mismatch: review application visibility/control rules.", "If policy deny: adjust the matching rule (requires approval)."],
        approval_required=True,
        rollback_instructions="Revert policy or App-ID changes to prior committed config.",
        post_fix_verification=["Confirm application works for the affected user.", "Verify allow log with correct App-ID."],
        escalation_criteria=["Application fails despite allow logs and correct App-ID (server-side issue)."],
    ),
]



RUNBOOKS += [
    _rb(
        id="security_policy_block",
        title="Unexpected security-policy block",
        topic="security_policy",
        version="1.0.0",
        applicable_symptoms=["traffic denied", "policy block", "connection refused by firewall"],
        prerequisites=["source/destination of the flow"],
        steps=[
            RunbookStep(id="find_deny", instruction="Locate the deny entry in traffic logs and capture the rule name.", expected_outcome="Specific deny rule identified.", abnormal_outcome="No deny found; issue may be downstream.", evidence_requirement="traffic logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="inspect_rule", instruction="Inspect the matching rule, its position, and whether it shadows a broader rule.", expected_outcome="Rule and order understood.", abnormal_outcome="Shadowed or overly-broad rule.", evidence_requirement="security policy config", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["Narrow or reorder the rule so the intended allow matches (requires approval)."],
        approval_required=True,
        rollback_instructions="Restore prior rule order/values from committed config.",
        post_fix_verification=["Confirm allow log for the flow after change."],
        escalation_criteria=["Rule change would impact production traffic broadly."],
    ),
    _rb(
        id="nat_routing_issue",
        title="NAT and routing issue",
        topic="nat_routing",
        version="1.0.0",
        applicable_symptoms=["nat failure", "wrong translation", "asymmetric routing", "no return path"],
        prerequisites=["known source/dest and expected translation"],
        steps=[
            RunbookStep(id="verify_nat", instruction="Verify source/destination NAT rule and the actual translation applied.", expected_outcome="Translation matches intent.", abnormal_outcome="Missing or wrong NAT rule.", evidence_requirement="NAT config + traffic logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="verify_route", instruction="Confirm egress route and next hop; check for asymmetric return path.", expected_outcome="Symmetric routing with correct next hop.", abnormal_outcome="Missing route or asymmetric path.", evidence_requirement="routing table", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["Correct the NAT rule or route (requires approval)."],
        approval_required=True,
        rollback_instructions="Revert NAT/route change to prior committed config.",
        post_fix_verification=["Confirm correct translation and bidirectional flow."],
        escalation_criteria=["Asymmetric routing requires upstream coordination."],
    ),
    _rb(
        id="threat_url_filtering",
        title="Threat and URL filtering block",
        topic="threat_url",
        version="1.0.0",
        applicable_symptoms=["threat block", "url blocked", "signature match", "malware alert"],
        prerequisites=["affected URL or threat signature"],
        steps=[
            RunbookStep(id="inspect_threat", instruction="Review threat logs for signature, severity, and action.", expected_outcome="Signature and action identified.", abnormal_outcome="No threat entry; may be URL filtering instead.", evidence_requirement="threat logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="inspect_url", instruction="Review URL logs for category and override action.", expected_outcome="Category and action identified.", abnormal_outcome="Category unknown or override missing.", evidence_requirement="url logs", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["Tune the threat profile or add a URL override for a false positive (requires approval)."],
        approval_required=True,
        rollback_instructions="Remove the override/suppression to restore default protection.",
        post_fix_verification=["Confirm the flow is permitted and no unintended exposure."],
        escalation_criteria=["True positive indicating a compromise; escalate to SOC."],
    ),
    _rb(
        id="tls_decryption_failure",
        title="TLS decryption failure",
        topic="tls_decryption",
        version="1.0.0",
        applicable_symptoms=["decrypt error", "certificate error", "ssl failure", "https broken"],
        prerequisites=["affected application or URL"],
        steps=[
            RunbookStep(id="inspect_decrypt", instruction="Review decryption logs for errors and the decrypt policy applied.", expected_outcome="Decrypt status and error identified.", abnormal_outcome="Decrypt error or missing policy.", evidence_requirement="decrypt logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_cert", instruction="Inspect certificate chain and trust for the destination.", expected_outcome="Valid, trusted certificate.", abnormal_outcome="Untrusted or expired certificate.", evidence_requirement="decrypt logs", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["Exclude the application from decryption or fix the trust chain (requires approval)."],
        approval_required=True,
        rollback_instructions="Restore prior decrypt policy from committed config.",
        post_fix_verification=["Confirm HTTPS works and decrypt status is as intended."],
        escalation_criteria=["Decryption breaks a critical application with no safe exclusion."],
    ),
]



RUNBOOKS += [
    _rb(
        id="globalprotect_vpn",
        title="GlobalProtect VPN issue",
        topic="globalprotect",
        version="1.0.0",
        applicable_symptoms=["vpn fails", "gp cannot connect", "tunnel down"],
        prerequisites=["affected user", "GP gateway name"],
        steps=[
            RunbookStep(id="check_gp_log", instruction="Review GP logs for auth and tunnel events.", expected_outcome="Auth success and tunnel up.", abnormal_outcome="Auth failure or tunnel error.", evidence_requirement="globalprotect logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_gp_route", instruction="Verify GP route and split-tunnel config.", expected_outcome="Routes pushed correctly.", abnormal_outcome="Missing or wrong routes.", evidence_requirement="GP config", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["Fix GP auth, gateway, or split-tunnel config (requires approval)."],
        approval_required=True,
        rollback_instructions="Restore prior GP config from committed config.",
        post_fix_verification=["Confirm user connects and reaches internal resources."],
        escalation_criteria=["Gateway-wide outage affecting all remote users."],
    ),
    _rb(
        id="resource_ha_pressure",
        title="High CPU/memory and packet loss",
        topic="resource_ha",
        version="1.0.0",
        applicable_symptoms=["high cpu", "high memory", "packet loss"],
        prerequisites=["firewall management access"],
        steps=[
            RunbookStep(id="check_resources", instruction="Inspect CPU, memory, session utilization.", expected_outcome="Utilization within bounds.", abnormal_outcome="Sustained high utilization.", evidence_requirement="system logs", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_ha", instruction="Verify HA state and failover readiness.", expected_outcome="HA healthy or single-node known.", abnormal_outcome="HA degraded.", evidence_requirement="HA state", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["Offload offending traffic; investigate sessions (approval for changes)."],
        approval_required=True,
        rollback_instructions="Revert any resource-mitigation change.",
        post_fix_verification=["Confirm utilization returns to normal."],
        escalation_criteria=["Sustained exhaustion risking outage."],
    ),
]


RUNBOOKS += [
    _rb(
        id="interface_ha_outage",
        title="Interface, HA and site-wide outage",
        topic="interface_ha",
        version="1.0.0",
        applicable_symptoms=["interface down", "ha failover", "site outage"],
        prerequisites=["affected interface or site"],
        steps=[
            RunbookStep(id="check_interface", instruction="Inspect admin/oper status, errors, drops.", expected_outcome="Up with no excessive errors.", abnormal_outcome="Down or erroring.", evidence_requirement="interface counters", risk=DiagnosticRisk.READ_ONLY),
            RunbookStep(id="check_ha_state", instruction="Verify HA state and recent failover events.", expected_outcome="HA as expected.", abnormal_outcome="Unexpected failover.", evidence_requirement="HA state", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["Coordinate link fix or HA repair (requires approval)."],
        approval_required=True,
        rollback_instructions="Restore prior interface/HA config where changed.",
        post_fix_verification=["Confirm interface up and reachability restored."],
        escalation_criteria=["Site-wide outage or HA split-brain."],
    ),
    _rb(
        id="packet_capture_escalation",
        title="Packet capture and escalation",
        topic="packet_capture",
        version="1.0.0",
        applicable_symptoms=["need capture", "deep triage", "escalate"],
        prerequisites=["explicit capture authorization", "narrow filter"],
        steps=[
            RunbookStep(id="authorize_capture", instruction="Record authorization; define narrow filter and duration.", expected_outcome="Authorized and bounded.", abnormal_outcome="No authorization; stop.", evidence_requirement="approval record", risk=DiagnosticRisk.REQUIRES_APPROVAL),
            RunbookStep(id="run_capture", instruction="Run bounded targeted capture on the flow.", expected_outcome="Completes within bounds.", abnormal_outcome="Exceeds bounds; abort.", evidence_requirement="capture file", risk=DiagnosticRisk.REQUIRES_APPROVAL),
            RunbookStep(id="escalate", instruction="Package evidence and escalate if unresolved.", expected_outcome="Escalation sent.", abnormal_outcome="Insufficient evidence.", evidence_requirement="evidence bundle", risk=DiagnosticRisk.READ_ONLY),
        ],
        safe_remediation=["Escalate with evidence; no config change required."],
        approval_required=True,
        rollback_instructions="Stop capture and delete temp files after use.",
        post_fix_verification=["Capture stopped; evidence retained per policy."],
        escalation_criteria=["Unresolved after evidence collection."],
    ),
]


class RunbookRegistry:
    """Searchable, versioned runbook catalogue with symptom matching."""

    def __init__(self, runbooks: list[Runbook] | None = None):
        self._runbooks = runbooks if runbooks is not None else list(RUNBOOKS)

    def all(self) -> list[Runbook]:
        return list(self._runbooks)

    def get(self, runbook_id: str) -> Runbook | None:
        for rb in self._runbooks:
            if rb.id == runbook_id:
                return rb
        return None

    def search(self, query: str) -> list[Runbook]:
        q = query.lower().strip()
        if not q:
            return []
        out = []
        for rb in self._runbooks:
            hay = " ".join([rb.title, rb.topic, rb.id] + rb.applicable_symptoms).lower()
            if q in hay:
                out.append(rb)
        return out

    def match(self, symptoms: list[str]) -> list[RunbookMatch]:
        """Score runbooks against observed symptoms (explainable overlap)."""
        sym = [s.lower().strip() for s in symptoms if s and s.strip()]
        if not sym:
            return []
        matches: list[RunbookMatch] = []
        for rb in self._runbooks:
            rb_sym = [s.lower() for s in rb.applicable_symptoms]
            hit = [s for s in sym if any(s in r or r in s for r in rb_sym)]
            if hit:
                score = round(100.0 * len(hit) / len(sym), 1)
                matches.append(RunbookMatch(runbook=rb, confidence_score=score, matched_symptoms=hit))
        matches.sort(key=lambda m: m.confidence_score, reverse=True)
        return matches
