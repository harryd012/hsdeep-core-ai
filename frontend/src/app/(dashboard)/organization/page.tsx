"use client";

import { useState, useEffect, useMemo } from "react";
import {
    Building2, Users, ChevronRight, ChevronDown, Plus, Eye,
  Shield, Server, type LucideIcon,
} from "lucide-react";
import {
  getTenantHealth, TenantHealthSummary,
  getMe, MeResponse,
  getTenantId,
} from "@/lib/api";

interface OrgNode {
  id: string;
  name: string;
  type: "organization" | "business_unit" | "department" | "team" | "user" | "placeholder";
  icon: LucideIcon;
  meta?: string;
  childCount?: number;
  children?: OrgNode[];
  status?: string;
}

export default function OrganizationPage() {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root"]));
  const [health, setHealth] = useState<TenantHealthSummary | null>(null);
  const [user, setUser] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [h, me] = await Promise.all([
          getTenantHealth().catch(() => null),
          getMe().catch(() => null),
        ]);
        if (!cancelled) { setHealth(h); setUser(me); setError(null); }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const timer = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  const tree = useMemo((): OrgNode[] => {
    const tenantId = getTenantId();
    const tenantName = user?.tenant_name ?? (tenantId === "00000000-0000-0000-0000-000000000000" ? "Default Organization" : `Tenant ${tenantId.slice(0, 8)}`);
    return [{
      id: "root", name: tenantName, type: "organization", icon: Building2,
      meta: health ? `Health: ${health.health_score}%` : undefined,
      childCount: health ? health.sites_count + health.devices_count : undefined,
      status: health?.health_score && health.health_score >= 90 ? "healthy" : health?.health_score && health.health_score >= 70 ? "warning" : "critical",
      children: [
        { id: "users", name: "Users & Team Members", type: "team", icon: Users, children: [
          { id: "current-user", name: user?.full_name ?? user?.email ?? "Current User", type: "user", icon: Shield, meta: user?.roles?.join(", ") ?? "operator", status: "healthy" },
        ]},
        { id: "dept-placeholder", name: "Departments", type: "placeholder", icon: Server, meta: "Backend model pending", children: [] },
      ],
    }];
  }, [health, user]);

  const filteredTree = useMemo(() => {
    if (!search.trim()) return tree;
    const q = search.toLowerCase();
    function filterNodes(nodes: OrgNode[]): OrgNode[] {
      return nodes.reduce<OrgNode[]>((acc, node) => {
        const matches = node.name.toLowerCase().includes(q) || node.type.includes(q);
        const filteredChildren = node.children ? filterNodes(node.children) : [];
        if (matches || filteredChildren.length > 0) acc.push({ ...node, children: filteredChildren.length > 0 ? filteredChildren : node.children });
        return acc;
      }, []);
    }
    return filterNodes(tree);
  }, [tree, search]);

  function toggle(id: string) {
    setExpanded((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }

  if (loading) return <div className="page-shell" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}><div className="infra-topo"><p style={{ color: "var(--muted)", fontSize: 13 }}>Loading organization hierarchy…</p></div></div>;

  return (
    <div className="page-shell" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="org-hierarchy">
        <h2 style={{ color: "var(--cyan)", fontSize: 14, margin: 0, textTransform: "uppercase", letterSpacing: "0.5px" }}>Organization Hierarchy</h2>
        {error && <p style={{ color: "var(--amber)", fontSize: 12 }}>{error}</p>}
        <div className="org-hierarchy__toolbar">
          <input type="text" placeholder="Search organization nodes…" value={search} onChange={(e) => setSearch(e.target.value)} className="org-hierarchy__search" />
        </div>
        <div className="org-hierarchy__tree">
          {filteredTree.map((node) => <OrgTreeNode key={node.id} node={node} depth={0} expanded={expanded} onToggle={toggle} />)}
          {filteredTree.length === 0 && <p className="org-node__empty">No organization nodes match your search.</p>}
        </div>
      </div>
    </div>
  );
}

function OrgTreeNode({ node, depth, expanded, onToggle }: { node: OrgNode; depth: number; expanded: Set<string>; onToggle: (id: string) => void }) {
  const isExpanded = expanded.has(node.id);
  const hasChildren = node.children && node.children.length > 0;
  const isPlaceholder = node.type === "placeholder";
  const Icon = node.icon;
  const statusColor = node.status === "healthy" ? "var(--green)" : node.status === "warning" ? "var(--amber)" : node.status === "critical" ? "var(--red)" : undefined;

  return (
    <div className="org-node">
      <div className="org-node__header" onClick={() => (hasChildren || isPlaceholder) ? onToggle(node.id) : null}>
        {(hasChildren || isPlaceholder) ? (isExpanded ? <ChevronDown size={14} className="org-node__chev" /> : <ChevronRight size={14} className="org-node__chev" />) : <span style={{ width: 14 }} />}
        <div className="org-node__icon"><Icon size={14} /></div>
        <div className="org-node__info">
          <span className="org-node__name">{node.name}</span>
          {node.meta && <span className="org-node__meta">{node.meta}</span>}
        </div>
        {statusColor && <span className="infra-node__status" style={{ color: statusColor }}><i className="infra-node__dot" style={{ background: statusColor }} /></span>}
        <div className="org-node__actions">
          <button className="org-node__action" onClick={(e) => e.stopPropagation()}><Eye size={11} /> View</button>
          {depth < 2 && <button className="org-node__action" onClick={(e) => e.stopPropagation()} title={isPlaceholder ? "Backend model pending" : "Add child"}><Plus size={11} /> Add</button>}
        </div>
      </div>
      {isExpanded && (hasChildren || isPlaceholder) && (
        <div className="org-node__children">
          {isPlaceholder && <p className="org-node__empty">This hierarchy level is not yet supported by the backend. Department, team, and business unit models will be added in a future migration.</p>}
          {hasChildren && node.children!.map((child) => <OrgTreeNode key={child.id} node={child} depth={depth + 1} expanded={expanded} onToggle={onToggle} />)}
        </div>
      )}
    </div>
  );
}