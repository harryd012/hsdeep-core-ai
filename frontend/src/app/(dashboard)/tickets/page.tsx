"use client";

import {
  Eye, Loader2, AlertTriangle, RefreshCw, Search, CheckCircle2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  getTicketDashboardStats, listTickets, resolveTicket,
  type Ticket, type TicketDashboardStats, type TicketPriority, type TicketStatus,
} from "@/lib/api";

const STATUSES: Array<{ value: TicketStatus | "all"; label: string }> = [
  { value: "all", label: "All Statuses" },
  { value: "open", label: "Open" },
  { value: "assigned", label: "Assigned" },
  { value: "in_progress", label: "In Progress" },
  { value: "pending_approval", label: "Pending Approval" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
  { value: "reopened", label: "Reopened" },
];

const PRIORITIES: Array<{ value: TicketPriority | "all"; label: string }> = [
  { value: "all", label: "All Priorities" },
  { value: "P1", label: "P1 — Critical" },
  { value: "P2", label: "P2 — High" },
  { value: "P3", label: "P3 — Normal" },
  { value: "P4", label: "P4 — Low" },
];

const PRIORITY_PILL: Record<string, string> = {
  P1: "bg-red-100 text-red-700 border-red-300",
  P2: "bg-amber-100 text-amber-700 border-amber-300",
  P3: "bg-blue-100 text-blue-700 border-blue-300",
  P4: "bg-gray-100 text-gray-600 border-gray-300",
};

const STATUS_PILL: Record<string, string> = {
  open: "bg-red-50 text-red-600 border-red-200",
  assigned: "bg-blue-50 text-blue-700 border-blue-200",
  in_progress: "bg-cyan-50 text-cyan-700 border-cyan-200",
  pending_approval: "bg-amber-50 text-amber-700 border-amber-200",
  resolved: "bg-emerald-50 text-emerald-700 border-emerald-200",
  closed: "bg-gray-100 text-gray-500 border-gray-200",
  cancelled: "bg-gray-100 text-gray-400 border-gray-200",
  reopened: "bg-purple-50 text-purple-700 border-purple-200",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function slaCountdown(t: Ticket): { text: string; breached: boolean } {
  if (!t.sla_due_at) return { text: "—", breached: false };
  const ms = new Date(t.sla_due_at).getTime() - Date.now();
  if (t.sla_breached || ms <= 0) return { text: "BREACHED", breached: true };
  const mins = Math.round(ms / 60000);
  const h = Math.floor(mins / 60);
  return { text: h > 0 ? `${h}h ${mins % 60}m` : `${mins}m`, breached: false };
}

function avgResolutionHours(tickets: Ticket[]): string {
  const done = tickets.filter((t) => t.resolved_at && t.opened_at);
  if (done.length === 0) return "—";
  const total = done.reduce(
    (sum, t) => sum + (new Date(t.resolved_at!).getTime() - new Date(t.opened_at).getTime()) / 3_600_000,
    0,
  );
  return `${(total / done.length).toFixed(1)}h`;
}

export default function TicketsPage() {
  return <TicketDashboard />;
}

function TicketDashboard() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [stats, setStats] = useState<TicketDashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<TicketStatus | "all">("all");
  const [priorityFilter, setPriorityFilter] = useState<TicketPriority | "all">("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expandedId, setExpandedId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [listRes, statRes] = await Promise.all([
        listTickets({ limit: 100 }),
        getTicketDashboardStats().catch(() => null),
      ]);
      setTickets(listRes.items);
      setStats(statRes);
    } catch {
      setError("Failed to load tickets — check that the backend API is reachable.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tickets.filter((t) => {
      if (statusFilter !== "all" && t.status !== statusFilter) return false;
      if (priorityFilter !== "all" && t.priority !== priorityFilter) return false;
      if (!q) return true;
      return (
        t.ticket_number.toLowerCase().includes(q) ||
        t.title.toLowerCase().includes(q) ||
        (t.description ?? "").toLowerCase().includes(q) ||
        (t.incident_id ?? "").toLowerCase().includes(q)
      );
    });
  }, [tickets, search, statusFilter, priorityFilter]);

  async function handleResolve(t: Ticket) {
    setActionMsg(null);
    try {
      await resolveTicket(t.id, "Resolved from ticket dashboard");
      await load();
      setActionMsg(`${t.ticket_number} resolved.`);
    } catch {
      setActionMsg(`Could not resolve ${t.ticket_number}.`);
    }
  }

  function toggleAll() {
    setSelected(selected.size === visible.length && visible.length > 0 ? new Set() : new Set(visible.map((t) => t.id)));
  }

  function toggleOne(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelected(next);
  }

  const pendingCount = stats ? stats.pending_approval_count + stats.pending_customer_count + stats.pending_vendor_count : 0;
  const avgRes = avgResolutionHours(tickets);

  const statCards = [
    { label: "OPEN TICKETS", value: stats?.open_count, color: "#ef4444" },
    { label: "IN PROGRESS", value: stats?.in_progress_count, color: "#22d3ee" },
    { label: "PENDING", value: stats ? pendingCount : undefined, color: "#f59e0b" },
    { label: "RESOLVED TODAY", value: stats?.resolved_today_count, color: "#10b981" },
    { label: "CLOSED TODAY", value: stats?.closed_today_count, color: "#64748b" },
    { label: "OVERDUE SLA", value: stats?.overdue_count, color: "#f97316" },
    { label: "SLA BREACHED", value: stats?.sla_breach_count, color: "#dc2626" },
    { label: "AVG RESOLUTION", value: avgRes === "—" ? undefined : avgRes, color: "#3aa9ff" },
  ];

  if (loading) {
    return (
      <div className="page-shell flex items-center justify-center min-h-screen bg-gray-50">
        <div className="flex items-center gap-3 text-gray-500">
          <Loader2 size={22} className="animate-spin" /> Loading tickets…
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="page-shell min-h-screen bg-gray-50 px-6 py-12">
        <div className="mx-auto max-w-md rounded-lg border border-red-300 bg-red-50 p-6 text-center">
          <AlertTriangle size={28} className="mx-auto mb-2 text-red-500" />
          <p className="text-sm font-semibold text-red-700">{error}</p>
          <button onClick={load} className="mt-4 inline-flex items-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700">
            <RefreshCw size={15} /> Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page-shell min-h-screen bg-gray-50" style={{ paddingBottom: "var(--copilot-bar-height)" }}>
      {/* Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 flex items-center justify-between gap-4">
          <h1 className="text-2xl font-bold tracking-wide text-gray-900">TICKET DASHBOARD</h1>
          <button onClick={load} className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:border-cyan-500 hover:text-cyan-700">
            <RefreshCw size={15} /> Refresh
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-5 py-6">
        {/* Stat cards */}
        <div className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(160px,1fr))]">
          {statCards.map((c) => (
            <div key={c.label} className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-xl font-bold" style={{ color: c.color }}>{c.value ?? "—"}</p>
              <p className="mt-1 text-[11px] font-semibold tracking-wide text-gray-500">{c.label}</p>
            </div>
          ))}
        </div>

        {actionMsg && (
          <div className="rounded-md border border-cyan-300 bg-cyan-50 px-4 py-2 text-sm text-cyan-800">{actionMsg}</div>
        )}

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[240px] max-w-md flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search tickets…"
              className="w-full rounded-md border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm text-gray-900 placeholder:text-gray-400 focus:border-cyan-500 focus:outline-none"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as TicketStatus | "all")}
            className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-cyan-500 focus:outline-none"
          >
            {STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <select
            value={priorityFilter ?? "all"}
            onChange={(e) => setPriorityFilter(e.target.value as TicketPriority | "all")}
            className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-cyan-500 focus:outline-none"
          >
            {PRIORITIES.map((p) => <option key={p.value ?? "all"} value={p.value ?? "all"}>{p.label}</option>)}
          </select>
        </div>

        {/* Table */}
        {visible.length === 0 ? (
          <div className="rounded-lg border border-gray-200 bg-white p-10 text-center text-sm text-gray-500">
            No tickets match your search or filters.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500">
                  <th className="px-3 py-2.5">
                    <input type="checkbox" checked={selected.size === visible.length && visible.length > 0} onChange={toggleAll} className="accent-cyan-600" />
                  </th>
                  <th className="px-3 py-2.5">Ticket</th>
                  <th className="px-3 py-2.5">Priority</th>
                  <th className="px-3 py-2.5">Status</th>
                  <th className="px-3 py-2.5">Incident</th>
                  <th className="px-3 py-2.5">Assigned Group</th>
                  <th className="px-3 py-2.5">Created</th>
                  <th className="px-3 py-2.5">SLA Countdown</th>
                  <th className="px-3 py-2.5">Actions</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t) => {
                  const sla = slaCountdown(t);
                  const expanded = expandedId === t.id;
                  const resolvable = ["open", "assigned", "in_progress", "reopened"].includes(t.status);
                  return (
                    <tr key={t.id} className={`border-b border-gray-100 hover:bg-cyan-50/40 ${selected.has(t.id) ? "bg-cyan-50/60" : ""}`}>
                      <td className="px-3 py-2.5">
                        <input type="checkbox" checked={selected.has(t.id)} onChange={() => toggleOne(t.id)} className="accent-cyan-600" />
                      </td>
                      <td className="px-3 py-2.5">
                        <p className="font-semibold text-cyan-700">{t.ticket_number}</p>
                        <p className="max-w-[280px] truncate text-xs text-gray-600">{t.title}</p>
                        {expanded && t.description && (
                          <p className="mt-1 max-w-[320px] rounded bg-gray-50 p-2 text-xs text-gray-600">{t.description}</p>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        {t.priority ? (
                          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-bold ${PRIORITY_PILL[t.priority]}`}>{t.priority}</span>
                        ) : <span className="text-gray-400">—</span>}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase ${STATUS_PILL[t.status] ?? ""}`}>{t.status.replace("_", " ")}</span>
                      </td>
                      <td className="px-3 py-2.5 font-mono text-xs text-gray-500">{t.incident_id ? t.incident_id.slice(0, 8) : "—"}</td>
                      <td className="px-3 py-2.5 text-xs text-gray-600">{t.assignment_group ?? t.assigned_team ?? t.assigned_to ?? "—"}</td>
                      <td className="px-3 py-2.5 text-xs text-gray-500">{timeAgo(t.opened_at)}</td>
                      <td className={`px-3 py-2.5 text-xs font-semibold ${sla.breached ? "text-red-600" : "text-gray-600"}`}>{sla.text}</td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-1.5">
                          <button onClick={() => setExpandedId(expanded ? null : t.id)} title="Toggle details" className="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-cyan-700">
                            <Eye size={15} />
                          </button>
                          {resolvable && (
                            <button onClick={() => handleResolve(t)} title="Resolve ticket" className="rounded p-1 text-emerald-600 hover:bg-emerald-50">
                              <CheckCircle2 size={15} />
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
        )}
      </div>
    </div>
  );
}
