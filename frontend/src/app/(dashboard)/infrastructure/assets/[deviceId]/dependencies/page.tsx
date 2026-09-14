"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { GraphResponse, getDeviceImpactAnalysis, getDeviceDependencyChain } from "@/lib/api";

type Mode = "impact" | "dependencies";

export default function AssetDependenciesPage() {
  const params = useParams();
  const deviceId = params.deviceId as string;
  const [mode, setMode] = useState<Mode>("impact");
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!deviceId) return;
    let cancelled = false;
    async function load() {
      try {
        const fn = mode === "impact" ? getDeviceImpactAnalysis : getDeviceDependencyChain;
        const data = await fn(deviceId);
        if (!cancelled) {
          setGraph(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load dependency graph");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
  }, [deviceId, mode]);

  const statusColor = (type: string) => {
    switch (type) {
      case "device": return "var(--cyan)";
      case "site": return "var(--green)";
      case "business_service": return "var(--amber)";
      default: return "var(--muted)";
    }
  };

  // Simple SVG layout: place nodes in a radial/grid layout by depth
  const layoutNodes = () => {
    if (!graph) return [];
    const byDepth = new Map<number, typeof graph.nodes>();
    for (const n of graph.nodes) {
      const arr = byDepth.get(n.depth) || [];
      arr.push(n);
      byDepth.set(n.depth, arr);
    }
    const cx = 400, cy = 300, radiusStep = 140;
    const out: Array<{ x: number; y: number; node: typeof graph.nodes[0] }> = [];
    byDepth.forEach((arr, depth) => {
      const r = depth * radiusStep;
      arr.forEach((n, i) => {
        const angle = (2 * Math.PI * i) / arr.length;
        out.push({ x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle), node: n });
      });
    });
    return out;
  };

  const placed = layoutNodes();
  const pos = new Map(placed.map((p) => [p.node.id, p]));

  if (loading) return <div className="dashboard" style={{ paddingTop: 104 }}><p style={{ color: "var(--muted)" }}>Loading dependency graph…</p></div>;
  if (error) return <div className="dashboard" style={{ paddingTop: 104 }}><p style={{ color: "var(--amber)" }}>{error}</p></div>;
  if (!graph) return <div className="dashboard" style={{ paddingTop: 104 }}><p style={{ color: "var(--muted)" }}>No graph data</p></div>;

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
        <section className="hud-panel">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2>DEPENDENCY GRAPH</h2>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => setMode("impact")} style={{ padding: "6px 12px", background: mode === "impact" ? "var(--cyan)" : "var(--line)", color: mode === "impact" ? "black" : "var(--muted)", border: "none", borderRadius: 4, cursor: "pointer" }}>Impact Analysis</button>
              <button onClick={() => setMode("dependencies")} style={{ padding: "6px 12px", background: mode === "dependencies" ? "var(--cyan)" : "var(--line)", color: mode === "dependencies" ? "black" : "var(--muted)", border: "none", borderRadius: 4, cursor: "pointer" }}>Dependencies</button>
            </div>
          </div>
          <p style={{ color: "var(--muted)", fontSize: 12, marginTop: 8 }}>
            {mode === "impact" ? "What breaks if this device fails?" : "What does this device depend on?"} — {graph.nodes.length} nodes, {graph.edges.length} edges
          </p>
          <div style={{ marginTop: 16, overflowX: "auto" }}>
            <svg width={800} height={600} style={{ background: "rgba(0,0,0,0.2)", borderRadius: 8 }}>
              <defs>
                <marker id="arrow" markerWidth="10" markerHeight="10" refX="20" refY="3" orient="auto" markerUnits="strokeWidth">
                  <path d="M0,0 L0,6 L9,3 z" fill="var(--cyan)" />
                </marker>
              </defs>
              {graph.edges.map((e, i) => {
                const s = pos.get(e.source);
                const t = pos.get(e.target);
                if (!s || !t) return null;
                return (
                  <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="var(--line)" strokeWidth={2} markerEnd="url(#arrow)" />
                );
              })}
              {placed.map(({ x, y, node }) => (
                <g key={node.id}>
                  <circle cx={x} cy={y} r={24} fill="var(--bg)" stroke={statusColor(node.type)} strokeWidth={2} />
                  <text x={x} y={y - 30} textAnchor="middle" fill="var(--fg)" fontSize={11} fontWeight={600}>{node.name}</text>
                  <text x={x} y={y + 4} textAnchor="middle" fill="var(--muted)" fontSize={9}>{node.type}</text>
                </g>
              ))}
            </svg>
          </div>
          <div style={{ marginTop: 12, display: "flex", gap: 16, flexWrap: "wrap" }}>
            {graph.nodes.map((n) => (
              <div key={n.id} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: statusColor(n.type), display: "inline-block" }} />
                <span style={{ color: "var(--muted)" }}>{n.name}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
