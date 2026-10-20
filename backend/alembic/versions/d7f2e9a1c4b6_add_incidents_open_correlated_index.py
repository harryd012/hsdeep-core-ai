"""add partial index for the correlated incident count (intelligence endpoint)

Adds ``ix_incidents_tenant_open_correlated`` on ``incidents (tenant_id)`` with the
partial predicate ``status = 'open' AND alert_count > 1 AND is_deleted IS false``.

Serves ``IncidentRepository._correlated_count`` (and therefore
``GET /api/incidents/intelligence``), which previously forced a full ~36.8s scan
of the ~2.7M-row tenant because the ``correlated`` filter reads the un-indexed
``alert_count`` column for every row.

Predicate spelling: ``is_deleted IS false`` (NOT ``= false``) is load-bearing —
repositories filter with ``Incident.is_deleted.is_(False)``, which SQLAlchemy
renders as ``is_deleted IS false``, and PostgreSQL cannot prove that ``IS false``
implies a ``= false`` partial-index predicate (see migration a4c7e2f91b36).

CONCURRENTLY: unlike prior incident migrations (transactional DDL, e.g.
a4c7e2f91b36), this one uses ``CREATE INDEX CONCURRENTLY`` so the build does not
take a SHARE lock on incidents for its duration (the table is ~5.2 GB). This is
the first CONCURRENTLY migration in the repository; it uses the Alembic
``autocommit_block`` context (Alembic 1.13.2).

Revision ID: d7f2e9a1c4b6
Revises: b3e5c8f2a7d4
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d7f2e9a1c4b6"
down_revision = "b3e5c8f2a7d4"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_incidents_tenant_open_correlated"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{INDEX_NAME} ON incidents (tenant_id) "
                "WHERE status = 'open' AND alert_count > 1 AND is_deleted IS false"
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
