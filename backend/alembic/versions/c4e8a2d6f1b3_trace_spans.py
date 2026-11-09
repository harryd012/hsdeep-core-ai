"""tracing: trace spans

Revision ID: c4e8a2d6f1b3
Revises: d2f8b4c6e0a7
Create Date: 2026-11-04 10:00:00.000000

Observability plan P8 slice 1 (docs/design/OBSERVABILITY_PLATFORM_PLAN.md):
OpenTelemetry spans received over OTLP/HTTP. Additive: one new table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c4e8a2d6f1b3'
down_revision: Union[str, None] = 'd2f8b4c6e0a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'trace_spans',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('monitoring_source_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('monitoring_sources.id', ondelete='SET NULL'), nullable=True),
        sa.Column('trace_id', sa.String(length=32), nullable=False),
        sa.Column('span_id', sa.String(length=16), nullable=False),
        sa.Column('parent_span_id', sa.String(length=16), nullable=True),
        sa.Column('service_name', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_ms', sa.Float(), nullable=False),
        sa.Column('status_code', sa.String(length=10), nullable=False),
        sa.Column('status_message', sa.Text(), nullable=True),
        sa.Column('attributes', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('resource_attributes', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('events', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('tenant_id', 'trace_id', 'span_id', name='uq_trace_spans_tenant_trace_span'),
    )
    op.create_index('ix_trace_spans_tenant_start', 'trace_spans', ['tenant_id', 'start_time'])
    op.create_index('ix_trace_spans_tenant_service_start', 'trace_spans', ['tenant_id', 'service_name', 'start_time'])
    op.create_index('ix_trace_spans_tenant_trace', 'trace_spans', ['tenant_id', 'trace_id'])


def downgrade() -> None:
    op.drop_index('ix_trace_spans_tenant_trace', table_name='trace_spans')
    op.drop_index('ix_trace_spans_tenant_service_start', table_name='trace_spans')
    op.drop_index('ix_trace_spans_tenant_start', table_name='trace_spans')
    op.drop_table('trace_spans')
