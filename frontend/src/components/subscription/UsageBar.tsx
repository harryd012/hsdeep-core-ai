"use client";

import { motion } from "framer-motion";
import { AlertTriangle, TrendingUp } from "lucide-react";
import { useState } from "react";

interface UsageBarProps {
  label: string;
  used: number;
  limit: number;
  unit?: string;
  delta?: number; // weekly delta if available
  onClick?: () => void;
}

/**
 * Animated usage bar with color-coded fill based on utilization.
 * Colors: <70% cyan, 70-90% amber, >90% red with warning icon.
 */
export default function UsageBar({ label, used, limit, unit = "", delta, onClick }: UsageBarProps) {
  const pct = limit > 0 ? Math.min(Math.round((used / limit) * 100), 100) : 0;
  const isWarning = pct >= 70 && pct < 90;
  const isCritical = pct >= 90;
  const isClickable = !!onClick;

  const [showTooltip, setShowTooltip] = useState(false);

  const barColor = isCritical ? "var(--red)" : isWarning ? "var(--amber)" : "var(--cyan)";
  const bgColor = isCritical ? "rgba(255,77,79,0.1)" : isWarning ? "rgba(255,178,0,0.1)" : "rgba(0,220,255,0.08)";

  return (
    <div
      style={{ position: "relative" }}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div
        onClick={onClick}
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 6,
          cursor: isClickable ? "pointer" : "default",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>{label}</span>
          {isCritical && <AlertTriangle size={12} style={{ color: "var(--red)" }} />}
        </div>
        <span style={{ fontSize: 12, fontWeight: 600, color: isCritical ? "var(--red)" : "var(--text)" }}>
          {used.toLocaleString()}/{limit.toLocaleString()}{unit} ({pct}%)
        </span>
      </div>

      {/* Bar track */}
      <div style={{
        width: "100%",
        height: 8,
        borderRadius: 4,
        background: bgColor,
        overflow: "hidden",
      }}>
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          style={{
            height: "100%",
            borderRadius: 4,
            background: barColor,
            boxShadow: `0 0 8px ${barColor}`,
          }}
        />
      </div>

      {/* Hover tooltip */}
      {showTooltip && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          style={{
            position: "absolute",
            top: -32,
            right: 0,
            fontSize: 11,
            padding: "4px 8px",
            borderRadius: 4,
            background: "rgba(7, 19, 27, 0.95)",
            border: "1px solid var(--line)",
            color: "var(--muted)",
            whiteSpace: "nowrap",
            zIndex: 10,
          }}
        >
          {delta !== undefined ? (
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <TrendingUp size={10} />
              {delta >= 0 ? "+" : ""}{delta} this week
            </span>
          ) : (
            <span style={{ color: "var(--muted)", fontStyle: "italic" }}>
              {/* TODO: wire weekly delta from backend */}
              {used}/{limit} {unit}
            </span>
          )}
        </motion.div>
      )}
    </div>
  );
}
