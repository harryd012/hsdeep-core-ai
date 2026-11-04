"""
Syslog parsing (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P7 slice 1).

Turns one syslog datagram (RFC 5424, or the older BSD format of RFC 3164)
into an ingest event (`schemas.ingest.EventItem` shape, telemetry_type LOG).
Pure: no I/O, no clock (the receive time is passed in). Nothing is guessed:

  * a field the message does not carry stays absent (no invented host or app);
  * a message with no valid <PRI> is kept as `notice` / `user` and flagged
    `pri_missing`, never dropped;
  * a timestamp that cannot be read falls back to the receive time, flagged
    `timestamp_source: received`.

Severity mapping (syslog -> platform): 0-2 (emerg, alert, crit) critical,
3-4 (err, warning) warning, 5-7 (notice, info, debug) info.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

MAX_MESSAGE = 8192
TITLE_MAX = 500

FACILITIES = (
    "kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news", "uucp", "cron", "authpriv", "ftp",
    "ntp", "audit", "alert", "clock", "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7",
)
SEVERITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")

_PRI = re.compile(r"^<(\d{1,3})>")
# RFC 5424: VERSION SP TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID SP [SD] [SP MSG]
_RFC5424 = re.compile(r"^(\d{1,2}) (\S+) (\S+) (\S+) (\S+) (\S+) (-|(?:\[.*?\])+)(?: (.*))?$", re.S)
# RFC 3164: "Mmm dd hh:mm:ss HOST TAG[PID]: MSG" (host and tag optional in practice)
_BSD_TS = re.compile(r"^([A-Z][a-z]{2}) +(\d{1,2}) (\d{2}):(\d{2}):(\d{2}) ")
_BSD_TAG = re.compile(r"^([A-Za-z0-9_.\-/]{1,48})(?:\[(\d{1,10})\])?: ?")
_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_SD_ELEMENT = re.compile(r"\[([^ \]]+)((?: [^=\]]+=\"(?:[^\"\\]|\\.)*\")*)\]")
_SD_PARAM = re.compile(r" ([^=\]]+)=\"((?:[^\"\\]|\\.)*)\"")


def platform_severity(code: int) -> str:
    """Syslog severity 0-7 -> critical | warning | info."""
    if code <= 2:
        return "critical"
    if code <= 4:
        return "warning"
    return "info"


def _category(facility: str) -> str:
    if facility in ("auth", "authpriv", "audit"):
        return "security"
    if facility in ("kern", "daemon", "syslog", "cron", "ntp", "clock"):
        return "system"
    return "application"


def _nil(v: str) -> str | None:
    return None if v == "-" else v


def _bsd_time(m: re.Match, received_at: datetime) -> datetime | None:
    """RFC 3164 has no year or zone: the receive year is used (the previous one
    when that would put the message more than a day in the future), in UTC."""
    month = _MONTHS.get(m.group(1))
    if month is None:
        return None
    try:
        t = datetime(received_at.year, month, int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), tzinfo=timezone.utc)
    except ValueError:
        return None
    if (t - received_at).total_seconds() > 86400:
        t = t.replace(year=t.year - 1)
    return t


def _structured_data(sd: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for element in _SD_ELEMENT.finditer(sd or ""):
        out[element.group(1)] = {k: v.replace('\\"', '"').replace("\\\\", "\\") for k, v in _SD_PARAM.findall(element.group(2))}
    return out


def parse_syslog(data: bytes, *, sender: str, received_at: datetime) -> dict:
    """One datagram -> an ingest event dict (EventItem fields)."""
    text = data[:MAX_MESSAGE].decode("utf-8", errors="replace").strip("\r\n\x00 ")
    if text.startswith("﻿"):
        text = text[1:]
    attrs: dict = {"sender": sender, "format": "unknown"}

    pri = _PRI.match(text)
    if pri and int(pri.group(1)) <= 191:
        value = int(pri.group(1))
        facility_code, severity_code = divmod(value, 8)
        text = text[pri.end():]
    else:
        facility_code, severity_code = 1, 5  # user.notice, as RFC 3164 prescribes for a missing PRI
        attrs["pri_missing"] = True
    facility = FACILITIES[facility_code] if facility_code < len(FACILITIES) else f"facility{facility_code}"

    timestamp: datetime | None = None
    host = app = procid = msgid = None
    message = text

    m5424 = _RFC5424.match(text)
    if m5424 and m5424.group(1) == "1":
        attrs["format"] = "rfc5424"
        raw_ts, host, app, procid, msgid = (_nil(m5424.group(i)) for i in range(2, 7))
        if raw_ts:
            try:
                timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = None
            except ValueError:
                timestamp = None
        sd = _structured_data(m5424.group(7))
        if sd:
            attrs["structured_data"] = sd
        message = (m5424.group(8) or "").lstrip("﻿")
    else:
        mts = _BSD_TS.match(text)
        if mts:
            attrs["format"] = "rfc3164"
            timestamp = _bsd_time(mts, received_at)
            rest = text[mts.end():]
            # The HOSTNAME field is a single token before the tag.
            parts = rest.split(" ", 1)
            if len(parts) == 2 and not parts[0].endswith(":"):
                host, rest = parts[0], parts[1]
            tag = _BSD_TAG.match(rest)
            if tag:
                app, procid = tag.group(1), tag.group(2)
                rest = rest[tag.end():]
            message = rest

    message = message.strip() or "(empty message)"
    attrs.update({
        "facility": facility, "syslog_severity": SEVERITIES[severity_code],
        **({"host": host} if host else {}), **({"app": app} if app else {}),
        **({"procid": procid} if procid else {}), **({"msgid": msgid} if msgid else {}),
        "timestamp_source": "message" if timestamp else "received",
    })
    return {
        "title": message[:TITLE_MAX],
        "description": message if len(message) > TITLE_MAX else None,
        "severity": platform_severity(severity_code),
        "event_category": _category(facility),
        "event_type": f"syslog.{facility}",
        "telemetry_type": "LOG",
        "timestamp": (timestamp or received_at).astimezone(timezone.utc).isoformat(),
        "attributes": attrs,
    }


__all__ = ["FACILITIES", "SEVERITIES", "parse_syslog", "platform_severity"]
