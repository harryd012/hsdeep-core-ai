"""Probe-layer tests (server-side discovery probes).

Probes are tested against LOCAL/loopback conditions and reserved unroutable
addresses only — nothing here asserts on a device that may not exist.
"""
from __future__ import annotations

import socket

from app.services.discovery.probes import (
    DEFAULT_SNMP_OIDS,
    IDENTITY_PORTS,
    OUTCOME_NO_RESPONSE,
    OUTCOME_SUCCESS,
    ProbeBundle,
    ProbeResult,
    expand_targets,
    icmp_probe,
    probe_target,
    snmp_probe,
    tcp_probe,
)


class TestProbeResultContract:
    def test_success_flag_tracks_outcome(self):
        assert ProbeResult("icmp", "1.1.1.1", OUTCOME_SUCCESS).succeeded is True
        assert ProbeResult("icmp", "1.1.1.1", OUTCOME_NO_RESPONSE).succeeded is False

    def test_as_dict_shape(self):
        r = ProbeResult("snmp", "1.1.1.1", OUTCOME_SUCCESS, "", {"sysName": "x"})
        payload = r.as_dict()
        assert payload["protocol"] == "snmp"
        assert payload["outcome"] == OUTCOME_SUCCESS
        assert payload["values"] == {"sysName": "x"}

    def test_bundle_reports_outcome_per_protocol(self):
        bundle = ProbeBundle(target="1.1.1.1")
        bundle.results = [
            ProbeResult("icmp", "1.1.1.1", OUTCOME_SUCCESS),
            ProbeResult("snmp", "1.1.1.1", "timeout"),
        ]
        assert bundle.as_dict()["probe_results"] == {
            "icmp": "success", "snmp": "timeout"
        }


class TestTargetExpansion:
    def test_single_address(self):
        assert expand_targets("192.168.1.10") == ["192.168.1.10"]

    def test_hostname_passes_through(self):
        assert expand_targets("router.example.com") == ["router.example.com"]

    def test_cidr_is_expanded_and_bounded(self):
        hosts = expand_targets("10.0.0.0/29")
        assert hosts == [
            "10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5", "10.0.0.6",
        ]
        # A large range must not produce an unbounded probe burst.
        assert len(expand_targets("10.0.0.0/16")) == 256

    def test_empty_target_yields_nothing(self):
        assert expand_targets("") == []
        assert expand_targets("   ") == []


class TestTcpProbe:
    def test_open_port_is_success(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert tcp_probe("127.0.0.1", port, timeout=2.0).outcome == OUTCOME_SUCCESS
        finally:
            server.close()

    def test_closed_port_is_not_success(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()
        assert tcp_probe("127.0.0.1", free_port, timeout=1.0).outcome != OUTCOME_SUCCESS


class TestIcmpProbe:
    def test_loopback_responds(self):
        assert icmp_probe("127.0.0.1", timeout_seconds=3).outcome == OUTCOME_SUCCESS

    def test_reserved_unroutable_target_is_not_success(self):
        # TEST-NET-1 (RFC 5737) is reserved and must not answer.
        assert icmp_probe("192.0.2.1", timeout_seconds=1).outcome != OUTCOME_SUCCESS


class TestSnmpProbeUsesUdp:
    def test_snmp_probe_never_uses_tcp(self):
        """SNMP is UDP/161: the probe must not gate on a TCP port state."""
        import inspect

        src = inspect.getsource(snmp_probe)
        assert "UdpTransportTarget" in src
        assert "create_connection" not in src  # no TCP socket usage at all

    async def test_unreachable_target_reports_a_non_success_state(self):
        # NOTE: awaited directly rather than via asyncio.run() — the suite uses
        # session-scoped event loops (pyproject asyncio_default_test_loop_scope),
        # and asyncio.run() clears the current loop on exit, which would break
        # every async test that runs afterwards.
        result = await snmp_probe("192.0.2.1", community="public", timeout=1, retries=0)
        assert result.outcome != OUTCOME_SUCCESS
        assert result.outcome in ("timeout", "no_response", "network_unreachable")

    def test_oid_set_includes_identity_evidence(self):
        for required in ("sysDescr", "sysObjectID", "sysName"):
            assert required in DEFAULT_SNMP_OIDS


class TestProbeTarget:
    async def test_icmp_failure_skips_deeper_probes(self):
        """A dead target must not spend time on SNMP/TCP timeouts."""
        bundle = await probe_target(
            "192.0.2.1", protocols=["icmp", "snmp", "https", "ssh"]
        )
        assert {r.protocol for r in bundle.results} == {"icmp"}
        assert bundle.evidence.reachable is False
        assert bundle.evidence.capabilities == []

    async def test_evidence_carries_only_observed_capabilities(self):
        bundle = await probe_target("127.0.0.1", protocols=["icmp"])
        assert bundle.evidence.reachable is True
        assert "icmp" in bundle.evidence.capabilities
        # SNMP was not requested, so it must never be claimed.
        assert "snmp" not in bundle.evidence.capabilities

    def test_identity_ports_are_the_evidence_bearing_services(self):
        assert (22, "ssh") in IDENTITY_PORTS
        assert (443, "api") in IDENTITY_PORTS

