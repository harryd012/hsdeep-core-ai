"""
Dependency injection container for application-wide singletons.

Registers the TenantUsageCollector and other long-lived services that
need to be initialized at app startup and shared across request scopes.
"""

from __future__ import annotations

from app.services.metering.collector import TenantUsageCollector, default_collector


# ---------------------------------------------------------------------------
# Singleton registry
# ---------------------------------------------------------------------------

_collector: TenantUsageCollector | None = None


def get_usage_collector() -> TenantUsageCollector:
    """Return the application-wide TenantUsageCollector singleton."""
    global _collector  # noqa: PLW0603
    if _collector is None:
        _collector = default_collector
    return _collector


def init_di() -> None:
    """Initialize all DI singletons at application startup.

    Called from the FastAPI lifespan/startup event.
    """
    global _collector  # noqa: PLW0603
    _collector = default_collector