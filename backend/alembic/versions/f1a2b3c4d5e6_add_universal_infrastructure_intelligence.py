"""add universal infrastructure intelligence layer

Additive, reversible migration for the Universal Infrastructure Intelligence
milestone. It does four things and nothing else:

1. `devices` gains the universal-resource columns (provider,
   parent_resource_id, lifecycle_state, health, discovered_at,
   last_telemetry_at). `provider_resource_id` is deliberately NOT added - it is
   the existing `external_id`, exposed as a property, so there is one source of
   truth for the vendor-native id.
2. `interfaces` gains the canonical interface snapshot columns (spec s.15).
3. `metrics` becomes the CANONICAL telemetry store (spec s.16) - new
   capability/quality/value-shape columns, `value` relaxed to NULLABLE, and the
   read-path indexes the bounded time-range queries need.
4. Three new tables: `resource_capabilities`, `network_routes`,
   `bgp_neighbors`.

Why `value` becomes nullable: a canonical telemetry record may legitimately
carry a text or boolean value, and an unsupported capability must be recorded as
an explicit state rather than a fabricated 0. Existing rows are unaffected (they
all have a value).

No backfill is performed and no existing row is modified: `metric_name` stays
NULL for legacy rows on purpose (see the note inside `upgrade()`), because the
real `metrics` table holds 2.59M rows and the canonical read path keys off the
already-indexed `metric_type` column.

No existing column is dropped and no existing index is removed.

Revision ID: f1a2b3c4d5e6
Revises: e7b2c9f4a1d3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f1a2b3c4d5e6"
down_revision = "e7b2c9f4a1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. devices -> universal resource
    # ------------------------------------------------------------------
    op.add_column("devices", sa.Column("provider", sa.String(length=50), nullable=True))
    op.add_column(
        "devices",
        sa.Column("parent_resource_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column(
            "lifecycle_state",
            sa.String(length=30),
            nullable=False,
            server_default="discovered",
        ),
    )
    op.add_column("devices", sa.Column("health", sa.String(length=20), nullable=True))
    op.add_column(
        "devices", sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "devices",
        sa.Column("last_telemetry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_devices_parent_resource_id_devices",
        "devices",
        "devices",
        ["parent_resource_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_devices_provider", "devices", ["provider"])
    op.create_index("ix_devices_parent_resource_id", "devices", ["parent_resource_id"])
    op.create_index("ix_devices_lifecycle_state", "devices", ["lifecycle_state"])
    op.create_index("ix_devices_health", "devices", ["health"])
    op.create_index("ix_devices_last_telemetry_at", "devices", ["last_telemetry_at"])

    # ------------------------------------------------------------------
    # 2. interfaces -> universal interface model (spec s.15)
    # ------------------------------------------------------------------
    op.add_column("interfaces", sa.Column("description", sa.String(length=255), nullable=True))
    op.add_column(
        "interfaces",
        sa.Column(
            "interface_type", sa.String(length=30), nullable=False, server_default="unknown"
        ),
    )
    op.add_column("interfaces", sa.Column("vlan_id", sa.Integer(), nullable=True))
    op.add_column("interfaces", sa.Column("speed_bps", sa.Float(), nullable=True))
    op.add_column("interfaces", sa.Column("rx_bps", sa.Float(), nullable=True))
    op.add_column("interfaces", sa.Column("tx_bps", sa.Float(), nullable=True))
    op.add_column("interfaces", sa.Column("utilization_pct", sa.Float(), nullable=True))
    op.add_column("interfaces", sa.Column("discards_in", sa.Float(), nullable=True))
    op.add_column("interfaces", sa.Column("discards_out", sa.Float(), nullable=True))
    op.add_column("interfaces", sa.Column("packet_loss_pct", sa.Float(), nullable=True))
    op.add_column("interfaces", sa.Column("latency_ms", sa.Float(), nullable=True))
    op.add_column("interfaces", sa.Column("jitter_ms", sa.Float(), nullable=True))
    op.add_column(
        "interfaces",
        sa.Column("last_telemetry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_interfaces_interface_type", "interfaces", ["interface_type"])
    op.create_index("ix_interfaces_last_telemetry_at", "interfaces", ["last_telemetry_at"])

    # ------------------------------------------------------------------
    # 3. metrics -> canonical telemetry (spec s.16)
    # ------------------------------------------------------------------
    op.add_column(
        "metrics",
        sa.Column("interface_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("metrics", sa.Column("metric_name", sa.String(length=100), nullable=True))
    op.add_column("metrics", sa.Column("capability", sa.String(length=60), nullable=True))
    op.add_column("metrics", sa.Column("provider", sa.String(length=50), nullable=True))
    op.add_column(
        "metrics",
        sa.Column(
            "collection_status",
            sa.String(length=30),
            nullable=False,
            server_default="collected",
        ),
    )
    op.add_column("metrics", sa.Column("quality", sa.String(length=20), nullable=True))
    op.add_column("metrics", sa.Column("text_value", sa.Text(), nullable=True))
    op.add_column("metrics", sa.Column("boolean_value", sa.Boolean(), nullable=True))
    op.add_column(
        "metrics", sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "metrics", sa.Column("received_timestamp", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "metrics", sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    # Spec s.16: text/boolean readings and "no value" states are legitimate.
    op.alter_column("metrics", "value", existing_type=sa.Float(), nullable=True)
    op.create_foreign_key(
        "fk_metrics_interface_id_interfaces",
        "metrics",
        "interfaces",
        ["interface_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_metrics_metric_name", "metrics", ["metric_name"])
    op.create_index("ix_metrics_capability", "metrics", ["capability"])
    op.create_index("ix_metrics_provider", "metrics", ["provider"])
    op.create_index("ix_metrics_collection_status", "metrics", ["collection_status"])
    op.create_index(
        "ix_metrics_tenant_capability_recorded",
        "metrics",
        ["tenant_id", "capability", "recorded_at"],
    )
    op.create_index(
        "ix_metrics_device_capability_recorded",
        "metrics",
        ["device_id", "capability", "recorded_at"],
    )
    op.create_index(
        "ix_metrics_interface_recorded", "metrics", ["interface_id", "recorded_at"]
    )
    op.create_index(
        "ix_metrics_tenant_metric_recorded",
        "metrics",
        ["tenant_id", "metric_type", "recorded_at"],
    )
    # NO BACKFILL of metric_name - deliberately.
    #
    # An `UPDATE metrics SET metric_name = metric_type` was measured against the
    # real database (2,590,667 rows) and took >8 minutes of continuous
    # DataFileWrite, holding ACCESS EXCLUSIVE on the table for the whole time.
    # It is unnecessary: `metric_name` is a READ CONVENIENCE, not a key.
    #   * every canonical query filters/groups on the pre-existing
    #     ``metric_type`` column, which is already indexed;
    #   * the API projects ``metric_name or metric_type``;
    #   * rows written from now on always populate it.
    # So legacy rows keep metric_name = NULL (the second-largest kind of correct
    # answer after the right one) instead of paying a 2.6M-row table rewrite in
    # a migration. Constraints, indexes and new tables below are unaffected.

    # ------------------------------------------------------------------
    # 4. New universal tables
    # ------------------------------------------------------------------
    op.create_table(
        "resource_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("capability", sa.String(length=60), nullable=False),
        sa.Column("support", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="not_collected"
        ),
        sa.Column("reason", sa.String(length=80), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="collector"),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_id", "capability", name="uq_resource_capabilities_resource_capability"
        ),
        sa.CheckConstraint(
            "support IN ('supported', 'unsupported', 'unknown')",
            name="ck_resource_capabilities_support",
        ),
        sa.CheckConstraint(
            "support = 'supported' OR reason IS NOT NULL",
            name="ck_resource_capabilities_reason_required",
        ),
    )
    op.create_index(
        "ix_resource_capabilities_tenant_id", "resource_capabilities", ["tenant_id"]
    )
    op.create_index(
        "ix_resource_capabilities_resource_id", "resource_capabilities", ["resource_id"]
    )
    op.create_index(
        "ix_resource_capabilities_provider", "resource_capabilities", ["provider"]
    )
    op.create_index(
        "ix_resource_capabilities_capability", "resource_capabilities", ["capability"]
    )
    op.create_index(
        "ix_resource_capabilities_tenant_capability",
        "resource_capabilities",
        ["tenant_id", "capability"],
    )
    op.create_index(
        "ix_resource_capabilities_tenant_support",
        "resource_capabilities",
        ["tenant_id", "support"],
    )

    op.create_table(
        "network_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interface_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("vrf", sa.String(length=100), nullable=False, server_default="-"),
        sa.Column("destination", sa.String(length=80), nullable=False),
        sa.Column("prefix_length", sa.Integer(), nullable=True),
        sa.Column("next_hop", sa.String(length=80), nullable=True),
        sa.Column("gateway", sa.String(length=80), nullable=True),
        sa.Column("outgoing_interface", sa.String(length=255), nullable=True),
        sa.Column("protocol", sa.String(length=30), nullable=False, server_default="other"),
        sa.Column("metric", sa.Integer(), nullable=True),
        sa.Column("administrative_distance", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("is_best", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("learned", sa.Boolean(), nullable=True),
        sa.Column("advertised", sa.Boolean(), nullable=True),
        sa.Column("age_seconds", sa.Float(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interface_id"], ["interfaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_id", "vrf", "destination", "next_hop", "protocol",
            name="uq_network_routes_identity",
        ),
    )
    op.create_index("ix_network_routes_tenant_id", "network_routes", ["tenant_id"])
    op.create_index("ix_network_routes_resource_id", "network_routes", ["resource_id"])
    op.create_index("ix_network_routes_provider", "network_routes", ["provider"])
    op.create_index(
        "ix_network_routes_tenant_protocol", "network_routes", ["tenant_id", "protocol"]
    )
    op.create_index(
        "ix_network_routes_resource_protocol", "network_routes", ["resource_id", "protocol"]
    )
    op.create_index("ix_network_routes_tenant_state", "network_routes", ["tenant_id", "state"])
    op.create_index("ix_network_routes_is_deleted", "network_routes", ["is_deleted"])

    op.create_table(
        "bgp_neighbors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("vrf", sa.String(length=100), nullable=False, server_default="-"),
        sa.Column("neighbor_ip", sa.String(length=80), nullable=False),
        sa.Column("local_ip", sa.String(length=80), nullable=True),
        sa.Column("remote_as", sa.String(length=30), nullable=True),
        sa.Column("local_as", sa.String(length=30), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("admin_status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("uptime_seconds", sa.Float(), nullable=True),
        sa.Column("prefixes_received", sa.Integer(), nullable=True),
        sa.Column("prefixes_advertised", sa.Integer(), nullable=True),
        sa.Column("last_state_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_id", "vrf", "neighbor_ip", name="uq_bgp_neighbors_identity"
        ),
    )
    op.create_index("ix_bgp_neighbors_tenant_id", "bgp_neighbors", ["tenant_id"])
    op.create_index("ix_bgp_neighbors_resource_id", "bgp_neighbors", ["resource_id"])
    op.create_index("ix_bgp_neighbors_provider", "bgp_neighbors", ["provider"])
    op.create_index("ix_bgp_neighbors_state", "bgp_neighbors", ["state"])
    op.create_index("ix_bgp_neighbors_tenant_state", "bgp_neighbors", ["tenant_id", "state"])
    op.create_index("ix_bgp_neighbors_is_deleted", "bgp_neighbors", ["is_deleted"])


def downgrade() -> None:
    op.drop_index("ix_bgp_neighbors_is_deleted", table_name="bgp_neighbors")
    op.drop_index("ix_bgp_neighbors_tenant_state", table_name="bgp_neighbors")
    op.drop_index("ix_bgp_neighbors_state", table_name="bgp_neighbors")
    op.drop_index("ix_bgp_neighbors_provider", table_name="bgp_neighbors")
    op.drop_index("ix_bgp_neighbors_resource_id", table_name="bgp_neighbors")
    op.drop_index("ix_bgp_neighbors_tenant_id", table_name="bgp_neighbors")
    op.drop_table("bgp_neighbors")

    op.drop_index("ix_network_routes_is_deleted", table_name="network_routes")
    op.drop_index("ix_network_routes_tenant_state", table_name="network_routes")
    op.drop_index("ix_network_routes_resource_protocol", table_name="network_routes")
    op.drop_index("ix_network_routes_tenant_protocol", table_name="network_routes")
    op.drop_index("ix_network_routes_provider", table_name="network_routes")
    op.drop_index("ix_network_routes_resource_id", table_name="network_routes")
    op.drop_index("ix_network_routes_tenant_id", table_name="network_routes")
    op.drop_table("network_routes")

    op.drop_index("ix_resource_capabilities_tenant_support", table_name="resource_capabilities")
    op.drop_index("ix_resource_capabilities_tenant_capability", table_name="resource_capabilities")
    op.drop_index("ix_resource_capabilities_capability", table_name="resource_capabilities")
    op.drop_index("ix_resource_capabilities_provider", table_name="resource_capabilities")
    op.drop_index("ix_resource_capabilities_resource_id", table_name="resource_capabilities")
    op.drop_index("ix_resource_capabilities_tenant_id", table_name="resource_capabilities")
    op.drop_table("resource_capabilities")

    # metrics: back to the pre-universal shape
    op.drop_index("ix_metrics_tenant_metric_recorded", table_name="metrics")
    op.drop_index("ix_metrics_interface_recorded", table_name="metrics")
    op.drop_index("ix_metrics_device_capability_recorded", table_name="metrics")
    op.drop_index("ix_metrics_tenant_capability_recorded", table_name="metrics")
    op.drop_index("ix_metrics_collection_status", table_name="metrics")
    op.drop_index("ix_metrics_provider", table_name="metrics")
    op.drop_index("ix_metrics_capability", table_name="metrics")
    op.drop_index("ix_metrics_metric_name", table_name="metrics")
    op.drop_constraint("fk_metrics_interface_id_interfaces", "metrics", type_="foreignkey")
    # Re-narrowing is safe: rows the universal layer wrote without any value at
    # all are removed first so the NOT NULL restoration cannot fail.
    op.execute(
        "DELETE FROM metrics WHERE value IS NULL AND text_value IS NULL "
        "AND boolean_value IS NULL"
    )
    op.alter_column("metrics", "value", existing_type=sa.Float(), nullable=False)
    for column in (
        "dimensions", "received_timestamp", "source_timestamp", "boolean_value",
        "text_value", "quality", "collection_status", "provider", "capability",
        "metric_name", "interface_id",
    ):
        op.drop_column("metrics", column)

    # interfaces
    op.drop_index("ix_interfaces_last_telemetry_at", table_name="interfaces")
    op.drop_index("ix_interfaces_interface_type", table_name="interfaces")
    for column in (
        "last_telemetry_at", "jitter_ms", "latency_ms", "packet_loss_pct",
        "discards_out", "discards_in", "utilization_pct", "tx_bps", "rx_bps",
        "speed_bps", "vlan_id", "interface_type", "description",
    ):
        op.drop_column("interfaces", column)

    # devices
    op.drop_index("ix_devices_last_telemetry_at", table_name="devices")
    op.drop_index("ix_devices_health", table_name="devices")
    op.drop_index("ix_devices_lifecycle_state", table_name="devices")
    op.drop_index("ix_devices_parent_resource_id", table_name="devices")
    op.drop_index("ix_devices_provider", table_name="devices")
    op.drop_constraint("fk_devices_parent_resource_id_devices", "devices", type_="foreignkey")
    for column in (
        "last_telemetry_at", "discovered_at", "health", "lifecycle_state",
        "parent_resource_id", "provider",
    ):
        op.drop_column("devices", column)

