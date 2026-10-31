"""Regression tests for the CorrelationResult construction contract.

Guards the two construction paths in CorrelationEngine.correlate():
  1. the "uncorrelated" early return (alert with no device_id/site_id), which
     previously used an outdated 4-field signature and raised
     ``TypeError: CorrelationResult.__init__() missing 13 required positional
     arguments``;
  2. the full construction, which must populate every correlation field with
     deterministic, explainable values.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.db import AsyncSessionLocal
from app.models.alert import Alert
from app.models.device import Device
from app.models.site import Site
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.alerting.correlation_engine import CorrelationEngine

pytestmark = pytest.mark.asyncio


class TestCorrelationResultContract:
    async def test_uncorrelated_alert_returns_empty_result(self, tenant):
        """An alert with neither device_id nor site_id is left uncorrelated
        (not force-grouped) and returns an honest empty result."""
        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            alert = Alert(
                tenant_id=tenant.id,
                title="No location alert",
                severity="info",
                status="open",
                metric_type="other",
                source="monitoring_sync",
                triggered_at=now,
                last_occurred_at=now,
                occurrence_count=1,
            )
            db.add(alert)
            await db.flush()
            await db.refresh(alert)

            engine = CorrelationEngine(AlertRepository(db), IncidentRepository(db))
            result = await engine.correlate(alert)
            await db.commit()

            assert result.incident is None
            assert result.is_new_incident is False
            assert result.correlation_id is None
            assert result.alert_ids == []
            assert result.tenant_id == tenant.id
            assert result.primary_resource is None
            assert result.related_resources == []
            assert result.correlation_signals == []
            assert result.confidence == 0.0
            assert result.score == 0
            assert result.first_seen is None
            assert result.last_seen is None
            assert result.status is None
            assert result.reason == "no device_id or site_id - left uncorrelated"
            assert result.is_root_cause is False
            assert result.is_suppressed is False
            assert result.recomputed is False

    async def test_correlated_alert_populates_all_fields(self, tenant):
        """The full construction path computes every correlation field from
        real stored data (device grouping, deterministic score/confidence)."""
        async with AsyncSessionLocal() as db:
            site = Site(tenant_id=tenant.id, name="Site", code="S")
            db.add(site)
            await db.flush()
            device = Device(
                tenant_id=tenant.id, site_id=site.id, name="corr.example.com",
                device_type="server", vendor="Generic", status="up",
            )
            db.add(device)
            await db.flush()

            now = datetime.now(timezone.utc)
            alert = Alert(
                tenant_id=tenant.id,
                device_id=device.id,
                site_id=site.id,
                title="Device unreachable",
                severity="critical",
                status="open",
                metric_type="availability",
                source="monitoring_sync",
                triggered_at=now,
                last_occurred_at=now,
                occurrence_count=1,
            )
            db.add(alert)
            await db.flush()
            await db.refresh(alert)

            engine = CorrelationEngine(AlertRepository(db), IncidentRepository(db))
            result = await engine.correlate(alert)
            await db.commit()

            assert result.incident is not None
            assert result.is_new_incident is True
            assert result.correlation_id == result.incident.id
            assert result.tenant_id == tenant.id
            assert result.alert_ids == [alert.id]
            assert result.primary_resource == {"type": "device", "id": str(device.id)}
            assert result.related_resources == []
            assert any(s.signal_type == "same_device" and s.strength == "strong" for s in result.correlation_signals)
            assert result.score >= 30
            assert 0.0 < result.confidence <= 1.0
            assert result.first_seen is not None
            assert result.last_seen is not None
            assert result.status == "open"
            assert result.reason.startswith("new incident opened via")
            assert result.recomputed is True
