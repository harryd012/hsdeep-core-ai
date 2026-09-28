"use client";

import Link from "next/link";
import { memo } from "react";
import type { CommandKpi, CommandKpiState } from "@/lib/dashboard/commandKpis";

/**
 * CommandKpiCards — PRESENTATIONAL (PHASE 1).
 *
 * Renders the normalized KPI view model produced by buildCommandKpis(). This
 * component owns no data, performs no fetching, and never substitutes a value: a
 * null value renders "N/A" alongside the reason the source supplied.
 *
 * Each card carries title, value, state and context, and exposes all three in one
 * accessible label - the state is never conveyed by colour alone.
 */

const STATE_CLASS: Record<CommandKpiState, string> = {
  healthy: "op-health__kpi--healthy",
  degraded: "op-health__kpi--warning",
  critical: "op-health__kpi--critical",
  unknown: "op-health__kpi--neutral",
  unavailable: "op-health__kpi--neutral",
};

const STATE_TEXT: Record<CommandKpiState, string> = {
  healthy: "OK",
  degraded: "Watch",
  critical: "Needs attention",
  unknown: "State unknown",
  unavailable: "Source unavailable",
};

function displayValue(kpi: CommandKpi): string {
  return kpi.value === null ? "N/A" : `${kpi.value}${kpi.unit ?? ""}`;
}

const CommandKpiCards = memo(function CommandKpiCards({
  kpis,
  label = "Command centre KPIs",
}: {
  kpis: CommandKpi[];
  label?: string;
}) {
  return (
    <ul className="op-health__strip kpi-row" aria-label={label}>
      {kpis.map((kpi) => (
        <li key={kpi.key} className={`op-health__kpi kpi-card ${STATE_CLASS[kpi.state]}`}>
          <Link
            href={kpi.href}
            className="op-health__link"
            aria-label={`${kpi.label}: ${displayValue(kpi)}. ${STATE_TEXT[kpi.state]}. ${kpi.context}`}
          >
            <small className="op-health__label">{kpi.label}</small>
            <b
              className={`op-health__value kpi-card__value${kpi.value === null ? " kpi-card__value--empty" : ""}`}
            >
              {displayValue(kpi)}
            </b>
            <small
              className="op-health__context kpi-card__subtext"
              style={{ textTransform: "uppercase", letterSpacing: 0.4 }}
            >
              {STATE_TEXT[kpi.state]}
            </small>
            <small className="op-health__context kpi-card__subtext">{kpi.context}</small>
          </Link>
        </li>
      ))}
    </ul>
  );
});

export default CommandKpiCards;
