"""Enterprise security programme S5 (part A): redaction, AI data use and retention.

Redaction of credentials (and optionally emails / IPs) in text and log lines;
the AI gateway refusing a tenant that turned AI off and redacting every prompt;
the privacy settings API (privacy.manage, validation, audit); per-tenant
retention overriding the platform default in both directions.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.ai.schemas import AIMessage, AIRequest, AIResponse
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.core.redaction import RedactingFilter, redact
from app.core.tenant_context import current_tenant_id
from app.main import app
from app.models.event import Event
from app.models.knowledge_audit import AuditEvent
from app.models.metric import Metric
from app.models.tenant import Tenant
from app.models.tenant_privacy import TenantPrivacySetting

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,secret", [
    ("login password=Hunter2! user=bob", "Hunter2!"),
    ('{"api_key": "abc123XYZ", "name": "x"}', "abc123XYZ"),
    ("Authorization: Bearer abcdefghijklmnop.qrs", "abcdefghijklmnop"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_abcdefg", "eyJzdWIiOiIxIn0"),  # secret-scan: allow (fake redaction fixture)
    ("postgresql://app:s3cretpw@db:5432/x", "s3cretpw"),
    ("snmp community: public2024", "public2024"),
    ("key AKIAABCDEFGHIJKLMNOP used", "AKIAABCDEFGHIJKLMNOP"),  # secret-scan: allow (fake redaction fixture)
    ("card 4111 1111 1111 1111 paid", "4111 1111 1111 1111"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIBOg\n-----END RSA PRIVATE KEY-----", "MIIBOg"),  # secret-scan: allow (fake redaction fixture)
])
def test_redact_removes_credentials(text, secret):
    assert secret not in redact(text)


def test_redact_keeps_operational_text_and_optional_pii():
    line = "device sw-core-01 Gi0/1 down cpu 93% ts 1699999999999 jane@corp.test 10.0.0.5"
    assert redact(line) == line
    masked = redact(line, emails=True, ips=True)
    assert "[EMAIL]" in masked and "[IP]" in masked and "1699999999999" in masked


def test_logging_filter_scrubs_messages():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "connecting with password=%s", ("TopSecret1",), None)
    assert RedactingFilter().filter(record) is True
    assert "TopSecret1" not in record.getMessage()


# ---------------------------------------------------------------------------
# AI gateway
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self):
        self.seen: list[AIRequest] = []

    async def chat(self, request):
        self.seen.append(request)
        return AIResponse(content="ok", model=request.model, request_id=request.request_id)


async def test_ai_gateway_respects_tenant_settings(tenant, monkeypatch):
    from app.ai import gateway as gw

    fake = _FakeProvider()
    monkeypatch.setattr(gw, "get_provider", lambda name: fake)
    gateway = gw.AIGateway()

    def req():
        return AIRequest(messages=[AIMessage(role="user", content="why is 10.1.1.1 down? admin is ops@corp.test, password=hunter22")])

    token = current_tenant_id.set(tenant.id)
    try:
        r = await gateway.chat(req())  # defaults: secrets and emails redacted, IPs kept
        assert r.content == "ok"
        sent = fake.seen[-1].messages[0].content
        assert "hunter22" not in sent and "ops@corp.test" not in sent and "10.1.1.1" in sent

        async with AsyncSessionLocal() as db:
            db.add(TenantPrivacySetting(tenant_id=tenant.id, ai_mode="on", ai_redact_emails=False, ai_redact_ips=True))
            await db.commit()
        await gateway.chat(req())
        sent = fake.seen[-1].messages[0].content
        assert "ops@corp.test" in sent and "10.1.1.1" not in sent and "hunter22" not in sent

        async with AsyncSessionLocal() as db:
            row = await db.get(TenantPrivacySetting, tenant.id)
            row.ai_mode = "off"
            await db.commit()
        calls = len(fake.seen)
        off = await gateway.chat(req())
        assert off.error == "ai_disabled_by_tenant" and off.content is None and len(fake.seen) == calls
    finally:
        current_tenant_id.reset(token)


# ---------------------------------------------------------------------------
# Privacy settings API
# ---------------------------------------------------------------------------


def _client(tenant_id, perms):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="owner@hsdeep.local",
                                is_superuser=False, permissions=perms)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


async def test_privacy_settings_api(tenant, seeded_subscription):
    async with _client(tenant.id, frozenset({"devices.read"})) as member:
        current = (await member.get("/api/settings/privacy")).json()
        assert current["is_default"] is True and current["ai_mode"] == "on" and current["ai_redact_emails"] is True
        assert (await member.put("/api/settings/privacy", json={"ai_mode": "off"})).status_code == 403
    async with _client(tenant.id, frozenset({"privacy.manage"})) as admin:
        for bad in ({"ai_mode": "maybe"}, {"metrics_retention_days": 3}, {"events_retention_days": 99999}):
            assert (await admin.put("/api/settings/privacy", json=bad)).status_code == 422, bad
        r = await admin.put("/api/settings/privacy", json={"ai_mode": "off", "metrics_retention_days": 90})
        assert r.status_code == 200 and r.json()["ai_mode"] == "off" and r.json()["metrics_retention_days"] == 90
        r = await admin.put("/api/settings/privacy", json={"metrics_retention_days": None})
        assert r.json()["metrics_retention_days"] is None and r.json()["ai_mode"] == "off"
    async with AsyncSessionLocal() as db:
        events = (await db.execute(select(AuditEvent).where(
            AuditEvent.tenant_id == tenant.id, AuditEvent.action == "privacy.settings_updated")
            .order_by(AuditEvent.created_at))).scalars().all()
        assert [e.new_values for e in events] == [{"ai_mode": "off", "metrics_retention_days": 90}, {"metrics_retention_days": None}]


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


async def test_tenant_retention_overrides_platform_default(tenant, monkeypatch):
    from app.scheduler import metric_jobs

    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.flush()
        for t in (tenant.id, other.id):
            for age in (5, 40, 400):
                db.add(Metric(tenant_id=t, metric_type="cpu", value=1.0, recorded_at=NOW - timedelta(days=age)))
                db.add(Event(tenant_id=t, event_type="x", message="m", occurred_at=NOW - timedelta(days=age)))
        # This tenant keeps metrics a year and raw events 30 days; the platform default is 30 days.
        db.add(TenantPrivacySetting(tenant_id=tenant.id, ai_mode="on", ai_redact_emails=True, ai_redact_ips=False,
                                    metrics_retention_days=365, events_retention_days=30))
        await db.commit()
    monkeypatch.setattr(metric_jobs, "get_settings", lambda: type("S", (), {"metric_retention_days": 30})())
    try:
        async with AsyncSessionLocal() as db:
            await metric_jobs.run_retention(db, now=NOW)

            async def ages(model, col, t):
                rows = (await db.execute(select(col).where(model.tenant_id == t))).scalars().all()
                return sorted(round((NOW - r).days) for r in rows)

            assert await ages(Metric, Metric.recorded_at, tenant.id) == [5, 40]      # its own 365 days
            assert await ages(Event, Event.occurred_at, tenant.id) == [5]           # its own 30 days
            assert await ages(Metric, Metric.recorded_at, other.id) == [5]          # platform default 30 days
            assert await ages(Event, Event.occurred_at, other.id) == [5, 40, 400]   # no event retention set
    finally:
        from sqlalchemy import delete

        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()
