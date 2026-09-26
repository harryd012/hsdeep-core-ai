"""add missing plan catalog columns

Revision ID: 20260825_add_plan_columns
Revises: 1c644b59d560
Create Date: 2026-08-25 12:00:00.000000

Additive migration to bring the DB `plans` table in line with the
`app.models.plan.Plan` dataclass fields added during later development.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_add_plan_columns"
down_revision: str | None = "1c644b59d560"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add metered/auxiliary columns with sensible defaults so existing DBs
    # continue to work without requiring data migration.
    op.add_column(
        "plans",
        sa.Column("alert_limit", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "plans",
        sa.Column("collector_run_limit", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "plans",
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "plans",
        sa.Column("trial_eligible", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("plans", "trial_eligible")
    op.drop_column("plans", "display_order")
    op.drop_column("plans", "collector_run_limit")
    op.drop_column("plans", "alert_limit")
