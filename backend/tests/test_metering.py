"""
Tests for the multi-tenant usage metering system.

Covers:
  - UsageCounter model
  - TenantUsageCollector (in-memory batching, flush, query)
  - Pipeline counter injection functions
  - Billing webhook stub
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage import (
    EVENT_TYPE_ALERT_INGESTED,
    EVENT_TYPE_METRIC_INGESTED,
    EVENT_TYPE_COLLECTOR_RUN,
    EVENT_TYPE_API_CALL,
    UsageCounter,
)
from app.services.ingester.pipeline import (
    count_ingested_alert,
    count_ingested_metric,
    count_collector_run,
    count_api_call,
    flush_usage_counters,
)
from app.services.metering.collector import TenantUsageCollector, _current_hour
from app.services.metering.webhook import BillingWebhook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def collector() -> TenantUsageCollector:
    return TenantUsageCollector(batch_size=100)


# ---------------------------------------------------------------------------
# TenantUsageCollector — core logic
# ---------------------------------------------------------------------------


class TestTenantUsageCollector:
    async def test_record_increments_in_memory(
        self, collector: TenantUsageCollector, tenant_id: uuid.UUID, db: AsyncSession
    ):
        """record() increments the in-memory counter without a DB write."""
        await collector.record(tenant_id, EVENT_TYPE_API_CALL, db, increment=5)
        key = (tenant_id, EVENT_TYPE_API_CALL, _current_hour())
        assert key in collector._buckets
        assert collector._buckets[key].count == 5

    async def test_flush_persists_to_db(
        self, collector: TenantUsageCollector, tenant_id: uuid.UUID, db: AsyncSession
    ):
        """flush() writes pending counters to the database."""
        await collector.record(tenant_id, EVENT_TYPE_ALERT_INGESTED, db, increment=3)
        await collector.flush(db)
        result = await db.execute(
            select(UsageCounter).where(UsageCounter.tenant_id == tenant_id)
        )
        rows = result.scalars().all()
        assert len(rows) >= 1
        assert rows[0].count == 3

    async def test_flush_is_idempotent(
        self, collector: TenantUsageCollector, tenant_id: uuid.UUID, db: AsyncSession
    ):
        """Flushing twice should not double-count."""
        await collector.record(tenant_id, EVENT_TYPE_METRIC_INGESTED, db, increment=2)
        await collector.flush(db)
        await collector.flush(db)  # second flush with no pending keys
        result = await db.execute(
            select(UsageCounter).where(UsageCounter.tenant_id == tenant_id)
        )
        rows = result.scalars().all()
        total = sum(r.count for r in rows if r.event_type == EVENT_TYPE_METRIC_INGESTED)
        assert total == 2

    async def test_get_aggregated_returns_correct_totals(
        self, collector: TenantUsageCollector, tenant_id: uuid.UUID, db: AsyncSession
    ):
        """get_aggregated() returns flat {event_type: total} dict."""
        await collector.record(tenant_id, EVENT_TYPE_API_CALL, db, increment=10)
        await collector.record(tenant_id, EVENT_TYPE_ALERT_INGESTED, db, increment=5)
        await collector.flush(db)
        agg = await collector.get_aggregated(tenant_id, db)
        assert agg.get(EVENT_TYPE_API_CALL) == 10
        assert agg.get(EVENT_TYPE_ALERT_INGESTED) == 5

    async def test_get_usage_filters_by_event_type(
        self, collector: TenantUsageCollector, tenant_id: uuid.UUID, db: AsyncSession
    ):
        """get_usage() filters by event_type when provided."""
        await collector.record(tenant_id, EVENT_TYPE_API_CALL, db, increment=1)
        await collector.record(tenant_id, EVENT_TYPE_COLLECTOR_RUN, db, increment=2)
        await collector.flush(db)
        rows = await collector.get_usage(
            tenant_id, db, event_type=EVENT_TYPE_API_CALL
        )
        assert all(r.event_type == EVENT_TYPE_API_CALL for r in rows)

    async def test_get_usage_time_range(
        self, collector: TenantUsageCollector, tenant_id: uuid.UUID, db: AsyncSession
    ):
        """get_usage() respects the since/until filters."""
        await collector.record(tenant_id, EVENT_TYPE_API_CALL, db, increment=1)
        await collector.flush(db)
        now = datetime.now(timezone.utc)
        rows = await collector.get_usage(
            tenant_id, db, since=now - timedelta(hours=2), until=now + timedelta(hours=2)
        )
        assert len(rows) >= 1

        # Query outside the range should return nothing
        rows_outside = await collector.get_usage(
            tenant_id, db, since=now + timedelta(days=1), until=now + timedelta(days=2)
        )
        assert len(rows_outside) == 0


# ---------------------------------------------------------------------------
# Pipeline counter injection
# ---------------------------------------------------------------------------


class TestPipelineCounters:
    async def test_count_ingested_alert(
        self, tenant_id: uuid.UUID, db: AsyncSession
    ):
        await count_ingested_alert(tenant_id, db)
        await flush_usage_counters(db)
        result = await db.execute(
            select(UsageCounter).where(
                UsageCounter.tenant_id == tenant_id,
                UsageCounter.event_type == EVENT_TYPE_ALERT_INGESTED,
            )
        )
        assert result.scalar_one().count >= 1

    async def test_count_ingested_metric(
        self, tenant_id: uuid.UUID, db: AsyncSession
    ):
        await count_ingested_metric(tenant_id, db)
        await flush_usage_counters(db)
        result = await db.execute(
            select(UsageCounter).where(
                UsageCounter.tenant_id == tenant_id,
                UsageCounter.event_type == EVENT_TYPE_METRIC_INGESTED,
            )
        )
        assert result.scalar_one().count >= 1

    async def test_count_collector_run(
        self, tenant_id: uuid.UUID, db: AsyncSession
    ):
        await count_collector_run(tenant_id, db)
        await flush_usage_counters(db)
        result = await db.execute(
            select(UsageCounter).where(
                UsageCounter.tenant_id == tenant_id,
                UsageCounter.event_type == EVENT_TYPE_COLLECTOR_RUN,
            )
        )
        assert result.scalar_one().count >= 1

    async def test_count_api_call(self, tenant_id: uuid.UUID, db: AsyncSession):
        await count_api_call(tenant_id, db)
        await flush_usage_counters(db)
        result = await db.execute(
            select(UsageCounter).where(
                UsageCounter.tenant_id == tenant_id,
                UsageCounter.event_type == EVENT_TYPE_API_CALL,
            )
        )
        assert result.scalar_one().count >= 1


# ---------------------------------------------------------------------------
# Billing webhook
# ---------------------------------------------------------------------------


class TestBillingWebhook:
    async def test_emit_skips_when_no_url(self):
        """No webhook URL configured => skips gracefully."""
        webhook = BillingWebhook(webhook_url="")
        # Should not raise
        await webhook.emit_if_threshold_exceeded(
            tenant_id=uuid.uuid4(),
            usage_summary={"api.call": 100_001},  # exceeds default 100k threshold
            db=None,  # type: ignore[arg-type]
        )

    async def test_emit_skips_when_below_threshold(self):
        """Below-threshold usage does not trigger a webhook."""
        webhook = BillingWebhook(webhook_url="https://billing.example.com/hook")
        # Should not raise
        await webhook.emit_if_threshold_exceeded(
            tenant_id=uuid.uuid4(),
            usage_summary={"api.call": 50},  # well below 100k threshold
            db=None,  # type: ignore[arg-type]
        )

    async def test_signature_is_deterministic(self):
        """HMAC-SHA256 signature should be deterministic for the same payload."""
        webhook = BillingWebhook(
            webhook_url="https://billing.example.com/hook",
            signing_secret="test-secret",
        )
        payload = {"event_id": "abc", "count": 100}
        sig1 = webhook._sign(payload)
        sig2 = webhook._sign(payload)
        assert sig1 == sig2
        assert len(sig1) == 64  # SHA256 hex digest

    async def test_custom_thresholds(self):
        """Custom thresholds are respected over defaults."""
        webhook = BillingWebhook(
            webhook_url="https://billing.example.com/hook",
            thresholds={"custom.event": 10},
        )
        # Should not raise (below threshold)
        await webhook.emit_if_threshold_exceeded(
            tenant_id=uuid.uuid4(),
            usage_summary={"custom.event": 5},
            db=None,  # type: ignore[arg-type]
        )