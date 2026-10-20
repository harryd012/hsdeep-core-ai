"""add discovery_probe_jobs / discovery_probe_hosts

Durable, tenant-scoped OBSERVATION ledger for Universal Discovery scope probes.
A probe sweep (which is observation-only today) now persists what it saw so a
separate, controlled materialize step can later turn eligible reachable-host
evidence into canonical devices through the EXISTING DiscoveryService.discover()
/ DeviceRepository.upsert() path. Nothing here duplicates canonical inventory or
the devices identity constraints.

Revision ID: b3e5c8f2a7d4
Revises: a4c7e2f91b36
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3e5c8f2a7d4"
down_revision = "a4c7e2f91b36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_probe_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("hosts_probed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hosts_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reachable", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("materialization_state", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_discovery_probe_jobs_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["discovery_scopes.id"],
            name="fk_discovery_probe_jobs_scope_id_discovery_scopes",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_discovery_probe_jobs_id_tenant_id"),
    )
    op.create_index("ix_discovery_probe_jobs_tenant_id", "discovery_probe_jobs", ["tenant_id"])
    op.create_index("ix_discovery_probe_jobs_scope_id", "discovery_probe_jobs", ["scope_id"])
    op.create_index("ix_discovery_probe_jobs_status", "discovery_probe_jobs", ["status"])

    op.create_table(
        "discovery_probe_hosts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("address", sa.String(length=45), nullable=False),
        sa.Column("reachable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("packet_loss_pct", sa.Float(), nullable=True),
        sa.Column(
            "protocols",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("materialization_outcome", sa.String(length=20), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["discovery_probe_jobs.id"],
            name="fk_discovery_probe_hosts_job_id_discovery_probe_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_discovery_probe_hosts_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name="fk_discovery_probe_hosts_device_id_devices",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("job_id", "address", name="uq_discovery_probe_hosts_job_id_address"),
    )
    op.create_index("ix_discovery_probe_hosts_job_id", "discovery_probe_hosts", ["job_id"])
    op.create_index("ix_discovery_probe_hosts_tenant_id", "discovery_probe_hosts", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_discovery_probe_hosts_tenant_id", table_name="discovery_probe_hosts")
    op.drop_index("ix_discovery_probe_hosts_job_id", table_name="discovery_probe_hosts")
    op.drop_table("discovery_probe_hosts")

    op.drop_index("ix_discovery_probe_jobs_status", table_name="discovery_probe_jobs")
    op.drop_index("ix_discovery_probe_jobs_scope_id", table_name="discovery_probe_jobs")
    op.drop_index("ix_discovery_probe_jobs_tenant_id", table_name="discovery_probe_jobs")
    op.drop_table("discovery_probe_jobs")
