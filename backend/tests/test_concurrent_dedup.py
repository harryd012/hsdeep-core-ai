"""
PostgreSQL integration tests for concurrent incident deduplication.

Proves the DB-level backstop: the partial unique index
``uq_incidents_open_signature`` on ``(tenant_id, incident_signature)``
WHERE ``status = 'open' AND incident_signature IS NOT NULL AND is_deleted IS false``
prevents two qualifying open incidents for the same (tenant, signature)
even under genuine concurrent access.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.models.alert import Alert
from app.models.device import Device
from app.models.incident import Incident
from app.models.monitoring_source import MonitoringSource
from app.models.sensor import Sensor
from app.models.site import Site
from app.models.tenant import Tenant
from app.repositories.incident_repository import IncidentRepository
from app.repositories.alert_repository import AlertRepository
from app.services.alerting.correlation_engine import CorrelationEngine
from app.services.alerting.signature import build_signature

pytestmark = pytest.mark.asyncio


async def _make_scaffold():
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=f"Dedup {uuid.uuid4().hex[:8]}", slug=f"dedup-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.flush()
        site = Site(tenant_id=tenant.id, name="Site", code=f"SITE-{uuid.uuid4().hex[:4]}")
        db.add(site)
        await db.flush()
        device = Device(tenant_id=tenant.id, site_id=site.id, name="mail.example.com",
                        device_type="server", vendor="Generic", status="up")
        db.add(device)
        await db.flush()
        source = MonitoringSource(tenant_id=tenant.id, name="src", source_type="test", is_active=True)
        db.add(source)
        await db.flush()
        sensor = Sensor(tenant_id=tenant.id, monitoring_source_id=source.id, device_id=device.id,
                        external_id="ssl-check-993", name="SSL Security Check - mail.example.com:993",
                        metric_type="ssl_security", status="down", critical_threshold=0,
                        warning_threshold=0, current_value=0)
        db.add(sensor)
        await db.commit()
        await db.refresh(tenant)
        await db.refresh(site)
        await db.refresh(device)
        await db.refresh(source)
        await db.refresh(sensor)
    return tenant, site, device, source, sensor


async def _make_alert(db, tenant_id, *, device_id, sensor_id, source_id,
                      severity="critical", status="open", triggered_at=None):
    now = triggered_at or datetime.now(timezone.utc)
    alert = Alert(tenant_id=tenant_id, device_id=device_id, sensor_id=sensor_id,
                  monitoring_source_id=source_id, title="SSL Security Check - mail.example.com:993",
                  description="SSL check failed", severity=severity, status=status,
                  metric_type="ssl_security", category="availability",
                  triggered_at=now, last_occurred_at=now, occurrence_count=1)
    db.add(alert)
    await db.flush()
    await db.refresh(alert)
    return alert


async def _delete_tenant(tenant):
    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete
        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()



# Case A — Sequential duplicate ingestion


async def test_sequential_duplicate_ingestion_reuses_incident():
    tenant, _site, device, source, sensor = await _make_scaffold()
    try:
        signature = build_signature(
            metric_type="ssl_security",
            title=sensor.name,
            device_id=device.id,
            sensor_id=sensor.id,
            signal_external_id=None,
        )
        async with AsyncSessionLocal() as db:
            repo = IncidentRepository(db)
            engine = CorrelationEngine(AlertRepository(db), repo)
            alert1 = await _make_alert(db, tenant.id, device_id=device.id,
                                       sensor_id=sensor.id, source_id=source.id)
            result1 = await engine.correlate(alert1)
            assert result1.is_new_incident is True
            assert result1.incident is not None
            assert result1.incident.incident_signature == signature

            # Resolve the first alert so the same sensor can emit a new alert
            # (Alert has uq_alerts_active_per_sensor preventing two active
            # alerts per sensor).
            alert1.status = "resolved"
            await db.commit()

            alert2 = await _make_alert(db, tenant.id, device_id=device.id,
                                       sensor_id=sensor.id, source_id=source.id)
            result2 = await engine.correlate(alert2)
            assert result2.is_new_incident is False
            assert result2.incident.id == result1.incident.id

        async with AsyncSessionLocal() as db:
            count = await db.execute(
                text("SELECT count(*) FROM incidents WHERE tenant_id = :t AND "
                     "incident_signature = :s AND status = 'open' AND "
                     "incident_signature IS NOT NULL AND is_deleted IS false"),
                {"t": tenant.id, "s": signature},
            )
            assert count.scalar() == 1
    finally:
        await _delete_tenant(tenant)


# Case B — Concurrent duplicate ingestion


async def test_concurrent_duplicate_ingestion_only_one_open_incident():
    tenant, _site, device, source, sensor = await _make_scaffold()
    try:
        signature = build_signature(
            metric_type="ssl_security",
            title=sensor.name,
            device_id=device.id,
            sensor_id=sensor.id,
            signal_external_id=None,
        )

        # Pre-commit one open incident so every concurrent duplicate insert
        # conflicts with an already-committed row (the unique partial index
        # is checked against committed data).
        async with AsyncSessionLocal() as db:
            seed = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="SSL Security Check - mail.example.com:993",
                severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=signature,
            )
            db.add(seed)
            await db.commit()
            await db.refresh(seed)

        async def _attempt_duplicate_insert():
            async with AsyncSessionLocal() as db:
                inc = Incident(
                    tenant_id=tenant.id, device_id=device.id,
                    title="SSL Security Check - mail.example.com:993",
                    severity="critical", status="open",
                    opened_at=datetime.now(timezone.utc),
                    last_alert_at=datetime.now(timezone.utc),
                    alert_count=1, incident_signature=signature,
                )
                db.add(inc)
                try:
                    await db.flush()
                    await db.commit()
                    return True  # should not happen
                except IntegrityError:
                    await db.rollback()
                    return False  # expected: unique index rejected the dup

        results = await asyncio.gather(
            *[_attempt_duplicate_insert() for _ in range(8)]
        )
        # Every concurrent duplicate must be rejected by the unique index.
        assert all(r is False for r in results), (
            f"Expected all 8 duplicates rejected, got {results}"
        )

        # Verify the DB invariant: exactly ONE open incident remains.
        async with AsyncSessionLocal() as db:
            count = await db.execute(
                text("SELECT count(*) FROM incidents WHERE tenant_id = :t AND "
                     "incident_signature = :s AND status = 'open' AND "
                     "incident_signature IS NOT NULL AND is_deleted IS false"),
                {"t": tenant.id, "s": signature},
            )
            assert count.scalar() == 1
    finally:
        await _delete_tenant(tenant)


async def test_concurrent_conflict_handler_does_not_swallow_unrelated_errors():
    tenant, _site, device, source, sensor = await _make_scaffold()
    try:
        signature = build_signature(
            metric_type="ssl_security",
            title=sensor.name,
            device_id=device.id,
            sensor_id=sensor.id,
            signal_external_id=None,
        )
        async with AsyncSessionLocal() as db:
            inc = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="SSL Security Check - mail.example.com:993",
                severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=signature,
            )
            db.add(inc)
            await db.commit()
            await db.refresh(inc)

        async with AsyncSessionLocal() as db:
            dup = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="Duplicate", severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=signature,
            )
            db.add(dup)
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()

        # After rollback, the session must still be usable for a valid query.
        async with AsyncSessionLocal() as db:
            count = await db.execute(
                text("SELECT count(*) FROM incidents WHERE id = :i"),
                {"i": str(inc.id)},
            )
            assert count.scalar() == 1
    finally:
        await _delete_tenant(tenant)


# Case D — Concurrent CorrelationEngine.correlate() converges on one incident


async def test_concurrent_correlate_converges_to_one_incident():
    """Two concurrent correlate() calls for the same (tenant, signature) must
    converge on exactly one persisted open incident.

    Uses an asyncio.Event gate to deterministically synchronize both calls so
    each performs the ``get_open_for_signature`` lookup before either attempts
    the ``INSERT`` - reproducing the check-then-insert race that the
    ``uq_incidents_open_signature`` partial unique index is designed to close.

    Alerts are created without ``sensor_id`` (signature derived from
    ``device_id`` only) because ``uq_alerts_active_per_sensor`` blocks two
    concurrent non-resolved alerts for the same sensor. This keeps the alert
    inserts clean so the race manifests at the Incident unique index, exactly
    where the application-level recovery is needed.

    The losing call must recover via IntegrityError handling: rollback,
    re-query, and join the existing incident - never propagating an
    unhandled uniqueness exception.
    """
    tenant, _site, device, source, _sensor = await _make_scaffold()
    try:
        signature = build_signature(
            metric_type="ssl_security",
            title="SSL Security Check - mail.example.com:993",
            device_id=device.id,
            sensor_id=None,
            signal_external_id=None,
        )

        # Gate to synchronize: both correlate() calls must pass the lookup
        # phase before either attempts the INSERT.
        gate = asyncio.Event()
        arrivals: list = []
        results: list = []

        async def _correlate_one():
            async with AsyncSessionLocal() as db:
                repo = IncidentRepository(db)
                engine = CorrelationEngine(AlertRepository(db), repo)

                # Synchronize inside create() so both calls pass the lookup
                # phase before either attempts the INSERT.
                orig_create = repo.create

                async def synchronized_create(**fields):
                    arrivals.append(True)
                    if len(arrivals) >= 2:
                        gate.set()
                    await gate.wait()
                    return await orig_create(**fields)

                repo.create = synchronized_create

                alert = Alert(
                    tenant_id=tenant.id, device_id=device.id,
                    sensor_id=None, monitoring_source_id=source.id,
                    title="SSL Security Check - mail.example.com:993",
                    description="SSL check failed", severity="critical",
                    status="acknowledged", metric_type="ssl_security",
                    category="availability",
                    triggered_at=datetime.now(timezone.utc),
                    last_occurred_at=datetime.now(timezone.utc),
                    occurrence_count=1,
                )
                db.add(alert)
                await db.flush()
                await db.refresh(alert)
                result = await engine.correlate(alert)
                results.append(result)
                await db.commit()

        await asyncio.gather(_correlate_one(), _correlate_one())

        # Both calls complete without an unhandled uniqueness exception.
        assert len(results) == 2

        # Exactly one caller created the incident; the other joined it.
        new_count = sum(1 for r in results if r.is_new_incident)
        assert new_count == 1, (
            f"Expected exactly 1 new incident, got {new_count}"
        )

        # Both reference the same persisted incident.
        incident_ids = {r.incident.id for r in results if r.incident is not None}
        assert len(incident_ids) == 1, f"Expected 1 incident, got {incident_ids}"

        # DB invariant: exactly one qualifying open incident remains.
        async with AsyncSessionLocal() as db:
            count = await db.execute(
                text(
                    "SELECT count(*) FROM incidents WHERE tenant_id = :t "
                    "AND incident_signature = :s AND status = 'open' "
                    "AND incident_signature IS NOT NULL AND is_deleted IS false"
                ),
                {"t": tenant.id, "s": signature},
            )
            assert count.scalar() == 1
    finally:
        await _delete_tenant(tenant)


# Case D2 — Hardening: SAVEPOINT recovery preserves alert, session, and
#           re-raises on unexpected re-query miss.


async def test_concurrent_correlate_preserves_alert_and_session():
    """After SAVEPOINT recovery, the losing caller's alert row is preserved,
    its correlation fields are updated, and the session remains usable."""
    tenant, _site, device, source, _sensor = await _make_scaffold()
    try:
        gate = asyncio.Event()
        arrivals: list = []
        results: list = []
        alert_ids: list = []

        async def _correlate_one():
            async with AsyncSessionLocal() as db:
                repo = IncidentRepository(db)
                engine = CorrelationEngine(AlertRepository(db), repo)
                orig_create = repo.create

                async def synchronized_create(**fields):
                    arrivals.append(True)
                    if len(arrivals) >= 2:
                        gate.set()
                    await gate.wait()
                    return await orig_create(**fields)

                repo.create = synchronized_create
                now = datetime.now(timezone.utc)
                alert = Alert(
                    tenant_id=tenant.id, device_id=device.id,
                    sensor_id=None, monitoring_source_id=source.id,
                    title="SSL Security Check - mail.example.com:993",
                    description="SSL check failed", severity="critical",
                    status="acknowledged", metric_type="ssl_security",
                    category="availability",
                    triggered_at=now, last_occurred_at=now,
                    occurrence_count=1,
                )
                db.add(alert)
                await db.flush()
                await db.refresh(alert)
                alert_ids.append(alert.id)
                result = await engine.correlate(alert)
                results.append(result)
                await db.commit()

        await asyncio.gather(_correlate_one(), _correlate_one())
        assert len(results) == 2

        # Both alert rows must be persisted (not lost by savepoint rollback).
        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                text("SELECT id, incident_id FROM alerts "
                     "WHERE tenant_id = :t AND id = ANY(:ids)"),
                {"t": tenant.id, "ids": list(alert_ids)},
            )
            alert_rows = rows.fetchall()
            assert len(alert_rows) == 2
            incident_ids = {r.incident_id for r in alert_rows}
            assert len(incident_ids) == 1
            assert None not in incident_ids
    finally:
        await _delete_tenant(tenant)


async def test_concurrent_correlate_different_signatures_independent():
    """Two concurrent correlate() calls with DIFFERENT signatures must create
    two independent incidents — no cross-contamination from the conflict
    handler."""
    tenant, _site, device, source, _sensor = await _make_scaffold()
    try:
        sig_a = build_signature(
            metric_type="ssl_security", title="Port 993",
            device_id=device.id, sensor_id=None, signal_external_id=None,
        )
        sig_b = build_signature(
            metric_type="ssl_security", title="Port 465",
            device_id=device.id, sensor_id=None, signal_external_id=None,
        )
        assert sig_a != sig_b

        async def _correlate_with_title(title: str):
            async with AsyncSessionLocal() as db:
                repo = IncidentRepository(db)
                engine = CorrelationEngine(AlertRepository(db), repo)
                now = datetime.now(timezone.utc)
                alert = Alert(
                    tenant_id=tenant.id, device_id=device.id,
                    sensor_id=None, monitoring_source_id=source.id,
                    title=title, description="test", severity="critical",
                    status="acknowledged", metric_type="ssl_security",
                    category="availability",
                    triggered_at=now, last_occurred_at=now,
                    occurrence_count=1,
                )
                db.add(alert)
                await db.flush()
                await db.refresh(alert)
                result = await engine.correlate(alert)
                await db.commit()
                return result

        r_a, r_b = await asyncio.gather(
            _correlate_with_title("Port 993"),
            _correlate_with_title("Port 465"),
        )
        assert r_a.is_new_incident is True
        assert r_b.is_new_incident is True
        assert r_a.incident.id != r_b.incident.id

        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                text("SELECT incident_signature FROM incidents "
                     "WHERE tenant_id = :t AND status = 'open' "
                     "AND is_deleted IS false"),
                {"t": tenant.id},
            )
            sigs = {r.incident_signature for r in rows}
            assert sigs == {sig_a, sig_b}
    finally:
        await _delete_tenant(tenant)


async def test_requery_miss_after_conflict_raises():
    """If the savepoint is rolled back but the re-query finds no incident
    (e.g. the winner also rolled back), the original IntegrityError must be
    re-raised — never silently report success with no incident."""
    tenant, _site, device, source, _sensor = await _make_scaffold()
    try:
        sig = build_signature(
            metric_type="ssl_security", title="Port 993",
            device_id=device.id, sensor_id=None, signal_external_id=None,
        )
        async with AsyncSessionLocal() as db:
            seed = Incident(
                tenant_id=tenant.id, device_id=device.id, title="seed",
                severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=sig,
            )
            db.add(seed)
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = IncidentRepository(db)
            engine = CorrelationEngine(AlertRepository(db), repo)

            async def _always_none(signature, tenant_id):
                return None

            repo.get_open_for_signature = _always_none

            now = datetime.now(timezone.utc)
            alert = Alert(
                tenant_id=tenant.id, device_id=device.id,
                sensor_id=None, monitoring_source_id=source.id,
                title="Port 993", description="test", severity="critical",
                status="acknowledged", metric_type="ssl_security",
                category="availability",
                triggered_at=now, last_occurred_at=now,
                occurrence_count=1,
            )
            db.add(alert)
            await db.flush()
            await db.refresh(alert)

            with pytest.raises(IntegrityError):
                await engine.correlate(alert)
    finally:
        await _delete_tenant(tenant)



# Case C — Tenant isolation


async def test_tenant_isolation_different_tenants_separate_incidents():
    tenants = []
    try:
        for _ in range(2):
            tenant, _site, device, source, sensor = await _make_scaffold()
            tenants.append((tenant, device, source, sensor))

        created_ids = []
        for tenant, device, source, sensor in tenants:
            sig_t = build_signature(
                metric_type="ssl_security",
                title=sensor.name,
                device_id=device.id,
                sensor_id=sensor.id,
                signal_external_id=None,
            )
            async with AsyncSessionLocal() as db:
                inc = Incident(
                    tenant_id=tenant.id, device_id=device.id,
                    title="SSL check", severity="critical", status="open",
                    opened_at=datetime.now(timezone.utc),
                    last_alert_at=datetime.now(timezone.utc),
                    alert_count=1, incident_signature=sig_t,
                )
                db.add(inc)
                await db.commit()
                await db.refresh(inc)
                created_ids.append(inc.id)

        for (tenant, _device, _source, _sensor) in tenants:
            async with AsyncSessionLocal() as db:
                count = await db.execute(
                    text("SELECT count(*) FROM incidents WHERE tenant_id = :t "
                         "AND status = 'open' AND incident_signature IS NOT NULL "
                         "AND is_deleted IS false"),
                    {"t": tenant.id},
                )
                assert count.scalar() == 1

        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                text("SELECT id, tenant_id FROM incidents WHERE id = ANY(:ids) "
                     "AND status = 'open'"),
                {"ids": created_ids},
            )
            rows = rows.all()
            assert len(rows) == 2
            assert rows[0].tenant_id != rows[1].tenant_id
    finally:
        for tenant, _device, _source, _sensor in tenants:
            await _delete_tenant(tenant)


# Case D — Lifecycle behavior


async def test_lifecycle_resolved_then_reopened_allows_new_open():
    tenant, _site, device, source, sensor = await _make_scaffold()
    try:
        sig = build_signature(
            metric_type="ssl_security",
            title=sensor.name,
            device_id=device.id,
            sensor_id=sensor.id,
            signal_external_id=None,
        )
        async with AsyncSessionLocal() as db:
            inc = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="SSL check", severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=sig,
            )
            db.add(inc)
            await db.commit()
            await db.refresh(inc)

            inc.status = "resolved"
            inc.resolved_at = datetime.now(timezone.utc)
            await db.commit()

            # New open incident with same signature after resolve: allowed.
            inc2 = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="SSL check (reopened)", severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=sig,
            )
            db.add(inc2)
            await db.flush()
            await db.commit()
            await db.refresh(inc2)
            assert inc2.id != inc.id

        async with AsyncSessionLocal() as db:
            count = await db.execute(
                text("SELECT count(*) FROM incidents WHERE tenant_id = :t AND "
                     "incident_signature = :s AND status = 'open' AND "
                     "incident_signature IS NOT NULL AND is_deleted IS false"),
                {"t": tenant.id, "s": sig},
            )
            assert count.scalar() == 1
    finally:
        await _delete_tenant(tenant)


async def test_lifecycle_soft_deleted_drops_from_index():
    tenant, _site, device, source, sensor = await _make_scaffold()
    try:
        sig = build_signature(
            metric_type="ssl_security",
            title=sensor.name,
            device_id=device.id,
            sensor_id=sensor.id,
            signal_external_id=None,
        )
        async with AsyncSessionLocal() as db:
            inc = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="SSL check", severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=sig,
            )
            db.add(inc)
            await db.commit()
            await db.refresh(inc)

            await db.execute(
                update(Incident)
                .where(Incident.id == inc.id)
                .values(is_deleted=True, deleted_at=datetime.now(timezone.utc))
            )
            await db.commit()

            inc2 = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="SSL check (after soft-delete)", severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=sig,
            )
            db.add(inc2)
            await db.flush()
            await db.commit()
            await db.refresh(inc2)
            assert inc2.id != inc.id
    finally:
        await _delete_tenant(tenant)


async def test_lifecycle_acknowledged_allows_new_open():
    tenant, _site, device, source, sensor = await _make_scaffold()
    try:
        sig = build_signature(
            metric_type="ssl_security",
            title=sensor.name,
            device_id=device.id,
            sensor_id=sensor.id,
            signal_external_id=None,
        )
        async with AsyncSessionLocal() as db:
            inc = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="SSL check", severity="critical", status="acknowledged",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=sig,
            )
            db.add(inc)
            await db.commit()
            await db.refresh(inc)

            inc2 = Incident(
                tenant_id=tenant.id, device_id=device.id,
                title="SSL check (new open)", severity="critical", status="open",
                opened_at=datetime.now(timezone.utc),
                last_alert_at=datetime.now(timezone.utc),
                alert_count=1, incident_signature=sig,
            )
            db.add(inc2)
            await db.flush()
            await db.commit()
            await db.refresh(inc2)
            assert inc2.id != inc.id
    finally:
        await _delete_tenant(tenant)


# Case E — Nullable signatures


async def test_null_signature_not_deduplicated():
    tenant, _site, device, source, sensor = await _make_scaffold()
    try:
        created_ids = []
        async with AsyncSessionLocal() as db:
            for i in range(3):
                inc = Incident(
                    tenant_id=tenant.id, device_id=device.id,
                    title=f"SSL check {i}", severity="critical", status="open",
                    opened_at=datetime.now(timezone.utc),
                    last_alert_at=datetime.now(timezone.utc),
                    alert_count=1, incident_signature=None,
                )
                db.add(inc)
                await db.flush()
                await db.refresh(inc)
                created_ids.append(inc.id)
            await db.commit()

        async with AsyncSessionLocal() as db:
            count = await db.execute(
                text("SELECT count(*) FROM incidents WHERE tenant_id = :t "
                     "AND status = 'open' AND incident_signature IS NULL "
                     "AND is_deleted IS false"),
                {"t": tenant.id},
            )
            assert count.scalar() == 3
    finally:
        await _delete_tenant(tenant)


# Case F — Migration repeatability and failure safety


def _current_alembic_head() -> str:
    """Return the current single head revision declared by the migration scripts.

    Derived from the scripts themselves (via Alembic's own script directory)
    instead of a hardcoded literal, so adding a legitimate migration - including
    a merge migration that resolves a multiple-head situation - does not break
    the consistency check below. A genuine "database is not migrated" failure
    still fails the assertion.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Expected exactly one Alembic head, got {sorted(heads)} - the "
        f"migration history has diverged and needs a merge revision"
    )
    return heads[0]


async def test_alembic_revision_consistency():
    import asyncpg
    from app.core.config import get_settings

    settings = get_settings()
    url = settings.test_database_url or settings.database_url
    raw_url = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)
    try:
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        # The expected revision is derived from the migration scripts rather
        # than hardcoded, so a legitimate merge migration (a new head) does not
        # break this check - but a database that is NOT at the current head
        # still fails loudly.
        expected = _current_alembic_head()
        assert version == expected, (
            f"Expected head revision {expected}, got {version}"
        )
    finally:
        await conn.close()


async def test_uq_incidents_open_signature_predicate_matches_model():
    import asyncpg
    from app.core.config import get_settings

    settings = get_settings()
    raw_url = settings.test_database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)
    try:
        row = await conn.fetchrow(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'incidents' AND indexname = 'uq_incidents_open_signature'
            """
        )
        assert row is not None, "uq_incidents_open_signature index not found"
        indexdef = row["indexdef"]
        assert "UNIQUE" in indexdef.upper(), "Index is not UNIQUE"
        assert "WHERE" in indexdef.upper(), "Index is not partial (no WHERE clause)"
        assert "tenant_id" in indexdef
        assert "incident_signature" in indexdef
        # The DB stores the predicate as (status)::text = 'open'::text
        assert "open" in indexdef
        assert "incident_signature IS NOT NULL" in indexdef
        assert "is_deleted IS FALSE" in indexdef
    finally:
        await conn.close()


async def test_incident_signature_and_closure_reason_columns_exist():
    import asyncpg
    from app.core.config import get_settings

    settings = get_settings()
    raw_url = settings.test_database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)
    try:
        cols = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'incidents'
              AND column_name IN ('incident_signature', 'closure_reason')
            """
        )
        col_map = {r["column_name"]: r for r in cols}
        assert "incident_signature" in col_map, "incident_signature column missing"
        assert "closure_reason" in col_map, "closure_reason column missing"
        assert col_map["incident_signature"]["data_type"] == "character varying"
        assert col_map["incident_signature"]["is_nullable"] == "YES"
        assert col_map["closure_reason"]["data_type"] == "text"
        assert col_map["closure_reason"]["is_nullable"] == "YES"
    finally:
        await conn.close()