# PHASE 4B — Graph Integration & Coverage

**Status:** implemented (one gap closed, one verified already complete)
**Milestone:** PHASE 4B (follows the PHASE 4 audit `7eb25e3bd`, which was correctly BLOCKED)
**Branch:** `main`

PHASE 4 was blocked because the repository already contained a mature
infrastructure graph. PHASE 4B therefore **reuses, extends and integrates** - it
builds no second graph, no second relationship model, no second traversal service,
no second API and no second topology UI. Exactly one genuine gap was found and
closed (§8); the other open question turned out to be already answered by an
existing endpoint (§7).

---

## 1. Existing graph architecture

| Layer | Artefacts |
|-------|-----------|
| Models | `resource_relationships` (`ResourceRelationship`) — the canonical evidence graph; `topology_links` (`TopologyLink`) — physical/transport bearers; `asset_relationships` (`AssetRelationship`) — legacy CMDB edge; plus `Device`, `Site`, `Zone`, `Interface`, `MonitoringSource`, `NetworkRoute`, `BgpNeighbor`, `ResourceCapability` |
| Services | `dependency_impact_service.py` (22 KB — bounded impact/dependency traversal), `relationship_evidence_service.py` (28 KB — provenance/freshness/conflict/materialisation), `topology_graph_service.py` (19 KB), `topology_live_service.py` (24 KB), `topology_service.py`, `topology_standard.py`, `dependency_graph.py`; packages `services/dependency_intelligence/` and `services/infrastructure_intelligence/` (incl. `hsignce.py`, `vocabulary.py` 37 KB) |
| APIs | `/api/topology/*`, `/api/assets/*`, `/api/infrastructure-intelligence/*`, `/api/infrastructure/topology/graph(+ /live)` |
| UI | `(dashboard)/infrastructure-topology` + `/live`, `components/topology/{TopologyCanvas,TopologyDetailDrawer,TopologyToolbar,TopologyKpiStrip,TopologyOverlays,TopologyVisuals,TopologyEmptyState}`, `lib/topology/{layout,status,kpis,emptyState,traffic,vendors}`, `lib/topologyApi.ts` |
| Tests | 13 graph-related suites (`test_topology*.py`, `test_dependency_*.py`, `test_relationship_evidence.py`, `test_universal_infrastructure_model.py`, …) |
| Docs | `docs/TOPOLOGY_STANDARD.md` |

## 2. Why no duplicate graph was created

PHASE 4's own STOP condition 7 (*"the repository already contains equivalent
functionality that would make the proposed implementation duplicate
architecture"*) applied, and the brief repeats the prohibition in three separate
sections. The codebase already reasons about this explicitly —
`dependency_impact_service.py` opens with a section titled *"Why this is not a
duplicate of app/services/dependency_graph.py"*.

Creating `GraphNode`/`GraphEdge`/`GraphDevice`, a second traversal, a `/api/graph`
prefix or a `GraphPage.tsx` would have produced a rival source of truth for the
same relationships. Nothing of the sort was added.

## 3. Existing models reused (nothing new)

`ResourceRelationship` already carries every field PHASE 4 asked for: `tenant_id`,
`source_resource_id`, `target_resource_id`, `relationship_type`,
`relationship_subtype`, `label`, `weight`, `confidence`, `discovered_by`,
`first_seen`, `last_seen`, `is_inferred`, `status`
(`active|degraded|failed|unknown`), `attributes` JSONB — with
`UniqueConstraint(tenant_id, source_resource_id, target_resource_id,
relationship_type)` giving deterministic deduplication and idempotent refresh
(`upsert()` updates `last_seen`/`confidence`/`status` instead of inserting a
duplicate). Indexes exist on `tenant_id`, `source`, `target`,
`relationship_type`, `status`. **No model was added and no migration was needed.**

## 4. Existing services reused (nothing new)

`DependencyImpactService` (bounded BFS: `max_depth`, `max_nodes`, `visited`,
`arrived_via`, de-duplication, stable ordering, OPERATIONAL vs PHYSICAL vs
MONITORING edge semantics), `RelationshipEvidenceService` (evidence sources,
freshness via the project's existing `V.TELEMETRY_STALE_AFTER_SECONDS`, resolution,
conflict detection, `materialize`), `TopologyGraphService`, `TopologyLiveService`,
`TopologyService`. **No service was added.**

## 5. Existing APIs reused (nothing new)

`/api/topology` (relationships CRUD, `materialize`, `summary`, links CRUD,
`connected`/`upstream`/`downstream`), `/api/assets` (`dependency-chain`,
`impact-analysis`, `full-graph`, relationships, business-service & tenant health),
`/api/infrastructure-intelligence` (resources, dependencies, operational state,
telemetry, network, per-type listings, `correlation-context`). **No new endpoint
and no `/api/graph` was created.**

## 6. Existing UI reused (nothing new)

The topology workspace, canvas, toolbar, detail drawer, KPI strip, overlays, the
deterministic layout engine and the truthful empty state are all reused as-is.
There is a **truthful status model** in the client today:
`TopologyGraphStatus = "ready" | "partial" | "not_discovered"`, a `limitations[]`
array, and `TopologyGraphEdge` already types `discovered_by`, `is_inferred`,
`confidence`, `status` and `unknown_relationship_type`. The file's own comment
records the rule: *"when none exist the response reports `status:
\"not_discovered\"` rather than inventing edges from subnets, hostnames or device
types"*.

## 7. Question 1 — Graph overview contract: **VERIFIED, no change made**

The PHASE 4 audit raised this because `TopologySummary`
(`schemas/topology.py:117`, served by `GET /api/topology/summary`) exposes only
`total_relationships`, `by_type` and `by_status` — no node count, no node-type
counts, no last-update.

Verification against the **endpoint the workspace actually consumes** closes the
question. `lib/topologyApi.ts` reads `GET /api/infrastructure/topology/graph`, and
its `TopologyGraph.summary` already returns:

| PHASE 4 asked for | Already provided by |
|-------------------|---------------------|
| node count | `summary.nodes_total` |
| relationship count | `summary.edges_total` |
| node-type counts | `summary.devices_total`, `sites_total`, `monitoring_sources_total`, `interfaces_total`, `sensors_total`; plus `by_type`/`by_family`/`by_provider` on `/api/infrastructure-intelligence/tenant/overview` |
| last graph update | `generated_at`, plus `view_freshness.last_telemetry_at` from `TopologyLiveService` for telemetry age |
| relationship-type counts | `/api/topology/summary` → `by_type` |
| graph completeness honesty | `status` (`ready\|partial\|not_discovered`) + `limitations[]` |

**Conclusion: the contract is complete.** Adding a `node_count` to
`TopologySummary` would now *duplicate* `nodes_total` on a second endpoint, which
the brief forbids ("If an existing endpoint already exposes the required data:
REUSE IT"). **No field was added.** The earlier "gap" was an artefact of reading
`/api/topology/summary` in isolation.

## 8. Question 2 — Relationship write audit: **GAP FOUND AND CLOSED**

### 8.1 What was verified

* **Row-level provenance already present** (unchanged, and not "fixed"): every edge
  records `discovered_by`, `is_inferred`, `confidence`, `first_seen`, `last_seen`,
  `status`.
* **No graph write was audited.** `api/routes/topology.py`, `assets.py`,
  `infrastructure.py` and `infrastructure_intelligence.py` contained **zero**
  references to `AuditService`; only admin/auth/billing/monitoring routes used it.

### 8.2 The change (reusing the existing framework only)

`AuditService` (`services/audit_service.py`) and its `audit_events` table
(`models/knowledge_audit.py`) were reused exactly as-is. Four write paths in
`api/routes/topology.py` now record an event **before** their existing
`db.commit()`, matching the established convention in
`admin_custom_properties.py:216-231`:

| Action | Path | Recorded values |
|--------|------|-----------------|
| `topology_relationship.created` | `POST /relationships` (first write) | `new_values` = the edge's stored fields |
| `topology_relationship.refreshed` | `POST /relationships` (idempotent re-discovery) | `new_values` = the edge's stored fields |
| `topology_relationship.updated` | `PATCH /relationships/{id}` | **`old_values` + `new_values`** (captured before the mutation) |
| `topology_relationship.deleted` | `DELETE /relationships/{id}` | `old_values` of the soft-deleted edge |
| `topology_relationships.materialized` | `POST /relationships/materialize` | the report's real counts (`dry_run`, `planned_total`, `written_total`, `refreshed_total`, `unresolved_total`, `skipped_total`) |

A create and an idempotent refresh are **distinct actions** on purpose: the unique
constraint means a repeated discovery scan must not look like a second creation.
Reads are deliberately **not** audited (asserted by test).

Every audited value is copied from a stored column by one helper,
`_relationship_audit_values(edge)` — the audit can never assert more than the row
does, and no secret is ever recorded.

### 8.3 A real defect the tests found — and the fix

The first run of the new tests failed:

```
ForeignKeyViolationError: insert or update on table "audit_events"
  violates foreign key constraint "audit_events_actor_id_fkey"
  DETAIL: Key (actor_id)=(7ed93a90-…) is not present in table "users".
→ PendingRollbackError  →  HTTP 500
```

`audit_events.actor_id` is FK-bound to `users.id`, but
`core.auth.get_current_user` resolves the principal **from its JWT claims with no
DB round-trip** (`auth.py:382-394`, "fast path - no DB round-trip"). A principal's
`user_id` is therefore *not* guaranteed to be a real row — a deleted user, an MSP
tenant override or an API-key principal can name a user that does not exist. And
because a failed flush inside the audit SAVEPOINT still leaves the ORM object
pending, that failure **poisoned the whole transaction**: the naive integration
would have turned graph writes into 500s in that situation, i.e. it would have
*regressed* previously-working writes.

Fix — contained at the call site, shared behaviour untouched: the actor is
resolved through the **existing** `UserRepository.get_by_id(user_id, tenant_id)`
before it is used as a foreign key, and the raw claim is preserved in the event
context:

* actor row exists in this tenant → `actor_id` = that id (the normal case);
* actor row absent → `actor_id` = `None`, `actor_email` still recorded, and
  `context["actor_user_id"]` retains the claim for traceability.

Cost: one indexed primary-key lookup per graph **write** request (only when an
audit event is written; `materialize` writes one event per call). Read paths gain
nothing.

> The underlying fragility in `AuditService.record()`'s "best-effort" guarantee is
> **pre-existing** and also affects the admin routes that pass an unvalidated
> `actor_id`. It is recorded in §16 rather than changed, because altering shared
> audit semantics for every caller is outside PHASE 4B's scope.

## 9. Question 3 — Integration points: **already present; nothing new required**

| Consumer | Existing integration |
|----------|---------------------|
| **Command Center** | `components/dashboard/CommandCenterTopology.tsx` renders the graph; PHASE 2 made degraded devices link to `/infrastructure/assets/{deviceId}` |
| **Incident Workspace** | **already graph-driven** — `components/investigation/InvestigationWorkspace.tsx` calls `getDeviceImpactAnalysis()` → `GraphResponse` and renders the graph/impact (blast-radius) section, with an explicit `unavailableReason` when the incident is not device-scoped (`:241-259`, `:321-329`) |
| **Infrastructure** | `(dashboard)/infrastructure-topology` workspace + `CanonicalTopologyPanel` |
| **Network Intelligence** | `components/network-intelligence/NetworkIntelligenceWidget` on the dashboard |
| **Topology** | the workspace, canvas, drawer, toolbar and the `/live` read model |
| **AI Copilot** | `POST /api/infrastructure-intelligence/correlation-context` is the existing hook for graph context; the Copilot was **not** modified |

There is therefore **no verified integration gap**, so no UI file was touched.
A "view infrastructure context" link from the incident workspace would be a
nice-to-have rather than a gap-fill, and its natural target
(`infrastructure-topology/page.tsx`) is **protected uncommitted WIP** owned by
another session — editing it would breach the milestone's ownership rule.

## 10. Impact analysis — reused unchanged

`DependencyImpactService._impact_map()` / `_needs_map()` / `_dependents_map()`
provide impact, exposed through `/api/topology/devices/{id}/upstream|downstream`
and `/api/assets/devices/{id}/impact-analysis`. `Hop.depth` keeps **directly
affected** (depth 1) distinct from **transitively affected** (depth > 1), and
`Traversal` reports `stale`, `conflicts`, `unresolved`, `cycles_detected`,
`truncated` and `max_depth_hit` so a partial view can say so. No semantics were
changed: OPERATIONAL edges create dependencies, PHYSICAL edges propagate impact
only, MONITORING edges do neither, and an unknown type is reported `unclassified`
rather than becoming a dependency.

## 11. Dependency traversal — reused unchanged

Bounded, deterministic BFS over a single bounded read of the tenant's relationship
rows: `max_depth` (documented `DEFAULT_MAX_DEPTH = 3`), `max_nodes`, a `visited`
set, an `arrived_via` guard, de-duplicated hops and a stable sort order. No second
traversal, no recursive SQL, no N+1, no change to the depth default.

## 12. Evidence / provenance — preserved and reused

* Per edge: `discovered_by`, `is_inferred`, `confidence`, `first_seen`,
  `last_seen`, `status` — untouched.
* Via `RelationshipEvidenceService`: evidence sources, freshness
  (`FRESHNESS_FRESH` / `FRESHNESS_STALE = V.COLLECTION_STALE` /
  `FRESHNESS_UNKNOWN`, reusing the platform's existing staleness constant rather
  than inventing a timeout) and conflict detection, so two disagreeing sources are
  **reported, not silently merged**.
* The new audit events **add** an accountability trail around writes; they do not
  replace or reinterpret the evidence fields. Nothing elevates inferred data to
  authoritative.

## 13. Tenant isolation — preserved

* Reads stay tenant-scoped (`relationships.list(tenant_id…)`,
  `devices.list(tenant_id…)`, `bgp.list_for_tenant(tenant_id…)`) and traversal
  still refuses to resolve an endpoint outside the tenant's own device map.
* The new audit rows are written with the request's own tenant and read back
  tenant-scoped (`test_graph_audit_is_tenant_scoped` asserts that a tenant which
  performed no graph write sees none of them).
* The new actor lookup uses `UserRepository.get_by_id(user_id, tenant_id)` — it is
  **tenant-scoped**, so a foreign user id cannot be recorded as the actor of this
  tenant's graph write.

## 14. Performance

* No new endpoint, no new query on any read path, no polling change and no
  frontend change → no N+1, no unbounded traversal, no payload growth.
* One additional indexed primary-key lookup (`users.id`) per graph **write**
  request, and one audit INSERT per write (bounded: `materialize` records a single
  event per call, not per written edge).
* Traversal bounds unchanged (`max_depth`, `max_nodes`).

## 15. Tests

**Baseline recorded before any change:** `22 modified + 107 untracked`, 0 staged —
unchanged afterwards except for this milestone's own files.

**New suite** — `backend/tests/test_topology.py::TestRelationshipWriteAudit`
(7 tests, added to the existing tracked suite; no existing test or assertion was
weakened):

| Test | Asserts |
|------|---------|
| `test_create_and_refresh_are_audited_distinctly` | one edge survives a repeated POST; `created` and `refreshed` are separate audited actions; `actor_id`/`actor_email` recorded; `new_values` copied from the row |
| `test_reads_are_not_audited` | list/get/summary/connected/upstream produce **no** new audit event |
| `test_update_records_old_and_new_values` | `PATCH` records a real before/after pair |
| `test_delete_records_the_previous_values` | `DELETE` preserves the soft-deleted edge's values |
| `test_materialize_dry_run_is_audited_and_writes_nothing` | the dry run is audited with `written_total == 0` |
| `test_graph_audit_is_tenant_scoped` | graph audit rows belong to the acting tenant only |
| `test_unknown_actor_does_not_break_the_write` | **regression guard for §8.3** — an actor with no `users` row still gets a 201, with `actor_id IS NULL` and the claim preserved in `context` |

**Direct API verification (executed, cascade-free):** the same scenarios were
driven through the real ASGI app with a fast cleanup path:

```
TOTAL: 20 passed, 0 failed
```

including `POST -> 201`, `re-POST refreshes the SAME edge`,
`reads write NO audit event (3 -> 3)`, `write by an UNKNOWN actor still succeeds
(no 500)`, all four actions + the materialisation audited, `actor_id recorded for a
REAL user`, `update records a real old/new pair (old=active new=degraded)`,
`materialize records real counts (dry run wrote nothing)` and
`graph audit is tenant-scoped`.

**`pytest` result:**

```
$ backend/.venv/Scripts/python.exe -m pytest tests/test_topology.py -k TestRelationshipWriteAudit -q
.......                                                                  [100%]
7 passed, 16 deselected, 7 warnings in 83.82s (0:01:23)
```

All **7** passed, 0 failed, 0 errors.

**Regression (the whole file, pre-existing tests included):**

```
$ backend/.venv/Scripts/python.exe -m pytest tests/test_topology.py -q
23 passed, 13 warnings in 47.34s
```

All 23 tests pass, so the three pre-existing classes
(`TestRelationshipRepository`, `TestTopologyServiceTraversal`, `TestTopologyApi`)
are unbroken by the audit changes — no existing behaviour regressed. The 13
warnings are a single pre-existing `httpx` deprecation about the `app=` shortcut
(used by the suite's existing style), not a defect introduced here.

### 15.1 Repro note for the shared dev database

Earlier attempts at this same command were blocked, not failed: each test's
teardown spends minutes inside the `tenant` fixture's `DELETE FROM tenants`, which
cascades across the ~77 tenant-referencing foreign keys documented in PHASE 3.1
(including the 5.2 GB `incidents`, 1.78 GB `alert_history` and 1.2 GB `metrics`
tables). While a concurrent session was scanning `incidents` (39 s per statement),
`pg_stat_activity` showed the runtime dominated by `DELETE FROM tenants` (103 s and
rising) with no test failing. Once the database was quiet the identical command
completed in 83.82 s.

For anyone re-running during contention, the direct verification described in §15
covers the same assertions through the same ASGI code path with a cascade-free
cleanup (child rows deleted explicitly, tenant soft-deleted) and finished 20/20.


## 16. Limitations

1. **`AuditService.record()`'s best-effort guarantee is incomplete.** A failed audit
   flush inside its SAVEPOINT can still leave the caller's session needing a
   rollback (observed in §8.3). PHASE 4B works around this at the graph call sites
   by never handing it an unvalidated FK. The same latent fragility affects other
   callers that pass an unvalidated `actor_id` (admin routes). Recommended
   follow-up — deliberately **not** done here, to avoid changing shared audit
   semantics for every caller: `record()` should `expunge` the pending event or
   write through an independent session.
2. **Topology-link writes are not audited.** `POST/PATCH/DELETE /topology/links`
   concern transport bearers, which carry their own `discovered_by` and state;
   question 2 asked specifically about *relationship* write paths, so links were
   left unchanged.
3. **`AssetRelationship` (legacy CMDB) writes are not audited** — a different table
   from the canonical graph.
4. **No UI change was made** (§9): the integration already exists and the candidate
   targets are protected WIP, so the incident→graph journey still ends at the
   rendered impact section rather than linking out to the topology workspace.
5. **Verification scope:** claims here are limited to the files actually read. The
   whole `schemas/` layer, `topology_link_repository.py` and every frontend page's
   runtime behaviour were not exhaustively inspected.
6. **One soft-deleted test tenant** remains from the direct verification (the
   cascade-free cleanup soft-deletes rather than hard-deletes). It is invisible to
   active queries, unlike the ~2 229 leftover tenants already recorded in PHASE 3.1.

## 17. Future HSI-GNCE integration

Already anticipated and **not duplicated**: `services/infrastructure_intelligence/
hsignce.py` exists, and `POST /api/infrastructure-intelligence/correlation-context`
is the existing entry point for correlation context. PHASE 4B adds nothing here —
the graph remains a reusable service layer rather than logic baked into the
dashboard, and the new audit trail plus the per-edge provenance already give future
correlation a "why does HSDEEP believe this edge exists?" answer.

## 18. Future Silent Agent integration

The foundation is present and unchanged: traversal returns per-hop justification
(evidence source, freshness, conflicts, unresolved endpoints, cycles) — exactly the
input an agent needs to answer "what should I inspect next?". No autonomous
behaviour, planning, execution or remediation was implemented; those remain
PHASE 8+.

