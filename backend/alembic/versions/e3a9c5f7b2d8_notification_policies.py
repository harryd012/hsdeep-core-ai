"""alerting: notification policies

Revision ID: e3a9c5f7b2d8
Revises: c4e8a2d6f1b3
Create Date: 2026-11-09 10:00:00.000000

Observability plan P9 slice 1 (docs/design/OBSERVABILITY_PLATFORM_PLAN.md):
incident routing policies with their own Microsoft Teams webhook. Additive: one
new table, no change to existing tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e3a9c5f7b2d8'
down_revision: Union[str, None] = 'c4e8a2d6f1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notification_policies',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('min_severity', sa.String(length=20), nullable=False, server_default='warning'),
        sa.Column('priorities', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('site_ids', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('channels', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('email_recipients', sa.Text(), nullable=True),
        sa.Column('teams_webhook_encrypted', sa.Text(), nullable=True),
        sa.Column('teams_webhook_hint', sa.String(length=8), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_notification_policies_tenant_name'),
    )
    op.create_index('ix_notification_policies_tenant_id', 'notification_policies', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_notification_policies_tenant_id', table_name='notification_policies')
    op.drop_table('notification_policies')
