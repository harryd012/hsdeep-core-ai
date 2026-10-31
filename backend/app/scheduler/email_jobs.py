"""Worker job: deliver queued email (app/services/email/outbox.py)."""
from __future__ import annotations

import logging

from app.core.db import AsyncSessionLocal
from app.services.email.outbox import deliver_due

logger = logging.getLogger(__name__)

EMAIL_DELIVERY_INTERVAL_SECONDS = 30


async def deliver_email_outbox_job() -> None:
    try:
        async with AsyncSessionLocal() as db:
            counts = await deliver_due(db)
        if any(counts.values()):
            logger.info("email outbox: %s", counts)
    except Exception:  # noqa: BLE001 - a delivery tick must never kill the scheduler
        logger.exception("email outbox delivery tick failed")
