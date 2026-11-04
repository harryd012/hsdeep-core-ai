"""dashboards: saved dashboards

Revision ID: b7d2e4f9a1c6
Revises: f6b4d0e2a8c3
Create Date: 2026-11-04 04:00:00.000000

Observability plan P6 slice 2 (docs/design/OBSERVABILITY_PLATFORM_PLAN.md):
named dashboards of registry widgets, private or shared within the tenant.
Additive: one new table, no change to existing tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b7d2e4f9a1c6'
down_revision: Union[str, None] = 'f6b4d0e2a8c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'saved_dashboards',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('widgets', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('shared', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('tenant_id', 'owner_id', 'name', name='uq_saved_dashboards_tenant_owner_name'),
    )
    op.create_index('ix_saved_dashboards_tenant_id', 'saved_dashboards', ['tenant_id'])
    op.create_index('ix_saved_dashboards_owner_id', 'saved_dashboards', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_saved_dashboards_owner_id', table_name='saved_dashboards')
    op.drop_index('ix_saved_dashboards_tenant_id', table_name='saved_dashboards')
    op.drop_table('saved_dashboards')
