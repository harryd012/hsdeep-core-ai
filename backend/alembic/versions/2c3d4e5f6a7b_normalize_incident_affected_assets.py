"""normalize incident affected_assets from dict to list

Revision ID: 2c3d4e5f6a7b
Revises: 1be605e3f990
Create Date: 2026-07-31 08:30:00.000000

Production Hardening — Bug 1 fix for ResponseValidationError on
GET /api/incidents.

The Pydantic schema declared ``affected_assets: dict`` while the DB
column (and the correlation engine's intent) stores a *list* of asset
dicts.  Every incident row was written with the ``server_default='[]'``
(list) value, so FastAPI's response validation rejected them with
``ResponseValidationError`` (``Input should be a valid dictionary``).

This migration normalizes any existing rows that were written by the old
correlation code path (which wrapped the list in ``{"assets": [...]}``)
or that somehow ended up as empty objects ``{}`` — converting them all
to the canonical list shape ``[...]``.

No schema column change is required — only a data backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c3d4e5f6a7b"
down_revision: Union[str, None] = "1be605e3f990"
branch_labels: Union[str, Sequence[str] | None] = None
depends_on: Union[str, Sequence[str] | None] = None


def upgrade() -> None:
    # Normalize affected_assets JSONB column on incidents table.
    #
    # Three cases to handle:
    #   1. {"assets": [...]}  -> [...]   (old correlation engine wrapper)
    #   2. {}                 -> []       (empty dict -> empty list)
    #   3. [...]              -> [...]     (already correct, no change)
    #
    # Only rows whose value is a JSON *object* (dict) need fixing;
    # rows that are already JSON arrays are left untouched.
    op.execute(
        sa.text(
            """
            UPDATE incidents
            SET affected_assets = CASE
                WHEN jsonb_typeof(affected_assets) = 'object'
                     AND affected_assets ? 'assets' THEN affected_assets->'assets'
                WHEN jsonb_typeof(affected_assets) = 'object'
                     AND affected_assets = '{}'::jsonb THEN '[]'::jsonb
                ELSE affected_assets
            END
            WHERE jsonb_typeof(affected_assets) = 'object';
            """
        )
    )


def downgrade() -> None:
    # This migration only normalizes data shape; there is no safe
    # "undo" for data that may have been lost (e.g. an empty dict {}
    # cannot be reliably distinguished from a pre-existing empty list).
    # In practice this migration is always paired with the schema/model
    # change that makes list the canonical type, so downgrade is a no-op.
    pass
