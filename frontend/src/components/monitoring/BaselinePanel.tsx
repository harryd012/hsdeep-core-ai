"use client";

/**
 * Dynamic baseline panel for the sensor page (observability plan P9 slice 2):
 * the sensor's learned usual range for the current hour, its latest verdict,
 * and (with monitoring.update) the switch, direction and sensitivity.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getSensorBaseline, putSensorBaseline, type BaselineDirection, type BaselineState } from "@/lib/baselines";
import { Banner, Panel, Pill, ago, type Tone } from "@/components/ui/PageKit";

const STATE: Record<BaselineState, { label: string; tone: Tone }> = {
  off: { label: "Off", tone: "neutral" },
  learning: { label: "Learning", tone: "info" },
  normal: { label: "Within its usual range", tone: "good" },
  anomalous: { label: "Unusual", tone: "warn" },
  stale: { label: "No recent reading", tone: "neutral" },
  paused: { label: "Paused", tone: "neutral" },
};

function fmt(v: number | null | undefined, unit: string | null): string {
  if (v === null || v === undefined) return "—";
  return `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`;
}

export default function BaselinePanel({ sensorId, unit, canEdit }: { sensorId: string; unit: string | null; canEdit: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sensor", sensorId, "baseline"], queryFn: () => getSensorBaseline(sensorId), retry: false, refetchInterval: 60_000 });
  const b = q.data;
  const [direction, setDirection] = useState<BaselineDirection | null>(null);
  const [sensitivity, setSensitivity] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dir = direction ?? b?.direction ?? "both";
  const sens = sensitivity ?? b?.sensitivity ?? 3;
  const hour = new Date().getUTCHours();
  const band = b?.hours.find((h) => h.hour === hour);
  const state = STATE[b?.state ?? "off"];

  async function save(enabled: boolean) {
    setBusy(true);
    setError(null);
    try {
      const out = await putSensorBaseline(sensorId, { enabled, direction: dir, sensitivity: sens });
      qc.setQueryData(["sensor", sensorId, "baseline"], out);
      setDirection(null);
      setSensitivity(null);
      void qc.invalidateQueries({ queryKey: ["sensor", sensorId] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "The baseline could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Dynamic baseline"
      info="Learns this sensor's usual value for each hour of the day from its own last 14 days, and raises a warning when a reading is far outside it."
      aside={b ? <Pill tone={state.tone}>{state.label}</Pill> : undefined}
      state={q.isLoading ? "loading" : q.isError ? "error" : "ready"} errorText="The baseline could not be loaded." className="pk-col-6">
      {error && <Banner tone="bad">{error}</Banner>}
      {b && (
        <>
          {b.configured && b.enabled && !b.ready && (
            <Banner tone="info">
              Learning: {b.samples} reading{b.samples === 1 ? "" : "s"} over {b.span_days} days so far; it starts judging after
              {" "}{b.min_span_days} days and {b.min_samples} readings. Until then it never alerts.
            </Banner>
          )}
          <dl className="hd-props">
            {([
              ["Usual now (UTC hour " + hour + ")", band ? `${fmt(band.expected, unit)} (${fmt(band.low, unit)} to ${fmt(band.high, unit)})` : null],
              ["Based on", band ? (band.basis === "hour" ? `${band.samples} readings at this hour` : "all hours (too few at this hour)") : null],
              ["Latest reading", b.last_value !== null ? `${fmt(b.last_value, unit)}${b.last_z !== null ? ` · ${Math.abs(b.last_z).toFixed(1)} std ${b.last_z >= 0 ? "above" : "below"}` : ""}` : null],
              ["Checked", b.last_evaluated_at ? ago(b.last_evaluated_at) : null],
              ["History used", b.configured ? `${b.samples} readings over ${b.span_days} days (window ${b.window_days} days)` : null],
            ] as [string, string | null][]).map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v ?? <span className="pk-muted">—</span>}</dd></div>
            ))}
          </dl>
          {canEdit && (
            <div className="pk-form" style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap", marginTop: 12 }}>
              <div className="pk-field"><label htmlFor="bl-dir">Alert when</label>
                <select id="bl-dir" className="pk-input" value={dir} onChange={(e) => setDirection(e.target.value as BaselineDirection)}>
                  <option value="both">Higher or lower than usual</option>
                  <option value="above">Higher than usual</option>
                  <option value="below">Lower than usual</option>
                </select></div>
              <div className="pk-field"><label htmlFor="bl-sens">Sensitivity</label>
                <select id="bl-sens" className="pk-input" value={sens} onChange={(e) => setSensitivity(Number(e.target.value))}>
                  <option value={2}>High (2 std)</option>
                  <option value={3}>Normal (3 std)</option>
                  <option value={4}>Low (4 std)</option>
                  <option value={5}>Very low (5 std)</option>
                </select></div>
              {b.enabled ? (
                <>
                  <button type="button" className="pk-button pk-button--primary" disabled={busy || (direction === null && sensitivity === null)} onClick={() => void save(true)}>Save</button>
                  <button type="button" className="pk-button" disabled={busy} onClick={() => void save(false)}>Turn off</button>
                </>
              ) : (
                <button type="button" className="pk-button pk-button--primary" disabled={busy} onClick={() => void save(true)}>Turn on</button>
              )}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
