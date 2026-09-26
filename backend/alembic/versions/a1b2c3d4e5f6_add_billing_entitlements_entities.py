"""add billing entitlements entities

Revision ID: a1b2c3d4e5f6
Revises: 2c3d4e5f6a7b
Create Date: 2026-08-04 08:10:00.000000

Billing & Entitlements Foundation sprint (Epic A-E):

  * plans: global subscription tier catalog (Starter/Pro/Enterprise)
  * subscriptions: tenant-scoped subscription lifecycle (trialing/active/past_due/canceled)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "2c3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- plans table ----
    op.create_table(
        "plans",
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tier", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_monthly", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("price_annual", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("device_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sensor_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("api_rate_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collector_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seats", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("features_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("stripe_price_id_monthly", sa.String(255), nullable=True),
        sa.Column("stripe_price_id_annual", sa.String(255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_plans_name"),
        sa.UniqueConstraint("tier", name="uq_plans_tier"),
    )
    op.create_index(op.f("ix_plans_name"), "plans", ["name"], unique=True)
    op.create_index(op.f("ix_plans_tier"), "plans", ["tier"], unique=True)
    op.create_index(op.f("ix_plans_is_deleted"), "plans", ["is_deleted"], unique=False)

    # ---- subscriptions table ----
    op.create_table(
        "subscriptions",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="trialing"),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_interval", sa.String(10), nullable=False, server_default="month"),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "status", name="uq_subscriptions_tenant_status"),
    )
    op.create_index(op.f("ix_subscriptions_tenant_id"), "subscriptions", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_plan_id"), "subscriptions", ["plan_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"], unique=False)
    op.create_index(op.f("ix_subscriptions_stripe_subscription_id"), "subscriptions", ["stripe_subscription_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_stripe_customer_id"), "subscriptions", ["stripe_customer_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_is_deleted"), "subscriptions", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_table("plans")