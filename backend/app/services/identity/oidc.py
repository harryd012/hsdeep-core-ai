"""
OIDC single sign-on (enterprise security programme S6).

Authorization Code flow with PKCE (S256), state and nonce, against the tenant's
identity provider (Microsoft Entra ID, Okta, Google Workspace, Auth0, Ping …):

  1. start_login: a one-time SsoLoginState (state, nonce, PKCE verifier, 10 min)
     and the provider's authorization URL.
  2. complete_login (callback): the state is consumed once; the code is
     exchanged at the token endpoint; the ID token's signature is checked
     against the provider's JWKS and its issuer, audience, expiry and nonce
     are checked; the email must be on one of the tenant's verified domains.
  3. The user is found by their linked subject, else by email (and linked);
     with just-in-time provisioning a new user is created with the default
     role; otherwise unknown people are refused.
  4. A one-time handoff code (60 s, stored hashed) goes back to the browser,
     which exchanges it for the session; tokens never appear in a URL.

The provider's discovery document and keys are cached for an hour (keys are
re-fetched once when a token names an unknown key id).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from jose import jwt
from jose.exceptions import JOSEError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import SecretCrypto
from app.models.identity import SsoHandoff, SsoLoginState, TenantSsoConfig, UserIdentity
from app.models.user import User
from app.services.identity.domains import email_domain, verified_domains

logger = logging.getLogger(__name__)

STATE_TTL = timedelta(minutes=10)
HANDOFF_TTL = timedelta(seconds=60)
CACHE_SECONDS = 3600
ALLOWED_ALGS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]

_cache: dict[str, tuple[float, dict]] = {}


class SsoError(ValueError):
    """A user-facing reason the SSO sign-in could not complete."""


def _client() -> httpx.AsyncClient:
    """HTTP client for the identity provider (tests replace this)."""
    return httpx.AsyncClient(timeout=10.0, follow_redirects=False)


async def _get_json(url: str, *, cache: bool = True, force: bool = False) -> dict:
    hit = _cache.get(url)
    if cache and hit and hit[0] > time.monotonic() and not force:
        return hit[1]
    async with _client() as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise SsoError(f"The identity provider answered {resp.status_code} for {url}")
    data = resp.json()
    if cache:
        _cache[url] = (time.monotonic() + CACHE_SECONDS, data)
    return data


async def discover(issuer: str) -> dict:
    doc = await _get_json(issuer.rstrip("/") + "/.well-known/openid-configuration")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if not doc.get(key):
            raise SsoError(f"The identity provider's configuration has no {key}")
    return doc


def redirect_uri() -> str:
    return get_settings().api_public_url.rstrip("/") + "/api/auth/sso/oidc/callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def start_login(db: AsyncSession, config: TenantSsoConfig, *, login_hint: str | None, next_path: str | None) -> str:
    doc = await discover(config.issuer)
    verifier, challenge = _pkce()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(SsoLoginState(state=state, tenant_id=config.tenant_id, nonce=nonce, code_verifier=verifier,
                         next_path=next_path, expires_at=datetime.now(timezone.utc) + STATE_TTL))
    await db.flush()
    params = {
        "response_type": "code", "client_id": config.client_id, "redirect_uri": redirect_uri(),
        "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{doc['authorization_endpoint']}?{urlencode(params)}"


async def _verify_id_token(id_token: str, doc: dict, config: TenantSsoConfig, nonce: str) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
    except JOSEError as exc:
        raise SsoError("The identity provider returned an unreadable ID token") from exc
    if header.get("alg") not in ALLOWED_ALGS:
        raise SsoError("The ID token uses an algorithm that is not accepted")
    kid = header.get("kid")

    async def key_for(force: bool) -> dict | None:
        jwks = await _get_json(doc["jwks_uri"], force=force)
        keys = jwks.get("keys", [])
        return next((k for k in keys if k.get("kid") == kid), keys[0] if (kid is None and len(keys) == 1) else None)

    key = await key_for(False) or await key_for(True)
    if key is None:
        raise SsoError("The ID token was signed with a key the identity provider does not publish")
    try:
        claims = jwt.decode(
            id_token, key, algorithms=[header["alg"]], audience=config.client_id, issuer=doc["issuer"],
            options={"verify_at_hash": False},
        )
    except JOSEError as exc:
        raise SsoError(f"The ID token is not valid ({exc})") from exc
    if claims.get("nonce") != nonce:
        raise SsoError("The ID token does not belong to this sign-in")
    return claims


def _email_of(claims: dict) -> str | None:
    for key in ("email", "preferred_username", "upn"):
        value = claims.get(key)
        if isinstance(value, str) and "@" in value:
            if key == "email" and claims.get("email_verified") is False:
                continue
            return value.strip().lower()
    return None


@dataclass
class SsoResult:
    user: User
    next_path: str | None
    created: bool


async def complete_login(db: AsyncSession, *, state: str, code: str) -> SsoResult:
    now = datetime.now(timezone.utc)
    row = await db.get(SsoLoginState, state)
    if row is None:
        raise SsoError("This sign-in link has expired. Start again.")
    await db.delete(row)  # one use, whatever happens next
    await db.flush()
    if row.expires_at <= now:
        raise SsoError("This sign-in link has expired. Start again.")
    config = await db.get(TenantSsoConfig, row.tenant_id)
    if config is None or not config.enabled or not config.issuer or not config.client_id:
        raise SsoError("Single sign-on is not enabled for this organization.")
    doc = await discover(config.issuer)

    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
            "client_id": config.client_id, "code_verifier": row.code_verifier}
    if config.client_secret_encrypted:
        form["client_secret"] = SecretCrypto().decrypt(config.client_secret_encrypted)
    async with _client() as client:
        resp = await client.post(doc["token_endpoint"], data=form, headers={"Accept": "application/json"})
    if resp.status_code != 200 or not resp.json().get("id_token"):
        raise SsoError("The identity provider did not complete the sign-in.")
    claims = await _verify_id_token(resp.json()["id_token"], doc, config, row.nonce)

    email = _email_of(claims)
    subject = str(claims.get("sub") or "")
    if not email or not subject:
        raise SsoError("The identity provider did not share your email address.")
    if email_domain(email) not in await verified_domains(db, config.tenant_id):
        raise SsoError("Your email domain is not verified for this organization.")

    user, created = await _resolve_user(db, config, email=email, subject=subject, name=claims.get("name"))
    if not user.is_active:
        raise SsoError("Your account is disabled. Contact your administrator.")
    return SsoResult(user=user, next_path=row.next_path, created=created)


async def _resolve_user(db: AsyncSession, config: TenantSsoConfig, *, email: str, subject: str, name) -> tuple[User, bool]:
    link = (await db.execute(select(UserIdentity).where(
        UserIdentity.tenant_id == config.tenant_id, UserIdentity.provider == "oidc", UserIdentity.subject == subject
    ))).scalar_one_or_none()
    if link is not None:
        user = await db.get(User, link.user_id)
        if user is not None and not user.is_deleted:
            return user, False
    user = (await db.execute(select(User).where(
        User.tenant_id == config.tenant_id, User.email.ilike(email), User.is_deleted.is_(False)
    ))).scalar_one_or_none()
    created = False
    if user is None:
        if not config.jit_provisioning:
            raise SsoError("You don't have an account in this organization yet. Ask your administrator to add you.")
        from app.repositories.role_repository import RoleRepository
        from app.repositories.user_repository import UserRepository

        users = UserRepository(db)
        user = await users.create(tenant_id=config.tenant_id, email=email, password_hash=None,
                                  full_name=name if isinstance(name, str) else None, is_superuser=False)
        if config.default_role_id:
            roles = await RoleRepository(db).get_many_by_ids(config.tenant_id, [config.default_role_id])
            if roles:
                await users.assign_roles(user, roles)
        created = True
    if link is None:
        db.add(UserIdentity(tenant_id=config.tenant_id, user_id=user.id, provider="oidc", subject=subject))
        await db.flush()
    return user, created


async def create_handoff(db: AsyncSession, user: User) -> str:
    code = secrets.token_urlsafe(32)
    db.add(SsoHandoff(code_hash=hashlib.sha256(code.encode()).hexdigest(), tenant_id=user.tenant_id, user_id=user.id,
                      expires_at=datetime.now(timezone.utc) + HANDOFF_TTL))
    await db.flush()
    return code


async def redeem_handoff(db: AsyncSession, code: str) -> User | None:
    row = await db.get(SsoHandoff, hashlib.sha256(code.encode()).hexdigest(), with_for_update=True)
    now = datetime.now(timezone.utc)
    if row is None or row.used_at is not None or row.expires_at <= now:
        return None
    row.used_at = now
    await db.flush()
    user = await db.get(User, row.user_id)
    return user if user is not None and user.is_active and not user.is_deleted else None


def forget_cache() -> None:
    _cache.clear()


__all__ = ["SsoError", "complete_login", "create_handoff", "discover", "redeem_handoff", "start_login"]
