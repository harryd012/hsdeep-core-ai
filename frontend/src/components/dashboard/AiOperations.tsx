"use client";

import Link from "next/link";
import { memo, useState } from "react";
import { Bot, Send } from "lucide-react";
import { copilotChat, type CopilotChatResponse } from "@/lib/api";
import type { AiOperationsData, SectionState } from "@/lib/dashboard/dashboardModels";

/**
 * AiOperations — Zone 2 (presentational).
 *
 * Semantic purpose: "What does AI currently know and what should the
 * operator do?" Data comes from the existing CommandCenterOverview payload
 * (useCommandCenterOverview, shared with /ai-copilot) plus getAIInsights —
 * both transformed by the dashboard data adapter. No new AI queries.
 *
 * The detailed AI workforce view remains on /ai-copilot; this zone only
 * summarizes. The compact copilot surface reuses the EXISTING copilotChat
 * API (same one the shared CopilotBar uses) — no new backend endpoints.
 */

const STATUS_LABEL = {
  online: { text: "AI ONLINE", color: "var(--green)" },
  degraded: { text: "AI DEGRADED", color: "var(--amber)" },
  offline: { text: "AI DATA UNAVAILABLE", color: "var(--muted)" },
} as const;

/** Quick actions — ONLY routes/actions that already exist in this app. */
const QUICK_ACTIONS = [
  { label: "Show critical alerts", href: "/alerts?severity=critical" },
  { label: "Check device status", href: "/infrastructure" },
  { label: "Analyze trends", href: "/reports" },
  { label: "View SOPs", href: "/sop-engine" },
] as const;

const AiOperations = memo(function AiOperations({
  data,
  state,
}: {
  data: AiOperationsData;
  state: SectionState;
}) {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<CopilotChatResponse | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);

  async function ask() {
    const text = question.trim();
    if (!text || asking) return;
    setAsking(true);
    setChatError(null);
    try {
      const response = await copilotChat(text);
      setAnswer(response);
      setQuestion("");
    } catch {
      setChatError("AI is not responding right now. Try again or use /ai-copilot.");
    } finally {
      setAsking(false);
    }
  }

  const status =
    state === "loading"
      ? { text: "AI CONNECTING", color: "var(--muted)" }
      : STATUS_LABEL[data.aiStatus];

  return (
    <section className="ai-ops" aria-label="AI Operations">
      <header className="ai-ops__header">
        <h2>
          <Bot size={16} /> AI OPERATIONS
        </h2>
        <span className="ai-ops__status" style={{ color: status.color }}>
          <i className="ai-ops__status-dot" style={{ background: status.color }} />
          {status.text}
        </span>
      </header>

      {state === "error" && <div className="ai-ops__note">AI data unavailable.</div>}

      <div className="ai-ops__grid">
        {/* Left: AI CTO / operational intelligence summary */}
        <div className="ai-ops__summary">
          <div className="ai-ops__cto">
            <span className="ai-ops__cto-title">AI CTO</span>
            <span className="ai-ops__status" style={{ color: status.color }}>
              <i className="ai-ops__status-dot" style={{ background: status.color }} />
              SYSTEM · {status.text}
            </span>
          </div>
          <dl className="ai-ops__metrics">
            <div>
              <dt>ACTIVE AGENTS</dt>
              <dd>{data.activeAgents === null ? "—" : `${data.activeAgents}/${data.totalAgents ?? "—"}`}</dd>
            </div>
            <div>
              <dt>ACTIVE TASKS</dt>
              <dd>{data.activeTasks}</dd>
            </div>
            <div>
              <dt>QUEUED</dt>
              <dd>{data.queuedTasks}</dd>
            </div>
            <div>
              <dt>FAILED</dt>
              <dd>{data.failedTasks}</dd>
            </div>
          </dl>

          {data.currentFocus.length > 0 && (
            <div className="ai-ops__focus">
              <h3>CURRENT FOCUS</h3>
              <ul>
                {data.currentFocus.map((task, i) => (
                  <li key={`${task}-${i}`}>{task}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="ai-ops__recommendations">
            <h3>AI ANALYSIS</h3>
            {state === "empty" && <p className="ai-ops__note">No AI insights at this time.</p>}
            {data.recommendations.map((rec) => (
              <p
                key={rec.id}
                className={`ai-ops__rec ai-ops__rec--${rec.severity}`}
                title={rec.kind.replace(/_/g, " ")}
              >
                {rec.message}
              </p>
            ))}
          </div>

          {data.lastUpdated && (
            <small className="ai-ops__updated">
              Updated {new Date(data.lastUpdated).toLocaleTimeString()}
            </small>
          )}
        </div>

        {/* Right: compact copilot (existing copilotChat API) */}
        <div className="ai-ops__copilot">
          <h3>AI COPILOT</h3>
          <div className="ai-ops__actions">
            {QUICK_ACTIONS.map((action) => (
              <Link key={action.href} href={action.href} className="ai-ops__action">
                {action.label}
              </Link>
            ))}
          </div>
          <div className="ai-ops__chat">
            <input
              type="text"
              value={question}
              placeholder="Ask HSDEEP CORE AI anything…"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") ask();
              }}
              disabled={asking}
              aria-label="Ask the AI copilot"
            />
            <button type="button" onClick={ask} disabled={asking || !question.trim()}>
              <Send size={14} />
              {asking ? "Thinking…" : "Ask"}
            </button>
          </div>
          {chatError && <p className="ai-ops__rec ai-ops__rec--critical">{chatError}</p>}
          {answer && (
            <p className="ai-ops__answer">
              {answer.answer}
              {answer.task_created && answer.task_number && (
                <small> · Task {answer.task_number} created</small>
              )}
            </p>
          )}
          <Link href="/ai-copilot" className="ai-ops__workspace-link">
            Open full AI workspace →
          </Link>
        </div>
      </div>
    </section>
  );
});

export default AiOperations;
