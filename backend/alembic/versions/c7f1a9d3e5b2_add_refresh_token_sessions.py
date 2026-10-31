"""add sign-in sessions to refresh tokens

Revision ID: c7f1a9d3e5b2
Revises: b5e8c2d4f6a1
Create Date: 2026-10-30 23:30:00.000000

refresh_tokens.session_id groups the rotations of one sign-in. Access tokens
carry it as the "sid" claim and are rejected once the session is revoked
(server-side logout, R1.4). Existing rows each become their own session.
Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'c7f1a9d3e5b2'
down_revision: Union[str, None] = 'b5e8c2d4f6a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('refresh_tokens', sa.Column('session_id', UUID(as_uuid=True), nullable=True))
    op.execute("UPDATE refresh_tokens SET session_id = id WHERE session_id IS NULL")
    op.create_index('ix_refresh_tokens_session_id', 'refresh_tokens', ['session_id'])


def downgrade() -> None:
    op.drop_index('ix_refresh_tokens_session_id', table_name='refresh_tokens')
    op.drop_column('refresh_tokens', 'session_id')
