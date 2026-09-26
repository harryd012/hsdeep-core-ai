"""
Focused tests for the bounded network probe (Phase 6.1 §8/§9).

Covers host discovery, protocol-state classification, the bounds that keep a
sweep safe, the no-persistence/no-secret safety rules, idempotency, and a
deterministic CONTENTION regression test for the Phase 6 live defect where a
64-host sweep produced false TCP timeouts.

The probe is pure (no DB, no persistence), so these tests use a mocked transport
and need no database.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services.discovery import network_probe as np

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

def _icmp(open_hosts: set[str], *, delay: float = 0.0, tracker=None):
    async def _fake(host: str, count: int, timeout: float) -> dict:
        if tracker is not None:
            tracker["icmp"]["in_flight"] += 1
            tracker["icmp"]["max"] = max(
                tracker["icmp"]["max"], tracker["icmp"]["in_flight"]
            )
        try:
            if delay:
                await asyncio.sleep(delay)
            ok = host in open_hosts
            return {
                "ok": ok,
                "latency_ms": 1.0 if ok else None,
                "packet_loss_pct": 0.0 if ok else 100.0,
            }
        finally:
            if tracker is not None:
                tracker["icmp"]["in_flight"] -= 1

    return _fake


def _tcp(open_ports: dict[str, set[int]], *, delay: float = 0.0, tracker=None):
    async def _fake(host: str, port: int, timeout: float) -> dict:
        if tracker is not None:
            t = tracker["tcp"]
            t["in_flight"] += 1
            t["max"] = max(t["max"], t["in_flight"])
            t["per_host"][host] = t["per_host"].get(host, 0) + 1
            t["max_per_host"] = max(t["max_per_host"], t["per_host"][host])
        try:
            if delay:
                await asyncio.sleep(delay)
            return {"ok": port in open_ports.get(host, set()), "port": port}
        finally:
            if tracker is not None:
                tracker["tcp"]["in_flight"] -= 1
                tracker["tcp"]["per_host"][host] -= 1

    return _fake


def _tracker() -> dict:
    return {
        "icmp": {"in_flight": 0, "max": 0},
        "tcp": {"in_flight": 0, "max": 0, "per_host": {}, "max_per_host": 0},
    }


class TestHostDiscovery:
    async def test_reachable_host_is_detected(self, monkeypatch):
        monkeypatch.setattr(np, "_icmp_result", _icmp({"10.0.0.1"}))
        monkeypatch.setattr(np, "_tcp_result", _tcp({}))
        report = await np.discover_network("10.0.0.0/29")
        assert [h.address for h in report.reachable] == ["10.0.0.1"]
        assert "icmp:reply" in report.reachable[0].evidence

    async def test_unreachable_hosts_are_not_reported(self, monkeypatch):
        """No ICMP reply and no TCP accept must NOT yield a reachable host."""
        monkeypatch.setattr(np, "_icmp_result", _icmp(set()))
        monkeypatch.setattr(np, "_tcp_result", _tcp({}))
        report = await np.discover_network("10.0.0.0/29")
        assert report.reachable == []
        assert report.hosts_probed == 6

    async def test_tcp_accepted_host_is_detected_without_icmp(self, monkeypatch):
        """A TCP accept is reachability evidence even when ICMP is blocked."""
        monkeypatch.setattr(np, "_icmp_result", _icmp(set()))
        monkeypatch.setattr(np, "_tcp_result", _tcp({"10.0.0.2": {443}}))
        report = await np.discover_network("10.0.0.0/29")
        host = next(h for h in report.hosts if h.address == "10.0.0.2")
        assert host.reachable is True
        assert host.protocols["https"] == np.STATE_REACHABLE
        assert "tcp/443:open" in host.evidence


class TestProtocolDetection:
    async def test_each_open_protocol_is_reachable(self, monkeypatch):
        ports = {22, 23, 80, 443}
        monkeypatch.setattr(np, "_icmp_result", _icmp({"10.0.0.1"}))
        monkeypatch.setattr(np, "_tcp_result", _tcp({"10.0.0.1": ports}))
        host = await np.probe_host("10.0.0.1")
        for name in ("ssh", "telnet", "http", "https"):
            assert host.protocols[name] == np.STATE_REACHABLE, name

    async def test_closed_port_is_timeout_not_reachable(self, monkeypatch):
        monkeypatch.setattr(np, "_icmp_result", _icmp({"10.0.0.1"}))
        monkeypatch.setattr(np, "_tcp_result", _tcp({"10.0.0.1": {22}}))
        host = await np.probe_host("10.0.0.1")
        assert host.protocols["ssh"] == np.STATE_REACHABLE
        assert host.protocols["rdp"] == np.STATE_TIMEOUT

    async def test_unprobeable_protocol_is_not_configured(self, monkeypatch):
        """SNMP is UDP and is never silently omitted."""
        monkeypatch.setattr(np, "_icmp_result", _icmp({"10.0.0.1"}))
        monkeypatch.setattr(np, "_tcp_result", _tcp({}))
        host = await np.probe_host("10.0.0.1")
        assert host.protocols["snmp"] == np.STATE_NOT_CONFIGURED

    async def test_probe_exception_does_not_abort_sweep(self, monkeypatch):
        monkeypatch.setattr(np, "_icmp_result", _icmp({"10.0.0.1"}))

        async def _boom(host, port, timeout):
            raise OSError("socket exhaustion")

        monkeypatch.setattr(np, "_tcp_result", _boom)
        report = await np.discover_network("10.0.0.0/29")
        assert report.hosts_probed == 6
        host = next(h for h in report.hosts if h.address == "10.0.0.1")
        assert host.protocols["ssh"] == np.STATE_TIMEOUT


class TestBounds:
    async def test_max_hosts_is_enforced(self, monkeypatch):
        monkeypatch.setattr(np, "_icmp_result", _icmp(set()))
        monkeypatch.setattr(np, "_tcp_result", _tcp({}))
        report = await np.discover_network("192.168.159.0/24", max_hosts=10)
        assert report.hosts_probed == 10
        assert report.hosts_total == 254
        assert report.truncated is True

    async def test_usable_hosts_exclude_network_and_broadcast(self):
        hosts, total, truncated = np.hosts_in_cidr("10.0.0.0/29")
        assert hosts == [
            "10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5", "10.0.0.6",
        ]
        assert total == 6
        assert truncated is False

    async def test_point_to_point_prefix_is_not_empty(self):
        hosts, _total, _t = np.hosts_in_cidr("10.10.13.0/30")
        assert hosts == ["10.10.13.1", "10.10.13.2"]

    async def test_host_concurrency_is_bounded(self, monkeypatch):
        tracker = _tracker()
        monkeypatch.setattr(np, "_icmp_result", _icmp(set(), delay=0.01, tracker=tracker))
        monkeypatch.setattr(np, "_tcp_result", _tcp({}, delay=0.01, tracker=tracker))
        await np.discover_network("10.0.0.0/24", max_concurrency=4)
        assert tracker["icmp"]["max"] <= 4, tracker["icmp"]

    async def test_timeout_is_passed_through_to_the_transport(self, monkeypatch):
        seen: list[float] = []

        async def _icmp_rec(host, count, timeout):
            seen.append(timeout)
            return {"ok": False}

        async def _tcp_rec(host, port, timeout):
            seen.append(timeout)
            return {"ok": False}

        monkeypatch.setattr(np, "_icmp_result", _icmp_rec)
        monkeypatch.setattr(np, "_tcp_result", _tcp_rec)
        await np.discover_network("10.0.0.0/30", timeout=2.5, max_hosts=1)
        assert seen and all(t == 2.5 for t in seen), seen


class TestSafety:
    async def test_probe_persists_nothing(self):
        """The probe module must not BIND any persistence collaborator.

        Checked against the module's actual namespace (imported symbols and
        definitions) rather than its source text, so documentation prose that
        merely names the downstream persistence path cannot mask a real
        dependency.
        """
        bound = set(vars(np))
        for forbidden in (
            "AsyncSessionLocal",
            "get_db",
            "DeviceRepository",
            "InterfaceRepository",
            "ResourceRelationship",
            "BundleSaver",
            "TaskRepository",
        ):
            assert forbidden not in bound, f"probe must not bind {forbidden}"

        # And it must not reach the database layer at all.
        module_imports = {
            name for name, obj in vars(np).items() if getattr(obj, "__module__", "") == "app.core.db"
        }
        assert module_imports == set(), module_imports

    async def test_results_contain_no_secret_material(self, monkeypatch):
        monkeypatch.setattr(np, "_icmp_result", _icmp({"10.0.0.1"}))
        monkeypatch.setattr(np, "_tcp_result", _tcp({"10.0.0.1": {22, 443}}))
        report = await np.discover_network("10.0.0.0/29")
        blob = json.dumps(
            {
                "cidr": report.cidr,
                "hosts": [
                    {"a": h.address, "p": h.protocols, "e": h.evidence} for h in report.hosts
                ],
            }
        ).lower()
        for secret in ("community", "password", "passwd", "secret", "token", "private_key"):
            assert secret not in blob


class TestIdempotency:
    async def test_repeated_sweep_is_stable(self, monkeypatch):
        monkeypatch.setattr(np, "_icmp_result", _icmp({"10.0.0.1", "10.0.0.3"}))
        monkeypatch.setattr(np, "_tcp_result", _tcp({"10.0.0.5": {80}}))
        first = await np.discover_network("10.0.0.0/29")
        second = await np.discover_network("10.0.0.0/29")
        assert [h.address for h in first.reachable] == ["10.0.0.1", "10.0.0.3", "10.0.0.5"]
        assert [h.address for h in second.reachable] == [
            h.address for h in first.reachable
        ]


class TestContentionRegression:
    """The Phase 6 live defect: high concurrency caused false TCP timeouts.

    Under the old implementation every protocol probe for a host ran
    concurrently, so a 64-host sweep issued hundreds of simultaneous connects
    and Windows dropped them, which was recorded as `timeout`. These tests use a
    transport that tracks in-flight probes and prove the caps hold and that no
    probe is lost to contention.
    """

    async def test_tcp_concurrency_is_capped_globally(self, monkeypatch):
        tracker = _tracker()
        ports = {22, 23, 80, 443}
        hosts = {f"10.0.0.{i}": ports for i in range(1, 41)}
        monkeypatch.setattr(np, "_icmp_result", _icmp(set(), delay=0.005, tracker=tracker))
        monkeypatch.setattr(np, "_tcp_result", _tcp(hosts, delay=0.005, tracker=tracker))

        await np.discover_network(
            "10.0.0.0/26", max_concurrency=16, max_tcp_concurrency=4
        )

        assert tracker["tcp"]["max"] <= 4, tracker["tcp"]
        # Per-host serialization: one host must never have two connects at once.
        assert tracker["tcp"]["max_per_host"] == 1, tracker["tcp"]

    async def test_no_false_timeouts_under_concurrent_sweep(self, monkeypatch):
        """Every open port must still classify as reachable when hosts overlap."""
        ports = {22, 23, 80, 443}
        hosts = {f"10.0.0.{i}" for i in range(1, 41)}
        monkeypatch.setattr(np, "_icmp_result", _icmp(hosts))
        monkeypatch.setattr(np, "_tcp_result", _tcp({h: ports for h in hosts}, delay=0.005))

        report = await np.discover_network(
            "10.0.0.0/26", max_concurrency=16, max_tcp_concurrency=4
        )

        assert len(report.reachable) == 40
        for host in report.reachable:
            for name in ("ssh", "telnet", "http", "https"):
                assert host.protocols[name] == np.STATE_REACHABLE, (host.address, name)

    async def test_tracker_detects_unsynchronized_probes(self, monkeypatch):
        """Guard the guard: the tracker must catch the OLD concurrent behaviour.

        Without this, the two tests above could pass vacuously.
        """
        tracker = _tracker()
        fake_tcp = _tcp({"10.0.0.1": {22, 23, 80, 443}}, delay=0.01, tracker=tracker)
        monkeypatch.setattr(np, "_tcp_result", fake_tcp)

        result = np.HostProbeResult(address="10.0.0.1")
        # Reproduce the OLD behaviour: all protocols at once for one host.
        await asyncio.gather(
            *(
                np._probe_protocol("10.0.0.1", name, port, 1.0, result)
                for name, port in np.PROTOCOL_PORTS.items()
            )
        )

        assert tracker["tcp"]["max_per_host"] > 1
