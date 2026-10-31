"use client";

import { useEffect, useMemo, useState } from "react";
import { MonitoringSourceOut, SensorOut, listMonitoringSources, listSourceSensors, listSensors } from "@/lib/api";
import { ALERTING_STATES, classifySensorState, type SensorState } from "@/lib/sensorState";
import FreshnessIndicator from "@/components/shared/FreshnessIndicator";
import {
  Banner,
  DataTable,
  PageShell,
  Panel,
  Pill,
  SearchBox,
  StatStrip,
  StatTile,
  Tabs,
  ago,
  statusTone,
  titleCaseWords,
  type Column,
} from "@/components/ui/PageKit";

/** A tenant-owned PRTG source paired with the canonical Sensor rows persisted
 *  for it by the Collector Framework. */
interface PrtgSensorGroup {
  source: MonitoringSourceOut;
  sensors: SensorOut[];
}

interface SensorRow {
  source: MonitoringSourceOut;
  sensor: SensorOut;
  state: ReturnType<typeof classifySensorState>;
}

type SensorFilter = "all" | "alerting" | "stale" | "healthy";

/** Render a canonical sensor reading, e.g. "12.4 ms", or an em dash when the
 *  collector has not recorded a numeric value. */
function formatSensorValue(sensor: SensorOut): string {
  if (sensor.current_value === null) return "—";
  return sensor.unit ? `${sensor.current_value} ${sensor.unit}` : String(sensor.current_value);
}

const STATE_TONE: Record<SensorState, "good" | "warn" | "bad" | "neutral"> = {
  HEALTHY: "good",
  WARNING: "warn",
  STALE: "warn",
  CRITICAL: "bad",
  DOWN: "bad",
  COLLECTION_FAILED: "bad",
  UNKNOWN: "neutral",
  NOT_CONFIGURED: "neutral",
} as Record<SensorState, "good" | "warn" | "bad" | "neutral">;

export default function MonitoringPage() {
  const [sensors, setSensors] = useState<SensorOut[]>([]);
  const [sources, setSources] = useState<MonitoringSourceOut[]>([]);
  const [prtgSensorGroups, setPrtgSensorGroups] = useState<PrtgSensorGroup[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<SensorFilter>("all");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [srcList, senList] = await Promise.all([
          listMonitoringSources(),
          listSensors(),
        ]);
        // Provider sensor rows come from the canonical, tenant-scoped
        // per-source endpoint. Each source is its own request so a failure on
        // one PRTG source cannot masquerade as a different tenant's data.
        const prtgSources = srcList.filter((s) => s.source_type === "prtg");
        const groups: PrtgSensorGroup[] = await Promise.all(
          prtgSources.map(async (source) => ({
            source,
            sensors: await listSourceSensors(source.id),
          })),
        );
        if (!cancelled) {
          setSources(srcList);
          setSensors(senList);
          setPrtgSensorGroups(groups);
          setError(null);
          setUpdatedAt(new Date().toISOString());
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load monitoring data");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    const timer = window.setInterval(load, 30000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  // Canonical states, not raw status strings: a `up` sensor whose last value is
  // older than 2h classifies as STALE and must not be counted as healthy.
  const states = sensors.map((s) => classifySensorState(s.status, s.last_value_at).state);
  const upCount = states.filter((s) => s === "HEALTHY").length;
  const downCount = states.filter((s) => ALERTING_STATES.includes(s)).length;
  const staleCount = states.filter((s) => s === "STALE").length;
  const connected = sources.filter((s) => statusTone(s.status) === "good").length;

  const rows: SensorRow[] = useMemo(
    () =>
      prtgSensorGroups.flatMap((group) =>
        group.sensors.map((sensor) => ({ source: group.source, sensor, state: classifySensorState(sensor.status, sensor.last_value_at) })),
      ),
    [prtgSensorGroups],
  );
  const visible = rows.filter((r) => {
    if (filter === "alerting" && !ALERTING_STATES.includes(r.state.state)) return false;
    if (filter === "stale" && r.state.state !== "STALE") return false;
    if (filter === "healthy" && r.state.state !== "HEALTHY") return false;
    const q = search.trim().toLowerCase();
    return !q || r.sensor.name.toLowerCase().includes(q) || r.source.name.toLowerCase().includes(q) || (r.sensor.metric_type ?? "").toLowerCase().includes(q);
  });

  const columns: Column<SensorRow>[] = [
    {
      key: "sensor",
      header: "Sensor",
      sortValue: (r) => r.sensor.name.toLowerCase(),
      render: (r) => (
        <>
          <span className="pk-cell-main">{r.sensor.name}</span>
          <span className="pk-cell-sub">{r.source.name}</span>
        </>
      ),
    },
    {
      key: "status",
      header: "Status",
      sortValue: (r) => r.state.label,
      render: (r) => <Pill tone={STATE_TONE[r.state.state] ?? "neutral"}>{r.state.label}</Pill>,
    },
    { key: "value", header: "Value", numeric: true, render: (r) => <span className="pk-mono">{formatSensorValue(r.sensor)}</span> },
    { key: "metric", header: "Metric", sortValue: (r) => r.sensor.metric_type, render: (r) => <span className="pk-muted">{titleCaseWords(r.sensor.metric_type)}</span> },
    {
      key: "fresh",
      header: "Freshness",
      sortValue: (r) => (r.sensor.last_value_at ? new Date(r.sensor.last_value_at).getTime() : null),
      render: (r) => <FreshnessIndicator lastCollectedAt={r.sensor.last_value_at} showAge />,
    },
  ];

  const loading = !loaded;
  const na = (v: number) => (loaded && !error ? v.toLocaleString("en-US") : "—");

  return (
    <PageShell
      title="Monitoring"
      subtitle="Collection sources and the sensors they persist for this tenant. Read through the tenant-scoped monitoring API — no direct probe access."
      meta={updatedAt ? `Updated ${ago(updatedAt)} · refreshes every 30 s` : undefined}
    >
      {error && <Banner tone="warn">Monitoring data could not be refreshed: {error}</Banner>}

      <StatStrip>
        <StatTile label="Sources" value={na(sources.length)} info="Monitoring sources configured for this tenant." sub={loaded ? `${connected} connected` : undefined} loading={loading} />
        <StatTile label="Sensors" value={na(sensors.length)} info="All sensors persisted by the collector framework." loading={loading} />
        <StatTile label="Healthy" value={na(upCount)} info="Sensors that are up with a recent value (canonical state, not the raw status)." sub={loaded && sensors.length ? `${Math.round((upCount / sensors.length) * 100)}% of sensors` : undefined} loading={loading} />
        <StatTile label="Alerting" value={na(downCount)} alarm={downCount > 0} info="Sensors in a warning, critical, down or collection-failed state." loading={loading} />
        <StatTile label="Stale" value={na(staleCount)} info="Sensors whose last value is too old to trust as current." loading={loading} />
      </StatStrip>

      <div className="pk-grid">
        <Panel
          title="Sources"
          info="Each configured collection source and its last sync."
          aside={loaded ? `${sources.length} total` : undefined}
          state={loading ? "loading" : sources.length === 0 ? "empty" : "ready"}
          emptyText="No monitoring sources configured."
          className="pk-col-4"
        >
          <ul className="pk-list">
            {sources.map((s) => (
              <li key={s.id}>
                <div className="pk-list__main">
                  <span className="pk-cell-main">{s.name}</span>
                  <span className="pk-cell-sub">
                    {titleCaseWords(s.vendor ?? s.source_type)}
                    {s.hostname ? ` · ${s.hostname}` : ""}
                  </span>
                  {s.last_error && (
                    <span className="pk-cell-sub" style={{ color: "var(--ecc-bad)" }} title={s.last_error}>
                      <span className="pk-truncate">{s.last_error}</span>
                    </span>
                  )}
                </div>
                <div className="pk-list__aside">
                  <Pill tone={statusTone(s.status)}>{titleCaseWords(s.status)}</Pill>
                  <div style={{ marginTop: 4 }}>{s.last_sync_at ? `Synced ${ago(s.last_sync_at)}` : "Never synced"}</div>
                </div>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          title="PRTG Sensors"
          info="Sensor rows collected for this tenant's PRTG sources."
          aside={loaded ? `${visible.length} of ${rows.length}` : undefined}
          state={loading ? "loading" : prtgSensorGroups.length === 0 ? "empty" : "ready"}
          emptyText="No PRTG monitoring source is configured for this tenant."
          skeletonHeight={320}
          className="pk-col-8"
        >
          <div className="pk-toolbar" style={{ marginBottom: 10 }}>
            <SearchBox value={search} onChange={setSearch} placeholder="Search sensors, sources, metrics" label="Search sensors" />
            <Tabs
              label="Sensor state"
              value={filter}
              onChange={setFilter}
              tabs={[
                { key: "all", label: "All", count: rows.length },
                { key: "alerting", label: "Alerting", count: rows.filter((r) => ALERTING_STATES.includes(r.state.state)).length },
                { key: "stale", label: "Stale", count: rows.filter((r) => r.state.state === "STALE").length },
                { key: "healthy", label: "Healthy", count: rows.filter((r) => r.state.state === "HEALTHY").length },
              ]}
            />
          </div>
          <DataTable
            caption="PRTG sensors"
            columns={columns}
            rows={visible}
            rowKey={(r) => r.sensor.id}
            emptyText={rows.length === 0 ? "No sensor rows have been collected for these sources yet." : "No sensors match the current filter."}
            maxHeight="max(480px, calc(100vh - 300px))"
          />
        </Panel>
      </div>
    </PageShell>
  );
}
