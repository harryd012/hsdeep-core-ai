"""Site reconciliation tests; mock operations never modify a database."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/8a02d3f5b971_reconcile_site_topology.py"


def load(monkeypatch, columns=(), indexes=(), fks=()):
    spec = importlib.util.spec_from_file_location("site_reconciliation", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = MagicMock()
    operations.get_context.return_value.as_sql = False
    operations.get_bind.return_value.dialect.name = "postgresql"
    operations.get_bind.return_value.scalar.return_value = "public"
    inspector = SimpleNamespace(get_columns=lambda *a, **k: list(columns),
                                get_indexes=lambda *a, **k: list(indexes),
                                get_foreign_keys=lambda *a, **k: list(fks))
    monkeypatch.setattr(module, "op", operations)
    monkeypatch.setattr(module.sa, "inspect", lambda _: inspector)
    return module, operations


def test_additive_canonical_definitions(monkeypatch):
    m, op = load(monkeypatch, [dict(name="building", type=sa.VARCHAR(255))])
    m.upgrade()
    cols = {c.args[1].name: c.args[1] for c in op.add_column.call_args_list}
    assert set(cols) == set(m.COLUMN_LENGTHS) | {"parent_site_id", "site_metadata"}
    for name, length in m.COLUMN_LENGTHS.items():
        assert cols[name].type.length == length
        assert cols[name].nullable and cols[name].server_default is None
    assert isinstance(cols["parent_site_id"].type, sa.Uuid)
    assert cols["parent_site_id"].nullable
    assert isinstance(cols["site_metadata"].type, postgresql.JSONB)
    assert not cols["site_metadata"].nullable
    assert str(cols["site_metadata"].server_default.arg) == "{}"
    assert op.create_index.call_count == 3
    op.create_foreign_key.assert_called_once_with(
        "fk_sites_parent_site_id_sites", "sites", "sites", ["parent_site_id"], ["id"],
        source_schema="public", referent_schema="public", ondelete="SET NULL")
    op.drop_column.assert_not_called()
    op.alter_column.assert_not_called()


def test_reconciled_noop(monkeypatch):
    m, _ = load(monkeypatch)
    cols = [dict(name=n, type=sa.VARCHAR(size), nullable=True, default=None)
            for n, size in m.COLUMN_LENGTHS.items()]
    cols += [dict(name="parent_site_id", type=postgresql.UUID(), nullable=True, default=None),
             dict(name="site_metadata", type=postgresql.JSONB(), nullable=False, default="'{}'::jsonb")]
    indexes = [dict(name=f"ix_sites_{n}", column_names=[n], unique=False) for n in m.INDEX_COLUMNS]
    fk = dict(constrained_columns=["parent_site_id"], referred_table="sites",
              referred_columns=["id"], referred_schema="public", options={"ondelete": "SET NULL"})
    m, op = load(monkeypatch, cols, indexes, [fk])
    m.upgrade()
    op.add_column.assert_not_called()
    op.create_index.assert_not_called()
    op.create_foreign_key.assert_not_called()


@pytest.mark.parametrize("column", [
    dict(name="country", type=sa.VARCHAR(99), nullable=True),
    dict(name="parent_site_id", type=sa.Integer(), nullable=True),
    dict(name="site_metadata", type=postgresql.JSONB(), nullable=True, default="'{}'::jsonb"),
])
def test_conflicts_fail_before_ddl(monkeypatch, column):
    m, op = load(monkeypatch, [column])
    with pytest.raises(RuntimeError, match="Incompatible"):
        m.upgrade()
    op.execute.assert_not_called()
    op.add_column.assert_not_called()


def test_fk_conflict(monkeypatch):
    fk = dict(constrained_columns=["parent_site_id"], referred_table="sites",
              referred_columns=["id"], options={"ondelete": "CASCADE"})
    m, op = load(monkeypatch, fks=[fk])
    with pytest.raises(RuntimeError, match="foreign key"):
        m.upgrade()
    op.execute.assert_not_called()


def test_partial_index_conflict(monkeypatch):
    m, op = load(monkeypatch, indexes=[dict(name="ix_sites_city", column_names=["city"],
                  unique=False, dialect_options={"postgresql_where": "city IS NOT NULL"})])
    with pytest.raises(RuntimeError, match="index"):
        m.upgrade()
    op.execute.assert_not_called()


def test_downgrade_blocked(monkeypatch):
    m, op = load(monkeypatch)
    with pytest.raises(RuntimeError, match="Forward-only"):
        m.downgrade()
    assert not op.mock_calls
