"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AppWindow, Boxes, Cloud, Container, Database, HardDrive, HelpCircle, Laptop, Layers, Network, Plug, Server,
  ShieldCheck, Wifi, type LucideIcon,
} from "lucide-react";
import { Banner, PageShell, Panel, StatStrip, StatTile, titleCaseWords } from "@/components/ui/PageKit";
import HealthCategoryCard, { type HealthCounts } from "@/components/infrastructure/HealthCategoryCard";
import { getInfrastructureOverview } from "@/lib/api";
import UniversalCollectionPanel from "@/components/universal-collection/UniversalCollectionPanel";
import CanonicalTopologyPanel from "@/components/infrastructure/CanonicalTopologyPanel";
import NetworkIntelligencePanel from "@/components/network-intelligence/NetworkIntelligencePanel";

/**
 * /infrastructure — infrastructure health overview.
 *
 * One card per resource family (servers, network, security, cloud ...) and per
 * cloud provider and service, each split by health, from the canonical
 * overview (`/api/infrastructure/overview`: `devices.by_category`,
 * `devices.cloud`, `monitoring_sources`). Counts are the backend's buckets of
 * real device status; an unrecognised status is "unknown", never healthy, and
 * a provider with no resources says "not connected" rather than showing 0.
 */

const FAMILY: Record<string, { label: string; icon: LucideIcon; href?: string }> = {
  compute: { label: "Servers", icon: Server, href: "/infrastructure/servers" },
  network: { label: "Network devices", icon: Network, href: "/network" },
  security: { label: "Firewalls & security", icon: ShieldCheck, href: "/infrastructure/firewalls" },
  wireless: { label: "Wireless", icon: Wifi, href: "/infrastructure/hosts" },
  virtualization: { label: "Virtualization", icon: Layers, href: "/infrastructure/hosts" },
  container: { label: "Containers", icon: Container, href: "/infrastructure/hosts" },
  endpoint: { label: "Endpoints", icon: Laptop, href: "/infrastructure/hosts" },
  storage: { label: "Storage", icon: HardDrive, href: "/infrastructure/hosts" },
  database: { label: "Databases", icon: Database, href: "/infrastructure/hosts" },
  application: { label: "Applications", icon: AppWindow, href: "/infrastructure/hosts" },
  cloud: { label: "Cloud resources", icon: Cloud, href: "/infrastructure/universal" },
  unknown: { label: "Unclassified", icon: HelpCircle, href: "/infrastructure/hosts" },
};

const PROVIDERS: { key: string; label: string }[] = [
  { key: "aws", label: "AWS" },
  { key: "azure", label: "Microsoft Azure" },
  { key: "gcp", label: "Google Cloud" },
];

const SERVICE: Record<string, { label: string; icon: LucideIcon }> = {
  virtual_machines: { label: "Virtual machines", icon: Server },
  networking: { label: "Networking", icon: Network },
  security: { label: "Security", icon: ShieldCheck },
  load_balancing: { label: "Load balancing", icon: Boxes },
  other: { label: "Other services", icon: Cloud },
};

type CloudProvider = HealthCounts & { services: Record<string, HealthCounts> };

function asCounts(v: unknown): HealthCounts | null {
  const o = v as Partial<HealthCounts> | null | undefined;
  return o && typeof o.total === "number" ? (o as HealthCounts) : null;
}

export default function InfrastructurePage() {
  const overview = useQuery({ queryKey: ["infrastructure-overview"], queryFn: () => getInfrastructureOverview(), refetchInterval: 60_000 });
  const data = overview.data;
  const loading = overview.isLoading;
  const byCategory = (data?.devices.by_category ?? {}) as Record<string, HealthCounts>;
  const providers = ((data?.devices.cloud as { providers?: Record<string, CloudProvider> } | undefined)?.providers) ?? {};
  const sources = data?.monitoring_sources as { total?: number; active?: number; failing?: number; items?: { effective_health: string }[] } | undefined;
  const sourceCounts: HealthCounts | null = sources?.items
    ? sources.items.reduce<HealthCounts>((acc, s) => {
        acc.total += 1;
        if (s.effective_health === "healthy") acc.healthy += 1;
        else if (s.effective_health === "failing") acc.critical += 1;
        else acc.unknown += 1;
        return acc;
      }, { total: 0, healthy: 0, warning: 0, critical: 0, unknown: 0 })
    : null;
  const h = data?.health;
  const families = Object.keys(byCategory).filter((f) => f !== "cloud");
  const extraProviders = Object.keys(providers).filter((p) => !PROVIDERS.some((x) => x.key === p));

  return (
    <PageShell
      title="Infrastructure"
      icon={Server}
      crumbs={[{ label: "Infrastructure" }]}
      subtitle="Health of every server, network device, firewall and cloud resource the platform monitors."
    >
      {overview.isError && <Banner tone="warn">The infrastructure overview could not be loaded{overview.error instanceof Error ? `: ${overview.error.message}` : "."}</Banner>}

      <StatStrip>
        <StatTile label="Devices" value={h ? h.total_devices.toLocaleString("en-US") : "—"} loading={loading}
          sub={h ? `${h.critical_devices} critical · ${h.warning_devices} warning · ${h.unknown_devices} unknown` : undefined}
          info="Every device and cloud resource, bucketed by its reported status." />
        <StatTile label="Sites" value={h ? String(h.total_sites) : "—"} loading={loading} sub={h && h.critical_sites ? `${h.critical_sites} critical` : undefined} info="Sites with their device health." />
        <StatTile label="Sensors down" value={h ? String(h.down_sensors) : "—"} loading={loading} sub={h ? `of ${h.total_sensors.toLocaleString("en-US")} sensors` : undefined} info="Sensors whose last reading is down or critical." />
        <StatTile label="Active alerts" value={h ? String(h.active_alerts) : "—"} loading={loading} sub={h ? `${h.critical_alerts} critical` : undefined} info="Open or acknowledged alerts." />
        <StatTile label="Open incidents" value={h ? String(h.open_incidents) : "—"} loading={loading} info="Incidents not yet resolved or closed." />
        <StatTile label="Failing sources" value={sources?.failing != null ? String(sources.failing) : "—"} loading={loading}
          sub={sources?.total != null ? `of ${sources.total} data sources` : undefined} info="Sources whose latest collection failed or that keep failing." />
      </StatStrip>

      <Panel title="Current health overview" info="Each card splits one kind of infrastructure by health: critical, warning, healthy and unknown (no recognised status). Select a card to drill down."
        state={data ? "ready" : overview.isError ? "error" : "loading"} onRetry={() => overview.refetch()} skeletonHeight={180}>
        {families.length === 0 ? (
          <p className="pk-muted" style={{ margin: 0 }}>No devices yet. Add a monitoring source or run discovery to start building the inventory.</p>
        ) : (
          <div className="ho-grid">
            {families.map((f) => {
              const meta = FAMILY[f] ?? { label: titleCaseWords(f), icon: HelpCircle, href: "/infrastructure/hosts" };
              return <HealthCategoryCard key={f} title={meta.label} icon={meta.icon} counts={byCategory[f]} href={meta.href} />;
            })}
            <HealthCategoryCard title="Data sources" icon={Plug} counts={sourceCounts} href="/monitoring"
              note={sourceCounts && sourceCounts.total === 0 ? "No data sources yet." : undefined} />
          </div>
        )}
      </Panel>

      <Panel title="Clouds" info="Cloud resources per provider and service, from the AWS, Azure and Google Cloud connectors. A provider with no resources is not connected (or has found nothing yet)."
        state={data ? "ready" : overview.isError ? "error" : "loading"} onRetry={() => overview.refetch()} skeletonHeight={160}>
        <div className="ho-grid">
          {[...PROVIDERS, ...extraProviders.map((k) => ({ key: k, label: k === "unknown" ? "Unidentified provider" : k.toUpperCase() }))].map((p) => (
            <HealthCategoryCard key={p.key} title={p.label} icon={Cloud} counts={asCounts(providers[p.key])}
              href={providers[p.key] ? "/infrastructure/universal" : undefined}
              note={providers[p.key] ? undefined : "Not connected"} />
          ))}
        </div>
        {Object.entries(providers).map(([key, prov]) => (
          <div key={key} className="ho-provider" style={{ marginTop: 16 }}>
            <h3 className="ho-section">{PROVIDERS.find((p) => p.key === key)?.label ?? key.toUpperCase()} services</h3>
            <div className="ho-grid">
              {Object.entries(prov.services).map(([svc, counts]) => {
                const meta = SERVICE[svc] ?? { label: titleCaseWords(svc), icon: Cloud };
                return <HealthCategoryCard key={svc} title={meta.label} icon={meta.icon} counts={counts} href="/infrastructure/universal" />;
              })}
            </div>
          </div>
        ))}
      </Panel>

      {/* Existing diagnostics, unchanged: collection readiness, canonical topology, SD-WAN / SASE evaluation. */}
      <UniversalCollectionPanel />
      <CanonicalTopologyPanel />
      <NetworkIntelligencePanel />
    </PageShell>
  );
}
