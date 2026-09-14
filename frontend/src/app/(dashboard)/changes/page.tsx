"use client";

/**
 * /changes — Change Management (CAB) module.
 *
 * Connects the frontend to the EXISTING backend /api/change-requests router
 * (model + CRUD + status lifecycle + summary already ship server-side). This
 * page is the first frontend surface for that backend capability.
 *
 * Built in the approved HSDEEP CORE AI dark glass/cyan language (same as the
 * login + refined dashboard), NOT a new visual system.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ListChecks, Plus, RefreshCw, AlertTriangle, X, CheckCircle2, Hammer,
  FileCheck2, Loader2, Search,
} from "lucide-react";
import {
  createChangeRequest, getChangeRequestSummary, listChangeRequests, updateChangeStatus,
  type ChangeRequest, type ChangeRequestSummary, type ChangeStatus, type ChangeType,
} from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  submitted: "Submitted",
  approved: "Approved",
  rejected: "Rejected",
  scheduled: "Scheduled",
  implementing: "Implementing",
  verifying: "Verifying",
  closed: "Closed",
  cancelled: "Cancelled",
};

const STATUS_COLOR: Record<string, string> = {
  draft: "bg-white/5 text-white/50 border-white/15",
  submitted: "bg-blue-500/10 text-blue-300 border-blue-400/30",
  approved: "bg-emerald-500/10 text-emerald-300 border-emerald-400/30",
  rejected: "bg-red-500/10 text-red-300 border-red-400/30",
  scheduled: "bg-cyan-500/10 text-cyan-300 border-cyan-400/30",
  implementing: "bg-amber-500/10 text-amber-300 border-amber-400/30",
  verifying: "bg-teal-500/10 text-teal-300 border-teal-400/30",
  closed: "bg-white/10 text-white/40 border-white/20",
  cancelled: "bg-white/5 text-white/35 border-white/15",
};

const RISK_COLOR: Record<string, string> = {
  low: "text-emerald-300 border-emerald-400/25",
  medium: "text-amber-300 border-amber-400/25",
  high: "text-orange-300 border-orange-400/25",
  critical: "text-red-300 border-red-400/30",
};

const TYPES: { value: ChangeType; label: string }[] = [
  { value: "config", label: "Configuration" },
  { value: "software", label: "Software" },
  { value: "hardware", label: "Hardware" },
  { value: "security", label: "Security" },
  { value: "network", label: "Network" },
  { value: "cloud", label: "Cloud" },
  { value: "database", label: "Database" },
  { value: "application", label: "Application" },
  { value: "other", label: "Other" },
];

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
function ChangeDashboard() {
  const [changes, setChanges] = useState<ChangeRequest[]>([]);
  const [summary, setSummary] = useState<ChangeRequestSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Core fetch: sets state only after awaits (safe for mount effects).
  async function fetchData() {
    try {
      const [list, summ] = await Promise.all([
        listChangeRequests({ limit: 200 }),
        getChangeRequestSummary().catch(() => null),
      ]);
      setChanges(list);
      setSummary(summ);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load change requests.");
    } finally {
      setLoading(false);
    }
  }

  // Manual refresh (user action): show the loading state, then fetch.
  async function load() {
    setLoading(true);
    await fetchData();
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [list, summ] = await Promise.all([
          listChangeRequests({ limit: 200 }),
          getChangeRequestSummary().catch(() => null),
        ]);
        if (cancelled) return;
        setChanges(list);
        setSummary(summ);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load change requests.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const byStatus = useMemo(() => {
    const map: Record<string, number> = {};
    for (const c of changes) map[c.status] = (map[c.status] ?? 0) + 1;
    return map;
  }, [changes]);

  const visible = useMemo(() => {
    return changes
      .filter((c) => statusFilter === "all" || c.status === statusFilter)
      .filter((c) =>
        !search ||
        c.title.toLowerCase().includes(search.toLowerCase()) ||
        c.change_number.toLowerCase().includes(search.toLowerCase()),
      )
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  }, [changes, statusFilter, search]);

  const statusOptions = ["all", ...Array.from(new Set(changes.map((c) => c.status)))];

  async function handleTransition(c: ChangeRequest, next: ChangeStatus) {
    setActionMsg(null);
    try {
      await updateChangeStatus(c.id, next);
      setActionMsg(`${c.change_number} → ${STATUS_LABEL[next]}`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Status transition failed.");
    }
  }

  const nextAction = (c: ChangeRequest): { next: ChangeStatus; label: string; icon: ReactNode } | null => {
    switch (c.status) {
      case "draft": return { next: "submitted", label: "Submit", icon: <CheckCircle2 size={15} /> };
      case "submitted": return { next: "approved", label: "Approve", icon: <FileCheck2 size={15} /> };
      case "approved": return { next: "scheduled", label: "Schedule", icon: <ListChecks size={15} /> };
      case "scheduled": return { next: "implementing", label: "Implement", icon: <Hammer size={15} /> };
      case "implementing": return { next: "verifying", label: "Verify", icon: <CheckCircle2 size={15} /> };
      case "verifying": return { next: "closed", label: "Close", icon: <CheckCircle2 size={15} /> };
      default: return null;
    }
  };

return (
    <div className="hs-dashboard page-shell">
      <header className="hud-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold uppercase tracking-[0.1em] text-white">Change Management</h1>
            <p className="mt-0.5 text-[11px] uppercase tracking-[0.18em] text-white/45">CAB · Change Advisory Board · Multi-Tenant</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void load()}
              className="flex items-center gap-2 rounded-lg border border-cyan-400/30 bg-white/5 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white/70 transition hover:bg-white/10 hover:text-white"
            >
              <RefreshCw size={15} /> Refresh
            </button>
            <button
              onClick={() => { setShowCreate(true); setError(null); }}
              className="flex items-center gap-2 rounded-lg border border-cyan-400/50 bg-gradient-to-br from-[#17d9ed] to-[#078df5] px-4 py-2 text-xs font-bold uppercase tracking-wide text-[#02080f] transition hover:brightness-110 hover:shadow-[0_0_18px_rgba(52,215,255,0.4)]"
            >
              <Plus size={15} /> New Change
            </button>
          </div>
        </div>
      </header>

      {actionMsg && (
        <div className="rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-300">
          {actionMsg}
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-400/30 bg-red-500/10 px-4 py-2 text-sm text-red-300">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      {/* Stat cards */}
      <section className="hs-zone">
        <h2 className="hs-zone__title">Change Overview</h2>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="Total Changes" value={summary?.total_changes ?? changes.length} />
          <StatCard label="Submitted / Pending" value={(byStatus.submitted ?? 0) + (byStatus.approved ?? 0)} tone="text-blue-300" />
          <StatCard label="In Progress" value={(byStatus.scheduled ?? 0) + (byStatus.implementing ?? 0) + (byStatus.verifying ?? 0)} tone="text-amber-300" />
          <StatCard label="Closed" value={byStatus.closed ?? 0} tone="text-emerald-300" />
        </div>
      </section>
{/* Change requests table */}
      <section className="hs-zone">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="hs-zone__title !mb-0 !border-0">Change Requests</h2>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/35" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search changes…"
                className="w-56 rounded-lg border border-white/15 bg-white/5 py-2 pl-9 pr-3 text-sm text-white outline-none placeholder:text-white/35 focus:border-cyan-400/50"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-lg border border-white/15 bg-[#04131f] py-2 px-3 text-sm text-white outline-none focus:border-cyan-400/50"
            >
              {statusOptions.map((s) => (
                <option key={s} value={s}>{s === "all" ? "All Statuses" : STATUS_LABEL[s] ?? s}</option>
              ))}
            </select>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center p-10 text-white/50">
            <Loader2 size={18} className="mr-2 animate-spin" /> Loading changes…
          </div>
        ) : visible.length === 0 ? (
          <div className="hud-panel p-8 text-center text-sm text-white/40">
            No change requests found. Create one to get started.
          </div>
        ) : (
          <div className="hud-panel overflow-hidden !p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-[11px] uppercase tracking-[0.12em] text-white/50">
                    <th className="px-4 py-3">Change</th>
                    <th className="px-3 py-3">Type</th>
                    <th className="px-3 py-3">Risk</th>
                    <th className="px-3 py-3">Status</th>
                    <th className="px-3 py-3">Created</th>
                    <th className="px-3 py-3">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((c) => {
                    const expanded = expandedId === c.id;
                    const act = nextAction(c);
                    return (
                      <tr key={c.id} className="border-b border-white/5 hover:bg-cyan-400/5">
                        <td className="px-4 py-3">
                          <p className="font-semibold text-cyan-300">{c.change_number}</p>
                          <p className="max-w-[280px] truncate text-white/80">{c.title}</p>
                          {expanded && c.description && (
                            <p className="mt-1 max-w-[340px] rounded bg-white/5 p-2 text-xs text-white/60">{c.description}</p>
                          )}
                        </td>
                        <td className="px-3 py-3 text-xs text-white/60">{TYPES.find((t) => t.value === c.change_type)?.label ?? c.change_type}</td>
                        <td className="px-3 py-3">
                          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-bold uppercase ${RISK_COLOR[c.risk_level] ?? ""}`}>{c.risk_level}</span>
                        </td>
                        <td className="px-3 py-3">
                          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase ${STATUS_COLOR[c.status] ?? ""}`}>
                            {STATUS_LABEL[c.status] ?? c.status}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-xs text-white/50">{timeAgo(c.created_at)}</td>
                        <td className="px-3 py-3">
                          <div className="flex items-center gap-1.5">
                            <button
                              onClick={() => setExpandedId(expanded ? null : c.id)}
                              title="Toggle details"
                              className="rounded p-1.5 text-white/50 hover:bg-white/10 hover:text-cyan-300"
                            >
                              <FileCheck2 size={15} />
                            </button>
                            {act && (
                              <button
                                onClick={() => void handleTransition(c, act.next)}
                                title={act.label}
                                className="flex items-center gap-1 rounded-md border border-cyan-400/40 bg-cyan-400/10 px-2 py-1 text-[11px] font-semibold uppercase text-cyan-200 transition hover:bg-cyan-400/20"
                              >
                                {act.icon} {act.label}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {showCreate && <CreateChangeModal onClose={() => setShowCreate(false)} onCreated={async () => { setShowCreate(false); await load(); }} />}
    </div>
  );
}
function StatCard({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="hud-panel p-4">
      <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/50">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${tone ?? "text-cyan-300"}`}>{value}</div>
    </div>
  );
}

export default function ChangesPage() {
  return <ChangeDashboard />;
}
function CreateChangeModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [changeType, setChangeType] = useState<ChangeType>("config");
  const [riskLevel, setRiskLevel] = useState("medium");
  const [priority, setPriority] = useState("normal");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!title.trim() || !description.trim()) {
      setErr("Title and description are required.");
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      await createChangeRequest({
        title: title.trim(),
        description: description.trim(),
        change_type: changeType,
        risk_level: riskLevel as ChangeRequest["risk_level"],
        priority: priority as ChangeRequest["priority"],
      });
      await onCreated();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to create change.");
    } finally {
      setSubmitting(false);
    }
  }

  const fieldCls =
    "w-full rounded-lg border border-white/15 bg-white/5 px-3 py-2.5 text-sm text-white outline-none placeholder:text-white/35 focus:border-cyan-400/50";
  const labelCls = "mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.12em] text-white/55";

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Create change request">
      <div className="w-full max-w-md rounded-2xl border border-cyan-400/40 bg-gradient-to-br from-[#06131f] to-[#020e18] p-6 shadow-[0_0_30px_rgba(0,176,255,0.1)]">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-bold uppercase tracking-[0.1em] text-white">New Change Request</h3>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-white/50 hover:bg-white/10 hover:text-white"><X size={18} /></button>
        </div>

        <div className="space-y-4">
          <div>
            <label className={labelCls}>Title</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} className={fieldCls} placeholder="e.g. Firewall firmware upgrade" />
          </div>
          <div>
            <label className={labelCls}>Description</label>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} className={fieldCls} placeholder="Describe the change…" />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className={labelCls}>Type</label>
              <select value={changeType} onChange={(e) => setChangeType(e.target.value as ChangeType)} className={fieldCls}>
                {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className={labelCls}>Risk</label>
              <select value={riskLevel} onChange={(e) => setRiskLevel(e.target.value)} className={fieldCls}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Priority</label>
              <select value={priority} onChange={(e) => setPriority(e.target.value)} className={fieldCls}>
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
                <option value="emergency">Emergency</option>
              </select>
            </div>
          </div>
        </div>

        {err && <div className="mt-3 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{err}</div>}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg border border-white/15 px-4 py-2 text-xs font-semibold uppercase text-white/60 hover:bg-white/5">Cancel</button>
          <button
            onClick={() => void submit()}
            disabled={submitting}
            className="flex items-center gap-2 rounded-lg border border-cyan-400/50 bg-gradient-to-br from-[#17d9ed] to-[#078df5] px-4 py-2 text-xs font-bold uppercase text-[#02080f] hover:brightness-110 disabled:opacity-50"
          >
            {submitting ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
            {submitting ? "Creating…" : "Create Change"}
          </button>
        </div>
      </div>
    </div>
  );
}