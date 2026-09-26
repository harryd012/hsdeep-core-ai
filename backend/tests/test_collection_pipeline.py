"""
Integration tests for the full collection pipeline.

Tests verify the end-to-end flow:
  Scheduler tick() -> CollectionOrchestrator.run() -> Collector ->
  BundleSaver -> Repositories -> AlertService

Runs against the real configured Postgres database (see conftest.py's
module docstring for why - advisory locks, JSONB, and partial unique
indexes are all Postgres-specific).

No collector implementations are modified. A minimal fake collector is
registered per-test to avoid network I/O, and its `normalize()` output
is crafted to exercise real persistence + alert evaluation paths.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.collectors.base import (
    BaseCollector,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedAlertSignal,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedEvent,
    NormalizedMetric,
    NormalizedSensorReading,
)
from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.monitoring_source import MonitoringSource
from app.models.tenant import Tenant
from app.repositories.alert_repository import AlertRepository
from app.repositories.alert_history_repository import AlertHistoryRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.services.alert_service import AlertService
from app.services.monitoring.bundle_saver import BundleSaver
from app.services.monitoring.collection_orchestrator import CollectionOrchestrator

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fake collector that returns known NormalizedBundle data
# ---------------------------------------------------------------------------

_FAKE_SOURCE_TYPE = "test_pipeline_collector"


class _PipelineTestCollector(BaseCollector):
    """Collector that returns a configurable NormalizedBundle from normalize().

    `normalize()` output is set before each test via class variable so the
    collector can be registered once and reused across test cases.
    """

    display_name = "Pipeline Test Collector"
    implemented = True

    _bundle: NormalizedBundle = NormalizedBundle()
    _authenticate_success: bool = True
    _authenticate_message: str = "ok"

    @classmethod
    def reset(cls) -> None:
        cls._bundle = NormalizedBundle()
        cls._authenticate_success = True
        cls._authenticate_message = "ok"

    async def authenticate(self) -> ConnectionCheck:
        return ConnectionCheck(success=self._authenticate_success, message=self._authenticate_message)

    async def collect(self) -> dict:
        return {"test": True}

    def normalize(self, raw: dict) -> NormalizedBundle:
        return self._bundle


def _register_pipeline_collector() -> None:
    _PipelineTestCollector.source_type = _FAKE_SOURCE_TYPE
    CollectorRegistry._collectors[_FAKE_SOURCE_TYPE] = _PipelineTestCollector


def _unregister_pipeline_collector() -> None:
    CollectorRegistry._collectors.pop(_FAKE_SOURCE_TYPE, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_tenant():
    """Creates a unique tenant for each test, cleaned up via cascade."""
    _register_pipeline_collector()
    yield
    _unregister_pipeline_collector()


async def _make_source(tenant_id: uuid.UUID, **overrides) -> MonitoringSource:
    async with AsyncSessionLocal() as db:
        source = MonitoringSource(
            tenant_id=tenant_id,
            name=f"pipeline-test-{uuid.uuid4().hex[:8]}",
            source_type=_FAKE_SOURCE_TYPE,
            is_active=True,
            poll_interval_seconds=60,
            status="pending",
            **overrides,
        )
        db.add(source)
        await db.commit()
        await db.refresh(source)
    return source


class TestCollectionPipeline:
    """End-to-end collection pipeline tests."""

    async def test_full_pipeline_creates_collector_run(self, pipeline_tenant):
        """A successful collection run creates a CollectorRun row with
        status='success' and records upserted entity counts."""
        _PipelineTestCollector._bundle = NormalizedBundle(
            devices=[
                NormalizedDevice(
                    external_id="dev-1",
                    name="Test Device",
                    device_type="other",
                    status="up",
                )
            ],
            sensors=[
                NormalizedSensorReading(
                    external_id="sens-1",
                    name="Test Sensor",
                    metric_type="test_metric",
                    value=0.0,
                    status="up",
                )
            ],
        )

        async with AsyncSessionLocal() as db:
            tenant = Tenant(name="Pipeline Test", slug=f"pt-{uuid.uuid4().hex[:8]}")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        try:
            source = await _make_source(tenant.id)
            source_id = source.id

            async with AsyncSessionLocal() as db:
                source_repo = MonitoringSourceRepository(db)
                attached_source = await source_repo.get(source_id, tenant.id)
                assert attached_source is not None

                orchestrator = CollectionOrchestrator(
                    run_repo=CollectorRunRepository(db),
                    saver=BundleSaver(
                        DeviceRepository(db),
                        InterfaceRepository(db),
                        SensorRepository(db),
                        MetricRepository(db),
                        EventRepository(db),
                    ),
                    alert_service=AlertService(AlertRepository(db)),
                    source_repo=source_repo,
                )
                run = await orchestrator.run(attached_source, trigger="manual")
                await db.commit()

            assert run.status == "success"
            assert run.trigger == "manual"
            assert run.devices_upserted == 1
            assert run.sensors_upserted == 1
            assert run.error is None

            # Verify device was persisted
            async with AsyncSessionLocal() as db:
                device_repo = DeviceRepository(db)
                device = await device_repo.get_by_external_id(source.id, "dev-1")
                assert device is not None
                assert device.name == "Test Device"

                # Verify sensor was persisted
                sensor_repo = SensorRepository(db)
                sensor = await sensor_repo.get_by_external_id(source.id, "sens-1")
                assert sensor is not None
                assert sensor.metric_type == "test_metric"

                # Verify the source status was updated
                source_repo = MonitoringSourceRepository(db)
                updated = await source_repo.get(source.id, tenant.id)
                assert updated is not None
                assert updated.status == "connected"
                assert updated.last_sync_at is not None
        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
                await db.commit()

    async def test_pipeline_with_sensor_status_change_generates_alert(self, pipeline_tenant):
        """A sensor with a warning/critical status change triggers alert
        creation through the alert service."""
        _PipelineTestCollector._bundle = NormalizedBundle(
            devices=[
                NormalizedDevice(
                    external_id="dev-alert-1",
                    name="Alert Device",
                    device_type="other",
                    status="up",
                )
            ],
            sensors=[
                NormalizedSensorReading(
                    external_id="sens-alert-1",
                    name="CPU Load",
                    metric_type="cpu_utilization",
                    status="warning",  # Triggers alert creation
                    value=85.0,
                    warning_threshold=80.0,
                )
            ],
        )

        async with AsyncSessionLocal() as db:
            tenant = Tenant(name="Alert Test", slug=f"at-{uuid.uuid4().hex[:8]}")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        try:
            source = await _make_source(tenant.id)
            source_id = source.id

            async with AsyncSessionLocal() as db:
                source_repo = MonitoringSourceRepository(db)
                attached_source = await source_repo.get(source_id, tenant.id)
                assert attached_source is not None

                orchestrator = CollectionOrchestrator(
                    run_repo=CollectorRunRepository(db),
                    saver=BundleSaver(
                        DeviceRepository(db),
                        InterfaceRepository(db),
                        SensorRepository(db),
                        MetricRepository(db),
                        EventRepository(db),
                    ),
                    alert_service=AlertService(AlertRepository(db)),
                    source_repo=source_repo,
                )
                run = await orchestrator.run(attached_source, trigger="manual")
                await db.commit()

            assert run.status == "success"
            assert run.alerts_upserted >= 1

            # Verify alert was created
            async with AsyncSessionLocal() as db:
                alert_repo = AlertRepository(db)
                # Find the alert triggered for the sensor
                sensor_repo = SensorRepository(db)
                sensor = await sensor_repo.get_by_external_id(source.id, "sens-alert-1")
                assert sensor is not None

                active = await alert_repo.get_active_for_sensor(sensor.id)
                assert active is not None
                assert active.severity == "warning"
                assert active.source == "monitoring_sync"

                # Verify alert history was created
                history_repo = AlertHistoryRepository(db)
                history = await history_repo.list_for_alert(active.id)
                assert len(history) >= 1
                assert any(h.event_type == "opened" for h in history)
        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
                await db.commit()

    async def test_pipeline_skips_concurrent_run(self, pipeline_tenant):
        """When a run is already in progress for a source, the orchestrator
        records a 'skipped' CollectorRun instead of running again."""
        _PipelineTestCollector._bundle = NormalizedBundle()

        async with AsyncSessionLocal() as db:
            tenant = Tenant(name="Skip Test", slug=f"skt-{uuid.uuid4().hex[:8]}")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        try:
            source = await _make_source(tenant.id)

            async with AsyncSessionLocal() as db:
                run_repo = CollectorRunRepository(db)
                source_repo = MonitoringSourceRepository(db)

                # Create a fake "running" collector run for this source
                running_run = await run_repo.create(
                    tenant_id=tenant.id,
                    monitoring_source_id=source.id,
                    trigger="scheduler",
                )

                attached_source = await source_repo.get(source.id, tenant.id)
                assert attached_source is not None

                orchestrator = CollectionOrchestrator(
                    run_repo=run_repo,
                    saver=BundleSaver(
                        DeviceRepository(db),
                        InterfaceRepository(db),
                        SensorRepository(db),
                        MetricRepository(db),
                        EventRepository(db),
                    ),
                    alert_service=AlertService(AlertRepository(db)),
                    source_repo=source_repo,
                )
                run = await orchestrator.run(attached_source, trigger="manual")
                await db.commit()

            assert run.status == "skipped"
            assert "already in progress" in (run.error or "")
        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
                await db.commit()

    async def test_pipeline_with_metrics_and_events(self, pipeline_tenant):
        """The pipeline correctly persists metrics and events alongside
        devices and sensors."""
        now = datetime.now(timezone.utc)
        _PipelineTestCollector._bundle = NormalizedBundle(
            devices=[
                NormalizedDevice(
                    external_id="dev-full-1",
                    name="Full Pipeline Device",
                    device_type="server",
                    vendor="TestVendor",
                    status="up",
                )
            ],
            sensors=[
                NormalizedSensorReading(
                    external_id="sens-full-1",
                    name="Memory Usage",
                    metric_type="memory_utilization",
                    status="up",
                    value=64.5,
                    unit="%",
                )
            ],
            metrics=[
                NormalizedMetric(
                    metric_type="memory_utilization",
                    value=64.5,
                    recorded_at=now,
                    unit="%",
                    device_external_id="dev-full-1",
                    sensor_external_id="sens-full-1",
                ),
                NormalizedMetric(
                    metric_type="memory_utilization",
                    value=62.0,
                    recorded_at=now - timedelta(minutes=5),
                    unit="%",
                    device_external_id="dev-full-1",
                    sensor_external_id="sens-full-1",
                ),
            ],
            events=[
                NormalizedEvent(
                    event_type="test_event",
                    message="Integration test event.",
                    occurred_at=now,
                    severity="info",
                    external_id="evt-1",
                ),
            ],
        )

        async with AsyncSessionLocal() as db:
            tenant = Tenant(name="Full Test", slug=f"ft-{uuid.uuid4().hex[:8]}")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        try:
            source = await _make_source(tenant.id)
            source_id = source.id

            async with AsyncSessionLocal() as db:
                source_repo = MonitoringSourceRepository(db)
                attached_source = await source_repo.get(source_id, tenant.id)
                assert attached_source is not None

                orchestrator = CollectionOrchestrator(
                    run_repo=CollectorRunRepository(db),
                    saver=BundleSaver(
                        DeviceRepository(db),
                        InterfaceRepository(db),
                        SensorRepository(db),
                        MetricRepository(db),
                        EventRepository(db),
                    ),
                    alert_service=AlertService(AlertRepository(db)),
                    source_repo=source_repo,
                )
                run = await orchestrator.run(attached_source, trigger="manual")
                await db.commit()

            assert run.status == "success"
            assert run.devices_upserted == 1
            assert run.sensors_upserted == 1
            assert run.metrics_recorded == 2

            # Verify metric persistence
            async with AsyncSessionLocal() as db:
                sensor_repo = SensorRepository(db)
                sensor = await sensor_repo.get_by_external_id(source.id, "sens-full-1")
                assert sensor is not None

                metric_repo = MetricRepository(db)
                metrics = await metric_repo.list_for_sensor(sensor.id, limit=10)
                # Expect 2 metrics recorded in this run
                assert len(metrics) >= 2
        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
                await db.commit()

    async def test_pipeline_failed_auth_returns_error_run(self, pipeline_tenant):
        """When authenticate() fails, the run is recorded with status='error'
        and the source status is updated to 'error'."""
        _PipelineTestCollector._authenticate_success = False
        _PipelineTestCollector._authenticate_message = "Connection refused: bad hostname"
        _PipelineTestCollector._bundle = NormalizedBundle()

        async with AsyncSessionLocal() as db:
            tenant = Tenant(name="Auth Fail Test", slug=f"aft-{uuid.uuid4().hex[:8]}")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        try:
            source = await _make_source(tenant.id)
            source_id = source.id

            async with AsyncSessionLocal() as db:
                source_repo = MonitoringSourceRepository(db)
                attached_source = await source_repo.get(source_id, tenant.id)
                assert attached_source is not None

                orchestrator = CollectionOrchestrator(
                    run_repo=CollectorRunRepository(db),
                    saver=BundleSaver(
                        DeviceRepository(db),
                        InterfaceRepository(db),
                        SensorRepository(db),
                        MetricRepository(db),
                        EventRepository(db),
                    ),
                    alert_service=AlertService(AlertRepository(db)),
                    source_repo=source_repo,
                )
                run = await orchestrator.run(attached_source, trigger="manual")
                await db.commit()

            assert run.status == "error"
            assert "Connection refused" in (run.error or "")

            # Source status should be updated
            async with AsyncSessionLocal() as db:
                source_repo = MonitoringSourceRepository(db)
                updated = await source_repo.get(source.id, tenant.id)
                assert updated is not None
                assert updated.status == "error"
        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
                await db.commit()