import ConsumptionTracker from "@/components/Usage/ConsumptionTracker";
import PlanSelector from "@/components/Usage/PlanSelector";

export const metadata = {
  title: "Usage & Billing | HSDEEP CORE AI",
  description: "Multi-tenant consumption metering and subscription tier monitoring",
};

export default function UsagePage() {
  return (
    <div className="page-shell min-h-screen bg-gray-50" style={{ paddingBottom: "var(--copilot-bar-height)" }}>
      {/* Page Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">
                Usage &amp; Billing
              </h1>
              <p className="mt-1 text-sm text-gray-500">
                Monitor tenant consumption, track subscription tier limits, and
                view usage trends over time.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <PlanSelector />
        <ConsumptionTracker days={30} refreshIntervalMs={60_000} />
      </div>
    </div>
  );
}