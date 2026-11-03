"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { Banner, DataTable, Panel, Pill, ago, statusTone, titleCaseWords, type Column } from "@/components/ui/PageKit";
import { useAuth } from "@/lib/auth-context";
import {
  createDiscoveryScope,
  deleteDiscoveryScope,
  listDiscoveryScopes,
  probeDiscoveryScope,
  updateDiscoveryScope,
  type DiscoveryScope,
  type DiscoveryScopeInput,
} from "@/lib/infrastructureIntelligence";

/**
 * Discovery scopes — where discovery may run (a range, address or seed
 * device), with which protocols, what to leave out and how often. A scheduled
 * scope is probed by the backend scheduler; its first run is one interval
 * after the schedule is set. Every probe only records what answered: hosts
 * become devices when someone approves them under "Probe jobs" below.
 */

/** Scope types the backend network probe can run (others are listed but not probed). */
const SCOPE_TYPES = [
  { key: "private_cidr", label: "Private range (CIDR)" },
  { key: "public_cidr", label: "Public range (CIDR)" },
  { key: "public_ip", label: "Public address" },
  { key: "seed_device", label: "Seed device" },
];
const PROTOCOLS = ["icmp", "snmp", "ssh", "https", "http", "wmi", "winrm"];
const SCHEDULES = [
  { value: "", label: "Off (run on demand)" },
  { value: "15m", label: "Every 15 minutes" },
  { value: "1h", label: "Every hour" },
  { value: "6h", label: "Every 6 hours" },
  { value: "12h", label: "Every 12 hours" },
  { value: "1d", label: "Every day" },
  { value: "7d", label: "Every week" },
];

export function scheduleLabel(schedule: string | null): string {
  if (!schedule) return "On demand";
  const known = SCHEDULES.find((s) => s.value === schedule);
  if (known) return known.label;
  const m = /^(\d+)([mhd])$/.exec(schedule);
  return m ? `Every ${m[1]} ${{ m: "min", h: "h", d: "d" }[m[2] as "m" | "h" | "d"]}` : schedule;
}

function until(iso: string | null): string {
  if (!iso) return "—";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 60_000) return "due now";
  const min = Math.round(ms / 60_000);
  if (min < 60) return `in ${min} min`;
  const h = Math.round(min / 60);
  return h < 48 ? `in ${h} h` : `in ${Math.round(h / 24)} d`;
}

function splitList(v: string): string[] {
  return v.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
}

function ScopeForm({ scope, busy, onSave, onCancel }: {
  scope: DiscoveryScope | null;
  busy: boolean;
  onSave: (body: DiscoveryScopeInput) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(scope?.name ?? "");
  const [scopeType, setScopeType] = useState(scope?.scope_type ?? "private_cidr");
  const [target, setTarget] = useState(scope?.target ?? "");
  const [protocols, setProtocols] = useState<string[]>(scope?.allowed_protocols ?? ["icmp"]);
  const [excluded, setExcluded] = useState((scope?.excluded_targets ?? []).join(", "));
  const [maxHosts, setMaxHosts] = useState(String(scope?.max_hosts ?? 256));
  const [schedule, setSchedule] = useState(scope?.schedule ?? "");
  const scheduleOptions = schedule && !SCHEDULES.some((s) => s.value === schedule)
    ? [...SCHEDULES, { value: schedule, label: scheduleLabel(schedule) }]
    : SCHEDULES;
  const hosts = Number(maxHosts);
  const valid = name.trim() !== "" && target.trim() !== "" && Number.isInteger(hosts) && hosts >= 1 && hosts <= 65536;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    onSave({
      name: name.trim(),
      scope_type: scopeType,
      target: target.trim(),
      allowed_protocols: protocols,
      excluded_targets: splitList(excluded),
      max_hosts: hosts,
      schedule: schedule || null,
    });
  };

  return (
    <form onSubmit={submit} style={{ marginBottom: 12 }}>
      <div className="pk-form" style={{ maxWidth: "none", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        <div className="pk-field">
          <label htmlFor="scope-name">Name</label>
          <input id="scope-name" className="pk-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={255} />
        </div>
        <div className="pk-field">
          <label htmlFor="scope-type">Type</label>
          <select id="scope-type" className="pk-input" value={scopeType} onChange={(e) => setScopeType(e.target.value)}>
            {SCOPE_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
            {!SCOPE_TYPES.some((t) => t.key === scopeType) && <option value={scopeType}>{titleCaseWords(scopeType)}</option>}
          </select>
        </div>
        <div className="pk-field">
          <label htmlFor="scope-target">Target</label>
          <input id="scope-target" className="pk-input" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="10.0.0.0/24" autoComplete="off" />
        </div>
        <div className="pk-field">
          <label htmlFor="scope-schedule">Schedule</label>
          <select id="scope-schedule" className="pk-input" value={schedule} onChange={(e) => setSchedule(e.target.value)}>
            {scheduleOptions.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <small>The first scheduled run is one interval after you save.</small>
        </div>
        <div className="pk-field">
          <label htmlFor="scope-exclude">Exclude</label>
          <input id="scope-exclude" className="pk-input" value={excluded} onChange={(e) => setExcluded(e.target.value)} placeholder="10.0.0.1, 10.0.0.128/25" autoComplete="off" />
          <small>Addresses or ranges never probed.</small>
        </div>
        <div className="pk-field">
          <label htmlFor="scope-max">Host limit</label>
          <input id="scope-max" className="pk-input" type="number" min={1} max={65536} value={maxHosts} onChange={(e) => setMaxHosts(e.target.value)} />
        </div>
      </div>
      <div className="pk-toolbar" style={{ marginTop: 12 }}>
        <span className="pk-count">Protocols</span>
        {PROTOCOLS.map((p) => (
          <label key={p} className="pk-switch">
            <input
              type="checkbox"
              checked={protocols.includes(p)}
              onChange={(e) => setProtocols((cur) => (e.target.checked ? [...cur, p] : cur.filter((x) => x !== p)))}
            />
            {p.toUpperCase()}
          </label>
        ))}
        <span className="pk-toolbar__spacer" />
        <button type="button" className="pk-button" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="pk-button pk-button--primary" disabled={!valid || busy}>
          {scope ? "Save scope" : "Create scope"}
        </button>
      </div>
    </form>
  );
}

export default function DiscoveryScopes() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const canEdit = !!user && (user.is_superuser || user.permissions.includes("devices.write"));
  const [editing, setEditing] = useState<DiscoveryScope | "new" | null>(null);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  const scopes = useQuery({ queryKey: ["discovery-scopes"], queryFn: listDiscoveryScopes, refetchInterval: 60_000 });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["discovery-scopes"] });
    qc.invalidateQueries({ queryKey: ["discovery-probe-jobs"] });
  };
  const fail = (err: unknown, what: string) =>
    setMessage({ tone: "bad", text: `${what}: ${err instanceof Error ? err.message : "request failed"}` });

  const save = useMutation({
    mutationFn: (body: DiscoveryScopeInput) =>
      editing && editing !== "new" ? updateDiscoveryScope(editing.id, body) : createDiscoveryScope(body),
    onSuccess: (s) => {
      setMessage({ tone: "good", text: `Scope ${s.name} saved.${s.next_run_at ? ` Next scheduled run ${until(s.next_run_at)}.` : ""}` });
      setEditing(null);
      refresh();
    },
    onError: (e) => fail(e, "The scope could not be saved"),
  });
  const toggle = useMutation({
    mutationFn: (s: DiscoveryScope) => updateDiscoveryScope(s.id, { enabled: !s.enabled }),
    onSuccess: (s) => { setMessage({ tone: "good", text: `Scope ${s.name} ${s.enabled ? "enabled" : "disabled"}.` }); refresh(); },
    onError: (e) => fail(e, "The scope could not be changed"),
  });
  const probe = useMutation({
    mutationFn: (s: DiscoveryScope) => probeDiscoveryScope(s.id),
    onSuccess: (r) => {
      setMessage({ tone: "good", text: `Probed ${r.hosts_probed} of ${r.hosts_total} addresses: ${r.reachable} answered${r.truncated ? " (host limit reached)" : ""}. Review them under Probe jobs.` });
      refresh();
    },
    onError: (e) => { fail(e, "The probe failed"); refresh(); },
  });
  const remove = useMutation({
    mutationFn: (s: DiscoveryScope) => deleteDiscoveryScope(s.id),
    onSuccess: () => { setMessage({ tone: "good", text: "Scope deleted." }); refresh(); },
    onError: (e) => fail(e, "The scope could not be deleted"),
  });
  const busy = save.isPending || toggle.isPending || probe.isPending || remove.isPending;

  const columns: Column<DiscoveryScope>[] = [
    { key: "name", header: "Scope", sortValue: (s) => s.name.toLowerCase(), render: (s) => <strong>{s.name}</strong> },
    { key: "target", header: "Target", sortValue: (s) => s.target, render: (s) => (
      <span>
        <span className="pk-mono">{s.target}</span>
        {s.excluded_targets.length > 0 && <span className="pk-muted" title={s.excluded_targets.join(", ")}> · {s.excluded_targets.length} excluded</span>}
      </span>
    ) },
    { key: "protocols", header: "Protocols", render: (s) => <span className="pk-muted">{s.allowed_protocols.length ? s.allowed_protocols.join(", ").toUpperCase() : "ICMP"}</span> },
    { key: "schedule", header: "Schedule", sortValue: (s) => s.schedule ?? "", render: (s) => (
      s.schedule ? <span><CalendarClock size={12} aria-hidden /> {scheduleLabel(s.schedule)}</span> : <span className="pk-muted">On demand</span>
    ) },
    { key: "last", header: "Last run", sortValue: (s) => (s.last_run_at ? new Date(s.last_run_at).getTime() : 0), render: (s) => (
      s.last_run_at ? (
        <span title={s.last_error ?? new Date(s.last_run_at).toLocaleString()}>
          <Pill tone={s.status === "error" ? "bad" : statusTone(s.status)}>{titleCaseWords(s.status)}</Pill> {ago(s.last_run_at)}
        </span>
      ) : <span className="pk-muted">Never</span>
    ) },
    { key: "next", header: "Next run", sortValue: (s) => (s.enabled && s.next_run_at ? new Date(s.next_run_at).getTime() : Number.MAX_SAFE_INTEGER), render: (s) => (
      !s.enabled ? <Pill tone="neutral">Disabled</Pill>
        : s.next_run_at ? <span title={new Date(s.next_run_at).toLocaleString()}>{until(s.next_run_at)}</span>
        : <span className="pk-muted">—</span>
    ) },
    ...(canEdit ? [{ key: "actions", header: "", render: (s: DiscoveryScope) => (
      <span style={{ display: "inline-flex", gap: 4 }}>
        <button type="button" className="pk-button" disabled={busy || !s.enabled} onClick={() => probe.mutate(s)} title={s.enabled ? "Probe now" : "Enable the scope to probe it"} aria-label={`Probe ${s.name} now`}>
          <Play size={12} /> {probe.isPending && probe.variables?.id === s.id ? "Probing…" : "Run now"}
        </button>
        <button type="button" className="pk-button" disabled={busy} onClick={() => toggle.mutate(s)}>{s.enabled ? "Disable" : "Enable"}</button>
        <button type="button" className="pk-button" disabled={busy} onClick={() => { setMessage(null); setEditing(s); }} aria-label={`Edit ${s.name}`}><Pencil size={12} /></button>
        <button type="button" className="pk-button" disabled={busy} aria-label={`Delete ${s.name}`}
          onClick={() => { if (window.confirm(`Delete scope ${s.name}? Its probe history is kept.`)) remove.mutate(s); }}>
          <Trash2 size={12} />
        </button>
      </span>
    ) }] : []),
  ];

  const list = scopes.data ?? [];
  const scheduled = list.filter((s) => s.enabled && s.schedule).length;

  return (
    <Panel
      title="Discovery scopes"
      info="Where discovery may run and how often. Scheduled probes run from the backend; what they find waits for review under Probe jobs before anything is added to inventory."
      aside={
        <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
          {scopes.data && <span className="pk-muted">{list.length} scopes · {scheduled} scheduled</span>}
          {canEdit && editing === null && (
            <button type="button" className="pk-button pk-button--primary" onClick={() => { setMessage(null); setEditing("new"); }}>
              <Plus size={12} /> New scope
            </button>
          )}
        </span>
      }
      state={scopes.data ? "ready" : scopes.isError ? "error" : "loading"}
      onRetry={() => scopes.refetch()}
    >
      {message && <div style={{ marginBottom: 12 }}><Banner tone={message.tone}>{message.text}</Banner></div>}
      {editing !== null && (
        <ScopeForm
          key={editing === "new" ? "new" : editing.id}
          scope={editing === "new" ? null : editing}
          busy={save.isPending}
          onSave={(b) => save.mutate(b)}
          onCancel={() => setEditing(null)}
        />
      )}
      <DataTable
        caption="Discovery scopes"
        rows={list}
        rowKey={(s) => s.id}
        columns={columns}
        defaultSort={{ key: "name", dir: "asc" }}
        emptyText={canEdit ? "No scopes yet. Create one to probe a range on demand or on a schedule." : "No discovery scopes have been set up."}
      />
    </Panel>
  );
}
