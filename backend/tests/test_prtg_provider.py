"""
PRTG action provider unit tests (Phase 2).

Pure unit tests: PRTG transport is monkeypatched at the
PrtgActionProvider._prtg_table / _mutation_request seams - no network, no
credentials on the wire, no fabricated monitoring data in production code.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.actions  # registers providers
from app.actions.base import (
    ActionNotSupported,
    ActionProviderRegistry,
    DeviceActionType,
)


def _sensor_row(objid="1234", status="Down", status_raw="5", paused=False):
    row = {"objid": objid, "sensor": "Ping", "status": status,
           "status_raw": status_raw, "lastvalue": "", "lastcheck": "now",
           "lastup": "", "lastdown": "1m", "device": "srv-01",
           "deviceid": "999", "group": "Office", "probe": "Local"}
    if paused:
        row["status"] = "Paused by user"
        row["status_raw"] = "7"
    return row


@pytest.fixture
def provider():
    from app.actions.providers.prtg import PrtgActionProvider
    return PrtgActionProvider()


@pytest.fixture
def target():
    from app.actions.base import ActionTarget
    import uuid as _uuid
    return ActionTarget(
        tenant_id=_uuid.uuid4(), device_id=_uuid.uuid4(), name="PRTG-linked host",
        ip_address="10.0.0.5",
        connection=SimpleNamespace(
            id=_uuid.uuid4(), tenant_id=_uuid.uuid4(), name="Dev PRTG",
            source_type="prtg", hostname="192.168.1.5", ip_address="192.168.1.5",
            port=None, protocol="https", snmp_version=None,
            username="prtg-admin", api_key=None, password="S3cretPasshash",
            config={}, verify_ssl=False,
        ),
    )


@pytest.fixture
def patch_prtg_table(monkeypatch):
    """Serve canned PRTG rows and record requested content/filters."""
    calls: list[tuple[str, dict]] = []
    served: dict[str, list[dict]] = {"sensors": [], "devices": []}

    async def fake_table(self, collector, content, extra=None):
        calls.append((content, dict(extra or {})))
        return served.get(content, [])

    monkeypatch.setattr(
        "app.actions.providers.prtg.PrtgActionProvider._prtg_table", fake_table
    )
    return SimpleNamespace(calls=calls, served=served)


@pytest.fixture
def patch_mutation(monkeypatch):
    """Record whitelisted mutation calls (secrets stripped from the record)."""
    calls: list[tuple[str, dict]] = []

    async def fake_mutation(self, collector, path, params):
        calls.append((path, {k: v for k, v in params.items()
                             if k not in ("username", "passhash")}))
        return {"envelope": "<html>ok</html>"}

    monkeypatch.setattr(
        "app.actions.providers.prtg.PrtgActionProvider._mutation_request", fake_mutation
    )
    return calls


class TestPrtgRegistration:
    def test_registered_in_registry(self):
        cls = ActionProviderRegistry.try_get("vendor:prtg")
        assert cls is not None and cls.implemented

    def test_supported_actions(self, provider):
        for action in (
            DeviceActionType.PRTG_QUERY_SENSOR,
            DeviceActionType.PRTG_QUERY_DEVICE,
            DeviceActionType.PRTG_PAUSE_SENSOR,
            DeviceActionType.PRTG_RESUME_SENSOR,
        ):
            assert provider.supports(action)
        assert not provider.supports(DeviceActionType.PING)


class TestPrtgReads:
    async def test_query_sensor_returns_normalized_row(self, provider, target, patch_prtg_table):
        patch_prtg_table.served["sensors"] = [_sensor_row()]
        result = await provider.execute(DeviceActionType.PRTG_QUERY_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert result.success
        assert result.output["sensor_id"] == "1234"
        assert result.output["status"] == "Down"
        assert result.output["device_id"] == "999"
        assert result.output["paused"] is False
        content, extra = patch_prtg_table.calls[-1]
        assert content == "sensors" and extra["filter_objid"] == "1234"

    async def test_query_unknown_sensor_is_honest_failure(self, provider, target, patch_prtg_table):
        patch_prtg_table.served["sensors"] = []
        result = await provider.execute(DeviceActionType.PRTG_QUERY_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert not result.success and "no usable response" in result.error

    async def test_query_device_resolves_via_sensor(self, provider, target, patch_prtg_table):
        patch_prtg_table.served["sensors"] = [_sensor_row()]
        patch_prtg_table.served["devices"] = [{
            "objid": "999", "device": "srv-01", "host": "10.0.0.5",
            "status": "Up", "status_raw": "3", "group": "Office", "probe": "Local",
            "totalsens": "10", "upsens": "9", "downsens": "1", "pausedsens": "0",
            "warnsens": "0", "unusualsens": "0", "undefinedsens": "0",
        }]
        result = await provider.execute(DeviceActionType.PRTG_QUERY_DEVICE, target,
                                        {"sensor_id": "1234"})
        assert result.success
        assert result.output["device_id"] == "999"
        assert result.output["sensors_down"] == "1"

    async def test_query_device_without_targeting_fails(self, provider, target, patch_prtg_table):
        result = await provider.execute(DeviceActionType.PRTG_QUERY_DEVICE, target, {})
        assert not result.success

    async def test_non_numeric_sensor_id_rejected(self, provider, target, patch_prtg_table):
        for evil in ("1234; rm -rf /", "../../admin", "1 OR 1=1", ""):
            result = await provider.execute(DeviceActionType.PRTG_QUERY_SENSOR,
                                            target, {"sensor_id": evil})
            assert not result.success


class TestPrtgPauseResume:
    async def test_pause_already_paused_is_idempotent(self, provider, target,
                                                      patch_prtg_table, patch_mutation):
        patch_prtg_table.served["sensors"] = [_sensor_row(paused=True)]
        result = await provider.execute(DeviceActionType.PRTG_PAUSE_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert result.success
        assert result.output["state"] == "already_in_desired_state"
        assert patch_mutation == []  # no mutation issued

    async def test_resume_already_running_is_idempotent(self, provider, target,
                                                        patch_prtg_table, patch_mutation):
        patch_prtg_table.served["sensors"] = [_sensor_row()]
        result = await provider.execute(DeviceActionType.PRTG_RESUME_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert result.success
        assert result.output["state"] == "already_in_desired_state"
        assert patch_mutation == []

    async def test_pause_issues_whitelisted_call(self, provider, target,
                                                 patch_prtg_table, patch_mutation):
        patch_prtg_table.served["sensors"] = [_sensor_row()]
        result = await provider.execute(DeviceActionType.PRTG_PAUSE_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert result.success and result.output["state"] == "pause_requested"
        path, params = patch_mutation[0]
        assert path == "/api/pause.htm"
        assert params["id"] == "1234" and params["pausenow"] == 1

    async def test_resume_issues_whitelisted_call(self, provider, target,
                                                  patch_prtg_table, patch_mutation):
        patch_prtg_table.served["sensors"] = [_sensor_row(paused=True)]
        result = await provider.execute(DeviceActionType.PRTG_RESUME_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert result.success and result.output["state"] == "resume_requested"
        path, params = patch_mutation[0]
        assert path == "/api/resume.htm" and params["id"] == "1234"

    async def test_pause_reason_whitelisted(self, provider, target,
                                            patch_prtg_table, patch_mutation):
        patch_prtg_table.served["sensors"] = [_sensor_row()]
        await provider.execute(DeviceActionType.PRTG_PAUSE_SENSOR, target,
                               {"sensor_id": "1234", "reason": "security"})
        _, params = patch_mutation[0]
        assert "security" in params["msg"]

    async def test_pause_reason_not_in_whitelist_defaults(self, provider, target,
                                                          patch_prtg_table, patch_mutation):
        patch_prtg_table.served["sensors"] = [_sensor_row()]
        await provider.execute(DeviceActionType.PRTG_PAUSE_SENSOR, target,
                               {"sensor_id": "1234", "reason": "because I said so"})
        _, params = patch_mutation[0]
        assert "maintenance" in params["msg"]  # falls back, never free-text


class TestPrtgFailures:
    async def test_mutation_http_error_is_failure(self, provider, target,
                                                  patch_prtg_table, monkeypatch):
        patch_prtg_table.served["sensors"] = [_sensor_row()]

        async def fake_mutation(self, collector, path, params):
            raise RuntimeError("PRTG returned 500")

        monkeypatch.setattr(
            "app.actions.providers.prtg.PrtgActionProvider._mutation_request", fake_mutation
        )
        result = await provider.execute(DeviceActionType.PRTG_PAUSE_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert not result.success

    async def test_mutation_error_envelope_is_failure(self, provider, target,
                                                      patch_prtg_table, monkeypatch):
        patch_prtg_table.served["sensors"] = [_sensor_row()]

        async def fake_mutation(self, collector, path, params):
            return {"_failed": True, "error": "permission denied by PRTG"}

        monkeypatch.setattr(
            "app.actions.providers.prtg.PrtgActionProvider._mutation_request", fake_mutation
        )
        result = await provider.execute(DeviceActionType.PRTG_PAUSE_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert not result.success

    async def test_malformed_table_payload_is_rejected(self, provider, target, monkeypatch):
        async def fake_table(self, collector, content, extra=None):
            raise ValueError("PRTG table 'sensors' returned a malformed payload.")

        monkeypatch.setattr(
            "app.actions.providers.prtg.PrtgActionProvider._prtg_table", fake_table
        )
        result = await provider.execute(DeviceActionType.PRTG_QUERY_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert not result.success and "malformed" in result.error

    async def test_timeout_is_failure_not_crash(self, provider, target, monkeypatch):
        async def boom(self, *a, **k):
            raise TimeoutError("PRTG API timeout")
        monkeypatch.setattr(
            "app.actions.providers.prtg.PrtgActionProvider._prtg_table", boom
        )
        result = await provider.execute(DeviceActionType.PRTG_QUERY_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert not result.success

    async def test_authentication_failure_is_failure(self, provider, target, monkeypatch):
        async def boom(self, *a, **k):
            raise PermissionError("401 unauthorized")
        monkeypatch.setattr(
            "app.actions.providers.prtg.PrtgActionProvider._prtg_table", boom
        )
        result = await provider.execute(DeviceActionType.PRTG_QUERY_SENSOR, target,
                                        {"sensor_id": "1234"})
        assert not result.success

    async def test_no_connection_is_honest_unavailable(self, provider, target):
        target.connection = None
        with pytest.raises(ActionNotSupported):
            await provider.execute(DeviceActionType.PRTG_QUERY_SENSOR, target,
                                   {"sensor_id": "1234"})


class TestPrtgSecurity:
    def test_safe_error_redacts_credential_words(self):
        from app.actions.providers.prtg import PrtgActionProvider
        text = PrtgActionProvider._safe_error(
            RuntimeError("passhash=abc123 username=prtg-admin token=xyz failed")
        )
        assert "passhash=" not in text and "username=" not in text
        assert "[redacted:" in text

    async def test_result_never_contains_connection_secret(self, provider, target,
                                                           patch_prtg_table):
        patch_prtg_table.served["sensors"] = [_sensor_row()]
        result = await provider.execute(DeviceActionType.PRTG_QUERY_SENSOR, target,
                                        {"sensor_id": "1234"})
        blob = json.dumps(result.output)
        assert "S3cretPasshash" not in blob and "prtg-admin" not in blob

    async def test_mutation_record_never_carries_secrets(self, provider, target,
                                                         patch_prtg_table, patch_mutation):
        patch_prtg_table.served["sensors"] = [_sensor_row()]
        await provider.execute(DeviceActionType.PRTG_PAUSE_SENSOR, target,
                               {"sensor_id": "1234"})
        blob = json.dumps(patch_mutation)
        assert "S3cretPasshash" not in blob and "prtg-admin" not in blob