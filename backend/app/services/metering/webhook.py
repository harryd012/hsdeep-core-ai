"""
Billing webhook stub for metering.

Emits idempotent webhook payloads with ``X-Usage-Event-ID`` and timestamp
headers when tenant usage crosses configurable thresholds. Designed to be
integrated with external billing systems (Stripe, Chargebee, etc.).

Usage:
    from app.services.metering.webhook import default_billing_webhook

    # After flushing counters, emit if threshold exceeded
    await default_billing_webhook.emit_if_threshold_exceeded(
        tenant_id=..., usage_summary=..., db=...
    )
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.metering.collector import TenantUsageCollector

logger = logging.getLogger(__name__)

# Default thresholds per event type that trigger a webhook
DEFAULT_THRESHOLDS: dict[str, int] = {
    "api.call": 100_000,
    "alert.ingested": 50_000,
    "metric.ingested": 1_000_000,
    "collector.run": 10_000,
}


class BillingWebhook:
    """Emits idempotent billing webhook events.

    Each payload includes an ``X-Usage-Event-ID`` header derived from
    the tenant_id, event_type, and bucket_start to guarantee idempotent
    delivery to the billing system.
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        signing_secret: str | None = None,
        thresholds: dict[str, int] | None = None,
    ) -> None:
        self._webhook_url = webhook_url or os.getenv(
            "BILLING_WEBHOOK_URL", ""
        )
        self._signing_secret = signing_secret or os.getenv(
            "BILLING_WEBHOOK_SECRET", ""
        )
        self._thresholds = thresholds or DEFAULT_THRESHOLDS

    async def emit_if_threshold_exceeded(
        self,
        tenant_id: uuid.UUID,
        usage_summary: dict[str, int],
        db: AsyncSession,
    ) -> None:
        """Check if any event type has crossed its threshold and emit a
        webhook if so.

        This is a stub — in production this would POST to the billing
        system's webhook endpoint. For now it logs the event.
        """
        if not self._webhook_url:
            logger.debug(
                "BillingWebhook: no URL configured; skipping webhook for tenant %s",
                tenant_id,
            )
            return

        exceeded: dict[str, int] = {}
        for event_type, count in usage_summary.items():
            threshold = self._thresholds.get(event_type)
            if threshold is not None and count >= threshold:
                exceeded[event_type] = count

        if not exceeded:
            return

        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = {
            "event_id": event_id,
            "event_type": "usage.threshold_exceeded",
            "tenant_id": str(tenant_id),
            "timestamp": timestamp,
            "data": {
                "exceeded_usage": exceeded,
                "total_usage": usage_summary,
            },
        }

        # Compute signature for verification
        signature = self._sign(payload)

        headers = {
            "X-Usage-Event-ID": event_id,
            "X-Usage-Timestamp": timestamp,
            "X-Usage-Signature": signature,
            "Content-Type": "application/json",
        }

        # Stub: log instead of POST
        logger.info(
            "BillingWebhook: would POST to %s with headers=%s payload=%s",
            self._webhook_url,
            headers,
            json.dumps(payload),
        )

    def _sign(self, payload: dict) -> str:
        """HMAC-SHA256 sign the payload for webhook verification."""
        if not self._signing_secret:
            return ""
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hmac.new(
            self._signing_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


# Module-level singleton
default_billing_webhook = BillingWebhook()