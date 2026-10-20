"""Surgical reconciliation: model-canonical columns/indexes added idempotently.

Revision ID: c4d9a2b7e105
Revises: b7d2e4f6a8c1 (head)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4d9a2b7e105"
down_revision = "b7d2e4f6a8c1"
branch_labels = None
depends_on = None


def _cols(table, schema):
    insp = sa.inspect(op.get_bind())
    return {c["name"]: c for c in insp.get_columns(table, schema=schema)}


def _indexes(table, schema):
    insp = sa.inspect(op.get_bind())
    return {i["name"]: i for i in insp.get_indexes(table, schema=schema)}


def _constraints(table, schema):
    insp = sa.inspect(op.get_bind())
    return {(c.get("name"), "unique") for c in insp.get_unique_constraints(table, schema=schema)}


def _guard():
    if op.get_context().as_sql:
        raise RuntimeError("Online schema inspection is required for this repair.")
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("This reconciliation requires PostgreSQL.")
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not schema:
        raise RuntimeError("No effective schema; refusing schema repair.")
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    return bind, schema


def upgrade() -> None:
    bind, schema = _guard()

    # -- topology_links: additive columns ----------------------------------
    tl = _cols("topology_links", schema)
    if "is_redundant" not in tl:
        op.add_column(
            "topology_links",
            sa.Column("is_redundant", sa.Boolean(), nullable=False, server_default="false"),
            schema=schema,
        )
    if "attributes" not in tl:
        op.add_column(
            "topology_links",
            sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
            schema=schema,
        )
    if "deleted_at" not in tl:
        op.add_column(
            "topology_links",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            schema=schema,
        )
    if "is_deleted" not in tl:
        op.add_column(
            "topology_links",
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
            schema=schema,
        )
    tl_idx = _indexes("topology_links", schema)
    if "ix_topology_links_is_deleted" not in tl_idx:
        op.create_index(
            "ix_topology_links_is_deleted", "topology_links", ["is_deleted"],
            unique=False, schema=schema,
        )
    if "ix_topology_links_tenant_status" not in tl_idx:
        op.create_index(
            "ix_topology_links_tenant_status", "topology_links",
            ["tenant_id", "oper_status"], unique=False, schema=schema,
        )
    if ("uq_topology_links_tenant_devices_name", "unique") not in _constraints("topology_links", schema):
        op.create_unique_constraint(
            "uq_topology_links_tenant_devices_name", "topology_links",
            ["tenant_id", "device_a_id", "device_b_id", "name"], schema=schema,
        )

    # -- sites.building: safe narrow 255 -> 100 -----------------------------
    mx = bind.scalar(sa.text("SELECT max(length(building)) FROM sites"))
    if mx is not None and mx > 100:
        raise RuntimeError(f"sites.building holds {mx}-char values; narrow unsafe.")
    op.alter_column(
        "sites", "building", existing_type=sa.VARCHAR(255),
        type_=sa.String(100), existing_nullable=True, schema=schema,
    )

    # -- tickets.ai_execution_confidence: float -> integer ------------------
    bad = bind.scalar(sa.text(
        "SELECT count(*) FROM tickets "
        "WHERE ai_execution_confidence IS NOT NULL "
        "AND ai_execution_confidence <> floor(ai_execution_confidence)"
    ))
    if bad:
        raise RuntimeError(f"{bad} tickets rows carry fractional ai_execution_confidence.")
    with op.batch_alter_table("tickets", schema=schema) as batch:
        batch.alter_column(
            "ai_execution_confidence", existing_type=sa.Float(),
            type_=sa.Integer(), existing_nullable=True,
        )

    # -- incidents.status: safe widen 20 -> 30 ------------------------------
    mx = bind.scalar(sa.text("SELECT max(length(status)) FROM incidents"))
    if mx is not None and mx > 30:
        raise RuntimeError(f"incidents.status holds {mx}-char values; widen unsafe.")
    op.alter_column(
        "incidents", "status", existing_type=sa.VARCHAR(20),
        type_=sa.String(30), existing_nullable=False, schema=schema,
    )


def downgrade() -> None:
    bind, schema = _guard()
    insp = sa.inspect(bind)
    tl_uq = {(c.get("name"), "unique") for c in insp.get_unique_constraints("topology_links", schema=schema)}
    if ("uq_topology_links_tenant_devices_name", "unique") in tl_uq:
        op.drop_constraint(
            "uq_topology_links_tenant_devices_name", "topology_links",
            type_="unique", schema=schema,
        )
    tl_idx = {i["name"] for i in insp.get_indexes("topology_links", schema=schema)}
    if "ix_topology_links_tenant_status" in tl_idx:
        op.drop_index("ix_topology_links_tenant_status", "topology_links", schema=schema)
    if "ix_topology_links_is_deleted" in tl_idx:
        op.drop_index("ix_topology_links_is_deleted", "topology_links", schema=schema)
    tl = {c["name"] for c in insp.get_columns("topology_links", schema=schema)}
    for col in ("is_deleted", "deleted_at", "attributes", "is_redundant"):
        if col in tl:
            op.drop_column("topology_links", col, schema=schema)
    op.alter_column(
        "incidents", "status", existing_type=sa.VARCHAR(30),
        type_=sa.String(20), existing_nullable=False, schema=schema,
    )
    with op.batch_alter_table("tickets", schema=schema) as batch:
        batch.alter_column(
            "ai_execution_confidence", existing_type=sa.Integer(),
            type_=sa.Float(), existing_nullable=True,
        )
    op.alter_column(
        "sites", "building", existing_type=sa.VARCHAR(100),
        type_=sa.String(255), existing_nullable=True, schema=schema,
    )
