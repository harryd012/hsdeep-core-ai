"""add source health columns and audit table

Revision ID: d4e5f6a7b8c9
Revises: abc1d2e3f4a5
Create Date: 2026-08-21 11:52:00.000000

Adds the health state machine columns to `monitoring_sources` and creates
the `source_health_audits` table for the immutable audit trail of health
state transitions and operational events (timeout, retry, rate-limited,
duplicate-skip).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "abc1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- monitoring_sources: health state machine columns ---------------
    op.add_column(
        "monitoring_sources",
        sa.Column(
            "health_status",
            sa.String(length=20),
            nullable=False,
            server_default="healthy",
        ),
    )
    op.add_column(
        "monitoring_sources",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "monitoring_sources",
        sa.Column(
            "consecutive_successes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "monitoring_sources",
        sa.Column(
            "last_successful_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "monitoring_sources",
        sa.Column(
            "last_failed_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "monitoring_sources",
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_monitoring_sources_next_run_at",
        "monitoring_sources",
        ["next_run_at"],
    )
    op.create_index(
        "ix_monitoring_sources_health_status",
        "monitoring_sources",
        ["health_status"],
    )

    # --- source_health_audits: immutable audit trail table --------------
    op.create_table(
        "source_health_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "monitoring_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("monitoring_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "collector_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collector_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("from_health_status", sa.String(length=20), nullable=True),
        sa.Column("to_health_status", sa.String(length=20), nullable=True),
        sa.Column("from_consecutive_failures", sa.Integer(), nullable=True),
        sa.Column("to_consecutive_failures", sa.Integer(), nullable=True),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_health_audits_source_created",
        "source_health_audits",
        ["monitoring_source_id", "created_at"],
    )
    op.create_index(
        "ix_source_health_audits_tenant_created",
        "source_health_audits",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_source_health_audits_event_type",
        "source_health_audits",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_source_health_audits_tenant_id",
        "source_health_audits",
        ["tenant_id"],
    )
    op.create_index(
        "ix_source_health_audits_monitoring_source_id",
        "source_health_audits",
        ["monitoring_source_id"],
    )
    op.create_index(
        "ix_source_health_audits_collector_run_id",
        "source_health_audits",
        ["collector_run_id"],
    )
    op.create_index(
        "ix_source_health_audits_event_type_2",
        "source_health_audits",
        ["event_type"],
    )


def downgrade() -> None:
    op.drop_table("source_health_audits")
    op.drop_index("ix_monitoring_sources_health_status", table_name="monitoring_sources")
    op.drop_index("ix_monitoring_sources_next_run_at", table_name="monitoring_sources")
    op.drop_column("monitoring_sources", "next_run_at")
    op.drop_column("monitoring_sources", "last_failed_run_at")
    op.drop_column("monitoring_sources", "last_successful_run_at")
    op.drop_column("monitoring_sources", "consecutive_successes")
    op.drop_column("monitoring_sources", "consecutive_failures")
    op.drop_column("monitoring_sources", "health_status")