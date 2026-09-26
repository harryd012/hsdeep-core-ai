"""ServiceNow ITSM Provider adapter (Phase 3)."""
from __future__ import annotations
import hashlib, hmac, json, logging, time
from datetime import datetime
from typing import Any
import httpx
from app.services.itsm.base_provider import (
    BaseITSMProvider, ITSMAuthError, ITSMCommentCreate, ITSMHealthCheckResult,
    ITSMHTTPError, ITSMProviderError, ITSMTicketCreate, ITSMTicketResponse,
    ITSMTicketUpdate, ITSMWebhookResult,
)

logger = logging.getLogger(__name__)
_STATE_REV = {1: "new", 2: "in_progress", 3: "on_hold", 6: "resolved", 7: "closed"}
_PRI_REV = {"1 - Critical": "P1", "2 - High": "P2", "3 - Moderate": "P3", "4 - Low": "P4"}
_PRI_TO = {"P1": "1 - Critical", "P2": "2 - High", "P3": "3 - Moderate", "P4": "4 - Low"}

class _RateLimiter:
    def __init__(self, rpm: int) -> None:
        self._rpm, self._ts = rpm, []

    async def acquire(self) -> None:
        import asyncio
        now = time.monotonic()
        cutoff = now - 60
        self._ts = [t for t in self._ts if t > cutoff]
        if len(self._ts) >= self._rpm:
            sf = self._ts[0] + 60 - now
            if sf > 0:
                await asyncio.sleep(sf)
                now = time.monotonic()
                self._ts = [t for t in self._ts if t > now - 60]
        self._ts.append(now)


class ServiceNowProvider(BaseITSMProvider):
    """ServiceNow provider using Table API."""

    provider_type = "servicenow"
    display_name = "ServiceNow"
    supported_auth_modes = ["oauth2", "basic"]
    capabilities = {"create_ticket": True, "update_ticket": True, "close_ticket": True,
                    "add_comment": True, "add_attachment": True, "bi_directional_sync": True,
                    "webhooks": True, "cmdb": True, "polling": True}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._client: httpx.AsyncClient | None = None
        self._rl = _RateLimiter(self.rate_limit_rpm)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.instance_url,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=httpx.Timeout(30.0))
        await self._ensure_token()
        return self._client

    async def _ensure_token(self) -> None:
        if self.auth_type != "oauth2":
            return
        tok = self.credentials.get("oauth_access_token")
        exp = self.credentials.get("oauth_token_expires_at")
        if tok and exp:
            try:
                if time.time() < float(exp):
                    self._client.headers["Authorization"] = f"Bearer {tok}"
                    return
            except (ValueError, TypeError):
                pass
        await self.refresh_token()

    async def refresh_token(self) -> dict[str, Any]:
        if self.auth_type != "oauth2":
            return {}
        cid = self.credentials.get("oauth_client_id")
        csec = self.credentials.get("oauth_client_secret")
        rtok = self.credentials.get("oauth_refresh_token")
        if not (cid and csec and rtok):
            raise ITSMAuthError("Missing OAuth2 credentials", provider_type=self.provider_type)
        async with httpx.AsyncClient(timeout=30.0) as c:
            resp = await c.post(f"{self.instance_url}/oauth_token.do", data={
                "grant_type": "refresh_token", "client_id": cid,
                "client_secret": csec, "refresh_token": rtok})
            if resp.status_code == 200:
                td = resp.json()
                self.credentials["oauth_access_token"] = td["access_token"]
                self.credentials["oauth_refresh_token"] = td.get("refresh_token", rtok)
                self.credentials["oauth_token_expires_at"] = time.time() + td.get("expires_in", 3600)
                self._client.headers["Authorization"] = f"Bearer {td['access_token']}"
                return td
            raise ITSMAuthError(f"Token refresh failed: {resp.status_code}", provider_type=self.provider_type)

    async def _req(self, method: str, path: str, *, jb: dict | None = None) -> dict[str, Any]:
        c = await self._get_client()
        await self._rl.acquire()
        resp = await c.request(method, f"/api/now/table/{path}", json=jb)
        if resp.status_code == 401:
            raise ITSMAuthError("Auth failed", provider_type=self.provider_type)
        elif resp.status_code >= 400:
            raise ITSMHTTPError(f"API error {resp.status_code}: {resp.text[:300]}",
                                provider_type=self.provider_type, status_code=resp.status_code)
        return resp.json() if resp.status_code != 204 else {}

    async def test_connection(self) -> ITSMHealthCheckResult:
        try:
            import time as _t
            s = _t.monotonic()
            await self._req("GET", "sys_user?syspath=&limit=1")
            return ITSMHealthCheckResult(healthy=True, message="OK",
                                        latency_ms=(_t.monotonic()-s)*1000, provider_type=self.provider_type)
        except ITSMAuthError as e:
            return ITSMHealthCheckResult(healthy=False, message=str(e), provider_type=self.provider_type)
        except Exception as e:
            return ITSMHealthCheckResult(healthy=False, message=str(e))

    def map_priority_to_external(self, p: str) -> str:
        return _PRI_TO.get(p, "3 - Moderate")

    def map_priority_from_external(self, e: str) -> str:
        return _PRI_REV.get(e, "P3")

    def map_status_from_external(self, e: str) -> str:
        try:
            return _STATE_REV.get(int(e), "open")
        except (ValueError, TypeError):
            return "open"

    def map_status_to_external(self, s: str) -> str:
        return str({"new": 1, "open": 1, "in_progress": 2, "resolved": 6, "closed": 7}.get(s, 1))

    async def create_ticket(self, payload: ITSMTicketCreate) -> ITSMTicketResponse:
        r = await self._req("POST", "incident", json_body={
            "short_description": payload.title, "description": payload.description,
            "priority": self.map_priority_to_external(payload.priority or "P3"),
            "category": payload.category or "Inquiry", "subcategory": payload.subcategory or "",
            "assignment_group": payload.assignment_group or "",
            "u_correlation_id": payload.correlation_id or ""})
        sn = r.get("result", {})
        if not sn:
            raise ITSMProviderError("No result", provider_type=self.provider_type)
        return ITSMTicketResponse(
            external_id=sn["sys_id"], external_number=sn.get("number"),
            title=sn.get("short_description"), priority=self.map_priority_from_external(str(sn.get("priority", ""))),
            status=self.map_status_from_external(str(sn.get("state", ""))),
            category=sn.get("category"), subcategory=sn.get("subcategory"),
            assignment_group=sn.get("assignment_group"), correlation_id=sn.get("u_correlation_id"), raw=sn)

    async def get_ticket(self, external_id: str) -> ITSMTicketResponse:
        r = await self._req("GET", f"incident/{external_id}")
        sn = r.get("result", {})
        if not sn:
            raise ITSMProviderError(f"Ticket {external_id} not found", provider_type=self.provider_type)
        return ITSMTicketResponse(
            external_id=sn["sys_id"], external_number=sn.get("number"),
            title=sn.get("short_description"), priority=self.map_priority_from_external(str(sn.get("priority", ""))),
            status=self.map_status_from_external(str(sn.get("state", ""))),
            category=sn.get("category"), subcategory=sn.get("subcategory"),
            assignment_group=sn.get("assignment_group"), raw=sn)

    async def update_ticket(self, ext_id: str, payload: ITSMTicketUpdate) -> ITSMTicketResponse:
        sn: dict[str, Any] = {}
        if payload.title: sn["short_description"] = payload.title
        if payload.description: sn["description"] = payload.description
        if payload.priority: sn["priority"] = self.map_priority_to_external(payload.priority)
        if payload.status: sn["state"] = self.map_status_to_external(payload.status)
        if payload.assignment_group: sn["assignment_group"] = payload.assignment_group
        await self._req("PATCH", f"incident/{ext_id}", json_body=sn)
        return await self.get_ticket(ext_id)

    async def close_ticket(self, ext_id: str, note: str | None = None) -> ITSMTicketResponse:
        await self._req("PATCH", f"incident/{ext_id}", json_body={
            "state": 7, "close_code": "Solved", "close_notes": note or "Closed via HSDEEP CORE AI"})
        return await self.get_ticket(ext_id)

    async def list_tickets(self, *, query: str | None = None, limit: int = 50,
                           updated_since: datetime | None = None) -> list[ITSMTicketResponse]:
        params = {"sysparm_limit": str(limit)}
        if query: params["sysparm_query"] = query
        if updated_since:
            ds = updated_since.strftime("%Y-%m-%d %H:%M:%S")
            q = params.get("sysparm_query", "")
            params["sysparm_query"] = f"{q}^sys_updated_on>{ds}" if q else f"sys_updated_on>{ds}"
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        r = await self._req("GET", f"incident?{qs}")
        return [ITSMTicketResponse(
            external_id=inc["sys_id"], external_number=inc.get("number"),
            title=inc.get("short_description"), priority=inc.get("priority"),
            status=self.map_status_from_external(str(inc.get("state", ""))), raw=inc)
            for inc in r.get("result", [])]

    async def add_comment(self, ext_id: str, payload: ITSMCommentCreate) -> dict[str, Any]:
        ct = "work_notes" if payload.is_internal else "comments"
        await self._req("PATCH", f"incident/{ext_id}", json_body={ct: payload.body})
        return {"external_comment_id": None, "is_internal": payload.is_internal, "body": payload.body}

    async def add_attachment(self, ext_id: str, filename: str, content: bytes,
                            content_type: str | None = None) -> dict[str, Any]:
        c = await self._get_client()
        await self._rl.acquire()
        files = {"file": (filename, content, content_type or "application/octet-stream")}
        url = f"/api/now/attachment/file?table_name=incident&table_sys_id={ext_id}"
        resp = await c.post(url, files=files)
        if resp.status_code >= 400:
            raise ITSMHTTPError(f"Attachment failed: {resp.status_code}", provider_type=self.provider_type)
        a = resp.json().get("result", {})
        return {"external_attachment_id": a.get("sys_id"), "filename": a.get("file_name", filename),
                "size_bytes": a.get("size")}

    async def verify_webhook_signature(self, body: bytes, sig: str, secret: str) -> bool:
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, f"sha256={expected}")

    async def parse_webhook_payload(self, body: bytes) -> ITSMWebhookResult:
        p = json.loads(body)
        return ITSMWebhookResult(external_id=p.get("sys_id", ""), external_number=p.get("number"),
                                 status=p.get("state"), priority=p.get("priority"), raw=p)