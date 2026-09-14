# HSDEEP CORE AI — Repository Audit

## 1. Repository Map

```
hsdeep-core-ai/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI entry point, middleware, router registration
│   │   ├── api/routes/                # REST API endpoints (18 route files)
│   │   ├── collectors/                # Monitoring collectors (10 vendor collectors)
│   │   ├── core/                      # auth, config, db, di, security, permissions, cache, redis, logging, metrics, entitlements
│   │   ├── models/                    # SQLAlchemy ORM models (30+ models)
│   │   ├── repositories/              # Repository pattern data access (25+ repositories)
│   │   ├── scheduler/                 # APScheduler jobs (monitoring, billing, servicenow)
│   │   ├── schemas/                   # Pydantic v2 schemas (20+ schema files)
│   │   ├── services/                  # Service layer (alerting, monitoring, priority, sop, workflow, servicenow, metering, ingester)
│   │   └── services/ai_insight_service.py
│   ├── alembic/versions/              # 20 Alembic migrations
│   ├── tests/                         # pytest test suite
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/(dashboard)/           # Next.js App Router pages
│   │   ├── components/                # React components (Sidebar, dashboard widgets, panels)
│   │   ├── hooks/                     # React data hooks
│   │   └── lib/                       # API client, utilities
│   └── package.json
├── docker/
├── docs/
├── scripts/
├── sop/                               # YAML SOP definitions
└── tests/
```

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Next.js Frontend                          │
│  Dashboard │ Alerts │ Incidents │ SOP │ Automation │ Settings    │
└───────────────────────┬─────────────────────────────────────────┘
                        │ REST API (JWT Bearer)
┌───────────────────────▼─────────────────────────────────────────┐
│                     FastAPI Backend                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │   Auth   │ │Monitoring│ │  Alerts  │ │Incidents │            │
│  │  & RBAC  │ │ Engine   │ │  Engine  │ │  Engine  │            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ Priority │ │   SOP    │ │ Workflow │ │ServiceNow│            │
│  │  Engine  │ │  Engine  │ │  Engine  │ │   Sync   │            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                         │
│  │ Billing  │ │ Metering │ │    AI    │                         │
│  │ (Stripe) │ │          │ │ Insights │                         │
│  └──────────┘ └──────────┘ └──────────┘                         │
└───────────────────────┬─────────────────────────────────────────┘
                        │ SQLAlchemy Async
┌───────────────────────▼─────────────────────────────────────────┐
│                    PostgreSQL + Alembic                          │
│  tenants │ users │ roles │ permissions │ monitoring_sources     │
│  devices │ alerts │ incidents │ sops │ runbooks │ workflows     │
│  servicenow_connections │ servicenow_incident_links │ sync_jobs │
│  plans │ subscriptions │ usage_counters │ settings              │
└─────────────────────────────────────────────────────────────────┘
```

## 3. Module Dependency Graph

```
main.py
├── api/routes/* (18 routers)
│   ├── core/auth.py (get_tenant_id, require_permission)
│   ├── core/db.py (get_db)
│   ├── repositories/* (25+ repositories)
│   ├── schemas/* (20+ schema files)
│   └── services/* (service layer)
├── scheduler/scheduler.py
│   ├── scheduler/jobs.py (monitoring tick)
│   ├── scheduler/billing_jobs.py (trial expiry, reconciliation)
│   └── scheduler/servicenow_jobs.py (sync tick)
├── services/metering/router.py
└── core/di.py (DI container)
```

## 4. API Inventory

| Prefix | Router | Endpoints |
|--------|--------|-----------|
| `/api/auth` | auth.py | login, refresh, me, users CRUD, roles |
| `/api/monitoring` | monitoring.py | sources CRUD, collectors, runs |
| `/api/alerts` | alerts.py | list, acknowledge, resolve |
| `/api/incidents` | incidents.py | list, detail, update, lifecycle |
| `/api/priorities` | priorities.py | list, override |
| `/api/dashboard` | dashboard.py | summary, widgets, AI insights |
| `/api/devices` | devices.py | CRUD, interfaces, sensors |
| `/api/infrastructure` | infrastructure.py | sites, business services, topology |
| `/api/sops` | sops.py | CRUD, versions, recommendations |
| `/api/settings` | settings.py | general, notifications, security |
| `/api/assets` | assets.py | CMDB assets, relationships |
| `/api/runbooks` | runbooks.py | CRUD, execute |
| `/api/workflows` | workflows.py | CRUD, execute, versions |
| `/api/servicenow` | servicenow.py | connections, links, jobs, webhooks |
| `/api/billing` | billing.py | plans, subscriptions, usage |
| `/api/stripe_webhook` | stripe_webhook.py | Stripe webhook receiver |
| `/health` | health.py | health check |
| `/metrics` | (inline) | Prometheus metrics |

## 5. Database Diagram (Existing Tables)

```
tenants (PK: id)
├── users (FK: tenant_id)
│   └── refresh_tokens (FK: user_id)
├── roles (FK: tenant_id) ←→ permissions (M:N via role_permissions)
│   └── user_roles (M:N)
├── sites (FK: tenant_id)
├── monitoring_sources (FK: tenant_id)
│   ├── devices (FK: source_id)
│   │   ├── interfaces (FK: device_id)
│   │   └── sensors (FK: device_id)
│   ├── events (FK: source_id)
│   ├── metrics (FK: source_id)
│   └── collector_runs (FK: source_id)
├── business_services (FK: tenant_id)
├── asset_relationships (FK: tenant_id)
├── alerts (FK: tenant_id, device_id, incident_id)
│   └── alert_history (FK: alert_id)
├── incidents (FK: tenant_id, site_id, device_id, business_service_id)
├── maintenance_windows (FK: tenant_id)
├── sops (FK: tenant_id)
│   ├── sop_versions (FK: sop_id)
│   └── runbooks (FK: sop_id, sop_version_id)
│       └── runbook_executions (FK: runbook_id)
├── workflows (FK: tenant_id)
│   └── workflow_executions (FK: workflow_id)
├── servicenow_connections (FK: tenant_id)
│   ├── servicenow_incident_links (FK: connection_id, incident_id)
│   └── servicenow_sync_jobs (FK: connection_id, incident_id)
├── plans (global, not tenant-scoped)
├── subscriptions (FK: tenant_id, plan_id)
├── usage_counters (FK: tenant_id)
├── settings (FK: tenant_id)
├── notification_settings (FK: tenant_id)
├── security_settings (FK: tenant_id)
└── api_keys (FK: tenant_id)
```

## 6. Existing Scheduler Jobs

| Job ID | Interval | Module | Purpose |
|--------|----------|--------|---------|
| `monitoring_collection_tick` | 60s | scheduler/jobs.py | Poll monitoring sources |
| `billing_trial_expiry` | 15min | scheduler/billing_jobs.py | Expire trial subscriptions |
| `billing_reconciliation_daily` | 03:00 UTC | scheduler/billing_jobs.py | Daily billing reconciliation |
| `servicenow_sync_tick` | (not registered in scheduler.py) | scheduler/servicenow_jobs.py | Drain SN sync jobs + pull tick |

**Note**: `servicenow_sync_tick` exists but is NOT registered in `start_scheduler()`. This is technical debt.

## 7. Existing Services

| Service | Module | Purpose |
|---------|--------|---------|
| AlertService | services/alert_service.py | Alert CRUD, acknowledge, resolve |
| AlertIntelligenceService | services/alert_intelligence_service.py | Alert classification |
| IncidentService | services/incident_service.py | Incident CRUD |
| IncidentLifecycleService | services/incident_lifecycle_service.py | Lifecycle transitions |
| PriorityService | services/priority_service.py | Priority scoring, overrides |
| DashboardService | services/dashboard_service.py | Dashboard aggregation |
| AuthService | services/auth_service.py | Login, token refresh, RBAC provisioning |
| BillingService | services/billing_service.py | Plans, subscriptions, Stripe |
| CollectorHealthService | services/collector_health_service.py | Collector health scoring |
| HealthScoreService | services/health_score.py | Device health scoring |
| InfrastructureService | services/infrastructure_service.py | Sites, business services |
| RunbookService | services/runbook_service.py | Runbook CRUD, execution |
| SOPService | services/sop_service.py | SOP CRUD, versions |
| WorkflowService | services/workflow_service.py | Workflow CRUD, execution |
| AutomationStatusService | services/automation_status_service.py | Automation status |
| AIInsightService | services/ai_insight_service.py | Heuristic AI insights |
| DependencyGraphService | services/dependency_graph.py | Asset topology |
| CorrelationEngine | services/alerting/correlation_engine.py | Alert correlation |
| EventClassifier | services/alerting/event_classifier.py | Alert classification |
| SeverityMapper | services/alerting/severity_mapper.py | Severity mapping |
| CollectionOrchestrator | services/monitoring/collection_orchestrator.py | Collection orchestration |
| MonitoringService | services/monitoring/monitoring_service.py | Monitoring CRUD |
| BundleSaver | services/monitoring/bundle_saver.py | Metric bundling |
| ServiceNowIncidentSyncService | services/servicenow/incident_sync.py | SN incident sync |
| ServiceNowClient | services/servicenow/client.py | SN REST API client |
| IncomingWebhookProcessor | services/servicenow/webhook_handler.py | SN webhook processing |
| PriorityEngine | services/priority/priority_engine.py | Priority scoring |
| RiskEngine | services/priority/risk_engine.py | Risk scoring |
| BusinessImpactEngine | services/priority/business_impact_engine.py | Business impact |
| ConfidenceScorer | services/priority/confidence_scorer.py | Confidence scoring |
| DecisionEngine | services/priority/decision_engine.py | Recommendations |
| SOPMatcher | services/sop/sop_matcher.py | SOP matching |
| RecommendationEngine | services/sop/recommendation_engine.py | SOP recommendations |
| WorkflowEngine | services/workflow/workflow_engine.py | Workflow execution |
| WorkflowSelector | services/workflow/workflow_selector.py | Workflow selection |

## 8. Existing Repositories

25+ repositories following the same pattern: `__init__(db: AsyncSession)`, tenant-scoped queries, soft-delete filtering.

Key repositories: AlertRepository, IncidentRepository, DeviceRepository, MonitoringSourceRepository, CollectorRunRepository, SOPRepository, RunbookRepository, WorkflowRepository, ServiceNowConnectionRepository, ServiceNowIncidentLinkRepository, ServiceNowSyncJobRepository, PlanRepository, SubscriptionRepository, UserRepository, RoleRepository, PermissionRepository, etc.

## 9. Existing UI Components

- **Sidebar.tsx**: Navigation with permission-based filtering
- **Dashboard widgets**: CoreHUD, CoreRing, MetricsCards, StatsCards, LiveFeed, TopologyMap
- **Panels**: AlertsPanel, CloudPanel, CopilotPanel, SecurityPanel, TicketPanel, ManagersGrid
- **Pages**: dashboard, infrastructure, monitoring, alerts, incidents, sop-engine, ai-copilot, automation, reports, settings, usage

## 10. Existing RBAC

**Permission Catalog** (core/permissions.py):
- monitoring.read/create/update/delete
- sites.read/write, devices.read/write, business_services.read/write
- alerts.read/acknowledge/resolve, incidents.read/write
- priorities.read/write
- sops.read/write, runbooks.read/execute, workflows.read/execute, remediation.execute
- servicenow.read/write
- dashboard.read
- usage.read, billing.read/write
- users.read/create/update/delete, roles.read/write

**Default Roles**: Owner (all), Admin (all except roles), Operator (NOC/SOC), Viewer (read-only)

## 11. Existing Billing Features

- Plan model (free/starter/pro/enterprise)
- Subscription model with Stripe integration
- Usage metering (api.call counter via middleware)
- Entitlements enforcement
- Stripe webhook processing
- Trial expiry scheduler job
- Daily reconciliation scheduler job

## 12. Existing Automation

- Runbook model (manual/semi_automated/automated execution modes)
- RunbookExecution tracking
- Workflow model with graph-based definitions
- WorkflowExecution tracking
- Node executors for workflow steps
- SOP recommendation engine

## 13. Existing SOP Engine

- SOP model with versioned SOPVersion
- Matching criteria (category, vendor, platform, device_type, site, business_service, region, min_priority)
- SOPMatcher scoring algorithm
- RecommendationEngine
- YAML SOP definitions in sop/ directory
- Execution tracking

## 14. Existing Dashboard Widgets

- CoreHUD/CoreRing (visual dashboard)
- MetricsCards, StatsCards
- AlertsPanel, LiveFeed
- TopologyMap
- CloudPanel, SecurityPanel
- CopilotPanel (AI)
- TicketPanel
- ManagersGrid

## 15. Existing AI Components

- AIInsightService: Heuristic insights (sustained_critical, flapping_sensor, collector_failing, no_data)
- PriorityEngine: P1-P4 priority scoring with reasoning
- RiskEngine: 4 risk scores (operational, business, technical, AI)
- BusinessImpactEngine: Business impact scoring
- ConfidenceScorer: Incident confidence scoring
- DecisionEngine: Recommended team, SOP, response time, escalation
- CorrelationEngine: Alert correlation and root cause analysis
- SOPMatcher: SOP recommendation matching

## 16. Existing Database Tables

30+ tables (see Database Diagram above). Key patterns:
- UUID primary keys (UUIDPKMixin)
- TimestampMixin (created_at, updated_at)
- SoftDeleteMixin (deleted_at, is_deleted)
- Tenant-scoped (tenant_id FK with CASCADE)
- JSONB for flexible fields
- Free-form strings (not enums) for extensibility

## 17. Existing Alembic Revisions

20 migrations. Current head: `1c644b59d560`

## 18. Existing API Endpoints

See API Inventory above. All endpoints are tenant-scoped via `Depends(get_tenant_id)`.

## 19. Existing Configuration

- `core/config.py`: Pydantic Settings with env vars
- JWT secret, encryption key, database URL, Redis URL
- Collector stale threshold, ServiceNow sync batch size
- CORS origins, rate limiting (60 RPM)

## 20. Technical Debt Report

1. **`servicenow_sync_tick` not registered**: The ServiceNow sync tick job exists in `scheduler/servicenow_jobs.py` but is NOT registered in `start_scheduler()` in `scheduler/scheduler.py`.
2. **ServiceNow hardcoded**: No provider abstraction — ServiceNow logic is hardcoded in models, services, and routes.
3. **No universal ticket model**: Incidents exist but no generic Ticket entity for ITSM integration.
4. **No approval engine**: Runbooks/Workflows have `requires_approval` flag but no approval workflow implementation.
5. **No autonomous remediation**: Runbook `automation_ref` is a free-form pointer with no execution engine.
6. **No knowledge base**: No knowledge article model or service.
7. **No audit event table**: Audit logging is scattered (alert_history, runbook_executions) with no unified audit trail.
8. **Placeholder implementations**: `incident_sync.py` has placeholder methods (`_build_incident_payload_from_link` returns hardcoded values).
9. **Duplicate `hashlib` import**: `client.py` imports hashlib twice (line 10 and line 17).

## Reuse / Extend / Replace Analysis

| Component | Decision | Rationale |
|-----------|----------|-----------|
| Incident model | **REUSE** | Existing model with AI fields, lifecycle, SLA |
| Alert model | **REUSE** | Core monitoring entity |
| ServiceNow client | **EXTEND** | Wrap in BaseITSMProvider adapter |
| ServiceNow models | **REUSE** | Keep for backward compatibility |
| Repository pattern | **REUSE** | Follow exact pattern for new repositories |
| Auth/RBAC | **EXTEND** | Add new permissions for ITSM, tickets, approvals, playbooks |
| Scheduler | **EXTEND** | Add new jobs for ITSM sync, approvals, executions |
| SOP/Runbook | **EXTEND** | Add Playbook model as structured extension |
| Workflow engine | **REUSE** | Existing workflow execution engine |
| Dashboard | **EXTEND** | Add new widgets, don't redesign |
| Frontend Sidebar | **EXTEND** | Add new navigation items |
| SecretCrypto | **REUSE** | Credential encryption for all ITSM providers |