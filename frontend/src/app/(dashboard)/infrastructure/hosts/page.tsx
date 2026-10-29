"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AlertOctagon, AlertTriangle, ChevronLeft, ChevronRight, Minus, Server, X } from "lucide-react";
import { useExplorerHosts } from "@/hooks/useExplorerHosts";
import type { HostExplorerRow, HostFacetDimension, HostHealth, HostsExplorerParams } from "@/lib/api";
import {
  Banner,
  DataTable,
  PageShell,
  Panel,
  Pill,
  SearchBox,
  Tabs,
  ago,
  statusTone,
  titleCaseWords,
  type Column,
} from "@/components/ui/PageKit";

/**
 * /infrastructure/hosts — the Hosts explorer: a facet rail with live counts
 * (health, status, type, vendor, criticality, environment) beside a
 * health-sorted host table. One read per filter change
 * (GET /api/explorer/hosts); filters live in the URL so a view can be shared.
 */

const PAGE = 50;
const FACETS: { key: HostFacetDimension; label: string }[] = [
  { key: "health", label: "Health" },
  { key: "status", label: "Status" },
  { key: "device_type", label: "Type" },
  { key: "vendor", label: "Vendor" },
  { key: "criticality", label: "Criticality" },
  { key: "environment", label: "Environment" },
];
const HEALTH_LABEL: Record<HostHealth, string> = { critical: "Critical", warning: "Warning", no_alerts: "No alerts" };
const HEALTH_ICON = { critical: AlertOctagon, warning: AlertTriangle, no_alerts: Minus } as const;
type View = "health" | "inventory";

function facetLabel(dim: HostFacetDimension, value: string): string {
  if (dim === "health") return HEALTH_LABEL[value as HostHealth] ?? value;
  return value === "" || value === "null" ? "Not set" : titleCaseWords(value);
}

function HostsExplorerContent() {
  const rawParams = useSearchParams();
  // Stable identity so the memo below only recomputes when the URL changes.
  const params = useMemo(() => rawParams ?? new URLSearchParams(), [rawParams]);
  const router = useRouter();
  const pathname = usePathname() ?? "/infrastructure/hosts";
  const [view, setView] = useState<View>("health");
  const [searchInput, setSearchInput] = useState(params.get("q") ?? "");

  const filters = useMemo<HostsExplorerParams>(() => {
    const out: HostsExplorerParams = { limit: PAGE, offset: Number(params.get("offset") ?? 0) || 0 };
    const q = params.get("q");
    if (q) out.search = q;
    for (const f of FACETS) {
      const v = params.get(f.key);
      if (v) (out as Record<string, unknown>)[f.key] = v;
    }
    return out;
  }, [params]);

  function setParam(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    if (!("offset" in patch)) next.delete("offset");
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  // Debounced search -> URL.
  useEffect(() => {
    const t = window.setTimeout(() => {
      if ((params.get("q") ?? "") !== searchInput.trim()) setParam({ q: searchInput.trim() || null });
    }, 300);
    return () => window.clearTimeout(t);
    // setParam reads the latest params; re-running on params would loop the debounce.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  const query = useExplorerHosts(filters);
  const data = query.data;
  const active = FACETS.filter((f) => params.get(f.key));
  const offset = filters.offset ?? 0;

  const healthColumns: Column<HostExplorerRow>[] = [
    {
      key: "host",
      header: "Host",
      render: (h) => (
        <span className={`hx-host hx-host--${h.health}`}>
          <span className="pk-cell-main">{h.name}</span>
          <span className="pk-cell-sub">{[h.hostname && h.hostname !== h.name ? h.hostname : null, h.ip_address].filter(Boolean).join(" · ") || "—"}</span>
        </span>
      ),
    },
    {
      key: "alerts",
      header: "Health alerts",
      render: (h) =>
        h.alerts.critical || h.alerts.warning ? (
          <span style={{ display: "inline-flex", gap: 4 }}>
            {h.alerts.critical > 0 && <span className="hx-badge hx-badge--critical"><AlertOctagon size={11} aria-hidden="true" />{h.alerts.critical}</span>}
            {h.alerts.warning > 0 && <span className="hx-badge hx-badge--warning"><AlertTriangle size={11} aria-hidden="true" />{h.alerts.warning}</span>}
          </span>
        ) : (
          <span className="pk-muted">—</span>
        ),
    },
    { key: "status", header: "Status", render: (h) => <Pill tone={statusTone(h.status)}>{titleCaseWords(h.status)}</Pill> },
    { key: "type", header: "Type", render: (h) => titleCaseWords(h.device_type) },
    { key: "site", header: "Site", render: (h) => (h.site_name ?? <span className="pk-muted">Unassigned</span>) },
    { key: "seen", header: "Last seen", render: (h) => <span className="pk-muted">{h.last_seen_at ? ago(h.last_seen_at) : "Never"}</span> },
  ];
  const inventoryColumns: Column<HostExplorerRow>[] = [
    healthColumns[0],
    { key: "vendor", header: "Vendor", render: (h) => (h.vendor ? titleCaseWords(h.vendor) : <span className="pk-muted">—</span>) },
    { key: "provider", header: "Provider", render: (h) => (h.provider ? titleCaseWords(h.provider) : <span className="pk-muted">—</span>) },
    { key: "crit", header: "Criticality", render: (h) => (h.criticality ? titleCaseWords(h.criticality) : <span className="pk-muted">—</span>) },
    { key: "env", header: "Environment", render: (h) => (h.environment ? titleCaseWords(h.environment) : <span className="pk-muted">—</span>) },
    { key: "life", header: "Lifecycle", render: (h) => <span className="pk-muted">{titleCaseWords(h.lifecycle_state)}</span> },
    { key: "telemetry", header: "Last telemetry", render: (h) => <span className="pk-muted">{h.last_telemetry_at ? ago(h.last_telemetry_at) : "Never"}</span> },
  ];

  return (
    <PageShell
      title="Hosts"
      icon={Server}
      subtitle="Every monitored host, sorted by health. Narrow the list with the facets; counts update with each filter."
      crumbs={[{ label: "Infrastructure", href: "/infrastructure" }, { label: "Hosts" }]}
      meta={data ? `Updated ${ago(data.generated_at)}` : undefined}
    >
      {query.isError && <Banner tone="warn">Hosts could not be loaded. Showing the last result if there is one.</Banner>}
      <div className="hx">
        <aside className="hx-facets" aria-label="Filters">
          {FACETS.map((f) => {
            const values = data?.facets[f.key] ?? [];
            const selected = params.get(f.key);
            return (
              <section key={f.key} className="hx-facet">
                <h3>{f.label}</h3>
                {values.length === 0 ? (
                  <p className="hx-facet__none">{data ? "No values" : "…"}</p>
                ) : (
                  <ul>
                    {values.map((v) => {
                      const on = selected === v.value;
                      const Icon = f.key === "health" ? HEALTH_ICON[v.value as HostHealth] : null;
                      return (
                        <li key={v.value}>
                          <label className={`hx-facet__row${on ? " hx-facet__row--on" : ""}`}>
                            <input type="checkbox" checked={on} onChange={() => setParam({ [f.key]: on ? null : v.value })} />
                            {Icon && <Icon size={13} className={`hx-ico hx-ico--${v.value}`} aria-hidden="true" />}
                            <span className="hx-facet__label">{facetLabel(f.key, v.value)}</span>
                            <span className="hx-facet__count">{v.count.toLocaleString("en-US")}</span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            );
          })}
        </aside>

        <Panel
          title={data ? `Hosts ${data.total.toLocaleString("en-US")}` : "Hosts"}
          state={data ? "ready" : query.isError ? "error" : "loading"}
          onRetry={() => query.refetch()}
          skeletonHeight={420}
          aside={data ? `${data.total ? offset + 1 : 0}–${Math.min(offset + PAGE, data.total)} of ${data.total.toLocaleString("en-US")}` : undefined}
        >
          <div className="pk-toolbar" style={{ marginBottom: 10 }}>
            <Tabs label="View" value={view} onChange={setView} tabs={[{ key: "health", label: "Health" }, { key: "inventory", label: "Inventory" }]} />
            <SearchBox value={searchInput} onChange={setSearchInput} placeholder="Filter by name, hostname or IP" label="Filter hosts" />
            {active.map((f) => (
              <button key={f.key} type="button" className="hx-chip" onClick={() => setParam({ [f.key]: null })} aria-label={`Remove ${f.label} filter`}>
                {f.label}: {facetLabel(f.key, params.get(f.key)!)} <X size={12} aria-hidden="true" />
              </button>
            ))}
            {active.length > 0 && (
              <button type="button" className="ecc-link" style={{ background: "none", border: 0, cursor: "pointer" }} onClick={() => setParam(Object.fromEntries(FACETS.map((f) => [f.key, null])))}>
                Clear all
              </button>
            )}
          </div>
          <DataTable
            caption="Hosts"
            columns={view === "health" ? healthColumns : inventoryColumns}
            rows={data?.rows ?? []}
            rowKey={(h) => h.id}
            rowHref={(h) => `/infrastructure/hosts/${h.id}`}
            emptyText={active.length || filters.search ? "No hosts match these filters." : "No hosts registered yet. Add a monitoring source or run discovery."}
            maxHeight="calc(100vh - 330px)"
          />
          {data && data.total > PAGE && (
            <div className="pk-table-foot">
              <span>Page {Math.floor(offset / PAGE) + 1} of {Math.ceil(data.total / PAGE)}</span>
              <span style={{ display: "inline-flex", gap: 6 }}>
                <button type="button" className="pk-button" style={{ height: 28 }} disabled={offset === 0} onClick={() => setParam({ offset: String(Math.max(0, offset - PAGE)) })}>
                  <ChevronLeft size={14} /> Previous
                </button>
                <button type="button" className="pk-button" style={{ height: 28 }} disabled={offset + PAGE >= data.total} onClick={() => setParam({ offset: String(offset + PAGE) })}>
                  Next <ChevronRight size={14} />
                </button>
              </span>
            </div>
          )}
        </Panel>
      </div>
    </PageShell>
  );
}

export default function HostsExplorerPage() {
  return (
    <Suspense fallback={<PageShell title="Hosts"><Panel title="Hosts" state="loading" skeletonHeight={420} /></PageShell>}>
      <HostsExplorerContent />
    </Suspense>
  );
}
