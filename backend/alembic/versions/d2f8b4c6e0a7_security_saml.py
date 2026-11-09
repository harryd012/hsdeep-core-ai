"""security S6: SAML 2.0 identity providers

Revision ID: d2f8b4c6e0a7
Revises: c9e7a3b5d1f6
Create Date: 2026-11-05 14:00:00.000000

Enterprise security programme S6 (SAML): tenant_saml_configs holds a tenant's
SAML identity provider (entity ID, sign-in URL, signing certificates, email
attribute). tenant_sso_configs.protocol selects "oidc" or "saml". Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'd2f8b4c6e0a7'
down_revision: Union[str, None] = 'c9e7a3b5d1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_saml_configs',
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('idp_entity_id', sa.String(500), nullable=True),
        sa.Column('idp_sso_url', sa.String(1000), nullable=True),
        sa.Column('idp_certificates', sa.Text(), nullable=True),
        sa.Column('email_attribute', sa.String(255), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('tenant_saml_configs')
