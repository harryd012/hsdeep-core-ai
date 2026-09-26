"""add api_key scopes column and merge migration heads

Revision ID: f6a1b2c3d4e5
Revises: c3d4e5f6a7b8, d3f4a1b2c5e6
Create Date: 2026-08-11 10:00:00.000000

Settings/API-Keys sprint: adds a `scopes` JSONB column to `api_keys` so each
key can carry a subset of permission codes (matching the RBAC permission
catalog). Also merges the two divergent migration heads (SLA engine and
lifecycle automation) into a single linear history.

The column is additive and backward-compatible: existing rows get an empty
array via server_default '[]'.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f6a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = ("c3d4e5f6a7b8", "d3f4a1b2c5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "scopes")