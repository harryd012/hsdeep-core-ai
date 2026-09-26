"""Surgical follow-up: drop signed-off legacy columns; harden timestamps.

Revision ID: d8f3a1c5e207
Revises: c4d9a2b7e105
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d8f3a1c5e207"
down_revision = "c4d9a2b7e105"
branch_labels = None
depends_on = None

_LEGACY_SITES_COLS = ("floor", "site_owner", "support_contact", "timezone")
_LEGACY_TL_COLS = ("first_seen_at", "last_seen_at", "metadata")

_MISSING_INDEXES = (
    ("ai_diagnoses", "ix_ai_diagnoses_is_deleted", ["is_deleted"]),
    ("change_requests", "ix_change_requests_is_deleted", ["is_deleted"]),
    ("integrations", "ix_integrations_is_deleted", ["is_deleted"]),
    ("knowledge_articles", "ix_knowledge_articles_source_incident_id", ["source_incident_id"]),
    ("knowledge_articles", "ix_knowledge_articles_source_ticket_id", ["source_ticket_id"]),
    ("normalized_events", "ix_normalized_events_correlation_id", ["correlation_id"]),
    ("normalized_events", "ix_normalized_events_event_signature", ["event_signature"]),
    ("normalized_events", "ix_normalized_events_is_deleted", ["is_deleted"]),
    ("normalized_events", "ix_normalized_events_source_event_id", ["source_event_id"]),
    ("normalized_events", "uq_normalized_events_active_per_signature",
     ["integration_id", "event_signature"]),
    ("playbooks", "ix_playbooks_device_type", ["device_type"]),
    ("playbooks", "ix_playbooks_playbook_number", ["playbook_number"]),
    ("playbooks", "ix_playbooks_sop_id", ["sop_id"]),
    ("resource_relationships", "ix_resource_relationships_is_deleted", ["is_deleted"]),
    ("resource_relationships", "ix_resource_relationships_relationship_type",
     ["relationship_type"]),
    ("resource_relationships", "ix_resource_relationships_source_resource_id",
     ["source_resource_id"]),
    ("resource_relationships", "ix_resource_relationships_target_resource_id",
     ["target_resource_id"]),
    ("zones", "ix_zones_is_deleted", ["is_deleted"]),
)


def _guard():
    if op.get_context().as_sql:
        raise RuntimeError("Online schema inspection is required for this repair.")
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("This reconciliation requires PostgreSQL.")
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not schema:
        raise RuntimeError("No effective schema; refusing schema repair.")
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    return bind, schema


def upgrade() -> None:
    bind, schema = _guard()
    insp = sa.inspect(bind)

    # -- A. signed-off legacy drops (raise on any live data) ----------------
    sites_cols = {c["name"] for c in insp.get_columns("sites", schema=schema)}
    for col in _LEGACY_SITES_COLS:
        if col not in sites_cols:
            continue
        n = bind.scalar(sa.text(f"SELECT count(*) FROM sites WHERE {col} IS NOT NULL"))
        if n:
            raise RuntimeError(f"sites.{col} carries {n} non-null rows; sign-off void.")
        op.drop_column("sites", col, schema=schema)
    tl_cols = {c["name"] for c in insp.get_columns("topology_links", schema=schema)}
    for col in ("first_seen_at", "last_seen_at"):
        if col not in tl_cols:
            continue
        n = bind.scalar(sa.text(f"SELECT count(*) FROM topology_links WHERE {col} IS NOT NULL"))
        if n:
            raise RuntimeError(f"topology_links.{col} carries {n} rows; sign-off void.")
        op.drop_column("topology_links", col, schema=schema)
    if "metadata" in tl_cols:
        n = bind.scalar(sa.text(
            "SELECT count(*) FROM topology_links "
            "WHERE metadata IS NOT NULL AND metadata::text <> '{}'"))
        if n:
            raise RuntimeError(f"topology_links.metadata carries {n} rows; sign-off void.")
        op.drop_column("topology_links", "metadata", schema=schema)

    # -- B. timestamp hardening ----------------------------------------------
    op.execute("UPDATE topology_links SET created_at = now() WHERE created_at IS NULL")
    op.execute("UPDATE topology_links SET updated_at = now() WHERE updated_at IS NULL")
    op.alter_column(
        "topology_links", "created_at", existing_type=sa.DateTime(timezone=True),
        nullable=False, server_default=sa.text("now()"),
        existing_nullable=True, schema=schema,
    )
    op.alter_column(
        "topology_links", "updated_at", existing_type=sa.DateTime(timezone=True),
        nullable=False, server_default=sa.text("now()"),
        existing_nullable=True, schema=schema,
    )

    # -- C. missing model indexes (additive, idempotent) ----------------------
    for table, name, cols in _MISSING_INDEXES:
        live = {i["name"] for i in insp.get_indexes(table, schema=schema)}
        if name in live:
            continue
        unique = name.startswith("uq_")
        op.create_index(name, table, cols, unique=unique, schema=schema)


def downgrade() -> None:
    bind, schema = _guard()
    op.alter_column(
        "topology_links", "updated_at", existing_type=sa.DateTime(timezone=True),
        nullable=True, server_default=None,
        existing_nullable=False, schema=schema,
    )
    op.alter_column(
        "topology_links", "created_at", existing_type=sa.DateTime(timezone=True),
        nullable=True, server_default=None,
        existing_nullable=False, schema=schema,
    )
    insp = sa.inspect(bind)
    tl_cols = {c["name"] for c in insp.get_columns("topology_links", schema=schema)}
    if "metadata" not in tl_cols:
        op.add_column(
            "topology_links",
            sa.Column("metadata", postgresql.JSONB(), nullable=True),
            schema=schema,
        )
    for col in ("last_seen_at", "first_seen_at"):
        if col not in tl_cols:
            op.add_column(
                "topology_links",
                sa.Column(col, sa.DateTime(timezone=True), nullable=True),
                schema=schema,
            )
    sites_cols = {c["name"] for c in insp.get_columns("sites", schema=schema)}
    _lengths = {"floor": 50, "site_owner": 255, "support_contact": 255, "timezone": 50}
    for col in ("timezone", "support_contact", "site_owner", "floor"):
        if col not in sites_cols:
            op.add_column(
                "sites", sa.Column(col, sa.String(_lengths[col]), nullable=True),
                schema=schema,
            )
