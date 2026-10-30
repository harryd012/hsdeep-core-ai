"use client";

import Link from "next/link";
import type { ComponentType, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, BellRing, BookOpenCheck, FileBarChart, Server } from "lucide-react";
import { getAlertSummary, getCollectorHealth, getExplorerHosts, listRunbookHistory, listSops } from "@/lib/api";
import { PageShell, Pill, type Tone } from "@/components/ui/PageKit";

/** One headline per report, read live, so the index already answers "is anything wrong?". */
function ReportCard({
  title,
  desc,
  href,
  icon: Icon,
  headline,
  detail,
  status,
}: {
  title: string;
  desc: string;
  href: string;
  icon: ComponentType<{ size?: number }>;
  headline: string;
  detail?: ReactNode;
  status?: { tone: Tone; label: string };
}) {
  return (
    <Link href={href} className="pk-tile pk-report-card">
      <div className="pk-report-card__top">
        <span className="pk-tile__icon" aria-hidden="true">
          <Icon size={16} />
        </span>
        {status && <Pill tone={status.tone}>{status.label}</Pill>}
      </div>
      <h2 className="pk-tile__title">{title}</h2>
      <p className="pk-report-card__headline">{headline}</p>
      {detail && <p className="pk-report-card__detail">{detail}</p>}
      <p className="pk-tile__desc">{desc}</p>
      <span className="pk-tile__cta">Open report →</span>
    </Link>
  );
}

const n = (v: number) => v.toLocaleString("en-US");

export default function ReportsPage() {
  const alerts = useQuery({ queryKey: ["reports", "alert-summary"], queryFn: () => getAlertSummary() });
  const collectors = useQuery({ queryKey: ["reports", "collector-health", "7d"], queryFn: () => getCollectorHealth(undefined, 24 * 7) });
  const hosts = useQuery({ queryKey: ["reports", "infrastructure-headline"], queryFn: () => getExplorerHosts({ limit: 1 }) });
  const sops = useQuery({ queryKey: ["reports", "sops"], queryFn: () => listSops({ limit: 500 }) });
  const runs = useQuery({ queryKey: ["reports", "runbook-history"], queryFn: () => listRunbookHistory(500) });

  const a = alerts.data;
  const c = collectors.data;
  const runsTotal = c?.collectors.reduce((s, x) => s + x.total_runs, 0) ?? 0;
  const runsOk = c?.collectors.reduce((s, x) => s + x.successful_runs, 0) ?? 0;
  const h = hosts.data;
  const hostCount = (dim: "health" | "status", v: string) => h?.facets[dim].find((f) => f.value === v)?.count ?? 0;
  const pending = runs.data?.filter((r) => r.status === "pending_approval").length ?? 0;

  return (
    <PageShell
      title="Reports"
      icon={FileBarChart}
      subtitle="Operational reports built from this tenant's collected data. Each card shows the live headline; open a report for the breakdown."
    >
      <div className="pk-tiles pk-tiles--reports">
        <ReportCard
          title="Infrastructure Summary"
          href="/reports/infrastructure-summary"
          icon={Server}
          desc="Inventory by type, vendor and site, alert health and telemetry freshness."
          headline={h ? `${n(h.total)} hosts` : "—"}
          detail={h ? `${n(hostCount("health", "critical"))} with critical alerts · ${n(hostCount("status", "down"))} down` : undefined}
          status={h ? (hostCount("health", "critical") > 0 ? { tone: "bad", label: "Attention" } : { tone: "good", label: "Healthy" }) : undefined}
        />
        <ReportCard
          title="Alert Analysis"
          href="/reports/alert-analysis"
          icon={BellRing}
          desc="Severity and status breakdown, alert activity, top sources, devices and the noisiest alerts."
          headline={a ? `${n(a.total)} alerts` : "—"}
          detail={a ? `${n(a.by_severity.critical)} critical · ${n(a.by_status.open)} open` : undefined}
          status={a ? (a.by_status.open > 0 ? { tone: "warn", label: `${n(a.by_status.open)} open` } : { tone: "good", label: "All handled" }) : undefined}
        />
        <ReportCard
          title="Collector Performance"
          href="/reports/collector-performance"
          icon={Activity}
          desc="Success rate per source, why runs fail and the full run history."
          headline={c ? (runsTotal ? `${Math.round((runsOk / runsTotal) * 100)}% success` : "No runs") : "—"}
          detail={c ? `${c.failing_count} failing · ${c.healthy_count} healthy · last 7 days` : undefined}
          status={c ? (c.failing_count > 0 ? { tone: "bad", label: `${c.failing_count} failing` } : { tone: "good", label: "Collecting" }) : undefined}
        />
        <ReportCard
          title="SOP Execution Log"
          href="/reports/sop-execution-log"
          icon={BookOpenCheck}
          desc="Runbook executions with approvals and outcomes, and the SOP catalogue behind them."
          headline={sops.data ? `${n(sops.data.length)} SOPs` : "—"}
          detail={runs.data ? `${n(runs.data.length)} runbook execution${runs.data.length === 1 ? "" : "s"}` : undefined}
          status={runs.data ? (pending > 0 ? { tone: "warn", label: `${pending} awaiting approval` } : { tone: "neutral", label: "No pending" }) : undefined}
        />
      </div>
    </PageShell>
  );
}
