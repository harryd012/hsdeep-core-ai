"""
PRTG Network Monitor collector.

Collector Framework migration (task 10.1) of the existing `PRTGAdapter`
(`app/services/monitoring/adapters/prtg.py`). Uses PRTG's HTTP API
(`/api/table.json`, `/api/status.json`, `/api/historicdata.json`), documented
at https://www.paessler.com/manuals/prtg/live_multiple_objects_status_reading .

Connection details come from `MonitoringSourceConnectionInfo`:

    hostname/port/protocol -> compose the PRTG base URL
    username                -> PRTG username
    password                -> PRTG **passhash** (not the plaintext password -
                                this is what PRTG's own docs recommend for API
                                integrations, same convention as the old
                                adapter's `config["passhash"]`)
    verify_ssl               -> TLS verification for the underlying
                                `httpx.AsyncClient` (defaults to False so a
                                dev server's self-signed cert works)

Single-file collector, matching every other vendor under `app/collectors/`
(no package layout). `authenticate()`/`collect()`/`normalize()` satisfy
`BaseCollector`'s abstract methods; `save()` is not overridden here - it
relies on `BaseCollector`'s default, which delegates to `BundleSaver`
(introduced separately in task 13.1).

Logging discipline: every log line goes through `logging.getLogger(__name__)`
and includes `source_id`/the request path/the outcome, but NEVER the
`username`, `passhash`, or full query-string params (which carry the
passhash) - matching the no-log rule already enforced for `SecretCrypto`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.collectors.base import (
    BaseCollector,
    CollectorConfigError,
    CollectorRegistry,
    ConnectionCheck,
    NormalizedAlertSignal,
    NormalizedBundle,
    NormalizedDevice,
    NormalizedMetric,
    NormalizedSensorReading,
)

logger = logging.getLogger(__name__)

# PRTG sensor status codes -> normalized status.
#
# Source of truth: Paessler's PRTG "filter_status table" (Sensor States), which
# defines the numeric codes PRTG returns for a sensor:
#
#     1  Unknown              2  Collecting           3  Up
#     4  Warning              5  Down                 6  NoProbe
#     7  PausedByUser         8  PausedByDependency   9  PausedBySchedule
#     10 Unusual              11 PausedByLicense      12 PausedUntil
#     13 DownAcknowledged     14 DownPartial
#
# This platform stores exactly four values on `Sensor.status` - up | down |
# warning | unknown (see `app/models/sensor.py`) - so the mapping is:
#
#   * 4 (Warning) must NOT be "down": that escalates every warning into an
#     outage.
#   * 5 (Down) must NOT be "warning": that SILENCES a real outage.
#   * 7/8/9/11/12 are deliberately PAUSED sensors - alerting on them is
#     wrong, so they resolve to "unknown" (visible, not alarming).
#   * 13/14 are genuine down states and must be "down", not unknown.
#
# The previous values here (3:up, 4:down, 5:warning, 7:warning, 10:warning)
# had warning and down inverted and treated a paused sensor as a warning.
_PRTG_STATUS_MAP = {
    1: "unknown",   # Unknown
    2: "unknown",   # Collecting
    3: "up",        # Up
    4: "warning",   # Warning
    5: "down",      # Down
    6: "unknown",   # NoProbe
    7: "unknown",   # Paused by user
    8: "unknown",   # Paused by dependency
    9: "unknown",   # Paused by schedule
    10: "warning",  # Unusual
    11: "unknown",  # Paused by license
    12: "unknown",  # Paused until
    13: "down",     # Down (Acknowledged)
    14: "down",     # Down (Partial)
}

# Text spellings of the same states, matched by substring so "Down
# (Acknowledged)" and "Down Partial" resolve to "down" - the states that the
# numeric codes 13/14 describe. Order matters: "down" is tested before "up"
# so "Down (Acknowledged)" cannot fall through to the "up" needle.
_PRTG_STATUS_TEXT = (
    ("down", "down"),
    ("warning", "warning"),
    ("unusual", "warning"),
    ("up", "up"),
)

# Message/log rows use PRTG's *log status values*, which are a DIFFERENT
# numeric scale from the sensor codes above. Paessler documents the log status
# values as the ones valid for table-based API calls (log.htm/sensors.htm via
# /api/table.xml) - which is exactly what `content=messages` is. On that scale
# the alarm states are:
#
#     608 Down          609 Warning      610 Unusual
#     619 Down (Acknowledged)           620 Down (Partial)
#
# (For reference 607 is "Up" and 611 is "OK" on that scale.) The previous
# value, "4,5,7,10", is the SENSOR scale for Warning/Down/PausedByUser/
# Unusual - on the log scale those ids mean "Probe Denied (IP)", "Probe Denied
# (Key)", "Web Server" and "Data", i.e. system noise rather than active alarms.
_ACTIVE_MESSAGE_STATUS_CODES = "608,609,610,619,620"

_STATUS_TIMEOUT = 10.0   # seconds, for the lightweight status.json check
_TABLE_TIMEOUT = 20.0    # seconds, for table/historic data calls
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0
_CHANNEL_FETCH_CONCURRENCY = 5  # bounds concurrent per-sensor/device sub-calls


@CollectorRegistry.register("prtg")
class PRTGCollector(BaseCollector):
    display_name = "PRTG Network Monitor"
    implemented = True
    category = "monitoring"

    # -- config / URL helpers -------------------------------------------------

    def _require_config(self) -> None:
        missing = [
            key
            for key, value in (
                ("hostname", self.source.hostname),
                ("username", self.source.username),
                ("password", self.source.password),
            )
            if not value
        ]
        if missing:
            raise CollectorConfigError(f"PRTG config missing required field(s): {missing}")

    def _base_url(self) -> str:
        protocol = self.source.protocol or "https"
        hostname = self.source.hostname
        port = self.source.port
        default_port = 443 if protocol == "https" else 80
        if port and port != default_port:
            return f"{protocol}://{hostname}:{port}"
        return f"{protocol}://{hostname}"

    def _auth_params(self) -> dict[str, str]:
        return {"username": self.source.username, "passhash": self.source.password}

    # -- shared HTTP helper -----------------------------------------------------

    async def _request(self, path: str, params: dict[str, Any], timeout: float) -> httpx.Response:
        """GET `path` with `params`, retrying transient failures up to
        `_MAX_ATTEMPTS` times. Never logs `params` (it carries the passhash) -
        only the path, source id, attempt count, and outcome/status code."""
        url = f"{self._base_url()}{path}"
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(verify=self.source.verify_ssl, timeout=timeout) as client:
                    response = await client.get(url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "PRTG request failed: source_id=%s path=%s attempt=%s/%s error_type=%s",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, type(exc).__name__,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise
            else:
                if response.status_code >= 500 and attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "PRTG request returned server error: source_id=%s path=%s attempt=%s/%s status=%s",
                        self.source.id, path, attempt, _MAX_ATTEMPTS, response.status_code,
                    )
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                logger.debug(
                    "PRTG request completed: source_id=%s path=%s attempt=%s/%s status=%s",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, response.status_code,
                )
                return response

        if last_exc is not None:
            raise last_exc
        assert response is not None  # pragma: no cover - defensive, unreachable
        return response

    async def _best_effort_table(self, content: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch a `/api/table.json` list, logging and returning `[]` on any
        failure rather than propagating - used for the "nice to have" API
        areas (groups, channels, historic data, messages)."""
        try:
            resp = await self._request("/api/table.json", params, timeout=_TABLE_TIMEOUT)
            resp.raise_for_status()
            return resp.json().get(content, [])
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.warning(
                "PRTG best-effort '%s' table failed: source_id=%s error_type=%s",
                content, self.source.id, type(exc).__name__,
            )
            return []

    # -- BaseCollector interface -------------------------------------------------

    async def authenticate(self) -> ConnectionCheck:
        self._require_config()
        try:
            resp = await self._request("/api/status.json", self._auth_params(), timeout=_STATUS_TIMEOUT)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return ConnectionCheck(success=False, message=f"Could not reach PRTG: {exc}")
        if resp.status_code == 200:
            return ConnectionCheck(success=True, message="Connected to PRTG core server.")
        return ConnectionCheck(success=False, message=f"PRTG responded with HTTP {resp.status_code}.")

    async def collect(self) -> dict[str, Any]:
        self._require_config()
        params = self._auth_params()
        result: dict[str, Any] = {}

        # Version info - also reused by authenticate(); best-effort here since
        # a failure shouldn't sink the whole run when auth already succeeded.
        try:
            status_resp = await self._request("/api/status.json", params, timeout=_STATUS_TIMEOUT)
            result["status"] = status_resp.json() if status_resp.status_code == 200 else {}
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            logger.warning("PRTG collect(): status.json failed: source_id=%s error_type=%s", self.source.id, type(exc).__name__)
            result["status"] = {}

        # Groups - best-effort, used only to resolve device site_hint.
        result["groups"] = await self._best_effort_table(
            "groups",
            {"content": "groups", "columns": "objid,name,parentid", "output": "json", **params},
        )

        # Devices and sensors are the core payload - propagate failures so
        # CollectionOrchestrator can record a real error instead of a
        # misleadingly "successful" run with no data.
        devices_resp = await self._request(
            "/api/table.json",
            {"content": "devices", "columns": "objid,name,host,status,parentid,group", "output": "json", **params},
            timeout=_TABLE_TIMEOUT,
        )
        devices_resp.raise_for_status()
        result["devices"] = devices_resp.json().get("devices", [])

        sensors_resp = await self._request(
            "/api/table.json",
            {
                "content": "sensors",
                "columns": "objid,name,status,status_raw,lastvalue_,device,parentid",
                "output": "json",
                **params,
            },
            timeout=_TABLE_TIMEOUT,
        )
        sensors_resp.raise_for_status()
        result["sensors"] = sensors_resp.json().get("sensors", [])

        # Sensor details/channels - best-effort per sensor, bounded concurrency.
        result["channels"] = await self._collect_channels(result["sensors"], params)

        # Historic data - best-effort per SENSOR (PRTG's historicdata.json
        # API requires a sensor id, not a device id - passing a device id
        # returns an HTTP error for every call). `MonitoringSourceConnectionInfo`
        # has no `last_sync_at`, so this is simplified to a fixed trailing
        # 1-hour window rather than "since the last successful run".
        result["historic"] = await self._collect_historic(result["sensors"], params)

        # Active alarms/messages - best-effort, filtered to non-"up" status codes.
        result["messages"] = await self._best_effort_table(
            "messages",
            {
                "content": "messages",
                "columns": "objid,name,type,datetime,status,message,parent",
                "filter_status": _ACTIVE_MESSAGE_STATUS_CODES,
                "output": "json",
                **params,
            },
        )

        return result

    async def _collect_channels(self, sensors: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        channels: dict[str, list[dict[str, Any]]] = {}
        semaphore = asyncio.Semaphore(_CHANNEL_FETCH_CONCURRENCY)

        async def _fetch(sensor_id: str) -> None:
            async with semaphore:
                rows = await self._best_effort_table(
                    "channels",
                    {"content": "channels", "id": sensor_id, "columns": "name,lastvalue_,unit", "output": "json", **params},
                )
                channels[sensor_id] = rows

        sensor_ids = [str(row["objid"]) for row in sensors if row.get("objid") is not None]
        await asyncio.gather(*(_fetch(sid) for sid in sensor_ids))
        return channels

    async def _collect_historic(self, sensors: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """Fetch historic data points per SENSOR - PRTG's `historicdata.json`
        endpoint is documented to accept a sensor (or group/device, but
        sensor is the reliable, universally-supported case) object id;
        passing a plain device id returns an HTTP error for most PRTG
        installations, which is why this is keyed on sensor ids."""
        historic: dict[str, list[dict[str, Any]]] = {}
        semaphore = asyncio.Semaphore(_CHANNEL_FETCH_CONCURRENCY)

        now = datetime.now(timezone.utc)
        sdate = (now - timedelta(hours=1)).strftime("%Y-%m-%d-%H-%M-%S")
        edate = now.strftime("%Y-%m-%d-%H-%M-%S")

        async def _fetch(sensor_id: str) -> None:
            async with semaphore:
                try:
                    resp = await self._request(
                        "/api/historicdata.json",
                        {"id": sensor_id, "sdate": sdate, "edate": edate, "output": "json", **params},
                        timeout=_TABLE_TIMEOUT,
                    )
                    resp.raise_for_status()
                    historic[sensor_id] = resp.json().get("histdata", [])
                except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                    logger.warning(
                        "PRTG historic data fetch failed: source_id=%s sensor_id=%s error_type=%s",
                        self.source.id, sensor_id, type(exc).__name__,
                    )
                    historic[sensor_id] = []

        sensor_ids = [str(row["objid"]) for row in sensors if row.get("objid") is not None]
        await asyncio.gather(*(_fetch(sid) for sid in sensor_ids))
        return historic

    def normalize(self, raw: dict[str, Any]) -> NormalizedBundle:
        now_ts = datetime.now(timezone.utc)
        groups_by_id = {str(g["objid"]): g for g in raw.get("groups", []) if g.get("objid") is not None}

        devices = [
            self._normalize_device(row, groups_by_id)
            for row in raw.get("devices", [])
            if row.get("objid") is not None
        ]
        sensors = [
            self._normalize_sensor(row)
            for row in raw.get("sensors", [])
            if row.get("objid") is not None
        ]
        metrics = self._normalize_channel_metrics(raw.get("channels", {}), now_ts)
        metrics.extend(self._normalize_historic_metrics(raw.get("historic", {}), now_ts))

        # BUG FIX (Alert Correlation Engine sprint): PRTG's messages.json
        # `parent` field is the device's NAME or HOST string, never its
        # numeric objid (verified against the live dev server - `parent`
        # values were e.g. "PRTG Core Server", "192.168.1.6", never a
        # numeric id) - a previous version of this method compared `parent`
        # against a set of numeric device objids, which could NEVER match,
        # so every vendor-native alert silently lost its device link. Build
        # a name-or-host -> objid lookup instead.
        device_external_id_by_name_or_host: dict[str, str] = {}
        for row in raw.get("devices", []):
            if row.get("objid") is None:
                continue
            objid = str(row["objid"])
            if row.get("name"):
                device_external_id_by_name_or_host[row["name"]] = objid
            if row.get("host"):
                device_external_id_by_name_or_host[row["host"]] = objid

        alerts = self._normalize_messages(raw.get("messages", []), device_external_id_by_name_or_host, now_ts)

        return NormalizedBundle(devices=devices, sensors=sensors, metrics=metrics, alerts=alerts)

    @staticmethod
    def _normalize_device(row: dict[str, Any], groups_by_id: dict[str, dict[str, Any]]) -> NormalizedDevice:
        group_id = row.get("group")
        site_hint = None
        if group_id is not None:
            group = groups_by_id.get(str(group_id))
            if group:
                site_hint = group.get("name")
        return NormalizedDevice(
            external_id=str(row["objid"]),
            name=row.get("name") or "unknown-device",
            # PRTG's devices/groups/sensors columns used here carry no
            # reliable device-type signal (no vendor/model/category column
            # requested) - defaulting to "other" rather than fabricating one.
            device_type="other",
            hostname=row.get("host"),
            status=_derive_device_status(row.get("status")),
            site_hint=site_hint,
        )

    @staticmethod
    def _normalize_sensor(row: dict[str, Any]) -> NormalizedSensorReading:
        parent_id = row.get("parentid")
        return NormalizedSensorReading(
            external_id=str(row["objid"]),
            name=row.get("name") or "unknown-sensor",
            metric_type="prtg_sensor",
            value=_parse_lastvalue(row.get("lastvalue_")),
            status=_normalize_sensor_status(row),
            device_external_id=str(parent_id) if parent_id is not None else None,
        )

    @staticmethod
    def _normalize_channel_metrics(channels: dict[str, list[dict[str, Any]]], recorded_at: datetime) -> list[NormalizedMetric]:
        metrics: list[NormalizedMetric] = []
        for sensor_id, rows in channels.items():
            for row in rows:
                value = _parse_lastvalue(row.get("lastvalue_"))
                if value is None:
                    continue
                metrics.append(
                    NormalizedMetric(
                        metric_type="prtg_channel",
                        value=value,
                        recorded_at=recorded_at,
                        unit=row.get("unit"),
                        sensor_external_id=str(sensor_id),
                    )
                )
        return metrics

    @staticmethod
    def _normalize_historic_metrics(historic: dict[str, list[dict[str, Any]]], fallback_recorded_at: datetime) -> list[NormalizedMetric]:
        metrics: list[NormalizedMetric] = []
        for sensor_id, points in historic.items():
            for point in points:
                recorded_at = _parse_prtg_datetime(point.get("datetime")) or fallback_recorded_at
                for key, raw_value in point.items():
                    if key in ("datetime",) or key.endswith("_raw"):
                        continue
                    value = _coerce_numeric(raw_value)
                    if value is None:
                        continue
                    metrics.append(
                        NormalizedMetric(
                            metric_type=f"prtg_historic_{key}",
                            value=value,
                            recorded_at=recorded_at,
                            sensor_external_id=str(sensor_id),
                        )
                    )
        return metrics

    @staticmethod
    def _normalize_messages(
        rows: list[dict[str, Any]],
        device_external_id_by_name_or_host: dict[str, str],
        fallback_triggered_at: datetime,
    ) -> list[NormalizedAlertSignal]:
        alerts: list[NormalizedAlertSignal] = []
        for row in rows:
            if row.get("objid") is None:
                continue
            parent = row.get("parent")
            # See normalize()'s BUG FIX note: `parent` is a device name/host
            # string, not a numeric objid - looked up accordingly. Messages
            # whose parent is a probe/group name with no matching device
            # (e.g. "Local Probe") correctly resolve to None here, same as
            # before the fix - this only stops the ALWAYS-None bug for
            # parents that DO name a real device.
            device_external_id = (
                device_external_id_by_name_or_host.get(parent) if parent else None
            )
            alerts.append(
                NormalizedAlertSignal(
                    external_id=str(row["objid"]),
                    title=row.get("name") or row.get("message") or "PRTG message",
                    severity=_derive_message_severity(row.get("status") or row.get("type")),
                    triggered_at=_parse_prtg_datetime(row.get("datetime")) or fallback_triggered_at,
                    description=row.get("message"),
                    device_external_id=device_external_id,
                )
            )
        return alerts


def _parse_lastvalue(raw: str | None) -> float | None:
    """Copied verbatim from the old `PRTGAdapter` (not imported - that module
    is retired in task 10.3). Strips PRTG's unit suffixes (e.g. "12 %",
    "3.4 kbit/s") down to a bare numeric value."""
    if not raw:
        return None
    numeric = "".join(ch for ch in raw if ch.isdigit() or ch in ".-")
    try:
        return float(numeric) if numeric else None
    except ValueError:
        return None


def _coerce_numeric(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        return _parse_lastvalue(raw)
    return None


def _coerce_status_code(raw: Any) -> int | None:
    """Interpret `raw` as a PRTG numeric status code, or return None.

    Accepts ints, whole floats and digit-only strings: PRTG's tables are JSON
    and the same logical field arrives as a number on some API surfaces and as
    a quoted string on others (the checked-in fixtures in
    `tests/fixtures/prtg_payloads.py` deliberately carry both forms).
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    return None


def _status_from_text(value: Any) -> str | None:
    """Map PRTG's human-readable status text to a canonical status, or None."""
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    for needle, status in _PRTG_STATUS_TEXT:
        if needle in lowered:
            return status
    return None


def _normalize_sensor_status(row: dict[str, Any]) -> str:
    """Resolve a PRTG sensors-table row to the canonical status vocabulary.

    PRTG exposes a sensor's status twice (`status` and `status_raw`) and which
    one holds the numeric code versus the text differs between API surfaces and
    versions, so both columns are accepted in both forms rather than betting on
    one and silently degrading every sensor to "unknown":

      1. numeric code, preferring `status`
      2. text spelling, preferring `status_raw`
      3. "unknown" - an unrecognised value is never upgraded into an alarm.

    Sensor code 5 (Down) therefore resolves to "down" whichever column carries
    it, and a deliberate "Paused" state never becomes alerting.
    """
    for column in ("status", "status_raw"):
        code = _coerce_status_code(row.get(column))
        if code is not None and code in _PRTG_STATUS_MAP:
            return _PRTG_STATUS_MAP[code]

    for column in ("status_raw", "status"):
        status = _status_from_text(row.get(column))
        if status is not None:
            return status

    return "unknown"


def _derive_device_status(status_text: str | None) -> str:
    """PRTG's `devices` table returns a human-readable status string (e.g.
    "Up", "Down", "Down (Acknowledged)", "Warning", "Paused") rather than a
    numeric code, so this maps by text instead of `_PRTG_STATUS_MAP`.

    Shares `_status_from_text` with the sensor path so the two can never
    disagree - including "Unusual", which PRTG also reports for devices and
    which the sensor path maps to "warning"."""
    return _status_from_text(status_text) or "unknown"


def _derive_message_severity(status_text: str | None) -> str:
    if not status_text:
        return "info"
    text = status_text.lower()
    if "error" in text or "down" in text or "critical" in text:
        return "critical"
    if "warning" in text:
        return "warning"
    return "info"


def _parse_prtg_datetime(raw: str | None) -> datetime | None:
    """Best-effort parse of PRTG's timestamp formats. Returns None (caller
    falls back to `now()`) if the format isn't recognized rather than raising."""
    if not raw:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d-%H-%M-%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
