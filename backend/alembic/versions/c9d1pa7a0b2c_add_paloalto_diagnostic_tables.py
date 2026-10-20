"""add palo alto diagnostic execution tables

Additive, reversible. Three new tenant-scoped tables; nothing existing moves:

1. `pa_diagnostic_executions` - one row per run (pending|running|passed|failed|blocked).
2. `pa_diagnostic_steps` - per-step state, unique per (execution, step_id).
3. `pa_evidence_refs` - evidence refs with verified flags.

Revision ID: c9d1pa7a0b2c
Revises: f7a4d5e6a8b1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9d1pa7a0b2c"
down_revision = "f7a4d5e6a8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pa_diagnostic_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("risk", sa.String(20), nullable=False, server_default="read_only"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("current_step_id", sa.String(100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_pa_diag_exec_tenant_status", "pa_diagnostic_executions",
                    ["tenant_id", "status"])
    op.create_index("ix_pa_diag_exec_tenant_incident", "pa_diagnostic_executions",
                    ["tenant_id", "incident_id"])
    op.create_index("ix_pa_diag_exec_tenant_device", "pa_diagnostic_executions",
                    ["tenant_id", "device_id"])

    op.create_table(
        "pa_diagnostic_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pa_diagnostic_executions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("step_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("execution_id", "step_id", name="uq_pa_diag_step_exec_step"),
    )
    op.create_index("ix_pa_diag_step_tenant", "pa_diagnostic_steps",
                    ["tenant_id", "status"])

    op.create_table(
        "pa_evidence_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("pa_diagnostic_executions.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("step_id", sa.String(100), nullable=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("source_device_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_ref", sa.Text(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_pa_evidence_tenant_kind", "pa_evidence_refs",
                    ["tenant_id", "kind"])
    op.create_index("ix_pa_evidence_exec", "pa_evidence_refs", ["execution_id"])


def downgrade() -> None:
    op.drop_table("pa_evidence_refs")
    op.drop_table("pa_diagnostic_steps")
    op.drop_table("pa_diagnostic_executions")
