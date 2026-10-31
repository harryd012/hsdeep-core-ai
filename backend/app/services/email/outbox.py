"""
Email outbox: enqueue in the request's transaction, deliver from the worker.

Delivery retries with backoff (1, 5, 15, 60 min) up to MAX_ATTEMPTS, then the
row stays `failed` with its last error for operators to see. Rows are claimed
with FOR UPDATE SKIP LOCKED so two workers never send the same message.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.email_outbox import EmailOutbox
from app.services.email.transport import EmailMessage, EmailTransport, get_transport

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
_BACKOFF_MINUTES = (1, 5, 15, 60)
_BATCH = 50


class EmailDisabled(RuntimeError):
    """EMAIL_TRANSPORT=none: flows that must send email cannot run."""


def email_enabled() -> bool:
    return get_settings().email_transport != "none"


async def enqueue_email(
    db: AsyncSession,
    *,
    to: str,
    subject: str,
    body_text: str,
    category: str,
    tenant_id: uuid.UUID | None,
) -> EmailOutbox:
    if not email_enabled():
        raise EmailDisabled("Email is not configured on this deployment.")
    row = EmailOutbox(
        tenant_id=tenant_id,
        to_address=to,
        subject=subject,
        body_text=body_text,
        category=category,
        status="queued",
        attempts=0,
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


async def deliver_due(db: AsyncSession, transport: EmailTransport | None = None) -> dict[str, int]:
    """Send due queued messages. Commits per message so one failure never
    rolls back another's `sent` state."""
    transport = transport or get_transport()
    if transport is None:
        return {"sent": 0, "failed": 0, "retrying": 0}
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(EmailOutbox)
            .where(EmailOutbox.status == "queued", EmailOutbox.next_attempt_at <= now)
            .order_by(EmailOutbox.next_attempt_at)
            .limit(_BATCH)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    counts = {"sent": 0, "failed": 0, "retrying": 0}
    for row in rows:
        row.attempts += 1
        try:
            await transport.send(EmailMessage(to=row.to_address, subject=row.subject, body_text=row.body_text))
        except Exception as exc:  # noqa: BLE001 - any transport error is a delivery failure
            row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
                counts["failed"] += 1
                logger.error("email %s failed permanently after %s attempts", row.id, row.attempts)
            else:
                delay = _BACKOFF_MINUTES[min(row.attempts - 1, len(_BACKOFF_MINUTES) - 1)]
                row.next_attempt_at = now + timedelta(minutes=delay)
                counts["retrying"] += 1
        else:
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)
            row.last_error = None
            counts["sent"] += 1
    await db.commit()
    return counts
