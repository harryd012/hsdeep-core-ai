"""
PRTG action provider (Phase 2) - production PRTG Network Monitor adapter.

REUSE, not duplication: PRTG transport (username+passhash auth params,
base URL construction, retry/backoff on transient failures, strict
no-passhash logging) is the PRTGCollector's proven implementation
(app/collectors/prtg.py). This provider drives that collector's transport
methods against a tenant PRTG source so there is exactly ONE PRTG HTTP
implementation in the codebase.

Action surface (Phase 2):
  READ_ONLY (auto-executable):
    PRTG_QUERY_SENSOR, PRTG_QUERY_DEVICE - live sensor/device readings
      via /api/table.json (the same table API the collector uses).
  CHANGE_REQUIRING_APPROVAL (human decision required):
    PRTG_PAUSE_SENSOR, PRTG_RESUME_SENSOR - /api/pause.htm and
      /api/resume.htm with pausenow/forever semantics.

Targeting contract (Phase 2 bridge to DeviceActionService):
  * PRTG actions target a Sensor row (monitoring_source_id + external_id
    are the PRTG source id and the PRTG numeric object id). The HSDEEP
    Device and Sensor rows carry the identity; the provider NEVER
    fabricates object ids and NEVER accepts an arbitrary URL - only the
    transport prefix + whitelisted paths below.
  * Credentials come ONLY from the decrypted MonitoringSourceConnectionInfo
    (username + passhash; never plaintext), promoted to `username`
    connection fields by the capability resolver. Passhash values never
    appear in results, errors, logs, or audit payloads.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.actions.base import (
    ActionNotSupported,
    ActionResult,
    ActionTarget,
    DeviceActionProvider,
    DeviceActionType,
    ActionProviderRegistry,
)
from app.collectors.prtg import PRTGCollector

logger = logging.getLogger(__name__)

# Whitelisted PRTG transport prefix and pause/resume actions. The provider
# builds URLs exclusively as <base_url> + one of these paths - an LLM or
# workflow step can NEVER supply an arbitrary URL.
_PRTG_TABLE_PATH = "/api/table.json"
_PRTG_PAUSE_PATH = "/api/pause.htm"
_PRTG_RESUME_PATH = "/api/resume.htm"

_SENSORS_CONTENT = "sensors"
_DEVICES_CONTENT = "devices"

_READ_COLUMNS = {
    _SENSORS_CONTENT: "objid,sensor,status,status_raw,lastvalue,lastcheck,lastup,lastdown,device,deviceid,group,probe",
    _DEVICES_CONTENT: "objid,device,host,status,status_raw,group,probe,totalsens,upsens,downsens,pausedsens,warnsens,unusualsens,undefinedsens",
}

_PAUSE_ID_REASONS = {"maintenance", "security", "investigation", "change"}

# Table queries reuse the collector's longer budget; the mutation calls
# below carry their own (fail fast on a hung control plane).
_MUTATION_TIMEOUT = 10.0

_TABLE_TIMEOUT = 20.0


def _is_paused(sensor_row: dict[str, Any]) -> bool:
    text_blob = " ".join(
        str(sensor_row.get(key, "")) for key in ("status", "status_raw", "message")
    ).lower()
    return "paused" in text_blob


def _pause_reason(params: dict[str, Any] | None) -> str:
    raw = str((params or {}).get("reason", "maintenance")).strip().lower()
    return raw if raw in _PAUSE_ID_REASONS else "maintenance"

_SENSORS_CONTENT = "sensors"
_DEVICES_CONTENT = "devices"


@ActionProviderRegistry.register("vendor:prtg")
class PrtgActionProvider(DeviceActionProvider):
    display_name = "PRTG Network Monitor"
    implemented = True

    def supported_actions(self) -> frozenset[DeviceActionType]:
        return frozenset({
            DeviceActionType.PRTG_QUERY_SENSOR,
            DeviceActionType.PRTG_QUERY_DEVICE,
            DeviceActionType.PRTG_PAUSE_SENSOR,
            DeviceActionType.PRTG_RESUME_SENSOR,
        })

    # -- targeting ----------------------------------------------------------

    @staticmethod
    def _sensor_id(params: dict[str, Any] | None) -> str:
        """The PRTG numeric sensor id comes ONLY from validated params that
        the service layer resolved from a Sensor row (external_id). Never
        from free text."""
        raw = (params or {}).get("sensor_id")
        sensor_id = str(raw or "").strip()
        if not sensor_id.isdigit():
            # Invalid parameters are a graceful failure (Phase 11), not an
            # "unsupported action" - the caller sees success=False.
            raise ValueError(
                "PRTG actions require a numeric sensor_id resolved from a "
                "Sensor row (external_id); free-text targets are rejected."
            )
        return sensor_id

    def _collector(self, target: ActionTarget) -> PRTGCollector:
        """The tenant's linked PRTG source supplies connection info (already
        decrypted by the capability resolver)."""
        if target.connection is None:
            raise ActionNotSupported(
                "Target has no linked PRTG monitoring source with credentials."
            )
        return PRTGCollector(source=target.connection)

    # -- DeviceActionProvider contract --------------------------------------

    async def execute(
        self,
        action: DeviceActionType,
        target: ActionTarget,
        params: dict[str, Any] | None = None,
    ) -> ActionResult:
        if not self.supports(action):
            raise ActionNotSupported(
                f"PRTG provider does not support action={action.value}."
            )
        started = datetime.now(timezone.utc)
        try:
            collector = self._collector(target)
            if action == DeviceActionType.PRTG_QUERY_SENSOR:
                output = await self._query_sensor(collector, params)
            elif action == DeviceActionType.PRTG_QUERY_DEVICE:
                output = await self._query_device(collector, params)
            elif action == DeviceActionType.PRTG_PAUSE_SENSOR:
                output = await self._pause_sensor(collector, params)
            else:
                output = await self._resume_sensor(collector, params)
        except ActionNotSupported:
            raise
        except Exception as exc:  # noqa: BLE001 - device-level failure
            logger.warning("PRTG action failed target=%s err=%s", target.device_id, type(exc).__name__)
            return ActionResult(
                success=False,
                error=self._safe_error(exc),
                provider=self.provider_kind,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

        ok = bool(output) and not output.get("_failed")
        return ActionResult(
            success=ok,
            output={k: v for k, v in output.items() if not k.startswith("_")},
            error=None if ok else "PRTG: no usable response (check sensor/object id and source connectivity).",
            provider=self.provider_kind,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )

    # -- read operations (Phase 2 read path) --------------------------------

    async def _prtg_table(
        self, collector: PRTGCollector, content: str, extra: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """One /api/table.json list, reusing the collector's auth/retry/no-log
        transport helper exactly as the collection path calls it."""
        params = {"content": content, "columns": _READ_COLUMNS.get(content, ""), "count": 5000}
        if extra:
            params.update(extra)
        params.update(collector._auth_params())  # noqa: SLF001
        resp = await collector._request(_PRTG_TABLE_PATH, params, timeout=_TABLE_TIMEOUT)  # noqa: SLF001
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError(f"PRTG table '{content}' returned a malformed payload.")
        rows = payload.get(content)
        if not isinstance(rows, list):
            raise ValueError(f"PRTG table '{content}' missing its row list.")
        return rows

    async def _query_sensor(self, collector: PRTGCollector, params: dict[str, Any] | None) -> dict[str, Any]:
        """Live sensor row(s) for the numeric sensor_id carried in params."""
        sensor_id = self._sensor_id(params)
        rows = await self._prtg_table(
            collector, _SENSORS_CONTENT,
            {"filter_objid": sensor_id, "output": "json"},
        )
        match = next((r for r in rows if str(r.get("objid", "")) == sensor_id), None)
        if match is None:
            return {}
        return {
            "sensor_id": sensor_id,
            "sensor": match.get("sensor"),
            "status": match.get("status"),
            "status_raw": match.get("status_raw"),
            "lastvalue": match.get("lastvalue"),
            "lastcheck": match.get("lastcheck"),
            "lastup": match.get("lastup"),
            "lastdown": match.get("lastdown"),
            "device": match.get("device"),
            "device_id": str(match.get("deviceid", "")),
            "group": match.get("group"),
            "probe": match.get("probe"),
            "paused": _is_paused(match),
        }

    async def _query_device(self, collector: PRTGCollector, params: dict[str, Any] | None) -> dict[str, Any]:
        """Live device row - either by explicit device_id or resolved from
        the sensor row when only sensor_id was supplied."""
        device_id = str((params or {}).get("device_id", "")).strip()
        device_name_hint = str((params or {}).get("device", "")).strip()
        rows = await self._prtg_table(collector, _DEVICES_CONTENT, {"output": "json"})
        match: dict[str, Any] | None = None
        if device_id.isdigit():
            match = next((r for r in rows if str(r.get("objid", "")) == device_id), None)
        elif device_name_hint:
            lowered = device_name_hint.lower()
            match = next((r for r in rows if str(r.get("device", "")).lower() == lowered), None)
        else:
            sensor_id = self._sensor_id(params)
            sensor = await self._query_sensor(collector, {"sensor_id": sensor_id})
            if sensor.get("device_id", "").isdigit():
                match = next(
                    (r for r in rows if str(r.get("objid", "")) == sensor["device_id"]), None
                )
        if match is None:
            return {}
        return {
            "device_id": str(match.get("objid", "")),
            "device": match.get("device"),
            "host": match.get("host"),
            "status": match.get("status"),
            "status_raw": match.get("status_raw"),
            "group": match.get("group"),
            "probe": match.get("probe"),
            "sensors_total": match.get("totalsens"),
            "sensors_up": match.get("upsens"),
            "sensors_down": match.get("downsens"),
            "sensors_paused": match.get("pausedsens"),
            "sensors_warning": match.get("warnsens"),
            "sensors_unusual": match.get("unusualsens"),
            "sensors_unknown": match.get("undefinedsens"),
        }

    # -- state-changing operations (Phase 4 pause/resume) -------------------

    async def _pause_sensor(self, collector: PRTGCollector, params: dict[str, Any] | None) -> dict[str, Any]:
        """Pause a sensor via /api/pause.htm?id=<objid>&pausenow=1 (indefinite).

        IDEMPOTENT: the current sensor row is read first - a sensor that is
        already paused resolves to `already_in_desired_state` instead of
        re-issuing the operation.
        """
        sensor_id = self._sensor_id(params)
        current = await self._query_sensor(collector, {"sensor_id": sensor_id})
        if not current:
            return {}
        if current.get("paused"):
            return {"sensor_id": sensor_id, "state": "already_in_desired_state",
                    "paused": True, "status": current.get("status")}
        failure = await self._apply_mutation(
            collector, _PRTG_PAUSE_PATH,
            {"id": sensor_id, "pausenow": 1, "action": 0,
             "msg": f"paused by HSDEEP ({_pause_reason(params)})"},
        )
        if failure:
            return failure
        return {"sensor_id": sensor_id, "state": "pause_requested",
                "previous_status": current.get("status")}

    async def _resume_sensor(self, collector: PRTGCollector, params: dict[str, Any] | None) -> dict[str, Any]:
        """Resume a sensor via /api/resume.htm?id=<objid>.
        IDEMPOTENT: an already-running sensor resolves to
        `already_in_desired_state`."""
        sensor_id = self._sensor_id(params)
        current = await self._query_sensor(collector, {"sensor_id": sensor_id})
        if not current:
            return {}
        if not current.get("paused"):
            return {"sensor_id": sensor_id, "state": "already_in_desired_state",
                    "paused": False, "status": current.get("status")}
        failure = await self._apply_mutation(collector, _PRTG_RESUME_PATH, {"id": sensor_id})
        if failure:
            return failure
        return {"sensor_id": sensor_id, "state": "resume_requested",
                "previous_status": current.get("status")}

    async def _apply_mutation(
        self, collector: PRTGCollector, path: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Run a mutation; return a failure-marker dict when PRTG rejected
        it (HTTP error / error envelope), else None on success. Callers
        merge the marker into their output so execute() reports failure."""
        outcome = await self._mutation_request(collector, path, params)
        if outcome is None or outcome.get("_failed"):
            failure: dict[str, Any] = {"_failed": True}
            if outcome:
                failure.update({k: v for k, v in outcome.items() if k != "_failed"})
            return failure
        return None

    async def _mutation_request(
        self, collector: PRTGCollector, path: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """A state-changing PRTG call on a whitelisted path only. PRTG answers
        pause/resume with an HTML/JSON envelope - success=true requires a
        semantically valid response (never HTTP 200 alone)."""
        if path not in (_PRTG_PAUSE_PATH, _PRTG_RESUME_PATH):
            raise ActionNotSupported(f"PRTG mutation path {path!r} is not whitelisted.")
        call_params = dict(params)
        call_params.update(collector._auth_params())  # noqa: SLF001
        resp = await collector._request(path, call_params, timeout=_MUTATION_TIMEOUT)  # noqa: SLF001
        if resp.status_code >= 400:
            return {"_failed": True, "status_code": resp.status_code}
        text = (resp.text or "").strip()
        lowered = text.lower()
        if lowered.startswith("{") or lowered.startswith("["):
            try:
                envelope = json.loads(text)
            except ValueError:
                return {"_failed": True, "status_code": resp.status_code}
            if isinstance(envelope, dict) and envelope.get("error"):
                return {"_failed": True, "error": str(envelope.get("error"))[:200]}
            return {"envelope": envelope}
        # PRTG mutation pages return a short HTML/text confirmation.
        return {"envelope": text[:500]}

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """Device-level error text with any credential-bearing fragments
        redacted (passhash/username/token/header/query secrets). Never room
        for a secret in an error, a log line, or an audit payload."""
        text = f"{type(exc).__name__}: {exc}"
        for secret in ("passhash", "username", "token", "password", "authorization"):
            text = text.replace(secret, f"[redacted:{secret}]")
        return text[:500]