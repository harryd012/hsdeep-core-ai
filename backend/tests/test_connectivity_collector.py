"""Pure unit tests for the connectivity (ICMP/TCP/DNS) collector parser.

No live network, no DB — tests only the pure parsing / normalization helpers.
"""
from __future__ import annotations

from app.collectors.connectivity import (
    _latency_sensors,
    _to_float,
    parse_ping_output,
)

# ---------------------------------------------------------------------------
# ICMP output parsing
# ---------------------------------------------------------------------------

_WIN_OK = (
    "Pinging 10.0.0.1 with 32 bytes of data:\r\n"
    "Reply from 10.0.0.1: bytes=32 time=12ms TTL=64\r\n"
    "Reply from 10.0.0.1: bytes=32 time=11ms TTL=64\r\n"
    "Reply from 10.0.0.1: bytes=32 time=10ms TTL=64\r\n"
    "Ping statistics for 10.0.0.1:\r\n"
    "    Packets: Sent = 3, Received = 3, Lost = 0 (0% loss),\r\n"
)

_WIN_DOWN = (
    "Reply from 10.0.0.1: Destination host unreachable.\r\n"
    "Ping statistics for 10.0.0.1:\r\n"
    "    Packets: Sent = 3, Received = 0, Lost = 3 (100% loss),\r\n"
)

_POSIX_OK = (
    "PING 10.0.0.1 (10.0.0.1) 56(84) bytes of data.\r\n"
    "64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.312 ms\r\n"
    "3 packets transmitted, 3 received, 0% packet loss, time 2041ms\r\n"
    "rtt min/avg/max/mdev = 0.300/0.312/0.330/0.010 ms\r\n"
)


def test_parse_windows_ok():
    r = parse_ping_output(_WIN_OK, windows=True)
    assert r["ok"] is True
    assert r["latency_ms"] == 12.0
    assert r["packet_loss_pct"] == 0.0


def test_parse_windows_down():
    r = parse_ping_output(_WIN_DOWN, windows=True)
    assert r["ok"] is False
    assert r["latency_ms"] is None  # down => no latency fabricated
    assert r["packet_loss_pct"] == 100.0


def test_parse_posix_ok():
    r = parse_ping_output(_POSIX_OK, windows=False)
    assert r["ok"] is True
    assert r["latency_ms"] == 0.312
    assert r["packet_loss_pct"] == 0.0


def test_parse_empty_output():
    r = parse_ping_output("", windows=True)
    assert r["ok"] is False
    assert r["latency_ms"] is None
    assert r["packet_loss_pct"] is None


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def test_latency_sensors_from_icmp():
    host = "10.0.0.1"
    sensors = _latency_sensors({"icmp": {"ok": True, "latency_ms": 11.0, "packet_loss_pct": 0.0}}, host)
    names = {s.external_id for s in sensors}
    assert names == {"latency_ms", "packet_loss_pct"}


def test_latency_sensors_none_when_down():
    sensors = _latency_sensors({"icmp": {"ok": False, "latency_ms": None, "packet_loss_pct": 100.0}}, "x")
    assert sensors == []


def test_to_float():
    assert _to_float("12.5") == 12.5
    assert _to_float("nope") is None
    assert _to_float(None) is None
