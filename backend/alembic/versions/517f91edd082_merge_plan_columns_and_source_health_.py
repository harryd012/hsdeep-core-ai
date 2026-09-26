"""merge plan columns and source health branches

Revision ID: 517f91edd082
Revises: 20260825_add_plan_columns, d4e5f6a7b8c9
Create Date: 2026-08-25 18:27:35.787666

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '517f91edd082'
down_revision: Union[str, None] = ('20260825_add_plan_columns', 'd4e5f6a7b8c9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
