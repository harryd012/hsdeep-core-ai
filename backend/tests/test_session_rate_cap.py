"""
Per-user fair-use cap for session-JWT (web UI) traffic (app/main.py
``_RateLimiter`` / ``_RateLimitMiddleware``).

Session traffic is never metered against the plan's per-period API allowance
(see test_api_quota.py), so a user could script against their own session
token unmetered, and the old per-minute window used ``Plan.api_rate_limit``
(~50_000 rpm on Starter) as its ceiling and slept instead of rejecting - so it
never limited anything. These tests pin the replacement:

  * session JWTs are keyed per user (``user:<tid>:<sub>``), not per tenant;
  * over the cap -> immediate 429 with Retry-After, no sleep;
  * rejected requests do not consume slots, and slots free after 60s;
  * IP-keyed traffic still waits, but never while holding the shared lock;
  * session requests are still never metered against the period quota.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.core.auth import create_access_token
from app.main import _RateLimiter, _RateLimitMiddleware


def _token(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        email="cap@example.test",
        is_superuser=False,
        permissions=frozenset(),
    )


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers
        self.client = None


def _app(session_rpm_per_user: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        _RateLimitMiddleware, requests_per_minute=60, session_rpm_per_user=session_rpm_per_user
    )

    @app.get("/api/thing")
    async def thing():
        return {"ok": True}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestKeying:
    def test_session_jwt_keyed_per_user(self):
        limiter = _RateLimiter(60, session_rpm_per_user=600)
        tid, u1, u2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        k1 = limiter.key_for_request(_FakeRequest({"authorization": f"Bearer {_token(tid, u1)}"}))
        k2 = limiter.key_for_request(_FakeRequest({"authorization": f"Bearer {_token(tid, u2)}"}))
        assert k1 == f"user:{tid}:{u1}"
        assert k2 == f"user:{tid}:{u2}"

    def test_anonymous_and_non_jwt_bearer_keyed_by_ip(self):
        limiter = _RateLimiter(60, session_rpm_per_user=600)
        assert limiter.key_for_request(_FakeRequest({})).startswith("ip:")
        # An API key (or any non-JWT bearer) is not a session.
        api_key = _FakeRequest({"authorization": "Bearer hsk_not_a_jwt"})
        assert limiter.key_for_request(api_key).startswith("ip:")

    async def test_rpm_for_ignores_plan_allowance(self):
        limiter = _RateLimiter(60, session_rpm_per_user=600)
        assert await limiter.rpm_for(f"user:{uuid.uuid4()}:{uuid.uuid4()}") == 600
        assert await limiter.rpm_for("ip:1.2.3.4") == 60

    async def test_nonpositive_cap_falls_back_to_default(self):
        limiter = _RateLimiter(60, session_rpm_per_user=0)
        assert await limiter.rpm_for("user:a:b") == _RateLimiter.DEFAULT_SESSION_RPM

    def test_default_cap_comes_from_settings(self):
        from app.core.config import get_settings

        limiter = _RateLimiter(60)
        assert limiter._session_rpm == get_settings().session_rpm_per_user


class TestSessionCap:
    async def test_over_cap_returns_429_immediately(self):
        tid, uid = uuid.uuid4(), uuid.uuid4()
        headers = {"authorization": f"Bearer {_token(tid, uid)}"}
        async with _client(_app(3)) as client:
            for _ in range(3):
                assert (await client.get("/api/thing", headers=headers)).status_code == 200
            started = time.monotonic()
            resp = await client.get("/api/thing", headers=headers)
            elapsed = time.monotonic() - started

        assert resp.status_code == 429
        assert elapsed < 1.0, "over-cap session requests must be rejected, not slept"
        retry_after = int(resp.headers["Retry-After"])
        assert 1 <= retry_after <= 60
        body = resp.json()
        assert body["error"] == "rate_limited"
        assert body["limit"] == 3
        assert body["retry_after"] == retry_after

    async def test_cap_is_per_user_not_per_tenant(self):
        tid = uuid.uuid4()
        alice = {"authorization": f"Bearer {_token(tid, uuid.uuid4())}"}
        bob = {"authorization": f"Bearer {_token(tid, uuid.uuid4())}"}
        async with _client(_app(2)) as client:
            for _ in range(2):
                await client.get("/api/thing", headers=alice)
            assert (await client.get("/api/thing", headers=alice)).status_code == 429
            assert (await client.get("/api/thing", headers=bob)).status_code == 200

    async def test_rejections_do_not_consume_slots_and_window_slides(self, monkeypatch):
        import app.main as main_mod

        clock = [1000.0]
        monkeypatch.setattr(main_mod.time, "monotonic", lambda: clock[0])
        limiter = _RateLimiter(60, session_rpm_per_user=2)
        key = "user:t:u"

        assert await limiter.try_acquire(key, 2) is None
        clock[0] += 10
        assert await limiter.try_acquire(key, 2) is None
        clock[0] += 10
        # Hammering while over the cap must not extend the lockout.
        for _ in range(50):
            wait = await limiter.try_acquire(key, 2)
            assert wait == pytest.approx(40.0)
        # First slot frees exactly 60s after it was taken.
        clock[0] = 1060.0
        assert await limiter.try_acquire(key, 2) is None
        assert await limiter.try_acquire(key, 2) is not None

    async def test_session_requests_never_metered(self, monkeypatch):
        import app.services.metering.api_quota as api_quota

        async def _boom(*_a, **_k):
            raise AssertionError("session traffic must not be metered")

        monkeypatch.setattr(api_quota, "record_api_call", _boom)
        monkeypatch.setattr(api_quota, "api_key_tenant", _boom)
        headers = {"authorization": f"Bearer {_token(uuid.uuid4(), uuid.uuid4())}"}
        async with _client(_app(5)) as client:
            for _ in range(5):
                assert (await client.get("/api/thing", headers=headers)).status_code == 200
            assert (await client.get("/api/thing", headers=headers)).status_code == 429


class TestIpWindow:
    async def test_ip_sleep_does_not_hold_lock(self):
        """A throttled IP caller waits for its slot without blocking anyone
        else (the old acquire slept inside the global lock)."""
        limiter = _RateLimiter(1, session_rpm_per_user=600)
        await limiter.acquire("ip:9.9.9.9", 1)
        waiter = asyncio.create_task(limiter.acquire("ip:9.9.9.9", 1))
        try:
            await asyncio.sleep(0.05)
            assert not waiter.done(), "second IP request should be waiting for its slot"
            assert await asyncio.wait_for(limiter.try_acquire("user:t:u", 600), 1.0) is None
            await asyncio.wait_for(limiter.acquire("ip:8.8.8.8", 1), 1.0)
        finally:
            waiter.cancel()
