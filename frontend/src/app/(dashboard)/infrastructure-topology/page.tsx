"use client";

import { useState, useEffect, useMemo } from "react";
import { Globe2, Server, Network, ChevronRight, ChevronDown, Eye, Shield, Wifi, Database, Cloud, Radio, type LucideIcon } from "lucide-react";
import { listAssetSites, AssetSite, listAssetDevices, AssetDevice, listZones, ZoneItem, getTenantHealth, TenantHealthSummary, getTenantId } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

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

  useEffect(() => {
    let c = false;
    async function load() {
      try {
        const [s, d, z, h] = await Promise.all([listAssetSites().catch(() => []), listAssetDevices().catch(() => []), listZones().catch(() => []), getTenantHealth().catch(() => null)]);
        if (!c) { setSites(s); setDevices(d); setZones(z); setHealth(h); setError(null); }
      } catch (e) { if (!c) setError(e instanceof Error ? e.message : "Failed to load"); }
      finally { if (!c) setLoading(false); }
    }
    load(); const t = setInterval(load, 60_000); return () => { c = true; clearInterval(t); };
  }, []);

  const tree = useMemo((): InfraNode[] => {
    const tid = getTenantId();
    const db = new Map<string, AssetDevice[]>(); for (const d of devices) { if (d.site_id) { if (!db.has(d.site_id)) db.set(d.site_id, []); db.get(d.site_id)!.push(d); } }
    const zb = new Map<string, ZoneItem[]>(); for (const z of zones) { if (!zb.has(z.site_id)) zb.set(z.site_id, []); zb.get(z.site_id)!.push(z); }
    const sn: InfraNode[] = sites.map((s) => {
      const sd = db.get(s.id) ?? []; const sz = zb.get(s.id) ?? [];
      const zn: InfraNode[] = sz.map((z) => ({ id: `z-${z.id}`, name: z.name, type: "zone" as const, icon: Network, meta: z.zone_type, children: sd.map((d) => ({ id: `d-${d.id}`, name: d.name, type: "device" as const, icon: deviceIcon(d.device_type), status: d.status, meta: d.ip_address ?? d.vendor ?? undefined })) }));
      if (!zn.length && sd.length) zn.push({ id: `uz-${s.id}`, name: "Unzoned", type: "placeholder" as const, icon: Network, meta: `${sd.length} device(s)`, children: sd.map((d) => ({ id: `d-${d.id}`, name: d.name, type: "device" as const, icon: deviceIcon(d.device_type), status: d.status, meta: d.ip_address ?? d.vendor ?? undefined })) });
      return { id: `s-${s.id}`, name: s.name, type: "site" as const, icon: Globe2, status: "healthy", meta: s.region ?? s.code, children: zn.length ? zn : undefined } as InfraNode;
    });
    return [{ id: "root", name: `Infra (${tid.slice(0, 8)})`, type: "tenant", icon: Server, meta: health ? `${sites.length} sites, ${devices.length} devices` : undefined, status: "healthy", children: sn.length ? sn : undefined }];
  }, [sites, devices, zones, health]);

  const filteredTree = useMemo(() => {
    if (!search.trim()) return tree;
    const q = search.toLowerCase();
    const fn = (n: InfraNode[]): InfraNode[] => n.reduce<InfraNode[]>((a, nd) => { const m = nd.name.toLowerCase().includes(q); const fc = nd.children ? fn(nd.children) : []; if (m || fc.length) a.push({ ...nd, children: fc.length ? fc : nd.children }); return a; }, []);
    return fn(tree);
    }, [tree, search]);

  const toggle = (id: string) => setExpanded((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  if (loading) return <div className="page-shell" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}><div className="infra-topo"><p style={{ color: "var(--muted)", fontSize: 13 }}>Loading infrastructure topology…</p></div></div>;

  return (
    <div className="page-shell" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="infra-topo">
        <h2 style={{ color: "var(--cyan)", fontSize: 14, margin: 0, textTransform: "uppercase" as const, letterSpacing: "0.5px" }}>Infrastructure Topology</h2>
        {error && <p style={{ color: "var(--amber)", fontSize: 12 }}>{error}</p>}
        <div className="infra-topo__toolbar"><input type="text" placeholder="Search sites, zones, devices…" value={search} onChange={(e) => setSearch(e.target.value)} className="infra-topo__search" /></div>
        <div className="infra-topo__tree">
          {filteredTree.map((nd) => <ITNode key={nd.id} node={nd} expanded={expanded} onToggle={toggle} />)}
          {!filteredTree.length && <p className="infra-node__empty">No infrastructure matches your search.</p>}
        </div>
      </div>
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