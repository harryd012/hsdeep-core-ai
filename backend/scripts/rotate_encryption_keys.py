"""
Encryption key status and rotation (enterprise security programme S4).

    python scripts/rotate_encryption_keys.py --status            # what each key still protects
    python scripts/rotate_encryption_keys.py --rotate            # re-encrypt with the current key
    python scripts/rotate_encryption_keys.py --rotate --tenant <uuid>

To rotate: set SECRET_ENCRYPTION_KEY to the new key, put the old one in
SECRET_ENCRYPTION_KEYS_PREVIOUS, restart the API, run --rotate, and remove the
old key once --status shows nothing left on it. Prints counts and key
fingerprints only, never a secret. Exits 1 if any value is unreadable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.services.key_rotation import encryption_status, rotate_encryption  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--rotate", action="store_true")
    parser.add_argument("--tenant", type=uuid.UUID, default=None, help="limit to one tenant")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        report = await (rotate_encryption if args.rotate else encryption_status)(db, args.tenant)
    print(json.dumps(report.as_dict(), indent=2))
    if report.unreadable:
        print(f"{report.unreadable} value(s) cannot be decrypted with any configured key.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
