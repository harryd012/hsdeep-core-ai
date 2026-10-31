"use client";

import { useState, useEffect, useMemo } from "react";
import { Globe2, Server, Network, ChevronRight, ChevronDown, Eye, Shield, Wifi, Database, Cloud, Radio, Link2, RefreshCw, AlertTriangle, Search, type LucideIcon } from "lucide-react";
import { listAssetSites, AssetSite, listAssetDevices, AssetDevice, listZones, ZoneItem, getTenantHealth, TenantHealthSummary, getTenantId, getTopologyGraph, TopologyGraph, TopologyGraphNode, TopologyGraphEdge, TopologyHealth } from "@/lib/api";
import { getTopologySummary, type TopologyQuickSummary } from "@/lib/topologyApi";
import { useAuth } from "@/lib/auth-context";
import FreshnessIndicator from "@/components/shared/FreshnessIndicator";

interface InfraNode { id: string; name: string; type: "tenant" | "site" | "zone" | "device" | "placeholder"; icon: LucideIcon; meta?: string; status?: string; deviceType?: string; children?: InfraNode[]; }

function deviceIcon(dt: string): LucideIcon {
  const m: Record<string, LucideIcon> = { firewall: Shield, router: Network, switch: Network, wireless: Wifi, database: Database, cloud: Cloud };
  return m[dt.toLowerCase()] ?? Radio;
}
function statColor(s: string): string {
  const l = s.toLowerCase();
  if (["up", "online", "operational", "active", "healthy"].includes(l)) return "var(--green)";
  if (["warning", "degraded"].includes(l)) return "var(--amber)";
  if (["down", "error", "failed", "offline"].includes(l)) return "var(--red)";
  return "var(--muted)";
}

export default function InfrastructureTopologyPage() {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root"]));
  const [sites, setSites] = useState<AssetSite[]>([]);
  const [devices, setDevices] = useState<AssetDevice[]>([]);
  const [zones, setZones] = useState<ZoneItem[]>([]);
  const [health, setHealth] = useState<TenantHealthSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Phase 3 topology graph (nodes/edges). Loaded separately from the site tree
  // so a topology failure never blanks the existing tree.
  const [graph, setGraph] = useState<TopologyGraph | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [nodeSearch, setNodeSearch] = useState("");
  const [healthFilter, setHealthFilter] = useState<"all" | TopologyHealth>("all");
  const [refreshing, setRefreshing] = useState(false);
  // Task 3: fast summary loaded first; graph loaded separately with 30s timeout
  const [summary, setSummary] = useState<TopologyQuickSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  // Step 9 / Step 10 detail panels. Holding the whole node/edge object means the
  // panel never has to re-fetch or guess at a value.
  const [selectedNode, setSelectedNode] = useState<TopologyGraphNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<TopologyGraphEdge | null>(null);

  useEffect(() => {
    let c = false;
    async function load() {
      try {
        const [s, d, z, h] = await Promise.all([listAssetSites().catch(() => []), listAssetDevices().catch(() => []), listZones().catch(() => []), getTenantHealth().catch(() => null)]);
        if (!c) { setSites(s); setDevices(d); setZones(z); setHealth(h); setError(null); }
      } catch (e) { if (!c) setError(e instanceof Error ? e.message : "Failed to load"); }
      finally { if (!c) setLoading(false); }
    }
    async function loadSummary() {
      try {
        const s = await getTopologySummary();
        if (!c) { setSummary(s); setSummaryError(null); }
      } catch (e) {
        if (!c) setSummaryError(e instanceof Error ? e.message : "Summary unavailable");
      }
    }
    async function loadGraph() {
      try {
        const g = await getTopologyGraph(undefined, 30_000);
        if (!c) { setGraph(g); setGraphError(null); }
      } catch (e) {
        if (!c) setGraphError(e instanceof Error ? e.message : "Topology unavailable");
      }
    }
    load(); loadSummary(); loadGraph();
    const t = setInterval(() => { load(); loadSummary(); loadGraph(); }, 60_000);
    return () => { c = true; clearInterval(t); };
  }, []);

  const refreshGraph = async () => {
    setRefreshing(true);
    try { setGraph(await getTopologyGraph(undefined, 30_000)); setGraphError(null); }
    catch (e) { setGraphError(e instanceof Error ? e.message : "Topology unavailable"); }
    finally { setRefreshing(false); }
  };

  const tree = useMemo((): InfraNode[] => {
    const tid = getTenantId();
    const db = new Map<string, AssetDevice[]>(); for (const d of devices) { if (d.site_id) { if (!db.has(d.site_id)) db.set(d.site_id, []); db.get(d.site_id)!.push(d); } }
    const zb = new Map<string, ZoneItem[]>(); for (const z of zones) { if (!zb.has(z.site_id)) zb.set(z.site_id, []); zb.get(z.site_id)!.push(z); }
    const sn: InfraNode[] = sites.map((s) => {
      const sd = db.get(s.id) ?? []; const sz = zb.get(s.id) ?? [];
      const zn: InfraNode[] = sz.map((z) => ({ id: `z-${z.id}`, name: z.name, type: "zone" as const, icon: Network, meta: z.zone_type, children: sd.map((d) => ({ id: `d-${d.id}`, name: d.name, type: "device" as const, icon: deviceIcon(d.device_type), status: d.status, meta: d.ip_address ?? d.vendor ?? undefined })) }));
      if (!zn.length && sd.length) zn.push({ id: `uz-${s.id}`, name: "Unzoned", type: "placeholder" as const, icon: Network, meta: `${sd.length} device(s)`, children: sd.map((d) => ({ id: `d-${d.id}`, name: d.name, type: "device" as const, icon: deviceIcon(d.device_type), status: d.status, meta: d.ip_address ?? d.vendor ?? undefined })) });
      return { id: `s-${s.id}`, name: s.name, type: "site" as const, icon: Globe2, meta: s.region ?? s.code, children: zn.length ? zn : undefined } as InfraNode;
    });
    return [{ id: "root", name: `Infra (${tid.slice(0, 8)})`, type: "tenant", icon: Server, meta: health ? `${sites.length} sites, ${devices.length} devices` : undefined, status: health?.status, children: sn.length ? sn : undefined }];
  }, [sites, devices, zones, health]);

  const filteredTree = useMemo(() => {
    if (!search.trim()) return tree;
    const q = search.toLowerCase();
    const fn = (n: InfraNode[]): InfraNode[] => n.reduce<InfraNode[]>((a, nd) => { const m = nd.name.toLowerCase().includes(q); const fc = nd.children ? fn(nd.children) : []; if (m || fc.length) a.push({ ...nd, children: fc.length ? fc : nd.children }); return a; }, []);
    return fn(tree);
    }, [tree, search]);

  const toggle = (id: string) => setExpanded((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const graphNodes = useMemo(() => graph?.nodes ?? [], [graph]);
  const graphEdges = useMemo(() => graph?.edges ?? [], [graph]);
  const nodeName = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of graphNodes) m.set(n.id, n.name);
    return m;
  }, [graphNodes]);

  const visibleNodes = useMemo(() => {
    const q = nodeSearch.trim().toLowerCase();
    return graphNodes.filter((n) => {
      // Health only applies to device nodes - site/source nodes carry no
      // canonical bucket of their own, so they are never silently dropped by a
      // health filter.
      if (healthFilter !== "all" && n.type === "device" && n.health !== healthFilter) return false;
      if (!q) return true;
      const hay = [n.name, n.device_type, n.device_type_raw,
        (n.metadata?.ip as string) ?? "", (n.metadata?.vendor as string) ?? "",
        (n.metadata?.model as string) ?? ""].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [graphNodes, nodeSearch, healthFilter]);

  const healthCounts = useMemo(() => {
    const c: Record<string, number> = { healthy: 0, warning: 0, critical: 0, unknown: 0 };
    for (const n of graphNodes) if (n.type === "device" && n.health) c[n.health] = (c[n.health] ?? 0) + 1;
    return c;
  }, [graphNodes]);

  if (loading) return <div className="page-shell" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}><div className="infra-topo"><p style={{ color: "var(--muted)", fontSize: 13 }}>Loading infrastructure topology…</p></div></div>;

  return (
    <div className="page-shell" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="infra-topo">
        <h2 style={{ color: "var(--cyan)", fontSize: 14, margin: 0, textTransform: "uppercase" as const, letterSpacing: "0.5px" }}>Infrastructure Topology</h2>
        {error && <p style={{ color: "var(--amber)", fontSize: 12 }}>{error}</p>}
        {graphError && <p style={{ color: "var(--amber)", fontSize: 12 }}>Topology graph unavailable: {graphError}</p>}

        {/* Task 3: Quick summary — loads fast, visible even while graph loads or fails */}
        {(summary || summaryError) && (
          <section style={{ marginTop: 14, border: "1px solid var(--line)", borderRadius: 8, padding: 14 }}>
            {summaryError && !summary && (
              <p style={{ fontSize: 11, color: "var(--amber)", margin: 0 }}>Summary unavailable: {summaryError}</p>
            )}
            {summary && (
              <>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
                  QUICK SUMMARY — {new Date(summary.generated_at).toLocaleString()}
                </div>
                <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 11, color: "var(--muted)" }}>
                  <Stat label="Devices" value={summary.devices_total} />
                  <Stat label="Sites" value={summary.sites_total} />
                  <Stat label="Sources" value={summary.monitoring_sources_total} />
                  <Stat label="Relationships" value={summary.relationships_total} />
                  <Stat label="Interfaces" value={summary.interfaces_total} />
                  <Stat label="Sensors" value={summary.sensors_total} />
                </div>
              </>
            )}
          </section>
        )}

        {/* Graph loading placeholder — shown when summary is available but graph is not yet */}
        {!graph && !graphError && summary && (
          <section style={{ marginTop: 14, border: "1px dashed var(--line)", borderRadius: 8, padding: 14, textAlign: "center" }}>
            <p style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>Loading topology graph…</p>
          </section>
        )}
        {/* Graph failed but summary succeeded — keep summary visible */}
        {!graph && graphError && summary && (
          <section style={{ marginTop: 14, border: "1px solid var(--amber)", borderRadius: 8, padding: 14, textAlign: "center" }}>
            <p style={{ fontSize: 12, color: "var(--amber)", margin: 0 }}>Graph detail unavailable — try again</p>
          </section>
        )}

        {graph && (
          <section style={{ marginTop: 14, border: "1px solid var(--line)", borderRadius: 8, padding: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <Link2 size={14} style={{ color: "var(--cyan)" }} />
              <strong style={{ fontSize: 12, letterSpacing: "0.4px" }}>TOPOLOGY GRAPH</strong>
              <StatusBadge status={graph.status} />
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
                Updated {new Date(graph.generated_at).toLocaleString()}
              </span>
              <button onClick={refreshGraph} disabled={refreshing} title="Refresh topology"
                style={{ display: "inline-flex", alignItems: "center", gap: 5, background: "transparent", border: "1px solid var(--line)", borderRadius: 6, color: "var(--muted)", fontSize: 11, padding: "3px 8px", cursor: "pointer" }}>
                <RefreshCw size={11} /> {refreshing ? "Refreshing…" : "Refresh"}
              </button>
            </div>

            {/* Real counts straight from the API - must agree with
                /infrastructure/overview, which the backend asserts in tests. */}
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginTop: 10, fontSize: 11, color: "var(--muted)" }}>
              <Stat label="Devices" value={graph.summary.devices_total} />
              <Stat label="Sites" value={graph.summary.sites_total} />
              <Stat label="Monitoring sources" value={graph.summary.monitoring_sources_total} />
              <Stat label="Sensors" value={graph.summary.sensors_total} />
              <Stat label="Interfaces" value={graph.summary.interfaces_total} />
              <Stat label="Relationships" value={graph.summary.edges_total} />
            </div>

            {/* The honest explanation. Rendered whenever the backend supplies
                one, so an empty graph is never a blank canvas. */}
            {graph.limitations.length > 0 && (
              <div style={{ marginTop: 12, borderLeft: "2px solid var(--amber)", paddingLeft: 10 }}>
                <div style={{ fontSize: 11, color: "var(--amber)", display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                  <AlertTriangle size={12} /> TOPOLOGY LIMITATIONS
                </div>
                {graph.limitations.map((line) => (
                  <p key={line} style={{ margin: "2px 0", fontSize: 11, color: "var(--muted)" }}>{line}</p>
                ))}
              </div>
            )}

            {graphEdges.length === 0 ? (
              <div style={{ marginTop: 14, padding: "18px 12px", textAlign: "center", border: "1px dashed var(--line)", borderRadius: 8 }}>
                <Network size={26} style={{ color: "var(--muted)", margin: "0 auto 8px" }} />
                <p style={{ margin: 0, fontSize: 13, color: "var(--fg)" }}>No topology relationships discovered</p>
                <p style={{ margin: "6px auto 0", fontSize: 11, color: "var(--muted)", maxWidth: 560 }}>
                  {graph.summary.devices_total} device{graph.summary.devices_total === 1 ? "" : "s"} are currently
                  inventoried, but no interface or device-to-device relationship data has been discovered for this
                  tenant. Nothing is drawn here because no evidence exists to draw.
                </p>
                <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 12, flexWrap: "wrap" }}>
                  <ActionLink href="/infrastructure/discovery" label="Run Discovery" />
                  <ActionLink href="/infrastructure/assets" label="View Devices" />
                  <ActionLink href="/monitoring" label="View Monitoring" />
                </div>
              </div>
            ) : (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>
                  {graphEdges.length} discovered relationship{graphEdges.length === 1 ? "" : "s"}
                </div>
                {graphEdges.map((e) => <EdgeRow key={e.id} edge={e} nameOf={nodeName} onSelect={setSelectedEdge} />)}
              </div>
            )}
          </section>
        )}

        {graph && graphNodes.length > 0 && (
          <section style={{ marginTop: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <strong style={{ fontSize: 12, letterSpacing: "0.4px" }}>NODES ({visibleNodes.length}/{graphNodes.length})</strong>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 5, border: "1px solid var(--line)", borderRadius: 6, padding: "2px 7px" }}>
                <Search size={11} style={{ color: "var(--muted)" }} />
                <input type="text" placeholder="Search nodes…" value={nodeSearch} onChange={(e) => setNodeSearch(e.target.value)}
                  style={{ background: "transparent", border: 0, outline: "none", color: "var(--fg)", fontSize: 11, width: 150 }} />
              </div>
              {(["all", "critical", "warning", "healthy", "unknown"] as const).map((f) => (
                <button key={f} onClick={() => setHealthFilter(f)}
                  style={{ fontSize: 10, padding: "3px 8px", borderRadius: 999, cursor: "pointer",
                    border: `1px solid ${healthFilter === f ? "var(--cyan)" : "var(--line)"}`,
                    background: "transparent", color: healthFilter === f ? "var(--cyan)" : "var(--muted)", textTransform: "capitalize" }}>
                  {f === "all" ? "All" : `${f} (${healthCounts[f] ?? 0})`}
                </button>
              ))}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 8, marginTop: 10 }}>
              {visibleNodes.map((n) => <NodeCard key={n.id} node={n} onSelect={setSelectedNode} />)}
            </div>
            {visibleNodes.length === 0 && <p className="infra-node__empty">No topology nodes match this filter.</p>}
            <p style={{ marginTop: 8, fontSize: 10, color: "var(--muted)" }}>
              Legend: <span style={{ color: "var(--green)" }}>● healthy</span>{" "}
              <span style={{ color: "var(--amber)" }}>● warning</span>{" "}
              <span style={{ color: "var(--red)" }}>● critical</span>{" "}
              <span style={{ color: "var(--muted)" }}>● unknown</span>
            </p>
          </section>
        )}

        <div className="infra-topo__toolbar"><input type="text" placeholder="Search sites, zones, devices…" value={search} onChange={(e) => setSearch(e.target.value)} className="infra-topo__search" /></div>
        <div className="infra-topo__tree">
          {filteredTree.map((nd) => <ITNode key={nd.id} node={nd} expanded={expanded} onToggle={toggle} />)}
          {!filteredTree.length && <p className="infra-node__empty">No infrastructure matches your search.</p>}
        </div>
      </div>
      <NodeDrawer node={selectedNode} onClose={() => setSelectedNode(null)} />
      <EdgeDrawer edge={selectedEdge} nameOf={nodeName} onClose={() => setSelectedEdge(null)} />
    </div>
  );
}

function ITNode({ node, expanded, onToggle }: { node: InfraNode; expanded: Set<string>; onToggle: (id: string) => void }) {
  const isExp = expanded.has(node.id);
  const hasC = node.children && node.children.length > 0;
  const isPh = node.type === "placeholder";
  const Icon = node.icon;
  const dc = node.status ? statColor(node.status) : undefined;
  return (
    <div className="infra-node">
      <div className="infra-node__header" onClick={() => (hasC || isPh) ? onToggle(node.id) : null}>
        {(hasC || isPh) ? (isExp ? <ChevronDown size={14} className="infra-node__chev" /> : <ChevronRight size={14} className="infra-node__chev" />) : <span style={{ width: 14 }} />}
        <div className="infra-node__icon"><Icon size={14} /></div>
        <div className="infra-node__info"><span className="infra-node__name">{node.name}</span>{node.meta && <span className="infra-node__meta">{node.meta}</span>}</div>
        {dc && <span className="infra-node__status" style={{ color: dc }}><i className="infra-node__dot" style={{ background: dc }} /></span>}
        <div className="org-node__actions"><button className="org-node__action" onClick={(e) => e.stopPropagation()}><Eye size={11} /> View</button></div>
      </div>
      {isExp && (hasC || isPh) && (
        <div className="infra-node__children">
          {isPh && <p className="infra-node__empty">{node.meta ?? "No children."}</p>}
          {hasC && node.children!.map((ch) => <ITNode key={ch.id} node={ch} expanded={expanded} onToggle={onToggle} />)}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Phase 3 topology graph presentation.
 *
 * Every value rendered here comes from the API verbatim. Where a field is
 * absent the line is omitted rather than filled with a placeholder - the same
 * rule the backend follows when it refuses to invent edges.
 * ------------------------------------------------------------------------- */

const HEALTH_COLOR: Record<string, string> = {
  healthy: "var(--green)",
  warning: "var(--amber)",
  critical: "var(--red)",
  unknown: "var(--muted)",
};

function StatusBadge({ status }: { status: string }) {
  const label = status === "ready" ? "READY"
    : status === "partial" ? "PARTIAL"
    : "NOT DISCOVERED";
  const color = status === "ready" ? "var(--green)"
    : status === "partial" ? "var(--amber)"
    : "var(--muted)";
  return (
    <span style={{ fontSize: 10, letterSpacing: "0.5px", padding: "2px 8px", borderRadius: 999,
      border: `1px solid ${color}`, color }}>
      {label}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <span>
      <strong style={{ color: "var(--fg)" }}>{value}</strong> {label}
    </span>
  );
}

function ActionLink({ href, label }: { href: string; label: string }) {
  return (
    <a href={href} style={{ fontSize: 11, padding: "4px 10px", borderRadius: 6,
      border: "1px solid var(--line)", color: "var(--cyan)", textDecoration: "none" }}>
      {label}
    </a>
  );
}

function NodeCard({ node, onSelect }: { node: TopologyGraphNode; onSelect?: (n: TopologyGraphNode) => void }) {
  const color = node.health ? HEALTH_COLOR[node.health] ?? "var(--muted)" : "var(--muted)";
  const ip = (node.metadata?.ip as string) ?? null;
  const vendor = (node.metadata?.vendor as string) ?? null;
  const model = (node.metadata?.model as string) ?? null;
  const counts = node.counts ?? {};
  const detail = vendor ?? node.device_type ?? node.source_type;
  // Rendered as a plain element unless a handler is supplied, so the card never
  // presents a clickable affordance that does nothing (the detail drawer is not
  // built yet).
  const interactive = typeof onSelect === "function";
  const Tag = interactive ? "button" : "div";
  return (
    <Tag
      {...(interactive ? { onClick: () => onSelect?.(node), type: "button" as const } : {})}
      data-topology-node={node.id}
      data-topology-health={node.health ?? "none"}
      style={{ textAlign: "left", cursor: interactive ? "pointer" : "default", background: "transparent",
        border: "1px solid var(--line)", borderRadius: 8, padding: "9px 11px",
        color: "var(--fg)", display: "block", width: "100%" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <i style={{ width: 7, height: 7, borderRadius: "50%", background: color, display: "inline-block", flexShrink: 0 }} />
        <span style={{ fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {node.name}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--muted)", textTransform: "uppercase" }}>
          {node.type === "monitoring_source" ? "source" : node.type}
        </span>
      </div>
      {detail && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{detail}{model ? ` · ${model}` : ""}</div>}
      {ip && <div style={{ fontSize: 10, color: "var(--muted)" }}>{ip}</div>}
      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 3 }}>
        {typeof counts.sensors === "number" && <span>{counts.sensors} sensors</span>}
        {typeof counts.interfaces === "number" && counts.interfaces > 0 && <span> · {counts.interfaces} interfaces</span>}
        {typeof counts.alerts === "number" && <span> · {counts.alerts} active alerts</span>}
        {typeof counts.devices === "number" && <span>{counts.devices} devices</span>}
      </div>
    </Tag>
  );
}

function EdgeRow({ edge, nameOf, onSelect }: { edge: TopologyGraphEdge; nameOf: Map<string, string>; onSelect?: (e: TopologyGraphEdge) => void }) {
  const interactive = typeof onSelect === "function";
  const Tag = interactive ? "button" : "div";
  return (
    <Tag
      {...(interactive ? { onClick: () => onSelect?.(edge), type: "button" as const } : {})}
      data-topology-edge={edge.id}
      style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", width: "100%",
        textAlign: "left", cursor: interactive ? "pointer" : "default", background: "transparent",
        color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 6, padding: "6px 9px",
        marginBottom: 5, fontSize: 11 }}>
      <span>{nameOf.get(edge.source_id) ?? edge.source_id}</span>
      <span style={{ color: "var(--cyan)" }}>—{edge.relationship_type}→</span>
      <span>{nameOf.get(edge.target_id) ?? edge.target_id}</span>
      <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)" }}>
        evidence: {edge.evidence}
        {edge.discovered_by ? ` · discovered by ${edge.discovered_by}` : ""}
        {edge.is_inferred ? " · inferred" : ""}
        {typeof edge.confidence === "number" ? ` · confidence ${edge.confidence}` : ""}
      </span>
      {edge.unknown_relationship_type && (
        <span style={{ fontSize: 10, color: "var(--amber)" }}>unrecognised relationship type</span>
      )}
    </Tag>
  );
}

/* ---------------------------------------------------------------------------
 * Step 9 — node detail drawer.
 *
 * Every row is either a real value from the payload or omitted. Nothing is
 * back-filled with a placeholder, and no action is offered for a route that
 * does not exist.
 * ------------------------------------------------------------------------- */

const DRAWER_STYLE: React.CSSProperties = {
  position: "fixed", top: 0, right: 0, height: "100vh", width: 360, zIndex: 60,
  background: "var(--bg, #0b0f14)", borderLeft: "1px solid var(--line)",
  padding: "16px 16px 24px", overflowY: "auto", boxShadow: "-8px 0 24px rgba(0,0,0,.4)",
};

function DrawerShell({ title, subtitle, onClose, children, testId }: {
  title: string; subtitle?: string | null; onClose: () => void;
  children: React.ReactNode; testId: string;
}) {
  return (
    <aside style={DRAWER_STYLE} data-topology-drawer={testId} role="dialog" aria-label={title}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <strong style={{ fontSize: 13, color: "var(--cyan)", letterSpacing: "0.5px" }}>{title}</strong>
        <button onClick={onClose} aria-label="Close"
          style={{ marginLeft: "auto", background: "transparent", border: "1px solid var(--line)",
            borderRadius: 6, color: "var(--muted)", fontSize: 12, padding: "2px 9px", cursor: "pointer" }}>
          Close
        </button>
      </div>
      {subtitle && <div style={{ fontSize: 12, marginTop: 6, color: "var(--fg)" }}>{subtitle}</div>}
      <div style={{ marginTop: 12 }}>{children}</div>
    </aside>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  // A null/empty value renders nothing: an absent field is not "unknown" data,
  // it is simply not recorded, and inventing a placeholder would mislead.
  if (value === null || value === undefined || value === "") return null;
  return (
    <div style={{ display: "flex", gap: 10, padding: "3px 0", fontSize: 11 }}>
      <span style={{ color: "var(--muted)", minWidth: 110 }}>{label}</span>
      <span style={{ color: "var(--fg)", wordBreak: "break-word" }}>{value}</span>
    </div>
  );
}

function Metric({ label, value, note }: { label: string; value: number | null; note?: string }) {
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "6px 8px" }}>
      <div style={{ fontSize: 15, fontWeight: 600 }}>{value === null ? "—" : value}</div>
      <div style={{ fontSize: 10, color: "var(--muted)" }}>{label}</div>
      {value === null && note && (
        <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 2 }}>{note}</div>
      )}
    </div>
  );
}

function NodeDrawer({ node, onClose }: { node: TopologyGraphNode | null; onClose: () => void }) {
  if (!node) return null;
  const meta = node.metadata ?? {};
  const counts = node.counts ?? {};
  const color = node.health ? HEALTH_COLOR[node.health] ?? "var(--muted)" : "var(--muted)";
  const deviceId = node.type === "device" ? node.id.replace(/^device:/, "") : null;
  const num = (v: unknown) => (typeof v === "number" ? v : null);

  return (
    <DrawerShell title="NODE DETAIL" subtitle={node.name} onClose={onClose} testId="node">
      <Row label="Type" value={node.type === "monitoring_source" ? "Monitoring source" : node.type} />
      <Row label="Status" value={node.status} />
      <Row label="Health" value={node.health
        ? <span style={{ color }}>● {node.health}</span>
        : <span style={{ color: "var(--muted)" }}>Not scored here</span>} />
      <Row label="Device type" value={node.device_type} />
      <Row label="Stored type" value={
        node.device_type_raw && node.device_type_raw !== node.device_type
          ? node.device_type_raw : null} />
      <Row label="IP" value={(meta.ip as string) ?? null} />
      <Row label="Vendor" value={(meta.vendor as string) ?? null} />
      <Row label="Model" value={(meta.model as string) ?? null} />
      <Row label="Hostname" value={(meta.hostname as string) ?? null} />
      <Row label="Site" value={node.site_id ?? "Not associated with a site"} />
      <Row label="Monitoring source" value={node.monitoring_source?.name ?? null} />
      <Row label="Source type" value={node.source_type ?? node.monitoring_source?.source_type ?? null} />
      <Row label="Last seen" value={meta.last_seen_at
        ? new Date(meta.last_seen_at as string).toLocaleString() : null} />
      {/* Same value as "Last seen" — never invent a separate timestamp. */}
      <Row label="Data freshness" value={
        <FreshnessIndicator
          lastCollectedAt={(meta.last_seen_at as string) ?? null}
          showAge
        />} />
      <Row label="Last sync" value={meta.last_sync_at
        ? new Date(meta.last_sync_at as string).toLocaleString() : null} />
      <Row label="Region" value={node.region ?? null} />
      <Row label="Coordinates" value={node.has_coordinates ? `${node.latitude}, ${node.longitude}` : null} />

      <div style={{ marginTop: 14, fontSize: 10, color: "var(--muted)", letterSpacing: "0.5px" }}>METRICS</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
        <Metric label="Sensors" value={num(counts.sensors)} />
        <Metric label="Alerts" value={num(counts.alerts)} />
        <Metric label="Interfaces" value={num(counts.interfaces)} />
        {/* Not aggregated server-side: the query costs ~1.4s per load on this
            database, so the real /incidents route is offered instead. */}
        <Metric label="Incidents" value={null} note="Not collected in this view" />
      </div>

      <div style={{ marginTop: 14, fontSize: 10, color: "var(--muted)", letterSpacing: "0.5px" }}>ACTIONS</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
        {deviceId && <ActionLink href={`/infrastructure/assets/${deviceId}`} label="Open Device" />}
        {deviceId && <ActionLink href="/monitoring" label="View Sensors" />}
        {deviceId && <ActionLink href="/alerts" label="View Alerts" />}
        {deviceId && <ActionLink href="/incidents" label="View Incidents" />}
        {node.type === "site" && <ActionLink href="/infrastructure" label="Open Site" />}
        {node.type === "monitoring_source" && <ActionLink href="/monitoring" label="View Monitoring" />}
      </div>
    </DrawerShell>
  );
}

/* ---------------------------------------------------------------------------
 * Step 10 — relationship detail.
 *
 * Only recorded fields are shown. Link speed, VLAN, latency and bandwidth are
 * never derived, and when the row carries no `attributes` the panel says so
 * explicitly rather than rendering an empty box.
 * ------------------------------------------------------------------------- */

function EdgeDrawer({ edge, nameOf, onClose }: {
  edge: TopologyGraphEdge | null; nameOf: Map<string, string>; onClose: () => void;
}) {
  if (!edge) return null;
  const metadata = edge.metadata ?? {};
  const metadataKeys = Object.keys(metadata);

  return (
    <DrawerShell
      title="RELATIONSHIP"
      subtitle={`${nameOf.get(edge.source_id) ?? edge.source_id} → ${nameOf.get(edge.target_id) ?? edge.target_id}`}
      onClose={onClose}
      testId="edge"
    >
      <Row label="Source" value={nameOf.get(edge.source_id) ?? edge.source_id} />
      <Row label="Target" value={nameOf.get(edge.target_id) ?? edge.target_id} />
      <Row label="Relationship type" value={
        <span>
          {edge.relationship_type}
          {edge.unknown_relationship_type && (
            <span style={{ color: "var(--amber)", marginLeft: 6 }}>unrecognised type</span>
          )}
        </span>} />
      <Row label="Evidence" value={edge.evidence} />
      <Row label="Discovered by" value={edge.discovered_by} />
      <Row label="Inferred" value={edge.is_inferred ? "Yes - not directly observed" : "No"} />
      <Row label="Confidence" value={typeof edge.confidence === "number" ? edge.confidence : null} />
      <Row label="Status" value={edge.status} />

      <div style={{ marginTop: 14, fontSize: 10, color: "var(--muted)", letterSpacing: "0.5px" }}>METADATA</div>
      {metadataKeys.length === 0 ? (
        <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
          Relationship metadata unavailable.
        </p>
      ) : (
        <div style={{ marginTop: 4 }}>
          {metadataKeys.map((k) => <Row key={k} label={k} value={String(metadata[k] ?? "")} />)}
        </div>
      )}
    </DrawerShell>
  );
}
