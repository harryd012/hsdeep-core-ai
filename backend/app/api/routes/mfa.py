"""
Multi-factor authentication API (enterprise security programme S2).

Sign-in second step (public, rate limited):
  POST /api/auth/login/mfa                 challenge token + code -> tokens

The signed-in user's own MFA:
  GET  /api/auth/mfa                       status
  POST /api/auth/mfa/setup                 new secret + QR (unconfirmed)
  POST /api/auth/mfa/confirm               first code turns MFA on -> recovery codes
  POST /api/auth/mfa/recovery-codes        current code -> a new set of recovery codes
  POST /api/auth/mfa/disable               current code -> MFA off (refused when required)

Administration (security.manage):
  POST /api/auth/users/{user_id}/mfa/reset remove a user's MFA and end their sessions

Every change is an audit event; failed codes at sign-in count towards lockout.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import _audit, _check_auth_rate, _client, _require_user, get_auth_service
from app.core.auth import Principal, get_current_user, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.schemas.auth import (
    MfaCodeRequest,
    MfaLoginRequest,
    MfaSetupOut,
    MfaStatusOut,
    RecoveryCodesOut,
    TokenResponse,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService, InvalidMfaCode
from app.services.mfa.service import MfaError, MfaService
from app.services.security_policy import load_policy

router = APIRouter(prefix="/auth", tags=["auth"])


async def _me(principal: Principal, service: AuthService):
    _require_user(principal)
    user = await service.users.get_by_id(principal.user_id, principal.tenant_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/login/mfa", response_model=TokenResponse)
async def login_mfa(
    payload: MfaLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
    _: None = Depends(_check_auth_rate),
):
    ip_address, user_agent = _client(request)
    try:
        access, refresh, expires_in, user, method = await service.complete_mfa_login(
            challenge_token=payload.mfa_token, code=payload.code, ip_address=ip_address, user_agent=user_agent,
        )
    except InvalidMfaCode as exc:
        # Persist the failed-attempt counter (lockout) before answering.
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if method == "recovery":
        remaining = (await MfaService(db).status(user.id)).recovery_codes_remaining
        await AuditService(db).record_entity(
            tenant_id=user.tenant_id, action="mfa.recovery_code_used", entity_type="user", entity_id=user.id,
            actor_id=user.id, actor_email=user.email,
            description=f"{user.email} signed in with a recovery code ({remaining} left)",
            new_values={"recovery_codes_remaining": remaining},
        )
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.get("/mfa", response_model=MfaStatusOut)
async def mfa_status(
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_user(principal)
    s = await MfaService(db).status(principal.user_id)
    policy = await load_policy(db, principal.tenant_id)
    return MfaStatusOut(
        enabled=s.enabled, confirmed_at=s.confirmed_at, recovery_codes_remaining=s.recovery_codes_remaining,
        setup_pending=s.setup_pending, required_by_policy=policy.require_mfa,
    )


@router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """Start (or restart) set-up: a new secret and its QR code. Nothing changes
    for sign-in until /mfa/confirm accepts a code from the app."""
    user = await _me(principal, service)
    try:
        setup = await MfaService(db).begin_setup(user)
    except MfaError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    return MfaSetupOut(secret=setup.secret, otpauth_uri=setup.otpauth_uri, qr_svg=setup.qr_svg)


@router.post("/mfa/confirm", response_model=RecoveryCodesOut)
async def mfa_confirm(
    payload: MfaCodeRequest,
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """Turn MFA on with the first code from the app. Returns the recovery
    codes, shown this once. Refresh the session afterwards to drop an
    enrolment-only restriction."""
    user = await _me(principal, service)
    try:
        codes = await MfaService(db).confirm(user, payload.code)
    except MfaError as exc:
        raise _bad(exc) from exc
    await _audit(db, principal, action="mfa.enabled", entity_type="user", entity_id=user.id,
                 description=f"{user.email} turned on multi-factor authentication")
    await db.commit()
    return RecoveryCodesOut(codes=codes)


@router.post("/mfa/recovery-codes", response_model=RecoveryCodesOut)
async def mfa_regenerate_codes(
    payload: MfaCodeRequest,
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_user(principal)
    try:
        codes = await MfaService(db).regenerate_recovery_codes(principal.user_id, payload.code)
    except MfaError as exc:
        raise _bad(exc) from exc
    await _audit(db, principal, action="mfa.recovery_codes_regenerated", entity_type="user", entity_id=principal.user_id,
                 description=f"{principal.email} replaced their recovery codes")
    await db.commit()
    return RecoveryCodesOut(codes=codes)


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(
    payload: MfaCodeRequest,
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_user(principal)
    if (await load_policy(db, principal.tenant_id)).require_mfa:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Your organization requires multi-factor authentication, so it can't be turned off.")
    try:
        await MfaService(db).disable(principal.user_id, payload.code)
    except MfaError as exc:
        raise _bad(exc) from exc
    await _audit(db, principal, action="mfa.disabled", entity_type="user", entity_id=principal.user_id,
                 description=f"{principal.email} turned off multi-factor authentication")
    await db.commit()


@router.post("/users/{user_id}/mfa/reset", dependencies=[Depends(require_active_subscription)])
async def mfa_admin_reset(
    user_id: uuid.UUID,
    principal: Principal = Depends(require_permission("security.manage")),
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
) -> dict[str, bool | int]:
    """Lost device: remove the user's MFA and end their sessions. They sign in
    with their password and set MFA up again (at once if the policy requires it)."""
    user = await service.users.get_by_id(user_id, principal.tenant_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    removed = await MfaService(db).remove(user.id)
    ended = await service.logout_everywhere(user.id)
    await _audit(db, principal, action="mfa.reset_by_admin", entity_type="user", entity_id=user.id,
                 description=f"{principal.email} reset multi-factor authentication for {user.email}",
                 new_values={"had_mfa": removed, "sessions_ended": ended})
    await db.commit()
    return {"mfa_removed": removed, "sessions_ended": ended}
