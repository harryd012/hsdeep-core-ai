"""
Incident notifications: email (through the outbox), Slack, PagerDuty and
Microsoft Teams.

Runs from the worker (app/scheduler/notification_jobs.py), not the event
bus: the bus is in-process, so incidents created by another process would be
missed. Each tick finds incidents opened or resolved since a destination was
set up (and within the last LOOKBACK), and delivers each
(incident, destination, event) once, recorded in incident_notifications:

  - opened:   incidents the destination is for (below);
  - resolved: only for destinations that were told about the opening, so
              nobody gets a "resolved" for an incident they never saw.
              PagerDuty resolves the alert it opened (dedup_key = incident id).

Which incidents a destination is for (observability plan P9):

  - without enabled notification policies, as before: every enabled channel
    of the tenant's NotificationSetting, at its minimum severity (warning for
    email / Slack; pagerduty_severity, default warning, for PagerDuty);
  - with enabled policies: a policy matches by minimum severity and,
    optionally, priority and site. A tenant channel (email / Slack /
    PagerDuty) a matching policy names is sent once, still at its own minimum;
    a policy's own Teams webhook or email recipients are a destination of
    their own (`teams:<policy>` / `email:<policy>`).

A failed delivery keeps its error and is retried on later ticks up to
MAX_ATTEMPTS. Setting up a destination never floods it with history:
incidents older than the settings row or policy, or than LOOKBACK, are skipped.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import SecretCrypto
from app.models.incident import Incident
from app.models.incident_notification import IncidentNotification
from app.models.notification_policy import NotificationPolicy
from app.models.notification_setting import NotificationSetting
from app.services.email.outbox import EmailDisabled, enqueue_email

logger = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=24)
MAX_ATTEMPTS = 3
_BATCH = 200
PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
SLACK_WEBHOOK_HOST = "hooks.slack.com"
#: Microsoft Teams incoming webhooks: the classic connector host and the
#: Workflows (Power Automate) hosts that replace it.
TEAMS_WEBHOOK_SUFFIXES = (".webhook.office.com", ".logic.azure.com", ".api.powerplatform.com")

# Incident severities are critical | warning | info; PagerDuty's "error" sits
# between warning and critical.
_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
_DEFAULT_MIN = "warning"

Post = Callable[[str, dict[str, Any]], Awaitable[int]]
Send = Callable[[Incident, str], Awaitable[None]]


def is_allowed_slack_webhook(url: str | None) -> bool:
    """Only Slack's own webhook host: a tenant-supplied URL must never make
    the platform call arbitrary (internal) addresses."""
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == SLACK_WEBHOOK_HOST


def is_allowed_teams_webhook(url: str | None) -> bool:
    """Only Microsoft's Teams / Workflows webhook hosts, over https (same reason as Slack)."""
    if not url:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host.endswith(s) and len(host) > len(s) for s in TEAMS_WEBHOOK_SUFFIXES)


def _meets(severity: str | None, minimum: str | None) -> bool:
    return _RANK.get((severity or "").lower(), 0) >= _RANK.get((minimum or _DEFAULT_MIN).lower(), 1)


def policy_matches(policy: NotificationPolicy, incident: Incident) -> bool:
    """Severity at or above the policy's minimum, and priority / site when the policy names any."""
    if not _meets(incident.severity, policy.min_severity):
        return False
    if policy.priorities and (incident.priority or "") not in policy.priorities:
        return False
    if policy.site_ids and str(incident.site_id) not in policy.site_ids:
        return False
    return True


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


def teams_card(incident: Incident, event: str) -> dict[str, Any]:
    """An Adaptive Card message, the format both Teams webhook kinds accept."""
    facts = [{"title": "Severity", "value": incident.severity or "unknown"}, {"title": "Status", "value": incident.status}]
    if incident.priority:
        facts.append({"title": "Priority", "value": incident.priority})
    facts.append({"title": "Opened", "value": incident.opened_at.isoformat()})
    if incident.resolved_at:
        facts.append({"title": "Resolved", "value": incident.resolved_at.isoformat()})
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "text": _summary(incident, event), "weight": "Bolder", "wrap": True,
                     "color": "Good" if event == "resolved" else ("Attention" if incident.severity == "critical" else "Warning")},
                    {"type": "FactSet", "facts": facts},
                ],
                "actions": [{"type": "Action.OpenUrl", "title": "Open the incident", "url": _link(incident)}],
            },
        }],
    }


@dataclass
class Destination:
    #: Stored in incident_notifications.channel (at most 20 characters).
    key: str
    send: Send
    matches: Callable[[Incident], bool]
    cutoff: datetime


class NotificationDispatcher:
    def __init__(self, db: AsyncSession, *, post: Post | None = None):
        self.db = db
        self._post = post or _http_post

    # -- channel senders ------------------------------------------------------

    async def _send_email_to(self, recipients_csv: str | None, incident: Incident, event: str) -> None:
        recipients = [r.strip() for r in (recipients_csv or "").split(",") if r.strip()]
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

    async def _send_email(self, setting: NotificationSetting, incident: Incident, event: str) -> None:
        await self._send_email_to(setting.email_recipients, incident, event)

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

    async def _send_teams(self, encrypted_url: str, incident: Incident, event: str) -> None:
        url = SecretCrypto().decrypt(encrypted_url)
        if not is_allowed_teams_webhook(url):
            raise ValueError("Teams webhook URL must be a Microsoft Teams or Workflows https URL")
        code = await self._post(url, teams_card(incident, event))
        if not 200 <= code < 300:
            raise RuntimeError(f"Teams answered HTTP {code}")

    def _channels(self, setting: NotificationSetting) -> dict[str, tuple[Callable, str]]:
        channels: dict[str, tuple[Callable, str]] = {}
        if setting.email_enabled:
            channels["email"] = (self._send_email, _DEFAULT_MIN)
        if setting.slack_enabled:
            channels["slack"] = (self._send_slack, _DEFAULT_MIN)
        if setting.pagerduty_enabled:
            channels["pagerduty"] = (self._send_pagerduty, setting.pagerduty_severity or _DEFAULT_MIN)
        return channels

    def destinations(self, setting: NotificationSetting | None, policies: list[NotificationPolicy],
                     now: datetime) -> list[Destination]:
        """Where this tenant's incidents go (see the module docstring)."""
        channels = self._channels(setting) if setting is not None else {}
        setting_cutoff = max(setting.created_at, now - LOOKBACK) if setting is not None else now - LOOKBACK
        enabled = [p for p in policies if p.enabled]

        def bind(sender, s):
            return lambda incident, event: sender(s, incident, event)

        if not enabled:
            return [
                Destination(key, bind(sender, setting), (lambda i, m=minimum: _meets(i.severity, m)), setting_cutoff)
                for key, (sender, minimum) in channels.items()
            ]

        out: list[Destination] = []
        routed: dict[str, list[NotificationPolicy]] = {}
        for policy in enabled:
            short = policy.id.hex[:12]
            policy_cutoff = max(policy.created_at, now - LOOKBACK)
            for channel in policy.channels or []:
                if channel == "email" and policy.email_recipients:
                    out.append(Destination(
                        f"email:{short}",
                        (lambda i, e, r=policy.email_recipients: self._send_email_to(r, i, e)),
                        (lambda i, p=policy: policy_matches(p, i)), policy_cutoff,
                    ))
                elif channel in channels:
                    routed.setdefault(channel, []).append(policy)
            if policy.teams_webhook_encrypted:
                out.append(Destination(
                    f"teams:{short}",
                    (lambda i, e, u=policy.teams_webhook_encrypted: self._send_teams(u, i, e)),
                    (lambda i, p=policy: policy_matches(p, i)), policy_cutoff,
                ))
        for channel, ps in routed.items():
            sender, minimum = channels[channel]
            out.append(Destination(
                channel, bind(sender, setting),
                (lambda i, ps=ps, m=minimum: _meets(i.severity, m) and any(policy_matches(p, i) for p in ps)),
                setting_cutoff,
            ))
        return out

    # -- delivery -------------------------------------------------------------

    async def _deliver(self, incident, dest: Destination, event, existing) -> str:
        row = existing.get((incident.id, dest.key, event))
        if row is not None and (row.status == "sent" or row.attempts >= MAX_ATTEMPTS):
            return "skipped"
        if row is None:
            row = IncidentNotification(
                tenant_id=incident.tenant_id, incident_id=incident.id,
                channel=dest.key, event=event, status="failed", attempts=0,
            )
            self.db.add(row)
            existing[(incident.id, dest.key, event)] = row
        row.attempts += 1
        try:
            await dest.send(incident, event)
        except EmailDisabled:
            row.status, row.attempts = "failed", MAX_ATTEMPTS  # retrying cannot help
            row.last_error = "Email is not configured on this deployment."
            return "failed"
        except Exception as exc:  # noqa: BLE001 - any channel error is a failed delivery
            row.status = "failed"
            row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            logger.warning("incident notification %s/%s failed: %s", dest.key, event, row.last_error)
            return "failed"
        row.status, row.last_error, row.sent_at = "sent", None, datetime.now(timezone.utc)
        return "sent"

    async def _policies(self, tenant_id: uuid.UUID) -> list[NotificationPolicy]:
        return list(
            (
                await self.db.execute(
                    select(NotificationPolicy)
                    .where(NotificationPolicy.tenant_id == tenant_id)
                    .order_by(NotificationPolicy.position, NotificationPolicy.name)
                )
            ).scalars()
        )

    async def dispatch_tenant(self, setting: NotificationSetting | None, *, now: datetime | None = None,
                              tenant_id: uuid.UUID | None = None,
                              policies: list[NotificationPolicy] | None = None) -> dict[str, int]:
        tenant_id = tenant_id or setting.tenant_id
        now = now or datetime.now(timezone.utc)
        if policies is None:
            policies = await self._policies(tenant_id)
        dests = self.destinations(setting, policies, now)
        counts = {"sent": 0, "failed": 0}
        if not dests:
            return counts
        cutoff = min(d.cutoff for d in dests)
        incidents = (
            await self.db.execute(
                select(Incident)
                .where(
                    Incident.tenant_id == tenant_id,
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
            for dest in dests:
                if incident.opened_at >= dest.cutoff and dest.matches(incident):
                    outcome = await self._deliver(incident, dest, "opened", existing)
                    if outcome in counts:
                        counts[outcome] += 1
                # Resolve only what this destination was actually told about.
                opened = existing.get((incident.id, dest.key, "opened"))
                if incident.resolved_at is not None and opened is not None and opened.status == "sent":
                    outcome = await self._deliver(incident, dest, "resolved", existing)
                    if outcome in counts:
                        counts[outcome] += 1
        await self.db.flush()
        return counts

    async def dispatch_all(self) -> dict[str, int]:
        settings = {
            s.tenant_id: s
            for s in (
                await self.db.execute(
                    select(NotificationSetting).where(
                        or_(
                            NotificationSetting.email_enabled.is_(True),
                            NotificationSetting.slack_enabled.is_(True),
                            NotificationSetting.pagerduty_enabled.is_(True),
                        )
                    )
                )
            ).scalars()
        }
        # Tenants whose only destinations are policy ones (a Teams webhook, own recipients).
        policy_tenants = set(
            (await self.db.execute(
                select(NotificationPolicy.tenant_id).where(NotificationPolicy.enabled.is_(True)).distinct()
            )).scalars()
        )
        for tenant_id in policy_tenants - set(settings):
            settings[tenant_id] = (
                await self.db.execute(select(NotificationSetting).where(NotificationSetting.tenant_id == tenant_id))
            ).scalar_one_or_none()
        totals = {"sent": 0, "failed": 0}
        for tenant_id, setting in settings.items():
            counts = await self.dispatch_tenant(setting, tenant_id=tenant_id)
            await self.db.commit()
            for k in totals:
                totals[k] += counts[k]
        return totals

    def _probe(self, tenant_id: uuid.UUID) -> Incident:
        return Incident(
            id=uuid.uuid4(),
            tenant_id=tenant_id, title="Test notification from HSDEEP CORE AI",
            severity="warning", status="open", opened_at=datetime.now(timezone.utc),
            last_alert_at=datetime.now(timezone.utc),
        )

    async def send_test(self, setting: NotificationSetting) -> dict[str, str]:
        """Send a clearly labelled test message to each enabled channel now.
        Nothing is recorded in incident_notifications."""
        results: dict[str, str] = {}
        probe = self._probe(setting.tenant_id)
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

    async def send_policy_test(self, policy: NotificationPolicy, setting: NotificationSetting | None) -> dict[str, str]:
        """A test message to one policy's destinations (its own and the tenant channels it names)."""
        results: dict[str, str] = {}
        probe = self._probe(policy.tenant_id)
        test_policy = NotificationPolicy(
            id=policy.id, tenant_id=policy.tenant_id, name=policy.name, enabled=True, min_severity="info",
            priorities=[], site_ids=[], channels=policy.channels, email_recipients=policy.email_recipients,
            teams_webhook_encrypted=policy.teams_webhook_encrypted, created_at=probe.opened_at,
        )
        for dest in self.destinations(setting, [test_policy], probe.opened_at):
            name = dest.key.split(":")[0]
            try:
                await dest.send(probe, "opened")
                if name == "pagerduty":
                    await dest.send(probe, "resolved")
                results[name] = "queued" if name == "email" else "sent"
            except Exception as exc:  # noqa: BLE001
                results[name] = f"failed: {exc}"[:300]
        named = set(policy.channels or []) - {k.split(":")[0] for k in results}
        for channel in named:
            results[channel] = "not enabled for the organization"
        return results
