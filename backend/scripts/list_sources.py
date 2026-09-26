"""List monitoring sources (read-only inspection for Phase 2 scoping)."""
import sys

sys.path.insert(0, r"I:\AI-LAB\hsdeep-core-ai\backend")

import asyncio  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT source_type, status, hostname, protocol, name "
            "FROM monitoring_sources WHERE is_deleted = false"
        ))).all()
        print("monitoring sources:", rows)


asyncio.run(main())