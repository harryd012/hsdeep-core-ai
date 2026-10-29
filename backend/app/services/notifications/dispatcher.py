"""
Incident notifications: email (through the outbox), Slack and PagerDuty.

Runs from the worker (app/scheduler/notification_jobs.py), not the event
bus: the bus is in-process, so incidents created by another process would be
missed. Each tick finds incidents opened or resolved since the tenant's
settings row was created (and within the last LOOKBACK), and delivers each
(incident, channel, event) once, recorded in incident_notifications:

  - opened:   incidents at or above the channel's minimum severity
              (warning for email/Slack; pagerduty_severity, default warning,
              for PagerDuty);
  - resolved: only for channels that were told about the opening, so nobody
              gets a "resolved" for an incident they never saw. PagerDuty
              resolves the alert it opened (dedup_key = incident id).

A failed delivery keeps its error and is retried on later ticks up to
MAX_ATTEMPTS. Enabling a channel never floods it with history: incidents
older than the settings row, or than LOOKBACK, are skipped.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.incident import Incident
from app.models.incident_notification import IncidentNotification
from app.models.notification_setting import NotificationSetting
from app.services.email.outbox import EmailDisabled, enqueue_email

logger = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=24)
MAX_ATTEMPTS = 3
_BATCH = 200
PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
SLACK_WEBHOOK_HOST = "hooks.slack.com"

# Incident severities are critical | warning | info; PagerDuty's "error" sits
# between warning and critical.
_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
_DEFAULT_MIN = "warning"

Post = Callable[[str, dict[str, Any]], Awaitable[int]]


def is_allowed_slack_webhook(url: str | None) -> bool:
    """Only Slack's own webhook host: a tenant-supplied URL must never make
    the platform call arbitrary (internal) addresses."""
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == SLACK_WEBHOOK_HOST


def _meets(severity: str | None, minimum: str | None) -> bool:
    return _RANK.get((severity or "").lower(), 0) >= _RANK.get((minimum or _DEFAULT_MIN).lower(), 1)


async def _http_post(url: str, payload: dict[str, Any]) -> int:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        return resp.status_code


def _link(incident: Incident) -> str:
    return f"{get_settings().app_public_url.rstrip('/')}/incidents/{incident.id}"


def _summary(incident: Incident, event: str) -> str:
    sev = (incident.severity or "unknown").upper()
    verb = "Resolved" if event == "resolved" else "Opened"
    return f"[{sev}] {verb}: {incident.title}"


class NotificationDispatcher:
    def __init__(self, db: AsyncSession, *, post: Post | None = None):
        self.db = db
        self._post = post or _http_post

    # -- channel senders ------------------------------------------------------

    async def _send_email(self, setting: NotificationSetting, incident: Incident, event: str) -> None:
        recipients = [r.strip() for r in (setting.email_recipients or "").split(",") if r.strip()]
        if not recipients:
            raise ValueError("No email recipients configured.")
        body = (
            f"{_summary(incident, event)}\n\n"
            f"Severity: {incident.severity}\nStatus: {incident.status}\n"
            f"Opened: {incident.opened_at.isoformat()}\n"
            + (f"Resolved: {incident.resolved_at.isoformat()}\n" if incident.resolved_at else "")
            + f"\nOpen the incident: {_link(incident)}\n"
        )
        for to in recipients:
            await enqueue_email(
                self.db, to=to, subject=_summary(incident, event), body_text=body,
                category=f"incident_{event}", tenant_id=incident.tenant_id,
            )

    async def _send_slack(self, setting: NotificationSetting, incident: Incident, event: str) -> None:
        if not is_allowed_slack_webhook(setting.slack_webhook_url):
            raise ValueError("Slack webhook URL must be https://hooks.slack.com/...")
        payload: dict[str, Any] = {"text": f"{_summary(incident, event)}\n<{_link(incident)}|Open the incident>"}
        if setting.slack_channel:
            payload["channel"] = setting.slack_channel
        code = await self._post(setting.slack_webhook_url, payload)
        if not 200 <= code < 300:
            raise RuntimeError(f"Slack answered HTTP {code}")

    async def _send_pagerduty(self, setting: NotificationSetting, incident: Incident, event: str) -> None:
        if not setting.pagerduty_integration_key:
            raise ValueError("No PagerDuty integration key configured.")
        payload: dict[str, Any] = {
            "routing_key": setting.pagerduty_integration_key,
            "event_action": "resolve" if event == "resolved" else "trigger",
            "dedup_key": str(incident.id),
        }
        if event == "opened":
            payload["payload"] = {
                "summary": _summary(incident, event)[:1024],
                "source": "HSDEEP CORE AI",
                "severity": "critical" if incident.severity == "critical" else "warning",
            }
            payload["links"] = [{"href": _link(incident), "text": "Open the incident"}]
        code = await self._post(PAGERDUTY_EVENTS_URL, payload)
        if not 200 <= code < 300:
            raise RuntimeError(f"PagerDuty answered HTTP {code}")

    def _channels(self, setting: NotificationSetting) -> dict[str, tuple[Callable, str]]:
        channels: dict[str, tuple[Callable, str]] = {}
        if setting.email_enabled:
            channels["email"] = (self._send_email, _DEFAULT_MIN)
        if setting.slack_enabled:
            channels["slack"] = (self._send_slack, _DEFAULT_MIN)
        if setting.pagerduty_enabled:
            channels["pagerduty"] = (self._send_pagerduty, setting.pagerduty_severity or _DEFAULT_MIN)
        return channels

    # -- delivery -------------------------------------------------------------

    async def _deliver(self, setting, incident, channel, sender, event, existing) -> str:
        row = existing.get((incident.id, channel, event))
        if row is not None and (row.status == "sent" or row.attempts >= MAX_ATTEMPTS):
            return "skipped"
        if row is None:
            row = IncidentNotification(
                tenant_id=incident.tenant_id, incident_id=incident.id,
                channel=channel, event=event, status="failed", attempts=0,
            )
            self.db.add(row)
            existing[(incident.id, channel, event)] = row
        row.attempts += 1
        try:
            await sender(setting, incident, event)
        except EmailDisabled:
            row.status, row.attempts = "failed", MAX_ATTEMPTS  # retrying cannot help
            row.last_error = "Email is not configured on this deployment."
            return "failed"
        except Exception as exc:  # noqa: BLE001 - any channel error is a failed delivery
            row.status = "failed"
            row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            logger.warning("incident notification %s/%s failed: %s", channel, event, row.last_error)
            return "failed"
        row.status, row.last_error, row.sent_at = "sent", None, datetime.now(timezone.utc)
        return "sent"

    async def dispatch_tenant(self, setting: NotificationSetting, *, now: datetime | None = None) -> dict[str, int]:
        channels = self._channels(setting)
        counts = {"sent": 0, "failed": 0}
        if not channels:
            return counts
        now = now or datetime.now(timezone.utc)
        cutoff = max(setting.created_at, now - LOOKBACK)
        incidents = (
            await self.db.execute(
                select(Incident)
                .where(
                    Incident.tenant_id == setting.tenant_id,
                    Incident.is_deleted.is_(False),
                    or_(Incident.opened_at >= cutoff, and_(Incident.resolved_at.is_not(None), Incident.resolved_at >= cutoff)),
                )
                .order_by(Incident.opened_at)
                .limit(_BATCH)
            )
        ).scalars().all()
        if not incidents:
            return counts
        existing = {
            (r.incident_id, r.channel, r.event): r
            for r in (
                await self.db.execute(
                    select(IncidentNotification).where(
                        IncidentNotification.incident_id.in_([i.id for i in incidents])
                    )
                )
            ).scalars()
        }
        for incident in incidents:
            for channel, (sender, minimum) in channels.items():
                if incident.opened_at >= cutoff and _meets(incident.severity, minimum):
                    outcome = await self._deliver(setting, incident, channel, sender, "opened", existing)
                    if outcome in counts:
                        counts[outcome] += 1
                # Resolve only what this channel was actually told about.
                opened = existing.get((incident.id, channel, "opened"))
                if incident.resolved_at is not None and opened is not None and opened.status == "sent":
                    outcome = await self._deliver(setting, incident, channel, sender, "resolved", existing)
                    if outcome in counts:
                        counts[outcome] += 1
        await self.db.flush()
        return counts

    async def dispatch_all(self) -> dict[str, int]:
        settings = (
            await self.db.execute(
                select(NotificationSetting).where(
                    or_(
                        NotificationSetting.email_enabled.is_(True),
                        NotificationSetting.slack_enabled.is_(True),
                        NotificationSetting.pagerduty_enabled.is_(True),
                    )
                )
            )
        ).scalars().all()
        totals = {"sent": 0, "failed": 0}
        for setting in settings:
            counts = await self.dispatch_tenant(setting)
            await self.db.commit()
            for k in totals:
                totals[k] += counts[k]
        return totals

    async def send_test(self, setting: NotificationSetting) -> dict[str, str]:
        """Send a clearly labelled test message to each enabled channel now.
        Nothing is recorded in incident_notifications."""
        results: dict[str, str] = {}
        probe = Incident(
            id=uuid.uuid4(),
            tenant_id=setting.tenant_id, title="Test notification from HSDEEP CORE AI",
            severity="warning", status="open", opened_at=datetime.now(timezone.utc),
            last_alert_at=datetime.now(timezone.utc),
        )
        for channel, (sender, _minimum) in self._channels(setting).items():
            try:
                await sender(setting, probe, "opened")
                if channel == "pagerduty":
                    # Never leave a test page open on call.
                    await sender(setting, probe, "resolved")
                results[channel] = "sent" if channel != "email" else "queued"
            except Exception as exc:  # noqa: BLE001
                results[channel] = f"failed: {exc}"[:300]
        return results
