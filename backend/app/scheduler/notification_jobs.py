"""Worker job: incident notifications (app/services/notifications)."""
from __future__ import annotations

import logging

from app.core.db import AsyncSessionLocal
from app.services.notifications import NotificationDispatcher

logger = logging.getLogger(__name__)

NOTIFICATION_INTERVAL_SECONDS = 30


async def dispatch_incident_notifications_job() -> None:
    try:
        async with AsyncSessionLocal() as db:
            totals = await NotificationDispatcher(db).dispatch_all()
        if any(totals.values()):
            logger.info("incident notifications: %s", totals)
    except Exception:  # noqa: BLE001 - a notification tick must never kill the scheduler
        logger.exception("incident notification tick failed")
