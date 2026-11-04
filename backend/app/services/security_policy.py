"""
Tenant security policy (enterprise security programme S1).

Single source for the policy a tenant sets on Settings > Security and for
enforcing it. Before S1 these values were stored and shown but nothing read
them; now:

  * password rules apply wherever a password is set (sign-up, admin create /
    reset, invitation, self-service reset);
  * ``max_login_attempts`` is the per-account lockout threshold at sign-in;
  * ``session_timeout_minutes`` is an idle timeout: a session whose refresh
    token has not been used for that long cannot be refreshed and ends.

Defaults (a tenant that never saved the page) follow NIST SP 800-63B: length
over composition rules, so no character classes are forced by default. A
tenant can turn them on. The idle timeout defaults to a working day.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.security_setting import SecuritySetting

#: Lower bound of the idle timeout: one access-token lifetime, so a session
#: that refreshes on time is never cut off between two refreshes.
MIN_SESSION_TIMEOUT_MINUTES = 15
MAX_SESSION_TIMEOUT_MINUTES = 30 * 24 * 60
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_LOGIN_ATTEMPTS_RANGE = (3, 20)

DEFAULT_SECURITY_SETTINGS: dict = {
    "min_password_length": MIN_PASSWORD_LENGTH,
    "require_uppercase": False,
    "require_numbers": False,
    "require_special_chars": False,
    "mfa_enabled": False,
    "session_timeout_minutes": 480,
    "max_login_attempts": 5,
}


def default_security_settings() -> dict:
    """What a tenant that never saved a policy gets. The lockout threshold is
    the deployment's LOGIN_MAX_FAILED_ATTEMPTS (0 turns lockout off there);
    a tenant that saves a policy sets its own."""
    return {**DEFAULT_SECURITY_SETTINGS, "max_login_attempts": get_settings().login_max_failed_attempts}


class PasswordPolicyError(ValueError):
    """The password breaks the tenant's policy; the message says how."""


@dataclass(frozen=True)
class SecurityPolicy:
    min_password_length: int
    require_uppercase: bool
    require_numbers: bool
    require_special_chars: bool
    session_timeout_minutes: int
    max_login_attempts: int
    #: Every user must sign in with MFA; those not enrolled must set it up first.
    require_mfa: bool = False
    #: False when the tenant never saved a policy (deployment defaults apply).
    saved: bool = False

    @classmethod
    def defaults(cls) -> "SecurityPolicy":
        d = default_security_settings()
        return cls(
            min_password_length=d["min_password_length"],
            require_uppercase=d["require_uppercase"],
            require_numbers=d["require_numbers"],
            require_special_chars=d["require_special_chars"],
            session_timeout_minutes=d["session_timeout_minutes"],
            max_login_attempts=d["max_login_attempts"],
        )

    def password_problems(self, password: str, email: str | None = None) -> list[str]:
        """Every rule the password breaks, as short phrases ('at least 12 characters')."""
        problems: list[str] = []
        if len(password) < self.min_password_length:
            problems.append(f"at least {self.min_password_length} characters")
        if len(password) > MAX_PASSWORD_LENGTH:
            problems.append(f"at most {MAX_PASSWORD_LENGTH} characters")
        if self.require_uppercase and not re.search(r"[A-Z]", password):
            problems.append("an upper-case letter")
        if self.require_numbers and not re.search(r"\d", password):
            problems.append("a number")
        if self.require_special_chars and not re.search(r"[^A-Za-z0-9]", password):
            problems.append("a symbol")
        local = (email or "").split("@", 1)[0].lower()
        if len(local) >= 4 and local in password.lower():
            problems.append("not your email name")
        return problems

    def check_password(self, password: str, email: str | None = None) -> None:
        problems = self.password_problems(password, email)
        if problems:
            raise PasswordPolicyError("The password needs " + ", ".join(problems) + ".")


async def load_policy(db: AsyncSession, tenant_id: uuid.UUID) -> SecurityPolicy:
    """The tenant's saved policy, or the defaults when it never saved one."""
    row = (
        await db.execute(select(SecuritySetting).where(SecuritySetting.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is None:
        return SecurityPolicy.defaults()
    return SecurityPolicy(
        min_password_length=max(row.min_password_length, MIN_PASSWORD_LENGTH),
        require_uppercase=row.require_uppercase,
        require_numbers=row.require_numbers,
        require_special_chars=row.require_special_chars,
        session_timeout_minutes=min(max(row.session_timeout_minutes, MIN_SESSION_TIMEOUT_MINUTES), MAX_SESSION_TIMEOUT_MINUTES),
        max_login_attempts=row.max_login_attempts,
        require_mfa=row.mfa_enabled,
        saved=True,
    )


__all__ = [
    "DEFAULT_SECURITY_SETTINGS",
    "MAX_LOGIN_ATTEMPTS_RANGE",
    "MAX_PASSWORD_LENGTH",
    "MAX_SESSION_TIMEOUT_MINUTES",
    "MIN_PASSWORD_LENGTH",
    "MIN_SESSION_TIMEOUT_MINUTES",
    "PasswordPolicyError",
    "SecurityPolicy",
    "default_security_settings",
    "load_policy",
]
