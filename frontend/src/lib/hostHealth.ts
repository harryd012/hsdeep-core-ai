/**
 * Host health verdict — explains, in plain sentences, WHY a host is in its
 * current state, from the data the Host page already loads (device, sensors,
 * alerts, interfaces). Deterministic rules, no model: every reason names the
 * fact it rests on and the tab where that fact lives. Missing data is never
 * read as healthy.
 */

export type VerdictTone = "good" | "warn" | "bad" | "neutral";
export type HostTab = "overview" | "sensors" | "alerts" | "interfaces" | "events" | "info";

export interface VerdictReason {
  id: string;
  tone: Exclude<VerdictTone, "neutral" | "good"> | "info";
  text: string;
  tab: HostTab;
}

export interface HostVerdict {
  tone: VerdictTone;
  headline: string;
  reasons: VerdictReason[];
}

export interface VerdictDevice {
  status: string;
  last_seen_at: string | null;
}
export interface VerdictSensor {
  id: string;
  name: string;
  status: string;
  unit: string | null;
  current_value: number | null;
  warning_threshold: number | null;
  critical_threshold: number | null;
  last_value_at: string | null;
}
export interface VerdictAlert {
  title: string;
  severity: string;
  status: string;
  occurrence_count: number;
  last_occurred_at: string;
}
export interface VerdictInterface {
  name: string;
  admin_status: string;
  oper_status: string;
}

export type SensorState = "ok" | "warning" | "critical" | "unknown";

const DOWN = new Set(["down", "critical", "offline", "unreachable", "error", "failed"]);
const STALE_SENSOR_MS = 60 * 60_000;
const STALE_HOST_MS = 24 * 3_600_000;
const FLAPPING_OCCURRENCES = 50;

function age(iso: string | null, now: number): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : Math.max(0, now - t);
}

function humanAge(ms: number): string {
  const m = Math.round(ms / 60_000);
  if (m < 60) return `${m} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h`;
  return `${Math.round(h / 24)} d`;
}

/**
 * Where a sensor's current value sits against its thresholds. Direction is
 * inferred from the thresholds themselves: critical above warning means
 * "higher is worse", below means "lower is worse" (e.g. free disk space).
 * Falls back to the provider status when no thresholds are set.
 */
export function sensorState(s: VerdictSensor): SensorState {
  const v = s.current_value;
  const w = s.warning_threshold;
  const c = s.critical_threshold;
  if (v !== null && (w !== null || c !== null)) {
    const higherWorse = w === null || c === null ? true : c >= w;
    const breach = (limit: number | null) => limit !== null && (higherWorse ? v >= limit : v <= limit);
    if (breach(c)) return "critical";
    if (breach(w)) return "warning";
    return "ok";
  }
  const st = s.status.toLowerCase();
  if (DOWN.has(st)) return "critical";
  if (["warning", "degraded", "unusual"].includes(st)) return "warning";
  if (["up", "ok", "healthy"].includes(st)) return "ok";
  return "unknown";
}

/** 0..1 position of the value on a scale that ends at the critical limit (for the gauge bar). */
export function sensorFill(s: VerdictSensor): number | null {
  const v = s.current_value;
  const c = s.critical_threshold ?? s.warning_threshold;
  if (v === null || c === null || c === 0) return null;
  const w = s.warning_threshold;
  const higherWorse = w === null || s.critical_threshold === null ? true : s.critical_threshold >= w;
  const ratio = higherWorse ? v / c : c / Math.max(v, Number.EPSILON);
  return Math.max(0, Math.min(1, ratio));
}

export function buildHostVerdict(input: {
  device: VerdictDevice | null;
  sensors?: VerdictSensor[] | null;
  alerts?: VerdictAlert[] | null;
  interfaces?: VerdictInterface[] | null;
  now?: number;
}): HostVerdict {
  const now = input.now ?? Date.now();
  const reasons: VerdictReason[] = [];
  const d = input.device;
  const sensors = input.sensors ?? [];
  const alerts = input.alerts ?? [];
  const ifaces = input.interfaces ?? [];

  if (!d) return { tone: "neutral", headline: "Host not loaded", reasons };

  const down = DOWN.has(d.status.toLowerCase());
  const seenAge = age(d.last_seen_at, now);
  if (down) {
    reasons.push({
      id: "down",
      tone: "bad",
      text: `Reported ${d.status.toLowerCase()} by its monitoring source${seenAge !== null ? `, last seen ${humanAge(seenAge)} ago` : ", never seen"}.`,
      tab: "info",
    });
  }
  if (seenAge !== null && seenAge > STALE_HOST_MS) {
    reasons.push({
      id: "stale-host",
      tone: "warn",
      text: `No contact for ${humanAge(seenAge)} — the state shown may be out of date; check the collector before acting.`,
      tab: "info",
    });
  }

  const open = alerts.filter((a) => a.status !== "resolved");
  const crit = open.filter((a) => a.severity === "critical");
  const warn = open.filter((a) => a.severity === "warning");
  if (crit.length || warn.length) {
    const newest = [...crit, ...warn].sort((a, b) => b.last_occurred_at.localeCompare(a.last_occurred_at))[0];
    const parts = [crit.length && `${crit.length} critical`, warn.length && `${warn.length} warning`].filter(Boolean).join(" and ");
    reasons.push({
      id: "alerts",
      tone: crit.length ? "bad" : "warn",
      text: `${parts} alert${crit.length + warn.length > 1 ? "s" : ""} open; latest: “${newest.title}”.`,
      tab: "alerts",
    });
  }
  const flapping = open.filter((a) => a.occurrence_count >= FLAPPING_OCCURRENCES);
  if (flapping.length) {
    const top = [...flapping].sort((a, b) => b.occurrence_count - a.occurrence_count)[0];
    reasons.push({
      id: "flapping",
      tone: "info",
      text: `“${top.title}” has fired ${top.occurrence_count.toLocaleString("en-US")} times — likely flapping; tune its threshold or add a delay.`,
      tab: "alerts",
    });
  }

  const states = sensors.map((s) => ({ s, st: sensorState(s) }));
  const breaching = states.filter((x) => x.st === "critical" || x.st === "warning");
  if (breaching.length) {
    const worst = breaching.find((x) => x.st === "critical") ?? breaching[0];
    const v = worst.s.current_value;
    reasons.push({
      id: "sensors",
      tone: breaching.some((x) => x.st === "critical") ? "bad" : "warn",
      text: `${breaching.length} of ${sensors.length} sensor${sensors.length > 1 ? "s" : ""} outside limits; worst: ${worst.s.name}${v !== null ? ` at ${fmt(v)}${worst.s.unit ? ` ${worst.s.unit}` : ""}` : ""}.`,
      tab: "sensors",
    });
  }
  const stale = sensors.filter((s) => {
    const a = age(s.last_value_at, now);
    return a === null || a > STALE_SENSOR_MS;
  });
  if (sensors.length && stale.length) {
    reasons.push({
      id: "stale-sensors",
      tone: stale.length === sensors.length ? "warn" : "info",
      text: `${stale.length} of ${sensors.length} sensor${sensors.length > 1 ? "s have" : " has"} not reported in the last hour.`,
      tab: "sensors",
    });
  }

  const ifDown = ifaces.filter((i) => i.admin_status.toLowerCase() === "up" && i.oper_status.toLowerCase() !== "up");
  if (ifDown.length) {
    reasons.push({
      id: "interfaces",
      tone: "warn",
      text: `${ifDown.length} interface${ifDown.length > 1 ? "s are" : " is"} enabled but not passing traffic (${ifDown.slice(0, 3).map((i) => i.name).join(", ")}).`,
      tab: "interfaces",
    });
  }

  const tone: VerdictTone = reasons.some((r) => r.tone === "bad")
    ? "bad"
    : reasons.some((r) => r.tone === "warn")
      ? "warn"
      : d.status.toLowerCase() === "up" && (sensors.length > 0 || alerts.length > 0)
        ? "good"
        : reasons.length
          ? "warn"
          : "neutral";

  // A host out of contact makes every other reading historical: lead with it
  // and say so in the headline, so nobody acts on month-old alerts as if live.
  const staleHost = seenAge !== null && seenAge > STALE_HOST_MS;
  const headline = staleHost
    ? `Data is ${humanAge(seenAge)} old — verify collection before acting`
    : tone === "good"
      ? sensors.length
        ? `Healthy — all ${sensors.length} sensor${sensors.length > 1 ? "s" : ""} within limits`
        : "Healthy — no open alerts"
      : tone === "neutral"
        ? "Not enough telemetry to judge this host"
        : down
          ? "Host is down"
          : tone === "bad"
            ? "Needs attention now"
            : "Degraded — worth a look";

  const order = { bad: 0, warn: 1, info: 2 } as const;
  reasons.sort((a, b) => {
    if (staleHost && (a.id === "stale-host") !== (b.id === "stale-host")) return a.id === "stale-host" ? -1 : 1;
    return order[a.tone] - order[b.tone];
  });
  return { tone, headline, reasons };
}

export function fmt(v: number): string {
  const a = Math.abs(v);
  if (a >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (a >= 10) return v.toLocaleString("en-US", { maximumFractionDigits: 1 });
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export interface SeriesStats {
  latest: number;
  min: number;
  max: number;
  avg: number;
  count: number;
}

export function seriesStats(values: number[]): SeriesStats | null {
  if (!values.length) return null;
  let min = Infinity;
  let max = -Infinity;
  let sum = 0;
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
    sum += v;
  }
  return { latest: values[values.length - 1], min, max, avg: sum / values.length, count: values.length };
}

/**
 * Down-sample a time series (oldest first) to at most `buckets` points,
 * keeping each bucket's max so short spikes stay visible.
 */
export function downsample(points: { t: number; v: number }[], buckets: number): { t: number; v: number }[] {
  if (points.length <= buckets) return points;
  const size = points.length / buckets;
  const out: { t: number; v: number }[] = [];
  for (let i = 0; i < buckets; i++) {
    const slice = points.slice(Math.floor(i * size), Math.floor((i + 1) * size));
    if (!slice.length) continue;
    let peak = slice[0];
    for (const p of slice) if (p.v > peak.v) peak = p;
    out.push({ t: slice[Math.floor(slice.length / 2)].t, v: peak.v });
  }
  return out;
}
