"""add covering index for active-incident aggregates (ECC-1 aging + site health)

Adds ``ix_incidents_tenant_status_site_active`` on
``incidents (tenant_id, status, site_id, severity) INCLUDE (opened_at, resolved_at)``
with the partial predicate ``is_deleted IS false``.

Serves two Executive Command Center reads that both aggregate the ACTIVE
incident set (open + acknowledged + investigating):

  * ``IncidentAnalyticsRepository.open_aging`` - needs ``opened_at`` and
    ``resolved_at`` for every active row. Measured 45.4s warm on the
    ~2.7M-incident dev tenant (2.67M active rows) because both columns were
    read from the heap.
  * ``SiteHealthRepository.active_incidents_by_site`` - GROUP BY ``site_id``
    over the active set; ~40.9s warm before this index.

With every referenced column in the key or INCLUDE list, PostgreSQL can answer
both with an index-only scan. Predicate spelled ``is_deleted IS false`` to match
what the repositories emit (see migration a4c7e2f91b36). Built CONCURRENTLY,
like d7f2e9a1c4b6, so the incidents table is not locked for the build.

Revision ID: e8c1d4f7a2b9
Revises: 7ba0fc4d636e
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8c1d4f7a2b9"
down_revision = "7ba0fc4d636e"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_incidents_tenant_status_site_active"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{INDEX_NAME} ON incidents (tenant_id, status, site_id, severity) "
                "INCLUDE (opened_at, resolved_at) "
                "WHERE is_deleted IS false"
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
