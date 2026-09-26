"""add settings, security_settings, notification_settings, api_keys tables

Revision ID: 9a1b2c3d4e5f
Revises: f5b9c3e7a2d1
Create Date: 2026-07-28 05:16:00.000000

Settings sprint: tenant-scoped configuration entities.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "9a1b2c3d4e5f"
down_revision: Union[str, None] = "f5b9c3e7a2d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- settings -----------------------------------------------------------
    op.create_table(
        "settings",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("platform_name", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("branding_logo_url", sa.String(length=1024), nullable=True),
        sa.Column("primary_color", sa.String(length=7), nullable=True),
        sa.Column("locale", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_settings_tenant_id"),
    )
    op.create_index(op.f("ix_settings_tenant_id"), "settings", ["tenant_id"], unique=False)

    # -- security_settings --------------------------------------------------
    op.create_table(
        "security_settings",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("min_password_length", sa.Integer(), nullable=False),
        sa.Column("require_uppercase", sa.Boolean(), nullable=False),
        sa.Column("require_numbers", sa.Boolean(), nullable=False),
        sa.Column("require_special_chars", sa.Boolean(), nullable=False),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False),
        sa.Column("session_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("max_login_attempts", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_security_settings_tenant_id"),
    )
    op.create_index(op.f("ix_security_settings_tenant_id"), "security_settings", ["tenant_id"], unique=False)

    # -- notification_settings ----------------------------------------------
    op.create_table(
        "notification_settings",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("email_recipients", sa.Text(), nullable=True),
        sa.Column("slack_enabled", sa.Boolean(), nullable=False),
        sa.Column("slack_webhook_url", sa.String(length=1024), nullable=True),
        sa.Column("slack_channel", sa.String(length=255), nullable=True),
        sa.Column("pagerduty_enabled", sa.Boolean(), nullable=False),
        sa.Column("pagerduty_integration_key", sa.String(length=255), nullable=True),
        sa.Column("pagerduty_severity", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_notification_settings_tenant_id"),
    )
    op.create_index(op.f("ix_notification_settings_tenant_id"), "notification_settings", ["tenant_id"], unique=False)

    # -- api_keys -----------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("last_used_at", sa.String(length=50), nullable=True),
        sa.Column("expires_at", sa.String(length=50), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_keys_tenant_id"), "api_keys", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_api_keys_key_prefix"), "api_keys", ["key_prefix"], unique=False)
    op.create_index(op.f("ix_api_keys_key_hash"), "api_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_key_hash"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_key_prefix"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_tenant_id"), table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index(op.f("ix_notification_settings_tenant_id"), table_name="notification_settings")
    op.drop_table("notification_settings")

    op.drop_index(op.f("ix_security_settings_tenant_id"), table_name="security_settings")
    op.drop_table("security_settings")

    op.drop_index(op.f("ix_settings_tenant_id"), table_name="settings")
    op.drop_table("settings")