"""backup: vault security settings and daily storage snapshots

Revision ID: d4f9b2c6e8a1
Revises: c3e8a1f5d7b9
Create Date: 2026-11-03 21:00:00.000000

BR-1 Backup Command Center (docs/design/BACKUP_RECOVERY_AND_LOG_ANALYTICS_PLAN.md):
the security posture view needs multi-user authorization, the encryption key
type, cross-region restore and public network access per storage location,
and storage analytics needs history, so each poll records the day's storage
per location. Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd4f9b2c6e8a1'
down_revision: Union[str, None] = 'c3e8a1f5d7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('backup_vaults', sa.Column('multi_user_authorization', sa.String(length=20), nullable=True))
    op.add_column('backup_vaults', sa.Column('encryption', sa.String(length=30), nullable=True))
    op.add_column('backup_vaults', sa.Column('cross_region_restore', sa.Boolean(), nullable=True))
    op.add_column('backup_vaults', sa.Column('public_network_access', sa.String(length=20), nullable=True))
    op.create_table(
        'backup_storage_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('vault_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('storage_used_bytes', sa.BigInteger(), nullable=True),
        sa.Column('capacity_bytes', sa.BigInteger(), nullable=True),
        sa.Column('free_bytes', sa.BigInteger(), nullable=True),
        sa.Column('items_total', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vault_id'], ['backup_vaults.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('vault_id', 'day', name='uq_backup_storage_snapshots_vault_day'),
    )
    op.create_index('ix_backup_storage_snapshots_tenant_id', 'backup_storage_snapshots', ['tenant_id'])
    op.create_index('ix_backup_storage_snapshots_vault_id', 'backup_storage_snapshots', ['vault_id'])


def downgrade() -> None:
    op.drop_index('ix_backup_storage_snapshots_vault_id', table_name='backup_storage_snapshots')
    op.drop_index('ix_backup_storage_snapshots_tenant_id', table_name='backup_storage_snapshots')
    op.drop_table('backup_storage_snapshots')
    op.drop_column('backup_vaults', 'public_network_access')
    op.drop_column('backup_vaults', 'cross_region_restore')
    op.drop_column('backup_vaults', 'encryption')
    op.drop_column('backup_vaults', 'multi_user_authorization')
