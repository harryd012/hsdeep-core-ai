"""Incident lifecycle scheduler job: the stale-close sweep.

Auto-closes open/acknowledged/… incidents that have received no new alert for
``Settings.incident_stale_minutes``. Guarded by a Postgres advisory lock so
only one worker sweeps at a time; no-ops when the lock is held elsewhere
(mirrors the connection-scoped lock handling in ``jobs.tick``).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal, engine
from app.repositories.incident_repository import IncidentRepository
from app.services.alerting.lifecycle import close_stale_incidents

logger = logging.getLogger(__name__)

# Arbitrary constant advisory-lock key for this job — must stay stable.
STALE_CLOSE_LOCK_KEY = 91827366


async def close_stale_incidents_job() -> None:
    """One stale-close sweep. Never raises; returns silently when skipped."""
    settings = get_settings()
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": STALE_CLOSE_LOCK_KEY}
        )
        if not bool(result.scalar_one()):
            logger.debug("Stale-close: advisory lock held by another worker, skipping.")
            return
        try:
            async with AsyncSessionLocal() as db:
                incident_repo = IncidentRepository(db)
                closed = await close_stale_incidents(
                    incident_repo, stale_minutes=settings.incident_stale_minutes
                )
                if closed:
                    await db.commit()
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": STALE_CLOSE_LOCK_KEY}
            )
