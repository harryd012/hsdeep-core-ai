"""Canonical device-state classification parity.

Feeds a fixed device fixture through ``DeviceRepository.counts_by_state`` and
asserts the up/down/stale/unknown/total classification matches the frontend
selector ``countDeviceStates()`` / ``classifyDevice()``
(frontend/src/lib/dashboard/metrics.ts) exactly. The SAME 18 rows are mirrored in
frontend/tests/metrics.test.mjs so the two implementations cannot drift.

The 24h freshness window is the same value as ``DEVICE_FRESHNESS_THRESHOLD_MS``
in metrics.ts, in seconds (``DEVICE_FRESHNESS_WINDOW_SECONDS`` in the service).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.device import Device
from app.models.tenant import Tenant
from app.repositories.device_repository import DeviceRepository

FRESHNESS_SECONDS = 24 * 60 * 60


@pytest.mark.asyncio
async def test_counts_by_state_classification(tenant, db):
    now = datetime.now(timezone.utc)
    recent = now - timedelta(seconds=60)
    old = now - timedelta(seconds=FRESHNESS_SECONDS + 60)
    # Exactly at the 24h boundary: NOT stale (the threshold is strict >).
    boundary = now - timedelta(seconds=FRESHNESS_SECONDS)

    other = Tenant(name="Parity Other Tenant", slug=f"parity-{uuid.uuid4().hex[:8]}")
    db.add(other)
    await db.flush()

    def dev(name: str, status: str, last_seen: datetime | None, t: Tenant = tenant, deleted: bool = False) -> Device:
        return Device(
            tenant_id=t.id,
            name=name,
            device_type="test",
            status=status,
            last_seen_at=last_seen,
            is_deleted=deleted,
        )

    db.add_all(
        [
            # up (freshness within window + up-ish status), incl. case/whitespace + boundary
            dev("up", "up", recent),
            dev("up-online", "online", recent),
            dev("up-cased", "UP", recent),
            dev("up-spaced", "  up  ", recent),
            dev("up-boundary", "up", boundary),
            # down (recent evidence + down-ish status)
            dev("down", "down", recent),
            dev("down-offline", "offline", recent),
            dev("down-critical", "critical", recent),
            dev("down-error", "error", recent),
            dev("down-unreachable", "unreachable", recent),
            dev("down-degraded", "degraded", recent),
            dev("down-warning", "warning", recent),
            # stale (freshness wins over status)
            dev("stale-up", "up", old),
            dev("stale-down", "down", old),
            # unknown (no usable evidence / unrecognised status)
            dev("unknown-null", "up", None),
            dev("unknown-status", "mystery", recent),
            dev("unknown-default", "unknown", recent),
            dev("unknown-empty", "", recent),
            # filtered out (soft-deleted + other tenant)
            dev("deleted", "up", recent, deleted=True),
            dev("other-tenant", "up", recent, t=other),
        ]
    )
    await db.flush()

    repo = DeviceRepository(db)
    counts = await repo.counts_by_state(
        tenant.id, now=now, freshness_seconds=FRESHNESS_SECONDS
    )

    assert counts == {
        "up": 5,       # up, up-online, up-cased, up-spaced, up-boundary
        "down": 7,     # down, offline, critical, error, unreachable, degraded, warning
        "stale": 2,    # stale-up, stale-down
        "unknown": 4,  # unknown-null, unknown-status, unknown-default, unknown-empty
        "total": 18,
    }
