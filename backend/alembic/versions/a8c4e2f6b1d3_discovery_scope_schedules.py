"""discovery: scheduled scope probes (next_run_at, probe job trigger)

Revision ID: a8c4e2f6b1d3
Revises: f2b7d9e4a6c1
Create Date: 2026-11-03 18:00:00.000000

Observability plan P2 (docs/design/OBSERVABILITY_PLATFORM_PLAN.md): a scope
with a schedule is probed by the scheduler. Additive; existing scopes have no
schedule and existing probe jobs were all started by hand.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a8c4e2f6b1d3'
down_revision: Union[str, None] = 'f2b7d9e4a6c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('discovery_scopes', sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_discovery_scopes_next_run_at', 'discovery_scopes', ['next_run_at'])
    op.add_column(
        'discovery_probe_jobs',
        sa.Column('trigger', sa.String(length=20), nullable=False, server_default='manual'),
    )


def downgrade() -> None:
    op.drop_column('discovery_probe_jobs', 'trigger')
    op.drop_index('ix_discovery_scopes_next_run_at', table_name='discovery_scopes')
    op.drop_column('discovery_scopes', 'next_run_at')
