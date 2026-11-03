"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { LifeBuoy } from "lucide-react";
import { getReplicationOverview, listReplicatedItems, type ReplicatedItem } from "@/lib/api";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import { Banner, DataTable, PageShell, Panel, Pill, SearchBox, ago, titleCaseWords, type Column } from "@/components/ui/PageKit";
import { PanelHeading, Props, SidePanel } from "../../infrastructure/backup/ui";
import { READINESS, READ_ONLY_NOTE, healthText, healthTone, lag, route } from "../drUi";

/**
 * /business-continuity/replicated-items — every machine Azure Site Recovery
 * replicates: source and target region, replication and failover health,
 * replication lag, last test failover and DR readiness, with filters kept in
 * the URL and a details drawer (health errors with Azure's recommended action).
 */

const columns: Column<ReplicatedItem>[] = [
  { key: "name", header: "Replicated item", sortValue: (i) => i.name, render: (i) => <><span className="pk-cell-main">{i.name}</span><span className="pk-cell-sub">{[i.replication_type, i.vault_name].filter(Boolean).join(" · ")}</span></> },
  { key: "route", header: "Source → target", sortValue: (i) => route(i.source_location, i.target_location), render: (i) => route(i.source_location, i.target_location) },
  { key: "health", header: "Replication health", sortValue: (i) => ["critical", "warning", null, "normal"].indexOf(i.replication_health), render: (i) => <Pill tone={healthTone(i.replication_health)}>{healthText(i.replication_health)}</Pill> },
  { key: "failover", header: "Failover health", sortValue: (i) => i.failover_health, render: (i) => <Pill tone={healthTone(i.failover_health)}>{healthText(i.failover_health)}</Pill> },
  { key: "active", header: "Active location", sortValue: (i) => i.active_location, render: (i) => titleCaseWords(i.active_location) || <span className="pk-muted">—</span> },
  { key: "lag", header: "Replication lag", numeric: true, sortValue: (i) => i.rpo_seconds, render: (i) => <span title={i.rpo_calculated_at ? `Calculated ${new Date(i.rpo_calculated_at).toLocaleString()}` : undefined}>{lag(i.rpo_seconds)}</span> },
  { key: "test", header: "Last test failover", sortValue: (i) => i.last_test_failover_at, render: (i) => (i.last_test_failover_at ? ago(i.last_test_failover_at) : <span className="pk-muted">Never</span>) },
  { key: "ready", header: "DR readiness", sortValue: (i) => ["not_ready", "failed_over", "ready"].indexOf(i.readiness), render: (i) => <span title={i.readiness_reasons.join("\n") || undefined}><Pill tone={READINESS[i.readiness].tone}>{READINESS[i.readiness].text}</Pill></span> },
];

const CSV: CsvColumn<ReplicatedItem>[] = [
  { header: "Replicated item", value: (i) => i.name },
  { header: "Vault", value: (i) => i.vault_name },
  { header: "Replication type", value: (i) => i.replication_type },
  { header: "Source resource", value: (i) => i.source_resource_id },
  { header: "Source region", value: (i) => i.source_location },
  { header: "Target region", value: (i) => i.target_location },
  { header: "Protection state", value: (i) => i.protection_state },
  { header: "Replication health", value: (i) => i.replication_health },
  { header: "Failover health", value: (i) => i.failover_health },
  { header: "Active location", value: (i) => i.active_location },
  { header: "Replication lag (s)", value: (i) => i.rpo_seconds },
  { header: "Last test failover (UTC)", value: (i) => i.last_test_failover_at },
  { header: "Last failover (UTC)", value: (i) => i.last_failover_at },
  { header: "Policy", value: (i) => i.policy_name },
  { header: "Recovery plans", value: (i) => i.recovery_plans.join("; ") },
  { header: "DR readiness", value: (i) => i.readiness },
  { header: "Readiness reasons", value: (i) => i.readiness_reasons.join("; ") },
];

function ItemDrawer({ item, onClose }: { item: ReplicatedItem; onClose: () => void }) {
  return (
    <SidePanel title={item.name} subtitle={[item.replication_type, route(item.source_location, item.target_location)].filter(Boolean).join(" · ")} onClose={onClose}>
      <p style={{ margin: "12px 0", display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Pill tone={READINESS[item.readiness].tone}>{READINESS[item.readiness].text}</Pill>
        <Pill tone={healthTone(item.replication_health)}>Replication: {healthText(item.replication_health)}</Pill>
      </p>
      {item.readiness_reasons.length > 0 && (
        <>
          <PanelHeading>Why it is not DR-ready</PanelHeading>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{item.readiness_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </>
      )}
      {item.health_errors.length > 0 && (
        <>
          <PanelHeading>Health errors reported by Site Recovery</PanelHeading>
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 10 }}>
            {item.health_errors.map((e, i) => (
              <li key={`${e.code}-${i}`} style={{ borderLeft: "3px solid var(--ecc-warn)", paddingLeft: 8 }}>
                <strong>{e.message ?? "Health error"}</strong>
                <div className="pk-muted">{[e.level, e.code && `code ${e.code}`, e.created_at && ago(e.created_at)].filter(Boolean).join(" · ")}</div>
                {e.recommended_action && <div style={{ marginTop: 4 }}>Recommended: {e.recommended_action}</div>}
              </li>
            ))}
          </ul>
        </>
      )}
      <PanelHeading>Replication</PanelHeading>
      <Props rows={[
        ["Protection state", item.protection_state_description ?? item.protection_state],
        ["Failover health", healthText(item.failover_health)],
        ["Active location", titleCaseWords(item.active_location) || null],
        ["Replication lag", item.rpo_seconds !== null ? `${lag(item.rpo_seconds)}${item.rpo_calculated_at ? ` (calculated ${ago(item.rpo_calculated_at)})` : ""}` : null],
        ["Last successful test failover", item.last_test_failover_at ? new Date(item.last_test_failover_at).toLocaleString() : "Never"],
        ["Last successful failover", item.last_failover_at ? new Date(item.last_failover_at).toLocaleString() : null],
        ["Operation running", item.current_scenario],
        ["Replication policy", item.policy_name],
        ["Recovery VM name", item.recovery_vm_name],
        ["Recovery plans", item.recovery_plans.length ? item.recovery_plans.join(", ") : "None"],
        ["Vault", item.vault_name],
        ["Source resource", item.source_resource_id ? <code key="r" style={{ wordBreak: "break-all" }}>{item.source_resource_id}</code> : null],
        ["Operations Azure allows now", item.allowed_operations.length ? item.allowed_operations.join(", ") : null],
      ]} />
      <p style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
        {item.device_id && <Link className="pk-button" href={`/infrastructure/hosts/${item.device_id}`}>View resource</Link>}
        <Link className="pk-button" href={`/infrastructure/backup/vaults/${item.vault_id}`}>View vault</Link>
      </p>
      <p className="pk-help">{READ_ONLY_NOTE}</p>
    </SidePanel>
  );
}

function ItemsContent() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const get = (k: string) => params.get(k) ?? "";
  const setParam = (updates: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(updates)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    const q = next.toString();
    router.replace(`${pathname}${q ? `?${q}` : ""}`, { scroll: false });
  };
  const overview = useQuery({ queryKey: ["dr", "overview"], queryFn: () => getReplicationOverview(), refetchInterval: 120_000 });
  const items = useQuery({ queryKey: ["dr", "items"], queryFn: () => listReplicatedItems(), enabled: !!overview.data?.configured, refetchInterval: 120_000 });
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<ReplicatedItem | null>(null);

  const all = items.data ?? [];
  const max = overview.data?.dr_test_max_age_days ?? 180;
  const health = get("health"), readiness = get("readiness"), source = get("source"), target = get("target"), test = get("test"), vault = get("vault");
  const rows = all.filter((i) =>
    (!health || (health === "unhealthy" ? i.replication_health === "warning" || i.replication_health === "critical" : health === "none" ? i.replication_health === null : i.replication_health === health))
    && (!readiness || i.readiness === readiness)
    && (!source || i.source_location === source)
    && (!target || i.target_location === target)
    && (!vault || i.vault_id === vault)
    && (!test || (test === "overdue" && (!i.last_test_failover_at || (Date.parse(overview.data!.generated_at) - Date.parse(i.last_test_failover_at)) / 86_400_000 > max)))
    && (!search || `${i.name} ${i.source_resource_id ?? ""} ${i.vault_name}`.toLowerCase().includes(search.toLowerCase())),
  );
  const opts = (pick: (i: ReplicatedItem) => string | null) => [...new Set(all.map(pick).filter((v): v is string => !!v))].sort();
  const anyFilter = ["health", "readiness", "source", "target", "test", "vault"].some((k) => params.get(k)) || !!search;
  const select = (key: string, label: string, allText: string, values: { value: string; label: string }[]) => (
    <select className="pk-input" aria-label={label} value={get(key)} onChange={(e) => setParam({ [key]: e.target.value })} style={{ width: "auto", maxWidth: 220 }}>
      <option value="">{allText}</option>
      {values.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
    </select>
  );

  return (
    <PageShell
      title="Replicated Items"
      icon={LifeBuoy}
      crumbs={[{ label: "Business Continuity", href: "/business-continuity" }, { label: "Replicated Items" }]}
      subtitle="Every machine Azure Site Recovery replicates, with its replication health, lag, failover state and DR readiness."
      meta={overview.data ? `Updated ${ago(overview.data.generated_at)}` : undefined}
    >
      {overview.data && !overview.data.configured && (
        <Banner tone="info">No Azure Backup source is connected. <Link className="ecc-link" href="/business-continuity">See the DR overview</Link> for setup.</Banner>
      )}
      {overview.data?.replication_status === "failed" && (
        <Banner tone="warn">Site Recovery could not be read for at least one vault (Site Recovery Reader role missing?). Rows below are from the last successful read.</Banner>
      )}
      {overview.data?.configured && (
        <Panel title={`${rows.length} of ${all.length} replicated items`} info="Select an item for its health errors, Azure's recommended action and why it is or is not DR-ready." state={items.isLoading ? "loading" : "ready"} className="pk-col-12">
          <div className="pk-toolbar">
            <SearchBox value={search} onChange={setSearch} placeholder="Search name, resource or vault" label="Search replicated items" />
            {select("readiness", "Filter by DR readiness", "Any readiness", Object.entries(READINESS).map(([k, v]) => ({ value: k, label: v.text })))}
            {select("health", "Filter by replication health", "Any replication health", [
              { value: "unhealthy", label: "Warning or critical" }, { value: "critical", label: "Critical" }, { value: "warning", label: "Warning" },
              { value: "normal", label: "Normal" }, { value: "none", label: "Not reported" },
            ])}
            {select("test", "Filter by test failover", "Any test failover", [{ value: "overdue", label: `None in ${max} days` }])}
            {select("source", "Filter by source region", "Any source region", opts((i) => i.source_location).map((v) => ({ value: v, label: v })))}
            {select("target", "Filter by target region", "Any target region", opts((i) => i.target_location).map((v) => ({ value: v, label: v })))}
            {select("vault", "Filter by vault", "Any vault", [...new Map(all.map((i) => [i.vault_id, i.vault_name])).entries()].map(([value, label]) => ({ value, label })))}
            {anyFilter && <button type="button" className="pk-button" onClick={() => { setSearch(""); router.replace(pathname, { scroll: false }); }}>Clear filters</button>}
            <button type="button" className="pk-button" disabled={rows.length === 0} onClick={() => downloadCsv(`replicated-items-${new Date().toISOString().slice(0, 10)}.csv`, CSV, rows)}>Export CSV</button>
          </div>
          <DataTable caption="Replicated items" columns={columns} rows={rows} rowKey={(i) => i.id} onRowClick={(i) => setSelected(i)} defaultSort={{ key: "ready", dir: "asc" }} pageSize={50} maxHeight="max(420px, calc(100vh - 380px))" emptyText={anyFilter ? "No replicated item matches these filters." : "No replicated items reported."} />
        </Panel>
      )}
      {selected && <ItemDrawer item={selected} onClose={() => setSelected(null)} />}
    </PageShell>
  );
}

export default function ReplicatedItemsPage() {
  return (
    <Suspense>
      <ItemsContent />
    </Suspense>
  );
}
