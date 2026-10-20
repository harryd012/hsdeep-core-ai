"""Incident correlation signature normalization (single source of truth).

A signature groups repeated failures of the SAME probe into ONE incident. The
previous inline ``f"{metric_type}:{title}"`` had two defects that defeated dedup:

  * a NULL ``metric_type`` rendered as the literal string ``"None"``
    (``"None:SSL Security Check (Port 993)"``), which is unstable and confusing;
  * the signature ignored device/sensor/signal identity, so two devices with the
    same probe title could merge into one incident.

This module fixes both: a NULL metric_type contributes nothing, whitespace/case
are normalized, and device/sensor/signal identity (the same keys the Alert
partial-unique indexes use) is included so independent devices never merge.
"""
from __future__ import annotations

import uuid


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    v = " ".join(value.strip().lower().split())
    return v or None


def normalize_metric_type(value: str | None) -> str | None:
    """Lowercase + trim a metric_type; ``None``/blank -> ``None`` (never "None")."""
    return _norm(value)


def build_signature(
    *,
    metric_type: str | None = None,
    title: str | None = None,
    device_id: uuid.UUID | str | None = None,
    sensor_id: uuid.UUID | str | None = None,
    signal_external_id: str | None = None,
) -> str | None:
    """Return a normalized, stable probe signature, or ``None`` when unusable.

    ``None`` means "no usable signature" and the caller falls back to
    device/site-level grouping. The returned value is deterministic across
    case/whitespace and includes an identity key so two devices with the same
    title do NOT merge.
    """
    title_key = _norm(title)
    if title_key is None:
        return None
    mt = _norm(metric_type)

    # Identity key in priority order, mirroring the Alert partial-unique index
    # keys (per-sensor, per-signal), with device as the final fallback.
    identity: str | None = None
    if sensor_id is not None:
        identity = f"sensor:{sensor_id}"
    elif signal_external_id:
        identity = f"signal:{signal_external_id}"
    elif device_id is not None:
        identity = f"device:{device_id}"

    if identity is not None:
        return f"{identity}:{mt}:{title_key}" if mt else f"{identity}:{title_key}"
    return f"{mt}:{title_key}" if mt else title_key
