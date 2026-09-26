"""
ServiceNow integration service package.

Imports here trigger registration of ServiceNowIncidentNodeExecutor via the
@NodeExecutorRegistry.register("servicenow_incident") decorator side-effect,
same "import for registration" pattern used by app/services/monitoring/__init__.py
and app/services/workflow/__init__.py.

Exports: client, incident_sync, webhook_handler, rate_limiter, audit, retry, metrics
"""
from __future__ import annotations

from app.services.servicenow import (
    audit,
    client,
    incident_sync,
    metrics,  # noqa: F401 - registers Prometheus collectors at module level
    rate_limiter,
    retry,
    webhook_handler,
)  # noqa: F401
