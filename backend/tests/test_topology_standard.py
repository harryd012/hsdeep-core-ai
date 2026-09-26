"""Pure unit tests for the topology standard service (Sections 14/17).

These run with NO database — they exercise only the dependency-free
algorithms in ``app.services.topology_standard``.
"""
from __future__ import annotations

from app.services.topology_standard import (
    DEVICE_ROLES,
    Link,
    normalize_link_order,
    path_exists,
    redundant_paths,
    role_class_for,
    trace_path,
    validate_link_attributes,
)


def _ce_pe_p_links() -> list[Link]:
    """Classic MPLS topology: two PEs, two P cores, plus dual CE links."""
    return [
        # CE-A dual-homed to PE-1 and PE-2 (redundant uplinks)
        Link(id="ceA-pe1", node_a="CE-A", node_b="PE-1", link_type="serial", vpn_name="VPN-A"),
        Link(id="ceA-pe2", node_a="CE-A", node_b="PE-2", link_type="serial", vpn_name="VPN-A"),
        # PE-1 <-> P-1 and PE-2 <-> P-2 (MPLS backbone)
        Link(id="pe1-p1", node_a="PE-1", node_b="P-1", link_type="mpls", protocol="ospf"),
        Link(id="pe2-p2", node_a="PE-2", node_b="P-2", link_type="mpls", protocol="ospf"),
        # redundant backbone between P cores
        Link(id="p1-p2-a", node_a="P-1", node_b="P-2", link_type="backbone", protocol="ospf"),
        Link(id="p1-p2-b", node_a="P-1", node_b="P-2", link_type="backbone", protocol="ospf"),
        # CE-B dual-homed
        Link(id="ceB-pe1", node_a="CE-B", node_b="PE-1", link_type="serial", vpn_name="VPN-A"),
        Link(id="ceB-pe2", node_a="CE-B", node_b="PE-2", link_type="serial", vpn_name="VPN-A"),
    ]


def test_role_class_mapping():
    assert role_class_for("CE") == "network"
    assert role_class_for("pe") == "network"
    assert role_class_for("P") == "network"
    assert role_class_for("firewall_edge") == "security"
    assert role_class_for("hypervisor") == "compute"
    assert role_class_for("cloud_resource") == "cloud"
    assert role_class_for("nonexistent_role") == "other"
    assert role_class_for(None) == "other"
    assert role_class_for("CE")  # CE/PE/P all present
    assert "ce" in DEVICE_ROLES and "pe" in DEVICE_ROLES and "p" in DEVICE_ROLES


def test_normalize_link_order():
    assert normalize_link_order("B", "A") == ("A", "B")
    assert normalize_link_order("A", "B") == ("A", "B")


def test_path_exists_single_path():
    links = [Link("a-b", "A", "B"), Link("b-c", "B", "C")]
    assert path_exists("A", "C", links)
    assert not path_exists("A", "Z", links)


def test_path_exists_respects_down_links():
    links = [Link("a-b", "A", "B", oper_status="down")]
    assert not path_exists("A", "B", links, up_only=True)
    assert path_exists("A", "B", links, up_only=False)


def test_trace_path_finds_all_simple_paths():
    links = _ce_pe_p_links()
    paths = trace_path("CE-A", "CE-B", links)
    # Multiple disjoint paths (CE-A->PE-1->P-1->... and CE-A->PE-2->...)
    assert len(paths) >= 2


def test_redundant_paths_detects_two_disjoint_paths():
    # A -- B -- C is redundant with A -- D -- E -- C
    links = [
        Link("1", "A", "B"), Link("2", "B", "C"),
        Link("4", "A", "D"), Link("5", "D", "E"), Link("6", "E", "C"),
    ]
    r = redundant_paths("A", "C", links)
    assert r["redundant"] is True
    assert r["link_disjoint_count"] == 2


def test_redundant_paths_single_path_not_redundant():
    links = [Link("1", "A", "B"), Link("2", "B", "C")]
    r = redundant_paths("A", "C", links)
    assert r["redundant"] is False
    assert r["link_disjoint_count"] == 1


def test_validate_link_attributes():
    assert validate_link_attributes({"link_type": "wan", "oper_status": "up"}) == []
    problems = validate_link_attributes(
        {"link_type": "bogus", "oper_status": "weird", "routing_protocols": ["foo"], "vpn_type": "nope"}
    )
    assert len(problems) == 4
