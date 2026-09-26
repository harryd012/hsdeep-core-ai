"""
Live-validation readiness (Phase 3B §10) — READ-ONLY diagnostic.

Answers exactly one question per source: **can this configured discovery source
be polled right now, and if not, precisely why?**

Reuse, not a second framework
-----------------------------
This module never talks to a device itself. It drives the SAME path the real
collection uses:

    MonitoringService.check_readiness()
        -> MonitoringService._build_collector()   (registry + credential decrypt)
        -> collector.authenticate()               (the existing safe handshake)

...and then classifies the outcome. Because `_build_collector` is reused, the
probe exercises the same registry lookup and the same credential decryption as a
real collection - a validation path that decrypted credentials differently (or
not at all) would validate something other than production.

Persistence safety
------------------
Only `authenticate()` is called. `collect()`, `normalize()` and `save()` are
never invoked, and `DiscoveryService.write_observed_neighbors()` is never
reached, so a probe creates zero Devices, Interfaces, Capabilities,
Relationships or topology edges.

NOT a discovery claim
---------------------
`status == ready` means "the transport answered and credentials were accepted".
It does NOT mean LLDP/CDP neighbours, VLANs, STP or LAG were collected - that is
a strictly higher evidence level reached only by a real collection. Reachability
and discovery are reported as separate facts on purpose.

Honest classification
---------------------
Every rule below matches EVIDENCE. An unrecognised error falls through to a
non-ready status (`protocol_error`), because "we could not tell" must never be
reported as success. Where the underlying library genuinely cannot separate two
causes (pysnmp reports "no response" for both an unreachable host and a wrong
community) the message keeps both possibilities instead of picking one.
"""
from __future__ import annotations

import socket
from typing import Any

from app.collectors.base import (
    READINESS_AUTHENTICATION_FAILED,
    READINESS_AUTHORIZATION_FAILED,
    READINESS_CONFIGURATION_ERROR,
    READINESS_CREDENTIAL_ERROR,
    READINESS_PROTOCOL_ERROR,
    READINESS_READY,
    READINESS_TIMEOUT,
    READINESS_UNREACHABLE,
    READINESS_UNSUPPORTED,
    STAGE_AUTHENTICATION,
    STAGE_CONFIGURATION,
    STAGE_CREDENTIALS,
    STAGE_NETWORK,
    STAGE_PROTOCOL,
    STAGE_TRANSPORT,
    CollectorConfigError,
    ReadinessResult,
)

#: Display label per source type. Deliberately not the raw source_type: an
#: operator thinks in protocols ("WMI/WSMan"), not registry keys ("wmi").
PROTOCOL_LABELS: dict[str, str] = {
    "snmp": "SNMP",
    "wmi": "WMI/WSMan",
    "windows_winrm": "WMI/WSMan",
}

#: The protocols this phase makes readiness-checkable. Used to report
#: "not configured" coverage per protocol rather than silently omitting it.
READINESS_PROTOCOLS: tuple[str, ...] = ("snmp", "wmi")

_WINRM_HTTP_PORT = 5985
_WINRM_HTTPS_PORT = 5986
_SNMP_DEFAULT_PORT = 161

NOT_CONFIGURED_TARGET = "not configured"

# Config keys whose VALUES are secrets, used only for redaction. Matching is
# case-insensitive and substring-based so a future "snmp_community" key is
# covered without a code change.
_SECRET_KEY_HINTS: tuple[str, ...] = (
    "password", "passwd", "secret", "community", "token",
    "api_key", "apikey", "auth_key", "priv_key", "privacy",
)


def protocol_label(source_type: str | None) -> str:
    """Human-facing protocol name; falls back to the raw type, never empty."""
    if not source_type:
        return "unknown"
    return PROTOCOL_LABELS.get(source_type, source_type)


def resolved_port(source: Any) -> int | None:
    """The port the collector would actually use, or None if undeterminable.

    Mirrors `SNMPCollector._port()` (161) and `WindowsWinRMCollector`
    (`port or (5986 if https else 5985)`) so the reported target is the real
    one rather than a guess.
    """
    explicit = getattr(source, "port", None)
    if explicit:
        return int(explicit)
    source_type = getattr(source, "source_type", None)
    if source_type == "snmp":
        return _SNMP_DEFAULT_PORT
    if source_type in ("wmi", "windows_winrm"):
        protocol = (getattr(source, "protocol", None) or "http").lower()
        return _WINRM_HTTPS_PORT if protocol == "https" else _WINRM_HTTP_PORT
    return None


def safe_target(source: Any) -> str:
    """`host:port` (or bare host), or a clear "not configured".

    Built ONLY from hostname/port. Credentials are structurally unable to reach
    this string, which is stronger than scrubbing them out afterwards.
    """
    hostname = getattr(source, "hostname", None) or getattr(source, "ip_address", None)
    if not hostname:
        return NOT_CONFIGURED_TARGET
    port = resolved_port(source)
    return f"{hostname}:{port}" if port else str(hostname)


def secret_values(source: Any) -> list[str]:
    """Every credential-ish value on a source, for REDACTION ONLY.

    The return value must never be logged, serialised or returned to a caller.
    """
    secrets: list[str] = []
    for attr in ("password", "api_key"):
        value = getattr(source, attr, None)
        if value:
            secrets.append(str(value))
    config = getattr(source, "config", None)
    if isinstance(config, dict):
        for key, value in config.items():
            if value is None or isinstance(value, (dict, list)):
                continue
            lowered = str(key).lower()
            if any(hint in lowered for hint in _SECRET_KEY_HINTS):
                secrets.append(str(value))
    # Longest first so a value containing another is fully replaced.
    return sorted({s for s in secrets if len(s) >= 3}, key=len, reverse=True)


def redact(text: str | None, source: Any) -> str:
    """Scrub any configured secret out of `text`.

    Last line of defence: transport libraries sometimes echo the credential they
    were built with. tests/test_readiness_probe.py asserts that no secret
    survives into any probe field.
    """
    if not text:
        return ""
    result = str(text)
    for secret in secret_values(source):
        if secret in result:
            result = result.replace(secret, "[redacted]")
    return result


def configuration_problem(source: Any) -> tuple[str, str, str] | None:
    """(status, stage, message) for a config that cannot be probed at all.

    Returns None when configuration is sufficient to attempt a probe. Checked
    BEFORE any network call: a missing hostname must never produce a connect
    attempt, and must never be reported as "unreachable" (which would wrongly
    imply a network problem).
    """
    source_type = getattr(source, "source_type", None)
    label = protocol_label(source_type)
    hostname = getattr(source, "hostname", None) or getattr(source, "ip_address", None)

    if not hostname:
        return (
            READINESS_CONFIGURATION_ERROR,
            STAGE_CONFIGURATION,
            f"{label} hostname is missing",
        )

    if source_type == "snmp":
        version = (getattr(source, "snmp_version", None) or "v2c").lower()
        if version not in ("v1", "v2c", "v3"):
            return (
                READINESS_UNSUPPORTED,
                STAGE_CONFIGURATION,
                f"Unsupported SNMP version {version!r}; expected v1|v2c|v3",
            )
        if version == "v3" and not getattr(source, "username", None):
            return (
                READINESS_CREDENTIAL_ERROR,
                STAGE_CREDENTIALS,
                "SNMPv3 requires a username for the USM user",
            )
        return None

    if source_type in ("wmi", "windows_winrm"):
        missing = [
            field
            for field, value in (
                ("username", getattr(source, "username", None)),
                ("password", getattr(source, "password", None)),
            )
            if not value
        ]
        if missing:
            # Named, never valued: we say WHICH credential is absent, never what
            # it contains.
            return (
                READINESS_CREDENTIAL_ERROR,
                STAGE_CREDENTIALS,
                f"{label} credentials missing required field(s): {missing}",
            )
        auth_type = str(
            ((getattr(source, "config", None) or {}).get("auth_type") or "ntlm")
        ).lower()
        if auth_type not in ("ntlm", "basic", "kerberos"):
            return (
                READINESS_CONFIGURATION_ERROR,
                STAGE_CONFIGURATION,
                f"Unsupported {label} auth_type {auth_type!r}; "
                "expected ntlm|basic|kerberos",
            )
        return None

    return None


# Ordered evidence rules: (needles, status, stage, why). Most specific first.
# Each needle is matched case-insensitively against the collector's own failure
# text (or a stringified exception), whose wording comes from pysnmp / pywinrm
# exception names and messages.
_SNMP_MESSAGE_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("authenticationfailure", "wrongdigest", "unknownusername",
      "unknownsecurityname", "decryptionerror", "authentication failure"),
     READINESS_AUTHENTICATION_FAILED, STAGE_AUTHENTICATION,
     "SNMP authentication was rejected (wrong community string, or wrong "
     "SNMPv3 credentials)."),
    (("authorizationerror", "noaccess"),
     READINESS_AUTHORIZATION_FAILED, STAGE_PROTOCOL,
     "SNMP credentials were accepted but the target denied access to the "
     "readiness OID."),
    (("timeout", "timed out"),
     READINESS_TIMEOUT, STAGE_TRANSPORT,
     "SNMP request timed out with no response."),
    (("gaierror", "getaddrinfo", "name or service not known",
      "nodename nor servname", "temporary failure in name resolution",
      "no address associated"),
     READINESS_UNREACHABLE, STAGE_NETWORK,
     "SNMP hostname could not be resolved (DNS failure)."),
    (("connectionrefused", "refused", "network is unreachable",
      "no route to host"),
     READINESS_UNREACHABLE, STAGE_TRANSPORT,
     "SNMP target could not be reached on the network."),
    (("nosuchname", "nosuchobject", "nosuchinstance"),
     READINESS_PROTOCOL_ERROR, STAGE_PROTOCOL,
     "SNMP target rejected the readiness OID (unsupported or not viewable)."),
    # pysnmp reports the SAME indication for an unreachable host and a wrong
    # community. The message keeps both rather than inventing a winner.
    (("no response",),
     READINESS_UNREACHABLE, STAGE_TRANSPORT,
     "SNMP target did not respond to sysDescr. The library cannot separate "
     "'host unreachable' from 'wrong community'; both are possible."),
)

_WMI_MESSAGE_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("invalidcredentials", "authenticationerror", "logon failure",
      "auth error", "wrong password"),
     READINESS_AUTHENTICATION_FAILED, STAGE_AUTHENTICATION,
     "WinRM authentication was rejected (invalid credentials)."),
    (("unauthorizederror", "accessdenied", "access denied", "unauthorized",
      "forbidden"),
     READINESS_AUTHORIZATION_FAILED, STAGE_AUTHENTICATION,
     "WinRM credentials were accepted but the target denied access."),
    (("sslerror", "certificate", "cert validation", "ssl handshake", "tls"),
     READINESS_PROTOCOL_ERROR, STAGE_TRANSPORT,
     "WinRM TLS/certificate validation failed. Check the certificate, or set "
     "verify_ssl=false for a self-signed lab host."),
    (("protocol setup failed",),
     READINESS_CONFIGURATION_ERROR, STAGE_CONFIGURATION,
     "WinRM endpoint could not be constructed from the configured "
     "host/port/protocol."),
    (("timeout", "operationtimeout"),
     READINESS_TIMEOUT, STAGE_TRANSPORT,
     "WinRM request timed out."),
    (("gaierror", "getaddrinfo", "name or service not known",
      "nodename nor servname", "name resolution"),
     READINESS_UNREACHABLE, STAGE_NETWORK,
     "WinRM hostname could not be resolved (DNS failure)."),
    (("connectionrefused", "refused", "no route to host",
      "winrmtransporterror", "cannot connect", "failed to connect"),
     READINESS_UNREACHABLE, STAGE_TRANSPORT,
     "WinRM endpoint is not reachable. Is the WSMan listener enabled on "
     "5985/5986? (This probe speaks WSMan, not legacy DCOM on 135.)"),
    (("wsmanfault", "namespace", "wsman fault"),
     READINESS_PROTOCOL_ERROR, STAGE_PROTOCOL,
     "WinRM rejected the WSMan/WMI request (WSMan fault or invalid namespace)."),
)


def classify_failure(message: str | None, source: Any) -> tuple[str, str, str]:
    """(status, stage, message) for a failed probe, from whatever evidence exists.

    An UNRECOGNISED failure returns `protocol_error` - deliberately non-ready.
    Reporting an unknown error as `ready`, or as a confident specific cause the
    evidence does not support, are both worse than saying "unclassified".
    """
    source_type = getattr(source, "source_type", None)
    rules = (
        _WMI_MESSAGE_RULES
        if source_type in ("wmi", "windows_winrm")
        else _SNMP_MESSAGE_RULES
    )
    text = str(message or "").lower()
    for needles, status, stage, explanation in rules:
        if any(needle in text for needle in needles):
            return status, stage, explanation

    label = protocol_label(source_type)
    detail = redact(message, source) or "no diagnostic detail was returned"
    return (
        READINESS_PROTOCOL_ERROR,
        STAGE_PROTOCOL,
        f"{label} probe failed and the cause could not be classified: {detail}",
    )


def classify_exception(exc: BaseException, source: Any) -> tuple[str, str, str]:
    """Classify a REAL exception object (as opposed to stringified evidence).

    Type-based checks come first because they are reliable; only then do we fall
    back to matching the exception's text. This is the path where a TLS failure
    can be detected unambiguously (`ssl.SSLError`), and it is why the
    TLS/certificate case is classifiable at all: the WinRM collector reduces
    failures to `type(exc).__name__`, which cannot distinguish an SSL error
    wrapped inside a generic transport error.
    """
    import ssl

    source_type = getattr(source, "source_type", None)
    label = protocol_label(source_type)

    if isinstance(exc, CollectorConfigError):
        return READINESS_CONFIGURATION_ERROR, STAGE_CONFIGURATION, str(exc)
    if isinstance(exc, socket.gaierror):
        return (
            READINESS_UNREACHABLE,
            STAGE_NETWORK,
            f"{label} hostname could not be resolved (DNS failure).",
        )
    if isinstance(exc, ssl.SSLError):
        return (
            READINESS_PROTOCOL_ERROR,
            STAGE_TRANSPORT,
            f"{label} TLS/certificate validation failed.",
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return READINESS_TIMEOUT, STAGE_TRANSPORT, f"{label} request timed out."
    if isinstance(exc, ConnectionRefusedError):
        return (
            READINESS_UNREACHABLE,
            STAGE_TRANSPORT,
            f"{label} target refused the connection.",
        )
    if isinstance(exc, OSError):
        return (
            READINESS_UNREACHABLE,
            STAGE_TRANSPORT,
            f"{label} target could not be reached ({type(exc).__name__}).",
        )

    # Fall back to the exception's own text, which for pysnmp/pywinrm carries
    # the real indication (and whose class NAME carries the WinRM error kind).
    return classify_failure(f"{type(exc).__name__}: {exc}", source)


def ready_result(
    source: Any, message: str, extra_details: dict[str, Any] | None = None
) -> ReadinessResult:
    """A successful probe: the transport answered AND credentials were accepted.

    Stage is `protocol`, never `ready`-as-in-collected: this says nothing about
    whether any neighbour, VLAN or interface was discovered.
    """
    return build_result(
        source,
        status=READINESS_READY,
        stage=STAGE_PROTOCOL,
        message=message,
        extra_details=extra_details,
    )


def not_configured_result(source_type: str) -> ReadinessResult:
    """Result for a protocol with NO source configured at all.

    Reported explicitly rather than omitted: "we never checked because nothing
    is configured" is a different, and actionable, fact from "we checked and it
    failed". No fake source is invented to hold it.
    """
    labels = {"snmp": "SNMP", "wmi": "WMI"}
    display = labels.get(source_type, source_type.upper())
    return ReadinessResult(
        status=READINESS_CONFIGURATION_ERROR,
        stage=STAGE_CONFIGURATION,
        message=f"No {display} discovery source is configured",
        target=NOT_CONFIGURED_TARGET,
        protocol=protocol_label(source_type),
        safe_details={"source_type": source_type, "configured": False},
    )


def build_result(
    source: Any,
    *,
    status: str,
    stage: str,
    message: str,
    extra_details: dict[str, Any] | None = None,
) -> ReadinessResult:
    """Assemble a ReadinessResult with every field redacted and secret-free."""
    source_type = getattr(source, "source_type", None)
    details: dict[str, Any] = {
        "port": resolved_port(source),
        "source_type": source_type,
        "source_id": str(getattr(source, "id", "") or "") or None,
        "name": getattr(source, "name", None),
    }
    if source_type == "snmp":
        details["snmp_version"] = (
            getattr(source, "snmp_version", None) or "v2c"
        ).lower()
    if source_type in ("wmi", "windows_winrm"):
        details["transport"] = (getattr(source, "protocol", None) or "http").lower()
        details["auth_type"] = str(
            ((getattr(source, "config", None) or {}).get("auth_type") or "ntlm")
        ).lower()
    if extra_details:
        details.update(extra_details)

    # EVERY string value is redacted, not just `message`. `safe_details` is part
    # of the API response, so a caller passing a raw collector message through
    # here must not be able to leak a credential. (This exact leak - a community
    # string reaching `safe_details["collector_message"]` - is what
    # tests/test_readiness_probe.py::test_probe_never_exposes_the_community_string
    # caught during development.)
    details = {
        key: (redact(value, source) if isinstance(value, str) else value)
        for key, value in details.items()
    }

    return ReadinessResult(
        status=status,
        stage=stage,
        message=redact(message, source),
        target=safe_target(source),
        protocol=protocol_label(source_type),
        safe_details=details,
    )
