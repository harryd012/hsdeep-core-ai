"""
Dependency Intelligence V1.1 - integration tests.

Exercises the REAL existing contracts imported from source: ``RootCauseResult`` /
``RootCauseAnalyser`` from ``app.services.rca_service`` and ``IncidentContext``
from ``app.services.sop.sop_matcher``. Incident/Alert objects are lightweight
stand-ins so the tests stay DB-free. Async code is driven with ``asyncio.run`` so
no pytest-asyncio plugin is required.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.services.dependency_intelligence.analysis import (
    DependencyIntelligenceAnalyzer,
)
from app.services.dependency_intelligence.integration import (
    SOURCE,
    DependencyEnrichedRootCauseAnalyser,
    DependencyIntelligenceRCAAdapter,
    DependencySOPContextAdapter,
    DependencyTopologyAdapter,
    analyse_incident_context,
    evidence_from_alerts,
)
from app.services.rca_service import RootCauseResult
from app.services.sop.sop_matcher import IncidentContext

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
DEVICE_1 = "dev-1"


def alert(category, status, *, title="sensor", device_id=DEVICE_1):
    return SimpleNamespace(
        category=category, status=status, title=title, device_id=device_id
    )


def incident(*, device_id=DEVICE_1, tenant_id=TENANT_A, priority="P2"):
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        device_id=device_id,
        tenant_id=tenant_id,
        site_id=None,
        priority=priority,
    )


class FakeInnerRCA:
    """Stands in for the EXISTING analyser (e.g. RuleBasedRootCauseAnalyser)."""

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises

    async def analyse(self, incident_obj, alerts):
        if self.raises:
            raise RuntimeError("existing RCA unavailable")
        return RootCauseResult(
            root_cause_summary="PRTG sensor alert on core switch",
            supporting_evidence=[{"source": "alert", "title": "SNMP unavailable"}],
        )


def analyse(alerts):
    return DependencyIntelligenceAnalyzer().analyze(
        DEVICE_1, evidence_from_alerts(alerts)
    )


# --------------------------------------------------- RCA integration (1,2,9-12)


def test_analysis_maps_into_existing_root_cause_result():
    a = analyse([alert("icmp", "resolved"), alert("snmp", "open")])
    adapter = DependencyIntelligenceRCAAdapter()
    evidence = adapter.to_supporting_evidence(a)
    assert evidence and all(isinstance(e, dict) for e in evidence)
    entry = evidence[0]
    for key in (
        "source", "capability", "state", "confidence", "candidate_causes",
        "suggested_checks", "healthy_dependencies", "failed_dependencies",
        "unknown_dependencies", "excluded_conclusions", "dependency_identifiers",
        "failure_scope",
    ):
        assert key in entry, key
    assert entry["capability"] == "snmp"
    assert "udp_161" in entry["dependency_identifiers"]
    assert entry["failure_scope"] == "monitoring_access"
    assert json.dumps(evidence)  # structured, serialisable


def test_dependency_analysis_reaches_existing_rca_flow():
    enriched = DependencyEnrichedRootCauseAnalyser(FakeInnerRCA())
    result = asyncio.run(
        enriched.analyse(
            incident(), [alert("icmp", "resolved"), alert("snmp", "open")]
        )
    )
    assert isinstance(result, RootCauseResult)  # real existing contract
    # existing RCA output preserved...
    assert "PRTG sensor alert on core switch" in result.root_cause_summary
    assert any(e.get("source") == "alert" for e in result.supporting_evidence)
    # ...and dependency findings are now part of it
    dep = [e for e in result.supporting_evidence if e.get("source") == SOURCE]
    assert len(dep) == 1 and dep[0]["capability"] == "snmp"
    assert "Dependency intelligence:" in result.root_cause_summary
    assert "udp_161" in result.root_cause_summary


def test_rca_output_is_unchanged_without_dependency_evidence():
    """Additive guarantee: no usable dependency evidence => existing behaviour."""
    enriched = DependencyEnrichedRootCauseAnalyser(FakeInnerRCA())
    result = asyncio.run(
        enriched.analyse(incident(), [alert("vendor_specific_thing", "open")])
    )
    assert result.root_cause_summary == "PRTG sensor alert on core switch"
    assert result.supporting_evidence == [
        {"source": "alert", "title": "SNMP unavailable"}
    ]


def test_rca_survives_dependency_intelligence_failure():
    class Exploding:
        @property
        def category(self):
            raise RuntimeError("boom")

    enriched = DependencyEnrichedRootCauseAnalyser(FakeInnerRCA())
    result = asyncio.run(enriched.analyse(incident(), [Exploding()]))
    assert isinstance(result, RootCauseResult)
    assert result.root_cause_summary == "PRTG sensor alert on core switch"


def test_dependency_analysis_survives_when_rca_fails():
    """If RCA fails, dependency analysis must still be obtainable."""
    enriched = DependencyEnrichedRootCauseAnalyser(FakeInnerRCA(raises=True))
    try:
        asyncio.run(enriched.analyse(incident(), [alert("snmp", "open")]))
        raised = False
    except RuntimeError:
        raised = True
    assert raised, "the inner RCA failure still propagates from analyse()"
    analysis = asyncio.run(
        enriched.analyse_dependencies_only(
            incident(), [alert("icmp", "resolved"), alert("snmp", "open")]
        )
    )
    assert analysis is not None and analysis.has_failures


class FakeTopologyService:
    """Stands in for TopologyService (mirrors get_upstream_dependencies)."""

    def __init__(self, edges=None, *, tenant=TENANT_A, raises=False) -> None:
        self.edges = edges or []
        self.tenant = tenant
        self.raises = raises
        self.calls: list[tuple] = []

    async def get_upstream_dependencies(self, device_id, tenant_id, max_depth=5):
        self.calls.append((str(device_id), tenant_id, max_depth))
        if self.raises:
            raise RuntimeError("topology repo down")
        if tenant_id != self.tenant:
            return []  # tenant-scoped: never leak another tenant's edges
        return [
            e for e in self.edges if str(e.get("source_device_id")) == str(device_id)
        ]


EDGE = {
    "source_device_id": DEVICE_1,
    "dependency_device_id": "dev-fw",
    "dependency_device_name": "fw-01",
    "relationship_type": "routes_through",
    "depth": 1,
}


# ------------------------------- Recommendation + SOP context (3,4)


def test_dependency_context_reaches_recommendation_unaltered():
    a = analyse([alert("icmp", "resolved"), alert("snmp", "open")])
    rec = SimpleNamespace(
        reasoning={"criteria_score": 85.0}, confidence_score=85.0, matched_criteria={}
    )
    returned = DependencySOPContextAdapter().enrich_recommendations([rec], a)
    assert returned[0] is rec
    # existing scored fields untouched
    assert rec.confidence_score == 85.0
    assert rec.matched_criteria == {}
    assert rec.reasoning["criteria_score"] == 85.0
    # dependency context attached additively
    context = rec.reasoning["dependency_context"]
    assert context["match_keys"] == ["snmp:monitoring_access"]
    assert context["findings"][0]["dependency_identifiers"] == ["udp_161"]


def test_existing_sop_matching_has_no_dependency_dimension():
    """Documents why SOP *scoring* is not modified: BOUNDARY ONLY."""
    fields = set(IncidentContext.__dataclass_fields__)
    assert "category" in fields  # the mandatory existing criterion
    assert not any("depend" in name.lower() for name in fields)


def test_recommendation_enrichment_is_a_noop_without_findings():
    a = analyse([alert("icmp", "resolved")])
    rec = SimpleNamespace(reasoning={}, confidence_score=10.0)
    DependencySOPContextAdapter().enrich_recommendations([rec], a)
    assert "dependency_context" not in rec.reasoning
    assert rec.confidence_score == 10.0


# ------------------------------------------------- topology integration (5,6)


def test_existing_topology_becomes_dependency_context():
    svc = FakeTopologyService([EDGE])
    ctx = asyncio.run(DependencyTopologyAdapter(svc).dependency_context(DEVICE_1, TENANT_A))
    assert ctx["available"] is True and ctx["state"] == "PASS"
    assert ctx["dependencies"] == [
        {
            "device_id": "dev-fw",
            "name": "fw-01",
            "relationship_type": "routes_through",
            "depth": 1,
        }
    ]


def test_topology_unavailable_returns_unknown():
    no_service = asyncio.run(
        DependencyTopologyAdapter(None).dependency_context(DEVICE_1, TENANT_A)
    )
    assert no_service["state"] == "UNKNOWN" and no_service["dependencies"] == []
    no_device = asyncio.run(
        DependencyTopologyAdapter(FakeTopologyService([EDGE])).dependency_context(
            None, TENANT_A
        )
    )
    assert no_device["state"] == "UNKNOWN"
    no_edges = asyncio.run(
        DependencyTopologyAdapter(FakeTopologyService([])).dependency_context(
            DEVICE_1, TENANT_A
        )
    )
    assert no_edges["state"] == "UNKNOWN" and no_edges["reason"]
    broken = asyncio.run(
        DependencyTopologyAdapter(FakeTopologyService(raises=True)).dependency_context(
            DEVICE_1, TENANT_A
        )
    )
    assert broken["state"] == "UNKNOWN" and broken["dependencies"] == []


def test_topology_lookup_is_bounded_and_tenant_scoped():
    svc = FakeTopologyService([EDGE])
    asyncio.run(
        DependencyTopologyAdapter(svc).dependency_context(DEVICE_1, TENANT_A, max_depth=2)
    )
    assert svc.calls == [(DEVICE_1, TENANT_A, 2)]
    assert svc.calls[0][1] is not None  # never called without a tenant scope


# ------------------------------------------------------- tenant isolation (7)


def test_cross_tenant_device_yields_no_topology():
    other = dict(EDGE, source_device_id="dev-b", dependency_device_name="tenant-b-fw")
    svc = FakeTopologyService([other], tenant=TENANT_B)
    ctx = asyncio.run(
        DependencyTopologyAdapter(svc).dependency_context("dev-b", TENANT_A)
    )
    assert ctx["available"] is False and ctx["state"] == "UNKNOWN"
    assert ctx["dependencies"] == []  # tenant A never sees tenant B's edges


def test_incident_context_uses_the_incidents_own_tenant_scope():
    svc = FakeTopologyService([])
    out = asyncio.run(
        analyse_incident_context(
            incident(tenant_id=TENANT_B),
            [alert("snmp", "open")],
            topology_service=svc,
        )
    )
    assert svc.calls[0][1] == TENANT_B
    assert out["analysis"]["findings"][0]["capability"] == "snmp"


# ------------------------------------------------- real incident context (8)


def test_incident_context_reuses_loaded_alerts_without_n_plus_one():
    svc = FakeTopologyService([EDGE])
    out = asyncio.run(
        analyse_incident_context(
            incident(),
            [alert("icmp", "resolved"), alert("snmp", "open")],
            topology_service=svc,
        )
    )
    assert len(svc.calls) == 1  # one bounded topology call per incident
    assert out["analysis"]["resource_id"] == DEVICE_1
    finding = out["analysis"]["findings"][0]
    assert finding["capability"] == "snmp"
    assert finding["healthy_dependencies"] == ["ipv4"]
    assert out["sop_context"]["match_keys"] == ["snmp:monitoring_access"]


# --------------------------------------------------- end-to-end scenarios


def _e2e(alerts):
    """Incident -> Dependency Intelligence -> RCA -> Recommendation/SOP context."""
    inc = incident()
    enriched = DependencyEnrichedRootCauseAnalyser(FakeInnerRCA())
    result = asyncio.run(enriched.analyse(inc, alerts))
    analysis = enriched.analyze_alerts(inc, alerts)
    dep_evidence = [
        e for e in result.supporting_evidence if e.get("source") == SOURCE
    ]
    recommendation = SimpleNamespace(reasoning={}, confidence_score=70.0)
    DependencySOPContextAdapter().enrich_recommendations([recommendation], analysis)
    return result, analysis, dep_evidence, recommendation


def test_e2e_snmp_failure_incident():
    result, analysis, dep_evidence, recommendation = _e2e(
        [alert("icmp", "resolved"), alert("snmp", "open")]
    )
    finding = analysis.findings[0]
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("udp/161", "snmp configuration", "snmp service", "credentials", "acl"):
        assert expect in causes, expect
    # IP reachability healthy
    assert "ipv4" in finding.healthy_dependencies
    # "Device is down" must NOT become the root cause. It may legitimately
    # appear as an explicitly EXCLUDED conclusion, but never as a candidate.
    assert "Device is down" in finding.excluded_conclusions
    assert "device is down" not in result.root_cause_summary.lower()
    for key in ("candidate_causes", "suggested_checks"):
        assert "device is down" not in " ".join(dep_evidence[0][key]).lower(), key
    # RCA window
    assert dep_evidence[0]["capability"] == "snmp"
    assert dep_evidence[0]["failure_scope"] == "monitoring_access"
    assert "udp_161" in dep_evidence[0]["dependency_identifiers"]
    # recommendation window
    assert recommendation.reasoning["dependency_context"]["match_keys"] == [
        "snmp:monitoring_access"
    ]
    assert recommendation.confidence_score == 70.0


def test_e2e_https_failure_incident():
    result, analysis, dep_evidence, recommendation = _e2e(
        [alert("icmp", "resolved"), alert("dns", "resolved"), alert("https", "open")]
    )
    finding = analysis.findings[0]
    assert finding.capability == "https"
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("tcp/443", "tls", "firewall", "listener", "application"):
        assert expect in causes, expect
    # DNS healthy, IP healthy
    assert "dns" in finding.healthy_dependencies
    assert "ipv4" in finding.healthy_dependencies
    # must not declare the application definitely down
    assert "not establish that the application is down" in " ".join(finding.notes)
    assert "application is down" not in result.root_cause_summary.lower()
    assert dep_evidence[0]["failure_scope"] == "application_access"
    assert "tcp_443" in dep_evidence[0]["dependency_identifiers"]


def test_e2e_bgp_failure_incident():
    from app.services.dependency_intelligence.models import Evidence

    evidence = [Evidence.of("interface_state", "UP"), Evidence.of("bgp", "DOWN")]
    analysis = DependencyIntelligenceAnalyzer().analyze(DEVICE_1, evidence)
    finding = analysis.findings[0]
    assert finding.capability == "bgp"
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("tcp/179", "acl", "bgp configuration", "authentication", "peer"):
        expect in causes or (_ for _ in ()).throw(AssertionError(expect))
    # interface health remains healthy
    assert "interface_state" in finding.healthy_dependencies
    assert dep_aligned(finding)


def dep_aligned(finding) -> bool:
    """Interface health must never be reported as a failed dependency."""
    assert "interface_state" not in finding.failed_dependencies
    assert "interface_state" not in finding.unknown_dependencies
    return True


def test_e2e_icmp_failure_does_not_invent_a_device_outage():
    result, analysis, dep_evidence, _ = _e2e([alert("icmp", "open")])
    finding = analysis.findings[0]
    assert finding.capability == "icmp"
    assert "Device is down" in finding.excluded_conclusions
    lowered = result.root_cause_summary.lower()
    assert "device is down" not in lowered
    assert "device down" not in lowered
    # lower-layer candidate layers must be present
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("physical", "interface", "vlan", "switching", "routing", "firewall"):
        assert expect in causes, expect
    assert dep_evidence[0]["failure_scope"] == "reachability"


# =========================================================================
# V1.2 - production wiring + real alert vocabulary
# =========================================================================

from app.services.dependency_intelligence.integration import alert_state  # noqa: E402
from app.services.dependency_intelligence.models import EvidenceState  # noqa: E402
from app.services.rca_service import (  # noqa: E402
    RCAService,
    RuleBasedRootCauseAnalyser,
)


# --------------------------- production RCA construction (17.1, 17.2)


def build_rca_service(**kwargs):
    """Real production construction path (session_factory or both repos)."""
    return RCAService(event_bus=SimpleNamespace(), **kwargs)


def test_production_rca_default_analyser_is_dependency_enriched():
    service = build_rca_service(alert_repo=object(), incident_repo=object())
    assert isinstance(service.analyser, DependencyEnrichedRootCauseAnalyser)
    # the EXISTING analyser is still the inner engine - not replaced
    assert isinstance(service.analyser.inner, RuleBasedRootCauseAnalyser)


def test_explicit_analyser_is_never_wrapped():
    sentinel = FakeInnerRCA()
    service = build_rca_service(
        alert_repo=object(), incident_repo=object(), analyser=sentinel
    )
    assert service.analyser is sentinel


def test_existing_rca_without_dependency_evidence_is_unchanged():
    """Backward compatibility for incidents with no usable dependency evidence."""
    wrapper = DependencyEnrichedRootCauseAnalyser(FakeInnerRCA())
    result = asyncio.run(
        wrapper.analyse(incident(), [alert("unsupported_protocol", "open")])
    )
    assert result.supporting_evidence == [
        {"source": "alert", "title": "SNMP unavailable"}
    ]
    assert not any(e.get("source") == SOURCE for e in result.supporting_evidence)


# ------------------------------- real alert vocabulary (17.3, 17.4, 17.5)


def test_real_alert_status_vocabulary():
    """app/models/alert.py: status = open | acknowledged | resolved."""
    assert alert_state("open") is EvidenceState.FAIL
    assert alert_state("acknowledged") is EvidenceState.FAIL
    assert alert_state("resolved") is EvidenceState.PASS
    assert alert_state("OPEN") is EvidenceState.FAIL  # case-insensitive


def test_acknowledged_alert_is_active_not_resolved():
    """V1.1 bug: 'acknowledged' was unmapped and treated as UNKNOWN."""
    ev = evidence_from_alerts([alert("snmp", "acknowledged")])
    assert ev[0].state is EvidenceState.FAIL


def test_unknown_alert_status_is_unknown_and_never_pass():
    for value in ("", None, "banana", "recovered", "cleared", 1):
        assert alert_state(value) is EvidenceState.UNKNOWN, value


def test_severity_does_not_imply_failure_state():
    """severity=critical + status=resolved must be PASS, not FAIL."""
    resolved_critical = SimpleNamespace(
        category="snmp", status="resolved", severity="critical", device_id=DEVICE_1
    )
    evidence = evidence_from_alerts([resolved_critical])
    assert evidence[0].state is EvidenceState.PASS
    # severity is retained as supporting metadata only
    assert "severity=critical" in (evidence[0].details or "")


def test_severity_value_is_not_accepted_as_a_status():
    """'critical' is a severity, not a status - must not map to FAIL."""
    assert alert_state("critical") is EvidenceState.UNKNOWN
    assert alert_state("warning") is EvidenceState.UNKNOWN
    assert alert_state("info") is EvidenceState.UNKNOWN


def test_real_vocabulary_snmp_end_to_end_via_rca():
    """Fixture-based SNMP e2e using the REAL status vocabulary."""
    result, analysis, dep_evidence, recommendation = _e2e(
        [
            SimpleNamespace(
                category="icmp", status="resolved", severity="info", device_id=DEVICE_1
            ),
            SimpleNamespace(
                category="snmp", status="open", severity="critical", device_id=DEVICE_1
            ),
        ]
    )
    finding = analysis.findings[0]
    assert finding.capability == "snmp"
    assert "ipv4" in finding.healthy_dependencies  # ICMP resolved -> PASS
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("udp/161", "snmp configuration", "credentials", "acl"):
        assert expect in causes, expect
    assert "Device is down" in finding.excluded_conclusions
    assert "device is down" not in result.root_cause_summary.lower()
    assert dep_evidence[0]["failure_scope"] == "monitoring_access"


# ------------------------------------- failure isolation (17.13, 17.14, 17.15)


def test_dependency_failure_never_breaks_rca():
    class ExplodingAnalyzer:
        def analyze(self, *_args, **_kwargs):
            raise RuntimeError("dependency engine unavailable")

    wrapper = DependencyEnrichedRootCauseAnalyser(
        FakeInnerRCA(), analyzer=ExplodingAnalyzer()
    )
    result = asyncio.run(wrapper.analyse(incident(), [alert("snmp", "open")]))
    assert result.root_cause_summary == "PRTG sensor alert on core switch"
    assert result.supporting_evidence == [
        {"source": "alert", "title": "SNMP unavailable"}
    ]


def test_sop_and_recommendation_remain_backward_compatible():
    """No dependency findings => recommendations and SOP context untouched."""
    a = analyse([SimpleNamespace(category="snmp", status="resolved", device_id=DEVICE_1)])
    rec = SimpleNamespace(reasoning={"criteria_score": 90.0}, confidence_score=90.0)
    DependencySOPContextAdapter().enrich_recommendations([rec], a)
    assert rec.reasoning == {"criteria_score": 90.0}
    assert a.findings == []
