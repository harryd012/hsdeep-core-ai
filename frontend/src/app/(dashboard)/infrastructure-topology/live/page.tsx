import OperationalTopologyPanel from "@/components/topology/OperationalTopologyPanel";

/**
 * /infrastructure-topology/live — the canonical LIVE operational topology.
 *
 * Server component shell only: the graph itself is a client component that reads
 * one composed read model (nodes + edges + interface/telemetry facts) through the
 * authenticated apiFetch client.
 *
 * The pre-existing card/tree view at /infrastructure-topology is deliberately left
 * untouched: it carries an unrelated uncommitted workstream.
 */
export default function LiveTopologyPage() {
  return <OperationalTopologyPanel />;
}