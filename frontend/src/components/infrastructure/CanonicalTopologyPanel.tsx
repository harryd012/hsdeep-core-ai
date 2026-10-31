"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Info } from "lucide-react";
import { TopologyGraph, TopologyGraphEdge, TopologyGraphNode, getTopologyGraph } from "@/lib/api";

/**
 * Canonical topology panel (Phase 5.1 §3/§4).
 *
 * Data source is `/api/infrastructure/topology/graph`, whose edges all come from
 * real `resource_relationships` rows. This component adds NO topology source of
 * its own and invents no nodes or edges - when the backend reports no edges it
 * renders the API's own `limitations` text rather than a blank canvas or a
 * decorative placeholder graph.
 *
 * Filters operate on the canonical node buckets and on the real provenance
 * fields (`discovered_by`, `is_inferred`, `evidence`).
 */

const NODE_FILTERS = [
  "All",
  "Network",
  "Firewalls",
  "Switches",
  "Servers",
  "Endpoints",
  "Cloud",
] as const;

const EDGE_FILTERS = ["All", "Physical", "Logical", "Internet", "Cloud", "Inferred"] as const;

type NodeFilter = (typeof NODE_FILTERS)[number];
type EdgeFilter = (typeof EDGE_FILTERS)[number];

/** Canonical bucket for a node, derived only from fields the API actually set. */
function nodeBucket(node: TopologyGraphNode): string {
  const dt = (node.device_type || node.type || "").toLowerCase();
  if (dt.includes("firewall")) return "firewall";
  if (dt.includes("switch")) return "switch";
  if (dt.includes("router")) return "router";
  if (dt.includes("server")) return "server";
  if (dt.includes("cloud")) return "cloud";
  if (node.is_network_device) return "network";
  if (dt.includes("endpoint") || dt.includes("workstation")) return "endpoint";
  return dt || "unknown";
}

function matchesNodeFilter(node: TopologyGraphNode, filter: NodeFilter): boolean {
  if (filter === "All") return true;
  const bucket = nodeBucket(node);
  switch (filter) {
    case "Network":
      return node.is_network_device === true || ["switch", "router", "firewall"].includes(bucket);
    case "Firewalls":
      return bucket === "firewall";
    case "Switches":
      return bucket === "switch";
    case "Servers":
      return bucket === "server";
    case "Endpoints":
      return bucket === "endpoint";
    case "Cloud":
      return bucket === "cloud" || (node.type || "").toLowerCase().includes("cloud");
    default:
      return true;
  }
}

function matchesEdgeFilter(edge: TopologyGraphEdge, filter: EdgeFilter): boolean {
  const type = (edge.relationship_type || "").toLowerCase();
  switch (filter) {
    case "All":
      return true;
    case "Inferred":
      // The real `is_inferred` column - never inferred from the type name.
      return edge.is_inferred === true;
    case "Physical":
      return ["connected_to", "cable", "physical_link", "link"].includes(type);
    case "Logical":
      return ["member_of", "hosted_on", "depends_on", "peers_with"].includes(type);
    case "Internet":
      return ["routes_through", "transits", "connects_to_internet"].includes(type);
    case "Cloud":
      return type.includes("cloud") || (edge.discovered_by || "").includes("cloud");
    default:
      return true;
  }
}

export default function CanonicalTopologyPanel() {
  const [graph, setGraph] = useState<TopologyGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [nodeFilter, setNodeFilter] = useState<NodeFilter>("All");
  const [edgeFilter, setEdgeFilter] = useState<EdgeFilter>("All");

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const data = await getTopologyGraph();
        if (cancelled) return;
        setGraph(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load topology");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const nodes = useMemo(
    () => (graph?.nodes ?? []).filter((n) => matchesNodeFilter(n, nodeFilter)),
    [graph, nodeFilter],
  );
  const visibleIds = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);
  const edges = useMemo(
    () =>
      (graph?.edges ?? []).filter(
        // An edge is only meaningful when both endpoints are visible.
        (e) =>
          matchesEdgeFilter(e, edgeFilter) &&
          visibleIds.has(e.source_id) &&
          visibleIds.has(e.target_id),
      ),
    [graph, edgeFilter, visibleIds],
  );

  return (
    <section className="hud-panel">
      <h2>CANONICAL TOPOLOGY</h2>

      {error && (
        <p style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 }}>
          <AlertTriangle size={14} /> {error}
        </p>
      )}

      <FilterRow
        label="Nodes"
        options={NODE_FILTERS}
        active={nodeFilter}
        onSelect={(v) => setNodeFilter(v as NodeFilter)}
      />
      <FilterRow
        label="Relationships"
        options={EDGE_FILTERS}
        active={edgeFilter}
        onSelect={(v) => setEdgeFilter(v as EdgeFilter)}
      />

      {graph && (
        <p style={{ marginTop: 8, fontSize: 11, color: "var(--muted)" }}>
          status <strong>{graph.status}</strong> · {graph.summary.nodes_total} nodes ·{" "}
          {graph.summary.edges_total} edges · {graph.summary.interfaces_total} interfaces ·
          showing {nodes.length} nodes / {edges.length} edges
        </p>
      )}

      {/* The API explains WHY the graph is incomplete (it is never empty when the
          graph is empty), so surface that rather than a blank panel. */}
      {loaded && graph && graph.limitations.length > 0 && (
        <div
          style={{
            marginTop: 8,
            padding: "8px 10px",
            border: "1px solid var(--line)",
            borderRadius: 6,
            display: "flex",
            gap: 6,
            alignItems: "flex-start",
          }}
        >
          <Info size={13} style={{ color: "var(--amber)", flexShrink: 0, marginTop: 2 }} />
          <div style={{ fontSize: 11, color: "var(--muted)" }}>
            {graph.limitations.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
        </div>
      )}

      {loaded && graph && graph.edges.length === 0 && (
        <p style={{ marginTop: 8, fontSize: 12, color: "var(--muted)" }}>
          No relationships have been discovered, so there is nothing to draw. Edges
          appear only after a collection observes real adjacency evidence.
        </p>
      )}

      <EdgeList graph={graph} edges={edges} />

      {loaded && graph && graph.edges.length > 0 && edges.length === 0 && (
        <p style={{ fontSize: 11, color: "var(--muted)" }}>
          No edges match the current filters.
        </p>
      )}
    </section>
  );
}

function EdgeList({
  graph,
  edges,
}: {
  graph: TopologyGraph | null;
  edges: TopologyGraphEdge[];
}) {
  return (
    <div style={{ display: "grid", gap: 6, marginTop: 12, maxHeight: 320, overflowY: "auto" }}>
      {edges.map((edge) => {
        const src = graph?.nodes.find((n) => n.id === edge.source_id);
        const dst = graph?.nodes.find((n) => n.id === edge.target_id);
        return (
          <div
            key={edge.id}
            style={{
              display: "grid",
              gridTemplateColumns: "1fr auto 1fr auto",
              gap: 8,
              alignItems: "center",
              padding: "6px 8px",
              border: "1px solid var(--line)",
              borderRadius: 6,
              fontSize: 12,
            }}
          >
            <span>{src?.name ?? edge.source_id}</span>
            <span style={{ color: "var(--cyan)" }}>→ {edge.relationship_type} →</span>
            <span>{dst?.name ?? edge.target_id}</span>
            {/* Real provenance fields, so an operator can judge the edge. */}
            <span style={{ fontSize: 10, color: "var(--muted)" }}>
              {edge.evidence}
              {edge.discovered_by ? ` · ${edge.discovered_by}` : ""}
              {edge.is_inferred ? " · inferred" : ""}
              {edge.confidence != null ? ` · ${edge.confidence}%` : ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function FilterRow({
  label,
  options,
  active,
  onSelect,
}: {
  label: string;
  options: readonly string[];
  active: string;
  onSelect: (value: string) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginTop: 6 }}>
      <span style={{ fontSize: 11, color: "var(--muted)", minWidth: 92 }}>{label}</span>
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onSelect(option)}
          style={{
            padding: "3px 9px",
            fontSize: 11,
            borderRadius: 999,
            cursor: "pointer",
            color: option === active ? "var(--bg)" : "var(--cyan)",
            background: option === active ? "var(--cyan)" : "transparent",
            border: "1px solid var(--cyan)",
          }}
        >
          {option}
        </button>
      ))}
    </div>
  );
}
