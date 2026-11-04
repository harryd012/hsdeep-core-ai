"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LifeBuoy } from "lucide-react";
import { getReplicationOverview, listRecoveryPlans, type RecoveryPlan } from "@/lib/api";
import { Banner, DataTable, PageShell, Panel, Pill, ago, type Column } from "@/components/ui/PageKit";
import { PanelHeading, Props, SidePanel } from "../../infrastructure/backup/ui";
import { READINESS, READ_ONLY_NOTE } from "../drUi";

/**
 * /business-continuity/recovery-plans — Site Recovery recovery plans: which
 * machines fail over together, when the plan was last tested or failed over,
 * and whether all its members are DR-ready.
 */

const columns: Column<RecoveryPlan>[] = [
  { key: "name", header: "Recovery plan", sortValue: (p) => p.name, render: (p) => <><span className="pk-cell-main">{p.name}</span><span className="pk-cell-sub">{[p.replication_providers.join(", "), p.vault_name].filter(Boolean).join(" · ")}</span></> },
  { key: "route", header: "Primary → recovery", sortValue: (p) => p.primary_location, render: (p) => `${p.primary_location ?? "?"} → ${p.recovery_location ?? "?"}` },
  { key: "items", header: "Items ready", numeric: true, sortValue: (p) => (p.items_found ? p.items_ready / p.items_found : null), render: (p) => `${p.items_ready} of ${p.items_total}` },
  { key: "groups", header: "Groups", numeric: true, sortValue: (p) => p.groups, render: (p) => p.groups },
  { key: "test", header: "Last test failover", sortValue: (p) => p.last_test_failover_at, render: (p) => (p.last_test_failover_at ? ago(p.last_test_failover_at) : <span className="pk-muted">Never</span>) },
  { key: "failover", header: "Last failover", sortValue: (p) => p.last_unplanned_failover_at ?? p.last_planned_failover_at, render: (p) => { const t = [p.last_planned_failover_at, p.last_unplanned_failover_at].filter(Boolean).sort().pop(); return t ? ago(t) : <span className="pk-muted">Never</span>; } },
  { key: "ready", header: "Readiness", sortValue: (p) => p.readiness, render: (p) => <span title={p.readiness_reasons.join("\n") || undefined}><Pill tone={READINESS[p.readiness].tone}>{READINESS[p.readiness].text}</Pill></span> },
];

export default function RecoveryPlansPage() {
  const overview = useQuery({ queryKey: ["dr", "overview"], queryFn: () => getReplicationOverview(), refetchInterval: 120_000 });
  const plans = useQuery({ queryKey: ["dr", "plans"], queryFn: () => listRecoveryPlans(), enabled: !!overview.data?.configured, refetchInterval: 120_000 });
  const [selected, setSelected] = useState<RecoveryPlan | null>(null);

  return (
    <PageShell
      title="Recovery Plans"
      icon={LifeBuoy}
      crumbs={[{ label: "Business Continuity", href: "/business-continuity" }, { label: "Recovery Plans" }]}
      subtitle="Ordered failover plans: which machines fail over together, when each plan was last tested, and whether all its machines are ready."
      meta={overview.data ? `Updated ${ago(overview.data.generated_at)}` : undefined}
    >
      {overview.data && !overview.data.configured && (
        <Banner tone="info">No Azure Backup source is connected. <Link className="ecc-link" href="/business-continuity">See the DR overview</Link> for setup.</Banner>
      )}
      {overview.data?.configured && (
        <Panel title={`${(plans.data ?? []).length} recovery plans`} info="A plan is ready when every member machine is DR-ready and the plan passed a test failover within the window. Select a plan for details." state={plans.isLoading ? "loading" : "ready"} className="pk-col-12">
          <DataTable caption="Recovery plans" columns={columns} rows={plans.data ?? []} rowKey={(p) => p.id} onRowClick={(p) => setSelected(p)} defaultSort={{ key: "name", dir: "asc" }} maxHeight="max(420px, calc(100vh - 360px))" emptyText="No recovery plans reported." />
        </Panel>
      )}
      {selected && (
        <SidePanel title={selected.name} subtitle={`${selected.primary_location ?? "?"} → ${selected.recovery_location ?? "?"}`} onClose={() => setSelected(null)}>
          <p style={{ margin: "12px 0" }}><Pill tone={READINESS[selected.readiness].tone}>{READINESS[selected.readiness].text}</Pill></p>
          {selected.readiness_reasons.length > 0 && (
            <>
              <PanelHeading>Why it is not ready</PanelHeading>
              <ul style={{ margin: 0, paddingLeft: 18 }}>{selected.readiness_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </>
          )}
          <PanelHeading>Plan</PanelHeading>
          <Props rows={[
            ["Vault", selected.vault_name],
            ["Replication", selected.replication_providers.join(", ") || null],
            ["Deployment model", selected.failover_deployment_model],
            ["Groups", String(selected.groups)],
            ["Machines", `${selected.items_total} (${selected.items_ready} DR-ready)`],
            ["Last test failover", selected.last_test_failover_at ? new Date(selected.last_test_failover_at).toLocaleString() : "Never"],
            ["Last planned failover", selected.last_planned_failover_at ? new Date(selected.last_planned_failover_at).toLocaleString() : "Never"],
            ["Last unplanned failover", selected.last_unplanned_failover_at ? new Date(selected.last_unplanned_failover_at).toLocaleString() : "Never"],
            ["Operation running", selected.current_scenario ? `${selected.current_scenario}${selected.current_scenario_status ? ` (${selected.current_scenario_status})` : ""}` : null],
            ["Operations Azure allows now", selected.allowed_operations.join(", ") || null],
          ]} />
          <p style={{ marginTop: 16 }}><Link className="pk-button" href={`/business-continuity/replicated-items?vault=${selected.vault_id}`}>View the vault&apos;s replicated items</Link></p>
          <p className="pk-help">{READ_ONLY_NOTE}</p>
        </SidePanel>
      )}
    </PageShell>
  );
}
