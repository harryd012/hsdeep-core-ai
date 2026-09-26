"""
Unit tests for ZabbixCollector.normalize().

Tests validate that raw Zabbix JSON-RPC API responses are correctly converted to
NormalizedBundle format without external I/O or database access.
"""
import pytest

from app.collectors.zabbix import ZabbixCollector
from app.collectors.base import NormalizedBundle
from tests.fixtures.zabbix_payloads import (
    ZABBIX_COLLECT_EMPTY,
    ZABBIX_COLLECT_SINGLE,
    ZABBIX_COLLECT_MULTI,
)


class TestZabbixNormalize:
    """Test ZabbixCollector.normalize() pure function."""

    def test_empty_payload(self):
        """Empty Zabbix response normalizes to empty bundle."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_EMPTY)

        assert isinstance(bundle, NormalizedBundle)
        assert len(bundle.devices) == 0
        assert len(bundle.interfaces) == 0
        assert len(bundle.sensors) == 0
        assert len(bundle.alerts) == 0

    def test_single_host_with_interfaces(self):
        """Single host with agent interface normalizes correctly."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_SINGLE)

        assert len(bundle.devices) == 1
        device = bundle.devices[0]
        assert device.external_id == "10001"
        assert device.name == "Web Server 01"
        assert device.hostname == "web-server-01"
        assert device.status == "up"
        assert device.vendor == "Zabbix"
        assert device.site_hint == "Linux servers / Production"

        # Should have one interface (the agent interface)
        assert len(bundle.interfaces) == 1
        interface = bundle.interfaces[0]
        assert interface.device_external_id == "10001"
        assert interface.external_id == "20001"
        assert "Agent" in interface.name
        assert "192.168.1.10" in interface.name

        # IP should be resolved from primary interface
        assert device.ip_address == "192.168.1.10"

    def test_multiple_hosts(self):
        """Multiple hosts with different interface types normalize correctly."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_MULTI)

        assert len(bundle.devices) == 3
        device_ids = {d.external_id for d in bundle.devices}
        assert device_ids == {"10001", "10002", "10003"}

        # DB Server should have 2 interfaces
        db_device = next(d for d in bundle.devices if d.external_id == "10002")
        assert db_device.hostname == "db-server-01"
        assert db_device.status == "up"
        assert db_device.ip_address == "192.168.1.20"

        # Unmonitored host should have "down" status
        monitor_device = next(d for d in bundle.devices if d.external_id == "10003")
        assert monitor_device.status == "down"

        # Should have 4 interfaces total across all hosts
        assert len(bundle.interfaces) == 4

        # Verify interface-to-device mapping
        for interface in bundle.interfaces:
            assert interface.device_external_id in device_ids

    def test_items_to_sensors(self):
        """Zabbix items convert to sensor readings."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_SINGLE)

        assert len(bundle.sensors) == 4  # CPU load, memory, disk, network

        # CPU load sensor
        cpu_sensor = next(s for s in bundle.sensors if s.metric_type == "cpu_load")
        assert cpu_sensor.value == 2.5
        assert cpu_sensor.device_external_id == "10001"

        # Memory sensor
        mem_sensor = next(s for s in bundle.sensors if s.metric_type == "memory_available")
        assert mem_sensor.value == 4294967296.0
        assert mem_sensor.unit == "B"

        # Disk sensor
        disk_sensor = next(s for s in bundle.sensors if s.metric_type == "disk_used")
        assert disk_sensor.value == 10737418240.0

        # Network sensor
        net_sensor = next(s for s in bundle.sensors if s.metric_type == "network_in")
        assert net_sensor.value == 1500000.0
        assert net_sensor.unit == "Bps"

    def test_string_items_skipped(self):
        """Non-numeric items (value_type=string/log) are skipped."""
        items_with_string = [
            {
                "itemid": "30005",
                "key_": "system.uname",
                "name": "System name",
                "type": "0",
                "value_type": "1",  # String
                "lastvalue": "Linux web-server-01 5.10.0",
                "hostid": "10001",
            },
        ]

        raw = dict(ZABBIX_COLLECT_SINGLE)
        raw["items"] = items_with_string
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(raw)

        # String items should be filtered out
        sensors = [s for s in bundle.sensors if s.external_id == "30005"]
        assert len(sensors) == 0

    def test_problems_to_alerts(self):
        """Active Zabbix problems convert to alert signals."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_SINGLE)

        assert len(bundle.alerts) == 2  # 2 active problems

        # High severity alert
        high_alert = next(a for a in bundle.alerts if a.external_id == "50001")
        assert "CPU load" in high_alert.title
        assert high_alert.severity == "warning"  # severity 4 maps to "warning"

        # Disaster severity alert
        disaster_alert = next(a for a in bundle.alerts if a.external_id == "50002")
        assert "Disk space" in disaster_alert.title
        assert disaster_alert.severity == "critical"  # severity 5 maps to "critical"

    def test_host_status_mapping(self):
        """Zabbix host status codes map to normalized statuses."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_MULTI)

        # Monitored hosts (status=0) -> "up"
        up_hosts = [d for d in bundle.devices if d.status == "up"]
        assert len(up_hosts) == 2  # web-server-01 and db-server-01

        # Unmonitored host (status=1) -> "down"
        down_hosts = [d for d in bundle.devices if d.status == "down"]
        assert len(down_hosts) == 1  # monitor-host
        assert down_hosts[0].external_id == "10003"

    def test_interface_ip_resolution(self):
        """Primary interface IP resolves to device.ip_address."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_MULTI)

        # web-server-01 has primary agent interface
        web_device = next(d for d in bundle.devices if d.external_id == "10001")
        assert web_device.ip_address == "192.168.1.10"

        # db-server-01 has primary agent interface (main=1)
        db_device = next(d for d in bundle.devices if d.external_id == "10002")
        assert db_device.ip_address == "192.168.1.20"

    def test_interface_naming(self):
        """Interface names are human-readable."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_MULTI)

        # Agent interface without explicit port hid default port
        agent_iface = next(
            i for i in bundle.interfaces
            if i.external_id == "20001"
        )
        assert "Agent" in agent_iface.name
        assert "192.168.1.10" in agent_iface.name
        assert "10050" not in agent_iface.name  # Default port hidden

        # SNMP interface with non-default port
        snmp_iface = next(
            i for i in bundle.interfaces
            if i.external_id == "20003"
        )
        assert "SNMP" in snmp_iface.name

    def test_site_hint_from_groups(self):
        """Site hint is constructed from host groups."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_SINGLE)

        device = bundle.devices[0]
        assert device.site_hint is not None
        assert "Linux servers" in device.site_hint
        assert "Production" in device.site_hint

    def test_normalize_is_pure_function(self):
        """Calling normalize() twice with same input produces identical output."""
        collector = ZabbixCollector(source=None)

        bundle1 = collector.normalize(ZABBIX_COLLECT_SINGLE)
        bundle2 = collector.normalize(ZABBIX_COLLECT_SINGLE)

        assert len(bundle1.devices) == len(bundle2.devices)
        assert len(bundle1.interfaces) == len(bundle2.interfaces)
        assert len(bundle1.sensors) == len(bundle2.sensors)
        assert len(bundle1.alerts) == len(bundle2.alerts)

        # Verify exact equality of first device
        assert bundle1.devices[0].external_id == bundle2.devices[0].external_id
        assert bundle1.devices[0].name == bundle2.devices[0].name
        assert bundle1.devices[0].status == bundle2.devices[0].status

        # Verify exact equality of interfaces
        for i1, i2 in zip(bundle1.interfaces, bundle2.interfaces):
            assert i1.name == i2.name
            assert i1.device_external_id == i2.device_external_id

    def test_no_hardcoded_values(self):
        """All device data comes from the payload."""
        collector = ZabbixCollector(source=None)
        bundle = collector.normalize(ZABBIX_COLLECT_SINGLE)

        # Verify all devices came from payload
        payload_host_ids = {h["hostid"] for h in ZABBIX_COLLECT_SINGLE["hosts"]}
        for device in bundle.devices:
            assert device.external_id in payload_host_ids

        # Verify all interfaces came from payload
        payload_iface_ids = set()
        for host in ZABBIX_COLLECT_SINGLE["hosts"]:
            for iface in host.get("interfaces", []):
                payload_iface_ids.add(iface["interfaceid"])
        for interface in bundle.interfaces:
            assert interface.external_id in payload_iface_ids

        # Vendor is hardcoded as "Zabbix"
        for device in bundle.devices:
            assert device.vendor == "Zabbix"