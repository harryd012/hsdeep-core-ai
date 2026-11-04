"""
SCIM 2.0 user provisioning (enterprise security programme S6, RFC 7643/7644).

Base URL: {API_PUBLIC_URL}/api/scim/v2, authenticated with the tenant's SCIM
bearer token (Settings > Single sign-on). Your identity provider (Entra ID,
Okta, OneLogin, JumpCloud …) creates, updates, deactivates and deletes users:

  GET    /ServiceProviderConfig
  GET    /Users?filter=userName eq "x"&startIndex=1&count=100
  GET    /Users/{id}
  POST   /Users
  PUT    /Users/{id}
  PATCH  /Users/{id}        (active, name, displayName; Entra and Okta forms)
  DELETE /Users/{id}

Deactivating or deleting a user ends their sessions at once. New users get the
SSO configuration's default role and no password (they sign in with SSO).
Every change is an audit event with actor type "scim". Groups are not
supported yet (assign roles in the platform).
"""
from __future__ import annotations

import hashlib
import re
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.identity import TenantSsoConfig, UserIdentity
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditService

router = APIRouter(prefix="/scim/v2", tags=["scim"])

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
MEDIA = "application/scim+json"


def _error(code: int, detail: str, scim_type: str | None = None) -> HTTPException:
    body = {"schemas": [ERROR_SCHEMA], "status": str(code), "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return HTTPException(status_code=code, detail=body)


async def scim_tenant(authorization: str | None = Header(None), db: AsyncSession = Depends(get_db)) -> TenantSsoConfig:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _error(401, "Missing bearer token")
    token = authorization[7:].strip()
    cfg = (await db.execute(select(TenantSsoConfig).where(
        TenantSsoConfig.scim_token_hash == hashlib.sha256(token.encode()).hexdigest()
    ))).scalar_one_or_none()
    if cfg is None:
        raise _error(401, "Invalid SCIM token")
    return cfg


def _scim(body: dict, code: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=code, media_type=MEDIA)


def _user_resource(user: User, request: Request, external_id: str | None) -> dict:
    given, _, family = (user.full_name or "").partition(" ")
    base = str(request.base_url).rstrip("/")
    return {
        "schemas": [USER_SCHEMA],
        "id": str(user.id),
        "externalId": external_id,
        "userName": user.email,
        "name": {"givenName": given or None, "familyName": family or None, "formatted": user.full_name},
        "displayName": user.full_name,
        "emails": [{"value": user.email, "primary": True, "type": "work"}],
        "active": bool(user.is_active),
        "meta": {"resourceType": "User", "created": user.created_at.isoformat() if user.created_at else None,
                 "lastModified": user.updated_at.isoformat() if user.updated_at else None,
                 "location": f"{base}/api/scim/v2/Users/{user.id}"},
    }


async def _external_id(db: AsyncSession, cfg: TenantSsoConfig, user_id: uuid.UUID) -> str | None:
    return (await db.execute(select(UserIdentity.subject).where(
        UserIdentity.tenant_id == cfg.tenant_id, UserIdentity.user_id == user_id, UserIdentity.provider == "scim"
    ))).scalar_one_or_none()


async def _get_user(db: AsyncSession, cfg: TenantSsoConfig, user_id: str) -> User:
    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise _error(404, "User not found") from exc
    user = (await db.execute(select(User).where(User.id == uid, User.tenant_id == cfg.tenant_id, User.is_deleted.is_(False)))).scalar_one_or_none()
    if user is None:
        raise _error(404, "User not found")
    return user


def _name_from(body: dict) -> str | None:
    name = body.get("name") or {}
    full = " ".join(p for p in (name.get("givenName"), name.get("familyName")) if p) or name.get("formatted") or body.get("displayName")
    return full.strip()[:255] if isinstance(full, str) and full.strip() else None


def _email_from(body: dict) -> str | None:
    emails = body.get("emails") or []
    primary = next((e.get("value") for e in emails if e.get("primary")), None) or (emails[0].get("value") if emails else None)
    value = body.get("userName") or primary
    return value.strip().lower() if isinstance(value, str) and "@" in value else None


async def _audit(db, cfg: TenantSsoConfig, action: str, user: User, description: str, new: dict | None = None) -> None:
    await AuditService(db).record(tenant_id=cfg.tenant_id, action=action, actor_type="scim", actor_email="scim",
                                  entity_type="user", entity_id=user.id, description=description, new_values=new)


async def _set_active(db, user: User, active: bool) -> bool:
    if bool(user.is_active) == active:
        return False
    user.is_active = active
    if not active:
        await RefreshTokenRepository(db).revoke_all_for_user(user.id)
        from app.core import auth

        auth.forget_session()
    return True


@router.get("/ServiceProviderConfig")
async def service_provider_config(_: TenantSsoConfig = Depends(scim_tenant)):
    return _scim({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200}, "changePassword": {"supported": False},
        "sort": {"supported": False}, "etag": {"supported": False},
        "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token", "description": "SCIM token from Settings > Single sign-on"}],
    })


_FILTER = re.compile(r'^\s*(userName|externalId|emails\.value)\s+eq\s+"([^"]+)"\s*$', re.I)


@router.get("/Users")
async def list_users(
    request: Request,
    filter: str | None = Query(None, max_length=500),  # noqa: A002 - SCIM parameter name
    startIndex: int = Query(1, ge=1),  # noqa: N803 - SCIM parameter name
    count: int = Query(100, ge=0, le=200),
    cfg: TenantSsoConfig = Depends(scim_tenant),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User).where(User.tenant_id == cfg.tenant_id, User.is_deleted.is_(False))
    if filter:
        m = _FILTER.match(filter)
        if not m:
            raise _error(400, "Only 'userName eq', 'externalId eq' and 'emails.value eq' filters are supported", "invalidFilter")
        attr, value = m.group(1).lower(), m.group(2)
        if attr == "externalid":
            stmt = stmt.join(UserIdentity, (UserIdentity.user_id == User.id) & (UserIdentity.provider == "scim")).where(UserIdentity.subject == value)
        else:
            stmt = stmt.where(func.lower(User.email) == value.lower())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    users = (await db.execute(stmt.order_by(User.created_at, User.id).offset(startIndex - 1).limit(count))).scalars().all()
    resources = [_user_resource(u, request, await _external_id(db, cfg, u.id)) for u in users]
    return _scim({"schemas": [LIST_SCHEMA], "totalResults": total, "startIndex": startIndex,
                  "itemsPerPage": len(resources), "Resources": resources})


@router.get("/Users/{user_id}")
async def get_user(user_id: str, request: Request, cfg: TenantSsoConfig = Depends(scim_tenant), db: AsyncSession = Depends(get_db)):
    user = await _get_user(db, cfg, user_id)
    return _scim(_user_resource(user, request, await _external_id(db, cfg, user.id)))


@router.post("/Users")
async def create_user(request: Request, cfg: TenantSsoConfig = Depends(scim_tenant), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    email = _email_from(body)
    if not email:
        raise _error(400, "userName (an email address) is required", "invalidValue")
    users = UserRepository(db)
    if await users.get_by_email(cfg.tenant_id, email) is not None:
        raise _error(409, f"A user with userName {email} already exists", "uniqueness")
    user = await users.create(tenant_id=cfg.tenant_id, email=email, password_hash=None, full_name=_name_from(body), is_superuser=False)
    if body.get("active") is False:
        user.is_active = False
    if cfg.default_role_id:
        roles = await RoleRepository(db).get_many_by_ids(cfg.tenant_id, [cfg.default_role_id])
        if roles:
            await users.assign_roles(user, roles)
    external = body.get("externalId")
    if isinstance(external, str) and external:
        db.add(UserIdentity(tenant_id=cfg.tenant_id, user_id=user.id, provider="scim", subject=external[:255]))
    await db.flush()
    await _audit(db, cfg, "scim.user_created", user, f"{email} provisioned by SCIM", {"email": email, "active": bool(user.is_active)})
    await db.commit()
    await db.refresh(user)
    return _scim(_user_resource(user, request, external if isinstance(external, str) else None), 201)


@router.put("/Users/{user_id}")
async def replace_user(user_id: str, request: Request, cfg: TenantSsoConfig = Depends(scim_tenant), db: AsyncSession = Depends(get_db)):
    user = await _get_user(db, cfg, user_id)
    body = await request.json()
    changes: dict = {}
    name = _name_from(body)
    if name is not None and name != user.full_name:
        user.full_name = name
        changes["name"] = name
    if "active" in body and await _set_active(db, user, bool(body["active"])):
        changes["active"] = bool(body["active"])
    if changes:
        await _audit(db, cfg, "scim.user_updated", user, f"{user.email} updated by SCIM: {', '.join(sorted(changes))}", changes)
    await db.commit()
    await db.refresh(user)
    return _scim(_user_resource(user, request, await _external_id(db, cfg, user.id)))


def _bool(value) -> bool:
    return value.strip().lower() == "true" if isinstance(value, str) else bool(value)


@router.patch("/Users/{user_id}")
async def patch_user(user_id: str, request: Request, cfg: TenantSsoConfig = Depends(scim_tenant), db: AsyncSession = Depends(get_db)):
    user = await _get_user(db, cfg, user_id)
    body = await request.json()
    changes: dict = {}
    for op in body.get("Operations") or []:
        kind = str(op.get("op", "")).lower()
        if kind not in ("replace", "add"):
            continue
        path, value = op.get("path"), op.get("value")
        # Entra: {"op": "Replace", "path": "active", "value": "False"};
        # Okta: {"op": "replace", "value": {"active": false}}.
        items = {path: value} if path else (value if isinstance(value, dict) else {})
        for key, val in items.items():
            k = str(key)
            if k == "active":
                if await _set_active(db, user, _bool(val)):
                    changes["active"] = _bool(val)
            elif k in ("displayName", "name.formatted") and isinstance(val, str) and val.strip():
                user.full_name = val.strip()[:255]
                changes["name"] = user.full_name
            elif k == "name" and isinstance(val, dict):
                name = _name_from({"name": val})
                if name:
                    user.full_name = name
                    changes["name"] = name
    if changes:
        await _audit(db, cfg, "scim.user_updated", user, f"{user.email} updated by SCIM: {', '.join(sorted(changes))}", changes)
    await db.commit()
    await db.refresh(user)
    return _scim(_user_resource(user, request, await _external_id(db, cfg, user.id)))


@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, cfg: TenantSsoConfig = Depends(scim_tenant), db: AsyncSession = Depends(get_db)):
    user = await _get_user(db, cfg, user_id)
    await _set_active(db, user, False)
    await UserRepository(db).soft_delete(user)
    await _audit(db, cfg, "scim.user_deleted", user, f"{user.email} removed by SCIM")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
