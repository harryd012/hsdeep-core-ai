"""
Model registration + metadata consistency regression tests.

Guards the reconciliation sprint:

* the six previously-unregistered tables are now visible to ``Base.metadata``
  (so Alembic autogenerate no longer proposes ``DROP TABLE`` for them);
* model-level index / unique / FK declarations match the live database.

Pure metadata tests - no database connection required.
"""
from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.models import Base


REGISTERED_TABLES = {
    "collector_credentials",
    "collector_enrollment_tokens",
    "discovery_probe_jobs",
    "discovery_probe_hosts",
    "discovery_scopes",
    "tenant_custom_properties",
}


def _index_names(table: str) -> set[str]:
    return {ix.name for ix in Base.metadata.tables[table].indexes}


def test_six_tables_are_registered_in_metadata():
    assert REGISTERED_TABLES <= set(Base.metadata.tables)


def test_credential_hash_unique_constraint_is_named_not_column_level():
    table = Base.metadata.tables["collector_credentials"]
    # column-level unique=True was removed to avoid a duplicate unnamed
    # constraint; uniqueness is enforced by the named constraint instead.
    assert not table.c.credential_hash.unique
    names = {c.name for c in table.constraints if isinstance(c, UniqueConstraint)}
    assert "uq_collector_credentials_credential_hash" in names


def test_token_hash_unique_constraint_is_named_not_column_level():
    table = Base.metadata.tables["collector_enrollment_tokens"]
    assert not table.c.token_hash.unique
    names = {c.name for c in table.constraints if isinstance(c, UniqueConstraint)}
    assert "uq_collector_enrollment_tokens_token_hash" in names


def test_bgp_neighbor_resource_index_name_matches_database():
    names = _index_names("bgp_neighbors")
    assert "ix_bgp_neighbors_resource_id" in names
    assert "ix_bgp_neighbors_resource" not in names


def test_network_routes_has_no_redundant_single_column_state_index():
    names = _index_names("network_routes")
    assert "ix_network_routes_state" not in names
    # tenant-scoped composite state index is retained
    assert "ix_network_routes_tenant_state" in names


def test_ticket_has_no_obsolete_servicenow_columns():
    cols = set(Base.metadata.tables["tickets"].columns.keys())
    assert "servicenow_sys_id" not in cols
    assert "servicenow_sync_status" not in cols


def test_ticket_link_is_the_external_identity_mapping():
    cols = set(Base.metadata.tables["ticket_links"].columns.keys())
    assert "external_id" in cols
    assert "external_number" in cols


def test_sites_parent_site_fk_is_set_null():
    fk = next(iter(Base.metadata.tables["sites"].c.parent_site_id.foreign_keys))
    assert fk.ondelete == "SET NULL"


def test_two_softdelete_tables_mirror_live_db_without_is_deleted_index():
    # migrations a3f7c1d84b26 / b8e42d9c7a15 omitted these indexes; the models
    # match the live schema (no single-column is_deleted index).
    for table in ("discovery_scopes", "tenant_custom_properties"):
        assert f"ix_{table}_is_deleted" not in _index_names(table)
