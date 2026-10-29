"use client";

/**
 * Chrome around the tree view: the Meraki-style status line, the device legend
 * and the "Not connected" panel for devices with no recorded relationship.
 */
import Link from "next/link";
import { useState } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";

import { DEVICE_STATE_VISUALS, type DeviceState } from "../../lib/topology/status";
import { groupBy } from "../../lib/topology/tree";
import type { VisualIconKey } from "../../lib/topology/vendors";
import type { RenderNode, RenderSummary } from "./topologyRenderTypes";
import { TreeGlyph, displayName } from "./TopologyTreeCanvas";

const STATUS_ORDER: { state: DeviceState; count: (summary: RenderSummary) => number }[] = [
  { state: "ONLINE", count: (s) => s.online },
  { state: "WARNING", count: (s) => s.warning },
  { state: "OFFLINE", count: (s) => s.offline },
  { state: "STALE", count: (s) => s.stale },
  { state: "UNKNOWN", count: (s) => s.unknown },
  { state: "MAINTENANCE", count: (s) => s.maintenance },
];

/** "■ 244 online ■ 1 alerting" — each count filters the map to that state. */
export function TopologyStatusLine({
  summary,
  active,
  onToggle,
}: {
  summary: RenderSummary;
  active: readonly DeviceState[];
  onToggle: (state: DeviceState) => void;
}) {
  return (
    <div
      data-testid="topology-status-line"
      role="group"
      aria-label="Devices by state"
      style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4 }}
    >
      {STATUS_ORDER.map(({ state, count }) => {
        const n = count(summary);
        if (!n) return null;
        const visual = DEVICE_STATE_VISUALS[state];
        const on = active.includes(state);
        return (
          <button
            key={state}
            type="button"
            onClick={() => onToggle(state)}
            aria-pressed={on}
            title={`${visual.description} Click to show only these devices.`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "3px 8px",
              borderRadius: 6,
              border: `1px solid ${on ? visual.color : "transparent"}`,
              background: on ? "rgba(148,163,184,0.12)" : "transparent",
              color: "var(--fg)",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            <span
              aria-hidden="true"
              style={{ width: 10, height: 10, borderRadius: 2, background: visual.color }}
            />
            <strong style={{ fontWeight: 600 }}>{n}</strong>
            <span style={{ color: "var(--muted)" }}>{visual.label.toLowerCase()}</span>
          </button>
        );
      })}
    </div>
  );
}

const LEGEND_SHAPES: { iconKey: VisualIconKey; label: string }[] = [
  { iconKey: "firewall", label: "Security appliance" },
  { iconKey: "router", label: "Router" },
  { iconKey: "switch", label: "Switch" },
  { iconKey: "access-point", label: "Access point / wireless" },
  { iconKey: "server", label: "Server / VM / storage" },
  { iconKey: "endpoint", label: "Endpoint" },
  { iconKey: "unknown", label: "Unclassified" },
];

/** Collapsible device legend (bottom-left of the tree, like Meraki's). */
export function TopologyTreeLegend() {
  const [open, setOpen] = useState(true);
  return (
    <div
      data-testid="topology-tree-legend"
      style={{
        position: "absolute",
        left: 12,
        bottom: 56,
        padding: open ? "8px 10px" : "4px 8px",
        background: "var(--ecc-surface-raised, rgba(2,6,23,0.85))",
        border: "1px solid var(--border)",
        borderRadius: 8,
        fontSize: 11,
        color: "var(--muted)",
        zIndex: 3,
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          background: "none",
          border: "none",
          padding: 0,
          color: "var(--fg)",
          fontSize: 11,
          fontWeight: 600,
          cursor: "pointer",
        }}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Device legend
      </button>
      {open ? (
        <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "grid", gap: 4 }}>
          {LEGEND_SHAPES.map((entry) => (
            <li key={entry.iconKey} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <svg width={20} height={18} viewBox="-10 -9 20 18" aria-hidden="true">
                <TreeGlyph iconKey={entry.iconKey} color="var(--muted)" />
              </svg>
              {entry.label}
            </li>
          ))}
          <li style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}>
            <svg width={20} height={10} aria-hidden="true">
              <line x1={0} y1={5} x2={20} y2={5} stroke="var(--muted)" strokeDasharray="3 4" />
            </svg>
            Redundant link
          </li>
          <li style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <svg width={20} height={10} aria-hidden="true">
              <circle cx={10} cy={5} r={2.6} fill="var(--muted)" />
            </svg>
            Port (recorded interface)
          </li>
        </ul>
      ) : null}
    </div>
  );
}

const ROWS_PER_GROUP = 40;

function typeLabel(node: RenderNode): string {
  if (node.unidentified || !node.deviceType || node.deviceType === "unclassified") return "Unclassified";
  return node.deviceType.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Devices with no recorded relationship, grouped by type. They are listed, not
 * drawn: placing hundreds of unconnected devices on the map would bury the
 * links that do exist.
 */
export function UnconnectedRail({
  devices,
  selectedNodeId,
  onSelect,
  onClose,
}: {
  devices: RenderNode[];
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const groups = groupBy(devices, typeLabel);
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set(groups.slice(0, 1).map((g) => g.key)));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  return (
    <aside
      data-testid="topology-unconnected"
      aria-label="Devices not connected"
      style={{
        flex: "0 0 280px",
        width: 280,
        display: "flex",
        flexDirection: "column",
        background: "var(--ecc-surface, rgba(2,6,23,0.92))",
        borderLeft: "1px solid var(--border)",
        zIndex: 4,
      }}
    >
      <header style={{ padding: "10px 12px 8px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <strong style={{ fontSize: 12, color: "var(--fg)" }}>Not connected · {devices.length}</strong>
          <button
            type="button"
            onClick={onClose}
            aria-label="Hide not-connected devices"
            style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer" }}
          >
            <X size={14} />
          </button>
        </div>
        <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--muted)", lineHeight: 1.4 }}>
          No LLDP/CDP neighbour or manual relationship is recorded for these devices, so they have no
          place on the map yet.{" "}
          <Link href="/infrastructure/discovery" style={{ color: "var(--accent, #38bdf8)" }}>
            Run discovery
          </Link>
        </p>
      </header>
      <div style={{ overflowY: "auto", flex: 1, padding: "4px 0" }}>
        {groups.map((group) => {
          const open = openGroups.has(group.key);
          const all = expanded.has(group.key);
          const rows = all ? group.items : group.items.slice(0, ROWS_PER_GROUP);
          return (
            <section key={group.key}>
              <button
                type="button"
                onClick={() => setOpenGroups((set) => toggle(set, group.key))}
                aria-expanded={open}
                style={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 12px",
                  background: "none",
                  border: "none",
                  color: "var(--fg)",
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <span style={{ flex: 1 }}>{group.key}</span>
                <span style={{ color: "var(--muted)", fontWeight: 400 }}>{group.items.length}</span>
              </button>
              {open ? (
                <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                  {rows.map((node) => {
                    const state = DEVICE_STATE_VISUALS[node.state];
                    return (
                      <li key={node.id}>
                        <button
                          type="button"
                          onClick={() => onSelect(node.id)}
                          aria-current={selectedNodeId === node.id}
                          title={`${displayName(node)} — ${state.label}`}
                          style={{
                            width: "100%",
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                            padding: "4px 12px 4px 28px",
                            background: selectedNodeId === node.id ? "rgba(56,189,248,0.12)" : "none",
                            border: "none",
                            color: "var(--fg)",
                            fontSize: 11,
                            cursor: "pointer",
                            textAlign: "left",
                          }}
                        >
                          <svg width={16} height={16} viewBox="-10 -10 20 20" aria-hidden="true">
                            <TreeGlyph
                              iconKey={node.visual.iconKey}
                              color={state.color}
                              hollow={node.state === "UNKNOWN" || node.state === "STALE"}
                            />
                          </svg>
                          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {displayName(node)}
                          </span>
                          <span style={{ color: state.color, fontSize: 10 }}>{state.label}</span>
                        </button>
                      </li>
                    );
                  })}
                  {group.items.length > rows.length ? (
                    <li>
                      <button
                        type="button"
                        onClick={() => setExpanded((set) => toggle(set, group.key))}
                        style={{
                          padding: "4px 12px 6px 28px",
                          background: "none",
                          border: "none",
                          color: "var(--accent, #38bdf8)",
                          fontSize: 11,
                          cursor: "pointer",
                        }}
                      >
                        Show {group.items.length - rows.length} more
                      </button>
                    </li>
                  ) : null}
                </ul>
              ) : null}
            </section>
          );
        })}
      </div>
    </aside>
  );
}
