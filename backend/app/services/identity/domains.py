"""
Verified email domains (enterprise security programme S6).

A tenant adds a domain and gets a token; it proves ownership by publishing

    _hsdeep-challenge.<domain>   TXT   "hsdeep-verify=<token>"

A verified domain belongs to exactly one tenant (a partial unique index backs
this), and only verified domains route sign-in to a tenant's identity provider
or fall under "require SSO".
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import TenantDomain

CHALLENGE_PREFIX = "_hsdeep-challenge"
TOKEN_PREFIX = "hsdeep-verify="
MAX_DOMAINS_PER_TENANT = 20


class DomainError(ValueError):
    pass


def normalize_domain(value: str) -> str:
    d = value.strip().lower().rstrip(".")
    if d.startswith("@"):
        d = d[1:]
    try:
        d = d.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DomainError(f"{value!r} is not a valid domain") from exc
    labels = d.split(".")
    if len(d) > 253 or len(labels) < 2 or not all(
        0 < len(label) <= 63 and label.replace("-", "").isalnum() and not label.startswith("-") and not label.endswith("-")
        for label in labels
    ):
        raise DomainError(f"{value!r} is not a valid domain")
    return d


def email_domain(email: str) -> str | None:
    if "@" not in email:
        return None
    try:
        return normalize_domain(email.rsplit("@", 1)[1])
    except DomainError:
        return None


async def lookup_txt(name: str) -> list[str]:
    """TXT strings published at `name` (empty on NXDOMAIN / no answer)."""
    import dns.asyncresolver
    import dns.exception
    import dns.resolver

    try:
        answer = await dns.asyncresolver.resolve(name, "TXT", lifetime=5.0)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


async def add_domain(db: AsyncSession, tenant_id: uuid.UUID, value: str) -> TenantDomain:
    domain = normalize_domain(value)
    existing = (await db.execute(select(TenantDomain).where(TenantDomain.tenant_id == tenant_id))).scalars().all()
    if any(d.domain == domain for d in existing):
        raise DomainError(f"{domain} is already listed")
    if len(existing) >= MAX_DOMAINS_PER_TENANT:
        raise DomainError(f"At most {MAX_DOMAINS_PER_TENANT} domains")
    row = TenantDomain(tenant_id=tenant_id, domain=domain, verification_token=secrets.token_hex(16))
    db.add(row)
    await db.flush()
    return row


async def verify_domain(db: AsyncSession, row: TenantDomain, txt_lookup=None) -> bool:
    """Check the TXT record; on success mark verified (refused if another tenant verified it first)."""
    if row.verified_at is not None:
        return True
    taken = (await db.execute(select(TenantDomain.id).where(
        TenantDomain.domain == row.domain, TenantDomain.verified_at.is_not(None), TenantDomain.tenant_id != row.tenant_id
    ))).first()
    if taken:
        raise DomainError(f"{row.domain} is already verified by another organization")
    records = await (txt_lookup or lookup_txt)(f"{CHALLENGE_PREFIX}.{row.domain}")
    if f"{TOKEN_PREFIX}{row.verification_token}" not in [r.strip() for r in records]:
        return False
    row.verified_at = datetime.now(timezone.utc)
    await db.flush()
    return True


async def tenant_for_domain(db: AsyncSession, domain: str) -> uuid.UUID | None:
    return (await db.execute(select(TenantDomain.tenant_id).where(
        TenantDomain.domain == domain, TenantDomain.verified_at.is_not(None)
    ))).scalar_one_or_none()


async def verified_domains(db: AsyncSession, tenant_id: uuid.UUID) -> set[str]:
    return set((await db.execute(select(TenantDomain.domain).where(
        TenantDomain.tenant_id == tenant_id, TenantDomain.verified_at.is_not(None)
    ))).scalars())
