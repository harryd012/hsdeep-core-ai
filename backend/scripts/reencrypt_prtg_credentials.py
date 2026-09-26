"""
One-off migration: re-encrypt the "Dev PRTG Server" MonitoringSource
credentials with the CURRENT SECRET_ENCRYPTION_KEY.

Background: a previous .env had a duplicate SECRET_ENCRYPTION_KEY entry.
The old value was overridden by the second value, so the Fernet key
changed and every credential encrypted with the old key became
undecryptable (InvalidToken). The CollectionOrchestrator catches this
and marks the source `credentials_invalid`.

This script reads the plaintext passhash from the PRTG_DEV_PASSHASH env
var (loaded from backend/.env, which is gitignored) and re-encrypts it
via MonitoringService.update_source(), which uses SecretCrypto with the
current key. It then verifies decryption succeeds and clears the
credentials_invalid status.

No secrets are hardcoded in this file. No secrets are printed to stdout.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.core.security import SecretCrypto, decrypt_connection_info
from app.models.monitoring_source import MonitoringSource
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.services.monitoring.monitoring_service import MonitoringService

SOURCE_NAME = "Dev PRTG Server"


async def main() -> int:
    passhash = os.environ.get("PRTG_DEV_PASSHASH")
    if not passhash:
        print("FAIL: PRTG_DEV_PASSHASH is not set in the environment / .env")
        return 1

    print("=" * 70)
    print("Re-encrypting 'Dev PRTG Server' credentials with current key")
    print("=" * 70)

    async with AsyncSessionLocal() as db:
        # 1. Find the source
        source = (
            await db.execute(
                select(MonitoringSource).where(MonitoringSource.name == SOURCE_NAME)
            )
        ).scalar_one_or_none()

        if source is None:
            print(f"FAIL: MonitoringSource named '{SOURCE_NAME}' not found")
            return 1

        print(f"\n[1] Before re-encryption:")
        print(f"    id         = {source.id}")
        print(f"    status     = {source.status}")
        print(f"    last_error = {source.last_error}")

        # 2. Re-encrypt via MonitoringService.update_source (uses current key)
        service = MonitoringService(
            source_repo=MonitoringSourceRepository(db),
            sensor_repo=SensorRepository(db),
        )
        source = await service.update_source(
            source,
            password=passhash,  # plaintext in-process only; encrypted inside update_source
            status="connected",
            last_error=None,
        )
        await db.commit()
        await db.refresh(source)

        print(f"\n[2] After re-encryption:")
        print(f"    status     = {source.status}")
        print(f"    last_error = {source.last_error}")

        # 3. Verify decryption succeeds with the current key
        try:
            conn = decrypt_connection_info(source)
            decrypted_ok = conn.password is not None
        except Exception as exc:
            print(f"\n[3] Decryption verification FAILED: {type(exc).__name__}: {exc}")
            return 1

        if not decrypted_ok:
            print("\n[3] Decryption verification FAILED: password is None after decrypt")
            return 1

        print(f"\n[3] Decryption verification: OK (password decrypts to a non-None value)")
        print(f"    (plaintext value intentionally not printed)")

        print("\n" + "=" * 70)
        print("SUCCESS: credentials re-encrypted with the current key.")
        print("The next collector run should no longer hit InvalidToken.")
        print("=" * 70)
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))