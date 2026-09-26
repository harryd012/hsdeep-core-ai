"use client";

/**
 * TopologyDetailDrawer — device and link detail (Phase 9.5).
 *
 * Everything shown here comes from the read model or the graph row it was built
 * from. A value that was never recorded renders as "N/A" (or as an explicit
 * sentence), never as 0, and evidence provenance stays visible: source,
 * classification, confidence and observed-vs-inferred.
 */
import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";

import {
  DEVICE_STATE_VISUALS,
  LINK_STATE_VISUALS,
  freshnessLabel,
} from "../../lib/topology/status";
import { formatBps, formatPercent, utilizationBand } from "../../lib/topology/traffic";
import type { RenderEdge, RenderNode } from "./topologyRenderTypes";
import { ResourceIcon } from "./TopologyVisuals";

export interface DetailDrawerProps {
  node: RenderNode | null;
  edge: RenderEdge | null;
  onClose: () => void;
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  const empty = value === null || value === undefined || value === "";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "128px 1fr",
        gap: 8,
        fontSize: 12,
        padding: "3px 0",
        borderBottom: "1px solid rgba(148,163,184,0.12)",
      }}
    >
      <span style={{ color: "var(--muted)" }}>{label}</span>
      <span style={{ color: empty ? "var(--muted)" : "var(--fg)" }}>
        {empty ? "N/A" : value}
      </span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 10, letterSpacing: "0.6px", color: "var(--muted)" }}>{title}</div>
      <div style={{ marginTop: 6 }}>{children}</div>
    </div>
  );
}

function InterfaceBlock({
  title,
  deviceLabel,
  name,
  iface,
}: {
  title: string;
  deviceLabel: string;
  name: string | null;
  iface: RenderEdge["localInterface"];
}) {
  const band = utilizationBand(iface?.utilizationPct ?? null);
  return (
    <Section title={title}>
      <Row label="Device" value={deviceLabel} />
      <Row label="Interface" value={name ?? iface?.name ?? null} />
      {iface ? (
        <>
          <Row label="Oper status" value={iface.operStatus} />
          <Row label="Speed" value={formatBps(iface.speedBps)} />
          <Row label="In traffic" value={formatBps(iface.rxBps)} />
          <Row label="Out traffic" value={formatBps(iface.txBps)} />
          <Row
            label="Utilisation"
            value={
              iface.utilizationPct === null
                ? null
                : `${formatPercent(iface.utilizationPct)}${band ? ` (${band})` : ""}`
            }
          />
          <Row
            label="Errors in/out"
            value={`${iface.inErrors ?? "N/A"} / ${iface.outErrors ?? "N/A"}`}
          />
          <Row
            label="Discards in/out"
            value={`${iface.discardsIn ?? "N/A"} / ${iface.discardsOut ?? "N/A"}`}
          />
          <Row label="VLAN" value={iface.vlanId === null ? null : String(iface.vlanId)} />
          <Row label="Last telemetry" value={iface.lastTelemetryAt} />
        </>
      ) : (
        <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
          {name
            ? `No interface named ${name} has been collected for this device, so its telemetry is unavailable.`
            : "The relationship records no interface for this end."}
        </p>
      )}
    </Section>
  );
}


export default function TopologyDetailDrawer({ node, edge, onClose }: DetailDrawerProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!node && !edge) return null;

  const title = node
    ? node.unidentified
      ? "Unknown Resource"
      : node.name
    : edge!.relationshipType.replace(/_/g, " ").toUpperCase();

  return (
    <aside
      data-testid="topology-drawer"
      role="complementary"
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        width: 360,
        maxWidth: "92%",
        height: "100%",
        overflowY: "auto",
        background: "rgba(9,13,24,0.97)",
        borderLeft: "1px solid var(--border)",
        padding: 16,
        zIndex: 20,
      }}
      aria-label="Topology detail"
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 8,
        }}
      >
        <div>
          <div style={{ fontSize: 10, color: "var(--muted)", letterSpacing: "0.6px" }}>
            {node ? "DEVICE" : "RELATIONSHIP"}
          </div>
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--fg)" }}>{title}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--muted)",
            cursor: "pointer",
          }}
        >
          <X size={16} />
        </button>
      </div>

      {node ? <DeviceDetail node={node} /> : null}
      {edge ? <EdgeDetail edge={edge} /> : null}
    </aside>
  );
}

function DeviceDetail({ node }: { node: RenderNode }) {
  const stateVisual = DEVICE_STATE_VISUALS[node.state];
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10 }}>
        <ResourceIcon iconKey={node.visual.iconKey} size={20} />
        <span style={{ fontSize: 12, color: stateVisual.color, letterSpacing: "0.5px" }}>
          {stateVisual.marker} · {stateVisual.label.toUpperCase()}
        </span>
      </div>
      <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
        {stateVisual.description}
      </p>

      <Section title="OVERVIEW">
        <Row label="Vendor" value={node.vendorLabel} />
        <Row label="Model" value={node.model} />
        <Row label="Device type" value={node.deviceTypeRaw ?? node.deviceType} />
        <Row label="Recognition" value={node.recognitionStatus} />
        <Row label="Visual source" value={node.visual.source} />
        <Row label="IP" value={node.ip} />
        <Row label="Hostname" value={node.hostname} />
        <Row label="Serial" value={node.serial} />
        <Row label="Site" value={node.siteLabel} />
      </Section>

      <Section title="OPERATIONAL STATE">
        <Row label="Operational" value={node.operationalState} />
        <Row label="Stored status" value={node.statusRaw} />
        <Row label="Health bucket" value={node.health} />
        <Row label="Lifecycle" value={node.lifecycleState} />
        <Row label="Telemetry" value={freshnessLabel(node.freshnessState)} />
        <Row
          label="Last telemetry"
          value={node.ageSeconds === null ? null : `${Math.round(node.ageSeconds)}s ago`}
        />
      </Section>

      <Section title="INTERFACES">
        {node.interfaces && node.interfaces.total > 0 ? (
          <>
            <Row label="Collected" value={String(node.interfaces.total)} />
            <Row label="Up / down" value={`${node.interfaces.up} / ${node.interfaces.down}`} />
            <Row
              label="Traffic in"
              value={node.interfaces.hasTelemetry ? formatBps(node.rxBps) : "Not measured"}
            />
            <Row
              label="Traffic out"
              value={node.interfaces.hasTelemetry ? formatBps(node.txBps) : "Not measured"}
            />
          </>
        ) : (
          <p style={{ fontSize: 11, color: "var(--muted)" }}>
            No interfaces have been collected for this device.
          </p>
        )}
      </Section>

      <Section title="RELATIONSHIPS">
        <Row label="Recorded relationships" value={String(node.relationshipCount)} />
        {node.relationshipCount === 0 ? (
          <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
            No relationship evidence recorded: discovery proves this resource exists, not how it is
            connected.
          </p>
        ) : null}
      </Section>

      <Section title="TELEMETRY">
        {node.interfaces?.hasTelemetry ? (
          <>
            <Row label="Traffic in" value={formatBps(node.rxBps)} />
            <Row label="Traffic out" value={formatBps(node.txBps)} />
            <Row label="Freshness" value={freshnessLabel(node.freshnessState)} />
          </>
        ) : (
          <p style={{ fontSize: 11, color: "var(--muted)" }}>
            No interface telemetry has been collected, so throughput is unavailable.
          </p>
        )}
      </Section>

      <Section title="EVIDENCE">
        <Row label="Recognition" value={node.recognitionStatus} />
        <Row label="Visual source" value={node.visual.source} />
        <Row label="Visual signature" value={node.visual.signature} />
        <Row label="Evidence type" value={node.visual.source === "generic" ? "unclassified" : null} />
      </Section>

      <Section title="EVENTS">
        <p style={{ fontSize: 11, color: "var(--muted)" }}>
          No data available — this view does not receive an event feed.
        </p>
      </Section>
    </>
  );
}

function EdgeDetail({ edge }: { edge: RenderEdge }) {
  const stateVisual = LINK_STATE_VISUALS[edge.state];
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10 }}>
        <span style={{ fontSize: 12, color: stateVisual.color, letterSpacing: "0.5px" }}>
          {stateVisual.marker} · {stateVisual.label.toUpperCase()}
        </span>
      </div>
      <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
        {stateVisual.description}
      </p>

      <Section title="RELATIONSHIP">
        <Row label="Type" value={edge.relationshipType} />
        {edge.unknownRelationshipType ? (
          <Row label="Warning" value="Recorded type is outside the known vocabulary" />
        ) : null}
        <Row label="Stored status" value={edge.statusRaw} />
        <Row label="Label" value={edge.label} />
      </Section>

      <InterfaceBlock
        title="LOCAL END"
        deviceLabel={edge.source}
        name={edge.localInterfaceName}
        iface={edge.localInterface}
      />
      <InterfaceBlock
        title="REMOTE END"
        deviceLabel={edge.target}
        name={edge.remoteInterfaceName}
        iface={edge.remoteInterface}
      />

      <Section title="TRAFFIC">
        {edge.totalBps === null ? (
          <p style={{ fontSize: 11, color: "var(--muted)" }}>
            No interface telemetry for this link, so throughput and utilisation are unavailable.
          </p>
        ) : (
          <>
            <Row label="Direction" value={edge.direction} />
            <Row label="Forward" value={edge.forwardLabel} />
            <Row label="Reverse" value={edge.reverseLabel} />
            <Row label="Capacity" value={edge.capacityLabel} />
            <Row
              label="Utilisation"
              value={
                edge.utilization.label === null
                  ? null
                  : `${edge.utilization.label}${
                      edge.utilization.source ? ` (${edge.utilization.source})` : ""
                    }`
              }
            />
            <Row label="Observed" value={edge.observedAt} />
          </>
        )}
      </Section>

      <Section title="EVIDENCE">
        <Row label="Source" value={edge.discoveredBy ?? edge.evidence} />
        <Row label="Classification" value={edge.classification ?? "Unclassified"} />
        <Row label="Confidence" value={edge.confidence === null ? null : String(edge.confidence)} />
        <Row label="Inferred" value={edge.inferred ? "Yes — not directly observed" : "No"} />
        <Row label="Telemetry" value={freshnessLabel(edge.freshnessState)} />
      </Section>
    </>
  );
}
