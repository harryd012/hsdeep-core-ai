"""Add composite indexes supporting bounded AI-insight alert queries.

The AI Insight service (app/services/ai_insight_service.py) reads ACTIVE
(non-resolved) alerts for its sustained_critical / flapping_sensor rules
using a bounded, server-side-filtered query:

    WHERE tenant_id = :t AND is_deleted = false AND status <> 'resolved'
      AND (severity = 'critical' OR occurrence_count >= N)
      AND (triggered_at <= :before)
    ORDER BY last_occurred_at DESC
    LIMIT 200

On a high-volume tenant whose alerts table holds hundreds of thousands of
resolved/historical rows, the pre-existing per-column indexes are insufficient
for this recurring pattern (each poll of the Copilot/dashboard path would
otherwise scan a large fraction of the table). These two composite indexes
are added only because they directly match that exact WHERE/ORDER BY pattern.

NOTE: postgres partial unique indexes
(uq_alerts_active_per_sensor / uq_alerts_active_per_signal) still drive alert
dedup; these new "active-row" composite indexes are pure scan-speedup, so
dropping them later is safe and non-destructive.
"""
from alembic import op

revision = "a9f2c7d4e1b6"
down_revision = "b8e3f7a9c1d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sustained_critical / flapping_sensor rule lookups: active rows filtered
    # by severity or re-trigger count, ordered by most-recent activity.
    op.create_index(
        "ix_alerts_tenant_status_severity_last",
        "alerts",
        ["tenant_id", "status", "severity", "last_occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_alerts_tenant_status_occurrence_last",
        "alerts",
        ["tenant_id", "status", "occurrence_count", "last_occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_tenant_status_occurrence_last", table_name="alerts")
    op.drop_index("ix_alerts_tenant_status_severity_last", table_name="alerts")