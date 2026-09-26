"""
Phase 3B §10 tests: live-validation readiness probe.

The probe is READ-ONLY and diagnostic. Two properties matter most and are
asserted explicitly throughout:

1. It never reports a state it cannot justify. An unrecognised failure must come
   back non-ready, and `ready` means only "transport answered + credentials
   accepted" - never "discovery data was collected".
2. It never leaks a credential - not into the result, its details, its message,
   its target, or an exception string.
"""
from __future__ import annotations

import ssl
import socket
import uuid
from types import SimpleNamespace

import pytest

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
    ConnectionCheck,
)
from app.services.monitoring import readiness as rd

SNMP_SECRET = "s3cr3t-community"
WMI_PASSWORD = "S3cr3t-Passw0rd!"


def _snmp(**overrides):
    fields = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="network-discovery",
        source_type="snmp", hostname="10.0.0.1", ip_address="10.0.0.1",
        port=None, protocol="snmp", snmp_version="v2c", username=None,
        api_key=SNMP_SECRET, password=None, config={}, verify_ssl=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _wmi(**overrides):
    fields = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="win-host",
        source_type="wmi", hostname="10.0.0.5", ip_address="10.0.0.5",
        port=None, protocol="http", snmp_version=None,
        username="CORP\\svc-monitor", api_key=None, password=WMI_PASSWORD,
        config={}, verify_ssl=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _flat(result) -> str:
    """Everything a caller could ever see, as one string, for leak checks."""
    return repr((result.status, result.stage, result.message, result.target,
                 result.protocol, result.safe_details))


class TestTargetAndPorts:
    """Target/port must mirror what the COLLECTOR would actually use."""

    def test_snmp_default_port_is_161(self):
        assert rd.resolved_port(_snmp()) == 161
        assert rd.safe_target(_snmp()) == "10.0.0.1:161"

    def test_snmp_explicit_port_wins(self):
        assert rd.resolved_port(_snmp(port=1161)) == 1161

    def test_wmi_http_default_port_is_5985(self):
        assert rd.resolved_port(_wmi()) == 5985
        assert rd.safe_target(_wmi()) == "10.0.0.5:5985"

    def test_wmi_https_default_port_is_5986(self):
        assert rd.resolved_port(_wmi(protocol="https")) == 5986

    def test_wmi_explicit_port_wins(self):
        assert rd.resolved_port(_wmi(port=5990)) == 5990

    def test_missing_hostname_reports_not_configured(self):
        assert rd.safe_target(_snmp(hostname=None, ip_address=None)) == "not configured"

    def test_ip_address_used_when_hostname_absent(self):
        assert rd.safe_target(_snmp(hostname=None, ip_address="10.9.9.9")) == "10.9.9.9:161"


class TestSecretSafety:

    def test_secret_values_collects_api_key_and_password(self):
        assert SNMP_SECRET in rd.secret_values(_snmp())
        assert WMI_PASSWORD in rd.secret_values(_wmi())

    def test_secret_values_reads_config_secret_keys(self):
        src = _snmp(config={"community": "cfg-secret", "auth_type": "v3"})
        assert "cfg-secret" in rd.secret_values(src)

    def test_redact_replaces_the_secret(self):
        src = _snmp()
        assert SNMP_SECRET not in rd.redact(f"failed with {SNMP_SECRET}", src)
        assert "[redacted]" in rd.redact(f"failed with {SNMP_SECRET}", src)

    def test_redact_is_harmless_without_secrets(self):
        assert rd.redact("plain message", _snmp()) == "plain message"
        assert rd.redact(None, _snmp()) == ""

    def test_target_never_contains_a_credential(self):
        """`target` is built only from hostname/port, so this holds by
        construction rather than by scrubbing."""
        for src in (_snmp(), _wmi()):
            assert SNMP_SECRET not in rd.safe_target(src)
            assert WMI_PASSWORD not in rd.safe_target(src)

    def test_build_result_leaks_no_secret_anywhere(self):
        """Redaction is scoped to the source being probed - which is the only
        credential its collector could ever embed in a message. Cross-source
        redaction is impossible here: an SNMP source has no knowledge of a WMI
        password, so asserting that would test a guarantee that cannot exist."""
        snmp_result = rd.build_result(
            _snmp(),
            status=READINESS_PROTOCOL_ERROR,
            stage=STAGE_PROTOCOL,
            message=f"boom {SNMP_SECRET}",
        )
        assert SNMP_SECRET not in _flat(snmp_result)
        assert "[redacted]" in snmp_result.message

        wmi_result = rd.build_result(
            _wmi(),
            status=READINESS_PROTOCOL_ERROR,
            stage=STAGE_PROTOCOL,
            message=f"boom {WMI_PASSWORD}",
        )
        assert WMI_PASSWORD not in _flat(wmi_result)
        assert "[redacted]" in wmi_result.message

    def test_build_result_redacts_every_source_secret(self):
        """A source can carry more than one secret (community + password); all
        of them must be scrubbed, not just the first."""
        src = _snmp(api_key="api-secret-value", password="v3-auth-key-value")
        result = rd.build_result(
            src,
            status=READINESS_PROTOCOL_ERROR,
            stage=STAGE_PROTOCOL,
            message="boom api-secret-value and v3-auth-key-value",
        )
        assert "api-secret-value" not in _flat(result)
        assert "v3-auth-key-value" not in _flat(result)

    def test_details_never_include_a_credential_field(self):
        result = rd.build_result(
            _wmi(config={"auth_type": "ntlm", "password": "nope"}),
            status=READINESS_READY, stage=STAGE_PROTOCOL, message="ok",
        )
        assert "nope" not in repr(result.safe_details)
        assert "password" not in result.safe_details


class TestConfigurationValidation:
    """Checked BEFORE any network call, so a config gap can never be reported as
    a network problem."""

    def test_snmp_missing_hostname(self):
        status, stage, message = rd.configuration_problem(
            _snmp(hostname=None, ip_address=None)
        )
        assert status == READINESS_CONFIGURATION_ERROR
        assert stage == STAGE_CONFIGURATION
        assert "hostname is missing" in message

    def test_wmi_missing_hostname(self):
        status, stage, message = rd.configuration_problem(
            _wmi(hostname=None, ip_address=None)
        )
        assert status == READINESS_CONFIGURATION_ERROR
        assert stage == STAGE_CONFIGURATION
        assert "hostname is missing" in message

    def test_wmi_missing_credentials_is_a_credential_error(self):
        status, stage, message = rd.configuration_problem(
            _wmi(username=None, password=None)
        )
        assert status == READINESS_CREDENTIAL_ERROR
        assert stage == STAGE_CREDENTIALS
        # Names the missing field, never its value.
        assert "username" in message
        assert WMI_PASSWORD not in message

    def test_wmi_bad_auth_type(self):
        status, stage, _ = rd.configuration_problem(
            _wmi(config={"auth_type": "negotiate"})
        )
        assert status == READINESS_CONFIGURATION_ERROR
        assert stage == STAGE_CONFIGURATION

    def test_snmp_unsupported_version(self):
        status, stage, _ = rd.configuration_problem(_snmp(snmp_version="v9"))
        assert status == READINESS_UNSUPPORTED
        assert stage == STAGE_CONFIGURATION

    def test_snmp_v3_without_username_is_a_credential_error(self):
        status, stage, _ = rd.configuration_problem(
            _snmp(snmp_version="v3", username=None)
        )
        assert status == READINESS_CREDENTIAL_ERROR
        assert stage == STAGE_CREDENTIALS

    def test_valid_snmp_config_has_no_problem(self):
        assert rd.configuration_problem(_snmp()) is None

    def test_valid_wmi_config_has_no_problem(self):
        assert rd.configuration_problem(_wmi()) is None


class TestSnmpClassification:
    """SNMP failures classified from the evidence pysnmp actually provides."""

    def test_authentication_failure(self):
        status, stage, _ = rd.classify_failure(
            "SNMP connection failed: AuthenticationFailure: authenticationFailure",
            _snmp(),
        )
        assert status == READINESS_AUTHENTICATION_FAILED
        assert stage == STAGE_AUTHENTICATION

    def test_wrong_digest_is_authentication(self):
        status, _, _ = rd.classify_failure("usm: wrongDigest", _snmp())
        assert status == READINESS_AUTHENTICATION_FAILED

    def test_authorization_error(self):
        status, _, _ = rd.classify_failure(
            "SNMP connection failed: authorizationError", _snmp()
        )
        assert status == READINESS_AUTHORIZATION_FAILED

    def test_timeout(self):
        status, stage, _ = rd.classify_failure(
            "SNMP connection failed: TimeoutError: request timed out", _snmp()
        )
        assert status == READINESS_TIMEOUT
        assert stage == STAGE_TRANSPORT

    def test_dns_failure_is_a_network_stage_problem(self):
        status, stage, message = rd.classify_failure(
            "SNMP connection failed: gaierror: [Errno -2] Name or service not known",
            _snmp(),
        )
        assert status == READINESS_UNREACHABLE
        assert stage == STAGE_NETWORK
        assert "DNS" in message

    def test_connection_refused(self):
        status, _, _ = rd.classify_failure(
            "SNMP connection failed: ConnectionRefusedError: refused", _snmp()
        )
        assert status == READINESS_UNREACHABLE

    def test_no_response_keeps_both_possibilities(self):
        """pysnmp cannot separate 'host unreachable' from 'wrong community'.
        Picking one would be an invented diagnosis."""
        status, stage, message = rd.classify_failure(
            "SNMP: no response to sysDescr GET (host unreachable or wrong "
            "community).",
            _snmp(),
        )
        assert status == READINESS_UNREACHABLE
        assert stage == STAGE_TRANSPORT
        assert "wrong community" in message

    def test_unknown_snmp_error_is_not_reported_as_ready(self):
        status, _, message = rd.classify_failure("kaboom", _snmp())
        assert status == READINESS_PROTOCOL_ERROR
        assert status != READINESS_READY
        assert "could not be classified" in message

    def test_snmp_failure_message_leaks_no_secret(self):
        status, stage, message = rd.classify_failure(
            f"SNMP connection failed: bad community {SNMP_SECRET}", _snmp()
        )
        assert SNMP_SECRET not in message
        assert "[redacted]" in message


class TestWmiClassification:
    """The WinRM collector reduces failures to `type(exc).__name__`, so these
    match the pywinrm exception names the collector actually embeds."""

    def test_invalid_credentials(self):
        status, stage, _ = rd.classify_failure(
            "WMI authentication failed: InvalidCredentialsError", _wmi()
        )
        assert status == READINESS_AUTHENTICATION_FAILED
        assert stage == STAGE_AUTHENTICATION

    def test_access_denied_is_authorization_not_authentication(self):
        """Distinct states: credentials accepted but access refused."""
        status, _, _ = rd.classify_failure(
            "WMI authentication failed: UnauthorizedError", _wmi()
        )
        assert status == READINESS_AUTHORIZATION_FAILED
        assert status != READINESS_AUTHENTICATION_FAILED

    def test_timeout(self):
        status, stage, _ = rd.classify_failure(
            "WMI authentication failed: WinRMOperationTimeoutError", _wmi()
        )
        assert status == READINESS_TIMEOUT
        assert stage == STAGE_TRANSPORT

    def test_endpoint_setup_failure_is_configuration(self):
        status, stage, _ = rd.classify_failure(
            "WMI protocol setup failed: ValueError", _wmi()
        )
        assert status == READINESS_CONFIGURATION_ERROR
        assert stage == STAGE_CONFIGURATION

    def test_transport_error_is_unreachable_and_mentions_wsman_not_dcom(self):
        status, stage, message = rd.classify_failure(
            "WMI authentication failed: WinRMTransportError", _wmi()
        )
        assert status == READINESS_UNREACHABLE
        assert stage == STAGE_TRANSPORT
        assert "5985/5986" in message
        assert "not legacy DCOM" in message

    def test_dns_failure(self):
        status, stage, _ = rd.classify_failure(
            "WMI protocol setup failed: gaierror", _wmi()
        )
        # 'protocol setup failed' is the earlier rule; either way it is NOT ready.
        assert status != READINESS_READY
        assert stage in (STAGE_CONFIGURATION, STAGE_NETWORK)

    def test_dns_failure_alone_is_network_stage(self):
        status, stage, _ = rd.classify_failure("gaierror: name resolution", _wmi())
        assert status == READINESS_UNREACHABLE
        assert stage == STAGE_NETWORK

    def test_wsman_fault_is_protocol(self):
        status, stage, _ = rd.classify_failure(
            "WSManFaultError: invalid namespace", _wmi()
        )
        assert status == READINESS_PROTOCOL_ERROR
        assert stage == STAGE_PROTOCOL

    def test_unknown_wmi_error_is_not_reported_as_ready(self):
        status, _, _ = rd.classify_failure("something odd", _wmi())
        assert status == READINESS_PROTOCOL_ERROR

    def test_wmi_failure_leaks_no_password(self):
        _, _, message = rd.classify_failure(
            f"WMI authentication failed: {WMI_PASSWORD}", _wmi()
        )
        assert WMI_PASSWORD not in message


class TestExceptionClassification:
    """Real exception objects classify by TYPE, which is reliable - and is the
    only path that can identify a TLS failure unambiguously."""

    def test_config_error(self):
        status, stage, _ = rd.classify_exception(
            CollectorConfigError("bad config"), _snmp()
        )
        assert status == READINESS_CONFIGURATION_ERROR
        assert stage == STAGE_CONFIGURATION

    def test_dns_error(self):
        status, stage, _ = rd.classify_exception(
            socket.gaierror(-2, "Name or service not known"), _snmp()
        )
        assert status == READINESS_UNREACHABLE
        assert stage == STAGE_NETWORK

    def test_tls_error_is_detectable_by_type(self):
        status, stage, message = rd.classify_exception(
            ssl.SSLError("certificate verify failed"), _wmi()
        )
        assert status == READINESS_PROTOCOL_ERROR
        assert stage == STAGE_TRANSPORT
        assert "TLS" in message

    def test_timeout_error(self):
        status, _, _ = rd.classify_exception(TimeoutError("timed out"), _snmp())
        assert status == READINESS_TIMEOUT

    def test_socket_timeout(self):
        status, _, _ = rd.classify_exception(socket.timeout("timed out"), _snmp())
        assert status == READINESS_TIMEOUT

    def test_connection_refused(self):
        status, _, _ = rd.classify_exception(
            ConnectionRefusedError("refused"), _snmp()
        )
        assert status == READINESS_UNREACHABLE

    def test_generic_oserror(self):
        status, _, _ = rd.classify_exception(OSError("no route"), _snmp())
        assert status == READINESS_UNREACHABLE

    def test_arbitrary_exception_falls_back_to_text_not_ready(self):
        class WeirdError(RuntimeError):
            pass

        status, stage, message = rd.classify_exception(
            WeirdError("SNMP connection failed: no response"), _snmp()
        )
        assert status != READINESS_READY
        assert status == READINESS_UNREACHABLE  # via the 'no response' rule
        # A matched rule returns its canned explanation, which deliberately does
        # not echo the raw (potentially credential-bearing) text.
        assert "wrong community" in message


# ---------------------------------------------------------------------------
# Service-level probe: drives the SAME _build_collector + authenticate() path
# production uses. _build_collector is replaced so no real host is contacted,
# but everything after it (config validation, classification, redaction) is real.
# ---------------------------------------------------------------------------

class _FakeCollector:
    def __init__(self, source, *, result=None, exc=None):
        self.source = source
        self.result = result
        self.exc = exc
        self.authenticate_calls = 0

    async def authenticate(self):
        self.authenticate_calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


def _service(db, monkeypatch, *, source, result=None, exc=None, build_exc=None):
    from app.repositories.monitoring_source_repository import (
        MonitoringSourceRepository,
    )
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.monitoring_service import MonitoringService

    service = MonitoringService(MonitoringSourceRepository(db), SensorRepository(db))
    fake = _FakeCollector(source, result=result, exc=exc)

    def _build(self, s):
        if build_exc is not None:
            raise build_exc
        return fake

    monkeypatch.setattr(MonitoringService, "_build_collector", _build)
    return service, fake


class TestServiceProbe:

    async def test_missing_hostname_never_touches_the_network(
        self, db, monkeypatch
    ):
        """Config validation must run BEFORE authenticate(): a missing hostname
        must not produce a connect attempt, nor be reported as 'unreachable'."""
        source = _snmp(hostname=None, ip_address=None)
        service, fake = _service(db, monkeypatch, source=source)

        result = await service.check_readiness(source)

        assert result.status == READINESS_CONFIGURATION_ERROR
        assert result.stage == STAGE_CONFIGURATION
        assert result.target == "not configured"
        assert result.protocol == "SNMP"
        assert "hostname is missing" in result.message
        assert fake.authenticate_calls == 0, "no network call may be attempted"

    async def test_successful_probe_is_ready_at_protocol_stage(
        self, db, monkeypatch
    ):
        source = _snmp()
        service, fake = _service(
            db, monkeypatch, source=source,
            result=ConnectionCheck(True, "SNMP reachable."),
        )

        result = await service.check_readiness(source)

        assert result.status == READINESS_READY
        assert result.is_ready
        # Protocol stage, NOT "collected": reachability != discovery.
        assert result.stage == STAGE_PROTOCOL
        assert result.target == "10.0.0.1:161"
        assert fake.authenticate_calls == 1

    async def test_failed_handshake_is_classified(self, db, monkeypatch):
        source = _snmp()
        service, _ = _service(
            db, monkeypatch, source=source,
            result=ConnectionCheck(
                False,
                "SNMP: no response to sysDescr GET (host unreachable or wrong "
                "community).",
            ),
        )
        result = await service.check_readiness(source)
        assert result.status == READINESS_UNREACHABLE
        assert result.stage == STAGE_TRANSPORT
        assert result.status != READINESS_READY

    async def test_raised_exception_is_classified_not_propagated(
        self, db, monkeypatch
    ):
        source = _snmp()
        service, _ = _service(
            db, monkeypatch, source=source,
            exc=socket.gaierror(-2, "Name or service not known"),
        )
        result = await service.check_readiness(source)
        assert result.status == READINESS_UNREACHABLE
        assert result.stage == STAGE_NETWORK

    async def test_unregistered_source_type_is_unsupported(self, db, monkeypatch):
        source = _snmp(source_type="nope-not-real")
        service, _ = _service(
            db, monkeypatch, source=source,
            build_exc=ValueError("No collector registered for source_type='nope'"),
        )
        result = await service.check_readiness(source)
        assert result.status == READINESS_UNSUPPORTED
        assert result.stage == STAGE_CONFIGURATION

    async def test_probe_never_exposes_the_community_string(self, db, monkeypatch):
        source = _snmp()
        service, _ = _service(
            db, monkeypatch, source=source,
            result=ConnectionCheck(False, f"SNMP: bad community {SNMP_SECRET}"),
        )
        result = await service.check_readiness(source)
        assert SNMP_SECRET not in _flat(result)

    async def test_wmi_probe_reports_wmi_protocol_label(self, db, monkeypatch):
        source = _wmi()
        service, _ = _service(
            db, monkeypatch, source=source,
            result=ConnectionCheck(True, "WMI connection established."),
        )
        result = await service.check_readiness(source)
        assert result.protocol == "WMI/WSMan"
        assert result.target == "10.0.0.5:5985"
        assert result.safe_details["auth_type"] == "ntlm"


class TestNotConfigured:
    """A protocol with no source is reported, never silently omitted."""

    def test_wmi_not_configured_result(self):
        result = rd.not_configured_result("wmi")
        assert result.status == READINESS_CONFIGURATION_ERROR
        assert result.stage == STAGE_CONFIGURATION
        assert result.target == "not configured"
        assert result.protocol == "WMI/WSMan"
        assert result.message == "No WMI discovery source is configured"
        assert result.safe_details["configured"] is False

    def test_snmp_not_configured_result(self):
        result = rd.not_configured_result("snmp")
        assert result.message == "No SNMP discovery source is configured"
        assert result.protocol == "SNMP"


# ---------------------------------------------------------------------------
# Persistence safety (mandatory acceptance criterion): a probe mutates NOTHING.
# ---------------------------------------------------------------------------

_COUNTS = """
    SELECT
      (SELECT count(*) FROM devices WHERE tenant_id = :t) AS devices,
      (SELECT count(*) FROM interfaces WHERE tenant_id = :t) AS interfaces,
      (SELECT count(*) FROM resource_relationships WHERE tenant_id = :t) AS rels,
      (SELECT count(*) FROM topology_links WHERE tenant_id = :t) AS links,
      (SELECT count(*) FROM device_capabilities c
         JOIN devices d ON d.id = c.device_id
        WHERE d.tenant_id = :t) AS caps
"""


def _monitoring_service(db):
    from app.repositories.monitoring_source_repository import (
        MonitoringSourceRepository,
    )
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.monitoring_service import MonitoringService

    return MonitoringService(MonitoringSourceRepository(db), SensorRepository(db))


class TestPersistenceSafety:

    async def test_probe_creates_no_inventory_or_topology(
        self, tenant, monitoring_source_factory, db
    ):
        """Runs a REAL probe (nothing monkeypatched) against a real source row
        and proves the row counts are untouched. This is what separates a
        diagnostic from a collection."""
        from sqlalchemy import text

        # No hostname: the probe short-circuits before any network I/O, which is
        # exactly the configured-but-unusable case that must be reported.
        source = await monitoring_source_factory(source_type="snmp", hostname=None)
        service = _monitoring_service(db)

        before = (await db.execute(text(_COUNTS), {"t": tenant.id})).one()
        result = await service.check_readiness(source)
        await db.flush()
        after = (await db.execute(text(_COUNTS), {"t": tenant.id})).one()

        assert result.status == READINESS_CONFIGURATION_ERROR
        assert before == after, "a readiness probe must not mutate any table"
        assert after.devices == 0
        assert after.interfaces == 0
        assert after.rels == 0
        assert after.links == 0
        assert after.caps == 0

    async def test_probe_writes_no_collector_run(
        self, tenant, monitoring_source_factory, db
    ):
        """A probe is not a collection: no CollectorRun row is created."""
        from sqlalchemy import text

        source = await monitoring_source_factory(source_type="snmp", hostname=None)
        service = _monitoring_service(db)

        async def _runs():
            return (await db.execute(
                text("SELECT count(*) FROM collector_runs WHERE tenant_id = :t"),
                {"t": tenant.id},
            )).scalar()

        before = await _runs()
        await service.check_readiness(source)
        await db.flush()
        after = await _runs()

        assert before == after
        assert after == 0

    async def test_readiness_report_covers_snmp_and_wmi(
        self, tenant, monitoring_source_factory, db
    ):
        """Coverage must name BOTH protocols, including one with no source."""
        source = await monitoring_source_factory(source_type="snmp", hostname=None)
        service = _monitoring_service(db)

        report = await service.readiness_report(tenant.id)

        assert report["sources_checked"] == 1
        by_type = {r.safe_details.get("source_type"): r for r in report["results"]}
        assert set(by_type) == {"snmp"}
        assert by_type["snmp"].status == READINESS_CONFIGURATION_ERROR

        coverage_types = {r.safe_details.get("source_type") for r in report["coverage"]}
        assert coverage_types == {"snmp", "wmi"}
        wmi = next(r for r in report["coverage"]
                   if r.safe_details.get("source_type") == "wmi")
        assert wmi.message == "No WMI discovery source is configured"
        assert wmi.safe_details["configured"] is False
