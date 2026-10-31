"""
Repository Pattern: direct SQLAlchemy access for RefreshToken rows (Auth +
RBAC sprint, P1). Stores/looks up only token HASHES - callers hash the
plaintext via app.core.auth.hash_refresh_token before touching this layer, so
a plaintext refresh token never reaches SQL or a log line.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        session_id: uuid.UUID | None = None,
    ) -> RefreshToken:
        row = RefreshToken(
            user_id=user_id, tenant_id=tenant_id, token_hash=token_hash, expires_at=expires_at,
            session_id=session_id or uuid.uuid4(),
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def get_active_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Return the row only if it exists, is not revoked, and is not
        expired - so callers get a single authoritative 'is this token usable'
        answer rather than re-checking the fields themselves."""
        now = datetime.now(timezone.utc)
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """The row for a presented token whatever its state, so a reused
        (already revoked) token can be told apart from an unknown one."""
        result = await self.db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        return result.scalar_one_or_none()

    async def revoke_session(self, session_id: uuid.UUID) -> int:
        """Revoke every active token of one sign-in session."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return result.rowcount or 0

    async def session_is_live(self, session_id: uuid.UUID) -> bool:
        """True while the session has an unrevoked, unexpired token AND its
        user is still active. Revoking (logout, password reset, replay) or
        disabling the user ends every access token of the session."""
        from app.models.user import User

        now = datetime.now(timezone.utc)
        stmt = (
            select(RefreshToken.id)
            .join(User, User.id == RefreshToken.user_id)
            .where(
                RefreshToken.session_id == session_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
                User.is_active.is_(True),
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).first() is not None

    async def revoke(self, row: RefreshToken) -> None:
        row.revoked_at = datetime.now(timezone.utc)
        await self.db.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Bulk-revoke every active token for a user (logout-everywhere / admin
        action). Returns the number of rows affected."""
        now = datetime.now(timezone.utc)
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        result = await self.db.execute(stmt)
        return result.rowcount or 0
