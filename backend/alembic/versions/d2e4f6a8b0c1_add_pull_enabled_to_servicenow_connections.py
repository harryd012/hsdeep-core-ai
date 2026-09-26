"""add pull_enabled to servicenow_connections

Revision ID: d2e4f6a8b0c1
Revises: a7c4e2f19d6b
Create Date: 2026-07-19 10:00:00.000000

Phase 2 of ServiceNow Enterprise Integration — per-connection pull control.
Adds pull_enabled boolean column to servicenow_connections with a safe
server_default of TRUE so existing rows continue pulling by default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd2e4f6a8b0c1'
down_revision: Union[str, None] = 'a7c4e2f19d6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'servicenow_connections',
        sa.Column('pull_enabled', sa.Boolean(), server_default='true', nullable=False, default=True),
    )
    op.create_index(
        op.f('ix_servicenow_connections_pull_enabled'),
        'servicenow_connections',
        ['pull_enabled'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_servicenow_connections_pull_enabled'), table_name='servicenow_connections')
    op.drop_column('servicenow_connections', 'pull_enabled')
