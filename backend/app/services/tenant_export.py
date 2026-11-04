"""
Tenant data export (enterprise security programme S5).

Builds a ZIP of everything a tenant owns: one JSON-lines file per table that
has a tenant_id column (so new data is included without changing this file),
plus manifest.json with row counts, what was left out and the audit chain head.

Left out on purpose: sign-in internals (sessions, refresh / account tokens, MFA
secrets and recovery codes, the email outbox, earlier exports) and every column
that holds a credential (names with password, hash, secret, token, key …). The
export is about the customer's data, not a way to lift live credentials.

Exports are written by a background job under EXPORT_DIR and deleted after
EXPORT_KEEP_DAYS.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base
from app.models.tenant import Tenant
from app.models.tenant_export import EXPORT_KEEP_DAYS, TenantExport
from app.services import audit_chain

logger = logging.getLogger(__name__)

EXCLUDED_TABLES = frozenset({
    "refresh_tokens", "auth_sessions", "account_tokens", "user_mfa", "mfa_recovery_codes",
    "tenant_exports", "email_outbox",
})
_SENSITIVE = re.compile(r"(password|passwd|hash|secret|token|_key$|api_key|private|credential|webhook_url|integration_key|encrypted)", re.I)
_ROW_BATCH = 2000


def export_dir() -> Path:
    base = os.environ.get("EXPORT_DIR") or str(Path(__file__).resolve().parents[2] / "var" / "exports")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def tenant_tables():
    import app.models  # noqa: F401 - registers every model

    tables = [t for t in Base.metadata.tables.values() if "tenant_id" in t.c and t.name not in EXCLUDED_TABLES]
    return sorted(tables, key=lambda t: t.name)


def _json_default(value):
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return None
    return str(value)


async def build_export(db: AsyncSession, export: TenantExport) -> None:
    """Write the ZIP for one export row and mark it ready (or failed)."""
    tenant = await db.get(Tenant, export.tenant_id)
    path = export_dir() / f"{export.tenant_id}-{export.id}.zip"
    counts: dict[str, int] = {}
    omitted: dict[str, list[str]] = {}
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for table in tenant_tables():
                cols = [c for c in table.c if not _SENSITIVE.search(c.name)]
                dropped = [c.name for c in table.c if _SENSITIVE.search(c.name)]
                if dropped:
                    omitted[table.name] = dropped
                n = 0
                with zf.open(f"data/{table.name}.jsonl", "w") as fh:
                    result = await db.stream(
                        select(*cols).where(table.c.tenant_id == export.tenant_id).execution_options(yield_per=_ROW_BATCH)
                    )
                    async for row in result.mappings():
                        fh.write((json.dumps(dict(row), default=_json_default, ensure_ascii=False) + "\n").encode("utf-8"))
                        n += 1
                counts[table.name] = n
            manifest = {
                "format": "hsdeep-tenant-export/1",
                "tenant": {"id": str(export.tenant_id), "name": tenant.name if tenant else None},
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "requested_by": export.requested_by,
                "tables": counts,
                "omitted_columns": omitted,
                "omitted_tables": sorted(EXCLUDED_TABLES),
                "audit_chain_head": await audit_chain.head(db, export.tenant_id),
                "note": "One JSON object per line. Credential columns and sign-in internals are not exported.",
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        export.status = "ready"
        export.file_path = str(path)
        export.size_bytes = path.stat().st_size
        export.summary = {k: v for k, v in counts.items() if v}
        export.completed_at = datetime.now(timezone.utc)
        export.expires_at = export.completed_at + timedelta(days=EXPORT_KEEP_DAYS)
    except Exception as exc:  # noqa: BLE001 - the row records the failure
        logger.exception("Tenant export %s failed", export.id)
        export.status = "failed"
        export.error = type(exc).__name__
        if path.exists():
            path.unlink()
    await db.commit()


async def run_pending_exports(db: AsyncSession, limit: int = 2) -> int:
    """Build pending exports (oldest first) and delete expired files."""
    done = 0
    for _ in range(limit):
        export = (
            await db.execute(
                select(TenantExport).where(TenantExport.status == "pending")
                .order_by(TenantExport.created_at).limit(1).with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if export is None:
            break
        export.status = "running"
        await db.commit()
        await build_export(db, export)
        done += 1
    now = datetime.now(timezone.utc)
    expired = (
        await db.execute(select(TenantExport).where(TenantExport.status == "ready", TenantExport.expires_at < now))
    ).scalars().all()
    for export in expired:
        if export.file_path and Path(export.file_path).exists():
            Path(export.file_path).unlink()
        export.status = "expired"
        export.file_path = None
    if expired:
        await db.commit()
    return done


async def tenant_export_job() -> None:
    from app.core.db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await run_pending_exports(db)
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the scheduler
        logger.exception("Tenant export job failed")
