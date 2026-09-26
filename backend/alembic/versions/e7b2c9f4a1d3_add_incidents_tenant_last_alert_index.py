"""add incidents tenant/last_alert_at composite partial index

Narrow, additive, reversible index-only migration for the Portal incident list
query path.

Measured problem (real database, 2,439,639 incidents of which 2,439,620 belong
to a single tenant): ``IncidentRepository.list(tenant_id, limit=100)`` produced

    Parallel Seq Scan on incidents
      -> Sort (last_alert_at DESC)
      -> Limit 100

because the existing single-column ``ix_incidents_tenant_id`` index is useless
when one tenant owns nearly the whole table, and no index covers the
``ORDER BY last_alert_at DESC``. Measured 1.87-2.21s for the bounded query.

This index lets PostgreSQL walk the requested order directly and stop after the
LIMIT, instead of scanning and sorting ~2.4M rows. No column, table, data or
application behaviour is changed; ``downgrade()`` removes exactly this index.

Index creation is deliberately NOT concurrent: this repository's Alembic
environment assumes transactional DDL and contains no ``autocommit_block`` /
``postgresql_concurrently`` usage anywhere, so a CONCURRENTLY build cannot be
expressed without inventing a new migration pattern. The build therefore takes
a SHARE lock on ``incidents`` for its duration (see remaining limitations).

Revision ID: e7b2c9f4a1d3
Revises: d5f1c8a3e6b9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7b2c9f4a1d3"
down_revision = "d5f1c8a3e6b9"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_incidents_tenant_last_alert_active"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "incidents",
        ["tenant_id", sa.text("last_alert_at DESC")],
        unique=False,
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="incidents")
