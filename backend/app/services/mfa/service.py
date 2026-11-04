"""
MFA service (enterprise security programme S2): authenticator-app enrolment,
sign-in codes and recovery codes for one user.

Rules:
  * Set-up stores a new encrypted secret, unconfirmed; it becomes MFA only
    after the user proves the app works with a valid code (confirm). Starting
    set-up again replaces an unconfirmed secret, never a confirmed one.
  * A TOTP code is accepted once: its time step must be newer than the last
    accepted one (replay protection).
  * Recovery codes: 10 one-time codes, shown once at confirm or regenerate,
    stored as SHA-256 hashes. Regenerating invalidates the old set.
  * Disabling needs a current code (or a recovery code); an admin reset needs
    security.manage and is enforced in the route.
"""
from __future__ import annotations

import hashlib
import io
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import segno
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SecretCrypto
from app.models.user import User
from app.models.user_mfa import MfaRecoveryCode, UserMfa
from app.services.mfa import totp

RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


class MfaError(ValueError):
    """User-facing MFA problem (bad code, wrong state)."""


@dataclass(frozen=True)
class MfaStatus:
    enabled: bool
    confirmed_at: datetime | None
    recovery_codes_remaining: int
    setup_pending: bool


@dataclass(frozen=True)
class MfaSetup:
    secret: str
    otpauth_uri: str
    qr_svg: str


def _hash_code(code: str) -> str:
    normalized = code.strip().lower().replace("-", "").replace(" ", "")
    return hashlib.sha256(normalized.encode()).hexdigest()


def _new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def _qr_svg(uri: str) -> str:
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False, svgns=True, dark="#0b1220", light="#ffffff")
    return buf.getvalue().decode()


class MfaService:
    def __init__(self, db: AsyncSession, crypto: SecretCrypto | None = None) -> None:
        self.db = db
        self.crypto = crypto or SecretCrypto()

    async def _row(self, user_id: uuid.UUID) -> UserMfa | None:
        return (await self.db.execute(select(UserMfa).where(UserMfa.user_id == user_id))).scalar_one_or_none()

    async def is_enabled(self, user_id: uuid.UUID) -> bool:
        row = await self._row(user_id)
        return row is not None and row.confirmed_at is not None

    async def status(self, user_id: uuid.UUID) -> MfaStatus:
        row = await self._row(user_id)
        remaining = (
            await self.db.execute(
                select(func.count()).select_from(MfaRecoveryCode)
                .where(MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None))
            )
        ).scalar_one()
        return MfaStatus(
            enabled=row is not None and row.confirmed_at is not None,
            confirmed_at=row.confirmed_at if row else None,
            recovery_codes_remaining=int(remaining),
            setup_pending=row is not None and row.confirmed_at is None,
        )

    async def begin_setup(self, user: User) -> MfaSetup:
        row = await self._row(user.id)
        if row is not None and row.confirmed_at is not None:
            raise MfaError("Multi-factor authentication is already on. Turn it off first to use a new device.")
        secret = totp.new_secret()
        if row is None:
            row = UserMfa(user_id=user.id, tenant_id=user.tenant_id, secret_encrypted=self.crypto.encrypt(secret))
            self.db.add(row)
        else:
            row.secret_encrypted = self.crypto.encrypt(secret)
            row.last_used_step = None
        await self.db.flush()
        uri = totp.provisioning_uri(secret, account=user.email)
        return MfaSetup(secret=secret, otpauth_uri=uri, qr_svg=_qr_svg(uri))

    async def confirm(self, user: User, code: str) -> list[str]:
        """Turn MFA on with a first valid code; returns the recovery codes (shown once)."""
        row = await self._row(user.id)
        if row is None:
            raise MfaError("Start set-up first.")
        if row.confirmed_at is not None:
            raise MfaError("Multi-factor authentication is already on.")
        step = totp.verify(self.crypto.decrypt(row.secret_encrypted), code)
        if step is None:
            raise MfaError("That code didn't match. Check the time on your device and try the newest code.")
        row.confirmed_at = datetime.now(timezone.utc)
        row.last_used_step = step
        codes = await self._replace_recovery_codes(user.id)
        await self.db.flush()
        return codes

    async def verify(self, user_id: uuid.UUID, code: str) -> str | None:
        """Check a sign-in (or step-up) code. Returns "totp" or "recovery" when
        accepted, None otherwise. A used TOTP step or recovery code is spent."""
        row = await self._row(user_id)
        if row is None or row.confirmed_at is None:
            return None
        cleaned = (code or "").strip()
        if cleaned.replace(" ", "").isdigit():
            step = totp.verify(self.crypto.decrypt(row.secret_encrypted), cleaned)
            if step is None or (row.last_used_step is not None and step <= row.last_used_step):
                return None
            row.last_used_step = step
            await self.db.flush()
            return "totp"
        rc = (
            await self.db.execute(
                select(MfaRecoveryCode).where(
                    MfaRecoveryCode.user_id == user_id,
                    MfaRecoveryCode.code_hash == _hash_code(cleaned),
                    MfaRecoveryCode.used_at.is_(None),
                ).with_for_update()
            )
        ).scalar_one_or_none()
        if rc is None:
            return None
        rc.used_at = datetime.now(timezone.utc)
        await self.db.flush()
        return "recovery"

    async def regenerate_recovery_codes(self, user_id: uuid.UUID, code: str) -> list[str]:
        if await self.verify(user_id, code) is None:
            raise MfaError("That code didn't match.")
        return await self._replace_recovery_codes(user_id)

    async def disable(self, user_id: uuid.UUID, code: str) -> None:
        if not await self.is_enabled(user_id):
            raise MfaError("Multi-factor authentication is not on.")
        if await self.verify(user_id, code) is None:
            raise MfaError("That code didn't match.")
        await self.remove(user_id)

    async def remove(self, user_id: uuid.UUID) -> bool:
        """Delete the enrolment and recovery codes (disable or admin reset). True if any existed."""
        result = await self.db.execute(delete(UserMfa).where(UserMfa.user_id == user_id))
        await self.db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
        await self.db.flush()
        return bool(result.rowcount)

    async def _replace_recovery_codes(self, user_id: uuid.UUID) -> list[str]:
        await self.db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
        codes = [_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        self.db.add_all(MfaRecoveryCode(user_id=user_id, code_hash=_hash_code(c)) for c in codes)
        await self.db.flush()
        return codes
