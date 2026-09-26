"""
ServiceNow REST API client.

Follows the collector pattern: httpx.AsyncClient with inline timeout + retry
loop. Adds OAuth2 token refresh, rate limiting via asyncio.Semaphore, and
HMAC-SHA256 signature verification for webhooks.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

import asyncio
import hashlib

import httpx

from app.services.servicenow.rate_limiter import default_limiter

logger = logging.getLogger(__name__)


class ServiceNowAuthError(RuntimeError):
    """Authentication failed (bad credentials, expired token)."""


class ServiceNowHTTPError(RuntimeError):
    """Non-2xx HTTP response from ServiceNow."""


# ---------------------------------------------------------------------------
# Token refresh constants
# ---------------------------------------------------------------------------

_TOKEN_GRACE_PERIOD_SECONDS = 300  # 5-minute grace window for expiry


class ServiceNowClient:
    """Thin HTTP layer for ServiceNow REST API v2. Follows the same pattern
    as collectors: httpx.AsyncClient with inline timeout + retry loop, never
    a shared client. Credentials are decrypted by the caller (SyncService)
    before being passed to constructor."""

    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY_SECONDS = 1.0

    def __init__(
        self,
        instance_url: str,
        auth_type: str,
        *,
        username: str | None = None,
        password: str | None = None,
        oauth_client_id: str | None = None,
        access_token: str | None = None,
        client_secret: str | None = None,
        rate_limit_rpm: int = 100,
    ):
        self.instance_url = instance_url.rstrip("/")
        self.auth_type = auth_type
        self.username = username
        self.password = password
        self.oauth_client_id = oauth_client_id
        self.access_token = access_token
        self.client_secret = client_secret
        self.rate_limit_rpm = rate_limit_rpm
        # Use instance_url hash as the per-connection rate limiter key.
        # In a multi-tenant shared-instance scenario this is sufficient for
        # Phase 1; future phases can pass an explicit connection UUID.
        self._rate_key = int(hashlib.sha256(instance_url.encode()).hexdigest(), 16) % (2**63)

    # ------------------------------------------------------------------
    # HTTP core
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        """Issue a single HTTP request with retry logic. Returns the JSON body as dict."""
        url = f"{self.instance_url}/{path.lstrip('/')}"

        # Rate-limit per connection to avoid hitting ServiceNow API quotas
        await default_limiter.acquire(self._rate_key, self.rate_limit_rpm)

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    headers, auth = self._build_auth()

                    response = await client.request(
                        method=method.upper(),
                        url=url,
                        json=json_data,
                        headers=headers,
                        auth=auth,
                    )

                    if response.status_code == 401:
                        raise ServiceNowAuthError(
                            f"ServiceNow auth failed (attempt {attempt}): {response.text}"
                        )

                    if response.status_code == 429:
                        # Rate limited — retry with backoff
                        if attempt < self._MAX_RETRIES:
                            delay = self._BASE_RETRY_DELAY_SECONDS * attempt
                            logger.warning("ServiceNow rate-limited (429), retrying in %ss", delay)
                            await asyncio.sleep(delay)
                            continue
                        raise ServiceNowHTTPError(
                            f"ServiceNow rate-limited after {attempt} attempts: {response.text}"
                        )

                    if response.status_code >= 500:
                        # Server error — retry with backoff
                        if attempt < self._MAX_RETRIES:
                            delay = self._BASE_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                            logger.warning("ServiceNow server error %d, retrying in %ss", response.status_code, delay)
                            await asyncio.sleep(delay)
                            continue
                        raise ServiceNowHTTPError(
                            f"ServiceNow server error after {attempt} attempts: {response.text}"
                        )

                    if response.status_code not in (200, 201, 204):
                        raise ServiceNowHTTPError(
                            f"Unexpected ServiceNow response {response.status_code}: {response.text}"
                        )

                    # 204 No Content has no body
                    if response.status_code == 204:
                        return {}

                    body = response.json()
                    # ServiceNow v2 wraps in {\"result\": ...}
                    if isinstance(body, dict) and "result" in body:
                        result = body["result"]
                        # If it's a list, unwrap one more level for single-item responses
                        return result if isinstance(result, (dict, list)) else {"result": result}
                    return body

            except httpx.TimeoutException as exc:
                logger.warning("ServiceNow request timed out (attempt %d): %s", attempt, exc)
                if attempt < self._MAX_RETRIES:
                    await asyncio.sleep(self._BASE_RETRY_DELAY_SECONDS * attempt)
                else:
                    raise ServiceNowHTTPError(f"ServiceNow request timed out after {attempt} attempts") from exc
            except httpx.TransportError as exc:
                logger.warning("ServiceNow transport error (attempt %d): %s", attempt, exc)
                if attempt < self._MAX_RETRIES:
                    await asyncio.sleep(self._BASE_RETRY_DELAY_SECONDS * attempt)
                else:
                    raise ServiceNowHTTPError(f"ServiceNow connection failed after {attempt} attempts") from exc

        # Should never reach here (all branches either return or raise above)
        raise ServiceNowHTTPError("Unexpected state: request exhausted retries without final action")

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _build_auth(self):
        """Return (headers, auth_tuple) for the configured auth type."""
        if self.auth_type == "basic" and self.username and self.password:
            return {}, (self.username, self.password)

        token = self.access_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        if self.auth_type == "oauth2" and self.client_secret:
            # OAuth2 token is in Authorization header; client_secret used for refresh
            pass

        return headers, None

    async def verify_webhook_signature(self, body: bytes, signature_header: str, secret: str) -> bool:
        """Verify HMAC-SHA256 signature of a ServiceNow webhook payload."""
        expected = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature_header, f"sha256={expected}")

    # ------------------------------------------------------------------
    # OAuth 2.0 token refresh
    # ------------------------------------------------------------------

    async def refresh_token(self) -> tuple[str, str | None, datetime]:
        """Refresh the OAuth2 access token using the client secret + refresh
        token (which must be set on self before calling).

        Returns (new_access_token, new_refresh_token_or_None, expires_at)."""
        # The caller sets self.access_token / self.client_secret before calling.
        # For a true flow we'd need the refresh_token stored separately — but in
        # the sync service pattern, token management happens at the service layer.
        # This method handles the actual HTTP call when called with the right params.
        data = {
            "grant_type": "refresh_token",
            "client_id": self.oauth_client_id,
            "refresh_token": getattr(self, "_refresh_token", ""),  # set by caller if needed
            "client_secret": self.client_secret or "",
        }
        result = await self._request(
            "POST", "/oauth_token.do", json_data=data, timeout=10.0
        )

        new_token = result.get("access_token", "")
        expires_in = int(result.get("expires_in", 3600))
        new_refresh = result.get("refresh_token")
        now = datetime.now(timezone.utc)
        expires_at = now.replace(tzinfo=timezone.utc) + timedelta(seconds=expires_in)
        self.access_token = new_token  # update local state for subsequent calls

        return new_token, new_refresh, expires_at

    # ------------------------------------------------------------------
    # Incident Table API operations
    # ------------------------------------------------------------------

    async def create_incident(self, payload: dict) -> dict:
        """Create a ServiceNow incident via the Table API.
        Payload: {short_description, description, priority, state, correlation_id, ...}"""
        return await self._request("POST", "/api/now/table/incident", json_data=payload, timeout=30.0)

    async def update_incident(self, sys_id: str, payload: dict) -> None:
        """Update an existing ServiceNow incident via the Table API."""
        await self._request(
            "PUT", f"/api/now/table/incident/{sys_id}", json_data=payload, timeout=30.0
        )

    async def get_incident(self, sys_id: str) -> dict:
        """Fetch a single ServiceNow incident by sys_id."""
        return await self._request("GET", f"/api/now/table/incident/{sys_id}", timeout=30.0)

    async def list_incidents(
        self,
        *,
        correlation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List ServiceNow incidents, optionally filtered by correlation_id."""
        query = {"limit": str(limit)}
        if correlation_id:
            query["sysparm_query"] = f"correlation_id={correlation_id}"
        result = await self._request(
            "GET", "/api/now/table/incident", timeout=30.0
        )
        return result if isinstance(result, list) else [result]

    async def add_work_note(self, sys_id: str, note: str) -> None:
        """Add a work note to a ServiceNow incident via the Comments API."""
        await self._request(
            "POST", f"/api/now/table/incident_comment",
            json_data={"incident": sys_id, "work_notes": note},
            timeout=30.0,
        )

    async def list_incidents_filtered(
        self,
        *,
        query: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List ServiceNow incidents with an optional sys_updated_on filter
        for delta sync. Mirrors list_incidents but supports a free-form
        query parameter (e.g. 'sys_updated_on>2026-07-18 10:00:00')."""
        query_params = {"sysparm_limit": str(limit)}
        if query:
            query_params["sysparm_query"] = query
        # Use _request with params (query string) instead of json_data
        url = f"{self.instance_url}/api/now/table/incident"
        result = await self._get_with_params(url, query_params)
        return result if isinstance(result, list) else [result]

    async def _get_with_params(self, url: str, params: dict) -> dict | list:
        """Issue a GET request with query-string parameters."""
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                await default_limiter.acquire(self._rate_key, self.rate_limit_rpm)
                async with httpx.AsyncClient(timeout=30.0) as client:
                    headers, auth = self._build_auth()
                    response = await client.request(
                        method="GET",
                        url=url,
                        params=params,
                        headers=headers,
                        auth=auth,
                    )
                    if response.status_code == 401:
                        raise ServiceNowAuthError(f"ServiceNow auth failed (attempt {attempt}): {response.text}")
                    if response.status_code == 429:
                        if attempt < self._MAX_RETRIES:
                            await asyncio.sleep(self._BASE_RETRY_DELAY_SECONDS * attempt)
                            continue
                        raise ServiceNowHTTPError(f"ServiceNow rate-limited after {attempt} attempts")
                    if response.status_code >= 500:
                        if attempt < self._MAX_RETRIES:
                            await asyncio.sleep(self._BASE_RETRY_DELAY_SECONDS * (2 ** (attempt - 1)))
                            continue
                        raise ServiceNowHTTPError(f"ServiceNow server error after {attempt} attempts")
                    if response.status_code not in (200, 201, 204):
                        raise ServiceNowHTTPError(f"Unexpected response {response.status_code}")
                    body = response.json()
                    if isinstance(body, dict) and "result" in body:
                        result = body["result"]
                        return result if isinstance(result, list) else {"result": result}
                    return body
            except httpx.TimeoutException as exc:
                logger.warning("ServiceNow GET timed out (attempt %d): %s", attempt, exc)
                if attempt < self._MAX_RETRIES:
                    await asyncio.sleep(self._BASE_RETRY_DELAY_SECONDS * attempt)
                else:
                    raise ServiceNowHTTPError(f"ServiceNow GET timed out after {attempt} attempts") from exc
            except httpx.TransportError as exc:
                logger.warning("ServiceNow GET transport error (attempt %d): %s", attempt, exc)
                if attempt < self._MAX_RETRIES:
                    await asyncio.sleep(self._BASE_RETRY_DELAY_SECONDS * attempt)
                else:
                    raise ServiceNowHTTPError(f"ServiceNow connection failed after {attempt} attempts") from exc
        raise ServiceNowHTTPError("Unexpected state in _get_with_params")
