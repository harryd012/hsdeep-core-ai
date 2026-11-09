"""alerting: sensor baselines

Revision ID: f4b1d7e9c3a5
Revises: e3a9c5f7b2d8
Create Date: 2026-11-09 12:00:00.000000

Observability plan P9 slice 2 (docs/design/OBSERVABILITY_PLATFORM_PLAN.md):
opt-in dynamic baselines per sensor. Additive: one new table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f4b1d7e9c3a5'
down_revision: Union[str, None] = 'e3a9c5f7b2d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sensor_baselines',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sensor_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sensors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('direction', sa.String(length=10), nullable=False, server_default='both'),
        sa.Column('sensitivity', sa.Float(), nullable=False, server_default='3'),
        sa.Column('stats', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False, server_default='learning'),
        sa.Column('last_value', sa.Float(), nullable=True),
        sa.Column('last_expected', sa.Float(), nullable=True),
        sa.Column('last_z', sa.Float(), nullable=True),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('sensor_id', name='uq_sensor_baselines_sensor_id'),
    )
    op.create_index('ix_sensor_baselines_tenant_id', 'sensor_baselines', ['tenant_id'])
    op.create_index('ix_sensor_baselines_enabled', 'sensor_baselines', ['enabled'])


def downgrade() -> None:
    op.drop_index('ix_sensor_baselines_enabled', table_name='sensor_baselines')
    op.drop_index('ix_sensor_baselines_tenant_id', table_name='sensor_baselines')
    op.drop_table('sensor_baselines')
