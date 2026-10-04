# PHASE 4 — Infrastructure Graph & Dependency Intelligence

**Status: BLOCKED — NOT IMPLEMENTED. Duplicate-architecture STOP condition.**
**Date of audit:** HEAD `79b0cb5f5` (`main`)
**Predecessors:** PHASE 1 `5a7be7729`, PHASE 3 `aab1a06b5`/`9f8bb8b7e`, PHASE 3.1 `dccc5d81b`, PHASE 2 `356e02ab6`+`79b0cb5f5`

---

## 0. Why this document exists instead of an implementation

The PHASE 4 brief's own STOP conditions include:

> **7.** The repository already contains equivalent functionality that would make
> the proposed implementation duplicate architecture.
> **Do not resolve these by guessing.**

The audit below establishes that condition. **Every core capability PHASE 4 asks
for already exists in this repository**, in some cases more completely than the
brief specifies (e.g. the existing code already classifies operational vs
physical vs monitoring relationship semantics, and already detects evidence
conflicts and staleness). Implementing PHASE 4 as written would have created a
second graph model, a second traversal service, a second set of APIs and a second
topology UI — the exact duplication the brief forbids in three separate sections
("DO NOT DUPLICATE EXISTING GRAPH FUNCTIONALITY", "Do not create duplicate
tables merely because a graph view is needed", "The graph should become a
projection/index over authoritative infrastructure entities").

No code was written. Nothing was committed except this document. §22 is the
requirement-by-requirement traceability matrix; §23 lists the genuinely missing
pieces, which are refinements rather than a milestone.

## 1. Objective as specified

Build the authoritative infrastructure relationship layer, with a relationship
data model (not merely a visualization), provenance, confidence, deduplication,
lifecycle, bounded traversal, dependency paths, impact analysis (direct vs
transitive), tenant-scoped APIs, and a graph UI reusing existing navigation.

## 2. Repository audit

| Area | Finding |
|------|---------|
| Branch / HEAD | `main` @ `79b0cb5f5`, 57 ahead of `origin/main`, nothing pushed |
| Protected WIP at audit start | **22 modified + 107 untracked** — unchanged and preserved |
| Backend | FastAPI, `backend/app/{models,repositories,services,api/routes,schemas}` |
| Backend graph-related models | `resource_relationship.py`, `topology_link.py`, `asset_relationship.py`, `network_route.py`, `bgp_neighbor.py`, `interface.py`, `device.py`, `site.py`, `zone.py`, `business_service.py`, `resource_capability.py`, `monitoring_source.py` |
| Backend graph-related services | `dependency_impact_service.py` (22 KB), `relationship_evidence_service.py` (28 KB), `topology_graph_service.py` (19 KB), `topology_live_service.py` (24 KB), `topology_service.py`, `topology_standard.py`, `dependency_graph.py`, `noc_correlation.py`, + packages `services/dependency_intelligence/` (6 modules) and `services/infrastructure_intelligence/` (12 modules incl. `hsignce.py`, `network.py`, `operational_state.py`, `telemetry.py`, `vocabulary.py` 37 KB) |
| Backend graph-related routes | `topology.py` (18.5 KB), `assets.py` (11 KB), `infrastructure_intelligence.py` (20.5 KB), `infrastructure.py`, `network_intelligence.py` |
| Existing graph tests | **13 files**: `test_topology.py`, `test_topology_graph.py`, `test_topology_integration.py`, `test_topology_live_read_model.py`, `test_topology_standard.py`, `test_dependency_impact.py`, `test_dependency_integration.py`, `test_dependency_intelligence.py`, `test_relationship_evidence.py`, `test_universal_infrastructure_model.py`, `test_infrastructure_overview.py`, `test_infrastructure_intelligence_api.py`, `test_incident_affected_assets.py` |
| Existing docs | `docs/TOPOLOGY_STANDARD.md` (the topology data standard) |
| Frontend pages | `app/(dashboard)/infrastructure-topology/page.tsx` (30.4 KB), `.../live/page.tsx` |
| Frontend components | `components/topology/{TopologyCanvas,TopologyDetailDrawer,TopologyEmptyState,TopologyKpiStrip,TopologyOverlays,TopologyToolbar,TopologyVisuals,OperationalTopologyPanel}.tsx`, `components/infrastructure/CanonicalTopologyPanel.tsx`, `components/TopologyMap.tsx`, `components/dashboard/CommandCenterTopology.tsx` |
| Frontend libs | `lib/topology/{layout.ts 15.4 KB,status.ts,kpis.ts,emptyState.ts,traffic.ts,vendors.ts}`, `lib/topologyApi.ts` |

**The codebase already reasons explicitly about not duplicating itself.**
`app/services/dependency_impact_service.py` opens with a section titled
*"Why this is not a duplicate of app/services/dependency_graph.py"*, explaining
that the older Sprint-16 service traverses the legacy CMDB table
(`asset_relationships`) while the newer one traverses the canonical evidence graph
(`resource_relationships`) and reuses the canonical view rather than inventing a
parallel graph.

## 3. Entity model — already authoritative

`resource_relationships` endpoints are `devices.id`, and the canonical entity
vocabulary lives in `services/infrastructure_intelligence/vocabulary.py` (37 KB),
consumed by `infrastructure_intelligence.py` (catalog, providers, tenant
overview/health, site overview, resources, operational state, capabilities,
health, interfaces, telemetry, network, dependencies, and per-type listings for
firewalls, switches, access points, servers, cloud resources, endpoints and
storage).

Authoritative entities that already exist as real tables — no `GraphDevice`,
`GraphSite` or `GraphAsset` was created, because the brief forbids it and the
equivalents exist:

| Entity | Model | Notes |
|--------|-------|-------|
| Device (firewall/switch/router/AP/server/VM/endpoint) | `models/device.py` (10.3 KB) | tenant-scoped, `device_type`, status, site, provider |
| Site | `models/site.py` | tenant-scoped |
| Zone | `models/zone.py` | membership target |
| Interface | `models/interface.py` | `PART_OF` a device |
| Monitoring source | `models/monitoring_source.py` | provider-side entity |
| Collector | `models/collector_run.py`, `collector_credential.py` | collection plane |
| Business service | `models/business_service.py` | service-level impact |
| Network route / BGP neighbour | `models/network_route.py`, `bgp_neighbor.py` | routing/peering evidence |
| Resource capability | `models/resource_capability.py` | capability projection |

`components/infrastructure/CanonicalTopologyPanel.tsx` and `lib/topology/*`
confirm the canonical (non-fabricated) entity projection is what the UI consumes.

## 4. Relationship model — already implemented (three complementary tables)

| Model | Table | Role |
|-------|-------|------|
| **`ResourceRelationship`** | `resource_relationships` | **The canonical evidence graph.** Generic directed edge between two devices: `tenant_id`, `source_resource_id`, `target_resource_id`, `relationship_type`, `relationship_subtype`, `label`, `weight`, **`confidence`**, **`discovered_by`**, **`first_seen`/`last_seen`**, **`is_inferred`**, **`status`** (`active\|degraded\|failed\|unknown`), `attributes` JSONB — with `UniqueConstraint(tenant_id, source_resource_id, target_resource_id, relationship_type)` |
| `TopologyLink` | `topology_links` | Physical/transport bearer: interfaces, `provider`, `circuit_id`, `bandwidth_mbps`, `latency_ms`, `packet_loss_pct`, `utilization_pct`, `admin_status`/`oper_status`, `last_state_change_at`, routing/VPN attrs (`ospf`, `bgp`, `vrfs`, `vpn_name`, `redundancy_group`), `UniqueConstraint(tenant_id, device_a_id, device_b_id, name)` |
| `AssetRelationship` | `asset_relationships` | Legacy CMDB edge (device→interface/sensor/application/business_service/collector), `UniqueConstraint(tenant_id, asset_id, related_asset_id, relationship_type)` |

**PHASE 4's "Recommended conceptual fields" already exist, field for field:**
`id`, `tenant_id`, `source_entity_id` (`source_resource_id`),
`target_entity_id` (`target_resource_id`), `relationship_type`, `source`
(`discovered_by`), `confidence`, `status`, `first_observed_at` (`first_seen`),
`last_observed_at` (`last_seen`), `created_at`/`updated_at` (via
`TimestampMixin`), `metadata` (`attributes`). The brief says *"Do not blindly copy
this field list. Inspect existing models first"* — inspection shows the existing
model is already a superset.

Indexes already present cover `tenant_id`, `source_resource_id`,
`target_resource_id`, `relationship_type`, `status`, plus
`ix_topology_links_tenant_status` — matching the brief's recommended index list.

## 5. Relationship vocabulary — already canonical

`ResourceRelationship` documents 14 built-in types (`connected_to`,
`depends_on`, `protected_by`, `managed_by`, `monitored_by`, `logged_to`,
`routes_through`, `peers_with`, `hosted_on`, `contains`, `member_of`,
`backup_for`, `failover_to`, `serves`), extended by the `REL_*` vocabulary in
`infrastructure_intelligence/vocabulary.py`, and formalised in
`docs/TOPOLOGY_STANDARD.md` and `topology_standard.py`.

| Brief suggests | Already available as |
|----------------|---------------------|
| `CONNECTS_TO` | `connected_to` |
| `DEPENDS_ON` | `depends_on` |
| `HOSTS` / `RUNS_ON` / `HOSTED_ON` | `hosted_on`, `runs_on` |
| `MEMBER_OF` / `PART_OF` / `CONTAINS` | `member_of`, `contains` |
| `ROUTES_TO` / `ROUTES_THROUGH` | `routes_through` |
| `SERVES` | `serves` |
| `MONITORS` | `monitored_by` / `monitors` |
| `BELONGS_TO` / `LOCATED_AT` | containment/site membership in the canonical vocabulary |
| `PEERS_WITH` | `peers_with` |
| `ATTACHED_TO` | `attached_to` |
| `PROTECTED_BY`, `MANAGED_BY`, `LOGGED_TO`, `BACKUP_FOR`, `FAILOVER_TO`, `USES` | `protected_by`, `managed_by`, `logged_to`, `backup_for`, `failover_to` |

## 6. Provenance — already implemented

* `ResourceRelationship.discovered_by` records the evidence source
  (`manual | lldp | bgp | snmp | api | inferred`).
* `ResourceRelationship.is_inferred` marks inferred edges explicitly.
* `services/relationship_evidence_service.py` (28 KB) is a dedicated provenance
  service: it returns each relationship's **evidence sources**, classifies
  **freshness**, computes **resolution**, and **detects conflicts between
  sources** (`_freshness`, `RESOLUTION_RESOLVED`, evidence-source blocks,
  `canonical_body["freshness"]`).
* `DependencyImpactService.Hop` carries everything needed to justify a traversed
  edge; `Traversal` reports `stale`, `conflicts`, `unresolved`,
  `cycles_detected` and `truncated`.

The brief's example — *"Why does HSDEEP believe these two devices are
connected?" → LLDP / Cisco / observed timestamp* — is answerable today from
`discovered_by` + `first_seen`/`last_seen` + the evidence service.

## 7. Authoritative vs inferred — already distinguished, and stronger than the brief asks

`is_inferred` flags inferred edges, and `dependency_impact_service.py`
additionally classifies every relationship type into three semantic classes so an
inference can never silently become a service dependency:

* **OPERATIONAL** (a dependency edge): `depends_on`, `runs_on`, `hosted_on`,
  `routes_through`, `logged_to`, `backed_by`, `protected_by`, `managed_by`,
  `backup_for`, `failover_to`, `member_of`, `contains`
* **PHYSICAL** (propagates impact, never a dependency): `connected_to`,
  `attached_to`, `peers_with`, `communicates_with`
* **MONITORING** (neither): `monitored_by`, `monitors`

an unknown type is reported as `unclassified` rather than becoming a dependency,
and the module states the rule the brief calls absolute: *"Physical adjacency is
NOT a dependency"*.

## 8. Identity resolution — already implemented, without unsafe merging

Identity modules exist (`services/discovery/identity.py`,
`services/recognition/identity.py`, plus `test_resource_identity.py` and
`test_identity_resolution.py`), and the evidence service **reports conflicts
between sources instead of merging them** (`conflict` on `Hop`,
`result.conflicts`, `RESOLUTION_RESOLVED`). That is precisely the brief's
requirement ("DO NOT automatically merge … Represent the conflict"). No new
identity model was created — none is needed.

## 9. Graph ingestion — already implemented

The canonical graph is produced by the existing evidence pipelines the service
docstring names explicitly: **LLDP/CDP neighbour discovery, PNETLab links,
containment, and BGP peering**, with `materialize_relationships` as the API entry
point, and idempotency guaranteed by the models themselves:

* `TopologyLink`: *"the repository normalises (device_a, device_b)
  deterministically so re-discovery upserts idempotently."*
* `ResourceRelationship`: unique constraint on `(tenant, source, target, type)`, so
  a 5-minute LLDP scan updates `last_seen` instead of inserting duplicate rows.

## 10. Lifecycle and staleness — already implemented, reusing the project constant

The brief warns *"Do not invent a timeout value without architectural
justification"* and *"Never introduce numeric confidence … unless the system has
a documented calculation."* The existing code already obeys both:

* `ResourceRelationship.status` ∈ `active | degraded | failed | unknown`, and
  `ResourceRelationship.confidence` is a documented `0-100` column (not an
  invented percentage).
* Freshness is derived from the project's **existing** staleness constant, and the
  source says so explicitly: *"The THRESHOLD is not re-implemented:
  `V.TELEMETRY_STALE_AFTER_SECONDS` is the project's existing staleness
  constant"*, with `FRESHNESS_FRESH` / `FRESHNESS_STALE = V.COLLECTION_STALE` /
  `FRESHNESS_UNKNOWN`. A stale edge *"never becomes fresh by being reconciled"*.
* Relationship state and entity state are kept separate, as the brief requires: a
  device may be `up` while its edge is `stale`, and neither is derived from the
  other.

## 11. Graph APIs — already implemented

`/api/topology` (`api/routes/topology.py`, 18.5 KB):

| Endpoint | Purpose |
|----------|---------|
| `POST /relationships`, `PATCH/DELETE /relationships/{id}`, `GET /relationships/{id}` | relationship CRUD |
| `GET /relationships` | paginated relationship listing |
| `POST /materialize` (`materialize_relationships`) | graph materialisation entry point |
| `GET /summary` | topology summary |
| `POST /links`, `GET /links`, `GET /links/summary`, `GET /links/{id}`, `PATCH /links/{id}`, `DELETE /links/{id}` | transport-link CRUD + summary |
| **`GET /devices/{device_id}/connected`** | **direct neighbourhood** (`TopologyNeighborsOut`) |
| **`GET /devices/{device_id}/upstream`** | upstream traversal (`TopologyImpactOut`) |
| **`GET /devices/{device_id}/downstream`** | downstream traversal (`TopologyImpactOut`) |

`/api/assets` (`api/routes/assets.py`): `GET /devices/{id}/relationships`,
`POST /relationships`, `GET /business-services/{id}/health`,
`GET /tenants/{id}/health`, and the canonical graph reads
**`GET /devices/{id}/dependency-chain`**, **`/impact-analysis`**,
**`/full-graph`** (all `GraphResponse`).

`/api/infrastructure-intelligence` (20.5 KB): catalog, providers, tenant
overview/health, site overview, resource list/get, operational state,
capabilities, health, interfaces, telemetry, network, **`/dependencies`**,
per-type listings, and `POST /correlation-context` (the HSI-GNCE-facing hook).

That covers every API the brief asks for: graph overview, nodes, relationships,
neighbourhood, dependency path and impact scope.

## 12. Neighbourhood traversal — already implemented and bounded

`GET /api/topology/devices/{id}/connected` returns direct neighbours. Multi-hop
neighbourhood comes from the same `DependencyImpactService` traversal with
`max_depth`, so the brief's "default 1 or 2 hops, bounded, no uncontrolled
recursion" holds by construction — `DEFAULT_MAX_DEPTH = 3` is the documented
ceiling, with `max_nodes` as a second bound.

## 13. Dependency traversal — already implemented, with explicit semantics

`DependencyImpactService.analyze()` performs BFS over an in-memory adjacency map
built from **one bounded read** of the tenant's relationship rows:

* `_walk()` uses a `visited` set (**cycle protection**), an `arrived_via` map (so a
  bidirectional physical pair is not mistaken for a cycle), `max_depth`
  (`result.max_depth_hit`) and `max_nodes` (`result.truncated`).
* Hops are de-duplicated and sorted deterministically
  (`(depth, to_name, relationship_id)`).
* Only OPERATIONAL edges create dependencies; PHYSICAL edges propagate impact;
  MONITORING edges do neither (§7).
* Unresolved endpoints (a peer that is not a device in this tenant) are **counted
  and never traversed** (`result.unresolved`), which also prevents cross-tenant
  traversal.

## 14. Impact analysis — already implemented, direct vs transitive distinguished

`DependencyImpactService._impact_map()` plus `_needs_map()`/`_dependents_map()`
produce the impact view, and `Hop.depth` distinguishes **directly affected**
(depth 1) from **transitively affected** (depth > 1). `Traversal` reports
`stale`, `conflicts`, `unresolved`, `cycles_detected`, `truncated` and
`max_depth_hit`, so the UI can be honest about partial coverage. Exposed through
`/devices/{id}/upstream`, `/downstream`, `/impact-analysis` and already covered by
`test_dependency_impact.py` and `test_incident_affected_assets.py`.

The brief's rule *"Do not call something affected if the graph has no dependency
relationship"* is enforced by the OPERATIONAL/PHYSICAL/MONITORING classification —
site-wide impact is never inferred from a shared `site_id` alone.

## 15. Tenant isolation — already structural

Every graph read is tenant-scoped: relationships via
`self.relationships.list(tenant_id, …)`, devices via `self.devices.list(tenant_id,
…)`, BGP neighbours via `self.bgp.list_for_tenant(tenant_id, …)`. The traversal
resolves an endpoint only if it exists in the tenant's own device map
(`if other not in names: result.unresolved += 1; continue`), so an edge can never
pull a foreign node into a result. Existing coverage: `test_dependency_impact.py`,
`test_relationship_evidence.py`, `test_topology.py`, `test_tenant_isolation.py`.

## 16. Performance — already the brief's stated approach

* **No graph database** — PostgreSQL plus a relational edge table indexed on
  `tenant_id`, `source_resource_id`, `target_resource_id`, `relationship_type`,
  `status`.
* **One bounded read** builds the adjacency map; traversal is in-memory
  (*"no recursive SQL, no N+1"*).
* `MAX_FETCH_LIMIT = 100_000` bounds ingestion reads.
* `max_depth` + `max_nodes` + `visited` bound every walk.

## 17. Frontend architecture — already implemented

| Artefact | Size | Role |
|----------|------|------|
| `app/(dashboard)/infrastructure-topology/page.tsx` | 30.4 KB | the topology/graph workspace |
| `app/(dashboard)/infrastructure-topology/live/page.tsx` | 1.5 KB | live read-model view |
| `components/topology/TopologyCanvas.tsx` | — | interactive graph surface |
| `components/topology/TopologyDetailDrawer.tsx` | — | selected-entity / relationship detail |
| `components/topology/TopologyToolbar.tsx` | — | filters / depth control |
| `components/topology/TopologyKpiStrip.tsx` | — | graph KPIs |
| `components/topology/TopologyOverlays.tsx`, `TopologyVisuals.tsx` | — | state/provenance rendering |
| `components/topology/TopologyEmptyState.tsx` + `lib/topology/emptyState.ts` | 2.9 KB | the truthful empty state |
| `lib/topology/layout.ts` | 15.4 KB | deterministic layout engine |
| `lib/topology/status.ts`, `kpis.ts`, `traffic.ts`, `vendors.ts` | — | state/visual semantics |
| `lib/topologyApi.ts` | 8.2 KB | `getTopologyGraph`, `getLiveTopology` |
| `components/infrastructure/CanonicalTopologyPanel.tsx`, `components/TopologyMap.tsx`, `components/dashboard/CommandCenterTopology.tsx` | — | existing consumers |

This is the brief's recommended layout (filters / canvas / detail drawer) built on
the existing HSDEEP visual language, with an explicit empty state rather than a
decorative demo graph.

## 18. HSI-GNCE compatibility — already anticipated

`services/infrastructure_intelligence/hsignce.py` (14 KB) exists, and
`POST /api/infrastructure-intelligence/correlation-context` is already the
correlation-facing entry point. The graph is therefore not hardcoded into the
dashboard; it is a reusable service layer, as the brief requires.

## 19. Silent Agent compatibility — foundation present

Because traversal returns `Hop` justifications (evidence source, freshness,
conflicts, unresolved, cycles), the "what should I inspect next?" reasoning the
brief describes for the future Silent Agent already has the data it needs.
Autonomous remediation was correctly **not** implemented.

## 20. Existing test coverage

13 graph-related test files already exist (§2), including dedicated suites for
impact (`test_dependency_impact.py`), evidence/provenance
(`test_relationship_evidence.py`), the canonical read model
(`test_topology_live_read_model.py`, 29 KB), the standard
(`test_topology_standard.py`), the universal infrastructure model (30.9 KB) and
incident-affected assets (`test_incident_affected_assets.py`).

**No test was run for this document** because no code was changed. Running those
suites is also subject to the known environment blocker recorded in
§24.

## 21. PHASE 4 acceptance-criteria traceability

| PHASE 4 criterion | Status | Existing artefact |
|-------------------|--------|-------------------|
| Existing infrastructure models audited | DONE | §2–§3 |
| Existing topology/relationship functionality audited | DONE | §2, §4, §11 |
| No duplicate graph entity model created | HELD | nothing created |
| Authoritative entities identified | DONE | §3 |
| Relationship model implemented | ALREADY | `resource_relationships` (+ `topology_links`, `asset_relationships`) |
| Relationship vocabulary documented | ALREADY | `docs/TOPOLOGY_STANDARD.md`, `topology_standard.py`, `infrastructure_intelligence/vocabulary.py` |
| Relationship provenance implemented | ALREADY | `discovered_by`, `is_inferred`, `relationship_evidence_service.py` |
| Authoritative vs inferred distinguished | ALREADY | `is_inferred` + OPERATIONAL/PHYSICAL/MONITORING classification |
| Relationship deduplication implemented | ALREADY | unique constraints + deterministic idempotent upserts |
| Relationship lifecycle implemented/documented | ALREADY | `status` + `freshness` reusing `V.TELEMETRY_STALE_AFTER_SECONDS` |
| `last_observed` supported | ALREADY | `first_seen` / `last_seen` |
| Tenant isolation implemented | ALREADY | tenant-scoped reads + endpoint resolution guard |
| Identity model reused | ALREADY | `discovery/identity.py`, `recognition/identity.py` |
| No IP-only identity assumptions | ALREADY | identity resolved to `devices.id`; IP is never the key |
| Graph ingestion idempotent | ALREADY | model-level uniqueness, `materialize_relationships` |
| Graph API implemented | ALREADY | §11 |
| Neighbourhood query implemented | ALREADY | `/devices/{id}/connected` |
| Bounded traversal implemented | ALREADY | `max_depth`/`max_nodes` |
| Cycle protection implemented | ALREADY | `visited` + `arrived_via` + `cycles_detected` |
| Dependency path implemented | ALREADY | `/devices/{id}/dependency-chain` |
| Impact analysis implemented | ALREADY | `DependencyImpactService`, `/impact-analysis` |
| Direct vs transitive impact distinguished | ALREADY | `Hop.depth` |
| No fake topology / fabricated relationships | ALREADY | §7, §9 |
| No fabricated graph coverage | ALREADY | counts from real aggregates; no invented percentages |
| Graph empty state works | ALREADY | `TopologyEmptyState` + `lib/topology/emptyState.ts` |
| Graph UI implemented (+ node/edge selection, details, dependency, impact views) | ALREADY | §17 |
| Existing dashboard / incident workspace / AI Copilot preserved | UNTOUCHED | no code changed |
| No frontend N+1 / unbounded traversal | ALREADY | §16 |
| TypeScript / ESLint / build | NOT APPLICABLE | no code changed (PHASE 2 left them green) |
| Documentation created | DONE | this document |
| Protected WIP preserved | PRESERVED | 22 modified + 107 untracked, unchanged |

## 22. Genuine gaps (the only remaining work)

The audit found **no PHASE 4 capability that is missing**. What remains are small
verification/wiring items, listed here rather than assumed:

1. **Unified graph-overview payload (needs verification, small).** Two summaries
   exist (`GET /api/topology/summary`, `GET /api/topology/links/summary`). Whether
   a single response reports *node-type* counts, *relationship-type* counts,
   last-update time and contributing sources across all canonical entities was
   **not verified in this audit** — `TopologySummary` is defined in the schemas
   layer, which was not opened. If it does not, the fix is one aggregate
   repository method plus one field block on the existing endpoint; it is *not* a
   new subsystem.
2. **Graph links from the Phase 2 dashboard cards.** PHASE 2 explicitly deferred
   "view infrastructure context" links because they require new fields on
   `AttentionRequiredData`/`AttentionEvidence`, i.e. edits to
   `dashboardModels.ts` + `dashboardDataAdapter.ts`, which are **protected WIP**.
   The destination already exists (`/infrastructure-topology`); only the link is
   missing.
3. **Per-source and per-incident graph entry points** — `test_incident_affected_assets.py`
   and `/api/assets/devices/{id}/impact-analysis` indicate incident→graph context
   largely exists; whether the PHASE 3 investigation workspace links to it should
   be verified before adding anything.
4. **Relationship write-audit trail.** The brief's auditability section allows
   reuse of existing audit architecture; `discovered_by` + `first_seen`/`last_seen`
   cover observation provenance, but whether every write emits an audit record was
   not verified.

Each item is a *verification-then-small-change*, not a milestone. **None of them
justify building a second graph model, traversal service, API surface or UI.**

## 23. Recommendation

Treat PHASE 4 as **already delivered by prior work** (this is the honest reading;
the brief's own STOP condition #7 applies), and replace the milestone with a small
**"graph integration & coverage"** pass that only:

* verifies/extends the single graph-overview payload (§22.1);
* wires the existing `/infrastructure-topology` destination into the dashboard and
  incident surfaces (§22.2–22.3), which requires resolving the protected-WIP
  conflict on the dashboard adapter **first**; and
* confirms the relationship write-audit path (§22.4).

That work is measured in tens of lines of frontend wiring plus at most one
repository method — and it must not start until the protected dashboard files are
free, because a PHASE 4 graph link added there would entangle this milestone with
another session's uncommitted work exactly as happened in PHASE 2.

## 24. Limitations and blockers of this audit

* **No code was written, no test was run**, because nothing needed implementing.
  The 13 existing graph suites were not executed; the environment blocker recorded
  in PHASE 3.1 (tenant teardown fanning out to ~77 cascading FKs over multi-GB
  `incidents`/`alert_history`/`metrics` tables) still applies and can slow any
  suite that touches `incidents`.
* **Not verified in this audit:** the `TopologySummary` schema (§22.1), the
  `schemas/` layer generally, `repositories/resource_relationship_repository.py`
  internals, and every frontend page's runtime behaviour. Claims above are limited
  to what was read.
* **Protected WIP:** 22 modified files + 107 untracked files were present before
  this milestone and are unchanged. This document is the only new file.
* **Documentation:** `docs/TOPOLOGY_STANDARD.md` and this repository's Phase
  9.4–9.11 service docstrings already document the topology standard; this
  document does not restate them.




