"""
TenantCustomProperty (Phase 6.3E).

A tenant-owned DEFINITION of an extra tenant/customer property. It stores
metadata only - never tenant values, never credentials - so a tenant can label
and shape its own customer attributes without any schema change.

Security posture: a definition is UNTRUSTED tenant configuration.
  * `validation` is a declarative whitelist (numeric/length/regex bounds), NOT an
    expression: there is no evaluation hook, so a definition can never cause code
    execution or SQL injection.
  * No secret-bearing type exists (no "password"/"token" type).
  * `key` is a fixed identifier and is never interpolated into SQL.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


#: Controlled data types. Deliberately small: only what the product needs, and
#: deliberately WITHOUT any secret-bearing type.
DATA_TYPES: tuple[str, ...] = (
    "string",
    "integer",
    "boolean",
    "date",
    "datetime",
    "enum",
)

#: Declarative validation keys a definition may carry. Anything else is rejected
#: by the service, so no expression can ever be smuggled in.
ALLOWED_VALIDATION_KEYS: frozenset[str] = frozenset(
    {"min_length", "max_length", "min", "max", "regex", "pattern"}
)

MAX_KEY_LENGTH = 64
MAX_DISPLAY_NAME_LENGTH = 255
MAX_ALLOWED_VALUES = 100


class TenantCustomProperty(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tenant_custom_properties"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "key", name="uq_tenant_custom_properties_tenant_id_key"
        ),
        # Defence in depth: even a direct SQL insert cannot introduce an unknown
        # data type.
        CheckConstraint(
            "data_type IN (" + ", ".join(f"'{t}'" for t in DATA_TYPES) + ")",
            name="ck_tenant_custom_properties_data_type",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Stable machine identifier. Never used as a SQL identifier.
    key: Mapped[str] = mapped_column(String(MAX_KEY_LENGTH), nullable=False, index=True)
    #: Human label the tenant sees (may differ freely from `key`).
    display_name: Mapped[str] = mapped_column(
        String(MAX_DISPLAY_NAME_LENGTH), nullable=False
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    data_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Stored as text and interpreted per `data_type` by the caller.
    default_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Only meaningful for data_type == "enum".
    allowed_values: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: Declarative, whitelisted bounds only - never an expression.
    validation_rules: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<TenantCustomProperty {self.key!r} type={self.data_type} "
            f"tenant={self.tenant_id} active={self.is_active}>"
        )
