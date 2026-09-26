"""Laboratory SNMP endpoints for controlled collection validation (Phase 9.7B).

WHY THIS FILE EXISTS
--------------------
Phase 9.7B has to prove the REAL collection path end to end:

    real SNMP -> interfaces + adjacency evidence -> BundleSaver ->
    DiscoveryService -> resource_relationships -> topology API -> dashboard

No SNMP device is reachable from this development environment (verified: every
device recorded in the database, every local interface and 127.0.0.1:161 time
out), so the controlled scope is a pair of LAB endpoints served by this module.

WHAT THIS IS - AND IS NOT
-------------------------
* It IS a real SNMPv2c agent: real UDP transport (pysnmp's asyncio datagram
  carrier), real BER-encoded PDUs, real GET/GETNEXT semantics with noSuchName
  and endOfMibView handling. The platform's existing `SNMPCollector` is its
  client and cannot tell it apart from a device.
* It is NOT a production device, and nothing here is presented as one. The two
  profiles describe ``hsdeep-lab-sw-a`` / ``hsdeep-lab-sw-b``: lab switches
  created for this validation, on 127.0.0.1 / 127.0.0.2. They deliberately
  carry no real vendor's sysObjectID, so recognition stays honest ("unknown")
  instead of classifying a lab endpoint as a vendor product.
* The MIB contents are the LAB topology's own truth: the interfaces the agent
  serves, and the neighbour each agent sees on its uplink, are the link that
  was actually built between these two endpoints. The agent reports what is
  there and never invents an adjacency the topology does not have.
* CDP is only served when a profile declares CDP neighbours; a profile without
  them exposes no CDP rows at all, which the collector correctly reports as
  ``unavailable`` rather than as a failure.

USAGE
-----
    python -m scripts.lab_snmp_agent --profile both --seconds 300
    python -m scripts.lab_snmp_agent --profile a             # 127.0.0.1:1161

Tests import `LabSnmpAgent` and run an endpoint in-process on an ephemeral
port, so SNMP parsing is covered by real PDUs rather than a mock.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field

from pyasn1.codec.ber import decoder, encoder
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.proto import api
from pysnmp.proto.api import v2c

logger = logging.getLogger(__name__)

# -- standard MIB nodes the platform's SNMPCollector actually reads -----------
OID_SYS_DESCR = (1, 3, 6, 1, 2, 1, 1, 1, 0)
OID_SYS_NAME = (1, 3, 6, 1, 2, 1, 1, 5, 0)
OID_SYS_OBJECT_ID = (1, 3, 6, 1, 2, 1, 1, 2, 0)
OID_IF_NUMBER = (1, 3, 6, 1, 2, 1, 2, 1, 0)
IF_TABLE = (1, 3, 6, 1, 2, 1, 2, 2, 1)
OID_IF_DESCR = IF_TABLE + (2,)
OID_IF_SPEED = IF_TABLE + (5,)
OID_IF_ADMIN_STATUS = IF_TABLE + (7,)
OID_IF_OPER_STATUS = IF_TABLE + (8,)
OID_IF_IN_OCTETS = IF_TABLE + (10,)
OID_IF_IN_ERRORS = IF_TABLE + (14,)
OID_IF_OUT_OCTETS = IF_TABLE + (16,)
OID_IF_OUT_ERRORS = IF_TABLE + (20,)

#: lldpLocChassisId.0 - the presence test that separates "LLDP enabled but no
#: neighbours" (empty) from "LLDP not implemented here" (unavailable).
OID_LLDP_LOC_CHASSIS_ID = (1, 0, 8802, 1, 1, 2, 1, 3, 2, 0)
LLDP_REM_TABLE = (1, 0, 8802, 1, 1, 2, 1, 4, 1, 1)
OID_LLDP_REM_CHASSIS_ID = LLDP_REM_TABLE + (5,)
OID_LLDP_REM_PORT_ID = LLDP_REM_TABLE + (7,)
OID_LLDP_REM_PORT_DESC = LLDP_REM_TABLE + (8,)
OID_LLDP_REM_SYS_NAME = LLDP_REM_TABLE + (9,)
OID_LLDP_REM_MAN_ADDR = (1, 0, 8802, 1, 1, 2, 1, 4, 2, 1, 4)

#: Cisco CDP cache columns (optional protocol: only served when a profile
#: declares CDP neighbours).
CDP_TABLE = (1, 3, 6, 1, 4, 1, 9, 9, 23, 1, 2, 1, 1)
OID_CDP_ADDRESS = CDP_TABLE + (4,)
OID_CDP_DEVICE_ID = CDP_TABLE + (6,)
OID_CDP_DEVICE_PORT = CDP_TABLE + (7,)
OID_CDP_PLATFORM = CDP_TABLE + (8,)

#: Private/experimental enterprise arc - deliberately NOT a real vendor's
#: sysObjectID, so recognition reports "unknown" for a lab endpoint.
LAB_SYS_OBJECT_ID = "1.3.6.1.4.1.99999.9.1"

ADMIN_UP, ADMIN_DOWN = 1, 2
OPER_UP, OPER_DOWN = 1, 2


@dataclass(frozen=True)
class LabInterface:
    index: int
    descr: str
    admin_status: int = ADMIN_UP
    oper_status: int = OPER_UP
    speed_bps: int = 1_000_000_000
    in_octets: int = 0
    out_octets: int = 0
    in_errors: int = 0
    out_errors: int = 0


@dataclass(frozen=True)
class LabLldpNeighbour:
    """One adjacency this endpoint genuinely has, as its own LLDP stack sees it."""

    local_index: int                 # ifIndex of the local port carrying the link
    remote_sys_name: str
    remote_port_id: str
    remote_chassis_id: str
    remote_management_address: str   # dotted quad of the peer's management IP
    remote_port_desc: str = ""


@dataclass(frozen=True)
class LabCdpNeighbour:
    local_index: int
    remote_device_id: str
    remote_port_id: str
    remote_platform: str
    remote_address: str


@dataclass
class LabDeviceProfile:
    sys_name: str
    sys_descr: str
    interfaces: list[LabInterface] = field(default_factory=list)
    lldp_neighbours: list[LabLldpNeighbour] = field(default_factory=list)
    cdp_neighbours: list[LabCdpNeighbour] = field(default_factory=list)
    expose_lldp_local_chassis: bool = True


def _lldp_row_oids(base: tuple[int, ...], local_port: int, value: str,
                   *, rem_index: int = 1, time_mark: int = 0) -> dict:
    """lldpRemTable row index is <timeMark>.<localPortNum>.<remIndex>."""
    return {base + (time_mark, local_port, rem_index): v2c.OctetString(value)}


def build_mib(profile: LabDeviceProfile) -> dict[tuple[int, ...], object]:
    """The endpoint's MIB as an OID -> value table.

    Only OIDs this endpoint really has are present: a profile with no CDP
    neighbours serves no CDP rows, so the collector reports ``unavailable``
    instead of a fabricated empty-but-present table.
    """
    mib: dict[tuple[int, ...], object] = {
        OID_SYS_DESCR: v2c.OctetString(profile.sys_descr),
        OID_SYS_NAME: v2c.OctetString(profile.sys_name),
        OID_SYS_OBJECT_ID: v2c.ObjectIdentifier(LAB_SYS_OBJECT_ID),
        OID_IF_NUMBER: v2c.Integer(len(profile.interfaces)),
    }

    for iface in profile.interfaces:
        index = (iface.index,)
        mib[OID_IF_DESCR + index] = v2c.OctetString(iface.descr)
        # ifSpeed is a 32-bit Gauge, so a real agent saturates anything faster
        # than ~4.29 Gbps at that ceiling (the true rate then lives in
        # ifHighSpeed). Reproduced faithfully rather than pretending the counter
        # is wider than the MIB allows.
        mib[OID_IF_SPEED + index] = v2c.Gauge32(min(iface.speed_bps, 4_294_967_295))
        mib[OID_IF_ADMIN_STATUS + index] = v2c.Integer(iface.admin_status)
        mib[OID_IF_OPER_STATUS + index] = v2c.Integer(iface.oper_status)
        # 64-bit counters: the high-capacity form a real switch exposes.
        mib[OID_IF_IN_OCTETS + index] = v2c.Counter64(iface.in_octets)
        mib[OID_IF_OUT_OCTETS + index] = v2c.Counter64(iface.out_octets)
        mib[OID_IF_IN_ERRORS + index] = v2c.Counter32(iface.in_errors)
        mib[OID_IF_OUT_ERRORS + index] = v2c.Counter32(iface.out_errors)

    if profile.expose_lldp_local_chassis:
        mib[OID_LLDP_LOC_CHASSIS_ID] = v2c.OctetString(profile.sys_name)

    for neighbour in profile.lldp_neighbours:
        for base, value in (
            (OID_LLDP_REM_SYS_NAME, neighbour.remote_sys_name),
            (OID_LLDP_REM_PORT_ID, neighbour.remote_port_id),
            (OID_LLDP_REM_CHASSIS_ID, neighbour.remote_chassis_id),
            (OID_LLDP_REM_PORT_DESC, neighbour.remote_port_desc),
        ):
            if value:
                mib.update(_lldp_row_oids(base, neighbour.local_index, value))
        # lldpRemManAddr index: timeMark.localPort.remIndex.addrSubtype.addrLen.<octets>
        octets = [int(part) for part in neighbour.remote_management_address.split(".")]
        if len(octets) == 4:
            oid = OID_LLDP_REM_MAN_ADDR + (0, neighbour.local_index, 1, 1, 4) + tuple(octets)
            mib[oid] = v2c.OctetString(neighbour.remote_management_address)

    for neighbour in profile.cdp_neighbours:
        index = (neighbour.local_index, 1)
        mib[OID_CDP_ADDRESS + index] = v2c.OctetString(neighbour.remote_address)
        mib[OID_CDP_DEVICE_ID + index] = v2c.OctetString(neighbour.remote_device_id)
        mib[OID_CDP_DEVICE_PORT + index] = v2c.OctetString(neighbour.remote_port_id)
        mib[OID_CDP_PLATFORM + index] = v2c.OctetString(neighbour.remote_platform)

    return mib


def _oid_text(key: tuple[int, ...]) -> str:
    """Dotted OID text for a tuple key (the form pyasn1 expects)."""
    return ".".join(str(part) for part in key)


def _var_bind(oid: str, value) -> tuple:
    """A ``(ObjectIdentifier, value)`` pair.

    The OID is passed as a pyasn1 ObjectIdentifier rather than a raw integer
    tuple so the wire form is unambiguous.
    """
    return (v2c.ObjectIdentifier(oid), value)


class LabSnmpAgent:
    """A real SNMPv2c command responder for one lab endpoint."""

    def __init__(
        self,
        profile: LabDeviceProfile,
        host: str = "127.0.0.1",
        port: int = 1161,
        community: str = "public",
        wrong_community_response: str = "drop",
    ) -> None:
        """`wrong_community_response` mirrors the two real v2c behaviours:

        * ``"drop"`` (default, what net-snmp does): the PDU is silently ignored,
          so the client sees a timeout - the honest case the collector must
          survive without mislabelling it "unsupported";
        * ``"reject"``: answers with errorStatus authorizationError, which is
          also legitimate v2c and lets a test cover the credential-rejection
          path without paying a timeout on every request.
        """
        self.profile = profile
        self.host = host
        self.port = port
        self.community = community
        self.wrong_community_response = wrong_community_response
        self.mib = build_mib(profile)
        self._sorted_oids = sorted(self.mib)
        self._transport = None
        # Non-secret counters, for test assertions. Never a credential.
        self.served = 0
        self.dropped_wrong_community = 0
        self.rejected_wrong_community = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._transport = udp.UdpTransport()
        self._transport.registerCbFun(self._on_datagram)
        self._transport.openServerMode((self.host, self.port))
        # `openServerMode` only SCHEDULES the endpoint creation. Await that
        # future so a bind failure (port already in use, address not local)
        # surfaces as an exception instead of leaving an endpoint that silently
        # receives nothing - which would look exactly like an unreachable device.
        bind = getattr(self._transport, "_lport", None)
        if bind is not None:
            await bind
        logger.info("lab SNMP endpoint %s listening on %s:%s",
                    self.profile.sys_name, self.host, self.port)

    async def stop(self) -> None:
        if self._transport is not None:
            try:
                self._transport.closeTransport()
            finally:
                self._transport = None

    # -- SNMP --------------------------------------------------------------

    def _next_oid(self, oid: tuple[int, ...]) -> tuple[int, ...] | None:
        """Lexicographically next OID in this endpoint's MIB (GETNEXT semantics)."""
        for candidate in self._sorted_oids:
            if candidate > oid:
                return candidate
        return None

    def _on_datagram(self, transport, transport_address, datagram) -> None:
        try:
            reply = self._handle(datagram)
        except Exception:  # noqa: BLE001 - a malformed PDU must not kill the agent
            logger.warning("lab SNMP endpoint dropped an undecodable datagram",
                           exc_info=True)
            return
        if reply is not None:
            transport.sendMessage(reply, transport_address)

    def _handle(self, datagram: bytes) -> bytes | None:
        version = api.decodeMessageVersion(datagram)
        pmod = api.protoModules.get(version)
        if pmod is None:
            return None

        req_msg, _rest = decoder.decode(datagram, asn1Spec=pmod.Message())
        # `getCommunity` returns a pyasn1 OctetString: coerce it to text before
        # comparing with the configured community, or the comparison is a type
        # mismatch that rejects every valid request.
        community = str(pmod.apiMessage.getCommunity(req_msg))
        if community != str(self.community):
            if self.wrong_community_response == "reject":
                self.rejected_wrong_community += 1
                reject = pmod.apiMessage.getResponse(req_msg)
                reject_pdu = pmod.apiMessage.getPDU(reject)
                pmod.apiPDU.setErrorStatus(reject_pdu, "authorizationError")
                pmod.apiPDU.setErrorIndex(reject_pdu, 0)
                return encoder.encode(reject)
            # A real v2c agent answers nothing when the community is wrong.
            self.dropped_wrong_community += 1
            return None

        req_pdu = pmod.apiMessage.getPDU(req_msg)
        rsp_msg = pmod.apiMessage.getResponse(req_msg)
        rsp_pdu = pmod.apiMessage.getPDU(rsp_msg)
        get_next = req_pdu.isSameTypeWith(pmod.GetNextRequestPDU())
        get_bulk = req_pdu.isSameTypeWith(pmod.GetBulkRequestPDU())
        get = req_pdu.isSameTypeWith(pmod.GetRequestPDU())

        if not (get or get_next or get_bulk):
            # SET and friends are not advertised by this endpoint.
            pmod.apiPDU.setErrorStatus(rsp_pdu, "noSuchName")
            pmod.apiPDU.setErrorIndex(rsp_pdu, 0)
            return encoder.encode(rsp_msg)

        var_binds = []
        for index, (oid, _value) in enumerate(pmod.apiPDU.getVarBinds(req_pdu)):
            key = tuple(int(part) for part in str(oid).split("."))
            if get:
                if key in self.mib:
                    var_binds.append(_var_bind(_oid_text(key), self.mib[key]))
                    continue
                pmod.apiPDU.setErrorStatus(rsp_pdu, "noSuchName")
                pmod.apiPDU.setErrorIndex(rsp_pdu, index + 1)
                return encoder.encode(rsp_msg)
            target = self._next_oid(key)
            if target is None:
                # Past the end of the MIB: the walker stops on endOfMibView.
                var_binds.append(_var_bind(_oid_text(key), v2c.EndOfMibView("")))
                continue
            var_binds.append(_var_bind(_oid_text(target), self.mib[target]))

        pmod.apiPDU.setVarBinds(rsp_pdu, var_binds)
        pmod.apiPDU.setErrorStatus(rsp_pdu, "noError")
        pmod.apiPDU.setErrorIndex(rsp_pdu, 0)
        self.served += 1
        return encoder.encode(rsp_msg)


# ---------------------------------------------------------------------------
# The two lab endpoints. A's uplink (Gi0/1) is B's uplink (Te1/1): the
# adjacency each agent reports is the link that actually exists between them.
# ---------------------------------------------------------------------------

LAB_SWITCH_A = LabDeviceProfile(
    sys_name="hsdeep-lab-sw-a",
    sys_descr="HSDEEP Lab SNMP Agent - validation endpoint A (switching, 3 ports)",
    interfaces=[
        LabInterface(1, "Gi0/1", speed_bps=1_000_000_000,
                     in_octets=123_456_789_012, out_octets=98_765_432_109),
        LabInterface(2, "Gi0/2", admin_status=ADMIN_DOWN, oper_status=OPER_DOWN,
                     speed_bps=1_000_000_000),
        LabInterface(3, "Gi0/3", speed_bps=100_000_000,
                     in_octets=5_555_555, out_octets=4_444_444,
                     in_errors=3, out_errors=2),
    ],
    lldp_neighbours=[
        LabLldpNeighbour(
            local_index=1,
            remote_sys_name="hsdeep-lab-sw-b",
            remote_port_id="Te1/1",
            remote_chassis_id="hsdeep-lab-sw-b",
            remote_management_address="127.0.0.2",
            remote_port_desc="TenGigabitEthernet1/1",
        )
    ],
)

LAB_SWITCH_B = LabDeviceProfile(
    sys_name="hsdeep-lab-sw-b",
    sys_descr="HSDEEP Lab SNMP Agent - validation endpoint B (switching, 2 ports)",
    interfaces=[
        LabInterface(1, "Te1/1", speed_bps=1_000_000_000,
                     in_octets=222_222_222_222, out_octets=111_111_111_111),
        LabInterface(2, "Gi1/2", oper_status=OPER_DOWN, speed_bps=1_000_000_000,
                     in_octets=10, out_octets=20, out_errors=1),
    ],
    lldp_neighbours=[
        LabLldpNeighbour(
            local_index=1,
            remote_sys_name="hsdeep-lab-sw-a",
            remote_port_id="Gi0/1",
            remote_chassis_id="hsdeep-lab-sw-a",
            remote_management_address="127.0.0.1",
            remote_port_desc="GigabitEthernet0/1",
        )
    ],
)

#: profile name -> (profile, host, port). Ports live in the unprivileged range
#: on purpose: the collector takes the port from the MonitoringSource row, so a
#: lab endpoint never needs port 161 on the host.
LAB_PROFILES: dict[str, tuple[LabDeviceProfile, str, int]] = {
    "a": (LAB_SWITCH_A, "127.0.0.1", 1161),
    "b": (LAB_SWITCH_B, "127.0.0.2", 1162),
}


async def run_profiles(
    names: list[str], seconds: float | None, community: str = "public"
) -> list[LabSnmpAgent]:
    agents: list[LabSnmpAgent] = []
    for name in names:
        profile, host, port = LAB_PROFILES[name]
        agent = LabSnmpAgent(profile, host=host, port=port, community=community)
        await agent.start()
        agents.append(agent)
    try:
        if seconds is None:
            await asyncio.Event().wait()
        else:
            await asyncio.sleep(seconds)
    finally:
        for agent in agents:
            await agent.stop()
    return agents


def main() -> None:
    parser = argparse.ArgumentParser(description="Lab SNMP endpoints (Phase 9.7B)")
    parser.add_argument("--profile", choices=[*LAB_PROFILES, "both"], default="both")
    parser.add_argument("--seconds", type=float, default=None,
                        help="run for this long then stop (default: until interrupted)")
    parser.add_argument("--community", default="public")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    names = list(LAB_PROFILES) if args.profile == "both" else [args.profile]
    print("lab endpoints: " + ", ".join(
        f"{name}={LAB_PROFILES[name][1]}:{LAB_PROFILES[name][2]}" for name in names
    ))
    asyncio.run(run_profiles(names, args.seconds, args.community))


if __name__ == "__main__":
    main()
