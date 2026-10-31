"""backup: policies, vault storage used, item restore points

Revision ID: a4c8e2f6b1d3
Revises: f3d6a8c2e4b7
Create Date: 2026-10-31 20:00:00.000000

Backup phase 1 (docs/engineering/azure-backup-monitoring.md): the policy
behind each protected item (schedule and retention per tier), the backup
storage each vault reports, and each item's restore point count and oldest
restore point. Additive: new table and nullable columns only.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = 'a4c8e2f6b1d3'
down_revision: Union[str, None] = 'f3d6a8c2e4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('backup_vaults', sa.Column('storage_used_bytes', sa.BigInteger(), nullable=True))
    op.add_column('backup_items', sa.Column('recovery_point_count', sa.Integer(), nullable=True))
    op.add_column('backup_items', sa.Column('oldest_recovery_point_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'backup_policies',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('vault_id', UUID(as_uuid=True), sa.ForeignKey('backup_vaults.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('workload_type', sa.String(50), nullable=True),
        sa.Column('schedule_frequency', sa.String(20), nullable=True),
        sa.Column('schedule_times', JSONB(), server_default='[]', nullable=False),
        sa.Column('daily_retention_days', sa.Integer(), nullable=True),
        sa.Column('weekly_retention_weeks', sa.Integer(), nullable=True),
        sa.Column('monthly_retention_months', sa.Integer(), nullable=True),
        sa.Column('yearly_retention_years', sa.Integer(), nullable=True),
        sa.Column('protected_items_count', sa.Integer(), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('vault_id', 'name', name='uq_backup_policies_vault_name'),
    )
    op.create_index('ix_backup_policies_tenant_id', 'backup_policies', ['tenant_id'])
    op.create_index('ix_backup_policies_vault_id', 'backup_policies', ['vault_id'])


def downgrade() -> None:
    op.drop_index('ix_backup_policies_vault_id', table_name='backup_policies')
    op.drop_index('ix_backup_policies_tenant_id', table_name='backup_policies')
    op.drop_table('backup_policies')
    op.drop_column('backup_items', 'oldest_recovery_point_at')
    op.drop_column('backup_items', 'recovery_point_count')
    op.drop_column('backup_vaults', 'storage_used_bytes')
