"""
Dynamic baseline maths (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P9 slice 2).

Pure functions. A sensor's baseline is the mean and standard deviation of its
own readings over the last WINDOW_DAYS, per hour of the day (UTC), with the
whole window as the fallback for an hour that has too few samples. A reading
is judged by how many standard deviations it lies from the expected value for
its hour (z). Honesty rules:

  * not enough history (fewer than MIN_SPAN_DAYS of data, or MIN_SAMPLES) ->
    "learning": no verdict, never an alert;
  * a flat series has a standard deviation of 0, so the spread is floored
    (SPREAD_FLOOR_RATIO of the mean, at least ABS_SPREAD_FLOOR) rather than
    calling every tiny change an anomaly;
  * an open anomaly clears only below RECOVERY_RATIO of the sensitivity
    (hysteresis), so a value hovering at the limit does not flap.
"""
from __future__ import annotations

from dataclasses import dataclass

WINDOW_DAYS = 14
MIN_SPAN_DAYS = 3
MIN_SAMPLES = 30
MIN_HOUR_SAMPLES = 5
SPREAD_FLOOR_RATIO = 0.05
ABS_SPREAD_FLOOR = 1e-6
RECOVERY_RATIO = 0.75
DIRECTIONS = ("both", "above", "below")


@dataclass(frozen=True)
class Stat:
    mean: float
    std: float
    n: int


@dataclass(frozen=True)
class Baseline:
    hours: dict[int, Stat]
    overall: Stat | None
    span_days: float

    @property
    def ready(self) -> bool:
        return self.overall is not None and self.overall.n >= MIN_SAMPLES and self.span_days >= MIN_SPAN_DAYS

    def expected(self, hour: int) -> tuple[Stat | None, str]:
        """The stat for this hour, and where it came from ("hour" | "overall")."""
        h = self.hours.get(hour)
        if h is not None and h.n >= MIN_HOUR_SAMPLES:
            return h, "hour"
        return self.overall, "overall"

    def to_json(self) -> dict:
        def s(x: Stat | None):
            return None if x is None else {"mean": x.mean, "std": x.std, "n": x.n}
        return {"hours": {str(k): s(v) for k, v in sorted(self.hours.items())}, "overall": s(self.overall), "span_days": self.span_days}

    @classmethod
    def from_json(cls, d: dict | None) -> "Baseline":
        d = d or {}

        def s(x):
            return None if not x else Stat(float(x["mean"]), float(x["std"]), int(x["n"]))
        return cls(hours={int(k): s(v) for k, v in (d.get("hours") or {}).items() if v},
                   overall=s(d.get("overall")), span_days=float(d.get("span_days") or 0.0))


def spread(stat: Stat) -> float:
    return max(stat.std, abs(stat.mean) * SPREAD_FLOOR_RATIO, ABS_SPREAD_FLOOR)


@dataclass(frozen=True)
class Verdict:
    #: learning | normal | anomalous
    state: str
    z: float | None = None
    expected: float | None = None
    low: float | None = None
    high: float | None = None
    basis: str | None = None


def judge(baseline: Baseline, value: float | None, hour: int, *, sensitivity: float, direction: str,
          currently_anomalous: bool) -> Verdict:
    """The verdict for one reading at one hour of the day."""
    if value is None or not baseline.ready:
        return Verdict("learning")
    stat, basis = baseline.expected(hour)
    if stat is None:
        return Verdict("learning")
    width = spread(stat)
    z = (value - stat.mean) / width
    limit = sensitivity * (RECOVERY_RATIO if currently_anomalous else 1.0)
    if direction == "above":
        outside = z >= limit
    elif direction == "below":
        outside = z <= -limit
    else:
        outside = abs(z) >= limit
    return Verdict(
        "anomalous" if outside else "normal", round(z, 3), stat.mean,
        stat.mean - sensitivity * width, stat.mean + sensitivity * width, basis,
    )


__all__ = [
    "Baseline", "DIRECTIONS", "MIN_SAMPLES", "MIN_SPAN_DAYS", "RECOVERY_RATIO", "Stat", "Verdict", "WINDOW_DAYS",
    "judge", "spread",
]
