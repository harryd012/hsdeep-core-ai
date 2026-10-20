"""add tasks table

Recreates the ``tasks`` table (Virtual Company Multi-Agent Organization) that
is referenced by ``a9c4e2f7b1d3`` (task approval state) but was never created
by any migration in the chain (it existed only in live databases).

Canonical schema taken from ``app/models/task.py`` and the live dev database.
The seven approval columns are added later by ``a9c4e2f7b1d3`` and are
intentionally absent here so that later migration does not collide.

Revision ID: d2f6a9c4e8b1
Revises: f7a4d5e6a8b1
Create Date: 2026-10-11 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d2f6a9c4e8b1"
down_revision: Union[str, Sequence[str], None] = "f7a4d5e6a8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("task_number", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("priority", sa.String(length=10), server_default="P3", nullable=False),
        sa.Column("complexity", sa.String(length=20), server_default="medium", nullable=False),
        sa.Column("risk_level", sa.String(length=20), server_default="low", nullable=False),
        sa.Column("required_skills", postgresql.ARRAY(sa.String(length=100)), server_default="{}", nullable=False),
        sa.Column("required_capabilities", postgresql.ARRAY(sa.String(length=100)), server_default="{}", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="created", nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default="3", nullable=False),
        sa.Column("assigned_agent_id", sa.UUID(), nullable=True),
        sa.Column("assigned_by_agent_id", sa.UUID(), nullable=True),
        sa.Column("manager_agent_id", sa.UUID(), nullable=True),
        sa.Column("owner_command", sa.Text(), nullable=True),
        sa.Column("command_source", sa.String(length=20), server_default="message", nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("execution_plan", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("execution_result", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("verification_result", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_agent_id", sa.UUID(), nullable=True),
        sa.Column("recommended_sop_id", sa.UUID(), nullable=True),
        sa.Column("sop_recommendation_score", sa.Integer(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("audit_log", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("incident_id", sa.UUID(), nullable=True),
        sa.Column("ticket_id", sa.UUID(), nullable=True),
        sa.Column("customer_id", sa.String(length=100), nullable=True),
        sa.Column("project_id", sa.String(length=100), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_by_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["manager_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["verified_by_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recommended_sop_id"], ["sops.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_tenant_id"), "tasks", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_tasks_task_number"), "tasks", ["task_number"], unique=False)
    op.create_index(op.f("ix_tasks_task_type"), "tasks", ["task_type"], unique=False)
    op.create_index(op.f("ix_tasks_priority"), "tasks", ["priority"], unique=False)
    op.create_index(op.f("ix_tasks_assigned_agent_id"), "tasks", ["assigned_agent_id"], unique=False)
    op.create_index(op.f("ix_tasks_assigned_by_agent_id"), "tasks", ["assigned_by_agent_id"], unique=False)
    op.create_index(op.f("ix_tasks_manager_agent_id"), "tasks", ["manager_agent_id"], unique=False)
    op.create_index(op.f("ix_tasks_correlation_id"), "tasks", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_tasks_incident_id"), "tasks", ["incident_id"], unique=False)
    op.create_index(op.f("ix_tasks_ticket_id"), "tasks", ["ticket_id"], unique=False)
    op.create_index(op.f("ix_tasks_customer_id"), "tasks", ["customer_id"], unique=False)
    op.create_index(op.f("ix_tasks_project_id"), "tasks", ["project_id"], unique=False)
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_index(op.f("ix_tasks_is_deleted"), "tasks", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_is_deleted"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_project_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_customer_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_ticket_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_incident_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_correlation_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_manager_agent_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_assigned_by_agent_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_assigned_agent_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_priority"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_task_type"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_task_number"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_tenant_id"), table_name="tasks")
    op.drop_table("tasks")
