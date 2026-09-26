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
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        row = RefreshToken(
            user_id=user_id, tenant_id=tenant_id, token_hash=token_hash, expires_at=expires_at
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
