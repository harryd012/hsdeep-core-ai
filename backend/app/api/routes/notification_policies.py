"""
Notification policies (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P9 slice 1).

  GET    /api/settings/notifications/policies
  POST   /api/settings/notifications/policies
  PATCH  /api/settings/notifications/policies/{id}
  DELETE /api/settings/notifications/policies/{id}
  POST   /api/settings/notifications/policies/{id}/test

Admin only, like the other notification settings; writes need an active
subscription. A Teams webhook must be a Microsoft Teams or Workflows https URL;
it is stored encrypted and only ever returned masked (its last 4 characters).
Every change is audited without the webhook.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_admin
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.core.security import SecretCrypto
from app.models.notification_policy import MAX_POLICIES_PER_TENANT, NotificationPolicy
from app.models.notification_setting import NotificationSetting
from app.models.site import Site
from app.services.audit_service import AuditService
from app.services.notifications.dispatcher import NotificationDispatcher, is_allowed_teams_webhook

router = APIRouter(prefix="/settings/notifications/policies", tags=["settings"])
_active_sub_guard = Depends(require_active_subscription)

SEVERITIES = ("critical", "warning", "info")
PRIORITIES = ("P1", "P2", "P3", "P4")
CHANNELS = ("email", "slack", "pagerduty")
_MASK = "••••"


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=100)
    enabled: bool | None = None
    position: int | None = Field(None, ge=0, le=10000)
    min_severity: str | None = None
    priorities: list[str] | None = Field(None, max_length=4)
    site_ids: list[uuid.UUID] | None = Field(None, max_length=100)
    channels: list[str] | None = Field(None, max_length=3)
    #: Comma-separated; "" clears (the tenant's recipients are used).
    email_recipients: str | None = Field(None, max_length=2000)
    #: A new webhook URL; "" removes it; a masked value keeps the stored one.
    teams_webhook_url: str | None = Field(None, max_length=2048)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if v is not None and not v.strip():
            raise ValueError("name must not be blank")
        return v.strip() if v else v

    @field_validator("min_severity")
    @classmethod
    def _sev(cls, v):
        if v is not None and v not in SEVERITIES:
            raise ValueError(f"min_severity must be one of {SEVERITIES}")
        return v

    @field_validator("priorities")
    @classmethod
    def _prio(cls, v):
        if v is not None and not set(v) <= set(PRIORITIES):
            raise ValueError(f"priorities must be among {PRIORITIES}")
        return None if v is None else sorted(set(v))

    @field_validator("channels")
    @classmethod
    def _chan(cls, v):
        if v is not None and not set(v) <= set(CHANNELS):
            raise ValueError(f"channels must be among {CHANNELS}")
        return None if v is None else sorted(set(v))

    @field_validator("email_recipients")
    @classmethod
    def _emails(cls, v):
        if not v:
            return v
        parts = [p.strip() for p in v.split(",") if p.strip()]
        if len(parts) > 50 or any("@" not in p or " " in p for p in parts):
            raise ValueError("email_recipients must be up to 50 comma-separated addresses")
        return ", ".join(parts)

    @field_validator("teams_webhook_url")
    @classmethod
    def _teams(cls, v):
        if v and not v.startswith(_MASK) and not is_allowed_teams_webhook(v):
            raise ValueError("Teams webhook must be an https URL on webhook.office.com, logic.azure.com or api.powerplatform.com")
        return v


class PolicyOut(BaseModel):
    id: uuid.UUID
    name: str
    enabled: bool
    position: int
    min_severity: str
    priorities: list[str]
    site_ids: list[str]
    channels: list[str]
    email_recipients: str | None
    teams_webhook: str | None
    created_at: datetime
    updated_at: datetime


def _out(p: NotificationPolicy) -> PolicyOut:
    return PolicyOut(
        id=p.id, name=p.name, enabled=p.enabled, position=p.position, min_severity=p.min_severity,
        priorities=p.priorities or [], site_ids=p.site_ids or [], channels=p.channels or [],
        email_recipients=p.email_recipients,
        teams_webhook=f"{_MASK}{p.teams_webhook_hint}" if p.teams_webhook_encrypted else None,
        created_at=p.created_at, updated_at=p.updated_at,
    )


def _snapshot(p: NotificationPolicy) -> dict:
    """Audit view of a policy: never the webhook, only whether one is set."""
    return {
        "name": p.name, "enabled": p.enabled, "min_severity": p.min_severity, "priorities": p.priorities,
        "site_ids": p.site_ids, "channels": p.channels, "email_recipients": p.email_recipients,
        "teams_webhook": bool(p.teams_webhook_encrypted),
    }


async def _check_sites(db: AsyncSession, tenant_id: uuid.UUID, site_ids: list[uuid.UUID] | None) -> None:
    if not site_ids:
        return
    found = set((await db.execute(select(Site.id).where(Site.tenant_id == tenant_id, Site.id.in_(site_ids)))).scalars())
    if set(site_ids) - found:
        raise HTTPException(status_code=422, detail="A site does not exist in your organization")


def _apply(p: NotificationPolicy, body: PolicyIn) -> None:
    data = body.model_dump(exclude_unset=True)
    for field in ("name", "enabled", "position", "min_severity", "priorities", "channels"):
        if field in data and data[field] is not None:
            setattr(p, field, data[field])
    if "site_ids" in data and data["site_ids"] is not None:
        p.site_ids = sorted({str(s) for s in data["site_ids"]})
    if "email_recipients" in data:
        p.email_recipients = data["email_recipients"] or None
    if "teams_webhook_url" in data:
        url = data["teams_webhook_url"]
        if not url:
            p.teams_webhook_encrypted, p.teams_webhook_hint = None, None
        elif not url.startswith(_MASK):
            p.teams_webhook_encrypted, p.teams_webhook_hint = SecretCrypto().encrypt(url), url[-4:]


async def _get(db: AsyncSession, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> NotificationPolicy:
    p = (await db.execute(select(NotificationPolicy).where(
        NotificationPolicy.id == policy_id, NotificationPolicy.tenant_id == tenant_id
    ))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return p


@router.get("", response_model=list[PolicyOut])
async def list_policies(
    tenant_id: uuid.UUID = Depends(get_tenant_id), _: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(NotificationPolicy).where(NotificationPolicy.tenant_id == tenant_id)
        .order_by(NotificationPolicy.position, NotificationPolicy.name)
    )).scalars().all()
    return [_out(p) for p in rows]


@router.post("", response_model=PolicyOut, status_code=status.HTTP_201_CREATED, dependencies=[_active_sub_guard])
async def create_policy(
    body: PolicyIn, tenant_id: uuid.UUID = Depends(get_tenant_id), principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if not body.name:
        raise HTTPException(status_code=422, detail="name is required")
    count = (await db.execute(select(func.count()).select_from(NotificationPolicy).where(NotificationPolicy.tenant_id == tenant_id))).scalar_one()
    if count >= MAX_POLICIES_PER_TENANT:
        raise HTTPException(status_code=409, detail=f"At most {MAX_POLICIES_PER_TENANT} policies per organization")
    if (await db.execute(select(NotificationPolicy.id).where(NotificationPolicy.tenant_id == tenant_id, NotificationPolicy.name == body.name))).first():
        raise HTTPException(status_code=409, detail=f"A policy named '{body.name}' already exists")
    await _check_sites(db, tenant_id, body.site_ids)
    p = NotificationPolicy(tenant_id=tenant_id, name=body.name, enabled=True, position=0, min_severity="warning",
                           priorities=[], site_ids=[], channels=[])
    _apply(p, body)
    db.add(p)
    await db.flush()
    await AuditService(db).record_entity(
        tenant_id=tenant_id, action="notification_policy.created", entity_type="notification_policy", entity_id=p.id,
        actor_id=principal.user_id, actor_email=principal.email, description=f"Notification policy '{p.name}' created",
        new_values=_snapshot(p),
    )
    await db.commit()
    await db.refresh(p)
    return _out(p)


@router.patch("/{policy_id}", response_model=PolicyOut, dependencies=[_active_sub_guard])
async def update_policy(
    policy_id: uuid.UUID, body: PolicyIn, tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    p = await _get(db, tenant_id, policy_id)
    if body.name and body.name != p.name and (await db.execute(select(NotificationPolicy.id).where(
        NotificationPolicy.tenant_id == tenant_id, NotificationPolicy.name == body.name
    ))).first():
        raise HTTPException(status_code=409, detail=f"A policy named '{body.name}' already exists")
    await _check_sites(db, tenant_id, body.site_ids)
    before = _snapshot(p)
    _apply(p, body)
    after = _snapshot(p)
    if after != before:
        changed = {k for k in after if after[k] != before[k]}
        await AuditService(db).record_entity(
            tenant_id=tenant_id, action="notification_policy.updated", entity_type="notification_policy", entity_id=p.id,
            actor_id=principal.user_id, actor_email=principal.email,
            description=f"Notification policy '{p.name}' changed: {', '.join(sorted(changed))}",
            old_values={k: before[k] for k in changed}, new_values={k: after[k] for k in changed},
        )
    await db.commit()
    await db.refresh(p)
    return _out(p)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_active_sub_guard])
async def delete_policy(
    policy_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_tenant_id), principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    p = await _get(db, tenant_id, policy_id)
    snapshot = _snapshot(p)
    await db.delete(p)
    await AuditService(db).record_entity(
        tenant_id=tenant_id, action="notification_policy.deleted", entity_type="notification_policy", entity_id=policy_id,
        actor_id=principal.user_id, actor_email=principal.email, description=f"Notification policy '{snapshot['name']}' deleted",
        old_values=snapshot,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{policy_id}/test", dependencies=[_active_sub_guard])
async def test_policy(
    policy_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_tenant_id), _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """A labelled test message to the policy's destinations; nothing is recorded as a delivery."""
    p = await _get(db, tenant_id, policy_id)
    setting = (await db.execute(select(NotificationSetting).where(NotificationSetting.tenant_id == tenant_id))).scalar_one_or_none()
    results = await NotificationDispatcher(db).send_policy_test(p, setting)
    await db.commit()  # email tests are queued in the outbox
    return results
