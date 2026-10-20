"""
Versioned request/response schemas for the secure telemetry ingestion API
(POST /api/v1/ingest/metrics and POST /api/v1/ingest/events).

Design rules (spec sections 8 and 21):
  * Bounded batches and request bodies - a batch is capped in COUNT here and
    the whole body is capped by an explicit middleware/route guard, so a
    caller cannot stream an unbounded payload.
  * Strict validation of timestamps, units, dimensions and value types.
  * No field lets a caller choose a tenant - the tenant is resolved from the
    authenticated collector credential, never from the payload.
  * Missing values are REJECTED, never coerced to zero.
  * Explicit partial-batch semantics: each item carries its own result, so a
    single malformed item does not silently discard the valid ones, and the
    caller can see exactly which items failed and retry only those.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Batch bounds (spec: bounded batch sizes and request bodies).
# ---------------------------------------------------------------------------
MAX_BATCH_ITEMS = 500
MAX_DIMENSION_KEYS = 32
MAX_DIMENSION_VALUE_LEN = 256

MetricValueType = Literal["numeric", "text", "boolean"]


def _check_dimensions(dimensions: dict | None) -> None:
    """Bound dimensions so a payload cannot carry an unbounded attribute blob."""
    if not dimensions:
        return
    if len(dimensions) > MAX_DIMENSION_KEYS:
        raise ValueError(
            f"dimensions may contain at most {MAX_DIMENSION_KEYS} keys"
        )
    for key, value in dimensions.items():
        if not isinstance(key, str) or not key:
            raise ValueError("dimension keys must be non-empty strings")
        # Values are coerced to strings for storage; bound their length.
        if value is not None and len(str(value)) > MAX_DIMENSION_VALUE_LEN:
            raise ValueError(
                f"dimension {key!r} value exceeds {MAX_DIMENSION_VALUE_LEN} chars"
            )


# ===========================================================================
# Metrics
# ===========================================================================


class MetricSample(BaseModel):
    """One canonical metric reading.

    ``value_type`` selects which of ``value``/``text_value``/``boolean_value``
    is authoritative, mirroring the canonical Metric contract (spec 16). A
    sample with no usable value for its declared type is rejected by the
    route rather than being stored as 0.
    """

    model_config = ConfigDict(extra="forbid")

    #: Canonical metric name, e.g. "cpu.utilization". Required.
    name: str = Field(min_length=1, max_length=100)
    value_type: MetricValueType = "numeric"
    #: Present when value_type == "numeric".
    value: float | None = None
    #: Present when value_type == "text".
    text_value: str | None = Field(default=None, max_length=1000)
    #: Present when value_type == "boolean".
    boolean_value: bool | None = None
    unit: str | None = Field(default=None, max_length=30)
    #: Optional capability/provider classification (canonical telemetry).
    capability: str | None = Field(default=None, max_length=60)
    provider: str | None = Field(default=None, max_length=50)
    #: collected | unsupported | unavailable | unknown (honest state, never 0).
    collection_status: str = Field(default="collected", max_length=30)
    quality: str | None = Field(default=None, max_length=20)
    #: When the sample was measured at the source (event time).
    timestamp: datetime
    #: Optional resource scope. All must belong to the credential's tenant.
    device_id: uuid.UUID | None = None
    interface_id: uuid.UUID | None = None
    sensor_id: uuid.UUID | None = None
    #: Caller-supplied stable id for idempotent retry (see route docstring).
    sample_id: str | None = Field(default=None, max_length=128)
    dimensions: dict | None = None


class IngestMetricsRequest(BaseModel):
    """A bounded batch of metric samples.

    A batch is accepted as a unit: every valid sample is persisted and every
    invalid one is reported in ``results`` with ``ok: false``. There is no
    silent drop and no coercion of a missing value to zero.
    """

    model_config = ConfigDict(extra="forbid")

    samples: list[MetricSample] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    #: Optional explicit source override; defaults to the credential's source.
    source_id: uuid.UUID | None = None


class ItemResult(BaseModel):
    """Per-item outcome so a partial batch is explicit, never silent."""

    index: int
    ok: bool
    #: Stable id when the caller supplied sample_id / event_id.
    id: str | None = None
    #: True when this exact sample was a duplicate (idempotent no-op).
    duplicate: bool = False
    #: Actionable, non-sensitive error code when ok is False.
    error: str | None = None
    detail: str | None = None


class IngestResponse(BaseModel):
    correlation_id: str
    accepted: int
    rejected: int
    duplicates: int
    results: list[ItemResult]


# ===========================================================================
# Events (structured; NOT a full-text log engine - see route docstring)
# ===========================================================================

EventSeverity = Literal["critical", "warning", "info"]
EventCategory = Literal[
    "health", "network", "security", "performance", "routing", "vpn",
    "sdwan", "configuration", "cloud", "system", "application", "topology",
]
EventTelemetryType = Literal[
    "METRIC", "LOG", "EVENT", "ALERT", "SECURITY_EVENT", "HEALTH", "TOPOLOGY_CHANGE",
]


class EventItem(BaseModel):
    """One structured, classified event.

    A raw log line is accepted as an event with telemetry_type="LOG" (the
    message maps to ``title`` and structured fields to ``attributes``), but
    this API does NOT provide full-text log search - that requires a log
    query engine the platform does not yet have (documented limitation).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    severity: EventSeverity
    event_category: EventCategory
    event_type: str = Field(min_length=1, max_length=100)
    telemetry_type: EventTelemetryType = "EVENT"
    timestamp: datetime
    description: str | None = None
    #: Dedup key. When present (with an integration/source), a repeat bumps
    #: occurrence_count instead of inserting a new active row.
    event_signature: str | None = Field(default=None, max_length=500)
    source_event_id: str | None = Field(default=None, max_length=255)
    device_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    integration_id: uuid.UUID | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=100.0)
    attributes: dict | None = None
    #: Caller-supplied stable id for idempotent retry.
    event_id: str | None = Field(default=None, max_length=128)


class IngestEventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[EventItem] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    events: list[EventItem] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    source_id: uuid.UUID | None = None


# ===========================================================================
# Heartbeat (Phase 3 - Edge Agent liveness)
# ===========================================================================
# The platform previously had NO collector-facing heartbeat endpoint
# (GET /api/devices/collector-runs is read-only and every CollectorRun row is
# written server-side by CollectionOrchestrator/discovery). This endpoint is
# the smallest compatible extension: it reuses the same collector-credential
# auth and quota path as metrics/events ingest and persists NO new tables.
#
# Durable liveness state comes from CollectorCredential.last_used_at (bumped by
# get_collector_principal on EVERY credential-authenticated request) plus the
# freshness of ingested metrics - so agent health can be derived without a
# second health subsystem or duplicate dashboard fetching.
HeartbeatStatus = Literal["starting", "running", "degraded", "stopping", "unhealthy"]


class HeartbeatRequest(BaseModel):
    """One agent heartbeat.

    Deliberately carries NO tenant_id (resolved from the credential) and no
    secret material. All fields are optional beyond a status so a minimal
    agent can heartbeat with almost nothing and still get a server ack.
    """

    model_config = ConfigDict(extra="forbid")

    status: HeartbeatStatus = "running"
    #: Agent build version, diagnostics only (never used for authorization).
    agent_version: str | None = Field(default=None, max_length=50)
    #: Hostname as self-reported by the agent - diagnostic, untrusted.
    hostname: str | None = Field(default=None, max_length=255)
    #: Local spool depth so backpressure is observable server-side.
    queue_depth: int | None = Field(default=None, ge=0, le=10_000_000)
    #: When the agent last successfully delivered telemetry.
    last_successful_collection: datetime | None = None
    #: Enabled collector names (bounded) - capabilities, never authorization.
    capabilities: list[str] | None = Field(default=None, max_length=32)


class HeartbeatResponse(BaseModel):
    """Server acknowledgement. Contains no secrets and no other tenant data."""

    ok: bool
    server_time: datetime
    source_id: uuid.UUID
    #: Recommended heartbeat period the agent should use.
    interval_seconds: int

    source_id: uuid.UUID | None = None