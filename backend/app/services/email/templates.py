"""Plain-text account emails. Links use APP_PUBLIC_URL, never the request Host."""
from __future__ import annotations

from urllib.parse import urlencode

from app.core.config import get_settings


def _link(path: str, token: str) -> str:
    base = get_settings().app_public_url.rstrip("/")
    return f"{base}{path}?{urlencode({'token': token})}"


def password_reset(token: str, minutes: int) -> tuple[str, str]:
    return (
        "Reset your HSDEEP CORE AI password",
        "Someone asked to reset the password for this account.\n\n"
        f"Set a new password: {_link('/reset-password', token)}\n\n"
        f"The link works once and expires in {minutes} minutes. If you did not "
        "ask for this, ignore this email; your password is unchanged.\n",
    )


def email_verify(token: str, hours: int) -> tuple[str, str]:
    return (
        "Confirm your email for HSDEEP CORE AI",
        f"Confirm this email address: {_link('/verify-email', token)}\n\n"
        f"The link works once and expires in {hours} hours.\n",
    )


def invite(token: str, org_name: str, days: int) -> tuple[str, str]:
    return (
        f"You're invited to {org_name} on HSDEEP CORE AI",
        f"You have been invited to {org_name}.\n\n"
        f"Set your password and sign in: {_link('/accept-invite', token)}\n\n"
        f"The link works once and expires in {days} days.\n",
    )
