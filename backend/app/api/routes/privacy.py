"""
Tenant privacy settings (enterprise security programme S5).

  GET    /api/settings/privacy                        AI data use, retention, offboarding (any signed-in user)
  PUT    /api/settings/privacy                        change AI data use and retention (privacy.manage)
  GET    /api/settings/privacy/exports                recent data exports (privacy.manage)
  POST   /api/settings/privacy/exports                queue a full data export (privacy.manage)
  GET    /api/settings/privacy/exports/{id}/download  the ZIP while it is kept (privacy.manage)
  POST   /api/settings/privacy/deletion               schedule deletion of the tenant (tenant.delete, Owner)
  DELETE /api/settings/privacy/deletion               cancel it (tenant.delete)

Every change is audited with old and new values.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, require_permission
from app.core.config import get_settings
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.models.tenant import Tenant
from app.models.tenant_export import TenantExport
from app.models.tenant_privacy import RETENTION_RANGE_DAYS
from app.services.audit_service import AuditService
from app.services.offboarding import scheduled_date
from app.services.privacy import load_privacy, privacy_row

router = APIRouter(prefix="/settings/privacy", tags=["settings"])

Days = Field(None, ge=RETENTION_RANGE_DAYS[0], le=RETENTION_RANGE_DAYS[1])


class PrivacyOut(BaseModel):
    ai_mode: Literal["on", "off"]
    ai_redact_emails: bool
    ai_redact_ips: bool
    metrics_retention_days: int | None
    events_retention_days: int | None
    #: The platform default used when the tenant sets none (None = kept).
    platform_metrics_retention_days: int | None
    deletion_requested_at: datetime | None
    deletion_scheduled_for: datetime | None
    deletion_requested_by: str | None
    is_default: bool


class PrivacyUpdate(BaseModel):
    """Omitted fields keep their value; send `null` retention to use the platform default."""

    ai_mode: Literal["on", "off"] | None = None
    ai_redact_emails: bool | None = None
    ai_redact_ips: bool | None = None
    metrics_retention_days: int | None = Days
    events_retention_days: int | None = Days


async def _out(db: AsyncSession, tenant_id) -> PrivacyOut:
    p = await load_privacy(db, tenant_id)
    platform = get_settings().metric_retention_days
    return PrivacyOut(
        ai_mode=p.ai_mode, ai_redact_emails=p.ai_redact_emails, ai_redact_ips=p.ai_redact_ips,
        metrics_retention_days=p.metrics_retention_days, events_retention_days=p.events_retention_days,
        platform_metrics_retention_days=platform or None,
        deletion_requested_at=p.deletion_requested_at, deletion_scheduled_for=p.deletion_scheduled_for,
        deletion_requested_by=p.deletion_requested_by, is_default=not p.saved,
    )


@router.get("", response_model=PrivacyOut)
async def get_privacy(principal: Principal = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _out(db, principal.tenant_id)


@router.put("", response_model=PrivacyOut, dependencies=[Depends(require_active_subscription)])
async def update_privacy(
    payload: PrivacyUpdate,
    principal: Principal = Depends(require_permission("privacy.manage")),
    db: AsyncSession = Depends(get_db),
):
    row = await privacy_row(db, principal.tenant_id)
    old: dict = {}
    new: dict = {}
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None and field in ("ai_mode", "ai_redact_emails", "ai_redact_ips"):
            continue
        if getattr(row, field) != value:
            old[field], new[field] = getattr(row, field), value
            setattr(row, field, value)
    if new:
        await AuditService(db).record_entity(
            tenant_id=principal.tenant_id, action="privacy.settings_updated", entity_type="privacy_settings",
            entity_id=principal.tenant_id, actor_id=principal.user_id, actor_email=principal.email,
            description=f"Privacy settings changed: {', '.join(sorted(new))}", old_values=old, new_values=new,
        )
    await db.commit()
    return await _out(db, principal.tenant_id)


# ---- Data export --------------------------------------------------------------

class ExportOut(BaseModel):
    id: uuid.UUID
    status: str
    requested_by: str | None
    size_bytes: int | None
    summary: dict
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None


def _export_out(e: TenantExport) -> ExportOut:
    return ExportOut(id=e.id, status=e.status, requested_by=e.requested_by, size_bytes=e.size_bytes, summary=e.summary or {},
                     error=e.error, created_at=e.created_at, completed_at=e.completed_at, expires_at=e.expires_at)


@router.get("/exports", response_model=list[ExportOut])
async def list_exports(principal: Principal = Depends(require_permission("privacy.manage")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(TenantExport).where(TenantExport.tenant_id == principal.tenant_id).order_by(TenantExport.created_at.desc()).limit(10)
    )).scalars().all()
    return [_export_out(e) for e in rows]


@router.post("/exports", response_model=ExportOut, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_active_subscription)])
async def request_export(principal: Principal = Depends(require_permission("privacy.manage")), db: AsyncSession = Depends(get_db)):
    """Queue an export of all the tenant's data; it is ready to download within minutes."""
    busy = (await db.execute(select(TenantExport.id).where(
        TenantExport.tenant_id == principal.tenant_id, TenantExport.status.in_(("pending", "running"))
    ))).first()
    if busy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An export is already being prepared.")
    export = TenantExport(tenant_id=principal.tenant_id, requested_by=principal.email, status="pending")
    db.add(export)
    await db.flush()
    await AuditService(db).record_entity(
        tenant_id=principal.tenant_id, action="privacy.export_requested", entity_type="tenant_export", entity_id=export.id,
        actor_id=principal.user_id, actor_email=principal.email, description=f"{principal.email} requested a full data export",
    )
    await db.commit()
    await db.refresh(export)
    return _export_out(export)


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: uuid.UUID,
    principal: Principal = Depends(require_permission("privacy.manage")),
    db: AsyncSession = Depends(get_db),
):
    export = (await db.execute(select(TenantExport).where(
        TenantExport.id == export_id, TenantExport.tenant_id == principal.tenant_id
    ))).scalar_one_or_none()
    if export is None or export.status != "ready" or not export.file_path or not Path(export.file_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export not available")
    await AuditService(db).record_entity(
        tenant_id=principal.tenant_id, action="privacy.export_downloaded", entity_type="tenant_export", entity_id=export.id,
        actor_id=principal.user_id, actor_email=principal.email, description=f"{principal.email} downloaded a data export",
    )
    await db.commit()
    stamp = export.created_at.strftime("%Y%m%d")
    return FileResponse(export.file_path, media_type="application/zip", filename=f"hsdeep-export-{stamp}.zip",
                        headers={"Cache-Control": "no-store"})


# ---- Offboarding --------------------------------------------------------------

class DeletionRequest(BaseModel):
    #: The organization's name, typed to confirm.
    confirm_name: str = Field(..., min_length=1, max_length=255)


@router.post("/deletion", response_model=PrivacyOut, dependencies=[Depends(require_active_subscription)])
async def request_deletion(
    payload: DeletionRequest,
    principal: Principal = Depends(require_permission("tenant.delete")),
    db: AsyncSession = Depends(get_db),
):
    """Schedule deletion of the whole tenant DELETION_GRACE_DAYS from now. Cancel any time before."""
    tenant = await db.get(Tenant, principal.tenant_id)
    if tenant is None or payload.confirm_name.strip().casefold() != tenant.name.strip().casefold():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Type your organization's name exactly to confirm.")
    row = await privacy_row(db, principal.tenant_id)
    if row.deletion_scheduled_for is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Deletion is already scheduled.")
    row.deletion_requested_at = datetime.now(timezone.utc)
    row.deletion_scheduled_for = scheduled_date(row.deletion_requested_at)
    row.deletion_requested_by = principal.email
    await AuditService(db).record_entity(
        tenant_id=principal.tenant_id, action="tenant.deletion_requested", entity_type="tenant", entity_id=principal.tenant_id,
        actor_id=principal.user_id, actor_email=principal.email,
        description=f"{principal.email} scheduled deletion of {tenant.name} for {row.deletion_scheduled_for:%Y-%m-%d}",
        new_values={"deletion_scheduled_for": row.deletion_scheduled_for.isoformat()},
    )
    await db.commit()
    return await _out(db, principal.tenant_id)


@router.delete("/deletion", response_model=PrivacyOut)
async def cancel_deletion(
    principal: Principal = Depends(require_permission("tenant.delete")),
    db: AsyncSession = Depends(get_db),
):
    row = await privacy_row(db, principal.tenant_id)
    if row.deletion_scheduled_for is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No deletion is scheduled.")
    old = row.deletion_scheduled_for.isoformat()
    row.deletion_requested_at = None
    row.deletion_scheduled_for = None
    row.deletion_requested_by = None
    await AuditService(db).record_entity(
        tenant_id=principal.tenant_id, action="tenant.deletion_cancelled", entity_type="tenant", entity_id=principal.tenant_id,
        actor_id=principal.user_id, actor_email=principal.email, description=f"{principal.email} cancelled the scheduled deletion",
        old_values={"deletion_scheduled_for": old},
    )
    await db.commit()
    return await _out(db, principal.tenant_id)
