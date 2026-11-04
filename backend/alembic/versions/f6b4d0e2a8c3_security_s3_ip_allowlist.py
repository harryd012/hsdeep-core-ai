"""security S3: per-tenant IP allowlist

Revision ID: f6b4d0e2a8c3
Revises: e5a3c9d1f7b2
Create Date: 2026-11-04 14:00:00.000000

Enterprise security programme S3 (docs/security/ENTERPRISE_SECURITY_PROGRAMME.md):
tenant_ip_rules, the networks a tenant's users and API keys may connect from.
No rules = no restriction, so existing tenants are unaffected. Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'f6b4d0e2a8c3'
down_revision: Union[str, None] = 'e5a3c9d1f7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_ip_rules',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('cidr', sa.String(64), nullable=False),
        sa.Column('label', sa.String(100), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'cidr', name='uq_tenant_ip_rules_tenant_cidr'),
    )
    op.create_index('ix_tenant_ip_rules_tenant_id', 'tenant_ip_rules', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_tenant_ip_rules_tenant_id', table_name='tenant_ip_rules')
    op.drop_table('tenant_ip_rules')
