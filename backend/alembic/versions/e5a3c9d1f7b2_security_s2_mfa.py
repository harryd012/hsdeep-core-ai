"""security S2: multi-factor authentication

Revision ID: e5a3c9d1f7b2
Revises: d4f2b8a6c1e3
Create Date: 2026-11-04 10:00:00.000000

Enterprise security programme S2 (docs/security/ENTERPRISE_SECURITY_PROGRAMME.md):
user_mfa (one authenticator-app enrolment per user, secret encrypted) and
mfa_recovery_codes (hashed one-time codes). The tenant policy's existing
mfa_enabled column becomes "require MFA". Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'e5a3c9d1f7b2'
down_revision: Union[str, None] = 'd4f2b8a6c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_mfa',
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('secret_encrypted', sa.Text(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_step', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_user_mfa_tenant_id', 'user_mfa', ['tenant_id'])
    op.create_table(
        'mfa_recovery_codes',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('code_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_mfa_recovery_codes_user_id', 'mfa_recovery_codes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_mfa_recovery_codes_user_id', table_name='mfa_recovery_codes')
    op.drop_table('mfa_recovery_codes')
    op.drop_index('ix_user_mfa_tenant_id', table_name='user_mfa')
    op.drop_table('user_mfa')
