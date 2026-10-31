"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { listFirewalls, listSops } from "@/lib/api";
import { getTopologySummary } from "@/lib/topologyApi";
import { CardShell, type CardState } from "./primitives";

/**
 * Executive Command Center coverage row: what the platform can actually see
 * beyond incidents. Firewall state (firewall gateway), how much of the estate
 * has recorded topology links, and the SOP library used for remediation.
 * Every number comes from its own API; a failed read says so.
 */

function stateOf(q: { isLoading: boolean; isError: boolean }, empty: boolean): CardState {
  if (q.isError) return "error";
  if (q.isLoading) return "loading";
  return empty ? "empty" : "ready";
}

function Stat({ label, value, tone, href }: { label: string; value: ReactNode; tone?: "good" | "warn" | "bad"; href?: string }) {
  const color = tone ? `var(--ecc-${tone})` : undefined;
  const body = (
    <div style={{ border: "1px solid var(--ecc-border)", borderRadius: 8, padding: "8px 10px" }}>
      <div style={{ fontSize: 11, color: "var(--ecc-text-muted)" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, color }}>{value}</div>
    </div>
  );
  return href ? <Link href={href} style={{ textDecoration: "none", color: "inherit" }}>{body}</Link> : body;
}

const grid = { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 } as const;

function FirewallsCard() {
  const q = useQuery({ queryKey: ["dashboard", "firewalls"], queryFn: listFirewalls, refetchInterval: 60_000 });
  const rows = q.data ?? [];
  const reporting = rows.filter((f) => f.evidence === "fresh");
  const outOfSync = reporting.filter((f) => f.ha_sync === "out_of_sync").length;
  const licence = rows.filter((f) => (f.licences ?? []).some((l) => l.status === "expired" || l.status === "expiring")).length;
  return (
    <CardShell
      title="Firewalls"
      info="Firewall state reported by the FortiGate, Palo Alto and SNMP adapters. A firewall with no recent report counts as not reporting, never as healthy."
      aside={<Link className="ecc-link" href="/infrastructure/firewalls">Firewalls →</Link>}
      state={stateOf(q, rows.length === 0)}
      emptyText="No firewalls yet. Add a FortiGate, Palo Alto or SNMP source."
      onRetry={() => q.refetch()}
      className="ecc-span-4"
    >
      <div style={grid}>
        <Stat label="Firewalls" value={rows.length} href="/infrastructure/firewalls" />
        <Stat label="Reporting (24 h)" value={`${reporting.length} of ${rows.length}`} tone={reporting.length < rows.length ? "warn" : "good"} />
        <Stat label="HA out of sync" value={outOfSync} tone={outOfSync ? "bad" : undefined} />
        <Stat label="Licence issues" value={licence} tone={licence ? "warn" : undefined} />
      </div>
    </CardShell>
  );
}

function TopologyCard() {
  const q = useQuery({ queryKey: ["dashboard", "topology-summary"], queryFn: () => getTopologySummary(), refetchInterval: 60_000 });
  const s = q.data;
  const linked = s?.devices_linked ?? null;
  const share = s && linked !== null && s.devices_total ? Math.round((linked / s.devices_total) * 100) : null;
  return (
    <CardShell
      title="Topology Coverage"
      info="Devices with at least one recorded link (LLDP/CDP, Meraki, controller or manual). Links are never inferred, so unlinked devices show unconnected on the map."
      aside={<Link className="ecc-link" href="/infrastructure-topology/live">Topology →</Link>}
      state={stateOf(q, s?.devices_total === 0)}
      emptyText="No devices discovered yet."
      onRetry={() => q.refetch()}
      className="ecc-span-4"
    >
      {s && (
        <>
          <div style={grid}>
            <Stat label="Devices linked" value={linked === null ? "—" : `${linked} of ${s.devices_total}`} tone={share !== null && share < 50 ? "warn" : undefined} />
            <Stat label="Recorded links" value={s.relationships_total} />
            <Stat label="Interfaces" value={s.interfaces_total} />
            <Stat label="Sources" value={s.monitoring_sources_total} href="/monitoring" />
          </div>
          {share !== null && share < 50 && (
            <p style={{ fontSize: 12, color: "var(--ecc-text-muted)", marginTop: 8 }}>
              {100 - share}% of devices have no recorded link. Run discovery with LLDP/CDP or connect a Meraki organization.
            </p>
          )}
        </>
      )}
    </CardShell>
  );
}

function SopLibraryCard() {
  const q = useQuery({ queryKey: ["dashboard", "sops"], queryFn: () => listSops({ limit: 200 }), refetchInterval: 5 * 60_000 });
  const sops = q.data ?? [];
  const active = sops.filter((s) => s.is_active && s.current_version_id);
  const byCat = new Map<string, number>();
  for (const s of active) byCat.set(s.category, (byCat.get(s.category) ?? 0) + 1);
  const top = [...byCat.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
  return (
    <CardShell
      title="SOP Library"
      info="Standard operating procedures available to incident recommendations and remediation."
      aside={<Link className="ecc-link" href="/sop-engine">SOP Engine →</Link>}
      state={stateOf(q, sops.length === 0)}
      emptyText="No SOPs in this tenant yet."
      onRetry={() => q.refetch()}
      className="ecc-span-4"
    >
      <div style={grid}>
        <Stat label="SOPs with a published version" value={`${active.length} of ${sops.length}`} href="/sop-engine" />
        <Stat label="Need approval to run" value={active.filter((s) => s.requires_approval).length} />
        {top.map(([cat, n]) => (
          <Stat key={cat} label={cat.charAt(0).toUpperCase() + cat.slice(1)} value={n} />
        ))}
      </div>
    </CardShell>
  );
}

export default function CoverageRow() {
  return (
    <div className="ecc-row">
      <FirewallsCard />
      <TopologyCard />
      <SopLibraryCard />
    </div>
  );
}
