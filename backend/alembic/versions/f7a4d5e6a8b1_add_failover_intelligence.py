"""add wan failover intelligence layer

Additive, reversible migration for the WAN Failover Intelligence milestone
(detect -> trace -> verify). Three new tables, nothing else:

1. `operational_sla_policies` - the OPERATIONAL threshold model the failover
   detector evaluates live WAN link telemetry against (warning / critical /
   clear level per metric + a consecutive-sample requirement). Deliberately
   separate from the existing business `sla_policies` table, which models SLA
   contracts (targets, business hours, breach windows), not live telemetry
   thresholds.
2. `failover_events` - append-only record of one observed failover/breach
   episode, capturing the route and BGP transitions that would otherwise be
   LOST: `topology_links`, `network_routes` and `bgp_neighbors` are all
   refreshed in place by every collection run and keep no transition history,
   so the timeline has to be captured at detection time. Includes a partial
   unique index so a flapping link extends one open episode instead of
   producing an event storm.
3. `failover_detector_state` - per-scope baseline (previous round's link /
   route / BGP snapshot, last path decision) plus the consecutive-breach and
   consecutive-clear counters. Kept in the database, not process memory, so a
   scheduler restart resumes the SAME episode rather than re-opening it.

No existing table, column or index is touched, and no backfill runs: with zero
policies configured the detector has nothing to evaluate against and stays
inert, which is the correct default for a tenant that has not opted in.

Revision ID: f7a4d5e6a8b1
Revises: f1a2b3c4d5e6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f7a4d5e6a8b1"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. operational_sla_policies
    # ------------------------------------------------------------------
    op.create_table(
        "operational_sla_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "scope", sa.String(length=30), nullable=False, server_default="all_wan",
        ),
        # Polymorphic (site id or topology link id, per `scope`) - intentionally
        # not FK-constrained; `scope` states which table it addresses.
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("warning_threshold", sa.Float(), nullable=True),
        sa.Column("critical_threshold", sa.Float(), nullable=True),
        sa.Column("clear_threshold", sa.Float(), nullable=True),
        sa.Column(
            "min_consecutive_samples", sa.Integer(), nullable=False, server_default="2",
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"),
        ),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "deleted_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_operational_sla_policies_tenant_id", "operational_sla_policies", ["tenant_id"]
    )
    op.create_index(
        "ix_operational_sla_policies_is_active", "operational_sla_policies", ["is_active"]
    )
    op.create_index(
        "ix_operational_sla_policies_is_deleted", "operational_sla_policies", ["is_deleted"]
    )
    op.create_index(
        "ix_operational_sla_policies_tenant_active",
        "operational_sla_policies", ["tenant_id", "is_active"],
    )
    op.create_index(
        "ix_operational_sla_policies_tenant_scope",
        "operational_sla_policies", ["tenant_id", "scope", "scope_id"],
    )

    # ------------------------------------------------------------------
    # 2. failover_events (append-only episode + captured transitions)
    # ------------------------------------------------------------------
    op.create_table(
        "failover_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "site_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "edge_device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("scope_key", sa.String(length=200), nullable=False),
        sa.Column("dedup_key", sa.String(length=300), nullable=False),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="detected",
        ),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "transition_log", postgresql.JSONB(), nullable=False, server_default="[]",
        ),
        sa.Column("trigger", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("decision", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "link_from_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topology_links.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("link_from_name", sa.String(length=255), nullable=True),
        sa.Column(
            "link_to_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topology_links.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("link_to_name", sa.String(length=255), nullable=True),
        sa.Column(
            "route_transitions", postgresql.JSONB(), nullable=False, server_default="[]",
        ),
        sa.Column(
            "bgp_transitions", postgresql.JSONB(), nullable=False, server_default="[]",
        ),
        sa.Column("blast_radius", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("verification", postgresql.JSONB(), nullable=True),
        sa.Column(
            "incident_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_over_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovering_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_failover_events_tenant_id", "failover_events", ["tenant_id"])
    op.create_index("ix_failover_events_site_id", "failover_events", ["site_id"])
    op.create_index(
        "ix_failover_events_edge_device_id", "failover_events", ["edge_device_id"]
    )
    op.create_index("ix_failover_events_status", "failover_events", ["status"])
    op.create_index("ix_failover_events_incident_id", "failover_events", ["incident_id"])
    op.create_index("ix_failover_events_detected_at", "failover_events", ["detected_at"])
    op.create_index(
        "ix_failover_events_tenant_status", "failover_events", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_failover_events_tenant_site_detected",
        "failover_events", ["tenant_id", "site_id", "detected_at"],
    )
    op.create_index(
        "ix_failover_events_tenant_created", "failover_events", ["tenant_id", "created_at"]
    )
    # Resume / sequence lookups are always (tenant, scope).
    op.create_index(
        "ix_failover_events_tenant_scope", "failover_events", ["tenant_id", "scope_key"]
    )
    # FK referencing columns: Postgres needs these to avoid a sequential scan of
    # failover_events for every topology link deleted with its tenant.
    op.create_index("ix_failover_events_link_from_id", "failover_events", ["link_from_id"])
    op.create_index("ix_failover_events_link_to_id", "failover_events", ["link_to_id"])
    # Only one OPEN episode per (tenant, dedup_key): a flapping path appends
    # transitions to the existing row rather than opening a new one per cycle.
    op.create_index(
        "uq_failover_events_open_dedup",
        "failover_events", ["tenant_id", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('detected', 'failed_over', 'recovering')"),
    )

    # ------------------------------------------------------------------
    # 3. failover_detector_state (restart-safe baseline + counters)
    # ------------------------------------------------------------------
    op.create_table(
        "failover_detector_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("scope_key", sa.String(length=200), nullable=False),
        sa.Column(
            "site_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "edge_device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "active_episode_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("failover_events.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "consecutive_breaches", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("consecutive_clear", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_breach_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previous_decision", postgresql.JSONB(), nullable=True),
        sa.Column(
            "link_snapshot", postgresql.JSONB(), nullable=False, server_default="{}",
        ),
        sa.Column(
            "route_snapshot", postgresql.JSONB(), nullable=False, server_default="{}",
        ),
        sa.Column(
            "bgp_snapshot", postgresql.JSONB(), nullable=False, server_default="{}",
        ),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "scope_key", name="uq_failover_detector_state_scope",
        ),
    )
    op.create_index(
        "ix_failover_detector_state_tenant_id", "failover_detector_state", ["tenant_id"]
    )
    op.create_index(
        "ix_failover_detector_state_scope_key", "failover_detector_state", ["scope_key"]
    )
    op.create_index(
        "ix_failover_detector_state_site_id", "failover_detector_state", ["site_id"]
    )
    op.create_index(
        "ix_failover_detector_state_edge_device_id",
        "failover_detector_state", ["edge_device_id"],
    )
    op.create_index(
        "ix_failover_detector_state_active_episode_id",
        "failover_detector_state", ["active_episode_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_failover_detector_state_active_episode_id", table_name="failover_detector_state"
    )
    op.drop_index(
        "ix_failover_detector_state_edge_device_id", table_name="failover_detector_state"
    )
    op.drop_index(
        "ix_failover_detector_state_site_id", table_name="failover_detector_state"
    )
    op.drop_index(
        "ix_failover_detector_state_scope_key", table_name="failover_detector_state"
    )
    op.drop_index(
        "ix_failover_detector_state_tenant_id", table_name="failover_detector_state"
    )
    op.drop_table("failover_detector_state")

    op.drop_index("uq_failover_events_open_dedup", table_name="failover_events")
    op.drop_index("ix_failover_events_link_to_id", table_name="failover_events")
    op.drop_index("ix_failover_events_link_from_id", table_name="failover_events")
    op.drop_index("ix_failover_events_tenant_scope", table_name="failover_events")
    op.drop_index("ix_failover_events_tenant_created", table_name="failover_events")
    op.drop_index("ix_failover_events_tenant_site_detected", table_name="failover_events")
    op.drop_index("ix_failover_events_tenant_status", table_name="failover_events")
    op.drop_index("ix_failover_events_detected_at", table_name="failover_events")
    op.drop_index("ix_failover_events_incident_id", table_name="failover_events")
    op.drop_index("ix_failover_events_status", table_name="failover_events")
    op.drop_index("ix_failover_events_edge_device_id", table_name="failover_events")
    op.drop_index("ix_failover_events_site_id", table_name="failover_events")
    op.drop_index("ix_failover_events_tenant_id", table_name="failover_events")
    op.drop_table("failover_events")

    op.drop_index(
        "ix_operational_sla_policies_tenant_scope", table_name="operational_sla_policies"
    )
    op.drop_index(
        "ix_operational_sla_policies_tenant_active", table_name="operational_sla_policies"
    )
    op.drop_index(
        "ix_operational_sla_policies_is_deleted", table_name="operational_sla_policies"
    )
    op.drop_index(
        "ix_operational_sla_policies_is_active", table_name="operational_sla_policies"
    )
    op.drop_index(
        "ix_operational_sla_policies_tenant_id", table_name="operational_sla_policies"
    )
    op.drop_table("operational_sla_policies")
