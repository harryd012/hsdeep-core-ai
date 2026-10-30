"""
Collector registration guard tests.

Regression cover for a real defect: `app/services/monitoring/__init__.py` only
imported `base, prtg, connectivity` "for registration side effects", so 8
fully-implemented collectors (azure, vmware, fortigate, fortimanager, meraki,
zabbix, linux_ssh, windows_winrm) were never registered at runtime. They were
invisible to `MonitoringService.supported_source_types()` and
`CollectorRegistry.get("azure")` raised:

    ValueError: No collector registered for source_type='azure'.
               Supported: ['connectivity', 'prtg']

...so an Azure or VMware monitoring source could never be collected even
though its collector was complete and its own unit tests passed. The existing
per-collector tests could not catch this because they instantiate the class
directly (`AzureCollector(source=None)`), never going through the registry.

These tests are deliberately written against the *registry*, not against
individual classes, and `_registered_modules_match_source_tree` fails when a
new collector module is added without being added to `_COLLECTOR_MODULES`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.collectors.base import CollectorRegistry
from app.services.monitoring import _COLLECTOR_MODULES
import app.services.monitoring  # noqa: F401  (registration side effects)

_COLLECTORS_DIR = Path(__file__).resolve().parent.parent / "app" / "collectors"

#: `@CollectorRegistry.register("prtg")` - captures the source_type token.
_REGISTER_RE = re.compile(
    r"""@CollectorRegistry\.register\(\s*["']([a-z0-9_]+)["']\s*\)"""
)

#: Modules in app/collectors that are helpers, not collectors.
_NON_COLLECTOR_MODULES = frozenset({"__init__", "base", "windows_profiles", "classification"})


def _declared_registrations() -> dict[str, str]:
    """Map module stem -> source_type for every collector module on disk."""
    found: dict[str, str] = {}
    for path in sorted(_COLLECTORS_DIR.glob("*.py")):
        if path.stem in _NON_COLLECTOR_MODULES:
            continue
        match = _REGISTER_RE.search(path.read_text(encoding="utf-8"))
        if match:
            found[path.stem] = match.group(1)
    return found


class TestRegistrationGuard:

    def test_collector_modules_exist_on_disk(self):
        """Every module named in _COLLECTOR_MODULES is a real file."""
        for module in _COLLECTOR_MODULES:
            assert (_COLLECTORS_DIR / f"{module}.py").is_file(), (
                f"_COLLECTOR_MODULES lists {module!r} but "
                f"app/collectors/{module}.py does not exist"
            )

    def test_registered_modules_match_source_tree(self):
        """A new collector module cannot be added without registering it.

        This is the test that would have caught the original defect.
        """
        on_disk = _declared_registrations()
        declared = set(_COLLECTOR_MODULES)
        missing = sorted(set(on_disk) - declared)
        assert not missing, (
            "these collector modules declare @CollectorRegistry.register but "
            f"are missing from _COLLECTOR_MODULES in app/services/monitoring/"
            f"__init__.py, so they are unreachable at runtime: {missing}"
        )

    def test_every_declared_module_is_actually_registered(self):
        """Importing the monitoring package populates the registry."""
        for module, source_type in _declared_registrations().items():
            assert source_type in CollectorRegistry._collectors, (
                f"{module}.py registers {source_type!r} but the registry does "
                "not contain it - the module import is missing"
            )

    def test_no_declared_module_is_silently_unregistered(self):
        registered = set(CollectorRegistry._collectors)
        for module in _COLLECTOR_MODULES:
            assert module in _declared_registrations() or module in registered, (
                f"{module!r} is in _COLLECTOR_MODULES but exposes no "
                "@CollectorRegistry.register class"
            )


class TestPreviouslyDeadCollectorsAreReachable:
    """The exact defect: these raised ValueError before the fix."""

    @pytest.mark.parametrize(
        "source_type",
        ["azure", "datadog", "vmware", "fortigate", "fortimanager", "meraki",
         "zabbix", "linux_ssh", "windows_winrm"],
    )
    def test_collector_resolves_through_registry(self, source_type):
        collector_cls = CollectorRegistry.get(source_type)
        assert collector_cls.source_type == source_type
        assert collector_cls.implemented is True

    @pytest.mark.parametrize("source_type", ["aws", "gcp", "kubernetes"])
    def test_new_cloud_collectors_resolve(self, source_type):
        collector_cls = CollectorRegistry.get(source_type)
        assert collector_cls.source_type == source_type
        assert collector_cls.display_name

    def test_supported_source_types_includes_cloud_providers(self):
        """The API listing must advertise the cloud collectors."""
        from app.services.monitoring.monitoring_service import MonitoringService

        advertised = {
            row["source_type"] for row in MonitoringService.supported_source_types()
        }
        for expected in ("azure", "aws", "gcp", "vmware", "kubernetes"):
            assert expected in advertised, f"{expected} missing from supported types"
