"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Waypoints } from "lucide-react";

import { ApiError } from "@/lib/api";
import { formatMs, getTrace, type Span } from "@/lib/traces";
import { Banner, PageShell, Panel, Pill, StatStrip, StatTile } from "@/components/ui/PageKit";

/**
 * /traces/[traceId] — one trace as a waterfall: every span placed on the
 * trace's timeline, indented under its parent, coloured by service and red
 * when it failed. Select a span for its attributes, resource and events.
 * Spans whose parent has not arrived are shown at the top level and counted.
 */

const PALETTE = ["#3b82f6", "#8b5cf6", "#14b8a6", "#f59e0b", "#ec4899", "#22c55e", "#0ea5e9", "#a855f7"];

function ordered(spans: Span[]): { span: Span; depth: number }[] {
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string | null, Span[]>();
  for (const s of spans) {
    const key = s.parent_span_id && ids.has(s.parent_span_id) ? s.parent_span_id : null;
    children.set(key, [...(children.get(key) ?? []), s]);
  }
  const out: { span: Span; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const s of (children.get(parent) ?? []).sort((a, b) => a.start_time.localeCompare(b.start_time))) {
      out.push({ span: s, depth });
      walk(s.span_id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

export default function TracePage() {
  const id = useParams<{ traceId: string }>()?.traceId ?? "";
  const trace = useQuery({ queryKey: ["traces", "trace", id], queryFn: () => getTrace(id), enabled: !!id, retry: false });
  const [selected, setSelected] = useState<string | null>(null);
  const t = trace.data;

  const rows = useMemo(() => (t ? ordered(t.spans) : []), [t]);
  const t0 = t ? Math.min(...t.spans.map((s) => Date.parse(s.start_time))) : 0;
  const t1 = t ? Math.max(...t.spans.map((s) => Date.parse(s.end_time))) : 1;
  const total = Math.max(1, t1 - t0);
  const services = useMemo(() => [...new Set((t?.spans ?? []).map((s) => s.service_name))], [t]);
  const color = (svc: string) => PALETTE[services.indexOf(svc) % PALETTE.length];
  const errors = (t?.spans ?? []).filter((s) => s.status_code === "error").length;
  const span = t?.spans.find((s) => s.span_id === selected) ?? null;
  const crumbs = [{ label: "Traces", href: "/traces" }, { label: id.slice(0, 12) }];

  if (trace.isError) {
    const notFound = trace.error instanceof ApiError && trace.error.status === 404;
    return (
      <PageShell title="Trace" icon={Waypoints} crumbs={crumbs}>
        <Banner tone="warn">{notFound ? "This trace does not exist or is not part of your organization." : `The trace could not be loaded${trace.error?.message ? `: ${trace.error.message}` : "."}`}</Banner>
        <p><Link className="ecc-link" href="/traces">Back to Traces</Link></p>
      </PageShell>
    );
  }

  return (
    <PageShell title={rows[0] ? rows[0].span.name : "Trace"} icon={Waypoints} crumbs={crumbs}
      subtitle={t ? `Trace ${t.trace_id}` : undefined}
      meta={t ? <Pill tone={errors ? "bad" : "good"}>{errors ? `${errors} failed span${errors === 1 ? "" : "s"}` : "No errors"}</Pill> : undefined}>
      {t && t.orphans > 0 && <Banner tone="info">{t.orphans} span{t.orphans === 1 ? "" : "s"} name a parent that has not arrived; they are shown at the top level.</Banner>}
      {t?.truncated && <Banner tone="warn">This trace has more spans than are shown (the first 5,000 by start time).</Banner>}
      <StatStrip>
        <StatTile label="Duration" value={t ? formatMs(total) : "—"} info="From the first span's start to the last span's end." loading={trace.isLoading} />
        <StatTile label="Spans" value={t ? String(t.spans.length) : "—"} info="Spans received for this trace." loading={trace.isLoading} />
        <StatTile label="Services" value={t ? String(services.length) : "—"} info="Services that took part in this trace." loading={trace.isLoading} />
        <StatTile label="Started" value={t ? new Date(t0).toLocaleTimeString() : "—"} sub={t ? new Date(t0).toLocaleDateString() : undefined} info="When the earliest span started." loading={trace.isLoading} />
      </StatStrip>
      <div className="pk-grid">
        <Panel title="Waterfall" info="Each bar is a span on the trace's timeline; select one for its details." state={trace.isLoading ? "loading" : "ready"} className="pk-col-8">
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
            {services.map((s) => <span key={s} style={{ display: "inline-flex", gap: 4, alignItems: "center", fontSize: 12 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: color(s) }} />{s}</span>)}
          </div>
          <div style={{ display: "grid", gap: 2 }}>
            {rows.map(({ span: s, depth }) => {
              const left = ((Date.parse(s.start_time) - t0) / total) * 100;
              const width = Math.max(0.4, (s.duration_ms / total) * 100);
              const failed = s.status_code === "error";
              return (
                <button key={s.span_id} type="button" onClick={() => setSelected(s.span_id)} aria-pressed={selected === s.span_id}
                  style={{ all: "unset", cursor: "pointer", display: "grid", gridTemplateColumns: "minmax(160px, 38%) 1fr", gap: 8, alignItems: "center", padding: "2px 4px", borderRadius: 4,
                    background: selected === s.span_id ? "var(--ecc-accent-soft, rgba(59,130,246,0.12))" : undefined }}>
                  <span style={{ paddingLeft: depth * 14, fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={`${s.service_name}: ${s.name}`}>
                    {failed && <span style={{ color: "var(--ecc-bad)" }}>● </span>}<span className="pk-muted">{s.service_name}</span> {s.name}
                  </span>
                  <span style={{ position: "relative", height: 16 }}>
                    <span style={{ position: "absolute", left: `${left}%`, width: `${width}%`, top: 3, height: 10, borderRadius: 2,
                      background: failed ? "var(--ecc-bad)" : color(s.service_name) }} />
                    <span style={{ position: "absolute", left: `min(${left + width}%, 85%)`, paddingLeft: 4, fontSize: 11 }} className="pk-muted">{formatMs(s.duration_ms)}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </Panel>
        <Panel title={span ? span.name : "Span"} state="ready" className="pk-col-4">
          {!span ? <p className="pk-muted">Select a span in the waterfall.</p> : (
            <>
              <dl className="hd-props">
                {([
                  ["Service", span.service_name],
                  ["Kind", span.kind],
                  ["Status", span.status_code === "error" ? `error${span.status_message ? `: ${span.status_message}` : ""}` : span.status_code],
                  ["Duration", formatMs(span.duration_ms)],
                  ["Starts at", `+${formatMs(Date.parse(span.start_time) - t0)}`],
                  ["Span id", span.span_id],
                  ["Parent", span.parent_span_id ?? "none (root)"],
                ] as [string, string][]).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
              </dl>
              <h4 style={{ margin: "12px 0 4px" }}>Attributes</h4>
              <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify(span.attributes, null, 2)}</pre>
              <h4 style={{ margin: "12px 0 4px" }}>Resource</h4>
              <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify(span.resource_attributes, null, 2)}</pre>
              {span.events.length > 0 && (
                <>
                  <h4 style={{ margin: "12px 0 4px" }}>Events</h4>
                  <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify(span.events, null, 2)}</pre>
                </>
              )}
            </>
          )}
        </Panel>
      </div>
    </PageShell>
  );
}
