"""add per-account login lockout columns

Revision ID: e3a7c5b9d1f4
Revises: c4e8a1f3d7b2
Create Date: 2026-10-30 14:00:00.000000

users.failed_login_count counts consecutive failed logins since the last
success; users.locked_until is when a lockout ends (NULL = not locked). Both
are written by AuthService.login. Additive and non-locking for existing rows
(server defaults), so it is safe to run before the new code is live.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e3a7c5b9d1f4'
down_revision: Union[str, None] = 'c4e8a1f3d7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('failed_login_count', sa.Integer(), server_default='0', nullable=False),
    )
    op.add_column('users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'locked_until')
    op.drop_column('users', 'failed_login_count')
