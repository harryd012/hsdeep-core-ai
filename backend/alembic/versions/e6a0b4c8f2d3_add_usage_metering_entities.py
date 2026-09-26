"""add usage metering entities

Revision ID: e6a0b4c8f2d3
Revises: f5b9c3e7a2d1
Create Date: 2026-07-26 12:25:00.000000

Production Hardening sprint — multi-tenant usage metering & billing hooks.

Additive and backward-compatible:

  * usage_counters: idempotent hourly bucketed counters keyed by
    (tenant_id, event_type, bucket_start) with ON CONFLICT DO UPDATE
    support for exactly-once semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6a0b4c8f2d3"
down_revision: str | None = "f5b9c3e7a2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("tenant_id", sa.UUID(), nullable=False, index=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "event_type", "bucket_start",
            name="uq_usage_tenant_event_bucket",
        ),
    )


def downgrade() -> None:
    op.drop_table("usage_counters")