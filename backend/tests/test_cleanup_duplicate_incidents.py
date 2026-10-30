"""scripts/cleanup_duplicate_incidents.py against a real database.

Covers grouping (title normalization, device scope, open statuses only), the
keeper choice (most recently active copy), dry-run purity, batched apply with
alert relinking, tenant isolation, idempotent re-runs and full undo.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text

from app.core.db import AsyncSessionLocal, engine
from app.models.alert import Alert
from app.models.device import Device
from app.models.incident import Incident
from app.models.monitoring_source import MonitoringSource
from app.models.site import Site
from app.models.tenant import Tenant
from scripts.cleanup_duplicate_incidents import apply_cleanup, build_plan, undo_run

T0 = datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc)


async def _scaffold():
    """A tenant with one device and the duplicate shapes seen in production:
    one group of five open copies (two title spellings), a singleton, and a
    title whose other copy is already resolved."""
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=f"Cleanup {uuid.uuid4().hex[:8]}", slug=f"cleanup-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.flush()
        site = Site(tenant_id=tenant.id, name="Site", code=f"S-{uuid.uuid4().hex[:4]}")
        db.add(site)
        await db.flush()
        device = Device(tenant_id=tenant.id, site_id=site.id, name="mail.example.com", device_type="server", status="up")
        source = MonitoringSource(tenant_id=tenant.id, name="src", source_type="test", is_active=True)
        db.add_all([device, source])
        await db.flush()

        def incident(title: str, minutes: int, status: str = "open") -> Incident:
            return Incident(
                tenant_id=tenant.id, device_id=device.id, title=title, severity="critical", status=status,
                opened_at=T0, last_alert_at=T0 + timedelta(minutes=minutes), alert_count=1,
            )

        dups = [incident("Incident: SSL Security Check (Port 993)", m) for m in (1, 5, 3, 2)]
        dups.append(incident("incident:  ssl security check   (port 993)", 4))  # same group once normalized
        singleton = incident("Incident: Ping v2", 1)
        resolved_pair = [incident("Incident: System Health", 1), incident("Incident: System Health", 2, status="resolved")]
        db.add_all([*dups, singleton, *resolved_pair])
        await db.flush()

        # Alerts pointing at two copies that will be archived, and at the keeper.
        keeper = dups[1]  # latest last_alert_at (+5 min)
        alerts = []
        for target in (dups[0], dups[2], keeper):
            a = Alert(
                tenant_id=tenant.id, device_id=device.id, monitoring_source_id=source.id, incident_id=target.id,
                title="SSL Security Check", severity="critical", status="open", category="availability",
                triggered_at=T0, last_occurred_at=T0, occurrence_count=1,
            )
            db.add(a)
            alerts.append(a)
        await db.commit()
        return {
            "tenant": tenant.id,
            "keeper": keeper.id,
            "dups": [d.id for d in dups],
            "alerts": {a.id: a.incident_id for a in alerts},
            "untouched": [singleton.id, resolved_pair[0].id, resolved_pair[1].id],
        }


async def _statuses(ids):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Incident.id, Incident.status).where(Incident.id.in_(ids)))).all()
    return {r[0]: r[1] for r in rows}


async def _alert_links(ids):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Alert.id, Alert.incident_id).where(Alert.id.in_(ids)))).all()
    return {r[0]: r[1] for r in rows}


async def _drop(tenant_ids):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        for table in ("incident_cleanup_undo", "incident_cleanup_pending"):
            exists = (await db.execute(text("SELECT to_regclass(:t)"), {"t": table})).scalar()
            if exists:
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id = ANY(:ids)"), {"ids": list(tenant_ids)})
        await db.commit()


async def test_dry_run_plan_counts_and_changes_nothing():
    s = await _scaffold()
    try:
        before = await _statuses(s["dups"] + s["untouched"])
        async with engine.connect() as conn:
            plan = await build_plan(conn, str(s["tenant"]))
            await conn.rollback()
        # 5 copies + singleton + one open System Health = 7 open; 3 kept.
        assert (plan.open_total, plan.keep, plan.archive, plan.groups) == (7, 3, 4, 1)
        assert plan.by_severity == {"critical": 4}
        assert plan.relinks == {"alerts.incident_id": 2}
        assert await _statuses(s["dups"] + s["untouched"]) == before
    finally:
        await _drop([s["tenant"]])


async def test_apply_archives_copies_relinks_alerts_and_undo_restores():
    s = await _scaffold()
    other = await _scaffold()  # identical duplicates in another tenant: must be untouched
    try:
        async with engine.connect() as conn:
            run_id = await apply_cleanup(conn, str(s["tenant"]), batch_size=2, log=lambda *_: None)
        assert run_id

        statuses = await _statuses(s["dups"] + s["untouched"])
        assert statuses[s["keeper"]] == "open"
        assert sorted(statuses[d] for d in s["dups"] if d != s["keeper"]) == ["archived"] * 4
        assert [statuses[u] for u in s["untouched"]] == ["open", "open", "resolved"]
        # Every alert now points at the kept incident.
        assert set((await _alert_links(list(s["alerts"]))).values()) == {s["keeper"]}
        # The other tenant was not touched.
        assert set((await _statuses(other["dups"])).values()) == {"open"}
        assert set((await _alert_links(list(other["alerts"]))).values()) == set(other["alerts"].values())

        async with AsyncSessionLocal() as db:
            undo_rows = (
                await db.execute(text("SELECT count(*), count(DISTINCT keeper_id) FROM incident_cleanup_undo WHERE run_id = :r"), {"r": run_id})
            ).one()
        assert tuple(undo_rows) == (4, 1)

        # Re-running finds nothing left to do.
        async with engine.connect() as conn:
            assert await apply_cleanup(conn, str(s["tenant"]), log=lambda *_: None) is None

        # Undo restores statuses and the original alert links.
        async with engine.connect() as conn:
            assert await undo_run(conn, run_id, log=lambda *_: None) == 4
        assert set((await _statuses(s["dups"])).values()) == {"open"}
        assert await _alert_links(list(s["alerts"])) == s["alerts"]
        async with AsyncSessionLocal() as db:
            left = (
                await db.execute(
                    text(
                        "SELECT (SELECT count(*) FROM incident_cleanup_undo WHERE run_id = :r)"
                        " + (SELECT count(*) FROM incident_cleanup_relinks WHERE run_id = :r)"
                    ),
                    {"r": run_id},
                )
            ).scalar()
        assert left == 0
    finally:
        await _drop([s["tenant"], other["tenant"]])


class _Crash(Exception):
    pass


async def test_interrupted_apply_resumes_the_same_run():
    s = await _scaffold()
    try:
        def crash_after_relink(line: str) -> None:
            # Logged right after the relink transaction commits, before any archive batch.
            if "incidents to archive" in line:
                raise _Crash(line)

        crashed_run = None
        async with engine.connect() as conn:
            try:
                await apply_cleanup(conn, str(s["tenant"]), batch_size=1, log=crash_after_relink)
            except _Crash as exc:
                crashed_run = str(exc).split()[1].rstrip(":")
        assert crashed_run
        # Relinked but nothing archived yet.
        assert set((await _alert_links(list(s["alerts"]))).values()) == {s["keeper"]}
        assert set((await _statuses(s["dups"])).values()) == {"open"}

        logs: list[str] = []
        async with engine.connect() as conn:
            resumed = await apply_cleanup(conn, str(s["tenant"]), batch_size=1, log=logs.append)
        assert resumed == crashed_run
        assert any("resuming run" in line for line in logs)
        assert sorted((await _statuses(s["dups"])).values()) == ["archived"] * 4 + ["open"]

        # Undo after a resumed run still restores everything.
        async with engine.connect() as conn:
            assert await undo_run(conn, resumed, log=lambda *_: None) == 4
        assert await _alert_links(list(s["alerts"])) == s["alerts"]
    finally:
        await _drop([s["tenant"]])
