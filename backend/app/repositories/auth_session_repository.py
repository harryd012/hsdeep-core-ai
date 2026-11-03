"""
Repository for sign-in session details (enterprise security programme S1).

Rows only describe sessions (device, start, last use); whether a session is
live is decided by its refresh tokens (RefreshTokenRepository).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_session import AuthSession
from app.models.refresh_token import RefreshToken


class AuthSessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def touch(
        self,
        *,
        session_id: uuid.UUID,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        """Record a sign-in (new row) or a refresh (last use, latest address)."""
        now = datetime.now(timezone.utc)
        ua = (user_agent or "")[:300] or None
        stmt = insert(AuthSession).values(
            session_id=session_id, tenant_id=tenant_id, user_id=user_id,
            ip_address=ip_address, user_agent=ua, created_at=now, last_seen_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[AuthSession.session_id],
            set_={
                "last_seen_at": now,
                "ip_address": func.coalesce(stmt.excluded.ip_address, AuthSession.ip_address),
                "user_agent": func.coalesce(stmt.excluded.user_agent, AuthSession.user_agent),
            },
        )
        await self.db.execute(stmt)

    async def live_sessions(self, user_id: uuid.UUID) -> list[dict]:
        """The user's live sessions (an unrevoked, unexpired refresh token),
        newest activity first, with their details when recorded."""
        now = datetime.now(timezone.utc)
        live = (
            select(
                RefreshToken.session_id.label("session_id"),
                func.min(RefreshToken.created_at).label("token_started"),
                func.max(RefreshToken.created_at).label("token_last"),
                func.max(RefreshToken.expires_at).label("expires_at"),
            )
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.session_id.is_not(None),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .group_by(RefreshToken.session_id)
            .subquery()
        )
        stmt = (
            select(live, AuthSession.ip_address, AuthSession.user_agent, AuthSession.created_at, AuthSession.last_seen_at)
            .outerjoin(AuthSession, AuthSession.session_id == live.c.session_id)
        )
        rows = (await self.db.execute(stmt)).mappings().all()
        out = [
            {
                "session_id": r["session_id"],
                "started_at": r["created_at"] or r["token_started"],
                "last_seen_at": r["last_seen_at"] or r["token_last"],
                "expires_at": r["expires_at"],
                "ip_address": r["ip_address"],
                "user_agent": r["user_agent"],
            }
            for r in rows
        ]
        out.sort(key=lambda s: s["last_seen_at"], reverse=True)
        return out
