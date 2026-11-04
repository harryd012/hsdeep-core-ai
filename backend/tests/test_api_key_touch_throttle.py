"""API keys record last_used_at, but at most once per interval per key, so
steady API-key traffic does not open a write connection per request."""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from app.core import auth
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.api_key import ApiKey

pytestmark = pytest.mark.asyncio


async def _key(tenant) -> tuple[str, uuid.UUID]:
    raw = f"hsk_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        row = ApiKey(tenant_id=tenant.id, name="touch", key_prefix=raw[:8], key_hash=auth.hash_api_key(raw),
                     scopes=["devices.read"])
        db.add(row)
        await db.commit()
        return raw, row.id


async def _last_used(key_id):
    async with AsyncSessionLocal() as db:
        return (await db.get(ApiKey, key_id)).last_used_at


async def _drain():
    if auth._touch_tasks:
        await asyncio.gather(*list(auth._touch_tasks))


async def test_last_used_is_written_once_per_interval(tenant, monkeypatch):
    monkeypatch.setattr(auth, "_api_key_touched", {})
    raw, key_id = await _key(tenant)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": f"Bearer {raw}"}) as c:
        assert (await c.get("/api/devices")).status_code == 200
        await _drain()
        first = await _last_used(key_id)
        assert first is not None

        for _ in range(5):
            assert (await c.get("/api/devices")).status_code == 200
        assert not auth._touch_tasks  # nothing new scheduled inside the interval
        assert await _last_used(key_id) == first

        monkeypatch.setattr(auth, "_API_KEY_TOUCH_INTERVAL_SECONDS", 0.0)
        assert (await c.get("/api/devices")).status_code == 200
        await _drain()
        assert await _last_used(key_id) != first

