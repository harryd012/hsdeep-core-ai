"""signature.build_signature normalizer — no DB.

Pins the identity rules for incident correlation: NULL metric_type never renders
as the literal "None", case/whitespace are normalized, and two devices with the
same title must NOT merge.
"""
from __future__ import annotations

from app.services.alerting.signature import build_signature, normalize_metric_type


def test_none_metric_type_never_becomes_literal_none():
    sig = build_signature(
        metric_type=None, title="SSL Security Check (Port 993)", device_id="dev-1"
    )
    assert sig is not None
    assert "None" not in sig


def test_case_and_whitespace_are_normalized():
    a = build_signature(
        metric_type="PRTG_SENSOR", title="  SSL   Security Check (Port 993) ", device_id="dev-1"
    )
    b = build_signature(
        metric_type="prtg_sensor", title="ssl security check (port 993)", device_id="dev-1"
    )
    assert a == b


def test_two_devices_same_title_do_not_merge():
    a = build_signature(metric_type="prtg_sensor", title="Ping v2", device_id="dev-A")
    b = build_signature(metric_type="prtg_sensor", title="Ping v2", device_id="dev-B")
    assert a != b


def test_missing_or_blank_title_returns_none():
    assert build_signature(metric_type="x", title=None) is None
    assert build_signature(metric_type="x", title="   ") is None


def test_normalize_metric_type():
    assert normalize_metric_type(None) is None
    assert normalize_metric_type("") is None
    assert normalize_metric_type("  PRTG_Sensor ") == "prtg_sensor"


def test_sensor_identity_takes_priority_over_device():
    a = build_signature(title="Ping v2", device_id="dev-1", sensor_id="s-1")
    b = build_signature(title="Ping v2", device_id="dev-1", sensor_id="s-2")
    assert a != b
