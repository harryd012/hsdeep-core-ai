"""Resource-context tests: normalized context for Device Details + AI handoff.

DB-backed, reusing the existing tenant / monitoring_source_factory fixtures.
Context is produced by the discovery service itself, so these tests prove the
whole chain end-to-end: evidence -> recognition -> Device -> capabilities ->
relationship -> normalized context.
"""
from __future__ import annotations

import uuid

import pytest_asyncio

from app.core.db import AsyncSessionLocal
from app.services.discovery import (
    DiscoveryEvidence,
    DiscoveryService,
    NeighborEvidence,
)
from app.services.resource_context import (
    WRITE_CAPABILITY_FLAGS,
    build_resource_context,
)

FORTIGATE = DiscoveryEvidence(
    address="192.168.159.130",
    transport="snmp",
    source="snmp",
    hostname="FG-LAB01",
    sys_descr="FortiGate-60F v7.2.8,build1396,230912 (GA.M)",
    sys_object_id="1.3.6.1.4.1.12356.101.1.1",
    serial="FGT60FTK20000001",
    model="FortiGate-60F",
    reachable=True,
    capabilities=["snmp"],
)

PARENT = DiscoveryEvidence(
    address="10.0.0.1",
    transport="snmp",
    source="snmp",
    hostname="core-sw",
    sys_descr="Cisco IOS Software, C2960S Software (C2960S-UNIVERSALK9-M)",
    reachable=True,
    neighbors=[
        NeighborEvidence(
            address="192.168.159.130",
            evidence_type="lldp",
            local_interface="Gi1/0/1",
            confidence=90.0,
        )
    ],
)


@pytest_asyncio.fixture
async def discovered(tenant, monitoring_source_factory):
    source = await monitoring_source_factory(
        source_type="snmp", protocol="snmp", hostname="192.168.159.130"
    )
    async with AsyncSessionLocal() as session:
        try:
            service = DiscoveryService(session)
            result = await service.discover(
                tenant_id=tenant.id,
                monitoring_source_id=source.id,
                evidence=[FORTIGATE, PARENT],
            )
            yield session, tenant, result
        finally:
            await session.rollback()


def _device_id(result, address: str) -> uuid.UUID:
    return uuid.UUID(
        next(r.device_id for r in result.resources if r.address == address)
    )


class TestResourceContext:
    async def test_identity_block_is_populated_from_discovery(self, discovered):
        session, tenant, result = discovered
        ctx = await build_resource_context(
            session, tenant.id, _device_id(result, "192.168.159.130")
        )

        assert ctx is not None
        resource = ctx["resource"]
        assert resource["resource_type"] == "firewall"
        assert resource["resource_category"] == "security"
        assert resource["vendor"] == "Fortinet"
        assert resource["provider"] == "fortinet"
        assert resource["serial_number"] == "FGT60FTK20000001"

    async def test_discovery_provenance_has_no_secrets(self, discovered):
        session, tenant, result = discovered
        ctx = await build_resource_context(
            session, tenant.id, _device_id(result, "192.168.159.130")
        )

        discovery = ctx["discovery"]
        assert discovery["identity_source"] == "serial_number"
        assert "identity_keys" in discovery
        assert discovery["recognition"]["method"] == "adapter_fingerprint"
        for forbidden in ("community", "password", "secret", "token", "api_key"):
            assert not any(forbidden in k.lower() for k in discovery)

    async def test_write_capabilities_are_separate_and_never_derived(self, discovered):
        session, tenant, result = discovered
        ctx = await build_resource_context(
            session, tenant.id, _device_id(result, "192.168.159.130")
        )

        assert ctx["write_capabilities"] == {}
        assert ctx["read_capabilities"].get("supports_snmp") is True
        for flag in WRITE_CAPABILITY_FLAGS:
            assert flag not in ctx["read_capabilities"]

    async def test_relationships_report_direction_and_evidence(self, discovered):
        session, tenant, result = discovered
        switch_id = _device_id(result, "10.0.0.1")
        ctx = await build_resource_context(session, tenant.id, switch_id)

        deps = ctx["relationships"]["depends_on"]
        assert len(deps) == 1
        edge = deps[0]
        assert edge["direction"] == "outbound"
        assert edge["discovered_by"] == "lldp"
        assert edge["confidence"] == 90.0
        assert edge["neighbour_name"] == "FG-LAB01"
        assert edge["attributes"]["local_interface"] == "Gi1/0/1"

        # The far endpoint sees the mirrored inbound edge.
        fg_ctx = await build_resource_context(
            session, tenant.id, _device_id(result, "192.168.159.130")
        )
        inbound = fg_ctx["relationships"]["depended_on_by"]
        assert len(inbound) == 1
        assert inbound[0]["direction"] == "inbound"

    async def test_ai_context_block_is_consumable(self, discovered):
        session, tenant, result = discovered
        device_id = _device_id(result, "192.168.159.130")
        ctx = await build_resource_context(session, tenant.id, device_id)

        ai = ctx["ai_context"]
        assert ai["resource_id"] == str(device_id)
        assert ai["resource_type"] == "firewall"
        assert ai["resource_category"] == "security"
        assert ai["vendor"] == "Fortinet"
        assert "supports_snmp" in ai["capabilities"]
        assert ai["status"] == "up"

    async def test_unknown_resource_context_is_honest(
        self, tenant, monitoring_source_factory
    ):
        """An ICMP-only resource yields unknown type and no invented vendor."""
        source = await monitoring_source_factory(source_type="icmp", hostname="10.9.9.9")
        async with AsyncSessionLocal() as session:
            try:
                result = await DiscoveryService(session).discover(
                    tenant_id=tenant.id,
                    monitoring_source_id=source.id,
                    evidence=[DiscoveryEvidence(
                        address="10.9.9.9", transport="icmp",
                        hostname="VPC1", reachable=True,
                    )],
                )
                ctx = await build_resource_context(
                    session, tenant.id, _device_id(result, "10.9.9.9")
                )
                assert ctx["resource"]["resource_type"] == "unknown"
                assert ctx["resource"]["vendor"] is None
                assert ctx["resource"]["provider"] is None
                assert ctx["discovery"]["recognition"]["matched"] is False
            finally:
                await session.rollback()

    async def test_other_tenant_cannot_read_context(self, discovered):
        session, _tenant, result = discovered
        assert await build_resource_context(
            session, uuid.uuid4(), _device_id(result, "192.168.159.130")
        ) is None
