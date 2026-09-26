"""widen incident status column to String(30)

Revision ID: abc1d2e3f4a5
Revises: f6a1b2c3d4e5
Create Date: 2026-08-16 12:38:00.000000

Phase 1 Lifecycle State Machine Extension: widens `incidents.status` from
String(20) to String(30) so the column can hold the superset of lifecycle
states (correlated, prioritized, rca_ready, remediation_pending,
remediating, validating, escalated, waiting_vendor, waiting_customer,
waiting_change, failed_remediation, reopened) in addition to the existing
Sprint 15 set.

Purely a column-width change: non-destructive, no data change, no default
change, and no other column is touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "abc1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "f6a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "incidents",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=30),
        existing_nullable=False,
        existing_server_default=sa.text("'open'::character varying"),
    )


def downgrade() -> None:
    op.alter_column(
        "incidents",
        "status",
        existing_type=sa.String(length=30),
        type_=sa.String(length=20),
        existing_nullable=False,
        existing_server_default=sa.text("'open'::character varying"),
    )