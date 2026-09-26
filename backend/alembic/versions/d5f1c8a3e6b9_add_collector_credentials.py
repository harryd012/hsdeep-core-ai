"""add collector_credentials

Narrow, revocable credential for a REGISTERED collector's outbound requests.
Additive and reversible: creates one new table and modifies nothing existing
(in particular MonitoringSource is untouched).

Revision ID: d5f1c8a3e6b9
Revises: c1a9f4e7d2b8
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d5f1c8a3e6b9"
down_revision = "c1a9f4e7d2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collector_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_hash", sa.String(length=128), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collector_version", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_collector_credentials_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["monitoring_sources.id"],
            name="fk_collector_credentials_source_id_monitoring_sources",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("source_id", name="uq_collector_credentials_source_id"),
        sa.UniqueConstraint(
            "credential_hash", name="uq_collector_credentials_credential_hash"
        ),
    )
    op.create_index(
        "ix_collector_credentials_tenant_id", "collector_credentials", ["tenant_id"]
    )
    op.create_index(
        "ix_collector_credentials_source_id", "collector_credentials", ["source_id"]
    )
    op.create_index(
        "ix_collector_credentials_credential_hash",
        "collector_credentials",
        ["credential_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collector_credentials_credential_hash", table_name="collector_credentials"
    )
    op.drop_index(
        "ix_collector_credentials_source_id", table_name="collector_credentials"
    )
    op.drop_index("ix_collector_credentials_tenant_id", table_name="collector_credentials")
    op.drop_table("collector_credentials")
