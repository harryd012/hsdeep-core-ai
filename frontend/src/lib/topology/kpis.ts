/**
 * KPI strip model for the live topology (Phase 9.7A).
 *
 * Pure projection of the read model's summary into the cards the strip renders.
 * Every number comes from the backend; nothing is estimated, and there is no
 * delta unless a real backend comparison exists — which it does not, so `delta`
 * is always `null` and the UI shows no percentage change rather than inventing
 * one.
 *
 * A value that the backend never measured is `null` here (rendered as
 * "No interface telemetry" and a neutral tone), never `0`.
 */

export type KpiTone = "neutral" | "good" | "warn" | "bad" | "muted";

export type KpiIconKey =
  | "devices"
  | "online"
  | "warning"
  | "offline"
  | "unknown"
  | "stale"
  | "links"
  | "interfaces"
  | "unidentified"
  | "traffic";

export interface KpiCard {
  key: string;
  label: string;
  /** null means "not measured" — never conflated with a measured zero. */
  value: number | null;
  /** Ready-to-render text for the value. */
  display: string;
  tone: KpiTone;
  iconKey: KpiIconKey;
  /** What exactly this counts, in the backend's own terms. */
  tooltip: string;
  /** Always null: no historical comparison is provided by the backend. */
  delta: null;
}

export interface KpiSummaryInput {
  devices: number;
  online: number;
  warning: number;
  offline: number;
  unknown: number;
  stale: number;
  maintenance: number;
  links: number;
  interfacesTotal: number;
  unidentified: number;
  trafficMeasured: boolean;
  rxLabel: string | null;
  txLabel: string | null;
}

/**
 * The ten cards the product specifies, in a fixed order so the strip never
 * reflows between refreshes.
 */
export function buildKpis(summary: KpiSummaryInput): KpiCard[] {
  const cards: KpiCard[] = [
    {
      key: "devices",
      label: "Devices",
      value: summary.devices,
      display: String(summary.devices),
      tone: "neutral",
      iconKey: "devices",
      tooltip: "Every device row recorded for this tenant.",
      delta: null,
    },
    {
      key: "online",
      label: "Online",
      value: summary.online,
      display: String(summary.online),
      tone: "good",
      iconKey: "online",
      tooltip: "Devices whose stored operational state is up and whose telemetry is current.",
      delta: null,
    },
    {
      key: "warning",
      label: "Warning",
      value: summary.warning,
      display: String(summary.warning),
      tone: "warn",
      iconKey: "warning",
      tooltip: "Devices reported as degraded, or with a warning/critical health bucket.",
      delta: null,
    },
    {
      key: "offline",
      label: "Offline",
      value: summary.offline,
      display: String(summary.offline),
      tone: "bad",
      iconKey: "offline",
      tooltip: "Devices whose stored operational state is down.",
      delta: null,
    },
    {
      key: "unknown",
      label: "Unknown",
      value: summary.unknown,
      display: String(summary.unknown),
      tone: "muted",
      iconKey: "unknown",
      tooltip: "Devices with no established operational state.",
      delta: null,
    },
    {
      key: "stale",
      label: "Stale",
      value: summary.stale,
      display: String(summary.stale),
      tone: "muted",
      iconKey: "stale",
      tooltip: "Devices whose telemetry has aged past the stale window, so no current state is asserted.",
      delta: null,
    },
    {
      key: "links",
      label: "Links",
      value: summary.links,
      display: String(summary.links),
      tone: "neutral",
      iconKey: "links",
      tooltip: "Relationship rows recorded for this tenant. Zero means no adjacency evidence exists.",
      delta: null,
    },
    {
      key: "interfaces",
      label: "Interfaces",
      value: summary.interfacesTotal,
      display: String(summary.interfacesTotal),
      tone: "neutral",
      iconKey: "interfaces",
      tooltip: "Interface rows collected for this tenant.",
      delta: null,
    },
    {
      key: "unidentified",
      label: "Unidentified",
      value: summary.unidentified,
      display: String(summary.unidentified),
      tone: "neutral",
      iconKey: "unidentified",
      tooltip: "Resources the platform never classified. They stay unknown rather than being guessed.",
      delta: null,
    },
  ];

  const measured = summary.trafficMeasured;
  cards.push({
    key: "traffic",
    label: "Traffic",
    value: null,
    display: measured
      ? `${summary.rxLabel ?? "N/A"} ↓ / ${summary.txLabel ?? "N/A"} ↑`
      : "No telemetry",
    tone: measured ? "neutral" : "muted",
    iconKey: "traffic",
    tooltip: measured
      ? "Sum of the interface receive/transmit counters the collectors recorded."
      : "No interface counters have ever been collected, so throughput is unavailable.",
    delta: null,
  });

  return cards;
}

/** Cards that need attention: a non-neutral tone AND a real non-zero count. */
export function attentionKpis(cards: readonly KpiCard[]): KpiCard[] {
  return cards.filter(
    (card) => (card.tone === "bad" || card.tone === "warn") && (card.value ?? 0) > 0
  );
}
