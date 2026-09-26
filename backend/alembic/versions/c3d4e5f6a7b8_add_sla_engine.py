"""add_sla_engine

Revision ID: c3d4e5f6a7b8
Revises: bb398c61a467
Create Date: 2026-08-05 11:02:00.000000

This migration adds:
  1. sla_policies table
  2. business_hours table
  3. holiday_calendars table
  4. SLA columns on tickets (sla_policy_id, sla_response_due_at,
     sla_resolution_due_at, sla_breached_at, sla_warning_sent,
     sla_paused_at, sla_paused_time_seconds)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'bb398c61a467'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- business_hours table (must be created before sla_policies due to FK) ---
    op.create_table(
        'business_hours',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('timezone', sa.String(length=50), server_default='UTC', nullable=False),
        sa.Column('monday_start', sa.Time(), nullable=True),
        sa.Column('monday_end', sa.Time(), nullable=True),
        sa.Column('tuesday_start', sa.Time(), nullable=True),
        sa.Column('tuesday_end', sa.Time(), nullable=True),
        sa.Column('wednesday_start', sa.Time(), nullable=True),
        sa.Column('wednesday_end', sa.Time(), nullable=True),
        sa.Column('thursday_start', sa.Time(), nullable=True),
        sa.Column('thursday_end', sa.Time(), nullable=True),
        sa.Column('friday_start', sa.Time(), nullable=True),
        sa.Column('friday_end', sa.Time(), nullable=True),
        sa.Column('saturday_start', sa.Time(), nullable=True),
        sa.Column('saturday_end', sa.Time(), nullable=True),
        sa.Column('sunday_start', sa.Time(), nullable=True),
        sa.Column('sunday_end', sa.Time(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_business_hours_is_active'), 'business_hours', ['is_active'], unique=False)
    op.create_index(op.f('ix_business_hours_is_deleted'), 'business_hours', ['is_deleted'], unique=False)
    op.create_index(op.f('ix_business_hours_name'), 'business_hours', ['name'], unique=False)
    op.create_index(op.f('ix_business_hours_tenant_id'), 'business_hours', ['tenant_id'], unique=False)

    # --- sla_policies table ---
    op.create_table(
        'sla_policies',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column('business_service_id', sa.UUID(), nullable=True),
        sa.Column('response_time_minutes', sa.Integer(), nullable=False),
        sa.Column('resolution_time_minutes', sa.Integer(), nullable=False),
        sa.Column('warning_threshold_percent', sa.Integer(), server_default='80', nullable=False),
        sa.Column('use_business_hours', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('business_hours_id', sa.UUID(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['business_service_id'], ['business_services.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['business_hours_id'], ['business_hours.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sla_policies_is_active'), 'sla_policies', ['is_active'], unique=False)
    op.create_index(op.f('ix_sla_policies_is_deleted'), 'sla_policies', ['is_deleted'], unique=False)
    op.create_index(op.f('ix_sla_policies_name'), 'sla_policies', ['name'], unique=False)
    op.create_index(op.f('ix_sla_policies_priority'), 'sla_policies', ['priority'], unique=False)
    op.create_index(op.f('ix_sla_policies_tenant_id'), 'sla_policies', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_sla_policies_business_service_id'), 'sla_policies', ['business_service_id'], unique=False)

    # --- holiday_calendars table ---
    op.create_table(
        'holiday_calendars',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('holiday_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_recurring', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_holiday_calendars_is_deleted'), 'holiday_calendars', ['is_deleted'], unique=False)
    op.create_index(op.f('ix_holiday_calendars_name'), 'holiday_calendars', ['name'], unique=False)
    op.create_index(op.f('ix_holiday_calendars_tenant_id'), 'holiday_calendars', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_holiday_calendars_holiday_date'), 'holiday_calendars', ['holiday_date'], unique=False)

    # --- SLA columns on tickets ---
    op.add_column('tickets', sa.Column('sla_policy_id', sa.UUID(), nullable=True))
    op.add_column('tickets', sa.Column('sla_response_due_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tickets', sa.Column('sla_resolution_due_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tickets', sa.Column('sla_breached_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tickets', sa.Column('sla_warning_sent', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('tickets', sa.Column('sla_paused_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tickets', sa.Column('sla_paused_time_seconds', sa.Integer(), server_default='0', nullable=False))

    op.create_index(op.f('ix_tickets_sla_policy_id'), 'tickets', ['sla_policy_id'], unique=False)
    op.create_foreign_key(
        'fk_tickets_sla_policy_id',
        'tickets',
        'sla_policies',
        ['sla_policy_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    # --- SLA columns on tickets ---
    op.drop_constraint('fk_tickets_sla_policy_id', 'tickets', type_='foreignkey')
    op.drop_index(op.f('ix_tickets_sla_policy_id'), table_name='tickets')
    op.drop_column('tickets', 'sla_paused_time_seconds')
    op.drop_column('tickets', 'sla_paused_at')
    op.drop_column('tickets', 'sla_warning_sent')
    op.drop_column('tickets', 'sla_breached_at')
    op.drop_column('tickets', 'sla_resolution_due_at')
    op.drop_column('tickets', 'sla_response_due_at')
    op.drop_column('tickets', 'sla_policy_id')

    # --- holiday_calendars table ---
    op.drop_index(op.f('ix_holiday_calendars_holiday_date'), table_name='holiday_calendars')
    op.drop_index(op.f('ix_holiday_calendars_tenant_id'), table_name='holiday_calendars')
    op.drop_index(op.f('ix_holiday_calendars_name'), table_name='holiday_calendars')
    op.drop_index(op.f('ix_holiday_calendars_is_deleted'), table_name='holiday_calendars')
    op.drop_table('holiday_calendars')

    # --- sla_policies table ---
    op.drop_index(op.f('ix_sla_policies_business_service_id'), table_name='sla_policies')
    op.drop_index(op.f('ix_sla_policies_tenant_id'), table_name='sla_policies')
    op.drop_index(op.f('ix_sla_policies_priority'), table_name='sla_policies')
    op.drop_index(op.f('ix_sla_policies_name'), table_name='sla_policies')
    op.drop_index(op.f('ix_sla_policies_is_deleted'), table_name='sla_policies')
    op.drop_index(op.f('ix_sla_policies_is_active'), table_name='sla_policies')
    op.drop_table('sla_policies')

    # --- business_hours table ---
    op.drop_index(op.f('ix_business_hours_tenant_id'), table_name='business_hours')
    op.drop_index(op.f('ix_business_hours_name'), table_name='business_hours')
    op.drop_index(op.f('ix_business_hours_is_deleted'), table_name='business_hours')
    op.drop_index(op.f('ix_business_hours_is_active'), table_name='business_hours')
    op.drop_table('business_hours')