"use client";

import { Suspense } from "react";
import ExecutiveCommandCenter from "@/components/command-center/ExecutiveCommandCenter";
import "@/app/styles/executive-command-center.css";

/**
 * /dashboard — the Executive Command Center (ECC-1): one screen answering
 * "is anything on fire, where and how bad, and are we responding well".
 *
 * The deeper NOC / SOC / cloud / AI / topology panels that used to stack
 * below are not deleted — they live on the pages that own them
 * (/infrastructure, /soc-analysis, /portal, /ai-copilot) and are reachable
 * from the sidebar and from every card's drill-down.
 *
 * Suspense is required because the global filters live in the URL
 * (useSearchParams).
 */
export default function DashboardPage() {
  return (
    <Suspense fallback={<div className="ecc" aria-busy="true" />}>
      <ExecutiveCommandCenter />
    </Suspense>
  );
}
