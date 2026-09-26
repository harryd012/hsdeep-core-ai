"""Fix tickets.created_at / updated_at missing DB defaults.

The migration that created the `tickets` table declared both timestamp
columns NOT NULL but WITHOUT the `now()` server default, even though the
ORM model (app/models/base.py TimestampMixin) declares
`server_default=func.now()`. Any INSERT that omits these columns (e.g.
TicketService.create_ticket and the autonomous-lifecycle auto-ticket
handler) therefore fails with a NotNullViolationError.

Additive and reversible: only attaches server defaults; no data changes.

Revision ID: b8e3f7a9c1d4
Revises: a7f2c9d4e1b8
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b8e3f7a9c1d4"
down_revision = "a7f2c9d4e1b8"
branch_labels = None
depends_on = None


# Tables whose migrations declared timestamp columns NOT NULL without the
# now() server default that TimestampMixin promises. Column existence varies
# (e.g. sop_versions has created_at but no updated_at), so the migration
# alters only the columns that actually exist.
_TABLES = [
    "approval_history",
    "approval_requests",
    "audit_events",
    "execution_jobs",
    "execution_logs",
    "itsm_connections",
    "itsm_providers",
    "itsm_sync_jobs",
    "knowledge_articles",
    "playbook_steps",
    "playbook_versions",
    "playbooks",
    "rollback_jobs",
    "sop_versions",
    "ticket_attachments",
    "ticket_comments",
    "ticket_history",
    "ticket_links",
    "tickets",
]

_TIMESTAMP_COLUMNS = ("created_at", "updated_at")


def _existing_timestamp_columns() -> list[tuple[str, str]]:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND column_name IN ('created_at', 'updated_at')"
    ))
    pairs = {(r[0], r[1]) for r in rows}
    return [
        (t, c)
        for t in _TABLES
        for c in _TIMESTAMP_COLUMNS
        if (t, c) in pairs
    ]


def upgrade() -> None:
    for table, column in _existing_timestamp_columns():
        op.alter_column(
            table, column,
            server_default=sa.text("now()"),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Restore the pre-migration state (no server defaults).
    for table, column in _existing_timestamp_columns():
        op.alter_column(
            table, column,
            server_default=sa.null(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
