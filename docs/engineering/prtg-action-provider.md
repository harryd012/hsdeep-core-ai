# PRTG Action Provider

Status: IMPLEMENTED + unit-tested (22 provider tests, 180-test regression
suite). Live PRTG integration test: BLOCKED (no development PRTG tenant
configured; no fake credentials will be created).

## Architecture

`PrtgActionProvider` (`app/actions/providers/prtg.py`) is registered in
`ActionProviderRegistry` under kind `vendor:prtg`. It REUSES the PRTG
HTTP transport of `PRTGCollector` (`_request`, `_auth_params`) so there is
exactly one PRTG HTTP implementation in the codebase.

## Actions

| Action              | Classification             | PRTG endpoint       |
|---------------------|----------------------------|---------------------|
| PRTG_QUERY_SENSOR   | READ_ONLY (auto)           | GET /api/table.json |
| PRTG_QUERY_DEVICE   | READ_ONLY (auto)           | GET /api/table.json |
| PRTG_PAUSE_SENSOR   | CHANGE_REQUIRING_APPROVAL  | POST /api/pause.htm |
| PRTG_RESUME_SENSOR  | CHANGE_REQUIRING_APPROVAL  | POST /api/resume.htm|

Required capability: `supports_monitoring_actions` (derived from a linked,
active `prtg` MonitoringSource).

## Targeting

- `sensor_id` must be a numeric id resolved from a Sensor row (`external_id`);  
  free-text or non-numeric ids fail gracefully (success=False).
- Device queries resolve via the sensor row when only sensor_id is given.
- No arbitrary URLs: paths are whitelisted module constants.

## Verification

- Pause: re-query sensor, require `paused=True` (idempotent: an already
  paused sensor returns `already_in_desired_state` without a mutation).
- Resume: re-query, require `paused=False` (same idempotency).
- Mutations must produce a semantically valid PRTG response - HTTP 200
  alone is never success. PRTG error envelopes -> VERIFICATION_FAILED.
- Read operations: structural validation (objid match, status present).

## Authentication

- PRTG username + passhash from the tenant MonitoringSource (decrypted
  per-execution). Passhash is never logged, returned, or audited.

## Errors handled

auth failure, HTTP >= 400, error envelope, malformed payload, timeout,
connection failure, no linked source (ActionNotSupported), invalid sensor
id, unknown action.

## Testing

Unit tests monkeypatch `_prtg_table` / `_mutation_request` (no network,
no real credentials): registration, reads, idempotent pause/resume,
whitelisted calls, reason whitelist, mutation failures, malformed payload,
timeout, auth failure, no-connection, credential redaction.
