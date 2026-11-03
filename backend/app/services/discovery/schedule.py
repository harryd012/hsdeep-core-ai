"""
Discovery scope schedules (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P2).

A scope's ``schedule`` is a repeat interval: a whole number followed by ``m``
(minutes), ``h`` (hours) or ``d`` (days), e.g. ``15m``, ``6h``, ``1d``, between
15 minutes and 30 days. It is stored normalized (lower case, no spaces) and
``None`` means the scope only runs when someone runs it.

The first scheduled run is one interval after the schedule is set, never at
once, so saving a schedule never starts an unplanned network sweep.

Pure functions only (no I/O).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

MIN_INTERVAL = timedelta(minutes=15)
MAX_INTERVAL = timedelta(days=30)

_PATTERN = re.compile(r"^(\d{1,5})([mhd])$")
_UNIT = {"m": "minutes", "h": "hours", "d": "days"}


class ScheduleInvalid(ValueError):
    pass


def parse_schedule(value: str) -> timedelta:
    """The interval a schedule string stands for; ScheduleInvalid otherwise."""
    match = _PATTERN.match(value.strip().lower().replace(" ", ""))
    if match is None:
        raise ScheduleInvalid(
            f"schedule {value!r} is not an interval; use a number and m, h or d (e.g. 15m, 6h, 1d)"
        )
    interval = timedelta(**{_UNIT[match.group(2)]: int(match.group(1))})
    if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
        raise ScheduleInvalid(f"schedule {value!r} must be between 15m and 30d")
    return interval


def normalize_schedule(value: str | None) -> str | None:
    """The stored form of a schedule (``None`` / blank clears it)."""
    if value is None or not value.strip():
        return None
    parse_schedule(value)
    return value.strip().lower().replace(" ", "")


def next_run_after(schedule: str | None, moment: datetime) -> datetime | None:
    """When a scope on ``schedule`` next runs, counted from ``moment``."""
    if schedule is None:
        return None
    return moment + parse_schedule(schedule)


__all__ = [
    "MAX_INTERVAL", "MIN_INTERVAL", "ScheduleInvalid", "next_run_after", "normalize_schedule", "parse_schedule",
]
