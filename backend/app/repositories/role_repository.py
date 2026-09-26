"""
Repository Pattern: direct SQLAlchemy access for tenant-scoped Role rows and
their permission grants (Auth + RBAC sprint, P1).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.permission import Permission
from app.models.role import Role

# Columns that need a DB round-trip to get their real value after insert
# (server_default timestamps). Deliberately excludes relationship attributes
# (permissions) - refreshing with an explicit attribute_names list only expires
# and reloads the named attributes, leaving everything else (including an
# already-loaded `permissions` collection) untouched. A bare `db.refresh(role)`
# with no attribute_names expires ALL attributes, including relationships,
# which then requires a lazy (sync) load the next time `.permissions` is
# touched - not allowed on an AsyncSession outside an awaited context.
_REFRESHABLE_COLUMNS = ("created_at", "updated_at")


class RoleRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _with_permissions(self, stmt):
        return stmt.options(selectinload(Role.permissions))

    async def get_by_id(self, role_id: uuid.UUID, tenant_id: uuid.UUID) -> Role | None:
        stmt = self._with_permissions(
            select(Role).where(
                Role.id == role_id, Role.tenant_id == tenant_id, Role.is_deleted.is_(False)
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, tenant_id: uuid.UUID, name: str) -> Role | None:
        stmt = self._with_permissions(
            select(Role).where(
                Role.tenant_id == tenant_id, Role.name == name, Role.is_deleted.is_(False)
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, tenant_id: uuid.UUID, role_ids: list[uuid.UUID]) -> list[Role]:
        if not role_ids:
            return []
        stmt = self._with_permissions(
            select(Role).where(
                Role.tenant_id == tenant_id,
                Role.id.in_(role_ids),
                Role.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def list(self, tenant_id: uuid.UUID) -> list[Role]:
        stmt = self._with_permissions(
            select(Role).where(Role.tenant_id == tenant_id, Role.is_deleted.is_(False)).order_by(Role.name)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def create(
        self, *, tenant_id: uuid.UUID, name: str, description: str | None, permissions: list[Permission]
    ) -> Role:
        role = Role(tenant_id=tenant_id, name=name, description=description)
        role.permissions = permissions
        self.db.add(role)
        await self.db.flush()
        await self.db.refresh(role)
        return role

    async def set_permissions(self, role: Role, permissions: list[Permission]) -> Role:
        """Replace the role's permission set.

        ``role.permissions`` uses the default ``lazy='select'`` strategy, so the
        assignment ``role.permissions = permissions`` triggers SQLAlchemy to
        compare against the *current* collection - a comparison that lazy-loads
        the old value. On an ``AsyncSession`` that lazy-load is synchronous and
        raises ``MissingGreenlet``.

        We therefore replace permission grants via the ``role_permissions``
        association table directly (delete + insert), then re-query the role
        with ``selectinload(Role.permissions)`` so the in-memory collection is
        populated asynchronously - no sync lazy-load is ever triggered.
        """
        from sqlalchemy import delete, insert
        from app.models.role import role_permissions as _role_permissions

        # 1. Clear existing permission grants via the association table.
        await self.db.execute(
            delete(_role_permissions).where(_role_permissions.c.role_id == role.id)
        )
        # 2. Insert new permission grants.
        if permissions:
            await self.db.execute(
                insert(_role_permissions).values(
                    [{"role_id": role.id, "permission_id": p.id} for p in permissions]
                )
            )
        await self.db.flush()

        # 3. Re-query the role with selectinload so the in-memory
        #    ``permissions`` collection is populated asynchronously (no lazy
        #    load). The identity map returns the SAME ``role`` object, so the
        #    caller's reference is updated in-place.
        stmt = (
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.id == role.id)
        )
        result = await self.db.execute(stmt)
        loaded = result.scalar_one()
        # Scoped refresh - do NOT expire ``permissions`` right after loading
        # it, or the next access would re-trigger a sync lazy-load.
        await self.db.refresh(loaded, attribute_names=list(_REFRESHABLE_COLUMNS))
        return loaded

    async def soft_delete(self, role: Role) -> None:
        role.is_deleted = True
        role.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()
