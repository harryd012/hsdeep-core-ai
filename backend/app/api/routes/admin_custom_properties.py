"""
Platform-admin custom-property definition API (Phase 6.3E §5/§6/§10).

Mounted under the existing admin tenant router, so it reuses `require_admin`
(platform admin only) and the explicit `{tenant_id}` in the path. No parallel
authorization system, and no implicit X-Tenant-Id override.

Read and write are deliberately the SAME audience here: these are platform-owned
definitions about a named tenant, and the existing contract for
/api/admin/tenants is superuser-only. `require_admin` is not weakened.

Every mutation is audited through the existing AuditService with canonical
old/new values.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require_admin
from app.core.db import get_db
from app.repositories.tenant_custom_property_repository import (
    TenantCustomPropertyRepository,
)
from app.services.audit_service import AuditService
from app.services.custom_properties import (
    PropertyDefinitionInvalid,
    validate_allowed_values,
    validate_data_type,
    validate_display_name,
    validate_key,
    validate_validation_rules,
)

logger = logging.getLogger(__name__)

#: Mounted by main.py under the existing /api/admin/tenants prefix via
#: admin_tenants.router, so paths read /api/admin/tenants/{id}/custom-properties.
router = APIRouter()


class CustomPropertyCreate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    data_type: str
    required: bool = False
    default_value: str | None = Field(default=None, max_length=500)
    allowed_values: list[str] = Field(default_factory=list)
    validation_rules: dict = Field(default_factory=dict)
    is_active: bool = True


class CustomPropertyUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    required: bool | None = None
    default_value: str | None = Field(default=None, max_length=500)
    allowed_values: list[str] | None = None
    validation_rules: dict | None = None
    is_active: bool | None = None


class CustomPropertyOut(BaseModel):
    """Canonical response contract: identical fields for GET, POST and PATCH."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    key: str
    display_name: str
    description: str | None
    data_type: str
    required: bool
    default_value: str | None
    allowed_values: list[str]
    validation_rules: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None


def _repo(db: AsyncSession = Depends(get_db)) -> TenantCustomPropertyRepository:
    return TenantCustomPropertyRepository(db)


async def _tenant_exists(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    from sqlalchemy import select

    from app.models.tenant import Tenant

    return (
        await db.execute(select(Tenant.id).where(Tenant.id == tenant_id))
    ).scalar_one_or_none() is not None


def _clean_property_fields(
    data_type: str,
    allowed_values: list | None,
    validation_rules: dict | None,
    *,
    required: bool | None = None,
    default_value: str | None = None,
) -> tuple[str, list[str], dict]:
    """Validate data_type + enum + rules as a unit.

    The data_type check is INSIDE the try: raising it unwrapped made an invalid
    type surface as a 500 instead of a clean 422.
    """
    try:
        clean_type = validate_data_type(data_type)
        values = validate_allowed_values(clean_type, allowed_values)
        rules = validate_validation_rules(clean_type, validation_rules)
    except PropertyDefinitionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # required + empty default is contradictory, and an enum default must be one
    # of the declared values.
    if required and (default_value is None or default_value == ""):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a required property must declare a default_value",
        )
    if clean_type == "enum" and default_value not in (None, "") and default_value not in values:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="default_value must be one of the allowed values",
        )
    return clean_type, values, rules


async def _get_or_404(
    repo: TenantCustomPropertyRepository, property_id: uuid.UUID, tenant_id: uuid.UUID
):
    prop = await repo.get(property_id, tenant_id)
    if prop is None:
        # 404 (not 403) so the API never confirms another tenant's property exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom property not found"
        )
    return prop


@router.get("/{tenant_id}/custom-properties", response_model=list[CustomPropertyOut])
async def list_custom_properties(
    tenant_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    repo: TenantCustomPropertyRepository = Depends(_repo),
):
    """Definitions for ONE explicitly named tenant."""
    if not await _tenant_exists(db, tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return await repo.list(tenant_id)


@router.post(
    "/{tenant_id}/custom-properties",
    response_model=CustomPropertyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_custom_property(
    tenant_id: uuid.UUID,
    body: CustomPropertyCreate,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    repo: TenantCustomPropertyRepository = Depends(_repo),
):
    if not await _tenant_exists(db, tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    try:
        key = validate_key(body.key)
        display_name = validate_display_name(body.display_name)
    except PropertyDefinitionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    clean_type, values, rules = _clean_property_fields(
        body.data_type,
        body.allowed_values,
        body.validation_rules,
        required=body.required,
        default_value=body.default_value,
    )

    if await repo.get_by_key(key, tenant_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A custom property with key {key!r} already exists for this tenant",
        )

    prop = await repo.create(
        tenant_id=tenant_id,
        key=key,
        display_name=display_name,
        description=body.description,
        data_type=clean_type,
        required=body.required,
        default_value=body.default_value,
        allowed_values=values,
        validation_rules=rules,
        is_active=body.is_active,
        created_by=principal.user_id,
    )
    await AuditService(db).record(
        tenant_id=tenant_id,
        action="custom_property.created",
        actor_id=principal.user_id,
        actor_email=principal.email,
        entity_type="tenant_custom_property",
        entity_id=prop.id,
        description=f"Custom property {prop.key!r} created",
        new_values={
            "key": prop.key,
            "display_name": prop.display_name,
            "data_type": prop.data_type,
        },
    )
    await db.commit()
    await db.refresh(prop)
    return prop


@router.patch(
    "/{tenant_id}/custom-properties/{property_id}", response_model=CustomPropertyOut
)
async def update_custom_property(
    tenant_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CustomPropertyUpdate,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    repo: TenantCustomPropertyRepository = Depends(_repo),
):
    prop = await _get_or_404(repo, property_id, tenant_id)
    fields = body.model_dump(exclude_unset=True)

    # `data_type` and `key` are intentionally immutable: changing either could
    # invalidate stored tenant values or break consumers, and this phase must not
    # risk existing data.
    required = fields.get("required", prop.required)
    default_value = fields.get("default_value", prop.default_value)
    _clean_type, values, rules = _clean_property_fields(
        prop.data_type,
        fields.get("allowed_values", prop.allowed_values),
        fields.get("validation_rules", prop.validation_rules),
        required=required,
        default_value=default_value,
    )
    if "allowed_values" in fields or "validation_rules" in fields:
        fields["allowed_values"] = values
        fields["validation_rules"] = rules
    if "display_name" in fields:
        try:
            fields["display_name"] = validate_display_name(fields["display_name"])
        except PropertyDefinitionInvalid as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    before = {
        "display_name": prop.display_name,
        "required": prop.required,
        "default_value": prop.default_value,
        "is_active": prop.is_active,
    }
    prop = await repo.update(prop, **fields)
    await AuditService(db).record(
        tenant_id=tenant_id,
        action="custom_property.updated",
        actor_id=principal.user_id,
        actor_email=principal.email,
        entity_type="tenant_custom_property",
        entity_id=prop.id,
        description=f"Custom property {prop.key!r} updated",
        old_values=before,
        new_values={
            "display_name": prop.display_name,
            "required": prop.required,
            "default_value": prop.default_value,
            "is_active": prop.is_active,
        },
    )
    await db.commit()
    await db.refresh(prop)
    return prop


@router.delete(
    "/{tenant_id}/custom-properties/{property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_custom_property(
    tenant_id: uuid.UUID,
    property_id: uuid.UUID,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    repo: TenantCustomPropertyRepository = Depends(_repo),
):
    """Soft-deactivate via the project's canonical soft delete. The row is
    retained so historical tenant values stay explicable."""
    prop = await _get_or_404(repo, property_id, tenant_id)
    prop = await repo.soft_delete(prop)
    await AuditService(db).record(
        tenant_id=tenant_id,
        action="custom_property.deleted",
        actor_id=principal.user_id,
        actor_email=principal.email,
        entity_type="tenant_custom_property",
        entity_id=prop.id,
        description=f"Custom property {prop.key!r} deleted",
        new_values={"is_active": False, "is_deleted": True},
    )
    await db.commit()
