"""
Unit tests for GcpCollector.normalize() and its config validation.

Pure, no I/O and no GCP credentials. Fixtures mirror the JSON form the
compute_v1 clients are converted into (`zone`/`machine_type` arrive as
self-links like `zones/europe-west1-b`, so `_tail()` must reduce them).
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.collectors.base import CollectorConfigError, NormalizedBundle
from app.collectors.gcp import GcpCollector, _nat_ip, _tail

_EMPTY_RAW = {
    "project_id": "proj-1",
    "zones": ["-"],
    "instances": [],
    "networks": [],
    "subnetworks": [],
    "firewalls": [],
    "errors": [],
}

_INSTANCES_RAW = {
    "project_id": "proj-1",
    "zones": ["europe-west1-b"],
    "instances": [
        {
            "name": "vm-web-01",
            "status": "RUNNING",
            "machine_type": "zones/europe-west1-b/machineTypes/e2-medium",
            "self_link": "https://www.googleapis.com/compute/v1/projects/proj-1/zones/europe-west1-b/instances/vm-web-01",
            "network_interfaces": [
                {
                    "network_ip": "10.132.0.2",
                    "network": "projects/proj-1/global/networks/prod-vpc",
                    "subnetwork": "projects/proj-1/regions/europe-west1/subnetworks/prod-sub",
                }
            ],
            "_zone": "europe-west1-b",
        },
        {
            "name": "vm-db-01",
            "status": "TERMINATED",
            "machine_type": "zones/europe-west1-b/machineTypes/n2-standard-4",
            # No network interfaces at all - ip_address must stay None.
            "_zone": "europe-west1-b",
        },
    ],
    "networks": [
        {
            "name": "prod-vpc",
            "auto_create_subnetworks": False,
            "routing_config": {"routing_mode": "REGIONAL"},
        }
    ],
    "subnetworks": [
        {
            "name": "prod-sub",
            "ip_cidr_range": "10.132.0.0/20",
            "network": "projects/proj-1/global/networks/prod-vpc",
            "_region": "europe-west1",
        }
    ],
    "firewalls": [
        {
            "name": "allow-https",
            "direction": "INGRESS",
            "priority": 1000,
            "source_ranges": ["0.0.0.0/0"],
            "allowed": [{"IPProtocol": "tcp"}],
            "network": "projects/proj-1/global/networks/prod-vpc",
        },
        {
            "name": "legacy-disabled-rule",
            "disabled": True,
            "network": "projects/proj-1/global/networks/prod-vpc",
        },
    ],
    "errors": [],
}


def _source(config=None, password=None):
    """Stand-in for MonitoringSourceConnectionInfo (secret never asserted)."""
    return SimpleNamespace(
        id=None,
        tenant_id=None,
        name="gcp-test",
        source_type="gcp",
        hostname=None,
        ip_address=None,
        port=None,
        protocol=None,
        snmp_version=None,
        username=None,
        api_key=None,
        password=password,
        config=config or {},
        verify_ssl=True,
    )


class TestGcpNormalize:

    def test_empty_payload_returns_empty_bundle(self):
        bundle = GcpCollector(source=None).normalize(_EMPTY_RAW)
        assert isinstance(bundle, NormalizedBundle)
        assert bundle.devices == []

    def test_instance_maps_core_fields(self):
        bundle = GcpCollector(source=None).normalize(_INSTANCES_RAW)
        vm = {d.name: d for d in bundle.devices}["vm-web-01"]

        assert vm.external_id == "gcp:proj-1:instance:vm-web-01"
        assert vm.device_type == "cloud_resource"
        assert vm.vendor == "Google"
        assert vm.model == "e2-medium"          # self-link reduced
        assert vm.ip_address == "10.132.0.2"
        assert vm.status == "up"
        assert vm.site_hint == "europe-west1-b"
        assert vm.custom_metadata["network"] == "prod-vpc"
        assert vm.custom_metadata["subnetwork"] == "prod-sub"

    def test_terminated_instance_maps_to_down(self):
        bundle = GcpCollector(source=None).normalize(_INSTANCES_RAW)
        assert {d.name: d for d in bundle.devices}["vm-db-01"].status == "down"

    def test_instance_without_interfaces_leaves_ip_none(self):
        bundle = GcpCollector(source=None).normalize(_INSTANCES_RAW)
        vm = {d.name: d for d in bundle.devices}["vm-db-01"]
        assert vm.ip_address is None
        assert vm.custom_metadata["network"] is None

    def test_network_subnet_and_firewall_are_emitted(self):
        bundle = GcpCollector(source=None).normalize(_INSTANCES_RAW)
        by_id = {d.external_id: d for d in bundle.devices}

        net = by_id["gcp:proj-1:network:prod-vpc"]
        assert net.status == "up"
        assert net.custom_metadata["auto_create_subnetworks"] is False
        assert net.custom_metadata["routing_mode"] == "REGIONAL"

        sub = by_id["gcp:proj-1:subnetwork:prod-sub"]
        assert sub.custom_metadata["cidr_block"] == "10.132.0.0/20"
        assert sub.site_hint == "europe-west1"

        fw = by_id["gcp:proj-1:firewall:allow-https"]
        assert fw.status == "up"
        assert fw.custom_metadata["allowed"] == ["tcp"]
        assert fw.custom_metadata["priority"] == 1000

    def test_disabled_firewall_maps_to_down(self):
        bundle = GcpCollector(source=None).normalize(_INSTANCES_RAW)
        by_id = {d.external_id: d for d in bundle.devices}
        assert by_id["gcp:proj-1:firewall:legacy-disabled-rule"].status == "down"

    def test_rows_without_name_are_skipped(self):
        raw = {
            "instances": [{"status": "RUNNING"}],
            "networks": [{"auto_create_subnetworks": True}],
            "subnetworks": [{"ip_cidr_range": "10.0.0.0/24"}],
            "firewalls": [{"direction": "INGRESS"}],
        }
        assert GcpCollector(source=None).normalize(raw).devices == []

    def test_project_falls_back_when_absent(self):
        """A raw dict without project_id must not crash normalize()."""
        bundle = GcpCollector(source=None).normalize(
            {"instances": [{"name": "vm-x", "status": "RUNNING"}]}
        )
        assert bundle.devices[0].external_id == "gcp:project:instance:vm-x"

    def test_normalize_does_not_mutate_input(self):
        raw = copy.deepcopy(_INSTANCES_RAW)
        GcpCollector(source=None).normalize(raw)
        assert raw == _INSTANCES_RAW


class TestGcpHelpers:

    def test_tail_reduces_self_links(self):
        assert _tail("zones/europe-west1-b") == "europe-west1-b"
        assert _tail("projects/p/global/networks/prod-vpc") == "prod-vpc"
        assert _tail(None) is None
        assert _tail("") is None

    def test_nat_ip_reads_proto_and_rest_field_names(self):
        assert _nat_ip({"access_configs": [{"nat_i_p": "34.1.2.3"}]}) == "34.1.2.3"
        assert _nat_ip({"access_configs": [{"natIP": "34.1.2.4"}]}) == "34.1.2.4"

    def test_nat_ip_absent_returns_none(self):
        assert _nat_ip({}) is None
        assert _nat_ip({"access_configs": []}) is None


class TestGcpConfig:

    def test_missing_project_id_raises(self):
        with pytest.raises(CollectorConfigError) as exc:
            GcpCollector(source=_source())._project_id()
        assert "project_id" in str(exc.value)

    def test_missing_secret_raises_config_error(self):
        collector = GcpCollector(source=_source(config={"project_id": "p"}))
        with pytest.raises(CollectorConfigError) as exc:
            collector._require_config()
        assert "service account JSON" in str(exc.value)

    def test_zones_prefers_list_over_single_zone(self):
        collector = GcpCollector(
            source=_source(config={"zones": ["europe-west1-b"],
                                   "zone": "us-central1-a"})
        )
        assert collector._zones() == ["europe-west1-b"]

    def test_zones_empty_means_aggregated_list(self):
        assert GcpCollector(source=_source(config={"project_id": "p"}))._zones() == []

    def test_invalid_service_account_json_raises(self):
        collector = GcpCollector(
            source=_source(config={"project_id": "p"}, password="{not json")
        )
        with pytest.raises(CollectorConfigError) as exc:
            collector._credentials()
        assert "could not parse JSON" in str(exc.value)
