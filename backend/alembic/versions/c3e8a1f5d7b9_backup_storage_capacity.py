"""backup: capacity and free space of fixed-size storage locations

Revision ID: c3e8a1f5d7b9
Revises: b7d2f4a9c1e6
Create Date: 2026-11-03 18:00:00.000000

Backup phase 3, Veeam connector (docs/engineering/veeam-backup-monitoring.md):
a Veeam repository has a fixed size, so its capacity and free space are
stored next to the space used, and low free space alerts. Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3e8a1f5d7b9'
down_revision: Union[str, None] = 'b7d2f4a9c1e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('backup_vaults', sa.Column('capacity_bytes', sa.BigInteger(), nullable=True))
    op.add_column('backup_vaults', sa.Column('free_bytes', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('backup_vaults', 'free_bytes')
    op.drop_column('backup_vaults', 'capacity_bytes')
