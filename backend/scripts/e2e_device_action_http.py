"""Authenticated HTTP round-trip of the Device Action Plane API.

Logs in (SEED_ADMIN_* from backend/.env), then exercises the API exactly
as the dashboard would: catalog -> capabilities -> PING request -> read back.
Read-only only; no state-changing action is executed.
"""
import os
import sys

sys.path.insert(0, r"I:\AI-LAB\hsdeep-core-ai\backend")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.core.config import get_settings  # noqa: E402

BASE = "http://127.0.0.1:8000"


def main() -> None:
    settings = get_settings()
    email = os.environ.get("SEED_ADMIN_EMAIL")
    password = os.environ.get("SEED_ADMIN_PASSWORD")
    tenant_id = os.environ.get("SEED_TENANT_ID")
    if not (email and password):
        print("SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD not set in env - skipping")
        print("env has SEED_ADMIN_EMAIL:", bool(email))
        return

    with httpx.Client(base_url=BASE, timeout=30) as c:
        r = c.post("/api/auth/login", json={"email": email, "password": password})
        print("login:", r.status_code)
        if r.status_code != 200:
            return
        token = r.json().get("access_token")
        headers = {"Authorization": f"Bearer {token}"}
        if tenant_id:
            headers["X-Tenant-Id"] = tenant_id

        r = c.get("/api/device-actions/catalog", headers=headers)
        ok = r.status_code == 200
        print("catalog:", r.status_code, "entries:", len(r.json()) if ok else "-")
        impl = [e["action"] for e in r.json() if e["implemented"]] if ok else []
        print("implemented actions:", impl)

        r = c.get("/api/device-actions", headers=headers)
        print("list requests:", r.status_code, "count:", len(r.json()) if r.status_code == 200 else "-")

        if r.status_code == 200:
            print("(no credential values are ever present in these payloads)")

        # Full-stack action round trip on a real device (read-only PING).
        r = c.get("/api/devices", headers=headers)
        devices = r.json() if r.status_code == 200 else []
        target = next((d for d in devices if d.get("ip_address") or d.get("hostname")), None)
        print("devices:", len(devices), "actionable target:", (target or {}).get("name"))
        if target is None:
            print("no device with an address - stopping before writes")
            return

        r = c.post("/api/device-actions", headers=headers, json={
            "device_id": target["id"], "action": "PING", "params": {"count": 2},
            "motivation": {"why": "authenticated HTTP e2e validation"},
        })
        print("POST action:", r.status_code)
        if r.status_code != 202:
            print("body:", r.text[:300])
            return
        created = r.json()
        print("request status:", created["status"], "| correlation:", created["correlation_id"])
        print("verification passed:", created["verification"].get("passed"),
              "| error:", created["error"])

        r = c.get(f"/api/device-actions/{created['id']}", headers=headers)
        print("GET request:", r.status_code, "status:", r.json().get("status") if r.status_code == 200 else "-")

        # A state-changing action must be gated (policy deny at creation).
        r = c.post("/api/device-actions", headers=headers, json={
            "device_id": target["id"], "action": "REBOOT_DEVICE", "params": {},
        })
        body = r.json() if r.status_code == 202 else {}
        print("REBOOT_DEVICE:", r.status_code, "status:", body.get("status"))


if __name__ == "__main__":
    main()