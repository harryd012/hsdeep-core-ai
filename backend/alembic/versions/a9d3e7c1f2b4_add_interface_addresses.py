"""add interface_addresses (L3 topology)

One row per IP address configured on a device interface, so the L3 topology can
connect devices that share a subnet. Additive: a new table only; ``interfaces``
and ``network_routes`` are unchanged.

Revision ID: a9d3e7c1f2b4
Revises: e8c1d4f7a2b9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a9d3e7c1f2b4"
down_revision = "e8c1d4f7a2b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interface_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "interface_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interfaces.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "monitoring_source_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("monitoring_sources.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("interface_name", sa.String(length=255), nullable=True),
        sa.Column("vrf", sa.String(length=100), nullable=False, server_default="-"),
        sa.Column("family", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=45), nullable=False),
        sa.Column("prefix_length", sa.Integer(), nullable=False),
        sa.Column("network", sa.String(length=49), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("device_id", "vrf", "address", name="uq_interface_addresses_identity"),
    )
    op.create_index("ix_interface_addresses_tenant_id", "interface_addresses", ["tenant_id"])
    op.create_index("ix_interface_addresses_device_id", "interface_addresses", ["device_id"])
    op.create_index(
        "ix_interface_addresses_monitoring_source_id", "interface_addresses", ["monitoring_source_id"]
    )
    op.create_index(
        "ix_interface_addresses_tenant_vrf_network",
        "interface_addresses", ["tenant_id", "vrf", "network"],
    )


def downgrade() -> None:
    op.drop_index("ix_interface_addresses_tenant_vrf_network", table_name="interface_addresses")
    op.drop_index("ix_interface_addresses_monitoring_source_id", table_name="interface_addresses")
    op.drop_index("ix_interface_addresses_device_id", table_name="interface_addresses")
    op.drop_index("ix_interface_addresses_tenant_id", table_name="interface_addresses")
    op.drop_table("interface_addresses")
