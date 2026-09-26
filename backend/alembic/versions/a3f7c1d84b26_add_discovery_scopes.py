"""add discovery_scopes

Phase 6.3: tenant-scoped Universal Discovery configuration. A DiscoveryScope is
configuration only - it never owns or duplicates canonical inventory. Device
identity/deduplication stays with the existing devices table and its
UNIQUE (tenant_id, serial_number) constraint, which this migration does not
touch.

Revision ID: a3f7c1d84b26
Revises: e1a7b3c9f204
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a3f7c1d84b26"
down_revision = "e1a7b3c9f204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_scopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("credential_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "allowed_protocols",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("max_depth", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_hosts", sa.Integer(), nullable=False, server_default="256"),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="32"),
        sa.Column("timeout", sa.Float(), nullable=False, server_default="1"),
        sa.Column(
            "excluded_targets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("schedule", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_discovery_scopes_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["credential_profile_id"],
            ["monitoring_sources.id"],
            name="fk_discovery_scopes_credential_profile_id_monitoring_sources",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_discovery_scopes_tenant_id_name"),
    )
    op.create_index("ix_discovery_scopes_tenant_id", "discovery_scopes", ["tenant_id"])
    op.create_index("ix_discovery_scopes_scope_type", "discovery_scopes", ["scope_type"])
    op.create_index("ix_discovery_scopes_target", "discovery_scopes", ["target"])
    op.create_index("ix_discovery_scopes_enabled", "discovery_scopes", ["enabled"])
    op.create_index(
        "ix_discovery_scopes_credential_profile_id", "discovery_scopes", ["credential_profile_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_scopes_credential_profile_id", table_name="discovery_scopes")
    op.drop_index("ix_discovery_scopes_enabled", table_name="discovery_scopes")
    op.drop_index("ix_discovery_scopes_target", table_name="discovery_scopes")
    op.drop_index("ix_discovery_scopes_scope_type", table_name="discovery_scopes")
    op.drop_index("ix_discovery_scopes_tenant_id", table_name="discovery_scopes")
    op.drop_table("discovery_scopes")
