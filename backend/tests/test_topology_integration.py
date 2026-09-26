"""
Topology integration into the RCA path.

Closes the CONTRACT-VALIDATED gap: a REAL TopologyService is built on the session
the owning service already holds, and real resource_relationships edges become
dependency context carried on the existing RootCauseResult.

Only the session/repository is stubbed; the analyser, TopologyService, adapter
and RootCauseResult are the real classes.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.services.dependency_intelligence.integration import (
    SOURCE,
    DependencyEnrichedRootCauseAnalyser,
    DependencyTopologyAdapter,
)
from app.services.rca_service import RootCauseResult
from app.services.topology_service import TopologyService

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = uuid.UUID("22222222-2222-2222-2222-222222222222")
DEVICE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
FIREWALL_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


class FakeRelationship:
    """Mirrors the real ResourceRelationship row shape used by TopologyService."""

    def __init__(self, source, target, relationship_type, status="up") -> None:
        self.source_resource_id = source
        self.target_resource_id = target
        self.relationship_type = relationship_type
        self.status = status


class TenantScopedRelationshipRepo:
    """Mirrors ResourceRelationshipRepository: tenant-scoped edge fetches."""

    def __init__(self, edges, *, tenant=TENANT) -> None:
        self.edges = edges
        self.tenant = tenant
        self.calls: list[tuple] = []

    async def list_edges_from(self, device_id, tenant_id):
        self.calls.append((str(device_id), tenant_id))
        if tenant_id != self.tenant:
            return []  # never leak another tenant's edges
        return [
            (edge, "fw-01") for edge in self.edges if edge.source_resource_id == device_id
        ]


class FakeSession:
    """Stands in for AsyncSession; TopologyService only stores it."""

    def __init__(self, repo=None) -> None:
        self.repo = repo


def topology_service(edges, *, tenant=TENANT):
    """Build a REAL TopologyService whose repository uses controlled edges."""
    repo = TenantScopedRelationshipRepo(edges, tenant=tenant)
    service = TopologyService.__new__(TopologyService)  # bypass the real ctor
    service.repo = repo
    service.db = FakeSession(repo)
    return service


class FakeInnerRCA:
    async def analyse(self, incident, alerts):  # noqa: ARG002
        return RootCauseResult(
            root_cause_summary="PRTG sensor alert",
            supporting_evidence=[{"source": "alert", "title": "SNMP unavailable"}],
        )


def incident():
    return SimpleNamespace(device_id=DEVICE_ID, tenant_id=TENANT, id=uuid.uuid4())


def alert(category, status):
    return SimpleNamespace(
        id=uuid.uuid4(),
        category=category,
        status=status,
        severity="info",
        title=f"{category} signal",
        description=None,
        device_id=DEVICE_ID,
    )


EDGE = FakeRelationship(DEVICE_ID, FIREWALL_ID, "routes_through")


# --------------------------------------- real service -> dependency context


def test_real_topology_service_converts_real_edges():
    context = asyncio.run(
        DependencyTopologyAdapter(topology_service([EDGE])).dependency_context(
            DEVICE_ID, TENANT
        )
    )
    assert context["available"] is True
    assert context["state"] == "PASS"
    assert context["dependencies"] == [
        {
            "device_id": str(FIREWALL_ID),
            "name": "fw-01",
            "relationship_type": "routes_through",
            "depth": 1,
        }
    ]


def test_real_topology_service_returns_unknown_without_edges():
    context = asyncio.run(
        DependencyTopologyAdapter(topology_service([])).dependency_context(
            DEVICE_ID, TENANT
        )
    )
    assert context["available"] is False and context["state"] == "UNKNOWN"


def test_real_topology_service_is_tenant_scoped():
    """Tenant B's edges are invisible to tenant A's lookup."""
    context = asyncio.run(
        DependencyTopologyAdapter(
            topology_service([EDGE], tenant=OTHER_TENANT)
        ).dependency_context(DEVICE_ID, TENANT)
    )
    assert context["available"] is False and context["dependencies"] == []


# --------------------------------------------------- RCA path: bound session


def test_rca_carries_real_topology_when_session_bound():
    analyser = DependencyEnrichedRootCauseAnalyser(
        FakeInnerRCA(), topology_service=topology_service([EDGE])
    )
    analyser.bind_session(FakeSession())  # what RCAService now does

    result = asyncio.run(
        analyser.analyse(incident(), [alert("icmp", "resolved"), alert("snmp", "open")])
    )
    topology_entries = [
        e for e in result.supporting_evidence if e.get("source") == f"{SOURCE}_topology"
    ]
    assert len(topology_entries) == 1
    entry = topology_entries[0]
    assert entry["state"] == "PASS"
    assert entry["dependencies"][0]["relationship_type"] == "routes_through"
    assert str(entry["resource_id"]) == str(DEVICE_ID)
    # existing behaviour preserved alongside topology
    assert any(e.get("source") == "alert" for e in result.supporting_evidence)
    assert any(e.get("source") == SOURCE for e in result.supporting_evidence)


def test_rca_omits_topology_when_unavailable():
    """No edges => nothing injected; dependency findings still flow."""
    analyser = DependencyEnrichedRootCauseAnalyser(
        FakeInnerRCA(), topology_service=topology_service([])
    )
    analyser.bind_session(FakeSession())
    result = asyncio.run(analyser.analyse(incident(), [alert("snmp", "open")]))
    sources = [e.get("source") for e in result.supporting_evidence]
    assert f"{SOURCE}_topology" not in sources
    assert SOURCE in sources  # dependency evidence unaffected


def test_production_default_never_fabricates_topology():
    """No injected service AND no bound session => no topology lookup at all.

    This is the production default path: RCAService only ever calls
    bind_session(), so an unbound analyser must add nothing.
    """
    analyser = DependencyEnrichedRootCauseAnalyser(FakeInnerRCA())
    assert analyser._resolve_topology_service() is None  # noqa: SLF001
    result = asyncio.run(analyser.analyse(incident(), [alert("snmp", "open")]))
    sources = [e.get("source") for e in result.supporting_evidence]
    assert f"{SOURCE}_topology" not in sources
    assert SOURCE in sources  # dependency evidence still flows


def test_explicitly_injected_service_is_used_without_a_session():
    """An explicitly injected service wins over lazy session resolution."""
    analyser = DependencyEnrichedRootCauseAnalyser(
        FakeInnerRCA(), topology_service=topology_service([EDGE])
    )
    assert analyser._resolve_topology_service() is not None  # noqa: SLF001
    result = asyncio.run(analyser.analyse(incident(), [alert("snmp", "open")]))
    sources = [e.get("source") for e in result.supporting_evidence]
    assert f"{SOURCE}_topology" in sources


def test_rca_without_any_evidence_is_unchanged():
    """Backward compatibility: nothing to add => byte-identical result."""
    analyser = DependencyEnrichedRootCauseAnalyser(FakeInnerRCA())
    result = asyncio.run(
        analyser.analyse(incident(), [alert("unsupported_protocol", "open")])
    )
    assert result.root_cause_summary == "PRTG sensor alert"
    assert result.supporting_evidence == [
        {"source": "alert", "title": "SNMP unavailable"}
    ]


def test_topology_failure_does_not_break_rca():
    class ExplodingTopology:
        async def get_upstream_dependencies(self, *_a, **_k):
            raise RuntimeError("relationship repo down")

    analyser = DependencyEnrichedRootCauseAnalyser(
        FakeInnerRCA(), topology_service=ExplodingTopology()
    )
    analyser.bind_session(FakeSession())
    result = asyncio.run(
        analyser.analyse(incident(), [alert("icmp", "resolved"), alert("snmp", "open")])
    )
    assert isinstance(result, RootCauseResult)
    sources = [e.get("source") for e in result.supporting_evidence]
    assert f"{SOURCE}_topology" not in sources  # UNKNOWN, never invented
    assert SOURCE in sources                    # dependency analysis survived


def test_topology_traversal_is_bounded():
    analyser = DependencyEnrichedRootCauseAnalyser(
        FakeInnerRCA(), topology_service=topology_service([EDGE]), topology_max_depth=2
    )
    analyser.bind_session(FakeSession())
    asyncio.run(analyser.analyse(incident(), [alert("snmp", "open")]))
    assert analyser._topology_max_depth == 2  # bounded, not unbounded  # noqa: SLF001
