"""add zone_id to devices

Revision ID: c1f2e3d4a5b6
Revises: e5d4c3b2a1f0
Create Date: 2026-09-10 12:00:00.000000

Adds the tenant-scoped zone foreign key to devices so the
Site -> Zone -> Resource hierarchy is fully wired in the schema.
Additive and backward-compatible (nullable column, no backfill).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1f2e3d4a5b6"
down_revision: Union[str, Sequence[str], None] = "e5d4c3b2a1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("zones.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_devices_zone_id", "devices", ["zone_id"])


def downgrade() -> None:
    op.drop_index("ix_devices_zone_id", table_name="devices")
    op.drop_column("devices", "zone_id")