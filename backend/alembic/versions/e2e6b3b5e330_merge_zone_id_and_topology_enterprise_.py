"""merge zone_id and topology-enterprise-standard heads

Revision ID: e2e6b3b5e330
Revises: c1f2e3d4a5b6, f2a1b3c4d5e6
Create Date: 2026-09-14 16:19:17.992707

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2e6b3b5e330'
down_revision: Union[str, None] = ('c1f2e3d4a5b6', 'f2a1b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
