"""add backup monitoring (vaults, protected items, jobs) and backup.read

Revision ID: 7826ac6377f8
Revises: c7f1a9d3e5b2
Create Date: 2026-10-30 18:00:00.000000

Azure Backup monitoring, phase B1 (docs/engineering/azure-backup-monitoring.md):
backup_vaults, backup_items and backup_jobs, tenant-scoped, plus the
backup.read permission granted to the Owner, Admin, Operator and Viewer roles.
Additive.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = '7826ac6377f8'
down_revision: Union[str, None] = 'c7f1a9d3e5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CODE = 'backup.read'
_DESCRIPTION = 'View backup vaults, protected items and backup jobs'
_ROLES = ('Owner', 'Admin', 'Operator', 'Viewer')


def _timestamps() -> list:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'backup_vaults',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('device_id', UUID(as_uuid=True), sa.ForeignKey('devices.id', ondelete='SET NULL'), nullable=True),
        sa.Column('monitoring_source_id', UUID(as_uuid=True), sa.ForeignKey('monitoring_sources.id', ondelete='SET NULL'), nullable=True),
        sa.Column('external_id', sa.String(500), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('provider', sa.String(30), server_default='azure', nullable=False),
        sa.Column('location', sa.String(100), nullable=True),
        sa.Column('resource_group', sa.String(255), nullable=True),
        sa.Column('subscription_id', sa.String(100), nullable=True),
        sa.Column('soft_delete_state', sa.String(30), nullable=True),
        sa.Column('immutability_state', sa.String(30), nullable=True),
        sa.Column('storage_redundancy', sa.String(30), nullable=True),
        sa.Column('table_status', JSONB(), server_default='{}', nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint('tenant_id', 'external_id', name='uq_backup_vaults_tenant_external'),
    )
    op.create_index('ix_backup_vaults_tenant_id', 'backup_vaults', ['tenant_id'])

    op.create_table(
        'backup_items',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('vault_id', UUID(as_uuid=True), sa.ForeignKey('backup_vaults.id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.String(700), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('workload_type', sa.String(50), nullable=True),
        sa.Column('protection_state', sa.String(30), nullable=True),
        sa.Column('last_backup_status', sa.String(30), nullable=True),
        sa.Column('last_backup_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source_resource_id', sa.String(500), nullable=True),
        sa.Column('policy_name', sa.String(255), nullable=True),
        sa.Column('health_status', sa.String(30), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint('vault_id', 'external_id', name='uq_backup_items_vault_external'),
    )
    op.create_index('ix_backup_items_tenant_id', 'backup_items', ['tenant_id'])
    op.create_index('ix_backup_items_vault_id', 'backup_items', ['vault_id'])

    op.create_table(
        'backup_jobs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('vault_id', UUID(as_uuid=True), sa.ForeignKey('backup_vaults.id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.String(700), nullable=False),
        sa.Column('item_name', sa.String(255), nullable=True),
        sa.Column('operation', sa.String(50), nullable=True),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('error_code', sa.String(100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint('vault_id', 'external_id', name='uq_backup_jobs_vault_external'),
    )
    op.create_index('ix_backup_jobs_tenant_id', 'backup_jobs', ['tenant_id'])
    op.create_index('ix_backup_jobs_vault_id', 'backup_jobs', ['vault_id'])
    op.create_index('ix_backup_jobs_started_at', 'backup_jobs', ['started_at'])

    bind = op.get_bind()
    exists = bind.execute(sa.text('SELECT 1 FROM permissions WHERE code = :c'), {'c': _CODE}).first()
    if not exists:
        bind.execute(
            sa.text(
                'INSERT INTO permissions (id, code, description, is_deleted) VALUES (:id, :c, :d, false)'
            ),
            {'id': uuid.uuid4(), 'c': _CODE, 'd': _DESCRIPTION},
        )
    bind.execute(sa.text("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code = :c
        WHERE r.name = ANY(:roles)
          AND NOT EXISTS (
              SELECT 1 FROM role_permissions rp
              WHERE rp.role_id = r.id AND rp.permission_id = p.id
          )
    """), {'c': _CODE, 'roles': list(_ROLES)})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("""
        DELETE FROM role_permissions rp
        USING permissions p
        WHERE rp.permission_id = p.id AND p.code = :c
    """), {'c': _CODE})
    bind.execute(sa.text('DELETE FROM permissions WHERE code = :c'), {'c': _CODE})
    op.drop_table('backup_jobs')
    op.drop_table('backup_items')
    op.drop_table('backup_vaults')
