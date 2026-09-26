"""
Device-type classification heuristics shared by the collectors.

The base ``device_type`` column is a free-form string with no enum. The
collectors previously hardcoded ``"other"`` (SNMP/PRTG) or ``"cloud_resource"``
(Meraki) because the raw vendor payloads carry no explicit "type" field — so
without classification, 100% of discovered devices land in the "Unclassified"
bucket (see the checkpoint sample: every device was ``other`` with empty
``custom_metadata``).

This module provides *honest* heuristics that derive a device_type (and, where
the payload exposes it, a vendor + model) from signals the collector already
has in hand — never inventing data:

* **SNMP** — the ``sysDescr`` string (OID .1.3.6.1.2.1.1.1.0) is already
  fetched by ``SNMPCollector.collect()``. It almost always contains the vendor
  name and platform family (e.g. ``"Cisco IOS Software, Catalyst 9300"``,
  ``"Juniper Networks Junos ... EX4300"``, ``"FortiGate 60F"``).
* **PRTG** — PRTG's device table carries no vendor column, but device *names*
  in PRTG deployments commonly encode role (``core-router-01``, ``edge-sw-2``).
  Classification is therefore name-based and deliberately best-effort.
* **Meraki** — the Dashboard API returns a ``model`` code whose prefix denotes
  the platform family (``MR``=AP, ``MS``=switch, ``MX``=security/firewall,
  ``MV``=camera, ``MT``=sensor, ``CW``=wireless).

Every helper returns the canonical vocabulary from
``app.models.device_types`` (and normalizes through
``normalize_device_type()``), so legacy spellings are never written by a
collector going forward. Anything the heuristic cannot determine is returned
as ``"unclassified"`` — the device is NOT force-fit into a category.
"""
from __future__ import annotations

import re

from app.models.device_types import (
    normalize_device_type,
)

# ---------------------------------------------------------------------------
# SNMP: classify from sysDescr
# ---------------------------------------------------------------------------

# Ordered "first match wins" rules. Each entry is (regex, type, vendor).
# Patterns are case-insensitive and intentionally conservative: a device whose
# sysDescr matches nothing is left "unclassified" rather than guessed.
#
# Vendor is only set when we can name it from sysDescr; model is left to the
# collector's own sysName/model parsing where available.
_SYDESCR_RULES: list[tuple[re.Pattern[str], str, str]] = [
    # --- Fortinet / FortiGate (firewall) -----------------------------------
    # Must come before the generic "Cisco" rules so a FortiGate isn't
    # misread as something else.
    (re.compile(r"fortinet|fortios|fortigate", re.I), "firewall", "Fortinet"),
    # --- Palo Alto (firewall) ---------------------------------------------
    (re.compile(r"palo alto|pan-os", re.I), "firewall", "Palo Alto"),
    # --- Juniper (router/switch) ------------------------------------------
    # MX-series = router, EX/QFX-series = switch. Other Juniper (SRX
    # firewall, ACX) default to switch/router heuristically by family token.
    (re.compile(r"juniper|junos", re.I), "switch", "Juniper"),
    # --- Cisco (router/switch) --------------------------------------------
    (re.compile(r"cisco", re.I), "switch", "Cisco"),
    # --- Ubiquiti / UniFi (access point / switch) -------------------------
    (re.compile(r"ubiquiti|unifi|uap-", re.I), "access_point", "Ubiquiti"),
    # --- Aruba (access point / switch) ------------------------------------
    (re.compile(r"aruba|airwave|instant on", re.I), "access_point", "Aruba"),
    # --- Linux host / generic server --------------------------------------
    (re.compile(r"linux", re.I), "server", "Linux"),
    # --- Windows host / generic server ------------------------------------
    (re.compile(r"windows nt|microsoft windows", re.I), "server", "Microsoft"),
]


def classify_from_sysdescr(
    sys_descr: str | None,
) -> tuple[str, str | None, str | None]:
    """Derive ``(device_type, vendor, model)`` from an SNMP ``sysDescr``.

    Returns canonical types via ``normalize_device_type()``. ``vendor`` and
    ``model`` are ``None`` when the sysDescr doesn't expose them — never
    invented. A sysDescr matching no rule yields ``("unclassified", None, None)``.
    """
    if not sys_descr:
        return normalize_device_type(None), None, None

    for pattern, device_type, vendor in _SYDESCR_RULES:
        if pattern.search(sys_descr):
            return normalize_device_type(device_type), vendor, None

    # Fallback: a sysDescr containing "switch"/"router"/"firewall"/"ap"
    # tokens we couldn't vendor-match → still classify the role but leave
    # vendor unknown.
    lower = sys_descr.lower()
    if "router" in lower:
        return "router", None, None
    if "switch" in lower:
        return "switch", None, None
    if "firewall" in lower:
        return "firewall", None, None
    if "access point" in lower or "wireless" in lower:
        return "access_point", None, None

    return normalize_device_type(None), None, None


# ---------------------------------------------------------------------------
# PRTG / generic: classify from a device name (best-effort)
# ---------------------------------------------------------------------------

# Ordered first-match-wins keyword rules for device *names*.
_NAME_RULES: list[tuple[str, str]] = [
    ("router", "router"),
    ("rtr-", "router"),
    ("-rtr", "router"),
    ("switch", "switch"),
    ("sw-", "switch"),
    ("-sw", "switch"),
    ("firewall", "firewall"),
    ("fw-", "firewall"),
    ("-fw", "firewall"),
    ("core", "switch"),       # "core" alone leans switch/router; caller can't tell
    ("ap", "access_point"),   # "ap-" / "-ap" / "accesspoint"
    ("access-point", "access_point"),
    ("access_point", "access_point"),
    ("server", "server"),
    ("srv-", "server"),
]


def classify_from_name(name: str | None) -> str:
    """Best-effort device_type from a device *name* (PRTG has no vendor
    column). Returns a canonical type or ``"unclassified"``.

    The Cisco/Juniper-style rules that disambiguate router-vs-switch don't
    apply when we only have a name, so a generic "core" device stays
    ``unclassified`` to avoid fabricating a switch-vs-router call — only
    explicit role tokens in the name (router/switch/firewall/ap/server)
    classify."""
    if not name:
        return normalize_device_type(None)
    lower = name.lower()
    for needle, device_type in _NAME_RULES:
        if needle in lower:
            return normalize_device_type(device_type)
    return normalize_device_type(None)


# ---------------------------------------------------------------------------
# Meraki: classify from a model code prefix
# ---------------------------------------------------------------------------

# Meraki model prefixes -> canonical device type. The Dashboard API returns
# codes like "MR36", "MS250-24", "MX68", "MV", "MT", "CW".
_MERAKI_MODEL_PREFIXES: list[tuple[str, str]] = [
    ("MR", "access_point"),
    ("MS", "switch"),
    ("MX", "firewall"),   # also "MV" camera vs "MX" firewall — order matters
    ("MV", "camera"),     # not in the canonical network set; normalizes -> unclassified
    ("MT", "sensor"),
    ("CW", "cloud_resource"),
    ("MG", "cellular_gateway"),
]


def classify_meraki_model(model: str | None) -> str:
    """Classify a Meraki device from its ``model`` code (e.g. ``"MR36"``).

    Returns a canonical type or ``"unclassified"`` for unknown prefixes.
    """
    if not model:
        return normalize_device_type(None)
    upper = model.upper()
    for prefix, device_type in _MERAKI_MODEL_PREFIXES:
        if upper.startswith(prefix):
            return normalize_device_type(device_type)
    return normalize_device_type(None)
