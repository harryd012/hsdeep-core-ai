"""security S6: verified domains, OIDC single sign-on and SCIM

Revision ID: c9e7a3b5d1f6
Revises: b8d6f2a4c0e5
Create Date: 2026-11-05 10:00:00.000000

Enterprise security programme S6 (docs/security/ENTERPRISE_SECURITY_PROGRAMME.md):
tenant_domains (DNS-verified email domains; a verified domain belongs to one
tenant), tenant_sso_configs (OIDC provider, require SSO, JIT, SCIM token hash),
user_identities (external subjects), sso_login_states and sso_handoffs
(one-time OIDC round-trip records). Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'c9e7a3b5d1f6'
down_revision: Union[str, None] = 'b8d6f2a4c0e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_domains',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('domain', sa.String(253), nullable=False),
        sa.Column('verification_token', sa.String(64), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'domain', name='uq_tenant_domains_tenant_domain'),
    )
    op.create_index('ix_tenant_domains_tenant_id', 'tenant_domains', ['tenant_id'])
    op.create_index('uq_tenant_domains_verified_domain', 'tenant_domains', ['domain'], unique=True,
                    postgresql_where=sa.text('verified_at IS NOT NULL'))
    op.create_table(
        'tenant_sso_configs',
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('protocol', sa.String(10), nullable=False, server_default='oidc'),
        sa.Column('issuer', sa.String(500), nullable=True),
        sa.Column('client_id', sa.String(255), nullable=True),
        sa.Column('client_secret_encrypted', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('require_sso', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('jit_provisioning', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('default_role_id', UUID(as_uuid=True), nullable=True),
        sa.Column('scim_token_hash', sa.String(64), nullable=True, unique=True),
        sa.Column('scim_token_prefix', sa.String(12), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'user_identities',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(10), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'provider', 'subject', name='uq_user_identities_subject'),
    )
    op.create_index('ix_user_identities_tenant_id', 'user_identities', ['tenant_id'])
    op.create_index('ix_user_identities_user_id', 'user_identities', ['user_id'])
    op.create_table(
        'sso_login_states',
        sa.Column('state', sa.String(64), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('nonce', sa.String(64), nullable=False),
        sa.Column('code_verifier', sa.String(128), nullable=False),
        sa.Column('next_path', sa.String(500), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'sso_handoffs',
        sa.Column('code_hash', sa.String(64), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('sso_handoffs')
    op.drop_table('sso_login_states')
    op.drop_index('ix_user_identities_user_id', table_name='user_identities')
    op.drop_index('ix_user_identities_tenant_id', table_name='user_identities')
    op.drop_table('user_identities')
    op.drop_table('tenant_sso_configs')
    op.drop_index('uq_tenant_domains_verified_domain', table_name='tenant_domains')
    op.drop_index('ix_tenant_domains_tenant_id', table_name='tenant_domains')
    op.drop_table('tenant_domains')
