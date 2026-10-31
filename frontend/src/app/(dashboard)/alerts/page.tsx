"use client";

import { useEffect, useState } from "react";
import { Alert, AlertSummary, listAlerts, getAlertSummary } from "@/lib/api";
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

type SeverityFilter = "all" | "critical" | "warning" | "info";

const SEVERITY_TONE = { critical: "bad", warning: "warn", info: "info" } as const;

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<SeverityFilter>("all");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [alertList, alertSum] = await Promise.all([
          listAlerts({ limit: 50 }),
          getAlertSummary(),
        ]);
        if (!cancelled) {
          setAlerts(alertList);
          setSummary(alertSum);
          setError(null);
          setUpdatedAt(new Date().toISOString());
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load alerts");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    load();
    const timer = window.setInterval(load, 15000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const loading = !loaded;
  const n = (v: number | undefined) => (summary && v !== undefined ? v.toLocaleString("en-US") : "—");
  const q = search.trim().toLowerCase();
  const visible = alerts.filter(
    (a) =>
      (severity === "all" || a.severity === severity) &&
      (!q ||
        a.title.toLowerCase().includes(q) ||
        (a.message ?? "").toLowerCase().includes(q) ||
        (a.device_name ?? "").toLowerCase().includes(q) ||
        (a.sensor_name ?? "").toLowerCase().includes(q)),
  );

  const columns: Column<Alert>[] = [
    {
      key: "severity",
      header: "Severity",
      width: 100,
      sortValue: (a) => ({ critical: 0, warning: 1, info: 2 })[a.severity] ?? 3,
      render: (a) => <Pill tone={SEVERITY_TONE[a.severity as keyof typeof SEVERITY_TONE] ?? "neutral"}>{titleCaseWords(a.severity)}</Pill>,
    },
    {
      key: "alert",
      header: "Alert",
      sortValue: (a) => a.title.toLowerCase(),
      render: (a) => (
        <>
          <span className="pk-cell-main">{a.title}</span>
          {/* Render the server-derived PLAIN TEXT. `a.description` is the raw
              provider value and can contain PRTG markup; it is never rendered
              in the list. */}
          {a.message && (
            <span className="pk-cell-sub pk-truncate" title={a.message}>
              {a.message}
              {a.description_contains_markup ? " · raw payload available" : ""}
            </span>
          )}
        </>
      ),
    },
    {
      key: "resource",
      header: "Resource",
      sortValue: (a) => (a.device_name ?? a.sensor_name ?? "").toLowerCase(),
      render: (a) => (
        <>
          <span className="pk-cell-main">{a.device_name ?? a.sensor_name ?? "—"}</span>
          <span className="pk-cell-sub">{[a.site_name, titleCaseWords(a.source)].filter(Boolean).join(" · ")}</span>
        </>
      ),
    },
    { key: "status", header: "Status", sortValue: (a) => a.status, render: (a) => <Pill tone={statusTone(a.status)}>{titleCaseWords(a.status)}</Pill> },
    { key: "count", header: "Occurrences", numeric: true, sortValue: (a) => a.occurrence_count, render: (a) => a.occurrence_count.toLocaleString("en-US") },
    {
      key: "last",
      header: "Last seen",
      sortValue: (a) => new Date(a.last_occurred_at).getTime(),
      render: (a) => <span className="pk-muted" title={new Date(a.last_occurred_at).toLocaleString()}>{ago(a.last_occurred_at)}</span>,
    },
  ];

  return (
    <PageShell
      title="Alerts"
      subtitle="Alerts raised by every connected monitoring source, de-duplicated per signal."
      meta={updatedAt ? `Updated ${ago(updatedAt)} · refreshes every 15 s` : undefined}
    >
      {error && <Banner tone="warn">Alerts could not be refreshed: {error}</Banner>}

      <StatStrip>
        <StatTile label="Critical" value={n(summary?.by_severity.critical)} alarm={!!summary?.by_severity.critical} info="Alerts with critical severity." href="/alerts" loading={loading} />
        <StatTile label="Warning" value={n(summary?.by_severity.warning)} info="Alerts with warning severity." loading={loading} />
        <StatTile label="Info" value={n(summary?.by_severity.info)} info="Informational alerts." loading={loading} />
        <StatTile label="Open" value={n(summary?.by_status.open)} info="Alerts nobody has acknowledged yet." loading={loading} />
        <StatTile label="Acknowledged" value={n(summary?.by_status.acknowledged)} info="Alerts an operator has acknowledged." loading={loading} />
        <StatTile label="Total" value={n(summary?.total)} info="All alerts for this tenant." sub={summary ? `${n(summary.by_status.resolved)} resolved` : undefined} loading={loading} />
      </StatStrip>

      <Panel
        title="Recent alerts"
        info="The 50 most recent alerts. Severity and search filter this list only."
        aside={loaded ? `${visible.length} of ${alerts.length} shown` : undefined}
        state={loading ? "loading" : "ready"}
        skeletonHeight={360}
      >
        <div className="pk-toolbar" style={{ marginBottom: 10 }}>
          <SearchBox value={search} onChange={setSearch} placeholder="Search alerts, devices, sensors" label="Search alerts" />
          <Tabs
            label="Severity"
            value={severity}
            onChange={setSeverity}
            tabs={[
              { key: "all", label: "All", count: alerts.length },
              { key: "critical", label: "Critical", count: alerts.filter((a) => a.severity === "critical").length },
              { key: "warning", label: "Warning", count: alerts.filter((a) => a.severity === "warning").length },
              { key: "info", label: "Info", count: alerts.filter((a) => a.severity === "info").length },
            ]}
          />
        </div>
        <DataTable
          caption="Recent alerts"
          columns={columns}
          rows={visible}
          rowKey={(a) => a.id}
          defaultSort={{ key: "last", dir: "desc" }}
          maxHeight="max(480px, calc(100vh - 300px))"
          emptyText={alerts.length === 0 ? "No alerts. All monitored signals are quiet." : "No alerts match the current filter."}
        />
      </Panel>
    </PageShell>
  );
}
