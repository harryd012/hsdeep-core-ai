"""
Sensor lifecycle and user thresholds (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P1).

Applied by `BundleSaver` to every sensor a collector reports, and by the
sensor API when a user changes a sensor, so both paths agree:

  * admin_state  active   - the collector's reading decides the status.
                 paused   - readings are kept, the status reads "paused" and
                            no alert is raised (an open one resolves).
                 disabled - readings are ignored (the saver skips the sensor).
  * thresholds   provider - the collector's thresholds and status stand.
                 user     - the user's thresholds survive every sync and
                            decide the status from the value; with no value
                            the collector's status stands.

Pure functions only (no I/O).
"""
from __future__ import annotations

from dataclasses import dataclass

ADMIN_STATES = ("active", "paused", "disabled")
#: Channel key of a sensor's own reading in its metric history.
PRIMARY_CHANNEL = "primary"
THRESHOLD_DIRECTIONS = ("above", "below")


@dataclass(frozen=True)
class UserThresholds:
    warning: float | None
    critical: float | None
    #: above: higher values are worse (CPU %); below: lower values are worse (free space %).
    direction: str


def threshold_status(value: float | None, t: UserThresholds, fallback: str) -> str:
    """up | warning | down from the value against the user's thresholds;
    `fallback` (the collector's status) when there is no value to judge."""
    if value is None:
        return fallback

    def crossed(limit: float | None) -> bool:
        if limit is None:
            return False
        return value >= limit if t.direction == "above" else value <= limit

    if crossed(t.critical):
        return "down"
    if crossed(t.warning):
        return "warning"
    return "up"


def effective_status(collector_status: str, value: float | None, admin_state: str,
                     thresholds: UserThresholds | None) -> str:
    """The status a sensor shows after its lifecycle and thresholds apply."""
    if admin_state == "paused":
        return "paused"
    if admin_state == "disabled":
        return "disabled"
    if thresholds is not None:
        return threshold_status(value, thresholds, collector_status)
    return collector_status


def user_thresholds_of(sensor) -> UserThresholds | None:
    """The sensor's own thresholds when the user set them, else None."""
    if getattr(sensor, "threshold_source", "provider") != "user":
        return None
    return UserThresholds(
        warning=sensor.warning_threshold,
        critical=sensor.critical_threshold,
        direction=sensor.threshold_direction or "above",
    )


__all__ = [
    "ADMIN_STATES", "PRIMARY_CHANNEL", "THRESHOLD_DIRECTIONS", "UserThresholds", "effective_status", "threshold_status",
    "user_thresholds_of",
]
