"""add incident cleanup bookkeeping tables

Revision ID: f3d6a8c2e4b7
Revises: e9a3c5d7b1f2
Create Date: 2026-10-31 00:10:00.000000

scripts/cleanup_duplicate_incidents.py records undo data, pending work and
relinked references in three tables. It used to create them at run time,
which needs CREATE on the schema (the CI/test role and a least-privilege
application role do not have it). They are now created here, idempotently,
so databases where the script already created them are unaffected.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f3d6a8c2e4b7'
down_revision: Union[str, None] = 'e9a3c5d7b1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UP = (
    """
    CREATE TABLE IF NOT EXISTS incident_cleanup_undo (
        id UUID PRIMARY KEY,
        incident_id UUID NOT NULL UNIQUE,
        tenant_id UUID NOT NULL,
        previous_status VARCHAR(20) NOT NULL,
        archived_at TIMESTAMPTZ NOT NULL,
        reason TEXT NOT NULL
    )
    """,
    "ALTER TABLE incident_cleanup_undo ADD COLUMN IF NOT EXISTS run_id UUID",
    "ALTER TABLE incident_cleanup_undo ADD COLUMN IF NOT EXISTS keeper_id UUID",
    "CREATE INDEX IF NOT EXISTS ix_incident_cleanup_undo_run ON incident_cleanup_undo (run_id)",
    """
    CREATE TABLE IF NOT EXISTS incident_cleanup_pending (
        incident_id UUID PRIMARY KEY,
        run_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        keeper_id UUID NOT NULL,
        previous_status VARCHAR(20) NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_incident_cleanup_pending_run ON incident_cleanup_pending (run_id)",
    """
    CREATE TABLE IF NOT EXISTS incident_cleanup_relinks (
        run_id UUID NOT NULL,
        table_name TEXT NOT NULL,
        column_name TEXT NOT NULL,
        row_id UUID NOT NULL,
        from_incident_id UUID NOT NULL,
        to_incident_id UUID NOT NULL,
        PRIMARY KEY (run_id, table_name, column_name, row_id)
    )
    """,
)


def upgrade() -> None:
    for ddl in _UP:
        op.execute(ddl)


def downgrade() -> None:
    # Drops the cleanup undo history: downgrade only on throwaway databases.
    op.execute("DROP TABLE IF EXISTS incident_cleanup_relinks")
    op.execute("DROP TABLE IF EXISTS incident_cleanup_pending")
    op.execute("DROP TABLE IF EXISTS incident_cleanup_undo")
