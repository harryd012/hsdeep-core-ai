"""
Pydantic v2 schemas for the Auth + RBAC API (P1). Kept separate from the ORM
models per Clean Architecture, same convention as schemas/alert.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from email_validator import EmailSyntaxError, validate_email
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


# ---- Authentication ------------------------------------------------------

def _check_email(value: str) -> str:
    """Full email syntax validation, but allow RFC 6761/6762 special-use
    domains (e.g. ``admin@hsdeep.local``).

    Why: the platform intentionally seeds internal/dev accounts under the
    reserved ``.local`` domain. ``EmailStr`` with email-validator >= 2.2
    rejects those as "special-use or reserved", which broke login for every
    seeded user after the venv rebuild picked up the pinned 2.2.0. All other
    syntax errors are still rejected exactly as before.
    """
    try:
        validate_email(value, check_deliverability=False)
    except EmailSyntaxError as exc:
        if "special-use or reserved name" not in str(exc):
            raise ValueError(str(exc)) from exc
        # Intentional internal address - accept.
    return value


InternalEmail = Annotated[str, AfterValidator(_check_email)]


class LoginRequest(BaseModel):
    tenant_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Tenant the user belongs to. Optional — a real end user never "
            "knows their tenant UUID, so omitting it resolves the tenant by "
            "email server-side (see AuthService.login). Still accepted "
            "explicitly for the superuser cross-tenant X-Tenant-Id flow and "
            "for any caller that already knows it (e.g. a saved session)."
        ),
    )
    email: InternalEmail
    password: str = Field(..., min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds")


class MeResponse(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str | None = None
    is_superuser: bool
    roles: list[str]
    permissions: list[str]
    # -- P0 tenant-context additions (fully additive; defaults keep the
    # -- response backward compatible for existing callers) ----------------
    # Display name of the ACTIVE tenant (authoritative DB row, not the token).
    tenant_name: str | None = None
    # Tenants the authenticated principal is authorized to operate on, derived
    # from the backend authorization model (users.tenant_id; superusers see all
    # live tenants per the interim MSP cross-tenant model). Never invented
    # client-side; never includes tenants the principal cannot access.
    accessible_tenants: list[TenantContext] = Field(default_factory=list)
    # The user's OWN tenant (from the token). Differs from tenant_id only
    # while a superuser acts in another tenant under a support grant.
    home_tenant_id: uuid.UUID | None = None


class TenantContext(BaseModel):
    """One authorized tenant membership for the authenticated principal."""

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    # The principal's role within THIS tenant (own tenant: the user's real
    # role name; superuser cross-tenant: "Superuser" per the interim model).
    role: str | None = None


# ---- User administration -------------------------------------------------

class UserCreate(BaseModel):
    email: InternalEmail
    # Omit with send_invite=true: the user sets their own password from the
    # emailed invitation link.
    password: str | None = Field(default=None, min_length=8, description="Argon2id-hashed before storage")
    full_name: str | None = None
    is_superuser: bool = False
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    send_invite: bool = False

    @model_validator(mode="after")
    def _password_or_invite(self) -> "UserCreate":
        if self.password is None and not self.send_invite:
            raise ValueError("Provide a password, or set send_invite to email an invitation.")
        return self


class ForgotPasswordRequest(BaseModel):
    email: InternalEmail


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)
    password: str = Field(..., min_length=8)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)


class AcceptInviteRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)
    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None


class SetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8)


class AssignRolesRequest(BaseModel):
    role_ids: list[uuid.UUID]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool
    is_superuser: bool
    last_login_at: datetime | None
    created_at: datetime


# ---- Roles & permissions -------------------------------------------------

class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    description: str | None


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    description: str | None = None
    permission_codes: list[str] | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    permission_codes: list[str]
