"""Secure telemetry ingestion API routes (mounted by app.main at /api/v1/ingest).

POST /metrics and POST /events accept bounded batches of canonical telemetry
authenticated by a collector credential (``X-HSDEEP-Collector-Credential``).
The tenant and monitoring source are ALWAYS derived from the credential - never
from the payload. Each item is validated independently (partial-batch
semantics): a missing value is rejected rather than coerced to zero, a resource
scope (device) is checked for tenant ownership, and caller-supplied stable ids
(``sample_id`` / ``event_id``) are deduplicated within a batch.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_refresh_token
from app.core.db import get_db
from app.models.collector_credential import CollectorCredential
from app.models.device import Device
from app.models.metric import Metric
from app.models.normalized_event import NormalizedEvent
from app.schemas.ingest import (
    IngestEventsRequest,
    IngestMetricsRequest,
    IngestResponse,
    ItemResult,
    MetricSample,
)

router = APIRouter(prefix="/ingest", tags=["ingest"])

COLLECTOR_CREDENTIAL_HEADER = "X-HSDEEP-Collector-Credential"


async def get_collector_principal(
    credential: str | None = Header(default=None, alias=COLLECTOR_CREDENTIAL_HEADER),
    db: AsyncSession = Depends(get_db),
) -> CollectorCredential:
    """Resolve the collector credential header to a live, non-revoked credential.

    Tenant and monitoring source are read from this row only; a collector can
    never supply them. The 401 detail deliberately reveals nothing about WHY
    the credential failed (missing vs. unknown vs. revoked).
    """
    if not credential:
        raise HTTPException(status_code=401, detail="Invalid or unusable collector credential")
    row = (
        await db.execute(
            select(CollectorCredential).where(
                CollectorCredential.credential_hash == hash_refresh_token(credential),
                CollectorCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or unusable collector credential")
    row.last_used_at = datetime.now(timezone.utc)
    return row


def _value_error(sample: MetricSample) -> str | None:
    """Reject a sample whose declared value type has no usable value."""
    if sample.value_type == "numeric" and sample.value is None:
        return "missing_value"
    if sample.value_type == "text" and sample.text_value is None:
        return "missing_value"
    if sample.value_type == "boolean" and sample.boolean_value is None:
        return "missing_value"
    return None


async def _device_owned_by(db: AsyncSession, device_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """True when the device exists and belongs to the credential's tenant."""
    device = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    return device is not None and device.tenant_id == tenant_id


@router.post("/metrics", response_model=IngestResponse)
async def ingest_metrics(
    payload: IngestMetricsRequest,
    cred: CollectorCredential = Depends(get_collector_principal),
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    tenant_id = cred.tenant_id
    # The credential's source is authoritative; a payload source override is
    # accepted by the schema but never used to redirect writes.
    source_id = cred.source_id
    now = datetime.now(timezone.utc)

    results: list[ItemResult] = []
    accepted = rejected = duplicates = 0
    seen_sample_ids: set[str] = set()

    for index, sample in enumerate(payload.samples):
        if sample.sample_id and sample.sample_id in seen_sample_ids:
            duplicates += 1
            results.append(ItemResult(index=index, ok=True, id=sample.sample_id, duplicate=True))
            continue
        if sample.sample_id:
            seen_sample_ids.add(sample.sample_id)

        error = _value_error(sample)
        if error is None and sample.device_id is not None:
            if not await _device_owned_by(db, sample.device_id, tenant_id):
                error = "forbidden_resource"

        if error is not None:
            rejected += 1
            results.append(ItemResult(index=index, ok=False, id=sample.sample_id, error=error))
            continue

        db.add(
            Metric(
                tenant_id=tenant_id,
                monitoring_source_id=source_id,
                device_id=sample.device_id,
                sensor_id=sample.sensor_id,
                interface_id=sample.interface_id,
                metric_type=sample.name,
                metric_name=sample.name,
                value=sample.value,
                unit=sample.unit,
                recorded_at=sample.timestamp,
                capability=sample.capability,
                provider=sample.provider,
                collection_status=sample.collection_status,
                quality=sample.quality,
                text_value=sample.text_value,
                boolean_value=sample.boolean_value,
                source_timestamp=sample.timestamp,
                received_timestamp=now,
                dimensions=sample.dimensions,
            )
        )
        accepted += 1
        results.append(ItemResult(index=index, ok=True, id=sample.sample_id))

    await db.commit()
    return IngestResponse(
        correlation_id=str(uuid.uuid4()),
        accepted=accepted,
        rejected=rejected,
        duplicates=duplicates,
        results=results,
    )


@router.post("/events", response_model=IngestResponse)
async def ingest_events(
    payload: IngestEventsRequest,
    cred: CollectorCredential = Depends(get_collector_principal),
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    tenant_id = cred.tenant_id

    results: list[ItemResult] = []
    accepted = rejected = duplicates = 0
    seen_event_ids: set[str] = set()

    for index, event in enumerate(payload.events):
        if event.event_id and event.event_id in seen_event_ids:
            duplicates += 1
            results.append(ItemResult(index=index, ok=True, id=event.event_id, duplicate=True))
            continue
        if event.event_id:
            seen_event_ids.add(event.event_id)

        error = None
        if event.device_id is not None:
            if not await _device_owned_by(db, event.device_id, tenant_id):
                error = "forbidden_resource"

        if error is not None:
            rejected += 1
            results.append(ItemResult(index=index, ok=False, id=event.event_id, error=error))
            continue

        db.add(
            NormalizedEvent(
                tenant_id=tenant_id,
                site_id=event.site_id,
                device_id=event.device_id,
                integration_id=event.integration_id,
                source_event_id=event.source_event_id,
                event_signature=event.event_signature,
                event_category=event.event_category,
                event_type=event.event_type,
                severity=event.severity,
                title=event.title,
                description=event.description,
                attributes=event.attributes or {},
                event_timestamp=event.timestamp,
                telemetry_type=event.telemetry_type,
                confidence_score=event.confidence_score,
                occurrence_count=1,
                last_occurred_at=event.timestamp,
            )
        )
        accepted += 1
        results.append(ItemResult(index=index, ok=True, id=event.event_id))

    await db.commit()
    return IngestResponse(
        correlation_id=str(uuid.uuid4()),
        accepted=accepted,
        rejected=rejected,
        duplicates=duplicates,
        results=results,
    )
