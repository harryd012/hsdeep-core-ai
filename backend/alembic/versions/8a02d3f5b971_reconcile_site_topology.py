"""Reconcile missing Site topology fields; preserve existing CMDB columns.

Revision ID: 8a02d3f5b971
Revises: 7d91c2e4a860
Canonical definitions: f2a1b3c4d5e6. Do not replay that revision: it
unconditionally adds columns already introduced by 1be605e3f990.
Online-only, PostgreSQL, additive. Downgrade cannot safely infer ownership.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "8a02d3f5b971"
down_revision = "7d91c2e4a860"
branch_labels = None
depends_on = None

COLUMN_LENGTHS = {
    "country": 100, "city": 100, "campus": 100,
    "datacenter_name": 100, "site_type": 50,
}
INDEX_COLUMNS = ("country", "city", "site_type")


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("Online schema inspection is required for this repair.")
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("This reconciliation requires PostgreSQL.")
    schema = bind.scalar(sa.text("SELECT current_schema()"))
    if not schema:
        raise RuntimeError("No effective schema; refusing schema repair.")
    inspector = sa.inspect(bind)
    columns = {c["name"]: c for c in inspector.get_columns("sites", schema=schema)}
    indexes = {i["name"]: i for i in inspector.get_indexes("sites", schema=schema)}
    fks = inspector.get_foreign_keys("sites", schema=schema)
    for name, length in COLUMN_LENGTHS.items():
        c = columns.get(name)
        if c is not None and (
            not isinstance(c["type"], sa.VARCHAR) or c["type"].length != length
            or not c["nullable"] or c.get("default") is not None
        ):
            raise RuntimeError(f"Incompatible existing column: {schema}.sites.{name}")
    parent = columns.get("parent_site_id")
    if parent is not None and (
        not isinstance(parent["type"], sa.Uuid) or not parent["nullable"]
        or parent.get("default") is not None
    ):
        raise RuntimeError("Incompatible existing parent_site_id")
    metadata = columns.get("site_metadata")
    if metadata is not None and (
        not isinstance(metadata["type"], postgresql.JSONB) or metadata["nullable"]
        or metadata.get("default") not in ("'{}'::jsonb", "'{}'")
    ):
        raise RuntimeError("Incompatible existing site_metadata")
    parent_fks = [f for f in fks if "parent_site_id" in f["constrained_columns"]]
    if any(
        f["constrained_columns"] != ["parent_site_id"]
        or f["referred_table"] != "sites" or f["referred_columns"] != ["id"]
        or f.get("referred_schema") not in (None, schema)
        or f.get("options", {}).get("ondelete") != "SET NULL"
        for f in parent_fks
    ):
        raise RuntimeError("Incompatible existing parent_site_id foreign key")
    for name in INDEX_COLUMNS:
        index = indexes.get(f"ix_sites_{name}")
        if index is not None:
            options = index.get("dialect_options", {})
            if (index["column_names"] != [name] or index["unique"]
                    or options.get("postgresql_where") is not None
                    or options.get("postgresql_include")
                    or options.get("postgresql_using", "btree") != "btree"):
                raise RuntimeError(f"Incompatible existing index: ix_sites_{name}")
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    for name, length in COLUMN_LENGTHS.items():
        if name not in columns:
            op.add_column("sites", sa.Column(name, sa.String(length), nullable=True), schema=schema)
    if parent is None:
        op.add_column("sites", sa.Column("parent_site_id", postgresql.UUID(as_uuid=True), nullable=True), schema=schema)
    if metadata is None:
        op.add_column("sites", sa.Column("site_metadata", postgresql.JSONB(), nullable=False, server_default="{}"), schema=schema)
    if not parent_fks:
        op.create_foreign_key("fk_sites_parent_site_id_sites", "sites", "sites",
                              ["parent_site_id"], ["id"], source_schema=schema,
                              referent_schema=schema, ondelete="SET NULL")
    for name in INDEX_COLUMNS:
        if f"ix_sites_{name}" not in indexes:
            op.create_index(f"ix_sites_{name}", "sites", [name], unique=False, schema=schema)


def downgrade() -> None:
    raise RuntimeError(
        "Forward-only reconciliation: automatic downgrade could delete site data. "
        "Use a separately reviewed recovery plan; do not stamp or drop columns."
    )
