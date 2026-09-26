"""Cisco per-process SNMP provider tests (Phase 3).

Cisco's per-process CPU/memory counters live in
CISCO-PROCESS-MIB::cpmProcessExtRevTable, a composite-indexed table
(INDEX {cpmCPUTotalIndex, cpmProcessPID}) whose columns are read-only
Gauge32 values: cpmProcExtUtil5SecRev/1MinRev/5MinRev (percent) and
cpmProcExtMemAllocatedRev/MemFreedRev (bytes). cpmProcessName (from the
sibling cpmProcessTable) supplies the label.

OIDs below are verified against the LibreNMS CISCO-PROCESS-MIB source
at the time of writing; they are the ONLY thing that should ever be
changed here if a device's agent reports different OID arcs.

No SSH/CLI/Netconf/DNA/Central - this is SNMP-only, reusing the single
SNMPCollector transport via VendorSnmpHealthProvider (no duplicated
credential/timeout/error logic).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.actions  # noqa: F401 - registers the built-in providers
from app.actions.base import (
    ACTION_CATALOG,
    IMPLEMENTED_ACTIONS,
    ActionNotSupported,
    ActionProviderRegistry,
    DeviceActionType,
)
from app.actions.providers.cisco import (
    CiscoActionProvider,
    _OID_PROC_MEM_ALLOC_REV,
    _OID_PROC_MEM_FREED_REV,
    _OID_PROC_NAME,
    _OID_PROC_UTIL_1MIN_REV,
    _OID_PROC_UTIL_5MIN_REV,
    _OID_PROC_UTIL_5SEC_REV,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


_WALK: dict[str, list[tuple[str, str]]] = {}


def _reset():
    _WALK.clear()


def _set_walk(oid: str, rows: list[tuple[str, str]]) -> None:
    _WALK[oid] = rows


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


@pytest.fixture
def cisco():
    return CiscoActionProvider()


@pytest.fixture
def collector():
    async def _walk(oid):
        return _WALK.get(oid, [])

    return SimpleNamespace(_walk=_walk)


@pytest.fixture
def target():
    return SimpleNamespace(
        device_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        connection=SimpleNamespace(
            host="10.0.0.1",
            port=161,
            version=2,
            community="public",
            username=None,
            auth_proto=None,
            auth_pass=None,
            priv_proto=None,
            priv_pass=None,
        ),
    )


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


class TestCiscoRegistration:
    def test_provider_is_registered(self, cisco):
        cls = ActionProviderRegistry.get("vendor:cisco")
        assert cls is CiscoActionProvider
        assert cls.provider_kind == "vendor:cisco"
        assert cls.display_name == "Cisco IOS (SNMP)"
        assert cls.implemented is True

    def test_supported_actions_include_health_and_processes(self, cisco):
        supported = cisco.supported_actions()
        assert DeviceActionType.CISCO_QUERY_HEALTH in supported
        assert DeviceActionType.CISCO_QUERY_PROCESSES in supported
        assert len(supported) == 2

    async def test_unsupported_action_is_denied(self, cisco, target):
        with pytest.raises(ActionNotSupported, match="does not support action=PING"):
            await cisco.execute(DeviceActionType.PING, target)

    def test_catalog_has_processes_entry(self):
        spec = ACTION_CATALOG[DeviceActionType.CISCO_QUERY_PROCESSES]
        assert spec.action is DeviceActionType.CISCO_QUERY_PROCESSES
        assert spec.classification.value == "READ_ONLY"
        assert spec.provider_kind == "vendor:cisco"
        assert spec.required_capability == "supports_snmp"
        assert "cpmProcessExtRevTable" in spec.description

    def test_implemented_actions_includes_processes(self):
        assert DeviceActionType.CISCO_QUERY_PROCESSES in IMPLEMENTED_ACTIONS


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


class TestCiscoProcessesHappy:
    async def test_single_process_all_fields(self, cisco, collector, target):
        _set_walk(
            _OID_PROC_UTIL_5SEC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.5.1.100", "42")],
        )
        _set_walk(
            _OID_PROC_UTIL_1MIN_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.6.1.100", "38")],
        )
        _set_walk(
            _OID_PROC_UTIL_5MIN_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.7.1.100", "35")],
        )
        _set_walk(
            _OID_PROC_MEM_ALLOC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.1.1.100", "1048576")],
        )
        _set_walk(
            _OID_PROC_MEM_FREED_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.2.1.100", "204800")],
        )
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        assert result["process_count"] == 1
        p = result["processes"][0]
        assert p["index"] == "1"
        assert p["pid"] == "100"
        assert p["cpu_5sec_pct"] == 42
        assert p["cpu_1min_pct"] == 38
        assert p["cpu_5min_pct"] == 35
        assert p["mem_allocated_bytes"] == 1048576
        assert p["mem_freed_bytes"] == 204800
        assert p["mem_net_bytes"] == 843776
        assert p['mem_net_pct_of_allocated'] == pytest.approx(80.5)

    async def test_named_process_label(self, cisco, collector, target):
        _set_walk(
            _OID_PROC_UTIL_5SEC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.5.2.200", "10")],
        )
        _set_walk(
            _OID_PROC_NAME,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.2.2.200", "snmpd")],
        )
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        p = result["processes"][0]
        assert p["name"] == "snmpd"
        assert p["pid"] == "200"
        assert p["index"] == "2"

    async def test_memory_net_calculation(self, cisco, collector, target):
        _set_walk(
            _OID_PROC_MEM_ALLOC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.1.1.100", "1048576")],
        )
        _set_walk(
            _OID_PROC_MEM_FREED_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.2.1.100", "204800")],
        )
        _set_walk(
            _OID_PROC_UTIL_5SEC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.5.1.100", "5")],
        )
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        p = result["processes"][0]
        assert p["mem_net_bytes"] == 843776
        assert p["mem_net_pct_of_allocated"] == pytest.approx(80.5)

    async def test_memory_net_pct_skipped_when_allocated_zero(
        self, cisco, collector, target,
    ):
        _set_walk(
            _OID_PROC_MEM_ALLOC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.1.3.300", "0")],
        )
        _set_walk(
            _OID_PROC_MEM_FREED_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.2.3.300", "0")],
        )
        _set_walk(
            _OID_PROC_UTIL_5SEC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.5.3.300", "1")],
        )
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        p = result["processes"][0]
        assert p["mem_allocated_bytes"] == 0
        assert p["mem_freed_bytes"] == 0
        assert p["mem_net_bytes"] == 0
        assert "mem_net_pct_of_allocated" not in p

    async def test_cpu_desc_sort_5sec(self, cisco, collector, target):
        _set_walk(
            _OID_PROC_UTIL_5SEC_REV,
            [
                (".1.3.6.1.4.1.9.9.109.1.2.1.1.5.1.10", "50"),
                (".1.3.6.1.4.1.9.9.109.1.2.1.1.5.2.20", "30"),
            ],
        )
        _set_walk(
            _OID_PROC_NAME,
            [
                (".1.3.6.1.4.1.9.9.109.1.2.1.2.1.10", "proc-a"),
                (".1.3.6.1.4.1.9.9.109.1.2.1.2.2.20", "proc-b"),
            ],
        )
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        assert result["processes"][0]["name"] == "proc-a"
        assert result["processes"][0]["cpu_5sec_pct"] == 50
        assert result["processes"][1]["name"] == "proc-b"
        assert result["processes"][1]["cpu_5sec_pct"] == 30

    async def test_cpu_desc_sort_1min_fallback(self, cisco, collector, target):
        _set_walk(
            _OID_PROC_UTIL_1MIN_REV,
            [
                (".1.3.6.1.4.1.9.9.109.1.2.1.1.6.1.11", "40"),
                (".1.3.6.1.4.1.9.9.109.1.2.1.1.6.2.12", "25"),
            ],
        )
        _set_walk(
            _OID_PROC_NAME,
            [
                (".1.3.6.1.4.1.9.9.109.1.2.1.2.1.11", "alpha"),
                (".1.3.6.1.4.1.9.9.109.1.2.1.2.2.12", "beta"),
            ],
        )
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        assert result["processes"][0]["name"] == "alpha"
        assert result["processes"][0]["cpu_1min_pct"] == 40
        assert result["processes"][1]["cpu_1min_pct"] == 25

    async def test_cpu_desc_sort_5min_fallback(self, cisco, collector, target):
        _set_walk(
            _OID_PROC_UTIL_5MIN_REV,
            [
                (".1.3.6.1.4.1.9.9.109.1.2.1.1.7.1.13", "60"),
                (".1.3.6.1.4.1.9.9.109.1.2.1.1.7.2.14", "45"),
            ],
        )
        _set_walk(
            _OID_PROC_NAME,
            [
                (".1.3.6.1.4.1.9.9.109.1.2.1.2.1.13", "x"),
                (".1.3.6.1.4.1.9.9.109.1.2.1.2.2.14", "y"),
            ],
        )
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        assert result["processes"][0]["cpu_5min_pct"] == 60
        assert result["processes"][1]["cpu_5min_pct"] == 45

    async def test_no_cpu_processes_sort_by_name_desc(
        self, cisco, collector, target,
    ):
        _set_walk(
            _OID_PROC_NAME,
            [
                (".1.3.6.1.4.1.9.9.109.1.2.1.2.1.500", "zebra"),
                (".1.3.6.1.4.1.9.9.109.1.2.1.2.2.501", "apple"),
            ],
        )
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        assert result["processes"][0]["name"] == "zebra"
        assert result["processes"][1]["name"] == "apple"
        assert "cpu_5sec_pct" not in result["processes"][0]
        assert "cpu_5sec_pct" not in result["processes"][1]

    async def test_process_count_matches_entries(self, cisco, collector, target):
        util_rows = []
        name_rows = []
        for i, cpu in enumerate([10, 20, 30], start=1):
            util_rows.append(
                (f".1.3.6.1.4.1.9.9.109.1.2.1.1.5.1.{i}", str(cpu))
            )
            name_rows.append(
                (f".1.3.6.1.4.1.9.9.109.1.2.1.2.1.{i}", f"p{i}")
            )
        _set_walk(_OID_PROC_UTIL_5SEC_REV, util_rows)
        _set_walk(_OID_PROC_NAME, name_rows)
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        assert result["process_count"] == 3
        assert len(result["processes"]) == 3
        assert {p["name"] for p in result["processes"]} == {"p1", "p2", "p3"}


# ---------------------------------------------------------------------------
# honest failures
# ---------------------------------------------------------------------------


class TestCiscoProcessesHonestFailures:
    async def test_empty_table_returns_empty_dict(self, cisco, collector, target):
        for oid in (
            _OID_PROC_UTIL_5SEC_REV,
            _OID_PROC_UTIL_1MIN_REV,
            _OID_PROC_UTIL_5MIN_REV,
            _OID_PROC_MEM_ALLOC_REV,
            _OID_PROC_MEM_FREED_REV,
            _OID_PROC_NAME,
        ):
            _set_walk(oid, [])
        result = await cisco.read_health(
            collector, DeviceActionType.CISCO_QUERY_PROCESSES
        )
        assert result == {}

    async def test_missing_connection_raises_action_not_supported(self, cisco):
        target = SimpleNamespace(
            device_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            connection=None,
        )
        with pytest.raises(ActionNotSupported, match="no linked SNMP monitoring source"):
            await cisco.execute(DeviceActionType.CISCO_QUERY_PROCESSES, target)

    async def test_transport_failure_returns_success_false(self, cisco, target):

        def _raise_walk(oid):
            raise RuntimeError("SNMP timeout")

        fake = SimpleNamespace(_walk=_raise_walk)
        original = cisco._collector
        cisco._collector = lambda _t: fake
        try:
            result = await cisco.execute(
                DeviceActionType.CISCO_QUERY_PROCESSES, target
            )
        finally:
            cisco._collector = original
        assert result.success is False
        assert result.error is not None
        assert "Cisco IOS (SNMP) SNMP query failed" in result.error
        assert "RuntimeError" in result.error
        assert result.provider == "vendor:cisco"


# ---------------------------------------------------------------------------
# execute envelope
# ---------------------------------------------------------------------------


class TestCiscoProcessesExecuteEnvelope:
    async def test_execute_happy_path(self, cisco, target, collector):
        _set_walk(
            _OID_PROC_UTIL_5SEC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.5.1.100", "42")],
        )
        _set_walk(
            _OID_PROC_NAME,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.2.1.100", "testproc")],
        )
        original = cisco._collector
        cisco._collector = lambda _t: collector
        try:
            result = await cisco.execute(
                DeviceActionType.CISCO_QUERY_PROCESSES, target
            )
        finally:
            cisco._collector = original
        assert result.success is True
        assert result.error is None
        assert result.provider == "vendor:cisco"
        assert result.output["process_count"] == 1
        assert result.output["processes"][0]["name"] == "testproc"

    async def test_execute_empty_result_success_false(self, cisco, target, collector):
        original = cisco._collector
        cisco._collector = lambda _t: collector
        try:
            result = await cisco.execute(
                DeviceActionType.CISCO_QUERY_PROCESSES, target
            )
        finally:
            cisco._collector = original
        assert result.success is False
        assert result.error is not None
        assert "no vendor health data returned" in result.error
        assert result.provider == "vendor:cisco"
        assert result.output == {}

    async def test_execute_populates_threshold_evaluation(
        self, cisco, target, collector,
    ):
        _set_walk(
            _OID_PROC_UTIL_5SEC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.5.1.100", "42")],
        )
        _set_walk(
            _OID_PROC_NAME,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.2.1.100", "testproc")],
        )
        original = cisco._collector
        cisco._collector = lambda _t: collector
        try:
            result = await cisco.execute(
                DeviceActionType.CISCO_QUERY_PROCESSES,
                target,
                params={"thresholds": {"cpu": {"warning": 40, "critical": 50}}},
            )
        finally:
            cisco._collector = original
        assert result.success is True
        assert "threshold_evaluation" in result.output
        te = result.output["threshold_evaluation"]
        assert te["health_status"] == "warning"
        assert any(
            v["metric"] == "process.1.pid100.cpu_5sec_pct" for v in te["verdicts"]
        )
        v = next(
            v
            for v in te["verdicts"]
            if v["metric"] == "process.1.pid100.cpu_5sec_pct"
        )
        assert v["status"] == "warning"
        assert v["warning"] == 40.0
        assert v["critical"] == 50.0

    async def test_execute_default_thresholds(self, cisco, target, collector):
        _set_walk(
            _OID_PROC_UTIL_5SEC_REV,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.1.5.1.100", "85")],
        )
        _set_walk(
            _OID_PROC_NAME,
            [(".1.3.6.1.4.1.9.9.109.1.2.1.2.1.100", "cpu-hog")],
        )
        original = cisco._collector
        cisco._collector = lambda _t: collector
        try:
            result = await cisco.execute(
                DeviceActionType.CISCO_QUERY_PROCESSES, target
            )
        finally:
            cisco._collector = original
        te = result.output["threshold_evaluation"]
        v = te["verdicts"][0]
        assert v["status"] == "warning"
        assert v["warning"] == 80.0
        assert v["critical"] == 95.0
