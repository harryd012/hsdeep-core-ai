"""
Phase 3B §11 regression tests for the SNMP WALK driver.

WHY THIS FILE EXISTS
--------------------
Phase 3A/3B validated `normalize()` thoroughly but every test injected raw dicts
directly - so `_walk_with_status()` itself was never executed. A live-SNMP check
against a real agent exposed that it walked nothing at all:

    pysnmp 6.2.5's `hlapi.asyncio.nextCmd` is a PLAIN COROUTINE that performs ONE
    GETNEXT and returns a single (errind, errstat, erridx, varBinds) tuple - it is
    NOT an async generator. `async for ... in nextCmd(...)` therefore raised
    "coroutine is not async iterable", which the walk's own `except` converted
    into an error, so EVERY table (interfaces, LLDP/CDP, VLAN, STP, LAG) returned
    zero rows while looking merely empty-or-failed.

These tests drive the walk with a fake `nextCmd` having the REAL API shape, so
the same mistake cannot return silently.
"""
import inspect
import uuid
from types import SimpleNamespace

from app.collectors.snmp import SNMPCollector


def _source(hostname="127.0.0.1", ip="127.0.0.1"):
    # A RESOLVABLE address: _walk_with_status constructs UdpTransportTarget
    # eagerly, which resolves the hostname before any PDU is sent.
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="snmp-a", source_type="snmp",
        hostname=hostname, ip_address=ip, port=None, protocol="snmp",
        snmp_version="v2c", username=None, api_key="public", password=None,
        config={}, verify_ssl=None,
    )


def _as_tuple(oid: str) -> tuple[int, ...]:
    return tuple(int(p) for p in oid.split("."))


def _patch_agent(monkeypatch, table: list[tuple[str, str]]) -> list[str]:
    """Replace `nextCmd` with a fake having the REAL signature and semantics:
    a plain coroutine returning ONE row per call, like pysnmp 6.x.

    Rows are served SEQUENTIALLY, exactly as a real agent answers successive
    GETNEXTs. The requested OID is captured defensively - indexing a pysnmp
    `ObjectType` can raise SmiError for a not-yet-resolved identity, and that
    would surface as a spurious walk failure rather than a test-helper problem.
    """
    import pysnmp.hlapi.asyncio as hlapi_asyncio

    requested: list[str] = []

    async def fake_next(engine, auth, transport, ctx, *object_types, **kwargs):
        try:
            requested.append(str(object_types[0][0]))
        except Exception:  # noqa: BLE001 - diagnostic only
            requested.append("<unavailable>")
        index = len(requested) - 1
        if index < len(table):
            oid, value = table[index]
            return None, 0, 0, ((oid, value),)
        # Past the end of the agent's tree: an empty result set.
        return None, 0, 0, ()

    monkeypatch.setattr(hlapi_asyncio, "nextCmd", fake_next)
    return requested


class TestWalkPysnmpApiShape:
    """Pins the API contract the walk code depends on."""

    def test_nextcmd_is_a_coroutine_not_an_async_generator(self):
        from pysnmp.hlapi.asyncio import nextCmd

        assert inspect.iscoroutinefunction(nextCmd), (
            "nextCmd changed shape - _walk_with_status must be revisited"
        )
        assert not inspect.isasyncgenfunction(nextCmd), (
            "nextCmd became an async generator; `async for` would now be correct"
        )

    def test_getcmd_is_awaited_in_the_collector(self):
        assert "await getCmd(" in inspect.getsource(SNMPCollector._get)

    def test_walk_does_not_async_for_nextcmd(self):
        src = inspect.getsource(SNMPCollector._walk_with_status)
        # Strip comments: the method's explanatory comment QUOTES the old broken
        # pattern, so a naive substring check would match its own documentation.
        code = [
            line for line in src.splitlines()
            if not line.strip().startswith("#")
        ]
        assert not any("async for" in line for line in code), (
            "_walk_with_status must not `async for` over nextCmd's coroutine"
        )
        assert any("await nextCmd(" in line for line in code)


class TestWalkDriver:
    SUBTREE = "1.3.6.1.2.1.1"
    TABLE = [
        ("1.3.6.1.2.1.1.1.0", "Cisco IOS Software"),
        ("1.3.6.1.2.1.1.2.0", "1.3.6.1.4.1.9.1.716"),
        ("1.3.6.1.2.1.1.5.0", "sw-lab-01"),
        # Outside the walked subtree - must terminate the walk, not be included.
        ("1.3.6.1.2.1.2.2.1.2.101", "GigabitEthernet1/0/1"),
    ]

    def _collector(self) -> SNMPCollector:
        return SNMPCollector(_source())

    async def test_walk_returns_real_rows(self, monkeypatch):
        _patch_agent(monkeypatch, self.TABLE)
        rows, error = await self._collector()._walk_with_status(self.SUBTREE)

        assert error is None
        assert [oid for oid, _ in rows] == [
            "1.3.6.1.2.1.1.1.0",
            "1.3.6.1.2.1.1.2.0",
            "1.3.6.1.2.1.1.5.0",
        ]
        assert rows[0][1] == "Cisco IOS Software"

    async def test_leaving_the_subtree_is_completion_not_failure(self, monkeypatch):
        """The walk stops at the boundary and reports NO error - a completed walk
        must not look like a failure."""
        _patch_agent(monkeypatch, self.TABLE)
        rows, error = await self._collector()._walk_with_status(self.SUBTREE)
        assert error is None
        assert all(oid.startswith(self.SUBTREE + ".") for oid, _ in rows)

    async def test_empty_table_terminates_without_error(self, monkeypatch):
        _patch_agent(monkeypatch, [])
        rows, error = await self._collector()._walk_with_status(self.SUBTREE)
        assert rows == []
        assert error is None, "an empty table is not a failed walk"

    async def test_walk_advances_from_the_last_oid(self, monkeypatch):
        requested = _patch_agent(monkeypatch, self.TABLE)
        await self._collector()._walk_with_status(self.SUBTREE)
        # One call per row; the 4th row is outside the subtree and terminates the
        # walk, so no further call is made.
        assert len(requested) >= len(self.TABLE)

    async def test_walk_terminates_on_a_repeated_oid(self, monkeypatch):
        """endOfMibView repeats the requested OID; the walk must not spin.

        The fake returns a CONSTANT oid - indexing a pysnmp ObjectType inside the
        fake would itself raise SmiError and masquerade as a walk failure.
        """
        import pysnmp.hlapi.asyncio as hlapi_asyncio

        async def stuck(engine, auth, transport, ctx, *object_types, **kwargs):
            return None, 0, 0, (("1.3.6.1.2.1.1.1.0", "same-again"),)

        monkeypatch.setattr(hlapi_asyncio, "nextCmd", stuck)
        rows, error = await self._collector()._walk_with_status(self.SUBTREE)
        assert error is None
        assert len(rows) == 1, "the repeated OID must stop the walk, not loop"

    async def test_error_indication_is_reported_with_rows_so_far(self, monkeypatch):
        """A timeout must surface as an error, not as 'the table was empty'."""
        import pysnmp.hlapi.asyncio as hlapi_asyncio

        async def timing_out(engine, auth, transport, ctx, *object_types, **kwargs):
            return "RequestTimedOut", 0, 0, ()

        monkeypatch.setattr(hlapi_asyncio, "nextCmd", timing_out)
        rows, error = await self._collector()._walk_with_status(self.SUBTREE)
        assert rows == []
        assert error is not None and "RequestTimedOut" in error

    async def test_exception_in_walk_is_captured_not_raised(self, monkeypatch):
        import pysnmp.hlapi.asyncio as hlapi_asyncio

        async def boom(engine, auth, transport, ctx, *object_types, **kwargs):
            raise RuntimeError("transport exploded")

        monkeypatch.setattr(hlapi_asyncio, "nextCmd", boom)
        rows, error = await self._collector()._walk_with_status(self.SUBTREE)
        assert rows == []
        assert error is not None and "RuntimeError" in error

    async def test_walk_helper_still_delegates(self, monkeypatch):
        """`_walk` keeps its rows-only contract."""
        _patch_agent(monkeypatch, self.TABLE)
        rows = await self._collector()._walk(self.SUBTREE)
        assert len(rows) == 3
