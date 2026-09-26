"""
Tests for the WMI collector (Phase 3B §2): WMI as a first-class source_type.

WMI already existed as an implementation - what was missing was a registry entry.
So these tests concentrate on the things that ARE different for a WMI source
(registration, identity, operator-facing wording, inherited capability) rather
than re-testing `normalize()`, which is shared by inheritance and already covered
by tests/test_windows_winrm_collector.py. The sharing itself is asserted below,
because "reuse, don't fork" is the whole point of this design.

No real Windows host is contacted.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.collectors.base import CollectorConfigError, CollectorRegistry
from app.collectors.windows_profiles import select_profile
from app.collectors.windows_winrm import WindowsWinRMCollector, reset_circuits
from app.collectors.wmi import WMICollector
from app.services.topology_graph_service import TOPOLOGY_CAPABLE_SOURCE_TYPES

# Importing the service package is what runs the registration decorators.
import app.services.monitoring  # noqa: F401  (registration side effects)


def _source(config=None, **overrides):
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="wmi-test",
        source_type="wmi",
        hostname="10.0.0.5",
        ip_address="10.0.0.5",
        port=None,
        protocol="http",
        snmp_version=None,
        username="CORP\\svc-monitor",
        api_key=None,
        password="secret-password",   # never asserted into any output
        config=config or {},
        verify_ssl=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _collector(**kwargs) -> WMICollector:
    reset_circuits()
    return WMICollector(_source(**kwargs))


class TestRegistration:
    """The gap this phase closed: `wmi` was a declared source_type with no
    collector, so CollectorRegistry.get("wmi") raised ValueError."""

    def test_wmi_is_registered(self):
        assert CollectorRegistry.get("wmi") is WMICollector

    def test_windows_winrm_still_registered(self):
        assert CollectorRegistry.get("windows_winrm") is WindowsWinRMCollector

    def test_registering_wmi_did_not_clobber_windows_winrm(self):
        """CollectorRegistry.register MUTATES `cls.source_type`. Registering the
        same class twice would leave one source_type overwritten, silently
        pointing two source types at one identity. A subclass avoids that -
        this is the regression guard for it."""
        assert WMICollector.source_type == "wmi"
        assert WindowsWinRMCollector.source_type == "windows_winrm"
        assert WMICollector is not WindowsWinRMCollector

    def test_wmi_is_listed_in_registry_types(self):
        assert "wmi" in {c.source_type for c in CollectorRegistry.list_types()}

    def test_wmi_is_declared_implemented(self):
        assert WMICollector.implemented is True


class TestReuseNotFork:
    """WMI must be the SAME implementation, not a parallel one."""

    def test_wmi_subclasses_the_windows_collector(self):
        assert issubclass(WMICollector, WindowsWinRMCollector)

    def test_shared_methods_are_the_same_functions(self):
        # Identity, not just equality: a future edit to the WMI path cannot
        # silently diverge from the WinRM path.
        assert WMICollector.collect is WindowsWinRMCollector.collect
        assert WMICollector.normalize is WindowsWinRMCollector.normalize
        assert WMICollector.authenticate is WindowsWinRMCollector.authenticate
        assert WMICollector._run_wmi is WindowsWinRMCollector._run_wmi
        assert WMICollector._build_protocol is WindowsWinRMCollector._build_protocol

    def test_only_identity_and_label_are_overridden(self):
        """No behaviour is forked: the only names this class adds beyond the
        base are the display/label identity. `source_type` (set by the registry
        decorator) and `_abc_impl` (ABCMeta) are framework bookkeeping."""
        overridden = {
            name for name in vars(WMICollector)
            if not name.startswith("_") and name != "source_type"
        }
        assert overridden == {"display_name", "implemented", "transport_label"}

    def test_wmi_reuses_the_same_wql_profiles(self):
        """Proves this really is WMI: the collected queries are Win32_* WQL."""
        profile = select_profile(None)
        queries = " ".join(profile.wmi_queries.values())
        assert "Win32_ComputerSystem" in queries
        assert "Win32_Processor" in queries
        assert profile.wmi_queries, "WMI profile must define queries"
        for wql in profile.wmi_queries.values():
            assert wql.upper().startswith("SELECT")


class TestIdentityAndWording:

    def test_display_name_is_wmi(self):
        assert WMICollector.display_name == "Windows (WMI)"
        assert WindowsWinRMCollector.display_name == "Windows (WinRM)"

    def test_transport_label_is_wmi(self):
        assert WMICollector.transport_label == "WMI"
        assert WindowsWinRMCollector.transport_label == "WinRM"

    def test_config_error_names_wmi_not_winrm(self):
        """An operator who configured a WMI source should not be told about
        'WinRM' - the label has to follow the source type."""
        collector = WMICollector(_source(hostname=None))
        with pytest.raises(CollectorConfigError) as exc:
            collector._require_config()
        message = str(exc.value)
        assert "WMI" in message
        assert "WinRM" not in message

    def test_auth_error_names_wmi(self):
        collector = WMICollector(_source(config={"auth_type": "bogus"}))
        with pytest.raises(CollectorConfigError) as exc:
            collector._auth_type()
        assert "WMI" in str(exc.value)

    def test_winrm_source_keeps_winrm_wording(self):
        """The shared code must not have been silently re-worded for everyone."""
        collector = WindowsWinRMCollector(
            _source(hostname=None, source_type="windows_winrm")
        )
        with pytest.raises(CollectorConfigError) as exc:
            collector._require_config()
        assert "WinRM" in str(exc.value)

    def test_missing_config_lists_the_missing_fields(self):
        collector = WMICollector(_source(hostname=None, username=None, password=None))
        with pytest.raises(CollectorConfigError) as exc:
            collector._require_config()
        message = str(exc.value)
        for field in ("hostname", "username", "password"):
            assert field in message


class TestTransport:
    """The transport is WMI over WSMan/WinRM (5985/5986), NOT legacy DCOM (135).

    These assertions pin the honest behaviour: a `wmi` source configured for
    port 135 will not silently 'work' - it builds a wsman endpoint on the
    resolved port, so the mismatch is visible rather than mysterious.
    """

    @staticmethod
    def _capture(monkeypatch, collector):
        captured: dict = {}

        def fake_protocol(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(**kwargs)

        import winrm
        monkeypatch.setattr(winrm, "Protocol", fake_protocol)
        collector._build_protocol()
        return captured

    def test_endpoint_is_wsman_over_http_on_5985(self, monkeypatch):
        captured = self._capture(monkeypatch, WMICollector(_source()))
        assert captured["endpoint"] == "http://10.0.0.5:5985/wsman"
        assert captured["transport"] == "ntlm"

    def test_endpoint_uses_5986_for_https(self, monkeypatch):
        captured = self._capture(
            monkeypatch, WMICollector(_source(protocol="https"))
        )
        assert captured["endpoint"] == "https://10.0.0.5:5986/wsman"

    def test_explicit_port_is_honoured(self, monkeypatch):
        captured = self._capture(monkeypatch, WMICollector(_source(port=5990)))
        assert ":5990/wsman" in captured["endpoint"]

    def test_cert_validation_follows_verify_ssl(self, monkeypatch):
        assert self._capture(
            monkeypatch, WMICollector(_source(verify_ssl=False))
        )["server_cert_validation"] == "ignore"
        assert self._capture(
            monkeypatch, WMICollector(_source(verify_ssl=True))
        )["server_cert_validation"] == "validate"

    def test_auth_type_is_passed_through(self, monkeypatch):
        captured = self._capture(
            monkeypatch, WMICollector(_source(config={"auth_type": "kerberos"}))
        )
        assert captured["transport"] == "kerberos"

    def test_credentials_never_appear_in_the_endpoint(self, monkeypatch):
        captured = self._capture(monkeypatch, WMICollector(_source()))
        assert "secret-password" not in captured["endpoint"]
        assert "svc-monitor" not in captured["endpoint"]


class TestCollectAndNormalize:
    """`collect()`/`normalize()` are inherited, so this only proves they work
    correctly when driven through a WMI source (no host is contacted)."""

    async def test_collect_issues_every_profile_wql_query(self, monkeypatch):
        """Proves the WMI path actually runs the WQL queries and maps each
        result set into `raw` under its profile key."""
        collector = _collector()
        seen: list[str] = []

        def fake_run_wmi(self, protocol, wql):   # bound: self is passed
            seen.append(wql)
            return []

        monkeypatch.setattr(WMICollector, "_build_protocol", lambda self: object())
        monkeypatch.setattr(WMICollector, "_run_wmi", fake_run_wmi)

        raw = await collector.collect()

        profile = select_profile(None)
        assert set(seen) == set(profile.wmi_queries.values())
        assert seen, "collect() must issue at least one WMI query"
        for name in profile.wmi_queries:
            assert name in raw
        assert "profile_queries" in raw

    def test_wmi_source_emits_a_normalized_device(self):
        """Inherited normalize() must work for a wmi source_type."""
        collector = _collector()
        bundle = collector.normalize({})
        assert bundle.devices, "a WMI source must still produce a device"
        assert bundle.devices[0].name

    def test_no_password_in_normalized_output(self):
        collector = _collector()
        bundle = collector.normalize({})
        assert "secret-password" not in repr(bundle)


class TestTopologyCapability:

    def test_wmi_is_topology_capable(self):
        """wmi inherits normalize(), which emits NormalizedInterface, so it must
        be listed or the topology graph would wrongly report it as incapable."""
        assert "wmi" in TOPOLOGY_CAPABLE_SOURCE_TYPES

    def test_windows_winrm_still_topology_capable(self):
        assert "windows_winrm" in TOPOLOGY_CAPABLE_SOURCE_TYPES
