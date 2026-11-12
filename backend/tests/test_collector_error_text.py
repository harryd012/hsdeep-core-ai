"""Collector error text (observability plan P12 slice 2, step 2a).

A failed connection check always names its cause, even when the transport
exception has an empty message ("Could not reach PRTG:" with nothing after it
was what operators saw), and never carries a configured secret.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.collectors.base import describe_error
from app.collectors.prtg import PRTGCollector


def _source(**kw):
    base = dict(id="src-1", password=None, api_key=None, config={}, verify_ssl=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_describe_error_names_the_type_when_the_message_is_empty():
    assert describe_error(httpx.ConnectTimeout("")) == "ConnectTimeout"
    assert describe_error(httpx.ConnectError("All connection attempts failed")) == (
        "ConnectError: All connection attempts failed")


def test_describe_error_scrubs_configured_secrets():
    src = _source(password="s3cr3t-hash", config={"passhash": "998877"})
    text = describe_error(
        httpx.ConnectError("GET https://prtg/api?passhash=998877&password=s3cr3t-hash failed"), src)
    assert "998877" not in text and "s3cr3t-hash" not in text
    assert text.startswith("ConnectError: ") and "[redacted]" in text


@pytest.mark.asyncio
async def test_prtg_unreachable_message_has_a_cause(monkeypatch):
    collector = PRTGCollector(source=_source(password="hash-123"))

    async def boom(*_a, **_kw):
        raise httpx.ConnectTimeout("")

    monkeypatch.setattr(collector, "_require_config", lambda: None)
    monkeypatch.setattr(collector, "_auth_params", lambda: {"passhash": "hash-123"})
    monkeypatch.setattr(collector, "_request", boom)

    check = await collector.authenticate()
    assert check.success is False
    assert check.message == "Could not reach PRTG: ConnectTimeout"
