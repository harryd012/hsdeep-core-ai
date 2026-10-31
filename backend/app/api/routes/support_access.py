"""
Break-glass support access API (R1.2).

Superusers open, list and close their own grants; a customer tenant's users
see the grants on their tenant and its admins can revoke them. Grant routes
always act on the caller's OWN identity, never through an X-Tenant-Id
override, so opening a grant cannot itself require one.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, require_permission
from app.core.db import get_db
from app.services.support_access import SupportAccessError, SupportAccessService, grant_status

router = APIRouter(prefix="/support-access", tags=["support-access"])


class GrantCreate(BaseModel):
    tenant_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)
    duration_minutes: int | None = None


class GrantOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_email: str
    reason: str
    expires_at: datetime
    revoked_at: datetime | None
    revoked_by_email: str | None
    created_at: datetime
    status: str


def _out(g) -> GrantOut:
    return GrantOut(
        id=g.id, tenant_id=g.tenant_id, user_email=g.user_email, reason=g.reason, expires_at=g.expires_at,
        revoked_at=g.revoked_at, revoked_by_email=g.revoked_by_email, created_at=g.created_at, status=grant_status(g),
    )


def _home_tenant(request: Request, principal: Principal) -> uuid.UUID:
    """The caller's own tenant. A request already running under an override
    is refused: grants are managed from the support user's own context."""
    token_tenant = getattr(request.state, "token_tenant_id", None)
    if token_tenant is not None and token_tenant != principal.tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Manage support grants from your own tenant, not under a tenant override.")
    return principal.tenant_id


def _require_superuser(principal: Principal) -> None:
    if not principal.is_superuser or principal.user_id.int == 0:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Support access is for platform support accounts")


@router.post("/grants", response_model=GrantOut, status_code=status.HTTP_201_CREATED)
async def open_grant(
    payload: GrantCreate,
    request: Request,
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_superuser(principal)
    home = _home_tenant(request, principal)
    try:
        grant = await SupportAccessService(db).create(
            user_id=principal.user_id, user_email=principal.email, home_tenant_id=home,
            tenant_id=payload.tenant_id, reason=payload.reason, duration_minutes=payload.duration_minutes,
            ip_address=request.client.host if request.client else None,
        )
    except SupportAccessError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    await db.commit()
    return _out(grant)


@router.get("/grants/mine", response_model=list[GrantOut])
async def my_grants(
    active: bool = True,
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_superuser(principal)
    return [_out(g) for g in await SupportAccessService(db).list_for_user(principal.user_id, active_only=active)]


@router.get("/grants/tenant", response_model=list[GrantOut], dependencies=[Depends(require_permission("users.read"))])
async def grants_on_my_tenant(principal: Principal = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Customer view: every support grant opened on this tenant."""
    return [_out(g) for g in await SupportAccessService(db).list_for_tenant(principal.tenant_id)]


@router.post("/grants/{grant_id}/revoke", response_model=GrantOut)
async def revoke_grant(
    grant_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = SupportAccessService(db)
    grant = await service.get(grant_id)
    is_holder = grant is not None and grant.user_id == principal.user_id
    is_customer_admin = (
        grant is not None and grant.tenant_id == principal.tenant_id and principal.has_permission("users.update")
    )
    if grant is None or not (is_holder or is_customer_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found")
    await service.revoke(grant, actor_id=principal.user_id if principal.user_id.int else None,
                         actor_email=principal.email, ip_address=request.client.host if request.client else None)
    await db.commit()
    return _out(grant)
