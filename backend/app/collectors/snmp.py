"""
SNMP collector (v1 / v2c / v3).

Walks standard MIBs on network devices to discover system info, interfaces,
and basic operational metrics.

Connection details from `MonitoringSourceConnectionInfo`:
    hostname        -> SNMP target host / IP
    port            -> SNMP UDP port (default 161)
    snmp_version    -> v1 | v2c | v3 (default v2c)
    username        -> SNMP v3 security name (v3 only)
    password        -> SNMP v3 auth password (v3 only, encrypted at rest)
    api_key         -> SNMP community string for v1/v2c (stored as api_key)

OIDs covered:
    sysDescr        (.1.3.6.1.2.1.1.1.0)
    sysName         (.1.3.6.1.2.1.1.5.0)
    ifTable         (.1.3.6.1.2.1.2.2.1) — ifDescr, ifAdminStatus, ifOperStatus,
                    ifInOctets, ifOutOctets, ifInErrors, ifOutErrors, ifSpeed
    IP-MIB / IP-FORWARD-MIB address and route tables (L3 topology) - see
                    app/collectors/snmp_l3.py

Dependency: pysnmp==6.2.5 (pinned in requirements.txt)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedNeighbor,
    NormalizedSensorReading,
)
from app.collectors.snmp_l3 import collect_l3, normalize_l3

from app.collectors.switching import (
    _DOT1D_BASE_PORT_IF_INDEX,
    _DOT1D_STP_DESIGNATED_ROOT,
    _DOT1D_STP_PORT_PATH_COST,
    _DOT1D_STP_PORT_STATE,
    _DOT1D_STP_PRIORITY,
    _DOT1D_STP_PROTOCOL_SPEC,
    _DOT1D_STP_ROOT_COST,
    _DOT1D_STP_ROOT_PORT,
    _DOT1D_STP_TOP_CHANGES,
    _DOT1Q_PVID,
    _DOT1Q_VLAN_STATIC_EGRESS,
    _DOT1Q_VLAN_STATIC_NAME,
    _DOT1Q_VLAN_STATIC_UNTAGGED,
    _LAG_AGG_MAC_ADDRESS,
    _LAG_AGG_PORT_LIST,
    _ENTITY_PHYSICAL_CLASS,
    _ENTITY_PHYSICAL_MODEL,
    _ENTITY_PHYSICAL_NAME,
    _ENTITY_PHYSICAL_SERIAL,
    _OID_SYS_OBJECT_ID,
    bridge_port_from_index,
    build_switching_metadata,
    classify_switch,
    decode_int,
    decode_mac,
    decode_port_list,
    recognise_vendor,
    select_chassis_identity,
    stp_port_state_name,
    vlan_id_from_index,
)

logger = logging.getLogger(__name__)

# Standard MIB OIDs
_OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
_OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
_OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
_OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
_OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
_OID_IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
_OID_IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
_OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
_OID_IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
_OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"

# -- LLDP (IEEE 802.1AB LLDP-MIB) ------------------------------------------
# Standard tables, so this works on any LLDP-speaking device regardless of
# vendor. The remote table's row index is
# <lldpRemTimeMark>.<lldpRemLocalPortNum>.<lldpRemIndex>.
_LLDP_REM_TABLE = "1.0.8802.1.1.2.1.4.1.1"
_LLDP_REM_CHASSIS_ID = f"{_LLDP_REM_TABLE}.5"
_LLDP_REM_PORT_ID = f"{_LLDP_REM_TABLE}.7"
_LLDP_REM_PORT_DESC = f"{_LLDP_REM_TABLE}.8"
_LLDP_REM_SYS_NAME = f"{_LLDP_REM_TABLE}.9"
# lldpRemManAddr carries the neighbour's management address. Its row index ends
# with <addrSubtype>.<addrLen>.<addr octets>, so the IP is recoverable from the
# OID itself when the address family is IPv4.
_LLDP_REM_MAN_ADDR = "1.0.8802.1.1.2.1.4.2.1.4"
# Scalar proving LLDP is actually enabled locally. Used to tell "LLDP enabled
# but no neighbours" apart from "LLDP not enabled" (Step 10).
_LLDP_LOC_CHASSIS_ID = "1.0.8802.1.1.2.1.3.2.0"

# -- Cisco CDP (CISCO-CDP-MIB) --------------------------------------------
# Optional: absent on non-Cisco gear. Row index is
# <cdpCacheIfIndex>.<cdpCacheDeviceIndex>.
_CDP_CACHE_TABLE = "1.3.6.1.4.1.9.9.23.1.2.1.1"
_CDP_CACHE_ADDRESS = f"{_CDP_CACHE_TABLE}.4"
_CDP_CACHE_VERSION = f"{_CDP_CACHE_TABLE}.5"
_CDP_CACHE_DEVICE_ID = f"{_CDP_CACHE_TABLE}.6"
_CDP_CACHE_DEVICE_PORT = f"{_CDP_CACHE_TABLE}.7"
_CDP_CACHE_PLATFORM = f"{_CDP_CACHE_TABLE}.8"
# Scalar proving CDP is running locally (1 = enabled).
_CDP_GLOBAL_RUN = ".1.3.6.1.4.1.9.9.23.1.3.1.0"

# Neighbour-collection outcomes. The distinction between FAILED (the walk could
# not be performed) and EMPTY (the table was read and held no neighbours) is
# mandatory - reporting a failed poll as "no neighbours found" is a lie.
NEIGHBOR_COLLECTED = "collected"
NEIGHBOR_EMPTY = "empty"
NEIGHBOR_UNAVAILABLE = "unavailable"
NEIGHBOR_FAILED = "failed"

_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_HEX4_RE = re.compile(r"[0-9a-fA-F]{8}")


def _normalize_oid(oid: Any) -> str:
    """Canonical dotted OID text, without a leading dot.

    A real agent (and pysnmp's client) reports OIDs as ``1.3.6.1...``, while MIB
    constants are commonly written ``.1.3.6.1...``. Mixing the two silently
    breaks every lookup: a walk's subtree test, the row-index suffix parsing that
    turns columns into interfaces/neighbours, and the scalar dict lookups that
    read sysDescr/sysName. Normalizing at the boundary means both spellings
    resolve to the same OID, verified against a real SNMP agent.
    """
    return str(oid).strip().lstrip(".")


def _iter_walk_var_binds(var_binds: Any):
    """Flatten pysnmp's GETNEXT/GETBULK varbind TABLE into individual var binds.

    hlapi's ``nextCmd``/``bulkCmd`` hand back a list of ROWS (one per
    repetition), while ``getCmd`` hands back a flat list. Iterating a table as
    if it were flat yields the ROW itself, whose text form is
    ``"<oid> = <value>"`` - a string that can never pass an OID comparison, so
    every walk silently collected zero rows against a real agent while looking
    merely empty. Both shapes are accepted here so neither can do that again.
    """
    for entry in var_binds or ():
        if (
            isinstance(entry, (list, tuple))
            and entry
            and not isinstance(entry[0], (str, bytes, bytearray, int))
        ):
            for inner in entry:
                yield inner
        else:
            yield entry


def _suffix_after(oid: str, base: str) -> str | None:
    """The table-row index suffix of `oid` under `base`, or None if unrelated."""
    prefix = _normalize_oid(base) + "."
    text = _normalize_oid(oid)
    if not text.startswith(prefix):
        return None
    suffix = text[len(prefix):]
    return suffix or None


def _parse_lldp_row_index(suffix: str) -> tuple[str, str, str] | None:
    """lldpRemTable index is <timeMark>.<localPortNum>.<remIndex>."""
    parts = suffix.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return parts[0], parts[1], parts[2]


def _parse_lldp_man_addr(suffix: str) -> tuple[str, str | None] | None:
    """lldpRemManAddr index is
    <timeMark>.<localPortNum>.<remIndex>.<addrSubtype>.<addrLen>.<addr octets>.

    Returns (row_key_without_addr_subtype, dotted_quad_or_None). The address is
    only decoded for IPv4 (subtype 1); any other family returns None rather than
    a guess.
    """
    parts = suffix.split(".")
    if len(parts) < 6 or not all(p.isdigit() for p in parts):
        return None
    time_mark, local_port, rem_index, subtype, addr_len = parts[:5]
    try:
        length = int(addr_len)
    except ValueError:
        return None
    octets = parts[5:]
    if length <= 0 or len(octets) < length:
        return None
    key = f"{time_mark}.{local_port}.{rem_index}"
    if subtype == "1" and length == 4:
        return key, ".".join(octets[:4])
    return key, None


def _parse_cdp_row_index(suffix: str) -> tuple[str, str] | None:
    """cdpCacheTable index is <cdpCacheIfIndex>.<cdpCacheDeviceIndex>."""
    parts = suffix.split(".")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return parts[0], parts[1]


def _coerce_ipv4(value: Any) -> str | None:
    """Best-effort dotted-quad from a provider address value, else None.

    pysnmp stringifies an OCTET STRING, so a 4-byte IPv4 address may arrive as
    four raw bytes, as dotted text, or as 8 hex digits. Anything else returns
    None - a malformed address must never become an invented one.
    """
    if value is None:
        return None
    text = str(value)
    stripped = text.strip()
    if not stripped:
        return None
    if _IPV4_RE.fullmatch(stripped):
        # Reject out-of-range octets rather than emitting a bogus address that
        # could never match a real Device.
        if all(int(octet) <= 255 for octet in stripped.split(".")):
            return stripped
        return None
    if _HEX4_RE.fullmatch(stripped):
        return ".".join(str(int(stripped[i:i + 2], 16)) for i in range(0, 8, 2))
    # Raw 4-byte form is checked on the UNSTRIPPED value on purpose: octet
    # values 10, 13, 32 and 9 are 0x0a/0x0d/0x20/0x09, so stripping would
    # silently shorten or corrupt any address ending in one of them (e.g.
    # 10.0.0.10 encodes as \x0a\x00\x00\x0a).
    raw = text.encode("latin-1", errors="ignore")
    if len(raw) == 4:
        return ".".join(str(b) for b in raw)
    return None


@CollectorRegistry.register("snmp")
class SNMPCollector(BaseCollector):
    display_name = "SNMP (v1/v2c/v3)"
    implemented = True

    # -- config helpers -------------------------------------------------------

    def __init__(self, source) -> None:
        super().__init__(source)
        # ONE SNMP engine + transport target per collector instance.
        #
        # Building a fresh engine for every request (the previous behaviour)
        # leaked an engine, a UDP transport and a dispatcher timer task for each
        # of the ~20 requests in a single collection run - enough to exhaust a
        # scheduled fleet's resources and to crash the interpreter at shutdown.
        # Reuse also lets pysnmp's own request cache do its job.
        self._engine = None
        self._udp_target = None

    def _snmp_engine(self):
        if self._engine is None:
            from pysnmp.hlapi.asyncio import SnmpEngine  # type: ignore[import]

            self._engine = SnmpEngine()
        return self._engine

    def _transport_target(self):
        if self._udp_target is None:
            try:
                from pysnmp.hlapi.asyncio import UdpTransportTarget  # type: ignore[import]
            except ImportError as exc:
                raise CollectorConfigError("pysnmp is required.") from exc

            # Built eagerly (resolves the hostname) as pysnmp requires.
            self._udp_target = UdpTransportTarget(
                (self.source.hostname, self._port()), timeout=5, retries=2
            )
        return self._udp_target

    def _require_config(self) -> None:
        if not self.source.hostname:
            raise CollectorConfigError("SNMP config missing required field: hostname")

    async def aclose(self) -> None:
        """Close the SNMP engine's dispatcher and its timer tasks.

        pysnmp keeps a transport dispatcher (with pending timer tasks) per
        SnmpEngine. Leaving it running after the run leaks a live task per
        collection and crashes the interpreter at shutdown. Never raises.
        """
        engine, self._engine = self._engine, None
        self._udp_target = None
        if engine is None:
            return
        try:
            engine.closeDispatcher()
        except Exception:  # noqa: BLE001 - cleanup must never fail a run
            logger.debug("SNMP engine close failed", exc_info=True)

    def _community(self) -> str:
        return self.source.api_key or "public"

    def _snmp_version(self) -> str:
        return (self.source.snmp_version or "v2c").lower()

    def _port(self) -> int:
        return self.source.port or 161

    def _build_auth(self) -> Any:
        """Build pysnmp CommunityData or UsmUserData based on SNMP version."""
        try:
            from pysnmp.hlapi.asyncio import CommunityData, UsmUserData  # type: ignore[import]
        except ImportError as exc:
            raise CollectorConfigError(
                "pysnmp is required for the SNMP collector. "
                "Install it via: pip install pysnmp==6.2.5"
            ) from exc

        version = self._snmp_version()
        if version == "v3":
            return UsmUserData(
                userName=self.source.username or "",
                authKey=self.source.password or "",
                privKey=self.source.password or "",
            )
        mp_model = 0 if version == "v1" else 1  # 0=SNMPv1, 1=SNMPv2c
        return CommunityData(self._community(), mpModel=mp_model)

    async def _get(self, *oids: str) -> dict[str, str]:
        """SNMP GET for one or more scalar OIDs. Returns {oid: value_str}."""
        try:
            from pysnmp.hlapi.asyncio import (  # type: ignore[import]
                SnmpEngine, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, getCmd,
            )
        except ImportError as exc:
            raise CollectorConfigError("pysnmp is required.") from exc

        engine = self._snmp_engine()
        transport = self._transport_target()
        auth = self._build_auth()
        object_types = [ObjectType(ObjectIdentity(oid)) for oid in oids]

        error_indication, error_status, error_index, var_binds = await getCmd(
            engine, auth, transport, ContextData(), *object_types
        )

        if error_indication or error_status:
            return {}

        return {
            _normalize_oid(var_bind[0]): str(var_bind[1]) for var_bind in var_binds
        }

    async def _walk(self, base_oid: str) -> list[tuple[str, str]]:
        """SNMP WALK from `base_oid`. Returns list of (oid_str, value_str)."""
        rows, _error = await self._walk_with_status(base_oid)
        return rows

    async def _walk_with_status(
        self, base_oid: str, max_rows: int | None = None
    ) -> tuple[list[tuple[str, str]], str | None]:
        """WALK that also reports WHY it returned nothing.

        `_walk` cannot distinguish "the table is empty" from "the walk could not
        be performed" - both yield []. Neighbour collection must tell those
        apart (Step 10), so this returns `(rows, error_reason)` and `_walk`
        delegates to it, keeping its existing contract unchanged.
        """
        try:
            from pysnmp.hlapi.asyncio import (  # type: ignore[import]
                SnmpEngine, UdpTransportTarget, ContextData, ObjectType,
                ObjectIdentity, nextCmd,
            )
        except ImportError as exc:
            raise CollectorConfigError("pysnmp is required.") from exc
        engine = self._snmp_engine()
        transport = self._transport_target()
        auth = self._build_auth()
        rows: list[tuple[str, str]] = []
        error: str | None = None

        # pysnmp 6.x's `hlapi.asyncio.nextCmd` is a PLAIN COROUTINE performing
        # ONE GETNEXT and returning a single (errind, errstat, erridx, varBinds)
        # tuple - it is NOT an async generator (verified: iscoroutinefunction
        # True, isasyncgenfunction False). `async for ... in nextCmd(...)` raised
        # "coroutine is not async iterable", which this method's own except
        # converted into `error`, so EVERY table looked simultaneously empty and
        # failed: interfaces, LLDP/CDP, VLAN, STP and LAG all returned zero rows.
        # The walk must therefore be driven explicitly, advancing from the last
        # OID the agent returned until it leaves the subtree.
        current = _normalize_oid(base_oid)
        base = current
        seen: set[str] = set()
        while True:
            try:
                error_indication, error_status, _idx, var_binds = await nextCmd(
                    engine, auth, transport, ContextData(),
                    ObjectType(ObjectIdentity(current)),
                )
            except Exception as exc:  # noqa: BLE001 - a walk never aborts collection
                error = f"{type(exc).__name__}: {exc}"
                break

            if error_indication:
                error = f"{type(error_indication).__name__}: {error_indication}"
                break
            if error_status:
                # e.g. noSuchName / authorizationError for an unsupported OID.
                error = str(error_status.prettyPrint())
                break
            if not var_binds:
                break

            advanced = False
            for var_bind in _iter_walk_var_binds(var_binds):
                oid = _normalize_oid(var_bind[0])
                # The agent moved past the subtree: the walk COMPLETED. This is
                # not an error, so `error` stays None.
                if oid != base and not oid.startswith(base + "."):
                    return rows, error
                # endOfMibView repeats the requested OID with no progress.
                if oid in seen:
                    return rows, error
                seen.add(oid)
                rows.append((oid, str(var_bind[1])))
                current = oid
                advanced = True
                if max_rows is not None and len(rows) >= max_rows:
                    return rows, error

            if not advanced:
                break

        return rows, error

    async def _scalar_present(self, oid: str) -> bool:
        """True when a scalar GET returns a non-empty value.

        Used to prove a protocol is enabled locally before claiming an empty
        neighbour table means "no neighbours".
        """
        try:
            values = await self._get(oid)
        except Exception:  # noqa: BLE001 - absence is not an error
            return False
        return bool(str(values.get(_normalize_oid(oid), "")).strip())

    async def _collect_lldp(self) -> tuple[dict[str, dict[str, str]], str]:
        """Read the standard LLDP remote tables.

        Returns `(columns, status)` where columns maps each LLDP attribute to
        `{row_key: value}`. `man_addr` values are already decoded to dotted-quad
        (or "" when the address family is not IPv4).

        Status distinguishes the four cases Step 10 requires:
        collected | empty | unavailable | failed.
        """
        columns: dict[str, dict[str, str]] = {
            "sys_name": {}, "chassis_id": {}, "port_id": {},
            "port_desc": {}, "man_addr": {},
        }
        error: str | None = None

        for key, base in (
            ("sys_name", _LLDP_REM_SYS_NAME),
            ("chassis_id", _LLDP_REM_CHASSIS_ID),
            ("port_id", _LLDP_REM_PORT_ID),
            ("port_desc", _LLDP_REM_PORT_DESC),
        ):
            rows, err = await self._walk_with_status(base)
            if err and error is None:
                error = err
            for oid, value in rows:
                suffix = _suffix_after(oid, base)
                if suffix is None:
                    continue
                if _parse_lldp_row_index(suffix):
                    columns[key][suffix] = value

        rows, err = await self._walk_with_status(_LLDP_REM_MAN_ADDR)
        if err and error is None:
            error = err
        for oid, value in rows:
            suffix = _suffix_after(oid, _LLDP_REM_MAN_ADDR)
            if suffix is None:
                continue
            parsed = _parse_lldp_man_addr(suffix)
            if parsed is None:
                continue
            row_key, dotted = parsed
            # Prefer the OID-encoded address; fall back to the column value.
            columns["man_addr"][row_key] = dotted or _coerce_ipv4(value) or ""

        if any(columns[key] for key in columns):
            return columns, NEIGHBOR_COLLECTED
        if error:
            return columns, NEIGHBOR_FAILED
        # The table read cleanly but held nothing. Only call that "empty" if
        # LLDP is actually enabled locally - otherwise the protocol is simply
        # not present, which is not the same statement.
        if await self._scalar_present(_LLDP_LOC_CHASSIS_ID):
            return columns, NEIGHBOR_EMPTY
        return columns, NEIGHBOR_UNAVAILABLE

    async def _collect_cdp(self) -> tuple[dict[str, dict[str, str]], str]:
        """Read the Cisco CDP cache table.

        Optional protocol: absent on non-Cisco devices, and its absence is
        reported as `unavailable`, never as a collector failure.
        """
        columns: dict[str, dict[str, str]] = {
            "address": {}, "device_id": {}, "device_port": {},
            "platform": {}, "version": {},
        }
        error: str | None = None

        for key, base in (
            ("address", _CDP_CACHE_ADDRESS),
            ("device_id", _CDP_CACHE_DEVICE_ID),
            ("device_port", _CDP_CACHE_DEVICE_PORT),
            ("platform", _CDP_CACHE_PLATFORM),
            ("version", _CDP_CACHE_VERSION),
        ):
            rows, err = await self._walk_with_status(base)
            if err and error is None:
                error = err
            for oid, value in rows:
                suffix = _suffix_after(oid, base)
                if suffix is None:
                    continue
                if _parse_cdp_row_index(suffix) is None:
                    continue
                columns[key][suffix] = (
                    _coerce_ipv4(value) or "" if key == "address" else value
                )

        if any(columns[key] for key in columns):
            return columns, NEIGHBOR_COLLECTED
        if error:
            return columns, NEIGHBOR_FAILED
        try:
            values = await self._get(_CDP_GLOBAL_RUN)
            running = str(values.get(_CDP_GLOBAL_RUN, "")).strip() == "1"
        except Exception:  # noqa: BLE001 - absence is not an error
            running = False
        return columns, NEIGHBOR_EMPTY if running else NEIGHBOR_UNAVAILABLE

    async def _collect_switching(self) -> dict[str, Any]:
        """Read the standard bridging/aggregation MIBs (Phase 3B).

        Vendor-neutral by construction: Cisco and Aruba are read through the same
        Q-BRIDGE-MIB / BRIDGE-MIB / IEEE8023-LAG-MIB objects as any other switch,
        so no per-vendor branch exists here.

        Every group is best-effort and independently statused, so a device that
        implements VLANs but not LACP still yields its VLAN inventory. Nothing is
        derived from anything else - if a table is absent it is reported absent.
        """
        result: dict[str, Any] = {
            "sys_object_id": None,
            "vlan_names": {}, "vlan_egress": {}, "vlan_untagged": {},
            "pvid": {}, "bridge_port_if_index": {},
            "stp": {}, "lag_aggregators": {}, "lag_port_lists": {},
        }
        status: dict[str, str] = {}

        # sysObjectID drives vendor recognition; a failure here is not fatal.
        try:
            scalar = await self._get(_OID_SYS_OBJECT_ID)
            result["sys_object_id"] = str(scalar.get(_OID_SYS_OBJECT_ID, "")).strip() or None
        except Exception:  # noqa: BLE001
            result["sys_object_id"] = None

        # -- VLANs (Q-BRIDGE-MIB) -------------------------------------------
        vlan_rows = 0
        vlan_error: str | None = None
        for key, base in (
            ("vlan_names", _DOT1Q_VLAN_STATIC_NAME),
            ("vlan_egress", _DOT1Q_VLAN_STATIC_EGRESS),
            ("vlan_untagged", _DOT1Q_VLAN_STATIC_UNTAGGED),
        ):
            rows, err = await self._walk_with_status(base)
            if err and vlan_error is None:
                vlan_error = err
            for oid, value in rows:
                suffix = _suffix_after(oid, base)
                if suffix is None:
                    continue
                vlan_id = vlan_id_from_index(suffix)
                if vlan_id is None:
                    continue
                result[key][vlan_id] = value
                vlan_rows += 1

        rows, err = await self._walk_with_status(_DOT1Q_PVID)
        if err and vlan_error is None:
            vlan_error = err
        for oid, value in rows:
            suffix = _suffix_after(oid, _DOT1Q_PVID)
            if suffix is None:
                continue
            bridge_port = bridge_port_from_index(suffix)
            pvid = decode_int(value)
            if bridge_port is None or pvid is None:
                continue
            result["pvid"][bridge_port] = pvid

        if vlan_rows or result["pvid"]:
            status["vlan"] = NEIGHBOR_COLLECTED
        elif vlan_error:
            status["vlan"] = NEIGHBOR_FAILED
        else:
            # No dot1qVlanStaticTable and no PVID: the device is not doing
            # 802.1Q (or does not expose it). Distinct from a failed read.
            status["vlan"] = NEIGHBOR_UNAVAILABLE

        # -- Bridge port -> ifIndex mapping (shared by VLAN and STP) --------
        rows, err = await self._walk_with_status(_DOT1D_BASE_PORT_IF_INDEX)
        for oid, value in rows:
            suffix = _suffix_after(oid, _DOT1D_BASE_PORT_IF_INDEX)
            if suffix is None:
                continue
            bridge_port = bridge_port_from_index(suffix)
            if_index = decode_int(value)
            if bridge_port is None or if_index is None:
                continue
            result["bridge_port_if_index"][bridge_port] = if_index

        # -- STP / RSTP / MSTP (BRIDGE-MIB) ---------------------------------
        stp: dict[str, Any] = {}
        stp_error: str | None = None
        for key, oid in (
            ("protocol", _DOT1D_STP_PROTOCOL_SPEC),
            ("priority", _DOT1D_STP_PRIORITY),
            ("top_changes", _DOT1D_STP_TOP_CHANGES),
            ("designated_root", _DOT1D_STP_DESIGNATED_ROOT),
            ("root_cost", _DOT1D_STP_ROOT_COST),
            ("root_port", _DOT1D_STP_ROOT_PORT),
        ):
            try:
                scalar = await self._get(oid)
            except Exception as exc:  # noqa: BLE001
                if stp_error is None:
                    stp_error = str(exc)
                continue
            stp[key] = str(scalar.get(oid, "")).strip() or None

        port_state: dict[int, str] = {}
        port_cost: dict[int, int] = {}
        for key, base in (
            ("state", _DOT1D_STP_PORT_STATE),
            ("cost", _DOT1D_STP_PORT_PATH_COST),
        ):
            rows, err = await self._walk_with_status(base)
            if err and stp_error is None:
                stp_error = err
            for oid, value in rows:
                suffix = _suffix_after(oid, base)
                if suffix is None:
                    continue
                bridge_port = bridge_port_from_index(suffix)
                if bridge_port is None:
                    continue
                if key == "state":
                    state = stp_port_state_name(value)
                    if state is not None:
                        port_state[bridge_port] = state
                else:
                    cost = decode_int(value)
                    if cost is not None:
                        port_cost[bridge_port] = cost

        if port_state:
            stp["port_state"] = port_state
        if port_cost:
            stp["port_path_cost"] = port_cost

        if any(value for value in stp.values()):
            result["stp"] = stp
            status["stp"] = NEIGHBOR_COLLECTED
        elif stp_error:
            status["stp"] = NEIGHBOR_FAILED

        # -- Link aggregation (IEEE8023-LAG-MIB) ----------------------------
        lag_rows = 0
        lag_error: str | None = None
        for key, base in (
            ("lag_aggregators", _LAG_AGG_MAC_ADDRESS),
            ("lag_port_lists", _LAG_AGG_PORT_LIST),
        ):
            rows, err = await self._walk_with_status(base)
            if err and lag_error is None:
                lag_error = err
            for oid, value in rows:
                suffix = _suffix_after(oid, base)
                if suffix is None:
                    continue
                agg_index = bridge_port_from_index(suffix)
                if agg_index is None:
                    continue
                result[key][agg_index] = value
                lag_rows += 1

        if lag_rows:
            status["lag"] = NEIGHBOR_COLLECTED
        elif lag_error:
            status["lag"] = NEIGHBOR_FAILED

        result["status"] = status
        return result

    async def _collect_entity_identity(self) -> dict[str, Any]:
        """Read ENTITY-MIB for AUTHORITATIVE device identity (serial/model).

        Best-effort: many devices expose no ENTITY-MIB (or an empty one), which is
        reported as `unavailable` rather than treated as an error. Only the
        chassis row is used - see `select_chassis_identity`.
        """
        identity: dict[str, Any] = {
            "serial_number": None, "model": None, "chassis_name": None,
            "status": NEIGHBOR_UNAVAILABLE,
        }
        error: str | None = None
        columns: dict[str, dict[str, str]] = {
            "class": {}, "serial": {}, "model": {}, "name": {},
        }

        for key, base in (
            ("class", _ENTITY_PHYSICAL_CLASS),
            ("serial", _ENTITY_PHYSICAL_SERIAL),
            ("model", _ENTITY_PHYSICAL_MODEL),
            ("name", _ENTITY_PHYSICAL_NAME),
        ):
            rows, err = await self._walk_with_status(base)
            if err and error is None:
                error = err
            for oid, value in rows:
                suffix = _suffix_after(oid, base)
                if suffix is None:
                    continue
                text = str(value).strip()
                if text:
                    columns[key][suffix] = text

        if any(columns.values()):
            serial, model, chassis_name = select_chassis_identity(
                columns["class"], columns["serial"],
                columns["model"], columns["name"],
            )
            identity.update({
                "serial_number": serial, "model": model,
                "chassis_name": chassis_name, "status": NEIGHBOR_COLLECTED,
            })
        elif error:
            identity["status"] = NEIGHBOR_FAILED

        return identity

    # -- BaseCollector interface -----------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        self._require_config()
        try:
            result = await self._get(_OID_SYS_DESCR)
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectionCheck(
                success=False,
                message=f"SNMP connection failed: {type(exc).__name__}: {exc}",
            )

        if result:
            return ConnectionCheck(success=True, message="SNMP reachable.")
        return ConnectionCheck(
            success=False,
            message="SNMP: no response to sysDescr GET (host unreachable or wrong community).",
        )

    async def collect(self) -> dict[str, Any]:
        self._require_config()
        raw: dict[str, Any] = {}

        try:
            scalars = await self._get(_OID_SYS_DESCR, _OID_SYS_NAME)
            raw["sys_descr"] = scalars.get(_OID_SYS_DESCR, "")
            raw["sys_name"] = scalars.get(_OID_SYS_NAME, "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("SNMP collect(): scalar GET failed: source_id=%s error=%s", self.source.id, exc)
            raw["sys_descr"] = ""
            raw["sys_name"] = ""

        # Interface table — best-effort per column
        raw["interfaces"] = {}
        for oid_key, base_oid in (
            ("descr", _OID_IF_DESCR),
            ("admin_status", _OID_IF_ADMIN_STATUS),
            ("oper_status", _OID_IF_OPER_STATUS),
            ("in_octets", _OID_IF_IN_OCTETS),
            ("out_octets", _OID_IF_OUT_OCTETS),
            ("in_errors", _OID_IF_IN_ERRORS),
            ("out_errors", _OID_IF_OUT_ERRORS),
            ("speed", _OID_IF_SPEED),
        ):
            try:
                rows = await self._walk(base_oid)
                raw["interfaces"][oid_key] = {
                    oid.split(".")[-1]: val for oid, val in rows
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SNMP collect(): walk %s failed: source_id=%s error=%s",
                    base_oid, self.source.id, exc,
                )
                raw["interfaces"][oid_key] = {}

        # Adjacency tables — LLDP (standard IEEE 802.1AB) and CDP (Cisco,
        # optional). Best-effort: a device that does not speak either protocol
        # must still collect its interfaces and sensors normally.
        for protocol, read in (("lldp", self._collect_lldp), ("cdp", self._collect_cdp)):
            try:
                columns, status = await read()
            except CollectorConfigError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SNMP collect(): %s neighbour read failed: source_id=%s error=%s",
                    protocol, self.source.id, exc,
                )
                columns, status = {}, NEIGHBOR_FAILED
            raw[protocol] = columns
            raw[f"{protocol}_status"] = status
            if status == NEIGHBOR_FAILED:
                logger.warning(
                    "SNMP collect(): %s adjacency UNKNOWN (poll failed) for source_id=%s "
                    "- this is a collection failure, not 'no neighbours'",
                    protocol.upper(), self.source.id,
                )

        # Switching discovery (Phase 3B): VLAN / STP / LAG via standard MIBs.
        # Best-effort like the adjacency tables - a device that is not a switch
        # (or exposes no bridging MIBs) still collects devices and interfaces.
        try:
            raw["switching"] = await self._collect_switching()
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SNMP collect(): switching discovery failed: source_id=%s error=%s",
                self.source.id, exc,
            )
            raw["switching"] = {"status": {"vlan": NEIGHBOR_FAILED, "stp": NEIGHBOR_FAILED,
                                           "lag": NEIGHBOR_FAILED}}

        # Authoritative device identity (ENTITY-MIB). Best-effort: a device with
        # no ENTITY-MIB still collects normally, it just cannot be correlated
        # across collectors by serial.
        try:
            raw["identity"] = await self._collect_entity_identity()
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SNMP collect(): ENTITY-MIB identity failed: source_id=%s error=%s",
                self.source.id, exc,
            )
            raw["identity"] = {"status": NEIGHBOR_FAILED}

        # Interface addresses and routes (L3 topology). Best-effort and
        # independently statused, like the tables above.
        try:
            raw["l3"] = await collect_l3(self._walk_with_status)
        except CollectorConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SNMP collect(): L3 address/route read failed: source_id=%s error=%s",
                self.source.id, exc,
            )
            raw["l3"] = {"addresses_status": NEIGHBOR_FAILED, "routes_status": NEIGHBOR_FAILED}

        return raw

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        hostname = self.source.hostname or "snmp-host"
        sys_name = raw.get("sys_name") or hostname
        sys_descr = raw.get("sys_descr") or None
        switching = raw.get("switching") or {}

        # Vendor recognition (Phase 3B §3/§4). sysObjectID's IANA enterprise
        # number is authoritative; sysDescr is a fallback hint. A device whose
        # vendor cannot be identified keeps vendor=None - it is never defaulted
        # to Cisco or "Generic". Model/OS are NOT parsed out of sysDescr: that
        # would be inference, so the raw string is preserved instead.
        vendor = recognise_vendor(switching.get("sys_object_id"), sys_descr)

        interfaces: list[NormalizedInterface] = []
        iface_data = raw.get("interfaces", {})
        descr_map: dict[str, str] = iface_data.get("descr", {})

        # ifIndex -> ifDescr, needed to resolve bridge ports (VLAN/STP/LAG) into
        # real interface names. A port with no name is dropped, not invented.
        if_index_to_name: dict[int, str] = {}
        for if_index, name in descr_map.items():
            parsed = decode_int(if_index)
            if parsed is not None and name:
                if_index_to_name[parsed] = name

        switching_metadata = build_switching_metadata(switching, if_index_to_name)

        # Authoritative identity from ENTITY-MIB (§16/§17). The chassis serial is
        # the cross-source identity key, so it is carried on the device itself
        # rather than buried in custom_metadata. `model` is only filled when the
        # device actually reported one - never parsed out of sysDescr.
        identity = raw.get("identity") or {}
        serial_number = identity.get("serial_number") or None
        model = identity.get("model") or None

        device = NormalizedDevice(
            external_id=hostname,
            name=sys_name,
            device_type=classify_switch("other", sys_descr) or "other",
            vendor=vendor,
            model=model,
            serial_number=serial_number,
            hostname=hostname,
            ip_address=self.source.ip_address or self.source.hostname,
            status="up",
            custom_metadata=switching_metadata or {},
        )

        for idx, iface_name in descr_map.items():
            admin_raw = iface_data.get("admin_status", {}).get(idx, "2")
            oper_raw = iface_data.get("oper_status", {}).get(idx, "2")
            # SNMP status: 1=up, 2=down, 3=testing
            admin_status = "up" if admin_raw == "1" else "down"
            oper_status = "up" if oper_raw == "1" else "down"

            speed_bps = _safe_float(iface_data.get("speed", {}).get(idx))
            speed_mbps = (speed_bps / 1_000_000) if speed_bps else None

            iface = NormalizedInterface(
                device_external_id=hostname,
                external_id=idx,
                name=iface_name or f"if{idx}",
                admin_status=admin_status,
                oper_status=oper_status,
                speed_mbps=speed_mbps,
                in_octets=_safe_float(iface_data.get("in_octets", {}).get(idx)),
                out_octets=_safe_float(iface_data.get("out_octets", {}).get(idx)),
                in_errors=_safe_float(iface_data.get("in_errors", {}).get(idx)),
                out_errors=_safe_float(iface_data.get("out_errors", {}).get(idx)),
            )
            interfaces.append(iface)

        neighbors: list[NormalizedNeighbor] = []
        # (local_interface, remote_address|remote_identity) already recorded.
        # LLDP and CDP frequently report the SAME adjacency; the repository keys
        # edges on (tenant, source, target, relationship_type), so emitting both
        # would update one row twice rather than create two links - but collapsing
        # them here keeps provenance deterministic and the log honest.
        seen_adjacency: set[tuple[str | None, str | None]] = set()

        # LLDP first: it is the standard protocol, so it wins provenance when
        # both protocols observe the same link.
        lldp = raw.get("lldp") or {}
        lldp_rows: set[str] = set()
        for column in ("sys_name", "chassis_id", "port_id", "port_desc", "man_addr"):
            lldp_rows |= set((lldp.get(column) or {}).keys())

        for row_key in sorted(lldp_rows):
            parsed = _parse_lldp_row_index(row_key)
            if parsed is None:
                continue
            local_if = descr_map.get(parsed[1])
            # Coerced here as well as in _collect_lldp: a malformed value must
            # never reach the address used for identity matching, whichever path
            # built this raw dict.
            remote_addr = _coerce_ipv4((lldp.get("man_addr") or {}).get(row_key))
            remote_identity = (lldp.get("sys_name") or {}).get(row_key) or None
            remote_iface = (
                (lldp.get("port_id") or {}).get(row_key)
                or (lldp.get("port_desc") or {}).get(row_key)
                or None
            )
            dedupe = (local_if, remote_addr or remote_identity)
            if dedupe in seen_adjacency:
                continue
            seen_adjacency.add(dedupe)
            neighbors.append(NormalizedNeighbor(
                device_external_id=hostname,
                local_interface=local_if,
                remote_address=remote_addr,
                remote_identity=remote_identity,
                remote_interface=remote_iface,
                remote_chassis_id=(lldp.get("chassis_id") or {}).get(row_key) or None,
                evidence_type="lldp",
            ))

        cdp = raw.get("cdp") or {}
        cdp_rows: set[str] = set()
        for column in ("address", "device_id", "device_port", "platform"):
            cdp_rows |= set((cdp.get(column) or {}).keys())

        for row_key in sorted(cdp_rows):
            parsed = _parse_cdp_row_index(row_key)
            if parsed is None:
                continue
            local_if = descr_map.get(parsed[0])
            remote_addr = _coerce_ipv4((cdp.get("address") or {}).get(row_key))
            remote_identity = (cdp.get("device_id") or {}).get(row_key) or None
            dedupe = (local_if, remote_addr or remote_identity)
            if dedupe in seen_adjacency:
                logger.info(
                    "SNMP normalize(): CDP adjacency %s on %s already observed via "
                    "LLDP - not duplicated", remote_identity or remote_addr, local_if,
                )
                continue
            seen_adjacency.add(dedupe)
            neighbors.append(NormalizedNeighbor(
                device_external_id=hostname,
                local_interface=local_if,
                remote_address=remote_addr,
                remote_identity=remote_identity,
                remote_interface=(cdp.get("device_port") or {}).get(row_key) or None,
                remote_platform=(cdp.get("platform") or {}).get(row_key) or None,
                evidence_type="cdp",
            ))

        addresses, routes, l3_status = normalize_l3(
            raw.get("l3") or {},
            device_external_id=hostname,
            if_names=descr_map,
            coerce_ipv4=_coerce_ipv4,
        )

        return NormalizedBundle(
            devices=[device],
            interfaces=interfaces,
            neighbors=neighbors,
            neighbor_status={
                "lldp": raw.get("lldp_status", NEIGHBOR_UNAVAILABLE),
                "cdp": raw.get("cdp_status", NEIGHBOR_UNAVAILABLE),
            },
            addresses=addresses,
            routes=routes,
            l3_status=l3_status,
        )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None
