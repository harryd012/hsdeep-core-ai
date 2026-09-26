"""
Deterministic dependency analysis + troubleshooting rules.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.services.dependency_intelligence.graph import DependencyGraph
from app.services.dependency_intelligence.models import (
    Confidence,
    DependencyAnalysis,
    Evidence,
    EvidenceState,
    Finding,
)

#: Observed capability names that are really taxonomy identifiers.
_ALIASES: dict[str, str] = {
    "ip": "ipv4",
    "ip_reachability": "ipv4",
    "reachability": "ipv4",
    "ping": "icmp",
    "icmp_echo": "icmp",
    "bgp_peer": "bgp",
    "bgp_session": "bgp",
    "name_resolution": "dns",
}


def normalize_capability(name: str) -> str:
    key = str(name).strip().lower()
    return _ALIASES.get(key, key)


def _state_of(by_cap: dict[str, Evidence], capability: str) -> EvidenceState:
    ev = by_cap.get(capability)
    return ev.state if ev else EvidenceState.NOT_TESTED


def _passed(by_cap: dict[str, Evidence], *caps: str) -> bool:
    return any(_state_of(by_cap, c) is EvidenceState.PASS for c in caps)


def _failed(by_cap: dict[str, Evidence], *caps: str) -> bool:
    return any(_state_of(by_cap, c) is EvidenceState.FAIL for c in caps)


class DependencyIntelligenceAnalyzer:
    """Deterministic, evidence-driven analyzer. No LLM, no graph database.

    Reuses the in-memory taxonomy graph only. Topology/relationship data is NOT
    read here - callers pass it in as additional evidence, which keeps this class
    free of database access and trivially testable.
    """

    def __init__(self, graph: DependencyGraph | None = None) -> None:
        self.graph = graph or DependencyGraph.from_taxonomy()

    def analyze(
        self, resource_id: str | None, evidence: Iterable[Evidence | dict]
    ) -> DependencyAnalysis:
        ev: list[Evidence] = []
        for item in evidence:
            if isinstance(item, Evidence):
                ev.append(item)
            elif isinstance(item, dict):
                ev.append(
                    Evidence.of(
                        item.get("capability", ""),
                        item.get("state"),
                        source=item.get("source", "unknown"),
                        resource_id=item.get("resource_id", resource_id),
                        details=item.get("details"),
                    )
                )

        by_cap: dict[str, Evidence] = {}
        for e in ev:
            by_cap[normalize_capability(e.capability)] = e

        findings = [
            self._analyse_failure(normalize_capability(e.capability), by_cap, resource_id)
            for e in ev
            if e.state is EvidenceState.FAIL
        ]
        return DependencyAnalysis(resource_id=resource_id, findings=findings, evidence=ev)

    def _analyse_failure(
        self, capability: str, by_cap: dict[str, Evidence], resource_id: str | None
    ) -> Finding:
        f = Finding(capability=capability, state=EvidenceState.FAIL)
        # Classify the dependency chain against the supplied evidence. This is the
        # evidence-priority mechanism: a PASS on a lower layer removes it from the
        # candidate set; it is never invalidated by a higher-level FAIL.
        for dep in self.graph.traverse(capability):
            state = _state_of(by_cap, dep)
            if state is EvidenceState.PASS:
                f.healthy_dependencies.append(dep)
            elif state is EvidenceState.FAIL:
                f.failed_dependencies.append(dep)
            else:
                f.unknown_dependencies.append(dep)

        healthy = set(f.healthy_dependencies)
        f.support = [by_cap[c].to_dict() for c in sorted(by_cap) if c in healthy]
        f.affected_capabilities = self.graph.direct_dependents(capability)
        f.notes.append(
            f"Analyzed resource {resource_id or 'unknown'} from supplied evidence only."
        )

        _RULES.get(capability, _rule_generic)(f, by_cap)
        f.healthy_dependencies = list(dict.fromkeys(f.healthy_dependencies))
        f.excluded_conclusions = list(dict.fromkeys(f.excluded_conclusions))
        return f


# --------------------------------------------------------------- rules


def _rule_generic(f: Finding, by_cap: dict[str, Evidence]) -> None:
    if f.healthy_dependencies:
        f.excluded_conclusions.extend(f.healthy_dependencies)
        f.candidate_causes = f.failed_dependencies or f.unknown_dependencies or [
            f"{f.capability} configuration or service"
        ]
        f.confidence = Confidence.MEDIUM if f.failed_dependencies else Confidence.LOW
    else:
        f.candidate_causes = f.unknown_dependencies or [
            f"{f.capability} service or configuration"
        ]
        f.confidence = Confidence.LOW
    f.suggested_checks = [f"Verify {c}" for c in f.candidate_causes[:5]]


def _rule_icmp(f: Finding, by_cap: dict[str, Evidence]) -> None:
    """RULE 2 / CASE E - ICMP FAIL must NOT be reported as a device outage."""
    f.excluded_conclusions.append("Device is down")
    f.candidate_causes = [
        "Physical/link layer",
        "Interface state",
        "VLAN",
        "Switching path",
        "Routing",
        "Firewall/ACL",
        "Device power or state",
    ]
    f.suggested_checks = [
        "Check physical link/cabling",
        "Check interface operational state",
        "Check VLAN membership",
        "Check the switching path",
        "Check routing to the target",
        "Check ACL/firewall on the path",
        "Check device power/state only if device-state evidence exists",
    ]
    f.confidence = Confidence.LOW
    f.notes.append(
        "ICMP failure does not establish a device outage; no device-state evidence "
        "was supplied."
    )


def _rule_snmp(f: Finding, by_cap: dict[str, Evidence]) -> None:
    """RULES 1 + 5 - isolate SNMP when lower layers and/or HTTPS are healthy."""
    ip_ok = _passed(by_cap, "ipv4", "icmp")
    https_ok = _passed(by_cap, "https")
    if ip_ok:
        f.healthy_dependencies.append("ipv4")
        f.excluded_conclusions.append("Device is down")
    f.candidate_causes = [
        "UDP/161 reachability",
        "SNMP configuration",
        "SNMP service",
        "SNMP credentials/security",
        "ACL/firewall affecting UDP/161",
    ]
    f.suggested_checks = [
        "Test UDP/161 reachability",
        "Verify SNMP configuration",
        "Verify SNMP credentials",
        "Verify ACL/firewall permits UDP/161",
        "Verify the SNMP service is running",
    ]
    if ip_ok:
        f.confidence = Confidence.HIGH
        f.notes.append(
            "IP reachability and HTTPS are confirmed, so a complete device outage "
            "is unlikely; the failure isolates to the SNMP dependency path."
            if https_ok
            else "IP reachability is confirmed, so the failure isolates to the SNMP path."
        )
    else:
        f.confidence = Confidence.LOW
        f.notes.append("No independent lower-layer confirmation was supplied.")


def _rule_https(f: Finding, by_cap: dict[str, Evidence]) -> None:
    """RULE 3 / CASE C."""
    if _passed(by_cap, "dns"):
        f.healthy_dependencies.append("dns")
    if _passed(by_cap, "ipv4", "icmp"):
        f.healthy_dependencies.append("ipv4")
    if _failed(by_cap, "tls", "tcp"):
        f.failed_dependencies.append("tls")
    f.candidate_causes = [
        "TCP/443 reachability",
        "TLS",
        "Firewall/ACL",
        "HTTPS listener",
        "Application/service",
    ]
    f.suggested_checks = [
        "Test TCP/443 connectivity",
        "Check TLS negotiation and certificate validity",
        "Check ACL/firewall permits TCP/443",
        "Verify the HTTPS listener is bound",
        "Check application/service state",
    ]
    f.confidence = Confidence.MEDIUM if f.healthy_dependencies else Confidence.LOW
    f.notes.append(
        "Healthy DNS and reachability do not establish that the application is "
        "down; the listener/TLS/ACL layers remain unproven."
    )


def _rule_bgp(f: Finding, by_cap: dict[str, Evidence]) -> None:
    """RULE 4 / CASE D."""
    if _passed(by_cap, "interface_state", "interface"):
        f.healthy_dependencies.append("interface_state")
    if _passed(by_cap, "ipv4", "icmp"):
        f.healthy_dependencies.append("ipv4")
    f.candidate_causes = [
        "TCP/179 reachability",
        "ACL/firewall blocking TCP/179",
        "BGP configuration",
        "BGP authentication",
        "Peer state",
        "Remote peer",
    ]
    f.suggested_checks = [
        "Test TCP/179 to the peer",
        "Check ACL/firewall permits TCP/179",
        "Verify BGP configuration (AS and neighbours)",
        "Verify BGP authentication",
        "Check peer/session state",
        "Verify the remote peer is operational",
    ]
    f.confidence = Confidence.MEDIUM if f.healthy_dependencies else Confidence.LOW
    f.notes.append(
        "An interface being up localises the fault to the BGP/TCP-179 path rather "
        "than the physical link."
    )


def _rule_dns(f: Finding, by_cap: dict[str, Evidence]) -> None:
    """RULE 6 - DNS FAIL with IP connectivity PASS."""
    if _passed(by_cap, "ipv4", "icmp"):
        f.healthy_dependencies.append("ipv4")
    f.candidate_causes = [
        "DNS server",
        "UDP/53 reachability",
        "TCP/53 reachability",
        "Resolver configuration",
        "ACL/firewall",
        "DNS service",
    ]
    f.suggested_checks = [
        "Test UDP/53 to the resolver",
        "Test TCP/53 to the resolver",
        "Verify resolver configuration",
        "Check ACL/firewall for port 53",
        "Verify the DNS service is running on the server",
    ]
    f.confidence = Confidence.HIGH if f.healthy_dependencies else Confidence.LOW
    f.notes.append(
        "IP connectivity is confirmed, so the failure isolates to DNS."
        if f.healthy_dependencies
        else "No independent IP confirmation was supplied."
    )


_RULES = {
    "icmp": _rule_icmp,
    "snmp": _rule_snmp,
    "https": _rule_https,
    "bgp": _rule_bgp,
    "dns": _rule_dns,
}


def analyze(
    resource_id: str | None, evidence: Iterable[Evidence | dict]
) -> DependencyAnalysis:
    """Convenience entry point using the default taxonomy graph."""
    return DependencyIntelligenceAnalyzer().analyze(resource_id, evidence)
