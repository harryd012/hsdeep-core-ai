# Live Infrastructure Intelligence — Command Center zone

**Status:** implemented (frontend integration only)
**Branch:** `main`
**Placement:** between ATTENTION REQUIRED and OPERATIONAL HEALTH on `/dashboard`

The Command Center had a large unused area between the attention counters and the
estate KPI grid. This milestone turns it into an operational intelligence zone that
answers, in the operator's own order of need:

| Question | Where it is answered |
|----------|----------------------|
| What is happening? | LEFT — the focused resource and its recorded relationships |
| What infrastructure is involved? | LEFT + RIGHT — recorded hops and the dependency path |
| What is affected? | RIGHT — the backend's impact aggregates |
| Why does HSDEEP believe that? | RIGHT — per-hop evidence, plus the graph's own `limitations` |
| What should the operator investigate? | Both panels — navigation only |

It is a **wiring** milestone: no second topology system, no second graph, no second
dependency engine, no second monitoring system, no fabricated metric, and **no
backend change at all**.

## 1. Where the space was

`frontend/src/components/dashboard/UnifiedCommandCenter.tsx` composes the page and
had nothing between `<AttentionRequired .../>` and the
`<section id="operational-health">`. That file is tracked and clean, so the zone was
inserted there — `dashboard/page.tsx` (which merely renders `UnifiedCommandCenter`)
was **not touched**.

## 2. Data reused (all existing authoritative contracts)

| Zone element | Source | Requests |
|--------------|--------|----------|
| Focus resource | `DashboardData.attention` (open critical/high incidents + degraded devices, already in the cache) | 0 |
| Relationship context + dependency path + impact | `GET /api/infrastructure/dependencies/{resource_id}` (`DependencyImpactService` over `resource_relationships` + `devices`) | 1 |
| Graph evidence block | `GET /api/infrastructure/topology/graph` (`getTopologyGraph()`) | 1 |
| Correlation block | `POST /api/infrastructure/intelligence/correlation/context` (HSI-GNCE; the backend documents this POST as a **read**) | 1 |
| Telemetry block | `DashboardData.infrastructure.sensors` + `operationalHealth.sensorsHealthy` | 0 |
| Collection block | `DashboardData.attention.evidence` — the PHASE 3.1 collector-health contract from `/api/incidents/collector-health` | 0 |
| Recent activity block | `DashboardData.activity` (real events/collector runs) | 0 |
| Root cause | `AttentionIncidentRow.rca` (the recorded RCA, `null` → "Not established") | 0 |

### Why `DependencyImpactService` and not a new impact engine
Its route already returns, per hop, `discovered_by`, `confidence`, `is_inferred`,
`freshness`, `resolution`, `conflict` and `evidence_sources`, plus a `summary` with
`direct_dependents`, `impacted_total`, `unresolved`, `stale`, `conflicts`,
`cycles_detected`, `truncated` and `max_depth_hit`. Every counter the zone shows is
one of those fields — **none is recomputed in the browser**, so the dashboard can
never disagree with the topology workspace about the same fact.

The legacy `/api/assets/devices/{id}/dependency-chain` (`AssetRelationship`) and
`/api/topology/devices/{id}/downstream` were both considered and rejected: the first
is the legacy CMDB edge, not the canonical evidence graph; the second carries no
`resolution`/`conflict`/`stale`/`unresolved` aggregate.

## 3. Request budget (no N+1)

Fixed, bounded and non-duplicating:

1. **graph** — one tenant-wide read model, 60 s.
2. **dependencies** — ONE read for the single focused resource
   (`max_depth=2`, `max_nodes=120`; the service's own ceiling is 6 / 1000), only when
   a focus exists, 60 s.
3. **correlation** — one read for the focus plus up to 9 nearest impacted resources
   (the endpoint allows 200), only when a focus exists, 60 s.

## 4. Files

| File | Change |
|------|--------|
| `frontend/src/lib/dependencyImpact.ts` | NEW — types + `getResourceDependencies()` for the canonical dependency/impact route (the frontend had no client for it; the endpoint itself is unchanged) |
| `frontend/src/lib/dashboard/liveInfraIntelModel.ts` | NEW — pure semantic builders (focus selection, relation rows, impact, path, correlation state, evidence blocks). No fetching, no invention — same contract as `dashboardDataAdapter.ts` |
| `frontend/src/hooks/useLiveInfraIntel.ts` | NEW — the 3 bounded React Query reads described in §3 |
| `frontend/src/components/dashboard/LiveInfrastructureIntelligence.tsx` | NEW — the zone (presentational) |
| `frontend/src/app/styles/live-infrastructure-intelligence.css` | NEW — layout only, scoped under `.hs-dashboard`, existing tokens |
| `frontend/src/components/dashboard/UnifiedCommandCenter.tsx` | EDITED — one import, one zone insertion, one nav link, one doc-comment line |
| `frontend/tests/live-infra-intel-browser.cjs` | NEW — real-browser validation (§7) |

No protected WIP file was touched: `dashboard/page.tsx`, `OperationalHealth.tsx`,
`CommandCenterTopology.tsx`, `dashboardModels.ts`, `dashboardDataAdapter.ts` and
`useDashboardData.ts` remain exactly as another session left them (the models and the
adapter were **read**, never written).

## 5. Reuse decisions and rejected alternatives

The brief said "reuse the existing topology/graph components; do not build a new
graph renderer", so each candidate was evaluated against what it actually needs:

| Candidate | Verdict |
|-----------|---------|
| `OperationalTopologyPanel` | **Rejected.** Self-fetches `getLiveTopology()` and renders the whole estate with its own toolbar/filters/minimap. Embedding it would add a second live-topology poll and load the full graph — both explicitly forbidden. |
| `CanonicalTopologyPanel` | **Rejected.** Self-fetches, and the file is another session's **untracked** work — depending on it would break the build for anyone without that WIP. |
| `TopologyCanvas` (the actual renderer, `TopologyCanvasProps`) | **Rejected.** It is a presentational canvas that takes `model + positions + viewport + handlers`, but the only payload→`RenderNode[]` mapper (`buildRenderModel`) is module-private inside `OperationalTopologyPanel`. Reusing the canvas therefore requires writing a second render-model mapper — i.e. duplicating the topology view layer. |
| `TopologyDetailDrawer` | **Rejected.** Needs a full `RenderNode`/`RenderEdge` (≈40 fields incl. `visual: VendorVisual`, `freshnessLabel`, interface blocks) — the same mapper problem, plus it is drawer chrome, not an inline panel. |
| `TopologyEmptyStatePanel` | **Evaluated, not used.** It takes a `TopologyEmptyState` from `buildEmptyState()`, which requires `online`, `offline` and `unidentified` counts. The dashboard's graph read model does not expose those, so calling it would mean **inventing three numbers** — the one thing this task forbids. The zone therefore renders the mandated empty-state copy (with the graph's real counts and its verbatim `limitations`) itself. |

What **is** reused: the existing data models, the existing API clients, the existing
`hud-panel` / `panel-header` / `panel-body` / `hs-zone` / `hs-zone__header` /
`hs-zone__title` visual language (all already loaded on `/dashboard`), the existing
routes, and the backend's own `limitations` strings rendered verbatim.

## 6. States

| State | Behaviour |
|-------|-----------|
| Loading | Four skeleton bars + "Resolving infrastructure context…", `role="status"`, `aria-live="polite"`, motion disabled under `prefers-reduced-motion` |
| Success | Focus resource, relationship rows, impact metrics, dependency path, correlation |
| Empty | "No active infrastructure context available" + the required explanation, the graph's real counts and its own limitations |
| Error / unavailable | "Infrastructure context unavailable. The topology and dependency APIs did not respond." (or the per-resource variant). The zone never breaks the rest of the dashboard |

Truthfulness rules enforced in the model and component:

- `null` → "N/A" or "—", **never 0**; a measured 0 stays 0.
- Correlation states are distinct: `ANALYZING` (loading), `CORRELATED`, `NO DATA`
  (the service answered with nothing) and `UNAVAILABLE` (the service could not be
  reached). "We could not ask" is never rendered as "there is nothing".
- Root cause shows only the recorded RCA; otherwise "Not established". The zone never
  claims an inferred root cause.
- Collection keeps the PHASE 3.1 contract: **unknown ≠ failed**, and `partial` counts
  as success — the numbers come from the backend aggregate, not from the zone.
- No health/quality/coverage score is introduced anywhere.

## 7. Accessibility and responsiveness

- Every interactive element is a real `<a>` with a meaningful label ("Open
  <resource>", "Open incident", "View topology", "Open monitoring").
- Relationship rows also render a plain-language sentence ("depends on X" / "feeds X")
  and a text direction badge, so meaning never depends on colour or an arrow glyph.
- State is conveyed by text (`DOWN`, `inferred`, `unresolved`, `hop 2`) alongside
  colour.
## 8. Validation

### TypeScript / lint / build

```
tsc --noEmit --incremental false      -> exit 0 (forced full project check)
eslint <5 changed files>              -> exit 0
next build                            -> Compiled successfully; 45/45 static pages
```

### Real-browser validation

`node tests/live-infra-intel-browser.cjs` against the production build, three
scenarios (live backend on :8000; the other two forced by intercepting only the
browser's own fetches):

| Scenario | Failures | Zone rendered | Order | Overflow | Errors |
|----------|----------|---------------|-------|----------|--------|
| live | **0** | yes | attention 82 → zone 1543 → health 2077 | none at 1920/1440/1366/1024/820/390 | 0 |
| deps-aborted | **0** | yes (unavailable state) | ✓ | none | 1 (the aborted request's own console entry, caused by the test) |
| empty-estate | **0** | yes (empty state) | ✓ | none | 0 |

Observed with real data (live scenario): focus `10.10.13.0` (`DOWN`, "Highest-priority
degraded device"), all seven impact counters, `DEPENDENCY PATH`, and
`CORRELATION → CORRELATED, 1 correlation group(s) across 1 resource(s)` with the
HSI-GNCE limitation rendered verbatim
(`primary_inventory_not_collected — no dependency or topology edges exist between the
requested resources, so each is reported as its own group`) and `Root cause: Not
established`.

Observed states: `empty-estate` produced exactly
"NO ACTIVE INFRASTRUCTURE CONTEXT AVAILABLE" plus the required explanation and the
graph's real counts/limitations; `deps-aborted` produced "Relationship evidence for
this resource is unavailable right now." **while the correlation block and the whole
rest of the dashboard kept working** (graceful degradation).

### No N+1 / no duplicate polling, proven from the traffic

Per dashboard load the browser made exactly **one** call to each of
`/api/infrastructure/dependencies/{id}`, `/api/infrastructure/intelligence/correlation/context`
and `/api/infrastructure/topology/graph`. (The only repeated paths were the
pre-existing dashboard queries, e.g. `/api/alerts` ×3 from three existing hooks —
untouched by this milestone.)

### Honest gaps

- The **loading skeleton was not observed in-browser** (`sawLoading: false` in all
  three scenarios): the local backend answers in well under the first poll interval,
  so React Query resolves before the state is capturable. The loading UI exists and
  type-checks/builds, but it has not been visually confirmed under a slow backend.
- Browser validation could not run on `:3100` (the PHASE 2 workaround port): the
  backend's CORS allow-list defaults to `http://localhost:3000` only, so the login
  `fetch` is blocked and the app never leaves `/login`. That is pre-existing
  configuration, unrelated to this change; the production build was therefore served
  on `:3000`. The dev server on `:3000` was not running, so no port conflict arose.

## 9. Limitations

1. **The zone is signal-driven, not selection-driven.** The focus is chosen
   automatically (critical incident resource → degraded device → open incident
   resource). There is no click-to-refocus interaction, because the existing
   attention rows already navigate to their own destinations and adding selection
   state would have meant editing protected WIP.
2. **Left panel is a relationship list, not a drawn graph** (§5): every renderer
   candidate required duplicating the topology render-model mapper. The operator gets
   the same facts (nodes, types, states, per-hop evidence) as rows.
3. **`Root cause` comes from the incident's recorded RCA**, not from the correlation
   service. The HSI-GNCE contract exposes no root-cause field, so claiming one from it
   would be invention.
4. **A "CONFLICT" correlation state is not shown.** The correlation contract exposes
   `limitations`, not a conflict flag; the impact panel shows the backend's
   `conflicts` counter (per-resource) instead.
5. **Depth is capped at 2** for a dashboard load, so a deep chain is reported as
   `truncated` / `max_depth_hit` by the backend rather than silently expanded.
6. **Verification scope:** claims are limited to the files read and the scenarios
   exercised above. The dashboard's other panels were observed only for regressions
   (order, errors, overflow), not re-verified feature by feature.


