"""
Tenant audit log reads (enterprise security programme S1).

Before S1 audit events were written (AuditService) but nothing could read
them. This is the read side: a filtered, newest-first page and a CSV export,
always for the caller's tenant only.

Export safety: cells that a spreadsheet would treat as a formula (starting
with = + - @, tab or carriage return) are prefixed with an apostrophe, so an
exported value such as a crafted email address cannot run in Excel.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_audit import AuditEvent

MAX_EXPORT_ROWS = 50_000
EXPORT_COLUMNS = (
    "created_at", "action", "actor_email", "actor_type", "entity_type", "entity_id",
    "description", "old_values", "new_values", "ip_address", "id",
)


@dataclass(frozen=True)
class AuditFilters:
    action: str | None = None  # exact action, or a prefix ending with "." ("user.")
    actor: str | None = None  # actor email substring
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    since: datetime | None = None
    until: datetime | None = None
    q: str | None = None  # text in the description


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _safe_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str, sort_keys=True)
    elif isinstance(value, datetime):
        text = value.isoformat()
    else:
        text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class AuditLogService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _where(tenant_id: uuid.UUID, f: AuditFilters) -> list:
        conds: list = [AuditEvent.tenant_id == tenant_id]
        if f.action:
            if f.action.endswith("."):
                conds.append(AuditEvent.action.like(_escape_like(f.action) + "%", escape="\\"))
            else:
                conds.append(AuditEvent.action == f.action)
        if f.actor:
            conds.append(AuditEvent.actor_email.ilike(f"%{_escape_like(f.actor)}%", escape="\\"))
        if f.entity_type:
            conds.append(AuditEvent.entity_type == f.entity_type)
        if f.entity_id:
            conds.append(AuditEvent.entity_id == f.entity_id)
        if f.since:
            conds.append(AuditEvent.created_at >= f.since)
        if f.until:
            conds.append(AuditEvent.created_at < f.until)
        if f.q:
            pattern = f"%{_escape_like(f.q)}%"
            conds.append(or_(AuditEvent.description.ilike(pattern, escape="\\"), AuditEvent.action.ilike(pattern, escape="\\")))
        return conds

    async def page(self, tenant_id: uuid.UUID, f: AuditFilters, *, limit: int, offset: int) -> tuple[int, list[AuditEvent]]:
        where = self._where(tenant_id, f)
        total = (await self.db.execute(select(func.count()).select_from(AuditEvent).where(*where))).scalar_one()
        rows = (
            await self.db.execute(
                select(AuditEvent).where(*where)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(limit).offset(offset)
            )
        ).scalars().all()
        return int(total), list(rows)

    async def facets(self, tenant_id: uuid.UUID) -> dict[str, list[str]]:
        """Distinct actions and entity types recorded for the tenant (filter menus)."""
        actions = (
            await self.db.execute(
                select(AuditEvent.action).where(AuditEvent.tenant_id == tenant_id).distinct().order_by(AuditEvent.action)
            )
        ).scalars().all()
        types = (
            await self.db.execute(
                select(AuditEvent.entity_type)
                .where(AuditEvent.tenant_id == tenant_id, AuditEvent.entity_type.is_not(None))
                .distinct().order_by(AuditEvent.entity_type)
            )
        ).scalars().all()
        return {"actions": list(actions), "entity_types": list(types)}

    async def export_count(self, tenant_id: uuid.UUID, f: AuditFilters) -> int:
        where = self._where(tenant_id, f)
        return int((await self.db.execute(select(func.count()).select_from(AuditEvent).where(*where))).scalar_one())

    @classmethod
    async def export_csv(cls, tenant_id: uuid.UUID, f: AuditFilters) -> AsyncIterator[str]:
        """CSV text in chunks, newest first, at most MAX_EXPORT_ROWS rows.

        Opens its own session: a streamed response outlives the request's
        session dependency."""
        from app.core.db import AsyncSessionLocal

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(EXPORT_COLUMNS)
        stmt = (
            select(AuditEvent).where(*cls._where(tenant_id, f))
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(MAX_EXPORT_ROWS)
            .execution_options(yield_per=1000)
        )
        async with AsyncSessionLocal() as db:
            result = await db.stream_scalars(stmt)
            n = 0
            async for ev in result:
                writer.writerow([_safe_cell(getattr(ev, c)) for c in EXPORT_COLUMNS])
                n += 1
                if n % 500 == 0:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)
        yield buf.getvalue()
