"""Deterministic SD-WAN path intelligence (HSDEEP network intelligence).

Pure computation layer: no I/O, no DB, no routers, no tunnels. It scores
candidate WAN paths from normalised telemetry (PRTG / Meraki / FortiGate /
custom collectors can all supply the shaped input) and selects the best
available path with honest failover semantics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Default contribution weights. Must sum to 1.0; normalised defensively in
# resolve_weights() so callers never need to handle the arithmetic.
DEFAULT_PATH_WEIGHTS: dict[str, float] = {
    "latency": 0.30,
    "jitter": 0.20,
    "packet_loss": 0.30,
    "bandwidth": 0.10,
    "availability": 0.10,
}

REFERENCE_LATENCY_MS = 100.0
REFERENCE_JITTER_MS = 30.0
LOSS_CAP_PCT = 10.0
REFERENCE_BANDWIDTH_MBPS = 1000.0

DECISION_FORWARD = "FORWARD"
DECISION_NO_AVAILABLE_PATH = "NO_AVAILABLE_PATH"


@dataclass(frozen=True)
class WanPath:
    """One normalised candidate WAN path."""

    name: str
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    packet_loss_pct: float = 0.0
    bandwidth_mbps: float = 0.0
    available: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WanPath":
        """Coerce a telemetry dict; unparsable numerics become worst-case."""
        def _num(key: str, default: float) -> float:
            try:
                return float(payload.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            name=str(payload.get("name", "")),
            latency_ms=max(0.0, _num("latency_ms", REFERENCE_LATENCY_MS * 3)),
            jitter_ms=max(0.0, _num("jitter_ms", REFERENCE_JITTER_MS * 3)),
            packet_loss_pct=max(0.0, _num("packet_loss_pct", LOSS_CAP_PCT)),
            bandwidth_mbps=max(0.0, _num("bandwidth_mbps", 0.0)),
            available=bool(payload.get("available", True)),
        )


@dataclass(frozen=True)
class ScoredPath:
    """A path plus its deterministic score breakdown."""

    path: WanPath
    score: float
    components: dict[str, float] = field(default_factory=dict)


def resolve_weights(overrides: dict[str, Any] | None) -> dict[str, float]:
    """Merge caller weights over defaults; always normalise to sum 1.0."""
    merged = dict(DEFAULT_PATH_WEIGHTS)
    for key, value in (overrides or {}).items():
        if key not in merged:
            continue
        try:
            merged[key] = float(value)
        except (TypeError, ValueError):
            continue
    total = sum(max(0.0, v) for v in merged.values())
    if total <= 0:
        return dict(DEFAULT_PATH_WEIGHTS)
    return {k: max(0.0, v) / total for k, v in merged.items()}


def score_path(path: WanPath, weights: dict[str, float] | None = None) -> ScoredPath:
    """Score one path 0-100. Unavailable paths always score 0."""
    w = resolve_weights(weights)
    if not path.available:
        return ScoredPath(path=path, score=0.0, components={
            "latency": 0.0, "jitter": 0.0, "packet_loss": 0.0,
            "bandwidth": 0.0, "availability": 0.0,
        })
    latency_c = 100.0 * math.exp(-max(0.0, path.latency_ms) / REFERENCE_LATENCY_MS)
    jitter_c = 100.0 * math.exp(-max(0.0, path.jitter_ms) / REFERENCE_JITTER_MS)
    loss_c = 100.0 * max(0.0, 1.0 - path.packet_loss_pct / LOSS_CAP_PCT)
    bw_ratio = math.log1p(max(0.0, path.bandwidth_mbps)) / math.log1p(REFERENCE_BANDWIDTH_MBPS)
    bw_c = 100.0 * min(1.0, bw_ratio)
    components = {
        "latency": round(latency_c, 2),
        "jitter": round(jitter_c, 2),
        "packet_loss": round(loss_c, 2),
        "bandwidth": round(bw_c, 2),
        "availability": 100.0,
    }
    total = sum(components[k] * w.get(k, 0.0) for k in components)
    score = round(min(100.0, max(0.0, total)), 1)
    return ScoredPath(path=path, score=score, components=components)


def health_of(score: float, available: bool) -> str:
    """Path health vocabulary: Healthy / Degraded / Down."""
    if not available:
        return "Down"
    if score >= 70.0:
        return "Healthy"
    if score >= 40.0:
        return "Degraded"
    return "Down"


def evaluate_paths(
    paths: list[WanPath] | list[dict[str, Any]],
    weights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate candidates and select the best available path.

    Deterministic: score desc, ties broken by path name ascending, so equal
    inputs select identically regardless of input order.
    """
    w = resolve_weights(weights)
    normalised = [
        p if isinstance(p, WanPath) else WanPath.from_dict(p) for p in (paths or [])
    ]
    scored = [score_path(p, w) for p in normalised]
    # Stable sort: name-asc pre-order, then score-desc keeps name order on ties.
    scored.sort(key=lambda s: s.path.name)
    scored.sort(key=lambda s: s.score, reverse=True)

    entries = [
        {
            "name": s.path.name,
            "score": s.score,
            "health": health_of(s.score, s.path.available),
            "latency_ms": s.path.latency_ms,
            "jitter_ms": s.path.jitter_ms,
            "packet_loss_pct": s.path.packet_loss_pct,
            "bandwidth_mbps": s.path.bandwidth_mbps,
            "available": s.path.available,
            "components": s.components,
        }
        for s in scored
    ]
    live = [s for s in scored if s.path.available]
    if not live:
        return {
            "selected_path": None,
            "score": 0.0,
            "decision": DECISION_NO_AVAILABLE_PATH,
            "paths": entries,
            "failover": False,
            "reason": "No WAN path is available",
        }
    best = live[0]
    failed_over = any(not s.path.available for s in scored)
    degraded = any(
        health_of(s.score, s.path.available) != "Healthy" for s in live
    )
    if best.score >= 70.0:
        reason = f"{best.path.name} provides the best available path"
    else:
        reason = f"{best.path.name} selected; all available paths degraded"
    return {
        "selected_path": best.path.name,
        "score": best.score,
        "decision": DECISION_FORWARD,
        "paths": entries,
        "failover": failed_over or degraded,
        "reason": reason,
    }

