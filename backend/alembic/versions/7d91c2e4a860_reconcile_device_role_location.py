"""Reconcile missing device role/location columns without changing existing data.

Revision ID: 7d91c2e4a860
Revises: e2e6b3b5e330

Canonical definitions: f2a1b3c4d5e6 and app.models.device.Device.
A legacy repair script advanced the revision without these DDL operations.
Do not replay that historical migration: it also duplicates existing columns.
This online-only repair validates existing objects, adds missing objects only,
and never drops columns. Downgrade is deliberately blocked: provenance of
pre-existing columns is unknown and subsequent values must not be destroyed.
"""
from alembic import op
import sqlalchemy as sa

revision = "7d91c2e4a860"
down_revision = "e2e6b3b5e330"
branch_labels = None
depends_on = None

COLUMN_LENGTHS = {
    "role": 50,
    "role_class": 30,
    "country": 100,
    "city": 100,
    "campus": 100,
    "building": 100,
    "datacenter_name": 100,
    "rack": 50,
}
INDEX_COLUMNS = ("role", "role_class", "country", "city")


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
    columns = {c["name"]: c for c in inspector.get_columns("devices", schema=schema)}
    indexes = {i["name"]: i for i in inspector.get_indexes("devices", schema=schema)}
    # Complete validation before the first DDL operation. Never silently accept
    # a same-name object with different semantics.
    for name, length in COLUMN_LENGTHS.items():
        if name not in columns:
            continue
        c = columns[name]
        if (not isinstance(c["type"], sa.VARCHAR)
                or c["type"].length != length
                or not c["nullable"] or c.get("default") is not None):
            raise RuntimeError(f"Incompatible existing column: {schema}.devices.{name}")
    for name in INDEX_COLUMNS:
        index = indexes.get(f"ix_devices_{name}")
        if index is not None:
            options = index.get("dialect_options", {})
            if (index["column_names"] != [name] or index["unique"]
                    or options.get("postgresql_where") is not None
                    or options.get("postgresql_include")
                    or options.get("postgresql_using", "btree") != "btree"):
                raise RuntimeError(f"Incompatible existing index: ix_devices_{name}")
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    for name, length in COLUMN_LENGTHS.items():
        if name not in columns:
            op.add_column("devices", sa.Column(name, sa.String(length), nullable=True), schema=schema)
    for name in INDEX_COLUMNS:
        if f"ix_devices_{name}" not in indexes:
            op.create_index(f"ix_devices_{name}", "devices", [name], unique=False, schema=schema)


def downgrade() -> None:
    raise RuntimeError(
        "Forward-only reconciliation: automatic downgrade could delete device data. "
        "Use a separately reviewed recovery plan; do not stamp or drop these columns."
    )
