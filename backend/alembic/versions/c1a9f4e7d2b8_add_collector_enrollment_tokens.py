"""add collector_enrollment_tokens

One-time, tenant-scoped collector enrollment tokens. Additive and reversible:
creates one new table and touches nothing existing (no MonitoringSource change).

Revision ID: c1a9f4e7d2b8
Revises: b8e42d9c7a15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c1a9f4e7d2b8"
down_revision = "b8e42d9c7a15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collector_enrollment_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_collector_enrollment_tokens_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_collector_enrollment_tokens_created_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "token_hash", name="uq_collector_enrollment_tokens_token_hash"
        ),
    )
    op.create_index(
        "ix_collector_enrollment_tokens_tenant_id",
        "collector_enrollment_tokens",
        ["tenant_id"],
    )
    op.create_index(
        "ix_collector_enrollment_tokens_token_hash",
        "collector_enrollment_tokens",
        ["token_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collector_enrollment_tokens_token_hash",
        table_name="collector_enrollment_tokens",
    )
    op.drop_index(
        "ix_collector_enrollment_tokens_tenant_id",
        table_name="collector_enrollment_tokens",
    )
    op.drop_table("collector_enrollment_tokens")
