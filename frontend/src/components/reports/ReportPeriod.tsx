"use client";

/**
 * Period picker for the observability reports (P11): presets (24 h - 90 d)
 * or custom dates, capped at 90 days like the API. The period is fixed when
 * chosen, so a report does not drift while it is being read.
 */
import { useState } from "react";

import type { Period } from "@/lib/observabilityReports";
import { Tabs } from "@/components/ui/PageKit";

const PRESETS = [
  { key: "1", label: "24 h" },
  { key: "7", label: "7 d" },
  { key: "30", label: "30 d" },
  { key: "90", label: "90 d" },
  { key: "custom", label: "Custom" },
];
const DAY = 86_400_000;

export function presetPeriod(days: number, now = Date.now()): Period {
  return { since: new Date(now - days * DAY).toISOString(), until: new Date(now).toISOString() };
}

export function usePeriod(initialDays = 7) {
  const [period, setPeriod] = useState<Period>(() => presetPeriod(initialDays));
  return [period, setPeriod] as const;
}

export default function ReportPeriod({ onChange }: { onChange: (p: Period) => void }) {
  const [preset, setPreset] = useState("7");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const custom = from && to ? { since: new Date(`${from}T00:00:00Z`).getTime(), until: new Date(`${to}T23:59:59Z`).getTime() } : null;
  const customError = custom && (custom.until <= custom.since ? "End must be after start." : custom.until - custom.since > 90 * DAY ? "At most 90 days." : null);

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <Tabs<string> value={preset} label="Report period" tabs={PRESETS}
        onChange={(k) => { setPreset(k); if (k !== "custom") onChange(presetPeriod(Number(k))); }} />
      {preset === "custom" && (
        <>
          <input type="date" className="pk-input" aria-label="From (UTC)" value={from} onChange={(e) => setFrom(e.target.value)} />
          <input type="date" className="pk-input" aria-label="To (UTC)" value={to} onChange={(e) => setTo(e.target.value)} />
          <button type="button" className="pk-button" disabled={!custom || !!customError}
            onClick={() => custom && onChange({ since: new Date(custom.since).toISOString(), until: new Date(custom.until).toISOString() })}>
            Apply
          </button>
          {customError && <span className="pk-muted">{customError}</span>}
        </>
      )}
    </div>
  );
}
