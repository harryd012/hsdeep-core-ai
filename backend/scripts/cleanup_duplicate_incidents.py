"""Archive duplicate open incidents (DRY-RUN by default), reversibly.

A recurrence bug opened a new incident on every poll instead of updating the
open one, so one failing sensor accumulated hundreds of thousands of open
incidents. This script groups still-open incidents by tenant + device (or
site) + normalized title and archives every copy but one per group.

What it does, per tenant:
  1. Keeper: in each group, the most recently active copy (latest
     ``last_alert_at``, then highest ``alert_count``, then id) - the copy that
     current alerts and work most likely point at.
  2. Relink: every row in any table with a foreign key to ``incidents.id``
     (alerts, workflow/runbook executions, tickets, ... - discovered from the
     catalog, not hardcoded) that points at a duplicate is repointed to its
     group's keeper, so nothing is left attached to an archived incident.
     All relinks happen in one transaction before anything is archived.
  3. Archive: duplicates are set to ``archived`` in set-based batches
     (``--batch-size``, one transaction each).

Every change is recorded (``incident_cleanup_undo`` for status,
``incident_cleanup_relinks`` for foreign keys) under a run id, and
``--undo <run_id>`` reverses both. Archives, never deletes.

Safety rails:
  * ``--tenant`` is required and repeatable - only those tenants are touched.
  * Dry-run is the default; ``--apply`` is required to change anything.
  * Resumable: an interrupted ``--apply`` continues the same run when re-run.

Usage:
    cd backend
    python -m scripts.cleanup_duplicate_incidents --tenant <UUID>            # dry-run report
    python -m scripts.cleanup_duplicate_incidents --tenant <UUID> --apply    # relink + archive
    python -m scripts.cleanup_duplicate_incidents --undo <RUN_ID>            # reverse a run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import engine  # noqa: E402

#: Statuses that are still "open" and therefore dedup candidates. Terminal
#: statuses (resolved/closed/archived) and the hold state "recovered" are left alone.
OPEN_STATUSES = ("open", "acknowledged", "investigating", "monitoring")
REASON = "Archived as duplicate incident by cleanup_duplicate_incidents.py"
DEFAULT_BATCH = 50_000

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS incident_cleanup_undo (
        id UUID PRIMARY KEY,
        incident_id UUID NOT NULL UNIQUE,
        tenant_id UUID NOT NULL,
        previous_status VARCHAR(20) NOT NULL,
        archived_at TIMESTAMPTZ NOT NULL,
        reason TEXT NOT NULL
    )
    """,
    "ALTER TABLE incident_cleanup_undo ADD COLUMN IF NOT EXISTS run_id UUID",
    "ALTER TABLE incident_cleanup_undo ADD COLUMN IF NOT EXISTS keeper_id UUID",
    "CREATE INDEX IF NOT EXISTS ix_incident_cleanup_undo_run ON incident_cleanup_undo (run_id)",
    """
    CREATE TABLE IF NOT EXISTS incident_cleanup_pending (
        incident_id UUID PRIMARY KEY,
        run_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        keeper_id UUID NOT NULL,
        previous_status VARCHAR(20) NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_incident_cleanup_pending_run ON incident_cleanup_pending (run_id)",
    """
    CREATE TABLE IF NOT EXISTS incident_cleanup_relinks (
        run_id UUID NOT NULL,
        table_name TEXT NOT NULL,
        column_name TEXT NOT NULL,
        row_id UUID NOT NULL,
        from_incident_id UUID NOT NULL,
        to_incident_id UUID NOT NULL,
        PRIMARY KEY (run_id, table_name, column_name, row_id)
    )
    """,
)

#: Open incidents of one tenant, ranked within their duplicate group. ``rn = 1``
#: is the keeper. The normalization mirrors the original script: lower-case,
#: drop a leading "incident:", collapse whitespace.
_RANKED = """
WITH open_i AS (
    SELECT id, status, severity, last_alert_at, alert_count,
           coalesce(device_id::text, site_id::text, 'no-scope') AS scope,
           btrim(regexp_replace(regexp_replace(lower(btrim(title)), '^incident:\\s*', ''), '\\s+', ' ', 'g')) AS ntitle,
           title
    FROM incidents
    WHERE tenant_id = CAST(:tid AS uuid)
      AND is_deleted = false
      AND status IN ('open', 'acknowledged', 'investigating', 'monitoring')
), ranked AS (
    SELECT *,
           row_number() OVER w AS rn,
           first_value(id) OVER w AS keeper_id,
           count(*) OVER (PARTITION BY scope, ntitle) AS group_size
    FROM open_i
    WINDOW w AS (PARTITION BY scope, ntitle ORDER BY last_alert_at DESC, alert_count DESC, id)
)
"""


@dataclass
class Plan:
    tenant_id: str
    open_total: int
    keep: int
    archive: int
    groups: int
    by_severity: dict[str, int] = field(default_factory=dict)
    largest: list[tuple[str, int]] = field(default_factory=list)
    relinks: dict[str, int] = field(default_factory=dict)


async def _no_timeout(conn: AsyncConnection) -> None:
    # Large tenants need minutes per statement; scoped to the transaction.
    await conn.execute(text("SET LOCAL statement_timeout = 0"))


async def incident_foreign_keys(conn: AsyncConnection) -> list[tuple[str, str]]:
    """Every (table, column) with a foreign key to incidents.id, from the catalog."""
    rows = (
        await conn.execute(
            text(
                """
                SELECT c.conrelid::regclass::text, a.attname
                FROM pg_constraint c
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                WHERE c.contype = 'f' AND c.confrelid = 'incidents'::regclass
                ORDER BY 1, 2
                """
            )
        )
    ).all()
    fks = [(r[0], r[1]) for r in rows]
    for table, _col in fks:
        has_id = (
            await conn.execute(
                text(
                    "SELECT 1 FROM pg_attribute WHERE attrelid = CAST(:t AS regclass) "
                    "AND attname = 'id' AND NOT attisdropped"
                ),
                {"t": table},
            )
        ).first()
        if not has_id:
            raise RuntimeError(f"{table} references incidents but has no id column; cannot record relinks")
    return fks


async def build_plan(conn: AsyncConnection, tenant_id: str) -> Plan:
    """Read-only: what an apply would keep, archive and relink."""
    await _no_timeout(conn)
    params = {"tid": tenant_id}
    totals = (
        await conn.execute(
            text(
                _RANKED
                + """
                SELECT count(*), count(*) FILTER (WHERE rn = 1), count(*) FILTER (WHERE rn > 1),
                       count(DISTINCT (scope, ntitle)) FILTER (WHERE group_size > 1)
                FROM ranked
                """
            ),
            params,
        )
    ).one()
    plan = Plan(tenant_id, int(totals[0]), int(totals[1]), int(totals[2]), int(totals[3]))
    if plan.archive == 0:
        return plan
    plan.by_severity = {
        r[0]: int(r[1])
        for r in (
            await conn.execute(text(_RANKED + "SELECT severity, count(*) FROM ranked WHERE rn > 1 GROUP BY 1 ORDER BY 2 DESC"), params)
        ).all()
    }
    plan.largest = [
        (r[0], int(r[1]))
        for r in (
            await conn.execute(
                text(_RANKED + "SELECT min(title), max(group_size) FROM ranked WHERE group_size > 1 GROUP BY scope, ntitle ORDER BY 2 DESC LIMIT 5"),
                params,
            )
        ).all()
    ]
    for table, col in await incident_foreign_keys(conn):
        n = (
            await conn.execute(
                text(_RANKED + f"SELECT count(*) FROM {table} x JOIN ranked r ON x.{col} = r.id WHERE r.rn > 1"),
                params,
            )
        ).scalar_one()
        if n:
            plan.relinks[f"{table}.{col}"] = int(n)
    return plan


def print_plan(plan: Plan, *, apply: bool) -> None:
    print(f"\ntenant {plan.tenant_id}")
    print(f"  open incidents        {plan.open_total:>12,}")
    print(f"  duplicate groups      {plan.groups:>12,}")
    print(f"  keep (one per group)  {plan.keep:>12,}")
    print(f"  {'ARCHIVE' if apply else 'would archive':<21} {plan.archive:>12,}")
    if plan.by_severity:
        print("  by severity           " + ", ".join(f"{k} {v:,}" for k, v in plan.by_severity.items()))
    for title, size in plan.largest:
        print(f"    {size:>10,} x {title}")
    if plan.relinks:
        print("  references repointed to the kept incident:")
        for name, n in plan.relinks.items():
            print(f"    {name:<40} {n:>8,}")
    else:
        print("  references repointed: none")


async def _pending_run(conn: AsyncConnection, tenant_id: str) -> str | None:
    row = (
        await conn.execute(
            text("SELECT run_id FROM incident_cleanup_pending WHERE tenant_id = CAST(:tid AS uuid) LIMIT 1"),
            {"tid": tenant_id},
        )
    ).first()
    return str(row[0]) if row else None


async def _cleanup_tables_exist(conn: AsyncConnection) -> bool:
    row = (
        await conn.execute(text(
            "SELECT to_regclass('incident_cleanup_undo') IS NOT NULL"
            " AND to_regclass('incident_cleanup_pending') IS NOT NULL"
            " AND to_regclass('incident_cleanup_relinks') IS NOT NULL"
            " AND EXISTS (SELECT 1 FROM information_schema.columns"
            "   WHERE table_name = 'incident_cleanup_undo' AND column_name = 'keeper_id')"
        ))
    ).first()
    return bool(row and row[0])


async def apply_cleanup(conn: AsyncConnection, tenant_id: str, *, batch_size: int = DEFAULT_BATCH, log=print) -> str | None:
    """Relink references and archive duplicates. Returns the run id (None when
    there was nothing to do). Commits as it goes; safe to re-run."""
    await _no_timeout(conn)
    # The tables come from migration f3d6a8c2e4b7. Create them here only on a
    # database that predates it: DDL needs schema rights the app role may lack.
    if not await _cleanup_tables_exist(conn):
        for ddl in _DDL:
            await conn.execute(text(ddl))
        await conn.commit()

    run_id = await _pending_run(conn, tenant_id)
    if run_id:
        log(f"  resuming run {run_id}")
    else:
        run_id = str(uuid.uuid4())
        await _no_timeout(conn)
        inserted = (
            await conn.execute(
                text(
                    _RANKED
                    + """
                    INSERT INTO incident_cleanup_pending (incident_id, run_id, tenant_id, keeper_id, previous_status)
                    SELECT id, CAST(:run AS uuid), CAST(:tid AS uuid), keeper_id, status FROM ranked WHERE rn > 1
                    """
                ),
                {"tid": tenant_id, "run": run_id},
            )
        ).rowcount
        if not inserted:
            await conn.rollback()
            return None
        # Relink every reference in ONE transaction before archiving anything:
        # if any table refuses (e.g. a unique constraint), nothing is changed.
        for table, col in await incident_foreign_keys(conn):
            await conn.execute(
                text(
                    f"""
                    INSERT INTO incident_cleanup_relinks
                        (run_id, table_name, column_name, row_id, from_incident_id, to_incident_id)
                    SELECT p.run_id, :tbl, :col, x.id, p.incident_id, p.keeper_id
                    FROM {table} x JOIN incident_cleanup_pending p ON x.{col} = p.incident_id
                    WHERE p.run_id = CAST(:run AS uuid)
                    """
                ),
                {"tbl": table, "col": col, "run": run_id},
            )
            moved = (
                await conn.execute(
                    text(
                        f"""
                        UPDATE {table} x SET {col} = p.keeper_id
                        FROM incident_cleanup_pending p
                        WHERE x.{col} = p.incident_id AND p.run_id = CAST(:run AS uuid)
                        """
                    ),
                    {"run": run_id},
                )
            ).rowcount
            if moved:
                log(f"  relinked {moved:,} {table}.{col}")
        await conn.commit()
        log(f"  run {run_id}: {inserted:,} incidents to archive")

    archived_total = 0
    while True:
        await _no_timeout(conn)
        now = datetime.now(timezone.utc)
        n = (
            await conn.execute(
                text(
                    """
                    WITH batch AS (
                        DELETE FROM incident_cleanup_pending
                        WHERE incident_id IN (
                            SELECT incident_id FROM incident_cleanup_pending
                            WHERE run_id = CAST(:run AS uuid) LIMIT :n
                        )
                        RETURNING incident_id, run_id, tenant_id, keeper_id, previous_status
                    ), archived AS (
                        UPDATE incidents i
                        SET status = 'archived', archived_at = :now, closure_reason = :reason, updated_at = :now
                        FROM batch b
                        WHERE i.id = b.incident_id AND i.status = b.previous_status
                        RETURNING i.id
                    )
                    INSERT INTO incident_cleanup_undo
                        (id, incident_id, tenant_id, previous_status, archived_at, reason, run_id, keeper_id)
                    SELECT gen_random_uuid(), b.incident_id, b.tenant_id, b.previous_status, :now, :reason, b.run_id, b.keeper_id
                    FROM batch b JOIN archived a ON a.id = b.incident_id
                    """
                ),
                {"run": run_id, "n": batch_size, "now": now, "reason": REASON},
            )
        ).rowcount
        remaining = (
            await conn.execute(
                text("SELECT count(*) FROM incident_cleanup_pending WHERE run_id = CAST(:run AS uuid)"),
                {"run": run_id},
            )
        ).scalar_one()
        await conn.commit()
        archived_total += n
        if remaining == 0:
            break
        log(f"  archived {archived_total:,} ... {remaining:,} left")
    log(f"  archived {archived_total:,} duplicate incident(s). Undo with: --undo {run_id}")
    return run_id


async def undo_run(conn: AsyncConnection, run_id: str, *, log=print) -> int:
    """Restore every incident archived by a run and repoint its references
    back. One transaction: all or nothing. Returns incidents restored."""
    await _no_timeout(conn)
    relinks = (
        await conn.execute(
            text(
                "SELECT DISTINCT table_name, column_name FROM incident_cleanup_relinks WHERE run_id = CAST(:run AS uuid)"
            ),
            {"run": run_id},
        )
    ).all()
    for table, col in relinks:
        moved = (
            await conn.execute(
                text(
                    f"""
                    UPDATE {table} x SET {col} = r.from_incident_id
                    FROM incident_cleanup_relinks r
                    WHERE r.run_id = CAST(:run AS uuid) AND r.table_name = :tbl AND r.column_name = :col
                      AND x.id = r.row_id AND x.{col} = r.to_incident_id
                    """
                ),
                {"run": run_id, "tbl": table, "col": col},
            )
        ).rowcount
        log(f"  restored {moved:,} {table}.{col}")
    restored = (
        await conn.execute(
            text(
                """
                UPDATE incidents i
                SET status = u.previous_status, archived_at = NULL, closure_reason = NULL, updated_at = now()
                FROM incident_cleanup_undo u
                WHERE u.run_id = CAST(:run AS uuid) AND i.id = u.incident_id AND i.status = 'archived'
                """
            ),
            {"run": run_id},
        )
    ).rowcount
    for table in ("incident_cleanup_relinks", "incident_cleanup_undo", "incident_cleanup_pending"):
        await conn.execute(text(f"DELETE FROM {table} WHERE run_id = CAST(:run AS uuid)"), {"run": run_id})
    await conn.commit()
    log(f"  restored {restored:,} incident(s) from run {run_id}")
    return restored


async def main() -> int:
    parser = argparse.ArgumentParser(description="Find and archive duplicate open incidents (reversibly).")
    parser.add_argument("--tenant", action="append", dest="tenants", default=[], help="Tenant UUID (repeatable).")
    parser.add_argument("--apply", action="store_true", help="Relink and archive (default: dry-run only).")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help="Incidents archived per transaction.")
    parser.add_argument("--undo", metavar="RUN_ID", help="Reverse a previous --apply run.")
    args = parser.parse_args()

    try:
        if args.undo:
            run_id = str(uuid.UUID(args.undo))
        else:
            if not args.tenants:
                parser.error("--tenant is required (no all-tenants mode)")
            tenant_ids = [str(uuid.UUID(t)) for t in args.tenants]
    except ValueError as exc:
        print(f"error: invalid UUID: {exc}")
        return 2

    async with engine.connect() as conn:
        if args.undo:
            await undo_run(conn, run_id)
            return 0
        for tid in tenant_ids:
            plan = await build_plan(conn, tid)
            await conn.rollback()
            print_plan(plan, apply=args.apply)
            if args.apply and plan.archive:
                await apply_cleanup(conn, tid, batch_size=args.batch_size)
        if not args.apply:
            print("\nDRY-RUN: nothing was changed. Re-run with --apply to relink and archive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
