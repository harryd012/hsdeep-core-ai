"""add audit_events timestamp server defaults

Revision ID: a7f2c9d4e1b8
Revises: 517f91edd082
Create Date: 2026-08-26

The `audit_events` table was created (migration b2c3d4e5f6a7) with NOT NULL
created_at/updated_at but NO server defaults, while the ORM model declares
`server_default=func.now()` for every TimestampMixin table. Every AuditService
INSERT therefore failed with a NotNullViolation on created_at, silently
dropping the entire audit trail. This migration aligns the DB with the model.

Additive and reversible: only column defaults are altered; no data changes.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f2c9d4e1b8"
down_revision: Union[str, Sequence[str], None] = "517f91edd082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "audit_events", "created_at",
        server_default=op.f("now()"),
        existing_nullable=False,
        existing_type="timestamp with time zone",
    )
    op.alter_column(
        "audit_events", "updated_at",
        server_default=op.f("now()"),
        existing_nullable=False,
        existing_type="timestamp with time zone",
    )


def downgrade() -> None:
    op.alter_column(
        "audit_events", "updated_at",
        server_default=None,
        existing_nullable=False,
        existing_type="timestamp with time zone",
    )
    op.alter_column(
        "audit_events", "created_at",
        server_default=None,
        existing_nullable=False,
        existing_type="timestamp with time zone",
    )
