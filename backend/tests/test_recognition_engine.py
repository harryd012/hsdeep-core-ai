"""Universal recognition engine tests (provider-agnostic discovery core).

Covers network vendor recognition (FortiGate / Cisco router / switch / Aruba AP),
cloud + virtualisation + container recognition, generic/unknown handling, and
deterministic adapter selection.
"""
from __future__ import annotations

from app.services.recognition import ProviderRegistry, recognize
from app.services.recognition.taxonomy import (
    CATEGORY_CLOUD,
    CATEGORY_CONTAINER,
    CATEGORY_ENDPOINT,
    CATEGORY_NETWORK,
    CATEGORY_SECURITY,
    CATEGORY_UNKNOWN,
    CATEGORY_VIRTUALIZATION,
    CATEGORY_WIRELESS,
    category_for,
    normalize_resource_type,
)

FORTIGATE_DESCR = "FortiGate-60F v7.2.8,build1396,230912 (GA.M)"
CISCO_ROUTER_DESCR = (
    "Cisco IOS Software, 7200 Software (C7200-ADVENTERPRISEK9-M), "
    "Version 15.2(4)M6, RELEASE SOFTWARE (fc2)"
)
CISCO_SWITCH_DESCR = "Cisco IOS Software, C2960S Software (C2960S-UNIVERSALK9-M)"
ARUBA_AP_DESCR = "ArubaOS (MODEL: AP-315), Version 8.6.0.0"


class TestNetworkRecognition:
    def test_fortigate_recognized_as_firewall(self):
        result = recognize({
            "ip": "192.168.159.130",
            "name": "FortiGate",
            "sys_descr": FORTIGATE_DESCR,
        }).as_dict()
        assert result["resource_type"] == "firewall"
        assert result["resource_category"] == CATEGORY_SECURITY
        assert result["vendor"] == "Fortinet"
        assert result["recognition"]["matched"] is True

    def test_fortinet_sys_object_id_claims_provider(self):
        result = recognize({
            "ip": "192.168.159.130",
            "sys_object_id": "1.3.6.1.4.1.12356.101.1.1",
            "sys_descr": FORTIGATE_DESCR,
        }).as_dict()
        assert result["provider"] == "fortinet"
        assert result["recognition"]["method"] == "adapter_fingerprint"

    def test_cisco_router_recognized(self):
        result = recognize({
            "ip": "10.10.13.2",
            "name": "R3",
            "sys_descr": CISCO_ROUTER_DESCR,
        }).as_dict()
        assert result["vendor"] == "Cisco"
        assert result["resource_type"] in ("router", "switch")
        assert result["resource_category"] == CATEGORY_NETWORK

    def test_cisco_switch_recognized(self):
        result = recognize({
            "ip": "10.0.0.5",
            "sys_descr": CISCO_SWITCH_DESCR,
        }).as_dict()
        assert result["vendor"] == "Cisco"
        assert result["resource_category"] == CATEGORY_NETWORK

    def test_aruba_ap_recognized_as_wireless(self):
        result = recognize({
            "ip": "10.0.1.20",
            "sys_descr": ARUBA_AP_DESCR,
        }).as_dict()
        assert result["resource_type"] == "access_point"
        assert result["resource_category"] == CATEGORY_WIRELESS

    def test_meraki_claim_requires_meraki_model_code(self):
        mr = recognize({"ip": "10.0.2.5", "model": "MR36"}).as_dict()
        assert mr["provider"] == "meraki"
        # A non-Meraki model must NOT be claimed by the Meraki adapter.
        other = recognize({
            "ip": "10.0.2.6",
            "model": "PA-5220",
            "sys_descr": "Palo Alto Networks PA-5220",
        }).as_dict()
        assert other.get("provider") != "meraki"


class TestCloudAndVirtualRecognition:
    def test_azure_vm_recognized(self):
        result = recognize({
            "provider": "azure",
            "resource_type": "microsoft.compute/virtualmachines",
            "name": "vm-app-01",
            "region": "westeurope",
        }).as_dict()
        assert result["resource_type"] == "virtual_machine"
        assert result["resource_category"] == CATEGORY_VIRTUALIZATION
        assert result["provider"] == "azure"
        assert result["region"] == "westeurope"

    def test_azure_vnet_recognized(self):
        result = recognize({
            "provider": "azure",
            "resource_type": "Microsoft.Network/virtualNetworks",
            "name": "vnet-prod",
        }).as_dict()
        assert result["resource_type"] == "vnet"
        assert result["resource_category"] == CATEGORY_CLOUD

    def test_aws_ec2_recognized(self):
        result = recognize({
            "provider": "aws",
            "resource_type": "aws_instance",
            "name": "i-0abc",
        }).as_dict()
        assert result["provider"] == "aws"
        assert result["resource_category"] == CATEGORY_CLOUD

    def test_kubernetes_pod_recognized(self):
        result = recognize({
            "provider": "kubernetes",
            "resource_type": "pod",
            "name": "api-7d9f",
            "kubernetes_uid": "3f2a-uuid",
        }).as_dict()
        assert result["resource_type"] == "pod"
        assert result["resource_category"] == CATEGORY_CONTAINER
        assert result["provider"] == "kubernetes"

    def test_vmware_vm_recognized(self):
        result = recognize({
            "provider": "vmware",
            "resource_type": "vm",
            "name": "web-01",
        }).as_dict()
        assert result["provider"] == "vmware"
        assert result["resource_category"] == CATEGORY_VIRTUALIZATION


class TestUnknownAndGeneric:
    def test_icmp_only_endpoint_is_unknown_but_inventoried(self):
        result = recognize({"ip": "10.10.31.10", "name": "VPC1"}).as_dict()
        assert result["resource_type"] == "unknown"
        assert result["resource_category"] == CATEGORY_UNKNOWN
        assert result["management_ip"] == "10.10.31.10"
        assert result["recognition"]["matched"] is False

    def test_unknown_keeps_raw_evidence_available(self):
        result = recognize({
            "ip": "10.0.9.9",
            "mac": "AA:BB:CC:DD:EE:FF",
            "sys_descr": "SomeVendor Thing 9000",
        }).as_dict()
        assert result["mac_address"] == "AA:BB:CC:DD:EE:FF"
        assert result["resource_type"] == "unknown"

    def test_unknown_never_force_fits_a_category(self):
        assert category_for("endpoint") == CATEGORY_ENDPOINT
        assert category_for("totally-made-up") == CATEGORY_UNKNOWN

    def test_normalize_unknown_type_never_guesses(self):
        assert normalize_resource_type("totally-made-up") == "unknown"
        assert normalize_resource_type(None) == "unknown"


class TestDeterminismAndRegistry:
    def test_selection_is_deterministic_for_identical_evidence(self):
        evidence = {"ip": "1.1.1.1", "sys_descr": CISCO_ROUTER_DESCR}
        assert recognize(evidence).as_dict() == recognize(evidence).as_dict()

    def test_registry_exposes_network_and_cloud_adapters(self):
        providers = ProviderRegistry.providers()
        for expected in ("cisco", "fortinet", "aruba", "meraki",
                         "azure", "aws", "gcp", "vmware", "kubernetes"):
            assert expected in providers

    def test_registry_capability_and_kind_lookup(self):
        assert ProviderRegistry.capable_of("snmp")
        cloud_only = ProviderRegistry.by_kind("cloud")
        assert cloud_only and all(a.adapter_kind == "cloud" for a in cloud_only)

    def test_unknown_provider_lookup_is_none_not_an_error(self):
        assert ProviderRegistry.try_get("not-a-provider") is None
