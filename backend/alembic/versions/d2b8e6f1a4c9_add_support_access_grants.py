"""add break-glass support access grants

Revision ID: d2b8e6f1a4c9
Revises: c7f1a9d3e5b2
Create Date: 2026-10-30 23:50:00.000000

support_access_grants: a superuser may act in another tenant (X-Tenant-Id)
only while holding a live grant with a reason and an expiry (R1.2). Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'd2b8e6f1a4c9'
down_revision: Union[str, None] = 'c7f1a9d3e5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'support_access_grants',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_email', sa.String(255), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_email', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_support_access_grants_tenant_id', 'support_access_grants', ['tenant_id'])
    op.create_index('ix_support_access_grants_user_tenant', 'support_access_grants', ['user_id', 'tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_support_access_grants_user_tenant', table_name='support_access_grants')
    op.drop_index('ix_support_access_grants_tenant_id', table_name='support_access_grants')
    op.drop_table('support_access_grants')
