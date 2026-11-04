"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutPanelLeft } from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import {
  SIMPLE_WIDGETS,
  createDashboard,
  deleteDashboard,
  getDashboard,
  listDashboards,
  newWidgetId,
  updateDashboard,
  type Dashboard,
  type DashboardWidget,
  type WidgetSize,
  type WidgetType,
} from "@/lib/dashboards";
import { WIDGET_LABELS, WidgetBody } from "@/components/dashboards/DashboardWidgets";
import { Banner, PageShell, Panel, Pill } from "@/components/ui/PageKit";

/**
 * /dashboards — saved dashboards built from the widget registry. Each widget
 * reads an existing API with the viewer's permissions. A dashboard is
 * personal unless shared (sharing needs devices.write); only its owner edits
 * it. Sensor and interface charts are added from the sensor and interface
 * pages ("Add to dashboard"); the rest are added here.
 */

const SIZE_COLS: Record<WidgetSize, string> = { small: "pk-col-4", medium: "pk-col-6", large: "pk-col-12" };

function Editor({ dashboard, canShare, onSaved, onCancel }: {
  dashboard: Dashboard;
  canShare: boolean;
  onSaved: (d: Dashboard) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(dashboard.name);
  const [description, setDescription] = useState(dashboard.description ?? "");
  const [widgets, setWidgets] = useState<DashboardWidget[]>(dashboard.widgets);
  const [shared, setShared] = useState(dashboard.shared);
  const [adding, setAdding] = useState<WidgetType>("network_summary");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const move = (i: number, by: number) => setWidgets((ws) => {
    const next = [...ws];
    const j = i + by;
    if (j < 0 || j >= next.length) return ws;
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });
  const patch = (i: number, change: Partial<DashboardWidget>) => setWidgets((ws) => ws.map((w, k) => (k === i ? { ...w, ...change } : w)));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const d = await updateDashboard(dashboard.id, {
        name: name.trim(), description: description.trim() || null, widgets, ...(canShare || !shared ? { shared } : {}),
      });
      onSaved(d);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The dashboard could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Edit dashboard" state="ready" className="pk-col-12">
      {error && <Banner tone="bad">{error}</Banner>}
      <div className="pk-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
        <div className="pk-field"><label htmlFor="db-name">Name</label>
          <input id="db-name" className="pk-input" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} /></div>
        <div className="pk-field"><label htmlFor="db-desc">Description</label>
          <input id="db-desc" className="pk-input" value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} /></div>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }} title={canShare ? undefined : "Sharing needs the devices.write permission"}>
          <input type="checkbox" checked={shared} disabled={!canShare} onChange={(e) => setShared(e.target.checked)} />
          Shared with my organization
        </label>
      </div>
      <ul style={{ listStyle: "none", padding: 0, margin: "12px 0", display: "grid", gap: 6 }}>
        {widgets.map((w, i) => (
          <li key={w.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <strong style={{ minWidth: 180 }}>{WIDGET_LABELS[w.type]}</strong>
            <input className="pk-input" aria-label={`Title of ${WIDGET_LABELS[w.type]}`} placeholder="Title (optional)" value={w.title ?? ""} maxLength={80}
              onChange={(e) => patch(i, { title: e.target.value || null })} />
            <select className="pk-input" aria-label="Size" value={w.size} onChange={(e) => patch(i, { size: e.target.value as WidgetSize })}>
              <option value="small">Small</option>
              <option value="medium">Medium</option>
              <option value="large">Full width</option>
            </select>
            <button type="button" className="pk-button" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
            <button type="button" className="pk-button" aria-label="Move down" disabled={i === widgets.length - 1} onClick={() => move(i, 1)}>↓</button>
            <button type="button" className="pk-button" onClick={() => setWidgets((ws) => ws.filter((_, k) => k !== i))}>Remove</button>
          </li>
        ))}
        {widgets.length === 0 && <li className="pk-muted">No widgets yet.</li>}
      </ul>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <select className="pk-input" aria-label="Widget to add" value={adding} onChange={(e) => setAdding(e.target.value as WidgetType)}>
          {SIMPLE_WIDGETS.map((t) => <option key={t} value={t}>{WIDGET_LABELS[t]}</option>)}
        </select>
        <button type="button" className="pk-button" disabled={widgets.length >= 24}
          onClick={() => setWidgets((ws) => [...ws, { id: newWidgetId(adding, ws), type: adding, title: null, size: "medium", config: {} }])}>
          Add widget
        </button>
        <span className="pk-muted">Sensor and interface charts: use “Add to dashboard” on a sensor or interface page.</span>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button type="button" className="pk-button pk-button--primary" disabled={busy || !name.trim()} onClick={() => void save()}>Save</button>
        <button type="button" className="pk-button" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </Panel>
  );
}

export default function DashboardsPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const canShare = !!user && (user.is_superuser || user.permissions.includes("devices.write"));
  const list = useQuery({ queryKey: ["dashboards"], queryFn: () => listDashboards() });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [newName, setNewName] = useState("");

  const items = list.data ?? [];
  // A selection that no longer exists (deleted, unshared) falls back to the first dashboard.
  const activeId = (selectedId && items.some((x) => x.id === selectedId) ? selectedId : items[0]?.id) ?? null;
  const dash = useQuery({ queryKey: ["dashboards", activeId], queryFn: () => getDashboard(activeId as string), enabled: !!activeId, retry: false });
  const d = dash.data;

  async function create() {
    const name = newName.trim();
    if (!name) return;
    try {
      const created = await createDashboard({
        name,
        widgets: [
          { id: "network-summary-1", type: "network_summary", title: null, size: "large", config: {} },
          { id: "busiest-links-1", type: "busiest_links", title: null, size: "medium", config: {} },
          { id: "interfaces-down-1", type: "interfaces_down", title: null, size: "medium", config: {} },
        ],
      });
      setNewName("");
      await qc.invalidateQueries({ queryKey: ["dashboards"] });
      setSelectedId(created.id);
      setEditing(true);
      setMessage(null);
    } catch (err) {
      setMessage({ tone: "bad", text: err instanceof Error ? err.message : "The dashboard could not be created." });
    }
  }

  async function remove(target: Dashboard) {
    if (!window.confirm(`Delete the dashboard "${target.name}"?`)) return;
    try {
      await deleteDashboard(target.id);
      setSelectedId(null);
      setEditing(false);
      await qc.invalidateQueries({ queryKey: ["dashboards"] });
      setMessage({ tone: "good", text: `Deleted "${target.name}".` });
    } catch (err) {
      setMessage({ tone: "bad", text: err instanceof Error ? err.message : "The dashboard could not be deleted." });
    }
  }

  return (
    <PageShell
      title={d?.name ?? "Dashboards"}
      icon={LayoutPanelLeft}
      crumbs={[{ label: "Dashboards", href: "/dashboards" }, ...(d ? [{ label: d.name }] : [])]}
      subtitle={d?.description ?? "Your own dashboards and those shared in your organization"}
      meta={d ? <Pill tone={d.shared ? "info" : "neutral"}>{d.shared ? "Shared" : "Private"}</Pill> : undefined}
      actions={
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {items.length > 0 && (
            <select className="pk-input" aria-label="Dashboard" value={activeId ?? ""} onChange={(e) => { setSelectedId(e.target.value); setEditing(false); setMessage(null); }}>
              {items.map((x) => <option key={x.id} value={x.id}>{x.name}{x.owned ? "" : " (shared)"}</option>)}
            </select>
          )}
          {d?.owned && !editing && <button type="button" className="pk-button" onClick={() => setEditing(true)}>Edit</button>}
          {d?.owned && <button type="button" className="pk-button" onClick={() => void remove(d)}>Delete</button>}
          <input className="pk-input" aria-label="New dashboard name" placeholder="New dashboard name" value={newName} maxLength={100} onChange={(e) => setNewName(e.target.value)} />
          <button type="button" className="pk-button pk-button--primary" disabled={!newName.trim()} onClick={() => void create()}>Create</button>
        </div>
      }
    >
      {message && <Banner tone={message.tone}>{message.text}</Banner>}
      {list.isError && <Banner tone="bad">Dashboards could not be loaded{list.error?.message ? `: ${list.error.message}` : "."}</Banner>}
      {list.data && items.length === 0 && (
        <Banner tone="info">No dashboards yet. Name one and select Create: it starts with network widgets you can change.</Banner>
      )}
      {d && !d.owned && <Banner tone="info">Shared by a colleague: you can view it but not change it.</Banner>}
      <div className="pk-grid">
        {d && editing && (
          <Editor key={d.id + d.updated_at} dashboard={d} canShare={canShare}
            onCancel={() => setEditing(false)}
            onSaved={(saved) => {
              qc.setQueryData(["dashboards", saved.id], saved);
              void qc.invalidateQueries({ queryKey: ["dashboards"] });
              setEditing(false);
              setMessage({ tone: "good", text: "Dashboard saved." });
            }} />
        )}
        {d?.widgets.map((w) => (
          <Panel key={w.id} title={w.title ?? WIDGET_LABELS[w.type]} state="ready" className={SIZE_COLS[w.size]}>
            <WidgetBody widget={w} />
          </Panel>
        ))}
        {d && d.widgets.length === 0 && !editing && (
          <Panel title="Empty dashboard" state="ready" className="pk-col-12">
            <p className="pk-muted">This dashboard has no widgets.{d.owned ? " Select Edit to add some." : ""}</p>
          </Panel>
        )}
      </div>
    </PageShell>
  );
}
