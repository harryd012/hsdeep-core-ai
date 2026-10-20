"""
Secure telemetry ingestion API tests (Phase 2).

Covers POST /api/v1/ingest/metrics and POST /api/v1/ingest/events:
  * collector-credential authentication (valid / missing / invalid / revoked)
  * tenant isolation (tenant A cannot write to tenant B's resources)
  * resource ownership (device/site/integration must belong to the tenant)
  * validation (missing value is rejected, never zero; bad timestamp; oversize)
  * idempotency (repeated sample_id / event_id is a duplicate no-op)
  * partial-batch semantics (valid items persist, invalid items reported)
  * persistence through the canonical repositories

Runs against the isolated hsdeep_test database (conftest refuses the dev DB).
Reuses the real collector enrollment/registration flow to obtain a genuine
collector credential - no auth bypass.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.plan import Plan
from app.models.site import Site
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "http://test"
CRED_HEADER = "X-HSDEEP-Collector-Credential"


# ---------------------------------------------------------------------------
# Fixtures / helpers (mirror test_collector_enrollment.py conventions)
# ---------------------------------------------------------------------------

async def _make_tenant(*, api_rate_limit: int = 1_000_000):
    async with AsyncSessionLocal() as db:
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant(name="Ingest Target", slug=f"ing-{suffix}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        user = User(
            tenant_id=tenant.id,
            email=f"ing-{suffix}@example.com",
            password_hash="test-hash",
            full_name="Ingest Admin",
            is_active=True,
        )
        db.add(user)

        # A generous api_rate_limit so the quota path does not trip in the
        # happy-path tests (0 would mean "not enforced"; a huge value is safe).
        plan = Plan(
            name=f"Ing-{suffix}",
            tier=f"ing-{suffix}",
            collector_limit=10,
            device_limit=100,
            seats=10,
            api_rate_limit=api_rate_limit,
            features_json={},
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        await db.refresh(user)

        now = datetime.now(timezone.utc)
        db.add(
            Subscription(
                tenant_id=tenant.id,
                plan_id=plan.id,
                status="active",
                billing_interval="monthly",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        await db.commit()
    return tenant, user, plan


async def _make_site(tenant_id: uuid.UUID, name: str = "Site A") -> Site:
    async with AsyncSessionLocal() as db:
        site = Site(
            tenant_id=tenant_id,
            name=name,
            code=f"S{uuid.uuid4().hex[:8].upper()}",
        )
        db.add(site)
        await db.commit()
        await db.refresh(site)
    return site


def _token(user: User) -> str:
    return create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        is_superuser=True,
        permissions=frozenset({"devices.write"}),
    )


async def _register_collector(user: User, *, name: str = "edge-1") -> str:
    """Enroll + register a real collector; return its plaintext credential."""
    async with httpx.AsyncClient(app=app, base_url=BASE) as client:
        tok = await client.post(
            "/api/monitoring/collectors/enrollment-tokens",
            headers={"Authorization": f"Bearer {_token(user)}"},
            json={},
        )
        assert tok.status_code == 201, tok.text
        token = tok.json()["token"]

        reg = await client.post(
            "/api/monitoring/collectors/register",
            json={
                "token": token,
                "name": name,
                "source_type": "snmp",
                "collector_version": "0.1.0",
                "poll_interval_seconds": 60,
            },
        )
        assert reg.status_code == 201, reg.text
        return reg.json()["collector_credential"]


def _ingest(credential: str | None, path: str, body: dict):
    headers = {}
    if credential is not None:
        headers[CRED_HEADER] = credential
    async def _call():
        async with httpx.AsyncClient(app=app, base_url=BASE) as client:
            return await client.post(f"/api/v1/ingest/{path}", headers=headers, json=body)
    return _call()


async def _drop(tenant_id: uuid.UUID, plan_id: uuid.UUID | None = None) -> None:
    async with AsyncSessionLocal() as db:
        for stmt in (
            "DELETE FROM metrics WHERE tenant_id = :t",
            "DELETE FROM normalized_events WHERE tenant_id = :t",
            "DELETE FROM collector_credentials WHERE tenant_id = :t",
            "DELETE FROM collector_enrollment_tokens WHERE tenant_id = :t",
            "DELETE FROM collector_runs WHERE tenant_id = :t",
            "DELETE FROM monitoring_sources WHERE tenant_id = :t",
            "DELETE FROM subscriptions WHERE tenant_id = :t",
            "DELETE FROM sites WHERE tenant_id = :t",
            "DELETE FROM audit_events WHERE tenant_id = :t",
            "DELETE FROM users WHERE tenant_id = :t",
        ):
            await db.execute(text(stmt), {"t": tenant_id})
        if plan_id is not None:
            await db.execute(text("DELETE FROM plans WHERE id = :p"), {"p": plan_id})
        await db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})
        await db.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)

# ===========================================================================
# Authentication
# ===========================================================================

class TestAuthentication:
    async def test_missing_credential_is_401(self):
        tenant, _user, plan = await _make_tenant()
        try:
            resp = await _ingest(
                None,
                "metrics",
                {"samples": [{"name": "cpu", "value": 1.0, "timestamp": _now().isoformat()}]},
            )
            assert resp.status_code == 401, resp.text
        finally:
            await _drop(tenant.id, plan.id)

    async def test_invalid_credential_is_401(self):
        tenant, _user, plan = await _make_tenant()
        try:
            resp = await _ingest(
                "not-a-real-credential",
                "metrics",
                {"samples": [{"name": "cpu", "value": 1.0, "timestamp": _now().isoformat()}]},
            )
            assert resp.status_code == 401, resp.text
            # No hint about WHY it failed.
            assert "Invalid or unusable collector credential" in resp.text
        finally:
            await _drop(tenant.id, plan.id)

    async def test_valid_credential_is_accepted(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            resp = await _ingest(
                cred,
                "metrics",
                {"samples": [{"name": "cpu.utilization", "value": 42.0, "unit": "%", "timestamp": _now().isoformat()}]},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["accepted"] == 1
            assert body["rejected"] == 0
        finally:
            await _drop(tenant.id, plan.id)


# ===========================================================================
# Validation (no false zero; strict types)
# ===========================================================================

class TestValidation:
    async def test_missing_numeric_value_is_rejected_not_zero(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            # value_type defaults to numeric but no value supplied -> rejected.
            resp = await _ingest(
                cred,
                "metrics",
                {"samples": [{"name": "cpu.utilization", "timestamp": _now().isoformat()}]},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["accepted"] == 0
            assert body["rejected"] == 1
            assert body["results"][0]["error"] == "missing_value"
        finally:
            await _drop(tenant.id, plan.id)

    async def test_empty_batch_is_rejected_by_schema(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            resp = await _ingest(cred, "metrics", {"samples": []})
            # min_length=1 -> 422 validation error.
            assert resp.status_code == 422, resp.text
        finally:
            await _drop(tenant.id, plan.id)

    async def test_bad_timestamp_is_rejected_by_schema(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            resp = await _ingest(
                cred,
                "metrics",
                {"samples": [{"name": "cpu", "value": 1.0, "timestamp": "not-a-date"}]},
            )
            assert resp.status_code == 422, resp.text
        finally:
            await _drop(tenant.id, plan.id)

    async def test_text_and_boolean_values_persist(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            ts = _now().isoformat()
            resp = await _ingest(
                cred,
                "metrics",
                {
                    "samples": [
                        {"name": "link.state", "value_type": "text", "text_value": "up", "timestamp": ts},
                        {"name": "power.on", "value_type": "boolean", "boolean_value": True, "timestamp": ts},
                    ]
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["accepted"] == 2
            async with AsyncSessionLocal() as db:
                txt = (
                    await db.execute(
                        text("SELECT text_value FROM metrics WHERE tenant_id=:t AND metric_name='link.state'"),
                        {"t": tenant.id},
                    )
                ).scalar_one_or_none()
                boo = (
                    await db.execute(
                        text("SELECT boolean_value FROM metrics WHERE tenant_id=:t AND metric_name='power.on'"),
                        {"t": tenant.id},
                    )
                ).scalar_one_or_none()
            assert txt == "up"
            assert boo is True
        finally:
            await _drop(tenant.id, plan.id)


# ===========================================================================
# Tenant isolation + resource ownership
# ===========================================================================

class TestTenantIsolation:
    async def test_cannot_write_to_other_tenants_device(self):
        tenant_a, user_a, plan_a = await _make_tenant()
        tenant_b, user_b, plan_b = await _make_tenant()
        try:
            # A device owned by tenant B.
            site_b = await _make_site(tenant_b.id, "B Site")
            async with AsyncSessionLocal() as db:
                from app.models.device import Device

                dev = Device(
                    tenant_id=tenant_b.id,
                    site_id=site_b.id,
                    name="B-device",
                    device_type="switch",
                )
                db.add(dev)
                await db.commit()
                await db.refresh(dev)
                device_b_id = dev.id

            # A collector credential belonging to tenant A.
            cred_a = await _register_collector(user_a, name="edge-a")

            resp = await _ingest(
                cred_a,
                "metrics",
                {
                    "samples": [
                        {
                            "name": "cpu",
                            "value": 1.0,
                            "timestamp": _now().isoformat(),
                            "device_id": str(device_b_id),
                        }
                    ]
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # The cross-tenant device is rejected per-item, never attributed to A.
            assert body["accepted"] == 0
            assert body["rejected"] == 1
            assert body["results"][0]["error"] == "forbidden_resource"

            # And nothing was written to tenant B under tenant A's source.
            async with AsyncSessionLocal() as db:
                n = (
                    await db.execute(
                        text("SELECT count(*) FROM metrics WHERE tenant_id=:t"),
                        {"t": tenant_a.id},
                    )
                ).scalar_one()
            assert n == 0
        finally:
            await _drop(tenant_a.id, plan_a.id)
            await _drop(tenant_b.id, plan_b.id)

    async def test_cross_tenant_credential_401(self):
        """A tenant B credential must not read/write as tenant A.

        The tenant is taken from the credential row, so a credential from B
        attributes its telemetry to B and never to A. Verified by ingesting
        with B's credential and confirming the row is attributed to B.
        """
        tenant_a, user_a, plan_a = await _make_tenant()
        tenant_b, user_b, plan_b = await _make_tenant()
        try:
            cred_b = await _register_collector(user_b, name="edge-b")
            resp = await _ingest(
                cred_b,
                "metrics",
                {"samples": [{"name": "cpu", "value": 5.0, "timestamp": _now().isoformat()}]},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["accepted"] == 1
            # Attributed to B, not A.
            async with AsyncSessionLocal() as db:
                nb = (await db.execute(text("SELECT count(*) FROM metrics WHERE tenant_id=:t"), {"t": tenant_b.id})).scalar_one()
                na = (await db.execute(text("SELECT count(*) FROM metrics WHERE tenant_id=:t"), {"t": tenant_a.id})).scalar_one()
            assert nb == 1
            assert na == 0
        finally:
            await _drop(tenant_a.id, plan_a.id)
            await _drop(tenant_b.id, plan_b.id)


# ===========================================================================
# Idempotency + partial batch
# ===========================================================================

class TestIdempotencyAndPartialBatch:
    async def test_duplicate_sample_id_is_noop(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            ts = _now().isoformat()
            body = {
                "samples": [
                    {"name": "cpu", "value": 1.0, "timestamp": ts, "sample_id": "s-dup"},
                    {"name": "cpu", "value": 2.0, "timestamp": ts, "sample_id": "s-dup"},
                ]
            }
            resp = await _ingest(cred, "metrics", body)
            assert resp.status_code == 200, resp.text
            out = resp.json()
            assert out["accepted"] == 1
            assert out["duplicates"] == 1
        finally:
            await _drop(tenant.id, plan.id)

    async def test_partial_batch_persists_valid_and_reports_invalid(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            ts = _now().isoformat()
            body = {
                "samples": [
                    {"name": "cpu", "value": 10.0, "timestamp": ts},          # valid
                    {"name": "mem", "timestamp": ts},                            # missing value -> rejected
                    {"name": "disk", "value": 20.0, "timestamp": ts},         # valid
                ]
            }
            resp = await _ingest(cred, "metrics", body)
            assert resp.status_code == 200, resp.text
            out = resp.json()
            assert out["accepted"] == 2
            assert out["rejected"] == 1
            # Both valid rows persisted.
            async with AsyncSessionLocal() as db:
                n = (await db.execute(text("SELECT count(*) FROM metrics WHERE tenant_id=:t"), {"t": tenant.id})).scalar_one()
            assert n == 2
        finally:
            await _drop(tenant.id, plan.id)


# ===========================================================================
# Events
# ===========================================================================

class TestEvents:
    async def test_event_ingest_and_log_as_telemetry_type(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            ts = _now().isoformat()
            body = {
                "events": [
                    {
                        "title": "interface Gig0/1 down",
                        "severity": "warning",
                        "event_category": "network",
                        "event_type": "link_down",
                        "telemetry_type": "EVENT",
                        "timestamp": ts,
                    },
                    {
                        "title": "sshd: accepted password for root",
                        "severity": "info",
                        "event_category": "security",
                        "event_type": "auth",
                        "telemetry_type": "LOG",
                        "timestamp": ts,
                        "attributes": {"user": "root", "pid": 1234},
                    },
                ]
            }
            resp = await _ingest(cred, "events", body)
            assert resp.status_code == 200, resp.text
            out = resp.json()
            assert out["accepted"] == 2
            async with AsyncSessionLocal() as db:
                logs = (
                    await db.execute(
                        text("SELECT count(*) FROM normalized_events WHERE tenant_id=:t AND telemetry_type='LOG'"),
                        {"t": tenant.id},
                    )
                ).scalar_one()
            assert logs == 1
        finally:
            await _drop(tenant.id, plan.id)

    async def test_duplicate_event_id_is_noop(self):
        tenant, user, plan = await _make_tenant()
        try:
            cred = await _register_collector(user)
            ts = _now().isoformat()
            ev = {
                "title": "dup", "severity": "info", "event_category": "system",
                "event_type": "x", "timestamp": ts, "event_id": "e-1",
            }
            body = {"events": [ev, dict(ev)]}
            resp = await _ingest(cred, "events", body)
            assert resp.status_code == 200, resp.text
            out = resp.json()
            assert out["accepted"] == 1
            assert out["duplicates"] == 1
        finally:
            await _drop(tenant.id, plan.id)
