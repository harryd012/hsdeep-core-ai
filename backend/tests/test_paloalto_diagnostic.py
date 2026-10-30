"""Palo Alto Diagnostic module tests (mocked providers; never live firewalls).

Covers: collector parsing/normalization, log correlation/dedupe, workflow
lifecycle, RCA attribution, runbook matching, safety policy, and CLI allowlist.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.services.paloalto.cli_reference import CLIReference
from app.services.paloalto.log_normalizer import LogNormalizer, NormalizedEvent
from app.services.paloalto.rca_engine import RCAEngine
from app.services.paloalto.runbooks import RunbookRegistry
from app.services.paloalto.safety import SafetyPolicy
from app.services.paloalto.workflow import (
    DIAGNOSTIC_STEPS,
    DiagnosticWorkflowEngine,
)
from app.schemas.paloalto import (
    DiagnosticExecutionCreate,
    DiagnosticRisk,
    DiagnosticStepStatus,
    EvidenceKind,
    EvidenceRef,
)
from app.collectors.paloalto import _parse_interfaces, _parse_xml


class _Src:
    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.hostname = kw.get("hostname", "pa-fw-01")
        self.protocol = kw.get("protocol", "https")
        self.port = kw.get("port", 443)
        self.username = kw.get("username", "admin")
        self.password = kw.get("password", "secret")
        self.api_key = kw.get("api_key", "")
        self.verify_ssl = kw.get("verify_ssl", True)
        self.ip_address = kw.get("ip_address", "10.0.0.1")


class _FakeCollector:
    def __init__(self, source):
        self.source = source


def _bind(fn, source):
    import types
    return types.MethodType(fn, _FakeCollector(source))


# ---------------------------------------------------------------------------
# Collector parsing: malformed / missing / partial provider data
# ---------------------------------------------------------------------------
class TestPaloAltoXMLParsing:
    def test_parse_empty_and_malformed_returns_empty(self):
        assert _parse_xml("") == {}
        assert _parse_xml("   ") == {}
        assert _parse_xml("<broken><unclosed") == {}

    def test_parse_system_info(self):
        xml = (
            "<response><result><system>"
            "<hostname>PA-220</hostname>"
            "<model>PA-220</model>"
            "<sw-version>11.1.2</sw-version>"
            "<serial-number>0123456789</serial-number>"
            "</system></result></response>"
        )
        out = _parse_xml(xml)
        assert out["hostname"] == "PA-220"
        assert out["sw-version"] == "11.1.2"
        assert out["serial-number"] == "0123456789"

    def test_parse_interfaces_ignores_non_iface_nodes(self):
        xml = "<response><result><hw><entry name='ethernet1/1'><state>up</state><link-status>up</link-status></entry></hw></result></response>"
        out = _parse_interfaces(xml, "dev-1")
        assert out == []

    def test_parse_interfaces_extracts_named(self):
        xml = (
            "<response><result>"
            "<ethernet name='ethernet1/1'><state>up</state><link-status>up</link-status></ethernet>"
            "<ethernet><name></name></ethernet>"
            "</result></response>"
        )
        out = _parse_interfaces(xml, "dev-1")
        assert len(out) == 1
        assert out[0].name == "ethernet1/1"
        assert out[0].device_external_id == "dev-1"


# ---------------------------------------------------------------------------
# Log normalizer: correlation, dedupe, clock skew, missing fields
# ---------------------------------------------------------------------------
def _mk_ev(**kw):
    base = dict(
        kind=EvidenceKind.TRAFFIC_LOG,
        tenant_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        provider_timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        collected_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        source_ip="10.0.0.5",
        dest_ip="93.184.216.34",
        dest_port=443,
        protocol="tcp",
        rule_action="deny",
        rule_name="block-out",
        raw={"type": "TRAFFIC"},
    )
    base.update(kw)
    return NormalizedEvent(**base)


class TestLogNormalizer:
    def test_normalize_rejects_untyped(self):
        n = LogNormalizer()
        assert n.normalize({}, tenant_id=uuid.uuid4()) is None
        assert n.normalize({"type": ""}, tenant_id=uuid.uuid4()) is None
        assert n.normalize("not-a-dict", tenant_id=uuid.uuid4()) is None

    def test_unkeyed_events_are_grouped_not_dropped(self):
        n = LogNormalizer()
        ev = _mk_ev(session_id=None, source_ip=None, dest_ip=None, dest_port=None)
        assert ev.flow_key is None
        groups = n.correlate([ev])
        assert ("unkeyed",) in groups

    def test_dedupe_keeps_first(self):
        n = LogNormalizer()
        a = _mk_ev(session_id="s1")
        b = _mk_ev(session_id="s1")
        assert len(n.dedupe([a, b])) == 1

    def test_clock_skew_flag(self):
        n = LogNormalizer()
        ok = _mk_ev()
        assert n.is_clock_skewed(ok) is False
        bad = _mk_ev(collected_at=datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc))
        assert n.is_clock_skewed(bad) is True

    def test_allow_is_not_proof(self):
        n = LogNormalizer()
        ev = _mk_ev(rule_action="allow", rule_name="allow-web")
        ref = n.to_evidence(ev)
        assert "ALLOW" in ref.summary
        assert ref.verified is True
        rca = RCAEngine().assess(uuid.uuid4(), [ref])
        assert rca.hypotheses[0].id == "insufficient_evidence"


def _mk_ref(kind=EvidenceKind.TRAFFIC_LOG, **kw):
    base = dict(
        kind=kind, summary="deny; rule=block-out",
        attributes={"rule_action": "deny"}, verified=True,
    )
    base.update(kw)
    return EvidenceRef(**base)


class TestWorkflowEngine:
    def _engine(self):
        return DiagnosticWorkflowEngine(safety_policy=SafetyPolicy())

    def test_new_execution_has_sixteen_steps(self):
        eng = self._engine()
        ex = eng.new_execution(uuid.uuid4(), DiagnosticExecutionCreate())
        assert len(ex.steps) == 16
        assert {s.id for s in ex.steps} == {s["id"] for s in DIAGNOSTIC_STEPS}
        assert ex.current_step_id == "incident_intake"

    @pytest.mark.asyncio
    async def test_failed_step_blocks_dependent(self):
        eng = self._engine()
        ex = eng.new_execution(uuid.uuid4(), DiagnosticExecutionCreate())
        ex = eng.mark_failed(ex, "traffic_flow_map", "no logs found")
        assert ex.status == DiagnosticStepStatus.FAILED
        assert ex.current_step_id == "traffic_flow_map"
        ex = await eng.run_step(ex, "traffic_log_investigation")
        dep = next(s for s in ex.steps if s.id == "traffic_log_investigation")
        assert dep.status == DiagnosticStepStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_resumption_keeps_passed_steps(self):
        eng = self._engine()
        ex = eng.new_execution(uuid.uuid4(), DiagnosticExecutionCreate())
        ex = await eng.run_step(ex, "incident_intake")
        first = next(s for s in ex.steps if s.id == "incident_intake")
        assert first.status == DiagnosticStepStatus.PASSED
        assert first.started_at is not None and first.finished_at is not None
        ex = eng.skip_step(ex, "traffic_flow_map", "out of scope for GP incident")
        skipped = next(s for s in ex.steps if s.id == "traffic_flow_map")
        assert skipped.status == DiagnosticStepStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_approval_step_blocked_without_grant(self):
        policy = SafetyPolicy()
        eng = DiagnosticWorkflowEngine(safety_policy=policy)
        ex = eng.new_execution(uuid.uuid4(), DiagnosticExecutionCreate())
        for sid in ("incident_intake", "traffic_flow_map", "traffic_log_investigation"):
            ex = await eng.run_step(ex, sid)
        ex = await eng.run_step(ex, "session_packet_capture")
        cap = next(s for s in ex.steps if s.id == "session_packet_capture")
        assert cap.status == DiagnosticStepStatus.BLOCKED
        policy.grant(ex.id, "session_packet_capture")
        cap._execution_id = str(ex.id)
        ex = await eng.run_step(ex, "session_packet_capture")
        assert cap.status == DiagnosticStepStatus.PASSED


class TestRCAEngine:
    def test_deny_attribution_with_confidence_basis(self):
        rca = RCAEngine().assess(uuid.uuid4(), [_mk_ref(), _mk_ref()])
        top = rca.hypotheses[0]
        assert top.id == "security_policy_deny"
        assert top.score_basis.value == "corroborated"
        assert len(top.supporting_evidence) == 2
        assert "security_policy_analysis" in (top.next_diagnostic_action or "")

    def test_single_source_never_near_certain(self):
        rca = RCAEngine().assess(uuid.uuid4(), [_mk_ref()])
        assert rca.hypotheses[0].confidence_score < 90.0

    def test_insufficient_when_no_evidence(self):
        rca = RCAEngine().assess(uuid.uuid4(), [])
        assert rca.hypotheses[0].id == "insufficient_evidence"
        assert rca.hypotheses[0].confidence_score == 0.0
        assert rca.ai_interpretation is None


class TestRunbookRegistry:
    def test_registry_has_ten_topics(self):
        reg = RunbookRegistry()
        assert len(reg.all()) == 10
        topics = {r.topic for r in reg.all()}
        assert {"internet", "application", "security_policy", "nat_routing",
                "threat_url", "tls_decryption", "globalprotect",
                "resource_ha", "interface_ha", "packet_capture"} <= topics

    def test_match_scores_and_sorts(self):
        reg = RunbookRegistry()
        ms = reg.match(["traffic denied", "policy block"])
        assert ms and ms[0].runbook.id == "security_policy_block"

    def test_search_and_get(self):
        reg = RunbookRegistry()
        assert reg.get("tls_decryption_failure") is not None
        assert any(r.id == "tls_decryption_failure" for r in reg.search("decrypt"))


class TestSafetyAndCLI:
    def test_read_only_allowed_for_engineer(self):
        from app.schemas.paloalto import DiagnosticStep
        pol = SafetyPolicy()
        first = next(s for s in DIAGNOSTIC_STEPS if s["id"] == "traffic_flow_map")
        step = DiagnosticStep(id=first["id"], name=first["name"], risk=first["risk"])
        assert pol.evaluate(step, "engineer").allowed is True

    def test_remediation_requires_approval(self):
        from app.schemas.paloalto import DiagnosticStep
        pol = SafetyPolicy()
        step = DiagnosticStep(id="remediation_planning", name="Remediation planning",
                              risk=DiagnosticRisk.REQUIRES_APPROVAL)
        assert pol.evaluate(step, "engineer").allowed is False

    def test_cli_allowlist_rejects_arbitrary(self):
        pol = SafetyPolicy()
        with pytest.raises(PermissionError):
            pol.assert_cli_allowed("delete everything", frozenset({"sys_info"}))

    def test_backend_safe_subset(self):
        ref = CLIReference()
        safe = ref.backend_safe()
        assert safe and all(c.diag_class.value == "backend_safe" for c in safe)
        assert ref.get("sys_info") is not None
        assert ref.get("nonexistent") is None
