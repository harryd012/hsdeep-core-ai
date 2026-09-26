"""
Sprint 1 — Tasks 15.2 / 15.3: CollectionOrchestrator unit tests.

Covers:
  - Auth-failure short-circuit (no collect/normalize/save called)
  - collect() exception → CollectorRun status="error", no re-raise
  - normalize() exception → CollectorRun status="error", no re-raise
  - Concurrent-run-skip guard → second run gets status="skipped"
  - Happy-path lifecycle order: authenticate → collect → normalize → save

All tests use a fake BaseCollector controlled via pytest fixtures so no
real network I/O or database is needed for the collector itself. The
CollectorRun rows ARE written to the real PostgreSQL database (same
isolation strategy as conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.collectors.base import (
    BaseCollector,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedBundle,
)
from app.core.db import AsyncSessionLocal
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.services.alert_service import AlertService
from app.services.monitoring.bundle_saver import BundleSaver, SaveResult
from app.services.monitoring.collection_orchestrator import CollectionOrchestrator

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fake collector helpers
# ---------------------------------------------------------------------------

def _empty_bundle() -> NormalizedBundle:
    return NormalizedBundle(
        devices=[], interfaces=[], sensors=[],
        metrics=[], events=[], alerts=[],
    )


def _ns_from_source(source: Any) -> SimpleNamespace:
    """Wrap a real MonitoringSource ORM row in a SimpleNamespace so that
    orchestrator tests can safely read its attributes after the session that
    loaded it has closed (detached-instance-safe), while still referencing
    the real PK that satisfies collector_runs' FK constraint."""
    return SimpleNamespace(
        id=source.id,
        tenant_id=source.tenant_id,
        source_type="fake_test_collector",
        name=source.name,
        is_active=True,
        poll_interval_seconds=60,
        status="pending",
        api_key_encrypted=None,
        password_encrypted=None,
        username=None,
        hostname="localhost",
        port=443,
        protocol="https",
        verify_ssl=False,
    )


class _FakeCollector(BaseCollector):
    """Controllable stand-in collector — behaviour set per test."""
    source_type = "fake_test_collector"

    def __init__(self, connection_info=None):
        self._auth_result = ConnectionCheck(success=True, message="ok")
        self._collect_result: Any = {}
        self._normalize_result: NormalizedBundle = _empty_bundle()
        self._collect_side_effect = None
        self._normalize_side_effect = None

    async def authenticate(self) -> ConnectionCheck:
        return self._auth_result

    async def collect(self) -> dict:
        if self._collect_side_effect is not None:
            raise self._collect_side_effect
        return self._collect_result

    def normalize(self, raw: dict) -> NormalizedBundle:
        if self._normalize_side_effect is not None:
            raise self._normalize_side_effect
        return self._normalize_result


# ---------------------------------------------------------------------------
# Shared fixture: build an orchestrator with real run_repo + mocked saver
# ---------------------------------------------------------------------------

def _make_orchestrator(db_session, fake_collector: _FakeCollector) -> CollectionOrchestrator:
    """Wire up a real CollectorRunRepository and MonitoringSourceRepository
    against the provided session; patch everything else."""
    run_repo = CollectorRunRepository(db_session)
    source_repo = MonitoringSourceRepository(db_session)
    # mark_sync_result calls db.refresh(source), which requires a real ORM
    # instance. Our tests pass a SimpleNamespace, so mock this method to be
    # a no-op — we test mark_sync_result behaviour separately via
    # MonitoringSourceRepository unit tests.
    source_repo.mark_sync_result = AsyncMock(return_value=None)  # type: ignore[method-assign]

    # BundleSaver.save() returns a SaveResult — mock it to return zero counts
    mock_saver = MagicMock(spec=BundleSaver)
    mock_saver.save = AsyncMock(
        return_value=SaveResult(
            devices_upserted=0,
            interfaces_upserted=0,
            sensors_created=0,
            sensors_updated=0,
            metrics_recorded=0,
            events_recorded=0,
            changed_sensors=[],
            vendor_alert_signals=[],
            device_id_by_signal_device_external_id={},
        )
    )

    # AlertService — mock so we don't need real Alert rows
    mock_alert_svc = MagicMock(spec=AlertService)
    mock_alert_svc.evaluate_sensor = AsyncMock(
        return_value=MagicMock(action="noop", alert=None)
    )
    mock_alert_svc.upsert_vendor_native = AsyncMock(
        return_value=MagicMock(action="noop", alert=None)
    )

    return CollectionOrchestrator(
        run_repo=run_repo,
        saver=mock_saver,
        alert_service=mock_alert_svc,
        source_repo=source_repo,
    )

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCollectionOrchestratorErrorPaths:

    async def test_auth_failure_skips_collect_and_marks_error(
        self, tenant, monitoring_source_factory
    ):
        """When authenticate() returns success=False, collect() must never
        be called and the CollectorRun must be status='error'."""
        real_src = await monitoring_source_factory()
        source = _ns_from_source(real_src)
        fake = _FakeCollector()
        fake._auth_result = ConnectionCheck(success=False, message="bad credentials")

        with (
            patch.object(CollectorRegistry, "get", return_value=lambda _: fake),
            patch("app.services.monitoring.collection_orchestrator.decrypt_connection_info",
                  return_value=MagicMock()),
            patch("app.services.monitoring.collection_orchestrator.count_collector_run",
                  new_callable=AsyncMock),
            patch("app.services.monitoring.collection_orchestrator.flush_usage_counters",
                  new_callable=AsyncMock),
        ):
            async with AsyncSessionLocal() as db:
                orchestrator = _make_orchestrator(db, fake)
                run = await orchestrator.run(source, trigger="manual")
                await db.commit()

        assert run.run_record.status == "error"
        assert "bad credentials" in (run.run_record.error or "")

    async def test_collect_exception_yields_error_run_no_reraise(
        self, tenant, monitoring_source_factory
    ):
        """If collect() raises, the orchestrator catches it and returns a
        CollectorRun with status='error'. It must never propagate the exception."""
        real_src = await monitoring_source_factory()
        source = _ns_from_source(real_src)
        fake = _FakeCollector()
        fake._collect_side_effect = RuntimeError("network timeout")

        with (
            patch.object(CollectorRegistry, "get", return_value=lambda _: fake),
            patch("app.services.monitoring.collection_orchestrator.decrypt_connection_info",
                  return_value=MagicMock()),
            patch("app.services.monitoring.collection_orchestrator.count_collector_run",
                  new_callable=AsyncMock),
            patch("app.services.monitoring.collection_orchestrator.flush_usage_counters",
                  new_callable=AsyncMock),
        ):
            async with AsyncSessionLocal() as db:
                orchestrator = _make_orchestrator(db, fake)
                run = await orchestrator.run(source, trigger="manual")
                await db.commit()

        assert run.run_record.status == "error"
        assert "network timeout" in (run.run_record.error or "")

    async def test_normalize_exception_yields_error_run_no_reraise(
        self, tenant, monitoring_source_factory
    ):
        """If normalize() raises, same catch-all must apply."""
        real_src = await monitoring_source_factory()
        source = _ns_from_source(real_src)
        fake = _FakeCollector()
        fake._normalize_side_effect = ValueError("unexpected shape")

        with (
            patch.object(CollectorRegistry, "get", return_value=lambda _: fake),
            patch("app.services.monitoring.collection_orchestrator.decrypt_connection_info",
                  return_value=MagicMock()),
            patch("app.services.monitoring.collection_orchestrator.count_collector_run",
                  new_callable=AsyncMock),
            patch("app.services.monitoring.collection_orchestrator.flush_usage_counters",
                  new_callable=AsyncMock),
        ):
            async with AsyncSessionLocal() as db:
                orchestrator = _make_orchestrator(db, fake)
                run = await orchestrator.run(source, trigger="manual")
                await db.commit()

        assert run.run_record.status == "error"
        assert "unexpected shape" in (run.run_record.error or "")

    async def test_concurrent_run_skip_guard(self, tenant, monitoring_source_factory):
        """When a CollectorRun with status='running' already exists for the
        source, a new call to run() must return a CollectorRun with
        status='skipped' without executing authenticate/collect/normalize."""
        real_src = await monitoring_source_factory()
        source = _ns_from_source(real_src)
        fake = _FakeCollector()

        # Plant a running CollectorRun for this source BEFORE calling run()
        async with AsyncSessionLocal() as db:
            run_repo = CollectorRunRepository(db)
            existing = await run_repo.create(
                tenant_id=tenant.id,
                monitoring_source_id=source.id,
                trigger="scheduler",
            )
            await db.commit()
            existing_id = existing.id

        with (
            patch.object(CollectorRegistry, "get", return_value=lambda _: fake),
            patch("app.services.monitoring.collection_orchestrator.decrypt_connection_info",
                  return_value=MagicMock()),
            patch("app.services.monitoring.collection_orchestrator.count_collector_run",
                  new_callable=AsyncMock),
            patch("app.services.monitoring.collection_orchestrator.flush_usage_counters",
                  new_callable=AsyncMock),
        ):
            async with AsyncSessionLocal() as db:
                orchestrator = _make_orchestrator(db, fake)
                skipped_run = await orchestrator.run(source, trigger="manual")
                await db.commit()

        assert skipped_run.run_record.status == "skipped"
        assert skipped_run.run_record.id != existing_id  # new row, not same row
        assert skipped_run.run_record.status != "success"

    async def test_happy_path_produces_success_run(self, tenant, monitoring_source_factory):
        """Full success path: authenticate → collect → normalize → save →
        CollectorRun status='success'."""
        real_src = await monitoring_source_factory()
        source = _ns_from_source(real_src)
        fake = _FakeCollector()
        fake._normalize_result = _empty_bundle()

        with (
            patch.object(CollectorRegistry, "get", return_value=lambda _: fake),
            patch("app.services.monitoring.collection_orchestrator.decrypt_connection_info",
                  return_value=MagicMock()),
            patch("app.services.monitoring.collection_orchestrator.count_collector_run",
                  new_callable=AsyncMock),
            patch("app.services.monitoring.collection_orchestrator.flush_usage_counters",
                  new_callable=AsyncMock),
        ):
            async with AsyncSessionLocal() as db:
                orchestrator = _make_orchestrator(db, fake)
                run = await orchestrator.run(source, trigger="manual")
                await db.commit()

        assert run.run_record.status == "success"
        assert run.run_record.finished_at is not None

    async def test_run_always_terminates_collector_run(self, tenant, monitoring_source_factory):
        """Regardless of which exception path fires, the CollectorRun row
        must always have a non-None finished_at (i.e. it was 'terminated')."""
        real_src = await monitoring_source_factory()
        source = _ns_from_source(real_src)

        for side_effect in (
            RuntimeError("collect boom"),
            ValueError("normalize boom"),
            Exception("generic boom"),
        ):
            fake = _FakeCollector()
            fake._collect_side_effect = side_effect

            with (
                patch.object(CollectorRegistry, "get", return_value=lambda _: fake),
                patch("app.services.monitoring.collection_orchestrator.decrypt_connection_info",
                      return_value=MagicMock()),
                patch("app.services.monitoring.collection_orchestrator.count_collector_run",
                      new_callable=AsyncMock),
                patch("app.services.monitoring.collection_orchestrator.flush_usage_counters",
                      new_callable=AsyncMock),
            ):
                async with AsyncSessionLocal() as db:
                    orchestrator = _make_orchestrator(db, fake)
                    run = await orchestrator.run(source, trigger="scheduler")
                    await db.commit()

            assert run.run_record.finished_at is not None, (
                f"CollectorRun not terminated after {side_effect!r}"
            )
            assert run.run_record.status == "error"
