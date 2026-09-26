"""add universal itsm, ticket, playbook, approval, execution, knowledge, audit entities

Revision ID: b2c3d4e5f6a7
Revises: 1c644b59d560
Create Date: 2025-01-15 10:00:00.000000

Phase 1-9: Universal ITSM Framework, Enterprise Ticket Engine,
Playbook/SOP Engine, Approval Engine, Autonomous Remediation Engine,
Knowledge Base, and unified Audit Event tables.

All new tables are ADDITIVE - no existing tables are modified or
renamed. The existing ServiceNow/Incident/Alert tables remain unchanged.

New tables:
  itsm_providers         - System-level provider catalog (NOT tenant-scoped)
  itsm_connections       - Universal tenant-scoped ITSM connections
  itsm_sync_jobs         - Universal sync/retry/dead-letter queue
  tickets                - Universal enterprise ticket entity
  ticket_links           - Bidirectional sync mapping (Ticket <-> external)
  ticket_comments        - Work notes and comments
  ticket_attachments     - File attachment metadata
  ticket_history         - Audit trail of ticket state changes
  playbooks              - Structured playbooks (Phase 5)
  playbook_versions      - Versioned playbook content
  playbook_steps         - Ordered execution steps
  approval_requests      - Approval workflow entity
  approval_history       - Approval action audit trail
  execution_jobs         - Remediation execution tracking
  execution_logs         - Per-step execution logs
  rollback_jobs          - Rollback execution tracking
  knowledge_articles     - Knowledge base articles
  audit_events           - Unified audit trail
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "1c644b59d560"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # itsm_providers (system-level, NOT tenant-scoped)
    # ------------------------------------------------------------------
    op.create_table(
        "itsm_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("class_path", sa.String(500), nullable=False),
        sa.Column("supported_auth_modes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("default_rate_limit_rpm", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_itsm_providers_provider_type", "itsm_providers", ["provider_type"])
    op.create_index("ix_itsm_providers_is_active", "itsm_providers", ["is_active"])
    op.create_unique_constraint("uq_itsm_providers_provider_type", "itsm_providers", ["provider_type"])

    # ------------------------------------------------------------------
    # itsm_connections (tenant-scoped universal connection table)
    # ------------------------------------------------------------------
    op.create_table(
        "itsm_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("itsm_providers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("instance_url", sa.String(500), nullable=False),
        sa.Column("auth_type", sa.String(20), nullable=False, server_default="oauth2"),
        sa.Column("credentials_encrypted", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("pull_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_check_ok", sa.Boolean(), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_itsm_connections_tenant_id", "itsm_connections", ["tenant_id"])
    op.create_index("ix_itsm_connections_provider_id", "itsm_connections", ["provider_id"])
    op.create_index("ix_itsm_connections_provider_type", "itsm_connections", ["provider_type"])
    op.create_index("ix_itsm_connections_status", "itsm_connections", ["status"])
    op.create_index("ix_itsm_connections_pull_enabled", "itsm_connections", ["pull_enabled"])
    op.create_index("ix_itsm_connections_is_active", "itsm_connections", ["is_active"])
    op.create_index("ix_itsm_connections_is_deleted", "itsm_connections", ["is_deleted"])
    op.create_unique_constraint("uq_itsm_connections_tenant_provider_url", "itsm_connections", ["tenant_id", "provider_type", "instance_url"])

    # ------------------------------------------------------------------
    # knowledge_articles (created before tickets, without FK to tickets)
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_articles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("article_number", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("resolution_steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("prevention", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_ai_generated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("helpful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_knowledge_articles_tenant_id", "knowledge_articles", ["tenant_id"])
    op.create_index("ix_knowledge_articles_article_number", "knowledge_articles", ["article_number"])
    op.create_index("ix_knowledge_articles_category", "knowledge_articles", ["category"])
    op.create_index("ix_knowledge_articles_status", "knowledge_articles", ["status"])
    op.create_index("ix_knowledge_articles_author_id", "knowledge_articles", ["author_id"])
    op.create_index("ix_knowledge_articles_is_deleted", "knowledge_articles", ["is_deleted"])

    # ------------------------------------------------------------------
    # tickets
    # ------------------------------------------------------------------
    # If this migration's own downgrade ran earlier, it leaves behind a
    # minimal 3-column tickets stub (see downgrade()) so the sibling branch
    # d3f4a1b2c5e6 can tear down its ServiceNow columns. Detect that exact
    # stub fingerprint and clear it; a real (full-schema) tickets table is
    # never touched.
    _bind = op.get_bind()
    if sa.inspect(_bind).has_table("tickets"):
        _cols = {c["name"] for c in sa.inspect(_bind).get_columns("tickets")}
        if _cols == {"id", "servicenow_sys_id", "servicenow_sync_status"}:
            # The sibling branch may already be (re)applied, meaning
            # sop_executions holds an FK into the stub.
            if sa.inspect(_bind).has_table("sop_executions"):
                op.drop_constraint(
                    "sop_executions_ticket_id_fkey",
                    "sop_executions",
                    type_="foreignkey",
                )
            op.drop_index(
                op.f("ix_tickets_servicenow_sync_status"), table_name="tickets"
            )
            op.drop_index(op.f("ix_tickets_servicenow_sys_id"), table_name="tickets")
            op.drop_table("tickets")

    op.create_table(
        "tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ticket_number", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="incident_triggered"),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(10), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_team", sa.String(100), nullable=True),
        sa.Column("assignment_group", sa.String(100), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_breached", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_approval_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ai_ticket_summary", sa.Text(), nullable=True),
        sa.Column("ai_executive_summary", sa.Text(), nullable=True),
        sa.Column("ai_engineer_recommendation", sa.Text(), nullable=True),
        sa.Column("ai_customer_update", sa.Text(), nullable=True),
        sa.Column("ai_playbook_selection", sa.String(255), nullable=True),
        sa.Column("ai_execution_confidence", sa.Float(), nullable=True),
        sa.Column("ai_automation_recommendation", sa.Text(), nullable=True),
        sa.Column("knowledge_article_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("watchers", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("custom_fields", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_tickets_tenant_id", "tickets", ["tenant_id"])
    op.create_index("ix_tickets_incident_id", "tickets", ["incident_id"])
    op.create_index("ix_tickets_ticket_number", "tickets", ["ticket_number"])
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_priority", "tickets", ["priority"])
    op.create_index("ix_tickets_category", "tickets", ["category"])
    op.create_index("ix_tickets_assigned_to", "tickets", ["assigned_to"])
    op.create_index("ix_tickets_assigned_team", "tickets", ["assigned_team"])
    op.create_index("ix_tickets_sla_breached", "tickets", ["sla_breached"])
    op.create_index("ix_tickets_knowledge_article_id", "tickets", ["knowledge_article_id"])
    op.create_index("ix_tickets_is_deleted", "tickets", ["is_deleted"])
    op.create_unique_constraint("uq_tickets_tenant_id_ticket_number", "tickets", ["tenant_id", "ticket_number"])

    # ------------------------------------------------------------------
    # ticket_links
    # ------------------------------------------------------------------
    op.create_table(
        "ticket_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("itsm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("external_number", sa.String(100), nullable=True),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("sync_direction", sa.String(30), nullable=False, server_default="bidirectional"),
        sa.Column("last_pushed_hash", sa.String(64), nullable=True),
        sa.Column("last_pulled_hash", sa.String(64), nullable=True),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("has_unresolved_conflict", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("conflict_details", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_ticket_links_tenant_id", "ticket_links", ["tenant_id"])
    op.create_index("ix_ticket_links_connection_id", "ticket_links", ["connection_id"])
    op.create_index("ix_ticket_links_ticket_id", "ticket_links", ["ticket_id"])
    op.create_index("ix_ticket_links_external_id", "ticket_links", ["external_id"])
    op.create_index("ix_ticket_links_correlation_id", "ticket_links", ["correlation_id"])
    op.create_index("ix_ticket_links_has_unresolved_conflict", "ticket_links", ["has_unresolved_conflict"])
    op.create_unique_constraint("uq_ticket_links_connection_id_ticket_id", "ticket_links", ["connection_id", "ticket_id"])
    op.create_unique_constraint("uq_ticket_links_connection_id_external_id", "ticket_links", ["connection_id", "external_id"])

    # ------------------------------------------------------------------
    # ticket_comments
    # ------------------------------------------------------------------
    op.create_table(
        "ticket_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_synced", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("external_comment_id", sa.String(100), nullable=True),
    )
    op.create_index("ix_ticket_comments_tenant_id", "ticket_comments", ["tenant_id"])
    op.create_index("ix_ticket_comments_ticket_id", "ticket_comments", ["ticket_id"])
    op.create_index("ix_ticket_comments_author_id", "ticket_comments", ["author_id"])
    op.create_index("ix_ticket_comments_external_comment_id", "ticket_comments", ["external_comment_id"])

    # ------------------------------------------------------------------
    # ticket_attachments
    # ------------------------------------------------------------------
    op.create_table(
        "ticket_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("is_synced", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("external_attachment_id", sa.String(100), nullable=True),
    )
    op.create_index("ix_ticket_attachments_tenant_id", "ticket_attachments", ["tenant_id"])
    op.create_index("ix_ticket_attachments_ticket_id", "ticket_attachments", ["ticket_id"])
    op.create_index("ix_ticket_attachments_external_attachment_id", "ticket_attachments", ["external_attachment_id"])

    # ------------------------------------------------------------------
    # ticket_history
    # ------------------------------------------------------------------
    op.create_table(
        "ticket_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_name", sa.String(255), nullable=True),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_ticket_history_tenant_id", "ticket_history", ["tenant_id"])
    op.create_index("ix_ticket_history_ticket_id", "ticket_history", ["ticket_id"])
    op.create_index("ix_ticket_history_action_type", "ticket_history", ["action_type"])
    op.create_index("ix_ticket_history_actor_id", "ticket_history", ["actor_id"])

    # ------------------------------------------------------------------
    # itsm_sync_jobs (universal sync queue)
    # ------------------------------------------------------------------
    op.create_table(
        "itsm_sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("itsm_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ticket_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_itsm_sync_jobs_tenant_id", "itsm_sync_jobs", ["tenant_id"])
    op.create_index("ix_itsm_sync_jobs_connection_id", "itsm_sync_jobs", ["connection_id"])
    op.create_index("ix_itsm_sync_jobs_status", "itsm_sync_jobs", ["status"])
    op.create_index("ix_itsm_sync_jobs_job_type", "itsm_sync_jobs", ["job_type"])
    op.create_index("ix_itsm_sync_jobs_ticket_id", "itsm_sync_jobs", ["ticket_id"])
    op.create_index("ix_itsm_sync_jobs_ticket_link_id", "itsm_sync_jobs", ["ticket_link_id"])
    op.create_index("ix_itsm_sync_jobs_next_attempt_at", "itsm_sync_jobs", ["next_attempt_at"])

    # ------------------------------------------------------------------
    # playbooks
    # ------------------------------------------------------------------
    op.create_table(
        "playbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("playbook_number", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sops.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(50)), nullable=False, server_default="{}"),
        sa.Column("business_impact", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("approval_policy", sa.String(20), nullable=False, server_default="single"),
        sa.Column("execution_type", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("vendor", sa.String(100), nullable=True),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("device_type", sa.String(100), nullable=True),
        sa.Column("min_priority", sa.String(10), nullable=True),
        sa.Column("supported_vendors", postgresql.ARRAY(sa.String(100)), nullable=False, server_default="{}"),
        sa.Column("supported_platforms", postgresql.ARRAY(sa.String(50)), nullable=False, server_default="{}"),
        sa.Column("dependencies", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("estimated_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_playbooks_tenant_id", "playbooks", ["tenant_id"])
    op.create_index("ix_playbooks_category", "playbooks", ["category"])
    op.create_index("ix_playbooks_tags", "playbooks", ["tags"], postgresql_using="gin")
    op.create_index("ix_playbooks_execution_type", "playbooks", ["execution_type"])
    op.create_index("ix_playbooks_vendor", "playbooks", ["vendor"])
    op.create_index("ix_playbooks_platform", "playbooks", ["platform"])
    op.create_index("ix_playbooks_is_active", "playbooks", ["is_active"])
    op.create_index("ix_playbooks_is_deleted", "playbooks", ["is_deleted"])
    op.create_index("ix_playbooks_owner_id", "playbooks", ["owner_id"])
    op.create_unique_constraint("uq_playbooks_tenant_id_playbook_number", "playbooks", ["tenant_id", "playbook_number"])

    # ------------------------------------------------------------------
    # playbook_versions
    # ------------------------------------------------------------------
    op.create_table(
        "playbook_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("rollback_steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("validation_steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("variables", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_playbook_versions_tenant_id", "playbook_versions", ["tenant_id"])
    op.create_index("ix_playbook_versions_playbook_id", "playbook_versions", ["playbook_id"])
    op.create_index("ix_playbook_versions_status", "playbook_versions", ["status"])
    op.create_unique_constraint("uq_playbook_versions_playbook_id_version_number", "playbook_versions", ["playbook_id", "version_number"])

    # ------------------------------------------------------------------
    # playbook_steps
    # ------------------------------------------------------------------
    op.create_table(
        "playbook_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("playbook_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("playbook_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("executor", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("required_variables", postgresql.ARRAY(sa.String(100)), nullable=False, server_default="{}"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("continue_on_failure", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_playbook_steps_tenant_id", "playbook_steps", ["tenant_id"])
    op.create_index("ix_playbook_steps_playbook_version_id", "playbook_steps", ["playbook_version_id"])
    op.create_index("ix_playbook_steps_order", "playbook_steps", ["order"])

    # ------------------------------------------------------------------
    # approval_requests
    # ------------------------------------------------------------------
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("required_role", sa.String(100), nullable=True),
        sa.Column("stage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("total_stages", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("is_emergency", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("delegated_from", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("delegated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_approval_requests_tenant_id", "approval_requests", ["tenant_id"])
    op.create_index("ix_approval_requests_entity_type", "approval_requests", ["entity_type"])
    op.create_index("ix_approval_requests_entity_id", "approval_requests", ["entity_id"])
    op.create_index("ix_approval_requests_approval_type", "approval_requests", ["approval_type"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    op.create_index("ix_approval_requests_approver_id", "approval_requests", ["approver_id"])
    op.create_index("ix_approval_requests_required_role", "approval_requests", ["required_role"])
    op.create_index("ix_approval_requests_deadline", "approval_requests", ["deadline"])

    # ------------------------------------------------------------------
    # approval_history
    # ------------------------------------------------------------------
    op.create_table(
        "approval_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_name", sa.String(255), nullable=True),
        sa.Column("action_type", sa.String(30), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_approval_history_tenant_id", "approval_history", ["tenant_id"])
    op.create_index("ix_approval_history_approval_request_id", "approval_history", ["approval_request_id"])
    op.create_index("ix_approval_history_action_type", "approval_history", ["action_type"])

    # ------------------------------------------------------------------
    # execution_jobs
    # ------------------------------------------------------------------
    op.create_table(
        "execution_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("variables", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("blast_radius", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("circuit_breaker_tripped", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("rollback_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("was_rolled_back", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_execution_jobs_tenant_id", "execution_jobs", ["tenant_id"])
    op.create_index("ix_execution_jobs_source_type", "execution_jobs", ["source_type"])
    op.create_index("ix_execution_jobs_source_id", "execution_jobs", ["source_id"])
    op.create_index("ix_execution_jobs_ticket_id", "execution_jobs", ["ticket_id"])
    op.create_index("ix_execution_jobs_incident_id", "execution_jobs", ["incident_id"])
    op.create_index("ix_execution_jobs_status", "execution_jobs", ["status"])
    op.create_index("ix_execution_jobs_approval_request_id", "execution_jobs", ["approval_request_id"])
    op.create_index("ix_execution_jobs_circuit_breaker_tripped", "execution_jobs", ["circuit_breaker_tripped"])

    # ------------------------------------------------------------------
    # rollback_jobs
    # ------------------------------------------------------------------
    op.create_table(
        "rollback_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("execution_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rollback_steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_rollback_jobs_tenant_id", "rollback_jobs", ["tenant_id"])
    op.create_index("ix_rollback_jobs_execution_job_id", "rollback_jobs", ["execution_job_id"])
    op.create_index("ix_rollback_jobs_status", "rollback_jobs", ["status"])

    # ------------------------------------------------------------------
    # execution_logs
    # ------------------------------------------------------------------
    op.create_table(
        "execution_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("execution_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("step_title", sa.String(255), nullable=False),
        sa.Column("executor", sa.String(50), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("input_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("output_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("was_dry_run", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_execution_logs_tenant_id", "execution_logs", ["tenant_id"])
    op.create_index("ix_execution_logs_execution_job_id", "execution_logs", ["execution_job_id"])
    op.create_index("ix_execution_logs_step_order", "execution_logs", ["step_order"])

    # ------------------------------------------------------------------
    # audit_events
    # ------------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_email", sa.String(255), nullable=True),
        sa.Column("actor_type", sa.String(20), nullable=False, server_default="user"),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("old_values", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("new_values", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_entity_type", "audit_events", ["entity_type"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    # ------------------------------------------------------------------
    # Add deferred foreign keys (now that all referenced tables exist)
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "fk_tickets_knowledge_article_id", "tickets", "knowledge_articles",
        ["knowledge_article_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_knowledge_articles_source_ticket_id", "knowledge_articles", "tickets",
        ["source_ticket_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_itsm_sync_jobs_ticket_id", "itsm_sync_jobs", "tickets",
        ["ticket_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_itsm_sync_jobs_ticket_link_id", "itsm_sync_jobs", "ticket_links",
        ["ticket_link_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_playbooks_current_version_id", "playbooks", "playbook_versions",
        ["current_version_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_execution_jobs_rollback_job_id", "execution_jobs", "rollback_jobs",
        ["rollback_job_id"], ["id"], ondelete="SET NULL"
    )

    # Note: ITSM provider catalog seeding is handled at runtime by
    # ProviderRegistry.initialize_from_builtins() if the table is empty.


def downgrade() -> None:
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_entity_id", table_name="audit_events")
    op.drop_index("ix_audit_events_entity_type", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_execution_logs_step_order", table_name="execution_logs")
    op.drop_index("ix_execution_logs_execution_job_id", table_name="execution_logs")
    op.drop_index("ix_execution_logs_tenant_id", table_name="execution_logs")
    op.drop_table("execution_logs")

    # Drop the deferred FK from execution_jobs -> rollback_jobs BEFORE
    # dropping rollback_jobs, otherwise Postgres refuses with
    # DependentObjectsStillExistError.
    op.drop_constraint("fk_execution_jobs_rollback_job_id", "execution_jobs", type_="foreignkey")

    op.drop_index("ix_rollback_jobs_status", table_name="rollback_jobs")
    op.drop_index("ix_rollback_jobs_execution_job_id", table_name="rollback_jobs")
    op.drop_index("ix_rollback_jobs_tenant_id", table_name="rollback_jobs")
    op.drop_table("rollback_jobs")

    op.drop_index("ix_execution_jobs_circuit_breaker_tripped", table_name="execution_jobs")
    op.drop_index("ix_execution_jobs_approval_request_id", table_name="execution_jobs")
    op.drop_index("ix_execution_jobs_status", table_name="execution_jobs")
    op.drop_index("ix_execution_jobs_incident_id", table_name="execution_jobs")
    op.drop_index("ix_execution_jobs_ticket_id", table_name="execution_jobs")
    op.drop_index("ix_execution_jobs_source_id", table_name="execution_jobs")
    op.drop_index("ix_execution_jobs_source_type", table_name="execution_jobs")
    op.drop_index("ix_execution_jobs_tenant_id", table_name="execution_jobs")
    op.drop_table("execution_jobs")

    op.drop_index("ix_approval_history_action_type", table_name="approval_history")
    op.drop_index("ix_approval_history_approval_request_id", table_name="approval_history")
    op.drop_index("ix_approval_history_tenant_id", table_name="approval_history")
    op.drop_table("approval_history")

    op.drop_index("ix_approval_requests_deadline", table_name="approval_requests")
    op.drop_index("ix_approval_requests_required_role", table_name="approval_requests")
    op.drop_index("ix_approval_requests_approver_id", table_name="approval_requests")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_approval_type", table_name="approval_requests")
    op.drop_index("ix_approval_requests_entity_id", table_name="approval_requests")
    op.drop_index("ix_approval_requests_entity_type", table_name="approval_requests")
    op.drop_index("ix_approval_requests_tenant_id", table_name="approval_requests")
    op.drop_table("approval_requests")

    op.drop_index("ix_playbook_steps_order", table_name="playbook_steps")
    op.drop_index("ix_playbook_steps_playbook_version_id", table_name="playbook_steps")
    op.drop_index("ix_playbook_steps_tenant_id", table_name="playbook_steps")
    op.drop_table("playbook_steps")

    # Drop the deferred FK from playbooks -> playbook_versions BEFORE
    # dropping playbook_versions (DependentObjectsStillExistError otherwise).
    op.drop_constraint("fk_playbooks_current_version_id", "playbooks", type_="foreignkey")

    op.drop_index("ix_playbook_versions_status", table_name="playbook_versions")
    op.drop_index("ix_playbook_versions_playbook_id", table_name="playbook_versions")
    op.drop_index("ix_playbook_versions_tenant_id", table_name="playbook_versions")
    op.drop_table("playbook_versions")

    op.drop_index("ix_playbooks_owner_id", table_name="playbooks")
    op.drop_index("ix_playbooks_is_deleted", table_name="playbooks")
    op.drop_index("ix_playbooks_is_active", table_name="playbooks")
    op.drop_index("ix_playbooks_platform", table_name="playbooks")
    op.drop_index("ix_playbooks_vendor", table_name="playbooks")
    op.drop_index("ix_playbooks_execution_type", table_name="playbooks")
    op.drop_index("ix_playbooks_tags", table_name="playbooks")
    op.drop_index("ix_playbooks_category", table_name="playbooks")
    op.drop_index("ix_playbooks_tenant_id", table_name="playbooks")
    op.drop_table("playbooks")

    # itsm_sync_jobs FK-references tickets and ticket_links (and itsm_connections),
    # so it must be torn down BEFORE any of those tables are dropped.
    op.drop_constraint("fk_itsm_sync_jobs_ticket_link_id", "itsm_sync_jobs", type_="foreignkey")
    op.drop_constraint("fk_itsm_sync_jobs_ticket_id", "itsm_sync_jobs", type_="foreignkey")
    op.drop_index("ix_itsm_sync_jobs_next_attempt_at", table_name="itsm_sync_jobs")
    op.drop_index("ix_itsm_sync_jobs_ticket_link_id", table_name="itsm_sync_jobs")
    op.drop_index("ix_itsm_sync_jobs_ticket_id", table_name="itsm_sync_jobs")
    op.drop_index("ix_itsm_sync_jobs_job_type", table_name="itsm_sync_jobs")
    op.drop_index("ix_itsm_sync_jobs_status", table_name="itsm_sync_jobs")
    op.drop_index("ix_itsm_sync_jobs_connection_id", table_name="itsm_sync_jobs")
    op.drop_index("ix_itsm_sync_jobs_tenant_id", table_name="itsm_sync_jobs")
    op.drop_table("itsm_sync_jobs")

    op.drop_constraint("fk_tickets_knowledge_article_id", "tickets", type_="foreignkey")
    op.drop_constraint("fk_knowledge_articles_source_ticket_id", "knowledge_articles", type_="foreignkey")

    op.drop_index("ix_ticket_history_actor_id", table_name="ticket_history")
    op.drop_index("ix_ticket_history_action_type", table_name="ticket_history")
    op.drop_index("ix_ticket_history_ticket_id", table_name="ticket_history")
    op.drop_index("ix_ticket_history_tenant_id", table_name="ticket_history")
    op.drop_table("ticket_history")

    op.drop_index("ix_ticket_attachments_external_attachment_id", table_name="ticket_attachments")
    op.drop_index("ix_ticket_attachments_ticket_id", table_name="ticket_attachments")
    op.drop_index("ix_ticket_attachments_tenant_id", table_name="ticket_attachments")
    op.drop_table("ticket_attachments")

    op.drop_index("ix_ticket_comments_external_comment_id", table_name="ticket_comments")
    op.drop_index("ix_ticket_comments_author_id", table_name="ticket_comments")
    op.drop_index("ix_ticket_comments_ticket_id", table_name="ticket_comments")
    op.drop_index("ix_ticket_comments_tenant_id", table_name="ticket_comments")
    op.drop_table("ticket_comments")

    op.drop_index("ix_ticket_links_has_unresolved_conflict", table_name="ticket_links")
    op.drop_index("ix_ticket_links_correlation_id", table_name="ticket_links")
    op.drop_index("ix_ticket_links_external_id", table_name="ticket_links")
    op.drop_index("ix_ticket_links_ticket_id", table_name="ticket_links")
    op.drop_index("ix_ticket_links_connection_id", table_name="ticket_links")
    op.drop_index("ix_ticket_links_tenant_id", table_name="ticket_links")
    op.drop_table("ticket_links")

    # Cross-branch dependency: sop_executions (created by migration
    # d3f4a1b2c5e6, a sibling of this revision off 1c644b59d560) holds an FK
    # into tickets. Alembic may downgrade this branch before the sibling
    # branch, so drop that constraint here if the table still exists.
    _bind = op.get_bind()
    if sa.inspect(_bind).has_table("sop_executions"):
        op.drop_constraint(
            "sop_executions_ticket_id_fkey", "sop_executions", type_="foreignkey"
        )

    # tickets is referenced by ticket_history/attachments/comments/links
    # (dropped above), so it must be torn down after all of them.
    op.drop_index("ix_tickets_is_deleted", table_name="tickets")
    op.drop_index("ix_tickets_knowledge_article_id", table_name="tickets")
    op.drop_index("ix_tickets_sla_breached", table_name="tickets")
    op.drop_index("ix_tickets_assigned_team", table_name="tickets")
    op.drop_index("ix_tickets_assigned_to", table_name="tickets")
    op.drop_index("ix_tickets_category", table_name="tickets")
    op.drop_index("ix_tickets_priority", table_name="tickets")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_index("ix_tickets_ticket_number", table_name="tickets")
    op.drop_index("ix_tickets_incident_id", table_name="tickets")
    op.drop_index("ix_tickets_tenant_id", table_name="tickets")
    op.drop_table("tickets")

    # Handoff for sibling branch d3f4a1b2c5e6: this migration CREATED tickets,
    # but d3f4a1b2c5e6.upgrade() adds ServiceNow columns to it and its
    # downgrade() removes them. Alembic walks merge revision f6a1b2c3d4e5's
    # parents left-to-right, so THIS branch always downgrades first and
    # d3f4a1b2c5e6.downgrade() would hit UndefinedObjectError on a missing
    # tickets table. Recreate a minimal stub carrying exactly the schema that
    # sibling expects so its teardown succeeds.
    op.create_table(
        "tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("servicenow_sys_id", sa.String(length=50), nullable=True),
        sa.Column(
            "servicenow_sync_status",
            sa.String(length=30),
            server_default="pending",
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_tickets_servicenow_sys_id"), "tickets", ["servicenow_sys_id"]
    )
    op.create_index(
        op.f("ix_tickets_servicenow_sync_status"),
        "tickets",
        ["servicenow_sync_status"],
    )

    op.drop_index("ix_knowledge_articles_is_deleted", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_author_id", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_status", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_category", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_article_number", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_tenant_id", table_name="knowledge_articles")
    op.drop_table("knowledge_articles")

    op.drop_index("ix_itsm_connections_is_deleted", table_name="itsm_connections")
    op.drop_index("ix_itsm_connections_is_active", table_name="itsm_connections")
    op.drop_index("ix_itsm_connections_pull_enabled", table_name="itsm_connections")
    op.drop_index("ix_itsm_connections_status", table_name="itsm_connections")
    op.drop_index("ix_itsm_connections_provider_type", table_name="itsm_connections")
    op.drop_index("ix_itsm_connections_provider_id", table_name="itsm_connections")
    op.drop_index("ix_itsm_connections_tenant_id", table_name="itsm_connections")
    op.drop_table("itsm_connections")

    op.drop_index("ix_itsm_providers_is_active", table_name="itsm_providers")
    op.drop_index("ix_itsm_providers_provider_type", table_name="itsm_providers")
    op.drop_constraint("uq_itsm_providers_provider_type", "itsm_providers", type_="unique")
    op.drop_table("itsm_providers")

    # Re-seed default permissions for the new permission codes
    # (The downgrade only drops tables, not permissions rows.)