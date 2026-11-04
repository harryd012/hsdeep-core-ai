"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { LifeBuoy } from "lucide-react";
import { getReplicationOverview, type ReplicationOverview } from "@/lib/api";
import { Banner, DataTable, PageShell, Panel, StatStrip, StatTile, ago, type Column } from "@/components/ui/PageKit";
import { READ_ONLY_NOTE, lag } from "./drUi";

/**
 * /business-continuity — disaster-recovery overview: replication health,
 * failover state and DR readiness of every machine Azure Site Recovery
 * replicates, and of the recovery plans. Every figure comes from what Site
 * Recovery reported; DR readiness is the platform rule described below.
 */

type RegionRow = ReplicationOverview["by_region"][number];

const regionColumns: Column<RegionRow>[] = [
  { key: "source", header: "Source region", sortValue: (r) => r.source_location, render: (r) => r.source_location ?? <span className="pk-muted">Not reported</span> },
  { key: "target", header: "Target region", sortValue: (r) => r.target_location, render: (r) => r.target_location ?? <span className="pk-muted">Not reported</span> },
  { key: "items", header: "Replicated items", numeric: true, sortValue: (r) => r.items, render: (r) => r.items },
  { key: "ready", header: "DR-ready", numeric: true, sortValue: (r) => (r.items ? r.ready / r.items : null), render: (r) => `${r.ready} (${Math.round((r.ready / r.items) * 100)}%)` },
  { key: "unhealthy", header: "Unhealthy replication", numeric: true, sortValue: (r) => r.unhealthy, render: (r) => (r.unhealthy ? <span style={{ color: "var(--ecc-bad)" }}>{r.unhealthy}</span> : "0") },
];

const ITEMS = "/business-continuity/replicated-items";

export default function DrOverviewPage() {
  const q = useQuery({ queryKey: ["dr", "overview"], queryFn: () => getReplicationOverview(), refetchInterval: 120_000 });
  const o = q.data;
  const has = !!o && o.items_total > 0;
  const n = (v: number | undefined) => (has && v !== undefined ? v.toLocaleString("en-US") : "—");

  return (
    <PageShell
      title="Disaster Recovery"
      icon={LifeBuoy}
      crumbs={[{ label: "Business Continuity" }, { label: "DR Overview" }]}
      subtitle="Which machines are replicated to a recovery region, whether replication is healthy, and whether each one is ready to fail over."
      meta={o ? `Updated ${ago(o.generated_at)}` : undefined}
    >
      {q.error && <Banner tone="bad">Disaster-recovery data could not be loaded: {q.error instanceof Error ? q.error.message : "request failed"}.</Banner>}
      {o && !o.configured && (
        <Banner tone="info">
          No Azure Backup source is connected. Site Recovery is read through it: <Link className="ecc-link" href="/infrastructure/backup">connect an Azure subscription</Link> and grant its service principal the <strong>Site Recovery Reader</strong> role.
        </Banner>
      )}
      {o?.configured && o.replication_status === "failed" && (
        <Banner tone="warn">
          Site Recovery could not be read for at least one vault. Grant the Azure Backup source&apos;s service principal the built-in <strong>Site Recovery Reader</strong> role on the vaults (or the subscription). Figures below are from the last successful read.
        </Banner>
      )}
      {o?.configured && o.replication_status !== "failed" && o.items_total === 0 && (
        <Banner tone="info">
          {o.replication_status === null ? "Site Recovery has not been read yet; it is read with the next Azure Backup collection." : "No replicated machines were found in the connected Recovery Services vaults."}
        </Banner>
      )}

      <StatStrip>
        <StatTile label="Replicated items" value={n(o?.items_total)} href={has ? ITEMS : undefined} info="Machines Site Recovery replicates to a recovery region." loading={q.isLoading} naText="None" />
        <StatTile label="DR-ready" value={has && o!.readiness_pct !== null ? `${o!.readiness_pct}%` : "—"} sub={has ? `${o!.ready} of ${o!.items_total}` : undefined} tone="good" href={has ? `${ITEMS}?readiness=ready` : undefined} info={`Healthy replication, failover health not degraded, running in the primary region, and a successful test failover within ${o?.dr_test_max_age_days ?? 180} days.`} loading={q.isLoading} naText="None" />
        <StatTile label="Replication unhealthy" value={has ? String(o!.health_warning + o!.health_critical) : "—"} sub={has ? `${o!.health_critical} critical · ${o!.health_warning} warning` : undefined} alarm={(o?.health_critical ?? 0) + (o?.health_warning ?? 0) > 0} href={has ? `${ITEMS}?health=unhealthy` : undefined} info="Replication health as Site Recovery reports it." loading={q.isLoading} naText="None" />
        <StatTile label="Failed over" value={n(o?.failed_over)} tone="warn" href={has ? `${ITEMS}?readiness=failed_over` : undefined} info="Machines currently running in their recovery region." loading={q.isLoading} naText="None" />
        <StatTile label="Test failover overdue" value={has ? String(o!.never_tested + o!.test_overdue) : "—"} sub={has ? `${o!.never_tested} never tested` : undefined} alarm={(o?.never_tested ?? 0) + (o?.test_overdue ?? 0) > 0} href={has ? `${ITEMS}?test=overdue` : undefined} info={`No successful test failover in the last ${o?.dr_test_max_age_days ?? 180} days.`} loading={q.isLoading} naText="None" />
        <StatTile label="Largest replication lag" value={has ? lag(o!.max_rpo_seconds) : "—"} info="The highest replication lag (RPO) Site Recovery last calculated for any machine." loading={q.isLoading} naText="None" />
        <StatTile label="Recovery plans ready" value={o && o.plans_total ? `${o.plans_ready} of ${o.plans_total}` : "—"} href={o?.plans_total ? "/business-continuity/recovery-plans" : undefined} info="Plans whose members are all DR-ready and that passed a test failover in the window." loading={q.isLoading} naText="None" />
      </StatStrip>

      {has && (
        <div className="pk-grid">
          <Panel title="By region pair" info="Replicated machines by source and recovery region." state="ready" className="pk-col-12">
            <DataTable caption="Replication by region pair" columns={regionColumns} rows={o!.by_region} rowKey={(r) => `${r.source_location}->${r.target_location}`} rowHref={(r) => (r.source_location ? `${ITEMS}?source=${encodeURIComponent(r.source_location)}` : null)} defaultSort={{ key: "items", dir: "desc" }} maxHeight={320} emptyText="—" />
          </Panel>
        </div>
      )}
      <p className="pk-help">
        DR readiness is a platform rule, not an Azure verdict: healthy replication, failover health not degraded, running in the primary region and a successful
        test failover within {o?.dr_test_max_age_days ?? 180} days. {READ_ONLY_NOTE}
      </p>
    </PageShell>
  );
}
