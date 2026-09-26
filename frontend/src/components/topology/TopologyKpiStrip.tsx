"use client";

/**
 * TopologyKpiStrip — the operational status strip (Phase 9.7A).
 *
 * Renders the tested KPI model (`lib/topology/kpis`): one card per real backend
 * value, in a fixed order. No delta is shown because no backend comparison
 * exists — inventing a percentage would be a lie about history the platform does
 * not have.
 *
 * Status is conveyed by the state NAME plus colour plus icon, never by colour
 * alone, and every card carries its meaning as a tooltip / aria description.
 */
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Fingerprint,
  Link2,
  Network,
  Server,
  Tag,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import type { KpiCard, KpiIconKey, KpiTone } from "../../lib/topology/kpis";

const ICONS: Record<KpiIconKey, LucideIcon> = {
  devices: Server,
  online: CheckCircle2,
  warning: AlertTriangle,
  offline: XCircle,
  unknown: Tag,
  stale: Clock,
  links: Link2,
  interfaces: Network,
  unidentified: Fingerprint,
  traffic: Activity,
};

const TONE_COLOR: Record<KpiTone, string> = {
  good: "var(--green)",
  warn: "var(--amber)",
  bad: "var(--red)",
  muted: "var(--muted)",
  neutral: "var(--fg)",
};

export interface TopologyKpiStripProps {
  cards: KpiCard[];
  /** Compact mode drops the icon column (used on narrow viewports). */
  compact?: boolean;
}

export default function TopologyKpiStrip({ cards, compact = false }: TopologyKpiStripProps) {
  return (
    <div
      data-testid="topology-kpi-strip"
      role="list"
      aria-label="Topology status summary"
      style={{
        display: "flex",
        gap: 8,
        overflowX: "auto",
        paddingBottom: 2,
        scrollbarWidth: "thin",
      }}
    >
      {cards.map((card) => {
        const Icon = ICONS[card.iconKey];
        const color = TONE_COLOR[card.tone];
        return (
          <div
            key={card.key}
            role="listitem"
            data-testid={`kpi-${card.key}`}
            data-kpi-value={card.value === null ? "unmeasured" : String(card.value)}
            title={card.tooltip}
            aria-label={`${card.label}: ${card.display}. ${card.tooltip}`}
            style={{
              flex: "1 1 0",
              // The traffic card carries a much longer value than a count, so it
              // gets the room it needs instead of being ellipsised.
              minWidth: card.key === "traffic" ? 152 : compact ? 88 : 100,
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 8px",
              border: "1px solid var(--border)",
              borderLeft: `2px solid ${color}`,
              borderRadius: 8,
              background: "rgba(15,23,42,0.55)",
            }}
          >
            <Icon size={15} aria-hidden="true" style={{ color, flexShrink: 0 }} />
            <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
              <span
                data-testid={`kpi-${card.key}-value`}
                style={{
                  fontSize: 15,
                  fontWeight: 600,
                  lineHeight: 1.1,
                  color: card.value === null ? "var(--muted)" : color,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {card.display}
              </span>
              <span
                style={{
                  fontSize: 10,
                  letterSpacing: "0.5px",
                  color: "var(--muted)",
                  whiteSpace: "nowrap",
                }}
              >
                {card.label.toUpperCase()}
                {/* No delta column: the backend provides no comparison, and an
                    invented percentage would misrepresent history. */}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}