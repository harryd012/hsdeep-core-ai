"use client";

import { useEffect, useState } from "react";
import { Clock, AlertTriangle, AlertCircle } from "lucide-react";

interface TrialCountdownProps {
  trialEndsAt: string | null;
}

/**
 * Live trial countdown that recalculates on each render.
 * Color shifts: green (>7 days), amber (3-7 days), red (<3 days).
 */
export default function TrialCountdown({ trialEndsAt }: TrialCountdownProps) {
  const [now, setNow] = useState(Date.now());

  // Recalculate every minute
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);

  if (!trialEndsAt) return null;

  const endDate = new Date(trialEndsAt).getTime();
  const diffMs = endDate - now;
  const daysLeft = Math.max(0, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));
  const hoursLeft = Math.max(0, Math.ceil(diffMs / (1000 * 60 * 60)));

  const isUrgent = daysLeft < 3;
  const isWarning = daysLeft >= 3 && daysLeft <= 7;
  const isComfortable = daysLeft > 7;

  const bannerStyle = isUrgent
    ? { background: "rgba(255,77,79,0.12)", borderColor: "var(--red)" }
    : isWarning
      ? { background: "rgba(255,178,0,0.1)", borderColor: "var(--amber)" }
      : { background: "rgba(0,211,141,0.08)", borderColor: "var(--green)" };

  const iconColor = isUrgent ? "var(--red)" : isWarning ? "var(--amber)" : "var(--green)";
  const textColor = isUrgent ? "var(--red)" : isWarning ? "var(--amber)" : "var(--green)";

  const Icon = isUrgent ? AlertCircle : isWarning ? AlertTriangle : Clock;

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "10px 14px",
      borderRadius: 8,
      border: `1px solid ${bannerStyle.borderColor}`,
      background: bannerStyle.background,
      marginBottom: 16,
    }}>
      <Icon size={16} style={{ color: iconColor, flexShrink: 0 }} />
      <span style={{ fontSize: 13, color: textColor, fontWeight: 600 }}>
        Trial active — {daysLeft} {daysLeft === 1 ? "day" : "days"} left
      </span>
      {hoursLeft <= 24 && hoursLeft > 0 && (
        <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
          {hoursLeft}h remaining
        </span>
      )}
    </div>
  );
}
