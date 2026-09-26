"""
Collector enrollment core tests.

Covers the secure trust path for one-time collector enrollment tokens:
creation, hash-only persistence, single-use, expiry, revocation, tenant
isolation, entitlement interaction, race safety and auditing.

Throwaway tenants/plans/sites are created and removed here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from app.core.auth import create_access_token, hash_refresh_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.plan import Plan
from app.models.site import Site
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "http://test"
COLLECTOR_LIMIT = 2


async def _make_tenant(*, collector_limit: int = COLLECTOR_LIMIT, subscribe: bool = True):
    """Tenant + admin user + hermetic plan/subscription. Returns (tenant, user, plan)."""
    async with AsyncSessionLocal() as db:
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant(name="Enroll Target", slug=f"enr-{suffix}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        user = User(
            tenant_id=tenant.id,
            email=f"enr-{suffix}@example.com",
            password_hash="test-hash",
            full_name="Enroll Admin",
            is_active=True,
        )
        db.add(user)

        plan = Plan(
            name=f"Enr-{suffix}",
            tier=f"enr-{suffix}",
            collector_limit=collector_limit,
            device_limit=50,
            seats=10,
            features_json={},
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        await db.refresh(user)

        if subscribe:
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
        # `sites.code` is NOT NULL, so it must be supplied explicitly.
        site = Site(
            tenant_id=tenant_id,
            name=name,
            code=f"S{uuid.uuid4().hex[:8].upper()}",
        )
        db.add(site)
        await db.commit()
        await db.refresh(site)
    return site


async def _drop(tenant_id: uuid.UUID, plan_id: uuid.UUID | None = None) -> None:
    async with AsyncSessionLocal() as db:
        for stmt in (
            "DELETE FROM collector_enrollment_tokens WHERE tenant_id = :t",
            "DELETE FROM subscriptions WHERE tenant_id = :t",
            "DELETE FROM monitoring_sources WHERE tenant_id = :t",
            "DELETE FROM sites WHERE tenant_id = :t",
            "DELETE FROM audit_events WHERE tenant_id = :t",
            "DELETE FROM users WHERE tenant_id = :t",
        ):
            await db.execute(text(stmt), {"t": tenant_id})
        if plan_id is not None:
            await db.execute(text("DELETE FROM plans WHERE id = :p"), {"p": plan_id})
        await db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})
        await db.commit()


def _token(user: User) -> str:
    return create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        is_superuser=True,
        permissions=frozenset({"devices.write"}),
    )


async def _create_token(user: User, **body):
    async with httpx.AsyncClient(app=app, base_url=BASE) as client:
        return await client.post(
            "/api/monitoring/collectors/enrollment-tokens",
            headers={"Authorization": f"Bearer {_token(user)}"},
            json=body or {},
        )


async def _register(**payload):
    """No Authorization header: the enrollment token IS the credential."""
    async with httpx.AsyncClient(app=app, base_url=BASE) as client:
        return await client.post("/api/monitoring/collectors/register", json=payload)


async def _source_count(tenant_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                text("SELECT count(*) FROM monitoring_sources WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        ).scalar_one()


async def _audit_rows(tenant_id: uuid.UUID, action: str) -> list[tuple]:
    async with AsyncSessionLocal() as db:
        return list(
            (
                await db.execute(
                    text(
                        "SELECT id, description, new_values FROM audit_events "
                        "WHERE tenant_id = :t AND action = :a"
                    ),
                    {"t": tenant_id, "a": action},
                )
            ).all()
        )


class TestTokenCreation:
    async def test_creation_returns_plaintext_once(self):
        tenant, user, plan = await _make_tenant()
        try:
            resp = await _create_token(user)
            assert resp.status_code == 201, resp.text
            body = resp.json()
            # 1 + 2: token created, plaintext returned exactly here.
            assert body["token"] and len(body["token"]) >= 16
            assert body["expires_at"]
            assert body["registration_url"] == "/api/monitoring/collectors/register"
            assert body["tenant_id"] == str(tenant.id)
            assert body["expires_in_seconds"] > 0
        finally:
            await _drop(tenant.id, plan.id)

    async def test_hash_persisted_not_plaintext(self):
        tenant, user, plan = await _make_tenant()
        try:
            plaintext = (await _create_token(user)).json()["token"]
            async with AsyncSessionLocal() as db:
                stored = (
                    await db.execute(
                        text(
                            "SELECT token_hash FROM collector_enrollment_tokens "
                            "WHERE tenant_id = :t"
                        ),
                        {"t": tenant.id},
                    )
                ).scalar_one()
            # 3: the hash is stored, and it is NOT the plaintext.
            assert stored != plaintext
            assert stored == hash_refresh_token(plaintext)
            assert plaintext not in stored
        finally:
            await _drop(tenant.id, plan.id)

    async def test_token_hash_never_returned(self):
        tenant, user, plan = await _make_tenant()
        try:
            body = (await _create_token(user)).json()
            assert "token_hash" not in body
        finally:
            await _drop(tenant.id, plan.id)

    async def test_creation_is_audited_without_plaintext(self):
        """15 - creation is audited, and the plaintext is never in the audit."""
        tenant, user, plan = await _make_tenant()
        try:
            plaintext = (await _create_token(user)).json()["token"]
            rows = await _audit_rows(tenant.id, "collector.enrollment_token_created")
            assert len(rows) == 1
            assert plaintext not in f"{rows[0][1]}{rows[0][2]}"
        finally:
            await _drop(tenant.id, plan.id)


class TestRegistration:
    async def test_successful_registration_creates_source_for_token_tenant(self):
        """4 - token validated, tenant from the token, MonitoringSource created."""
        tenant, user, plan = await _make_tenant()
        try:
            plaintext = (await _create_token(user)).json()["token"]
            resp = await _register(
                token=plaintext,
                name="Enrolled Collector",
                source_type="prtg",
                hostname="prtg.example.com",
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            # Response contract: the existing MonitoringSource + the one-time
            # collector credential.
            assert body["source"]["tenant_id"] == str(tenant.id)
            assert body["source"]["name"] == "Enrolled Collector"
            assert await _source_count(tenant.id) == 1
            # 15: registration audited.
            assert len(await _audit_rows(tenant.id, "collector.registered")) == 1
        finally:
            await _drop(tenant.id, plan.id)

    async def test_token_is_single_use(self):
        """5 - the second registration is refused and creates nothing."""
        tenant, user, plan = await _make_tenant()
        try:
            plaintext = (await _create_token(user)).json()["token"]
            first = await _register(token=plaintext, name="C1", source_type="prtg")
            assert first.status_code == 201, first.text
            second = await _register(token=plaintext, name="C2", source_type="prtg")
            assert second.status_code in (401, 409)
            assert await _source_count(tenant.id) == 1
        finally:
            await _drop(tenant.id, plan.id)

    async def test_expired_token_rejected(self):
        """6."""
        tenant, user, plan = await _make_tenant()
        try:
            plaintext = (await _create_token(user)).json()["token"]
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text(
                        "UPDATE collector_enrollment_tokens SET expires_at = now() "
                        "- interval '1 hour' WHERE tenant_id = :t"
                    ),
                    {"t": tenant.id},
                )
                await db.commit()
            resp = await _register(token=plaintext, name="C", source_type="prtg")
            assert resp.status_code == 401
            assert await _source_count(tenant.id) == 0
        finally:
            await _drop(tenant.id, plan.id)

    async def test_revoked_token_rejected(self):
        """7."""
        tenant, user, plan = await _make_tenant()
        try:
            plaintext = (await _create_token(user)).json()["token"]
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text(
                        "UPDATE collector_enrollment_tokens SET revoked_at = now() "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": tenant.id},
                )
                await db.commit()
            resp = await _register(token=plaintext, name="C", source_type="prtg")
            assert resp.status_code == 401
            assert await _source_count(tenant.id) == 0
        finally:
            await _drop(tenant.id, plan.id)

    async def test_invalid_token_rejected_without_leaking_tenant(self):
        """8 - and the failure reveals nothing about any tenant."""
        resp = await _register(token="x" * 40, name="C", source_type="prtg")
        assert resp.status_code == 401
        assert "tenant" not in resp.text.lower()

    async def test_secrets_not_returned_in_registration_response(self):
        """16 - credential returned ONCE; everything else secret stays hidden.

        ALLOWED: the one-time collector credential, exactly once.
        FORBIDDEN: enrollment token, token hash, credential hash, PRTG
        credentials, passwords, API keys, encrypted secret material.
        """
        tenant, user, plan = await _make_tenant()
        try:
            enrollment_token = (await _create_token(user)).json()["token"]
            resp = await _register(
                token=enrollment_token,
                name="Secret Check",
                source_type="prtg",
                # Write-only PRTG credentials: must never be echoed back.
                api_key="prtg-api-key-DO-NOT-ECHO",
                password="prtg-password-DO-NOT-ECHO",
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()

            # ALLOWED - the response contract, and nothing else at top level.
            assert set(body) == {"source", "collector_credential"}
            assert body["collector_credential"]

            # FORBIDDEN - by exact field name anywhere in the response.
            for forbidden_key in (
                "token_hash",
                "credential_hash",
                "api_key",
                "api_key_encrypted",
                "password",
                "password_encrypted",
                "password_hash",
                "secret",
            ):
                assert f'"{forbidden_key}"' not in resp.text, forbidden_key

            # FORBIDDEN - by value.
            assert enrollment_token not in resp.text
            assert "prtg-api-key-DO-NOT-ECHO" not in resp.text
            assert "prtg-password-DO-NOT-ECHO" not in resp.text
        finally:
            await _drop(tenant.id, plan.id)


async def _credential_rows(tenant_id: uuid.UUID) -> list[tuple]:
    async with AsyncSessionLocal() as db:
        return list(
            (
                await db.execute(
                    text(
                        "SELECT source_id, credential_hash, collector_version, revoked_at "
                        "FROM collector_credentials WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                )
            ).all()
        )


class TestCollectorCredential:
    """The credential minted atomically with registration."""

    async def test_credential_returned_once_and_hash_persisted(self):
        """1 + 2 + 3 - returned once; only the hash is persisted."""
        tenant, user, plan = await _make_tenant()
        try:
            enrollment_token = (await _create_token(user)).json()["token"]
            resp = await _register(
                token=enrollment_token,
                name="Cred Collector",
                source_type="prtg",
                collector_version="1.2.3",
            )
            assert resp.status_code == 201, resp.text
            credential = resp.json()["collector_credential"]
            # 1: the credential is returned on successful registration.
            assert credential and len(credential) >= 16

            rows = await _credential_rows(tenant.id)
            assert len(rows) == 1
            source_id, stored_hash, version, revoked_at = rows[0]
            # 2: the plaintext credential is NOT in the database.
            assert stored_hash != credential
            assert credential not in stored_hash
            # 3: the hash IS persisted and matches the returned credential.
            assert stored_hash == hash_refresh_token(credential)
            assert version == "1.2.3"
            assert revoked_at is None
        finally:
            await _drop(tenant.id, plan.id)

    async def test_credential_belongs_to_new_source_and_token_tenant(self):
        """6 + 7 - bound to the newly created source and the token's tenant."""
        tenant, user, plan = await _make_tenant()
        try:
            enrollment_token = (await _create_token(user)).json()["token"]
            body = (
                await _register(token=enrollment_token, name="Bound", source_type="prtg")
            ).json()
            rows = await _credential_rows(tenant.id)
            assert len(rows) == 1
            # 6: the credential's source is the MonitoringSource just created.
            assert str(rows[0][0]) == body["source"]["id"]
        finally:
            await _drop(tenant.id, plan.id)

    async def test_enrollment_token_not_returned(self):
        """4 - the enrollment token never comes back in the response."""
        tenant, user, plan = await _make_tenant()
        try:
            enrollment_token = (await _create_token(user)).json()["token"]
            resp = await _register(token=enrollment_token, name="No Echo", source_type="prtg")
            assert resp.status_code == 201, resp.text
            assert enrollment_token not in resp.text
            # And the credential is a DIFFERENT secret, not the token reused.
            assert resp.json()["collector_credential"] != enrollment_token
        finally:
            await _drop(tenant.id, plan.id)

    async def test_prtg_credentials_not_returned(self):
        """5 - submitted PRTG credentials are never echoed back."""
        tenant, user, plan = await _make_tenant()
        try:
            enrollment_token = (await _create_token(user)).json()["token"]
            resp = await _register(
                token=enrollment_token,
                name="PRTG No Echo",
                source_type="prtg",
                hostname="prtg.internal.example",
                api_key="api-key-SHOULD-NOT-ECHO",
                username="prtg-user",
                password="pw-SHOULD-NOT-ECHO",
            )
            assert resp.status_code == 201, resp.text
            assert "api-key-SHOULD-NOT-ECHO" not in resp.text
            assert "pw-SHOULD-NOT-ECHO" not in resp.text
            # The non-secret hostname IS expected in the source block.
            assert resp.json()["source"]["hostname"] == "prtg.internal.example"
        finally:
            await _drop(tenant.id, plan.id)

    async def test_failed_registration_leaves_no_credential(self):
        """8 - a rejected registration leaves no CollectorCredential behind."""
        tenant, user, plan = await _make_tenant()
        try:
            enrollment_token = (await _create_token(user)).json()["token"]
            bad = await _register(
                token=enrollment_token, name="Bad", source_type="not-a-real-collector"
            )
            assert bad.status_code == 422, bad.text
            assert await _credential_rows(tenant.id) == []
            # The token survived, so the retry mints exactly one credential.
            good = await _register(token=enrollment_token, name="Good", source_type="prtg")
            assert good.status_code == 201, good.text
            assert len(await _credential_rows(tenant.id)) == 1
        finally:
            await _drop(tenant.id, plan.id)

    async def test_successful_registration_consumes_token_exactly_once(self):
        """9 - one use recorded, and re-use is refused with no duplicate minted."""
        tenant, user, plan = await _make_tenant()
        try:
            enrollment_token = (await _create_token(user)).json()["token"]
            first = await _register(token=enrollment_token, name="Once", source_type="prtg")
            assert first.status_code == 201, first.text

            async with AsyncSessionLocal() as db:
                used = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM collector_enrollment_tokens "
                            "WHERE tenant_id = :t AND used_at IS NOT NULL"
                        ),
                        {"t": tenant.id},
                    )
                ).scalar_one()
            assert used == 1

            second = await _register(token=enrollment_token, name="Twice", source_type="prtg")
            assert second.status_code in (401, 409), second.text
            # Still exactly one credential - no duplicate minted.
            assert len(await _credential_rows(tenant.id)) == 1
        finally:
            await _drop(tenant.id, plan.id)


class TestIsolationAndEntitlement:
    async def test_cross_tenant_token_registers_under_token_tenant_only(self):
        """9 + 10 - tenant comes from the token; a caller cannot override it."""
        tenant_a, user_a, plan_a = await _make_tenant()
        tenant_b, user_b, plan_b = await _make_tenant()
        try:
            plaintext = (await _create_token(user_a)).json()["token"]
            # tenant_id is not a field of the payload; injecting one must have
            # no effect at all.
            resp = await _register(
                token=plaintext,
                name="Cross",
                source_type="prtg",
                tenant_id=str(tenant_b.id),
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["source"]["tenant_id"] == str(tenant_a.id)
            assert await _source_count(tenant_a.id) == 1
            assert await _source_count(tenant_b.id) == 0
        finally:
            await _drop(tenant_a.id, plan_a.id)
            await _drop(tenant_b.id, plan_b.id)

    async def test_cross_tenant_site_rejected(self):
        """11 - another tenant's site cannot be attached."""
        tenant_a, user_a, plan_a = await _make_tenant()
        tenant_b, user_b, plan_b = await _make_tenant()
        try:
            site_b = await _make_site(tenant_b.id, "B Site")
            plaintext = (await _create_token(user_a)).json()["token"]
            resp = await _register(
                token=plaintext,
                name="Cross Site",
                source_type="prtg",
                site_id=str(site_b.id),
            )
            assert resp.status_code == 422, resp.text
            assert await _source_count(tenant_a.id) == 0
            # The rejected attempt did NOT consume the token.
            ok = await _register(token=plaintext, name="Retry", source_type="prtg")
            assert ok.status_code == 201, ok.text
        finally:
            await _drop(tenant_a.id, plan_a.id)
            await _drop(tenant_b.id, plan_b.id)

    async def test_entitlement_rejection_does_not_consume_token(self):
        """12 + 13 - no collector capacity -> 402, token stays unused."""
        tenant, user, plan = await _make_tenant(collector_limit=0)
        try:
            plaintext = (await _create_token(user)).json()["token"]
            resp = await _register(token=plaintext, name="Over", source_type="prtg")
            assert resp.status_code == 402, resp.text
            assert resp.json()["detail"]["error"] == "plan_limit_exceeded"
            assert resp.json()["detail"]["resource"] == "collectors"
            assert await _source_count(tenant.id) == 0

            async with AsyncSessionLocal() as db:
                used_at = (
                    await db.execute(
                        text(
                            "SELECT used_at FROM collector_enrollment_tokens "
                            "WHERE tenant_id = :t"
                        ),
                        {"t": tenant.id},
                    )
                ).scalar_one()
            assert used_at is None
        finally:
            await _drop(tenant.id, plan.id)

    async def test_failed_registration_does_not_consume_token(self):
        """13 - an unknown source_type leaves the token intact for retry."""
        tenant, user, plan = await _make_tenant()
        try:
            plaintext = (await _create_token(user)).json()["token"]
            bad = await _register(
                token=plaintext, name="Bad Type", source_type="not-a-real-collector"
            )
            assert bad.status_code == 422, bad.text
            assert await _source_count(tenant.id) == 0
            # Retry succeeds -> the token survived the failure.
            good = await _register(token=plaintext, name="Good", source_type="prtg")
            assert good.status_code == 201, good.text
            assert await _source_count(tenant.id) == 1
        finally:
            await _drop(tenant.id, plan.id)

    async def test_double_registration_creates_exactly_one_collector(self):
        """14 - two sequential uses of one token yield exactly one collector."""
        tenant, user, plan = await _make_tenant()
        try:
            plaintext = (await _create_token(user)).json()["token"]
            first = await _register(token=plaintext, name="Race 1", source_type="prtg")
            second = await _register(token=plaintext, name="Race 2", source_type="prtg")
            assert first.status_code == 201, first.text
            assert second.status_code in (401, 409), second.text
            # Exactly one MonitoringSource, and only one use recorded.
            assert await _source_count(tenant.id) == 1
            async with AsyncSessionLocal() as db:
                used = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM collector_enrollment_tokens "
                            "WHERE tenant_id = :t AND used_at IS NOT NULL"
                        ),
                        {"t": tenant.id},
                    )
                ).scalar_one()
            assert used == 1
        finally:
            await _drop(tenant.id, plan.id)
