"use client";

import { ExternalLink, Gauge } from "lucide-react";

import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, type Column } from "@/components/ui/PageKit";
import PlatformLiveMetrics from "@/components/observability/PlatformLiveMetrics";
import PrometheusTargetsPanel from "@/components/observability/PrometheusTargetsPanel";
import { useAuth } from "@/lib/auth-context";
import {
  APPROVED_DECISIONS,
  AUDIT_FINDINGS,
  DELIVERY_STATUS_LABEL,
  DELIVERY_STATUS_TONE,
  METRIC_CATALOGUE,
  PLAN_PHASES,
  REFERENCES,
  grafanaUrl,
  phaseCounts,
  type CatalogueMetric,
  type PlanPhase,
} from "@/lib/observability/platformObservabilityPlan";

/**
 * Platform Observability - the Prometheus + Grafana audit and plan for
 * monitoring HSDEEP itself. Superusers only (platform internals, not tenant
 * data). The live section reads this backend process's own metrics through
 * the superuser-only summary API; windows, history and the other processes
 * belong in Grafana, linked when NEXT_PUBLIC_GRAFANA_URL is set.
 */

const GRAFANA_URL = grafanaUrl(process.env.NEXT_PUBLIC_GRAFANA_URL);

const SEVERITY_TONE = { high: "bad", medium: "warn", low: "neutral" } as const;

const phaseColumns: Column<PlanPhase>[] = [
  { key: "id", header: "Phase", width: 64, render: (p) => <span className="pk-mono">{p.id}</span>, sortValue: (p) => p.id },
  { key: "name", header: "Scope", render: (p) => (
      <>
        <span className="pk-cell-main">{p.name}</span>
        <span className="pk-cell-sub" style={{ whiteSpace: "normal" }}>{p.work}</span>
      </>
    ) },
  { key: "status", header: "Status", width: 220, render: (p) => (
      <>
        <Pill tone={DELIVERY_STATUS_TONE[p.status]}>{DELIVERY_STATUS_LABEL[p.status]}</Pill>
        <span className="pk-cell-sub" style={{ whiteSpace: "normal" }}>{p.note}</span>
      </>
    ) },
];

const metricColumns: Column<CatalogueMetric>[] = [
  { key: "name", header: "Metric", render: (m) => <span className="pk-mono">{m.name}</span>, sortValue: (m) => m.name },
  { key: "type", header: "Type", width: 100, render: (m) => m.type, sortValue: (m) => m.type },
  { key: "labels", header: "Labels", render: (m) => <span className="pk-mono pk-muted">{m.labels}</span> },
  { key: "state", header: "State", width: 150, sortValue: (m) => m.state, render: (m) =>
      m.state === "emitted"
        ? <Pill tone="good">Emitted</Pill>
        : <Pill tone="neutral">Declared, not yet recorded</Pill> },
];

export default function PlatformObservabilityPage() {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <PageShell title="Platform Observability" icon={Gauge}>
        <Panel title="Loading" state="loading" />
      </PageShell>
    );
  }
  if (!user?.is_superuser) {
    return (
      <PageShell title="Platform Observability" icon={Gauge}>
        <Banner tone="warn">This page is for platform administrators. It describes how HSDEEP itself is monitored, not your organization&apos;s data.</Banner>
      </PageShell>
    );
  }

  const counts = phaseCounts();
  const done = counts["implemented-tested"] + counts["implemented-not-live"];
  const groups = ["Prometheus", "Grafana", "Method"] as const;

  return (
    <PageShell
      title="Platform Observability"
      icon={Gauge}
      crumbs={[{ label: "Manage" }, { label: "Platform Observability" }]}
      subtitle="Prometheus and Grafana for the HSDEEP platform itself: API, worker and scheduler processes and their pipelines. Customer infrastructure stays in the Command Center."
      actions={
        GRAFANA_URL ? (
          <a className="pk-button pk-button--primary" href={GRAFANA_URL} target="_blank" rel="noopener noreferrer">
            Open Grafana <ExternalLink size={14} aria-hidden="true" />
          </a>
        ) : (
          <Pill tone="neutral">Grafana not configured</Pill>
        )
      }
      meta={<span style={{ overflowWrap: "anywhere" }}>Source: docs/design/PROMETHEUS_GRAFANA_IMPLEMENTATION_AUDIT.md</span>}
    >
      <Banner tone="info">
        <span style={{ overflowWrap: "anywhere" }}>
          Live figures below come from this backend process only, cumulative since it started. History, time windows and
          the worker and scheduler processes appear in Grafana once Phases 3–4 are deployed
          {GRAFANA_URL ? "" : " and NEXT_PUBLIC_GRAFANA_URL is set"}.
        </span>
      </Banner>

      <PrometheusTargetsPanel />

      <PlatformLiveMetrics />

      <h2 style={{ margin: "8px 0 0", fontSize: 15, fontWeight: 600 }}>Delivery plan</h2>
      <StatStrip>
        <StatTile label="Phases delivered" value={`${done} of ${PLAN_PHASES.length}`}
          info="Phases implemented and tested, or implemented and awaiting live verification in Prometheus."
          sub="Tested or awaiting live check" />
        <StatTile label="Partially done" value={String(counts.partial)} info="Phases with some of their scope delivered." />
        <StatTile label="Not started" value={String(counts["not-implemented"])} info="Planned phases with no code yet." />
        <StatTile label="Metrics emitted" value={String(METRIC_CATALOGUE.filter((m) => m.state === "emitted").length)}
          info="Platform metric families recorded by the backend today."
          sub={`of ${METRIC_CATALOGUE.length} in the catalogue`} />
      </StatStrip>

      <div className="pk-grid">
        <div className="pk-col-6">
          <Panel title="Scope" state="ready">
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.6 }}>
              <li><strong>Prometheus</strong> scrapes each process&apos;s own <span className="pk-mono">/metrics</span> (api, worker, scheduler) with a bearer token from a mounted secret.</li>
              <li><strong>Grafana</strong> is private, with anonymous access off, for platform engineering.</li>
              <li><strong>Collectors and PostgreSQL</strong> keep monitoring customer infrastructure, unchanged.</li>
              <li><strong>No tenant, user, device, IP or credential</strong> is ever a metric label.</li>
            </ul>
          </Panel>
        </div>
        <div className="pk-col-6">
          <Panel title="Approved decisions" state="ready">
            <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.6 }}>
              {APPROVED_DECISIONS.map((d) => <li key={d}>{d}</li>)}
            </ol>
          </Panel>
        </div>

        <div className="pk-col-12">
          <Panel title="Implementation plan" state="ready">
            <DataTable caption="Implementation phases" columns={phaseColumns} rows={PLAN_PHASES} rowKey={(p) => p.id} maxHeight="none" />
          </Panel>
        </div>

        <div className="pk-col-12">
          <Panel title="Audit findings" state="ready">
            <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 10 }}>
              {AUDIT_FINDINGS.map((f) => (
                <li key={f.title} style={{ display: "grid", gridTemplateColumns: "90px minmax(0, 1fr)", gap: 10, alignItems: "start" }}>
                  <Pill tone={SEVERITY_TONE[f.severity]}>{f.severity}</Pill>
                  <span style={{ fontSize: 13, overflowWrap: "anywhere" }}>
                    <strong>{f.title}.</strong> <span className="pk-muted">{f.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>

        <div className="pk-col-12">
          <Panel title="Metric catalogue" state="ready" info="Platform-level metrics only, with bounded labels. Emitted: recorded by the backend today.">
            <DataTable caption="Platform metrics" columns={metricColumns} rows={METRIC_CATALOGUE} rowKey={(m) => m.name} maxHeight="none" />
          </Panel>
        </div>

        <div className="pk-col-12">
          <Panel title="References" state="ready" info="Official Prometheus, Grafana and Google SRE documentation the plan follows.">
            <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(min(260px, 100%), 1fr))" }}>
              {groups.map((g) => (
                <div key={g}>
                  <h3 style={{ margin: "0 0 6px", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.04em" }} className="pk-muted">{g}</h3>
                  <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 8 }}>
                    {REFERENCES.filter((r) => r.group === g).map((r) => (
                      <li key={r.url} style={{ fontSize: 13, overflowWrap: "anywhere" }}>
                        <a href={r.url} target="_blank" rel="noopener noreferrer">{r.title}</a>
                        <span className="pk-cell-sub">{r.why}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </PageShell>
  );
}
