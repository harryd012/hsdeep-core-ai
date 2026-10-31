"""
Settings endpoints (general, security, notifications, api-keys).

NOTE(auth): tenant_id is now derived from the authenticated principal
via Depends(get_tenant_id), matching the pattern in priorities.py and
runbooks.py. create_api_key's created_by_user_id is still accepted as an
optional query param since there is no user session to derive it from yet
(see app/models/api_key.py nullable column).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, hash_api_key as core_hash_api_key, require_admin
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.models.api_key import ApiKey
from app.models.incident_notification import IncidentNotification
from app.models.notification_setting import NotificationSetting
from app.models.security_setting import SecuritySetting
from app.models.setting import Setting
from app.services.audit_service import AuditService
from app.services.notifications import NotificationDispatcher, is_allowed_slack_webhook

router = APIRouter(prefix="/settings", tags=["settings"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_audit_service(db: AsyncSession = Depends(get_db)) -> AuditService:
    return AuditService(db)


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None = None
    tenant_id: uuid.UUID
    platform_name: str
    timezone: str
    branding_logo_url: str | None
    primary_color: str | None
    locale: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_default: bool = False


class SettingUpdate(BaseModel):
    platform_name: str = "HSDEEP Core AI"
    timezone: str = "UTC"
    branding_logo_url: str | None = None
    primary_color: str | None = None
    locale: str = "en"


class SecuritySettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # id/created_at/updated_at are optional so the "no row yet" default
    # payload (flagged is_default=True) can be returned without them.
    id: uuid.UUID | None = None
    tenant_id: uuid.UUID
    min_password_length: int
    require_uppercase: bool
    require_numbers: bool
    require_special_chars: bool
    mfa_enabled: bool
    session_timeout_minutes: int
    max_login_attempts: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_default: bool = False


class SecuritySettingUpdate(BaseModel):
    min_password_length: int = 8
    require_uppercase: bool = True
    require_numbers: bool = True
    require_special_chars: bool = True
    mfa_enabled: bool = False
    session_timeout_minutes: int = 60
    max_login_attempts: int = 5

    @field_validator("min_password_length")
    @classmethod
    def _min_password_length(cls, v: int) -> int:
        if v < 1:
            raise ValueError("min_password_length must be a positive integer")
        return v

    @field_validator("session_timeout_minutes", "max_login_attempts")
    @classmethod
    def _positive_int(cls, v: int, info) -> int:
        if v < 1:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return v


_MASK = "••••"


def _is_masked(value: str | None) -> bool:
    return bool(value) and value.startswith(_MASK)


def _mask(value: str | None) -> str | None:
    """Secrets are never returned in full: the last 4 characters identify which one is stored."""
    return f"{_MASK}{value[-4:]}" if value else None


class NotificationSettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # id/created_at/updated_at are optional so the "no row yet" default
    # payload (flagged is_default=True) can be returned without them.
    id: uuid.UUID | None = None
    tenant_id: uuid.UUID
    email_enabled: bool
    email_recipients: str | None
    slack_enabled: bool
    slack_webhook_url: str | None
    slack_channel: str | None
    pagerduty_enabled: bool
    pagerduty_integration_key: str | None
    pagerduty_severity: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_default: bool = False


class NotificationSettingUpdate(BaseModel):
    email_enabled: bool = False
    email_recipients: str | None = None
    slack_enabled: bool = False
    slack_webhook_url: str | None = None
    slack_channel: str | None = None
    pagerduty_enabled: bool = False
    pagerduty_integration_key: str | None = None
    pagerduty_severity: str | None = None

    @field_validator("email_recipients")
    @classmethod
    def _email_recipients(cls, v: str | None) -> str | None:
        if v:
            parts = [p.strip() for p in v.split(",") if p.strip()]
            invalid = [p for p in parts if "@" not in p]
            if invalid:
                raise ValueError(f"Invalid email address: {invalid[0]}")
            return ", ".join(parts)
        return v

    @field_validator("slack_webhook_url")
    @classmethod
    def _slack_webhook(cls, v: str | None, info) -> str | None:
        # Only Slack's webhook host: the platform POSTs to this URL, so any
        # other address would let a tenant make it call internal services.
        # A masked value (unchanged secret) is passed through and kept.
        if v and not _is_masked(v) and not is_allowed_slack_webhook(v):
            raise ValueError("slack_webhook_url must be a Slack incoming webhook (https://hooks.slack.com/...)")
        return v

    @field_validator("pagerduty_integration_key")
    @classmethod
    def _pagerduty_key(cls, v: str | None, info) -> str | None:
        # PagerDuty integration key required when PagerDuty is enabled. The
        # raw payload value is checked via model root; here we only enforce a
        # non-trivial value if provided.
        if v is not None and v.strip() == "":
            return None
        return v


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str] = []
    created_at: datetime
    last_used_at: str | None
    expires_at: str | None
    is_active: bool


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str] = []
    expires_at: str | None = None


class ApiKeyCreateResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str] = []
    api_key: str
    created_at: datetime
    expires_at: str | None


# ---- General settings -----------------------------------------------------

# Defaults mirror Setting column defaults (see app/models/setting.py) so the
# "no row yet" response is indistinguishable from a freshly-created row, apart
# from the is_default flag.
DEFAULT_GENERAL_SETTINGS = {
    "platform_name": "HSDEEP Core AI",
    "timezone": "UTC",
    "branding_logo_url": None,
    "primary_color": None,
    "locale": "en",
}


@router.get("/general", response_model=SettingOut)
async def get_general_settings(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(Setting).where(Setting.tenant_id == tenant_id))).scalar_one_or_none()
    if not row:
        # Return defaults instead of 404 — don't error just because
        # the row hasn't been created yet.
        return SettingOut(tenant_id=tenant_id, **DEFAULT_GENERAL_SETTINGS, is_default=True)
    return row


@router.put("/general", dependencies=[_active_sub_guard], response_model=SettingOut)
async def update_general_settings(
    payload: SettingUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(Setting).where(Setting.tenant_id == tenant_id))).scalar_one_or_none()
    if not row:
        # Upsert: create the row on first save rather than 404'ing.
        row = Setting(tenant_id=tenant_id)
        db.add(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return row


# ---- Security settings ----------------------------------------------------

# Defaults mirror SecuritySetting column defaults (see app/models/security_setting.py)
# so the "no row yet" response is indistinguishable from a freshly-created row,
# apart from the is_default flag.
DEFAULT_SECURITY_SETTINGS = {
    "min_password_length": 8,
    "require_uppercase": True,
    "require_numbers": True,
    "require_special_chars": True,
    "mfa_enabled": False,
    "session_timeout_minutes": 60,
    "max_login_attempts": 5,
}


@router.get("/security", response_model=SecuritySettingOut)
async def get_security_settings(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(SecuritySetting).where(SecuritySetting.tenant_id == tenant_id))).scalar_one_or_none()
    if not row:
        # Return defaults instead of 404 — don't error just because
        # the row hasn't been created yet
        return SecuritySettingOut(tenant_id=tenant_id, **DEFAULT_SECURITY_SETTINGS, is_default=True)
    return row


@router.put("/security", dependencies=[_active_sub_guard], response_model=SecuritySettingOut)
async def update_security_settings(
    payload: SecuritySettingUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(SecuritySetting).where(SecuritySetting.tenant_id == tenant_id))).scalar_one_or_none()
    if not row:
        # Upsert: create the row on first save rather than 404'ing.
        row = SecuritySetting(tenant_id=tenant_id)
        db.add(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return row


# ---- Notification settings ------------------------------------------------

# Defaults mirror NotificationSetting column defaults (see app/models/notification_setting.py).
DEFAULT_NOTIFICATION_SETTINGS = {
    "email_enabled": False,
    "email_recipients": None,
    "slack_enabled": False,
    "slack_webhook_url": None,
    "slack_channel": None,
    "pagerduty_enabled": False,
    "pagerduty_integration_key": None,
    "pagerduty_severity": None,
}


@router.get("/notifications", response_model=NotificationSettingOut)
async def get_notification_settings(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(NotificationSetting).where(NotificationSetting.tenant_id == tenant_id))).scalar_one_or_none()
    if not row:
        # Return defaults instead of 404 — don't error just because
        # the row hasn't been created yet
        return NotificationSettingOut(tenant_id=tenant_id, **DEFAULT_NOTIFICATION_SETTINGS, is_default=True)
    return _notification_out(row)


def _notification_out(row: NotificationSetting) -> NotificationSettingOut:
    out = NotificationSettingOut.model_validate(row)
    out.slack_webhook_url = _mask(row.slack_webhook_url)
    out.pagerduty_integration_key = _mask(row.pagerduty_integration_key)
    return out


@router.put("/notifications", dependencies=[_active_sub_guard], response_model=NotificationSettingOut)
async def update_notification_settings(
    payload: NotificationSettingUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Validate: PagerDuty integration key required when PagerDuty enabled.
    if payload.pagerduty_enabled and not payload.pagerduty_integration_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="PagerDuty integration key is required when PagerDuty is enabled",
        )
    row = (await db.execute(select(NotificationSetting).where(NotificationSetting.tenant_id == tenant_id))).scalar_one_or_none()
    if not row:
        # Upsert: create the row on first save rather than 404'ing.
        row = NotificationSetting(tenant_id=tenant_id)
        db.add(row)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field in ("slack_webhook_url", "pagerduty_integration_key") and _is_masked(value):
            continue  # unchanged secret: keep the stored value
        setattr(row, field, value)
    if row.pagerduty_enabled and not row.pagerduty_integration_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="PagerDuty integration key is required when PagerDuty is enabled",
        )
    await db.commit()
    await db.refresh(row)
    return _notification_out(row)


class NotificationDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: uuid.UUID
    channel: str
    event: str
    status: str
    attempts: int
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime


@router.get("/notifications/deliveries", response_model=list[NotificationDeliveryOut])
async def list_notification_deliveries(
    limit: int = 50,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Most recent incident notification deliveries, including failures and their errors."""
    rows = (
        await db.execute(
            select(IncidentNotification)
            .where(IncidentNotification.tenant_id == tenant_id)
            .order_by(IncidentNotification.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
    ).scalars().all()
    return rows


@router.post("/notifications/test", dependencies=[_active_sub_guard])
async def send_test_notification(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, dict[str, str]]:
    """Send a labelled test message to every enabled channel now."""
    row = (await db.execute(select(NotificationSetting).where(NotificationSetting.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None or not (row.email_enabled or row.slack_enabled or row.pagerduty_enabled):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enable at least one channel first.")
    results = await NotificationDispatcher(db).send_test(row)
    await db.commit()
    return {"results": results}


# ---- API keys -------------------------------------------------------------


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc()))).scalars().all()
    return rows


@router.post("/api-keys", dependencies=[_active_sub_guard], response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    raw = secrets.token_urlsafe(24)
    prefix = raw[:12]
    # The raw key is returned EXACTLY ONCE in this response and never stored;
    # only its SHA-256 hash is persisted. JWT principals carry a real user id;
    # API-key principals use the sentinel UUID(int=0), in which case we record
    # no creator rather than a bogus FK.
    creator_id = (
        principal.user_id
        if principal.user_id != uuid.UUID(int=0)
        else None
    )
    row = ApiKey(
        tenant_id=tenant_id,
        name=payload.name,
        key_prefix=prefix,
        key_hash=core_hash_api_key(raw),
        scopes=payload.scopes,
        created_by_user_id=creator_id,
        expires_at=payload.expires_at,
        is_active=True,
    )
    db.add(row)
    await get_audit_service(db).record_entity(
        tenant_id=tenant_id,
        action="api_key.created",
        entity_type="api_key",
        entity_id=row.id,
        actor_id=creator_id,
        actor_email=principal.email,
        description=f"API key {payload.name!r} created",
        new_values={"name": payload.name, "key_prefix": prefix, "scopes": payload.scopes},
    )
    await db.commit()
    await db.refresh(row)
    return ApiKeyCreateResponse(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        scopes=row.scopes,
        api_key=raw,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


@router.delete("/api-keys/{key_id}", dependencies=[_active_sub_guard], status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: uuid.UUID,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key (soft-revoke: is_active=False). The row is kept for
    auditability; a revoked key can never authenticate again because
    _resolve_api_key_principal filters on is_active."""
    row = (await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    row.is_active = False
    actor_id = principal.user_id if principal.user_id != uuid.UUID(int=0) else None
    await get_audit_service(db).record_entity(
        tenant_id=tenant_id,
        action="api_key.revoked",
        entity_type="api_key",
        entity_id=row.id,
        actor_id=actor_id,
        actor_email=principal.email,
        description=f"API key {row.name!r} revoked",
        old_values={"is_active": True},
        new_values={"is_active": False},
    )
    await db.commit()


def hash_api_key(raw: str) -> str:
    import hashlib
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()