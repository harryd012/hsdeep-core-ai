"""add firewall gateway tables

Revision ID: b5e8c2d4f6a1
Revises: a1d4f7b2c9e3
Create Date: 2026-10-30 23:00:00.000000

firewall_status (one row per firewall device) and vpn_tunnels (one row per
device + tunnel name) for the firewall gateway
(docs/architecture/firewall-gateway.md). Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = 'b5e8c2d4f6a1'
down_revision: Union[str, None] = 'a1d4f7b2c9e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'firewall_status',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('device_id', UUID(as_uuid=True), sa.ForeignKey('devices.id', ondelete='CASCADE'), nullable=False),
        sa.Column('monitoring_source_id', UUID(as_uuid=True), sa.ForeignKey('monitoring_sources.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ha_mode', sa.String(20), nullable=True),
        sa.Column('ha_role', sa.String(20), nullable=True),
        sa.Column('ha_sync', sa.String(20), nullable=True),
        sa.Column('ha_peer_serial', sa.String(100), nullable=True),
        sa.Column('session_count', sa.Integer(), nullable=True),
        sa.Column('session_limit', sa.Integer(), nullable=True),
        sa.Column('cpu_percent', sa.Float(), nullable=True),
        sa.Column('memory_percent', sa.Float(), nullable=True),
        sa.Column('firmware_version', sa.String(100), nullable=True),
        sa.Column('licences', JSONB(), nullable=True),
        sa.Column('table_status', JSONB(), server_default='{}', nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint('device_id', name='uq_firewall_status_device'),
    )
    op.create_index('ix_firewall_status_tenant_id', 'firewall_status', ['tenant_id'])

    op.create_table(
        'vpn_tunnels',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('device_id', UUID(as_uuid=True), sa.ForeignKey('devices.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('tunnel_type', sa.String(20), nullable=False),
        sa.Column('remote_gateway', sa.String(255), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('ever_up', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('phase2_up', sa.Integer(), nullable=True),
        sa.Column('phase2_total', sa.Integer(), nullable=True),
        sa.Column('bytes_in', sa.Float(), nullable=True),
        sa.Column('bytes_out', sa.Float(), nullable=True),
        sa.Column('last_change_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint('device_id', 'name', name='uq_vpn_tunnels_device_name'),
    )
    op.create_index('ix_vpn_tunnels_tenant_id', 'vpn_tunnels', ['tenant_id'])
    op.create_index('ix_vpn_tunnels_device_id', 'vpn_tunnels', ['device_id'])


def downgrade() -> None:
    op.drop_index('ix_vpn_tunnels_device_id', table_name='vpn_tunnels')
    op.drop_index('ix_vpn_tunnels_tenant_id', table_name='vpn_tunnels')
    op.drop_table('vpn_tunnels')
    op.drop_index('ix_firewall_status_tenant_id', table_name='firewall_status')
    op.drop_table('firewall_status')
