"""
Per-tenant IP allowlist API (enterprise security programme S3).

  GET    /api/settings/network/my-ip          the caller's address as the platform sees it
  GET    /api/settings/network/ip-rules       the tenant's rules (security.manage)
  POST   /api/settings/network/ip-rules       add a network (security.manage)
  DELETE /api/settings/network/ip-rules/{id}  remove one (security.manage)

No rules = open to every network. Adding or removing a rule is refused if the
resulting allowlist would not include the caller's own address, so an admin
cannot lock themselves (and the tenant's other admins on the same network)
out. Every change is audited.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, require_permission
from app.core.client_ip import client_ip
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.models.tenant_ip_rule import MAX_IP_RULES_PER_TENANT, TenantIpRule
from app.services import network_policy
from app.services.audit_service import AuditService

router = APIRouter(prefix="/settings/network", tags=["settings"])


class IpRuleCreate(BaseModel):
    cidr: str = Field(..., min_length=2, max_length=64, description="An address (203.0.113.7) or range (10.0.0.0/8)")
    label: str | None = Field(None, max_length=100)


class IpRuleOut(BaseModel):
    id: uuid.UUID
    cidr: str
    label: str | None
    created_at: str


def _out(rule: TenantIpRule) -> IpRuleOut:
    return IpRuleOut(id=rule.id, cidr=rule.cidr, label=rule.label, created_at=rule.created_at.isoformat())


async def _rules(db: AsyncSession, tenant_id: uuid.UUID) -> list[TenantIpRule]:
    return list((await db.execute(
        select(TenantIpRule).where(TenantIpRule.tenant_id == tenant_id).order_by(TenantIpRule.created_at)
    )).scalars())


def _lockout_guard(cidrs: list[str], ip: str | None) -> None:
    import ipaddress

    nets = [ipaddress.ip_network(c) for c in cidrs]
    if nets and not network_policy.ip_in(nets, ip):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"This would block your own address ({ip or 'unknown'}). Add a rule that includes it first.",
        )


@router.get("/my-ip")
async def my_ip(request: Request, _: Principal = Depends(get_current_user)) -> dict[str, str | None]:
    return {"ip_address": client_ip(request)}


@router.get("/ip-rules", response_model=list[IpRuleOut])
async def list_ip_rules(
    principal: Principal = Depends(require_permission("security.manage")),
    db: AsyncSession = Depends(get_db),
):
    return [_out(r) for r in await _rules(db, principal.tenant_id)]


@router.post("/ip-rules", response_model=IpRuleOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_active_subscription)])
async def add_ip_rule(
    payload: IpRuleCreate,
    request: Request,
    principal: Principal = Depends(require_permission("security.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        cidr = network_policy.normalize_cidr(payload.cidr)
    except network_policy.InvalidNetwork as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    existing = await _rules(db, principal.tenant_id)
    if any(r.cidr == cidr for r in existing):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{cidr} is already allowed")
    if len(existing) >= MAX_IP_RULES_PER_TENANT:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"At most {MAX_IP_RULES_PER_TENANT} rules")
    ip = client_ip(request)
    _lockout_guard([r.cidr for r in existing] + [cidr], ip)
    rule = TenantIpRule(
        tenant_id=principal.tenant_id, cidr=cidr, label=(payload.label or "").strip() or None,
        created_by=principal.user_id if principal.user_id.int else None,
    )
    db.add(rule)
    await db.flush()
    await AuditService(db).record_entity(
        tenant_id=principal.tenant_id, action="network.ip_rule_added", entity_type="ip_rule", entity_id=rule.id,
        actor_id=principal.user_id, actor_email=principal.email,
        description=f"Allowed network {cidr}{' (' + rule.label + ')' if rule.label else ''}"
                    + (" - the allowlist is now enforced" if not existing else ""),
        new_values={"cidr": cidr, "label": rule.label, "rules": len(existing) + 1},
    )
    await db.commit()
    await db.refresh(rule)
    network_policy.forget(principal.tenant_id)
    return _out(rule)


@router.delete("/ip-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_active_subscription)])
async def remove_ip_rule(
    rule_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("security.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = await _rules(db, principal.tenant_id)
    rule = next((r for r in existing if r.id == rule_id), None)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    remaining = [r.cidr for r in existing if r.id != rule_id]
    _lockout_guard(remaining, client_ip(request))
    await db.delete(rule)
    await AuditService(db).record_entity(
        tenant_id=principal.tenant_id, action="network.ip_rule_removed", entity_type="ip_rule", entity_id=rule_id,
        actor_id=principal.user_id, actor_email=principal.email,
        description=f"Removed allowed network {rule.cidr}" + (" - no rules left, access is open to every network" if not remaining else ""),
        old_values={"cidr": rule.cidr, "label": rule.label}, new_values={"rules": len(remaining)},
    )
    await db.commit()
    network_policy.forget(principal.tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

