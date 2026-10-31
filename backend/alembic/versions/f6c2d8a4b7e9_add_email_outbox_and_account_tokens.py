"""add email outbox, account tokens and email verification columns

Revision ID: f6c2d8a4b7e9
Revises: e3a7c5b9d1f4
Create Date: 2026-10-30 18:00:00.000000

Email foundation (docs/architecture/email-foundation.md):
  * email_outbox: queued outbound mail delivered by the worker.
  * account_tokens: hashed single-use tokens for password reset, email
    verification and invitations.
  * users.email_verified_at / users.requires_email_verification. Existing
    users are backfilled as verified (and not required to verify), so nobody
    is locked out by this migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'f6c2d8a4b7e9'
down_revision: Union[str, None] = 'e3a7c5b9d1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_outbox',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('to_address', sa.String(255), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('body_text', sa.Text(), nullable=False),
        sa.Column('category', sa.String(40), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_email_outbox_tenant_id', 'email_outbox', ['tenant_id'])
    op.create_index('ix_email_outbox_status_next_attempt', 'email_outbox', ['status', 'next_attempt_at'])

    op.create_table(
        'account_tokens',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('purpose', sa.String(30), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_account_tokens_user_id', 'account_tokens', ['user_id'])
    op.create_index('ix_account_tokens_tenant_id', 'account_tokens', ['tenant_id'])

    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'users',
        sa.Column('requires_email_verification', sa.Boolean(), server_default='false', nullable=False),
    )
    op.execute("UPDATE users SET email_verified_at = COALESCE(created_at, now())")


def downgrade() -> None:
    op.drop_column('users', 'requires_email_verification')
    op.drop_column('users', 'email_verified_at')
    op.drop_index('ix_account_tokens_tenant_id', table_name='account_tokens')
    op.drop_index('ix_account_tokens_user_id', table_name='account_tokens')
    op.drop_table('account_tokens')
    op.drop_index('ix_email_outbox_status_next_attempt', table_name='email_outbox')
    op.drop_index('ix_email_outbox_tenant_id', table_name='email_outbox')
    op.drop_table('email_outbox')
