"""
Auth + RBAC API (P1).

Two groups of endpoints:
  * Unauthenticated auth flow: /login, /refresh, /logout (token in body).
  * Authenticated IAM administration: /me, plus /users and /roles CRUD guarded
    by require_permission(...). These are NEW endpoints, so unlike the legacy
    routes they derive tenant_id from the authenticated Principal from day one
    (no interim tenant_id query param). This is the target pattern the rest of
    the API migrates toward during the phased AUTH_ENFORCED cutover.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription, require_entitlement
from app.models.role import Role
from app.repositories.permission_repository import PermissionRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    AcceptInviteRequest,
    AssignRolesRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    PermissionOut,
    RefreshRequest,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    SetPasswordRequest,
    TenantContext,
    TokenResponse,
    UserCreate,
    UserOut,
)
from app.services.account_service import AccountService, InvalidAccountToken
from app.services.auth_service import (
    AuthError,
    AuthService,
    Conflict,
    EmailNotVerified,
    NotFound,
)
from app.services.email.outbox import EmailDisabled

router = APIRouter(prefix="/auth", tags=["auth"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
# ---- Auth-rate limiter (5 rpm per IP) ----
class _AuthRateLimiter:
    """Stricter sliding-window limiter for login/refresh endpoints."""

    def __init__(self, requests_per_minute: int = 5) -> None:
        self._rpm = requests_per_minute
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        window = self._windows[key]
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self._rpm:
            return False
        window.append(now)
        return True


_auth_limiter = _AuthRateLimiter()


def _check_auth_rate(request: Request) -> None:
    """Dependency that raises 429 if the client has exceeded the auth rate limit."""
    client_ip = request.client.host if request.client else "unknown"
    if not _auth_limiter.acquire(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Try again later.",
        )


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(
        users=UserRepository(db),
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        refresh_tokens=RefreshTokenRepository(db),
    )


def _role_out(role: Role) -> RoleOut:
    return RoleOut(
        id=role.id,
        tenant_id=role.tenant_id,
        name=role.name,
        description=role.description,
        permission_codes=sorted(p.code for p in role.permissions),
    )


# ---- Authentication flow -------------------------------------------------

@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
    _: None = Depends(_check_auth_rate),
):
    try:
        access, refresh, expires_in, _resolved_tenant_id = await service.login(
            tenant_id=payload.tenant_id, email=payload.email, password=payload.password
        )
    except EmailNotVerified as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AuthError as exc:
        # Security audit: every failed login attempt is recorded (best-effort
        # - auditing must never change the 401 response). AuditEvent rows are
        # tenant-scoped by design, so a failed attempt with NO resolvable
        # tenant (omitted tenant_id + unknown/ambiguous email) has nowhere
        # tenant-scoped to write to - it's still covered by the per-IP auth
        # rate limiter above, just not by the per-tenant audit trail.
        if payload.tenant_id is not None:
            from app.services.audit_service import AuditService

            await AuditService(db).record_auth(
                tenant_id=payload.tenant_id,
                action="auth.login_failed",
                email=payload.email,
                success=False,
            )
        # Persist the failed-attempt counter / lockout (AuthService.login).
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)


# ---- Account self-service (public, rate limited per IP) --------------------

_EMAIL_OFF = "Email is not configured on this deployment. Ask your administrator."
_RESET_SENT = "If an account exists for that email, a reset link has been sent."


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_check_auth_rate),
) -> dict[str, str]:
    """Always the same answer, whether or not the email has an account."""
    try:
        await AccountService(db).request_password_reset(payload.email)
    except EmailDisabled as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_EMAIL_OFF) from exc
    await db.commit()
    return {"detail": _RESET_SENT}


@router.post("/password/reset")
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_check_auth_rate),
) -> dict[str, str]:
    try:
        await AccountService(db).reset_password(payload.token, payload.password)
    except InvalidAccountToken as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    return {"detail": "Password updated. Sign in with your new password."}


@router.post("/email/verify")
async def verify_email(
    payload: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_check_auth_rate),
) -> dict[str, str]:
    try:
        await AccountService(db).verify_email(payload.token)
    except InvalidAccountToken as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    return {"detail": "Email confirmed. You can sign in now."}


@router.post("/invite/accept")
async def accept_invite(
    payload: AcceptInviteRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_check_auth_rate),
) -> dict[str, str]:
    try:
        await AccountService(db).accept_invite(payload.token, payload.password)
    except InvalidAccountToken as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    return {"detail": "Password set. Sign in to continue."}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
    _: None = Depends(_check_auth_rate),
):
    try:
        access, refresh_token, expires_in = await service.refresh(refresh_token=payload.refresh_token)
    except AuthError as exc:
        # Commit even on failure: a replayed refresh token revokes its whole
        # session, and that revocation must persist.
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh_token, expires_in=expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    await service.logout(refresh_token=payload.refresh_token)
    await db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """End every session of the signed-in user (all devices). API keys are
    not sessions and are revoked separately."""
    if principal.user_id.int == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API keys have no sessions to end")
    await service.logout_everywhere(principal.user_id)
    await db.commit()


@router.get("/me", response_model=MeResponse)
async def me(
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    user = await service.users.get_by_id(principal.user_id, principal.tenant_id)
    role_names = sorted(r.name for r in user.roles) if user else []

    # -- P0 tenant context (authoritative DB rows, never token-invented) -----
    # accessible_tenants follows the existing authorization model exactly:
    #   * a normal user is pinned to their own tenant (users.tenant_id FK)
    #   * a superuser is authorized for every live tenant (interim MSP
    #     cross-tenant model, app/core/auth.py)
    # A soft-deleted tenant yields nothing (fail closed).
    tenants = TenantRepository(db)
    accessible: list[TenantContext] = []
    tenant_name: str | None = None

    if principal.is_superuser:
        all_rows = await tenants.list_all()
        accessible = [
            TenantContext(
                id=t.id, name=t.name, slug=t.slug, is_active=t.is_active,
                role="Superuser",
            )
            for t in all_rows
        ]
        active = await tenants.get_existing(principal.tenant_id)
        tenant_name = active.name if active else None
    else:
        own = await tenants.get_existing(principal.tenant_id) if user else None
        if own is not None:
            tenant_name = own.name
            accessible = [
                TenantContext(
                    id=own.id, name=own.name, slug=own.slug,
                    is_active=own.is_active,
                    role=role_names[0] if role_names else None,
                )
            ]

    return MeResponse(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        email=principal.email,
        full_name=user.full_name if user else None,
        is_superuser=principal.is_superuser,
        roles=role_names,
        permissions=sorted(principal.permissions),
        tenant_name=tenant_name,
        accessible_tenants=accessible,
    )


# ---- Permissions catalog -------------------------------------------------

@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(
    _: Principal = Depends(require_permission("roles.read")),
    service: AuthService = Depends(get_auth_service),
):
    return await service.list_permissions()


# ---- Role administration -------------------------------------------------

@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    principal: Principal = Depends(require_permission("roles.read")),
    service: AuthService = Depends(get_auth_service),
):
    return [_role_out(r) for r in await service.list_roles(principal.tenant_id)]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_permission("roles.write")),
    service: AuthService = Depends(get_auth_service),
):
    try:
        role = await service.create_role(
            tenant_id=principal.tenant_id,
            name=payload.name,
            description=payload.description,
            permission_codes=payload.permission_codes,
        )
    except Conflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    return _role_out(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_permission("roles.write")),
    service: AuthService = Depends(get_auth_service),
):
    try:
        role = await service.update_role(
            tenant_id=principal.tenant_id,
            role_id=role_id,
            description=payload.description,
            permission_codes=payload.permission_codes,
        )
    except NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await db.commit()
    return _role_out(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_permission("roles.write")),
    service: AuthService = Depends(get_auth_service),
):
    try:
        await service.delete_role(tenant_id=principal.tenant_id, role_id=role_id)
    except NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await db.commit()


# ---- User administration -------------------------------------------------

@router.get("/users", response_model=list[UserOut])
async def list_users(
    principal: Principal = Depends(require_permission("users.read")),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: AuthService = Depends(get_auth_service),
):
    return await service.list_users(principal.tenant_id, limit=limit, offset=offset)


@router.post("/users", dependencies=[_active_sub_guard, Depends(require_entitlement("seats"))],
             response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_permission("users.create")),
    service: AuthService = Depends(get_auth_service),
):
    try:
        user = await service.create_user(
            tenant_id=principal.tenant_id,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            is_superuser=payload.is_superuser,
            role_ids=payload.role_ids,
        )
        if payload.send_invite:
            await AccountService(db).send_invite(user)
    except Conflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EmailDisabled as exc:
        # Nothing is committed: no half-created user without a way in.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_EMAIL_OFF) from exc
    await db.commit()
    return user


@router.post("/users/{user_id}/password", response_model=UserOut)
async def set_user_password(
    user_id: uuid.UUID,
    payload: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_permission("users.update")),
    service: AuthService = Depends(get_auth_service),
):
    try:
        user = await service.set_password(
            tenant_id=principal.tenant_id, user_id=user_id, password=payload.password
        )
    except NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await db.commit()
    return user


@router.post("/users/{user_id}/roles", response_model=UserOut)
async def assign_user_roles(
    user_id: uuid.UUID,
    payload: AssignRolesRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_permission("users.update")),
    service: AuthService = Depends(get_auth_service),
):
    try:
        user = await service.assign_roles(
            tenant_id=principal.tenant_id, user_id=user_id, role_ids=payload.role_ids
        )
    except NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await db.commit()
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_permission("users.delete")),
    service: AuthService = Depends(get_auth_service),
):
    try:
        await service.delete_user(tenant_id=principal.tenant_id, user_id=user_id)
    except NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await db.commit()
