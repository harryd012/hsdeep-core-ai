"use client";

import { ReactNode, memo } from "react";

interface PanelProps {
  title: string;
  icon?: ReactNode;
  status?: "healthy" | "warning" | "critical" | "info";
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

const Panel = memo(function Panel({ title, icon, status, children, className, style }: PanelProps) {
  const statusColor = {
    healthy: "var(--green)",
    warning: "var(--amber)",
    critical: "var(--red)",
    info: "var(--information)",
  }[status || "info"];

  return (
    <section
      className={`hud-panel ${className || ""}`}
      style={style}
      tabIndex={0}
      aria-label={title}
    >
      <div className="panel-status-line" style={{ backgroundColor: statusColor }} />
      <div className="panel-header">
        <div className="panel-title-group">
          {icon && <span className="panel-icon">{icon}</span>}
          <h2>{title}</h2>
        </div>
        <div className="panel-indicators">
          <span className="panel-status-dot" style={{ backgroundColor: statusColor }} aria-hidden="true" />
        </div>
      </div>
      <div className="panel-body">
        {children}
      </div>
    </section>
  );
});

export default Panel;