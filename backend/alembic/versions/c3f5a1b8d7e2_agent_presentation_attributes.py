"""implement the published Agent presentation attributes

Additive, reversible migration for the four Agent fields that
``app/schemas/agent.py`` has always published - ``AgentBase`` (inherited by
``AgentCreate`` and ``AgentRead``) plus ``AgentUpdate`` - but that the model
never implemented: ``description``, ``avatar_url``, ``config`` and
``last_active_at``.

Because the model lacked them the published contract was unimplementable:
creating an agent raised ``TypeError: 'description' is an invalid keyword
argument for Agent`` and ``AgentRead`` could not serialize the declared fields.
Implementing the contract in the model is the additive fix; removing the fields
from the schema would have removed published API functionality instead.

Safe for existing rows: the two string columns and the timestamp are nullable,
and ``config`` is NOT NULL with an empty-object server default, so every
existing row is immediately valid. No backfill, no data rewrite, no index or
constraint change, and no other table is touched.

Revision ID: c3f5a1b8d7e2
Revises: a9c4e2f7b1d3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3f5a1b8d7e2"
down_revision = "a9c4e2f7b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents", sa.Column("description", sa.String(length=2000), nullable=True)
    )
    op.add_column(
        "agents", sa.Column("avatar_url", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "agents",
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "agents",
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "last_active_at")
    op.drop_column("agents", "config")
    op.drop_column("agents", "avatar_url")
    op.drop_column("agents", "description")
