"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

/**
 * Back arrow for report detail sub-pages.
 *
 * The reports index route is `/reports` (it lives inside the `(dashboard)`
 * route group, so `dashboard` is NOT part of the URL). The old hardcoded
 * `<Link href="/dashboard/reports">` produced a 404 because no page backs
 * that path.
 *
 * We use `router.back()` so the browser history behaves consistently with the
 * in-app nav (Dashboard -> Reports -> detail -> Back should return to Reports,
 * and further Back returns to Dashboard). If there is no history to go back to
 * (e.g. the user deep-linked straight into a detail page), fall back to
 * navigating to `/reports` so we never land on a blank/broken state.
 */
export default function ReportsBackButton() {
  const router = useRouter();

  function handleBack() {
    if (window.history.length > 1) {
      router.back();
    } else {
      router.replace("/reports");
    }
  }

  return (
    <button
      type="button"
      onClick={handleBack}
      aria-label="Back to Reports"
      style={{
        color: "var(--cyan)",
        display: "inline-flex",
        background: "transparent",
        border: "none",
        padding: 0,
        cursor: "pointer",
      }}
    >
      <ArrowLeft size={20} />
    </button>
  );
}