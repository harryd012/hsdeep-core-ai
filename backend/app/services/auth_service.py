"""
AuthService (Auth + RBAC sprint, P1).

Application-service facade for authentication and IAM administration - the
thin layer the /auth routes call, orchestrating core/auth (hashing + JWT) and
the user/role/permission/refresh-token repositories. Follows the same
convention as the other services/<name>_service.py facades: business rules
and cross-repository coordination live here; repositories only run SQL;
routes stay thin and own the commit.

Security invariants enforced here:
  * A login only succeeds for an active user with a verifiable local password.
  * The same generic error is returned for unknown-user and bad-password, so
    the API never reveals which emails exist (user-enumeration defense).
  * Refresh is single-use: on every refresh the presented token is revoked
    and a new one issued (rotation), so a stolen-then-used token is detectable
    as a revoked-token reuse and cannot be replayed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.core import auth
from app.core.config import get_settings
from app.core.permissions import DEFAULT_ROLES, resolve_role_permissions
from app.models.role import Role
from app.models.user import User
from app.repositories.permission_repository import PermissionRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.user_repository import UserRepository

settings = get_settings()


class AuthError(Exception):
    """Base for auth failures the route maps to 401."""


class InvalidCredentials(AuthError):
    pass


class InvalidRefreshToken(AuthError):
    pass


class NotFound(Exception):
    """Maps to 404."""


class Conflict(Exception):
    """Maps to 409 (e.g. duplicate email/role name)."""


class AuthService:
    def __init__(
        self,
        *,
        users: UserRepository,
        roles: RoleRepository,
        permissions: PermissionRepository,
        refresh_tokens: RefreshTokenRepository,
    ):
        self.users = users
        self.roles = roles
        self.permissions = permissions
        self.refresh_tokens = refresh_tokens

    # -- Token minting ----------------------------------------------------

    async def _issue_tokens(self, user: User) -> tuple[str, str, int]:
        """Mint an access JWT + a rotated refresh token for `user`. The
        refresh token plaintext is returned to the caller and only its hash is
        persisted."""
        perms = UserRepository.resolved_permissions(user)
        access = auth.create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            is_superuser=user.is_superuser,
            permissions=perms,
        )
        refresh_plain = auth.generate_refresh_token()
        await self.refresh_tokens.create(
            user_id=user.id,
            tenant_id=user.tenant_id,
            token_hash=auth.hash_refresh_token(refresh_plain),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_expire_days),
        )
        expires_in = settings.access_token_expire_minutes * 60
        return access, refresh_plain, expires_in

    # -- Authentication ---------------------------------------------------

    async def login(
        self, *, tenant_id: uuid.UUID | None, email: str, password: str
    ) -> tuple[str, str, int, uuid.UUID | None]:
        """Authenticate and return (access, refresh, expires_in,
        resolved_tenant_id). resolved_tenant_id is the tenant the login
        actually succeeded/failed against, for the caller's audit trail -
        it's None only when tenant_id was omitted AND email resolution
        could not settle on exactly one tenant (unknown email, or the same
        email registered under more than one organization).

        When tenant_id is omitted (the normal case for a real end user, who
        never knows their tenant UUID), the tenant is resolved by a global
        email lookup. Zero or more-than-one matches both fall through to the
        SAME generic InvalidCredentials error as a wrong password would -
        never revealing which case occurred (user-enumeration defense, same
        principle already documented above for the tenant_id-supplied path).
        """
        resolved_tenant_id = tenant_id
        user: User | None = None
        if tenant_id is not None:
            user = await self.users.get_by_email(tenant_id, email)
        else:
            candidates = await self.users.get_by_email_any_tenant(email)
            if len(candidates) == 1:
                user = candidates[0]
                resolved_tenant_id = user.tenant_id
            # 0 or >1 matches: user stays None -> generic InvalidCredentials
            # below, and resolved_tenant_id stays None for the caller's audit.

        # Verify a password even when the user is missing to keep timing/shape
        # uniform, then reject with a single generic error either way.
        password_ok = auth.verify_password(password, user.password_hash if user else None)
        if user is None or not user.is_active or not password_ok:
            raise InvalidCredentials("Invalid email or password")
        await self.users.touch_last_login(user)
        access, refresh, expires_in = await self._issue_tokens(user)
        return access, refresh, expires_in, user.tenant_id

    async def refresh(self, *, refresh_token: str) -> tuple[str, str, int]:
        token_hash = auth.hash_refresh_token(refresh_token)
        row = await self.refresh_tokens.get_active_by_hash(token_hash)
        if row is None:
            raise InvalidRefreshToken("Invalid or expired refresh token")
        user = await self.users.get_by_id_any_tenant(row.user_id)
        if user is None or not user.is_active:
            raise InvalidRefreshToken("Invalid or expired refresh token")
        # Rotate: revoke the presented token before issuing the replacement.
        await self.refresh_tokens.revoke(row)
        return await self._issue_tokens(user)

    async def logout(self, *, refresh_token: str) -> None:
        row = await self.refresh_tokens.get_active_by_hash(auth.hash_refresh_token(refresh_token))
        if row is not None:
            await self.refresh_tokens.revoke(row)

    # -- Tenant RBAC provisioning ----------------------------------------

    async def provision_tenant_rbac(self, tenant_id: uuid.UUID) -> list[Role]:
        """Create the DEFAULT_ROLES for a tenant from the shared catalog.
        Idempotent: skips a role that already exists (so re-running against an
        already-provisioned tenant is safe). Called at tenant creation and by
        seed/test helpers."""
        created: list[Role] = []
        for name, (description, grant_spec) in DEFAULT_ROLES.items():
            existing = await self.roles.get_by_name(tenant_id, name)
            if existing is not None:
                continue
            codes = resolve_role_permissions(grant_spec)
            perms = await self.permissions.get_by_codes(codes)
            role = await self.roles.create(
                tenant_id=tenant_id, name=name, description=description, permissions=perms
            )
            created.append(role)
        return created

    # -- User administration ---------------------------------------------

    async def create_user(
        self,
        *,
        tenant_id: uuid.UUID,
        email: str,
        password: str,
        full_name: str | None,
        is_superuser: bool,
        role_ids: list[uuid.UUID],
    ) -> User:
        if await self.users.get_by_email(tenant_id, email) is not None:
            raise Conflict(f"A user with email {email!r} already exists in this tenant")
        user = await self.users.create(
            tenant_id=tenant_id,
            email=email,
            password_hash=auth.hash_password(password),
            full_name=full_name,
            is_superuser=is_superuser,
        )
        if role_ids:
            roles = await self.roles.get_many_by_ids(tenant_id, role_ids)
            await self.users.assign_roles(user, roles)
        return user

    async def set_password(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, password: str) -> User:
        user = await self.users.get_by_id(user_id, tenant_id)
        if user is None:
            raise NotFound("User not found")
        await self.users.set_password(user, password_hash=auth.hash_password(password))
        # Force re-auth everywhere: a password change should invalidate
        # outstanding refresh tokens.
        await self.refresh_tokens.revoke_all_for_user(user.id)
        return user

    async def assign_roles(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, role_ids: list[uuid.UUID]) -> User:
        user = await self.users.get_by_id(user_id, tenant_id)
        if user is None:
            raise NotFound("User not found")
        roles = await self.roles.get_many_by_ids(tenant_id, role_ids)
        return await self.users.assign_roles(user, roles)

    async def list_users(self, tenant_id: uuid.UUID, *, limit: int, offset: int) -> list[User]:
        return await self.users.list(tenant_id, limit=limit, offset=offset)

    async def delete_user(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        user = await self.users.get_by_id(user_id, tenant_id)
        if user is None:
            raise NotFound("User not found")
        await self.users.soft_delete(user)
        await self.refresh_tokens.revoke_all_for_user(user.id)

    # -- Role administration ---------------------------------------------

    async def create_role(
        self, *, tenant_id: uuid.UUID, name: str, description: str | None, permission_codes: list[str]
    ) -> Role:
        if await self.roles.get_by_name(tenant_id, name) is not None:
            raise Conflict(f"A role named {name!r} already exists in this tenant")
        perms = await self.permissions.get_by_codes(permission_codes)
        return await self.roles.create(
            tenant_id=tenant_id, name=name, description=description, permissions=perms
        )

    async def update_role(
        self,
        *,
        tenant_id: uuid.UUID,
        role_id: uuid.UUID,
        description: str | None,
        permission_codes: list[str] | None,
    ) -> Role:
        role = await self.roles.get_by_id(role_id, tenant_id)
        if role is None:
            raise NotFound("Role not found")
        if description is not None:
            role.description = description
        if permission_codes is not None:
            perms = await self.permissions.get_by_codes(permission_codes)
            await self.roles.set_permissions(role, perms)
        return role

    async def list_roles(self, tenant_id: uuid.UUID) -> list[Role]:
        return await self.roles.list(tenant_id)

    async def delete_role(self, *, tenant_id: uuid.UUID, role_id: uuid.UUID) -> None:
        role = await self.roles.get_by_id(role_id, tenant_id)
        if role is None:
            raise NotFound("Role not found")
        await self.roles.soft_delete(role)

    async def list_permissions(self):
        return await self.permissions.list_all()
