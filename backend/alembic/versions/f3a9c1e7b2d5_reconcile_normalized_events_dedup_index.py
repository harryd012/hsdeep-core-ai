"""reconcile normalized_events dedup index to the model's partial predicate

Replaces the *non-partial* unique index ``uq_normalized_events_active_per_signature``
(created without a WHERE predicate by migration ``d8f3a1c5e207``) with the
*partial* unique index declared by the ORM model
(``app/models/normalized_event.py::NormalizedEvent.__table_args__``):

    UNIQUE (integration_id, event_signature)
    WHERE is_suppressed = false AND event_signature IS NOT NULL AND is_deleted = false

Why: the live index is non-partial (model/DB drift). A non-partial unique index on
``(integration_id, event_signature)`` is *stricter* than intended — once any row
exists for a signature, a re-fire after suppression/soft-delete can never insert a
fresh active event (it violates uniqueness), silently breaking legitimate
re-creation. The partial predicate restores "at most one ACTIVE event per
signature" and is exactly what ``NormalizedEventRepository.upsert_occurrence()``'s
``INSERT ... ON CONFLICT`` clause targets.

Predicate spelling is load-bearing and copied verbatim from the model's
``postgresql_where``; the ON CONFLICT arbiter inference in the repository and the
index created here must agree on this exact predicate or the upsert cannot
resolve its arbiter index.

Safety: the new index is built under a temporary name FIRST and the old index is
dropped only afterwards, so there is never a window with no uniqueness guarantee.
Because the existing non-partial index is a strict superset of the partial
predicate, the partial CREATE cannot fail on duplicate rows while the DB is
consistent, and a failed CREATE leaves the old index intact.

Rollback (downgrade) constraint: re-creating the *non-partial* index can fail if
any suppressed/soft-deleted row shares a signature with an active row (allowed by
the partial index, rejected by the non-partial one). Clean those up before
downgrading.

Revision ID: f3a9c1e7b2d5
Revises: 6af42cfe9d48
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a9c1e7b2d5"
down_revision = "6af42cfe9d48"
branch_labels = None
depends_on = None

FINAL_INDEX = "uq_normalized_events_active_per_signature"
TEMP_INDEX = "uq_normalized_events_active_per_signature_p1"

# Verbatim from app/models/normalized_event.py NormalizedEvent.__table_args__
PARTIAL_PREDICATE = (
    "is_suppressed = false AND event_signature IS NOT NULL AND is_deleted = false"
)


def upgrade() -> None:
    # CONCURRENTLY cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        # 1) Build the partial index under a temporary name (old index untouched).
        op.execute(
            sa.text(
                f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {TEMP_INDEX} "
                f"ON normalized_events (integration_id, event_signature) "
                f"WHERE {PARTIAL_PREDICATE}"
            )
        )
        # 2) Drop the stale non-partial index (dedup still enforced by TEMP_INDEX).
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {FINAL_INDEX}"))
        # 3) Rename into the model-declared name so model == DB.
        op.execute(sa.text(f"ALTER INDEX {TEMP_INDEX} RENAME TO {FINAL_INDEX}"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        # Recreate the non-partial index under a temp name, then swap.
        # NOTE: may fail if suppressed/deleted rows share a signature with an
        # active row (see module docstring) - clean those up before downgrading.
        op.execute(
            sa.text(
                f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {TEMP_INDEX} "
                f"ON normalized_events (integration_id, event_signature)"
            )
        )
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {FINAL_INDEX}"))
        op.execute(sa.text(f"ALTER INDEX {TEMP_INDEX} RENAME TO {FINAL_INDEX}"))
