"""add tenant_custom_properties

Phase 6.3E: tenant-owned custom-property DEFINITIONS. Metadata only - no tenant
values, no credentials. Reversible and non-destructive: creates one new table
and touches nothing existing, so it is safe for current tenants.

Revision ID: b8e42d9c7a15
Revises: a3f7c1d84b26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b8e42d9c7a15"
down_revision = "a3f7c1d84b26"
branch_labels = None
depends_on = None

DATA_TYPES = ("string", "integer", "boolean", "date", "datetime", "enum")


def upgrade() -> None:
    op.create_table(
        "tenant_custom_properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("default_value", sa.String(length=500), nullable=True),
        sa.Column(
            "allowed_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "validation_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_custom_properties_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_tenant_custom_properties_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "tenant_id", "key", name="uq_tenant_custom_properties_tenant_id_key"
        ),
        sa.CheckConstraint(
            "data_type IN (" + ", ".join(f"'{t}'" for t in DATA_TYPES) + ")",
            name="ck_tenant_custom_properties_data_type",
        ),
    )
    op.create_index(
        "ix_tenant_custom_properties_tenant_id", "tenant_custom_properties", ["tenant_id"]
    )
    op.create_index(
        "ix_tenant_custom_properties_key", "tenant_custom_properties", ["key"]
    )
    op.create_index(
        "ix_tenant_custom_properties_data_type", "tenant_custom_properties", ["data_type"]
    )
    op.create_index(
        "ix_tenant_custom_properties_is_active", "tenant_custom_properties", ["is_active"]
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_custom_properties_is_active", table_name="tenant_custom_properties")
    op.drop_index("ix_tenant_custom_properties_data_type", table_name="tenant_custom_properties")
    op.drop_index("ix_tenant_custom_properties_key", table_name="tenant_custom_properties")
    op.drop_index("ix_tenant_custom_properties_tenant_id", table_name="tenant_custom_properties")
    op.drop_table("tenant_custom_properties")
