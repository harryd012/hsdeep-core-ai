"""Enterprise security programme S6: verified domains, OIDC single sign-on,
require-SSO and SCIM provisioning, against a fake identity provider (real RSA
keys, a JWKS document and a token endpoint served by httpx.MockTransport).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from sqlalchemy import select

from app.api.routes import auth as auth_routes
from app.core import auth
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.identity import SsoLoginState, TenantSsoConfig
from app.models.knowledge_audit import AuditEvent
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.role_repository import RoleRepository
from app.services.auth_service import AuthService
from app.services.identity import domains as dom
from app.services.identity import oidc

ISSUER = "https://idp.example.test"
CLIENT_ID = "hsdeep-client"
PASSWORD = "Correct-Horse-9-battery"
ADMIN = frozenset({"security.manage", "users.read"})


class FakeIdP:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public = jwk.construct(self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo), "RS256").to_dict()
        self.jwks = {"keys": [{**public, "kid": "k1", "use": "sig", "alg": "RS256"}]}
        self.claims: dict = {}
        self.token_requests: list[dict] = []

    def id_token(self, **overrides) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300, **self.claims, **overrides}
        return jwt.encode(claims, self.pem.decode(), algorithm="RS256", headers={"kid": "k1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                                             "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"})
        if url == f"{ISSUER}/jwks":
            return httpx.Response(200, json=self.jwks)
        if url == f"{ISSUER}/token":
            self.token_requests.append(dict(parse_qs(request.content.decode())))
            return httpx.Response(200, json={"id_token": self.id_token(), "access_token": "at", "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdP()
    monkeypatch.setattr(oidc, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    oidc.forget_cache()
    monkeypatch.setattr(auth_routes._auth_limiter, "limit", 10**9)
    yield fake
    oidc.forget_cache()


@pytest.fixture
def dns(monkeypatch):
    records: dict[str, list[str]] = {}

    async def lookup(name):
        return records.get(name, [])

    monkeypatch.setattr(dom, "lookup_txt", lookup)
    return records


def _client(tenant_id, perms=ADMIN, token=None):
    token = token or create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="admin@hsdeep.local",
                                         is_superuser=False, permissions=perms)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


def _anon():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def org(tenant, seeded_subscription, idp, dns):
    """A tenant with a verified domain and OIDC enabled; returns (tenant, domain)."""
    domain = f"corp-{uuid.uuid4().hex[:6]}.test"
    async with _client(tenant.id) as c:
        added = (await c.post("/api/settings/identity/domains", json={"domain": domain.upper()})).json()
        assert added["domain"] == domain and added["verified"] is False
        assert (await c.post(f"/api/settings/identity/domains/{added['id']}/verify")).status_code == 422
        dns[added["txt_name"]] = [added["txt_value"]]
        assert (await c.post(f"/api/settings/identity/domains/{added['id']}/verify")).json()["verified"] is True
        r = await c.put("/api/settings/identity/sso", json={"issuer": ISSUER + "/", "client_id": CLIENT_ID,
                                                             "client_secret": "s3cret", "enabled": True})
        assert r.status_code == 200, r.text
        body = r.json()["sso"]
        assert body["enabled"] and body["client_secret_set"] and "s3cret" not in r.text and body["issuer"] == ISSUER
    return tenant, domain


async def _sso_round_trip(start_email: str, idp: FakeIdP, *, sub: str = "sub-1", **claims) -> httpx.Response:
    email = start_email
    async with _anon() as anon:
        start = await anon.post("/api/auth/sso/start", json={"email": email, "next": "/alerts"})
        assert start.status_code == 200, start.text
        q = parse_qs(urlparse(start.json()["redirect_url"]).query)
        assert q["code_challenge_method"] == ["S256"] and q["login_hint"] == [email] and q["client_id"] == [CLIENT_ID]
        async with AsyncSessionLocal() as db:
            nonce = (await db.get(SsoLoginState, q["state"][0])).nonce
        idp.claims = {"sub": sub, "email": email, "email_verified": True, "nonce": nonce, **claims}
        return await anon.get("/api/auth/sso/oidc/callback", params={"code": "auth-code", "state": q["state"][0]})


def _redirect_params(resp: httpx.Response) -> dict:
    assert resp.status_code == 303
    loc = urlparse(resp.headers["location"])
    assert loc.path == "/sso/complete"
    return {k: v[0] for k, v in parse_qs(loc.query).items()}


# ---------------------------------------------------------------------------


def test_domain_normalization():
    assert dom.normalize_domain(" @Corp.Example.COM. ") == "corp.example.com"
    for bad in ("localhost", "-bad.com", "a..b", "x" * 70 + ".com"):
        with pytest.raises(dom.DomainError):
            dom.normalize_domain(bad)
    assert dom.email_domain("Jane@Corp.Test") == "corp.test"


async def test_sso_sign_in_existing_user_and_one_time_handoff(org, idp):
    tenant, domain = org
    async with AsyncSessionLocal() as db:
        db.add(User(tenant_id=tenant.id, email=f"jane@{domain}", password_hash=None))
        await db.commit()
    params = _redirect_params(await _sso_round_trip(f"Jane@{domain}".lower(), idp))
    assert params["next"] == "/alerts" and "error" not in params
    assert idp.token_requests[-1]["client_secret"] == ["s3cret"] and "code_verifier" in idp.token_requests[-1]
    async with _anon() as anon:
        tokens = await anon.post("/api/auth/sso/exchange", json={"code": params["code"]})
        assert tokens.status_code == 200 and tokens.json()["access_token"]
        assert (await anon.post("/api/auth/sso/exchange", json={"code": params["code"]})).status_code == 401
        me = await anon.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens.json()['access_token']}"})
        assert me.json()["email"] == f"jane@{domain}"
        assert (await anon.post("/api/auth/sso/start", json={"email": "x@unknown-domain.test"})).status_code == 404
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant.id, AuditEvent.action == "auth.sso_login"))).first()


async def test_unknown_user_jit_and_rejected_tokens(org, idp):
    tenant, domain = org
    # Unknown person without JIT: refused.
    assert "don't have an account" in _redirect_params(await _sso_round_trip(f"new@{domain}", idp))["error"]
    # A token for another audience, or a forged nonce, is refused.
    assert "not valid" in _redirect_params(await _sso_round_trip(f"x@{domain}", idp, aud="someone-else"))["error"]
    assert "does not belong" in _redirect_params(await _sso_round_trip(f"x@{domain}", idp, nonce="forged"))["error"]
    # The IdP vouching for an email outside the verified domains is refused at the callback.
    assert "not verified" in _redirect_params(await _sso_round_trip(f"x@{domain}", idp, email="x@other.test"))["error"]

    async with AsyncSessionLocal() as db:
        viewer = await RoleRepository(db).get_by_name(tenant.id, "Viewer")
    async with _client(tenant.id) as c:
        await c.put("/api/settings/identity/sso", json={"jit_provisioning": True, "default_role_id": str(viewer.id) if viewer else None})
    params = _redirect_params(await _sso_round_trip(f"new@{domain}", idp, sub="sub-new", name="New Person"))
    assert "code" in params
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.tenant_id == tenant.id, User.email == f"new@{domain}"))).scalar_one()
        assert user.full_name == "New Person" and user.password_hash is None


async def test_require_sso_blocks_password_sign_in_except_owners(org, idp):
    tenant, domain = org
    async with AsyncSessionLocal() as db:
        member = User(tenant_id=tenant.id, email=f"member@{domain}", password_hash=auth.hash_password(PASSWORD))
        owner = User(tenant_id=tenant.id, email=f"owner@{domain}", password_hash=auth.hash_password(PASSWORD))
        outsider = User(tenant_id=tenant.id, email="contractor@elsewhere.test", password_hash=auth.hash_password(PASSWORD))
        db.add_all([member, owner, outsider])
        await db.commit()
        svc = AuthService.__new__(AuthService)  # roles via the API helpers below
    from app.repositories.permission_repository import PermissionRepository
    from app.repositories.refresh_token_repository import RefreshTokenRepository
    from app.repositories.user_repository import UserRepository

    async with AsyncSessionLocal() as db:
        svc = AuthService(users=UserRepository(db), roles=RoleRepository(db), permissions=PermissionRepository(db),
                          refresh_tokens=RefreshTokenRepository(db))
        await svc.provision_tenant_rbac(tenant.id)
        owner_role = await RoleRepository(db).get_by_name(tenant.id, "Owner")
        await svc.assign_roles(tenant_id=tenant.id, user_id=owner.id, role_ids=[owner_role.id])
        await db.commit()

    async with _client(tenant.id) as c:
        assert (await c.put("/api/settings/identity/sso", json={"require_sso": True})).status_code == 200
    async with _anon() as anon:
        r = await anon.post("/api/auth/login", json={"email": member.email, "password": PASSWORD})
        assert r.status_code == 403 and r.json()["detail"].startswith("SSO_REQUIRED")
        assert (await anon.post("/api/auth/login", json={"email": member.email, "password": "wrong"})).status_code == 401
        assert (await anon.post("/api/auth/login", json={"email": owner.email, "password": PASSWORD})).status_code == 200
        assert (await anon.post("/api/auth/login", json={"email": outsider.email, "password": PASSWORD})).status_code == 200


async def test_domain_belongs_to_one_tenant(org, dns):
    _, domain = org
    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
    try:
        from app.models.subscription import Subscription  # noqa: F401 - writes need a subscription; use the service directly
        async with AsyncSessionLocal() as db:
            row = await dom.add_domain(db, other.id, domain)
            dns[f"{dom.CHALLENGE_PREFIX}.{domain}"] = [f"{dom.TOKEN_PREFIX}{row.verification_token}"]
            with pytest.raises(dom.DomainError, match="another organization"):
                await dom.verify_domain(db, row)
    finally:
        async with AsyncSessionLocal() as db:
            await db.delete(await db.get(Tenant, other.id))
            await db.commit()


# ---------------------------------------------------------------------------
# SCIM
# ---------------------------------------------------------------------------


async def test_scim_provisioning_lifecycle(org):
    tenant, domain = org
    async with _client(tenant.id) as c:
        token = (await c.post("/api/settings/identity/scim-token")).json()["token"]
    scim = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/scim/v2",
                             headers={"Authorization": f"Bearer {token}"})
    async with _anon() as anon:
        assert (await anon.get("/api/scim/v2/ServiceProviderConfig")).status_code == 401
    async with scim:
        assert (await scim.get("/ServiceProviderConfig")).json()["patch"]["supported"] is True
        created = await scim.post("/Users", json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                                                  "userName": f"Pat@{domain}", "externalId": "ext-42",
                                                  "name": {"givenName": "Pat", "familyName": "Lee"}, "active": True})
        assert created.status_code == 201 and created.headers["content-type"].startswith("application/scim+json")
        uid = created.json()["id"]
        assert created.json()["userName"] == f"pat@{domain}" and created.json()["externalId"] == "ext-42"
        assert (await scim.post("/Users", json={"userName": f"pat@{domain}"})).status_code == 409

        found = (await scim.get("/Users", params={"filter": f'userName eq "PAT@{domain}"'})).json()
        assert found["totalResults"] == 1 and found["Resources"][0]["id"] == uid
        assert (await scim.get("/Users", params={"filter": 'externalId eq "ext-42"'})).json()["totalResults"] == 1
        assert (await scim.get("/Users", params={"filter": "title co x"})).status_code == 400

        # Entra form, then Okta form.
        r = await scim.patch(f"/Users/{uid}", json={"Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
        assert r.json()["active"] is False
        r = await scim.patch(f"/Users/{uid}", json={"Operations": [{"op": "replace", "value": {"active": True, "displayName": "Pat Q Lee"}}]})
        assert r.json()["active"] is True and r.json()["displayName"] == "Pat Q Lee"
        assert (await scim.delete(f"/Users/{uid}")).status_code == 204
        assert (await scim.get(f"/Users/{uid}")).status_code == 404

    # A revoked token stops working; another tenant's users are invisible.
    async with _client(tenant.id) as c:
        assert (await c.delete("/api/settings/identity/scim-token")).status_code == 204
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/scim/v2",
                                 headers={"Authorization": f"Bearer {token}"}) as revoked:
        assert (await revoked.get("/Users")).status_code == 401
    async with AsyncSessionLocal() as db:
        actions = sorted({e.action for e in (await db.execute(select(AuditEvent).where(
            AuditEvent.tenant_id == tenant.id, AuditEvent.action.like("scim.%")))).scalars()})
        assert actions == ["scim.user_created", "scim.user_deleted", "scim.user_updated"]
        cfg = await db.get(TenantSsoConfig, tenant.id)
        assert cfg.scim_token_hash is None and datetime.now(timezone.utc)
