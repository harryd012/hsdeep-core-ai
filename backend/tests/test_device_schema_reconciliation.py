"""Pure migration tests: no database writes or application fixtures required."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/7d91c2e4a860_reconcile_device_role_location.py"


def load(monkeypatch, columns=(), indexes=()):
    spec = importlib.util.spec_from_file_location("device_reconciliation", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = MagicMock()
    operations.get_context.return_value.as_sql = False
    operations.get_bind.return_value.dialect.name = "postgresql"
    operations.get_bind.return_value.scalar.return_value = "public"
    inspector = SimpleNamespace(get_columns=lambda *a, **k: list(columns),
                                get_indexes=lambda *a, **k: list(indexes))
    monkeypatch.setattr(module, "op", operations)
    monkeypatch.setattr(module.sa, "inspect", lambda _: inspector)
    return module, operations


def definitions(module):
    return [dict(name=n, type=sa.VARCHAR(size), nullable=True, default=None)
            for n, size in module.COLUMN_LENGTHS.items()]


def test_missing_columns_and_indexes_only(monkeypatch):
    m, op = load(monkeypatch)
    m.upgrade()
    assert op.add_column.call_count == 8
    assert op.create_index.call_count == 4
    for call in op.add_column.call_args_list:
        table, column = call.args
        assert table == "devices"
        assert column.type.length == m.COLUMN_LENGTHS[column.name]
        assert column.nullable and column.server_default is None
        assert not column.foreign_keys
        assert call.kwargs == {"schema": "public"}
    assert [c.args[0] for c in op.execute.call_args_list] == [
        "SET LOCAL lock_timeout = '5s'", "SET LOCAL statement_timeout = '30s'"]
    op.drop_column.assert_not_called()


def test_already_reconciled_is_noop(monkeypatch):
    m, _ = load(monkeypatch)
    cols = definitions(m)
    indexes = [dict(name=f"ix_devices_{n}", column_names=[n], unique=False)
               for n in m.INDEX_COLUMNS]
    m, op = load(monkeypatch, cols, indexes)
    m.upgrade()
    op.add_column.assert_not_called()
    op.create_index.assert_not_called()


def test_partial_repair_preserves_existing(monkeypatch):
    m, op = load(monkeypatch, [dict(name="role", type=sa.VARCHAR(50), nullable=True, default=None)])
    m.upgrade()
    assert op.add_column.call_count == 7
    assert all(c.args[1].name != "role" for c in op.add_column.call_args_list)


@pytest.mark.parametrize("changes", [dict(type=sa.VARCHAR(99)), dict(nullable=False), dict(default="'x'")])
def test_incompatible_column_fails_before_ddl(monkeypatch, changes):
    column = dict(name="role", type=sa.VARCHAR(50), nullable=True, default=None)
    column.update(changes)
    m, op = load(monkeypatch, [column])
    with pytest.raises(RuntimeError, match="Incompatible existing column"):
        m.upgrade()
    op.add_column.assert_not_called()
    op.execute.assert_not_called()


def test_incompatible_index_fails_before_ddl(monkeypatch):
    m, op = load(monkeypatch, indexes=[dict(name="ix_devices_role", column_names=["city"], unique=False)])
    with pytest.raises(RuntimeError, match="Incompatible existing index"):
        m.upgrade()
    op.add_column.assert_not_called()
    op.execute.assert_not_called()


def test_downgrade_cannot_destroy_data(monkeypatch):
    m, op = load(monkeypatch)
    with pytest.raises(RuntimeError, match="Forward-only"):
        m.downgrade()
    assert not op.mock_calls
