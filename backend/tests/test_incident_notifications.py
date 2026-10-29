"""Incident notifications: delivery, idempotency, resolve pairing, safety."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.email_outbox import EmailOutbox
from app.models.incident_notification import IncidentNotification
from app.models.notification_setting import NotificationSetting
from app.repositories.incident_repository import IncidentRepository
from app.services.notifications.dispatcher import MAX_ATTEMPTS, PAGERDUTY_EVENTS_URL, NotificationDispatcher

SLACK_URL = "https://hooks.slack.com/services/T000/B000/XXXX"
NOW = datetime.now(timezone.utc)


class FakePost:
    def __init__(self, code: int = 200):
        self.code = code
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, url, payload):
        self.calls.append((url, payload))
        return self.code


async def _setting(db, tenant, **kw) -> NotificationSetting:
    fields = dict(
        tenant_id=tenant.id, email_enabled=True, email_recipients="noc@hsdeep.local",
        slack_enabled=True, slack_webhook_url=SLACK_URL,
        pagerduty_enabled=True, pagerduty_integration_key="pd-routing-key-1234", pagerduty_severity="critical",
    )
    fields.update(kw)
    row = NotificationSetting(**fields)
    db.add(row)
    await db.commit()
    # Created an hour ago, so incidents opened since then are in scope.
    row.created_at = NOW - timedelta(hours=1)
    await db.commit()
    return row


async def _incident(db, tenant, *, severity="critical", opened_at=None):
    opened = opened_at or NOW - timedelta(minutes=5)
    inc = await IncidentRepository(db).create(
        tenant_id=tenant.id, title="Core switch unreachable", status="open",
        severity=severity, opened_at=opened, last_alert_at=opened,
    )
    await db.commit()
    return inc


async def _tick(post: FakePost, tenant):
    async with AsyncSessionLocal() as db:
        setting = (await db.execute(select(NotificationSetting).where(NotificationSetting.tenant_id == tenant.id))).scalar_one()
        counts = await NotificationDispatcher(db, post=post).dispatch_tenant(setting)
        await db.commit()
        return counts


async def _rows(incident_id):
    async with AsyncSessionLocal() as db:
        return {
            (r.channel, r.event): r
            for r in (await db.execute(select(IncidentNotification).where(IncidentNotification.incident_id == incident_id))).scalars()
        }


async def test_opened_once_per_channel_then_resolved(tenant, db):
    await _setting(db, tenant)
    inc = await _incident(db, tenant)
    post = FakePost()

    assert await _tick(post, tenant) == {"sent": 3, "failed": 0}
    urls = [u for u, _ in post.calls]
    assert urls == [SLACK_URL, PAGERDUTY_EVENTS_URL]
    pd = post.calls[1][1]
    assert (pd["event_action"], pd["dedup_key"], pd["payload"]["severity"]) == ("trigger", str(inc.id), "critical")
    async with AsyncSessionLocal() as s:
        mails = (await s.execute(select(EmailOutbox).where(EmailOutbox.category == "incident_opened", EmailOutbox.tenant_id == tenant.id))).scalars().all()
    assert [m.to_address for m in mails] == ["noc@hsdeep.local"]

    # Idempotent: a second tick sends nothing new.
    assert await _tick(post, tenant) == {"sent": 0, "failed": 0}
    assert len(post.calls) == 2

    inc.resolved_at = NOW
    inc.status = "resolved"
    await db.commit()
    assert await _tick(post, tenant) == {"sent": 3, "failed": 0}
    resolve = post.calls[-1][1]
    assert (resolve["event_action"], resolve["dedup_key"]) == ("resolve", str(inc.id))
    assert {k for k, r in (await _rows(inc.id)).items() if r.status == "sent"} == {
        (c, e) for c in ("email", "slack", "pagerduty") for e in ("opened", "resolved")
    }


async def test_severity_thresholds_and_no_history_flood(tenant, db):
    await _setting(db, tenant)
    warning = await _incident(db, tenant, severity="warning")
    await _incident(db, tenant, severity="info")
    await _incident(db, tenant, opened_at=NOW - timedelta(hours=3))  # before the settings row
    post = FakePost()

    await _tick(post, tenant)
    # Only the warning incident: email + Slack (PagerDuty minimum is critical).
    assert set((await _rows(warning.id)).keys()) == {("email", "opened"), ("slack", "opened")}
    assert [u for u, _ in post.calls] == [SLACK_URL]


async def test_failures_are_recorded_and_retried_a_bounded_number_of_times(tenant, db):
    await _setting(db, tenant, email_enabled=False, pagerduty_enabled=False)
    inc = await _incident(db, tenant)
    post = FakePost(code=500)

    for _ in range(MAX_ATTEMPTS + 2):
        await _tick(post, tenant)
    row = (await _rows(inc.id))[("slack", "opened")]
    assert (row.status, row.attempts) == ("failed", MAX_ATTEMPTS)
    assert "HTTP 500" in row.last_error
    assert len(post.calls) == MAX_ATTEMPTS


async def test_stored_non_slack_url_is_never_called(tenant, db):
    """A legacy value pointing elsewhere (SSRF) fails without any request."""
    await _setting(db, tenant, email_enabled=False, pagerduty_enabled=False,
                   slack_webhook_url="http://169.254.169.254/latest/meta-data")
    inc = await _incident(db, tenant)
    post = FakePost()
    await _tick(post, tenant)
    assert post.calls == []
    assert "hooks.slack.com" in (await _rows(inc.id))[("slack", "opened")].last_error


# ---------------------------------------------------------------------------
# Settings API
# ---------------------------------------------------------------------------


def _client(tenant_id, *, superuser: bool):
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, email="admin@hsdeep.local",
        is_superuser=superuser, permissions=frozenset(),
    )
    return httpx.AsyncClient(app=app, base_url="http://test", headers={"Authorization": f"Bearer {token}"})


@pytest.mark.usefixtures("seeded_subscription")
async def test_settings_mask_secrets_and_reject_non_slack_urls(tenant, db):
    await _setting(db, tenant)
    async with _client(tenant.id, superuser=False) as viewer:
        got = (await viewer.get("/api/settings/notifications")).json()
    assert got["slack_webhook_url"] == "••••XXXX"
    assert got["pagerduty_integration_key"] == "••••1234"

    async with _client(tenant.id, superuser=True) as admin:
        # Saving the masked values back keeps the stored secrets.
        saved = await admin.put("/api/settings/notifications", json={**got, "slack_channel": "#noc"})
        assert saved.status_code == 200, saved.text
        bad = await admin.put("/api/settings/notifications", json={**got, "slack_webhook_url": "http://10.0.0.5/hook"})
        assert bad.status_code == 422
    async with AsyncSessionLocal() as s:
        stored = (await s.execute(select(NotificationSetting).where(NotificationSetting.tenant_id == tenant.id))).scalar_one()
    assert (stored.slack_webhook_url, stored.pagerduty_integration_key, stored.slack_channel) == (
        SLACK_URL, "pd-routing-key-1234", "#noc",
    )


@pytest.mark.usefixtures("seeded_subscription")
async def test_send_test_notification(tenant, db, monkeypatch):
    from app.services.notifications import dispatcher as dispatcher_module

    await _setting(db, tenant, email_enabled=False)
    post = FakePost()
    monkeypatch.setattr(dispatcher_module, "_http_post", post)
    async with _client(tenant.id, superuser=True) as admin:
        resp = await admin.post("/api/settings/notifications/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == {"slack": "sent", "pagerduty": "sent"}
    # PagerDuty test: trigger then resolve, so no page is left open.
    assert [p.get("event_action") for u, p in post.calls if u == PAGERDUTY_EVENTS_URL] == ["trigger", "resolve"]
    async with _client(tenant.id, superuser=False) as viewer:
        assert (await viewer.post("/api/settings/notifications/test")).status_code == 403


@pytest.mark.usefixtures("seeded_subscription")
async def test_deliveries_endpoint_is_tenant_scoped(tenant, db):
    await _setting(db, tenant, email_enabled=False, pagerduty_enabled=False)
    await _incident(db, tenant)
    await _tick(FakePost(), tenant)
    async with _client(tenant.id, superuser=False) as client:
        own = (await client.get("/api/settings/notifications/deliveries")).json()
    async with _client(uuid.uuid4(), superuser=False) as client:
        foreign = (await client.get("/api/settings/notifications/deliveries")).json()
    assert [(d["channel"], d["status"]) for d in own] == [("slack", "sent")]
    assert foreign == []
