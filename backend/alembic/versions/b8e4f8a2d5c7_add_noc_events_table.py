"""add noc_events table

Create the noc_events table for the NOC Event + SOC Analyst feature.
NOC events are normalized, severity-classified operational signals derived
from alerts/webhooks/probe checks. SOC analysts are existing Agent rows
(department='soc', role='soc_analyst') — no separate table needed.

Revision ID: b8e4f8a2d5c7
Revises: b4a7c2e8f1d9
Create Date: 2026-09-10 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b8e4f8a2d5c7"
down_revision: Union[str, Sequence[str], None] = "b4a7c2e8f1d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "noc_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("event_signature", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'open'::text"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("assigned_analyst_id", sa.Uuid(), nullable=True),
        sa.Column("source_agent_id", sa.Uuid(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_analyst_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_noc_events_tenant_id"), "noc_events", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_noc_events_site_id"), "noc_events", ["site_id"], unique=False)
    op.create_index(op.f("ix_noc_events_device_id"), "noc_events", ["device_id"], unique=False)
    op.create_index(op.f("ix_noc_events_event_signature"), "noc_events", ["event_signature"], unique=False)
    op.create_index(op.f("ix_noc_events_source"), "noc_events", ["source"], unique=False)
    op.create_index(op.f("ix_noc_events_category"), "noc_events", ["category"], unique=False)
    op.create_index(op.f("ix_noc_events_severity"), "noc_events", ["severity"], unique=False)
    op.create_index(op.f("ix_noc_events_status"), "noc_events", ["status"], unique=False)
    op.create_index(op.f("ix_noc_events_acknowledged_by"), "noc_events", ["acknowledged_by"], unique=False)
    op.create_index(op.f("ix_noc_events_resolved_by"), "noc_events", ["resolved_by"], unique=False)
    op.create_index(op.f("ix_noc_events_assigned_analyst_id"), "noc_events", ["assigned_analyst_id"], unique=False)
    op.create_index(op.f("ix_noc_events_source_agent_id"), "noc_events", ["source_agent_id"], unique=False)
    op.create_index(op.f("ix_noc_events_is_deleted"), "noc_events", ["is_deleted"], unique=False)
    # Partial unique index for flapping dedup
    op.execute(
        "CREATE UNIQUE INDEX uq_noc_events_active_per_signature "
        "ON noc_events (source, event_signature) "
        "WHERE status <> 'resolved' AND event_signature IS NOT NULL AND is_deleted = false"
    )


def downgrade() -> None:
    op.drop_index("uq_noc_events_active_per_signature", table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_is_deleted"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_source_agent_id"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_assigned_analyst_id"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_resolved_by"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_acknowledged_by"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_status"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_severity"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_category"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_source"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_event_signature"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_device_id"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_site_id"), table_name="noc_events")
    op.drop_index(op.f("ix_noc_events_tenant_id"), table_name="noc_events")
    op.drop_table("noc_events")
