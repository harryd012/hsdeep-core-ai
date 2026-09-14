"use client";

import { useState } from "react";
import { Cpu, ChevronDown, ChevronRight, Eye } from "lucide-react";
import {
  CCAgent,
  CCActivityEvent,
  CCMetrics,
  CCTask,
  CCWorkload,
  CommandCenterHealth,
} from "@/lib/api";
import { useCommandCenterOverview } from "@/hooks/useAiQueries";

/* ---------------------------------------------------------------------------
 * REAL backend data only. Compact hierarchy redesign: CTO summary → responsive
 * department grid → expandable agent cards. Every value traces to the
 * CommandCenterOverview payload (GET /api/command-center/overview).
 * ------------------------------------------------------------------------ */

const DEPT_LABEL: Record<string, string> = {
  noc: "NOC",
  soc: "SOC",
  it: "ITSM",
  cloud: "CLOUD",
  infra: "INFRA",
  data: "DATA",
};

function deptLabel(dept: string | null | undefined): string {
  const key = (dept || "").toLowerCase();
  return DEPT_LABEL[key] ?? (dept || "DEPT").toUpperCase();
}

function statusColor(status: string): string {
  switch (status) {
    case "available": case "ready": case "online": case "healthy":
    case "operational": case "active": case "verified": case "recorded":
      return "var(--green, #2ecc71)";
    case "degraded":
      return "var(--amber, #f5a623)";
    case "busy": case "working": case "assigned": case "accepted":
    case "investigating": case "executing": case "verifying":
    case "retrying": case "reassigned":
      return "var(--red, #ff5c5c)";
    case "failed": case "escalated": case "cancelled":
      return "var(--red, #ff5c5c)";
    default:
      return "var(--muted)";
  }
}

function isTerminal(status: string): boolean {
  return status === "recorded" || status === "cancelled";
}

export default function CommandCenterTopology() {
  const { data: overview, isLoading, isError, error, refetch } = useCommandCenterOverview();
  const [expandedDept, setExpandedDept] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<CCAgent | null>(null);

  if (isLoading && overview === undefined) {
    return (
      <div className="org-compact">
        <div className="org-compact__loading">Loading AI Operations…</div>
      </div>
    );
  }

  if (isError && overview === undefined) {
    return (
      <div className="org-compact">
        <div className="org-compact__error">
          <p>Command Center backend unavailable.</p>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>{error?.message ?? String(error)}</p>
          <button onClick={() => refetch()} className="org-compact__retry">Retry</button>
        </div>
      </div>
    );
  }

  const o = overview!;
  const m: CCMetrics = o.metrics;
  const tasks = o.tasks ?? [];
  const agents: CCAgent[] = o.agents ?? [];
  const workload = o.workload ?? [];
  const activity = o.activity ?? [];

  const taskByAgent = new Map<string, CCTask>();
  for (const t of tasks) {
    if (t.assigned_agent_id && !isTerminal(t.status)) {
      taskByAgent.set(t.assigned_agent_id, t);
    }
  }

  const deptAgents = new Map<string, CCAgent[]>();
  for (const a of agents) {
    const key = (a.department || "unassigned").toUpperCase();
    if (!deptAgents.has(key)) deptAgents.set(key, []);
    deptAgents.get(key)!.push(a);
  }

  const activeTasks = tasks.filter((t) => !isTerminal(t.status));

  return (
    <div className="org-compact">
      <CtoSummaryCard m={m} systemOnline={o.system_online} health={o.health ?? null} />
      <div className="org-compact__depts">
        {workload.map((w) => {
          const key = (w.department || "unassigned").toUpperCase();
                    const dAgents = deptAgents.get(key) ?? [];
          const lead = dAgents.find((a) => a.level === "manager") ?? dAgents[0] ?? null;
          const deptTasks = activeTasks.filter(
            (t) => (t.department || "").toUpperCase() === key,
          );
          const isExpanded = expandedDept === key;
          return (
                        <DeptCard
              key={key}
              workload={w}
              leadName={lead?.name ?? null}
              agentCount={dAgents.length}
              activeTaskCount={deptTasks.length}
              isExpanded={isExpanded}
              onToggle={() => setExpandedDept(isExpanded ? null : key)}
            />
          );
        })}
        {workload.length === 0 && (
          <div className="org-compact__empty">No active AI departments.</div>
        )}
      </div>
      {expandedDept && (
        <AgentGrid
          agents={deptAgents.get(expandedDept) ?? []}
          taskByAgent={taskByAgent}
          onSelectAgent={setSelectedAgent}
        />
      )}
      {selectedAgent && (
        <AgentDetailDrawer
          agent={selectedAgent}
          task={taskByAgent.get(selectedAgent.id) ?? null}
          onClose={() => setSelectedAgent(null)}
        />
      )}
      {activity.length > 0 && <ActivityStrip activity={activity} />}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* CTO Summary Card                                                    */
/* ------------------------------------------------------------------ */

function CtoSummaryCard({
  m,
  systemOnline,
  health,
}: {
  m: CCMetrics;
  systemOnline: boolean;
  health: CommandCenterHealth | null;
}) {
  const sysColor = systemOnline ? "var(--green)" : "var(--red)";
  const sysText = systemOnline ? "SYSTEM ONLINE" : "SYSTEM OFFLINE";

  return (
    <div className="org-cto">
      <div className="org-cto__header">
        <Cpu size={16} />
        <span className="org-cto__title">AI CTO</span>
        <span className="org-cto__status" style={{ color: sysColor }}>
          <i className="org-cto__dot" style={{ background: sysColor }} />
          {sysText}
        </span>
      </div>
      <div className="org-cto__metrics">
        <div className="org-cto__metric">
          <span className="org-cto__metric-value" style={{ color: "var(--cyan)" }}>
            {m.active_agents}/{m.total_agents}
          </span>
          <span className="org-cto__metric-label">ACTIVE AGENTS</span>
        </div>
        <div className="org-cto__metric">
          <span className="org-cto__metric-value" style={{ color: "var(--amber)" }}>
            {m.working}
          </span>
          <span className="org-cto__metric-label">WORKING</span>
        </div>
        <div className="org-cto__metric">
          <span className="org-cto__metric-value" style={{ color: "var(--red)" }}>
            {m.critical}
          </span>
          <span className="org-cto__metric-label">CRITICAL</span>
        </div>
        <div className="org-cto__metric">
          <span className="org-cto__metric-value" style={{ color: "var(--muted)" }}>
            {m.queued}
          </span>
          <span className="org-cto__metric-label">QUEUED</span>
        </div>
        {health && (
          <div className="org-cto__metric">
            <span className="org-cto__metric-value" style={{ color: "var(--green)" }}>
              {health.status}%
            </span>
            <span className="org-cto__metric-label">HEALTH</span>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Department Card                                                     */
/* ------------------------------------------------------------------ */

function DeptCard({
  workload,
  leadName,
  agentCount,
  activeTaskCount,
  isExpanded,
  onToggle,
}: {
  workload: CCWorkload;
  leadName: string | null;
  agentCount: number;
  activeTaskCount: number;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const hasActive = activeTaskCount > 0;
  const dotColor = hasActive ? "var(--amber)" : agentCount > 0 ? "var(--green)" : "var(--muted)";

  return (
    <div className={`org-dept-card ${isExpanded ? "org-dept-card--active" : ""}`}>
      <div className="org-dept-card__header" onClick={onToggle}>
        <div className="org-dept-card__info">
          <span className="org-dept-card__name">{deptLabel(workload.department)}</span>
          {leadName && <span className="org-dept-card__lead">Lead: {leadName}</span>}
        </div>
        <div className="org-dept-card__status" style={{ color: dotColor }}>
          <i className="org-dept-card__dot" style={{ background: dotColor }} />
          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </div>
      <div className="org-dept-card__stats">
        <div className="org-dept-card__stat">
          <span className="org-dept-card__stat-value">{agentCount}</span>
          <span className="org-dept-card__stat-label">Agents</span>
        </div>
        <div className="org-dept-card__stat">
          <span className="org-dept-card__stat-value" style={{ color: hasActive ? "var(--amber)" : undefined }}>
            {activeTaskCount}
          </span>
          <span className="org-dept-card__stat-label">Tasks</span>
        </div>
        <div className="org-dept-card__stat">
          <span className="org-dept-card__stat-value">{workload.queued_tasks}</span>
          <span className="org-dept-card__stat-label">Queued</span>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Agent Grid                                                          */
/* ------------------------------------------------------------------ */

function AgentGrid({
  agents,
  taskByAgent,
  onSelectAgent,
}: {
  agents: CCAgent[];
  taskByAgent: Map<string, CCTask>;
  onSelectAgent: (a: CCAgent) => void;
}) {
  if (agents.length === 0) {
    return (
      <div className="org-agent-grid">
        <p className="org-agent-grid__empty">No agents in this department.</p>
      </div>
    );
  }

  return (
    <div className="org-agent-grid">
      <div className="org-agent-grid__header">
        <span>AGENTS ({agents.length})</span>
      </div>
      <div className="org-agent-grid__cards">
        {agents.map((a) => {
          const task = taskByAgent.get(a.id) ?? null;
          const color = statusColor(a.availability);
          return (
            <div key={a.id} className="org-agent-card">
              <div className="org-agent-card__top">
                <span className="org-agent-card__name">{a.name}</span>
                <span className="org-agent-card__level">{a.level}</span>
              </div>
              <div className="org-agent-card__status" style={{ color }}>
                <i className="org-agent-card__dot" style={{ background: color }} />
                {a.availability}
              </div>
              {task && (
                <div className="org-agent-card__task">
                  <span className="org-agent-card__task-number">{task.task_number}</span>
                  <span className="org-agent-card__task-title">{task.title}</span>
                </div>
              )}
              <button className="org-agent-card__view" onClick={() => onSelectAgent(a)}>
                <Eye size={12} /> Details
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Agent Detail Drawer                                                 */
/* ------------------------------------------------------------------ */

function AgentDetailDrawer({
  agent,
  task,
  onClose,
}: {
  agent: CCAgent;
  task: CCTask | null;
  onClose: () => void;
}) {
  const color = statusColor(agent.availability);

  return (
    <div className="org-drawer-overlay" onClick={onClose}>
      <div className="org-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="org-drawer__header">
          <div>
            <h3 className="org-drawer__title">{agent.name}</h3>
            <span className="org-drawer__subtitle">{agent.level} · {agent.department ?? "unassigned"}</span>
          </div>
          <button className="org-drawer__close" onClick={onClose}>×</button>
        </div>
        <div className="org-drawer__body">
          <div className="org-drawer__row">
            <span className="org-drawer__label">Status</span>
            <span className="org-drawer__value" style={{ color }}>
              <i className="org-agent-card__dot" style={{ background: color }} />
              {agent.availability}
            </span>
          </div>
          {agent.skills && agent.skills.length > 0 && (
            <div className="org-drawer__row">
              <span className="org-drawer__label">Capabilities</span>
              <div className="org-drawer__chips">
                {agent.skills.map((c) => (
                  <span key={c} className="org-drawer__chip">{c}</span>
                ))}
              </div>
            </div>
          )}
          {task && (
            <>
              <div className="org-drawer__divider" />
              <h4 className="org-drawer__section-title">ACTIVE TASK</h4>
              <div className="org-drawer__row">
                <span className="org-drawer__label">Task</span>
                <span className="org-drawer__value">{task.task_number} · {task.title}</span>
              </div>
              <div className="org-drawer__row">
                <span className="org-drawer__label">Status</span>
                <span className="org-drawer__value" style={{ color: statusColor(task.status) }}>
                  {task.status}
                </span>
              </div>
              <div className="org-drawer__row">
                <span className="org-drawer__label">Priority</span>
                <span className="org-drawer__value">{task.priority}</span>
              </div>
                            {task.task_type && (
                <div className="org-drawer__row">
                  <span className="org-drawer__label">Task Type</span>
                  <span className="org-drawer__value org-drawer__value--wrap">{task.task_type}</span>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Activity Strip                                                      */
/* ------------------------------------------------------------------ */

function ActivityStrip({ activity }: { activity: CCActivityEvent[] }) {
  return (
    <div className="org-activity">
      <div className="org-activity__header">RECENT ACTIVITY</div>
      <div className="org-activity__list">
        {activity.slice(0, 8).map((e, i) => (
          <div key={`${e.task_id}-${i}`} className="org-activity__item">
            <span className="org-activity__task">{e.task_number}</span>
            <span className="org-activity__action">
              {e.from_status ? `${e.from_status} → ` : ""}{e.to_status ?? e.action}
            </span>
            {e.actor && <span className="org-activity__actor"> · {e.actor}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
