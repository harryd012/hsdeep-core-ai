"""
Per-tenant API-request quota (``Plan.api_rate_limit``).

``Plan.api_rate_limit`` is a PER-BILLING-PERIOD allowance, not a per-minute
one: ``BillingService.get_entitlements()`` sums ``api.call`` usage_counters
since ``subscription.current_period_start`` and compares that total with the
plan value (50_000 Starter / 500_000 Pro / 5_000_000 Enterprise).

Two things were missing for that quota to be real:

1. Nothing recorded ``api.call``. ``app/services/ingester/pipeline.py``'s
   ``count_api_call()`` shipped with the docstring "Called from middleware or
   route handlers" but had ZERO callers, so usage always read 0 (live evidence:
   ``{"resource":"api_calls","limit":50000,"used":0}`` on a system that had
   served thousands of requests) and the quota could never trigger.
2. The request limiter applied ``api_rate_limit`` as a PER-MINUTE value in a
   60-second sliding window and, when it tripped, slept instead of rejecting -
   so a 50_000/month allowance never blocked anything.

This module supplies the missing meter and the period-allowance check. It
reuses the existing ``TenantUsageCollector`` (in-memory hourly buckets, flushed
through the existing ``flush_usage_counters``) and ``BillingService``, so there
is no second metering system and no second source of truth.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict

logger = logging.getLogger(__name__)

# `api.call` increments are accumulated in memory and written in batches, so
# the request path never pays for a DB write.
FLUSH_THRESHOLD = 50

# How long a tenant's allowance (limit + period usage) is cached. One billing
# query per tenant per minute, never one per request.
ALLOWANCE_TTL_SECONDS = 60.0

_pending: dict[uuid.UUID, int] = defaultdict(int)
_allowance_cache: dict[uuid.UUID, tuple[float, int, int, bool]] = {}
# sha256(raw key) -> (expires_at_monotonic, tenant_id or None). Negative
# results are cached too, so a garbage bearer cannot force a DB hit per request.
_api_key_cache: dict[str, tuple[float, uuid.UUID | None]] = {}
_lock = asyncio.Lock()


def pending_for(tenant_id: uuid.UUID) -> int:
    """Unflushed ``api.call`` increments for a tenant (in-process only)."""
    return _pending.get(tenant_id, 0)


def reset_state() -> None:
    """Clear all in-process state. Test hook only."""
    _pending.clear()
    _allowance_cache.clear()
    _api_key_cache.clear()


async def record_api_call(tenant_id: uuid.UUID, increment: int = 1) -> None:
    """Count one authenticated API request for metering.

    Cheap by design: the increment is held in memory and persisted in batches,
    so a metering failure can never fail a request.
    """
    async with _lock:
        _pending[tenant_id] += increment
        due = _pending[tenant_id] >= FLUSH_THRESHOLD
    if due:
        await flush_api_calls(tenant_id)


async def flush_api_calls(tenant_id: uuid.UUID | None = None) -> None:
    """Persist accumulated ``api.call`` increments through the existing meter.

    Uses the previously-unwired ``count_api_call()`` plus the existing
    ``flush_usage_counters()``, so counts land in ``usage_counters`` exactly
    like ``collector.run`` and ``alert.ingested`` already do.
    """
    from app.core.db import AsyncSessionLocal
    from app.services.ingester.pipeline import count_api_call, flush_usage_counters

    async with _lock:
        if tenant_id is None:
            batch = dict(_pending)
            _pending.clear()
        else:
            batch = {tenant_id: _pending.pop(tenant_id, 0)}
    batch = {t: n for t, n in batch.items() if n > 0}
    if not batch:
        return

    try:
        async with AsyncSessionLocal() as db:
            for tid, increment in batch.items():
                await count_api_call(tid, db, increment=increment)
            await flush_usage_counters(db)
            await db.commit()
    except Exception:  # noqa: BLE001 - metering must never break a request
        logger.warning("Failed to flush api.call usage counters.", exc_info=True)
        # Put the counts back so the next flush retries instead of losing them.
        async with _lock:
            for tid, increment in batch.items():
                _pending[tid] += increment


async def api_allowance(tenant_id: uuid.UUID) -> tuple[int | None, int, bool]:
    """Cached ``(limit, period_used, over_limit)`` for the ``api_calls`` quota.

    Reads the same entitlement computation the UI and
    ``BillingService.check_limit("api_calls")`` use. ``(None, 0, False)`` means
    "no enforceable allowance" (no subscription, or a non-positive plan value),
    which must never block a request.
    """
    import time

    now = time.monotonic()
    cached = _allowance_cache.get(tenant_id)
    if cached and cached[0] > now:
        return cached[1], cached[2], cached[3]

    limit: int | None = None
    used = 0
    over = False
    try:
        from app.core.db import AsyncSessionLocal
        from app.repositories.plan_repository import PlanRepository
        from app.repositories.subscription_repository import SubscriptionRepository
        from app.services.billing_service import BillingService, SubscriptionNotFound

        async with AsyncSessionLocal() as db:
            billing = BillingService(
                plans=PlanRepository(db),
                subscriptions=SubscriptionRepository(db),
                db=db,
            )
            try:
                entitlements = await billing.get_entitlements(tenant_id)
            except SubscriptionNotFound:
                entitlements = None
            if entitlements:
                for entry in entitlements["limits"]:
                    if entry["resource"] == "api_calls":
                        limit = int(entry["limit"])
                        used = int(entry["used"])
                        over = bool(entry["over_limit"])
                        break
    except Exception:  # noqa: BLE001 - never let quota config break requests
        logger.warning("Failed to read api_calls allowance.", exc_info=True)
        limit, used, over = None, 0, False

    # A non-positive limit means "no metered allowance configured" - the Plan
    # model documents 0 as not-enforced - so it must not block.
    if limit is not None and limit <= 0:
        limit = None
        over = False

    _allowance_cache[tenant_id] = (now + ALLOWANCE_TTL_SECONDS, limit, used, over)
    return limit, used, over


# ---------------------------------------------------------------------------
# Who is metered
# ---------------------------------------------------------------------------
# The allowance sells programmatic API access, so only API-key traffic is
# metered and enforced. HSDEEP's own web UI authenticates with a session JWT
# and polls continuously (one Host page load is ~15 calls); metering it let a
# single open dashboard tab exhaust a Starter tenant's 50_000/period allowance
# in hours and lock the customer out of the product (live: 429 at 50_400
# used). Before this rule the middleware only recognised JWTs, so it metered
# exactly the UI and never the API keys it was meant for.
#
# Known gap: a user can script against their own session JWT (login +
# refresh) unmetered. The per-minute burst limiter still applies; a session
# fair-use cap is a separate change.


async def api_key_tenant(raw_key: str) -> uuid.UUID | None:
    """Tenant owning an active, unexpired API key, or None. Cached 60s.

    Metering only: authentication (and the 401) stays in
    ``app.core.auth.get_current_user``. Fails open to None, so a lookup
    failure can never block a request.
    """
    import time
    from datetime import datetime, timezone

    from app.core.auth import hash_api_key

    key_hash = hash_api_key(raw_key)
    now = time.monotonic()
    cached = _api_key_cache.get(key_hash)
    if cached and cached[0] > now:
        return cached[1]

    tenant_id: uuid.UUID | None = None
    try:
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.api_key import ApiKey

        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(ApiKey.tenant_id, ApiKey.expires_at).where(
                        ApiKey.key_hash == key_hash,
                        ApiKey.is_active.is_(True),
                        ApiKey.is_deleted.is_(False),
                    )
                )
            ).one_or_none()
        if row is not None:
            tenant_id = row.tenant_id
            if row.expires_at:
                try:
                    expires = datetime.fromisoformat(row.expires_at.replace("Z", "+00:00"))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires <= datetime.now(timezone.utc):
                        tenant_id = None
                except ValueError:
                    tenant_id = None  # malformed expiry: auth rejects it too
    except Exception:  # noqa: BLE001 - metering must never break a request
        logger.warning("Failed to resolve API key for metering.", exc_info=True)
        return None  # do not cache a transient failure

    _api_key_cache[key_hash] = (now + ALLOWANCE_TTL_SECONDS, tenant_id)
    return tenant_id


# ---------------------------------------------------------------------------
# Control-plane (session) endpoints
# ---------------------------------------------------------------------------
# These establish or maintain the authenticated session itself: login, token
# refresh, logout, and the /me identity read the frontend performs immediately
# after login. They must remain reachable even when the tenant has exhausted
# its application API allowance, otherwise a locked-out tenant cannot log in
# to reach /usage and resolve the lockout. Live evidence of the defect this
# guard fixes: ``POST /api/auth/login`` -> 200 (token-in-body, IP-scoped, so
# never tenant-metered) followed immediately by ``GET /api/auth/me`` -> 429
# ``plan_limit_exceeded``: authentication succeeded and the session could
# never be established.
#
# Exempting these paths from BOTH metering and enforcement is deliberate:
#   * enforcement - a session-establishment request is not "application API
#     usage"; blocking it bricks authentication for the entire tenant, and
#   * metering - counting session traffic would let /me and refresh polling
#     burn the sellable allowance and conflate session establishment with
#     application API usage in the Usage UI.
# Every other API-key request is metered and blocked as usual. The
# per-minute burst limiter and the per-IP auth rate limiter (5 rpm on
# login/refresh) still apply to these paths, so the exemption does not open
# an unauthenticated flood gate.
CONTROL_PLANE_PATHS: frozenset[str] = frozenset(
    {
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/auth/me",
    }
)


def is_control_plane_path(path: str) -> bool:
    """True for session/control-plane paths that must survive quota lockout.

    Matches on the normalized path so ``/api/auth/me/`` (trailing slash)
    behaves exactly like ``/api/auth/me``. Anything not in
    ``CONTROL_PLANE_PATHS`` - i.e. every normal tenant API - is metered and
    enforced unchanged.
    """
    normalized = path.rstrip("/") or "/"
    return normalized in CONTROL_PLANE_PATHS
