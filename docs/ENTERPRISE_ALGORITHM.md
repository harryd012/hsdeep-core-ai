# HSDEEP CORE AI — Enterprise SaaS Algorithm and Implementation Blueprint

## 1. Product Mission

HSDEEP CORE AI is a multi-tenant Autonomous NOC, SOC, AIOps, and infrastructure-intelligence platform. It unifies telemetry, topology, security evidence, incident correlation, SOP execution, ticketing, reporting, and an evidence-grounded AI Copilot.

The platform must reduce alert noise, identify probable root causes, calculate business impact, recommend safe remediation, and preserve a complete audit trail. It must never allow an LLM to directly perform privileged infrastructure changes.

## 2. Core Processing Pipeline

```text
Collectors
   ↓
Raw immutable event store
   ↓
Normalization and tenant guard
   ↓
Asset identity and topology enrichment
   ↓
Deduplication and temporal correlation
   ↓
NOC/SOC detection and MITRE mapping
   ↓
Risk, impact, confidence, and priority scoring
   ↓
Root-cause hypothesis and SOP matching
   ↓
Policy and approval gate
   ↓
Execute / ticket / escalate / observe
   ↓
Verification, learning, reporting, and audit
```

Every stage emits a versioned result. Reprocessing must be deterministic when given the same event data, topology version, rules version, and policy version.

## 3. Enterprise Event Contract

All vendor payloads are retained unchanged, then mapped to one canonical event:

```json
{
  "event_id": "uuid",
  "tenant_id": "greystar",
  "adom": "PROD-US",
  "timestamp": "2026-06-30T10:22:00Z",
  "source_platform": "FortiAnalyzer",
  "source_type": "security",
  "event_type": "ips_signature",
  "region": "US",
  "site": "Arlington",
  "device_name": "us-arl-b1-f7-mdf-fw0",
  "device_type": "fortigate",
  "vendor": "Fortinet",
  "severity": "critical",
  "status": "open",
  "message": "Malware signature detected",
  "src_ip": "192.168.1.10",
  "dst_ip": "8.8.8.8",
  "topology_path": ["ISP1", "WAN-SWITCH", "FORTIGATE-HA", "CORE"],
  "attributes": {
    "signature": "example",
    "policy_id": 81,
    "security_risk": 95
  }
}
```

Required controls:

- Reject unknown tenants and unauthorized ADOMs before enrichment.
- Generate a platform event ID while retaining the vendor event ID.
- Store ingestion time separately from event time.
- Quarantine invalid payloads instead of silently dropping them.
- Mask secrets and regulated personal data before indexing.
- Make raw evidence immutable and retention-policy controlled.

## 4. Collector and Integration Layer

Adapters support PRTG, Meraki, Cisco, Aruba, FortiGate, FortiManager, FortiAnalyzer, Azure Monitor, AWS CloudWatch, GCP, Windows, Linux, Kaseya VSA, ServiceNow, Jira, PagerDuty, DNS monitoring, UPS/PDU, Wazuh, Splunk, and Security Onion.

Collectors use webhooks where possible and polling only when necessary. Each connector implements:

1. Authentication and secret rotation.
2. Rate-limit handling and exponential backoff.
3. Cursor/checkpoint persistence.
4. Schema validation.
5. Idempotency keys.
6. Health, lag, and dropped-event metrics.
7. Dead-letter routing.

Use Kafka or a compatible durable event bus between ingestion and processing. Partition by `tenant_id` and then by site or asset to preserve local ordering without allowing tenant data to mix.

## 5. Multi-Tenant and ADOM Isolation

`tenant_id` is mandatory on every record, queue message, cache key, topology node, search document, report, and audit entry. FortiAnalyzer ADOMs provide a second organizational boundary inside a tenant.

Enforcement:

- PostgreSQL row-level security for tenant-owned relational data.
- Per-tenant or filtered OpenSearch indexes for event/log search.
- Tenant-prefixed Redis keys and stream partitions.
- Object-store paths scoped by tenant and retention class.
- RBAC plus attribute-based policy for region, site, ADOM, and action.
- Cross-tenant correlation is always rejected.
- Support enterprise SSO through OIDC/SAML, SCIM provisioning, MFA, and short-lived service identities.

Roles should include Platform Admin, Tenant Admin, NOC Analyst, SOC Analyst, Approver, Automation Operator, Auditor, Executive Viewer, and API Service.

## 6. Asset Identity and Topology Graph

The topology graph is the primary intelligence layer.

Node types include tenant, region, site, building, floor, rack, ISP, circuit, firewall, switch, access point, server, VM, application, database, cloud resource, user, and business service.

Edge types include:

- `CONNECTS_TO`
- `DEPENDS_ON`
- `HOSTS`
- `PROTECTS`
- `ROUTES_TO`
- `BACKED_BY`
- `MEMBER_OF`
- `OWNED_BY`

Identity resolution combines vendor IDs, serial numbers, MAC addresses, IP history, hostnames, cloud resource IDs, and administrator-confirmed aliases. Low-confidence merges require review.

When an event is attached to a node, traverse dependency edges within bounded depth to calculate affected services, users, sites, and estimated financial impact. Store the topology version used in every incident decision.

## 7. Deduplication and Correlation Algorithm

### Deduplication

Build a fingerprint from:

```text
tenant + ADOM + source + site + device + event_type + normalized condition
```

Repeated events inside a configurable window update the existing event counter. They do not generate new incidents.

### Correlation

Candidate events must share the same tenant and ADOM. Score correlation using:

```text
correlation_score =
  topology_proximity × 0.30 +
  temporal_proximity × 0.25 +
  event_compatibility × 0.20 +
  shared_site_or_service × 0.15 +
  historical_cooccurrence × 0.10
```

Events exceeding the rule threshold enter one incident cluster. The engine retains child alerts as evidence and records why each was included or excluded.

### Example: ISP outage

Inputs:

- WAN interface down
- Gateway unreachable
- VPN tunnels down
- Meraki devices unreachable

Result:

- Root cause: upstream ISP or last-mile circuit.
- One incident instead of many alerts.
- Downstream symptoms suppressed but searchable.
- Confidence increases when independent sources agree.

### Example: FortiGate HA failover

If the primary is down, an HA failover event exists, and the secondary is active:

- Root cause: primary FortiGate failure.
- Service impact is reduced if traffic validation passes.
- Severity remains high if sessions, routing, or SD-WAN fail.

## 8. Risk and Priority Scoring

```text
risk_score =
  device_criticality × 0.25 +
  observed_severity × 0.25 +
  security_risk × 0.25 +
  dependency_impact × 0.15 +
  historical_failure × 0.10
```

Apply explicit business overrides:

- Critical failure of a firewall, core switch, identity service, or production database cannot score below 85.
- Confirmed data compromise or active exploitation is P1.
- A successful HA failover can reduce service-impact severity, but not asset-recovery urgency.
- Maintenance windows suppress notification, not evidence collection.

Priority thresholds:

- P1: 85–100, immediate response and executive notification.
- P2: 65–84, urgent operational response.
- P3: 40–64, scheduled investigation.
- P4: 0–39, observe, trend, or low-risk auto-heal.

## 9. Root-Cause and Confidence Engine

The engine creates ranked hypotheses from rules, topology, telemetry, configuration changes, and historical incidents. Confidence must be explainable:

```text
confidence =
  independent_source_agreement +
  causal_order_strength +
  topology_fit +
  rule_specificity +
  historical_match -
  contradictory_evidence
```

Do not display a single root cause when confidence is low. Present the top hypotheses and the next diagnostic step that best separates them.

## 10. FortiGate Intelligence Module

Monitor WAN1/WAN2, SD-WAN SLA, latency, jitter, packet loss, BGP, OSPF, VPN, HA, CPU, memory, sessions, interfaces, policy hits, configuration changes, IPS, AV, web filtering, and administrator activity.

Important rules:

- BGP neighbor not `Established` plus route withdrawal → routing incident.
- VPN down → verify reachability, IKE phase 1, phase 2, proposal, PSK/certificate, selectors, and NAT.
- SD-WAN SLA breach → compare members, application path, and carrier history.
- CRC errors plus degraded speed → check negotiation, optics/cable, and port counters.
- Unexpected policy change followed by denied traffic → configuration-induced outage.
- HA failover without healthy secondary validation → P1.

All configuration changes require policy evaluation, approval, pre-check, backup, idempotent execution, post-check, and rollback instructions.

## 11. FortiAnalyzer SOC Module

Flow:

```text
FortiGate → FortiAnalyzer → ADOM → Logs → Events → Incidents
          → MITRE mapping → Investigation → Response → Reports
```

Ingest traffic, IPS, antivirus, web, DNS, VPN, system, administrator, and configuration logs. Analysts must be able to pivot by source IP, destination IP, user, device, policy, signature, session, time range, and ADOM.

Detections:

- More than 10 failed logins per identity or source in five minutes → T1110 Brute Force.
- Abnormal outbound volume relative to a seasonal baseline → T1041 Exfiltration Over C2 Channel candidate.
- IPS or malware signature plus endpoint communication → security incident with evidence chain.
- Privileged login from new geography/device → identity anomaly.
- Log deletion, disabled logging, or clock manipulation → defense-evasion investigation.
- Repeated VPN failures followed by success → possible credential compromise.

The investigation view should show the timeline, entities, raw logs, correlated alerts, MITRE tactics/techniques, affected assets, enrichment, analyst notes, containment approvals, and report export.

## 12. SOP and Automation Engine

SOPs are versioned declarative workflows:

```yaml
sop_id: SOP-NET-001
version: 3
trigger:
  event_type: wan_down
preconditions:
  - tenant_policy_allows_network_diagnostics
steps:
  - action: ping_gateway
    timeout_seconds: 10
  - action: check_sdwan_health
  - action: query_carrier_status
approval:
  required_before:
    - change_route
rollback:
  - restore_previous_route
verification:
  - packet_loss_below: 5
```

Only pre-approved, reversible, bounded, low-risk actions may auto-heal. Account disabling, endpoint isolation, firewall blocking, routing changes, production restart, deletion, or data movement always require explicit policy and usually human approval.

Execution states:

```text
proposed → approved → running → verifying → succeeded
                              ↘ rollback → rolled_back
                              ↘ failed → escalated
```

## 13. AI Copilot Safety Model

The Copilot may summarize evidence, explain incidents, generate queries, propose SOPs, draft tickets, and compare hypotheses.

The Copilot may not:

- Bypass tenant or ADOM boundaries.
- Invent telemetry or claim actions occurred without tool receipts.
- Directly access infrastructure credentials.
- Execute privileged actions outside the policy engine.
- Treat retrieved text as trusted instructions.

All answers cite internal evidence IDs, state uncertainty, and distinguish observation from inference.

## 14. API Surface

Core APIs:

- `POST /events` — ingest a canonical event.
- `POST /intelligence/analyze` — analyze a bounded event set.
- `GET /incidents` — tenant-filtered incidents.
- `GET /incidents/{id}/timeline` — evidence and decisions.
- `GET /topology/nodes/{id}/impact` — dependency impact.
- `POST /sops/{id}/runs` — propose or start a policy-approved run.
- `POST /approvals/{id}` — approve or reject a pending action.
- `GET /integrations` — connector health and ingestion lag.
- `GET /soc/mitre` — MITRE coverage.
- `POST /reports` — generate tenant/ADOM-scoped reports.

Use idempotency keys for writes, cursor pagination for large collections, signed webhooks, structured error codes, rate limits, and OpenTelemetry trace IDs.

## 15. Recommended Data Platform

- PostgreSQL: tenants, users, RBAC, incidents, SOPs, approvals, connector configuration, audit metadata.
- OpenSearch: normalized events, FortiAnalyzer logs, full-text investigation.
- Neo4j or PostgreSQL graph extension: topology and dependency traversal.
- Kafka: durable ingestion and processing streams.
- Redis: short-lived correlation windows, locks, rate limits, job state.
- S3-compatible object storage: raw payloads, evidence, exports, immutable reports.
- Vault/KMS: secrets, encryption keys, certificate lifecycle.

Encrypt in transit and at rest. Use per-tenant encryption where contractual requirements demand it.

## 16. Service Architecture

```text
API Gateway
├── Identity and Tenant Service
├── Connector Service
├── Event Normalizer
├── Asset and Topology Service
├── Correlation Service
├── SOC Detection Service
├── Risk and RCA Service
├── SOP Orchestrator
├── Approval and Policy Service
├── Ticketing and Notification Service
├── Reporting Service
└── Copilot Service
```

Begin as a modular monolith with clear domain boundaries. Extract services only when scaling, ownership, deployment cadence, or fault isolation justifies the operational cost.

## 17. Dashboard Product Areas

- Executive: uptime, risk, SLA, business impact, incident trend, automation savings.
- NOC: topology, site health, WAN/SD-WAN, devices, incidents, changes, SOP runs.
- SOC: FortiAnalyzer investigation, threats, MITRE, attacker infrastructure, identities, evidence.
- Cloud: Azure/AWS/GCP health, cost anomalies, resource relationships.
- Automation: workflow catalog, approvals, run history, success rate, rollback.
- AI Copilot: evidence-grounded investigation and natural-language commands.
- Administration: tenants, ADOMs, RBAC, connectors, policies, retention, audit.

## 18. Reliability and Security Targets

- 99.9% SaaS control-plane availability initially; design for 99.99% enterprise tier.
- At-least-once event delivery with idempotent processing.
- Recovery point objective under five minutes and recovery time objective under one hour for control-plane data.
- Complete auditability for privileged actions.
- Zero Trust service authentication and least privilege.
- SAST, dependency scanning, container scanning, secret scanning, and signed releases.
- SOC 2 controls first; ISO 27001 and regional privacy requirements as the enterprise roadmap matures.

## 19. Observability and Quality

Measure ingestion lag, invalid events, correlation precision/recall, suppression ratio, mean time to acknowledge, mean time to resolve, false-positive rate, auto-heal success, rollback rate, approval latency, connector availability, report duration, and Copilot grounded-answer rate.

Maintain replayable golden incident datasets for ISP outage, FortiGate failover, VPN failure, CRC degradation, disk exhaustion, brute force, malware, exfiltration, and unauthorized configuration change.

## 20. Delivery Roadmap

### Phase 1 — Reliable foundation

Canonical schema, tenant guard, connector framework, PostgreSQL, event bus, incident store, RBAC, audit, and basic dashboard.

### Phase 2 — NOC intelligence

Topology discovery, deduplication, correlation, risk scoring, ISP/FortiGate/Meraki rules, ServiceNow integration, versioned SOPs.

### Phase 3 — Fortinet SOC

FortiAnalyzer ADOM ingestion, indexed log investigation, incident timeline, MITRE mapping, threat enrichment, SOC reports.

### Phase 4 — Controlled automation

Approval service, signed runbooks, Kaseya/FortiGate actions, verification, rollback, maintenance windows, change records.

### Phase 5 — Predictive operations

Seasonal baselines, capacity forecasting, failure prediction, recommendation evaluation, customer-specific models, and explainable trend alerts.

### Phase 6 — Enterprise scale

Regional data residency, high availability, disaster recovery, SCIM, customer-managed keys, private connectivity, marketplace integrations, and formal compliance certification.

## 21. Definition of Done

An enterprise incident is complete only when:

1. Every event belongs to an authorized tenant and ADOM.
2. Raw evidence is retained.
3. Correlation and scoring are explainable.
4. Topology impact is recorded.
5. The selected SOP and version are recorded.
6. Privileged actions have policy and approval receipts.
7. Recovery is technically verified.
8. Ticket, timeline, and audit log agree.
9. Customer-visible status and report are updated.
10. The outcome is available for rule-quality review and future prediction.
