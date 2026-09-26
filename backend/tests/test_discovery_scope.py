"""
Focused DiscoveryScope tests (Phase 6.3A §13).

Target/protocol validation and policy->probe mapping are pure and tested with no
external dependency. Tenant isolation is tested against the real database, because
isolation must not be mocked.

The bounded probe itself is NOT re-tested here - Phase 6.1 owns that suite.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.db import AsyncSessionLocal
from app.models.discovery_scope import DiscoveryScope
from app.models.monitoring_source import MonitoringSource
from app.models.tenant import Tenant
from app.repositories.discovery_scope_repository import DiscoveryScopeRepository
from app.services.discovery.scope_runner import (
    ScopeNotProbeable,
    protocols_for_scope,
)
from app.services.discovery.scope_target import (
    ScopeTargetInvalid,
    validate_protocols,
    validate_target,
)

pytestmark = pytest.mark.asyncio


class TestTargetValidation:
    """§5 - a target is validated by shape; ownership is never inferred."""

    def test_private_cidr_normalised(self):
        assert validate_target("private_cidr", "192.168.159.1/24") == "192.168.159.0/24"
        assert validate_target("private_cidr", "10.0.0.0/8") == "10.0.0.0/8"

    def test_invalid_cidr_rejected(self):
        for bad in ("not-a-cidr", "10.0.0.0/33", "", "   "):
            with pytest.raises(ScopeTargetInvalid):
                validate_target("private_cidr", bad)

    def test_public_cidr_cannot_be_unbounded(self):
        """0.0.0.0/0 (and anything broader than /16) must be refused."""
        for bad in ("0.0.0.0/0", "0.0.0.0/8", "::/0"):
            with pytest.raises(ScopeTargetInvalid):
                validate_target("public_cidr", bad)
        assert validate_target("public_cidr", "203.0.113.0/24") == "203.0.113.0/24"

    def test_public_ip_requires_a_global_address(self):
        with pytest.raises(ScopeTargetInvalid):
            validate_target("public_ip", "10.0.0.5")
        assert validate_target("public_ip", "9.9.9.9") == "9.9.9.9"

    def test_seed_device_accepts_ip_or_hostname(self):
        assert validate_target("seed_device", "10.0.0.10") == "10.0.0.10"
        assert validate_target("seed_device", "Router-01.corp.example.com") == (
            "router-01.corp.example.com"
        )
        with pytest.raises(ScopeTargetInvalid):
            validate_target("seed_device", "not a host!!")

    def test_dns_domain_and_cloud_identifiers(self):
        assert validate_target("dns_domain", "Example.COM") == "example.com"
        with pytest.raises(ScopeTargetInvalid):
            validate_target("dns_domain", "no-tld")
        assert validate_target("cloud_account", "123456789012") == "123456789012"
        assert validate_target("cloud_project", "my-project-1") == "my-project-1"

    def test_unknown_scope_type_rejected(self):
        with pytest.raises(ScopeTargetInvalid):
            validate_target("internet", "0.0.0.0/0")

    def test_unknown_protocol_rejected_and_deduplicated(self):
        assert validate_protocols(["SSH", "ssh", "https"]) == ["ssh", "https"]
        assert validate_protocols(None) == []
        with pytest.raises(ScopeTargetInvalid):
            validate_protocols(["telnet"])


class TestPolicyToProbe:
    """§7/§8 - the scope's policy decides what is probed. Nothing is hard-coded."""

    def _scope(self, **kw) -> DiscoveryScope:
        base = {
            "tenant_id": uuid.uuid4(),
            "name": "s",
            "scope_type": "private_cidr",
            "target": "10.0.0.0/30",
            "allowed_protocols": [],
        }
        base.update(kw)
        return DiscoveryScope(**base)

    def test_only_allowed_tcp_protocols_are_probed(self):
        scope = self._scope(allowed_protocols=["ssh", "https"])
        mapping = protocols_for_scope(scope)
        assert set(mapping) == {"ssh", "https"}
        assert mapping["ssh"] == 22
        assert mapping["https"] == 443

    def test_empty_allow_list_means_icmp_only(self):
        """A scope with no allowed protocols must not open a single TCP socket."""
        assert protocols_for_scope(self._scope(allowed_protocols=[])) == {}

    async def test_unprobeable_scope_types_are_refused(self):
        from app.services.discovery.scope_runner import probe_scope

        for scope_type in ("cloud_account", "dns_domain", "cloud_project"):
            with pytest.raises(ScopeNotProbeable):
                await probe_scope(self._scope(scope_type=scope_type))


# ---------------------------------------------------------------------------
# Tenant isolation - against the real database, never mocked (§13/§14)
# ---------------------------------------------------------------------------

async def _make_tenant(label: str) -> Tenant:
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=f"Scope {label}", slug=f"scope-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
        return tenant


async def _make_scope(tenant_id: uuid.UUID, name: str, target: str) -> DiscoveryScope:
    async with AsyncSessionLocal() as db:
        scope = await DiscoveryScopeRepository(db).create(
            tenant_id=tenant_id,
            name=name,
            scope_type="private_cidr",
            target=target,
            allowed_protocols=["ssh"],
            max_depth=2,
            max_hosts=16,
            max_concurrency=4,
            timeout=1.0,
            excluded_targets=["10.10.0.9"],
        )
        await db.commit()
        await db.refresh(scope)
        return scope


async def _drop_tenant(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            DiscoveryScope.__table__.delete().where(DiscoveryScope.tenant_id == tenant_id)
        )
        await db.execute(
            MonitoringSource.__table__.delete().where(MonitoringSource.tenant_id == tenant_id)
        )
        await db.execute(Tenant.__table__.delete().where(Tenant.id == tenant_id))
        await db.commit()


class TestTenantIsolation:
    async def test_same_cidr_in_two_tenants_is_independent(self):
        """§4: Tenant A and Tenant B may both own 10.10.0.0/16."""
        a = await _make_tenant("A")
        b = await _make_tenant("B")
        try:
            scope_a = await _make_scope(a.id, "LAN", "10.10.0.0/16")
            scope_b = await _make_scope(b.id, "LAN", "10.10.0.0/16")
            assert scope_a.id != scope_b.id
            assert scope_a.tenant_id == a.id
            assert scope_b.tenant_id == b.id

            async with AsyncSessionLocal() as db:
                repo = DiscoveryScopeRepository(db)
                # Each tenant sees exactly its own scope, despite the same target.
                only_a = await repo.list(a.id)
                only_b = await repo.list(b.id)
                assert [s.id for s in only_a] == [scope_a.id]
                assert [s.id for s in only_b] == [scope_b.id]
                # And cannot read the other's by id.
                assert await repo.get(scope_b.id, a.id) is None
                assert await repo.get(scope_a.id, b.id) is None
        finally:
            await _drop_tenant(a.id)
            await _drop_tenant(b.id)

    async def test_policy_fields_persist(self):
        """§7: allowed_protocols / depth / hosts / concurrency / exclusions."""
        a = await _make_tenant("P")
        try:
            scope = await _make_scope(a.id, "Policy", "10.10.0.0/28")
            async with AsyncSessionLocal() as db:
                fresh = await DiscoveryScopeRepository(db).get(scope.id, a.id)
            assert fresh.allowed_protocols == ["ssh"]
            assert fresh.max_depth == 2
            assert fresh.max_hosts == 16
            assert fresh.max_concurrency == 4
            assert fresh.timeout == 1.0
            assert fresh.excluded_targets == ["10.10.0.9"]
            assert fresh.enabled is True
            assert fresh.status == "pending"
        finally:
            await _drop_tenant(a.id)

    async def test_scope_carries_no_secret_and_credential_is_a_reference(self):
        """§6/§11: credentials are referenced, never stored or returned."""
        a = await _make_tenant("C")
        try:
            scope = await _make_scope(a.id, "CredRef", "10.10.0.0/28")
            columns = set(scope.__table__.columns.keys())
            for forbidden in ("password", "password_encrypted", "community", "api_key"):
                assert forbidden not in columns, forbidden
            # credential_profile_id is a FK to the existing credential store.
            assert "credential_profile_id" in columns
            assert scope.credential_profile_id is None
        finally:
            await _drop_tenant(a.id)
