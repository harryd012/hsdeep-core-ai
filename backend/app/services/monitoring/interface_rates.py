"""Two-sample interface rate + utilisation (Phase 9.8, step 8).

An SNMP poll returns CUMULATIVE octet counters. A rate needs two samples of the
same counter over time, so this module is the only place that turns
``in_octets``/``out_octets`` into ``rx_bps``/``tx_bps``. Keeping it pure (no
database, no clock, no I/O) means every edge case - a first sample, a counter
reset, an absurd timestamp - is testable directly.

Honesty rules, in order of importance:

* one sample  -> NO rate, NO utilisation (never ``0``, which would claim a
  measured idle link);
* counter went backwards -> that direction is a RESET, so no rate from it (never
  a negative one);
* elapsed <= 0 -> no rate (a duplicate poll is not an infinite rate);
* no interface speed -> no utilisation percentage (bits/second are still valid);
* a rate above the interface speed is not published as utilisation: ifSpeed is a
  32-bit gauge that saturates near 4.29 Gbps, so a real 10 Gbps port would
  otherwise report >200%.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Why a rate could not be produced. Reported, never guessed away.
REASON_NO_COUNTERS = "no_counters"
REASON_NO_PREVIOUS_SAMPLE = "no_previous_sample"
REASON_INVALID_TIMESTAMPS = "invalid_timestamps"
REASON_NON_POSITIVE_ELAPSED = "non_positive_elapsed"
REASON_COUNTER_RESET = "counter_reset"
REASON_NO_SPEED = "no_speed"
REASON_RATE_EXCEEDS_SPEED = "rate_exceeds_speed"

#: Max plausible utilisation. Above this, the SPEED is the suspect value, not
#: the rate, so utilisation is withheld instead of published as >100%.
_MAX_UTILIZATION_PCT = 100.0


@dataclass(frozen=True)
class InterfaceRate:
    """The derived rate facts for ONE interface, or why they are unavailable."""

    rx_bps: float | None = None
    tx_bps: float | None = None
    utilization_pct: float | None = None
    #: None when at least one direction produced a rate.
    reason: str | None = None
    #: True when this sample carried real counters (drives telemetry freshness).
    has_counters: bool = False

    @property
    def measured(self) -> bool:
        return self.rx_bps is not None or self.tx_bps is not None


def _direction_rate(
    previous: float | None, current: float | None, elapsed: float
) -> tuple[float | None, bool]:
    """(bits_per_second, reset_detected) for ONE counter direction."""
    if previous is None or current is None:
        return None, False
    if current < previous:
        # Counter rollover or a device/agent restart: the delta is meaningless,
        # and a negative rate would be worse than no rate.
        return None, True
    return (current - previous) * 8.0 / elapsed, False


def compute_interface_rate(
    *,
    previous_in: float | None,
    previous_out: float | None,
    previous_at: datetime | None,
    current_in: float | None,
    current_out: float | None,
    observed_at: datetime,
    speed_mbps: float | None = None,
) -> InterfaceRate:
    """Derive rx/tx bits-per-second and utilisation from two counter samples."""
    has_counters = current_in is not None or current_out is not None
    if not has_counters:
        return InterfaceRate(reason=REASON_NO_COUNTERS)

    if previous_at is None or (previous_in is None and previous_out is None):
        # First observation: the counters are real, but there is no delta yet.
        return InterfaceRate(reason=REASON_NO_PREVIOUS_SAMPLE, has_counters=True)

    if previous_at.tzinfo is None or observed_at.tzinfo is None:
        return InterfaceRate(reason=REASON_INVALID_TIMESTAMPS, has_counters=True)

    elapsed = (observed_at - previous_at).total_seconds()
    if elapsed <= 0:
        return InterfaceRate(reason=REASON_NON_POSITIVE_ELAPSED, has_counters=True)

    rx_bps, rx_reset = _direction_rate(previous_in, current_in, elapsed)
    tx_bps, tx_reset = _direction_rate(previous_out, current_out, elapsed)

    reason = None
    if rx_bps is None and tx_bps is None:
        reason = REASON_COUNTER_RESET if (rx_reset or tx_reset) else REASON_NO_COUNTERS
        return InterfaceRate(reason=reason, has_counters=True)

    utilization = _utilization(rx_bps, tx_bps, speed_mbps)
    if utilization is None and speed_mbps is not None and speed_mbps > 0:
        reason = REASON_RATE_EXCEEDS_SPEED
    elif utilization is None:
        reason = REASON_NO_SPEED

    return InterfaceRate(
        rx_bps=rx_bps,
        tx_bps=tx_bps,
        utilization_pct=utilization,
        reason=None if utilization is not None else reason,
        has_counters=True,
    )


def _utilization(
    rx_bps: float | None, tx_bps: float | None, speed_mbps: float | None
) -> float | None:
    """Busiest-direction utilisation against the reported interface speed."""
    if speed_mbps is None or speed_mbps <= 0:
        return None
    speed_bps = speed_mbps * 1_000_000.0
    busiest = max(value for value in (rx_bps or 0.0, tx_bps or 0.0))
    if busiest <= 0:
        # A genuinely idle interface IS 0% - that is a measurement, not a guess.
        return 0.0
    percentage = busiest / speed_bps * 100.0
    if percentage > _MAX_UTILIZATION_PCT:
        return None
    return round(percentage, 4)