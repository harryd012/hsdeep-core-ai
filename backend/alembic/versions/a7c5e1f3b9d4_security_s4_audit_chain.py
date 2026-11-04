"""security S4: tamper-evident audit chain

Revision ID: a7c5e1f3b9d4
Revises: b7d2e4f9a1c6
Create Date: 2026-11-04 18:00:00.000000

Enterprise security programme S4 (docs/security/ENTERPRISE_SECURITY_PROGRAMME.md):
audit_chain, a per-tenant SHA-256 hash chain over audit_events. Existing
events are linked by the sealing job (oldest first) after the upgrade.
Additive; audit_events itself is unchanged.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'a7c5e1f3b9d4'
down_revision: Union[str, None] = 'b7d2e4f9a1c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'audit_chain',
        sa.Column('event_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('seq', sa.BigInteger(), nullable=False),
        sa.Column('prev_hash', sa.String(64), nullable=False),
        sa.Column('hash', sa.String(64), nullable=False),
        sa.Column('linked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'seq', name='uq_audit_chain_tenant_seq'),
    )
    op.create_index('ix_audit_chain_tenant_id', 'audit_chain', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_audit_chain_tenant_id', table_name='audit_chain')
    op.drop_table('audit_chain')
