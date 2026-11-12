"use client";

import { formatMs, type ServiceEdge, type ServiceStats } from "@/lib/traces";

/**
 * Service map: a layered left-to-right layout, callers left of callees
 * (cycles fall back to the first seen layer). A red edge or box had errors.
 * Shared by /traces and /apm.
 */
export default function ServiceMap({ services, edges, onPick }: { services: ServiceStats[]; edges: ServiceEdge[]; onPick: (s: string) => void }) {
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
