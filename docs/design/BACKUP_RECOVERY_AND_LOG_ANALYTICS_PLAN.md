# Backup & Recovery Command Center + Analytics & Log Management: audit and phased plan

Status: **approved** (2026-11-03, "Approve, start BR-1"), then **extended by the full master brief**
("Enterprise Backup Center, Business Continuity, Log Analytics & Community Hub", 2026-11-03), which
supersedes the truncated brief and settles D1 (a *Protect* sidebar section; more sections as their
pages exist), D2 (write operations allowed where the provider supports them, behind confirmation,
RBAC, approval and audit) and D5. D3/D4 (KQL editor, chart library) are decided when LA-2 starts.
See section 5 for the brief's phases mapped onto this plan.

| BR-1 slice | Status |
|---|---|
| 1. Filters on every read; items by workload type; failure analysis by error code; security & compliance checks (MUA, encryption key, cross-region restore, public network access added to the collector); daily storage snapshots with growth estimate | **shipped** (`d4f9b2c6e8a1`) |
| 2. Protect navigation, overview time range, status by subscription / region, recovery-point age, clickable KPIs, Backup Instances inventory (search, filters in URL, columns, paging, CSV, drawer) | **shipped** |
| 3 (brief P2). Backup Jobs page + job detail, Vaults & Storage list + vault detail, Backup Policies + retention simulation | **shipped** |
| 4. Recovery points per item on demand (with expiry); Azure Data Protection backup vaults; Veeam job schedule/retention | next |
| 3. Azure Data Protection backup vaults (PostgreSQL flexible, blobs, disks); Reports | after 2 |
Source brief: "HSDEEP CORE AI — Enterprise Azure Backup, Log Analytics & Observability
Platform" (received truncated after *Query visualization*; sections after that are not covered).

## 1. Audit: what exists today (verified in the repo)

| Area | Exists | Where |
|---|---|---|
| Azure Backup collection (Recovery Services vaults, protected items, jobs, policies, usages) | yes, read-only, Backup Reader | `backend/app/collectors/azure_backup.py` |
| Veeam Backup & Replication collection | yes, read-only (`feat/backup-phase2`) | `backend/app/collectors/veeam_backup.py` |
| Provider-neutral backup model + shared status vocabulary | yes | `backend/app/collectors/backup_vocabulary.py`, `app/models/backup.py` |
| Backup API: overview, vaults, items, item detail, coverage, jobs, settings (per-tenant RPO) | yes, `backup.read` / `backup.write`, tenant-isolated, tested | `backend/app/api/routes/backup.py`, `app/services/backup_service.py` |
| Backup alerts (RPO, last result, protection, soft delete, repository free space) | yes, via the sensor → alert → incident path | `app/services/monitoring/backup_saver.py` |
| Backup UI: one page with tabs (items, coverage, jobs, storage), item detail, CSV export, site filter | yes | `frontend/src/app/(dashboard)/infrastructure/backup/` |
| Dashboard Backup Health row, Cloud Operations Backup Readiness | yes | `ExecutiveCommandCenter.tsx`, `cloud_operations_service.py` |
| Azure credentials (service principal, `ClientSecretCredential`, secret encrypted at rest) | yes, reusable | `backend/app/collectors/azure.py` |
| Approval workflow for state-changing actions | yes (device actions: request → approve → execute, audited) | `app/services/device_action_service.py`, `device_actions.*` permissions |
| Scheduler (APScheduler interval jobs) | yes | `backend/app/scheduler/` |
| Reports module | yes (4 reports on the page kit) | `frontend/src/app/(dashboard)/reports/` |
| Charts | hand-built page-kit components (DayColumns, DonutRing, Legend, StatTile); **no chart library** | `components/ui/PageKit.tsx`, `components/command-center/primitives` |
| Azure Log Analytics / KQL | **none** (no code, model, route or dependency) | — |
| Azure Site Recovery (replication, failover, recovery plans) | **none**. `failover_*` models are WAN/HA *network* failover, unrelated | `app/models/failover_event.py` |
| Azure Data Protection backup vaults (PostgreSQL flexible, blobs, disks, AKS) | **none**: only Recovery Services vaults are read | — |
| Backup history over time (storage growth, trends beyond job history) | **none**: storage and counts are current values only | — |
| Code editor for KQL | **none**: no Monaco/CodeMirror dependency | `frontend/package.json` |

### Gaps against the brief

1. **Data the brief asks for that Azure does not give us yet, or does not give at all**
   - *Recovery-point expiry, latest recovery point*: needs a per-item recovery points read (`.../protectedItems/{id}/recoveryPoints`): one call per item, so on demand (item detail) rather than every poll.
   - *Next scheduled backup*: not reported; computable from the policy schedule (shown as "scheduled", never as fact).
   - *RTO compliance*: Azure reports no RTO. Only **measured restore duration** from restore jobs can be shown.
   - *Storage growth and forecasting*: needs a daily snapshot table; forecasts only after enough history (≥ 14 days), labelled as estimates.
   - *Initiating user* on jobs: available on some job types only; shown when reported.
   - *Costs*: only from the Cost Management API (extra role); measured volume and estimated cost always kept apart.
2. **Write operations** (create/edit/clone/assign policy, on-demand backup, retry, restore, failover):
   today the platform is strictly read-only (Backup Reader). Writes need *Backup Contributor* /
   *Site Recovery Contributor* and must go through the existing approval workflow. This is a
   security-model change and is planned as its own phase, with its own approval.
3. **Navigation conflict**: Backup was moved *into* Infrastructure on your request; the brief
   proposes two new top-level sections with 16 + 16 pages. See decision D1.

## 2. Phased plan

Order: value first from data we already collect, then the brief's top priority (KQL), then
DR, then write operations. Each phase ships the full vertical slice (model, migration, service,
API, RBAC, tenant isolation, audit, tests, docs, UI) and is approved separately.

| Phase | Scope | New Azure access | Effort |
|---|---|---|---|
| **BR-1 Backup Command Center (read)** | Executive Overview (KPIs by workload type: VMs, databases, file shares; success rate, in progress, never backed up, RPO breaches, restore points, storage, retention compliance); filters: time range, subscription, resource group, region, storage, workload type, search; pages: Protected Workloads, Jobs (filters, detail, failure analysis by error code), Policies + Retention (read, effective retention preview, tiers), Recovery Points (per item, on demand, with expiry), Storage Analytics (daily snapshots → trends; forecast after 14 days), Security & Compliance (soft delete, immutability, multi-user authorization, encryption, redundancy), Reports. Adds Data Protection backup vaults (PostgreSQL flexible, blobs, disks). | none beyond Backup Reader | ~6–8 days |
| **LA-1 Log Analytics inventory & usage (read)** | New `azure_log_analytics` collector: workspaces (SKU, retention, region), tables (plan, retention), data collection rules, diagnostic settings coverage; measured ingestion from the `Usage` table (daily/monthly, by table, by resource where `_ResourceId` exists); health: ingestion delay vs missing vs failure (`_LogOperation`); workspace list + detail page (Overview, Usage, Health, Tables, DCRs, Retention). | Log Analytics Reader | ~6–7 days |
| **LA-2 KQL Query Studio** | Backend query proxy (`POST /api/analytics/query` → Log Analytics Query API), never from the browser; new `analytics.query` permission; server-side row/time/timeout limits; every query audited; saved queries, history, favourites, shared library (tenant-scoped tables); multi-tab editor; results table / chart / JSON / statistics; CSV export with limits; table & schema explorer. | Log Analytics Reader (same) | ~7–9 days |
| **LA-3 Cost, retention & anomalies** | Cost Management (actual billed cost where the role is granted; otherwise volume only, clearly labelled), per-table retention view, high-volume / unused table detection, daily-ingestion anomaly detection, forecast. | Cost Management Reader (optional) | ~4–5 days |
| **BR-2 Disaster Recovery (read)** | Azure Site Recovery: replicated items, replication health, RPO lag, last test failover, recovery plans; DR readiness score; Replication, DR, Recovery Plans, DR Testing pages. | Site Recovery Reader | ~5–6 days |
| **BR-3 Operations (write, behind approval)** | On-demand backup, retry failed job, policy create/edit/clone/assign with Azure capability validation, restore (to alternate location first), test failover, failover/failback. Each action: request → approval → execute → verify, dry-run where Azure supports it, audited, rate-limited. | Backup Contributor, Site Recovery Contributor (opt-in per tenant) | ~10–12 days |

Retention: Azure Backup retention (policies, restore points) and Log Analytics data retention
(workspace and table retention) stay separate models, APIs and pages, as the brief requires.

## 3. Decisions needed before BR-1

- **D1 Navigation.** (a) Two new sidebar sections, *Backup & Recovery* and *Analytics & Logs*,
  with the existing Backup page becoming the Executive Overview (recommended: the brief's scope
  no longer fits one Infrastructure tab); or (b) keep Backup under Infrastructure with sub-pages.
- **D2 Write operations (BR-3).** Approve the move beyond read-only at all, and whether it is
  opt-in per tenant (recommended) or global.
- **D3 KQL editor.** Monaco with the Kusto language service (real IntelliSense; ~3–4 MB, loaded
  only on the Query Studio page; recommended) or CodeMirror 6 (light, highlighting only).
- **D4 Charts.** Keep extending the hand-built page-kit charts (no new dependency; recommended
  for consistency) or add a chart library for the Query Studio's ad-hoc charts.
- **D5 Rest of the brief.** The pasted brief stops at *Query visualization*; send the remainder
  (likely alerts, reports, AI, deployment) so it can be folded into this plan.

## 4. Impact summary

- **Architecture**: extends the existing collector framework, neutral backup model, sensor/alert
  path, approval workflow and page kit. No duplicate services or APIs; new modules only for Log
  Analytics and Site Recovery, which have no existing code.
- **Security**: read phases need only reader roles; KQL runs server-side with limits and audit;
  write phases are isolated behind approvals and opt-in.
- **Performance**: per-item reads (recovery points) are on demand; snapshots are daily; KQL has
  server-side limits and timeouts; Log Analytics usage queries run on the collector schedule.
- **Commercial**: BR-1 + LA-1/2 give MSPs one console for backup posture and log analytics
  across customers (Azure Backup Center + Workspace Insights + Commvault-style command center
  in one product), which is the platform's positioning above existing tools.

## 5. Master brief (2026-11-03) mapped onto this plan

Priority order is the brief's (P0 → P10). "Exists" is what the repository has today, verified;
nothing in this table is claimed as tested against a real Azure tenant (all Azure tests replay
documented API shapes; see each engineering doc).

| Brief phase | Priority | Exists today | Plan phase / next step |
|---|---|---|---|
| 0 Audit, B1 stabilization | P0 | Audit in section 1; B1 + phases 1–3.1 shipped and tested; full backend suite green | done; repeat per phase |
| 1 Backup Command Center | P1 | **Partial**: KPIs (clickable), job trend over 24 h–90 d, RPO compliance, workloads, subscription / region, recovery-point age, filters, refresh | Missing: restore / replication / alert KPIs (need BR-2 and an alert view), custom date range |
| 2 Backup Instances | P1 | **Partial**: inventory page with search, filters (in URL), columns, paging, CSV, drawer | Missing: tags (not collected), bulk selection and context actions (need write phase BR-3) |
| 4 Job Controller | P2 | **Partial**: jobs page (24 h–90 d, filters, search, CSV, detail drawer with timeline, error, related jobs) | Missing: progress %, initiated-by, replication / DR jobs (BR-2), retry / cancel (BR-3) |
| 6 Vault Management | P2 | **Partial**: storage list + detail (overview, instances, policies, jobs, storage, security) | Missing: Data Protection backup vaults, private endpoints, diagnostic settings, activity log, access control |
| 7 Policies & retention | P2 | **Partial**: policy inventory, items per policy, retention simulation (tested) | Missing: create / edit / delete (BR-3), drift and comparison, governance rules (P4) |
| 3 Replicated items & DR | P3 | none (`failover_*` is network HA, unrelated) | BR-2 (Site Recovery reader); failover workflows in BR-3 behind approval |
| 5 Alerts & metrics | P4 | Platform alert engine; backup sensors raise alerts (RPO, result, protection, soft delete, free space) | Backup alert view filtered from the existing engine (no second alert system); metric rules reuse sensor thresholds |
| 8 Compliance & security | P4 | Security checks per storage location (BR-1 slice 1) | compliance rules + exceptions on top of the same checks |
| 9 Log Analytics workspaces | P5 | none | LA-1 |
| 10 KQL Query Studio | P5 | none | LA-2 |
| 11 Backup-to-logs correlation | P6 | Incident correlation engine exists for alerts | after LA-2: correlate backup jobs with Activity Log / diagnostics; facts vs. suspected cause kept apart |
| 12 Community hub | P7 | SOP engine, knowledge (`knowledge_audit`) | after P6; internal community only (never presented as Microsoft Q&A) |
| 13 Automation center | P8 | Workflows, SOP engine, device-action approvals | backup event triggers into existing workflows |
| 14 AI Copilot | P9 | AI Copilot exists | backup/log tools with evidence, RBAC-scoped |
| 15 Navigation | with each phase | Sidebar from `lib/navigation.ts` (only real pages are listed) | sections added as their pages ship |
| 16–19 Providers, tenancy, data model, tests | P10 / every phase | Provider-neutral backup model, tenant isolation and RBAC tested on every backup route | continue per phase |
