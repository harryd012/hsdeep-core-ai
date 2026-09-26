"""
Development-only PRTG MonitoringSource seed script.

Reads PRTG connection details from environment variables and creates (or
reuses, if one already exists) a single MonitoringSource pointed at the
developer's PRTG server - routed entirely through the existing
MonitoringService/SecretCrypto encryption path and MonitoringSourceRepository,
never via raw SQL or a direct model insert.

Environment variables read:
    PRTG_DEV_URL        e.g. "https://192.168.1.5"
    PRTG_DEV_USERNAME   PRTG username
    PRTG_DEV_PASSHASH   PRTG passhash (never logged or printed)

Idempotent: running this script multiple times will not create duplicate
MonitoringSource rows - it looks up an existing PRTG source for the dev
tenant by hostname before creating a new one.

Usage (from the `backend` directory, with its virtualenv active):
    python scripts/seed_prtg_dev.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Allow running this script directly (`python scripts/seed_prtg_dev.py`)
# regardless of the current working directory, without requiring `python -m`
# or a pre-set PYTHONPATH - put `backend/` (this script's parent directory)
# on sys.path so `import app...` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

# Load backend/.env into the process environment so PRTG_DEV_* vars defined
# there (gitignored, real values only ever live locally) are visible via
# os.environ, matching how the rest of the app already gets its config from
# that file via pydantic-settings.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.models.monitoring_source import MonitoringSource  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.repositories.monitoring_source_repository import MonitoringSourceRepository  # noqa: E402
from app.repositories.sensor_repository import SensorRepository  # noqa: E402
from app.services.monitoring.monitoring_service import MonitoringService  # noqa: E402

DEV_TENANT_SLUG = "dev"
DEV_SOURCE_NAME = "Dev PRTG Server"
REQUIRED_ENV_VARS = ("PRTG_DEV_URL", "PRTG_DEV_USERNAME", "PRTG_DEV_PASSHASH")


def _missing_required_env_vars() -> list[str]:
    return [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]


async def _get_or_create_dev_tenant(db: AsyncSession) -> Tenant:
    stmt = select(Tenant).where(Tenant.slug == DEV_TENANT_SLUG, Tenant.is_deleted.is_(False))
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()
    if tenant is not None:
        return tenant
    tenant = Tenant(name="Development", slug=DEV_TENANT_SLUG, is_active=True)
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)
    return tenant


async def seed_prtg_dev(db: AsyncSession) -> tuple[MonitoringSource | None, bool]:
    """Get-or-create the dev tenant's PRTG MonitoringSource from
    PRTG_DEV_URL / PRTG_DEV_USERNAME / PRTG_DEV_PASSHASH.

    Returns (source, created). `source` is None if required env vars are
    missing (a no-op, not an error) or the URL can't be parsed. Does not
    commit - the caller owns the transaction boundary.
    """
    missing = _missing_required_env_vars()
    if missing:
        print(f"[seed_prtg_dev] Skipped: missing environment variable(s): {', '.join(missing)}")
        return None, False

    raw_url = os.environ["PRTG_DEV_URL"]
    username = os.environ["PRTG_DEV_USERNAME"]
    passhash = os.environ["PRTG_DEV_PASSHASH"]

    parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
    protocol = parsed.scheme or "https"
    hostname = parsed.hostname
    port = parsed.port or (443 if protocol == "https" else 80)

    if not hostname:
        print(f"[seed_prtg_dev] Failed: could not parse a hostname from PRTG_DEV_URL={raw_url!r}")
        return None, False

    tenant = await _get_or_create_dev_tenant(db)

    source_repo = MonitoringSourceRepository(db)
    existing_sources = await source_repo.list(tenant.id, source_type="prtg")
    for existing in existing_sources:
        if existing.hostname == hostname:
            print(
                f"[seed_prtg_dev] Already exists: MonitoringSource id={existing.id} "
                f"hostname={hostname} - no duplicate created."
            )
            return existing, False

    service = MonitoringService(source_repo=source_repo, sensor_repo=SensorRepository(db))
    source = await service.create_source(
        tenant_id=tenant.id,
        name=DEV_SOURCE_NAME,
        source_type="prtg",
        vendor="prtg",
        protocol=protocol,
        hostname=hostname,
        port=port,
        username=username,
        password=passhash,  # plaintext in-process only - encrypted via SecretCrypto inside create_source()
        verify_ssl=False,  # dev server uses a self-signed certificate
        is_active=True,
    )
    print(f"[seed_prtg_dev] Created: MonitoringSource id={source.id} hostname={hostname}")
    return source, True


async def main() -> int:
    try:
        async with AsyncSessionLocal() as db:
            source, created = await seed_prtg_dev(db)
            if source is None:
                return 1
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - top-level script: report and exit non-zero, never raise a raw traceback that could leak details
        print(f"[seed_prtg_dev] Failed: {exc}")
        return 1

    action = "created" if created else "reused existing"
    print(f"[seed_prtg_dev] Success: {action} MonitoringSource id={source.id} for tenant_id={source.tenant_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
