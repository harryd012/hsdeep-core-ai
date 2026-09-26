"""
Device Action Plane unit tests (Phase 1).

Pure unit tests - no database, no network, no async DB fixtures (the
passing-test pattern used by test_prtg_collector.py). Device-level I/O is
monkeypatched at the provider module seam; the policy engine and
capability derivation are exercised as the pure functions they are.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.actions  # registers providers
from app.actions.base import (
    ACTION_CATALOG,
    IMPLEMENTED_ACTIONS,
    ActionClassification,
    ActionNotSupported,
    ActionProviderRegistry,
    DeviceActionType,
)
from app.actions.policy import evaluate_action_policy, catalog_for_api
from app.actions.verification import plan_for, run_verification
from app.repositories.device_capability_repository import derive_capabilities

PING_OK = {"supports_ping": True, "supports_snmp": False}
SNMP_OK = {"supports_ping": True, "supports_snmp": True}


@pytest.fixture
def stub_transports(monkeypatch):
    """Register minimal IMPLEMENTED providers for the ssh/vendor transports
    so the policy engine's CLASSIFICATION routing can be exercised (the real
    adapters ship in later phases, which will add their actions to
    IMPLEMENTED_ACTIONS - mirrored here).

    The registry is snapshotted and restored on teardown - no leakage into
    other tests, and no device I/O."""
    from app.actions import policy as policy_module
    from app.actions.base import (
        ACTION_CATALOG,
        IMPLEMENTED_ACTIONS,
        ActionClassification,
        ActionProviderRegistry,
        DeviceActionProvider,
    )

    saved = dict(ActionProviderRegistry._providers)

    class _StubProvider(DeviceActionProvider):
        display_name = "stub"
        implemented = True

        def supported_actions(self):
            return frozenset(DeviceActionType)

        async def execute(self, action, target, params=None):
            raise ActionNotSupported("stub transport - test double")

    class _StubSsh(_StubProvider):
        provider_kind = "ssh"

    class _StubVendor(_StubProvider):
        provider_kind = "vendor"

    ActionProviderRegistry._providers["ssh"] = _StubSsh
    ActionProviderRegistry._providers["vendor"] = _StubVendor

    # Mirror what a future adapter phase does: its shipped actions join
    # IMPLEMENTED_ACTIONS. Here we ship the stubbed state-changing set.
    extended = frozenset(set(IMPLEMENTED_ACTIONS) | {
        a for a, s in ACTION_CATALOG.items()
        if s.classification != ActionClassification.READ_ONLY
    })
    monkeypatch.setattr(policy_module, "IMPLEMENTED_ACTIONS", extended)
    yield
    ActionProviderRegistry._providers = saved


class TestCatalog:
    def test_every_action_has_a_spec(self):
        assert set(ACTION_CATALOG) == set(DeviceActionType)

    def test_every_spec_has_classification_and_capability(self):
        for spec in ACTION_CATALOG.values():
            assert spec.classification in set(ActionClassification)
            assert spec.required_capability.startswith("supports_")

    def test_read_only_actions_are_implemented_diagnostics(self):
        read_only = {
            a for a, s in ACTION_CATALOG.items()
            if s.classification == ActionClassification.READ_ONLY
        }
        # Every implemented READ_ONLY action is a shipped diagnostic; the
        # only implemented non-read-only actions are the approval-gated
        # PRTG pause/resume pair (Phase 2).
        assert IMPLEMENTED_ACTIONS & read_only == {
            DeviceActionType.PING,
            DeviceActionType.HEALTH_CHECK,
            DeviceActionType.GET_STATUS,
            DeviceActionType.GET_INTERFACES,
            DeviceActionType.GET_FIRMWARE,
            DeviceActionType.PRTG_QUERY_SENSOR,
            DeviceActionType.PRTG_QUERY_DEVICE,
            DeviceActionType.CISCO_QUERY_HEALTH,
            DeviceActionType.CISCO_QUERY_PROCESSES,
            DeviceActionType.ARUBA_QUERY_HEALTH,
        }
        non_read_only = IMPLEMENTED_ACTIONS - read_only
        assert non_read_only == {
            DeviceActionType.PRTG_PAUSE_SENSOR,
            DeviceActionType.PRTG_RESUME_SENSOR,
        }
        # The not-yet-implemented read-only actions are catalogued but their
        # transports (ssh) are not shipped - policy must deny them.
        assert DeviceActionType.COLLECT_DIAGNOSTICS in read_only
        assert DeviceActionType.COLLECT_DIAGNOSTICS not in IMPLEMENTED_ACTIONS

    def test_all_state_changing_actions_require_approval_or_worse(self):
        """Every action that is not READ_ONLY must be approval-gated (the
        only auto-executable class is READ_ONLY)."""
        for action, spec in ACTION_CATALOG.items():
            if spec.classification != ActionClassification.READ_ONLY:
                assert spec.classification in (
                    ActionClassification.LOW_RISK,
                    ActionClassification.CHANGE_REQUIRING_APPROVAL,
                    ActionClassification.HIGH_RISK,
                    ActionClassification.DESTRUCTIVE,
                )
        # And every implemented READ_ONLY action is a diagnostic/query.
        for action in IMPLEMENTED_ACTIONS:
            if ACTION_CATALOG[action].classification == ActionClassification.READ_ONLY:
                assert action in (
                    DeviceActionType.PING,
                    DeviceActionType.HEALTH_CHECK,
                    DeviceActionType.GET_STATUS,
                    DeviceActionType.GET_INTERFACES,
                    DeviceActionType.GET_FIRMWARE,
                    DeviceActionType.PRTG_QUERY_SENSOR,
                    DeviceActionType.PRTG_QUERY_DEVICE,
                    DeviceActionType.CISCO_QUERY_HEALTH,
                    DeviceActionType.CISCO_QUERY_PROCESSES,
                    DeviceActionType.ARUBA_QUERY_HEALTH,
                )

    def test_catalog_for_api_shape(self):
        entries = catalog_for_api()
        assert len(entries) == len(ACTION_CATALOG)
        for e in entries:
            assert {"action", "classification", "provider_kind",
                    "required_capability", "description", "implemented"} <= set(e)
        by_action = {e["action"]: e for e in entries}
        assert by_action["PING"]["implemented"] is True
        assert by_action["REBOOT_DEVICE"]["implemented"] is False
        assert by_action["ROLLBACK_FIRMWARE"]["classification"] == "DESTRUCTIVE"

    def test_registry_has_phase1_providers(self):
        assert ActionProviderRegistry.try_get("icmp") is not None
        assert ActionProviderRegistry.try_get("snmp") is not None
        assert ActionProviderRegistry.try_get("ssh") is None
        assert ActionProviderRegistry.try_get("vendor") is None


class TestPolicyEngine:
    def test_read_only_capable_auto_executes(self):
        d = evaluate_action_policy(action=DeviceActionType.PING, capabilities=PING_OK)
        assert d.allowed and d.auto_execute and not d.requires_approval

    def test_read_only_without_capability_denied(self):
        d = evaluate_action_policy(
            action=DeviceActionType.PING,
            capabilities={"supports_ping": None, "supports_snmp": True},
        )
        assert not d.allowed and d.status == "policy_denied"
        assert "unknown" in d.reason  # honest tri-state, never guessed

    def test_read_only_capability_explicitly_false_denied(self):
        d = evaluate_action_policy(
            action=DeviceActionType.GET_STATUS, capabilities={"supports_snmp": False}
        )
        assert not d.allowed and "disabled" in d.reason

    def test_tenant_policy_can_require_approval_for_read_only(self):
        d = evaluate_action_policy(
            action=DeviceActionType.PING, capabilities=PING_OK,
            tenant_policy={"allow_auto_read_only": False},
        )
        assert d.allowed and d.requires_approval and not d.auto_execute

    def test_unimplemented_provider_denied_up_front(self):
        # COLLECT_DIAGNOSTICS is read-only but its SSH adapter has not
        # shipped - policy denies honestly instead of failing at exec time.
        d = evaluate_action_policy(
            action=DeviceActionType.COLLECT_DIAGNOSTICS,
            capabilities={"supports_ssh": True},
        )
        assert not d.allowed and "not shipped" in d.reason

    def test_low_risk_requires_approval_by_default(self, stub_transports):
        d = evaluate_action_policy(
            action=DeviceActionType.SYNC_CONFIGURATION,
            capabilities={"supports_config": True},
        )
        assert d.allowed and d.requires_approval and not d.auto_execute

    def test_low_risk_auto_only_with_explicit_tenant_opt_in(self, stub_transports):
        d = evaluate_action_policy(
            action=DeviceActionType.SYNC_CONFIGURATION,
            capabilities={"supports_config": True},
            tenant_policy={"allow_auto_low_risk": True},
        )
        assert d.allowed and d.auto_execute

    def test_state_change_requires_approval(self, stub_transports):
        d = evaluate_action_policy(
            action=DeviceActionType.RESTART_SERVICE,
            capabilities={"supports_ssh": True},
        )
        assert d.allowed and d.requires_approval and d.status == "awaiting_approval"

    def test_high_risk_denied_outside_maintenance_window(self, stub_transports):
        d = evaluate_action_policy(
            action=DeviceActionType.REBOOT_DEVICE,
            capabilities={"supports_reboot": True},
        )
        assert not d.allowed and d.maintenance_window_required
        assert "maintenance window" in d.reason

    def test_high_risk_allowed_inside_maintenance_window(self, stub_transports):
        now = datetime.now(timezone.utc)
        windows = [{"starts_at": now - timedelta(hours=1),
                    "ends_at": now + timedelta(hours=1)}]
        d = evaluate_action_policy(
            action=DeviceActionType.REBOOT_DEVICE,
            capabilities={"supports_reboot": True},
            maintenance_windows=windows, now=now,
        )
        assert d.allowed and d.requires_approval and d.required_role == "Admin"

    def test_maintenance_window_accepts_iso_strings(self, stub_transports):
        now = datetime.now(timezone.utc)
        windows = [{"starts_at": (now - timedelta(minutes=5)).isoformat(),
                    "ends_at": (now + timedelta(minutes=5)).isoformat()}]
        d = evaluate_action_policy(
            action=DeviceActionType.REBOOT_DEVICE,
            capabilities={"supports_reboot": True},
            maintenance_windows=windows, now=now,
        )
        assert d.allowed and d.requires_approval

    def test_destructive_never_auto_even_in_window(self, stub_transports):
        now = datetime.now(timezone.utc)
        windows = [{"starts_at": now - timedelta(hours=1),
                    "ends_at": now + timedelta(hours=1)}]
        d = evaluate_action_policy(
            action=DeviceActionType.ROLLBACK_FIRMWARE,
            capabilities={"supports_rollback": True},
            maintenance_windows=windows, now=now,
        )
        assert d.allowed and d.requires_approval and not d.auto_execute
        assert d.required_role == "Admin"
        assert not d.maintenance_window_required  # approval-gated, no window rule

    def test_destructive_denied_without_capability(self, stub_transports):
        d = evaluate_action_policy(
            action=DeviceActionType.ROLLBACK_FIRMWARE, capabilities={}
        )
        assert not d.allowed


class TestCapabilityDerivation:
    """Monitoring credentials NEVER imply configuration privileges."""

    @staticmethod
    def _device(**kw):
        base = dict(ip_address="10.0.0.5", hostname=None, site_id=None,
                    monitoring_source_id=None)
        base.update(kw)
        return SimpleNamespace(**base)

    @staticmethod
    def _source(**kw):
        base = dict(protocol="snmp", is_active=True, source_type="snmp")
        base.update(kw)
        return SimpleNamespace(**base)

    def test_address_implies_ping(self):
        caps = derive_capabilities(self._device(), None)
        assert caps["supports_ping"] is True

    def test_prtg_source_implies_monitoring_actions(self):
        from app.collectors.prtg import PRTGCollector

        caps = derive_capabilities(
            self._device(), self._source(protocol="https", source_type="prtg")
        )
        assert caps["supports_monitoring_actions"] is True

    def test_non_prtg_source_does_not_imply_monitoring_actions(self):
        caps = derive_capabilities(
            self._device(), self._source(protocol="snmp", source_type="snmp")
        )
        assert caps["supports_monitoring_actions"] is None

    def test_no_address_no_ping(self):
        caps = derive_capabilities(self._device(ip_address=None, hostname=None), None)
        assert caps["supports_ping"] is None

    def test_snmp_source_implies_snmp(self):
        caps = derive_capabilities(self._device(), self._source(protocol="snmp"))
        assert caps["supports_snmp"] is True

    def test_https_source_implies_rest(self):
        caps = derive_capabilities(self._device(), self._source(protocol="https"))
        assert caps["supports_rest"] is True

    def test_inactive_source_grants_nothing(self):
        caps = derive_capabilities(self._device(), self._source(is_active=False))
        assert caps["supports_snmp"] is None and caps["supports_rest"] is None

    def test_state_change_capabilities_never_derived(self):
        caps = derive_capabilities(self._device(), self._source(protocol="snmp"))
        for flag in ("supports_ssh", "supports_config", "supports_reboot",
                     "supports_firmware", "supports_backup", "supports_rollback"):
            assert caps[flag] is None, f"{flag} must never be derived"
class TestIcmpProvider:
    """Ping transport is REUSED from ConnectivityCollector; here we test the
    provider's contract (classification dispatch, clamping, honest failure)."""

    @pytest.fixture
    def provider(self):
        from app.actions.providers.icmp import IcmpActionProvider
        return IcmpActionProvider()

    @pytest.fixture
    def target(self):
        from app.actions.base import ActionTarget
        import uuid as _uuid
        return ActionTarget(
            tenant_id=_uuid.uuid4(), device_id=_uuid.uuid4(), name="sw-core",
            ip_address="10.0.0.5",
        )

    async def test_ping_success_passes_through_probe_result(self, provider, target, monkeypatch):
        async def fake_icmp(host, count, timeout):
            return {"ok": True, "latency_ms": 1.2, "packet_loss_pct": 0.0}
        monkeypatch.setattr("app.actions.providers.icmp._icmp_result", fake_icmp)
        result = await provider.execute(DeviceActionType.PING, target, {"count": 4})
        assert result.success and result.output["ok"]
        assert result.output["latency_ms"] == 1.2
        assert result.output["address"] == "10.0.0.5"
        assert result.provider == "icmp"

    async def test_unreachable_is_failure_not_exception(self, provider, target, monkeypatch):
        async def fake_icmp(host, count, timeout):
            return {"ok": False, "latency_ms": None, "packet_loss_pct": 100.0}
        monkeypatch.setattr("app.actions.providers.icmp._icmp_result", fake_icmp)
        result = await provider.execute(DeviceActionType.PING, target)
        assert not result.success and result.error

    async def test_health_check_adds_verdict(self, provider, target, monkeypatch):
        async def fake_icmp(host, count, timeout):
            return {"ok": True}
        monkeypatch.setattr("app.actions.providers.icmp._icmp_result", fake_icmp)
        result = await provider.execute(DeviceActionType.HEALTH_CHECK, target)
        assert result.output["verdict"] == "reachable"

    async def test_no_address_fails_honestly(self, provider, target):
        target.ip_address = None
        target.hostname = None
        result = await provider.execute(DeviceActionType.PING, target)
        assert not result.success and "no IP address" in result.error

    async def test_unsupported_action_raises(self, provider, target):
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.GET_STATUS, target)

    async def test_params_are_clamped(self, provider, target, monkeypatch):
        seen = {}

        async def fake_icmp(host, count, timeout):
            seen.update(count=count, timeout=timeout)
            return {"ok": True}
        monkeypatch.setattr("app.actions.providers.icmp._icmp_result", fake_icmp)
        await provider.execute(
            DeviceActionType.PING, target, {"count": 999, "timeout_seconds": 999}
        )
        assert seen["count"] == 10 and seen["timeout"] == 10.0


class TestSnmpProvider:
    """SNMP transport is REUSED from SNMPCollector (pysnmp); these tests
    monkeypatch the collector's transport methods, so no UDP socket opens."""

    @pytest.fixture
    def provider(self):
        from app.actions.providers.snmp import SnmpActionProvider
        return SnmpActionProvider()

    @pytest.fixture
    def target(self):
        from app.actions.base import ActionTarget
        import uuid as _uuid
        return ActionTarget(
            tenant_id=_uuid.uuid4(), device_id=_uuid.uuid4(), name="fw-01",
            ip_address="10.0.0.9",
            connection=SimpleNamespace(hostname="10.0.0.9", port=161),
        )

    def test_extract_firmware_finds_version(self):
        from app.actions.providers.snmp import extract_firmware
        assert extract_firmware("FortiGate-60F v7.4.1 build2463") == "7.4.1"
        assert extract_firmware("Cisco IOS Version 15.2(7)E") == "15.2"
        assert extract_firmware("no version here") is None
        assert extract_firmware("") is None

    async def test_get_status_returns_real_fields(self, provider, target, monkeypatch):
        async def fake_get(self, *oids):
            return {
                ".1.3.6.1.2.1.1.1.0": "FortiGate-60F v7.4.1",
                ".1.3.6.1.2.1.1.5.0": "fw-01",
                ".1.3.6.1.2.1.1.3.0": "123456",
            }
        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._get", fake_get)
        result = await provider.execute(DeviceActionType.GET_STATUS, target)
        assert result.success
        assert result.output["sys_name"] == "fw-01"
        assert result.output["firmware"] == "7.4.1"

    async def test_get_firmware_only_needs_sysdescr(self, provider, target, monkeypatch):
        async def fake_get(self, *oids):
            return {".1.3.6.1.2.1.1.1.0": "Switch OS version 2.5.9"}
        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._get", fake_get)
        result = await provider.execute(DeviceActionType.GET_FIRMWARE, target)
        assert result.success and result.output["firmware"] == "2.5.9"

    async def test_get_interfaces_maps_status_codes(self, provider, target, monkeypatch):
        columns = {
            ".1.3.6.1.2.1.2.2.1.2": [(".1.3.6.1.2.1.2.2.1.2.1", "port1")],
            ".1.3.6.1.2.1.2.2.1.8": [(".1.3.6.1.2.1.2.2.1.8.1", "1")],
            ".1.3.6.1.2.1.2.2.1.7": [(".1.3.6.1.2.1.2.2.1.7.1", "1")],
        }

        async def fake_walk(self, base_oid):
            return columns.get(base_oid, [])

        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._walk", fake_walk)
        result = await provider.execute(DeviceActionType.GET_INTERFACES, target)
        assert result.success
        assert result.output["count"] == 1
        iface = result.output["interfaces"][0]
        assert iface["name"] == "port1"
        assert iface["oper_status"] == "up" and iface["admin_status"] == "up"

    async def test_no_connection_raises_honest_error(self, provider, target):
        target.connection = None
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.GET_STATUS, target)

    async def test_get_config_status_not_supported_yet(self, provider, target):
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.GET_CONFIG_STATUS, target)

    async def test_empty_snmp_response_is_failure(self, provider, target, monkeypatch):
        async def fake_get(self, *oids):
            return {}
        monkeypatch.setattr("app.collectors.snmp.SNMPCollector._get", fake_get)
        result = await provider.execute(DeviceActionType.GET_STATUS, target)
        assert not result.success and "no response" in result.error.lower()


class TestVerification:
    def test_ping_plan_requires_independent_recheck(self):
        from app.actions.base import ActionTarget
        import uuid as _uuid
        t = ActionTarget(tenant_id=_uuid.uuid4(), device_id=_uuid.uuid4(), name="x")
        assert plan_for(DeviceActionType.PING, t) == ["independent_reachability_recheck"]
        assert plan_for(DeviceActionType.GET_STATUS, t) == ["response_carries_real_device_data"]

    async def test_all_probes_must_pass(self):
        from app.actions.verification import run_verification

        async def ok():
            return True, {"a": 1}

        async def bad():
            return False, {"b": 2}

        outcome = await run_verification([("one", ok), ("two", bad)])
        assert not outcome.passed
        assert len(outcome.steps) == 2

    async def test_raising_probe_is_a_failed_step_never_a_crash(self):
        from app.actions.verification import run_verification

        async def boom():
            raise RuntimeError("probe blew up")

        async def ok():
            return True, {}

        outcome = await run_verification([("boom", boom), ("ok", ok)])
        assert not outcome.passed
        assert "RuntimeError" in outcome.steps[0].detail["error"]
        assert outcome.to_dict()["passed"] is False

    async def test_single_passing_probe_verifies(self):
        from app.actions.verification import run_verification

        async def ok():
            return True, {"recheck_ok": True}

        outcome = await run_verification([("recheck", ok)])
        assert outcome.passed and outcome.to_dict()["passed"] is True
