"""Pure unit tests for the extended device-type / vendor vocabulary (Sections 14/15)."""

from __future__ import annotations

from app.models.device_types import (
    ALL_VENDORS,
    CANONICAL_DEVICE_TYPES,
    DEVICE_FAMILY_TO_TYPE,
    VENDOR_CATALOG,
    normalize_device_type,
    vendor_families,
)


def test_canonical_types_include_security_cloud_hosting():
    for t in ("router", "switch", "firewall", "security_appliance",
              "load_balancer", "hypervisor", "vm", "storage", "cloud_resource"):
        assert t in CANONICAL_DEVICE_TYPES


def test_vendor_catalog_covers_major_vendors():
    for v in ("Cisco", "Juniper", "Arista", "Fortinet", "Palo Alto Networks",
              "Check Point", "F5", "Dell Networking", "Huawei", "MikroTik",
              "Ubiquiti", "VMware"):
        assert v in VENDOR_CATALOG
        assert vendor_families(v)


def test_unknown_vendor_empty():
    assert vendor_families("NoSuchVendor") == []
    assert vendor_families(None) == []


def test_normalize_still_works():
    assert normalize_device_type("wireless_ap") == "access_point"
    assert normalize_device_type("router") == "router"
    assert normalize_device_type(None) == "unclassified"


def test_device_family_to_type_covers_section_15_families():
    # Section 15 device families map to a canonical type.
    for fam in ("customer_edge", "provider_edge", "provider_router", "ngfw",
                "ids_ips", "waf", "vpn_concentrator", "siem", "hypervisor",
                "kubernetes_node", "backup_appliance", "storage"):
        assert fam in DEVICE_FAMILY_TO_TYPE
        assert normalize_device_type(DEVICE_FAMILY_TO_TYPE[fam]) != "unclassified"
