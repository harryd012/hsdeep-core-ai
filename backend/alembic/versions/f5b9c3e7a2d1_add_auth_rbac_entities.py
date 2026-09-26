"""add auth + rbac entities

Revision ID: f5b9c3e7a2d1
Revises: d2e4f6a8b0c1
Create Date: 2026-07-25 09:00:00.000000

Auth + RBAC sprint (P1). Additive and backward-compatible:

  * users: password_hash (nullable - null means "no local password"),
    is_superuser (NOT NULL, server_default false so existing rows are safe),
    last_login_at (nullable).
  * refresh_tokens: new table storing only SHA-256 hashes of opaque refresh
    tokens, revocable via revoked_at.
  * permissions: data-seed of the canonical catalog. The codes are imported
    from app.core.permissions.PERMISSION_CATALOG rather than duplicated here,
    so the catalog has exactly one source of truth. Idempotent-ish: seeds
    only codes not already present.

Default ROLES are intentionally NOT seeded here - roles are tenant-scoped and
created per tenant at runtime by AuthService.provision_tenant_rbac().
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.core.permissions import PERMISSION_CATALOG

revision: str = 'f5b9c3e7a2d1'
down_revision: Union[str, None] = 'd2e4f6a8b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- users: auth columns ------------------------------------------------
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column(
        'users',
        sa.Column('is_superuser', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True))

    # -- refresh_tokens table ----------------------------------------------
    op.create_table(
        'refresh_tokens',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_refresh_tokens_user_id'), 'refresh_tokens', ['user_id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_tenant_id'), 'refresh_tokens', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_token_hash'), 'refresh_tokens', ['token_hash'], unique=True)

    # -- seed the permission catalog ---------------------------------------
    permissions_tbl = sa.table(
        'permissions',
        sa.column('id', UUID(as_uuid=True)),
        sa.column('code', sa.String),
        sa.column('description', sa.String),
        sa.column('is_deleted', sa.Boolean),
    )
    bind = op.get_bind()
    existing = {row[0] for row in bind.execute(sa.text('SELECT code FROM permissions'))}
    rows = [
        {'id': uuid.uuid4(), 'code': code, 'description': desc, 'is_deleted': False}
        for code, desc in PERMISSION_CATALOG
        if code not in existing
    ]
    if rows:
        op.bulk_insert(permissions_tbl, rows)


def downgrade() -> None:
    # Remove only the catalog codes this migration introduced.
    codes = [code for code, _ in PERMISSION_CATALOG]
    bind = op.get_bind()
    bind.execute(
        sa.text('DELETE FROM permissions WHERE code = ANY(:codes)'), {'codes': codes}
    )

    op.drop_index(op.f('ix_refresh_tokens_token_hash'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_tenant_id'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_user_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')

    op.drop_column('users', 'last_login_at')
    op.drop_column('users', 'is_superuser')
    op.drop_column('users', 'password_hash')
