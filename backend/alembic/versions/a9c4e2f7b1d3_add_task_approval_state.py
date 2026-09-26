"""add task approval state (P0 protected-action boundary)

Additive, reversible migration: seven columns on the existing `tasks` table.
No table, index or row is removed or rewritten.

Existing tasks stay valid and unaffected: `approval_required` defaults to
false and `approval_status` to 'not_required', which reproduces today's
behaviour exactly (protected execution proceeds as before). Nothing is
backfilled - no approval history is invented for existing rows.

The approval decision is deliberately NOT a new `tasks.status` value: the
proven lifecycle machine and its validator are untouched, so this migration
only adds the persisted state the Action-Plane gate reads.

Revision ID: a9c4e2f7b1d3
Revises: f7a4d5e6a8b1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9c4e2f7b1d3"
down_revision = "f7a4d5e6a8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "approval_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "approval_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_required",
        ),
    )
    op.add_column(
        "tasks", sa.Column("approval_requested_by", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "tasks",
        sa.Column("approval_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks", sa.Column("approval_decided_by", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "tasks",
        sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("tasks", sa.Column("approval_result", sa.Text(), nullable=True))
    op.create_index("ix_tasks_approval_required", "tasks", ["approval_required"])
    op.create_index("ix_tasks_approval_status", "tasks", ["approval_status"])


def downgrade() -> None:
    op.drop_index("ix_tasks_approval_status", table_name="tasks")
    op.drop_index("ix_tasks_approval_required", table_name="tasks")
    op.drop_column("tasks", "approval_result")
    op.drop_column("tasks", "approval_decided_at")
    op.drop_column("tasks", "approval_decided_by")
    op.drop_column("tasks", "approval_requested_at")
    op.drop_column("tasks", "approval_requested_by")
    op.drop_column("tasks", "approval_status")
    op.drop_column("tasks", "approval_required")
