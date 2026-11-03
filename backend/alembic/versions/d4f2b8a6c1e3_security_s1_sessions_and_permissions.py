"""security S1: session details, security.manage and audit.read

Revision ID: d4f2b8a6c1e3
Revises: c3e9a1f7d2b5
Create Date: 2026-11-03 23:00:00.000000

Enterprise security programme S1 (docs/security/ENTERPRISE_SECURITY_PROGRAMME.md):
* auth_sessions: device and activity details for "Your sessions" (liveness
  stays with refresh_tokens).
* security.manage (Owner, Admin): set the tenant security policy and sign
  users out. audit.read (Owner, Admin): view and export the audit log.
Additive.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'd4f2b8a6c1e3'
down_revision: Union[str, None] = 'c3e9a1f7d2b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSIONS = (
    ('security.manage', 'Set the tenant security policy and sign users out of their sessions'),
    ('audit.read', 'View and export the tenant audit log'),
)
_ROLES = ('Owner', 'Admin')


def upgrade() -> None:
    op.create_table(
        'auth_sessions',
        sa.Column('session_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_auth_sessions_tenant_id', 'auth_sessions', ['tenant_id'])
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'])

    bind = op.get_bind()
    for code, description in _PERMISSIONS:
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
        """), {'c': code, 'roles': list(_ROLES)})


def downgrade() -> None:
    bind = op.get_bind()
    for code, _ in _PERMISSIONS:
        bind.execute(sa.text("""
            DELETE FROM role_permissions rp
            USING permissions p
            WHERE rp.permission_id = p.id AND p.code = :c
        """), {'c': code})
        bind.execute(sa.text('DELETE FROM permissions WHERE code = :c'), {'c': code})
    op.drop_index('ix_auth_sessions_user_id', table_name='auth_sessions')
    op.drop_index('ix_auth_sessions_tenant_id', table_name='auth_sessions')
    op.drop_table('auth_sessions')
