"""
Scrape-target health from the platform Prometheus, for the superuser-only
Platform Observability page.

This is Prometheus's own view: which HSDEEP processes it is scraping, whether
the last scrape worked, and when. It covers every role (api / worker /
scheduler), unlike the in-process summary, which only sees its own process.

Read-only, server-side, bounded: one GET to ``/api/v1/targets`` with a short
timeout. ``settings.prometheus_url`` is never sent to the browser, and errors
are reported as a category, not as a raw exception that might echo a URL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

TIMEOUT_SECONDS = 3.0
MAX_ERROR_CHARS = 200


def _valid_base(url: str) -> str | None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        return None
    return url.strip().rstrip("/")


def _target(t: dict[str, Any]) -> dict[str, Any]:
    labels = t.get("labels") or {}
    error = (t.get("lastError") or "").strip()
    return {
        "job": labels.get("job", ""),
        "component": labels.get("component"),
        "instance": labels.get("instance", ""),
        "health": t.get("health", "unknown"),  # up | down | unknown
        "last_scrape": t.get("lastScrape"),
        "last_scrape_seconds": t.get("lastScrapeDuration"),
        "last_error": error[:MAX_ERROR_CHARS] or None,
    }


async def fetch_targets(base_url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    base = _valid_base(base_url) if base_url else None
    if not base_url:
        return {"configured": False, "reachable": False, "error": None, "checked_at": checked_at,
                "targets": [], "jobs": []}
    if base is None:
        return {"configured": True, "reachable": False, "error": "invalid_url", "checked_at": checked_at,
                "targets": [], "jobs": []}

    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        resp = await client.get(f"{base}/api/v1/targets", params={"state": "active"})
        resp.raise_for_status()
        payload = resp.json()
        active = payload["data"]["activeTargets"]
    except httpx.TimeoutException:
        return {"configured": True, "reachable": False, "error": "timeout", "checked_at": checked_at,
                "targets": [], "jobs": []}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"configured": True, "reachable": False, "error": "unreachable", "checked_at": checked_at,
                "targets": [], "jobs": []}
    finally:
        if own:
            await client.aclose()

    targets = sorted((_target(t) for t in active), key=lambda t: (t["job"], t["instance"]))
    jobs: dict[str, dict[str, Any]] = {}
    for t in targets:
        row = jobs.setdefault(t["job"], {"job": t["job"], "up": 0, "total": 0})
        row["total"] += 1
        row["up"] += 1 if t["health"] == "up" else 0
    return {"configured": True, "reachable": True, "error": None, "checked_at": checked_at,
            "targets": targets, "jobs": sorted(jobs.values(), key=lambda j: j["job"])}
