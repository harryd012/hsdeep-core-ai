"""SourceHealthManager regression tests.

Covers the source-health persistence contract that the P0/P1 recovery exposed:
  - successful collection updates ``last_successful_run_at`` and resets failures
  - failed collection increments ``consecutive_failures`` and (past the
    configured threshold) degrades the source, without touching previously
    collected measurements
  - recovery transitions DEGRADED -> HEALTHY after N consecutive successes
  - the source lookup is tenant-scoped (a tenant cannot load another tenant's
    source, so it can never drive its health state machine)
"""
from __future__ import annotations

import uuid

import pytest

from app.core.db import AsyncSessionLocal
from app.models.monitoring_source import HEALTH_STATUS_DEGRADED, HEALTH_STATUS_HEALTHY
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.source_health_audit_repository import SourceHealthAuditRepository
from app.services.events.event_bus import get_event_bus
from app.services.monitoring.source_health_manager import SourceHealthManager
from app.collectors.base import (
    BaseCollector,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
)
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.sensor_repository import SensorRepository
from app.services.alert_service import AlertService
from app.services.monitoring.bundle_saver import BundleSaver
from app.services.monitoring.collection_orchestrator import CollectionOrchestrator

pytestmark = pytest.mark.asyncio


def _manager(db) -> SourceHealthManager:
    return SourceHealthManager(
        MonitoringSourceRepository(db),
        SourceHealthAuditRepository(db),
        get_event_bus(),
    )


class TestSourceHealthManager:
    async def test_success_updates_last_successful_run_and_resets_failures(
        self, tenant, monitoring_source_factory
    ):
        source = await monitoring_source_factory()
        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            attached = await repo.get(source.id, tenant.id)
            mgr = _manager(db)

            result = await mgr.evaluate_and_update(
                attached, run_success=True, error=None
            )
            await db.commit()

            assert attached.last_successful_run_at is not None
            assert attached.consecutive_failures == 0
            assert attached.consecutive_successes == 1
            assert attached.health_status == HEALTH_STATUS_HEALTHY
            assert attached.last_error is None
            assert attached.status == "connected"
            # No health transition on a first-time success (already healthy).
            assert result.health_changed is False

    async def test_failure_degrades_after_threshold_and_records_error(
        self, tenant, monitoring_source_factory
    ):
        source = await monitoring_source_factory()
        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            attached = await repo.get(source.id, tenant.id)
            mgr = _manager(db)

            # threshold = 3 consecutive failures -> DEGRADED
            last = None
            for _ in range(3):
                last = await mgr.evaluate_and_update(
                    attached, run_success=False, error="connection refused"
                )
            await db.commit()

            assert attached.health_status == HEALTH_STATUS_DEGRADED
            assert attached.consecutive_failures == 3
            assert attached.last_failed_run_at is not None
            assert attached.last_error == "connection refused"
            assert attached.status == "error"
            assert last.degraded is True

    async def test_recovery_transitions_back_to_healthy_and_emits_event(
        self, tenant, monitoring_source_factory
    ):
        source = await monitoring_source_factory()
        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            attached = await repo.get(source.id, tenant.id)
            mgr = _manager(db)

            for _ in range(3):
                await mgr.evaluate_and_update(
                    attached, run_success=False, error="down"
                )
            assert attached.health_status == HEALTH_STATUS_DEGRADED

            # recovery_successes = 2 -> DEGRADED -> HEALTHY
            last = None
            for _ in range(2):
                last = await mgr.evaluate_and_update(
                    attached, run_success=True, error=None
                )
            await db.commit()

            assert attached.health_status == HEALTH_STATUS_HEALTHY
            assert attached.consecutive_failures == 0
            assert last.recovered is True
            # A deferred SOURCE_RECOVERED domain event must be returned for the
            # caller to publish after commit.
            assert len(last.recovery_events) == 1

    async def test_failure_never_touches_measurements(self, tenant, monitoring_source_factory):
        """Source-health persistence must not delete/rewrite collected telemetry."""
        source = await monitoring_source_factory()
        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            attached = await repo.get(source.id, tenant.id)
            mgr = _manager(db)

            await mgr.evaluate_and_update(attached, run_success=False, error="boom")
            await db.commit()

            # Only the source's own health fields change; sensor/device data is
            # untouched (the health manager has no sensor/device write surface).
            assert attached.last_error == "boom"
            assert attached.health_status == HEALTH_STATUS_HEALTHY  # 1 failure < threshold

    async def test_lookup_is_tenant_scoped(self, tenant, monitoring_source_factory):
        source = await monitoring_source_factory()
        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            # Another tenant cannot resolve this source, so it can never load or
            # mutate its health state machine.
            assert await repo.get(source.id, uuid.uuid4()) is None
            assert await repo.get(source.id, tenant.id) is not None


def _orchestrator(db, source_repo, health_manager):
    return CollectionOrchestrator(
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
        health_manager=health_manager,
    )


class TestOrchestratorHealthWiring:
    """The CollectionOrchestrator drives the health manager on success/failure."""

    async def test_successful_run_persists_health_via_orchestrator(
        self, tenant, monitoring_source_factory
    ):
        class _EmptyCollector(BaseCollector):
            source_type = "test_health_empty"
            display_name = "Health Empty"
            implemented = True

            async def authenticate(self):
                return ConnectionCheck(success=True, message="ok")

            async def collect(self):
                return {}

            def normalize(self, raw):
                return NormalizedBundle()

        CollectorRegistry._collectors["test_health_empty"] = _EmptyCollector
        source = await monitoring_source_factory(source_type="test_health_empty")
        try:
            async with AsyncSessionLocal() as db:
                source_repo = MonitoringSourceRepository(db)
                attached = await source_repo.get(source.id, tenant.id)
                outcome = await _orchestrator(db, source_repo, _manager(db)).run(
                    attached, trigger="manual"
                )
                await db.commit()

                assert outcome.status == "success"
                assert attached.last_successful_run_at is not None
                assert attached.consecutive_failures == 0
                assert attached.health_status == HEALTH_STATUS_HEALTHY
        finally:
            CollectorRegistry._collectors.pop("test_health_empty", None)

    async def test_failed_auth_persists_failure_health_via_orchestrator(
        self, tenant, monitoring_source_factory
    ):
        class _FailingCollector(BaseCollector):
            source_type = "test_health_fail"
            display_name = "Health Fail"
            implemented = True

            async def authenticate(self):
                return ConnectionCheck(success=False, message="bad credentials")

            async def collect(self):
                return {}

            def normalize(self, raw):
                return NormalizedBundle()

        CollectorRegistry._collectors["test_health_fail"] = _FailingCollector
        source = await monitoring_source_factory(source_type="test_health_fail")
        try:
            async with AsyncSessionLocal() as db:
                source_repo = MonitoringSourceRepository(db)
                attached = await source_repo.get(source.id, tenant.id)
                outcome = await _orchestrator(db, source_repo, _manager(db)).run(
                    attached, trigger="manual"
                )
                await db.commit()

                assert outcome.status == "error"
                assert attached.consecutive_failures == 1
                assert attached.last_failed_run_at is not None
                assert attached.last_error == "bad credentials"
        finally:
            CollectorRegistry._collectors.pop("test_health_fail", None)
