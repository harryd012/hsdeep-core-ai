"""
Verify the tamper-evident audit chain (enterprise security programme S4).

    python scripts/verify_audit_chain.py                 # every tenant, full history
    python scripts/verify_audit_chain.py --tenant <uuid>

Prints one line per tenant with its status, links checked and chain head.
Exits 1 if any chain is broken.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.models.audit_chain import AuditChainLink  # noqa: E402
from app.services.audit_chain import verify  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", type=uuid.UUID, default=None)
    args = parser.parse_args()
    broken = 0
    async with AsyncSessionLocal() as db:
        tenants = [args.tenant] if args.tenant else (
            await db.execute(select(AuditChainLink.tenant_id).distinct())
        ).scalars().all()
        for tid in tenants:
            r = await verify(db, tid, full=True)
            broken += r.status == "broken"
            print(json.dumps({"tenant_id": str(tid), **r.as_dict()}))
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
