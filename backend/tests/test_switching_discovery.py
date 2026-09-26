"""
Phase 3B tests: vendor-neutral switching discovery (VLAN / STP / LAG) and
Cisco/Aruba recognition.

All decoding is verified against real wire encodings - in particular the Q-BRIDGE
`PortList` bitmap, whose bit order (MSB = lowest port) is easy to get backwards
and would silently mis-attribute every port in a VLAN.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.collectors import switching as sw
from app.collectors.base import NormalizedBundle, NormalizedDevice
from app.collectors.snmp import SNMPCollector
from app.repositories.device_repository import DeviceRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.sensor_repository import SensorRepository

# Enterprise OIDs used by real devices.
CISCO_CATALYST_2960 = "1.3.6.1.4.1.9.1.716"
ARUBA_2930F = "1.3.6.1.4.1.14823.1.2.3"
HPE_PROCurve = "1.3.6.1.4.1.11.2.3.7.11.60"
JUNIPER_EX = "1.3.6.1.4.1.2636.1.1.1.2.9"


def _source(hostname="sw-a.lab", ip="10.0.0.1"):
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="snmp-a", source_type="snmp",
        hostname=hostname, ip_address=ip, port=None, protocol="snmp",
        snmp_version="v2c", username=None, api_key=None, password="public",
        config={}, verify_ssl=None,
    )


class TestPortListDecoding:
    """RFC 4363: octet 1 = ports 1-8, and within an octet the MOST SIGNIFICANT
    bit is the LOWEST numbered port."""

    def test_first_octet_msb_is_port_one(self):
        assert sw.decode_port_list("\x80") == {1}

    def test_first_octet_lsb_is_port_eight(self):
        assert sw.decode_port_list("\x01") == {8}

    def test_full_first_octet_is_ports_one_to_eight(self):
        assert sw.decode_port_list("\xff") == {1, 2, 3, 4, 5, 6, 7, 8}

    def test_second_octet_continues_at_port_nine(self):
        assert sw.decode_port_list("\x00\x80") == {9}

    def test_realistic_two_octet_bitmap(self):
        # 0x80 -> port 1 ; 0x02 -> (port 9 + 6) = port 15
        assert sw.decode_port_list("\x80\x02\x00\x00\x00\x00\x00\x00") == {1, 15}

    def test_sparse_high_ports(self):
        # octet 3 (index 2) bit 0 -> port 17
        assert sw.decode_port_list("\x00\x00\x80") == {17}

    def test_empty_and_absent_are_unknown_not_zero_ports(self):
        """A zero-length bitmap is indistinguishable from a column that was not
        returned, so it must NOT be reported as 'no member ports'."""
        assert sw.decode_port_list("") is None
        assert sw.decode_port_list(None) is None

    def test_all_zero_bitmap_is_a_real_empty_set(self):
        assert sw.decode_port_list("\x00\x00") == set()


class TestScalarDecoders:

    def test_mac(self):
        assert sw.decode_mac("\xaa\xbb\xcc\xdd\xee\xff") == "aa:bb:cc:dd:ee:ff"

    def test_mac_wrong_length_is_none(self):
        assert sw.decode_mac("\xaa\xbb") is None
        assert sw.decode_mac("") is None

    def test_int(self):
        assert sw.decode_int("32768") == 32768
        assert sw.decode_int("-1") == -1

    def test_int_rejects_non_numeric(self):
        assert sw.decode_int("abc") is None
        assert sw.decode_int("") is None
        assert sw.decode_int(None) is None

    def test_stp_protocol_enum(self):
        assert sw.stp_protocol_name("3") == "ieee8021d"
        assert sw.stp_protocol_name("2") == "decLb100"

    def test_stp_protocol_unknown_code_is_none(self):
        assert sw.stp_protocol_name("9") is None

    def test_stp_port_states(self):
        assert sw.stp_port_state_name("5") == "forwarding"
        assert sw.stp_port_state_name("2") == "blocking"
        assert sw.stp_port_state_name("1") == "disabled"

    def test_stp_port_state_unknown_code_is_none(self):
        assert sw.stp_port_state_name("99") is None

    def test_vlan_id_range_enforced(self):
        assert sw.vlan_id_from_index("100") == 100
        assert sw.vlan_id_from_index("1") == 1
        assert sw.vlan_id_from_index("4094") == 4094
        # Out of 802.1Q range or not a plain integer -> rejected, not truncated.
        assert sw.vlan_id_from_index("0") is None
        assert sw.vlan_id_from_index("4095") is None
        assert sw.vlan_id_from_index("1.2") is None
        assert sw.vlan_id_from_index("x") is None

    def test_bridge_port_index(self):
        assert sw.bridge_port_from_index("7") == 7
        assert sw.bridge_port_from_index("1.2") is None


class TestVendorRecognition:

    def test_cisco_from_enterprise_number(self):
        assert sw.recognise_vendor(CISCO_CATALYST_2960, None) == "Cisco"

    def test_aruba_from_enterprise_number(self):
        assert sw.recognise_vendor(ARUBA_2930F, None) == "Aruba"

    def test_hpe_from_enterprise_number(self):
        assert sw.recognise_vendor(HPE_PROCurve, None) == "HPE"

    def test_juniper_from_enterprise_number(self):
        assert sw.recognise_vendor(JUNIPER_EX, None) == "Juniper"

    def test_unknown_enterprise_is_none_and_not_defaulted(self):
        """An unrecognised vendor must never be reported as Cisco or 'Generic'."""
        assert sw.recognise_vendor("1.3.6.1.4.1.99999.1.2", None) is None

    def test_enterprise_prefix_must_not_swallow_longer_pen(self):
        """`.1.3.6.1.4.1.11` (HPE) is a prefix of the unrelated `...1100` OID;
        a naive startswith() would mislabel that device as HPE."""
        assert sw.recognise_vendor("1.3.6.1.4.1.1100.1.2.3", None) is None
        assert sw.enterprise_from_oid("1.3.6.1.4.1.1100.1.2.3") is None

    def test_longest_match_wins(self):
        # A device registered under a sub-enterprise still resolves to the
        # closest matching PEN, never a shorter unrelated one.
        assert sw.enterprise_from_oid("1.3.6.1.4.1.14823.1.2.3") == "1.3.6.1.4.1.14823"

    def test_sysdescr_fallback_when_oid_unavailable(self):
        assert sw.recognise_vendor(None, "Cisco IOS Software, C2960 Software") == "Cisco"
        assert sw.recognise_vendor(None, "ArubaOS-CX ... ") == "Aruba"

    def test_sysdescr_fallback_not_used_when_oid_identifies_vendor(self):
        # The authoritative signal wins even if sysDescr looks like another vendor.
        assert sw.recognise_vendor(CISCO_CATALYST_2960, "Aruba something") == "Cisco"

    def test_no_signal_is_none(self):
        assert sw.recognise_vendor(None, None) is None
        assert sw.recognise_vendor("", "unknown device") is None

    def test_classify_switch_from_sysdescr(self):
        assert sw.classify_switch("other", "Cisco IOS Catalyst 2960") == "switch"
        assert sw.classify_switch("other", "Aruba 2930F Switch") == "switch"

    def test_classify_router_from_sysdescr(self):
        assert sw.classify_switch("other", "Cisco ASR 1001-X") == "router"

    def test_classify_does_not_override_a_real_classification(self):
        """A server that happens to mention a switch must stay a server."""
        assert sw.classify_switch("server", "Cisco") == "server"
        assert sw.classify_switch("vm", "switch") == "vm"


def _switching_raw(*, vlan=True, stp=True, lag=True, status=None) -> dict:
    """A switching block shaped exactly like `_collect_switching()` output.

    Bridge port 1 -> ifIndex 101 -> Gi1/0/1 ; bridge port 2 -> ifIndex 102 -> Gi1/0/2
    """
    data: dict = {
        "sys_object_id": CISCO_CATALYST_2960,
        "vlan_names": {}, "vlan_egress": {}, "vlan_untagged": {},
        "pvid": {}, "bridge_port_if_index": {1: 101, 2: 102},
        "stp": {}, "lag_aggregators": {}, "lag_port_lists": {},
        "status": status or {"vlan": "collected", "stp": "collected", "lag": "collected"},
    }
    if vlan:
        data["vlan_names"] = {10: "USERS", 20: "VOICE"}
        # PortList bitmaps: ONE octet covers ports 1-8. So ports {1,2} is 0xc0
        # (0x80 = MSB = port 1, 0x40 = port 2). Writing "\x80\x40" would mean
        # port 1 and port 10, because the second octet starts at port 9.
        # VLAN 10: ports 1-2 tagged, port 2 untagged
        data["vlan_egress"] = {10: "\xc0", 20: "\x40"}
        data["vlan_untagged"] = {10: "\x40", 20: "\x80"}
        data["pvid"] = {1: 20, 2: 10}
    if stp:
        data["stp"] = {
            "protocol": "3", "priority": "32768", "top_changes": "7",
            "designated_root": "\x00\x11\x22\x33\x44\x55",
            "root_cost": "20000", "root_port": "1",
            "port_state": {1: "forwarding", 2: "blocking"},
            "port_path_cost": {1: 19, 2: 100},
        }
    if lag:
        data["lag_aggregators"] = {500: "\xaa\xbb\xcc\xdd\xee\xff"}
        # Ports {1,2} again - see the PortList note above.
        data["lag_port_lists"] = {500: "\xc0"}
    return data


class TestSwitchingMetadata:
    IF_MAP = {101: "Gi1/0/1", 102: "Gi1/0/2", 500: "Po1"}

    def test_returns_none_when_nothing_was_discovered(self):
        assert sw.build_switching_metadata(None, self.IF_MAP) is None
        assert sw.build_switching_metadata({}, self.IF_MAP) is None

    def test_vlan_inventory_with_names(self):
        meta = sw.build_switching_metadata(_switching_raw(), self.IF_MAP)
        vlans = {v["vlan_id"]: v for v in meta["vlans"]}
        assert set(vlans) == {10, 20}
        assert vlans[10]["name"] == "USERS"
        assert vlans[20]["name"] == "VOICE"
        assert meta["vlan_count"] == 2

    def test_vlan_member_ports_resolved_to_interface_names(self):
        """Bridge ports must be translated through ifIndex to real names; a
        bridge port number leaking into the output would be unusable."""
        meta = sw.build_switching_metadata(_switching_raw(), self.IF_MAP)
        vlans = {v["vlan_id"]: v for v in meta["vlans"]}
        assert vlans[10]["member_ports"] == ["Gi1/0/1", "Gi1/0/2"]
        assert vlans[10]["untagged_ports"] == ["Gi1/0/2"]

    def test_pvid_keyed_by_interface_name(self):
        meta = sw.build_switching_metadata(_switching_raw(), self.IF_MAP)
        assert meta["pvid_by_interface"] == {"Gi1/0/1": 20, "Gi1/0/2": 10}

    def test_unresolvable_port_is_omitted_not_invented(self):
        meta = sw.build_switching_metadata(_switching_raw(), {101: "Gi1/0/1"})
        assert meta["pvid_by_interface"] == {"Gi1/0/1": 20}

    def test_stp_block(self):
        meta = sw.build_switching_metadata(_switching_raw(), self.IF_MAP)
        stp = meta["stp"]
        assert stp["protocol"] == "ieee8021d"
        assert stp["priority"] == 32768
        assert stp["designated_root"] == "00:11:22:33:44:55"
        assert stp["root_cost"] == 20000
        assert stp["root_port"] == 1
        assert stp["topology_changes"] == 7
        assert stp["ports"]["Gi1/0/1"] == {"state": "forwarding", "path_cost": 19}
        assert stp["ports"]["Gi1/0/2"] == {"state": "blocking", "path_cost": 100}

    def test_stp_is_root_bridge_is_null_when_bridge_address_unknown(self):
        """`is_root_bridge` must be null, never False, without the bridge address
        to compare against."""
        meta = sw.build_switching_metadata(_switching_raw(), self.IF_MAP)
        assert meta["stp"]["is_root_bridge"] is None

    def test_link_aggregation_members(self):
        meta = sw.build_switching_metadata(_switching_raw(), self.IF_MAP)
        agg = meta["link_aggregation"][0]
        assert agg["name"] == "Po1"
        assert agg["mac_address"] == "aa:bb:cc:dd:ee:ff"
        assert agg["member_ports"] == ["Gi1/0/1", "Gi1/0/2"]

    def test_status_is_carried_so_failed_is_distinguishable_from_empty(self):
        meta = sw.build_switching_metadata(
            _switching_raw(vlan=False, stp=False, lag=False,
                           status={"vlan": "failed", "stp": "unavailable", "lag": "empty"}),
            self.IF_MAP,
        )
        assert meta["vlan_status"] == "failed"
        assert meta["stp_status"] == "unavailable"
        assert meta["lag_status"] == "empty"
        assert "vlans" not in meta
        assert "stp" not in meta
        assert "link_aggregation" not in meta

    def test_no_fabricated_bandwidth_or_speed(self):
        meta = sw.build_switching_metadata(_switching_raw(), self.IF_MAP)
        for forbidden in ("speed_mbps", "bandwidth", "latency_ms", "confidence", "throughput"):
            assert forbidden not in repr(meta)


class TestSnmpSwitchNormalize:
    """End-to-end: raw SNMP -> NormalizedDevice carrying vendor + switching."""

    def _raw(self, switching=None, sys_descr="Cisco IOS Software, Catalyst 2960"):
        return {
            "sys_name": "SW-A",
            "sys_descr": sys_descr,
            "interfaces": {"descr": {"101": "Gi1/0/1", "102": "Gi1/0/2"}},
            "lldp": {k: {} for k in ("sys_name", "chassis_id", "port_id", "port_desc", "man_addr")},
            "cdp": {k: {} for k in ("address", "device_id", "device_port", "platform", "version")},
            "lldp_status": "unavailable",
            "cdp_status": "unavailable",
            "switching": _switching_raw() if switching is None else switching,
        }

    def test_cisco_switch_recognised(self):
        device = SNMPCollector(_source()).normalize(self._raw()).devices[0]
        assert device.vendor == "Cisco"
        assert device.device_type == "switch"

    def test_aruba_switch_recognised_from_oid(self):
        switching = _switching_raw()
        switching["sys_object_id"] = ARUBA_2930F
        raw = self._raw(switching, sys_descr="ArubaOS-CX FL.10.06")
        device = SNMPCollector(_source()).normalize(raw).devices[0]
        assert device.vendor == "Aruba"
        assert device.device_type == "switch"

    def test_unknown_vendor_stays_none(self):
        switching = _switching_raw()
        switching["sys_object_id"] = "1.3.6.1.4.1.99999.7"
        raw = self._raw(switching, sys_descr="SomeSwitch OS 1.0")
        device = SNMPCollector(_source()).normalize(raw).devices[0]
        assert device.vendor is None

    def test_router_is_not_labelled_a_switch(self):
        raw = self._raw(sys_descr="Cisco IOS Software, ASR1001-X Software")
        device = SNMPCollector(_source()).normalize(raw).devices[0]
        assert device.vendor == "Cisco"
        assert device.device_type == "router"

    def test_switching_facts_land_in_custom_metadata(self):
        device = SNMPCollector(_source()).normalize(self._raw()).devices[0]
        meta = device.custom_metadata
        assert meta["vlan_count"] == 2
        assert {v["vlan_id"] for v in meta["vlans"]} == {10, 20}
        # Bridge ports 1/2 map through ifIndex -> the collected ifDescr values.
        assert meta["pvid_by_interface"] == {"Gi1/0/1": 20, "Gi1/0/2": 10}
        assert meta["stp"]["protocol"] == "ieee8021d"
        assert meta["link_aggregation"][0]["member_ports"] == ["Gi1/0/1", "Gi1/0/2"]

    def test_non_switch_carries_no_switching_metadata(self):
        """A server must not get an empty switching block that implies discovery
        ran and found nothing."""
        raw = self._raw(
            switching={"status": {"vlan": "unavailable", "stp": "unavailable",
                                  "lag": "unavailable"}},
            sys_descr="Linux server",
        )
        device = SNMPCollector(_source()).normalize(raw).devices[0]
        assert device.device_type == "other"
        assert device.custom_metadata == {}

    def test_missing_switching_block_does_not_break_normalize(self):
        raw = self._raw()
        raw.pop("switching")
        bundle = SNMPCollector(_source()).normalize(raw)
        assert bundle.devices[0].custom_metadata == {}

    def test_existing_outputs_unchanged_by_switching_discovery(self):
        """Phase 3A guarantees must survive Phase 3B."""
        bundle = SNMPCollector(_source()).normalize(self._raw())
        assert [i.name for i in bundle.interfaces] == ["Gi1/0/1", "Gi1/0/2"]
        assert bundle.neighbors == []
        assert bundle.neighbor_status == {"lldp": "unavailable", "cdp": "unavailable"}


# ---------------------------------------------------------------------------
# Persistence: does the switching metadata actually REACH THE DATABASE?
#
# This is not ceremony. `NormalizedDevice.custom_metadata` was declared in the
# collector contract ("persisted to Device.custom_metadata") but DeviceRepository
# .upsert() did not accept it, so every collector's metadata was silently
# discarded. Phase 3B facts would have been computed and thrown away.
# Fixture order (tenant, monitoring_source_factory, db) keeps `db` teardown first.
# ---------------------------------------------------------------------------

def _saver(db):
    from app.repositories.event_repository import EventRepository
    from app.repositories.metric_repository import MetricRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(
        device_repo=DeviceRepository(db),
        interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db),
        metric_repo=MetricRepository(db),
        event_repo=EventRepository(db),
    )


def _bundle(custom_metadata=None, device_type="switch", vendor="Cisco"):
    return NormalizedBundle(devices=[NormalizedDevice(
        external_id="sw-a", name="SW-A", device_type=device_type,
        vendor=vendor, ip_address="10.0.0.1", status="up",
        custom_metadata=custom_metadata or {},
    )])


class TestSwitchingMetadataPersistence:

    async def test_switching_metadata_is_persisted(
        self, tenant, monitoring_source_factory, db
    ):
        source = await monitoring_source_factory(source_type="snmp")
        raw = {
            "sys_name": "SW-A", "sys_descr": "Cisco IOS Software, Catalyst 2960",
            "interfaces": {"descr": {"101": "Gi1/0/1", "102": "Gi1/0/2"}},
            "lldp": {k: {} for k in ("sys_name", "chassis_id", "port_id", "port_desc", "man_addr")},
            "cdp": {k: {} for k in ("address", "device_id", "device_port", "platform", "version")},
            "lldp_status": "unavailable", "cdp_status": "unavailable",
            "switching": _switching_raw(),
        }
        bundle = SNMPCollector(_source()).normalize(raw)

        await _saver(db).save(source, bundle)

        device = await DeviceRepository(db).get_by_external_id(source.id, "sw-a.lab")
        assert device is not None
        assert device.vendor == "Cisco"
        assert device.device_type == "switch"
        meta = device.custom_metadata
        assert meta is not None
        assert meta["vlan_count"] == 2
        assert {v["vlan_id"] for v in meta["vlans"]} == {10, 20}
        assert meta["pvid_by_interface"] == {"Gi1/0/1": 20, "Gi1/0/2": 10}
        assert meta["stp"]["protocol"] == "ieee8021d"
        assert meta["link_aggregation"][0]["member_ports"] == ["Gi1/0/1", "Gi1/0/2"]

    async def test_absent_metadata_does_not_wipe_existing(
        self, tenant, monitoring_source_factory, db
    ):
        """A source that knows nothing about switching must not erase what
        another source discovered."""
        source = await monitoring_source_factory(source_type="snmp")
        saver = _saver(db)
        await saver.save(source, _bundle({"vlans": [{"vlan_id": 10}]}))
        # Second sync supplies no metadata at all.
        await saver.save(source, _bundle(None))

        device = await DeviceRepository(db).get_by_external_id(source.id, "sw-a")
        assert device.custom_metadata == {"vlans": [{"vlan_id": 10}]}

    async def test_metadata_merge_preserves_keys_from_other_sources(
        self, tenant, monitoring_source_factory, db
    ):
        source = await monitoring_source_factory(source_type="snmp")
        saver = _saver(db)
        await saver.save(source, _bundle({"other_fact": "kept"}))
        await saver.save(source, _bundle({"vlan_count": 2}))

        device = await DeviceRepository(db).get_by_external_id(source.id, "sw-a")
        assert device.custom_metadata == {"other_fact": "kept", "vlan_count": 2}

    async def test_vendor_from_switching_discovery_is_persisted(
        self, tenant, monitoring_source_factory, db
    ):
        source = await monitoring_source_factory(source_type="snmp")
        await _saver(db).save(source, _bundle(vendor="Aruba", device_type="switch"))
        device = await DeviceRepository(db).get_by_external_id(source.id, "sw-a")
        assert device.vendor == "Aruba"
        assert device.device_type == "switch"
