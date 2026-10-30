"""
Authentication & authorization primitives (Auth + RBAC sprint, P1).

This module is the ONLY place that knows how to hash a password, mint/verify
a JWT, and turn a bearer token into a `Principal`. Routes depend on the
FastAPI dependencies exported here (`get_current_user`, `require_permission`)
and never parse tokens themselves - same "one seam" discipline the Collector
Framework uses for vendors.

Design notes:
  * Passwords: Argon2id via passlib. Deliberately NOT SecretCrypto (Fernet) -
    password storage must be a one-way KDF, not reversible encryption.
  * Access token: stateless HS256 JWT carrying the tenant id and the user's
    resolved permission set, so a per-request authorization check needs no DB
    round-trip. Kept short-lived (minutes) precisely because it cannot be
    revoked server-side.
  * Refresh token: an opaque high-entropy string (NOT a JWT) whose SHA-256
    hash is persisted (app/models/refresh_token.py) so it can be revoked.
  * MSP cross-tenant (interim): a superuser may act on any tenant by sending
    an `X-Tenant-Id` header; a normal user's principal is pinned to the
    tenant in their token. This is the approved interim model pending a
    formal MSP-org hierarchy (see audit-master-roadmap).
  * API keys (Sprint 2): a raw API key presented as `Authorization: Bearer
    <key>` is resolved against the `api_keys` table (SHA-256 hash lookup),
    checked for expiry/active state, and its scopes are enforced as the
    Principal's permission set. `get_current_user` transparently supports
    both JWTs and API keys so existing routes gain API-key auth without
    changes.

`Principal` is a frozen dataclass, not a Pydantic/ORM object, so it is cheap,
immutable, and never accidentally serialized to a client.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.core.config import get_settings

settings = get_settings()

# Argon2id. `deprecated="auto"` lets us add stronger schemes later and have
# passlib flag old hashes for rehash-on-next-login without a migration.
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# auto_error=False: we raise our own 401s (with consistent shape) and also
# support the AUTH_ENFORCED=false transitional mode where an absent token is
# tolerated by get_optional_user.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

_ACCESS_TOKEN_TYPE = "access"


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(plaintext: str) -> str:
    """Return an Argon2id hash. Never logs the plaintext."""
    return _pwd_context.hash(plaintext)


def verify_password(plaintext: str, password_hash: str | None) -> bool:
    """Constant-time-ish verify via passlib. A null stored hash (user has no
    local password) always returns False rather than raising."""
    if not password_hash:
        return False
    try:
        return _pwd_context.verify(plaintext, password_hash)
    except ValueError:
        # Malformed/unknown hash format - treat as a failed login, never 500.
        return False


# ---------------------------------------------------------------------------
# Refresh tokens (opaque; only the hash is stored)
# ---------------------------------------------------------------------------

def generate_refresh_token() -> str:
    """A URL-safe 256-bit random token handed to the client exactly once."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """SHA-256 hex digest stored in refresh_tokens.token_hash. A fast hash is
    fine here (unlike passwords) because the token is high-entropy."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Access JWTs
# ---------------------------------------------------------------------------

def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    email: str,
    is_superuser: bool,
    permissions: frozenset[str],
    session_id: uuid.UUID | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    claims: dict = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "email": email,
        "su": is_superuser,
        "perms": sorted(permissions),
        "type": _ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    if session_id is not None:
        claims["sid"] = str(session_id)
    return jwt.encode(claims, settings.effective_jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token, settings.effective_jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if payload.get("type") != _ACCESS_TOKEN_TYPE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# ---------------------------------------------------------------------------
# API keys (Sprint 2: expiry + scope enforcement + lastUsedAt)
# ---------------------------------------------------------------------------

def hash_api_key(raw: str) -> str:
    """SHA-256 hex digest of a raw API key. Same scheme used by the settings
    route when creating a key (app/api/routes/settings.py::hash_api_key)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _resolve_api_key_principal(
    raw_key: str, db: AsyncSession, x_tenant_id: uuid.UUID | None
) -> Principal | None:
    """Resolve a raw API key into a Principal, or return None if the key is
    unknown/inactive/expired. Never raises - the caller decides the 401 shape.

    Enforces:
      * key exists (SHA-256 hash lookup) and is_active
      * expires_at (ISO-8601 string) is in the future
      * tenant isolation: the key's tenant_id is used (X-Tenant-Id override
        is NOT permitted for API keys - a key is pinned to its tenant)
      * scopes become the Principal's permission set, so `require_permission`
        enforces key scopes automatically.

    Fire-and-forget lastUsedAt update: we schedule a background task that
    opens its own session so the current request is never slowed down by the
    write. The task is best-effort and swallows errors (a failed lastUsedAt
    update must never break the authenticated request).
    """
    from app.models.api_key import ApiKey

    key_hash = hash_api_key(raw_key)
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active.is_(True),
            ApiKey.is_deleted.is_(False),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    # Expiry enforcement: expires_at is stored as an ISO-8601 string.
    if row.expires_at:
        try:
            expires = datetime.fromisoformat(row.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                return None
        except ValueError:
            # Malformed expiry - treat as expired (fail closed).
            return None

    # Fire-and-forget lastUsedAt update (best-effort, own session).
    async def _touch_last_used() -> None:
        try:
            from app.core.db import AsyncSessionLocal
            async with AsyncSessionLocal() as touch_db:
                touch_row = (
                    await touch_db.execute(
                        select(ApiKey).where(ApiKey.id == row.id)
                    )
                ).scalar_one_or_none()
                if touch_row is not None:
                    touch_row.last_used_at = datetime.now(timezone.utc).isoformat()
                    await touch_db.commit()
        except Exception:  # noqa: BLE001 - best-effort, never break the request
            logger.debug("Failed to update api_key.last_used_at for key %s", row.id)

    asyncio.get_running_loop().create_task(_touch_last_used())

    return Principal(
        user_id=uuid.UUID(int=0),  # API keys have no user identity
        tenant_id=row.tenant_id,
        email=f"apikey:{row.name}",
        is_superuser=False,
        permissions=frozenset(row.scopes or []),
    )


# ---------------------------------------------------------------------------
# Principal + dependencies
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Principal:
    """The authenticated caller for the duration of one request."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    is_superuser: bool
    permissions: frozenset[str]

    def has_permission(self, code: str) -> bool:
        # Superusers bypass the grant check by design (interim MSP model).
        return self.is_superuser or code in self.permissions


def _principal_from_payload(payload: dict, x_tenant_id: uuid.UUID | None) -> Principal:
    is_superuser = bool(payload.get("su", False))
    token_tenant = uuid.UUID(payload["tid"])

    # Superuser tenant targeting (interim MSP model). A normal user supplying
    # X-Tenant-Id is ignored - never allow it to widen their scope.
    if x_tenant_id is not None and x_tenant_id != token_tenant:
        if not is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="X-Tenant-Id override is not permitted for this user",
            )
        # Allowed only with a live break-glass grant, checked in
        # get_current_user (_require_support_grant), which also logs the use.
        effective_tenant = x_tenant_id
    else:
        effective_tenant = token_tenant

    return Principal(
        user_id=uuid.UUID(payload["sub"]),
        tenant_id=effective_tenant,
        email=payload.get("email", ""),
        is_superuser=is_superuser,
        permissions=frozenset(payload.get("perms", [])),
    )


async def _validate_override_tenant(target_tenant_id: uuid.UUID) -> None:
    """P0 hardening for the superuser X-Tenant-Id override.

    The override target must be a REAL, live (non-soft-deleted) tenant. Before
    this check an override pointing at a random or deleted tenant UUID was
    accepted silently — an unvalidated active-tenant claim. Suspended
    (is_active=False) tenants still pass: superusers must be able to
    administer them (reads stay open by design).

    Opens its own short-lived session (same pattern as the API-key resolution
    and the impersonation audit) so no request-scoped session is needed. The
    normal no-override fast path never pays this DB round-trip.
    """
    from app.core.db import AsyncSessionLocal
    from app.repositories.tenant_repository import TenantRepository

    async with AsyncSessionLocal() as db:
        row = await TenantRepository(db).get_existing(target_tenant_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target tenant does not exist or is not accessible",
        )


SUPPORT_GRANT_REQUIRED = (
    "Support access to this tenant needs an active break-glass grant "
    "(POST /api/support-access/grants with a reason)."
)


async def _require_support_grant(principal: Principal) -> None:
    """403 unless the superuser holds an unrevoked, unexpired grant for the
    target tenant. Every allowed use is logged with the grant id."""
    from app.core.db import AsyncSessionLocal
    from app.services.support_access import SupportAccessService

    async with AsyncSessionLocal() as db:
        grant = await SupportAccessService(db).live_grant(principal.user_id, principal.tenant_id)
    if grant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SUPPORT_GRANT_REQUIRED)
    logger.info(
        "support access: user=%s tenant=%s grant=%s expires=%s",
        principal.user_id, principal.tenant_id, grant.id, grant.expires_at.isoformat(),
    )


def _override_applied(principal: Principal, payload: dict) -> bool:
    """True when the request's effective tenant came from an X-Tenant-Id
    override rather than the token's own tenant claim (only possible for a
    superuser — `_principal_from_payload` already 403s everyone else)."""
    return principal.tenant_id != uuid.UUID(payload["tid"])


# -- Session liveness -------------------------------------------------------
# Positive results are cached per process for SESSION_CHECK_CACHE_SECONDS so
# an authenticated request does not always pay a database round trip. A
# revocation in this process clears the entry at once; in another process it
# takes effect within the cache window (default 10 s, 0 disables caching).
_live_sessions: dict[str, float] = {}


def forget_session(session_id: uuid.UUID | str | None = None) -> None:
    """Drop one cached session, or all of them when no id is given."""
    if session_id is None:
        _live_sessions.clear()
    else:
        _live_sessions.pop(str(session_id), None)


async def session_is_live(session_id: str) -> bool:
    import time

    from app.core.db import AsyncSessionLocal
    from app.repositories.refresh_token_repository import RefreshTokenRepository

    ttl = settings.session_check_cache_seconds
    now = time.monotonic()
    cached = _live_sessions.get(session_id)
    if ttl > 0 and cached is not None and now - cached < ttl:
        return True
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return False
    async with AsyncSessionLocal() as db:
        live = await RefreshTokenRepository(db).session_is_live(sid)
    if live and ttl > 0:
        if len(_live_sessions) > 50_000:
            _live_sessions.clear()
        _live_sessions[session_id] = now
    else:
        _live_sessions.pop(session_id, None)
    return live


def _publish_principal(request: Request, principal: Principal) -> None:
    """Expose the successfully-resolved principal on ``request.state``.

    Cross-cutting middleware (structured request observability) needs to
    attribute a request to its tenant/user, but must not parse tokens itself.
    Publishing the resolved principal keeps auth the single seam that
    understands credentials.

    SECURITY: only the immutable ``Principal`` is stored — never the raw
    token, the Authorization header, a password or an API key. `state` is
    request-scoped and discarded when the request ends, so nothing leaks
    between requests or tenants.
    """
    request.state.principal = principal


async def get_current_user(
    request: Request,
    token: str | None = Depends(_oauth2_scheme),
    x_tenant_id: uuid.UUID | None = Header(default=None, alias="X-Tenant-Id"),
) -> Principal:
    """Resolve the authenticated principal or raise 401. Use this on any route
    that requires a logged-in user but no specific permission.

    Supports both JWT bearer tokens and raw API keys. An API key is resolved
    against the api_keys table (hash lookup), checked for expiry/active state,
    and its scopes become the Principal's permission set."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Try JWT first.
    try:
        payload = _decode_access_token(token)
    except HTTPException:
        # Not a valid JWT - fall through to API key resolution.
        pass
    else:
        # A valid JWT: authorization failures (e.g. a forbidden X-Tenant-Id)
        # are final here, never retried as an API key.
        principal = _principal_from_payload(payload, x_tenant_id)
        # Server-side revocation: an access token bound to a sign-in session
        # stops working as soon as the session is revoked (logout, password
        # reset, refresh-token replay) or the user is disabled.
        if payload.get("sid") and not await session_is_live(payload["sid"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has ended. Sign in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Superuser acting in another tenant: the target must exist AND the
        # caller must hold a live break-glass grant for it (R1.2).
        if _override_applied(principal, payload):
            await _validate_override_tenant(principal.tenant_id)
            await _require_support_grant(principal)
        request.state.token_tenant_id = uuid.UUID(payload["tid"])
        _publish_principal(request, principal)
        return principal

    # API key path: needs a DB session.
    from app.core.db import get_db  # noqa: F401 - imported lazily to avoid a cycle
    async for session in get_db():
        principal = await _resolve_api_key_principal(token, session, x_tenant_id)
        if principal is not None:
            _publish_principal(request, principal)
            return principal
        break

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_user(
    token: str | None = Depends(_oauth2_scheme),
    x_tenant_id: uuid.UUID | None = Header(default=None, alias="X-Tenant-Id"),
) -> Principal | None:
    """Transitional dependency for the phased cutover (AUTH_ENFORCED=false):
    returns a Principal if a valid token is present, else None. A present but
    INVALID token still 401s - only absence is tolerated. Lets a route accept
    either principal-derived tenancy or the legacy tenant_id query param while
    routes are migrated one at a time."""
    if not token:
        return None
    try:
        payload = _decode_access_token(token)
        principal = _principal_from_payload(payload, x_tenant_id)
    except HTTPException:
        # Not a JWT - try API key resolution.
        from app.core.db import get_db
        async for session in get_db():
            principal = await _resolve_api_key_principal(token, session, x_tenant_id)
            if principal is not None:
                return principal
            break
        return None
    else:
        # P0 hardening: validate the override target before trusting it.
        # (Outside the except block so a 403 for a bogus override is NOT
        # silently swallowed into "no principal".)
        if _override_applied(principal, payload):
            await _validate_override_tenant(principal.tenant_id)
        return principal


def require_permission(code: str):
    """Dependency factory: guard a route with a specific permission.

        @router.get(..., dependencies=[Depends(require_permission("alerts.read"))])

    or inject the principal too:

        principal: Principal = Depends(require_permission("alerts.read"))
    """

    async def _dependency(principal: Principal = Depends(get_current_user)) -> Principal:
        if not principal.has_permission(code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {code}",
            )
        return principal

    return _dependency


def get_tenant_id(principal: Principal = Depends(get_current_user)) -> uuid.UUID:
    """Extract the tenant_id from the authenticated principal.

    Use this dependency as a replacement for the legacy `tenant_id: uuid.UUID = Query(...)`
    pattern. This enforces tenant isolation: the caller's identity determines the tenant,
    not an arbitrary query parameter.

        @router.get("")
        async def list_items(tenant_id: uuid.UUID = Depends(get_tenant_id), ...):
            ...
    """
    return principal.tenant_id


# API keys authenticate with this nil user id (see _resolve_api_key_principal).
_NO_USER_ID = uuid.UUID(int=0)


def get_actor_user_id(principal: Principal = Depends(get_current_user)) -> uuid.UUID | None:
    """The signed-in user an action (acknowledge, assign) is attributed to,
    or None for an API key, which has no user identity. Never taken from the
    request body."""
    return None if principal.user_id == _NO_USER_ID else principal.user_id


def get_acting_user_id(principal: Principal = Depends(get_current_user)) -> uuid.UUID:
    """The signed-in user an approval or rejection is attributed to.

    Approver identity always comes from the authenticated principal, never
    from the request body (a client could otherwise name any approver).
    API keys carry no user identity, so they cannot approve or reject.
    """
    if principal.user_id == _NO_USER_ID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Approvals require a signed-in user; API keys cannot approve or reject.",
        )
    return principal.user_id


def require_admin(principal: Principal = Depends(get_current_user)) -> Principal:
    """Dependency factory: guard a route so only superusers/admins can access it.

        @router.get("/admin/...", dependencies=[Depends(require_admin)])

    or inject the principal too:

        principal: Principal = Depends(require_admin)
    """
    if not principal.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return principal
