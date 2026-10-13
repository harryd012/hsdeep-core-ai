# Centralized Observability Contract (Phase 9A)

The application already emits one structured line per API request
(`app/core/request_logging.py`). Phase 9A adds the ability to ship it to a
central platform **without adding a paid vendor dependency** and without
changing the message format.

## Request log line

Fields present on every routed request (unchanged since Phase 8):

| Field | Meaning |
| --- | --- |
| `method` | HTTP method |
| `route` | request path (no query string) |
| `status` | response status code |
| `duration_ms` | wall-clock handler duration, one decimal |
| `correlation_id` | caller-supplied `X-Correlation-ID` or a generated uuid4 |
| `tenant_id` | tenant of the **resolved principal** (null when unauthenticated) |
| `user_id` | user of the resolved principal (null when unauthenticated) |

`tenant_id`/`user_id` come from `request.state.principal`, published by
`get_current_user` - the token itself is never read by the logger, and no token,
password, API key, cookie or PRTG credential is ever logged.

Levels: 2xx -> INFO, 4xx -> WARNING, 5xx -> ERROR. An unhandled exception is
logged as `api_request_failed` at ERROR **and re-raised**. `/health`, `/metrics`,
`/docs`, `/openapi.json` and `/redoc` are excluded from per-request logging
(health/readiness probes would otherwise dominate the log volume).

### text mode (default, unchanged)

```
2026-09-25T11:12:56+0530 | INFO | app.request | api_request method=GET route=/api/incidents status=200 duration_ms=2321.9 correlation_id=probe-corr-9 tenant_id=54dd0ca8-873f-4b8e-bb54-c4f45f64d0b3 user_id=55221eed-abf1-41e9-a862-88156e01774e
```

### json mode (`LOG_FORMAT=json`, recommended in production)

```json
{"timestamp":"2026-09-25T05:42:56.873822+00:00","level":"INFO","logger":"app.request","message":"api_request method=GET route=/api/incidents status=200 duration_ms=2321.9 correlation_id=probe-corr-9 tenant_id=54dd0ca8-873f-4b8e-bb54-c4f45f64d0b3 user_id=55221eed-abf1-41e9-a862-88156e01774e","method":"GET","route":"/api/incidents","status":200,"duration_ms":2321.9,"correlation_id":"probe-corr-9","tenant_id":"54dd0ca8-873f-4b8e-bb54-c4f45f64d0b3","user_id":"55221eed-abf1-41e9-a862-88156e01774e"}
```

The seven fields are emitted as **first-class JSON keys** (not just inside
`message`), so a log platform can filter `tenant_id` / `correlation_id` /
`status` directly. Implementation: `app/core/logging.py::_JsonFormatter` (stdlib
`json` only), reusing the existing `_JsonFormatter` precedent in
`app/services/servicenow/audit.py`.

## Shipping

* All logs go to **stdout** (uvicorn + application). Ship them with the
  platform's collector - Fluent Bit, Vector, CloudWatch Logs, Datadog agent, or
  `kubectl logs`-based forwarding. No application code change is needed for that.
* Set `LOG_LEVEL` explicitly in production (enforced).
* `uvicorn`'s access log is disabled in the container image (`--no-access-log`)
  because `api_request` already covers it with tenant context.

## Correlation across boundaries

* Inbound `X-Correlation-ID` is honoured, echoed on the response, and stored on
  `request.state.correlation_id`; a missing header gets a generated uuid4.
* Domain events carry their own `correlation_id` (`DomainEvent.correlation_id`),
  and the RCA trigger derives a deterministic per-incident id
  (`rca-<incident_id>`) so a replay is recognisable in logs and by the
  duplicate-event guard.
* **Known gap (Phase 9B):** the request `correlation_id` is not yet propagated
  into events published by the same request, and `EventBus` is in-process, so a
  *worker-process* log cannot be joined to the originating request log by this
  field alone. Cross-process propagation needs the shared transport described in
  `README.md`.

## Safety rules (enforced in CI)

* `ci/secret_scan.py` fails the pipeline if credential material appears on a
  shipping path, and `--self-test` proves the patterns actually fire.
* Tests assert the request log contains no credential material (no `Bearer`,
  `eyJ`, `password`, `Authorization` substrings).
* `httpx`/`httpcore` loggers are pinned to WARNING so collector URLs (which may
  carry PRTG credentials as query parameters) can never be printed at INFO.
* Health/readiness responses expose dependency *states* only - never URLs or
  credentials.

## What Phase 9A deliberately does NOT do

* No observability vendor is added or configured.
* No dashboards, SLOs or alert rules are created.
* Nothing here claims logs are already being shipped by a production collector -
  that is a Phase 9B deployment-wiring item.
