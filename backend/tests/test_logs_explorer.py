"""Syslog receiver and Logs Explorer (observability plan P7 slice 1).

Syslog parsing (pure); the receiver's buffer, settings and delivery rules
(mocked transport); and end to end through the real collector ingest API:
log lines linked to a device only when the host is unambiguous, then
searched, filtered, bucketed and faceted through /api/logs with RBAC and
tenant isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.edge.syslog_receiver import Buffer, Settings, send_batch
from app.main import app
from app.models.device import Device
from app.services.logs.explorer import WindowInvalid, window
from app.services.logs.syslog import parse_syslog, platform_severity
from tests.test_ingest_api import _drop, _ingest, _make_tenant, _register_collector

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------


def test_parse_rfc5424_with_structured_data():
    raw = b'<165>1 2026-11-04T11:59:58.123Z fw-01 sshd 4242 AUTH [origin ip="10.0.0.9"][meta seq="7"] Failed password for root'
    e = parse_syslog(raw, sender="10.0.0.9", received_at=NOW)
    a = e["attributes"]
    assert (e["severity"], e["event_category"], e["event_type"], e["telemetry_type"]) == ("info", "application", "syslog.local4", "LOG")
    assert (a["format"], a["host"], a["app"], a["procid"], a["msgid"], a["syslog_severity"]) == ("rfc5424", "fw-01", "sshd", "4242", "AUTH", "notice")
    assert a["structured_data"] == {"origin": {"ip": "10.0.0.9"}, "meta": {"seq": "7"}}
    assert e["title"] == "Failed password for root" and e["timestamp"].startswith("2026-11-04T11:59:58.123")
    assert a["timestamp_source"] == "message"


def test_parse_bsd_and_degraded_messages():
    e = parse_syslog(b"<34>Nov  4 11:58:00 core-sw-1 kernel[12]: link down Gi0/1", sender="10.0.0.2", received_at=NOW)
    a = e["attributes"]
    assert (a["format"], a["host"], a["app"], a["procid"], a["facility"]) == ("rfc3164", "core-sw-1", "kernel", "12", "auth")
    assert (e["severity"], e["event_category"], e["title"]) == ("critical", "security", "link down Gi0/1")
    assert e["timestamp"] == "2026-11-04T11:58:00+00:00"
    # Dec 31 received on Jan 1 belongs to the previous year.
    new_year = datetime(2027, 1, 1, 0, 5, tzinfo=timezone.utc)
    assert parse_syslog(b"<14>Dec 31 23:59:00 h app: x", sender="s", received_at=new_year)["timestamp"].startswith("2026-12-31")
    # No PRI: kept as user.notice and flagged; no host is invented.
    bare = parse_syslog(b"something happened", sender="10.0.0.5", received_at=NOW)
    assert (bare["severity"], bare["attributes"]["pri_missing"], bare["attributes"]["format"]) == ("info", True, "unknown")
    assert "host" not in bare["attributes"] and bare["attributes"]["timestamp_source"] == "received"
    assert parse_syslog(b"<13>", sender="s", received_at=NOW)["title"] == "(empty message)"
    long = parse_syslog(b"<14>" + b"x" * 900, sender="s", received_at=NOW)
    assert len(long["title"]) == 500 and len(long["description"]) == 900
    assert [platform_severity(c) for c in range(8)] == ["critical"] * 3 + ["warning"] * 2 + ["info"] * 3


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------


def test_receiver_settings_and_buffer():
    with pytest.raises(SystemExit):
        Settings.from_env({"HSDEEP_API_URL": "https://p.example"})
    with pytest.raises(SystemExit):
        Settings.from_env({"HSDEEP_API_URL": "http://p.example", "HSDEEP_COLLECTOR_CREDENTIAL": "c"})
    s = Settings.from_env({"HSDEEP_API_URL": "https://p.example/", "HSDEEP_COLLECTOR_CREDENTIAL": "s3cret", "SYSLOG_BATCH": "9999"})
    assert (s.api_url, s.batch, s.port) == ("https://p.example", 500, 5514)
    assert "s3cret" not in repr(s)

    b = Buffer(limit=3)
    for i in range(5):
        b.add({"n": i})
    assert ([e["n"] for e in b.items], b.dropped, b.received) == ([2, 3, 4], 2, 5)
    taken = b.take(2)
    b.add({"n": 5})
    b.put_back(taken)  # back to the front, in order; one no longer fits
    assert [e["n"] for e in b.items] == [3, 4, 5] and b.dropped == 3


async def test_receiver_delivery_rules():
    s = Settings(api_url="https://p.example", credential="cred")
    seen = []

    def handler(status, body=None):
        def h(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("X-HSDEEP-Collector-Credential"))
            return httpx.Response(status, json=body or {})
        return h

    for status, body, expected in ((200, {"rejected": 1}, (True, 1)), (503, None, (False, 0)), (429, None, (False, 0)), (400, None, (True, 2))):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler(status, body))) as client:
            assert await send_batch(client, s, [{"a": 1}, {"a": 2}]) == expected

    def boom(request):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        assert await send_batch(client, s, [{"a": 1}]) == (False, 0)
    assert set(seen) == {"cred"}


def test_window_rules():
    since, until = window(None, None, now=NOW)
    assert (until - since) == timedelta(hours=1)
    for a, b in ((NOW, NOW - timedelta(minutes=1)), (NOW - timedelta(days=8), NOW), (datetime(2026, 1, 1), NOW)):
        with pytest.raises(WindowInvalid):
            window(a, b, now=NOW)


# ---------------------------------------------------------------------------
# End to end: ingest -> /api/logs
# ---------------------------------------------------------------------------


def _client(tenant_id, permissions=frozenset({"monitoring.read"})):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=permissions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


async def test_ingested_syslog_is_linked_searched_and_isolated():
    tenant, user, plan = await _make_tenant()
    try:
        credential = await _register_collector(user)
        async with AsyncSessionLocal() as db:
            sw = Device(tenant_id=tenant.id, name="core-sw-1", device_type="switch", ip_address="10.0.0.2")
            db.add_all([sw, Device(tenant_id=tenant.id, name="dup-a", device_type="server", hostname="web"),
                        Device(tenant_id=tenant.id, name="dup-b", device_type="server", hostname="web")])
            await db.commit()
            await db.refresh(sw)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        lines = [
            (b"<34>%s core-sw-1 kernel: link down Gi0/1" % now.strftime("%b %d %H:%M:%S").encode(), "10.0.0.2"),
            (b"<30>1 %s - nginx - - - upstream timed out" % now.isoformat().encode(), "10.0.0.2"),
            (b"<14>1 %s web app - - - started 100%% fine" % now.isoformat().encode(), "10.9.9.9"),
            (b"<11>1 %s unknown-host app - - - disk failure" % now.isoformat().encode(), "10.9.9.8"),
        ]
        events = [parse_syslog(raw, sender=sender, received_at=now) for raw, sender in lines]
        r = await _ingest(credential, "events", {"events": events})
        assert r.status_code == 200 and r.json()["accepted"] == 4, r.text

        async with _client(tenant.id) as client:
            page = (await client.get("/api/logs")).json()
            assert page["total"] == 4
            by_msg = {i["message"]: i for i in page["items"]}
            # core-sw-1 by HOSTNAME, and the HOSTNAME-less line by its sender address.
            assert by_msg["link down Gi0/1"]["device_name"] == "core-sw-1"
            assert by_msg["upstream timed out"]["device_id"] == str(sw.id)
            # "web" names two devices: not linked. An unknown host: not linked.
            assert by_msg["started 100% fine"]["device_id"] is None and by_msg["disk failure"]["device_id"] is None

            assert (await client.get("/api/logs", params={"q": "100%"})).json()["total"] == 1  # % is literal
            assert (await client.get("/api/logs", params={"q": "TIMED"})).json()["total"] == 1
            assert (await client.get("/api/logs", params={"severity": ["critical", "warning"]})).json()["total"] == 2
            assert (await client.get("/api/logs", params={"host": "core-sw-1"})).json()["total"] == 1
            assert (await client.get("/api/logs", params={"device_id": str(sw.id)})).json()["total"] == 2
            assert (await client.get("/api/logs", params={"severity": "loud"})).status_code == 422
            old = (now - timedelta(days=9)).isoformat()
            assert (await client.get("/api/logs", params={"since": old})).status_code == 422

            stats = (await client.get("/api/logs/stats", params={"severity": "critical"})).json()
            # <34> is auth.crit (critical); <11> is user.err, which maps to warning.
            assert sum(b["critical"] for b in stats["histogram"]) == 1 and sum(b["info"] for b in stats["histogram"]) == 0
            # A facet ignores its own filter, so every severity stays visible.
            assert {f["value"] for f in stats["facets"]["severity"]} == {"critical", "warning", "info"}
            assert {f["value"] for f in stats["facets"]["host"]} >= {"core-sw-1"}

        async with _client(tenant.id, permissions=frozenset()) as client:
            assert (await client.get("/api/logs")).status_code == 403
        async with _client(uuid.uuid4()) as client:
            assert (await client.get("/api/logs")).json()["total"] == 0
    finally:
        await _drop(tenant.id, plan.id)
