"""Cleanup duplicate incidents (DRY-RUN by default).

The historical 30-minute correlation window split the SAME probe's repeated
failures into multiple open incidents. This script finds those duplicates —
same tenant + device/site + normalized title — and ARCHIVES all but the
canonical (oldest) one per group.

Archives, never resolves/deletes: an archived incident stays queryable and is
recorded in ``incident_cleanup_undo`` so the operation can be reversed.

Safety rails (enforced in code; there is no "all tenants" path):
  * ``--tenant`` is required and repeatable — only those IDs are touched.
  * Dry-run is the default; ``--apply`` is required to actually archive.
  * The real/production tenant is never hardcoded. Run against a test tenant
    by explicit ID first, never the real tenant.

Usage:
    cd backend
    python -m scripts.cleanup_duplicate_incidents --tenant <UUID>            # measure + dry-run
    python -m scripts.cleanup_duplicate_incidents --tenant <UUID> --apply    # archive duplicates
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

sys.path.insert(0, r"I:\AI-LAB\hsdeep-core-ai\backend")

from app.core.db import AsyncSessionLocal  # noqa: E402

# Statuses considered "still open" and therefore candidates for dedup. Terminal
# statuses (resolved/closed/archived) and the deliberate hold state "recovered"
# are left alone.
_DUPLICATE_STATUSES = ("open", "acknowledged", "investigating", "monitoring")

_UNDO_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS incident_cleanup_undo (
    id UUID PRIMARY KEY,
    incident_id UUID NOT NULL UNIQUE,
    tenant_id UUID NOT NULL,
    previous_status VARCHAR(20) NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL
)
"""


def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    t = title.strip().lower()
    if t.startswith("incident:"):
        t = t[len("incident:"):].strip()
    return " ".join(t.split())


def _dedup_key(row) -> tuple[str, str, str]:
    """Stable duplicate key: tenant + device (or site) scope + normalized title."""
    scope = (
        str(row.device_id)
        if row.device_id is not None
        else (str(row.site_id) if row.site_id is not None else "no-scope")
    )
    return (str(row.tenant_id), scope, _normalize_title(row.title))


async def _measure(db, tenant_ids: list[str]) -> None:
    """Read-only: how many incidents each tenant has, and how many are referenced
    by at least one alert (vs. orphaned)."""
    print("\n=== MEASURE (read-only) ===")
    for tid in tenant_ids:
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                        COUNT(i.id) AS total,
                        COUNT(DISTINCT a.incident_id) AS referenced
                    FROM incidents i
                    LEFT JOIN alerts a
                      ON a.incident_id = i.id AND a.is_deleted = false
                    WHERE i.tenant_id = CAST(:tid AS uuid) AND i.is_deleted = false
                    """
                ),
                {"tid": tid},
            )
        ).one()
        total, referenced = int(row[0]), int(row[1])
        print(
            f"tenant {tid}: total={total} referenced={referenced} "
            f"orphaned={total - referenced}"
        )


async def _find_duplicates(db, tenant_id: str) -> dict[tuple, list]:
    """Return open/… incidents grouped by dedup key (oldest first per group)."""
    rows = (
        await db.execute(
            text(
                """
                SELECT id, tenant_id, device_id, site_id, title, opened_at, status
                FROM incidents
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND is_deleted = false
                  AND status IN ('open', 'acknowledged', 'investigating', 'monitoring')
                ORDER BY opened_at ASC, id ASC
                """
            ),
            {"tid": tenant_id},
        )
    ).all()
    groups: dict[tuple, list] = {}
    for row in rows:
        groups.setdefault(_dedup_key(row), []).append(row)
    return groups


async def _report_or_archive(db, tenant_id: str, groups, *, apply: bool, limit: int) -> None:
    duplicates = [rows[1:] for rows in groups.values() if len(rows) > 1]
    flat = [row for group in duplicates for row in group]
    flat = flat[:limit]

    if not flat:
        print(f"tenant {tenant_id}: no duplicate incidents found.")
        return

    now = datetime.now(timezone.utc)
    reason = "Archived as duplicate incident by cleanup_duplicate_incidents.py"

    print(
        f"\ntenant {tenant_id}: {len(flat)} duplicate incident(s) "
        f"({'ARCHIVING' if apply else 'would archive'}):"
    )
    for row in flat:
        print(f"  - {row.id}  status={row.status}  title={row.title!r}")

    if not apply:
        print("DRY-RUN: no changes written. Re-run with --apply to archive.")
        return

    await db.execute(text(_UNDO_TABLE_DDL))
    archived = 0
    for row in flat:
        undo_id = uuid.uuid4()
        await db.execute(
            text(
                """
                UPDATE incidents
                SET status = 'archived', archived_at = :now, closure_reason = :reason
                WHERE id = :iid
                """
            ),
            {"now": now, "reason": reason, "iid": row.id},
        )
        await db.execute(
            text(
                """
                INSERT INTO incident_cleanup_undo
                    (id, incident_id, tenant_id, previous_status, archived_at, reason)
                VALUES (:uid, :iid, :tid, :prev, :now, :reason)
                """
            ),
            {
                "uid": undo_id,
                "iid": row.id,
                "tid": tenant_id,
                "prev": row.status,
                "now": now,
                "reason": reason,
            },
        )
        archived += 1
    await db.commit()
    print(f"Archived {archived} duplicate incident(s); undo rows written.")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Find and archive duplicate incidents.")
    parser.add_argument(
        "--tenant",
        action="append",
        required=True,
        dest="tenants",
        help="Tenant UUID to inspect/clean (repeatable). Never omit this.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually archive duplicates (default: dry-run only).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max duplicate incidents to archive per run.",
    )
    args = parser.parse_args()

    try:
        tenant_ids = [str(uuid.UUID(t)) for t in args.tenants]
    except ValueError as exc:
        print(f"error: invalid --tenant UUID: {exc}")
        return 2

    async with AsyncSessionLocal() as db:
        await _measure(db, tenant_ids)
        for tid in tenant_ids:
            groups = await _find_duplicates(db, tid)
            await _report_or_archive(db, tid, groups, apply=args.apply, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

