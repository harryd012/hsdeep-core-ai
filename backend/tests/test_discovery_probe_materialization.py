"""Probe persistence + controlled materialization regression tests.

Covers the two-stage Probe -> Review -> Materialize flow against the REAL
database: durable probe results, tenant isolation, fabricated-evidence
rejection, incomplete/failed-job rejection, ICMP-only -> unknown device,
SNMP not_configured eligibility, existing-device identification, idempotency,
quota, partial failure/retry, concurrency, and the observation-only probe
contract.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.core.db import AsyncSessionLocal
from app.models.device import Device
from app.models.discovery_probe import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    OUTCOME_CREATED,
    OUTCOME_EXISTING,
    OUTCOME_REJECTED,
)
from app.models.discovery_scope import DiscoveryScope
from app.models.monitoring_source import MonitoringSource
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.repositories.discovery_probe_repository import DiscoveryProbeRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService
from app.services.discovery.materialization import (
    ProbeJobNotMaterializable,
    ProbeJobNotFound,
    UnknownHostsError,
    materialize_probe_job,
)

pytestmark = pytest.mark.asyncio

REACHABLE_A = {
    "address": "192.168.159.10",
    "reachable": True,
    "latency_ms": 1.2,
    "protocols": {"ssh": "reachable", "snmp": "not_configured"},
    "evidence": ["icmp:reply", "tcp/22:open"],
}
REACHABLE_B = {
    "address": "192.168.159.11",
    "reachable": True,
    "latency_ms": 2.1,
    "protocols": {"snmp": "not_configured"},
    "evidence": ["icmp:reply"],
}
UNREACHABLE = {
    "address": "192.168.159.20",
    "reachable": False,
    "protocols": {"ssh": "timeout", "snmp": "not_configured"},
    "evidence": [],
}


def _billing(db) -> BillingService:
    return BillingService(
        plans=PlanRepository(db),
        subscriptions=SubscriptionRepository(db),
        db=db,
    )


async def _make_tenant(name: str) -> Tenant:
    async with AsyncSessionLocal() as db:
        t = Tenant(name=name, slug=f"t-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t


async def _drop_tenant(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.commit()


async def _make_scope_and_job(
    tenant_id: uuid.UUID,
    *,
    host_specs: list[dict],
    status: str = JOB_STATUS_COMPLETED,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a private_cidr scope + a probe job + its host rows. Returns (scope_id, job_id)."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        scope = DiscoveryScope(
            tenant_id=tenant_id,
            name=f"lab-{uuid.uuid4().hex[:6]}",
            scope_type="private_cidr",
            target="192.168.159.0/24",
            allowed_protocols=["icmp", "snmp"],
        )
        db.add(scope)
        await db.flush()

        repo = DiscoveryProbeRepository(db)
        reachable = sum(1 for s in host_specs if s.get("reachable"))
        job = await repo.create_job(
            tenant_id=tenant_id,
            scope_id=scope.id,
            target="192.168.159.0/24",
            scope_type="private_cidr",
            status=status,
            hosts_probed=len(host_specs),
            hosts_total=254,
            reachable=reachable,
            truncated=False,
            started_at=now,
            finished_at=now if status == JOB_STATUS_COMPLETED else None,
        )
        await repo.add_hosts(job.id, tenant_id, [dict(s) for s in host_specs])
        await db.commit()
        return scope.id, job.id


async def _device_count(db, tenant_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Device).where(
        Device.tenant_id == tenant_id, Device.is_deleted.is_(False)
    )
    return int((await db.execute(stmt)).scalar_one())


class TestDurability:
    async def test_probe_results_persist_and_are_retrievable(self, tenant):
        scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_A, UNREACHABLE]
        )
        async with AsyncSessionLocal() as db:
            repo = DiscoveryProbeRepository(db)
            job = await repo.get_job(job_id, tenant.id)
            assert job is not None
            assert job.status == JOB_STATUS_COMPLETED
            assert job.scope_id == scope_id
            assert job.hosts_probed == 2
            assert job.reachable == 1

            hosts = await repo.get_hosts(job_id, tenant.id)
            by_addr = {h.address: h for h in hosts}
            assert set(by_addr) == {REACHABLE_A["address"], UNREACHABLE["address"]}
            assert by_addr[REACHABLE_A["address"]].reachable is True
            assert by_addr[REACHABLE_A["address"]].protocols["snmp"] == "not_configured"
            assert by_addr[UNREACHABLE["address"]].reachable is False


class TestTenantIsolation:
    async def test_other_tenant_cannot_view_or_materialize(self, tenant):
        other = await _make_tenant("Other")
        try:
            scope_id, job_id = await _make_scope_and_job(
                tenant.id, host_specs=[REACHABLE_A]
            )
            async with AsyncSessionLocal() as db:
                repo = DiscoveryProbeRepository(db)
                assert await repo.get_job(job_id, other.id) is None
                assert await repo.get_job(scope_id, other.id) is None
                with pytest.raises(ProbeJobNotFound):
                    await materialize_probe_job(
                        db=db,
                        tenant_id=other.id,
                        job_id=job_id,
                        addresses=[REACHABLE_A["address"]],
                        monitoring_source_id=uuid.uuid4(),
                    )
        finally:
            await _drop_tenant(other.id)


class TestEligibility:
    async def test_arbitrary_address_not_in_job_is_rejected(self, tenant):
        _scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_A]
        )
        async with AsyncSessionLocal() as db:
            with pytest.raises(UnknownHostsError):
                await materialize_probe_job(
                    db=db,
                    tenant_id=tenant.id,
                    job_id=job_id,
                    addresses=["10.0.0.99"],
                    monitoring_source_id=uuid.uuid4(),
                )

    async def test_incomplete_or_failed_job_cannot_be_materialized(self, tenant):
        for bad_status in ("running", JOB_STATUS_FAILED):
            _scope_id, job_id = await _make_scope_and_job(
                tenant.id, host_specs=[REACHABLE_A], status=bad_status
            )
            async with AsyncSessionLocal() as db:
                with pytest.raises(ProbeJobNotMaterializable):
                    await materialize_probe_job(
                        db=db,
                        tenant_id=tenant.id,
                        job_id=job_id,
                        addresses=[REACHABLE_A["address"]],
                        monitoring_source_id=uuid.uuid4(),
                    )


class TestMaterializeSuccess:
    async def test_icmp_only_host_becomes_unknown_device(
        self, tenant, seeded_subscription, monitoring_source_factory
    ):
        source = await monitoring_source_factory(source_type="snmp")
        _scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_B]
        )
        async with AsyncSessionLocal() as db:
            report = await materialize_probe_job(
                db=db,
                tenant_id=tenant.id,
                job_id=job_id,
                addresses=[REACHABLE_B["address"]],
                monitoring_source_id=source.id,
                billing=_billing(db),
            )
            await db.commit()

        assert report.created == 1
        assert report.rejected == 0
        async with AsyncSessionLocal() as db:
            devs = (
                await db.execute(
                    select(Device).where(
                        Device.tenant_id == tenant.id,
                        Device.ip_address == REACHABLE_B["address"],
                    )
                )
            ).scalars().all()
            assert len(devs) == 1
            assert devs[0].device_type == "unknown"

    async def test_snmp_not_configured_does_not_block_creation(
        self, tenant, seeded_subscription, monitoring_source_factory
    ):
        """A host with snmp=not_configured is still eligible; SNMP is never required."""
        source = await monitoring_source_factory(source_type="snmp")
        spec = dict(REACHABLE_B)
        assert spec["protocols"]["snmp"] == "not_configured"
        _scope_id, job_id = await _make_scope_and_job(tenant.id, host_specs=[spec])

        async with AsyncSessionLocal() as db:
            report = await materialize_probe_job(
                db=db,
                tenant_id=tenant.id,
                job_id=job_id,
                addresses=[spec["address"]],
                monitoring_source_id=source.id,
                billing=_billing(db),
            )
            await db.commit()

        assert report.created == 1
        assert report.rejected == 0
        assert report.failed == 0

    async def test_existing_device_is_identified(
        self, tenant, seeded_subscription, monitoring_source_factory
    ):
        source = await monitoring_source_factory(source_type="snmp")
        _scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_A]
        )
        # Pre-create a canonical device for the same address + source.
        async with AsyncSessionLocal() as db:
            await DeviceRepository(db).upsert(
                tenant_id=tenant.id,
                monitoring_source_id=source.id,
                external_id=f"address:{REACHABLE_A['address']}",
                name="already-there",
                device_type="unknown",
                ip_address=REACHABLE_A["address"],
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            report = await materialize_probe_job(
                db=db,
                tenant_id=tenant.id,
                job_id=job_id,
                addresses=[REACHABLE_A["address"]],
                monitoring_source_id=source.id,
                billing=_billing(db),
            )
            await db.commit()

        assert report.created == 0
        assert report.existing == 1
        async with AsyncSessionLocal() as db:
            assert await _device_count(db, tenant.id) == 1

    async def test_repeated_materialization_does_not_duplicate(
        self, tenant, seeded_subscription, monitoring_source_factory
    ):
        source = await monitoring_source_factory(source_type="snmp")
        _scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_A]
        )

        async with AsyncSessionLocal() as db:
            r1 = await materialize_probe_job(
                db=db,
                tenant_id=tenant.id,
                job_id=job_id,
                addresses=[REACHABLE_A["address"]],
                monitoring_source_id=source.id,
                billing=_billing(db),
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            r2 = await materialize_probe_job(
                db=db,
                tenant_id=tenant.id,
                job_id=job_id,
                addresses=[REACHABLE_A["address"]],
                monitoring_source_id=source.id,
                billing=_billing(db),
            )
            await db.commit()

        assert r1.created == 1
        assert r2.created == 0
        assert r2.existing == 1
        async with AsyncSessionLocal() as db:
            assert await _device_count(db, tenant.id) == 1


class TestPartialFailureAndRetry:
    async def test_partial_failures_reported_and_retryable(
        self, tenant, seeded_subscription, monitoring_source_factory
    ):
        source = await monitoring_source_factory(source_type="snmp")
        _scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_A, UNREACHABLE]
        )
        addrs = [REACHABLE_A["address"], UNREACHABLE["address"]]

        async with AsyncSessionLocal() as db:
            r1 = await materialize_probe_job(
                db=db,
                tenant_id=tenant.id,
                job_id=job_id,
                addresses=addrs,
                monitoring_source_id=source.id,
                billing=_billing(db),
            )
            await db.commit()

        assert r1.created == 1
        assert r1.rejected == 1
        o1 = {o.address: o for o in r1.hosts}
        assert o1[REACHABLE_A["address"]].outcome == OUTCOME_CREATED
        assert o1[UNREACHABLE["address"]].outcome == OUTCOME_REJECTED

        # Retry: the created host is terminal (existing), the unreachable host
        # is still rejected - never fabricated, never duplicated.
        async with AsyncSessionLocal() as db:
            r2 = await materialize_probe_job(
                db=db,
                tenant_id=tenant.id,
                job_id=job_id,
                addresses=addrs,
                monitoring_source_id=source.id,
                billing=_billing(db),
            )
            await db.commit()

        assert r2.created == 0
        assert r2.existing == 1
        assert r2.rejected == 1
        async with AsyncSessionLocal() as db:
            assert await _device_count(db, tenant.id) == 1


class TestQuota:
    async def test_quota_exhaustion_blocks_device_creation(
        self, tenant, monitoring_source_factory
    ):
        source = await monitoring_source_factory(source_type="snmp")
        # Active subscription whose device_limit is 0 -> over_limit -> 0 headroom.
        async with AsyncSessionLocal() as db:
            plan = Plan(
                name=f"zero-{uuid.uuid4().hex[:6]}",
                tier=f"zero-{uuid.uuid4().hex[:6]}",
                device_limit=0,
            )
            db.add(plan)
            await db.flush()
            now = datetime.now(timezone.utc)
            db.add(
                Subscription(
                    tenant_id=tenant.id,
                    plan_id=plan.id,
                    status="active",
                    current_period_start=now,
                    current_period_end=now + timedelta(days=30),
                )
            )
            await db.commit()

        _scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_A]
        )

        async with AsyncSessionLocal() as db:
            report = await materialize_probe_job(
                db=db,
                tenant_id=tenant.id,
                job_id=job_id,
                addresses=[REACHABLE_A["address"]],
                monitoring_source_id=source.id,
                billing=_billing(db),
            )
            await db.commit()

        assert report.created == 0
        assert report.rejected == 1
        assert report.hosts[0].error == "device_quota_exceeded"
        async with AsyncSessionLocal() as db:
            assert await _device_count(db, tenant.id) == 0


class TestConcurrency:
    async def test_concurrent_materialization_does_not_duplicate(
        self, tenant, seeded_subscription, monitoring_source_factory
    ):
        source = await monitoring_source_factory(source_type="snmp")
        _scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_A]
        )

        async def _do():
            async with AsyncSessionLocal() as db:
                report = await materialize_probe_job(
                    db=db,
                    tenant_id=tenant.id,
                    job_id=job_id,
                    addresses=[REACHABLE_A["address"]],
                    monitoring_source_id=source.id,
                    billing=_billing(db),
                )
                await db.commit()
                return report

        r1, r2 = await asyncio.gather(_do(), _do())

        assert r1.created + r2.created == 1
        assert r1.existing + r2.existing == 1
        async with AsyncSessionLocal() as db:
            assert await _device_count(db, tenant.id) == 1


class TestObservationOnlyContract:
    async def test_probe_persistence_creates_no_device(
        self, tenant, seeded_subscription
    ):
        """The probe/persist step is observation-only: a job + hosts exist but
        NO canonical Device is created. Only materialize creates devices."""
        _scope_id, job_id = await _make_scope_and_job(
            tenant.id, host_specs=[REACHABLE_A, REACHABLE_B]
        )
        async with AsyncSessionLocal() as db:
            repo = DiscoveryProbeRepository(db)
            job = await repo.get_job(job_id, tenant.id)
            assert job is not None
            hosts = await repo.get_hosts(job_id, tenant.id)
            assert len(hosts) == 2
            assert await _device_count(db, tenant.id) == 0

