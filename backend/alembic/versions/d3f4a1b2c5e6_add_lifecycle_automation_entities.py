"""add lifecycle automation entities: SOPExecution model, ServiceNow sync fields on Ticket.

Revision ID: d3f4a1b2c5e6
Revises: 1c644b59d560
Create Date: 2026-08-10 10:36:00.000000

Adds:
- ``sop_executions`` table for tracking live SOP step execution (auto/manual/gated)
  with pause-and-resume support for the AutomationRunner.
- ``servicenow_sys_id`` + ``servicenow_sync_status`` on ``tickets`` so the
  ServiceNow lifecycle-sync listener can store the external record identity
  and sync status per ticket.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd3f4a1b2c5e6'
down_revision: Union[str, None] = '1c644b59d560'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- sop_executions table ----------------------------------------------
    op.create_table('sop_executions',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('sop_id', sa.UUID(), nullable=False),
    sa.Column('ticket_id', sa.UUID(), nullable=True),
    sa.Column('incident_id', sa.UUID(), nullable=True),
    sa.Column('correlation_id', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='running', nullable=False),
    sa.Column('current_step_index', sa.Integer(), server_default='0', nullable=False),
    sa.Column('step_results', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('assigned_to', sa.UUID(), nullable=True),
    sa.Column('operator_id', sa.UUID(), nullable=True),
    sa.Column('approved_by', sa.UUID(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('result_notes', sa.Text(), nullable=True),
    sa.Column('rollback_required', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('rollback_performed', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['operator_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sop_id'], ['sops.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sop_executions_assigned_to'), 'sop_executions', ['assigned_to'], unique=False)
    op.create_index(op.f('ix_sop_executions_correlation_id'), 'sop_executions', ['correlation_id'], unique=False)
    op.create_index(op.f('ix_sop_executions_current_step_index'), 'sop_executions', ['current_step_index'], unique=False)
    op.create_index(op.f('ix_sop_executions_incident_id'), 'sop_executions', ['incident_id'], unique=False)
    op.create_index(op.f('ix_sop_executions_is_deleted'), 'sop_executions', ['is_deleted'], unique=False)
    op.create_index(op.f('ix_sop_executions_status'), 'sop_executions', ['status'], unique=False)
    op.create_index(op.f('ix_sop_executions_sop_id'), 'sop_executions', ['sop_id'], unique=False)
    op.create_index(op.f('ix_sop_executions_ticket_id'), 'sop_executions', ['ticket_id'], unique=False)
    op.create_index(op.f('ix_sop_executions_tenant_id'), 'sop_executions', ['tenant_id'], unique=False)

    # --- servicenow sync fields on tickets --------------------------------
    op.add_column('tickets',
    sa.Column('servicenow_sys_id', sa.String(length=50), nullable=True),
    )
    op.create_index(op.f('ix_tickets_servicenow_sys_id'), 'tickets', ['servicenow_sys_id'], unique=False)
    op.add_column('tickets',
    sa.Column('servicenow_sync_status', sa.String(length=30),
              server_default='pending', nullable=False),
    )
    op.create_index(op.f('ix_tickets_servicenow_sync_status'), 'tickets', ['servicenow_sync_status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tickets_servicenow_sync_status'), table_name='tickets')
    op.drop_column('tickets', 'servicenow_sync_status')
    op.drop_index(op.f('ix_tickets_servicenow_sys_id'), table_name='tickets')
    op.drop_column('tickets', 'servicenow_sys_id')

    op.drop_index(op.f('ix_sop_executions_tenant_id'), table_name='sop_executions')
    op.drop_index(op.f('ix_sop_executions_ticket_id'), table_name='sop_executions')
    op.drop_index(op.f('ix_sop_executions_sop_id'), table_name='sop_executions')
    op.drop_index(op.f('ix_sop_executions_status'), table_name='sop_executions')
    op.drop_index(op.f('ix_sop_executions_is_deleted'), table_name='sop_executions')
    op.drop_index(op.f('ix_sop_executions_incident_id'), table_name='sop_executions')
    op.drop_index(op.f('ix_sop_executions_current_step_index'), table_name='sop_executions')
    op.drop_index(op.f('ix_sop_executions_correlation_id'), table_name='sop_executions')
    op.drop_index(op.f('ix_sop_executions_assigned_to'), table_name='sop_executions')
    op.drop_table('sop_executions')
