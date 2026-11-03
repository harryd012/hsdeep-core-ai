"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Library } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import {
  applySensorTemplate,
  getSensorLibrary,
  getSensorRecommendations,
  type ApplyTemplateResult,
  type RecommendedHost,
  type SensorTemplate,
} from "@/lib/sensorLibrary";
import { Banner, DataTable, PageShell, Panel, Pill, StatStrip, StatTile, Tabs, titleCaseWords, type Column, type Tone } from "@/components/ui/PageKit";

/**
 * /monitoring/library — the sensor library: one template per kind of
 * measurement the collectors really report, how many of the organization's
 * sensors each covers, and (with monitoring.update) applying a template's
 * thresholds to all of its sensors at once. The Coverage tab lists hosts that
 * miss a kind of monitoring; it suggests, it never creates a check.
 */

const PAGE = 100;

function fmt(v: number | null, unit: string | null): string {
  if (v === null) return "—";
  return `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`;
}

function worse(t: SensorTemplate): string {
  if (t.direction === "above") return "Higher";
  if (t.direction === "below") return "Lower";
  return t.category === "vendor" ? "Set by vendor" : "State";
}

function statusMix(byStatus: Record<string, number>): React.ReactNode {
  const order: [string, Tone][] = [["down", "bad"], ["warning", "warn"], ["up", "good"]];
  const known = order.filter(([s]) => byStatus[s]);
  const other = Object.entries(byStatus).filter(([s]) => !order.some(([o]) => o === s)).reduce((n, [, v]) => n + v, 0);
  if (known.length === 0 && other === 0) return <span className="pk-muted">—</span>;
  return (
    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
      {known.map(([s, tone]) => <Pill key={s} tone={tone}>{byStatus[s]} {s}</Pill>)}
      {other > 0 && <Pill tone="neutral">{other} other</Pill>}
    </span>
  );
}

function ApplyForm({ template, onDone }: { template: SensorTemplate; onDone: (text: string, tone: "good" | "bad") => void }) {
  const [warning, setWarning] = useState(template.default_warning?.toString() ?? "");
  const [critical, setCritical] = useState(template.default_critical?.toString() ?? "");
  const [direction, setDirection] = useState<"above" | "below">(template.direction ?? "above");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<ApplyTemplateResult | null>(null);
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  const valid = [warning, critical].every((v) => v.trim() === "" || Number.isFinite(Number(v))) && (warning.trim() !== "" || critical.trim() !== "");
  const thresholds = { warning: num(warning), critical: num(critical), direction };

  async function run(dryRun: boolean, reset = false) {
    setBusy(true);
    try {
      const r = await applySensorTemplate(template.key, reset ? { reset: true, dry_run: dryRun } : { thresholds, dry_run: dryRun });
      if (dryRun) setPreview(r);
      else {
        setPreview(null);
        onDone(
          reset
            ? `Provider thresholds restored on ${r.changed} of ${r.matched} sensors (from their next collection).`
            : `Thresholds applied to ${r.changed} of ${r.matched} sensors; ${r.status_changed} changed status at once.`,
          "good",
        );
      }
    } catch (err) {
      onDone(err instanceof Error ? err.message : "The thresholds could not be applied.", "bad");
    } finally {
      setBusy(false);
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid) void run(true);
  };

  return (
    <div>
      <form className="pk-form" onSubmit={submit} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8, alignItems: "end" }}>
        <div className="pk-field"><label htmlFor="lib-dir">Alert when value is</label>
          <select id="lib-dir" className="pk-input" value={direction} onChange={(e) => { setDirection(e.target.value as "above" | "below"); setPreview(null); }}>
            <option value="above">At or above</option>
            <option value="below">At or below</option>
          </select>
        </div>
        <div className="pk-field"><label htmlFor="lib-warn">Warning{template.unit ? ` (${template.unit})` : ""}</label>
          <input id="lib-warn" className="pk-input" type="number" step="any" value={warning} onChange={(e) => { setWarning(e.target.value); setPreview(null); }} /></div>
        <div className="pk-field"><label htmlFor="lib-crit">Critical{template.unit ? ` (${template.unit})` : ""}</label>
          <input id="lib-crit" className="pk-input" type="number" step="any" value={critical} onChange={(e) => { setCritical(e.target.value); setPreview(null); }} /></div>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" className="pk-button" disabled={!valid || busy}>Check</button>
          {template.user_thresholds > 0 && (
            <button type="button" className="pk-button" disabled={busy}
              onClick={() => { if (window.confirm(`Return all ${template.name} sensors to their provider's thresholds?`)) void run(false, true); }}>
              Use provider thresholds
            </button>
          )}
        </div>
      </form>
      {preview && (
        <div style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span>{preview.matched === 0 ? "No sensor matches this template." : `${preview.matched} sensor${preview.matched === 1 ? "" : "s"} will use these thresholds. Status and alerts follow at once.`}</span>
          {preview.matched > 0 && (
            <button type="button" className="pk-button pk-button--primary" disabled={busy} onClick={() => void run(false)}>
              Apply to {preview.matched} sensor{preview.matched === 1 ? "" : "s"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function TemplatesTab({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const lib = useQuery({ queryKey: ["sensor-library"], queryFn: () => getSensorLibrary() });
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const templates = lib.data?.templates ?? [];
  const selected = templates.find((t) => t.key === selectedKey) ?? null;
  const inUse = templates.filter((t) => t.sensors > 0);
  const sensors = templates.reduce((n, t) => n + t.sensors, 0);
  const own = templates.reduce((n, t) => n + t.user_thresholds, 0);
  const uncatalogued = lib.data?.uncatalogued ?? [];

  const columns: Column<SensorTemplate>[] = [
    { key: "name", header: "Template", sortValue: (t) => t.name, render: (t) => <><strong>{t.name}</strong><div className="pk-muted">{titleCaseWords(t.category)}</div></> },
    { key: "collectors", header: "Collectors", render: (t) => <span className="pk-muted">{t.source_types.join(", ")}</span> },
    { key: "unit", header: "Unit", render: (t) => t.unit ?? <span className="pk-muted">—</span> },
    { key: "worse", header: "Worse when", sortValue: (t) => worse(t), render: (t) => worse(t) },
    {
      key: "defaults", header: "Default thresholds",
      render: (t) => (t.default_warning === null && t.default_critical === null
        ? <span className="pk-muted">None defined</span>
        : <span title={t.default_origin ?? undefined}>{fmt(t.default_warning, t.unit)} / {fmt(t.default_critical, t.unit)}</span>),
    },
    { key: "sensors", header: "Sensors", numeric: true, sortValue: (t) => t.sensors, render: (t) => t.sensors.toLocaleString("en-US") },
    { key: "status", header: "Status", render: (t) => statusMix(t.by_status) },
    { key: "own", header: "Own thresholds", numeric: true, sortValue: (t) => t.user_thresholds, render: (t) => t.user_thresholds || <span className="pk-muted">0</span> },
  ];

  return (
    <>
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
      {lib.isError && <Banner tone="bad">The sensor library could not be loaded{lib.error?.message ? `: ${lib.error.message}` : "."}</Banner>}
      <StatStrip>
        <StatTile label="Templates" value={lib.data ? String(templates.length) : "—"} info="Kinds of measurement the platform's collectors report." loading={lib.isLoading} />
        <StatTile label="In use" value={lib.data ? String(inUse.length) : "—"} info="Templates with at least one of your sensors." loading={lib.isLoading} />
        <StatTile label="Sensors covered" value={lib.data ? sensors.toLocaleString("en-US") : "—"} info="Your sensors that match a template." loading={lib.isLoading} />
        <StatTile label="On own thresholds" value={lib.data ? own.toLocaleString("en-US") : "—"} info="Sensors judged by thresholds set here rather than by their provider." loading={lib.isLoading} />
      </StatStrip>
      <div className="pk-grid">
        <Panel title="Templates" info="Select a template for details and, with permission, to apply its thresholds." state={lib.isLoading ? "loading" : "ready"} className="pk-col-12">
          <DataTable caption="Sensor templates" columns={columns} rows={templates} rowKey={(t) => t.key}
            onRowClick={(t) => { setSelectedKey(t.key); setMessage(null); }} defaultSort={{ key: "sensors", dir: "desc" }} maxHeight={480} />
        </Panel>
        {selected && (
          <Panel title={selected.name} info={selected.description} state="ready" className="pk-col-12">
            <dl className="hd-props">
              {([
                ["Measures", selected.description],
                ["Metric types", <code key="m">{selected.metric_types.join(", ")}</code>],
                ["Collectors", selected.source_types.join(", ")],
                ["Reporting for you", selected.reporting_source_types.length ? selected.reporting_source_types.join(", ") : null],
                ["Default thresholds", selected.default_origin ? `${fmt(selected.default_warning, selected.unit)} / ${fmt(selected.default_critical, selected.unit)} (${selected.default_origin})` : "None defined"],
              ] as [string, React.ReactNode][]).map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{v ?? <span className="pk-muted">—</span>}</dd></div>
              ))}
            </dl>
            <div style={{ marginTop: 12 }}>
              {!selected.thresholds_applicable ? (
                <p className="pk-muted">{selected.key === "prtg_sensor"
                  ? "PRTG defines each sensor's kind and thresholds; set them per sensor or in PRTG."
                  : "A state sensor (up or down): it has no thresholds to apply."}</p>
              ) : selected.sensors === 0 ? (
                <p className="pk-muted">None of your sensors use this template yet.</p>
              ) : canEdit ? (
                <ApplyForm key={selected.key} template={selected} onDone={(text, tone) => {
                  setMessage({ text, tone });
                  void qc.invalidateQueries({ queryKey: ["sensor-library"] });
                }} />
              ) : (
                <p className="pk-muted">Applying thresholds needs the monitoring update permission.</p>
              )}
            </div>
          </Panel>
        )}
        {uncatalogued.length > 0 && (
          <Panel title="Not in the library" info="Sensor types your collectors report that no template covers yet." state="ready" className="pk-col-12">
            <DataTable caption="Uncatalogued sensor types" rowKey={(u) => u.metric_type} rows={uncatalogued}
              columns={[
                { key: "type", header: "Metric type", render: (u) => <code>{u.metric_type}</code> },
                { key: "sources", header: "Collectors", render: (u) => u.source_types.join(", ") },
                { key: "n", header: "Sensors", numeric: true, render: (u) => u.total },
              ]} />
          </Panel>
        )}
      </div>
    </>
  );
}

function CoverageTab() {
  const [rule, setRule] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const rec = useQuery({ queryKey: ["sensor-recommendations", rule, offset], queryFn: () => getSensorRecommendations(rule, PAGE, offset) });
  const rules = rec.data?.rules ?? [];
  const titles = Object.fromEntries(rules.map((r) => [r.key, r.title]));
  const active = rules.find((r) => r.key === rule);

  const columns: Column<RecommendedHost>[] = [
    { key: "name", header: "Host", sortValue: (h) => h.name, render: (h) => <Link className="ecc-link" href={`/infrastructure/hosts/${h.device_id}`}>{h.name}</Link> },
    { key: "type", header: "Type", sortValue: (h) => h.device_type, render: (h) => titleCaseWords(h.device_type) },
    { key: "address", header: "Address", render: (h) => h.address ?? <span className="pk-muted">—</span> },
    { key: "sensors", header: "Sensors", numeric: true, sortValue: (h) => h.sensors, render: (h) => h.sensors },
    { key: "ifaces", header: "Interfaces", numeric: true, sortValue: (h) => h.interfaces, render: (h) => h.interfaces },
    { key: "rules", header: "Missing", render: (h) => <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>{h.rules.map((r) => <Pill key={r} tone={r === "unmonitored" ? "bad" : "warn"}>{titles[r] ?? r}</Pill>)}</span> },
  ];

  return (
    <>
      {rec.isError && <Banner tone="bad">Coverage could not be loaded{rec.error?.message ? `: ${rec.error.message}` : "."}</Banner>}
      <StatStrip>
        {rules.map((r) => (
          <StatTile key={r.key} label={r.title} value={rec.data ? r.hosts.toLocaleString("en-US") : "—"} info={r.description} loading={rec.isLoading}
            tone={r.hosts === 0 ? "good" : r.key === "unmonitored" ? "bad" : "warn"} />
        ))}
      </StatStrip>
      {rules.length > 0 && (
        <Tabs<string>
          value={rule ?? "all"}
          onChange={(k) => { setRule(k === "all" ? null : k); setOffset(0); }}
          label="Filter hosts by gap"
          tabs={[{ key: "all", label: "All gaps" }, ...rules.map((r) => ({ key: r.key, label: `${r.title} (${r.hosts})` }))]}
        />
      )}
      {active && <Banner tone="info">{active.title}: {active.action}</Banner>}
      <Panel
        title={active ? `${active.title} (${rec.data?.total ?? 0})` : `Hosts with a gap (${rec.data?.total ?? 0})`}
        info="Computed from the sensors and polled interfaces each host has now. Nothing is added automatically: covering a host needs a monitoring source and, for most collectors, credentials."
        state={rec.isLoading ? "loading" : "ready"}
      >
        <DataTable caption="Hosts with a monitoring gap" columns={columns} rows={rec.data?.hosts ?? []} rowKey={(h) => h.device_id}
          emptyText="Every host has the monitoring its type calls for." maxHeight={560} />
        {rec.data && (offset > 0 || rec.data.truncated) && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
            <button type="button" className="pk-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
            <span className="pk-muted">{offset + 1}–{offset + rec.data.hosts.length} of {rec.data.total}</span>
            <button type="button" className="pk-button" disabled={!rec.data.truncated} onClick={() => setOffset(offset + PAGE)}>Next</button>
          </div>
        )}
      </Panel>
    </>
  );
}

export default function SensorLibraryPage() {
  const { user } = useAuth();
  const canEdit = !!user && (user.is_superuser || user.permissions.includes("monitoring.update"));
  const [tab, setTab] = useState<"templates" | "coverage">("templates");
  return (
    <PageShell
      title="Sensor Library"
      icon={Library}
      crumbs={[{ label: "Monitoring", href: "/monitoring" }, { label: "Sensor Library" }]}
      subtitle="What your collectors measure, the thresholds they are judged by, and where monitoring is missing"
      actions={<Tabs<"templates" | "coverage"> value={tab} onChange={setTab} label="Sensor library view" tabs={[{ key: "templates", label: "Templates" }, { key: "coverage", label: "Coverage" }]} />}
    >
      {tab === "templates" ? <TemplatesTab canEdit={canEdit} /> : <CoverageTab />}
    </PageShell>
  );
}
