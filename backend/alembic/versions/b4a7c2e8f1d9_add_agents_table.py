"""add agents table

Recreates the ``agents`` table (Agent Registry) that is referenced by
``noc_events`` (b8e4f8a2d5c7), ``integrations`` (e5d4c3b2a1f0), ``tasks``
and the agent-presentation migration (c3f5a1b8d7e2), but was never created by
any migration in the chain (it existed only in live databases).

Canonical schema taken from ``app/models/agent.py`` and the live dev database.
The four presentation columns (``description``/``avatar_url``/``config``/
``last_active_at``) are added later by ``c3f5a1b8d7e2`` and are intentionally
absent here so that later migration does not collide.

Revision ID: b4a7c2e8f1d9
Revises: a9f2c7d4e1b6
Create Date: 2026-10-11 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b4a7c2e8f1d9"
down_revision: Union[str, Sequence[str], None] = "a9f2c7d4e1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("manager_id", sa.UUID(), nullable=True),
        sa.Column("permissions", postgresql.ARRAY(sa.String(length=50)), server_default="{}", nullable=False),
        sa.Column("escalation_authority", sa.String(length=20), server_default="none", nullable=False),
        sa.Column("can_approve", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("approval_limit", sa.String(length=20), server_default="low", nullable=False),
        sa.Column("max_workload", sa.Integer(), server_default="5", nullable=False),
        sa.Column("current_workload", sa.Integer(), server_default="0", nullable=False),
        sa.Column("availability", sa.String(length=20), server_default="available", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("tasks_completed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tasks_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_resolution_minutes", sa.Integer(), nullable=True),
        sa.Column("performance_score", sa.Integer(), server_default="100", nullable=False),
        sa.Column("assigned_customers", postgresql.ARRAY(sa.String(length=100)), server_default="{}", nullable=False),
        sa.Column("assigned_projects", postgresql.ARRAY(sa.String(length=100)), server_default="{}", nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("skills", postgresql.ARRAY(sa.String(length=100)), server_default="{}", nullable=False),
        sa.Column("capabilities", postgresql.ARRAY(sa.String(length=100)), server_default="{}", nullable=False),
        sa.Column("supported_task_types", postgresql.ARRAY(sa.String(length=50)), server_default="{}", nullable=False),
        sa.Column("responsibilities", postgresql.ARRAY(sa.String(length=255)), server_default="{}", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["manager_id"], ["agents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agents_tenant_id"), "agents", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_agents_agent_id"), "agents", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agents_role"), "agents", ["role"], unique=False)
    op.create_index(op.f("ix_agents_department"), "agents", ["department"], unique=False)
    op.create_index(op.f("ix_agents_level"), "agents", ["level"], unique=False)
    op.create_index(op.f("ix_agents_manager_id"), "agents", ["manager_id"], unique=False)
    op.create_index(op.f("ix_agents_is_active"), "agents", ["is_active"], unique=False)
    op.create_index(op.f("ix_agents_is_deleted"), "agents", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_agents_is_deleted"), table_name="agents")
    op.drop_index(op.f("ix_agents_is_active"), table_name="agents")
    op.drop_index(op.f("ix_agents_manager_id"), table_name="agents")
    op.drop_index(op.f("ix_agents_level"), table_name="agents")
    op.drop_index(op.f("ix_agents_department"), table_name="agents")
    op.drop_index(op.f("ix_agents_role"), table_name="agents")
    op.drop_index(op.f("ix_agents_agent_id"), table_name="agents")
    op.drop_index(op.f("ix_agents_tenant_id"), table_name="agents")
    op.drop_table("agents")
