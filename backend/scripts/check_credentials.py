"""
CLI script to detect all MonitoringSource rows where stored credentials
can no longer be decrypted (Fernet InvalidToken).

Usage:
    cd backend && python -m scripts.check_credentials

This reads the real database (via .env DATABASE_URL) and prints every
source whose api_key_encrypted or password_encrypted fails decryption.
Exits 0 if all sources are healthy, 1 if any source has broken credentials.

Bug 2 — Production Hardening: supports the fix by letting an operator
see the blast radius before deploying the `credentials_invalid` marking.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import SecretCrypto


def main() -> int:
    parser = argparse.ArgumentParser(description="Check monitoring source credentials for decryption failures.")
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Only report, don't modify any rows (default: true).",
    )
    parser.add_argument(
        "--fix", action="store_true", default=False,
        help="Mark broken sources as status='credentials_invalid' with a clear error message.",
    )
    args = parser.parse_args()

    settings = get_settings()
    crypto = SecretCrypto()
    sync_url = settings.database_url.replace("+asyncpg", "").replace("+psycopg", "")

    engine = create_engine(sync_url)
    broken: list[dict] = []
    exit_code = 0

    try:
        with Session(engine) as session:
            rows = session.execute(
                text("""
                    SELECT id, tenant_id, name, source_type, status,
                           api_key_encrypted, password_encrypted, last_error
                    FROM monitoring_sources
                    WHERE is_deleted = false
                    ORDER BY name
                """)
            ).mappings().all()

            for row in rows:
                failures: list[str] = []

                # Try decrypting api_key
                api_key_raw = row["api_key_encrypted"]
                if api_key_raw:
                    try:
                        crypto.decrypt(api_key_raw)
                    except Exception:
                        failures.append("api_key_encrypted")

                # Try decrypting password
                password_raw = row["password_encrypted"]
                if password_raw:
                    try:
                        crypto.decrypt(password_raw)
                    except Exception:
                        failures.append("password_encrypted")

                if failures:
                    entry = {
                        "id": str(row["id"]),
                        "tenant_id": str(row["tenant_id"]),
                        "name": row["name"],
                        "source_type": row["source_type"],
                        "current_status": row["status"],
                        "current_error": row["last_error"],
                        "failed_fields": failures,
                    }
                    broken.append(entry)

            if not broken:
                print("✓ All monitoring sources have valid credentials.")
                exit_code = 0
            else:
                print(f"\n⚠ Found {len(broken)} source(s) with broken credentials:\n")
                print(f"{'ID':<38} {'Name':<30} {'Type':<15} {'Status':<20} {'Failed Fields'}")
                print("-" * 120)
                for b in broken:
                    print(f"{b['id']:<38} {b['name']:<30} {b['source_type']:<15} {b['current_status']:<20} {', '.join(b['failed_fields'])}")
                print()

                if args.fix:
                    now = datetime.now(timezone.utc)
                    msg = "Stored credentials could not be decrypted. Please re-enter them."
                    for b in broken:
                        session.execute(
                            text("""
                                UPDATE monitoring_sources
                                SET status = 'credentials_invalid',
                                    last_error = :msg,
                                    last_sync_at = :now
                                WHERE id = :sid AND is_deleted = false
                            """),
                            {"sid": b["id"], "msg": msg, "now": now},
                        )
                    session.commit()
                    print(f"✓ Marked {len(broken)} source(s) as 'credentials_invalid'.")
                else:
                    print(f"Run with --fix to mark these sources as 'credentials_invalid'.")
                    print("After fixing, the scheduler will skip them (see MonitoringSourceRepository.list_due).")

                exit_code = 1
    finally:
        engine.dispose()

    return exit_code
