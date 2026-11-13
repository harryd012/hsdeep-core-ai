"use client";

import { useQuery } from "@tanstack/react-query";

import { getServiceTimeseries, type Window } from "@/lib/traces";

/**
 * A service's requests per bucket as a tiny line, errors as red ticks. Its
 * own read, cached per service and window; shown for the first rows only.
 */
export default function ServiceTrend({ service, window }: { service: string; window: Window }) {
  const q = useQuery({
    queryKey: ["apm", "timeseries", service, window],
    queryFn: () => getServiceTimeseries(service, window),
    staleTime: 60_000,
  });
  const points = q.data?.points ?? [];
  if (points.length < 2) return <span className="pk-muted">{q.isLoading ? "…" : "—"}</span>;
  const W = 120, H = 28;
  const max = Math.max(1, ...points.map((p) => p.requests));
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => H - 2 - (v / max) * (H - 4);
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${service} requests over the window`}>
      <title>{`Requests per ${Math.round((q.data?.bucket_seconds ?? 60) / 60)} min, peak ${max}`}</title>
      <path d={`M ${points.map((p, i) => `${x(i)} ${y(p.requests)}`).join(" L ")}`} fill="none" stroke="var(--ecc-accent)" strokeWidth={1.4} />
      {points.map((p, i) => (p.errors > 0 ? <line key={p.t} x1={x(i)} x2={x(i)} y1={H - 1} y2={H - 7} stroke="var(--ecc-bad)" strokeWidth={1.4} /> : null))}
    </svg>
  );
}
