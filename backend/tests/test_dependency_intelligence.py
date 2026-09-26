"""
Dependency Intelligence V1 - focused tests.

Pure, DB-free: the analyzer reasons only from supplied evidence plus the
in-code taxonomy, so these run without fixtures. Both positive AND negative
reasoning are asserted (e.g. ICMP FAIL must not yield a device-down conclusion).
"""
from __future__ import annotations

from app.services.dependency_intelligence import (
    Confidence,
    DependencyGraph,
    DependencyIntelligenceAnalyzer,
    Evidence,
    EvidenceState,
    all_protocols,
    coerce_state,
    get_protocol,
)
from app.services.dependency_intelligence.taxonomy import (
    CATEGORIES,
    protocols_in_category,
)

ANALYZER = DependencyIntelligenceAnalyzer()


def find(analysis, capability):
    return next(f for f in analysis.findings if f.capability == capability)


def joined(items):
    return " | ".join(items).lower()


# ------------------------------------------------------------------ taxonomy


def test_taxonomy_registers_protocols():
    ids = {p.identifier for p in all_protocols()}
    # One representative from every required group.
    for expected in (
        "ipv4", "ipv6", "icmp", "icmpv6", "arp", "ndp", "tcp", "udp", "quic",
        "dns", "dhcp", "dhcpv6", "ntp", "snmp", "ssh", "http", "https", "tls",
        "ldap", "ldaps", "radius", "tacacs", "syslog", "smtp", "imap", "pop3",
        "bgp", "ospf", "rip", "gre", "ipsec", "ssl_vpn", "site_to_site_vpn", "ztna",
        "vlan", "stp", "rstp", "mstp", "lacp", "lldp", "cdp",
        "wifi", "capwap", "802_1x",
    ):
        assert expected in ids, expected


def test_taxonomy_categories_and_lookup():
    for cat in CATEGORIES:
        assert protocols_in_category(cat), cat
    snmp = get_protocol("snmp")
    assert snmp is not None and snmp.port == 161 and snmp.transport == "udp"
    assert get_protocol("SNMP") is snmp  # case-insensitive
    assert get_protocol("not-a-protocol") is None


def test_ports_are_not_invented_for_l2_protocols():
    for id_ in ("vlan", "stp", "lldp", "cdp", "lacp"):
        proto = get_protocol(id_)
        assert proto.port is None, id_
        assert proto.transport is None, id_


# --------------------------------------------------------------------- graph


def test_graph_dependency_lookup():
    g = DependencyGraph.from_taxonomy()
    assert "udp" in g.direct_dependencies("snmp")
    assert "tls" in g.direct_dependencies("https")
    assert "tcp" in g.direct_dependencies("tls")
    assert "ipv4" in g.direct_dependencies("tcp")
    assert "bgp" in g.direct_dependents("tcp")
    assert "udp" in g.traverse("snmp")
    assert "ipv4" in g.traverse("snmp")


def test_graph_traversal_and_path():
    g = DependencyGraph.from_taxonomy()
    chain = g.traverse("https")
    assert chain.index("tls") < chain.index("tcp") < chain.index("ipv4")
    path = g.dependency_path("https", "ipv4")
    assert path == ["https", "tls", "tcp", "ipv4"]
    assert g.dependency_path("ipv4", "https") is None  # wrong direction


def test_graph_cycle_protection():
    g = DependencyGraph()
    g.add_protocol("ipv4")
    # Inject a cycle directly; traversal must terminate and visit each once.
    g.nodes.update({"a", "b"})
    g._required["a"] = ("b",)
    g._required["b"] = ("a",)
    chain = g.traverse("a")
    assert chain.count("a") == 0 and chain.count("b") == 1
    assert g.dependency_path("a", "a") is None


# ------------------------------------------------------------------ evidence


def test_evidence_state_coercion_is_fail_closed():
    assert coerce_state("PASS") is EvidenceState.PASS
    assert coerce_state("down") is EvidenceState.FAIL
    assert coerce_state(True) is EvidenceState.PASS
    assert coerce_state(False) is EvidenceState.FAIL
    # Unrecognised is NEVER treated as healthy.
    assert coerce_state("banana") is EvidenceState.UNKNOWN
    assert coerce_state(None) is EvidenceState.UNKNOWN


def test_pass_evidence_produces_no_findings():
    a = ANALYZER.analyze("dev-1", [Evidence.of("icmp", "PASS"), Evidence.of("snmp", "PASS")])
    assert a.findings == []
    assert a.has_failures is False


def test_unknown_evidence_produces_no_failure_finding():
    a = ANALYZER.analyze("dev-1", [Evidence.of("snmp", "UNKNOWN")])
    assert a.findings == []


def test_analysis_is_serialisable_and_deterministic():
    ev = [Evidence.of("icmp", "PASS"), Evidence.of("snmp", "FAIL")]
    first = ANALYZER.analyze("dev-1", ev).to_dict()
    second = ANALYZER.analyze("dev-1", ev).to_dict()
    # analyzed_at differs; every reasoning field must be identical.
    assert first["findings"] == second["findings"]
    assert first["resource_id"] == "dev-1"
    import json

    json.dumps(first)  # must be JSON-serialisable for RCA/RR/SOP consumers


# ------------------------------------------------------------ rule 1 / case A


def test_rule1_icmp_pass_snmp_fail_isolates_snmp():
    a = ANALYZER.analyze("dev-1", [Evidence.of("icmp", "PASS"), Evidence.of("snmp", "FAIL")])
    f = find(a, "snmp")
    assert f.state is EvidenceState.FAIL
    # IP is confirmed healthy and is therefore ELIMINATED as a candidate.
    assert "ipv4" in f.healthy_dependencies
    causes = joined(f.candidate_causes)
    assert "udp/161" in causes and "snmp" in causes and "acl" in causes
    checks = joined(f.suggested_checks)
    for expect in ("udp/161", "credentials", "acl", "service", "configuration"):
        assert expect in checks
    # NEGATIVE: no device-down conclusion.
    assert "Device is down" in f.excluded_conclusions
    assert "device is down" not in joined(f.notes)
    assert f.confidence is Confidence.HIGH


def test_rule1_does_not_report_device_down():
    a = ANALYZER.analyze("dev-1", [Evidence.of("icmp", "PASS"), Evidence.of("snmp", "FAIL")])
    blob = joined(find(a, "snmp").notes + find(a, "snmp").candidate_causes)
    assert "device is down" not in blob


# ---------------------------------------------------------------- rule 5 / case B


def test_rule5_snmp_fail_with_https_pass():
    a = ANALYZER.analyze(
        "dev-1",
        [Evidence.of("icmp", "PASS"), Evidence.of("https", "PASS"), Evidence.of("snmp", "FAIL")],
    )
    f = find(a, "snmp")
    assert "ipv4" in f.healthy_dependencies
    assert f.confidence is Confidence.HIGH
    # Evidence priority: a healthy higher-level protocol must not be invalidated,
    # and must be cited as proof the device is not wholly down.
    assert "unlikely" in joined(f.notes)
    assert "Device is down" in f.excluded_conclusions


# ---------------------------------------------------------------- rule 2 / case E


def test_rule2_icmp_fail_is_not_a_device_outage():
    a = ANALYZER.analyze("dev-1", [Evidence.of("icmp", "FAIL")])
    f = find(a, "icmp")
    # NEGATIVE reasoning is the point of this test.
    assert "Device is down" in f.excluded_conclusions
    blob = joined(f.candidate_causes + f.notes)
    assert "device is down" not in blob
    assert f.confidence is Confidence.LOW
    causes = joined(f.candidate_causes)
    for expect in ("physical", "interface", "vlan", "switching", "routing", "firewall"):
        assert expect in causes
    # Checks should proceed from the lower layers toward the device.
    assert "physical link" in joined(f.suggested_checks[:2])


# ---------------------------------------------------------------- rule 3 / case C


def test_rule3_dns_pass_icmp_pass_https_fail():
    a = ANALYZER.analyze(
        "dev-1",
        [Evidence.of("dns", "PASS"), Evidence.of("icmp", "PASS"), Evidence.of("https", "FAIL")],
    )
    f = find(a, "https")
    causes = joined(f.candidate_causes)
    for expect in ("tcp/443", "tls", "firewall", "listener", "application"):
        assert expect in causes
    # Must not definitively blame the application.
    assert "do not establish that the application is down" in joined(f.notes)
    assert "tls" in f.healthy_dependencies or "dns" in f.healthy_dependencies


# ---------------------------------------------------------------- rule 4 / case D


def test_rule4_interface_up_bgp_down():
    a = ANALYZER.analyze(
        "dev-1",
        [Evidence.of("interface_state", "UP"), Evidence.of("bgp", "DOWN")],
    )
    f = find(a, "bgp")
    assert f.state is EvidenceState.FAIL
    causes = joined(f.candidate_causes)
    for expect in ("tcp/179", "acl", "bgp configuration", "authentication", "peer"):
        assert expect in causes
    assert "interface_state" in f.healthy_dependencies


# ---------------------------------------------------------------- rule 6 / case F


def test_rule6_dns_fail_with_ip_pass():
    a = ANALYZER.analyze("dev-1", [Evidence.of("icmp", "PASS"), Evidence.of("dns", "FAIL")])
    f = find(a, "dns")
    causes = joined(f.candidate_causes)
    for expect in ("dns server", "udp/53", "tcp/53", "resolver", "acl", "dns service"):
        assert expect in causes
    assert f.confidence is Confidence.HIGH
    assert "ipv4" in f.healthy_dependencies


def test_ip_reachability_alias_is_understood():
    """A caller may pass 'ip_reachability'; it must map onto ipv4."""
    a = ANALYZER.analyze("dev-1", [Evidence.of("ip_reachability", "PASS"), Evidence.of("snmp", "FAIL")])
    assert a.findings, "snmp failure should be analysed"
    assert "ipv4" in find(a, "snmp").healthy_dependencies


def test_unknown_capability_failure_is_handled_generically():
    a = ANALYZER.analyze("dev-1", [Evidence.of("made_up_protocol", "FAIL")])
    f = find(a, "made_up_protocol")
    assert f.confidence is Confidence.LOW
    assert f.candidate_causes


# ------------------------------------------------- RCA / RR / SOP boundary


def test_findings_expose_rca_sop_ready_fields():
    """The boundary contract for RCA / Resolution Recommendation / SOP.

    Asserts the deterministic identifiers exist; it does NOT claim those
    subsystems were modified or wired.
    """
    a = ANALYZER.analyze("dev-1", [Evidence.of("icmp", "PASS"), Evidence.of("snmp", "FAIL")])
    d = a.to_dict()
    f = d["findings"][0]
    assert f["capability"] == "snmp"
    assert f["confidence"] in {"HIGH", "MEDIUM", "LOW"}
    assert isinstance(f["candidate_causes"], list) and f["candidate_causes"]
    assert isinstance(f["suggested_checks"], list) and f["suggested_checks"]
    assert isinstance(f["support"], list)
    assert d["has_failures"] is True
