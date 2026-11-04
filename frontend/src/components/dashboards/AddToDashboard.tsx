"use client";

/**
 * "Add to dashboard" for a chart widget (observability plan P6 slice 2): used
 * on the sensor and interface pages so a chart is added from what the user is
 * looking at, never by typing an id. Appends to one of the user's own
 * dashboards, or creates one.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { createDashboard, getDashboard, listDashboards, newWidgetId, updateDashboard, type DashboardWidget } from "@/lib/dashboards";

export default function AddToDashboard({ widget }: { widget: Omit<DashboardWidget, "id"> }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["dashboards"], queryFn: () => listDashboards(), enabled: open });
  const own = (list.data ?? []).filter((d) => d.owned);

  async function add() {
    setBusy(true);
    setNote(null);
    try {
      if (target === "__new__" || own.length === 0) {
        const name = newName.trim() || "My dashboard";
        await createDashboard({ name, widgets: [{ ...widget, id: newWidgetId(widget.type, []) }] });
        setNote(`Added to the new dashboard "${name}".`);
      } else {
        const d = await getDashboard(target || own[0].id);
        await updateDashboard(d.id, { widgets: [...d.widgets, { ...widget, id: newWidgetId(widget.type, d.widgets) }] });
        setNote(`Added to "${d.name}".`);
      }
      void qc.invalidateQueries({ queryKey: ["dashboards"] });
      setOpen(false);
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Could not add the chart.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
        <button type="button" className="pk-button" onClick={() => { setOpen(true); setNote(null); }}>Add to dashboard</button>
        {note && <span className="pk-muted">{note}</span>}
      </span>
    );
  }
  const creating = target === "__new__" || (list.data && own.length === 0);
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      {own.length > 0 && (
        <select className="pk-input" aria-label="Dashboard" value={target || own[0].id} onChange={(e) => setTarget(e.target.value)}>
          {own.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          <option value="__new__">New dashboard…</option>
        </select>
      )}
      {creating && (
        <input className="pk-input" aria-label="New dashboard name" placeholder="New dashboard name" value={newName} maxLength={100} onChange={(e) => setNewName(e.target.value)} />
      )}
      <button type="button" className="pk-button pk-button--primary" disabled={busy || list.isLoading} onClick={() => void add()}>Add</button>
      <button type="button" className="pk-button" onClick={() => setOpen(false)}>Cancel</button>
      {note && <span className="pk-muted">{note}</span>}
    </span>
  );
}
