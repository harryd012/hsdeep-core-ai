"use client";

import {
  Activity, AlertTriangle, Bell, BookOpen, Bot, CheckCircle2,
  CircleGauge, Cloud, Cpu, Database, Globe2, HardDrive,
  Menu, Mic, Network, Radio, Send, Server, Settings, ShieldCheck, Sparkles,
  Workflow, X, Zap,
  Brain,
} from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { AlertSummaryPanel, TopAlertsPanel } from "@/components/AlertsPanel";
import AICTOStatusCard from "@/components/AICTOStatusCard";
import {
  AIInsight, AutomationStatus, CloudStatus, DashboardSummary, NetworkStatus,
  getAIInsights, getAutomationStatus, getCloudStatus, getDashboardSummary, getNetworkStatus,
} from "@/lib/api";

// All five panels below poll every 30 seconds, per the "refresh
// automatically every 30 seconds" requirement - a different cadence than
// AlertsPanel.tsx's 15s, which is fine since each panel owns its own cycle.
const REFRESH_MS = 30000;

const ERROR_STYLE = { display: "flex", gap: 6, alignItems: "center", color: "var(--amber)", fontSize: 12 } as const;

function severityIcon(severity: AIInsight["severity"]) {
  if (severity === "critical") return AlertTriangle;
  if (severity === "warning") return Sparkles;
  return CheckCircle2;
}

function capitalize(value: string): string {
  return value.length === 0 ? value : value[0].toUpperCase() + value.slice(1);
}

function InfrastructureOverviewPanel() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await getDashboardSummary();
        if (!cancelled) {
          setSummary(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load infrastructure summary");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }

    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const rows: [string, string, typeof Globe2][] = [
    [loaded ? String(summary?.sites ?? 0) : "…", "SITES", Globe2],
    [loaded ? String(summary?.devices ?? 0) : "…", "DEVICES", Server],
    [loaded ? String(summary?.sensors ?? 0) : "…", "SENSORS", Radio],
    [loaded ? String(summary?.cloud_resources ?? 0) : "…", "CLOUD RESOURCES", Cloud],
  ];

  return (
    <Panel title="INFRASTRUCTURE OVERVIEW" className="infra">
      {error && <p style={ERROR_STYLE}><AlertTriangle size={14} /> {error}</p>}
      {rows.map(([value, label, Icon]) =>
        <div className="infra-row" key={label}><span><Icon /></span><div><b>{value}</b><small>{label}</small></div></div>)}
    </Panel>
  );
}

function AIInsightsPanel() {
  const [insights, setInsights] = useState<AIInsight[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await getAIInsights();
        if (!cancelled) {
          setInsights(data.insights);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load AI insights");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }

    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <Panel title="AI INSIGHTS" className="insights">
      {!loaded && <p><CircleGauge /> Loading insights…</p>}
      {error && <p style={ERROR_STYLE}><AlertTriangle size={14} /> {error}</p>}
      {loaded && !error && insights.length === 0 && <p><CheckCircle2 /> No insights at this time.</p>}
      {insights.map((insight, i) => {
        const Icon = severityIcon(insight.severity);
        return <p key={`${insight.kind}-${i}`}><Icon /> {insight.message}</p>;
      })}
    </Panel>
  );
}

function NetworkHealthPanel() {
  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await getNetworkStatus();
        if (!cancelled) {
          setStatus(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load network status");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }

    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  let label = "…";
  if (loaded) {
    label = !status || status.sites_total === 0
      ? "No sites configured yet"
      : `${status.sites_online}/${status.sites_total} Sites Online`;
  }

  return (
    <Panel title="NETWORK HEALTH" className="network-panel">
      <div className="world-map"><Globe2 /><span className="node n1" /><span className="node n2" /><span className="node n3" /><span className="node n4" /></div>
      <small>Global Connectivity</small><b>{label}</b>
      {error && <p style={ERROR_STYLE}><AlertTriangle size={14} /> {error}</p>}
    </Panel>
  );
}

function CloudStatusPanel() {
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await getCloudStatus();
        if (!cancelled) {
          setStatus(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load cloud status");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }

    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const vendors = status?.vendors ?? [];

  return (
    <Panel title="CLOUD STATUS" className="cloud-panel">
      {!loaded && <p style={{ color: "#75909b", fontSize: 12 }}>Loading cloud status…</p>}
      {error && <p style={ERROR_STYLE}><AlertTriangle size={14} /> {error}</p>}
      {loaded && !error && vendors.length === 0 && <p>No cloud resources detected yet.</p>}
      {vendors.map((vendor) =>
        <div key={vendor.vendor}><Cloud /><b>{vendor.vendor}</b><small>{vendor.resource_count} Resources</small><em>{capitalize(vendor.status)}</em></div>)}
    </Panel>
  );
}

function AutomationCenterPanel() {
  const [status, setStatus] = useState<AutomationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await getAutomationStatus();
        if (!cancelled) {
          setStatus(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load automation status");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }

    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const rows: [string, string, typeof Workflow][] = [
    [loaded ? String(status?.sources_connected ?? 0) : "…", "Active", Workflow],
    [loaded ? String(status?.runs_last_24h ?? 0) : "…", "Executed Today", Cpu],
    [loaded ? `${status?.success_rate_pct ?? 0}%` : "…", "Overall", CheckCircle2],
  ];

  return (
    <Panel title="AUTOMATION CENTER" className="automation">
      {error && <p style={ERROR_STYLE}><AlertTriangle size={14} /> {error}</p>}
      {rows.map(([value, label, Icon]) => <div key={label}><Icon /><strong>{value}</strong><small>{label}</small></div>)}
    </Panel>
  );
}

const replies = [
  "All core systems are online. I found no critical infrastructure faults.",
  "Network health is stable at 98.6%. I am monitoring two latency variations.",
  "Automation Center has completed 137 actions today with no failed workflows.",
];

function Panel({ title, className = "", children }: { title: string; className?: string; children: React.ReactNode }) {
  return <section className={`hud-panel ${className}`}><h2>{title}</h2>{children}</section>;
}

export default function CoreHUD() {
  const [time, setTime] = useState<Date | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [chat, setChat] = useState<{ role: "ai" | "user"; text: string }[]>([
    { role: "ai", text: "Hello Hardeep. I’m your AI copilot. How can I assist you today?" },
  ]);
  const [thinking, setThinking] = useState(false);
  const chatBarRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const timer = window.setInterval(() => setTime(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  // Reserve space for the fixed bottom chat bar. Mirrors the documented
  // CopilotBar contract: measure the bar's real rendered footprint (chat form
  // plus issue badge when present) and publish it as `--copilot-bar-height`,
  // which the shared `.hs-app` container in globals.css adds to its bottom
  // padding so the last content section is never hidden underneath the bar.
  useEffect(() => {
    const chatBar = chatBarRef.current;
    if (!chatBar || typeof window === "undefined") return;

    const root = document.documentElement;

    const measure = () => {
      const chatRect = chatBar.getBoundingClientRect();
      let reserved = window.innerHeight - chatRect.top;

      const badge = document.querySelector<HTMLElement>(".issue-badge");
      if (badge) {
        const badgeRect = badge.getBoundingClientRect();
        reserved = Math.max(reserved, window.innerHeight - badgeRect.top);
      }

      root.style.setProperty(
        "--copilot-bar-height",
        `${Math.round(Math.max(0, reserved))}px`
      );
    };

    measure();

    const resizeObserver = new ResizeObserver(measure);
    resizeObserver.observe(chatBar);
    const mutationObserver = new MutationObserver(measure);
    mutationObserver.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", measure);

    return () => {
      resizeObserver.disconnect();
      mutationObserver.disconnect();
      window.removeEventListener("resize", measure);
      // Leaving /dashboard (the only page rendering the bar): clear the
      // reservation so other pages don't inherit stale bottom padding.
      root.style.removeProperty("--copilot-bar-height");
    };
  }, []);

  function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (!message.trim() || thinking) return;
    const prompt = message.trim();
    setChat((items) => [...items, { role: "user", text: prompt }]);
    setMessage("");
    setThinking(true);
    window.setTimeout(() => {
      setChat((items) => [...items, { role: "ai", text: replies[items.length % replies.length] }]);
      setThinking(false);
    }, 850);
  }

  return (
    <>
      <header className="hs-header">
        <button className="menu-button" onClick={() => setMenuOpen(true)} aria-label="Open menu"><Menu /></button>
        <div className="mini-brand"><span>HS</span><div><b>HSDEEP <em>CORE AI</em></b><small>Autonomous Infrastructure Intelligence</small></div></div>
        <div className="hero-brand"><h1>HSDEEP <span>CORE AI</span></h1><p>Autonomous Infrastructure Intelligence</p></div>
        <div className="header-tools">
          <div className="timebox"><b>{time ? time.toLocaleTimeString("en-US") : "--:--:--"}</b><small>{time ? time.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "SYSTEM TIME"}</small></div>
          <div className="userbox"><Bot /><div><b>Hardeep Singh</b><small>System Architect</small></div></div>
        </div>
      </header>

      {menuOpen && (
        <aside className="hs-sidebar open">
          <button className="close-menu" onClick={() => setMenuOpen(false)} aria-label="Close menu"><X /></button>
          <nav>
            {(["Dashboard", "Infrastructure", "Monitoring", "Alerts", "SOP Engine", "AI Copilot", "Automation", "Reports", "Settings"]).map((label) => (
              <button key={label} className={label === "Dashboard" ? "active" : ""}>{label}</button>
            ))}
          </nav>
          <div className="system-status"><b>HSDEEP CORE AI<br />SYSTEM STATUS</b><div className="pulse-icon"><Activity /></div><small><i /> All Systems Operational</small></div>
        </aside>
      )}
      {menuOpen && <button className="menu-scrim" onClick={() => setMenuOpen(false)} aria-label="Close menu" />}

      <main className="dashboard">
        <div className="overview">
          <InfrastructureOverviewPanel />

          <section className="core-stage">
            <div className="core-gridlines" />
            <div className={`reactor ${thinking ? "thinking" : ""}`}>
              <div className="ring ring-one"><i /><i /><i /></div>
              <div className="ring ring-two"><i /><i /><i /></div>
              <div className="ring ring-three" />
              <div className="ring ring-four" />
              <div className="core-center"><strong>HS</strong><b>MODE ACTIVE</b><small><i /> SYSTEM ONLINE</small></div>
            </div>
            <div className="waveform">{Array.from({ length: 54 }).map((_, i) => <i key={i} style={{ height: `${7 + ((i * 17) % 25)}px` }} />)}</div>
          </section>

          <AICTOStatusCard />

          <AIInsightsPanel />

          <div className="right-stack">
            <AlertSummaryPanel />
            <TopAlertsPanel />
          </div>
        </div>

        <div className="middle-grid">
          <NetworkHealthPanel />
          <CloudStatusPanel />
          <Panel title="SECURITY POSTURE" className="security-panel">
            <div className="shield-score"><ShieldCheck /><strong>92<small>/100</small></strong><b>SECURE</b></div>
            <ul><li>Threats Blocked <b>128</b></li><li>Vulnerabilities <b>5</b></li><li>Compliance <b>✓</b></li></ul>
          </Panel>
          <Panel title="SOP ENGINE STATUS" className="sop-panel">
            <div className="sop-icon"><BookOpen /></div><div><strong>142</strong><small>SOPs Available</small><strong>23</strong><small>Auto Executions Today</small></div>
          </Panel>
        </div>

        <div className="bottom-grid">
          <Panel title="AI COPILOT COMMAND CENTER" className="copilot">
            <div className="ai-face"><Bot /></div>
            <div className="chat-log">
              {chat.slice(-2).map((item, i) => <p key={i} className={item.role}>{item.role === "ai" ? "‹ " : ""}{item.text}</p>)}
              {thinking && <p className="ai typing">Analyzing system telemetry…</p>}
              <div className="quick-actions"><button onClick={() => setMessage("Show infrastructure health")}>Show infrastructure health</button><button onClick={() => setMessage("Check open alerts")}>Check open alerts</button><button onClick={() => setMessage("Run SOP: VPN Troubleshoot")}>Run SOP: VPN Troubleshoot</button></div>
            </div>
          </Panel>
          <AutomationCenterPanel />
        </div>
      </main>

      <form ref={chatBarRef} className="chat-bar" onSubmit={sendMessage}>
        <div className="mode"><i /> HSDEEP CORE AI MODE: <b>ACTIVE</b></div>
        <div className="chat-input"><Bot /><input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Ask HSDEEP CORE AI anything…" aria-label="Message HSDEEP Core AI" /><button type="button" aria-label="Voice input"><Mic /></button><button type="submit" aria-label="Send message"><Send /></button></div>
      </form>
    </>
  );
}
