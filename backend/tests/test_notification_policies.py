"""Notification policies and the Microsoft Teams channel (observability plan P9 slice 1).

Teams webhook allow-list and card (pure); routing through policies (who gets
which incident, once, resolved only where opened, original behaviour without
policies); and the admin API: validation, the webhook stored encrypted and
returned masked, audit without the URL, tests that make no real calls, and
tenant isolation.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.core.security import SecretCrypto
from app.main import app
from app.models.notification_policy import NotificationPolicy
from app.models.site import Site
from app.services.notifications import dispatcher as dispatcher_module
from app.services.notifications.dispatcher import (
    NotificationDispatcher,
    is_allowed_teams_webhook,
    policy_matches,
    teams_card,
)
from tests.test_incident_notifications import NOW, SLACK_URL, FakePost, _incident, _rows, _setting

TEAMS_URL = "https://contoso.webhook.office.com/webhookb2/abc/IncomingWebhook/def/ghi"
WORKFLOW_URL = "https://prod-12.westeurope.logic.azure.com:443/workflows/xyz/triggers/manual/paths/invoke?sig=s3cr3t"


def test_teams_allow_list_card_and_matching():
    assert is_allowed_teams_webhook(TEAMS_URL) and is_allowed_teams_webhook(WORKFLOW_URL)
    assert is_allowed_teams_webhook("https://x.environment.api.powerplatform.com/powerautomate/automations/direct/workflows/1")
    for bad in ("http://contoso.webhook.office.com/x", "https://webhook.office.com.evil.example/x",
                "https://169.254.169.254/latest", "https://evilwebhook.office.com/x", None, ""):
        assert not is_allowed_teams_webhook(bad), bad

    class _I:
        id, severity, status, priority, title, site_id = uuid.uuid4(), "critical", "open", "P1", "Core down", None
        opened_at, resolved_at = NOW, None
    card = teams_card(_I(), "opened")
    content = card["attachments"][0]["content"]
    assert card["type"] == "message" and content["type"] == "AdaptiveCard"
    assert content["body"][0]["text"] == "[CRITICAL] Opened: Core down" and content["actions"][0]["type"] == "Action.OpenUrl"

    site = uuid.uuid4()
    p = NotificationPolicy(min_severity="warning", priorities=["P1"], site_ids=[str(site)])
    i = _I()
    assert not policy_matches(p, i)  # no site
    i.site_id = site
    assert policy_matches(p, i)
    i.priority = "P3"
    assert not policy_matches(p, i)
    assert not policy_matches(NotificationPolicy(min_severity="critical", priorities=[], site_ids=[]), type("W", (), {"severity": "warning", "priority": None, "site_id": None})())


async def _policy(db, tenant, **kw) -> NotificationPolicy:
    fields = dict(tenant_id=tenant.id, name=f"p-{uuid.uuid4().hex[:6]}", enabled=True, position=0,
                  min_severity="warning", priorities=[], site_ids=[], channels=[])
    teams = kw.pop("teams", None)
    fields.update(kw)
    p = NotificationPolicy(**fields)
    if teams:
        p.teams_webhook_encrypted, p.teams_webhook_hint = SecretCrypto().encrypt(teams), teams[-4:]
    db.add(p)
    await db.commit()
    p.created_at = NOW - timedelta(hours=1)
    await db.commit()
    return p


async def _tick(post, tenant):
    async with AsyncSessionLocal() as db:
        from app.models.notification_setting import NotificationSetting

        setting = (await db.execute(select(NotificationSetting).where(NotificationSetting.tenant_id == tenant.id))).scalar_one_or_none()
        counts = await NotificationDispatcher(db, post=post).dispatch_tenant(setting, tenant_id=tenant.id)
        await db.commit()
        return counts


async def test_policies_route_incidents_to_their_destinations(tenant, db):
    await _setting(db, tenant)  # email, Slack, PagerDuty (critical) all enabled
    site = Site(tenant_id=tenant.id, name="Branch", code=f"B{uuid.uuid4().hex[:6].upper()}")
    db.add(site)
    await db.commit()
    critical = await _policy(db, tenant, name="Critical to Teams", min_severity="critical", channels=["slack"], teams=TEAMS_URL)
    branch = await _policy(db, tenant, name="Branch warnings", site_ids=[str(site.id)], channels=["email"],
                           email_recipients="branch@hsdeep.local")

    core = await _incident(db, tenant, severity="critical")
    at_branch = await _incident(db, tenant, severity="warning")
    at_branch.site_id = site.id
    quiet = await _incident(db, tenant, severity="info")
    await db.commit()

    post = FakePost()
    await _tick(post, tenant)
    a, c = critical.id.hex[:12], branch.id.hex[:12]
    assert {k for k in await _rows(core.id)} == {("slack", "opened"), (f"teams:{a}", "opened")}
    assert {k for k in await _rows(at_branch.id)} == {(f"email:{c}", "opened")}
    assert await _rows(quiet.id) == {}
    urls = [u for u, _ in post.calls]
    assert urls.count(TEAMS_URL) == 1 and urls.count(SLACK_URL) == 1
    assert not any("pagerduty" in u for u in urls)  # no policy routes to PagerDuty

    # Once only, then resolved where it was opened.
    await _tick(post, tenant)
    assert len(post.calls) == 2
    core.status, core.resolved_at = "resolved", NOW
    await db.commit()
    await _tick(post, tenant)
    assert {k for k in await _rows(core.id)} >= {("slack", "resolved"), (f"teams:{a}", "resolved")}

    # Without enabled policies the original behaviour returns: PagerDuty gets critical incidents.
    for p in (critical, branch):
        p.enabled = False
    await db.commit()
    later = await _incident(db, tenant, severity="critical")
    await _tick(post, tenant)
    assert {k[0] for k in await _rows(later.id)} == {"email", "slack", "pagerduty"}


async def test_teams_only_tenant_without_settings_row(tenant, db):
    await _policy(db, tenant, name="Teams only", teams=WORKFLOW_URL)
    inc = await _incident(db, tenant, severity="warning")
    post = FakePost()
    async with AsyncSessionLocal() as s:
        await NotificationDispatcher(s, post=post).dispatch_all()
    assert [u for u, _ in post.calls if u == WORKFLOW_URL] and len(await _rows(inc.id)) == 1


def _client(tenant_id, admin=True):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="admin@hsdeep.local",
                                is_superuser=admin, permissions=frozenset())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


@pytest.mark.usefixtures("seeded_subscription")
async def test_policy_api_masks_encrypts_audits_and_isolates(tenant, db, monkeypatch):
    from app.models.knowledge_audit import AuditEvent

    url = "/api/settings/notifications/policies"
    async with _client(tenant.id, admin=False) as client:
        assert (await client.get(url)).status_code == 403
    async with _client(tenant.id) as client:
        assert (await client.post(url, json={"name": "x", "teams_webhook_url": "https://example.com/hook"})).status_code == 422
        assert (await client.post(url, json={"name": "x", "channels": ["fax"]})).status_code == 422
        assert (await client.post(url, json={"name": "x", "site_ids": [str(uuid.uuid4())]})).status_code == 422
        r = await client.post(url, json={"name": "On call", "min_severity": "critical", "priorities": ["P1", "P1"],
                                         "channels": ["pagerduty"], "teams_webhook_url": WORKFLOW_URL})
        assert r.status_code == 201, r.text
        p = r.json()
        assert (p["teams_webhook"], p["priorities"]) == ("••••" + WORKFLOW_URL[-4:], ["P1"])
        assert "s3cr3t" not in r.text
        assert (await client.post(url, json={"name": "On call"})).status_code == 409

        # A masked value keeps the stored webhook; "" removes it.
        kept = (await client.patch(f"{url}/{p['id']}", json={"teams_webhook_url": p["teams_webhook"], "enabled": False})).json()
        assert (kept["teams_webhook"], kept["enabled"]) == (p["teams_webhook"], False)
        row = (await db.execute(select(NotificationPolicy).where(NotificationPolicy.id == uuid.UUID(p["id"])))).scalar_one()
        assert row.teams_webhook_encrypted and WORKFLOW_URL not in row.teams_webhook_encrypted
        assert SecretCrypto().decrypt(row.teams_webhook_encrypted) == WORKFLOW_URL

        # The test endpoint posts through the dispatcher; no real call is made here.
        sent = []

        async def fake_post(target, payload):
            sent.append(target)
            return 200
        monkeypatch.setattr(dispatcher_module, "_http_post", fake_post)
        results = (await client.post(f"{url}/{p['id']}/test")).json()
        assert results["teams"] == "sent" and sent == [WORKFLOW_URL]
        assert results["pagerduty"] == "not enabled for the organization"

        removed = (await client.patch(f"{url}/{p['id']}", json={"teams_webhook_url": ""})).json()
        assert removed["teams_webhook"] is None
        assert [x["name"] for x in (await client.get(url)).json()] == ["On call"]

    async with _client(uuid.uuid4()) as client:
        assert (await client.get(url)).json() == []
        assert (await client.patch(f"{url}/{p['id']}", json={"name": "mine"})).status_code in (402, 404)

    async with _client(tenant.id) as client:
        assert (await client.delete(f"{url}/{p['id']}")).status_code == 204
    events = (await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant.id,
                                                        AuditEvent.entity_type == "notification_policy"))).scalars().all()
    assert [e.action for e in sorted(events, key=lambda e: e.created_at)] == [
        "notification_policy.created", "notification_policy.updated", "notification_policy.updated", "notification_policy.deleted"]
    assert all("s3cr3t" not in str(e.new_values) + str(e.old_values) for e in events)
