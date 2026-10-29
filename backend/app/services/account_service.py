"""
Account self-service: password reset, email verification and invitations.

Tokens are 32 random bytes, stored only as a SHA-256 hash, single use and
short-lived; issuing a new token of a purpose retires the user's earlier
unused ones. Request endpoints never reveal whether an email has an account:
an unknown email and a rate-limited one both look like success.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import auth
from app.models.account_token import AccountToken
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditService
from app.services.email import templates
from app.services.email.outbox import EmailDisabled, email_enabled, enqueue_email

PASSWORD_RESET_TTL = timedelta(minutes=30)
EMAIL_VERIFY_TTL = timedelta(hours=24)
INVITE_TTL = timedelta(days=7)
# Per-account cap on reset emails, on top of the per-IP auth rate limit.
MAX_RESETS_PER_HOUR = 3


class InvalidAccountToken(Exception):
    """Unknown, expired, already used or wrong-purpose token."""


class AccountService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.users = UserRepository(db)
        self.audit = AuditService(db)

    # -- tokens ---------------------------------------------------------------

    async def _issue(self, user: User, purpose: str, ttl: timedelta) -> str:
        now = datetime.now(timezone.utc)
        await self.db.execute(
            update(AccountToken)
            .where(
                AccountToken.user_id == user.id,
                AccountToken.purpose == purpose,
                AccountToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        token = auth.generate_refresh_token()
        self.db.add(AccountToken(
            user_id=user.id,
            tenant_id=user.tenant_id,
            purpose=purpose,
            token_hash=auth.hash_refresh_token(token),
            expires_at=now + ttl,
        ))
        await self.db.flush()
        return token

    async def _consume(self, token: str, purpose: str) -> User:
        now = datetime.now(timezone.utc)
        row = (
            await self.db.execute(
                select(AccountToken)
                .where(AccountToken.token_hash == auth.hash_refresh_token(token))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.purpose != purpose or row.used_at is not None or row.expires_at <= now:
            raise InvalidAccountToken("This link is invalid or has expired.")
        user = await self.users.get_by_id(row.user_id, row.tenant_id)
        if user is None or not user.is_active:
            raise InvalidAccountToken("This link is invalid or has expired.")
        row.used_at = now
        await self.db.flush()
        return user

    async def _audit(self, user: User, action: str, description: str) -> None:
        await self.audit.record(
            tenant_id=user.tenant_id,
            action=action,
            actor_id=user.id,
            actor_email=user.email,
            entity_type="auth",
            description=description,
        )

    async def _set_password(self, user: User, password: str) -> None:
        await self.users.set_password(user, password_hash=auth.hash_password(password))
        await self.users.clear_lockout(user)
        await RefreshTokenRepository(self.db).revoke_all_for_user(user.id)

    # -- password reset -------------------------------------------------------

    async def request_password_reset(self, email: str) -> None:
        if not email_enabled():
            raise EmailDisabled("Email is not configured on this deployment.")
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        for user in await self.users.get_by_email_any_tenant(email):
            if not user.is_active:
                continue
            recent = (
                await self.db.execute(
                    select(func.count()).where(
                        AccountToken.user_id == user.id,
                        AccountToken.purpose == "password_reset",
                        AccountToken.created_at >= since,
                    )
                )
            ).scalar_one()
            if recent >= MAX_RESETS_PER_HOUR:
                continue
            token = await self._issue(user, "password_reset", PASSWORD_RESET_TTL)
            subject, body = templates.password_reset(token, int(PASSWORD_RESET_TTL.total_seconds() // 60))
            await enqueue_email(
                self.db, to=user.email, subject=subject, body_text=body,
                category="password_reset", tenant_id=user.tenant_id,
            )
            await self._audit(user, "auth.password_reset_requested", "Password reset link sent")

    async def reset_password(self, token: str, password: str) -> User:
        user = await self._consume(token, "password_reset")
        await self._set_password(user, password)
        # Receiving the link proves the user controls the inbox.
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(timezone.utc)
        await self._audit(user, "auth.password_reset", "Password reset with an emailed link")
        return user

    # -- email verification ---------------------------------------------------

    async def send_verification(self, user: User) -> None:
        token = await self._issue(user, "email_verify", EMAIL_VERIFY_TTL)
        subject, body = templates.email_verify(token, int(EMAIL_VERIFY_TTL.total_seconds() // 3600))
        await enqueue_email(
            self.db, to=user.email, subject=subject, body_text=body,
            category="email_verify", tenant_id=user.tenant_id,
        )

    async def verify_email(self, token: str) -> User:
        user = await self._consume(token, "email_verify")
        user.email_verified_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self._audit(user, "auth.email_verified", "Email address verified")
        return user

    # -- invitations ----------------------------------------------------------

    async def send_invite(self, user: User) -> None:
        tenant = await self.db.get(Tenant, user.tenant_id)
        token = await self._issue(user, "invite", INVITE_TTL)
        subject, body = templates.invite(token, tenant.name if tenant else "your organization", INVITE_TTL.days)
        await enqueue_email(
            self.db, to=user.email, subject=subject, body_text=body,
            category="invite", tenant_id=user.tenant_id,
        )
        await self._audit(user, "auth.invite_sent", "Invitation sent")

    async def accept_invite(self, token: str, password: str) -> User:
        user = await self._consume(token, "invite")
        await self._set_password(user, password)
        user.email_verified_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self._audit(user, "auth.invite_accepted", "Invitation accepted")
        return user

