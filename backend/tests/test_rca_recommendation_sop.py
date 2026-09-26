"""
RCA -> Resolution Recommendation -> SOP V1 integration tests.

Real classes: RuleBasedRootCauseAnalyser, SOPRecommendationEngine,
DependencyEnrichedRootCauseAnalyser, RootCauseResult. Only the session and
repositories are stubbed (no DB required).

Note: this repository has NO pre-existing SOP/recommendation test module - these
are the first tests to exercise that layer directly.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.dependency_intelligence.integration import (
    SOURCE,
    DependencyEnrichedRootCauseAnalyser,
)
from app.services.rca_service import RootCauseResult, RuleBasedRootCauseAnalyser
from app.services.sop.recommendation_engine import SOPRecommendationEngine

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = uuid.UUID("22222222-2222-2222-2222-222222222222")
DEVICE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


def incident(*, tenant=TENANT, recommended_sop=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant,
        device_id=DEVICE_ID,
        site_id=None,
        priority="P2",
        status="open",
        title="PRTG SNMP CPU Usage",
        recommended_sop=recommended_sop,
        root_cause_summary=None,
        supporting_evidence=None,
    )


def alert(category, status, *, severity="info", title=None):
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        category=category,
        status=status,
        severity=severity,
        title=title or f"{category} signal",
        description=f"{category} description",
        device_id=DEVICE_ID,
        site_id=None,
        is_root_cause_candidate=False,
        vendor=None,
        region=None,
        monitoring_source_id=None,
        metric_type=None,
        triggered_at=ts,
        last_occurred_at=ts,
        occurrence_count=1,
    )


class SpySOPEngine:
    """Records how the real RCA analyser invokes the recommendation layer."""

    def __init__(self, recommendations=None) -> None:
        self.recommendations = recommendations or []
        self.calls: list[dict] = []

    async def recommend(self, incident_obj, *, limit=5, min_confidence=0.0):
        self.calls.append(
            {
                "incident_id": str(incident_obj.id),
                "tenant_id": str(incident_obj.tenant_id),
                "limit": limit,
                "min_confidence": min_confidence,
            }
        )
        return list(self.recommendations)


def build_analyser(spy):
    """The production wrapper shape: enriched(raw rule-based engine)."""
    return DependencyEnrichedRootCauseAnalyser(
        RuleBasedRootCauseAnalyser(sop_recommendation_engine=spy)
    )


def sop_context_entries(result):
    return [
        e for e in result.supporting_evidence if e.get("source") == f"{SOURCE}_sop_context"
    ]


# ------------------------------------------------- recommendation wiring (SOP)


def test_bind_session_builds_the_real_sop_engine():
    analyser = RuleBasedRootCauseAnalyser()
    assert analyser._sop_engine is None  # noqa: SLF001 - dead in production before
    analyser.bind_session(object())      # any session-like object
    assert isinstance(analyser._sop_engine, SOPRecommendationEngine)  # noqa: SLF001


def test_wrapper_propagates_session_to_the_inner_engine():
    inner = RuleBasedRootCauseAnalyser()
    wrapper = DependencyEnrichedRootCauseAnalyser(inner)
    wrapper.bind_session(object())
    assert isinstance(inner._sop_engine, SOPRecommendationEngine)  # noqa: SLF001


def test_existing_engine_is_never_replaced():
    spy = SpySOPEngine()
    analyser = RuleBasedRootCauseAnalyser(sop_recommendation_engine=spy)
    analyser.bind_session(object())
    assert analyser._sop_engine is spy  # noqa: SLF001


def test_rebinding_a_new_session_rebuilds_the_engine():
    """RCAService opens a fresh session per event; a stale one must not be kept."""
    analyser = RuleBasedRootCauseAnalyser()
    session_one = object()
    analyser.bind_session(session_one)
    engine_one = analyser._sop_engine  # noqa: SLF001
    assert isinstance(engine_one, SOPRecommendationEngine)

    analyser.bind_session(session_one)  # same session: no needless rebuild
    assert analyser._sop_engine is engine_one  # noqa: SLF001

    analyser.bind_session(object())  # next event's fresh session
    assert analyser._sop_engine is not engine_one  # noqa: SLF001
    assert isinstance(analyser._sop_engine, SOPRecommendationEngine)  # noqa: SLF001


def test_recommendation_layer_receives_the_incident_tenant():
    spy = SpySOPEngine()
    inc = incident()
    asyncio.run(
        build_analyser(spy).analyse(
            inc, [alert("icmp", "resolved"), alert("snmp", "open", severity="critical")]
        )
    )
    assert len(spy.calls) == 1
    assert spy.calls[0]["tenant_id"] == str(TENANT)  # incident tenant, not caller
    assert spy.calls[0]["incident_id"] == str(inc.id)
    assert spy.calls[0]["limit"] == 1  # existing RCA contract preserved


def test_sop_engine_failure_does_not_break_rca():
    class ExplodingSOPEngine:
        async def recommend(self, *_a, **_k):
            raise RuntimeError("sop repos down")

    result = asyncio.run(
        build_analyser(ExplodingSOPEngine()).analyse(incident(), [alert("snmp", "open")])
    )
    assert isinstance(result, RootCauseResult)
    assert result.recommended_sop_id is None  # no fabricated match
    assert sop_context_entries(result)        # dependency context still preserved


def test_existing_recommended_sop_fallback_is_preserved():
    """Unchanged RCA behaviour: falls back to incident.recommended_sop."""
    result = asyncio.run(
        build_analyser(SpySOPEngine()).analyse(
            incident(recommended_sop="sop-123"), [alert("snmp", "open")]
        )
    )
    assert result.recommended_sop_id == "sop-123"


# ------------------------------------------------------- SNMP scenario


def test_snmp_rca_context_reaches_recommendation_and_sop():
    spy = SpySOPEngine()
    alerts = [
        alert("icmp", "resolved", title="PRTG ping"),
        alert("snmp", "open", severity="critical", title="PRTG SNMP CPU Usage"),
    ]
    result = asyncio.run(build_analyser(spy).analyse(incident(), alerts))

    assert len(spy.calls) == 1  # recommendation layer really invoked

    entries = sop_context_entries(result)
    assert len(entries) == 1
    assert entries[0]["match_keys"] == ["snmp:monitoring_access"]
    finding = entries[0]["findings"][0]
    assert finding["capability"] == "snmp"
    assert finding["failure_scope"] == "monitoring_access"
    assert "udp_161" in finding["dependency_identifiers"]
    assert finding["confidence"] == "HIGH"

    dep = [e for e in result.supporting_evidence if e.get("source") == SOURCE]
    assert len(dep) == 1
    assert dep[0]["healthy_dependencies"] == ["ipv4"]
    causes = " | ".join(dep[0]["candidate_causes"]).lower()
    for expect in ("udp/161", "snmp configuration", "credentials", "acl"):
        assert expect in causes, expect
    assert "Device is down" in dep[0]["excluded_conclusions"]
    assert "device is down" not in result.root_cause_summary.lower()


# ------------------------------------------------------ HTTPS scenario


def test_https_rca_context_reaches_recommendation_and_sop():
    spy = SpySOPEngine()
    alerts = [alert("dns", "resolved"), alert("icmp", "resolved"), alert("https", "open")]
    result = asyncio.run(build_analyser(spy).analyse(incident(), alerts))

    assert len(spy.calls) == 1
    entries = sop_context_entries(result)
    assert len(entries) == 1
    assert entries[0]["match_keys"] == ["https:application_access"]
    assert "tcp_443" in entries[0]["findings"][0]["dependency_identifiers"]

    dep = [e for e in result.supporting_evidence if e.get("source") == SOURCE][0]
    causes = " | ".join(dep["candidate_causes"]).lower()
    for expect in ("tcp/443", "tls", "firewall", "listener", "application"):
        assert expect in causes, expect
    assert dep["healthy_dependencies"] == ["dns", "ipv4"]
    # must not assert an application outage as fact
    assert "application is down" not in result.root_cause_summary.lower()


# -------------------------------------------------------- BGP scenario


def test_bgp_context_reaches_the_sop_layer():
    from app.services.dependency_intelligence.analysis import (
        DependencyIntelligenceAnalyzer,
    )
    from app.services.dependency_intelligence.integration import (
        DependencySOPContextAdapter,
    )
    from app.services.dependency_intelligence.models import Evidence

    # BGP needs explicit interface-state evidence: no vendor alert source maps
    # to interface state, so nothing is invented here.
    analysis = DependencyIntelligenceAnalyzer().analyze(
        DEVICE_ID, [Evidence.of("interface_state", "UP"), Evidence.of("bgp", "DOWN")]
    )
    finding = analysis.findings[0]
    assert finding.capability == "bgp"
    assert "interface_state" in finding.healthy_dependencies
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("tcp/179", "acl", "bgp configuration", "authentication", "peer"):
        assert expect in causes, expect

    context = DependencySOPContextAdapter().to_sop_context(analysis)
    assert context["match_keys"] == ["bgp:routing"]
    assert "tcp_179" in context["findings"][0]["dependency_identifiers"]


# --------------------------------------------- no RCA / insufficient evidence


def test_no_dependency_evidence_fabricates_nothing():
    spy = SpySOPEngine()
    result = asyncio.run(
        build_analyser(spy).analyse(incident(), [alert("unsupported_protocol", "open")])
    )
    # RCA output untouched
    assert result.recommended_sop_id is None
    assert sop_context_entries(result) == []
    assert [e for e in result.supporting_evidence if e.get("source") == SOURCE] == []
    # the recommendation layer was still consulted and returned its real empty
    # result - nothing fabricated in its place
    assert len(spy.calls) == 1


def test_empty_alerts_still_produce_a_valid_result():
    result = asyncio.run(build_analyser(SpySOPEngine()).analyse(incident(), []))
    assert isinstance(result, RootCauseResult)
    assert result.supporting_evidence == []


# ----------------------------------------------------------- tenant isolation


def test_tenant_scoping_is_preserved_end_to_end():
    """Tenant A's pipeline must never carry another tenant's scope."""
    spy = SpySOPEngine()
    asyncio.run(
        build_analyser(spy).analyse(
            incident(tenant=OTHER_TENANT), [alert("icmp", "resolved"), alert("snmp", "open")]
        )
    )
    assert spy.calls[0]["tenant_id"] == str(OTHER_TENANT)
    assert spy.calls[0]["tenant_id"] != str(TENANT)


# --------------------------------------------------------- topology UNKNOWN


def test_topology_unknown_does_not_block_recommendation_or_sop():
    """Zero resource_relationship rows: RCA, recommendation and SOP all work."""
    spy = SpySOPEngine()
    analyzer = DependencyEnrichedRootCauseAnalyser(
        RuleBasedRootCauseAnalyser(sop_recommendation_engine=spy)
    )
    analyzer._session = None  # noqa: SLF001 - unbound, as in production pre-bind
    assert analyzer._resolve_topology_service() is None  # noqa: SLF001

    result = asyncio.run(
        analyzer.analyse(incident(), [alert("icmp", "resolved"), alert("snmp", "open")])
    )
    sources = [e.get("source") for e in result.supporting_evidence]
    assert f"{SOURCE}_topology" not in sources  # no fake topology introduced
    assert f"{SOURCE}_sop_context" in sources   # SOP context still delivered
    assert len(spy.calls) == 1                  # recommendation still invoked
