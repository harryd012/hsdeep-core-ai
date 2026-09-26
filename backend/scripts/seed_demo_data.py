"""
Seed demo operational history and default settings for local/dev environments.

Idempotent:
  - Creates default settings row for the dev tenant if missing.
  - Seeds realistic Events and CollectorRuns for the last 24-48 hours
    so the Dashboard charts and health bars have data to display.

Environment variables read (from backend/.env):
    SEED_TENANT_ID      UUID of the tenant to seed (required)
    SEED_DEFAULT_TENANT_ID  Same as SEED_TENANT_ID (for convenience)

Usage (from backend directory with venv active):
    python scripts/seed_demo_data.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.models.collector_run import CollectorRun  # noqa: E402
from app.models.event import Event  # noqa: E402
from app.models.monitoring_source import MonitoringSource  # noqa: E402
from app.models.setting import Setting  # noqa: E402
from app.repositories.monitoring_source_repository import MonitoringSourceRepository  # noqa: E402

REQUIRED_ENV_VARS = ("SEED_TENANT_ID",)


def _missing_required_env_vars() -> list[str]:
    return [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]


async def _seed_default_settings(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Create default settings row if missing. Returns True if created."""
    existing = (
        await db.execute(select(Setting).where(Setting.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if existing is not None:
        return False

    now = datetime.now(timezone.utc)
    setting = Setting(
        tenant_id=tenant_id,
        platform_name="HSDEEP Core AI",
        timezone="UTC",
        branding_logo_url=None,
        primary_color=None,
        locale="en",
        created_at=now,
        updated_at=now,
    )
    db.add(setting)
    await db.flush()
    return True


async def _seed_demo_collector_runs(db: AsyncSession, tenant_id: uuid.UUID, sources: list[MonitoringSource]) -> int:
    """Seed 24-48h of realistic collector run history."""
    created = 0
    now = datetime.now(timezone.utc)

    for source in sources:
        for hour_offset in range(48, -1, -3):  # Every 3 hours for 48h
            started_at = now - hour_offset * 3600
            finished_at = started_at.replace(microsecond=0)
            # Simulate mostly successful runs with occasional errors
            if hour_offset % 17 == 0:
                status = "error"
                error = "Connection timeout"
            elif hour_offset % 13 == 0:
                status = "partial"
                error = None
            else:
                status = "success"
                error = None

            run = CollectorRun(
                tenant_id=tenant_id,
                monitoring_source_id=source.id,
                trigger="scheduler",
                status=status,
                error=error,
                devices_upserted=0,
                interfaces_upserted=0,
                sensors_upserted=0,
                metrics_recorded=0,
                events_recorded=0,
                alerts_upserted=0,
                started_at=started_at,
                finished_at=finished_at,
            )
            db.add(run)
            created += 1

    return created


async def _seed_demo_events(db: AsyncSession, tenant_id: uuid.UUID, sources: list[MonitoringSource]) -> int:
    """Seed realistic events for the last 24-48 hours."""
    created = 0
    now = datetime.now(timezone.utc)

    event_templates = [
        {"event_type": "config_change", "severity": "info", "message": "Configuration updated"},
        {"event_type": "reboot", "severity": "warning", "message": "Device rebooted"},
        {"event_type": "login", "severity": "info", "message": "Admin login detected"},
        {"event_type": "ha_failover", "severity": "critical", "message": "HA failover triggered"},
        {"event_type": "security", "severity": "warning", "message": "Security event detected"},
        {"event_type": "sensor_alert", "severity": "warning", "message": "Sensor threshold exceeded"},
        {"event_type": "alert", "severity": "info", "message": "Alert triggered"},
        {"event_type": "incident", "severity": "critical", "message": "Incident detected"},
    ]

    for source in sources:
        for hour_offset in range(48, -1, -1):  # Hourly events for 48h
            # 1-3 events per hour
            for _ in range(1, 4):
                template = event_templates[
                    (hour_offset + int(now.timestamp())) % len(event_templates)
                ]
                occurred_at = now - hour_offset * 3600 + _ * 600
                event = Event(
                    tenant_id=tenant_id,
                    monitoring_source_id=source.id,
                    event_type=template["event_type"],
                    severity=template["severity"],
                    message=template["message"],
                    occurred_at=occurred_at,
                    raw_payload={},
                )
                db.add(event)
                created += 1

    return created


async def seed_demo_data(db: AsyncSession) -> int:
    missing = _missing_required_env_vars()
    if missing:
        print(f"[seed_demo_data] Aborted: missing env vars: {', '.join(missing)}")
        return 1

    tenant_id = uuid.UUID(os.environ["SEED_TENANT_ID"])
    print(f"[seed_demo_data] Seeding demo data for tenant_id={tenant_id}")

    # 1. Default settings
    created_settings = await _seed_default_settings(db, tenant_id)
    if created_settings:
        print("[seed_demo_data] Created default settings row")
    else:
        print("[seed_demo_data] Settings row already exists")

    # 2. Find monitoring sources for this tenant
    source_repo = MonitoringSourceRepository(db)
    sources = await source_repo.list(tenant_id)
    if not sources:
        print("[seed_demo_data] No monitoring sources found - skipping event/run seeding")
        await db.flush()
        return 0

    print(f"[seed_demo_data] Found {len(sources)} monitoring source(s)")

    # 3. Seed collector runs
    runs_created = await _seed_demo_collector_runs(db, tenant_id, sources)
    print(f"[seed_demo_data] Created {runs_created} collector run(s)")

    # 4. Seed events
    events_created = await _seed_demo_events(db, tenant_id, sources)
    print(f"[seed_demo_data] Created {events_created} event(s)")

    await db.flush()
    return 0


async def main() -> int:
    try:
        async with AsyncSessionLocal() as db:
            exit_code = await seed_demo_data(db)
            if exit_code == 0:
                await db.commit()
            else:
                await db.rollback()
            return exit_code
    except Exception as exc:  # noqa: BLE001
        print(f"[seed_demo_data] Failed: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))