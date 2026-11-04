"""Enterprise security programme S4: encryption key rotation and the
tamper-evident audit chain.

Keys: previous keys still decrypt, values are re-encrypted with the current key
(per tenant, JSON credential dicts included), unreadable values are reported
and left alone. Chain: AuditService links events at once, events written
directly are sealed later, verification finds a modified event, a deleted
event and an altered link, the export carries the chain head, and the
integrity API needs audit.read.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select, update

from app.core import auth
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.core.security import SecretCrypto
from app.main import app
from app.models.audit_chain import AuditChainLink
from app.models.knowledge_audit import AuditEvent
from app.models.user import User
from app.models.user_mfa import UserMfa
from app.services import audit_chain
from app.services.audit_service import AuditService
from app.services.key_rotation import encryption_status, rotate_encryption

OLD, NEW = "s4-old-key-0123456789abcdef", "s4-new-key-fedcba9876543210"


async def _user(tenant_id) -> User:
    async with AsyncSessionLocal() as db:
        u = User(tenant_id=tenant_id, email=f"s4-{uuid.uuid4().hex[:6]}@hsdeep.local", password_hash=auth.hash_password("x" * 12))
        db.add(u)
        await db.commit()
        return u


# ---------------------------------------------------------------------------
# Key rotation
# ---------------------------------------------------------------------------


async def test_rotation_reencrypts_with_the_current_key(tenant, monitoring_source_factory):
    old = SecretCrypto(OLD, previous=[])
    new = SecretCrypto(NEW, previous=[OLD])
    lost = SecretCrypto("a-key-nobody-has-anymore-000", previous=[])

    user = await _user(tenant.id)
    source = await monitoring_source_factory(source_type="prtg")
    async with AsyncSessionLocal() as db:
        db.add(UserMfa(user_id=user.id, tenant_id=tenant.id, secret_encrypted=old.encrypt("JBSWY3DPEHPK3PXP"),
                       confirmed_at=datetime.now(timezone.utc)))
        await db.execute(update(type(source)).where(type(source).id == source.id).values(
            api_key_encrypted=old.encrypt("api-key-1"), password_encrypted=lost.encrypt("pw")))
        await db.commit()

        before = {f.name: f for f in (await encryption_status(db, tenant.id, crypto=new)).fields}
        assert before["user_mfa.secret_encrypted"].previous == 1
        assert before["monitoring_sources.api_key_encrypted"].previous == 1
        assert before["monitoring_sources.password_encrypted"].unreadable == 1

        report = await rotate_encryption(db, tenant.id, crypto=new)
        assert report.needs_rotation == 2 and report.unreadable == 1
        assert sum(f.rotated for f in report.fields) == 2

    async with AsyncSessionLocal() as db:
        after = await encryption_status(db, tenant.id, crypto=new)
        assert after.needs_rotation == 0 and after.unreadable == 1
        mfa = await db.get(UserMfa, user.id)
        assert new.key_index(mfa.secret_encrypted) == 0 and new.decrypt(mfa.secret_encrypted) == "JBSWY3DPEHPK3PXP"
        # Only the current key is needed now.
        assert SecretCrypto(NEW, previous=[]).decrypt(mfa.secret_encrypted) == "JBSWY3DPEHPK3PXP"


def test_json_credentials_rotate_per_value():
    from app.services.key_rotation import ENCRYPTED_FIELDS

    assert any(f.kind == "json" for f in ENCRYPTED_FIELDS)
    new = SecretCrypto(NEW, previous=[OLD])
    token = SecretCrypto(OLD, previous=[]).encrypt("v")
    assert new.key_index(token) == 1 and new.key_index(new.rotate(token)) == 0
    assert new.fingerprints[0] != new.fingerprints[1] and len(new.fingerprints[0]) == 8


# ---------------------------------------------------------------------------
# Audit chain
# ---------------------------------------------------------------------------


async def _record(tenant_id, user, action):
    async with AsyncSessionLocal() as db:
        await AuditService(db).record_entity(tenant_id=tenant_id, action=action, entity_type="test", entity_id=None,
                                             actor_id=user.id, actor_email=user.email, description=action,
                                             new_values={"n": action})
        await db.commit()


async def test_chain_links_verifies_and_detects_tampering(tenant):
    user = await _user(tenant.id)
    for i in range(3):
        await _record(tenant.id, user, f"test.event_{i}")
    # Written directly (as some services do): unchained until sealed.
    async with AsyncSessionLocal() as db:
        direct = AuditEvent(tenant_id=tenant.id, action="test.direct", entity_type="test", description="direct")
        db.add(direct)
        await db.commit()

    async with AsyncSessionLocal() as db:
        r = await audit_chain.verify(db, tenant.id)
        assert r.status == "intact" and r.links_checked == 3 and r.unchained_events == 1
        assert await audit_chain.seal_unchained(db, tenant.id) == 1
        r = await audit_chain.verify(db, tenant.id)
        assert r.status == "intact" and r.links_checked == 4 and r.unchained_events == 0
        assert r.head["seq"] == 4 and len(r.head["hash"]) == 64
        links = (await db.execute(select(AuditChainLink).where(AuditChainLink.tenant_id == tenant.id)
                                  .order_by(AuditChainLink.seq))).scalars().all()
        assert links[0].prev_hash == audit_chain.GENESIS and all(b.prev_hash == a.hash for a, b in zip(links, links[1:]))

        # Someone edits an event in the database.
        await db.execute(update(AuditEvent).where(AuditEvent.id == links[1].event_id).values(description="edited"))
        await db.commit()
        r = await audit_chain.verify(db, tenant.id)
        assert r.status == "broken" and r.problems == [{"seq": 2, "event_id": str(links[1].event_id), "problem": "event_modified"}]

        # ...and deletes another.
        await db.execute(delete(AuditEvent).where(AuditEvent.id == links[2].event_id))
        await db.commit()
        r = await audit_chain.verify(db, tenant.id)
        assert [p["problem"] for p in r.problems] == ["event_modified", "event_deleted"]

        # Rewriting a link's hash breaks the link after it.
        await db.execute(update(AuditChainLink).where(AuditChainLink.event_id == links[0].event_id).values(hash="f" * 64))
        await db.commit()
        r = await audit_chain.verify(db, tenant.id)
        assert {"seq": 1, "event_id": str(links[0].event_id), "problem": "event_modified"} in r.problems
        assert any(p["seq"] == 2 and p["problem"] == "broken_link" for p in r.problems)


async def test_integrity_api_and_export_head(tenant, seeded_subscription):
    user = await _user(tenant.id)
    await _record(tenant.id, user, "test.api")

    def client(perms):
        token = create_access_token(user_id=user.id, tenant_id=tenant.id, email=user.email, is_superuser=False, permissions=perms)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": f"Bearer {token}"})

    async with client(frozenset({"devices.read"})) as viewer:
        assert (await viewer.get("/api/audit/integrity")).status_code == 403
    async with client(frozenset({"audit.read"})) as c:
        r = (await c.get("/api/audit/integrity")).json()
        assert r["status"] == "intact" and r["head"]["seq"] >= 1
        export = await c.get("/api/audit/events/export")
        seq, digest = export.headers["X-Audit-Chain-Head"].split(":")
        assert int(seq) >= r["head"]["seq"] and len(digest) == 64
        # The export itself was audited and chained.
        assert (await c.get("/api/audit/integrity")).json()["head"]["seq"] == r["head"]["seq"] + 1


async def test_default_check_covers_recent_links_full_check_everything(tenant, monkeypatch):
    user = await _user(tenant.id)
    for i in range(5):
        await _record(tenant.id, user, f"test.window_{i}")
    monkeypatch.setattr(audit_chain, "RECENT_LINKS", 2)
    async with AsyncSessionLocal() as db:
        first = (await db.execute(select(AuditChainLink).where(AuditChainLink.tenant_id == tenant.id, AuditChainLink.seq == 1))).scalar_one()
        await db.execute(update(AuditEvent).where(AuditEvent.id == first.event_id).values(description="old edit"))
        await db.commit()
        recent = await audit_chain.verify(db, tenant.id)
        assert (recent.scope, recent.from_seq, recent.links_checked, recent.status) == ("recent", 4, 2, "intact")
        full = await audit_chain.verify(db, tenant.id, full=True)
        assert full.scope == "full" and full.status == "broken" and full.problems[0]["seq"] == 1
