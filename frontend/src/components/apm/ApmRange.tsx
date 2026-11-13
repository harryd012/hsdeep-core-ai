"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { Tabs } from "@/components/ui/PageKit";
import type { Window } from "@/lib/traces";

/**
 * The APM section's shared time range. It lives in the URL (`?range=6h`), so
 * moving between APM pages keeps it and a link carries it. The window is
 * anchored when the page loads and moves forward on Refresh.
 */

export const APM_RANGES = [
  { key: "1h", label: "1 h", ms: 3_600_000 },
  { key: "6h", label: "6 h", ms: 6 * 3_600_000 },
  { key: "24h", label: "24 h", ms: 24 * 3_600_000 },
  { key: "7d", label: "7 d", ms: 7 * 24 * 3_600_000 },
] as const;
export type ApmRangeKey = (typeof APM_RANGES)[number]["key"];

export function useApmRange() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/apm";
  const raw = params?.get("range");
  const range: ApmRangeKey = APM_RANGES.some((r) => r.key === raw) ? (raw as ApmRangeKey) : "1h";
  const [anchor, setAnchor] = useState(() => Date.now());

  const window = useMemo<Window>(() => {
    const ms = APM_RANGES.find((r) => r.key === range)!.ms;
    return { since: new Date(anchor - ms).toISOString(), until: new Date(anchor).toISOString() };
  }, [range, anchor]);

  function setRange(next: ApmRangeKey) {
    const q = new URLSearchParams(params?.toString() ?? "");
    if (next === "1h") q.delete("range");
    else q.set("range", next);
    setAnchor(Date.now());
    const s = q.toString();
    router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
  }

  /** `href` with the current range kept. */
  function withRange(href: string): string {
    return range === "1h" ? href : `${href}${href.includes("?") ? "&" : "?"}range=${range}`;
  }

  return { range, window, setRange, refresh: () => setAnchor(Date.now()), withRange };
}

const SECTIONS = [
  { href: "/apm", label: "Services", match: (p: string) => p === "/apm" || p.startsWith("/apm/services") },
  { href: "/apm/errors", label: "Errors", match: (p: string) => p.startsWith("/apm/errors") },
  { href: "/traces", label: "Traces", match: () => false },
];

/** Section links, range and Refresh, for each APM page's header. */
export function ApmActions({ apm }: { apm: ReturnType<typeof useApmRange> }) {
  const pathname = usePathname() ?? "";
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <nav className="pk-tabs" aria-label="APM sections">
        {SECTIONS.map((s) => (
          <Link key={s.href} href={apm.withRange(s.href)} className="pk-tab" aria-current={s.match(pathname) ? "page" : undefined}
            aria-selected={s.match(pathname)} style={{ textDecoration: "none" }}>
            {s.label}
          </Link>
        ))}
      </nav>
      <Tabs<ApmRangeKey> value={apm.range} label="Time range" onChange={apm.setRange} tabs={APM_RANGES.map((r) => ({ key: r.key, label: r.label }))} />
      <button type="button" className="pk-button" onClick={apm.refresh}>Refresh</button>
    </div>
  );
}
