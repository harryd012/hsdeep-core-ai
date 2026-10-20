"""align incident partial indexes with the ORM soft-delete predicate

Narrow, additive, reversible index-only migration for the Command Center brief
(``OperationalIntelligenceService.command_center_brief``) query path.

=== MEASURED PROBLEM 1: five full scans of a 4.8 GB heap per request ===

Real database: 2,701,404 incidents / 5.2 GB, 2,701,401 owned by ONE tenant.
``EXPLAIN (ANALYZE, BUFFERS)`` on the five incident aggregates the brief runs
showed every one choosing

    Parallel Seq Scan on incidents
      Filter: (NOT is_deleted AND tenant_id = ... AND <time/status predicate>)
      Rows Removed by Filter: 900477   (x3 workers = the whole table)
      Buffers: shared read=603727      (~4.7 GB per query)

    hourly_counts(opened_at >= now-24h)      14,019 ms  -> 0 rows
    count(status IN active)                   8,424 ms  -> 2,701,401
    count(resolved_at >= now-24h)             4,152 ms  -> 0 rows
    mean_resolution_minutes(resolved_at >=)   3,145 ms  -> NULL
    count(severity=critical, status IN act)   2,776 ms  -> 1,081,593

The two windowed queries are the clearest waste: they return ZERO rows (the
newest incident here predates the 24h window) yet each still reads the whole
table to discover that. The single-column ``ix_incidents_tenant_id`` /
``ix_incidents_status`` / ``ix_incidents_severity`` indexes are useless because
one tenant owns nearly the whole table and every status is 'open', so neither
column is selective.

=== MEASURED PROBLEM 2: the existing partial indexes were unreachable ===

Adding the obvious partial indexes ``... WHERE is_deleted = false`` did NOT help,
which exposed a latent defect affecting this whole table:

    WHERE is_deleted = false   -> Index Only Scan (1.0 ms)
    WHERE is_deleted IS false  -> Parallel Seq Scan (full table)

SQLAlchemy's ``Incident.is_deleted.is_(False)`` - the convention used 244 times
across ``app/repositories`` - emits ``is_deleted IS false``. PostgreSQL cannot
prove that ``IS false`` implies a partial-index predicate written as ``= false``,
so it refuses EVERY such index. That is why
``ix_incidents_tenant_last_alert_active`` (revision e7b2c9f4a1d3) never actually
served ``IncidentRepository.list``: measured 11,456 ms in the Command Center path
despite that index existing.

Because ``is_deleted`` is ``nullable=False`` (``SoftDeleteMixin``), ``IS false``
and ``= false`` are logically identical - only the planner's predicate proof
differs. The fix is to write the index predicates the way the application
actually queries, not to rewrite 244 call sites.

=== INDEXES ===

All partial on ``is_deleted IS false``; ``tenant_id`` LEADS every one.
Before -> after, each measured with EXPLAIN (ANALYZE, BUFFERS):

    ix_incidents_tenant_opened_active          (tenant_id, opened_at)
        hourly_counts          14,019 ms ->   1.0 ms  Index Only Scan
    ix_incidents_tenant_resolved_active        (tenant_id, resolved_at)
        count(resolved_since)   4,152 ms ->   0.2 ms  Index Only Scan
        mean_resolution_minutes 3,145 ms ->   0.2 ms
    ix_incidents_tenant_status_severity_active (tenant_id, status, severity)
        count(active)+critical  3,336 ms -> 1,560 ms  one Index Only Scan
    ix_incidents_tenant_last_alert_active      (tenant_id, last_alert_at DESC)
        list(...)              11,456 ms ->   0.9 ms  REBUILT, same name/columns

``ix_incidents_tenant_last_alert_active`` is dropped and recreated with the SAME
name and SAME columns - only its predicate changes, so no model, query or other
migration reference breaks. ``downgrade()`` restores it to the exact ``= false``
form revision e7b2c9f4a1d3 created, plus drops the three new ones.

TENANT ISOLATION: unchanged and still enforced in SQL. ``tenant_id`` is a bound
predicate in every query and the leading column of every index, so each plan is
an index-range scan bounded by the authenticated principal's tenant. No filtering
moves to Python and no cross-tenant row becomes reachable.

NO column, table, data, metric semantic or application behaviour is changed.

Index creation is deliberately NOT concurrent, matching revision e7b2c9f4a1d3:
this repository's Alembic environment assumes transactional DDL and contains no
``autocommit_block`` / ``postgresql_concurrently`` usage anywhere, so a
CONCURRENTLY build cannot be expressed without inventing a new migration pattern.
The builds take a SHARE lock on ``incidents`` for their duration - measured
~14.5s, 10.4s, 17.8s and 16.4s on the 2.7M-row table (~59s total), so run this in
a low-write window. Reads are unaffected. Sizes: 18 MB each.

Revision ID: a4c7e2f91b36
Revises: c3f5a1b8d7e2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a4c7e2f91b36"
down_revision = "c3f5a1b8d7e2"
branch_labels = None
depends_on = None

#: Matches what SQLAlchemy's ``Incident.is_deleted.is_(False)`` emits. Written as
#: ``IS false`` (not ``= false``) so PostgreSQL can prove the query predicate
#: implies the index predicate and therefore actually USES these indexes.
PARTIAL_WHERE = sa.text("is_deleted IS false")

#: The form revision e7b2c9f4a1d3 used - restored verbatim by ``downgrade()``.
LEGACY_WHERE = sa.text("is_deleted = false")

LAST_ALERT_INDEX = "ix_incidents_tenant_last_alert_active"
LAST_ALERT_COLUMNS = ["tenant_id", sa.text("last_alert_at DESC")]

#: New indexes: (name, columns).
NEW_INDEXES: tuple[tuple[str, list], ...] = (
    ("ix_incidents_tenant_opened_active", ["tenant_id", "opened_at"]),
    ("ix_incidents_tenant_resolved_active", ["tenant_id", "resolved_at"]),
    (
        "ix_incidents_tenant_status_severity_active",
        ["tenant_id", "status", "severity"],
    ),
)


def upgrade() -> None:
    # Rebuild the pre-existing index so its predicate matches what the ORM
    # emits. Same name, same columns - only the WHERE clause changes.
    op.drop_index(LAST_ALERT_INDEX, table_name="incidents")
    op.create_index(
        LAST_ALERT_INDEX,
        "incidents",
        LAST_ALERT_COLUMNS,
        unique=False,
        postgresql_where=PARTIAL_WHERE,
    )
    for name, columns in NEW_INDEXES:
        op.create_index(
            name,
            "incidents",
            columns,
            unique=False,
            postgresql_where=PARTIAL_WHERE,
        )


def downgrade() -> None:
    # Drop only what upgrade() added, in reverse order.
    for name, _columns in reversed(NEW_INDEXES):
        op.drop_index(name, table_name="incidents")
    # Restore exactly what revision e7b2c9f4a1d3 created.
    op.drop_index(LAST_ALERT_INDEX, table_name="incidents")
    op.create_index(
        LAST_ALERT_INDEX,
        "incidents",
        LAST_ALERT_COLUMNS,
        unique=False,
        postgresql_where=LEGACY_WHERE,
    )

