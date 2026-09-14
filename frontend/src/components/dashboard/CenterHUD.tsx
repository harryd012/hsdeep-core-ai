"use client";

import { memo, useEffect, useState, type CSSProperties } from "react";
import { Globe2, Radio, ShieldCheck, Workflow } from "lucide-react";
import { getDashboardSummary, getAutomationStatus } from "@/lib/api";

const CenterHUD = memo(function CenterHUD() {
  const [metrics, setMetrics] = useState<{
    health: number | null;
    collectors: number | null;
    sites: number | null;
    sensors: number | null;
  }>({ health: null, collectors: null, sites: null, sensors: null });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [summary, automation] = await Promise.all([
          getDashboardSummary().catch(() => null),
          getAutomationStatus().catch(() => null),
        ]);
        if (cancelled) return;
        // Real values only: when a backend value is unavailable we render null
        // ("—") rather than a fabricated placeholder like a fixed 98%.
        setMetrics({
          health: automation && automation.runs_last_24h > 0
            ? Math.round(automation.success_rate_pct)
            : null,
          collectors: automation?.sources_connected ?? summary?.monitoring_sources ?? null,
          sites: summary?.sites ?? null,
          sensors: summary?.sensors ?? null,
        });
      } catch {
        // leave nulls — honest "unavailable" state
      }
    }
    load();
    const timer = setInterval(load, 60000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  const health = metrics.health;
  const ringColor = health === null ? "var(--muted)" : health >= 90 ? "var(--hud-color)" : health >= 70 ? "var(--amber)" : "var(--red)";
  const isOnline = metrics.collectors !== null || metrics.sites !== null || metrics.sensors !== null;

  // Equalizer bar heights (deterministic pseudo-random for stable SSR)
  const barHeights = [70, 40, 90, 55, 100, 30, 75, 60];

  // Hydration-safe "Last sync" timestamp: initialize as null, set in useEffect
  const [lastSync, setLastSync] = useState<string | null>(null);
  useEffect(() => {
    setLastSync(new Date().toLocaleTimeString());
    const timer = setInterval(() => setLastSync(new Date().toLocaleTimeString()), 60000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div
      className="center-hud"
      aria-label="Center HUD"
      style={{ "--ring-color": ringColor } as CSSProperties}
    >
      {/* ── Radar sweep (conic gradient wedge) ── */}
      <div className="center-hud__sweep" aria-hidden="true" />

      {/* ── Multi-ring JARVIS HUD ── */}
      <div className="center-hud__rings-wrapper" aria-hidden="true">
        <svg
          viewBox="0 0 200 200"
          className="center-hud__rings"
          role="img"
          aria-label="System status rings"
        >
          <defs>
            <linearGradient id="hudGrad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="var(--hud-color)" stopOpacity="0.8" />
              <stop offset="100%" stopColor="var(--hud-color)" stopOpacity="0.1" />
            </linearGradient>
          </defs>

          {/* ── Ring 4 (outermost): thin dotted circle (very slow clockwise) ── */}
          <g className="center-hud__ring center-hud__ring--4">
            <circle
              cx="100" cy="100" r="95"
              fill="none"
              stroke="var(--ring-color)"
              strokeWidth="1"
              strokeOpacity="0.5"
              strokeDasharray="2 6"
            />
          </g>

          {/* ── Ring 3: segmented-arc circle (counter-clockwise) ── */}
          <g className="center-hud__ring center-hud__ring--3">
            <circle
              cx="100" cy="100" r="80"
              fill="none"
              stroke="var(--ring-color)"
              strokeWidth="1"
              strokeOpacity="0.4"
              strokeDasharray="4 6"
            />
          </g>

          {/* ── Ring 2: dashed circle (clockwise) ── */}
          <g className="center-hud__ring center-hud__ring--2">
            <circle
              cx="100" cy="100" r="65"
              fill="none"
              stroke="var(--ring-color)"
              strokeWidth="1.5"
              strokeOpacity="0.6"
              strokeDasharray="6 8"
            />
            {/* Orbiting dots on Ring 2 (rotate with the ring) */}
            <circle cx="100" cy="20" r="3" fill="var(--ring-color)" stroke="var(--ring-color)" strokeWidth="1" />
            <circle cx="30" cy="100" r="3" fill="var(--ring-color)" stroke="var(--ring-color)" strokeWidth="1" />
            <circle cx="100" cy="180" r="3" fill="var(--ring-color)" stroke="var(--ring-color)" strokeWidth="1" />
          </g>

          {/* ── Ring 1 (innermost): solid thin stroke + tick marks ── */}
          <g className="center-hud__ring center-hud__ring--1">
            <circle
              cx="100" cy="100" r="50"
              fill="none"
              stroke="var(--ring-color)"
              strokeWidth="2"
            />
            {/* Tick marks around Ring 1 (6 ticks, every 60°) */}
            <g stroke="var(--ring-color)" strokeWidth="1.5" strokeOpacity="0.7">
              <line x1="100" y1="44" x2="100" y2="40" />
              <line x1="100" y1="156" x2="100" y2="160" />
              <line x1="44" y1="100" x2="40" y2="100" />
              <line x1="156" y1="100" x2="160" y2="100" />
              <line x1="62" y1="62" x2="59" y2="59" />
              <line x1="138" y1="138" x2="141" y2="141" />
            </g>
          </g>
        </svg>

        {/* ── Ring 4 orbiting nodes (CSS, non-rotating dots) ── */}
        <div className="center-hud__orbit-wrapper">
          <div className="center-hud__orbit-node" style={{ animationDelay: "0s" }} />
          <div className="center-hud__orbit-node center-hud__orbit-node--2" style={{ animationDelay: "3s" }} />
        </div>
      </div>

      {/* ── Content ── */}
      <div className="center-hud__content">
        {/* CORE: "HSDEEP" logo */}
        <div className="center-hud__logo">HSDEEP</div>

        {/* Status line — reflects actual backend reachability, never assumed */}
        <div className="center-hud__status">
          <div className="center-hud__dot" style={isOnline ? undefined : { background: "var(--red)" }} />
          <span>{isOnline ? "SYSTEM ONLINE" : "AWAITING BACKEND"}</span>
        </div>

        {/* Equalizer / waveform bar */}
        <div className="center-hud__equalizer" aria-hidden="true">
          {barHeights.map((h, i) => (
            <div
              key={i}
              className="center-hud__eq-bar"
              style={{
                height: `${h}%`,
                animationDelay: `${i * 0.12}s`,
              } as CSSProperties}
            />
          ))}
        </div>

        {/* 4 stat mini-cards — live data from backend */}
        <div className="center-hud__metrics">
          <div className="center-hud__metric">
            <ShieldCheck size={18} />
            <div>
              <b>{metrics.health === null ? "—" : `${metrics.health}%`}</b>
              <small>Health</small>
            </div>
          </div>
          <div className="center-hud__metric">
            <Workflow size={18} />
            <div>
              <b>{metrics.collectors ?? "—"}</b>
              <small>Collectors</small>
            </div>
          </div>
          <div className="center-hud__metric">
            <Globe2 size={18} />
            <div>
              <b>{metrics.sites ?? "—"}</b>
              <small>Sites</small>
            </div>
          </div>
          <div className="center-hud__metric">
            <Radio size={18} />
            <div>
              <b>{metrics.sensors === null ? "—" : metrics.sensors >= 1000 ? `${(metrics.sensors / 1000).toFixed(1)}k` : metrics.sensors}</b>
              <small>Sensors</small>
            </div>
          </div>
        </div>

        <div className="center-hud__last-sync">
          <small>Last sync: {lastSync ?? "—"}</small>
        </div>
      </div>
    </div>
  );
});

export default CenterHUD;
