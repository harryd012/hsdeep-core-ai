import Link from "next/link";

import OperationalTopologyPanel from "@/components/topology/OperationalTopologyPanel";

/**
 * /infrastructure-topology/live — the canonical LIVE operational topology.
 *
 * Server component shell: breadcrumb + the client graph. The graph reads ONE
 * composed read model (nodes + edges + interface/telemetry facts) through the
 * authenticated apiFetch client, so tenant identity comes from the session and
 * never from the URL.
 *
 * The pre-existing card/tree view at /infrastructure-topology is left on disk
 * untouched: it carries an unrelated uncommitted workstream. This route is what
 * the dashboard "Infrastructure → Topology" entry now opens.
 */
export default function LiveTopologyPage() {
  return (
    <div
      style={{
        paddingTop: "clamp(84px,8.5vh,104px)",
        paddingLeft: 16,
        paddingRight: 16,
        paddingBottom: 24,
      }}
    >
      <nav
        aria-label="Breadcrumb"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 11,
          color: "var(--muted)",
        }}
      >
        <Link href="/infrastructure" style={{ color: "var(--muted)" }}>
          Infrastructure
        </Link>
        <span aria-hidden="true">/</span>
        <span style={{ color: "var(--fg)" }} aria-current="page">
          Topology
        </span>
      </nav>
      <OperationalTopologyPanel />
    </div>
  );
}