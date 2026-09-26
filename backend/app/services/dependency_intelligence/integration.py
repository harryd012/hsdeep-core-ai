"""
Dependency Intelligence V1.1 - integration with the EXISTING HSDEEP pipeline.

Wiring only. The V1 engine (taxonomy/graph/models/analysis) is not rebuilt.

Real contracts targeted (read, not guessed):

* RCA - ``app/services/rca_service.py``:
    ``RootCauseResult(root_cause_summary, supporting_evidence: list[dict],
    recommended_sop_id, recommended_sop_name, requires_approval)``
    ``RootCauseAnalyser`` Protocol: ``analyse(incident, alerts) -> RootCauseResult``
  NOTE: ``schemas/correlation.py::RootCauseCandidate`` belongs to the *correlation*
  engine (event_id/title/severity/selection_reason) and has no
  cause/confidence/explanation fields, so findings are NOT forced into it.

* Recommendation - no separate Resolution Recommendation engine exists here.
  ``sop/recommendation_engine.py::SOPRecommendationEngine`` IS the recommendation
  mechanism. ``SOPMatchResult``/``IncidentContext`` expose no dependency
  dimension, so SOP *scoring* is not modified; context is attached additively to
  the returned ``reasoning`` dict.

* Topology - ``topology_service.py::TopologyService.get_upstream_dependencies(
  device_id, tenant_id, max_depth)``, already tenant-scoped at the repository layer.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.dependency_intelligence.analysis import (
    DependencyIntelligenceAnalyzer,
    normalize_capability,
)
from app.services.dependency_intelligence.models import (
    DependencyAnalysis,
    Evidence,
    Finding,
    coerce_state,
)

logger = logging.getLogger(__name__)

SOURCE = "dependency_intelligence"

#: capability -> failure scope, for deterministic SOP identifiers.
_FAILURE_SCOPE: dict[str, str] = {
    "snmp": "monitoring_access",
    "https": "application_access",
    "bgp": "routing",
    "ospf": "routing",
    "dns": "name_resolution",
    "icmp": "reachability",
    "tls": "transport_security",
    "radius": "authentication",
}

#: "UDP/161" or "udp_161" -> ("UDP", "161")
_PORT_TOKEN = re.compile(r"\b([A-Za-z]{3,4})[/_\-](\d{1,5})\b")

#: Alert category -> taxonomy capability. Absent categories are skipped.
_ALERT_CATEGORY_TO_CAPABILITY: dict[str, str] = {
    "snmp": "snmp",
    "icmp": "icmp",
    "ping": "icmp",
    "availability": "icmp",
    "http": "http",
    "https": "https",
    "web": "https",
    "tls": "tls",
    "dns": "dns",
    "bgp": "bgp",
    "ospf": "ospf",
    "routing": "bgp",
    "ssh": "ssh",
    "radius": "radius",
}


def dependency_identifiers(finding: Finding) -> list[str]:
    """Deterministic ``protocol_port`` identifiers, e.g. "udp_161". Pure string work."""
    found: list[str] = []
    for text in list(finding.candidate_causes) + list(finding.suggested_checks):
        for proto, port in _PORT_TOKEN.findall(str(text)):
            ident = f"{proto.lower()}_{port}"
            if ident not in found:
                found.append(ident)
    return found


def failure_scope(capability: str) -> str:
    return _FAILURE_SCOPE.get(normalize_capability(capability), "capability_specific")


class DependencyIntelligenceRCAAdapter:
    """DependencyAnalysis -> the EXISTING RCA contract.

    Emits structured dicts only (never an LLM paragraph). ``supporting_evidence``
    is already ``list[dict[str, Any]]``, so no schema change and no new fields.
    """

    def __init__(self, analyzer: DependencyIntelligenceAnalyzer | None = None) -> None:
        self.analyzer = analyzer or DependencyIntelligenceAnalyzer()

    def to_supporting_evidence(self, analysis: DependencyAnalysis) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for finding in analysis.findings:
            evidence.append(
                {
                    "source": SOURCE,
                    "resource_id": analysis.resource_id,
                    "capability": finding.capability,
                    "state": finding.state.value,
                    "confidence": finding.confidence.value,
                    "candidate_causes": list(finding.candidate_causes),
                    "suggested_checks": list(finding.suggested_checks),
                    "healthy_dependencies": list(finding.healthy_dependencies),
                    "failed_dependencies": list(finding.failed_dependencies),
                    "unknown_dependencies": list(finding.unknown_dependencies),
                    "excluded_conclusions": list(finding.excluded_conclusions),
                    "affected_capabilities": list(finding.affected_capabilities),
                    "dependency_identifiers": dependency_identifiers(finding),
                    "failure_scope": failure_scope(finding.capability),
                }
            )
        return evidence

    def to_summary_fragment(self, analysis: DependencyAnalysis) -> str | None:
        """One deterministic sentence. Rule-based assembly, not a narrative."""
        if not analysis.findings:
            return None
        parts: list[str] = []
        for finding in analysis.findings:
            idents = dependency_identifiers(finding)
            target = ", ".join(idents) if idents else finding.capability
            clause = f"{finding.capability.upper()} failure isolated to {target}"
            if finding.healthy_dependencies:
                clause += f" (healthy: {', '.join(finding.healthy_dependencies)})"
            parts.append(clause)
        return "Dependency intelligence: " + "; ".join(parts) + "."

    def apply_to_summary(self, summary: str, analysis: DependencyAnalysis) -> str:
        """Backward-compatible enrichment of the existing summary text."""
        fragment = self.to_summary_fragment(analysis)
        if not fragment:
            return summary
        return f"{summary} {fragment}".strip() if summary else fragment


#: Real ``Alert.status`` vocabulary from ``app/models/alert.py``:
#: "open | acknowledged | resolved" (default "open").
#: An 'active' alert is open or acknowledged - i.e. not yet resolved.
#: ``Alert.severity`` ("critical | warning | info") is DELIBERATELY not used to
#: derive evidence state: severity and state are different concepts, so
#: severity=critical + status=resolved is PASS, never FAIL. Severity is carried
#: as supporting metadata only.
_ACTIVE_ALERT_STATUSES = frozenset({"open", "acknowledged"})
_RESOLVED_ALERT_STATUSES = frozenset({"resolved"})


def alert_state(status: Any) -> Any:
    """Map the REAL alert status vocabulary onto an evidence state.

    Fail-closed: any status outside the real vocabulary (including None, empty,
    or a severity value like "critical") becomes UNKNOWN - never PASS.
    """
    status_text = str(status or "").strip().lower()
    if status_text in _ACTIVE_ALERT_STATUSES:
        return coerce_state("FAIL")
    if status_text in _RESOLVED_ALERT_STATUSES:
        return coerce_state("PASS")
    return coerce_state("UNKNOWN")


def evidence_from_alerts(alerts: list[Any]) -> list[Evidence]:
    """Derive dependency evidence from EXISTING alert rows - no extra queries.

    Reads only ``category`` (-> capability), ``status`` (-> state) and
    ``severity`` (-> supporting metadata) via ``getattr``, so it works against the
    real Alert model without importing or changing it. Unrecognised categories are
    SKIPPED, never guessed.
    """
    evidence: list[Evidence] = []
    for alert in alerts or []:
        category = getattr(alert, "category", None)
        if not category:
            continue
        capability = _ALERT_CATEGORY_TO_CAPABILITY.get(str(category).strip().lower())
        if capability is None:
            continue
        severity = getattr(alert, "severity", None)
        details = str(getattr(alert, "title", "") or "") or None
        if severity:
            details = f"{details or 'alert'} (severity={severity})"
        evidence.append(
            Evidence.of(
                capability,
                alert_state(getattr(alert, "status", None)),
                source="alert",
                resource_id=str(getattr(alert, "device_id", "") or "") or None,
                details=details,
            )
        )
    return evidence


class DependencyTopologyAdapter:
    """Consumes EXISTING topology via the existing service. Never invents edges.

    Delegates entirely to ``TopologyService.get_upstream_dependencies`` (already
    tenant-scoped at the repository layer and bounded by ``max_depth``). Returns
    UNKNOWN - never a fabricated chain - when the service or relationships are
    unavailable, per the requirement to never invent
    firewall/router/switch/interface relationships.
    """

    def __init__(self, topology_service: Any | None = None) -> None:
        self.topology = topology_service

    async def dependency_context(
        self, device_id: Any | None, tenant_id: Any, *, max_depth: int = 3
    ) -> dict[str, Any]:
        if self.topology is None or device_id is None:
            return {
                "available": False,
                "state": "UNKNOWN",
                "dependencies": [],
                "reason": "no topology service or device id supplied",
            }
        try:
            chain = await self.topology.get_upstream_dependencies(
                device_id, tenant_id, max_depth=max_depth
            )
        except Exception as exc:  # noqa: BLE001 - degrade, never break analysis
            logger.warning("topology lookup failed: %s", exc)
            return {
                "available": False,
                "state": "UNKNOWN",
                "dependencies": [],
                "reason": f"topology lookup failed: {type(exc).__name__}",
            }
        if not chain:
            return {
                "available": False,
                "state": "UNKNOWN",
                "dependencies": [],
                "reason": "no stored topology relationships for this device",
            }
        # Only real stored relationship_type values are carried; device roles such
        # as firewall/router/switch are never inferred.
        return {
            "available": True,
            "state": "PASS",
            "reason": None,
            "dependencies": [
                {
                    "device_id": str(edge.get("dependency_device_id")),
                    "name": edge.get("dependency_device_name"),
                    "relationship_type": edge.get("relationship_type"),
                    "depth": edge.get("depth"),
                }
                for edge in chain
            ],
        }


class DependencySOPContextAdapter:
    """DependencyAnalysis -> deterministic SOP matching context.

    The existing ``SOPMatcher`` scores ``SOP.category`` against the incident's
    root-cause category plus vendor/platform/device_type/site/business_service/
    region/min_priority. It has NO dependency dimension, so this adapter does not
    alter scoring - it exposes deterministic identifiers
    (``capability`` / ``dependency_identifiers`` / ``failure_scope``) that an
    existing SOP can later match on, and attaches them additively to
    ``SOPRecommendation.reasoning`` (already a free-form dict). No SOP records are
    created and no match is fabricated.
    """

    def to_sop_context(self, analysis: DependencyAnalysis) -> dict[str, Any]:
        findings = [
            {
                "capability": finding.capability,
                "dependency_identifiers": dependency_identifiers(finding),
                "failure_scope": failure_scope(finding.capability),
                "confidence": finding.confidence.value,
            }
            for finding in analysis.findings
        ]
        return {
            "source": SOURCE,
            "resource_id": analysis.resource_id,
            "findings": findings,
            "match_keys": [f"{f['capability']}:{f['failure_scope']}" for f in findings],
        }

    def enrich_recommendations(
        self, recommendations: list[Any], analysis: DependencyAnalysis
    ) -> list[Any]:
        """Attach dependency context to existing recommendations, additively.

        Returns the SAME objects with every scored field unchanged
        (confidence_score, matched_criteria, business_impact_score, ...).
        """
        context = self.to_sop_context(analysis)
        if not context["findings"]:
            return recommendations
        for recommendation in recommendations:
            reasoning = getattr(recommendation, "reasoning", None)
            if isinstance(reasoning, dict):
                reasoning["dependency_context"] = context
        return recommendations


class DependencyEnrichedRootCauseAnalyser:
    """Implements the existing ``RootCauseAnalyser`` Protocol.

    Wraps the EXISTING analyser (e.g. ``RuleBasedRootCauseAnalyser``) and adds
    dependency findings to the ``RootCauseResult`` the existing workflow already
    consumes. Existing RCA logic is not replaced or rewritten.

    Graceful degradation: if dependency analysis fails the inner RCA result is
    returned untouched; if the inner RCA fails,
    ``analyse_dependencies_only`` still yields a DependencyAnalysis.
    """

    def __init__(
        self,
        inner: Any,
        *,
        analyzer: DependencyIntelligenceAnalyzer | None = None,
        adapter: DependencyIntelligenceRCAAdapter | None = None,
        topology_service: Any | None = None,
        topology_max_depth: int = 3,
    ) -> None:
        self.inner = inner
        self.analyzer = analyzer or DependencyIntelligenceAnalyzer()
        self.adapter = adapter or DependencyIntelligenceRCAAdapter(self.analyzer)
        # Topology: an explicit service wins; otherwise a real TopologyService is
        # built lazily from the session bound by the owning service (see
        # bind_session). No session bound => topology stays UNKNOWN, never invented.
        self._topology_service = topology_service
        self._topology_max_depth = topology_max_depth
        self._session: Any | None = None

    def bind_session(self, session: Any) -> None:
        """Bind the caller's existing AsyncSession for topology lookups.

        Additive opt-in hook: ``RCAService`` calls this with the session it
        already owns, so no new engine/sessionmaker is created. Also propagated
        to the wrapped engine, which uses it to build the real SOP
        recommendation engine on that same session.
        """
        self._session = session
        binder = getattr(type(self.inner), "bind_session", None)
        if callable(binder):
            binder(self.inner, session)

    def _resolve_topology_service(self) -> Any | None:
        if self._topology_service is not None:
            return self._topology_service
        if self._session is None:
            return None
        # Imported lazily so this module stays import-light and cycle-free.
        from app.services.topology_service import TopologyService

        return TopologyService(self._session)

    async def _topology_entry(self, incident: Any) -> dict[str, Any] | None:
        """Real topology as an additive RCA evidence entry, or None.

        Returns None when topology is unavailable (no session/service, no stored
        relationships, or a lookup failure) - UNKNOWN is reported by the adapter
        itself and is deliberately NOT injected into RCA evidence.
        """
        service = self._resolve_topology_service()
        if service is None:
            return None
        context = await DependencyTopologyAdapter(service).dependency_context(
            getattr(incident, "device_id", None),
            getattr(incident, "tenant_id", None),
            max_depth=self._topology_max_depth,
        )
        if not context.get("available"):
            return None
        return {
            "source": f"{SOURCE}_topology",
            "resource_id": str(getattr(incident, "device_id", "") or "") or None,
            "state": context["state"],
            "dependencies": context["dependencies"],
        }

    def analyze_alerts(self, incident: Any, alerts: list[Any]) -> DependencyAnalysis:
        """Analyse evidence already loaded for this incident (no new queries)."""
        return self.analyzer.analyze(
            str(getattr(incident, "device_id", "") or "") or None,
            evidence_from_alerts(alerts),
        )

    async def analyse(self, incident: Any, alerts: list[Any]) -> Any:
        result = await self.inner.analyse(incident, alerts)
        try:
            analysis = self.analyze_alerts(incident, alerts)
            topology_entry = await self._topology_entry(incident)
        except Exception as exc:  # noqa: BLE001 - RCA result must survive
            logger.warning("dependency intelligence unavailable: %s", exc)
            return result
        extra = (
            self.adapter.to_supporting_evidence(analysis) if analysis.findings else []
        )
        if topology_entry is not None:
            extra.append(topology_entry)
        if analysis.findings:
            # RCA-derived SOP matching context, preserved OUTWARD (persisted on
            # the Incident via supporting_evidence) so the recommendation /
            # runbook layer's deterministic identifiers are not lost. Nothing is
            # attached to recommendation objects the caller discards.
            sop_context = dict(DependencySOPContextAdapter().to_sop_context(analysis))
            sop_context["source"] = f"{SOURCE}_sop_context"
            extra.append(sop_context)
        if not extra:
            return result  # additive: nothing to add => existing output unchanged
        result.supporting_evidence = list(result.supporting_evidence) + extra
        if analysis.findings:
            result.root_cause_summary = self.adapter.apply_to_summary(
                result.root_cause_summary, analysis
            )
        return result

    async def analyse_dependencies_only(
        self, incident: Any, alerts: list[Any]
    ) -> DependencyAnalysis | None:
        """Used when RCA is unavailable so the layer still degrades usefully."""
        try:
            return self.analyze_alerts(incident, alerts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dependency intelligence unavailable: %s", exc)
            return None


async def analyse_incident_context(
    incident: Any,
    alerts: list[Any],
    *,
    topology_service: Any | None = None,
    analyzer: DependencyIntelligenceAnalyzer | None = None,
    max_depth: int = 3,
) -> dict[str, Any]:
    """Incident -> Resource -> Evidence -> Topology -> Dependency analysis.

    Reuses the incident's ALREADY-LOADED alerts (no N+1) and the existing
    tenant-scoped topology service. At most one bounded topology call per incident.
    """
    analyzer = analyzer or DependencyIntelligenceAnalyzer()
    resource_id = str(getattr(incident, "device_id", "") or "") or None
    analysis = analyzer.analyze(resource_id, evidence_from_alerts(alerts))
    topology = await DependencyTopologyAdapter(topology_service).dependency_context(
        getattr(incident, "device_id", None),
        getattr(incident, "tenant_id", None),
        max_depth=max_depth,
    )
    return {
        "analysis": analysis.to_dict(),
        "topology": topology,
        "sop_context": DependencySOPContextAdapter().to_sop_context(analysis),
    }
