"""Verify the device action plane migration landed (tables + permission seeds)."""
import asyncio
from sqlalchemy import text
from app.core.db import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as db:
        version = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        print("alembic_version:", version)

        tables = (await db.execute(text("""
            SELECT tablename FROM pg_tables
            WHERE schemaname='public'
              AND tablename IN ('device_action_requests','device_capabilities')
            ORDER BY tablename
        """))).scalars().all()
        print("new tables:", tables)

        perms = (await db.execute(text("""
            SELECT code FROM permissions WHERE code LIKE 'device_actions%' ORDER BY code
        """))).scalars().all()
        print("permission codes:", perms)

        grants = (await db.execute(text("""
            SELECT r.name, p.code FROM role_permissions rp
            JOIN roles r ON r.id = rp.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE p.code LIKE 'device_actions%'
            GROUP BY r.name, p.code ORDER BY r.name, p.code
        """))).all()
        print("role grants (distinct role/code pairs):")
        for name, code in grants:
            print(f"  {name} -> {code}")

        cols = (await db.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='device_capabilities' ORDER BY ordinal_position
        """))).scalars().all()
        print("device_capabilities columns:", len(cols))


asyncio.run(main())