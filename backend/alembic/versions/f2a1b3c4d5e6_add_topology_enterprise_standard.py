"""
add topology enterprise standard (links, assets, site hierarchy)

Revision ID: f2a1b3c4d5e6
Revises: 1c644b59d560
Create Date: 2026-09-14 12:00:00.000000

Implements Sections 13-23 topology standard:
  * new `topology_links` table (rich link/WAN/VPN/MPLS edges with bandwidth,
    latency, packet loss, utilization, provider/circuit, VRF/VPN, routing
    protocol, redundancy group, admin/oper status)
  * Device: `role`, `role_class`, location-hierarchy columns
    (country/city/campus/building/datacenter/rack) and `custom_metadata`
  * Site: location-hierarchy columns + `site_type`, `parent_site_id`,
    `latitude`/`longitude`, and `site_metadata`

All new columns are nullable/additive; existing rows are unclassified rather
than guessed, per the project's "honest, no fabrication" convention.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a1b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "1c644b59d560"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================================
    # topology_links
    # ============================================================
    op.create_table(
        "topology_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_a_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_b_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_interface_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("interfaces.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dest_interface_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("interfaces.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("link_type", sa.String(50), nullable=False, server_default="other"),
        sa.Column("discovered_by", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("provider", sa.String(150), nullable=True),
        sa.Column("circuit_id", sa.String(150), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("bandwidth_mbps", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("packet_loss_pct", sa.Float(), nullable=True),
        sa.Column("utilization_pct", sa.Float(), nullable=True),
        sa.Column("admin_status", sa.String(20), nullable=False, server_default="up"),
        sa.Column("oper_status", sa.String(20), nullable=False, server_default="up"),
        sa.Column("last_state_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("routing_protocols", postgresql.JSONB(), nullable=True),
        sa.Column("ospf", postgresql.JSONB(), nullable=True),
        sa.Column("bgp", postgresql.JSONB(), nullable=True),
        sa.Column("vrfs", postgresql.JSONB(), nullable=True),
        sa.Column("vpn_name", sa.String(150), nullable=True),
        sa.Column("vpn_type", sa.String(50), nullable=True),
        sa.Column("redundancy_group", sa.String(100), nullable=True),
        sa.Column("is_redundant", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("tenant_id", "device_a_id", "device_b_id", "name",
                            name="uq_topology_links_tenant_devices_name"),
    )
    op.create_index("ix_topology_links_tenant_id", "topology_links", ["tenant_id"])
    op.create_index("ix_topology_links_device_a", "topology_links", ["device_a_id"])
    op.create_index("ix_topology_links_device_b", "topology_links", ["device_b_id"])
    op.create_index("ix_topology_links_link_type", "topology_links", ["link_type"])
    op.create_index("ix_topology_links_oper_status", "topology_links", ["oper_status"])


    # ============================================================
    # devices: role + location hierarchy + custom_metadata
    # ============================================================
    op.add_column("devices", sa.Column("role", sa.String(50), nullable=True))
    op.add_column("devices", sa.Column("role_class", sa.String(30), nullable=True))
    op.add_column("devices", sa.Column("country", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("campus", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("building", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("datacenter_name", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("rack", sa.String(50), nullable=True))
    # NOTE: devices.custom_metadata is already added by the earlier revision
    # 1be605e3f990 (CMDB fields). Do NOT re-add it here: a fresh
    # `alembic upgrade head` would otherwise fail with DuplicateColumnError.
    op.create_index("ix_devices_role", "devices", ["role"])
    op.create_index("ix_devices_role_class", "devices", ["role_class"])
    op.create_index("ix_devices_country", "devices", ["country"])
    op.create_index("ix_devices_city", "devices", ["city"])

    # ============================================================
    # sites: location hierarchy + topology attributes
    # ============================================================
    op.add_column("sites", sa.Column("country", sa.String(100), nullable=True))
    op.add_column("sites", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("sites", sa.Column("campus", sa.String(100), nullable=True))
    op.add_column("sites", sa.Column("building", sa.String(100), nullable=True))
    op.add_column("sites", sa.Column("datacenter_name", sa.String(100), nullable=True))
    op.add_column("sites", sa.Column("rack", sa.String(50), nullable=True))
    op.add_column("sites", sa.Column("site_type", sa.String(50), nullable=True))
    op.add_column("sites", sa.Column("parent_site_id", postgresql.UUID(as_uuid=True),
                                     sa.ForeignKey("sites.id", ondelete="SET NULL"), nullable=True))
    op.add_column("sites", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("sites", sa.Column("longitude", sa.Float(), nullable=True))
    op.add_column("sites", sa.Column("site_metadata", postgresql.JSONB(),
                                     nullable=False, server_default="{}"))
    op.create_index("ix_sites_country", "sites", ["country"])
    op.create_index("ix_sites_city", "sites", ["city"])
    op.create_index("ix_sites_site_type", "sites", ["site_type"])


def downgrade() -> None:
    op.drop_index("ix_sites_site_type", table_name="sites")
    op.drop_index("ix_sites_city", table_name="sites")
    op.drop_index("ix_sites_country", table_name="sites")
    op.drop_column("sites", "site_metadata")
    op.drop_column("sites", "longitude")
    op.drop_column("sites", "latitude")
    op.drop_column("sites", "parent_site_id")
    op.drop_column("sites", "site_type")
    op.drop_column("sites", "rack")
    op.drop_column("sites", "datacenter_name")
    op.drop_column("sites", "building")
    op.drop_column("sites", "campus")
    op.drop_column("sites", "city")
    op.drop_column("sites", "country")

    op.drop_index("ix_devices_city", table_name="devices")
    op.drop_index("ix_devices_country", table_name="devices")
    op.drop_index("ix_devices_role_class", table_name="devices")
    op.drop_index("ix_devices_role", table_name="devices")
    op.drop_column("devices", "rack")
    op.drop_column("devices", "datacenter_name")
    op.drop_column("devices", "building")
    op.drop_column("devices", "campus")
    op.drop_column("devices", "city")
    op.drop_column("devices", "country")
    op.drop_column("devices", "role_class")
    op.drop_column("devices", "role")

    op.drop_index("ix_topology_links_oper_status", table_name="topology_links")
    op.drop_index("ix_topology_links_link_type", table_name="topology_links")
    op.drop_index("ix_topology_links_device_b", table_name="topology_links")
    op.drop_index("ix_topology_links_device_a", table_name="topology_links")
    op.drop_index("ix_topology_links_tenant_id", table_name="topology_links")
    op.drop_table("topology_links")

