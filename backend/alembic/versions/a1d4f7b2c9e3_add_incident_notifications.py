"""add incident notification delivery log

Revision ID: a1d4f7b2c9e3
Revises: f6c2d8a4b7e9
Create Date: 2026-10-30 21:00:00.000000

incident_notifications: one row per (incident, channel, event) so incident
notifications (email via the outbox, Slack, PagerDuty) are sent once and
every failure is visible. Additive.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'a1d4f7b2c9e3'
down_revision: Union[str, None] = 'f6c2d8a4b7e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'incident_notifications',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('incident_id', UUID(as_uuid=True), sa.ForeignKey('incidents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('event', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('incident_id', 'channel', 'event', name='uq_incident_notifications_incident_channel_event'),
    )
    op.create_index('ix_incident_notifications_tenant_id', 'incident_notifications', ['tenant_id'])
    op.create_index('ix_incident_notifications_incident_id', 'incident_notifications', ['incident_id'])


def downgrade() -> None:
    op.drop_index('ix_incident_notifications_incident_id', table_name='incident_notifications')
    op.drop_index('ix_incident_notifications_tenant_id', table_name='incident_notifications')
    op.drop_table('incident_notifications')
