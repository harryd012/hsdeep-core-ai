# Deployment Model (Section 19: Online and Offline Appliance Architecture)

Reference implementation: `backend/app/core/deployment.py`.

## Requirement

The platform's core monitoring and IT operations capabilities must run in
**disconnected** environments. Cloud connectivity is optional, not assumed —
many customers will not permit transmitting infrastructure or security data
to a cloud. Every mode offers a specific posture.

## Offline appliance requirements (offline / air-gapped / hybrid / on-prem)

All of these run locally and do **not** require constant cloud connectivity:

- Local web interface
- Local authentication
- Local database
- Local monitoring collectors
- Local event processing
- Local alerting
- Local topology discovery
- Local AI models (Ollama-based, where supported)
- Local runbooks / knowledge articles
- Local audit logs
- Local reporting
- Local backup & restore
- Secure export / import

## Online deployment requirements (SaaS control plane)

- Central multi-tenant management
- Central billing
- Central subscription enforcement
- Cloud-based AI services where configured
- Centralized fleet management
- Remote collector management
- Centralized audit & reporting
- Secure tenant-scoped data isolation

## Hybrid deployment requirements

- **Local collectors** at customer sites
- **Secure outbound-only** communication (no inbound firewall exposure)
- **Local buffering** during connectivity loss
- **Retry & replay** of buffered events
- **Duplicate-event prevention** (idempotent sync)
- **Synchronization-status** visibility
- **Conflict resolution**
- **Tenant-scoped** synchronization

## Configuration options

The platform must be configurable for all of the following, per customer:

1. Fully cloud-connected
2. Private cloud
3. On-premises
4. Offline
5. Air-gapped
6. Hybrid
7. Customer-managed AI provider
8. Local-only AI processing

## How code represents modes

`core/deployment.py` exposes:

- `MODES` — the 8-mode catalogue (each a `DeploymentMode` dataclass with
  local_ui/local_auth/local_db/local_collectors/local_alerting/local_ai,
  cloud_sync_allowed, requires_cloud, sync_transport, capabilities).
- `get_mode(key)`, `offline_eligible(key)`, `sync_behaviour(mode_key)`.

These are consumed by configuration tooling and (future) the sync scheduler to
decide whether a job may be emitted to the cloud. They are deliberately pure
(stdlib-only) and unit-tested in `tests/test_deployment_model.py`.