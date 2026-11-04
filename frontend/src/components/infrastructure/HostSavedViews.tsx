"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, BookmarkPlus, Save, Trash2, Users } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import {
  createHostView,
  deleteHostView,
  listHostViews,
  updateHostView,
  type HostSavedView,
  type HostViewFilters,
  type HostViewLayout,
} from "@/lib/api";

/**
 * Saved views for the Hosts explorer: named filter sets in the facet rail.
 * Your own views come first; views a colleague shared are listed below them
 * and can be applied but not changed. Sharing needs devices.write.
 */

const QUERY_KEY = ["explorer", "hosts", "views"];

/** True when two filter sets select the same hosts (empty values ignored). */
export function sameFilters(a: HostViewFilters, b: HostViewFilters): boolean {
  const norm = (f: HostViewFilters) =>
    JSON.stringify(Object.entries(f).filter(([, v]) => v !== undefined && v !== null && v !== "").sort(([x], [y]) => x.localeCompare(y)));
  return norm(a) === norm(b);
}

export default function HostSavedViews({
  filters,
  layout,
  activeId,
  onApply,
}: {
  filters: HostViewFilters;
  layout: HostViewLayout;
  activeId: string | null;
  onApply: (view: HostSavedView) => void;
}) {
  const qc = useQueryClient();
  const { user, isAuthenticated } = useAuth();
  const canShare = !!user && (user.is_superuser || user.permissions.includes("devices.write"));
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const views = useQuery({ queryKey: QUERY_KEY, queryFn: listHostViews, enabled: isAuthenticated, staleTime: 30_000 });
  const done = () => qc.invalidateQueries({ queryKey: QUERY_KEY });
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : "The request failed");

  const create = useMutation({
    mutationFn: () => createHostView({ name: name.trim(), filters, layout, shared: canShare && shared }),
    onSuccess: (v) => {
      setAdding(false);
      setName("");
      setShared(false);
      setError(null);
      done();
      onApply(v);
    },
    onError: fail,
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof updateHostView>[1] }) => updateHostView(id, body),
    onSuccess: () => { setError(null); done(); },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteHostView(id),
    onSuccess: () => { setError(null); done(); },
    onError: fail,
  });
  const busy = create.isPending || update.isPending || remove.isPending;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (name.trim()) create.mutate();
  }

  const list = views.data ?? [];
  return (
    <section className="hx-facet hx-views" aria-label="Saved views">
      <h3>Saved views</h3>
      {views.isError ? (
        <p className="hx-facet__none">Views could not be loaded.</p>
      ) : list.length === 0 ? (
        <p className="hx-facet__none">{views.data ? "No saved views yet." : "…"}</p>
      ) : (
        <ul>
          {list.map((v) => {
            const on = v.id === activeId;
            const modified = on && (!sameFilters(v.filters, filters) || v.layout !== layout);
            return (
              <li key={v.id} className={`hx-view${on ? " hx-view--on" : ""}`}>
                <button type="button" className="hx-view__apply" onClick={() => onApply(v)} aria-current={on ? "true" : undefined} title={v.owned ? undefined : "Shared by a colleague"}>
                  {v.shared ? <Users size={13} aria-label="Shared" /> : <Bookmark size={13} aria-hidden="true" />}
                  <span className="hx-facet__label">{v.name}</span>
                  {modified && <span className="hx-view__mod">edited</span>}
                </button>
                {v.owned && (
                  <span className="hx-view__actions">
                    {modified && (
                      <button type="button" disabled={busy} title="Save the current filters to this view" aria-label={`Update ${v.name} with the current filters`}
                        onClick={() => update.mutate({ id: v.id, body: { filters, layout } })}>
                        <Save size={13} />
                      </button>
                    )}
                    {canShare && (
                      <button type="button" disabled={busy} title={v.shared ? "Stop sharing" : "Share with your organization"} aria-label={v.shared ? `Stop sharing ${v.name}` : `Share ${v.name}`}
                        aria-pressed={v.shared} onClick={() => update.mutate({ id: v.id, body: { shared: !v.shared } })}>
                        <Users size={13} />
                      </button>
                    )}
                    <button type="button" disabled={busy} title="Delete view" aria-label={`Delete ${v.name}`}
                      onClick={() => { if (window.confirm(`Delete the view "${v.name}"?`)) remove.mutate(v.id); }}>
                      <Trash2 size={13} />
                    </button>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {adding ? (
        <form className="hx-view-form" onSubmit={submit}>
          <label className="ecc-sr-only" htmlFor="hx-view-name">View name</label>
          <input id="hx-view-name" className="pk-input" autoFocus maxLength={100} placeholder="View name" value={name} onChange={(e) => setName(e.target.value)} />
          {canShare && (
            <label className="hx-facet__row">
              <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
              <span className="hx-facet__label">Share with my organization</span>
            </label>
          )}
          <span style={{ display: "flex", gap: 6 }}>
            <button type="submit" className="pk-button pk-button--primary" style={{ height: 28 }} disabled={busy || !name.trim()}>Save</button>
            <button type="button" className="pk-button" style={{ height: 28 }} onClick={() => { setAdding(false); setError(null); }}>Cancel</button>
          </span>
        </form>
      ) : (
        <button type="button" className="hx-view-add" onClick={() => setAdding(true)}>
          <BookmarkPlus size={13} aria-hidden="true" /> Save current view
        </button>
      )}
      {error && <p className="hx-view-error" role="alert">{error}</p>}
    </section>
  );
}
