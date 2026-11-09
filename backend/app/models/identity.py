"""
Enterprise identity (enterprise security programme S6).

TenantDomain: an email domain a tenant claims. It becomes usable for single
sign-on only once verified by a DNS TXT record, and a verified domain belongs
to exactly one tenant (partial unique index), so one customer can never route
another's employees to its identity provider.

TenantSsoConfig: the tenant's OIDC identity provider (issuer, client id,
encrypted client secret), whether SSO is on, whether it is required for
password sign-in, just-in-time user creation, and the SCIM provisioning token
(stored hashed).

UserIdentity: links a user to an external identity: the IdP's subject (``sub``)
for OIDC, or the IdP's externalId for SCIM.

SsoLoginState / SsoHandoff: one-time, short-lived records for the OIDC round
trip (state + nonce + PKCE verifier) and for handing the signed-in session to
the browser without putting tokens in a URL.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class TenantDomain(Base, UUIDPKMixin):
    __tablename__ = "tenant_domains"
    __table_args__ = (
        UniqueConstraint("tenant_id", "domain", name="uq_tenant_domains_tenant_domain"),
        Index("uq_tenant_domains_verified_domain", "domain", unique=True, postgresql_where=text("verified_at IS NOT NULL")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TenantSsoConfig(Base):
    __tablename__ = "tenant_sso_configs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    protocol: Mapped[str] = mapped_column(String(10), nullable=False, default="oidc", server_default="oidc")
    issuer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    #: Password sign-in refused for users on the tenant's verified domains (Owners excepted, as break-glass).
    require_sso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    #: Create a user on first SSO sign-in (with default_role_id) instead of refusing unknown people.
    jit_provisioning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    default_role_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scim_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    scim_token_prefix: Mapped[str | None] = mapped_column(String(12), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UserIdentity(Base, UUIDPKMixin):
    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "subject", name="uq_user_identities_subject"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(10), nullable=False)  # "oidc" | "scim"
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SsoLoginState(Base):
    __tablename__ = "sso_login_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    next_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SsoHandoff(Base):
    __tablename__ = "sso_handoffs"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantSamlConfig(Base):
    """SAML 2.0 identity provider for a tenant (used when TenantSsoConfig.protocol
    is "saml"). The IdP's signing certificate is public, so it is stored as is."""

    __tablename__ = "tenant_saml_configs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    idp_entity_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idp_sso_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: One or more PEM certificates (several during an IdP certificate rollover).
    idp_certificates: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Attribute holding the email; None = NameID, then the usual email attributes.
    email_attribute: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
