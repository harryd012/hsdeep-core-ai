"""sensors: lifecycle (pause / disable), description and user thresholds

Revision ID: f2b7d9e4a6c1
Revises: e6a1c3d8f2b4
Create Date: 2026-11-04 01:00:00.000000

Observability plan P1 (docs/design/OBSERVABILITY_PLATFORM_PLAN.md): a user can
pause or disable a sensor and set its own thresholds, which survive every
sync. Additive; existing sensors are active with provider thresholds.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2b7d9e4a6c1'
down_revision: Union[str, None] = 'e6a1c3d8f2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sensors', sa.Column('admin_state', sa.String(length=20), nullable=False, server_default='active'))
    op.add_column('sensors', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('sensors', sa.Column('threshold_source', sa.String(length=20), nullable=False, server_default='provider'))
    op.add_column('sensors', sa.Column('threshold_direction', sa.String(length=10), nullable=True))
    op.create_index('ix_sensors_admin_state', 'sensors', ['admin_state'])


def downgrade() -> None:
    op.drop_index('ix_sensors_admin_state', table_name='sensors')
    op.drop_column('sensors', 'threshold_direction')
    op.drop_column('sensors', 'threshold_source')
    op.drop_column('sensors', 'description')
    op.drop_column('sensors', 'admin_state')
