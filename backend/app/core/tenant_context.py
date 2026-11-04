"""
The tenant of the request being served, for code far from the request (the
AI gateway applies the tenant's AI data-use settings). Set by core.auth when a
principal is resolved; None in scheduler jobs and other background work.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

current_tenant_id: ContextVar[uuid.UUID | None] = ContextVar("current_tenant_id", default=None)
