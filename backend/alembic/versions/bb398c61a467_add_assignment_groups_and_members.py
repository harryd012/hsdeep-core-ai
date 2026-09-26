"""add_assignment_groups_and_members

Revision ID: bb398c61a467
Revises: b2c3d4e5f6a7
Create Date: 2026-08-04 23:56:39.644714

Manually reviewed/cleaned after autogenerate:
- Removed unrelated index create/drop churn on pre-existing tables
  (playbooks, knowledge_articles, approval_requests, execution_jobs,
  itsm_sync_jobs, ticket_comments, ticket_history) - that is pre-existing
  model/DB drift outside this epic's scope.
- Removed the unrelated type change on tickets.ai_execution_confidence
  (DOUBLE_PRECISION -> Integer) - the model says Integer but the live DB
  has DOUBLE_PRECISION; fixing that drift belongs in a separate migration.

This migration ONLY adds:
  1. assignment_groups table
  2. assignment_group_members table
  3. tickets.assigned_group_id column + FK + index
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bb398c61a467'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- assignment_groups table ---
    op.create_table(
        'assignment_groups',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('strategy', sa.String(length=30), server_default='round_robin', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_assignment_groups_tenant_id_name'),
    )
    op.create_index(op.f('ix_assignment_groups_is_active'), 'assignment_groups', ['is_active'], unique=False)
    op.create_index(op.f('ix_assignment_groups_is_deleted'), 'assignment_groups', ['is_deleted'], unique=False)
    op.create_index(op.f('ix_assignment_groups_name'), 'assignment_groups', ['name'], unique=False)
    op.create_index(op.f('ix_assignment_groups_strategy'), 'assignment_groups', ['strategy'], unique=False)
    op.create_index(op.f('ix_assignment_groups_tenant_id'), 'assignment_groups', ['tenant_id'], unique=False)

    # --- assignment_group_members table ---
    op.create_table(
        'assignment_group_members',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('skills_json', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('capacity_weight', sa.Integer(), server_default='1', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['assignment_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'user_id', name='uq_assignment_group_members_group_id_user_id'),
    )
    op.create_index(op.f('ix_assignment_group_members_group_id'), 'assignment_group_members', ['group_id'], unique=False)
    op.create_index(op.f('ix_assignment_group_members_is_active'), 'assignment_group_members', ['is_active'], unique=False)
    op.create_index(op.f('ix_assignment_group_members_is_deleted'), 'assignment_group_members', ['is_deleted'], unique=False)
    op.create_index(op.f('ix_assignment_group_members_tenant_id'), 'assignment_group_members', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_assignment_group_members_user_id'), 'assignment_group_members', ['user_id'], unique=False)

    # --- tickets.assigned_group_id column + FK + index ---
    op.add_column('tickets', sa.Column('assigned_group_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_tickets_assigned_group_id'), 'tickets', ['assigned_group_id'], unique=False)
    op.create_foreign_key(
        'fk_tickets_assigned_group_id',
        'tickets',
        'assignment_groups',
        ['assigned_group_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    # --- tickets.assigned_group_id ---
    op.drop_constraint('fk_tickets_assigned_group_id', 'tickets', type_='foreignkey')
    op.drop_index(op.f('ix_tickets_assigned_group_id'), table_name='tickets')
    op.drop_column('tickets', 'assigned_group_id')

    # --- assignment_group_members table ---
    op.drop_index(op.f('ix_assignment_group_members_user_id'), table_name='assignment_group_members')
    op.drop_index(op.f('ix_assignment_group_members_tenant_id'), table_name='assignment_group_members')
    op.drop_index(op.f('ix_assignment_group_members_is_deleted'), table_name='assignment_group_members')
    op.drop_index(op.f('ix_assignment_group_members_is_active'), table_name='assignment_group_members')
    op.drop_index(op.f('ix_assignment_group_members_group_id'), table_name='assignment_group_members')
    op.drop_table('assignment_group_members')

    # --- assignment_groups table ---
    op.drop_index(op.f('ix_assignment_groups_tenant_id'), table_name='assignment_groups')
    op.drop_index(op.f('ix_assignment_groups_strategy'), table_name='assignment_groups')
    op.drop_index(op.f('ix_assignment_groups_name'), table_name='assignment_groups')
    op.drop_index(op.f('ix_assignment_groups_is_deleted'), table_name='assignment_groups')
    op.drop_index(op.f('ix_assignment_groups_is_active'), table_name='assignment_groups')
    op.drop_table('assignment_groups')