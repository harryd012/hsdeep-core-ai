"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { ApiError, getSensor, getSensorHistory, updateSensor, type SensorDetail, type SensorUpdate } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { classifySensorState } from "@/lib/sensorState";
import AddToDashboard from "@/components/dashboards/AddToDashboard";
import BaselinePanel from "@/components/monitoring/BaselinePanel";
import { Banner, DataTable, LineChart, PageShell, Panel, Pill, StatStrip, StatTile, Tabs, ago, titleCaseWords, type Column, type Tone } from "@/components/ui/PageKit";

/**
 * /monitoring/sensors/[id] — one sensor: its status and thresholds, every
 * measurement channel with its latest value, the history of a channel over a
 * chosen window (bucketed by the API: average line, min/max band), and the
 * lifecycle controls (pause / disable / resume, own thresholds) for users with
 * monitoring.update. Every change is applied and audited by the backend.
 */

const WINDOWS = [
  { hours: 1, label: "1 h" },
  { hours: 24, label: "24 h" },
  { hours: 168, label: "7 d" },
  { hours: 720, label: "30 d" },
];

function stateTone(status: string): Tone {
  const s = classifySensorState(status).state;
  if (s === "HEALTHY") return "good";
  if (s === "WARNING" || s === "STALE") return "warn";
  if (s === "DOWN" || s === "CRITICAL" || s === "COLLECTION_FAILED") return "bad";
  return "neutral";
}

function fmt(v: number | null, unit: string | null): string {
  if (v === null) return "—";
  return `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`;
}

type Channel = SensorDetail["channels"][number];

function ThresholdForm({ sensor, onSave, busy }: { sensor: SensorDetail; onSave: (c: SensorUpdate) => void; busy: boolean }) {
  const [warning, setWarning] = useState(sensor.warning_threshold?.toString() ?? "");
  const [critical, setCritical] = useState(sensor.critical_threshold?.toString() ?? "");
  const [direction, setDirection] = useState<"above" | "below">((sensor.threshold_direction as "above" | "below") ?? "above");
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  const valid = [warning, critical].every((v) => v.trim() === "" || Number.isFinite(Number(v))) && (warning.trim() !== "" || critical.trim() !== "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid) onSave({ thresholds: { warning: num(warning), critical: num(critical), direction } });
  };
  return (
    <form className="pk-form" onSubmit={submit} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8, alignItems: "end" }}>
      <div className="pk-field"><label htmlFor="th-dir">Alert when value is</label>
        <select id="th-dir" className="pk-input" value={direction} onChange={(e) => setDirection(e.target.value as "above" | "below")}>
          <option value="above">At or above</option>
          <option value="below">At or below</option>
        </select>
      </div>
      <div className="pk-field"><label htmlFor="th-warn">Warning</label><input id="th-warn" className="pk-input" type="number" step="any" value={warning} onChange={(e) => setWarning(e.target.value)} /></div>
      <div className="pk-field"><label htmlFor="th-crit">Critical</label><input id="th-crit" className="pk-input" type="number" step="any" value={critical} onChange={(e) => setCritical(e.target.value)} /></div>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="pk-button pk-button--primary" disabled={!valid || busy}>Save thresholds</button>
        {sensor.threshold_source === "user" && (
          <button type="button" className="pk-button" disabled={busy} onClick={() => onSave({ reset_thresholds: true })}>Use provider thresholds</button>
        )}
      </div>
    </form>
  );
}

export default function SensorPage() {
  const id = useParams<{ id: string }>()?.id ?? "";
  const qc = useQueryClient();
  const { user } = useAuth();
  const canEdit = !!user && (user.is_superuser || user.permissions.includes("monitoring.update"));
  const [hours, setHours] = useState(24);
  const [channel, setChannel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [editThresholds, setEditThresholds] = useState(false);

  const sensor = useQuery({ queryKey: ["sensor", id], queryFn: () => getSensor(id), enabled: !!id, retry: false, refetchInterval: 60_000 });
  const s = sensor.data;
  const selected = channel ?? s?.channels[0]?.key ?? "primary";
  const history = useQuery({
    queryKey: ["sensor", id, "history", selected, hours],
    queryFn: () => getSensorHistory(id, selected, hours),
    enabled: !!s && s.channels.length > 0,
    refetchInterval: 60_000,
  });

  async function change(c: SensorUpdate, done: string) {
    setBusy(true);
    setMessage(null);
    try {
      const updated = await updateSensor(id, c);
      qc.setQueryData(["sensor", id], updated);
      setMessage({ tone: "good", text: done });
      setEditThresholds(false);
    } catch (err) {
      setMessage({ tone: "bad", text: err instanceof Error ? err.message : "The change could not be saved." });
    } finally {
      setBusy(false);
    }
  }

  const crumbs = (last: string) => [{ label: "Monitoring", href: "/monitoring" }, { label: last }];
  if (sensor.isError) {
    const notFound = sensor.error instanceof ApiError && sensor.error.status === 404;
    return (
      <PageShell title="Sensor" icon={Activity} crumbs={crumbs(notFound ? "Not found" : "Unavailable")}>
        <Banner tone="warn">{notFound ? "This sensor does not exist or is not part of your organization." : `The sensor could not be loaded${sensor.error?.message ? `: ${sensor.error.message}` : "."}`}</Banner>
        <p><Link className="ecc-link" href="/monitoring">Back to Monitoring</Link></p>
      </PageShell>
    );
  }

  const state = s ? classifySensorState(s.status, s.last_value_at) : null;
  const sel = s?.channels.find((c) => c.key === selected);
  const unit = history.data?.unit ?? sel?.unit ?? null;
  const thresholdLines = s && selected === "primary"
    ? [
        ...(s.warning_threshold !== null ? [{ value: s.warning_threshold, label: "Warning", color: "var(--ecc-warn)" }] : []),
        ...(s.critical_threshold !== null ? [{ value: s.critical_threshold, label: "Critical", color: "var(--ecc-bad)" }] : []),
      ]
    : [];

  const channelColumns: Column<Channel>[] = [
    { key: "key", header: "Channel", sortValue: (c) => c.key, render: (c) => (c.key === "primary" ? <strong>Sensor value</strong> : c.key) },
    { key: "value", header: "Last value", numeric: true, sortValue: (c) => c.last_value, render: (c) => fmt(c.last_value, c.unit) },
    { key: "at", header: "Recorded", sortValue: (c) => c.last_recorded_at, render: (c) => <span className="pk-muted">{ago(c.last_recorded_at)}</span> },
    { key: "samples", header: "Samples (24 h)", numeric: true, sortValue: (c) => c.samples_24h, render: (c) => c.samples_24h },
  ];

  return (
    <PageShell
      title={s?.name ?? "Sensor"}
      icon={Activity}
      crumbs={crumbs(s?.name ?? "Sensor")}
      subtitle={s ? [titleCaseWords(s.metric_type), s.device?.name, `${s.source.name} (${s.source_type})`].filter(Boolean).join(" · ") : undefined}
      meta={state ? <Pill tone={stateTone(s!.status)}>{state.label}</Pill> : undefined}
      actions={
        s && canEdit ? (
          <div style={{ display: "flex", gap: 8 }}>
            {s.admin_state === "active" ? (
              <>
                <button type="button" className="pk-button" disabled={busy} onClick={() => change({ admin_state: "paused" }, "Sensor paused: no alerts until it is resumed.")}>Pause</button>
                <button type="button" className="pk-button" disabled={busy} onClick={() => { if (window.confirm("Disable this sensor? Its readings will be ignored until it is resumed.")) change({ admin_state: "disabled" }, "Sensor disabled."); }}>Disable</button>
              </>
            ) : (
              <button type="button" className="pk-button pk-button--primary" disabled={busy} onClick={() => change({ admin_state: "active" }, "Sensor resumed: its status updates with the next collection.")}>Resume</button>
            )}
          </div>
        ) : undefined
      }
    >
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
      {s?.admin_state === "paused" && <Banner tone="info">Paused here: readings are still recorded but raise no alerts. Pausing does not pause the check in {s.source_type.toUpperCase()} itself.</Banner>}
      {s?.admin_state === "disabled" && <Banner tone="warn">Disabled: readings from {s.source.name} are ignored and nothing is recorded until the sensor is resumed.</Banner>}
      {s?.open_alert && <Banner tone={s.open_alert.severity === "critical" ? "bad" : "warn"}>Open alert: {s.open_alert.title}. <Link className="ecc-link" href="/alerts">View alerts</Link></Banner>}

      <StatStrip>
        <StatTile label="Current value" value={s ? fmt(s.current_value, s.unit) : "—"} sub={s?.last_value_at ? `recorded ${ago(s.last_value_at)}` : undefined} info="The sensor's latest reading from its collector." loading={sensor.isLoading} naText="No value" />
        <StatTile label="Warning at" value={s?.warning_threshold != null ? fmt(s.warning_threshold, s.unit) : "—"} sub={s ? (s.threshold_source === "user" ? `set here · ${s.threshold_direction === "below" ? "at or below" : "at or above"}` : "from the provider") : undefined} info="Warning threshold: the provider's, or your own when set here." loading={sensor.isLoading} naText="None" />
        <StatTile label="Critical at" value={s?.critical_threshold != null ? fmt(s.critical_threshold, s.unit) : "—"} sub={s ? (s.threshold_source === "user" ? "set here" : "from the provider") : undefined} info="Critical threshold; crossing it raises a critical alert." loading={sensor.isLoading} naText="None" />
        <StatTile label="Channels" value={s ? String(s.channels.length) : "—"} info="Measurement channels with recorded history." loading={sensor.isLoading} />
      </StatStrip>

      {s && (
        <div className="pk-grid">
          <Panel
            title={`History: ${selected === "primary" ? "sensor value" : selected}`}
            info="Average per time bucket, with the minimum-maximum range shaded. Gaps mean no measurement was recorded."
            aside={<Tabs<string> value={String(hours)} onChange={(h) => setHours(Number(h))} label="Time window" tabs={WINDOWS.map((w) => ({ key: String(w.hours), label: w.label }))} />}
            state={history.isLoading ? "loading" : "ready"}
            className="pk-col-12"
          >
            {s.channels.length > 0 && (
              <div style={{ marginBottom: 8 }}>
                <AddToDashboard widget={{ type: "sensor_chart", title: null, size: "medium", config: { sensor_id: s.id, channel: selected, hours } }} />
              </div>
            )}
            {s.channels.length === 0 ? (
              <p className="pk-muted pk-empty-note">No measurements recorded for this sensor yet. History starts with the next collection that reports a value.</p>
            ) : history.data ? (
              <LineChart
                label={`${s.name} ${selected} history`}
                points={history.data.points.filter((p) => p.avg !== null).map((p) => ({ t: Date.parse(p.t), value: p.avg as number, min: p.min, max: p.max }))}
                format={(v) => fmt(v, unit)}
                thresholds={thresholdLines}
                gapMs={history.data.bucket_seconds * 1000 * 3}
                height={220}
              />
            ) : null}
          </Panel>

          <Panel title="Channels" info="Select a channel to chart it." state="ready" className="pk-col-6">
            <DataTable caption="Sensor channels" columns={channelColumns} rows={s.channels} rowKey={(c) => c.key} onRowClick={(c) => setChannel(c.key)} maxHeight={320} emptyText="No channels recorded yet." />
          </Panel>

          <Panel title="Configuration" info="Where the sensor comes from and how it is judged." state="ready" className="pk-col-6">
            <dl className="hd-props">
              {([
                ["Status", state?.label ?? s.status],
                ["Lifecycle", titleCaseWords(s.admin_state)],
                ["Metric type", s.metric_type],
                ["Device", s.device ? <Link key="d" className="ecc-link" href={`/infrastructure/hosts/${s.device.id}`}>{s.device.name}</Link> : null],
                ["Interface", s.interface?.name ?? null],
                ["Source", `${s.source.name} (${s.source_type})`],
                ["Thresholds", s.threshold_source === "user" ? `Set here (${s.threshold_direction === "below" ? "lower is worse" : "higher is worse"})` : "From the provider"],
                ["Description", s.description],
                ["External id", <code key="x">{s.external_id}</code>],
              ] as [string, React.ReactNode][]).map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{v ?? <span className="pk-muted">—</span>}</dd></div>
              ))}
            </dl>
            {canEdit && (
              <div style={{ marginTop: 12 }}>
                {editThresholds ? (
                  <ThresholdForm sensor={s} busy={busy} onSave={(c) => change(c, c.reset_thresholds ? "Provider thresholds restored from the next collection." : "Thresholds saved and applied.")} />
                ) : (
                  <button type="button" className="pk-button" onClick={() => setEditThresholds(true)}>Set thresholds</button>
                )}
              </div>
            )}
          </Panel>

          <BaselinePanel sensorId={s.id} unit={s.unit} canEdit={canEdit} />
        </div>
      )}
    </PageShell>
  );
}
