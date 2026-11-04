"""backup: provider-neutral model and per-tenant RPO

Revision ID: b7d2f4a9c1e6
Revises: a4c8e2f6b1d3
Create Date: 2026-11-03 12:00:00.000000

Backup phase 2 (docs/engineering/azure-backup-monitoring.md): a vault is any
backup storage location (``kind``) and says whether it keeps an off-site copy
and whether its restore points are irreversibly immutable; items gain their
backup size. ``backup_settings`` holds each tenant's RPO, and the
backup.write permission (Owner, Admin) lets a tenant change it.

Existing Azure vaults are backfilled from what they already store
(geo-redundant -> off-site, locked immutability -> immutable). Additive.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'b7d2f4a9c1e6'
down_revision: Union[str, None] = 'a4c8e2f6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CODE = 'backup.write'
_DESCRIPTION = 'Change backup targets (RPO) for the tenant'
_ROLES = ('Owner', 'Admin')


def upgrade() -> None:
    op.add_column('backup_vaults', sa.Column('kind', sa.String(20), server_default='vault', nullable=False))
    op.add_column('backup_vaults', sa.Column('offsite', sa.Boolean(), nullable=True))
    op.add_column('backup_vaults', sa.Column('immutable', sa.Boolean(), nullable=True))
    op.add_column('backup_items', sa.Column('size_bytes', sa.BigInteger(), nullable=True))

    op.execute("""
        UPDATE backup_vaults
        SET offsite = CASE WHEN storage_redundancy IS NULL THEN NULL
                           ELSE storage_redundancy LIKE '%geo%' END,
            immutable = CASE WHEN immutability_state IS NULL THEN NULL
                             ELSE immutability_state = 'locked' END
        WHERE provider = 'azure'
    """)

    op.create_table(
        'backup_settings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('rpo_hours', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('tenant_id', name='uq_backup_settings_tenant'),
    )

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
    op.drop_table('backup_settings')
    op.drop_column('backup_items', 'size_bytes')
    op.drop_column('backup_vaults', 'immutable')
    op.drop_column('backup_vaults', 'offsite')
    op.drop_column('backup_vaults', 'kind')
