import { Suspense } from "react";

/**
 * /apm — Application Performance Monitoring over OpenTelemetry traces.
 * The pages read the shared time range from the URL (useSearchParams), which
 * needs a Suspense boundary; the app shell itself comes from (dashboard)/layout.
 */
export default function ApmLayout({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={null}>{children}</Suspense>;
}
