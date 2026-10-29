"use client";

import Link from "next/link";
import { useState, useEffect, useMemo } from "react";
import { Building2, Users, ChevronRight, ChevronDown, Shield, Network, type LucideIcon } from "lucide-react";
import { getTenantHealth, TenantHealthSummary, getMe, MeResponse, getTenantId } from "@/lib/api";
import { Banner, PageShell, Panel, Pill, SearchBox, StatStrip, StatTile, titleCaseWords, type Tone } from "@/components/ui/PageKit";

interface OrgNode {
  id: string;
  name: string;
  icon: LucideIcon;
  meta?: string;
  tone?: Tone;
  badge?: string;
  /** Existing page that manages this node; omitted when none exists yet. */
  href?: string;
  /** Shown instead of children when the backend has no model for this level. */
  pendingNote?: string;
  children?: OrgNode[];
}

function healthTone(score: number | null | undefined): Tone {
  if (score === null || score === undefined) return "neutral";
  return score >= 90 ? "good" : score >= 70 ? "warn" : "bad";
}

export default function OrganizationPage() {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root", "users"]));
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
        if (!cancelled) {
          setHealth(h);
          setUser(me);
          setError(h === null && me === null ? "Organization data is unavailable" : null);
        }
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
    const tenants = user?.accessible_tenants ?? [];
    return [{
      id: "root",
      name: tenantName,
      icon: Building2,
      meta: health ? `${health.sites_count} sites · ${health.devices_count} devices · ${health.sensors_count} sensors` : undefined,
      tone: healthTone(health?.health_score),
      badge: health ? (health.health_score === null ? "Health unknown" : `Health ${health.health_score}%`) : undefined,
      href: "/infrastructure",
      children: [
        {
          id: "users",
          name: "Users & team members",
          icon: Users,
          href: "/settings/users-roles",
          children: user
            ? [{
                id: "current-user",
                name: user.full_name ?? user.email,
                icon: Shield,
                meta: `${user.email} · ${user.roles.length ? user.roles.map(titleCaseWords).join(", ") : "No roles"}`,
                badge: "You",
                tone: "good" as Tone,
              }]
            : [],
        },
        ...(tenants.length > 1
          ? [{
              id: "tenants",
              name: "Accessible tenants",
              icon: Network,
              meta: `${tenants.length} tenants`,
              href: "/admin/customers",
              children: [],
            }]
          : []),
        {
          id: "departments",
          name: "Departments & business units",
          icon: Network,
          pendingNote: "Departments, teams and business units are not modelled in the backend yet, so nothing is shown here.",
        },
      ],
    }];
  }, [health, user]);

  const filteredTree = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tree;
    function filterNodes(nodes: OrgNode[]): OrgNode[] {
      return nodes.reduce<OrgNode[]>((acc, node) => {
        const kids = node.children ? filterNodes(node.children) : [];
        if (node.name.toLowerCase().includes(q) || kids.length > 0) acc.push({ ...node, children: kids.length > 0 ? kids : node.children });
        return acc;
      }, []);
    }
    return filterNodes(tree);
  }, [tree, search]);

  function toggle(id: string) {
    setExpanded((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }

  const v = (x: number | undefined) => (health && x !== undefined ? x.toLocaleString("en-US") : "—");

  return (
    <PageShell title="Organization" subtitle="The tenant, its people and the estate it owns.">
      {error && <Banner tone="warn">{error}</Banner>}
      <StatStrip>
        <StatTile label="Health score" value={health && health.health_score !== null ? `${health.health_score}%` : "—"} info={health && health.health_score === null ? "No device in this tenant has a known status yet, so there is no score." : `Tenant health score reported by the asset service${health ? `, from ${health.devices_scored} of ${health.devices_count} devices with a known status` : ""}.`} loading={loading} />
        <StatTile label="Sites" value={v(health?.sites_count)} info="Sites registered for this tenant." href="/infrastructure" loading={loading} />
        <StatTile label="Devices" value={v(health?.devices_count)} info="Devices registered for this tenant." href="/explorer" loading={loading} />
        <StatTile label="Sensors" value={v(health?.sensors_count)} info="Sensors collected for this tenant." href="/monitoring" loading={loading} />
        <StatTile label="Active alerts" value={v(health?.alerts_active)} alarm={!!health?.alerts_active} info="Alerts currently active." href="/alerts" loading={loading} />
      </StatStrip>

      <Panel title="Hierarchy" state={loading ? "loading" : "ready"} skeletonHeight={220}>
        <div className="pk-toolbar" style={{ marginBottom: 10 }}>
          <SearchBox value={search} onChange={setSearch} placeholder="Search organization" label="Search organization" />
        </div>
        {filteredTree.length === 0 ? (
          <div className="ecc-state" role="status">Nothing in the organization matches “{search}”.</div>
        ) : (
          <ul className="pk-tree" aria-label="Organization hierarchy">
            {filteredTree.map((node) => <TreeNode key={node.id} node={node} expanded={expanded} onToggle={toggle} />)}
          </ul>
        )}
      </Panel>
    </PageShell>
  );
}

function TreeNode({ node, expanded, onToggle }: { node: OrgNode; expanded: Set<string>; onToggle: (id: string) => void }) {
  const expandable = (node.children && node.children.length > 0) || !!node.pendingNote;
  const open = expanded.has(node.id);
  const Icon = node.icon;
  return (
    <li>
      <div className="pk-tree__row">
        {expandable ? (
          <button type="button" className="pk-tree__toggle" aria-expanded={open} aria-label={open ? `Collapse ${node.name}` : `Expand ${node.name}`} onClick={() => onToggle(node.id)}>
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        ) : (
          <span style={{ width: 18 }} />
        )}
        <span className="pk-tree__icon" aria-hidden="true"><Icon size={14} /></span>
        <div style={{ minWidth: 0 }}>
          <div className="pk-tree__name">{node.name}</div>
          {node.meta && <div className="pk-tree__meta">{node.meta}</div>}
        </div>
        <div className="pk-tree__actions">
          {node.badge && <Pill tone={node.tone ?? "neutral"}>{node.badge}</Pill>}
          {node.href && <Link className="pk-button" style={{ height: 26, fontSize: 12 }} href={node.href}>Manage</Link>}
        </div>
      </div>
      {open && node.pendingNote && <p className="pk-tree__meta" style={{ margin: "2px 0 8px 58px" }}>{node.pendingNote}</p>}
      {open && node.children && node.children.length > 0 && (
        <ul>
          {node.children.map((child) => <TreeNode key={child.id} node={child} expanded={expanded} onToggle={onToggle} />)}
        </ul>
      )}
    </li>
  );
}
