"""
Repository Pattern: all direct SQLAlchemy access for User (Auth + RBAC
sprint, P1). Same convention as AlertRepository - the service layer talks to
this class only, repositories flush (never commit); the caller/route owns the
transaction boundary.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.role import Role
from app.models.user import User

# Columns that need a DB round-trip to get their real value after insert
# (server_default timestamps). Deliberately excludes relationship attributes
# (roles) - refreshing with an explicit attribute_names list only expires
# and reloads the named attributes, leaving everything else (including an
# already-loaded, e.g. freshly-initialized-empty, `roles` collection)
# untouched. A bare `db.refresh(user)` with no attribute_names expires ALL
# attributes, including relationships, which then requires a lazy (sync) load
# the next time `.roles` is touched - not allowed on an AsyncSession outside
# an awaited context, and the source of the
# "greenlet_spawn has not been called" error if triggered here.
_REFRESHABLE_COLUMNS = ("created_at", "updated_at")


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _with_grants(self, stmt):
        """Eager-load roles -> permissions so building a Principal's permission
        set never triggers lazy I/O on an async session (which would raise)."""
        return stmt.options(selectinload(User.roles).selectinload(Role.permissions))

    async def get_by_email(self, tenant_id: uuid.UUID, email: str) -> User | None:
        stmt = self._with_grants(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
                User.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> User | None:
        stmt = self._with_grants(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
                User.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_any_tenant(self, user_id: uuid.UUID) -> User | None:
        """Used on the refresh path, where the tenant is taken from the stored
        refresh-token row rather than a caller-supplied value."""
        stmt = self._with_grants(
            select(User).where(User.id == user_id, User.is_deleted.is_(False))
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email_any_tenant(self, email: str) -> list[User]:
        """Cross-tenant email lookup for the tenant-less login path.

        A real end user never knows their tenant UUID, so /auth/login
        supports omitting tenant_id and resolving it from the email instead.
        Returns ALL matches (ideally 0 or 1 in the common case; more than one
        is a rare-but-legitimate B2B edge case where the same email was used
        to sign up under more than one organization). The caller decides how
        to handle a non-single-match result - this method never guesses.
        """
        stmt = self._with_grants(
            select(User).where(User.email == email, User.is_deleted.is_(False))
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def list(self, tenant_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[User]:
        stmt = (
            self._with_grants(select(User).where(User.tenant_id == tenant_id, User.is_deleted.is_(False)))
            .order_by(User.email)
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        email: str,
        password_hash: str | None,
        full_name: str | None = None,
        is_superuser: bool = False,
    ) -> User:
        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            is_superuser=is_superuser,
        )
        self.db.add(user)
        await self.db.flush()
        # Scoped refresh only - see _REFRESHABLE_COLUMNS docstring. A fresh
        # instance's `roles` collection is already loaded (empty); don't
        # expire it here, since AuthService.create_user() may immediately
        # call assign_roles() on this same object.
        await self.db.refresh(user, attribute_names=list(_REFRESHABLE_COLUMNS))
        return user

    async def update(
        self,
        user: User,
        *,
        full_name: str | None = None,
        is_active: bool | None = None,
        is_superuser: bool | None = None,
    ) -> User:
        """Apply partial updates to a user's profile fields. Only fields that
        are not None are applied, so callers can update a single field without
        clobbering the others."""
        if full_name is not None:
            user.full_name = full_name
        if is_active is not None:
            user.is_active = is_active
        if is_superuser is not None:
            user.is_superuser = is_superuser
        await self.db.flush()
        await self.db.refresh(user, attribute_names=list(_REFRESHABLE_COLUMNS))
        return user

    async def set_password(self, user: User, *, password_hash: str) -> User:
        user.password_hash = password_hash
        await self.db.flush()
        return user

    async def touch_last_login(self, user: User) -> User:
        user.last_login_at = datetime.now(timezone.utc)
        user.failed_login_count = 0
        user.locked_until = None
        await self.db.flush()
        return user

    async def record_failed_login(self, user: User, *, max_attempts: int, lockout_minutes: int) -> User:
        """Count a failed login; lock the account once max_attempts is reached."""
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if max_attempts > 0 and user.failed_login_count >= max_attempts:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=lockout_minutes)
            user.failed_login_count = 0
        await self.db.flush()
        return user

    async def clear_lockout(self, user: User) -> User:
        user.failed_login_count = 0
        user.locked_until = None
        await self.db.flush()
        return user

    async def assign_roles(self, user: User, roles: list[Role]) -> User:
        """Replace the user's role set.

        ``user.roles`` uses the default ``lazy='select'`` strategy, which means
        the assignment ``user.roles = roles`` triggers SQLAlchemy to compare
        against the *current* collection — a comparison that lazy-loads the old
        value.  On an ``AsyncSession`` that lazy-load is synchronous and raises
        ``MissingGreenlet``.

        The project's ``Base`` class does not inherit ``AsyncAttrs``, so
        ``awaitable_attrs`` is unavailable.  We therefore replace role
        assignments via the ``user_roles`` association table directly (delete
        + insert), then re-query the user with ``selectinload(User.roles)`` so
        the in-memory ``roles`` collection is populated asynchronously — no
        sync lazy-load is ever triggered.
        """
        from sqlalchemy import delete, insert
        from app.models.role import user_roles as _user_roles

        # 1. Clear existing role assignments via the association table.
        await self.db.execute(
            delete(_user_roles).where(_user_roles.c.user_id == user.id)
        )
        # 2. Insert new role assignments.
        if roles:
            await self.db.execute(
                insert(_user_roles).values(
                    [{"user_id": user.id, "role_id": r.id} for r in roles]
                )
            )
        await self.db.flush()

        # 3. Re-query the user with selectinload so the in-memory ``roles``
        #    collection is populated asynchronously (no lazy load).  The
        #    identity map returns the SAME ``user`` object, so the caller's
        #    reference is updated in-place.
        stmt = select(User).options(selectinload(User.roles)).where(User.id == user.id)
        result = await self.db.execute(stmt)
        loaded = result.scalar_one()
        # Scoped refresh — do NOT expire ``roles`` right after loading it,
        # or the next access (e.g. resolved_permissions) would re-trigger a
        # sync lazy-load.
        await self.db.refresh(loaded, attribute_names=list(_REFRESHABLE_COLUMNS))
        return loaded

    async def soft_delete(self, user: User) -> None:
        user.is_deleted = True
        user.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()

    @staticmethod
    def resolved_permissions(user: User) -> frozenset[str]:
        """Flatten a (grants-loaded) user's roles into a permission-code set.
        Pure function over already-loaded relationships - no I/O."""
        return frozenset(
            perm.code for role in user.roles for perm in role.permissions
        )
