"""Enterprise security programme S5 (part B): tenant data export and offboarding.

Export: privacy.manage, one at a time, the job writes a ZIP with a manifest and
one JSON-lines file per tenant table, credentials and sign-in internals left
out, download audited and tenant-scoped, expired files deleted. Offboarding:
Owner-only (tenant.delete), confirmed by name, scheduled 30 days out,
cancellable, and the purge removes the tenant with its data.
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update

from app.core import auth
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.device import Device
from app.models.knowledge_audit import AuditEvent
from app.models.tenant import Tenant
from app.models.tenant_export import TenantExport
from app.models.tenant_privacy import TenantPrivacySetting
from app.models.user import User
from app.services.offboarding import purge_due_tenants
from app.services.tenant_export import run_pending_exports


def _client(tenant_id, perms):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="owner@hsdeep.local",
                                is_superuser=False, permissions=perms)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


async def _actions(tenant_id, prefix):
    async with AsyncSessionLocal() as db:
        return sorted(e.action for e in (await db.execute(select(AuditEvent).where(
            AuditEvent.tenant_id == tenant_id, AuditEvent.action.like(f"{prefix}%")))).scalars())


async def test_export_contains_data_without_credentials(tenant, seeded_subscription, monitoring_source_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path))
    source = await monitoring_source_factory(source_type="prtg")
    async with AsyncSessionLocal() as db:
        db.add(User(tenant_id=tenant.id, email="exported@corp.test", password_hash=auth.hash_password("Secret-Pass-123")))
        db.add(Device(tenant_id=tenant.id, name="core-sw-01", device_type="switch"))
        await db.execute(update(type(source)).where(type(source).id == source.id).values(password_encrypted="ciphertext-xyz"))
        await db.commit()

    async with _client(tenant.id, frozenset({"devices.read"})) as member:
        assert (await member.post("/api/settings/privacy/exports")).status_code == 403
    async with _client(tenant.id, frozenset({"privacy.manage"})) as admin:
        r = await admin.post("/api/settings/privacy/exports")
        assert r.status_code == 202 and r.json()["status"] == "pending"
        export_id = r.json()["id"]
        assert (await admin.post("/api/settings/privacy/exports")).status_code == 409  # one at a time

        async with AsyncSessionLocal() as db:
            assert await run_pending_exports(db) >= 1
        [listed] = [e for e in (await admin.get("/api/settings/privacy/exports")).json() if e["id"] == export_id]
        assert listed["status"] == "ready" and listed["summary"]["devices"] == 1 and listed["size_bytes"] > 0

        dl = await admin.get(f"/api/settings/privacy/exports/{export_id}/download")
        assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
        zf = zipfile.ZipFile(io.BytesIO(dl.content))
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["tenant"]["id"] == str(tenant.id) and manifest["tables"]["devices"] == 1
        assert "password_hash" in manifest["omitted_columns"]["users"]
        assert "refresh_tokens" in manifest["omitted_tables"] and "data/refresh_tokens.jsonl" not in zf.namelist()
        users = [json.loads(line) for line in zf.read("data/users.jsonl").decode().splitlines()]
        assert any(u["email"] == "exported@corp.test" for u in users) and all("password_hash" not in u for u in users)
        sources = zf.read("data/monitoring_sources.jsonl").decode()
        assert "ciphertext-xyz" not in sources and "password_encrypted" not in sources
        assert "core-sw-01" in zf.read("data/devices.jsonl").decode()

    # Another tenant cannot download it.
    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
    try:
        async with _client(other.id, frozenset({"privacy.manage"})) as outsider:
            assert (await outsider.get(f"/api/settings/privacy/exports/{export_id}/download")).status_code == 404
    finally:
        async with AsyncSessionLocal() as db:
            await db.delete(await db.get(Tenant, other.id))
            await db.commit()

    # After its expiry the file is deleted.
    async with AsyncSessionLocal() as db:
        await db.execute(update(TenantExport).where(TenantExport.id == uuid.UUID(export_id))
                         .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
        await run_pending_exports(db)
        row = await db.get(TenantExport, uuid.UUID(export_id))
        assert row.status == "expired" and row.file_path is None and not list(tmp_path.glob("*.zip"))

    assert await _actions(tenant.id, "privacy.export") == ["privacy.export_downloaded", "privacy.export_requested"]


async def test_offboarding_schedule_cancel_and_purge(seeded_subscription, tenant):
    # Admin (privacy.manage, no tenant.delete) cannot request deletion.
    async with _client(tenant.id, frozenset({"privacy.manage"})) as admin:
        assert (await admin.post("/api/settings/privacy/deletion", json={"confirm_name": tenant.name})).status_code == 403
    async with _client(tenant.id, frozenset({"tenant.delete"})) as owner:
        assert (await owner.post("/api/settings/privacy/deletion", json={"confirm_name": "wrong"})).status_code == 422
        r = await owner.post("/api/settings/privacy/deletion", json={"confirm_name": f"  {tenant.name.upper()} "})
        assert r.status_code == 200
        scheduled = datetime.fromisoformat(r.json()["deletion_scheduled_for"])
        assert timedelta(days=29, hours=23) < scheduled - datetime.now(timezone.utc) <= timedelta(days=30)
        assert (await owner.post("/api/settings/privacy/deletion", json={"confirm_name": tenant.name})).status_code == 409
        assert (await owner.delete("/api/settings/privacy/deletion")).json()["deletion_scheduled_for"] is None
        assert (await owner.delete("/api/settings/privacy/deletion")).status_code == 404
        assert (await owner.post("/api/settings/privacy/deletion", json={"confirm_name": tenant.name})).status_code == 200
    assert await _actions(tenant.id, "tenant.deletion") == [
        "tenant.deletion_cancelled", "tenant.deletion_requested", "tenant.deletion_requested"]

    async with AsyncSessionLocal() as db:
        db.add(Device(tenant_id=tenant.id, name="to-be-purged", device_type="server"))
        await db.commit()
        # Not due yet: nothing happens.
        assert await purge_due_tenants(db) == []
        await db.execute(update(TenantPrivacySetting).where(TenantPrivacySetting.tenant_id == tenant.id)
                         .values(deletion_scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
        assert await purge_due_tenants(db) == [str(tenant.id)]
    async with AsyncSessionLocal() as db:
        assert await db.get(Tenant, tenant.id) is None
        assert (await db.execute(select(Device).where(Device.tenant_id == tenant.id))).first() is None
