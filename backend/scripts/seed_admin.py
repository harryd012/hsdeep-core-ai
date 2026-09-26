"""
Bootstrap seed: system permission catalog, default tenant roles, and a
first login (Owner) for a given tenant.

Solves the auth "chicken and egg" problem: /api/auth/users requires
require_permission("users.create"), so nobody can create the first user
through the API. This script creates it directly, through the same
AuthService/repositories the API itself uses (never raw SQL for the
auth rows), so password hashing and role/permission wiring are identical
to what the app would do at runtime.

Idempotent:
  - Permissions are inserted only if their code doesn't already exist
    (PERMISSION_CATALOG is the single source of truth - see
    app/core/permissions.py).
  - Roles are provisioned via AuthService.provision_tenant_rbac(), which
    already skips a role that exists for the tenant.
  - The seed user is only created if no user with SEED_ADMIN_EMAIL exists
    yet for the tenant; otherwise the script reports it and exits cleanly.

Environment variables read (from backend/.env, same convention as
scripts/seed_prtg_dev.py):
    SEED_TENANT_ID      UUID of the tenant to seed (required)
    SEED_ADMIN_EMAIL    Login email for the first user (required)
    SEED_ADMIN_PASSWORD Plaintext password, hashed via app.core.auth
                         before storage - never logged (required)
    SEED_ADMIN_NAME     Optional full_name, defaults to "Admin"

Usage (from the `backend` directory, with its virtualenv active):
    python scripts/seed_admin.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

# Put backend/ (this script's parent directory) on sys.path so `import
# app...` resolves regardless of current working directory - same trick
# used by scripts/seed_prtg_dev.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.core.permissions import PERMISSION_CATALOG  # noqa: E402
from app.models.permission import Permission  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.repositories.permission_repository import PermissionRepository  # noqa: E402
from app.repositories.refresh_token_repository import RefreshTokenRepository  # noqa: E402
from app.repositories.role_repository import RoleRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.services.auth_service import AuthService, Conflict  # noqa: E402

REQUIRED_ENV_VARS = ("SEED_TENANT_ID", "SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD")


def _missing_required_env_vars() -> list[str]:
    return [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]


def _get_auth_service(db: AsyncSession) -> AuthService:
    # Mirrors app.api.routes.auth.get_auth_service exactly.
    return AuthService(
        users=UserRepository(db),
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        refresh_tokens=RefreshTokenRepository(db),
    )


async def _seed_permission_catalog(db: AsyncSession) -> int:
    """Insert any PERMISSION_CATALOG rows missing from the `permissions`
    table. Returns the number of rows created."""
    existing_codes = set(
        (await db.execute(select(Permission.code))).scalars().all()
    )
    created = 0
    for code, description in PERMISSION_CATALOG:
        if code in existing_codes:
            continue
        db.add(Permission(code=code, description=description))
        created += 1
    if created:
        await db.flush()
    return created


async def _get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    stmt = select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted.is_(False))
    return (await db.execute(stmt)).scalar_one_or_none()


async def seed_admin(db: AsyncSession) -> int:
    missing = _missing_required_env_vars()
    if missing:
        print(f"[seed_admin] Aborted: missing environment variable(s): {', '.join(missing)}")
        return 1

    tenant_id = uuid.UUID(os.environ["SEED_TENANT_ID"])
    admin_email = os.environ["SEED_ADMIN_EMAIL"]
    admin_password = os.environ["SEED_ADMIN_PASSWORD"]
    admin_name = os.environ.get("SEED_ADMIN_NAME", "Admin")

    tenant = await _get_tenant(db, tenant_id)
    if tenant is None:
        print(f"[seed_admin] Aborted: no tenant found with id={tenant_id}")
        return 1

    # 1. Permission catalog (system-wide, not tenant-scoped).
    created_perms = await _seed_permission_catalog(db)
    print(f"[seed_admin] Permission catalog: {created_perms} new row(s) inserted "
          f"({len(PERMISSION_CATALOG)} total in catalog).")

    service = _get_auth_service(db)

    # 2. Default roles for this tenant (Owner/Admin/Operator/Viewer).
    #    provision_tenant_rbac() itself skips any role that already exists.
    created_roles = await service.provision_tenant_rbac(tenant_id)
    if created_roles:
        print(f"[seed_admin] Roles created for tenant: {[r.name for r in created_roles]}")
    else:
        print("[seed_admin] Roles: all default roles already exist for this tenant.")

    owner_role = await service.roles.get_by_name(tenant_id, "Owner")
    if owner_role is None:
        print("[seed_admin] Aborted: Owner role could not be found or created.")
        return 1

    # 3. First user, granted Owner + is_superuser (cross-tenant operator per
    #    the interim MSP model documented in app/core/auth.py).
    try:
        user = await service.create_user(
            tenant_id=tenant_id,
            email=admin_email,
            password=admin_password,
            full_name=admin_name,
            is_superuser=True,
            role_ids=[owner_role.id],
        )
    except Conflict:
        print(f"[seed_admin] User {admin_email!r} already exists for tenant {tenant_id} - no changes made.")
        return 0

    print(f"[seed_admin] Created user id={user.id} email={user.email} "
          f"tenant_id={tenant_id} role=Owner is_superuser=True")
    return 0


async def main() -> int:
    try:
        async with AsyncSessionLocal() as db:
            exit_code = await seed_admin(db)
            if exit_code == 0:
                await db.commit()
            else:
                await db.rollback()
            return exit_code
    except Exception as exc:  # noqa: BLE001 - top-level script: report and exit non-zero
        print(f"[seed_admin] Failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))