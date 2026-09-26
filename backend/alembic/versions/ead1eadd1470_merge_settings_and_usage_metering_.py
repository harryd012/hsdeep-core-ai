"""Merge settings and usage metering branches

Revision ID: ead1eadd1470
Revises: e6a0b4c8f2d3, 9a1b2c3d4e5f
Create Date: 2026-07-28 15:18:03.374964

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ead1eadd1470'
down_revision: Union[str, None] = ('e6a0b4c8f2d3', '9a1b2c3d4e5f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
