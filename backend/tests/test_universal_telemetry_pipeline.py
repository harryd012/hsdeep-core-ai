"""
Canonical telemetry + capability PERSISTENCE (real Postgres).

Proves the chain from spec section 43 end to end at the persistence boundary:

    collector NormalizedBundle
        -> BundleSaver
        -> canonical telemetry rows (metrics) + capability rows
           (resource_capabilities) + resource facts (devices)
        -> canonical read path (MetricRepository canonical queries)

It also pins the invariants that make the layer trustworthy:
  * a provider's own metric spelling still gets a capability,
  * the capability table never stores an unexplained "no",
  * re-running collection refreshes instead of duplicating,
  * every row is tenant-scoped.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.collectors.base import (
    NormalizedBundle,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedMetric,
)
from app.core.db import AsyncSessionLocal
from app.models.device import Device
from app.models.metric import Metric
from app.models.resource_capability import ResourceCapability
from app.repositories.device_repository import DeviceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.resource_capability_repository import (
    ResourceCapabilityRepository,
)
from app.repositories.sensor_repository import SensorRepository
from app.services.infrastructure_intelligence import vocabulary as V
from app.services.infrastructure_intelligence.pipeline import normalize_bundle
from app.services.monitoring.bundle_saver import BundleSaver

pytestmark = pytest.mark.asyncio


def _bundle(external_id: str = "sw-1") -> NormalizedBundle:
    """A realistic collector bundle. TEST fixture data, not product data."""
    now = datetime.now(timezone.utc)
    return NormalizedBundle(
        devices=[
            NormalizedDevice(
                external_id=external_id,
                name="core-sw-01",
                device_type="switch",
                vendor="Cisco",
                status="up",
            )
        ],
        interfaces=[
            NormalizedInterface(
                device_external_id=external_id,
                external_id="Gi1/0/1",
                name="Gi1/0/1",
                oper_status="up",
                in_octets=100.0,
                out_octets=200.0,
            )
        ],
        metrics=[
            NormalizedMetric(
                metric_type="CPU",          # provider spelling, not canonical
                value=23.0,
                recorded_at=now,
                unit="%",
                device_external_id=external_id,
            ),
            NormalizedMetric(
                metric_type="memory",       # another provider spelling
                value=61.0,
                recorded_at=now,
                unit="%",
                device_external_id=external_id,
            ),
        ],
    )


def _saver(db) -> BundleSaver:
    return BundleSaver(
        device_repo=DeviceRepository(db),
        interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db),
        metric_repo=MetricRepository(db),
        event_repo=EventRepository(db),
    )


async def _save(source, bundle: NormalizedBundle):
    async with AsyncSessionLocal() as db:
        result = await _saver(db).save(source, bundle)
        await db.commit()
        return result


async def test_bundle_save_persists_canonical_telemetry_and_capabilities(
    tenant, monitoring_source_factory
):
    source = await monitoring_source_factory(
        tenant_id=tenant.id, source_type="snmp"
    )
    save_result = await _save(source, _bundle())

    assert save_result.metrics_recorded == 2
    assert save_result.availability_recorded == 1
    assert save_result.capabilities_asserted > 0
    assert save_result.resources_enriched == 1

    async with AsyncSessionLocal() as db:
        metrics = (
            await db.execute(
                Metric.__table__.select().where(Metric.tenant_id == tenant.id)
            )
        ).mappings().all()
        by_type = {row["metric_type"]: row for row in metrics}

        # Provider spelling preserved AND canonicalised, with capability+provider.
        cpu = by_type["CPU"]
        assert cpu["metric_name"] == "cpu_utilization_pct"
        assert cpu["capability"] == "cpu"
        assert cpu["provider"] == "snmp"
        assert cpu["value"] == 23.0
        assert by_type["memory"]["capability"] == "memory"

        # Availability is an explicit observation, not a derived number.
        reach = next(row for row in metrics if row["metric_type"] == "reachability")
        assert reach["capability"] == "availability"
        assert reach["boolean_value"] is True
        assert reach["text_value"] == "up"

        device = (
            await db.execute(
                Device.__table__.select().where(Device.tenant_id == tenant.id)
            )
        ).mappings().one()
        assert device["provider"] == "snmp"
        assert device["last_telemetry_at"] is not None
        assert device["lifecycle_state"] == "active"
        assert device["discovered_at"] is not None

        capabilities = (
            await db.execute(
                ResourceCapability.__table__.select().where(
                    ResourceCapability.tenant_id == tenant.id
                )
            )
        ).mappings().all()
        assert capabilities, "the universal layer must assert capabilities"
        by_capability = {row["capability"]: row for row in capabilities}
        assert by_capability["cpu"]["support"] == V.SUPPORT_SUPPORTED
        assert by_capability["cpu"]["status"] == V.COLLECTION_COLLECTED
        assert by_capability["interfaces"]["support"] == V.SUPPORT_SUPPORTED
        # An unsupported capability must carry a reason.
        unsupported = [row for row in capabilities if row["support"] != "supported"]
        assert all(row["reason"] for row in unsupported)


async def test_second_collection_refreshes_instead_of_duplicating(
    tenant, monitoring_source_factory
):
    source = await monitoring_source_factory(
        tenant_id=tenant.id, source_type="snmp"
    )
    await _save(source, _bundle())
    await _save(source, _bundle())

    async with AsyncSessionLocal() as db:
        capability_repo = ResourceCapabilityRepository(db)
        total = await capability_repo.count_for_tenant(tenant.id)
        rows = (
            await db.execute(
                ResourceCapability.__table__.select().where(
                    ResourceCapability.tenant_id == tenant.id
                )
            )
        ).mappings().all()
        # One row per (resource, capability) - re-running must not grow the table.
        assert len(rows) == total
        keys = {(str(row["resource_id"]), row["capability"]) for row in rows}
        assert len(keys) == len(rows)


async def test_capability_rows_are_tenant_scoped(
    tenant, monitoring_source_factory
):
    from app.core.db import AsyncSessionLocal as Session
    from app.models.tenant import Tenant

    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    async with Session() as db:
        db.add(other)
        await db.commit()
        await db.refresh(other)

    source = await monitoring_source_factory(
        tenant_id=tenant.id, source_type="snmp"
    )
    await _save(source, _bundle())

    async with AsyncSessionLocal() as db:
        leaked = await ResourceCapabilityRepository(db).count_for_tenant(other.id)
        assert leaked == 0

    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete

        await db.execute(delete(Tenant).where(Tenant.id == other.id))
        await db.commit()


async def test_canonical_telemetry_read_path(tenant, monitoring_source_factory):
    source = await monitoring_source_factory(
        tenant_id=tenant.id, source_type="snmp"
    )
    await _save(source, _bundle())

    async with AsyncSessionLocal() as db:
        device = (
            await db.execute(
                Device.__table__.select().where(Device.tenant_id == tenant.id)
            )
        ).mappings().one()
        device_id = device["id"]
        repo = MetricRepository(db)

        rows = await repo.list_canonical(
            tenant.id, device_id=device_id, capability="cpu"
        )
        assert len(rows) == 1
        assert rows[0].metric_name == "cpu_utilization_pct"

        total = await repo.count_canonical(tenant.id, device_id=device_id)
        assert total == 3  # two readings + one availability observation

        latest = await repo.latest_canonical_per_metric(
            tenant.id, device_id=device_id
        )
        assert len(latest) == 3  # one row per metric, not per sample

        capabilities = await repo.distinct_capabilities_for_device(
            tenant.id, device_id
        )
        assert "cpu" in capabilities and "availability" in capabilities

        by_capability = await repo.count_by_capability_for_tenant(tenant.id)
        assert by_capability["cpu"] == 1

        newest = await repo.last_recorded_at(tenant.id, device_id=device_id)
        assert newest is not None

        # Bounded queries: a caller cannot ask for an unbounded slice.
        bounded = await repo.list_canonical(tenant.id, limit=1)
        assert len(bounded) == 1


async def test_capability_repository_refuses_an_unexplained_no(tenant):
    async with AsyncSessionLocal() as db:
        repo = ResourceCapabilityRepository(db)
        with pytest.raises(ValueError):
            await repo.upsert(
                tenant_id=tenant.id,
                resource_id=uuid.uuid4(),
                capability="cpu",
                support=V.SUPPORT_UNSUPPORTED,
                reason=None,
            )


async def test_normalization_never_invents_records_for_a_silent_bundle(tenant):
    """A bundle with a device and nothing else produces availability only."""
    bundle = NormalizedBundle(
        devices=[
            NormalizedDevice(
                external_id="ap-1", name="ap-1", device_type="access_point",
                status="unknown",
            )
        ]
    )
    result = normalize_bundle(
        bundle,
        tenant_id=tenant.id,
        provider="meraki",
        device_id_by_external={"ap-1": uuid.uuid4()},
        interface_id_by_key={},
    )
    assert result.metric_records == []
    assert len(result.availability_records) == 1
    # `unknown` device status must NOT be reported as reachable.
    assert result.availability_records[0].boolean_value is False

