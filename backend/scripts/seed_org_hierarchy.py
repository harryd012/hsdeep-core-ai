"""Reconcile + seed the virtual-company org hierarchy for the seed tenant.

Idempotent:
  - Reconciles the legacy NOC worker rows (14 in the seed tenant): orders them by
    created_at ascending, keeps the FIRST 4 active under NOC-MGR-001 as the
    visible NOC branch workers, and deactivates the remaining 10 (still linked to
    NOC-MGR-001 so they are hidden-but-not-orphaned). No agent.id UUID is changed,
    so every existing task's assigned_agent_id reference still resolves.
  - Seeds CTO-001, NOC/SOC/ITSM managers, 4 SOC workers, and 4 IT workers
    (skips any row whose agent_id already exists for the tenant).

Environment variables read (from backend/.env):
    SEED_TENANT_ID  UUID of the tenant to seed (required)

Usage (from backend directory with venv active):
    python scripts/seed_org_hierarchy.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.models.agent import Agent  # noqa: E402

REQUIRED_ENV_VARS = ("SEED_TENANT_ID",)

# How many of the oldest legacy NOC rows become the visible active NOC branch.
KEEP_ACTIVE_NOC = 4

# Root + manager rows (agent_id, name, role, department, level).
HIERARCHY = [
    ("CTO-001", "Chief Technology Officer", "cto", "engineering", "cto"),
    ("NOC-MGR-001", "NOC Manager", "noc_manager", "noc", "manager"),
    ("SOC-MGR-001", "SOC Manager", "soc_manager", "soc", "manager"),
    ("ITSM-MGR-001", "ITSM Manager", "itsm_manager", "it", "manager"),
]

# Worker rosters: (id_prefix, role, department, level, count, name_format)
WORKER_ROSTERS = [
    ("SOC", "soc_analyst", "soc", "l1", 4, "SOC Analyst {:02d}"),
    ("ITSM", "itsm_engineer", "it", "l1", 4, "ITSM Engineer {:02d}"),
]


def _missing_required_env_vars() -> list[str]:
    return [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]


async def _get_by_agent_id(db, tenant_id: uuid.UUID, agent_id: str) -> Agent | None:
    stmt = select(Agent).where(
        Agent.tenant_id == tenant_id,
        Agent.agent_id == agent_id,
        Agent.is_deleted.is_(False),
    )
    return (await db.execute(stmt)).scalars().first()


async def _make_agent(
    db,
    tenant_id: uuid.UUID,
    agent_id: str,
    name: str,
    role: str,
    department: str,
    level: str,
    manager_id: uuid.UUID | None = None,
    *,
    is_active: bool = True,
) -> Agent:
    existing = await _get_by_agent_id(db, tenant_id, agent_id)
    if existing is not None:
        return existing
    agent = Agent(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name=name,
        role=role,
        department=department,
        level=level,
        manager_id=manager_id,
        is_active=is_active,
        status="active",
        availability="available",
        current_workload=0,
        max_workload=5,
        tasks_completed=0,
        tasks_failed=0,
    )
    db.add(agent)
    return agent


async def main() -> int:
    missing = _missing_required_env_vars()
    if missing:
        print(f"[seed_org_hierarchy] Aborted: missing {', '.join(missing)} in backend/.env")
        return 1

    tenant_id = uuid.UUID(os.environ["SEED_TENANT_ID"])

    async with AsyncSessionLocal() as db:
        # --- 1. Seed CTO + managers (CTO is the root) ---
        cto = await _make_agent(
            db, tenant_id, "CTO-001", "Chief Technology Officer",
            role="cto", department="engineering", level="cto",
        )
        await db.flush()

        managers: dict[str, Agent] = {}
        for agent_id, name, role, department, level in HIERARCHY[1:]:
            managers[department] = await _make_agent(
                db, tenant_id, agent_id, name,
                role=role, department=department, level=level,
                manager_id=cto.id,
            )
        await db.flush()
        noc_mgr = managers["noc"]

        # --- 2. Reconcile the legacy NOC worker rows ---
        legacy = (
            (
                await db.execute(
                    select(Agent)
                    .where(
                        Agent.tenant_id == tenant_id,
                        Agent.is_deleted.is_(False),
                        Agent.department == "noc",
                        Agent.level == "l1",
                    )
                    .order_by(Agent.created_at.asc(), Agent.id.asc())
                )
            )
            .scalars()
            .all()
        )
        if len(legacy) < KEEP_ACTIVE_NOC:
            print(
                f"[seed_org_hierarchy] Aborted: expected at least {KEEP_ACTIVE_NOC} "
                f"legacy NOC l1 rows, found {len(legacy)}"
            )
            return 1

        print(
            f"[seed_org_hierarchy] Reconcile {len(legacy)} legacy NOC rows: "
            f"keep {KEEP_ACTIVE_NOC} active, deactivate {len(legacy) - KEEP_ACTIVE_NOC}."
        )
        kept = legacy[:KEEP_ACTIVE_NOC]
        deactivated = legacy[KEEP_ACTIVE_NOC:]
        for idx, agent in enumerate(kept, start=1):
            agent.manager_id = noc_mgr.id
            agent.is_active = True
            # Give the visible NOC branch meaningful display names (id is never
            # changed, so task references are unaffected).
            if agent.name == "Task API Agent":
                agent.name = f"NOC Worker {idx:02d}"
        for agent in deactivated:
            agent.manager_id = noc_mgr.id
            agent.is_active = False

        # --- 3. Seed SOC + IT workers under their managers ---
        for prefix, role, department, level, count, name_fmt in WORKER_ROSTERS:
            mgr_id = managers[department].id
            for i in range(1, count + 1):
                await _make_agent(
                    db,
                    tenant_id,
                    agent_id=f"{prefix}-{i:03d}",
                    name=name_fmt.format(i),
                    role=role,
                    department=department,
                    level=level,
                    manager_id=mgr_id,
                )

        await db.commit()

        report = (
            await db.execute(
                select(Agent)
                .where(Agent.tenant_id == tenant_id, Agent.is_deleted.is_(False))
                .order_by(Agent.level, Agent.department, Agent.agent_id)
            )
        ).scalars().all()
        print(f"\n[seed_org_hierarchy] Final roster ({len(report)} agents):")
        for a in report:
            print(
                f"  [{'on' if a.is_active else 'off'}] {a.agent_id:<14} {a.name[:24]:<24} "
                f"role={a.role:<16} dept={a.department:<12} lvl={a.level:<8} "
                f"mgr={a.manager_id is not None}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))