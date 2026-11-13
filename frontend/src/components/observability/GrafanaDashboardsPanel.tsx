"use client";

import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { Panel, Pill, Tabs } from "@/components/ui/PageKit";
import { GRAFANA_DASHBOARDS, grafanaDashboardUrl } from "@/lib/observability/platformMetrics";

/**
 * Links to the provisioned HSDEEP Grafana dashboards, opened with the chosen
 * time range. Linked, not embedded: Grafana runs with anonymous access off and
 * default frame protection, so an iframe would need either anonymous access or
 * shared sign-in, and neither is configured. No Grafana credential reaches the
 * browser; Grafana asks the user to sign in itself.
 */

const RANGES = [
  { key: "now-1h", label: "1 h" },
  { key: "now-6h", label: "6 h" },
  { key: "now-24h", label: "24 h" },
  { key: "now-7d", label: "7 d" },
] as const;

type RangeKey = (typeof RANGES)[number]["key"];

export default function GrafanaDashboardsPanel({ grafanaUrl }: { grafanaUrl: string | null }) {
  const [range, setRange] = useState<RangeKey>("now-6h");

  if (!grafanaUrl) {
    return <Panel title="Grafana dashboards" state="empty"
      emptyText="Grafana is not linked: set NEXT_PUBLIC_GRAFANA_URL in the frontend environment (for example http://127.0.0.1:3002)." />;
  }

  return (
    <Panel title="Grafana dashboards" state="ready"
      info="Provisioned from deployment/observability/grafana/dashboards. Each opens in Grafana with the time range chosen here."
      aside={<Tabs label="Time range" value={range} onChange={setRange} tabs={RANGES.map((r) => ({ key: r.key, label: r.label }))} />}>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 10,
                   gridTemplateColumns: "repeat(auto-fit, minmax(min(260px, 100%), 1fr))" }}>
        {GRAFANA_DASHBOARDS.map((d) => (
          <li key={d.uid} style={{ border: "1px solid var(--ecc-border)", borderRadius: 8, padding: 12, display: "grid", gap: 6 }}>
            <strong style={{ fontSize: 13 }}>{d.title}</strong>
            <span className="pk-muted" style={{ fontSize: 12, overflowWrap: "anywhere" }}>{d.about}</span>
            <a className="pk-button" style={{ justifySelf: "start" }} href={grafanaDashboardUrl(grafanaUrl, d.uid, range) ?? undefined}
              target="_blank" rel="noopener noreferrer">
              Open in Grafana <ExternalLink size={13} aria-hidden="true" />
            </a>
          </li>
        ))}
      </ul>
      <p className="pk-muted" style={{ margin: "10px 0 0", fontSize: 12 }}>
        <Pill tone="neutral" dot={false}>Linked, not embedded</Pill> Grafana keeps its own sign-in and frame protection, so it is not embedded here.
      </p>
    </Panel>
  );
}
