"""merge paloalto diagnostic and incident index heads

Revision ID: 7ba0fc4d636e
Revises: c9d1pa7a0b2c, f3a9c1e7b2d5
Create Date: 2026-10-20 14:57:08.362353

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7ba0fc4d636e'
down_revision: Union[str, None] = ('c9d1pa7a0b2c', 'f3a9c1e7b2d5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
