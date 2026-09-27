"""Threshold evaluation for vendor SNMP health readings (Phase 3).

PURE functions - no I/O, no DB, no OIDs. They classify the CPU/memory
readings that `VendorSnmpHealthProvider.read_health()` returns into the
warning/critical vocabulary the Sensor model already uses
(`warning_threshold` / `critical_threshold` / `status`), so the action
plane and the alert pipeline speak the same language.

Defaults are conservative industry conventions (CPU 80/95, memory 85/95
percent) and are OVERRIDABLE per request via params:
    {"thresholds": {"cpu": {"warning": 70, "critical": 90},
                    "memory": {"warning": 80, "critical": 92}}}

A reading breaches a threshold at >= (exactly-at counts as breached).
Missing readings stay "unknown" - a missing value is never coerced to ok.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Metric bucket -> (default warning, default critical), percent.
DEFAULT_THRESHOLDS: dict[str, tuple[float, float]] = {
    "cpu": (80.0, 95.0),
    "memory": (85.0, 95.0),
}

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"
STATUS_UNKNOWN = "unknown"

_WORST_ORDER = {STATUS_OK: 0, STATUS_UNKNOWN: 1, STATUS_WARNING: 2, STATUS_CRITICAL: 3}


@dataclass(frozen=True)
class ThresholdVerdict:
    """One reading's classification against its metric bucket's thresholds."""

    metric: str
    bucket: str
    value: float
    warning: float | None
    critical: float | None
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "bucket": self.bucket,
            "value": self.value,
            "warning": self.warning,
            "critical": self.critical,
            "status": self.status,
        }


def bucket_for(metric: str) -> str:
    """Map a reading label to its threshold bucket ('cpu' | 'memory')."""
    lowered = metric.lower()
    if lowered.startswith(("cpu", "processor", "process.")):
        return "cpu"
    if lowered.startswith("memory"):
        return "memory"
    return "memory"  # vendor memory pools are the only other bucket today


def evaluate_threshold(
    value: float, warning: float | None, critical: float | None
) -> str:
    """Classify one value. Breach is >= (exactly-at counts)."""
    if critical is not None and value >= critical:
        return STATUS_CRITICAL
    if warning is not None and value >= warning:
        return STATUS_WARNING
    return STATUS_OK


def extract_readings(output: dict[str, Any]) -> list[tuple[str, float]]:
    """Pull (label, percent-value) readings from a vendor health payload.

    Handles BOTH vendor shapes:
        * Cisco health  -> ``cpus`` / ``memory_pools``
        * Cisco processes -> ``processes`` (cpmProcessExtRevTable, composite index)
        * Aruba          -> ``processors`` / ``memory``
    Non-numeric or missing readings are skipped - never coerced.
    """
    readings: list[tuple[str, float]] = []
    for cpu in output.get("cpus", []) or []:
        index = cpu.get("index", "")
        for key in ("cpu_1min_pct", "cpu_5min_pct"):
            value = cpu.get(key)
            if isinstance(value, (int, float)):
                readings.append((f"cpu.{index}.{key}", float(value)))
    for proc in output.get("processes", []) or []:
        index = proc.get("index", "")
        pid = proc.get("pid", "")
        label = f"process.{index}" + (f".pid{pid}" if pid else "")
        for key in ("cpu_5sec_pct", "cpu_1min_pct", "cpu_5min_pct"):
            value = proc.get(key)
            if isinstance(value, (int, float)):
                readings.append((f"{label}.{key}", float(value)))
    for proc in output.get("processors", []) or []:
        # Aruba (WLSX-SYSTEMEXT-MIB) reports one load percentage per processor
        # rather than the Cisco "several CPU windows" shape.
        load = proc.get("load_pct")
        if isinstance(load, (int, float)):
            index = proc.get("index", "")
            readings.append((f"processor.{index}.load_pct", float(load)))
    memory = output.get("memory")
    if isinstance(memory, dict) and isinstance(memory.get("used_pct"), (int, float)):
        readings.append(("memory.used_pct", float(memory["used_pct"])))
    for pool in output.get("memory_pools", []) or []:
        value = pool.get("used_pct")
        if isinstance(value, (int, float)):
            label = pool.get("name") or f"pool{pool.get('index', '')}"
            readings.append((f"memory_pool.{label}.used_pct", float(value)))
    return readings


def resolve_thresholds(
    bucket: str, overrides: dict[str, Any] | None
) -> tuple[float | None, float | None]:
    """(warning, critical) for a bucket: params override the defaults.

    A malformed override (wrong type, non-numeric values) falls back to the
    defaults rather than raising - a bad params payload must never turn a
    successful device read into a failure.
    """
    default_warning, default_critical = DEFAULT_THRESHOLDS.get(
        bucket, (None, None)
    )
    override = (overrides or {}).get(bucket)
    if not isinstance(override, dict):
        override = {}

    def _num(key: str, default: float | None) -> float | None:
        value = override.get(key, default)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    return _num("warning", default_warning), _num("critical", default_critical)


def evaluate_health(
    output: dict[str, Any], threshold_overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Classify every reading in a vendor health payload.

    Returns {"verdicts": [...], "health_status": str} where health_status is
    the WORST verdict (critical > warning > unknown > ok) and "unknown" when
    the payload carried no readable readings at all.
    """
    verdicts: list[ThresholdVerdict] = []
    for metric, value in extract_readings(output):
        bucket = bucket_for(metric)
        warning, critical = resolve_thresholds(bucket, threshold_overrides)
        verdicts.append(
            ThresholdVerdict(
                metric=metric,
                bucket=bucket,
                value=value,
                warning=warning,
                critical=critical,
                status=evaluate_threshold(value, warning, critical),
            )
        )
    if not verdicts:
        return {"verdicts": [], "health_status": STATUS_UNKNOWN}
    worst = max(verdicts, key=lambda v: _WORST_ORDER[v.status]).status
    return {
        "verdicts": [v.as_dict() for v in verdicts],
        "health_status": worst,
    }
