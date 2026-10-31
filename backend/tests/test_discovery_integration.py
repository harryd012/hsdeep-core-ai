"""Discovery integration tests: recognition -> identity -> Device ->
capabilities -> relationships, against the REAL database.

The DB-backed tests use the existing `tenant` / `db` / `monitoring_source_factory`
fixtures from tests/conftest.py — no new fixture infrastructure, no mock
persistence.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.repositories.device_capability_repository import CAPABILITY_FLAGS
from app.repositories.device_repository import DeviceRepository
from app.services.discovery import (
    DiscoveryEvidence,
    DiscoveryService,
    NeighborEvidence,
)
from app.services.discovery.service import _capability_flags, _merge_with_existing

FORTIGATE_SNMP_EVIDENCE = DiscoveryEvidence(
    address="192.168.159.130",
    transport="snmp",
    source="snmp",
    hostname="FG-LAB01",
    sys_descr="FortiGate-60F v7.2.8,build1396,230912 (GA.M)",
    sys_object_id="1.3.6.1.4.1.12356.101.1.1",
    serial="FGT60FTK20000001",
    model="FortiGate-60F",
    vendor=None,
    reachable=True,
    capabilities=["snmp"],
)

CISCO_ROUTER_EVIDENCE = DiscoveryEvidence(
    address="10.10.13.2",
    transport="snmp",
    source="snmp",
    hostname="R3",
    sys_descr=(
        "Cisco IOS Software, 7200 Software (C7200-ADVENTERPRISEK9-M), "
        "Version 15.2(4)M6, RELEASE SOFTWARE (fc2)"
    ),
    serial="JAE12345678",
    reachable=True,
    capabilities=["snmp"],
)

VPC_EVIDENCE = DiscoveryEvidence(
    address="10.10.31.10",
    transport="icmp",
    source="icmp",
    hostname="VPC1",
    reachable=True,
)


class TestEvidenceContract:
    def test_only_populated_fields_reach_the_recogniser(self):
        ev = DiscoveryEvidence(address="10.0.0.1")
        assert ev.recognition_input() == {"ip": "10.0.0.1"}

    def test_full_evidence_is_emitted(self):
        payload = FORTIGATE_SNMP_EVIDENCE.recognition_input()
        assert payload["sys_descr"].startswith("FortiGate-60F")
        assert payload["sys_object_id"] == "1.3.6.1.4.1.12356.101.1.1"
        assert payload["serial_number"] == "FGT60FTK20000001"

    def test_merge_preserves_evidence_a_later_poll_lost(self):
        """Poll 1 saw SNMP; poll 2 only reached ICMP. Nothing may be erased."""
        later = DiscoveryEvidence(
            address="192.168.159.130", transport="icmp", reachable=True
        )
        merged = FORTIGATE_SNMP_EVIDENCE.merge(later)
        assert merged.sys_descr == FORTIGATE_SNMP_EVIDENCE.sys_descr
        assert merged.serial == "FGT60FTK20000001"
        assert merged.hostname == "FG-LAB01"
        # ...and the new observation is still folded in.
        assert merged.transport == "icmp"


class TestMergeIntoStoredDevice:
    def test_new_evidence_wins_when_present(self):
        merged = _merge_with_existing(
            {"resource_type": "firewall", "vendor": "Fortinet",
             "management_ip": "10.0.0.9"},
            None,
        )
        assert merged["device_type"] == "firewall"
        assert merged["vendor"] == "Fortinet"
        assert merged["ip_address"] == "10.0.0.9"

    def test_missing_evidence_never_blanks_a_stored_field(self):
        class Stored:
            device_type = "firewall"
            vendor = "Fortinet"
            model = "FortiGate-60F"
            os_version = "v7.2.8"
            hostname = "FG-LAB01"
            ip_address = "192.168.159.130"
            mac_address = "aa:bb:cc:dd:ee:ff"
            serial_number = "FGT60FTK20000001"
            name = "FG-LAB01"

        # An ICMP-only re-discovery yields no vendor/model/serial at all.
        merged = _merge_with_existing(
            {"resource_type": "unknown", "management_ip": "192.168.159.130"},
            Stored(),
        )
        assert merged["vendor"] == "Fortinet"
        assert merged["model"] == "FortiGate-60F"
        assert merged["serial_number"] == "FGT60FTK20000001"
        assert merged["hostname"] == "FG-LAB01"


class TestCapabilityMapping:
    def test_observed_tokens_map_to_existing_flags(self):
        flags, provider_caps = _capability_flags(
            {"capabilities": ["snmp", "ssh", "api"]},
            DiscoveryEvidence(address="10.0.0.1", transport="snmp", reachable=True),
        )
        assert flags["supports_snmp"] is True
        assert flags["supports_ssh"] is True
        assert flags["supports_rest"] is True
        assert flags["supports_ping"] is True  # ICMP reachability observed
        assert provider_caps == []

    def test_tokens_without_a_flag_become_provider_metadata(self):
        _flags, provider_caps = _capability_flags(
            {"capabilities": ["firewall", "vpn", "sdwan", "routing"]},
            DiscoveryEvidence(address="10.0.0.1", transport="api"),
        )
        assert set(provider_caps) >= {"firewall", "vpn", "sdwan", "routing"}

    def test_write_privilege_flags_are_never_derived(self):
        """Discovery must not grant — or erase — write privileges.

        The flags are deliberately ABSENT from the mapping rather than set to
        None: DeviceCapabilityRepository.upsert only touches flags present in
        the dict, so an operator's explicit grant survives a discovery run.
        """
        flags, _ = _capability_flags(
            {"capabilities": ["config", "reboot", "firmware", "backup"]},
            DiscoveryEvidence(address="10.0.0.1", transport="api", reachable=True),
        )
        for privileged in (
            "supports_config", "supports_reboot", "supports_firmware",
            "supports_backup", "supports_rollback", "supports_monitoring_actions",
        ):
            assert privileged not in flags, f"{privileged} must not be touched"

    def test_flags_only_ever_cover_real_capability_columns(self):
        flags, _ = _capability_flags({}, DiscoveryEvidence(address="10.0.0.1"))
        assert set(flags) <= set(CAPABILITY_FLAGS)


# ---------------------------------------------------------------------------
# DB-backed end-to-end: recognition -> identity -> Device -> caps -> edges
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def discovery(tenant, monitoring_source_factory):
    """A DiscoveryService bound to its OWN session.

    Why not the shared `db` fixture: `db` keeps an uncommitted transaction open
    for the whole test while `tenant` teardown runs first and issues
    `DELETE FROM tenants` — which cascades to the Device rows that transaction
    just inserted, so Postgres blocks on those row locks and the test hangs.
    Owning the session here means it is rolled back (locks released) BEFORE the
    tenant is deleted.
    """
    from app.core.db import AsyncSessionLocal

    source = await monitoring_source_factory(
        source_type="snmp", protocol="snmp", hostname="192.168.159.130"
    )
    async with AsyncSessionLocal() as session:
        try:
            yield DiscoveryService(session), source, tenant
        finally:
            await session.rollback()


class TestEndToEndDiscovery:
    async def test_fortigate_evidence_creates_a_device(self, discovery):
        service, source, tenant = discovery
        result = await service.discover(
            tenant_id=tenant.id,
            monitoring_source_id=source.id,
            evidence=[FORTIGATE_SNMP_EVIDENCE],
        )

        assert result.status == "SUCCESS"
        assert result.created == 1
        outcome = result.resources[0]
        assert outcome.resource_type == "firewall"
        assert outcome.resource_category == "security"
        assert outcome.provider == "fortinet"
        assert outcome.recognition["method"] == "adapter_fingerprint"
        assert outcome.identity_source == "serial_number"
        assert outcome.device_id

        # The EXISTING Device model really holds the normalized resource.
        stored = await DeviceRepository(service.db).get_by_external_id(
            source.id, outcome.external_id
        )
        assert stored is not None
        assert stored.device_type == "firewall"
        assert stored.vendor == "Fortinet"
        assert stored.serial_number == "FGT60FTK20000001"
        assert stored.status == "up"
        assert stored.custom_metadata["discovery"]["provider"] == "fortinet"

    async def test_rediscovery_updates_instead_of_duplicating(self, discovery):
        service, source, tenant = discovery
        first = await service.discover(
            tenant_id=tenant.id, monitoring_source_id=source.id,
            evidence=[FORTIGATE_SNMP_EVIDENCE],
        )
        second = await service.discover(
            tenant_id=tenant.id, monitoring_source_id=source.id,
            evidence=[FORTIGATE_SNMP_EVIDENCE],
        )
        assert first.created == 1
        assert second.created == 0 and second.updated == 1
        assert first.resources[0].device_id == second.resources[0].device_id

    async def test_changing_evidence_does_not_create_a_duplicate(self, discovery):
        """Poll 2 loses SNMP entirely; the device must not fork."""
        service, source, tenant = discovery
        first = await service.discover(
            tenant_id=tenant.id, monitoring_source_id=source.id,
            evidence=[FORTIGATE_SNMP_EVIDENCE],
        )
        icmp_only = DiscoveryEvidence(
            address="192.168.159.130", transport="icmp", hostname="FG-LAB01",
            reachable=True,
        )
        second = await service.discover(
            tenant_id=tenant.id, monitoring_source_id=source.id,
            evidence=[icmp_only],
        )
        assert second.resources[0].device_id == first.resources[0].device_id
        assert second.resources[0].external_id == first.resources[0].external_id

    async def test_icmp_only_resource_is_unknown_not_guessed(self, discovery):
        service, source, tenant = discovery
        result = await service.discover(
            tenant_id=tenant.id, monitoring_source_id=source.id,
            evidence=[VPC_EVIDENCE],
        )
        outcome = result.resources[0]
        assert outcome.resource_type == "unknown"
        assert outcome.resource_category == "unknown"
        assert outcome.provider is None
        assert outcome.recognition["matched"] is False
        assert result.unknown == 1
        # Still inventoried (honest unknown, not silently dropped).
        assert outcome.device_id

    async def test_capabilities_persist_to_existing_store(self, discovery):
        service, source, tenant = discovery
        result = await service.discover(
            tenant_id=tenant.id, monitoring_source_id=source.id,
            evidence=[FORTIGATE_SNMP_EVIDENCE],
        )
        row = await service.capabilities.get_for_device(
            uuid.UUID(result.resources[0].device_id), tenant.id
        )
        assert row is not None
        assert row.source == "discovery"
        assert row.supports_snmp is True
        assert row.supports_ping is True
        # Discovery never asserts write privileges.
        assert row.supports_config is None
        assert row.supports_reboot is None

    async def test_relationship_written_only_for_resolved_neighbour(self, discovery):
        service, source, tenant = discovery
        parent = DiscoveryEvidence(
            address="10.0.0.1", transport="snmp", hostname="core-sw",
            sys_descr="Cisco IOS Software, C2960S Software (C2960S-UNIVERSALK9-M)",
            reachable=True,
            neighbors=[
                NeighborEvidence(
                    address="192.168.159.130",
                    evidence_type="lldp",
                    local_interface="Gi1/0/1",
                    confidence=90.0,
                ),
                # Never discovered -> must NOT become an edge.
                NeighborEvidence(address="10.9.9.9", evidence_type="lldp"),
            ],
        )
        result = await service.discover(
            tenant_id=tenant.id,
            monitoring_source_id=source.id,
            evidence=[FORTIGATE_SNMP_EVIDENCE, parent],
        )

        assert result.relationships_written == 1
        assert result.unresolved_neighbors == 1

        source_id = next(
            uuid.UUID(r.device_id) for r in result.resources
            if r.address == "10.0.0.1"
        )
        edges = await _edges_from(service, tenant.id, source_id)
        assert len(edges) == 1
        edge = edges[0]
        assert edge.relationship_type == "connected_to"
        assert edge.discovered_by == "lldp"
        assert edge.confidence == 90.0
        assert edge.attributes["local_interface"] == "Gi1/0/1"

    async def test_tenant_isolation_of_discovery(self, discovery):
        service, source, tenant = discovery
        result = await service.discover(
            tenant_id=tenant.id, monitoring_source_id=source.id,
            evidence=[FORTIGATE_SNMP_EVIDENCE],
        )
        device_id = uuid.UUID(result.resources[0].device_id)
        # A different tenant must not resolve this device.
        assert await DeviceRepository(service.db).get(device_id, uuid.uuid4()) is None

    async def test_zero_results_is_empty_not_success(self, discovery):
        """A clean run that recognises nothing must not report SUCCESS.

        Regresses the misleading "SUCCESS with 0 created devices" state: when
        the probe produced no evidence at all (nothing reachable/identifiable),
        the job must surface as EMPTY rather than SUCCESS, which would imply
        devices were discovered.
        """
        service, source, tenant = discovery
        result = await service.discover(
            tenant_id=tenant.id, monitoring_source_id=source.id, evidence=[],
        )
        assert result.status == "EMPTY"
        assert result.created == 0
        assert result.updated == 0
        assert result.resources == []


async def _edges_from(service, tenant_id, source_id):
    """Read edges through the repository's own query surface."""
    from sqlalchemy import select

    from app.models.resource_relationship import ResourceRelationship

    stmt = select(ResourceRelationship).where(
        ResourceRelationship.tenant_id == tenant_id,
        ResourceRelationship.source_resource_id == source_id,
        ResourceRelationship.deleted_at.is_(None),
    )
    return list((await service.db.execute(stmt)).scalars().all())
