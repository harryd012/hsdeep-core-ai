"""Pure unit tests for the deployment model (Section 19)."""

from __future__ import annotations

from app.core.deployment import (
    ALL_KEYS,
    MODES,
    get_mode,
    offline_eligible,
    sync_behaviour,
)


def test_all_modes_present():
    for key in ("saas", "private_cloud", "on_premises", "customer_hosted",
                "offline", "air_gapped", "hybrid", "edge"):
        assert key in MODES


def test_default_mode_is_saas():
    assert get_mode(None).key == "saas"
    assert get_mode("bogus").key == "saas"


def test_air_gapped_has_no_cloud_sync():
    mode = get_mode("air_gapped")
    assert mode.local_db is True
    assert mode.local_ai is True
    assert mode.cloud_sync_allowed is False
    assert mode.allow_sync() is False


def test_saas_requires_cloud_not_offline():
    assert get_mode("saas").requires_cloud is True
    assert offline_eligible("saas") is False


def test_offline_and_onprem_eligible_without_cloud():
    assert offline_eligible("offline") is True
    assert offline_eligible("on_premises") is True
    assert offline_eligible("hybrid") is True
    assert offline_eligible("air_gapped") is True


def test_hybrid_sync_behaviour():
    b = sync_behaviour("hybrid")
    assert b["sync_allowed"] is True
    assert b["local_buffer"] is True
    assert b["retry_replay"] is True
    assert b["duplicate_event_prevention"] is True
    assert b["conflict_resolution"] is True
    assert b["tenant_scoped_sync"] is True


def test_air_gapped_sync_behaviour_no_egress():
    b = sync_behaviour("air_gapped")
    assert b["sync_allowed"] is False
    # still keeps local-only capabilities
    assert b["local_ai"] is True
    assert b["local_backup"] is True
    assert b["secure_export_import"] is True


def test_edge_is_forwarder_only():
    mode = get_mode("edge")
    assert mode.local_ui is False
    assert mode.local_db is False
    assert mode.local_collectors is True
    assert mode.allow_sync() is True
