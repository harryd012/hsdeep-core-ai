"""backup: Site Recovery replicated items and recovery plans

Revision ID: e6a1c3d8f2b4
Revises: d4f9b2c6e8a1
Create Date: 2026-11-03 23:00:00.000000

Master brief P3, replicated items and disaster recovery
(docs/design/BACKUP_RECOVERY_AND_LOG_ANALYTICS_PLAN.md): the Azure Backup
collector also reads Site Recovery replication protected items and recovery
plans from each Recovery Services vault. Two new tenant-scoped tables. Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e6a1c3d8f2b4'
down_revision: Union[str, None] = 'd4f9b2c6e8a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _common() -> list:
    return [
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('vault_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('external_id', sa.String(length=700), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
    ]


def _tail(table: str) -> list:
    return [
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vault_id'], ['backup_vaults.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('vault_id', 'external_id', name=f'uq_{table}_vault_external'),
    ]


def upgrade() -> None:
    jsonb_list = dict(nullable=False, server_default='[]')
    op.create_table(
        'backup_replicated_items',
        *_common(),
        sa.Column('source_resource_id', sa.String(length=500), nullable=True),
        sa.Column('replication_type', sa.String(length=50), nullable=True),
        sa.Column('protected_item_type', sa.String(length=100), nullable=True),
        sa.Column('source_location', sa.String(length=100), nullable=True),
        sa.Column('target_location', sa.String(length=100), nullable=True),
        sa.Column('protection_state', sa.String(length=60), nullable=True),
        sa.Column('protection_state_description', sa.String(length=255), nullable=True),
        sa.Column('replication_health', sa.String(length=20), nullable=True),
        sa.Column('failover_health', sa.String(length=20), nullable=True),
        sa.Column('active_location', sa.String(length=20), nullable=True),
        sa.Column('test_failover_state', sa.String(length=60), nullable=True),
        sa.Column('last_failover_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_test_failover_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rpo_seconds', sa.BigInteger(), nullable=True),
        sa.Column('rpo_calculated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('policy_name', sa.String(length=255), nullable=True),
        sa.Column('recovery_vm_name', sa.String(length=255), nullable=True),
        sa.Column('current_scenario', sa.String(length=60), nullable=True),
        sa.Column('allowed_operations', postgresql.JSONB(astext_type=sa.Text()), **jsonb_list),
        sa.Column('health_errors', postgresql.JSONB(astext_type=sa.Text()), **jsonb_list),
        *_tail('backup_replicated_items'),
    )
    op.create_index('ix_backup_replicated_items_tenant_id', 'backup_replicated_items', ['tenant_id'])
    op.create_index('ix_backup_replicated_items_vault_id', 'backup_replicated_items', ['vault_id'])

    op.create_table(
        'backup_recovery_plans',
        *_common(),
        sa.Column('primary_location', sa.String(length=100), nullable=True),
        sa.Column('recovery_location', sa.String(length=100), nullable=True),
        sa.Column('failover_deployment_model', sa.String(length=50), nullable=True),
        sa.Column('replication_providers', postgresql.JSONB(astext_type=sa.Text()), **jsonb_list),
        sa.Column('allowed_operations', postgresql.JSONB(astext_type=sa.Text()), **jsonb_list),
        sa.Column('current_scenario', sa.String(length=60), nullable=True),
        sa.Column('current_scenario_status', sa.String(length=60), nullable=True),
        sa.Column('last_planned_failover_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_unplanned_failover_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_test_failover_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('groups', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('item_external_ids', postgresql.JSONB(astext_type=sa.Text()), **jsonb_list),
        *_tail('backup_recovery_plans'),
    )
    op.create_index('ix_backup_recovery_plans_tenant_id', 'backup_recovery_plans', ['tenant_id'])
    op.create_index('ix_backup_recovery_plans_vault_id', 'backup_recovery_plans', ['vault_id'])


def downgrade() -> None:
    op.drop_index('ix_backup_recovery_plans_vault_id', table_name='backup_recovery_plans')
    op.drop_index('ix_backup_recovery_plans_tenant_id', table_name='backup_recovery_plans')
    op.drop_table('backup_recovery_plans')
    op.drop_index('ix_backup_replicated_items_vault_id', table_name='backup_replicated_items')
    op.drop_index('ix_backup_replicated_items_tenant_id', table_name='backup_replicated_items')
    op.drop_table('backup_replicated_items')
