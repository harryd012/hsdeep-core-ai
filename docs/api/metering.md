# Usage Metering API

## Overview

The Usage Metering system tracks per-tenant consumption of platform resources. It provides:

- **Idempotent usage counting** — counters are bucketed by hour and upserted via `ON CONFLICT DO UPDATE`
- **In-memory batching** — counters are batched in memory (default 500 entries) before flushing to PostgreSQL, keeping the hot path under 2ms p99 overhead
- **REST API** — aggregated summaries and hourly bucket data for the frontend dashboard
- **Billing webhook stub** — emits webhook events when tenant usage crosses configurable thresholds
- **Subscription tier validation** — frontend `ConsumptionTracker` compares current usage against tier limits

## Event Types

| Event Type | Description |
|------------|-------------|
| `api.call` | HTTP API requests |
| `alert.ingested` | Alerts ingested from monitoring sources |
| `metric.ingested` | Metric data points recorded |
| `collector.run` | Collector execution runs |
| `sop.executed` | SOP/runbook executions |
| `workflow.executed` | Workflow automation executions |
| `auth.login` | Authentication login attempts |

## API Endpoints

Base path: `/api/v1`

### Get Usage Summary

```
GET /tenants/{tenant_id}/usage/summary?days=30
```

**Permissions:** `usage.read`

**Response:**

```json
{
  "tenant_id": "uuid",
  "total_api_calls": 1234,
  "total_alerts_ingested": 567,
  "total_metrics_ingested": 89012,
  "total_collector_runs": 345,
  "total_sop_executions": 12,
  "total_workflow_executions": 8,
  "total_auth_logins": 45,
  "grand_total": 91223,
  "period_start": "2026-06-26T00:00:00+00:00",
  "period_end": "2026-07-26T12:00:00+00:00"
}
```

### Get Usage Events (Hourly Buckets)

```
GET /tenants/{tenant_id}/usage/events?days=7&event_type=api.call
```

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `days` | int | 7 | Lookback window (1-90) |
| `event_type` | string | — | Optional filter by event type |

**Response:**

```json
{
  "tenant_id": "uuid",
  "events": [
    {
      "event_type": "api.call",
      "bucket_start": "2026-07-26T10:00:00+00:00",
      "count": 42
    }
  ],
  "total": 42
}
```

## Architecture

```
┌──────────────┐     ┌──────────────────────┐     ┌──────────────┐
│  Pipeline     │────▶│  TenantUsageCollector │────▶│  PostgreSQL  │
│  Events       │     │  (in-memory batch)    │     │  (upsert)    │
└──────────────┘     └──────────────────────┘     └──────────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │  BillingWebhook  │
                     │  (HTTP POST)     │
                     └──────────────────┘
```

### Collection Pipeline Injection

The `TenantUsageCollector` is injected into the ingestion pipeline via `app/services/ingester/pipeline.py`. Counter functions are called at key points in the collection lifecycle:

- `count_ingested_alert()` — called from `AlertService` after alert upsert
- `count_ingested_metric()` — called from `BundleSaver` after metric persistence
- `count_collector_run()` — called from `CollectionOrchestrator` after run completion
- `count_api_call()` — called from middleware or route handlers

### Idempotent Upsert

Counters are stored with a unique constraint on `(tenant_id, event_type, bucket_start)`. The upsert uses PostgreSQL `ON CONFLICT DO UPDATE`:

```sql
INSERT INTO usage_counters (tenant_id, event_type, bucket_start, count, created_at, updated_at)
VALUES (:tenant_id, :event_type, :bucket_start, :count, NOW(), NOW())
ON CONFLICT (tenant_id, event_type, bucket_start)
DO UPDATE SET count = usage_counters.count + :count, updated_at = NOW()
```

This guarantees exactly-once semantics even if the same event is processed multiple times.

## Billing Webhook

The `BillingWebhook` stub emits webhook events when tenant usage exceeds configurable thresholds.

**Default Thresholds:**

| Event Type | Threshold |
|------------|-----------|
| `api.call` | 100,000 |
| `alert.ingested` | 50,000 |
| `metric.ingested` | 1,000,000 |
| `collector.run` | 10,000 |

**Configuration:**

| Env Variable | Description |
|-------------|-------------|
| `BILLING_WEBHOOK_URL` | Target URL for billing webhook POST |
| `BILLING_WEBHOOK_SECRET` | HMAC-SHA256 signing secret |

**Webhook Payload:**

```json
{
  "event_id": "uuid",
  "event_type": "usage.threshold_exceeded",
  "tenant_id": "uuid",
  "timestamp": "2026-07-26T12:00:00+00:00",
  "data": {
    "exceeded_usage": {
      "api.call": 105000
    },
    "total_usage": {
      "api.call": 105000,
      "alert.ingested": 50000
    }
  }
}
```

**Headers:**

| Header | Description |
|--------|-------------|
| `X-Usage-Event-ID` | Idempotency key |
| `X-Usage-Timestamp` | ISO 8601 timestamp |
| `X-Usage-Signature` | HMAC-SHA256 hex digest |

## Database Schema

### `usage_counters`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | UUID | PK |
| `tenant_id` | UUID | NOT NULL, INDEX |
| `event_type` | VARCHAR(128) | NOT NULL |
| `bucket_start` | TIMESTAMPTZ | NOT NULL |
| `count` | BIGINT | DEFAULT 0 |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() |

**Unique constraint:** `(tenant_id, event_type, bucket_start)`

## Frontend Integration

The `ConsumptionTracker` component polls the usage API and renders:

1. **Executive Summary** — KPI cards for total API calls, alerts, metrics, collector runs
2. **Subscription Tier Limits** — Progress bars showing current usage vs. tier limits, with red highlighting when exceeded
3. **Daily Usage Trend** — Bar chart of total daily usage over the lookback window

Usage Page: `/usage`

## Performance

The `TenantUsageCollector` uses in-memory batching to avoid per-event DB writes. The batching parameters:

- `batch_size`: 500 (flush threshold)
- Flush is also triggered at the end of each request cycle via `flush_usage_counters()`

Target: **<2ms p99 overhead** on the ingestion hot path.