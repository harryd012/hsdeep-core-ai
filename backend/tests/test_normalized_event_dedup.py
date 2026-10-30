"""Normalized-event dedup / atomic upsert regression tests.

Covers the Sprint-1 correctness contract for
``NormalizedEventRepository.upsert_occurrence`` (PostgreSQL
``INSERT ... ON CONFLICT DO UPDATE`` against the partial unique index
``uq_normalized_events_active_per_signature``) and the service's ``ingest``
dispatch:

  1. Replay of the same signature -> one active event, occurrence_count bumped.
  2. Concurrent ingestion -> no uncaught uniqueness race, count preserved.
  3. Re-fire after suppression -> a NEW active event is created.
  4. Re-fire after soft-delete -> a NEW active event is created.
  5. Tenant B can neither read nor affect Tenant A's event.
  6. Missing/unknown signature -> legacy bypass (always a new row).

Runs against the real Postgres DB per project convention (conftest.py); the
partial-unique-index + ON CONFLICT behavior under test is Postgres-specific.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app.core.db import AsyncSessionLocal
from app.models.integration import Integration
from app.models.tenant import Tenant
from app.repositories.normalized_event_repository import NormalizedEventRepository
from app.services.normalized_event_service import NormalizedEventService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_integration(db, tenant_id: uuid.UUID, name: str) -> Integration:
    integ = Integration(
        tenant_id=tenant_id,
        name=name,
        provider_type="monitoring",
        provider="prtg",
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _make_tenant(db) -> Tenant:
    t = Tenant(name="Dedup Other", slug=f"dedup-other-{uuid.uuid4().hex[:8]}")
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


def _svc(db) -> NormalizedEventService:
    return NormalizedEventService(NormalizedEventRepository(db))


def _ingest_kwargs(
    integration_id: uuid.UUID | None,
    signature: str | None,
    *,
    title: str = "CPU high",
    ts: datetime | None = None,
) -> dict:
    return dict(
        title=title,
        severity="warning",
        event_category="performance",
        event_type="threshold",
        telemetry_type="metric",
        event_timestamp=ts or datetime.now(timezone.utc),
        integration_id=integration_id,
        event_signature=signature,
    )


async def _ingest_in_new_session(tenant_id: uuid.UUID, **kwargs) -> tuple:
    """Upsert through its OWN committed session (for the concurrency test)."""
    async with AsyncSessionLocal() as db:
        event, created = await _svc(db).ingest(tenant_id, **kwargs)
        await db.commit()
        return event, created


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNormalizedEventDedup:
    async def test_replay_same_signature_increments_occurrence(
        self, tenant: Tenant, db
    ):
        """A repeat of the same active signature is deduplicated: one active
        row, occurrence_count incremented, identity preserved."""
        integ = await _make_integration(db, tenant.id, f"dedup-{uuid.uuid4().hex[:6]}")
        sig = f"sig:{uuid.uuid4().hex[:6]}"
        svc = _svc(db)

        ev1, created1 = await svc.ingest(
            tenant.id, **_ingest_kwargs(integ.id, sig, title="first")
        )
        await db.commit()
        assert created1 is True
        assert ev1.occurrence_count == 1

        ev2, created2 = await svc.ingest(
            tenant.id, **_ingest_kwargs(integ.id, sig, title="replay")
        )
        await db.commit()
        assert created2 is False, "replay must NOT create a second active row"
        assert ev2.id == ev1.id, "replay must bump the SAME active row"
        assert ev2.occurrence_count == 2

        # Exactly one active row exists for the signature.
        repo = NormalizedEventRepository(db)
        active = await repo.find_active_by_signature(tenant.id, integ.id, sig)
        assert active is not None
        assert active.id == ev1.id
        assert active.occurrence_count == 2

    async def test_concurrent_ingest_no_race_single_active_row(
        self, tenant: Tenant, db
    ):
        """Two simultaneous writers for a brand-new signature must not raise an
        uncaught UniqueViolation: exactly one INSERT wins, the other takes the
        DO UPDATE branch, and the count is preserved."""
        integ = await _make_integration(db, tenant.id, f"conc-{uuid.uuid4().hex[:6]}")
        sig = f"conc:{uuid.uuid4().hex[:6]}"

        results = await asyncio.gather(
            _ingest_in_new_session(tenant.id, **_ingest_kwargs(integ.id, sig)),
            _ingest_in_new_session(tenant.id, **_ingest_kwargs(integ.id, sig)),
        )
        # Exactly one insert (created=True) and one update (created=False).
        flags = sorted(created for _, created in results)
        assert flags == [False, True]

        repo = NormalizedEventRepository(db)
        active = await repo.find_active_by_signature(tenant.id, integ.id, sig)
        assert active is not None
        assert active.occurrence_count == 2

    async def test_refire_after_suppression_creates_new_active(
        self, tenant: Tenant, db
    ):
        """Suppressing an event frees the partial-unique key: a re-fire creates
        a fresh active row instead of violating uniqueness."""
        integ = await _make_integration(db, tenant.id, f"supp-{uuid.uuid4().hex[:6]}")
        sig = f"supp:{uuid.uuid4().hex[:6]}"
        svc = _svc(db)

        ev1, _ = await svc.ingest(tenant.id, **_ingest_kwargs(integ.id, sig))
        await db.commit()
        assert ev1.occurrence_count == 1

        await svc.suppress(ev1.id, tenant.id)
        await db.commit()
        assert ev1.is_suppressed is True

        ev2, created2 = await svc.ingest(
            tenant.id, **_ingest_kwargs(integ.id, sig, title="refire")
        )
        await db.commit()
        assert created2 is True, "re-fire after suppression must be a new active row"
        assert ev2.id != ev1.id
        assert ev2.is_suppressed is False
        assert ev2.occurrence_count == 1

    async def test_refire_after_soft_delete_creates_new_active(
        self, tenant: Tenant, db
    ):
        """Soft-delete (is_deleted=true) also frees the key for re-fire."""
        integ = await _make_integration(db, tenant.id, f"sdel-{uuid.uuid4().hex[:6]}")
        sig = f"sdel:{uuid.uuid4().hex[:6]}"
        svc = _svc(db)

        ev1, _ = await svc.ingest(tenant.id, **_ingest_kwargs(integ.id, sig))
        await db.commit()

        # Soft-delete directly (no service delete API exists today).
        ev1.is_deleted = True
        ev1.deleted_at = datetime.now(timezone.utc)
        await db.commit()

        ev2, created2 = await svc.ingest(
            tenant.id, **_ingest_kwargs(integ.id, sig, title="after-delete")
        )
        await db.commit()
        assert created2 is True
        assert ev2.id != ev1.id
        assert ev2.is_deleted is False

    async def test_tenant_isolation(self, tenant: Tenant, db):
        """Tenant B can neither read nor affect Tenant A's active event, and
        B's own upsert for a shared signature text is a distinct row keyed by
        B's own integration (dedup key is integration-scoped, tenant-safe)."""
        sig = f"shared:{uuid.uuid4().hex[:6]}"
        integ_a = await _make_integration(db, tenant.id, f"a-{uuid.uuid4().hex[:6]}")
        other = await _make_tenant(db)
        integ_b = await _make_integration(db, other.id, f"b-{uuid.uuid4().hex[:6]}")
        svc = _svc(db)

        ev_a, _ = await svc.ingest(tenant.id, **_ingest_kwargs(integ_a.id, sig))
        await db.commit()

        repo = NormalizedEventRepository(db)

        # Tenant B cannot see Tenant A's event even with A's integration id:
        # the tenant_id predicate is applied at SQL level.
        leaked = await repo.find_active_by_signature(other.id, integ_a.id, sig)
        assert leaked is None
        # Nor by primary key.
        assert await repo.get(ev_a.id, other.id) is None

        # Tenant B's upsert uses B's own integration -> distinct row, and does
        # NOT bump Tenant A's occurrence_count.
        ev_b, created_b = await svc.ingest(
            other.id, **_ingest_kwargs(integ_b.id, sig, title="tenant-b")
        )
        await db.commit()
        assert created_b is True
        assert ev_b.id != ev_a.id

        still_a = await repo.find_active_by_signature(tenant.id, integ_a.id, sig)
        assert still_a is not None
        assert still_a.id == ev_a.id
        assert still_a.occurrence_count == 1, "Tenant B must not affect Tenant A"

    async def test_missing_signature_bypass_creates_new_each_time(
        self, tenant: Tenant, db
    ):
        """No dedup key -> legacy behavior preserved: every ingest is a new row."""
        integ = await _make_integration(db, tenant.id, f"nosig-{uuid.uuid4().hex[:6]}")
        svc = _svc(db)

        ev1, c1 = await svc.ingest(tenant.id, **_ingest_kwargs(integ.id, None))
        ev2, c2 = await svc.ingest(tenant.id, **_ingest_kwargs(integ.id, None))
        await db.commit()
        assert c1 is True and c2 is True
        assert ev1.id != ev2.id

    async def test_missing_integration_bypass_creates_new_each_time(
        self, tenant: Tenant, db
    ):
        """Signature present but integration_id absent -> still bypasses dedup
        (the ON CONFLICT key requires BOTH integration_id and signature)."""
        sig = f"noint:{uuid.uuid4().hex[:6]}"
        svc = _svc(db)

        ev1, c1 = await svc.ingest(
            tenant.id, **_ingest_kwargs(None, sig, title="no-integration-1")
        )
        ev2, c2 = await svc.ingest(
            tenant.id, **_ingest_kwargs(None, sig, title="no-integration-2")
        )
        await db.commit()
        assert c1 is True and c2 is True
        assert ev1.id != ev2.id
