"""merge support access grants and backup monitoring heads

Revision ID: e9a3c5d7b1f2
Revises: d2b8e6f1a4c9, 7826ac6377f8
Create Date: 2026-10-30 23:59:00.000000

Joins the two lines that both branched from c7f1a9d3e5b2: break-glass
support access (d2b8e6f1a4c9, release branch) and Azure Backup monitoring
(7826ac6377f8, feat/azure-backup-b1). No schema changes.
"""
from typing import Sequence, Union

revision: str = 'e9a3c5d7b1f2'
down_revision: Union[str, Sequence[str], None] = ('d2b8e6f1a4c9', '7826ac6377f8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
