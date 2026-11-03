"""inventory: host saved views

Revision ID: c3e9a1f7d2b5
Revises: a8c4e2f6b1d3
Create Date: 2026-11-03 21:00:00.000000

Observability plan P2 slice 2 (docs/design/OBSERVABILITY_PLATFORM_PLAN.md):
named Hosts-explorer filter sets, private or shared within the tenant.
Additive: one new table, no change to existing tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c3e9a1f7d2b5'
down_revision: Union[str, None] = 'a8c4e2f6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'host_saved_views',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('filters', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('layout', sa.String(length=20), nullable=False, server_default='health'),
        sa.Column('shared', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('tenant_id', 'owner_id', 'name', name='uq_host_saved_views_tenant_owner_name'),
    )
    op.create_index('ix_host_saved_views_tenant_id', 'host_saved_views', ['tenant_id'])
    op.create_index('ix_host_saved_views_owner_id', 'host_saved_views', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_host_saved_views_owner_id', table_name='host_saved_views')
    op.drop_index('ix_host_saved_views_tenant_id', table_name='host_saved_views')
    op.drop_table('host_saved_views')
