"""sop: recommendation decisions (accept / dismiss per incident)

Revision ID: e7a3c9d1b4f6
Revises: f4b1d7e9c3a5
Create Date: 2026-11-09 12:00:00.000000

An operator's accept / dismiss answer to an SOP recommended for an incident
(docs/SOP_RECOMMENDATIONS.md). Additive: one new table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e7a3c9d1b4f6'
down_revision: Union[str, None] = 'f4b1d7e9c3a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sop_recommendation_decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('incident_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('incidents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sop_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sops.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sop_version_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sop_versions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('decision', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('confidence_score', sa.Float(), nullable=True),
        sa.Column('decided_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('incident_id', 'sop_id', name='uq_sop_recommendation_decisions_incident_sop'),
        sa.CheckConstraint("decision IN ('accepted', 'dismissed')", name='ck_sop_recommendation_decisions_decision'),
    )
    op.create_index('ix_sop_recommendation_decisions_tenant_id', 'sop_recommendation_decisions', ['tenant_id'])
    op.create_index('ix_sop_recommendation_decisions_incident_id', 'sop_recommendation_decisions', ['incident_id'])
    op.create_index('ix_sop_recommendation_decisions_sop_id', 'sop_recommendation_decisions', ['sop_id'])


def downgrade() -> None:
    op.drop_index('ix_sop_recommendation_decisions_sop_id', table_name='sop_recommendation_decisions')
    op.drop_index('ix_sop_recommendation_decisions_incident_id', table_name='sop_recommendation_decisions')
    op.drop_index('ix_sop_recommendation_decisions_tenant_id', table_name='sop_recommendation_decisions')
    op.drop_table('sop_recommendation_decisions')
