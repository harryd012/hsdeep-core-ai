"""
Device Action Plane (Phase 1).

Importing this package populates the ActionProviderRegistry with the
built-in providers (same registration side-effect convention as the
Collector Framework - app/main.py imports the collectors package once
and the registry is populated).
"""
from app.actions.base import (  # noqa: F401
    ACTION_CATALOG,
    IMPLEMENTED_ACTIONS,
    ActionClassification,
    ActionNotSupported,
    ActionResult,
    ActionSpec,
    ActionTarget,
    DeviceActionProvider,
    DeviceActionType,
    ActionProviderRegistry,
)

# Provider registration side effects (populate the registry).
from app.actions.providers import icmp as _icmp_provider  # noqa: F401
from app.actions.providers import snmp as _snmp_provider  # noqa: F401
from app.actions.providers import prtg as _prtg_provider  # noqa: F401
from app.actions.providers import cisco as _cisco_provider  # noqa: F401
from app.actions.providers import aruba as _aruba_provider  # noqa: F401
