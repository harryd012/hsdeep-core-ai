"""
Tool Gateway — central registry for real tool/adapter integrations.

Adapters implementing real device/API actions should register themselves
here (e.g. PRTGAdapter, MerakiAdapter, SSHAdapter). Each adapter must
expose an async `execute(input: dict, context: dict) -> dict` method that
returns a dict with keys: `success` (bool), `output` (dict) and `error`
(optional string).

If no adapter is registered for a requested tool, callers will receive an
honest capability-unavailable response and the task audit log will record
it rather than fabricating execution evidence.
"""
from __future__ import annotations

from typing import Any, Dict


_ADAPTERS: Dict[str, Any] = {}


def register_adapter(name: str, adapter: Any) -> None:
    """Register an adapter under a canonical tool name."""
    _ADAPTERS[name] = adapter


def get_adapter(name: str) -> Any | None:
    """Return the adapter for `name` or None if unavailable."""
    return _ADAPTERS.get(name)


# Register built-in adapters (safe, read-only adapters that rely on existing
# repository reads). Import lazily to avoid import cycles when the module is
# imported in tests that don't need adapters.
try:
    from app.services.adapters.prtg_adapter import adapter as prtg_adapter  # type: ignore
    register_adapter("prtg_lookup", prtg_adapter)
except Exception:
    # If the adapter's dependencies aren't available in some test contexts,
    # silently skip registration; callers will receive "adapter unavailable".
    pass
