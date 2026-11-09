"""
OTLP/HTTP trace ingest (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P8 slice 1).

    POST /api/v1/ingest/otlp/v1/traces

So an OpenTelemetry exporter configured with

    OTEL_EXPORTER_OTLP_ENDPOINT=https://<platform>/api/v1/ingest/otlp
    OTEL_EXPORTER_OTLP_PROTOCOL=http/json
    OTEL_EXPORTER_OTLP_HEADERS=X-HSDEEP-Collector-Credential=<credential>

sends here unchanged. Authenticated by a collector credential, exactly like
the other ingest routes: the tenant and monitoring source come from the
credential, never from the payload. JSON encoding only (protobuf is answered
with 415 and says so). The response follows OTLP: 200 with `partialSuccess`
when some spans were rejected; a resent span is a no-op.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.ingest import get_collector_principal
from app.core.db import get_db
from app.models.collector_credential import CollectorCredential
from app.models.trace_span import TraceSpan
from app.services.tracing.otlp import decode

router = APIRouter(prefix="/ingest/otlp", tags=["otlp"])

MAX_BODY_BYTES = 8 * 1024 * 1024
INSERT_CHUNK = 1000


@router.post("/v1/traces")
async def export_traces(
    request: Request,
    cred: CollectorCredential = Depends(get_collector_principal),
    db: AsyncSession = Depends(get_db),
) -> dict:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail="Only OTLP/HTTP JSON is accepted: set OTEL_EXPORTER_OTLP_PROTOCOL=http/json",
        )
    if request.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
        raise HTTPException(status_code=415, detail="Compressed bodies are not accepted; send uncompressed JSON")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"Body larger than {MAX_BODY_BYTES // (1024 * 1024)} MB; send smaller batches")
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Body is not valid JSON") from exc

    decoded = decode(payload)
    rows = [{**s, "tenant_id": cred.tenant_id, "monitoring_source_id": cred.source_id} for s in decoded.spans]
    for start in range(0, len(rows), INSERT_CHUNK):
        await db.execute(
            insert(TraceSpan).values(rows[start:start + INSERT_CHUNK])
            .on_conflict_do_nothing(constraint="uq_trace_spans_tenant_trace_span")
        )
    await db.commit()

    if decoded.rejected:
        return {"partialSuccess": {"rejectedSpans": decoded.rejected, "errorMessage": decoded.first_error}}
    return {}
