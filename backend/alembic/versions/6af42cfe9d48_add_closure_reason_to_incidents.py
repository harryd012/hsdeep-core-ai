"""add closure_reason to incidents (drift remediation)

The prior migration e5f7a2c9d4b1 was applied to the dev database before the
``closure_reason`` column was added to it, leaving the dev DB stamped at
e5f7a2c9d4b1 but missing ``closure_reason``. This migration backfills that gap
idempotently: on the drifted dev DB it adds the column; on a fresh database
(where e5f7a2c9d4b1 already created it) the ``IF NOT EXISTS`` makes this a no-op.

Revision ID: 6af42cfe9d48
Revises: e5f7a2c9d4b1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6af42cfe9d48"
down_revision = "e5f7a2c9d4b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS closure_reason TEXT"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE incidents DROP COLUMN IF EXISTS closure_reason"))
