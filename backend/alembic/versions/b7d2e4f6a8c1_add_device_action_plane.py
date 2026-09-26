"""add device action plane (Phase 1)

Revision ID: b7d2e4f6a8c1
Revises: 8a02d3f5b971
Create Date: 2026-09-19 12:00:00.000000

Device Action & Infrastructure Control Plane:

  * device_action_requests - one row per requested device action (the
    WHY/WHO/WHAT/WHEN/WHERE/RESULT audit spine), FK-linked to the EXISTING
    approval_requests and execution_jobs engines (no duplicate engines).
  * device_capabilities - per-device tri-state capability model (1:1 with
    devices). Derived rows only assert read-only transports; state-change
    capabilities require an explicit assertion.
  * permissions: seeds the three new device_actions.* codes from
    app.core.permissions.PERMISSION_CATALOG (single source of truth, same
    pattern as f5b9c3e7a2d1).
  * role_permissions backfill: existing tenants' default roles receive the
    new codes per the DEFAULT_ROLES spec (Owner/Admin: all three; Operator:
    read+execute; Viewer: read) so already-provisioned tenants are not
    left without grants until re-provisioning.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.permissions import PERMISSION_CATALOG

revision: str = 'b7d2e4f6a8c1'
down_revision: Union[str, None] = '8a02d3f5b971'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_CODES = ("device_actions.read", "device_actions.execute", "device_actions.approve")

# role name -> codes granted by this migration (mirrors DEFAULT_ROLES).
_ROLE_GRANTS = {
    "Owner": _NEW_CODES,
    "Admin": _NEW_CODES,
    "Operator": ("device_actions.read", "device_actions.execute"),
    "Viewer": ("device_actions.read",),
}


def upgrade() -> None:
    op.create_table(
        'device_action_requests',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False),
        sa.Column('device_id', UUID(as_uuid=True), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('classification', sa.String(length=40), nullable=False),
        sa.Column('provider_kind', sa.String(length=40), nullable=True),
        sa.Column('params', JSONB(), server_default='{}', nullable=False),
        sa.Column('status', sa.String(length=30), server_default='pending', nullable=False),
        sa.Column('motivation', JSONB(), server_default='{}', nullable=False),
        sa.Column('requested_by', UUID(as_uuid=True), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_by', UUID(as_uuid=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('incident_id', UUID(as_uuid=True), nullable=True),
        sa.Column('approval_request_id', UUID(as_uuid=True), nullable=True),
        sa.Column('execution_job_id', UUID(as_uuid=True), nullable=True),
        sa.Column('correlation_id', sa.String(length=64), nullable=False),
        sa.Column('policy_decision', JSONB(), server_default='{}', nullable=False),
        sa.Column('result', JSONB(), server_default='{}', nullable=False),
        sa.Column('verification', JSONB(), server_default='{}', nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['approval_request_id'], ['approval_requests.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['execution_job_id'], ['execution_jobs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('tenant_id', 'device_id', 'action', 'status', 'requested_by',
                'incident_id', 'approval_request_id', 'execution_job_id',
                'correlation_id'):
        op.create_index(
            op.f(f'ix_device_action_requests_{col}'), 'device_action_requests', [col], unique=False
        )

    op.create_table(
        'device_capabilities',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False),
        sa.Column('device_id', UUID(as_uuid=True), nullable=False),
        sa.Column('source', sa.String(length=20), server_default='derived', nullable=False),
        sa.Column('supports_ping', sa.Boolean(), nullable=True),
        sa.Column('supports_snmp', sa.Boolean(), nullable=True),
        sa.Column('supports_ssh', sa.Boolean(), nullable=True),
        sa.Column('supports_rest', sa.Boolean(), nullable=True),
        sa.Column('supports_config', sa.Boolean(), nullable=True),
        sa.Column('supports_reboot', sa.Boolean(), nullable=True),
        sa.Column('supports_firmware', sa.Boolean(), nullable=True),
        sa.Column('supports_backup', sa.Boolean(), nullable=True),
        sa.Column('supports_rollback', sa.Boolean(), nullable=True),
        sa.Column('firmware_version', sa.String(length=100), nullable=True),
        sa.Column('management_address', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', name='uq_device_capabilities_device_id'),
    )
    op.create_index(op.f('ix_device_capabilities_tenant_id'), 'device_capabilities', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_device_capabilities_device_id'), 'device_capabilities', ['device_id'], unique=False)

    # -- seed the new permission codes (catalog = single source of truth) --
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

    # -- backfill grants for existing tenants' default roles ---------------
    for role_name, codes in _ROLE_GRANTS.items():
        code_list = ", ".join(f"'{c}'" for c in codes)
        bind.execute(sa.text(f"""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM roles r
            JOIN permissions p ON p.code IN ({code_list})
            WHERE r.name = :role_name
              AND NOT EXISTS (
                  SELECT 1 FROM role_permissions rp
                  WHERE rp.role_id = r.id AND rp.permission_id = p.id
              )
        """), {'role_name': role_name})


def downgrade() -> None:
    # Remove the grants this migration backfilled, then the seeded codes.
    bind = op.get_bind()
    for role_name, codes in _ROLE_GRANTS.items():
        code_list = ", ".join(f"'{c}'" for c in codes)
        bind.execute(sa.text(f"""
            DELETE FROM role_permissions rp
            USING roles r, permissions p
            WHERE rp.role_id = r.id AND rp.permission_id = p.id
              AND r.name = :role_name AND p.code IN ({code_list})
        """), {'role_name': role_name})
    bind.execute(
        sa.text('DELETE FROM permissions WHERE code = ANY(:codes)'),
        {'codes': list(_NEW_CODES)},
    )

    op.drop_index(op.f('ix_device_capabilities_device_id'), table_name='device_capabilities')
    op.drop_index(op.f('ix_device_capabilities_tenant_id'), table_name='device_capabilities')
    op.drop_table('device_capabilities')
    for col in ('tenant_id', 'device_id', 'action', 'status', 'requested_by',
                'incident_id', 'approval_request_id', 'execution_job_id',
                'correlation_id'):
        op.drop_index(op.f(f'ix_device_action_requests_{col}'), table_name='device_action_requests')
    op.drop_table('device_action_requests')
