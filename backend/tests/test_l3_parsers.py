"""
L3 topology: address normalisation and the per-collector address/route parsers.

Pure-function tests - no network, no database. Vendor payload shapes follow the
documented APIs (FortiOS REST, PAN-OS XML API, iproute2 JSON, IP-MIB /
IP-FORWARD-MIB walk rows).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.collectors.fortigate import FortiGateCollector
from app.collectors.linux_ssh import _parse_ip_addr_json, _parse_ip_route_json
from app.collectors.paloalto import _parse_addresses as pan_addresses
from app.collectors.paloalto import _parse_routes as pan_routes
from app.collectors.snmp import _coerce_ipv4
from app.collectors import snmp_l3
from app.core.ip_addressing import (
    is_topological,
    normalise_vrf,
    parse_interface_address,
    parse_next_hop,
    parse_route_destination,
)


# ---------------------------------------------------------------------------
# ip_addressing
# ---------------------------------------------------------------------------
class TestParseInterfaceAddress:
    def test_cidr_form(self):
        p = parse_interface_address("10.1.2.3/24")
        assert (p.family, p.address, p.prefix_length, p.network) == (4, "10.1.2.3", 24, "10.1.2.0/24")

    def test_fortios_space_mask_form(self):
        p = parse_interface_address("192.168.1.1 255.255.255.0")
        assert p.network == "192.168.1.0/24"

    def test_separate_netmask(self):
        assert parse_interface_address("10.0.0.9", "255.255.255.252").prefix_length == 30

    def test_integer_prefix(self):
        assert parse_interface_address("10.0.0.9", 8).network == "10.0.0.0/8"

    def test_ipv6_with_zone_index(self):
        p = parse_interface_address("fe80::1%eth0", 64)
        assert p.family == 6 and p.address == "fe80::1"

    def test_malformed_returns_none(self):
        assert parse_interface_address("not-an-ip/24") is None
        assert parse_interface_address("10.0.0.1/33") is None
        assert parse_interface_address("10.0.0.1", "255.0.255.0") is None  # non-contiguous
        assert parse_interface_address("10.0.0.1") is None  # no prefix at all
        assert parse_interface_address(None) is None

    def test_topological_filter(self):
        assert is_topological(parse_interface_address("10.0.0.1/24"))
        assert not is_topological(parse_interface_address("10.0.0.1/32"))
        assert not is_topological(parse_interface_address("127.0.0.1/8"))
        assert not is_topological(parse_interface_address("169.254.1.1/16"))
        assert not is_topological(parse_interface_address("fe80::1/64"))


class TestRouteHelpers:
    def test_default_keyword(self):
        assert parse_route_destination("default") == ("0.0.0.0/0", 0)

    def test_host_bits_cleared(self):
        assert parse_route_destination("10.1.2.3/8") == ("10.0.0.0/8", 8)

    def test_bare_address_is_host_route(self):
        assert parse_route_destination("10.1.2.3") == ("10.1.2.3/32", 32)

    def test_unspecified_next_hop_is_none(self):
        assert parse_next_hop("0.0.0.0") is None
        assert parse_next_hop("ethernet1/1") is None
        assert parse_next_hop("203.0.113.1") == "203.0.113.1"

    def test_global_vrf_spellings(self):
        for spelling in (None, "", "0", 0, "default", "main", "global", "-"):
            assert normalise_vrf(spelling) == "-"
        assert normalise_vrf("CUST-A") == "CUST-A"


# ---------------------------------------------------------------------------
# FortiGate
# ---------------------------------------------------------------------------
def _forti() -> FortiGateCollector:
    return FortiGateCollector(source=SimpleNamespace(id="s", hostname="fgt", password="x", api_key=None))


class TestFortiGate:
    def test_space_separated_ip_and_secondaries(self):
        raw = {
            "status": {"results": {"hostname": "fgt"}},
            "interfaces": {"results": [
                {"name": "port1", "ip": "192.168.1.1 255.255.255.0",
                 "secondaryip": [{"id": 1, "ip": "192.168.2.1 255.255.255.0"}]},
                {"name": "port2", "ip": "0.0.0.0 0.0.0.0"},
                {"name": "wan1", "ip": "203.0.113.2 255.255.255.248", "vrf": 0},
            ]},
        }
        bundle = _forti().normalize(raw)
        got = {(a.interface_name, a.address, a.prefix_length, a.is_primary) for a in bundle.addresses}
        assert got == {
            ("port1", "192.168.1.1", 24, True),
            ("port1", "192.168.2.1", 24, False),
            ("wan1", "203.0.113.2", 29, True),
        }
        assert bundle.l3_status == {"addresses": "collected"}

    def test_split_netmask_fixture_shape_still_parses(self):
        raw = {"interfaces": {"results": [{"name": "port1", "ip": "192.168.1.1", "netmask": "255.255.255.0"}]}}
        assert [a.prefix_length for a in _forti().normalize(raw).addresses] == [24]

    def test_routes(self):
        raw = {
            "interfaces": {"results": []},
            "routes_status": "collected",
            "routes": {"results": [
                {"ip_mask": "0.0.0.0/0", "type": "static", "gateway": "203.0.113.1",
                 "interface": "wan1", "distance": 10, "metric": 0, "vrf": 0},
                {"ip_mask": "192.168.1.0/24", "type": "connect", "gateway": "0.0.0.0",
                 "interface": "port1", "distance": 0},
                {"ip_mask": "10.9.0.0/16", "type": "bgp", "gateway": "10.255.0.1", "vrf": 3},
            ]},
        }
        bundle = _forti().normalize(raw)
        routes = {r.destination: r for r in bundle.routes}
        assert routes["0.0.0.0/0"].protocol == "static"
        assert routes["0.0.0.0/0"].next_hop == "203.0.113.1"
        assert routes["0.0.0.0/0"].administrative_distance == 10
        assert routes["192.168.1.0/24"].protocol == "connected"
        assert routes["192.168.1.0/24"].next_hop is None
        assert routes["10.9.0.0/16"].vrf == "3"
        assert bundle.l3_status == {"addresses": "empty", "routes": "collected"}

    def test_failed_reads_are_reported_not_emptied(self):
        raw = {"interfaces": {"results": []}, "interfaces_failed": True, "routes_status": "failed"}
        assert _forti().normalize(raw).l3_status == {"addresses": "failed", "routes": "failed"}

    def test_old_raw_without_l3_keys_reports_nothing(self):
        assert _forti().normalize({}).l3_status == {}


# ---------------------------------------------------------------------------
# Palo Alto
# ---------------------------------------------------------------------------
PAN_INTERFACES = """<response status="success"><result>
<ifnet>
  <entry><name>ethernet1/1</name><fwd>vr:default</fwd><ip>203.0.113.2/29</ip><addr/></entry>
  <entry><name>ethernet1/2</name><fwd>vr:default</fwd><ip>10.0.0.1/24</ip>
    <addr><member>10.0.0.1/24</member><member>10.0.1.1/24</member></addr></entry>
  <entry><name>ethernet1/3</name><fwd>vr:CUST</fwd><ip>N/A</ip></entry>
</ifnet>
<hw><entry><name>ethernet1/1</name><state>up</state></entry></hw>
</result></response>"""

PAN_ROUTES = """<response status="success"><result>
<entry><virtual-router>default</virtual-router><destination>0.0.0.0/0</destination>
  <nexthop>203.0.113.1</nexthop><metric>10</metric><flags>A S</flags><interface>ethernet1/1</interface></entry>
<entry><virtual-router>default</virtual-router><destination>10.0.0.0/24</destination>
  <nexthop>0.0.0.0</nexthop><metric>0</metric><flags>A C</flags><interface>ethernet1/2</interface></entry>
<entry><virtual-router>default</virtual-router><destination>10.0.0.1/32</destination>
  <nexthop>0.0.0.0</nexthop><flags>A H</flags></entry>
<entry><virtual-router>default</virtual-router><destination>172.16.0.0/12</destination>
  <nexthop>10.0.0.254</nexthop><flags>S</flags></entry>
</result></response>"""


class TestPaloAlto:
    def test_addresses(self):
        got = {(a.interface_name, a.address, a.vrf, a.is_primary) for a in pan_addresses(PAN_INTERFACES, "pa")}
        assert got == {
            ("ethernet1/1", "203.0.113.2", "-", True),
            ("ethernet1/2", "10.0.0.1", "-", True),
            ("ethernet1/2", "10.0.1.1", "-", False),
        }

    def test_routes_keep_active_non_host_entries(self):
        routes = {r.destination: r for r in pan_routes(PAN_ROUTES, "pa")}
        assert set(routes) == {"0.0.0.0/0", "10.0.0.0/24"}  # host + inactive dropped
        assert routes["0.0.0.0/0"].protocol == "static"
        assert routes["0.0.0.0/0"].metric == 10
        assert routes["10.0.0.0/24"].protocol == "connected"

    def test_garbage_xml_is_empty(self):
        assert pan_addresses("<not xml", "pa") == []
        assert pan_routes("", "pa") == []


# ---------------------------------------------------------------------------
# Linux (iproute2 JSON)
# ---------------------------------------------------------------------------
IP_ADDR = json.dumps([
    {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
    {"ifname": "eth0", "addr_info": [
        {"family": "inet", "local": "10.0.0.5", "prefixlen": 24},
        {"family": "inet", "local": "10.0.0.6", "prefixlen": 24, "secondary": True},
        {"family": "inet6", "local": "2001:db8::5", "prefixlen": 64},
    ]},
    {"ifname": "docker0", "addr_info": [{"family": "inet", "local": "172.17.0.1", "prefixlen": 16}]},
])
IP_ROUTE = json.dumps([
    {"dst": "default", "gateway": "10.0.0.1", "dev": "eth0", "protocol": "dhcp", "metric": 100},
    {"dst": "10.0.0.0/24", "dev": "eth0", "protocol": "kernel", "scope": "link"},
    {"dst": "172.17.0.0/16", "dev": "docker0", "protocol": "kernel", "scope": "link"},
])


class TestLinux:
    def test_addresses_skip_loopback_and_container_bridges(self):
        got = {(a.interface_name, a.address, a.is_primary) for a in _parse_ip_addr_json(IP_ADDR, "h")}
        assert got == {("eth0", "10.0.0.5", True), ("eth0", "10.0.0.6", False), ("eth0", "2001:db8::5", True)}

    def test_routes(self):
        routes = {r.destination: r for r in _parse_ip_route_json(IP_ROUTE, "h")}
        assert set(routes) == {"0.0.0.0/0", "10.0.0.0/24"}
        assert routes["0.0.0.0/0"].protocol == "static"
        assert routes["0.0.0.0/0"].next_hop == "10.0.0.1"
        assert routes["10.0.0.0/24"].protocol == "connected"

    def test_non_json_output_is_unavailable(self):
        assert _parse_ip_addr_json('Option "-j" is unknown', "h") is None
        assert _parse_ip_route_json("", "h") is None


# ---------------------------------------------------------------------------
# SNMP (IP-MIB / IP-FORWARD-MIB)
# ---------------------------------------------------------------------------
NAMES = {"1": "Gi0/1", "2": "Gi0/2"}


class TestSnmpAddressTables:
    def test_ip_addr_table_with_raw_octet_mask(self):
        rows = [(f"{snmp_l3.IP_ADDR_IFINDEX}.10.0.0.1", "1")]
        masks = [(f"{snmp_l3.IP_ADDR_NETMASK}.10.0.0.1", "\xff\xff\xff\x00")]
        got = snmp_l3.parse_ip_addr_table(
            rows, masks, device_external_id="sw", if_names=NAMES, coerce_ipv4=_coerce_ipv4
        )
        assert [(a.address, a.prefix_length, a.interface_external_id, a.interface_name) for a in got] == [
            ("10.0.0.1", 24, "1", "Gi0/1")
        ]

    def test_ip_address_table_ipv4_and_ipv6(self):
        v6 = ".".join(["32", "1", "13", "184"] + ["0"] * 11 + ["1"])  # 2001:db8::1
        rows = [
            (f"{snmp_l3.IP_ADDRESS_IFINDEX}.1.4.10.0.0.1", "1"),
            (f"{snmp_l3.IP_ADDRESS_IFINDEX}.2.16.{v6}", "2"),
        ]
        prefixes = [
            (f"{snmp_l3.IP_ADDRESS_PREFIX}.1.4.10.0.0.1", "1.3.6.1.2.1.4.32.1.5.1.1.4.10.0.0.0.24"),
            (f"{snmp_l3.IP_ADDRESS_PREFIX}.2.16.{v6}", "1.3.6.1.2.1.4.32.1.5.2.2.16.1.64"),
        ]
        got = {(a.address, a.prefix_length) for a in snmp_l3.parse_ip_address_table(
            rows, prefixes, device_external_id="sw", if_names=NAMES)}
        assert got == {("10.0.0.1", 24), ("2001:db8::1", 64)}

    def test_zero_dot_zero_prefix_is_skipped(self):
        rows = [(f"{snmp_l3.IP_ADDRESS_IFINDEX}.1.4.10.0.0.1", "1")]
        prefixes = [(f"{snmp_l3.IP_ADDRESS_PREFIX}.1.4.10.0.0.1", "0.0")]
        assert snmp_l3.parse_ip_address_table(rows, prefixes, device_external_id="sw", if_names=NAMES) == []

    def test_merge_prefers_first_table(self):
        a = snmp_l3.parse_ip_address_table(
            [(f"{snmp_l3.IP_ADDRESS_IFINDEX}.1.4.10.0.0.1", "1")],
            [(f"{snmp_l3.IP_ADDRESS_PREFIX}.1.4.10.0.0.1", "1.3.6.1.2.1.4.32.1.5.1.1.4.10.0.0.0.24")],
            device_external_id="sw", if_names=NAMES,
        )
        b = snmp_l3.parse_ip_addr_table(
            [(f"{snmp_l3.IP_ADDR_IFINDEX}.10.0.0.1", "2")],
            [(f"{snmp_l3.IP_ADDR_NETMASK}.10.0.0.1", "255.255.0.0")],
            device_external_id="sw", if_names=NAMES, coerce_ipv4=_coerce_ipv4,
        )
        merged = snmp_l3.merge_addresses(a, b)
        assert [(m.address, m.prefix_length) for m in merged] == [("10.0.0.1", 24)]


class TestSnmpRouteTables:
    def test_ip_cidr_route_table(self):
        index = "0.0.0.0.0.0.0.0.0.203.0.113.1"
        got = snmp_l3.parse_ip_cidr_route_table(
            [(f"{snmp_l3.IP_CIDR_ROUTE_PROTO}.{index}", "3")],
            [(f"{snmp_l3.IP_CIDR_ROUTE_IFINDEX}.{index}", "2")],
            device_external_id="r", if_names=NAMES,
        )
        assert [(r.destination, r.next_hop, r.protocol, r.outgoing_interface) for r in got] == [
            ("0.0.0.0/0", "203.0.113.1", "static", "Gi0/2")
        ]

    def test_inet_cidr_route_table(self):
        # dest 10.1.0.0/16, policy 0.0 (len 2), next hop 10.0.0.2
        index = "1.4.10.1.0.0.16.2.0.0.1.4.10.0.0.2"
        got = snmp_l3.parse_inet_cidr_route_table(
            [(f"{snmp_l3.INET_CIDR_ROUTE_PROTO}.{index}", "14")],
            [], device_external_id="r", if_names=NAMES,
        )
        assert [(r.destination, r.next_hop, r.protocol) for r in got] == [("10.1.0.0/16", "10.0.0.2", "bgp")]

    def test_legacy_ip_route_table(self):
        got = snmp_l3.parse_ip_route_table(
            [(f"{snmp_l3.IP_ROUTE_PROTO}.10.0.0.0", "2")],
            [(f"{snmp_l3.IP_ROUTE_MASK}.10.0.0.0", "255.255.255.0")],
            [(f"{snmp_l3.IP_ROUTE_NEXT_HOP}.10.0.0.0", "0.0.0.0")],
            [(f"{snmp_l3.IP_ROUTE_IFINDEX}.10.0.0.0", "1")],
            device_external_id="r", if_names=NAMES, coerce_ipv4=_coerce_ipv4,
        )
        assert [(r.destination, r.next_hop, r.protocol) for r in got] == [("10.0.0.0/24", None, "connected")]


class TestSnmpCollectionStatus:
    async def test_newest_route_table_wins_and_others_are_not_walked(self):
        walked: list[str] = []
        index = "0.0.0.0.0.0.0.0.0.203.0.113.1"

        async def walk(base, max_rows=None):
            walked.append(base)
            if base == snmp_l3.IP_CIDR_ROUTE_PROTO:
                return [(f"{base}.{index}", "3")], None
            return [], None

        raw = await snmp_l3.collect_l3(walk)
        assert raw["route_table"] == "ip_cidr"
        assert snmp_l3.IP_ROUTE_PROTO not in walked
        _addresses, routes, status = snmp_l3.normalize_l3(
            raw, device_external_id="r", if_names={}, coerce_ipv4=_coerce_ipv4
        )
        assert [r.destination for r in routes] == ["0.0.0.0/0"]
        assert status == {"addresses": "empty", "routes": "collected"}

    async def test_every_walk_erroring_is_failed(self):
        async def walk(base, max_rows=None):
            return [], "RequestTimedOut"

        raw = await snmp_l3.collect_l3(walk)
        _a, _r, status = snmp_l3.normalize_l3(raw, device_external_id="r", if_names={}, coerce_ipv4=_coerce_ipv4)
        assert status == {"addresses": "failed", "routes": "failed"}

    async def test_truncated_route_walk_is_partial(self):
        async def walk(base, max_rows=None):
            if base == snmp_l3.INET_CIDR_ROUTE_PROTO:
                return [(f"{base}.1.4.10.{i // 256}.{i % 256}.0.24.2.0.0.1.4.10.0.0.1", "14")
                        for i in range(snmp_l3.MAX_ROUTE_ROWS)], None
            return [], None

        raw = await snmp_l3.collect_l3(walk)
        _a, _r, status = snmp_l3.normalize_l3(raw, device_external_id="r", if_names={}, coerce_ipv4=_coerce_ipv4)
        assert status["routes"] == "partial"
