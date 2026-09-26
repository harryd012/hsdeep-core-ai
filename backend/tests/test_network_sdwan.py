"""SD-WAN path intelligence tests (HSDEEP network intelligence).

Pure computation layer: no I/O, no DB, no routers, no tunnels. Fixtures use
plain dicts shaped like normalised collector telemetry (PRTG / Meraki /
FortiGate / custom REST can all supply this shape).
"""
from __future__ import annotations

from app.services.network_intelligence.sdwan import (
    DECISION_FORWARD,
    DECISION_NO_AVAILABLE_PATH,
    evaluate_paths,
    health_of,
    resolve_weights,
    score_path,
    WanPath,
)

WAN1_HEALTHY = {
    "name": "WAN1",
    "latency_ms": 45,
    "jitter_ms": 8,
    "packet_loss_pct": 0.4,
    "bandwidth_mbps": 200,
    "available": True,
}
WAN2_HEALTHY = {
    "name": "WAN2",
    "latency_ms": 35,
    "jitter_ms": 7,
    "packet_loss_pct": 0.5,
    "bandwidth_mbps": 500,
    "available": True,
}
WAN1_DEGRADED = {
    "name": "WAN1",
    "latency_ms": 180,
    "jitter_ms": 42,
    "packet_loss_pct": 8,
    "bandwidth_mbps": 200,
    "available": True,
}


class TestSdwanScoring:
    def test_healthy_wan1_selected(self):
        result = evaluate_paths([WAN1_HEALTHY, WAN2_HEALTHY])
        assert result["decision"] == DECISION_FORWARD
        assert result["selected_path"] in ("WAN1", "WAN2")
        assert result["score"] > 0

    def test_degraded_wan1_causes_wan2_selection(self):
        result = evaluate_paths([WAN1_DEGRADED, WAN2_HEALTHY])
        assert result["decision"] == DECISION_FORWARD
        assert result["selected_path"] == "WAN2"
        assert result["failover"] is True
        degraded = next(p for p in result["paths"] if p["name"] == "WAN1")
        assert degraded["health"] in ("Degraded", "Down")

    def test_wan1_unavailable_causes_wan2_selection(self):
        down = {**WAN1_HEALTHY, "available": False}
        result = evaluate_paths([down, WAN2_HEALTHY])
        assert result["decision"] == DECISION_FORWARD
        assert result["selected_path"] == "WAN2"
        assert result["failover"] is True

    def test_both_wans_unavailable_returns_no_path(self):
        result = evaluate_paths([
            {**WAN1_HEALTHY, "available": False},
            {**WAN2_HEALTHY, "available": False},
        ])
        assert result["decision"] == DECISION_NO_AVAILABLE_PATH
        assert result["selected_path"] is None
        assert result["failover"] is False

    def test_equal_paths_produce_deterministic_selection(self):
        a = evaluate_paths([WAN1_HEALTHY, WAN2_HEALTHY])
        b = evaluate_paths([WAN2_HEALTHY, WAN1_HEALTHY])
        assert a["selected_path"] == b["selected_path"]
        assert a["score"] == b["score"]

    def test_weights_are_configurable(self):
        custom = {"latency": 0.9, "jitter": 0.05, "packet_loss": 0.05,
                  "bandwidth": 0.0, "availability": 0.0}
        w = resolve_weights(custom)
        assert abs(sum(w.values()) - 1.0) < 1e-9
        # latency-heavy weights favour the lower-latency path
        slow = {**WAN2_HEALTHY, "name": "SLOW", "latency_ms": 90.0}
        fast = {**WAN2_HEALTHY, "name": "FAST", "latency_ms": 10.0}
        assert evaluate_paths([slow, fast], custom)["selected_path"] == "FAST"

    def test_health_vocabulary(self):
        assert health_of(90.0, True) == "Healthy"
        assert health_of(50.0, True) == "Degraded"
        assert health_of(90.0, False) == "Down"
        assert health_of(5.0, True) == "Down"

    def test_unavailable_scores_zero(self):
        scored = score_path(WanPath.from_dict({**WAN1_HEALTHY, "available": False}))
        assert scored.score == 0.0
