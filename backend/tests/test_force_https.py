"""FORCE_HTTPS refuses plaintext API calls but always answers health probes."""
import httpx
import pytest
from fastapi import FastAPI

from app.core.request_logging import HTTPS_EXEMPT_PATHS, ForceHttpsMiddleware

pytestmark = pytest.mark.asyncio


def _app(enabled: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ForceHttpsMiddleware, enabled=enabled)

    @app.get("/api/thing")
    async def thing():
        return {"ok": True}

    for path in HTTPS_EXEMPT_PATHS:
        app.add_api_route(path, lambda: {"status": "ok"}, methods=["GET"])
    return app


async def _get(app, path, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers=headers or {})


async def test_plaintext_api_call_is_refused():
    resp = await _get(_app(True), "/api/thing")
    assert resp.status_code == 400
    assert resp.json() == {"detail": "HTTPS is required."}


async def test_forwarded_https_is_allowed():
    resp = await _get(_app(True), "/api/thing", {"x-forwarded-proto": "https"})
    assert resp.status_code == 200


@pytest.mark.parametrize("path", sorted(HTTPS_EXEMPT_PATHS))
async def test_health_probes_are_answered_over_plain_http(path):
    assert (await _get(_app(True), path)).status_code == 200


async def test_disabled_allows_everything():
    assert (await _get(_app(False), "/api/thing")).status_code == 200
