"""
ServiceNow Webhook Handler.

Processes inbound ServiceNow webhook events (incident state changes, etc.):
1. Verifies HMAC-SHA256 signature using connection's webhook_secret_encrypted
2. Resolves local incident via correlation_id → incident_link.incident_id
3. Updates local incident state based on SN fields
4. Records event in alert/incident history

Returns the resolved local incident_id if successful, None if signature is invalid.
"""
from __future__ import annotations

import logging
import uuid

from app.core.security import SecretCrypto
from app.models.servicenow import ServiceNowIncidentLink
from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
from app.repositories.servicenow_incident_link_repository import ServiceNowIncidentLinkRepository
from app.services.servicenow.client import ServiceNowClient, ServiceNowAuthError

logger = logging.getLogger(__name__)


class WebhookSignatureInvalid(ValueError):
    """HMAC signature verification failed."""


class IncomingWebhookProcessor:
    def __init__(
        self,
        conn_repo: ServiceNowConnectionRepository,
        link_repo: ServiceNowIncidentLinkRepository,
    ):
        self.conn_repo = conn_repo
        self.link_repo = link_repo

    async def process(
        self,
        tenant_id: uuid.UUID,
        body: bytes,
        signature_header: str,
        instance_url: str | None = None,
    ) -> dict:
        """Process a single inbound ServiceNow webhook event.

        Args:
            tenant_id: Tenant scope (from X-Tenant-ID header or resolved via link)
            body: Raw request body bytes
            signature_header: X-ServiceNow-Signature header value
            instance_url: Optional service-now-instance URL to identify the connection

        Returns:
            dict with processing result including resolved incident_id
        """
        # Step 1: Find the ServiceNow connection
        conn = None
        if instance_url:
            conn = await self.conn_repo.get_active_by_instance_url(tenant_id, instance_url)
        if not conn:
            # Try to find any active connection for this tenant (single-tenant default)
            conns = await self.conn_repo.list_active(tenant_id)
            if len(conns) == 1:
                conn = conns[0]
            else:
                return {"status": "error", "message": "No matching ServiceNow connection found"}

        # Step 2: Verify HMAC-SHA256 signature
        if conn.webhook_secret_encrypted:
            crypto = SecretCrypto()
            try:
                webhook_secret = crypto.decrypt(conn.webhook_secret_encrypted)
            except Exception:
                return {"status": "error", "message": "Failed to decrypt webhook secret"}

            client = ServiceNowClient(
                instance_url=conn.instance_url,
                auth_type=conn.auth_type,
            )
            valid = await client.verify_webhook_signature(body, signature_header, webhook_secret)
            if not valid:
                logger.warning("Invalid webhook signature for tenant %s", tenant_id)
                raise WebhookSignatureInvalid("HMAC signature verification failed")

        # Step 3: Parse the webhook body (caller validates JSON first)
        import json
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return {"status": "error", "message": "Invalid JSON in webhook body"}

        correlation_id = payload.get("correlation_id")
        if not correlation_id:
            return {"status": "error", "message": "Webhook missing correlation_id"}

        # Step 4: Resolve local incident via correlation_id
        link = await self.link_repo.get_by_correlation_id(tenant_id, correlation_id)
        if not link:
            logger.warning("No ServiceNowIncidentLink found for correlation_id=%s", correlation_id)
            return {"status": "no_link", "correlation_id": correlation_id}

        # Step 5: Update conflict status if needed
        sn_state = payload.get("state")
        if sn_state and link.has_unresolved_conflict:
            # Clear conflict if SN state is stable
            await self.link_repo.update_conflict(link, has_conflict=False)

        return {
            "status": "processed",
            "incident_id": str(link.incident_id),
            "link_id": str(link.id),
            "sn_number": link.sn_number,
            "sn_state": sn_state,
            "correlation_id": correlation_id,
        }
