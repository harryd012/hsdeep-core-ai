"use client";

import { memo, useEffect, useState } from "react";
import { AlertTriangle, Bell, Bot, CheckCircle2, CreditCard, HardDrive, Server, Workflow } from "lucide-react";
import { getAlertSummary, getDevices, getSensors, getAutomationStatus, getAIInsights, listAlerts } from "@/lib/api";
import type { SubscriptionWithPlan } from "@/lib/usage-api";
import { PLANS, derivePlanIdFromTier } from "@/lib/plans";
import { computeDeduplicatedSummary } from "@/lib/alert-dedup";
import {
  subscribeToSubscription,
  refreshSubscription,
} from "@/lib/subscription-store";
import { StatCard } from "@/components/ui/StatCard";

const REFRESH_MS = 60000;

// Status -> (human label, tailwind accent) for the compact Subscription card.
const STATUS_LABEL: Record<string, string> = {
  trialing: "TRIALING",
  active: "ACTIVE",
  past_due: "PAST DUE",
  suspended: "SUSPENDED",
  canceled: "CANCELED",
  incomplete: "INCOMPLETE",
  incomplete_expired: "INCOMPLETE",
};

function statusAccent(status: string | undefined): string {
  if (!status || status === "canceled" || status === "suspended" || status === "past_due" || status?.startsWith("incomplete"))
    return "text-red-500 border-red-500/40";
  if (status === "trialing") return "text-yellow-500 border-yellow-500/40";
  if (status === "active") return "text-emerald-500 border-emerald-500/40";
  return "text-slate-400 border-slate-400/40";
}

const ExecutiveMetrics = memo(function ExecutiveMetrics() {
  const [loaded, setLoaded] = useState(false);
  const [alerts, setAlerts] = useState({ active: 0, critical: 0, rawEventCount: 0 });
  const [devices, setDevices] = useState({ online: 0, offline: 0 });
  const [sensors, setSensors] = useState({ healthy: 0, warning: 0, down: 0 });
  const [automation, setAutomation] = useState({ success: 0, runs: 0 });
  const [aiCount, setAiCount] = useState(0);
  const [subscription, setSubscription] = useState<SubscriptionWithPlan | null>(null);
  const [subState, setSubState] = useState<"loading" | "loaded" | "error">("loading");
  // Interim fallback flag: after ~3s of unresolved loading the card shows
  // "NO PLAN" instead of "…" so a hung/missing backend is never rendered as
  // a permanent ellipsis. The real value swaps in when the store settles.
  const [slowLoad, setSlowLoad] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [a, d, s, au, ai, alertsList] = await Promise.all([
          getAlertSummary(),
          getDevices().catch(() => []),
          getSensors().catch(() => []),
          getAutomationStatus().catch(() => null),
          getAIInsights().catch(() => ({ insights: [] })),
          listAlerts({ status: "open", limit: 100 }).catch(() => []),
        ]);
        if (cancelled) return;

        // Deduplicated counts (user-facing) with raw count for debugging
        const dedup = computeDeduplicatedSummary(alertsList);
        setAlerts({
          active: dedup.total,
          critical: dedup.by_severity.critical,
          rawEventCount: dedup.raw_event_count,
        });

        const online = d.filter(x => x.status === "up").length;
        const down = d.filter(x => x.status === "down").length;
        const warning = d.filter(x => x.status === "warning").length;
        setDevices({ online, offline: down + warning });
        const h = { healthy: 0, warning: 0, down: 0 };
        s.forEach(x => {
          if (x.status === "up") h.healthy++;
          else if (x.status === "warning") h.warning++;
          else h.down++;
        });
        setSensors(h);
        setAutomation(au ? { success: au.success_rate_pct, runs: au.runs_last_24h } : { success: 0, runs: 0 });
        setAiCount(ai.insights.length);
        setLoaded(true);
      } catch {
        // handled by loading state
      }
    }
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  // Compact Subscription state for the 8th dashboard card.
  //
  // BUG FIX: this previously registered ONLY window.setInterval(loadSub,
  // REFRESH_MS) with no initial call, so the first subscription fetch did
  // not fire until 60s after mount and the card sat on "…" forever.
  // Now it subscribes to the shared subscription store (single source of
  // truth shared with /usage and /subscription), which kicks its first load
  // immediately and notifies synchronously; dedup means the dashboard card,
  // status panel and plan selector share one request.
  useEffect(() => {
    let slowTimer: number | undefined;
    const unsubscribe = subscribeToSubscription((s) => {
      if (slowTimer) { window.clearTimeout(slowTimer); slowTimer = undefined; }
      setSubscription(s.subscription); // null == no plan yet (legit lifecycle state)
      setSubState(s.error ? "error" : "loaded");
    });
    slowTimer = window.setTimeout(() => setSlowLoad(true), 3000);
    // Keep the card fresh without firing our own competing request — the
    // store dedups concurrent refreshes across all consumers.
    const timer = window.setInterval(() => void refreshSubscription(), REFRESH_MS);
    return () => {
      unsubscribe();
      if (slowTimer) window.clearTimeout(slowTimer);
      window.clearInterval(timer);
    };
  }, []);

  // Never flash "NO PLAN" while the fetch is in flight, and never claim "NO
  // PLAN" when the backend is unreachable - only a *loaded* null subscription
  // is a legitimate "no plan" lifecycle state. Exception: after 3s of an
  // unresolved load we intentionally degrade "…" -> "NO PLAN" per spec so an
  // ellipsis is never user-facing beyond a beat.
  //
  // Plan NAME comes from the PLANS config (single source of truth) keyed by
  // the backend tier; server-provided labels remain as fallback for any tier
  // not present in the frontend catalog.
  const configPlanId = derivePlanIdFromTier(subscription?.plan_tier);
  const subPlan =
    subState === "loading"
      ? (slowLoad ? "NO PLAN" : "…") :
    subState === "error" ? "N/A" :
    (configPlanId ? PLANS[configPlanId].name : subscription?.plan_name ?? subscription?.plan_tier ?? "").toUpperCase() || "NO PLAN";
  const subStatus = subscription?.status;
  const subLabel =
    subState === "loading" ? "LOADING..." :
    subState === "error" ? "UNAVAILABLE" :
    STATUS_LABEL[subStatus ?? ""] ?? "NOT ACTIVE";
  const subAccent =
    subState === "loading" || subState === "error"
      ? "text-slate-400 border-slate-400/40"
      : statusAccent(subStatus);

  return (
    <div className="executive-metrics">
      <StatCard
        icon={<Bell size={18} />}
        count={loaded ? alerts.active : "…"}
        label="Active Alerts"
        subtext={loaded && alerts.rawEventCount > alerts.active ? `${alerts.rawEventCount} raw` : undefined}
        href="/alerts?status=active"
        accentColor="text-yellow-500 border-yellow-500/40"
      />
      <StatCard
        icon={<AlertTriangle size={18} />}
        count={loaded ? alerts.critical : "…"}
        label="Critical Incidents"
        href="/incidents?severity=critical"
        accentColor="text-red-500 border-red-500/40"
      />
      <StatCard
        icon={<Server size={18} />}
        count={loaded ? devices.online : "…"}
        label="Devices Online"
        href="/infrastructure?status=online"
        accentColor="text-emerald-500 border-emerald-500/40"
      />
      <StatCard
        icon={<HardDrive size={18} />}
        count={loaded ? devices.offline : "…"}
        label="Devices Offline"
        href="/infrastructure?status=offline"
        accentColor="text-red-500 border-red-500/40"
      />
      <StatCard
        icon={<CheckCircle2 size={18} />}
        count={loaded ? sensors.healthy : "…"}
        label="Sensors Healthy"
        href="/monitoring?sensor_status=healthy"
        accentColor="text-emerald-500 border-emerald-500/40"
      />
      <StatCard
        icon={<Workflow size={18} />}
        count={loaded ? `${automation.success}%` : "…"}
        label="Collector Success"
        href="/monitoring?view=collectors"
        accentColor="text-cyan-500 border-cyan-500/40"
      />
      <StatCard
        icon={<Bot size={18} />}
        count={loaded ? aiCount : "…"}
        label="AI Recommendations"
        href="/ai-copilot?tab=recommendations"
        accentColor="text-purple-500 border-purple-500/40"
      />
      <StatCard
        icon={<CreditCard size={18} />}
        count={subPlan}
        label="Subscription"
        subtext={subState === "loading" ? undefined : `● ${subLabel}`}
        href="/subscription"
        accentColor={subAccent}
      />
    </div>
  );
});

export default ExecutiveMetrics;
