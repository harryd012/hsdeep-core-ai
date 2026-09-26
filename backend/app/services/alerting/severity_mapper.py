"""
Severity mapping - extracted from `AlertService` (where this lived as a
private `_STATUS_SEVERITY` dict) so both the sensor-threshold path and any
future vendor-native/classification path share exactly one mapping instead
of two copies drifting apart.

Kept deliberately tiny and dependency-free (no I/O, no imports from other
`alerting` modules) so it can be unit-tested in isolation and reused by
`EventClassifier`/`CorrelationEngine` without a circular import.
"""
from __future__ import annotations

# Sensor.status -> Alert.severity. "up" and "unknown" never open an alert
# (mapped to None - the caller's signal to auto-resolve/no-op instead).
_STATUS_SEVERITY: dict[str, str] = {
    "down": "critical",
    "warning": "warning",
}

# Ordering used to compare severities (e.g. "is this incident's max
# severity still accurate after adding this alert?", "should this alert's
# severity update override the existing one?"). Higher number = more severe.
_SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "warning": 1,
    "critical": 2,
}


def severity_for_sensor_status(status: str) -> str | None:
    """Maps a normalized Sensor.status to an Alert.severity, or None if
    that status should never open/keep an alert (up/unknown)."""
    return _STATUS_SEVERITY.get(status)


def severity_rank(severity: str) -> int:
    """Numeric rank for comparing two severities. Unknown severity strings
    rank below "info" (0) rather than raising, so a malformed/future
    severity value never crashes a comparison - it just loses."""
    return _SEVERITY_RANK.get(severity, -1)


def max_severity(severities: list[str]) -> str:
    """Highest-ranked severity in `severities`, defaulting to "info" for an
    empty list (never raises on empty input - callers may pass an incident
    with zero currently-active member alerts, e.g. right after the last one
    resolved)."""
    if not severities:
        return "info"
    return max(severities, key=severity_rank)
