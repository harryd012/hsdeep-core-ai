"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

/**
 * Back arrow for the usage overview page.
 *
 * The usage index route is `/usage` (it lives inside the `(dashboard)` route
 * group, so `dashboard` is NOT part of the URL). The old hardcoded
 * `<Link href="/dashboard/usage">` produced a 404 because no page backs that
 * path.
 *
 * We use `router.back()` so the browser history behaves consistently with the
 * in-app nav. If there is no history to go back to (e.g. the user deep-linked
 * straight into the page), fall back to navigating to `/dashboard` so we never
 * land on a blank/broken state.
 */
export default function UsageBackButton() {
  const router = useRouter();

  function handleBack() {
    if (window.history.length > 1) {
      router.back();
    } else {
      router.replace("/dashboard");
    }
  }

  return (
    <button
      type="button"
      onClick={handleBack}
      aria-label="Back to Dashboard"
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