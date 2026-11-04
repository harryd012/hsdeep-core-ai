"""security S5: privacy settings, data export and offboarding

Revision ID: b8d6f2a4c0e5
Revises: a7c5e1f3b9d4
Create Date: 2026-11-04 22:00:00.000000

Enterprise security programme S5 (docs/security/ENTERPRISE_SECURITY_PROGRAMME.md):
* tenant_privacy_settings: AI data use, retention, pending deletion.
* tenant_exports: requested data exports (a ZIP kept for 7 days).
* privacy.manage (Owner, Admin): privacy settings and data export.
  tenant.delete (Owner only): request or cancel the tenant's deletion.
Additive.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'b8d6f2a4c0e5'
down_revision: Union[str, None] = 'a7c5e1f3b9d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSIONS = (
    ('privacy.manage', 'Set AI data use and retention, and export the tenant data', ('Owner', 'Admin')),
    ('tenant.delete', 'Request or cancel deletion of the whole tenant', ('Owner',)),
)


def upgrade() -> None:
    op.create_table(
        'tenant_privacy_settings',
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('ai_mode', sa.String(10), nullable=False, server_default='on'),
        sa.Column('ai_redact_emails', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('ai_redact_ips', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('metrics_retention_days', sa.Integer(), nullable=True),
        sa.Column('events_retention_days', sa.Integer(), nullable=True),
        sa.Column('deletion_requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deletion_scheduled_for', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deletion_requested_by', sa.String(255), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'tenant_exports',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('requested_by', sa.String(255), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('file_path', sa.String(500), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('summary', sa.dialects.postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('error', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_tenant_exports_tenant_id', 'tenant_exports', ['tenant_id'])

    bind = op.get_bind()
    for code, description, roles in _PERMISSIONS:
        exists = bind.execute(sa.text('SELECT 1 FROM permissions WHERE code = :c'), {'c': code}).first()
        if not exists:
            bind.execute(
                sa.text('INSERT INTO permissions (id, code, description, is_deleted) VALUES (:id, :c, :d, false)'),
                {'id': uuid.uuid4(), 'c': code, 'd': description},
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
        """), {'c': code, 'roles': list(roles)})


def downgrade() -> None:
    bind = op.get_bind()
    for code, _, _ in _PERMISSIONS:
        bind.execute(sa.text("""
            DELETE FROM role_permissions rp
            USING permissions p
            WHERE rp.permission_id = p.id AND p.code = :c
        """), {'c': code})
        bind.execute(sa.text('DELETE FROM permissions WHERE code = :c'), {'c': code})
    op.drop_index('ix_tenant_exports_tenant_id', table_name='tenant_exports')
    op.drop_table('tenant_exports')
    op.drop_table('tenant_privacy_settings')
