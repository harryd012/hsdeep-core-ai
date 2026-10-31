"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Boxes,
  Cloud,
  Globe2,
  Layers,
  Network,
  Server,
} from "lucide-react";
import { InfrastructureOverview, getInfrastructureOverview } from "@/lib/api";

/**
 * Top KPI row (Phase 5.1 §2).
 *
 * EVERY number comes from `/api/infrastructure/overview`, which computes it from
 * real rows. Nothing is hard-coded and nothing is estimated.
 *
 * Where the backend has NO source for a figure, the card says "No data" instead
 * of showing a fabricated 0. A real 0 is shown as 0 — the two cases are
 * deliberately different, because "we have no such data" and "the answer is
 * zero" are different facts.
 */
export default function InfrastructureKpiRow() {
  const [overview, setOverview] = useState<InfrastructureOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    // Loader declared INSIDE the effect (house pattern): calling an outer
    // useCallback that sets state in the effect body trips
    // react-hooks/set-state-in-effect.
    let cancelled = false;
    async function refresh() {
      try {
        const data = await getInfrastructureOverview();
        if (cancelled) return;
        setOverview(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load overview");
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

  const health = overview?.health;
  const devices = overview?.devices;
  const total = devices?.total ?? health?.total_devices;

  /** "n Type · n Type" built from the backend's canonical by_type buckets. */
  const typeBreakdown = (): string => {
    const entries = Object.entries(devices?.by_type ?? {});
    if (!entries.length) return "No type breakdown reported";
    return entries.map(([t, n]) => `${n} ${t}`).join(" · ");
  };

  const cards: KpiCardProps[] = [
    {
      label: "TOTAL ASSETS",
      value: loaded ? total : undefined,
      Icon: Boxes,
      color: "var(--cyan)",
      breakdown: loaded ? typeBreakdown() : "Loading…",
    },
    {
      label: "ONLINE",
      value: loaded ? health?.healthy_devices : undefined,
      Icon: Activity,
      color: "var(--green)",
      breakdown: loaded
        ? `${health?.critical_devices ?? 0} critical · ${health?.warning_devices ?? 0} warning`
        : "Loading…",
    },
    {
      label: "SENSORS",
      value: loaded ? health?.total_sensors : undefined,
      Icon: Server,
      color: "var(--cyan)",
      breakdown: loaded
        ? `${health?.down_sensors ?? 0} down · ${health?.warning_sensors ?? 0} warning`
        : "Loading…",
    },
    {
      label: "INTERFACES",
      value: loaded ? health?.total_interfaces : undefined,
      Icon: Network,
      color: "var(--amber)",
      breakdown: loaded
        ? health?.total_interfaces
          ? "From collected interface rows"
          : "No interfaces collected yet"
        : "Loading…",
    },
    {
      label: "RELATIONSHIPS",
      value: loaded ? health?.total_relationships : undefined,
      Icon: Layers,
      color: "var(--amber)",
      breakdown: loaded
        ? health?.total_relationships
          ? "Derived from collected evidence"
          : "No adjacency evidence collected yet"
        : "Loading…",
    },
    {
      label: "ACTIVE ALERTS",
      value: loaded ? health?.active_alerts : undefined,
      Icon: AlertTriangle,
      color: (health?.active_alerts ?? 0) > 0 ? "var(--red)" : "var(--green)",
      breakdown: loaded
        ? `${health?.total_alerts ?? 0} total · ${health?.critical_alerts ?? 0} critical`
        : "Loading…",
    },
    {
      label: "SITES",
      value: loaded ? health?.total_sites : undefined,
      Icon: Globe2,
      color: "var(--cyan)",
      breakdown: loaded
        ? `${health?.healthy_sites ?? 0} healthy · ${health?.critical_sites ?? 0} critical`
        : "Loading…",
    },
    // The backend has NO source for these. "No data" is the honest answer;
    // rendering 0 would falsely assert "there are none".
    { label: "VLANs", value: null, noData: true, Icon: Layers, color: "var(--muted)" },
    { label: "PUBLIC IPs", value: null, noData: true, Icon: Cloud, color: "var(--muted)" },
  ];

  return (
    <section className="hud-panel">
      <h2>INFRASTRUCTURE KPIs</h2>
      {error && (
        <p style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 }}>
          <AlertTriangle size={14} /> {error}
        </p>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 12,
        }}
      >
        {cards.map((card) => (
          <KpiCard key={card.label} {...card} />
        ))}
      </div>
      {overview && (
        <p style={{ marginTop: 10, fontSize: 11, color: "var(--muted)" }}>
          Source: canonical infrastructure overview · updated{" "}
          {new Date(overview.last_updated).toLocaleString()}
        </p>
      )}
    </section>
  );
}

interface KpiCardProps {
  label: string;
  value?: number | null;
  Icon: typeof Boxes;
  color: string;
  breakdown?: string;
  /** True when the backend has no data source for this metric at all. */
  noData?: boolean;
}

function KpiCard({ label, value, Icon, color, breakdown, noData }: KpiCardProps) {
  const display = noData ? "No data" : value === undefined ? "…" : String(value);
  return (
    <div
      style={{
        padding: 14,
        border: "1px solid var(--line)",
        borderRadius: 10,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <span
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          color: "var(--muted)",
          fontSize: 11,
        }}
      >
        <Icon size={15} style={{ color }} /> {label}
      </span>
      <strong
        style={{
          fontSize: noData ? 15 : "clamp(22px,1.7vw,30px)",
          color: noData ? "var(--muted)" : color,
          lineHeight: 1.1,
        }}
      >
        {display}
      </strong>
      {breakdown && (
        <span style={{ fontSize: 11, color: "var(--muted)" }}>{breakdown}</span>
      )}
    </div>
  );
}
