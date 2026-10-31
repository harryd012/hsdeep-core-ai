"""Palo Alto log/event normalization and correlation.

Normalizes provider traffic/threat/url/decrypt/system/GlobalProtect events
into one internal evidence model (EvidenceRef) without losing original
provider fields (kept verbatim in `attributes["raw"]`). Handles clock skew,
duplicate events, late-arriving events, and missing fields honestly.

Hard rules:
  - An allow log is NEVER proof the application worked.
  - A single deny log is NEVER sufficient proof of root cause alone.
  - Missing fields stay missing; never defaulted to a plausible value.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.paloalto import EvidenceKind, EvidenceRef


class NormalizedEvent:
    """One provider event after normalization.

    `raw` keeps the untouched provider record. Field accessors are `| None`
    because PAN-OS rows legitimately omit fields.
    """

    __slots__ = (
        "kind", "tenant_id", "device_id", "incident_id", "provider_timestamp",
        "collected_at", "source_ip", "dest_ip", "source_port", "dest_port",
        "protocol", "application", "source_zone", "dest_zone", "rule_name",
        "rule_action", "session_id", "bytes", "packets", "end_reason",
        "threat_name", "threat_severity", "threat_action", "url", "url_category",
        "url_action", "decryption_status", "tls_error", "user", "interface",
        "raw",
    )

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def flow_key(self) -> tuple | None:
        """Stable identity for correlation, or None when insufficient."""
        if self.session_id is not None:
            return ("session", str(self.session_id))
        if self.source_ip and self.dest_ip and self.dest_port is not None:
            return ("five", self.source_ip, self.dest_ip, self.dest_port, self.protocol)
        return None


_KIND_MAP = {
    "TRAFFIC": EvidenceKind.TRAFFIC_LOG,
    "THREAT": EvidenceKind.THREAT_LOG,
    "URL": EvidenceKind.URL_LOG,
    "DECRYPTION": EvidenceKind.DECRYPT_LOG,
    "SYSTEM": EvidenceKind.SYSTEM_LOG,
    "GLOBALPROTECT": EvidenceKind.GP_LOG,
    "SESSION": EvidenceKind.SESSION,
    "CONFIG": EvidenceKind.CONFIG,
}


def _ts(value: Any) -> datetime | None:
    """Parse a provider timestamp; return None when absent/unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        txt = value.strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(txt[:19], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


class LogNormalizer:
    """Normalize + correlate Palo Alto events into evidence."""

    def normalize(self, raw_event: dict[str, Any], *, tenant_id, device_id=None,
                  incident_id=None) -> NormalizedEvent | None:
        """Convert one provider dict into a NormalizedEvent.

        Returns None only when the event has no usable type; otherwise builds
        an honest event with whatever fields are actually present.
        """
        if not isinstance(raw_event, dict):
            return None
        log_type = (raw_event.get("type") or raw_event.get("log_type") or "").upper()
        if not log_type:
            return None

        provider_ts = _ts(
            raw_event.get("receive_time")
            or raw_event.get("time_generated")
            or raw_event.get("time")
        )
        collected_at = _ts(raw_event.get("_collected_at")) or datetime.now(timezone.utc)

        return NormalizedEvent(
            kind=_KIND_MAP.get(log_type),
            tenant_id=tenant_id,
            device_id=device_id,
            incident_id=incident_id,
            provider_timestamp=provider_ts,
            collected_at=collected_at,
            source_ip=raw_event.get("src") or raw_event.get("source_ip"),
            dest_ip=raw_event.get("dst") or raw_event.get("dest_ip"),
            source_port=_int(raw_event.get("src_port")),
            dest_port=_int(raw_event.get("dst_port")),
            protocol=raw_event.get("proto"),
            application=raw_event.get("app"),
            source_zone=raw_event.get("from_zone") or raw_event.get("src_zone"),
            dest_zone=raw_event.get("to_zone") or raw_event.get("dst_zone"),
            rule_name=raw_event.get("rule") or raw_event.get("rule_name"),
            rule_action=raw_event.get("action"),
            session_id=raw_event.get("sessionid") or raw_event.get("session_id"),
            bytes=_int(raw_event.get("bytes")),
            packets=_int(raw_event.get("packets")),
            end_reason=raw_event.get("repeat") or raw_event.get("reason"),
            threat_name=raw_event.get("threat_name") or raw_event.get("threatid"),
            threat_severity=raw_event.get("severity"),
            threat_action=raw_event.get("action"),
            url=raw_event.get("url"),
            url_category=raw_event.get("category"),
            url_action=raw_event.get("action"),
            decryption_status=raw_event.get("decryption_status") or raw_event.get("status"),
            tls_error=raw_event.get("error") or raw_event.get("tls_error"),
            user=raw_event.get("userid") or raw_event.get("user"),
            interface=raw_event.get("if_name") or raw_event.get("interface"),
            raw=dict(raw_event),
        )


from typing import Iterable  # noqa: E402

_CLOCK_SKEW = timedelta(minutes=2)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _summarize(ev: NormalizedEvent) -> str:
    parts = []
    if ev.rule_action:
        parts.append(str(ev.rule_action).upper())
    if ev.application:
        parts.append(f"app={ev.application}")
    if ev.source_ip and ev.dest_ip:
        parts.append(f"{ev.source_ip} -> {ev.dest_ip}")
    if ev.dest_port is not None:
        parts.append(f"port {ev.dest_port}")
    if ev.rule_name:
        parts.append(f"rule={ev.rule_name}")
    if ev.threat_name:
        parts.append(f"threat={ev.threat_name}")
    if ev.url:
        parts.append(f"url={ev.url}")
    if not parts:
        parts.append("provider event (fields sparse)")
    return "; ".join(parts)


def _to_evidence(ev: NormalizedEvent) -> EvidenceRef:
    """Project a NormalizedEvent into an EvidenceRef, preserving raw."""
    verified = ev.provider_timestamp is not None and ev.device_id is not None
    return EvidenceRef(
        kind=ev.kind or EvidenceKind.INFERRED,
        source_device_id=ev.device_id,
        collected_at=ev.collected_at,
        provider_timestamp=ev.provider_timestamp,
        summary=_summarize(ev),
        attributes={
            "raw": ev.raw,
            "rule_name": ev.rule_name,
            "rule_action": ev.rule_action,
            "application": ev.application,
            "user": ev.user,
        },
        verified=verified,
    )


def _correlate(events: Iterable[NormalizedEvent]) -> dict[tuple, list[NormalizedEvent]]:
    """Group events by flow/session identity. Events with no flow key go under
    ("unkeyed",) so they remain visible rather than silently dropped."""
    groups: dict[tuple, list[NormalizedEvent]] = {}
    for ev in events:
        key = ev.flow_key or ("unkeyed",)
        groups.setdefault(key, []).append(ev)
    for key in groups:
        groups[key].sort(key=_sort_ts)
    return groups


def _sort_ts(ev: NormalizedEvent) -> datetime:
    return ev.provider_timestamp or ev.collected_at or datetime.min.replace(tzinfo=timezone.utc)


def _dedupe(events: Iterable[NormalizedEvent]) -> list[NormalizedEvent]:
    """Drop exact duplicate events (same flow key + provider timestamp +
    action), keeping the first occurrence. Late/duplicate provider rows are
    common and must not double-count evidence."""
    seen: set[tuple] = set()
    out: list[NormalizedEvent] = []
    for ev in events:
        sig = (ev.flow_key, ev.provider_timestamp, ev.rule_action, ev.threat_name, ev.url)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(ev)
    return out


def _is_clock_skewed(ev: NormalizedEvent) -> bool:
    """True when provider and collection timestamps disagree beyond skew."""
    if ev.provider_timestamp is None or ev.collected_at is None:
        return False
    return abs(ev.collected_at - ev.provider_timestamp) > _CLOCK_SKEW


# Bind module-level helpers onto LogNormalizer as methods.
LogNormalizer.to_evidence = staticmethod(_to_evidence)
LogNormalizer.correlate = staticmethod(_correlate)
LogNormalizer.dedupe = staticmethod(_dedupe)
LogNormalizer.is_clock_skewed = staticmethod(_is_clock_skewed)
