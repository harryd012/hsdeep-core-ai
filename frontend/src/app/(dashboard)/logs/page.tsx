"use client";

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";

import { getLogStats, searchLogs, type LogFilters, type LogLine, type LogSeverity, type LogStats } from "@/lib/logs";
import { Banner, PageShell, Panel, Pill, SearchBox, Tabs, type Tone } from "@/components/ui/PageKit";

/**
 * /logs — Logs Explorer: log lines collected as events (syslog through the
 * syslog receiver, or any collector posting logs), searched within a window
 * of at most 7 days. Facets ignore their own filter so their other values stay
 * visible; a line links to its device when the sending host matched exactly
 * one device.
 */

const RANGES = [
  { key: "15m", label: "15 min", ms: 15 * 60_000 },
  { key: "1h", label: "1 h", ms: 3_600_000 },
  { key: "6h", label: "6 h", ms: 6 * 3_600_000 },
  { key: "24h", label: "24 h", ms: 24 * 3_600_000 },
  { key: "7d", label: "7 d", ms: 7 * 24 * 3_600_000 },
];
const PAGE = 100;
const SEVERITIES: LogSeverity[] = ["critical", "warning", "info"];
const TONE: Record<LogSeverity, Tone> = { critical: "bad", warning: "warn", info: "neutral" };
const COLOR: Record<LogSeverity, string> = { critical: "var(--ecc-bad)", warning: "var(--ecc-warn)", info: "var(--ecc-accent, #3b82f6)" };

function Histogram({ stats }: { stats: LogStats }) {
  if (stats.histogram.length === 0) return <p className="pk-muted pk-empty-note">No log lines in this window.</p>;
  const W = 600, H = 120, pad = 18;
  const t0 = Date.parse(stats.since), t1 = Date.parse(stats.until);
  const bw = Math.max(1, ((stats.bucket_seconds * 1000) / Math.max(1, t1 - t0)) * (W - 2) - 1);
  const max = Math.max(1, ...stats.histogram.map((b) => b.critical + b.warning + b.info));
  return (
    <svg viewBox={`0 0 ${W} ${H + pad}`} role="img" aria-label="Log volume over time by severity" style={{ width: "100%", height: 140 }}>
      {stats.histogram.map((b) => {
        const x = ((Date.parse(b.t) - t0) / Math.max(1, t1 - t0)) * (W - 2);
        let y = H;
        return (
          <g key={b.t}>
            <title>{`${new Date(b.t).toLocaleString()}: ${b.critical} critical, ${b.warning} warning, ${b.info} info`}</title>
            {SEVERITIES.map((s) => {
              const h = (b[s] / max) * (H - 4);
              y -= h;
              return h > 0 ? <rect key={s} x={x} y={y} width={bw} height={h} fill={COLOR[s]} /> : null;
            })}
          </g>
        );
      })}
      <line x1={0} y1={H} x2={W} y2={H} stroke="var(--border, #8884)" />
      <text x={0} y={H + 14} fontSize={11} fill="currentColor" opacity={0.7}>{new Date(t0).toLocaleString()}</text>
      <text x={W} y={H + 14} fontSize={11} fill="currentColor" opacity={0.7} textAnchor="end">{new Date(t1).toLocaleString()}</text>
      <text x={W} y={10} fontSize={11} fill="currentColor" opacity={0.7} textAnchor="end">max {max} / bucket</text>
    </svg>
  );
}

function Facet({ title, values, active, onPick }: {
  title: string;
  values: { value: string; count: number }[];
  active: string | null;
  onPick: (v: string | null) => void;
}) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="pk-muted" style={{ fontSize: 12, marginBottom: 4 }}>{title}</div>
      {values.length === 0 ? <div className="pk-muted" style={{ fontSize: 12 }}>—</div> : values.map((v) => (
        <button key={v.value} type="button" onClick={() => onPick(active === v.value ? null : v.value)} aria-pressed={active === v.value}
          style={{ all: "unset", cursor: "pointer", display: "flex", justifyContent: "space-between", width: "100%", fontSize: 13, padding: "2px 4px", borderRadius: 4,
            background: active === v.value ? "var(--ecc-accent-soft, rgba(59,130,246,0.15))" : undefined }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 170 }} title={v.value}>{v.value}</span>
          <span className="pk-muted">{v.count}</span>
        </button>
      ))}
    </div>
  );
}

export default function LogsPage() {
  const [range, setRange] = useState("1h");
  const [anchor, setAnchor] = useState(() => Date.now());
  const [q, setQ] = useState("");
  const [severity, setSeverity] = useState<LogSeverity[]>([]);
  const [host, setHost] = useState<string | null>(null);
  const [app, setApp] = useState<string | null>(null);
  const [eventType, setEventType] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);

  const filters: LogFilters = useMemo(() => {
    const ms = RANGES.find((r) => r.key === range)?.ms ?? 3_600_000;
    return { since: new Date(anchor - ms).toISOString(), until: new Date(anchor).toISOString(), q, severity, host, app, eventType };
  }, [range, anchor, q, severity, host, app, eventType]);

  const lines = useQuery({ queryKey: ["logs", filters, offset], queryFn: () => searchLogs(filters, PAGE, offset) });
  const stats = useQuery({ queryKey: ["logs", "stats", filters], queryFn: () => getLogStats(filters) });
  const reset = () => setOffset(0);
  const total = lines.data?.total ?? 0;
  const anyFilter = !!(q || severity.length || host || app || eventType);

  return (
    <PageShell
      title="Logs Explorer"
      icon={ScrollText}
      crumbs={[{ label: "Logs Explorer" }]}
      subtitle="Search syslog and collector log lines across your devices"
      actions={
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Tabs<string> value={range} label="Time range" onChange={(k) => { setRange(k); setAnchor(Date.now()); reset(); }} tabs={RANGES.map((r) => ({ key: r.key, label: r.label }))} />
          <button type="button" className="pk-button" onClick={() => { setAnchor(Date.now()); reset(); }}>Refresh</button>
        </div>
      }
    >
      {(lines.isError || stats.isError) && <Banner tone="bad">Logs could not be loaded{lines.error?.message ? `: ${lines.error.message}` : "."}</Banner>}
      {lines.data && total === 0 && !anyFilter && (
        <Banner tone="info">
          No log lines in this window. Point your devices&apos; syslog at a syslog receiver
          (<code>python -m app.edge.syslog_receiver</code> on a host in your network, with a collector credential), or post
          log lines to the collector ingest API.
        </Banner>
      )}
      <div className="pk-grid">
        <Panel title="Volume" info="Log lines per time bucket, stacked by severity." state={stats.isLoading ? "loading" : "ready"} className="pk-col-12">
          {stats.data && <Histogram stats={stats.data} />}
        </Panel>
        <Panel title="Filters" state={stats.isLoading ? "loading" : "ready"} className="pk-col-4">
          <div style={{ marginBottom: 12 }}>
            <div className="pk-muted" style={{ fontSize: 12, marginBottom: 4 }}>Severity</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {SEVERITIES.map((s) => {
                const on = severity.includes(s);
                const n = stats.data?.facets.severity.find((f) => f.value === s)?.count ?? 0;
                return (
                  <button key={s} type="button" className="pk-button" aria-pressed={on} style={on ? { outline: `2px solid ${COLOR[s]}` } : undefined}
                    onClick={() => { setSeverity(on ? severity.filter((x) => x !== s) : [...severity, s]); reset(); }}>
                    {s} ({n})
                  </button>
                );
              })}
            </div>
          </div>
          <Facet title="Host" values={stats.data?.facets.host ?? []} active={host} onPick={(v) => { setHost(v); reset(); }} />
          <Facet title="Application" values={stats.data?.facets.app ?? []} active={app} onPick={(v) => { setApp(v); reset(); }} />
          <Facet title="Type" values={stats.data?.facets.event_type ?? []} active={eventType} onPick={(v) => { setEventType(v); reset(); }} />
          {anyFilter && (
            <button type="button" className="pk-button" onClick={() => { setQ(""); setSeverity([]); setHost(null); setApp(null); setEventType(null); reset(); }}>Clear filters</button>
          )}
        </Panel>
        <Panel title={`Log lines (${total.toLocaleString("en-US")})`}
          info="Newest first. Text search matches the message, case-insensitive, within the selected window."
          aside={<SearchBox value={q} onChange={(v) => { setQ(v); reset(); }} placeholder="Search messages" label="Search log messages" />}
          state={lines.isLoading ? "loading" : "ready"} className="pk-col-8">
          {lines.data && lines.data.items.length === 0 ? (
            <p className="pk-muted pk-empty-note">{anyFilter ? "No log line matches these filters." : "No log lines in this window."}</p>
          ) : (
            <div className="pk-table-wrap">
              <table className="pk-table" aria-label="Log lines">
                <thead>
                  <tr><th>Time</th><th>Severity</th><th>Host</th><th>App</th><th>Message</th></tr>
                </thead>
                <tbody>
                  {(lines.data?.items ?? []).map((l: LogLine) => (
                    <Fragment key={l.id}>
                      <tr onClick={() => setOpen(open === l.id ? null : l.id)} style={{ cursor: "pointer" }} aria-expanded={open === l.id}>
                        <td style={{ whiteSpace: "nowrap" }}>{new Date(l.timestamp).toLocaleString()}</td>
                        <td><Pill tone={TONE[l.severity]}>{l.syslog_severity ?? l.severity}</Pill></td>
                        <td style={{ whiteSpace: "nowrap" }}>
                          {l.device_id ? <Link className="ecc-link" href={`/infrastructure/hosts/${l.device_id}`} onClick={(e) => e.stopPropagation()}>{l.device_name ?? l.host}</Link> : (l.host ?? <span className="pk-muted">—</span>)}
                        </td>
                        <td>{l.app ?? <span className="pk-muted">—</span>}</td>
                        <td style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 12, wordBreak: "break-word" }}>{open === l.id ? l.message : l.message.slice(0, 240)}</td>
                      </tr>
                      {open === l.id && (
                        <tr>
                          <td colSpan={5}>
                            <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap" }}>{JSON.stringify({ event_type: l.event_type, ...l.attributes }, null, 2)}</pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {total > PAGE && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              <button type="button" className="pk-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</button>
              <span className="pk-muted">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
              <button type="button" className="pk-button" disabled={offset + PAGE >= total || offset + PAGE > 10000} onClick={() => setOffset(offset + PAGE)}>Older</button>
            </div>
          )}
        </Panel>
      </div>
    </PageShell>
  );
}
