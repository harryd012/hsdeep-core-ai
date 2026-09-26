"""
Event Classification.

Deterministic, rule-based mapping from a Sensor's `metric_type` (or a
vendor-native signal's title/description) to a normalized Alert `category`.
Same "honest heuristic, not ML" philosophy as `AIInsightService` - every
rule here is a plain substring/keyword match over real stored fields, never
a random or simulated classification.

Categories (the sprint's "Event Classification" deliverable):
    availability   - the monitored thing is down/unreachable
    performance    - a monitored value is degraded but the thing is up
    capacity       - disk/memory/storage approaching a limit
    connectivity   - network path / interface / link issues
    configuration  - config drift, HA failover, reboot-type conditions
    security       - auth failures, security-flagged vendor signals
    other          - fallback when no rule matches

New monitoring platforms need zero code changes here unless they introduce
a genuinely new metric_type keyword pattern - the classifier works off the
metric_type/title text itself, not a per-vendor lookup table.
"""
from __future__ import annotations

# Ordered rule list: (category, keywords). Checked top-to-bottom against a
# lowercased metric_type/title string; first match wins. Order matters -
# more specific categories are checked before generic ones (e.g. "down"
# alone would otherwise be swallowed by a generic connectivity rule).
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("capacity", ("disk", "storage", "memory", "capacity", "quota")),
    ("performance", ("cpu", "latency", "response_time", "throughput", "performance", "load")),
    ("connectivity", ("interface", "traffic", "bandwidth", "link", "wan", "vpn", "network", "packet_loss")),
    ("security", ("auth", "login", "security", "intrusion", "certificate", "cert_expiry")),
    ("configuration", ("config", "reboot", "ha_failover", "firmware", "backup")),
    ("availability", ("ping", "uptime", "down", "unreachable", "status", "health")),
]

_DEFAULT_CATEGORY = "other"


def classify(*, metric_type: str | None = None, title: str | None = None, description: str | None = None) -> str:
    """Returns a normalized `category` string for an alert, given whichever
    of `metric_type` (sensor-threshold alerts) / `title`/`description`
    (vendor-native alerts) is available. Never raises - falls back to
    "other" when nothing matches or all inputs are None/empty."""
    haystack = " ".join(filter(None, [metric_type, title, description])).lower()
    if not haystack:
        return _DEFAULT_CATEGORY

    for category, keywords in _RULES:
        if any(keyword in haystack for keyword in keywords):
            return category

    return _DEFAULT_CATEGORY
