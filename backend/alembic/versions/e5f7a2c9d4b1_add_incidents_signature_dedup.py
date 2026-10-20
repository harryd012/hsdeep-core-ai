"""add incident_signature + unique partial index for incident dedup

Adds a nullable ``incident_signature`` column and a unique partial index
``uq_incidents_open_signature`` on ``(tenant_id, incident_signature)`` WHERE
``status = 'open' AND incident_signature IS NOT NULL AND is_deleted IS false``.

This is the DB-enforced backstop for incident dedup: CorrelationEngine now
writes a normalized signature, and concurrent workers can no longer create two
open incidents with the same (tenant, signature).

No backfill: historical rows get NULL signatures and are deduplicated by the
separate cleanup script, never by this migration.

Predicate spelling ``is_deleted IS false`` is load-bearing (matches the ORM's
``is_deleted.is_(False)``); a ``= false`` predicate would silently never be used.

Revision ID: e5f7a2c9d4b1
Revises: d7f2e9a1c4b6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f7a2c9d4b1"
down_revision = "d7f2e9a1c4b6"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_incidents_open_signature"


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("incident_signature", sa.String(255), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("closure_reason", sa.Text(), nullable=True),
    )
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
                "ON incidents (tenant_id, incident_signature) "
                "WHERE status = 'open' AND incident_signature IS NOT NULL "
                "AND is_deleted IS false"
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
    op.drop_column("incidents", "closure_reason")
    op.drop_column("incidents", "incident_signature")
