"""
add integrations, normalized events, topology, zones, change requests, ai diagnoses

Revision ID: e5d4c3b2a1f0
Revises: a7f2c9d4e1b8, b8e4f8a2d5c7
Create Date: 2026-09-10 10:00:00.000000

Adds the core models for the unified NOC+SOC+ITSM+AIOps platform.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5d4c3b2a1f0"
down_revision: Union[str, Sequence[str], None] = ("a7f2c9d4e1b8", "b8e4f8a2d5c7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================================
    # integrations
    # ============================================================
    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="disabled"),
        sa.Column("health_score", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("configuration", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("credential_reference", sa.String(500), nullable=True),
        sa.Column("discovery_state", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("synchronization_state", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("last_successful_collection", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_discovery", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synchronization", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(1000), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telemetry_rate_per_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_rate_per_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_events_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_integrations_tenant_id_name"),
    )
    op.create_index("ix_integrations_tenant_id", "integrations", ["tenant_id"])
    op.create_index("ix_integrations_provider_type", "integrations", ["provider_type"])
    op.create_index("ix_integrations_provider", "integrations", ["provider"])
    op.create_index("ix_integrations_status", "integrations", ["status"])

    # ============================================================
    # normalized_events
    # ============================================================
    op.create_table(
        "normalized_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integrations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="SET NULL"), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_event_id", sa.String(255), nullable=True),
        sa.Column("event_signature", sa.String(500), nullable=True),
        sa.Column("event_category", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("noc_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("noc_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_suppressed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("telemetry_type", sa.String(50), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("raw_reference", sa.String(500), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_normalized_events_tenant_id", "normalized_events", ["tenant_id"])
    op.create_index("ix_normalized_events_integration_id", "normalized_events", ["integration_id"])
    op.create_index("ix_normalized_events_site_id", "normalized_events", ["site_id"])
    op.create_index("ix_normalized_events_device_id", "normalized_events", ["device_id"])
    op.create_index("ix_normalized_events_severity", "normalized_events", ["severity"])
    op.create_index("ix_normalized_events_event_category", "normalized_events", ["event_category"])
    op.create_index("ix_normalized_events_event_type", "normalized_events", ["event_type"])
    op.create_index("ix_normalized_events_telemetry_type", "normalized_events", ["telemetry_type"])
    op.create_index("ix_normalized_events_is_suppressed", "normalized_events", ["is_suppressed"])
    op.create_index("ix_normalized_events_event_timestamp", "normalized_events", ["event_timestamp"])
    op.create_index("ix_normalized_events_noc_event_id", "normalized_events", ["noc_event_id"])

    # ============================================================
    # resource_relationships
    # ============================================================
    op.create_table(
        "resource_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_resource_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_resource_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=False),
        sa.Column("relationship_subtype", sa.String(50), nullable=True),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="100.0"),
        sa.Column("discovered_by", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_inferred", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint(
            "tenant_id", "source_resource_id", "target_resource_id", "relationship_type",
            name="uq_resource_relationships",
        ),
    )
    op.create_index("ix_resource_relationships_tenant_id", "resource_relationships", ["tenant_id"])
    op.create_index("ix_resource_relationships_source", "resource_relationships", ["source_resource_id"])
    op.create_index("ix_resource_relationships_target", "resource_relationships", ["target_resource_id"])
    op.create_index("ix_resource_relationships_type", "resource_relationships", ["relationship_type"])
    op.create_index("ix_resource_relationships_status", "resource_relationships", ["status"])

    # ============================================================
    # zones
    # ============================================================
    op.create_table(
        "zones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("zone_type", sa.String(50), nullable=False, server_default="network"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("tenant_id", "site_id", "code", name="uq_zones_tenant_id_site_id_code"),
    )
    op.create_index("ix_zones_tenant_id", "zones", ["tenant_id"])
    op.create_index("ix_zones_site_id", "zones", ["site_id"])

    # ============================================================
    # change_requests
    # ============================================================
    op.create_table(
        'change_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('change_number', sa.String(50), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('change_type', sa.String(50), nullable=False),
        sa.Column('risk_level', sa.String(20), nullable=False, server_default='medium'),
        sa.Column('priority', sa.String(20), nullable=False, server_default='normal'),
        sa.Column('status', sa.String(30), nullable=False, server_default='draft'),
        sa.Column('business_justification', sa.Text(), nullable=True),
        sa.Column('implementation_plan', sa.Text(), nullable=True),
        sa.Column('backout_plan', sa.Text(), nullable=True),
        sa.Column('test_plan', sa.Text(), nullable=True),
        sa.Column('validation_plan', sa.Text(), nullable=True),
        sa.Column('security_impact', sa.Text(), nullable=True),
        sa.Column('affected_devices', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('affected_services', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('downtime_expected', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('downtime_duration_minutes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('maintenance_window_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('maintenance_window_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('requested_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approved_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approval_notes', sa.Text(), nullable=True),
        sa.Column('implemented_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('implemented_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('implementation_result', sa.Text(), nullable=True),
        sa.Column('rollback_executed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('verified_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verification_result', sa.Text(), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closure_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),
        sa.UniqueConstraint('tenant_id', 'change_number', name='uq_change_requests_tenant_id_change_number'),
    )
    op.create_index('ix_change_requests_tenant_id', 'change_requests', ['tenant_id'])
    op.create_index('ix_change_requests_change_number', 'change_requests', ['change_number'])
    op.create_index('ix_change_requests_status', 'change_requests', ['status'])
    op.create_index('ix_change_requests_change_type', 'change_requests', ['change_type'])
    op.create_index('ix_change_requests_risk_level', 'change_requests', ['risk_level'])

    # ============================================================
    # ai_diagnoses
    # ============================================================
    op.create_table(
        'ai_diagnoses',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('noc_event_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('noc_events.id', ondelete='CASCADE'), nullable=False),
        sa.Column('requested_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('diagnosis_summary', sa.Text(), nullable=True),
        sa.Column('root_cause_candidates', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('evidence', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('impact_assessment', sa.Text(), nullable=True),
        sa.Column('recommended_actions', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('risk_assessment', sa.String(20), nullable=True),
        sa.Column('approval_required', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('verification_plan', sa.Text(), nullable=True),
        sa.Column('overall_confidence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('evidence_sufficiency', sa.String(20), nullable=False, server_default='insufficient'),
        sa.Column('context_snapshot', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('analyst_approved', sa.Boolean(), nullable=True),
        sa.Column('analyst_feedback', sa.Text(), nullable=True),
        sa.Column('analyst_revised_diagnosis', sa.Text(), nullable=True),
        sa.Column('analysis_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('analysis_completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('model_used', sa.String(100), nullable=False, server_default='ollama-local'),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_index('ix_ai_diagnoses_tenant_id', 'ai_diagnoses', ['tenant_id'])
    op.create_index('ix_ai_diagnoses_noc_event_id', 'ai_diagnoses', ['noc_event_id'])
    op.create_index('ix_ai_diagnoses_status', 'ai_diagnoses', ['status'])


def downgrade() -> None:
    # Reverse order
    op.drop_index('ix_ai_diagnoses_status', table_name='ai_diagnoses')
    op.drop_index('ix_ai_diagnoses_noc_event_id', table_name='ai_diagnoses')
    op.drop_index('ix_ai_diagnoses_tenant_id', table_name='ai_diagnoses')
    op.drop_table('ai_diagnoses')

    op.drop_index('ix_change_requests_risk_level', table_name='change_requests')
    op.drop_index('ix_change_requests_change_type', table_name='change_requests')
    op.drop_index('ix_change_requests_status', table_name='change_requests')
    op.drop_index('ix_change_requests_change_number', table_name='change_requests')
    op.drop_index('ix_change_requests_tenant_id', table_name='change_requests')
    op.drop_table('change_requests')

    op.drop_index('ix_zones_site_id', table_name='zones')
    op.drop_index('ix_zones_tenant_id', table_name='zones')
    op.drop_table('zones')

    op.drop_index('ix_resource_relationships_status', table_name='resource_relationships')
    op.drop_index('ix_resource_relationships_type', table_name='resource_relationships')
    op.drop_index('ix_resource_relationships_target', table_name='resource_relationships')
    op.drop_index('ix_resource_relationships_source', table_name='resource_relationships')
    op.drop_index('ix_resource_relationships_tenant_id', table_name='resource_relationships')
    op.drop_table('resource_relationships')

    op.drop_index('ix_normalized_events_is_suppressed', table_name='normalized_events')
    op.drop_index('iq_normalized_events_active_per_signature', table_name='normalized_events')
    op.drop_index('ix_normalized_events_raw_reference', table_name='normalized_events')
    op.drop_index('ix_normalized_events_last_occurred_at', table_name='normalized_events')
    op.drop_index('ix_normalized_events_noc_event_id', table_name='normalized_events')
    op.drop_index('ix_normalized_events_event_timestamp', table_name='normalized_events')
    op.drop_index('ix_normalized_events_correlation_id', table_name='normalized_events')
    op.drop_index('ix_normalized_events_source_event_id', table_name='normalized_events')
    op.drop_index('ix_normalized_events_event_type', table_name='normalized_events')
    op.drop_index('ix_normalized_events_event_signature', table_name='normalized_events')
    op.drop_index('ix_normalized_events_event_category', table_name='normalized_events')
    op.drop_index('ix_normalized_events_severity', table_name='normalized_events')
    op.drop_table('normalized_events')

    op.drop_index('ix_integrations_status', table_name='integrations')
    op.drop_index('ix_integrations_provider', table_name='integrations')
    op.drop_index('ix_integrations_provider_type', table_name='integrations')
    op.drop_index('ix_integrations_tenant_id', table_name='integrations')
    op.drop_table('integrations')
