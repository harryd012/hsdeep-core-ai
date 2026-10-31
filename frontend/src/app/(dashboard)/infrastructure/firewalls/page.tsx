"use client";

import { Suspense, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Shield } from "lucide-react";
import { getFirewall, listFirewalls, type FirewallSummary, type FirewallTunnel } from "@/lib/api";
import { evidenceLabel, haLabel, licenceLabel, sessionsText, tunnelsText } from "@/lib/firewalls";
import {
  DataTable,
  PageShell,
  Panel,
  Pill,
  StatStrip,
  StatTile,
  ago,
  titleCaseWords,
  type Column,
} from "@/components/ui/PageKit";

/**
 * /infrastructure/firewalls: every firewall in the tenant with the state its
 * adapter reported (HA, sessions, CPU/memory, VPN tunnels, licences). Read
 * only. A firewall no adapter has reported for yet is listed with "No data".
 */

const pct = (v: number | null) => (v === null ? "No data" : `${Math.round(v)}%`);

const columns: Column<FirewallSummary>[] = [
  {
    key: "name",
    header: "Firewall",
    render: (f) => (
      <div>
        <div className="pk-cell-main">{f.name}</div>
        <div className="pk-cell-sub">{[f.vendor, f.model, f.firmware_version].filter(Boolean).join(" · ") || "—"}</div>
      </div>
    ),
    sortValue: (f) => f.name,
  },
  {
    key: "evidence",
    header: "Data",
    render: (f) => {
      const l = evidenceLabel(f);
      return <span title={f.observed_at ? `Last reported ${ago(f.observed_at)}` : "Never reported"}><Pill tone={l.tone}>{l.text}</Pill></span>;
    },
    sortValue: (f) => f.evidence,
  },
  { key: "ha", header: "HA", render: (f) => { const l = haLabel(f); return <Pill tone={l.tone}>{l.text}</Pill>; }, sortValue: (f) => f.ha_sync ?? "" },
  { key: "sessions", header: "Sessions", render: (f) => sessionsText(f), sortValue: (f) => f.session_count, numeric: true },
  { key: "cpu", header: "CPU", render: (f) => pct(f.cpu_percent), sortValue: (f) => f.cpu_percent, numeric: true },
  { key: "memory", header: "Memory", render: (f) => pct(f.memory_percent), sortValue: (f) => f.memory_percent, numeric: true },
  { key: "vpn", header: "VPN tunnels", render: (f) => tunnelsText(f), sortValue: (f) => f.tunnels_total },
  { key: "licences", header: "Licences", render: (f) => { const l = licenceLabel(f); return <Pill tone={l.tone}>{l.text}</Pill>; } },
];

const tunnelColumns: Column<FirewallTunnel>[] = [
  { key: "name", header: "Tunnel", render: (t) => <span className="pk-cell-main">{t.name}</span>, sortValue: (t) => t.name },
  {
    key: "status",
    header: "Status",
    render: (t) => (
      <Pill tone={t.status === "up" ? "good" : t.ever_up ? "warn" : "neutral"}>
        {t.status === "up" ? "Up" : t.ever_up ? "Down" : "Never up"}
      </Pill>
    ),
    sortValue: (t) => t.status,
  },
  { key: "gw", header: "Remote gateway", render: (t) => t.remote_gateway ?? "—" },
  { key: "p2", header: "Phase 2", render: (t) => (t.phase2_total === null ? "—" : `${t.phase2_up ?? 0} of ${t.phase2_total}`) },
  { key: "change", header: "Last change", render: (t) => ago(t.last_change_at), sortValue: (t) => t.last_change_at },
];

function FirewallDetailPanel({ deviceId }: { deviceId: string }) {
  const q = useQuery({ queryKey: ["firewall", deviceId], queryFn: () => getFirewall(deviceId), refetchInterval: 60_000 });
  const f = q.data;
  return (
    <Panel
      title={f ? `${f.name}: tunnels and licences` : "Firewall detail"}
      state={q.isError ? "error" : q.isLoading ? "loading" : "ready"}
      errorText="Could not load this firewall."
      onRetry={() => q.refetch()}
      className="pk-col-12"
    >
      {f && (
        <>
          <DataTable
            caption="VPN tunnels"
            columns={tunnelColumns}
            rows={f.tunnels}
            rowKey={(t) => t.name}
            defaultSort={{ key: "name", dir: "asc" }}
            emptyText={f.table_status.vpn ? "No VPN tunnels configured." : "This adapter has not reported VPN tunnels."}
          />
          <h3 className="pk-cell-main" style={{ margin: "16px 0 8px" }}>Licences</h3>
          {f.licences && f.licences.length > 0 ? (
            <ul className="pk-list">
              {f.licences.map((l) => (
                <li key={l.feature}>
                  <div className="pk-list__main">
                    <span className="pk-cell-main">{titleCaseWords(l.feature.replace(/_/g, " "))}</span>
                    <span className="pk-cell-sub"> · {l.expires_at ? `expires ${new Date(l.expires_at).toLocaleDateString()}` : "no expiry reported"}</span>
                  </div>
                  <div className="pk-list__aside">
                    <Pill tone={l.status === "expired" ? "bad" : l.status === "expiring" ? "warn" : l.status === "valid" ? "good" : "neutral"}>
                      {l.status === "unknown" ? "No data" : titleCaseWords(l.status)}
                    </Pill>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="pk-cell-sub">No licence data reported.</p>
          )}
        </>
      )}
    </Panel>
  );
}

function FirewallsContent() {
  const params = useSearchParams();
  const selected = params?.get("device") ?? null;
  const q = useQuery({ queryKey: ["firewalls"], queryFn: listFirewalls, refetchInterval: 60_000 });
  const rows = useMemo(() => q.data ?? [], [q.data]);

  const reporting = rows.filter((f) => f.evidence === "fresh").length;
  const outOfSync = rows.filter((f) => f.evidence === "fresh" && f.ha_sync === "out_of_sync").length;
  const licenceIssues = rows.filter((f) => (f.licences ?? []).some((l) => l.status === "expired" || l.status === "expiring")).length;
  const loading = q.isLoading;

  return (
    <PageShell
      title="Firewalls"
      icon={Shield}
      subtitle="HA, load, VPN tunnels and licences as each firewall reports them. Read only."
      crumbs={[{ label: "Infrastructure", href: "/infrastructure" }, { label: "Firewalls" }]}
    >
      <StatStrip>
        <StatTile label="Firewalls" info="Devices classified as firewalls in this tenant." value={String(rows.length)} loading={loading} />
        <StatTile label="Reporting (24 h)" info="Firewalls whose adapter reported HA, load or licence state in the last 24 hours." value={String(reporting)} loading={loading} sub={rows.length ? `${rows.length - reporting} with no recent data` : undefined} />
        <StatTile label="HA out of sync" info="Reporting HA clusters whose members are out of sync or have lost their peer." value={String(outOfSync)} loading={loading} tone={outOfSync ? "bad" : undefined} />
        <StatTile label="Licence issues" info="Firewalls with a licence or subscription expired or expiring within 30 days." value={String(licenceIssues)} loading={loading} tone={licenceIssues ? "warn" : undefined} />
      </StatStrip>
      <div className="pk-grid">
        <Panel
          title="All firewalls"
          info="Select a firewall to see its tunnels and licences."
          aside={q.data ? `${rows.length}` : undefined}
          state={q.isError ? "error" : loading ? "loading" : rows.length ? "ready" : "empty"}
          emptyText="No firewalls yet. Add a FortiGate, Palo Alto or SNMP source under Monitoring."
          errorText="Could not load firewalls."
          onRetry={() => q.refetch()}
          className="pk-col-12"
        >
          <DataTable
            caption="Firewalls"
            columns={columns}
            rows={rows}
            rowKey={(f) => f.device_id}
            rowHref={(f) => `/infrastructure/firewalls?device=${encodeURIComponent(f.device_id)}`}
            defaultSort={{ key: "name", dir: "asc" }}
          />
        </Panel>
        {selected && <FirewallDetailPanel deviceId={selected} />}
      </div>
    </PageShell>
  );
}

export default function FirewallsPage() {
  return (
    <Suspense fallback={null}>
      <FirewallsContent />
    </Suspense>
  );
}
