"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlarmClock, CheckCircle2, Clock, Hourglass, Inbox, PlayCircle, RefreshCw, ShieldAlert, Ticket as TicketIcon } from "lucide-react";
import {
  getTicketDashboardStats, listTickets, resolveTicket,
  type Ticket, type TicketDashboardStats, type TicketPriority, type TicketStatus,
} from "@/lib/api";
import { formatMinutes } from "@/lib/commandCenter/format";
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
  type Tone,
} from "@/components/ui/PageKit";

const LIST_LIMIT = 100;

const PRIORITY_TONE: Record<string, Tone> = { P1: "bad", P2: "warn", P3: "info", P4: "neutral" };
const PRIORITY_LABEL: Record<string, string> = { P1: "P1 · Critical", P2: "P2 · High", P3: "P3 · Normal", P4: "P4 · Low" };

type StatusTab = "active" | "all" | TicketStatus;
const ACTIVE_STATUSES: TicketStatus[] = ["open", "assigned", "in_progress", "pending_approval", "reopened"];

function slaCountdown(t: Ticket): { text: string; tone: Tone } {
  if (!t.sla_due_at) return { text: "No SLA", tone: "neutral" };
  if (t.resolved_at || t.status === "closed") return { text: t.sla_breached ? "Breached" : "Met", tone: t.sla_breached ? "bad" : "good" };
  const ms = new Date(t.sla_due_at).getTime() - Date.now();
  if (t.sla_breached || ms <= 0) return { text: "Breached", tone: "bad" };
  const mins = Math.round(ms / 60000);
  return { text: `${formatMinutes(mins)} left`, tone: mins < 60 ? "warn" : "good" };
}

/** Mean resolution of the resolved tickets on this page; null when none. */
function avgResolutionMinutes(tickets: Ticket[]): number | null {
  const done = tickets.filter((t) => t.resolved_at && t.opened_at);
  if (done.length === 0) return null;
  const total = done.reduce((sum, t) => sum + (new Date(t.resolved_at!).getTime() - new Date(t.opened_at).getTime()) / 60000, 0);
  return total / done.length;
}

export default function TicketsPage() {
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [stats, setStats] = useState<TicketDashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusTab, setStatusTab] = useState<StatusTab>("active");
  const [priority, setPriority] = useState<NonNullable<TicketPriority> | "all">("all");
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    const [list, st] = await Promise.allSettled([listTickets({ limit: LIST_LIMIT }), getTicketDashboardStats()]);
    if (list.status === "fulfilled") { setTickets(list.value.items); setError(null); }
    else setError("Tickets could not be loaded — check that the backend API is reachable.");
    if (st.status === "fulfilled") setStats(st.value);
    setUpdatedAt(new Date().toISOString());
    setRefreshing(false);
  }, []);

  useEffect(() => {
    const first = window.setTimeout(load, 0);
    const timer = window.setInterval(load, 30000);
    return () => { window.clearTimeout(first); window.clearInterval(timer); };
  }, [load]);

  async function handleResolve(t: Ticket) {
    setActionMsg(null);
    setBusy(t.id);
    try {
      await resolveTicket(t.id, "Resolved from ticket dashboard");
      await load();
      setActionMsg({ tone: "good", text: `${t.ticket_number} resolved.` });
    } catch {
      setActionMsg({ tone: "bad", text: `Could not resolve ${t.ticket_number}.` });
    } finally {
      setBusy(null);
    }
  }

  const list = useMemo(() => tickets ?? [], [tickets]);
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return list.filter((t) => {
      if (statusTab === "active" && !ACTIVE_STATUSES.includes(t.status)) return false;
      if (statusTab !== "active" && statusTab !== "all" && t.status !== statusTab) return false;
      if (priority !== "all" && t.priority !== priority) return false;
      if (!q) return true;
      return [t.ticket_number, t.title, t.description, t.incident_id, t.assigned_team, t.assigned_to].some((v) => (v ?? "").toLowerCase().includes(q));
    });
  }, [list, search, statusTab, priority]);

  const count = (st: TicketStatus) => list.filter((t) => t.status === st).length;
  const pending = stats ? stats.pending_approval_count + stats.pending_customer_count + stats.pending_vendor_count : null;
  const avgRes = tickets ? avgResolutionMinutes(list) : null;
  const statsLoading = !stats && !error;
  const v = (x: number | null | undefined) => (x === null || x === undefined ? "—" : x.toLocaleString("en-US"));

  const columns: Column<Ticket>[] = [
    {
      key: "ticket",
      header: "Ticket",
      sortValue: (t) => t.ticket_number,
      render: (t) => (
        <>
          <span className="pk-cell-main" style={{ whiteSpace: "normal" }}>{t.title}</span>
          <span className="pk-cell-sub">
            <span className="pk-mono">{t.ticket_number}</span>
            {t.category ? ` · ${titleCaseWords(t.category)}` : ""}
            {t.escalation_level > 0 ? ` · escalated L${t.escalation_level}` : ""}
          </span>
        </>
      ),
    },
    {
      key: "priority",
      header: "Priority",
      sortValue: (t) => t.priority ?? "P9",
      render: (t) => (t.priority ? <Pill tone={PRIORITY_TONE[t.priority] ?? "neutral"}>{PRIORITY_LABEL[t.priority] ?? t.priority}</Pill> : <span className="pk-muted">Not set</span>),
    },
    { key: "status", header: "Status", sortValue: (t) => t.status, render: (t) => <Pill tone={t.status === "assigned" || t.status === "in_progress" ? "info" : statusTone(t.status)}>{titleCaseWords(t.status)}</Pill> },
    {
      key: "assigned",
      header: "Assigned",
      sortValue: (t) => t.assigned_team ?? t.assigned_to ?? "",
      render: (t) => (t.assigned_team || t.assigned_to ? titleCaseWords(t.assigned_team ?? t.assigned_to) : <span className="pk-muted">Unassigned</span>),
    },
    {
      key: "sla",
      header: "SLA",
      sortValue: (t) => (t.sla_due_at ? new Date(t.sla_due_at).getTime() : null),
      render: (t) => {
        const sla = slaCountdown(t);
        return <Pill tone={sla.tone} dot={false}>{sla.text}</Pill>;
      },
    },
    {
      key: "incident",
      header: "Incident",
      render: (t) => (t.incident_id ? <Link className="ecc-link" href={`/incidents/${t.incident_id}`} onClick={(e) => e.stopPropagation()}>Open</Link> : <span className="pk-muted">—</span>),
    },
    { key: "opened", header: "Opened", sortValue: (t) => new Date(t.opened_at).getTime(), render: (t) => <span className="pk-muted">{ago(t.opened_at)}</span> },
    {
      key: "action",
      header: "",
      render: (t) =>
        ACTIVE_STATUSES.includes(t.status) ? (
          <button type="button" className="pk-button" style={{ height: 26, fontSize: 12 }} disabled={busy !== null} onClick={() => handleResolve(t)}>
            {busy === t.id ? "Resolving…" : "Resolve"}
          </button>
        ) : null,
    },
  ];

  const tabs: { key: StatusTab; label: string; count?: number }[] = [
    { key: "active", label: "Active", count: list.filter((t) => ACTIVE_STATUSES.includes(t.status)).length },
    { key: "open", label: "Open", count: count("open") },
    { key: "in_progress", label: "In progress", count: count("in_progress") },
    { key: "pending_approval", label: "Pending approval", count: count("pending_approval") },
    { key: "resolved", label: "Resolved", count: count("resolved") },
    { key: "all", label: "All", count: list.length },
  ];

  return (
    <PageShell
      title="Tickets"
      icon={TicketIcon}
      subtitle="Service tickets raised from incidents and requests, with live SLA tracking."
      meta={updatedAt ? `Updated ${ago(updatedAt)} · refreshes every 30 s` : undefined}
      actions={
        <button type="button" className="pk-button" onClick={load} disabled={refreshing}>
          <RefreshCw size={14} /> {refreshing ? "Refreshing" : "Refresh"}
        </button>
      }
    >
      {actionMsg && <Banner tone={actionMsg.tone}>{actionMsg.text}</Banner>}

      <StatStrip>
        <StatTile label="Open" value={v(stats?.open_count)} icon={Inbox} alarm={!!stats?.open_count} info="Tickets not yet picked up." loading={statsLoading} />
        <StatTile label="In progress" value={v(stats?.in_progress_count)} icon={PlayCircle} info="Tickets being worked." loading={statsLoading} />
        <StatTile label="Pending" value={v(pending)} icon={Hourglass} tone="warn" info="Waiting on approval, the customer or a vendor." loading={statsLoading} />
        <StatTile label="Resolved today" value={v(stats?.resolved_today_count)} icon={CheckCircle2} tone="good" info="Tickets resolved since midnight." loading={statsLoading} />
        <StatTile label="Overdue SLA" value={v(stats?.overdue_count)} icon={AlarmClock} alarm={!!stats?.overdue_count} tone="warn" info="Active tickets past their SLA due time." loading={statsLoading} />
        <StatTile label="SLA breached" value={v(stats?.sla_breach_count)} icon={ShieldAlert} alarm={!!stats?.sla_breach_count} info="Tickets that breached their SLA." loading={statsLoading} />
        <StatTile
          label="Avg resolution"
          value={formatMinutes(avgRes)}
          icon={Clock}
          info={`Mean time to resolve, across resolved tickets among the ${LIST_LIMIT} most recent. Not measured until a ticket is resolved.`}
          loading={!tickets && !error}
        />
      </StatStrip>

      <Panel
        title="Ticket queue"
        info={`The ${LIST_LIMIT} most recent tickets. Filters apply to this list.`}
        aside={tickets ? `${visible.length} of ${list.length}` : undefined}
        state={tickets ? "ready" : error ? "error" : "loading"}
        errorText={error ?? undefined}
        onRetry={load}
        skeletonHeight={360}
      >
        <div className="pk-toolbar" style={{ marginBottom: 10 }}>
          <SearchBox value={search} onChange={setSearch} placeholder="Search number, title, team, incident" label="Search tickets" />
          <select className="pk-input" aria-label="Priority" value={priority} onChange={(e) => setPriority(e.target.value as NonNullable<TicketPriority> | "all")}>
            <option value="all">All priorities</option>
            {(["P1", "P2", "P3", "P4"] as const).map((p) => <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>)}
          </select>
          <span className="pk-toolbar__spacer" />
          <Tabs label="Status" value={statusTab} onChange={setStatusTab} tabs={tabs} />
        </div>
        <DataTable
          caption="Tickets"
          columns={columns}
          rows={visible}
          rowKey={(t) => t.id}
          defaultSort={{ key: "opened", dir: "desc" }}
          emptyText={list.length === 0 ? "No tickets yet. Tickets are raised from incidents or created by integrations." : "No tickets match the current filters."}
        />
      </Panel>
    </PageShell>
  );
}
