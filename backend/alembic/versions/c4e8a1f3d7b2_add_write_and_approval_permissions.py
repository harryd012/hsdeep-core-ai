"""add write and approval permission codes

Revision ID: c4e8a1f3d7b2
Revises: a9d3e7c1f2b4
Create Date: 2026-10-30 12:00:00.000000

Mutating routes for SOP/runbook/workflow authoring and approval,
integrations, NOC/normalized events, agent tasks, agents and change requests
had no permission check (any authenticated tenant user, including Viewer,
could call them). This migration seeds the codes those routes now require
from app.core.permissions.PERMISSION_CATALOG (same pattern as b7d2e4f6a8c1)
and backfills grants for existing tenants' default roles, mirroring
DEFAULT_ROLES: Owner and Admin get every new code; Operator gets noc.write,
tasks.write, changes.write, tickets.write and tickets.execute; Viewer gets
billing.read (Viewer holds every .read code).

It also seeds five codes that 46 existing routes already required but that
were never in the catalog (tickets.write, tickets.execute, itsm.write,
billing.write, billing.read), so no role could hold them and only superusers
could create tickets, view or change a subscription, see onboarding status or
configure ITSM connections.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.core.permissions import PERMISSION_CATALOG

revision: str = 'c4e8a1f3d7b2'
down_revision: Union[str, None] = 'a9d3e7c1f2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_CODES = (
    "sops.approve",
    "runbooks.write",
    "runbooks.approve",
    "workflows.write",
    "workflows.approve",
    "integrations.write",
    "noc.write",
    "topology.write",
    "tasks.write",
    "agents.write",
    "changes.write",
    "tickets.write",
    "tickets.execute",
    "itsm.write",
    "billing.write",
    "billing.read",
)

_ROLE_GRANTS = {
    "Owner": _NEW_CODES,
    "Admin": _NEW_CODES,
    "Operator": ("noc.write", "tasks.write", "changes.write", "tickets.write", "tickets.execute"),
    "Viewer": ("billing.read",),
}


def upgrade() -> None:
    permissions_tbl = sa.table(
        'permissions',
        sa.column('id', UUID(as_uuid=True)),
        sa.column('code', sa.String),
        sa.column('description', sa.String),
        sa.column('is_deleted', sa.Boolean),
    )
    bind = op.get_bind()
    existing = {row[0] for row in bind.execute(sa.text('SELECT code FROM permissions'))}
    rows = [
        {'id': uuid.uuid4(), 'code': code, 'description': desc, 'is_deleted': False}
        for code, desc in PERMISSION_CATALOG
        if code in _NEW_CODES and code not in existing
    ]
    if rows:
        op.bulk_insert(permissions_tbl, rows)

    for role_name, codes in _ROLE_GRANTS.items():
        bind.execute(sa.text("""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM roles r
            JOIN permissions p ON p.code = ANY(:codes)
            WHERE r.name = :role_name
              AND NOT EXISTS (
                  SELECT 1 FROM role_permissions rp
                  WHERE rp.role_id = r.id AND rp.permission_id = p.id
              )
        """), {'role_name': role_name, 'codes': list(codes)})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("""
        DELETE FROM role_permissions rp
        USING permissions p
        WHERE rp.permission_id = p.id AND p.code = ANY(:codes)
    """), {'codes': list(_NEW_CODES)})
    bind.execute(
        sa.text('DELETE FROM permissions WHERE code = ANY(:codes)'),
        {'codes': list(_NEW_CODES)},
    )
