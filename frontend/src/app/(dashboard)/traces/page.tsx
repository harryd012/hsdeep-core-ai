"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Waypoints } from "lucide-react";

import { formatMs, getServiceMap, listTraces, type ServiceEdge, type ServiceStats, type TraceSummary } from "@/lib/traces";
import { Banner, DataTable, PageShell, Panel, Pill, SearchBox, StatStrip, StatTile, Tabs, ago, type Column } from "@/components/ui/PageKit";

/**
 * /traces — distributed tracing: services (entry-span latency and error rate),
 * a service map of caller -> callee edges built from parent / child spans,
 * and traces with filters. Spans arrive over OTLP/HTTP JSON from any
 * OpenTelemetry SDK or collector.
 */

const RANGES = [
  { key: "15m", label: "15 min", ms: 15 * 60_000 },
  { key: "1h", label: "1 h", ms: 3_600_000 },
  { key: "6h", label: "6 h", ms: 6 * 3_600_000 },
  { key: "24h", label: "24 h", ms: 24 * 3_600_000 },
  { key: "7d", label: "7 d", ms: 7 * 24 * 3_600_000 },
];
const PAGE = 50;

/** Layered left-to-right layout: callers left of callees (cycles fall back to the first seen layer). */
function ServiceMapSvg({ services, edges, onPick }: { services: ServiceStats[]; edges: ServiceEdge[]; onPick: (s: string) => void }) {
  const names = services.map((s) => s.service);
  const layer: Record<string, number> = Object.fromEntries(names.map((n) => [n, 0]));
  for (let i = 0; i < names.length; i += 1) {
    let changed = false;
    for (const e of edges) {
      if ((layer[e.target] ?? 0) <= (layer[e.source] ?? 0) && (layer[e.source] ?? 0) < names.length) {
        layer[e.target] = (layer[e.source] ?? 0) + 1;
        changed = true;
      }
    }
    if (!changed) break;
  }
  const columns: string[][] = [];
  for (const n of names) (columns[layer[n]] ??= []).push(n);
  const W = 760, colW = W / Math.max(1, columns.length), rowH = 64;
  const H = Math.max(120, Math.max(...columns.map((c) => c?.length ?? 0)) * rowH + 20);
  const pos: Record<string, { x: number; y: number }> = {};
  columns.forEach((col, ci) => (col ?? []).forEach((n, ri) => { pos[n] = { x: ci * colW + colW / 2, y: ri * rowH + rowH / 2 + 10 }; }));
  const stats = Object.fromEntries(services.map((s) => [s.service, s]));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Service map" style={{ width: "100%", height: H }}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="currentColor" opacity={0.6} />
        </marker>
      </defs>
      {edges.map((e) => {
        const a = pos[e.source], b = pos[e.target];
        if (!a || !b) return null;
        const bad = e.errors > 0;
        return (
          <g key={`${e.source}-${e.target}`}>
            <title>{`${e.source} → ${e.target}: ${e.calls} calls, ${e.errors} errors, avg ${formatMs(e.avg_ms)}`}</title>
            <line x1={a.x + 60} y1={a.y} x2={b.x - 62} y2={b.y} stroke={bad ? "var(--ecc-bad)" : "currentColor"} strokeOpacity={bad ? 0.9 : 0.4}
              strokeWidth={Math.min(6, 1 + Math.log10(e.calls + 1) * 2)} markerEnd="url(#arrow)" />
            <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6} fontSize={11} textAnchor="middle" fill="currentColor" opacity={0.7}>
              {e.calls}{e.errors ? ` · ${e.errors} err` : ""}
            </text>
          </g>
        );
      })}
      {names.map((n) => {
        const p = pos[n];
        const s = stats[n];
        const bad = s && s.error_spans > 0;
        return (
          <g key={n} onClick={() => onPick(n)} style={{ cursor: "pointer" }}>
            <title>{`${n}: ${s?.spans ?? 0} spans, ${s?.error_rate_pct ?? 0} % errors, p95 ${formatMs(s?.p95_ms)}`}</title>
            <rect x={p.x - 60} y={p.y - 20} width={120} height={40} rx={8} fill="var(--ecc-surface, #fff)"
              stroke={bad ? "var(--ecc-bad)" : "var(--ecc-border, #8886)"} strokeWidth={bad ? 2 : 1} />
            <text x={p.x} y={p.y - 2} fontSize={12} textAnchor="middle" fill="currentColor">{n.length > 18 ? `${n.slice(0, 17)}…` : n}</text>
            <text x={p.x} y={p.y + 13} fontSize={10} textAnchor="middle" fill="currentColor" opacity={0.65}>p95 {formatMs(s?.p95_ms)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export default function TracesPage() {
  const [range, setRange] = useState("1h");
  const [anchor, setAnchor] = useState(() => Date.now());
  const [tab, setTab] = useState<"services" | "traces">("services");
  const [service, setService] = useState<string | null>(null);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [minMs, setMinMs] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);

  const w = useMemo(() => {
    const ms = RANGES.find((r) => r.key === range)?.ms ?? 3_600_000;
    return { since: new Date(anchor - ms).toISOString(), until: new Date(anchor).toISOString() };
  }, [range, anchor]);
  const map = useQuery({ queryKey: ["traces", "map", w], queryFn: () => getServiceMap(w) });
  const minDurationMs = minMs.trim() && Number.isFinite(Number(minMs)) ? Number(minMs) : null;
  const traces = useQuery({
    queryKey: ["traces", "list", w, service, errorsOnly, minDurationMs, q, offset],
    queryFn: () => listTraces(w, { service, errorsOnly, minDurationMs, q, limit: PAGE, offset }),
    enabled: tab === "traces",
  });

  const services = map.data?.services ?? [];
  const spans = services.reduce((n, s) => n + s.spans, 0);
  const errors = services.reduce((n, s) => n + s.error_spans, 0);
  const showTraces = (s: string | null) => { setService(s); setOffset(0); setTab("traces"); };

  const serviceColumns: Column<ServiceStats>[] = [
    { key: "service", header: "Service", sortValue: (s) => s.service, render: (s) => <strong>{s.service}</strong> },
    { key: "traces", header: "Traces", numeric: true, sortValue: (s) => s.traces, render: (s) => s.traces.toLocaleString("en-US") },
    { key: "spans", header: "Spans", numeric: true, sortValue: (s) => s.spans, render: (s) => s.spans.toLocaleString("en-US") },
    { key: "errors", header: "Error rate", numeric: true, sortValue: (s) => s.error_rate_pct,
      render: (s) => (s.error_spans ? <Pill tone="bad">{s.error_rate_pct} %</Pill> : <span className="pk-muted">0 %</span>) },
    { key: "p50", header: "p50", numeric: true, sortValue: (s) => s.p50_ms, render: (s) => formatMs(s.p50_ms) },
    { key: "p95", header: "p95", numeric: true, sortValue: (s) => s.p95_ms, render: (s) => formatMs(s.p95_ms) },
    { key: "seen", header: "Last span", sortValue: (s) => s.last_seen, render: (s) => <span className="pk-muted">{ago(s.last_seen)}</span> },
  ];
  const traceColumns: Column<TraceSummary>[] = [
    { key: "start", header: "Started", sortValue: (t) => t.start, render: (t) => new Date(t.start).toLocaleString() },
    { key: "root", header: "Root", render: (t) => <><strong>{t.root_name ?? "—"}</strong><div className="pk-muted">{t.root_service}{t.root_complete ? "" : " · root span missing"}</div></> },
    { key: "duration", header: "Duration", numeric: true, sortValue: (t) => t.duration_ms, render: (t) => formatMs(t.duration_ms) },
    { key: "spans", header: "Spans", numeric: true, sortValue: (t) => t.spans, render: (t) => t.spans },
    { key: "errors", header: "Errors", numeric: true, sortValue: (t) => t.errors, render: (t) => (t.errors ? <Pill tone="bad">{t.errors}</Pill> : <span className="pk-muted">0</span>) },
    { key: "services", header: "Services", render: (t) => <span className="pk-muted">{t.services.join(", ")}</span> },
  ];

  return (
    <PageShell
      title="Traces"
      icon={Waypoints}
      crumbs={[{ label: "Traces" }]}
      subtitle="Distributed traces from OpenTelemetry: services, dependencies and slow or failing requests"
      actions={
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Tabs<"services" | "traces"> value={tab} onChange={setTab} label="Traces view" tabs={[{ key: "services", label: "Services" }, { key: "traces", label: "Traces" }]} />
          <Tabs<string> value={range} label="Time range" onChange={(k) => { setRange(k); setAnchor(Date.now()); setOffset(0); }} tabs={RANGES.map((r) => ({ key: r.key, label: r.label }))} />
          <button type="button" className="pk-button" onClick={() => { setAnchor(Date.now()); setOffset(0); }}>Refresh</button>
        </div>
      }
    >
      {(map.isError || traces.isError) && <Banner tone="bad">Traces could not be loaded{map.error?.message ? `: ${map.error.message}` : "."}</Banner>}
      {map.data && services.length === 0 && (
        <Banner tone="info">
          No spans in this window. Point an OpenTelemetry SDK or collector at this platform: endpoint{" "}
          <code>{"<platform>/api/v1/ingest/otlp"}</code>, protocol <code>http/json</code>, and header{" "}
          <code>X-HSDEEP-Collector-Credential</code> with a collector credential.
        </Banner>
      )}
      {tab === "services" ? (
        <>
          <StatStrip>
            <StatTile label="Services" value={map.data ? String(services.length) : "—"} info="Services that sent spans in this window." loading={map.isLoading} />
            <StatTile label="Spans" value={map.data ? spans.toLocaleString("en-US") : "—"} info="Spans received in this window." loading={map.isLoading} />
            <StatTile label="Error spans" value={map.data ? errors.toLocaleString("en-US") : "—"} tone={errors > 0 ? "bad" : "good"} info="Spans whose status is error." loading={map.isLoading} />
            <StatTile label="Dependencies" value={map.data ? String(map.data.edges.length) : "—"} info="Caller → callee service pairs seen in parent / child spans." loading={map.isLoading} />
          </StatStrip>
          <div className="pk-grid">
            <Panel title="Service map" info="Callers on the left, callees to the right; a red edge or box had errors. Select a service for its traces." state={map.isLoading ? "loading" : "ready"} className="pk-col-12">
              {services.length > 0 ? <ServiceMapSvg services={services} edges={map.data?.edges ?? []} onPick={showTraces} /> : <p className="pk-muted pk-empty-note">No services in this window.</p>}
            </Panel>
            <Panel title="Services" info="Latency is over each service's entry spans (server or consumer spans, or spans without a parent)." state={map.isLoading ? "loading" : "ready"} className="pk-col-12">
              <DataTable caption="Services" columns={serviceColumns} rows={services} rowKey={(s) => s.service} onRowClick={(s) => showTraces(s.service)} emptyText="No services in this window." />
            </Panel>
          </div>
        </>
      ) : (
        <Panel title={`Traces (${(traces.data?.total ?? 0).toLocaleString("en-US")})`} info="Newest first. A trace's duration runs from its first span's start to its last span's end."
          aside={<SearchBox value={q} onChange={(v) => { setQ(v); setOffset(0); }} placeholder="Text in a span name" label="Search span names" />}
          state={traces.isLoading ? "loading" : "ready"}>
          <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
            <select className="pk-input" aria-label="Service" value={service ?? ""} onChange={(e) => { setService(e.target.value || null); setOffset(0); }}>
              <option value="">All services</option>
              {services.map((s) => <option key={s.service} value={s.service}>{s.service}</option>)}
            </select>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={errorsOnly} onChange={(e) => { setErrorsOnly(e.target.checked); setOffset(0); }} /> With errors
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              At least <input className="pk-input" type="number" min={0} value={minMs} style={{ width: 90 }} aria-label="Minimum duration in milliseconds"
                onChange={(e) => { setMinMs(e.target.value); setOffset(0); }} /> ms
            </label>
          </div>
          <DataTable caption="Traces" columns={traceColumns} rows={traces.data?.items ?? []} rowKey={(t) => t.trace_id} rowHref={(t) => `/traces/${t.trace_id}`}
            emptyText="No trace matches in this window." maxHeight={620} />
          {(traces.data?.total ?? 0) > PAGE && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              <button type="button" className="pk-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</button>
              <span className="pk-muted">{offset + 1}–{Math.min(offset + PAGE, traces.data?.total ?? 0)} of {traces.data?.total}</span>
              <button type="button" className="pk-button" disabled={offset + PAGE >= (traces.data?.total ?? 0)} onClick={() => setOffset(offset + PAGE)}>Older</button>
            </div>
          )}
        </Panel>
      )}
    </PageShell>
  );
}
