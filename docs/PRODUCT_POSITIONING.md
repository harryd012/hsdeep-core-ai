# HSDEEP CORE AI — Product Positioning (Section 13)

## Positioning statement

HSDEEP CORE AI is **an AI-native IT operations platform for intelligent
observability, multi-agent orchestration, ITSM automation, and enterprise
infrastructure intelligence.**

It is deliberately **not** "just a dashboard". It is an operational platform
that **discovers, observes, analyzes, automates, and manages** enterprise
infrastructure end-to-end — network, security, compute, storage, wireless,
cloud, and facilities — as a single topology-aware, tenant-scoped model.

## Deployment models

The same core product supports every deployment shape the customer needs:

| Mode                        | Code key            | Cloud required | Synced to cloud |
|-----------------------------|---------------------|----------------|-----------------|
| SaaS (central control plane)| `saas`              | Yes            | n/a (is cloud)  |
| Private cloud               | `private_cloud`     | No             | Outbound HTTPS  |
| On-premises                 | `on_premises`       | No             | No              |
| Customer-hosted (BYOC)      | `customer_hosted`   | No             | Outbound HTTPS  |
| Offline appliance           | `offline`           | No             | Optional/delayed|
| Air-gapped appliance        | `air_gapped`        | No             | No (never)      |
| Hybrid (local + sync)       | `hybrid`            | No             | Outbound HTTPS  |
| Edge collector appliance    | `edge`              | No             | Outbound HTTPS  |

Implemented model:
`backend/app/core/deployment.py` (pure catalogue) — see `docs/DEPLOYMENT_MODEL.md`.

## Target markets

Same core product, sized by subscription:

- Small & medium businesses
- Managed Service Providers (MSPs) / Managed Security Service Providers (MSSPs)
- Medium-to-large enterprises

Geographies: **US, Canada, Europe, UK, APAC, Middle East, global enterprise**.

## Not "just a dashboard"

The platform provides, in addition to the topology dashboard:

1. **Discovery** — SNMP/SSH/API/cloud/agentless collectors build the live inventory.
2. **Observation** — health, metrics, sensors, flow/telemetry ingestion.
3. **Analysis** — AI agents correlate events, compute service impact, root-cause,
   redundant-path and capacity analysis.
4. **Automation** — runbooks/SOPs with human approval gates for high-risk changes.
5. **Management** — ITSM tickets, SLA, change requests, subscription/entitlement
   enforcement, offline-first appliance operation.

See `docs/SECTION_13_23_IMPLEMENTATION.md` for the acceptance-criteria mapping.