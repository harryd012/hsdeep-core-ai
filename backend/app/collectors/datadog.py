"""
Datadog telemetry collector (v1 REST API).

Maps credentials: ``api_key`` -> DD API key, ``password`` -> DD APP key
(both headers-only, never URLs/logs). Site resolves from an allowlist via
``config['site']``. Phase A: hosts, metrics, events, monitors.
"""
from __future__ import annotations

import asyncio
import logging
import random
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
    NormalizedEvent,
    NormalizedMetric,
    NormalizedSensorReading,
)

logger = logging.getLogger(__name__)

# Allowed Datadog site suffixes -> https://api.<site>. US1-FED (ddog-gov.com)
# is deliberately excluded (unverified endpoint family). No silent fallback.
_DATADOG_ALLOWED_SITES: frozenset[str] = frozenset(
    {"datadoghq.com", "us3.datadoghq.com", "us5.datadoghq.com",
     "datadoghq.eu", "ap1.datadoghq.com"}
)

_STATUS_TIMEOUT = 10.0
_TABLE_TIMEOUT = 25.0
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0
_MAX_RETRY_AFTER_SECONDS = 30.0
_DEFAULT_WINDOW_MINUTES = 60
_MAX_WINDOW_MINUTES = 24 * 60
_DEFAULT_MAX_RESULTS = 100
_MAX_RESULTS_HARD_CAP = 1000
_DEFAULT_MAX_POINTS_PER_SERIES = 60


def _retry_after_seconds(response: httpx.Response) -> float:
    """Parse Retry-After (delta-seconds), bounded. Defaults to 1.0s."""
    try:
        delay = float(response.headers.get("Retry-After", "1"))
    except (TypeError, ValueError):
        return _RETRY_DELAY_SECONDS
    if delay < 0 or delay != delay:  # NaN guard
        return _RETRY_DELAY_SECONDS
    return min(delay, _MAX_RETRY_AFTER_SECONDS)


@CollectorRegistry.register("datadog")
class DatadogCollector(BaseCollector):
    display_name = "Datadog"
    implemented = True
    category = "monitoring"

    def _require_config(self) -> None:
        missing: list[str] = []
        if not self.source.api_key:
            missing.append("api_key (Datadog API key)")
        if not self.source.password:
            missing.append("password (Datadog application key)")
        try:
            self._resolve_site()
        except CollectorConfigError as exc:
            missing.append(str(exc))
        if missing:
            raise CollectorConfigError(
                f"Datadog config missing required field(s): {missing}"
            )

    @staticmethod
    def _normalize_site_candidate(raw: str) -> str:
        return (raw or "").strip().lower()

    def _resolve_site(self) -> str:
        """Resolve site suffix from ``config['site']`` (preferred) or
        ``source.hostname``. Only ``<suffix>``/``api.<suffix>`` forms present
        in the allowlist are accepted. Rejects URLs, ports, user-info,
        unknown hosts, and ddog-gov.com. Raises CollectorConfigError."""
        cfg = self.source.config or {}
        candidates: list[str] = []
        raw_site = cfg.get("site")
        if isinstance(raw_site, str) and raw_site.strip():
            candidates.append(self._normalize_site_candidate(raw_site))
        if self.source.hostname:
            candidates.append(self._normalize_site_candidate(self.source.hostname))
        for candidate in candidates:
            if not candidate:
                continue
            if ("://" in candidate or "@" in candidate or "/" in candidate
                    or " " in candidate or ":" in candidate):
                continue
            host = candidate[4:] if candidate.startswith("api.") else candidate

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float = _TABLE_TIMEOUT,
    ) -> httpx.Response:
        """GET ``path`` with bounded retries. Retries transport errors,
        timeouts, 5xx and 429 (honouring Retry-After, capped). Never retries
        401/403/404. Logs path/attempt/status only - never secrets."""
        url = f"{self._api_base_url()}{path}"
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(verify=True, timeout=timeout) as client:
                    response = await client.get(
                        url, headers=self._auth_headers(), params=params
                    )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "Datadog request failed: source_id=%s path=%s attempt=%s/%s error_type=%s",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, type(exc).__name__,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(
                        _RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                        + random.uniform(0, 0.5)
                    )
                    continue
                raise
            status = response.status_code
            if status in (401, 403, 404):
                return response
            if status == 429 and attempt < _MAX_ATTEMPTS:
                delay = _retry_after_seconds(response)
                logger.warning(
                    "Datadog rate-limited: source_id=%s path=%s attempt=%s/%s retry_after=%ss",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, delay,
                )
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                continue
            if status >= 500 and attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "Datadog server error: source_id=%s path=%s attempt=%s/%s status=%s",
                    self.source.id, path, attempt, _MAX_ATTEMPTS, status,
                )
                await asyncio.sleep(
                    _RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                    + random.uniform(0, 0.5)
                )
                continue
            return response

        if last_exc is not None:
            raise last_exc
        assert response is not None  # pragma: no cover
        return response

    @staticmethod
    def _clamp_window_minutes(value: Any) -> int:
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            return _DEFAULT_WINDOW_MINUTES
        return max(1, min(minutes, _MAX_WINDOW_MINUTES))

    @staticmethod
    def _clamp_limit(value: Any, default: int = _DEFAULT_MAX_RESULTS) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, min(limit, _MAX_RESULTS_HARD_CAP))

    async def authenticate(self) -> ConnectionCheck:
        """Validate via GET /api/v1/validate. Never raises for expected
        failures - returns ConnectionCheck(False, ...) instead. Raises
        CollectorConfigError only when config itself is invalid."""
        self._require_config()
        try:
            resp = await self._request("/api/v1/validate", timeout=_STATUS_TIMEOUT)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return ConnectionCheck(
                success=False, message=f"Could not reach Datadog: {exc}"
            )
        if resp.status_code == 200:
            return ConnectionCheck(success=True, message="Connected to Datadog.")
        if resp.status_code in (401, 403):
            return ConnectionCheck(
                success=False,
                message="Datadog: Unauthorized (invalid API/application key).",
            )
        return ConnectionCheck(
            success=False,
            message=f"Datadog responded with HTTP {resp.status_code}.",
        )

    async def collect(self) -> dict[str, Any]:
        """Pull hosts, metrics, events, monitors independently (best-effort
        per endpoint). A single capability failing never discards successful
        results from the others."""
        self._require_config()
        cfg = self.source.config or {}
        now = datetime.now(timezone.utc)
        metrics_window = self._clamp_window_minutes(
            cfg.get("metrics_window_minutes", _DEFAULT_WINDOW_MINUTES)
        )
        try:
            events_hours = float(cfg.get("events_window_hours", 2))
        except (TypeError, ValueError):
            events_hours = 2.0
        events_hours = max(1.0 / 60.0, min(events_hours, 24.0))
        max_hosts = self._clamp_limit(cfg.get("max_hosts"))
        max_events = self._clamp_limit(cfg.get("max_events"))
        max_monitors = self._clamp_limit(cfg.get("max_monitors"))
        result: dict[str, Any] = {
            "collected_at": now.isoformat(),
            "hosts": [], "metric_series": [],
            "events": [], "monitors": [], "capability_status": {},
        }

        # Hosts (v1 /hosts).
        try:
            hosts_resp = await self._request(
                "/api/v1/hosts", params={"count": max_hosts}
            )
            if hosts_resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    "datadog hosts unavailable", request=hosts_resp.request,
                    response=hosts_resp,
                )
            hosts_payload = hosts_resp.json()
            result["hosts"] = (
                hosts_payload.get("host_list")
                or hosts_payload.get("hosts")
                or []
            )[:max_hosts]
            result["capability_status"]["hosts"] = "collected"
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.warning(
                "Datadog collect(): hosts failed: source_id=%s error_type=%s",
                self.source.id, type(exc).__name__,
            )
            result["hosts"] = []
            result["capability_status"]["hosts"] = "failed"

        # Metrics (v1 /query, bounded window, explicit allowlist).
        queries = [
            q for q in (cfg.get("metric_queries") or cfg.get("metrics") or [])
            if isinstance(q, str) and q.strip()
        ]
        to_epoch = int(now.timestamp())
        from_epoch = to_epoch - metrics_window * 60
        result["metric_series"] = []
        if not queries:
            result["capability_status"]["metrics"] = "not_configured"
        else:
            metric_failures = 0
            max_points = self._clamp_limit(
                cfg.get("max_points_per_series"), _DEFAULT_MAX_POINTS_PER_SERIES
            )
            for query in queries:
                try:
                    query_resp = await self._request(
                        "/api/v1/query",
                        params={"from": from_epoch, "to": to_epoch,
                                "query": query},
                    )
                    query_resp.raise_for_status()
                    for series in query_resp.json().get("series", []):
                        points = series.get("pointlist") or []
                        if len(points) > max_points:
                            points = points[-max_points:]
                        result["metric_series"].append({
                            "query": query,
                            "metric": series.get("metric"),
                            "host": series.get("host"),
                            "unit": series.get("unit"),
                            "tag_set": series.get("tag_set") or [],
                            "pointlist": points,
                        })
                except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                    metric_failures += 1
                    logger.warning(
                        "Datadog collect(): metric query failed: source_id=%s "
                        "query_len=%s error_type=%s",
                        self.source.id, len(query), type(exc).__name__,
                    )
                    continue
            result["capability_status"]["metrics"] = (
                "failed" if metric_failures == len(queries) and queries
                else "collected"
            )

        # Events (v1 /events, bounded window).
        events_end = to_epoch
        events_start = events_end - int(events_hours * 3600)
        try:
            events_resp = await self._request(
                "/api/v1/events",
                params={"start": events_start, "end": events_end,
                        "per_page": max_events},
            )
            events_resp.raise_for_status()
            events_payload = events_resp.json()
            result["events"] = (events_payload.get("events") or [])[:max_events]
            result["capability_status"]["events"] = "collected"
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.warning(
                "Datadog collect(): events failed: source_id=%s error_type=%s",
                self.source.id, type(exc).__name__,
            )
            result["events"] = []
            result["capability_status"]["events"] = "failed"

        # Monitors (v1 /monitor, bounded).
        try:
            monitors_resp = await self._request(
                "/api/v1/monitor", params={"page": 0, "per_page": max_monitors}
            )
            monitors_resp.raise_for_status()
            monitors_payload = monitors_resp.json()
            monitors = (
                monitors_payload if isinstance(monitors_payload, list)
                else monitors_payload.get("monitors", [])
            )
            result["monitors"] = monitors[:max_monitors]
            result["capability_status"]["monitors"] = "collected"
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            logger.warning(
                "Datadog collect(): monitors failed: source_id=%s error_type=%s",
                self.source.id, type(exc).__name__,
            )
            result["monitors"] = []
            result["capability_status"]["monitors"] = "failed"

        return result

    # -- normalization helpers (pure; no I/O) ----------------------------------

    @staticmethod
    def _as_utc(ts: Any, fallback: datetime) -> datetime:
        """Best-effort epoch (s or ms) or ISO-8601 -> aware UTC datetime."""
        try:
            if isinstance(ts, (int, float)) and ts == ts:
                value = float(ts)
                if value > 1e14:  # microseconds
                    value = value / 1e6
                elif value > 1e11:  # milliseconds
                    value = value / 1e3
                return datetime.fromtimestamp(value, tz=timezone.utc)
            if isinstance(ts, str) and ts.strip():
                text = ts.strip()
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
        except (ValueError, OverflowError, OSError):
            pass
        return fallback

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if result == result else None  # drop NaN

    @staticmethod
    def _monitor_severity(overall_state: Any) -> str:
        state = (overall_state or "").strip().lower()
        if state == "alert":
            return "critical"
        if state == "warn":
            return "warning"
        return "info"

    @staticmethod
    def _event_severity(alert_type: Any) -> str:
        kind = (alert_type or "").strip().lower()
        if kind in ("error", "warning"):
            return "critical" if kind == "error" else "warning"
        return "info"

    @staticmethod
    def _tags(entry: Any) -> list[str]:
        tags = (entry or {}).get("tags") if isinstance(entry, dict) else None
        if isinstance(tags, list):
            return [str(t) for t in tags if t is not None]
        return []

    def normalize(self, raw: Any) -> NormalizedBundle:
        """Raw vendor payload -> NormalizedBundle. Pure function: no I/O,
        never raises on malformed records (they are skipped), never invents
        identities - host/device/alert rows only ever come from fields the
        API actually returned."""
        payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
        now = datetime.now(timezone.utc)
        devices: list[NormalizedDevice] = []
        sensors: list[NormalizedSensorReading] = []
        metrics: list[NormalizedMetric] = []
        events: list[NormalizedEvent] = []
        alerts: list[NormalizedAlertSignal] = []

        # Hosts -> devices (stable external id: reported id, else hostname).
        seen_devices: set[str] = set()
        for host in (payload.get("hosts") or []):
            if not isinstance(host, dict):
                continue
            host_id = host.get("id")
            hostname = host.get("hostname") or host.get("name")
            external_id = str(host_id) if host_id is not None else None
            if not external_id and hostname:
                external_id = f"hostname:{hostname}"
            if not external_id:
                continue  # no identity: never fabricate one
            if external_id in seen_devices:
                continue
            seen_devices.add(external_id)
            meta = host.get("metadata") or {}
            role = host.get("role")
            devices.append(NormalizedDevice(
                external_id=external_id,
                name=hostname or f"datadog-host-{external_id}",
                device_type="server",
                vendor="Datadog",
                hostname=hostname,
                ip_address=(meta.get("gohai") or {}).get("network_ip")
                if isinstance(meta.get("gohai"), dict) else None,
                status=self._host_status(host),
                custom_metadata={
                    "provider": "datadog",
                    "datadog_host_id": host_id,
                    "datadog_role": role,
                    "datadog_apps": host.get("apps"),
                    "datadog_report_agent_version": host.get("report_agent_version"),
                },
            ))

        # Time-series -> metrics. Every persisted point keeps the original
        # metric name, value, unit, tags and host identity. Point timestamps
        # are Datadog epoch-milliseconds normalized to aware UTC.
        for series in (payload.get("metric_series") or []):
            if not isinstance(series, dict):
                continue
            metric_name = series.get("metric")
            if not isinstance(metric_name, str) or not metric_name.strip():
                continue
            unit = series.get("unit")
            tags = series.get("tag_set") or []
            tag_list = [str(t) for t in tags if t is not None] if isinstance(tags, list) else []
            host = series.get("host")
            scope_host = str(host) if host else None
            for point in (series.get("pointlist") or []):
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    continue
                recorded_at = self._as_utc(point[0], now)
                value = self._number(point[1])
                if value is None:
                    continue  # null/NaN carries no signal: skip, don't zero-fill
                metrics.append(NormalizedMetric(
                    metric_type=metric_name.strip(),
                    value=value,
                    recorded_at=recorded_at,
                    unit=unit if isinstance(unit, str) and unit else None,
                    device_external_id=scope_host,
                ))
            # Sensor snapshot: one deterministic row per series so collection
            # freshness (sensors.last_value_at) and per-sensor analytics
            # reflect this poll. External id binds series+host, never fabricating
            # a device: the host must already identify one (same value written
            # to device_external_id above, for the BundleSaver to resolve).
            latest_value: float | None = None
            latest_at: datetime | None = None
            for point in (series.get("pointlist") or []):
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    continue
                value = self._number(point[1])
                if value is None:
                    continue
                recorded_at = self._as_utc(point[0], now)
                if latest_at is None or recorded_at > latest_at:
                    latest_value, latest_at = value, recorded_at
            if scope_host is not None and latest_at is not None and latest_value is not None:
                sensors.append(NormalizedSensorReading(
                    external_id=f"datadog-metric:{metric_name.strip()}:{scope_host}",
                    name=f"{metric_name.strip()} ({scope_host})",
                    metric_type=metric_name.strip(),
                    value=latest_value,
                    unit=unit if isinstance(unit, str) and unit else None,
                    status="unknown",
                    device_external_id=scope_host,
                ))


        # Events -> events. Dedup key: the native Datadog event id
        # (v1 events/idempotent). A fetch window that re-queries the same
        # interval therefore re-persists the same ids instead of duplicating.
        for event in (payload.get("events") or []):
            if not isinstance(event, dict):
                continue
            event_id = event.get("id")
            title = event.get("title")
            if event_id is None or not title:
                continue
            host = event.get("host")
            events.append(NormalizedEvent(
                event_type="datadog_event",
                message=str(title),
                occurred_at=self._as_utc(
                    event.get("timestamp") or event.get("date_happened"), now
                ),
                severity=self._event_severity(event.get("alert_type")),
                external_id=f"datadog-event-{event_id}",
                device_external_id=str(host) if host else None,
                raw_payload={
                    "provider": "datadog",
                    "datadog_event_id": event_id,
                    "datadog_text": event.get("text"),
                    "datadog_tags": self._tags(event),
                },
            ))

        # Monitors -> alert signals. Only actionable states become alert
        # signals: overall_state Alert/Warn. Definitions missing an id/name
        # are skipped. One row per monitor (not per poll), so repeated
        # polls re-assert the same external id instead of opening incidents.
        for monitor in (payload.get("monitors") or []):
            if not isinstance(monitor, dict):
                continue
            monitor_id = monitor.get("id")
            name = monitor.get("name")
            if monitor_id is None or not name:
                continue
            severity = self._monitor_severity(monitor.get("overall_state"))
            if severity not in ("critical", "warning"):
                continue
            alerts.append(NormalizedAlertSignal(
                external_id=f"datadog-monitor-{monitor_id}",
                title=str(name),
                severity=severity,
                triggered_at=self._as_utc(monitor.get("modified"), now),
                description=str(monitor.get("message", "") or "") or None,
                device_external_id=None,
            ))

        return NormalizedBundle(
            devices=devices, sensors=sensors, metrics=metrics,
            events=events, alerts=alerts,
        )

    @staticmethod
    def _host_status(host: dict[str, Any]) -> str:
        """Datadog is_up boolean -> up/down/unknown. Never infers outage."""
        is_up = host.get("is_up")
        if is_up is True:
            return "up"
        if is_up is False:
            return "down"
        return "unknown"

    def _api_base_url(self) -> str:
        return f"https://api.{self._resolve_site()}"

    def _auth_headers(self) -> dict[str, str]:
        # Secrets only ever leave this class inside these headers.
        return {
            "DD-API-KEY": self.source.api_key or "",
            "DD-APPLICATION-KEY": self.source.password or "",
            "Content-Type": "application/json",
        }
