"""
Tenant audit log (enterprise security programme S1).

  GET /api/audit/events          - filtered page, newest first
  GET /api/audit/events/facets   - actions and entity types for filter menus
  GET /api/audit/events/export   - CSV of the filtered events (max 50,000 rows)

All need audit.read (Owner, Admin; not the read-only Viewer role) and are
scoped to the caller's tenant. An export is itself an audit event.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require_permission
from app.core.db import get_db
from app.schemas.audit_log import AuditEventOut, AuditFacetsOut, AuditPageOut
from app.services.audit_log_service import MAX_EXPORT_ROWS, AuditFilters, AuditLogService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


def _filters(
    action: str | None = Query(None, max_length=50, description="Exact action, or a prefix ending in '.' (e.g. 'user.')"),
    actor: str | None = Query(None, max_length=255, description="Part of the actor's email"),
    entity_type: str | None = Query(None, max_length=50),
    entity_id: uuid.UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    q: str | None = Query(None, max_length=200, description="Text in the description or action"),
) -> AuditFilters:
    return AuditFilters(
        action=action or None, actor=actor or None, entity_type=entity_type or None, entity_id=entity_id,
        since=since, until=until, q=q or None,
    )


@router.get("/events", response_model=AuditPageOut)
async def list_audit_events(
    filters: AuditFilters = Depends(_filters),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
):
    total, rows = await AuditLogService(db).page(principal.tenant_id, filters, limit=limit, offset=offset)
    return AuditPageOut(total=total, limit=limit, offset=offset, rows=[AuditEventOut.model_validate(r) for r in rows])


@router.get("/events/facets", response_model=AuditFacetsOut)
async def audit_facets(
    principal: Principal = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
):
    return AuditFacetsOut(**await AuditLogService(db).facets(principal.tenant_id))


@router.get("/events/export")
async def export_audit_events(
    filters: AuditFilters = Depends(_filters),
    principal: Principal = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
):
    """CSV download. Recorded as `audit.exported` before the file is sent."""
    service = AuditLogService(db)
    count = await service.export_count(principal.tenant_id, filters)
    await AuditService(db).record_entity(
        tenant_id=principal.tenant_id,
        action="audit.exported",
        entity_type="audit_log",
        entity_id=None,
        actor_id=principal.user_id,
        actor_email=principal.email,
        description=f"{principal.email} exported {min(count, MAX_EXPORT_ROWS)} audit events",
        new_values={k: (str(v) if v is not None else None) for k, v in filters.__dict__.items() if v is not None},
    )
    await db.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        AuditLogService.export_csv(principal.tenant_id, filters),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="audit-log-{stamp}.csv"',
            "Cache-Control": "no-store",
            "X-Total-Count": str(count),
        },
    )
