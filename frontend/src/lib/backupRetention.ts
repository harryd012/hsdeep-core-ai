/**
 * Retention simulation for backup policies (grandfather-father-son tiers).
 *
 * Pure arithmetic over the policy's schedule and retention tiers: how far
 * back restore points reach and roughly how many exist once the policy has
 * run long enough (steady state). It does not estimate storage or cost: the
 * size of each restore point depends on the data's change rate, which the
 * providers do not report per point. This is backup recovery-point retention
 * only - unrelated to Log Analytics data retention.
 */

export interface RetentionTiers {
  /** Days daily restore points are kept; null = tier not used. */
  daily_days: number | null;
  weekly_weeks: number | null;
  monthly_months: number | null;
  yearly_years: number | null;
}

export interface RetentionEstimate {
  /** Age of the oldest restore point once the policy is in steady state, days. */
  horizon_days: number;
  /** Steady-state restore points per tier, counting a point once even when it is in several tiers. */
  daily: number;
  weekly: number;
  monthly: number;
  yearly: number;
  total: number;
}

const DAYS_PER_MONTH = 365 / 12;

/** Steady-state estimate for a policy that backs up once a day (`daily`) or once a week (`weekly`). */
export function estimateRetention(t: RetentionTiers, frequency: "daily" | "weekly" = "daily"): RetentionEstimate {
  const d = frequency === "daily" ? Math.max(0, t.daily_days ?? 0) : 0;
  const w = Math.max(0, t.weekly_weeks ?? 0);
  const m = Math.max(0, t.monthly_months ?? 0);
  const y = Math.max(0, t.yearly_years ?? 0);

  // Each longer tier only adds the points older than what the shorter tiers already keep.
  const daily = d;
  const reachDaily = d;
  const weekly = Math.max(0, w - Math.floor(reachDaily / 7));
  const reachWeekly = Math.max(reachDaily, w * 7);
  const monthly = Math.max(0, m - Math.floor(reachWeekly / DAYS_PER_MONTH));
  const reachMonthly = Math.max(reachWeekly, m * DAYS_PER_MONTH);
  const yearly = Math.max(0, y - Math.floor(reachMonthly / 365));
  const horizon = Math.max(reachMonthly, y * 365);

  return { horizon_days: Math.round(horizon), daily, weekly, monthly, yearly, total: daily + weekly + monthly + yearly };
}

/** "90 days", "12 weeks", "7 years": the largest whole unit. */
export function horizonLabel(days: number): string {
  if (days <= 0) return "none";
  if (days >= 365 && days % 365 === 0) return `${days / 365} year${days === 365 ? "" : "s"}`;
  if (days >= 365) return `${(days / 365).toFixed(1)} years`;
  if (days >= 60) return `${Math.round(days / DAYS_PER_MONTH)} months`;
  return `${days} day${days === 1 ? "" : "s"}`;
}
