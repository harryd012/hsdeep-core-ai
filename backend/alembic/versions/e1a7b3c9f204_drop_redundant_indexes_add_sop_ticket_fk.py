"""Remove redundant duplicate indexes; add missing sop_executions ticket FK.

Revision ID: e1a7b3c9f204
Revises: d8f3a1c5e207

Scope (both verified by direct pg_index comparison before writing):
  A. DROP 4 redundant duplicate indexes (each is byte-identical to a
     retained index over the same column(s), same table, same btree method,
     same non-unique flag):
       resource_relationships: ix_resource_relationships_source
         == ix_resource_relationships_source_resource_id (source_resource_id)
       resource_relationships: ix_resource_relationships_target
         == ix_resource_relationships_target_resource_id (target_resource_id)
       resource_relationships: ix_resource_relationships_type
         == ix_resource_relationships_relationship_type (relationship_type)
       source_health_audits: ix_source_health_audits_event_type_2 (event_type)
         is redundant - the retained composite index
         ix_source_health_audits_event_type (event_type, created_at) already
         leads on event_type.
     The migration re-verifies each pair at runtime and refuses if the
     "keeper" index is missing (so we can never leave a column unindexed).

  B. ADD the missing FK sop_executions.ticket_id -> tickets.id
     (ON DELETE SET NULL), which the model declares but the database never
     created. Guarded: refuses if orphan ticket_id values exist.

Downgrade: re-creates the 4 dropped indexes and drops the added FK.
"""
from alembic import op
import sqlalchemy as sa

revision = "e1a7b3c9f204"
down_revision = "d8f3a1c5e207"
branch_labels = None
depends_on = None

# (table, redundant_index, keeper_index)
_REDUNDANT_INDEXES = (
    ("resource_relationships", "ix_resource_relationships_source",
     "ix_resource_relationships_source_resource_id"),
    ("resource_relationships", "ix_resource_relationships_target",
     "ix_resource_relationships_target_resource_id"),
    ("resource_relationships", "ix_resource_relationships_type",
     "ix_resource_relationships_relationship_type"),
    ("source_health_audits", "ix_source_health_audits_event_type_2",
     "ix_source_health_audits_event_type"),
)

_FK_NAME = "sop_executions_ticket_id_fkey"


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

    # -- A. drop redundant duplicates only when the keeper exists -----------
    for table, redundant, keeper in _REDUNDANT_INDEXES:
        have = {
            r[0] for r in bind.execute(sa.text(
                "SELECT i.relname FROM pg_index x "
                "JOIN pg_class t ON t.oid=x.indrelid "
                "JOIN pg_class i ON i.oid=x.indexrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                "WHERE n.nspname=:s AND t.relname=:t"), {"s": schema, "t": table})
        }
        if redundant not in have:
            continue
        if keeper not in have:
            raise RuntimeError(
                f"Refusing to drop {redundant}: keeper index {keeper} is missing."
            )
        op.drop_index(redundant, table_name=table, schema=schema)

    # -- B. add the model-declared FK if absent, guarded against orphans -----
    have_fk = bind.scalar(sa.text(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname=:n AND conrelid='sop_executions'::regclass"), {"n": _FK_NAME})
    if not have_fk:
        orphans = bind.scalar(sa.text(
            "SELECT count(*) FROM sop_executions s "
            "WHERE s.ticket_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM tickets t WHERE t.id = s.ticket_id)"))
        if orphans:
            raise RuntimeError(
                f"sop_executions has {orphans} orphan ticket_id rows; FK add refused."
            )
        op.create_foreign_key(
            _FK_NAME, "sop_executions", "tickets",
            ["ticket_id"], ["id"], source_schema=schema,
            referent_schema=schema, ondelete="SET NULL",
        )


def downgrade() -> None:
    bind, schema = _guard()
    have_fk = bind.scalar(sa.text(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname=:n AND conrelid='sop_executions'::regclass"), {"n": _FK_NAME})
    if have_fk:
        op.drop_constraint(_FK_NAME, "sop_executions", type_="foreignkey", schema=schema)

    _recreate = {
        "ix_resource_relationships_source": ("resource_relationships", ["source_resource_id"]),
        "ix_resource_relationships_target": ("resource_relationships", ["target_resource_id"]),
        "ix_resource_relationships_type": ("resource_relationships", ["relationship_type"]),
        "ix_source_health_audits_event_type_2": ("source_health_audits", ["event_type"]),
    }
    for name, (table, cols) in _recreate.items():
        have = {
            r[0] for r in bind.execute(sa.text(
                "SELECT i.relname FROM pg_index x "
                "JOIN pg_class t ON t.oid=x.indrelid "
                "JOIN pg_class i ON i.oid=x.indexrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace "
                "WHERE n.nspname=:s AND t.relname=:t"), {"s": schema, "t": table})
        }
        if name not in have:
            op.create_index(name, table, cols, unique=False, schema=schema)
