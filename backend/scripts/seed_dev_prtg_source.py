"""
Dev-only seed script: creates one MonitoringSource pointed at the
developer's live PRTG server, sourced entirely from PRTG_DEV_* environment
variables (never hardcoded). Idempotent - safe to run any number of times;
the get-or-create is keyed on (tenant_id, source_type, hostname).

Guarded to development only:
  - Never runs the seeding logic when APP_ENV=production.
  - When invoked directly (`python -m scripts.seed_dev_prtg_source`), the
    explicit invocation itself is sufficient (still subject to the
    production guard above); missing PRTG_DEV_* vars simply result in a
    logged no-op (exit 0), not an error.
  - When imported and called programmatically (e.g. from application
    startup) rather than run as a script, seeding only proceeds when
    APP_ENV=development AND all required PRTG_DEV_* vars are present.

Credentials are routed through MonitoringService.create_source(), which
encrypts the PRTG passhash via SecretCrypto before it is ever persisted -
this script never writes to the database directly for the MonitoringSource
row, and never logs the passhash value.
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.monitoring_source import MonitoringSource
from app.models.tenant import Tenant
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.services.monitoring.monitoring_service import MonitoringService

logger = logging.getLogger(__name__)

DEV_TENANT_SLUG = "dev"
DEV_SOURCE_NAME = "Dev PRTG Server"

# Vars with no sensible default - their absence means "don't seed" rather
# than "use a default". PRTG_DEV_PORT/PROTOCOL/VERIFY_SSL/POLL_INTERVAL_SECONDS
# all have safe defaults below and are read regardless.
REQUIRED_ENV_VARS = ("PRTG_DEV_HOST", "PRTG_DEV_USERNAME", "PRTG_DEV_PASSHASH")


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


async def seed_dev_prtg_source(db: AsyncSession) -> MonitoringSource | None:
    """Get-or-create the dev tenant's PRTG MonitoringSource from PRTG_DEV_*
    env vars. Returns None (no-op) if required env vars are missing.

    Idempotency key: (tenant_id, source_type="prtg", hostname). Does not
    commit - callers own the transaction boundary.
    """
    missing = _missing_required_env_vars()
    if missing:
        logger.info("Skipping dev PRTG seed: missing env var(s) %s", missing)
        return None

    host = os.environ["PRTG_DEV_HOST"]
    port = int(os.environ.get("PRTG_DEV_PORT", "443"))
    protocol = os.environ.get("PRTG_DEV_PROTOCOL", "https")
    username = os.environ["PRTG_DEV_USERNAME"]
    passhash = os.environ["PRTG_DEV_PASSHASH"]
    verify_ssl = os.environ.get("PRTG_DEV_VERIFY_SSL", "false").strip().lower() in ("1", "true", "yes")
    poll_interval_seconds = int(os.environ.get("PRTG_DEV_POLL_INTERVAL_SECONDS", "60"))

    tenant = await _get_or_create_dev_tenant(db)

    source_repo = MonitoringSourceRepository(db)
    existing_sources = await source_repo.list(tenant.id, source_type="prtg")
    for existing in existing_sources:
        if existing.hostname == host:
            logger.info("Dev PRTG MonitoringSource already exists (id=%s); skipping create.", existing.id)
            return existing

    service = MonitoringService(source_repo=source_repo, sensor_repo=SensorRepository(db))
    source = await service.create_source(
        tenant_id=tenant.id,
        name=DEV_SOURCE_NAME,
        source_type="prtg",
        vendor="prtg",
        protocol=protocol,
        hostname=host,
        port=port,
        username=username,
        password=passhash,  # plaintext in-process only - encrypted by MonitoringService before persisting
        verify_ssl=verify_ssl,
        poll_interval_seconds=poll_interval_seconds,
        is_active=True,
    )
    logger.info("Created dev PRTG MonitoringSource id=%s hostname=%s", source.id, host)
    return source


async def main(*, explicit: bool = False) -> None:
    settings = get_settings()
    if settings.app_env == "production":
        logger.warning("Refusing to run dev PRTG seed script: APP_ENV=production")
        return

    missing = _missing_required_env_vars()
    allowed = explicit or (settings.app_env == "development" and not missing)
    if not allowed:
        logger.info(
            "Skipping dev PRTG seed: not an explicit script invocation and not "
            "(APP_ENV=development with all required PRTG_DEV_* vars present)"
        )
        return

    async with AsyncSessionLocal() as db:
        source = await seed_dev_prtg_source(db)
        if source is None:
            return
        await db.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(explicit=True))
