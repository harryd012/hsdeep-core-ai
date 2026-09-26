"""
Platform-admin tenant read API (Phase 6.3B follow-up).

A superuser-only read surface for ONE explicitly named tenant, so the Customer
Admin screens can be built panel-by-panel against real data.

Design rules:
  * Authority is the backend: every route depends on ``require_admin``.
  * The tenant is named EXPLICITLY in the path - there is no implicit
    X-Tenant-Id override, so a platform admin can never accidentally act in, or
    read, a tenant they did not name.
  * Every query is scoped by that tenant id. No cross-tenant leakage.
  * Canonical values only: subscription/limits/usage come from
    ``BillingService.get_entitlements`` - the same computation the Usage and
    Billing screens use - so the figures cannot disagree with them.
  * No credential material is ever returned: no api keys, passwords, SNMP
    communities, tokens or connection strings.

Read-only EXCEPT for ONE controlled mutation: ``PATCH /admin/tenants/{tenant_id}``
(rename). It is superuser-gated, scoped to the tenant named in the path, validated,
and audited with before/after values. No second tenant-management surface exists.
"""
from __future__ import annotations

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require_admin
from app.core.db import get_db
from app.models.device import Device
from app.models.discovery_scope import DiscoveryScope
from app.models.permission import Permission
from app.models.role import Role, role_permissions, user_roles
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.audit_service import AuditService
from app.services.billing_service import BillingService, SubscriptionNotFound

logger = logging.getLogger(__name__)

#: Custom-property definition endpoints are mounted onto this SAME router so they
#: inherit the /admin/tenants prefix and the require_admin gate - no second admin
#: surface, no parallel authorization.
from app.api.routes.admin_custom_properties import (  # noqa: E402
    router as custom_properties_router,
)

router = APIRouter(prefix="/admin/tenants", tags=["platform-admin"])
router.include_router(custom_properties_router)


def _billing(db: AsyncSession) -> BillingService:
    return BillingService(
        plans=PlanRepository(db),
        subscriptions=SubscriptionRepository(db),
        db=db,
    )


async def _get_tenant_or_404(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


async def _count(db: AsyncSession, model, tenant_id: uuid.UUID, *where) -> int:
    stmt = select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
    for clause in where:
        stmt = stmt.where(clause)
    return int((await db.execute(stmt)).scalar_one())


async def _subscription_block(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Canonical subscription + limits + usage. Never raises for the admin view."""
    try:
        entitlements = await _billing(db).get_entitlements(tenant_id)
    except SubscriptionNotFound:
        entitlements = None
    except Exception:  # noqa: BLE001 - an admin read must not 500 on one tenant
        logger.warning("Entitlements read failed for tenant %s", tenant_id, exc_info=True)
        entitlements = None

    if entitlements is None:
        return {
            "plan_name": None,
            "plan_tier": None,
            "subscription_status": None,
            "billing_interval": None,
            "current_period_start": None,
            "current_period_end": None,
            "trial_ends_at": None,
            "over_limit": None,
            "over_limit_blocked": None,
            "limits": [],
        }
    return {
        "plan_name": entitlements.get("plan_name"),
        "plan_tier": entitlements.get("plan_tier"),
        "subscription_status": entitlements.get("subscription_status"),
        "billing_interval": entitlements.get("billing_interval"),
        "current_period_start": entitlements.get("current_period_start"),
        "current_period_end": entitlements.get("current_period_end"),
        "trial_ends_at": entitlements.get("trial_ends_at"),
        # True/False only when the canonical service answered; None means unknown.
        "over_limit": entitlements.get("over_limit"),
        "over_limit_blocked": entitlements.get("over_limit_blocked"),
        "limits": entitlements.get("limits") or [],
    }


async def _users_block(db: AsyncSession, tenant_id: uuid.UUID, limit: int = 200) -> dict:
    """Tenant-scoped user list with their role names. READ-ONLY.

    Two bounded queries (users, then user->role pairs) - never one per user.

    Security: `password_hash` and every other credential column is deliberately
    NOT selected. Building the dict from explicit columns rather than the ORM
    object is what guarantees a secret cannot leak into the response even if the
    model later gains new credential fields.
    """
    total = await _count(db, User, tenant_id)
    rows = (
        await db.execute(
            select(
                User.id,
                User.email,
                User.full_name,
                User.is_active,
                User.is_superuser,
                User.last_login_at,
                User.created_at,
            )
            .where(User.tenant_id == tenant_id)
            .order_by(User.email)
            .limit(limit)
        )
    ).all()

    user_ids = [row[0] for row in rows]
    roles_by_user: dict[uuid.UUID, list[str]] = {}
    if user_ids:
        role_rows = (
            await db.execute(
                select(user_roles.c.user_id, Role.name)
                .join(Role, Role.id == user_roles.c.role_id)
                .where(
                    user_roles.c.user_id.in_(user_ids),
                    # Role must belong to the same tenant as the user, so a stale
                    # cross-tenant association can never be displayed.
                    Role.tenant_id == tenant_id,
                )
            )
        ).all()
        for user_id, role_name in role_rows:
            roles_by_user.setdefault(user_id, []).append(role_name)
        for names in roles_by_user.values():
            names.sort()

    users = [
        {
            "id": str(uid),
            "email": email,
            "full_name": full_name,
            "is_active": is_active,
            "is_superuser": is_superuser,
            "last_login_at": last_login.isoformat() if last_login else None,
            "created_at": created_at.isoformat() if created_at else None,
            "roles": roles_by_user.get(uid, []),
        }
        for uid, email, full_name, is_active, is_superuser, last_login, created_at in rows
    ]
    return {"total": total, "returned": len(users), "truncated": total > len(users), "items": users}


async def _rbac_block(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:

    """Tenant-scoped roles with their permissions and user counts.

    Three bounded queries total (roles, role->permission pairs, per-role user
    counts) - never one query per role, per permission or per user, so the panel
    cannot turn into an N+1.

    Roles ARE tenant-scoped, so this reads only the selected tenant's roles.
    Permissions are global by design (see app/models/permission.py: "Permissions
    are not tenant-scoped"), so a permission row may be referenced by roles in
    several tenants - that is the existing contract, not leakage. The role rows
    that expose them here are strictly tenant-scoped, and user counts are
    restricted to this tenant's users.
    """
    roles = list(
        (
            await db.execute(
                select(Role)
                .where(Role.tenant_id == tenant_id, Role.is_deleted.is_(False))
                .order_by(Role.name)
            )
        )
        .scalars()
        .all()
    )
    if not roles:
        return []

    role_ids = [r.id for r in roles]

    perms_by_role: dict[uuid.UUID, list[dict]] = {}
    perm_rows = (
        await db.execute(
            select(
                role_permissions.c.role_id,
                Permission.id,
                Permission.code,
                Permission.description,
            )
            .join(Permission, Permission.id == role_permissions.c.permission_id)
            .where(role_permissions.c.role_id.in_(role_ids))
        )
    ).all()
    for role_id, perm_id, code, description in perm_rows:
        perms_by_role.setdefault(role_id, []).append(
            {
                "id": str(perm_id),
                "key": code,
                "name": code,
                "description": description,
            }
        )
    for entries in perms_by_role.values():
        entries.sort(key=lambda p: p["key"])

    # User counts, scoped to THIS tenant's users so a role shared by name across
    # tenants cannot inflate the count.
    counts: dict[uuid.UUID, int] = {}
    count_rows = (
        await db.execute(
            select(user_roles.c.role_id, func.count())
            .join(User, User.id == user_roles.c.user_id)
            .where(user_roles.c.role_id.in_(role_ids), User.tenant_id == tenant_id)
            .group_by(user_roles.c.role_id)
        )
    ).all()
    for role_id, total in count_rows:
        counts[role_id] = int(total)

    return [
        {
            "id": str(role.id),
            "name": role.name,
            "description": role.description,
            "scope": "tenant",
            "user_count": counts.get(role.id, 0),
            "permissions": perms_by_role.get(role.id, []),
        }
        for role in roles
    ]


def _scopes_block(scopes: list[DiscoveryScope]) -> list[dict]:

    """Safe scope projection: configured/not, never credential contents."""
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "scope_type": s.scope_type,
            "target": s.target,
            "enabled": s.enabled,
            "allowed_protocols": list(s.allowed_protocols or []),
            "credential_configured": s.credential_profile_id is not None,
            "status": s.status,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        }
        for s in scopes
    ]


@router.get("")
async def list_tenants(
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Every tenant, minimal profile only (id resolution for the detail view)."""
    tenants = list((await db.execute(select(Tenant).order_by(Tenant.name))).scalars().all())
    return [
        {
            "tenant_id": str(t.id),
            "name": t.name,
            "slug": t.slug,
            "is_active": t.is_active,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tenants
    ]


@router.get("/{tenant_id}")
async def get_tenant_detail(
    tenant_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Full read view of ONE tenant, named explicitly in the path.

    Read-only. Returns profile, canonical subscription/limits/usage, resource
    counts and discovery scopes. Never returns credential material.
    """
    tenant = await _get_tenant_or_404(db, tenant_id)

    users_total = await _count(db, User, tenant_id)
    users_active = await _count(db, User, tenant_id, User.is_active.is_(True))
    roles_total = await _count(db, Role, tenant_id)
    devices_total = await _count(db, Device, tenant_id, Device.is_deleted.is_(False))

    scopes = list(
        (
            await db.execute(
                select(DiscoveryScope).where(
                    DiscoveryScope.tenant_id == tenant_id,
                    DiscoveryScope.is_deleted.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )

    last_login = (
        await db.execute(
            select(func.max(User.last_login_at)).where(User.tenant_id == tenant_id)
        )
    ).scalar_one()

    subscription = await _subscription_block(db, tenant_id)

    # Usage comes from the canonical entitlements limits, so the Customer Admin
    # view and the Usage screen cannot disagree. A real 0 stays 0.
    usage = {
        entry["resource"]: {
            "label": entry["label"],
            "used": entry["used"],
            "limit": entry["limit"],
            "pct": entry["pct"],
            "over_limit": entry["over_limit"],
        }
        for entry in subscription["limits"]
    }

    return {
        "profile": {
            "tenant_id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "is_active": tenant.is_active,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
            "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
            # Absent on the canonical Tenant model: reported null, never invented.
            "display_name": None,
            "legal_name": None,
            "industry": None,
            "country": None,
            "timezone": None,
            "locale": None,
            "contacts": None,
            "branding": None,
            "custom_properties": None,
        },
        "subscription": subscription,
        "usage": usage,
        "counts": {
            "users_total": users_total,
            "users_active": users_active,
            "users_inactive": users_total - users_active,
            "roles_total": roles_total,
            "devices_total": devices_total,
            "discovery_scopes_total": len(scopes),
        },
        "last_activity_at": last_login.isoformat() if last_login else None,
        "discovery_scopes": _scopes_block(scopes),
        "roles": await _rbac_block(db, tenant_id),
        "users": await _users_block(db, tenant_id),
    }


# ---------------------------------------------------------------------------
# Tenant rename (Phase 6.3F) - the ONE controlled mutation on this surface
# ---------------------------------------------------------------------------

#: Slug shape: lowercase alphanumerics in dash-separated groups. Deliberately
#: strict - a slug appears in URLs and must never carry wildcards, dots or
#: traversal sequences.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

MAX_TENANT_NAME_LENGTH = 255
MAX_TENANT_SLUG_LENGTH = 100


class TenantRenamePayload(BaseModel):
    """Rename payload. At least one of `name`/`slug` must be supplied."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_TENANT_NAME_LENGTH)
    slug: str | None = Field(default=None, max_length=MAX_TENANT_SLUG_LENGTH)


def _validate_name(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="name must not be empty",
        )
    if len(value) > MAX_TENANT_NAME_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"name must be at most {MAX_TENANT_NAME_LENGTH} characters",
        )
    # Control characters would corrupt logs, UI rendering and audit records.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="name must not contain control characters",
        )
    return value


def _validate_slug(raw: str) -> str:
    # Deliberately NOT lower-cased. This is an explicit admin mutation, so the
    # value is taken exactly as sent: the audit before/after then reflects the
    # request, and a mistyped case is a clear 422 instead of a silent rewrite.
    # (Tenant *generation* elsewhere uses billing._slugify, which lower-cases -
    # that is a non-interactive path with no audit obligation.)
    value = (raw or "").strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="slug must not be empty",
        )
    if len(value) > MAX_TENANT_SLUG_LENGTH or not _SLUG_RE.match(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "slug must be lowercase letters, digits and single dashes "
                f"(max {MAX_TENANT_SLUG_LENGTH} characters)"
            ),
        )
    return value



@router.patch("/{tenant_id}")
async def rename_tenant(
    tenant_id: uuid.UUID,
    body: TenantRenamePayload,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Rename ONE tenant, named explicitly in the path.

    The only mutation on the admin tenant surface. It reuses the platform's
    existing authorization (`require_admin`), audit (`AuditService.record`) and
    session patterns - there is no second tenant-management mechanism.

    Scope: the tenant is named in the path, so the operation can only ever touch
    that tenant. A soft-deleted tenant is refused, and the write is audited with
    canonical before/after values.
    """
    if body.name is None and body.slug is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provide name and/or slug",
        )

    tenant = await _get_tenant_or_404(db, tenant_id)

    # A deleted tenant must not be mutated. Reported as 404 so the endpoint never
    # confirms the existence of a deleted record.
    if tenant.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    new_name = _validate_name(body.name) if body.name is not None else tenant.name
    new_slug = _validate_slug(body.slug) if body.slug is not None else tenant.slug

    before = {"name": tenant.name, "slug": tenant.slug}
    after = {"name": new_name, "slug": new_slug}

    # No-op: nothing is written and nothing is audited, so the audit trail stays
    # meaningful (it only ever records real changes).
    if before == after:
        return {
            "tenant_id": str(tenant.id),
            "changed": False,
            "before": before,
            "after": before,
        }

    # `tenants.slug` is globally unique. Checking here turns what would be an
    # IntegrityError into a clean 409 with a usable message.
    if new_slug != tenant.slug:
        clash = (
            await db.execute(
                select(Tenant.id).where(Tenant.slug == new_slug, Tenant.id != tenant.id)
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A tenant with slug {new_slug!r} already exists",
            )

    tenant.name = new_name
    tenant.slug = new_slug
    await db.flush()

    await AuditService(db).record(
        tenant_id=tenant.id,
        action="tenant.renamed",
        actor_id=principal.user_id,
        actor_email=principal.email,
        entity_type="tenant",
        entity_id=tenant.id,
        description=f"Tenant renamed from {before['name']!r} to {new_name!r}",
        old_values=before,
        new_values=after,
    )
    await db.commit()
    await db.refresh(tenant)

    return {
        "tenant_id": str(tenant.id),
        "changed": True,
        "before": before,
        "after": {"name": tenant.name, "slug": tenant.slug},
    }



# ---------------------------------------------------------------------------
# Tenant lifecycle: suspend / reactivate (Phase 6.4A)
# ---------------------------------------------------------------------------


class TenantStatusPayload(BaseModel):
    """Lifecycle toggle. Reuses the existing `tenants.is_active` column."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool


@router.patch("/{tenant_id}/status")
async def set_tenant_status(
    tenant_id: uuid.UUID,
    body: TenantStatusPayload,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Suspend or reactivate ONE tenant, named explicitly in the path.

    Suspension is a LIFECYCLE STATE ONLY: it flips ``tenants.is_active``. Nothing
    the tenant owns is touched - users, roles, permissions, custom-property
    definitions, discovery scopes, devices, topology, incidents and audit records
    all remain intact, and no billing record is modified.

    There is no second status column and no second lifecycle system: this reuses
    the existing model, `require_admin` gate and `AuditService`.
    """
    tenant = await _get_tenant_or_404(db, tenant_id)

    # A deleted tenant has no lifecycle to change, and reporting 404 means the
    # endpoint never confirms that a deleted record exists.
    if tenant.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    before = {"is_active": bool(tenant.is_active)}
    after = {"is_active": bool(body.is_active)}

    # No-op: no write and NO audit event, so the trail only ever records real
    # state transitions.
    if before == after:
        return {
            "tenant_id": str(tenant.id),
            "changed": False,
            "before": before,
            "after": before,
        }

    tenant.is_active = body.is_active
    # flush() first: if the UPDATE fails it raises here, so the audit record below
    # is never written. Both then commit in the same transaction, so a commit
    # failure rolls the audit entry back too.
    await db.flush()

    await AuditService(db).record(
        tenant_id=tenant.id,
        action="tenant.status_changed",
        actor_id=principal.user_id,
        actor_email=principal.email,
        entity_type="tenant",
        entity_id=tenant.id,
        description=(
            "Tenant suspended" if not body.is_active else "Tenant reactivated"
        ),
        old_values=before,
        new_values=after,
    )
    await db.commit()
    await db.refresh(tenant)

    return {
        "tenant_id": str(tenant.id),
        "changed": True,
        "before": before,
        "after": {"is_active": bool(tenant.is_active)},
    }

