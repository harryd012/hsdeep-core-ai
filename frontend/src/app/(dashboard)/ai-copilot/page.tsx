"use client";

import {
  CCAgent,
  CCActivityEvent,
  CCMetrics,
  CCWorkload,
  CCTask,
  CommandCenterHealth,
  TOOL_GATEWAY_ADAPTERS,
} from "@/lib/api";
import { useCommandCenterOverview } from "@/hooks/useAiQueries";
import CommandCenterTopology from "@/components/dashboard/CommandCenterTopology";

function statusColor(status: string): string {
  switch (status) {
    case "active": case "available": case "verified": case "recorded":
      return "var(--cyan)";
    case "investigating": case "executing": case "assigned": case "accepted":
      return "var(--amber, #f5a623)";
    case "failed": case "escalated": case "cancelled":
      return "var(--red, #ff5c5c)";
    case "busy": case "retrying": case "reassigned":
      return "var(--amber, #f5a623)";
    default:
      return "var(--muted)";
  }
}

const panelStyle: React.CSSProperties = {
  border: "1px solid var(--line)",
  background: "rgba(1,10,17,0.55)",
  padding: 12,
};

const btnStyle: React.CSSProperties = {
  border: "1px solid var(--line)",
  background: "rgba(1,10,17,0.8)",
  color: "var(--cyan)",
  padding: "8px 16px",
  cursor: "pointer",
  marginTop: 12,
};

export default function AiCopilotPage() {
  const {
    data: overview,
    isLoading,
    isError,
    error,
    refetch,
  } = useCommandCenterOverview();

  if (isLoading && overview === undefined) {
    return (
      <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
        <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
          <section className="hud-panel">
            <h2>AI OPERATIONS COMMAND CENTER</h2>
            <p style={{ color: "var(--muted)" }}>Loading AI operations…</p>
          </section>
        </div>
      </div>
    );
  }

  if (isError && overview === undefined) {
    return (
      <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
        <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>
          <section className="hud-panel">
            <h2>AI OPERATIONS COMMAND CENTER</h2>
            <p style={{ color: "var(--red, #ff5c5c)" }}>Command Center backend unavailable.</p>
            <p style={{ color: "var(--muted)", fontSize: 13 }}>{error?.message ?? String(error)}</p>
            <button onClick={() => refetch()} style={btnStyle}>Retry</button>
          </section>
        </div>
      </div>
    );
  }

  const o = overview!;
  const m: CCMetrics = o.metrics;

  return (
    <div className="dashboard" style={{ paddingTop: "clamp(84px,8.5vh,104px)" }}>
      <div className="overview" style={{ gridTemplateColumns: "1fr", gap: 16 }}>

                <CtoStatusBar m={m} systemOnline={o.system_online} health={o.health ?? null} generatedAt={o.generated_at} />

        {/* Shared AI Control Plane topology — real agents/managers/tasks
            (same backend source of truth as /dashboard). */}
        <CommandCenterTopology />

        <ToolGatewayPanel />

        <section className="hud-panel">
          <h2>MANAGERS</h2>
          {o.workload.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>No active AI agents.</p>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: 12 }}>
              {o.workload.map((w) => (
                <DepartmentBlock key={w.department} w={w} agents={o.agents} />
              ))}
            </div>
          )}
        </section>

        <section className="hud-panel">
          <h2>DEPARTMENT WORKLOAD</h2>
          <WorkloadPanel workload={o.workload} />
        </section>

        <section className="hud-panel">
          <h2>ACTIVE TASKS</h2>
          <TasksPanel tasks={o.tasks} />
        </section>

        <section className="hud-panel">
          <h2>LIVE ACTIVITY FEED</h2>
          <ActivityPanel activity={o.activity} />
        </section>
      </div>
    </div>
  );
}

function CtoStatusBar({ m, systemOnline, health, generatedAt }: { m: CCMetrics; systemOnline: boolean; health: CommandCenterHealth | null; generatedAt: string }) {
  const depText = health?.dependencies
    ? Object.entries(health.dependencies).map(([k, v]) => `${k}: ${v}`).join(" · ")
    : "unknown";
  const aiText = health?.ai_ready ? "AI: READY" : health ? "AI: DEGRADED" : "AI: UNKNOWN";

  // AI CTO status: use health?.status as authoritative source (matching
  // CtoNode in CommandCenterTopology.tsx). Don't rely solely on systemOnline
  // which can be true even when the control plane is degraded.
  const ctoHealthStatus = health?.status ?? (systemOnline ? "ok" : "unavailable");
  const ctoIsOnline = ctoHealthStatus !== "unavailable";
  const ctoIsDegraded = ctoHealthStatus === "degraded";
  const aiCtoStatus = ctoIsOnline
    ? ctoIsDegraded
      ? "DEGRADED"
      : "ONLINE"
    : "OFFLINE";
  const aiCtoColor = ctoIsOnline
    ? ctoIsDegraded
      ? "var(--amber, #f5a623)"
      : "var(--green, #2ecc71)"
    : "var(--red, #ff5c5c)";

  const stats = [
    { label: "AI CTO", value: aiCtoStatus, color: aiCtoColor },
    { label: "AGENTS", value: `${m.active_agents}/${m.total_agents}`, color: "#a7bec7" },
    { label: "WORKING", value: m.working_agents, color: "var(--amber, #f5a623)" },
    { label: "TASKS", value: m.total_tasks, color: "#a7bec7" },
    { label: "CRITICAL", value: m.critical, color: m.critical > 0 ? "var(--red, #ff5c5c)" : "#a7bec7",
    },
  ];
  return (
    <section className="hud-panel">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 16 }}>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          {stats.map((s) => (
            <div key={s.label}>
              <div style={{ fontSize: 10, color: "var(--muted)", letterSpacing: 1 }}>{s.label}</div>
              <div style={{ fontSize: 20, color: s.color, fontWeight: 600 }}>{s.value}</div>
            </div>
          ))}
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          <div>{depText}</div>
          <div>{aiText}</div>
          <div>updated {new Date(generatedAt).toLocaleTimeString()}</div>
        </div>
      </div>
    </section>
  );
}

function ToolGatewayPanel() {
  return (
    <section className="hud-panel">
      <h2>TOOL GATEWAY <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>(informational — no live execution tracking yet)</span></h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(160px,1fr))", gap: 8 }}>
        {TOOL_GATEWAY_ADAPTERS.map((a) => (
          <div key={a.id} style={{ ...panelStyle, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "#a7bec7" }}>{a.name}</span>
            <span style={{ fontSize: 10, color: "var(--muted)" }}>registered</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function DepartmentBlock({ w, agents }: { w: CCWorkload; agents: CCAgent[] }) {
  const deptAgents = agents.filter((a) => a.department.toUpperCase() === w.department.toUpperCase());
  const lead = deptAgents.find((a) => a.level === "manager") ?? deptAgents[0];
  return (
    <div style={panelStyle}>
      <div style={{ fontSize: 13, color: "var(--cyan)", fontWeight: 600, marginBottom: 4 }}>
        {w.department.toUpperCase()} {lead ? `· ${lead.name}` : ""}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
        {w.active_agents}/{w.agent_count} agents · {w.active_tasks} active tasks
      </div>
      {deptAgents.length === 0 ? (
        <p style={{ color: "var(--muted)", fontSize: 11 }}>No agents registered.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {deptAgents.map((a) => (
            <div key={a.id} style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
              <span style={{ color: "#a7bec7" }}>{a.name}</span>
              <span style={{ color: statusColor(a.availability) }}>{a.availability}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function WorkloadPanel({ workload }: { workload: CCWorkload[] }) {
  if (workload.length === 0) return <p style={{ color: "var(--muted)" }}>No department workload data.</p>;
  const rows: { label: string; key: keyof CCWorkload }[] = [
    { label: "Agents", key: "agent_count" },
    { label: "Active", key: "active_agents" },
    { label: "Active tasks", key: "active_tasks" },
    { label: "Queued", key: "queued_tasks" },
    { label: "Verifying", key: "verifying_tasks" },
    { label: "Completed", key: "completed_tasks" },
    { label: "Critical", key: "critical_tasks" },
  ];
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ color: "var(--muted)", textAlign: "left" }}>
            <th style={{ padding: "6px 10px" }}>Department</th>
            {rows.map((r) => (
              <th key={r.key as string} style={{ padding: "6px 10px" }}>{r.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {workload.map((w) => (
            <tr key={w.department} style={{ borderTop: "1px solid var(--line)" }}>
              <td style={{ padding: "6px 10px", color: "var(--cyan)" }}>{w.department}</td>
              {rows.map((r) => (
                <td key={r.key as string} style={{ padding: "6px 10px", color: "#a7bec7" }}>
                  {w[r.key] as number}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TasksPanel({ tasks }: { tasks: CCTask[] }) {
  if (tasks.length === 0) return <p style={{ color: "var(--muted)" }}>No active AI tasks.</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 320, overflowY: "auto" }}>
      {tasks.map((t) => (
        <div key={t.id} style={{ ...panelStyle, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: "#a7bec7" }}>
              <span style={{ color: "var(--cyan)" }}>{t.task_number}</span> {t.title}
            </div>
            <div style={{ fontSize: 11, color: "var(--muted)" }}>
              {t.assigned_agent_name ? `→ ${t.assigned_agent_name}` : "unassigned"}
              {t.department ? ` · ${t.department}` : ""}
            </div>
          </div>
          <span style={{ fontSize: 11, color: statusColor(t.status), whiteSpace: "nowrap" }}>
            {t.status.toUpperCase()}
          </span>
        </div>
      ))}
    </div>
  );
}

function ActivityPanel({ activity }: { activity: CCActivityEvent[] }) {
  if (activity.length === 0) return <p style={{ color: "var(--muted)" }}>No activity recorded yet.</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 260, overflowY: "auto" }}>
      {activity.map((e, i) => (
        <div key={`${e.task_id}-${i}`} style={{ fontSize: 12, color: "#a7bec7", borderLeft: "2px solid var(--line)", paddingLeft: 8 }}>
          <span style={{ color: "var(--cyan)" }}>{e.task_number}</span>{" "}
          {e.from_status ? `${e.from_status} → ` : ""}{e.to_status ?? e.action}
          {e.actor ? <span style={{ color: "var(--muted)" }}> · {e.actor}</span> : null}
        </div>
      ))}
    </div>
  );
}
