// Dashboard Data Types

export interface ExecutiveMetrics {
  activeAlerts: number;
  criticalIncidents: number;
  devicesOnline: number;
  devicesOffline: number;
  sensorsHealthy: number;
  sensorsWarning: number;
  sensorsDown: number;
  collectorSuccess: number;
  collectorRuns: number;
  aiRecommendations: number;
}

export interface HealthSnapshot {
  network: HealthRow;
  cloud: HealthRow;
  collector: HealthRow;
  devices: HealthRow;
  sensors: HealthRow;
  database: HealthRow;
  api: HealthRow;
}

export interface HealthRow {
  label: string;
  pct: number;
  sub: string;
}

export interface OverviewItem {
  label: string;
  count: number;
  status: "healthy" | "warning" | "critical" | "unknown";
  health: number;
}

export interface AlertBreakdown {
  critical: number;
  warning: number;
  info: number;
  total: number;
  by_status: {
    open: number;
    acknowledged: number;
    resolved: number;
  };
}

export interface ActivityItem {
  id: string;
  time: string;
  text: string;
  status?: string;
  severity?: string;
  category: "device" | "alert" | "incident" | "collector" | "system";
}

export interface AIInsight {
  id: string;
  severity: "critical" | "warning" | "info";
  summary: string;
  confidence: number;
  impact: "high" | "medium" | "low";
  recommendedAction: string;
  suggestedSOP: string;
  affectedDevices: string;
  timestamp: string;
}

export interface MapCell {
  label: string;
  value: number;
  icon: typeof import("lucide-react").Globe2;
}

export interface ChartDataPoint {
  label: string;
  value: number;
}

export interface DonutSegment {
  label: string;
  value: number;
  color: string;
}