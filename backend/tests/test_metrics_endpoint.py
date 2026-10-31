"""/metrics is open in development, token-gated when a token is set, and not
published in production without one."""
from types import SimpleNamespace

import httpx
import pytest

import app.main as main_module
from app.main import app

pytestmark = pytest.mark.asyncio


def _use(monkeypatch, *, app_env: str, token: str) -> None:
    fake = SimpleNamespace(app_env=app_env, metrics_bearer_token=token)
    monkeypatch.setattr(main_module, "get_settings", lambda: fake)


async def _get(headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/metrics", headers=headers or {})


async def test_open_in_development_without_token(monkeypatch):
    _use(monkeypatch, app_env="development", token="")
    resp = await _get()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


async def test_hidden_in_production_without_token(monkeypatch):
    _use(monkeypatch, app_env="production", token="")
    resp = await _get()
    assert resp.status_code == 404


@pytest.mark.parametrize("app_env", ["development", "production"])
async def test_token_required_when_configured(monkeypatch, app_env):
    _use(monkeypatch, app_env=app_env, token="scrape-secret")
    assert (await _get()).status_code == 401
    assert (await _get({"Authorization": "Bearer wrong"})).status_code == 401
    ok = await _get({"Authorization": "Bearer scrape-secret"})
    assert ok.status_code == 200
