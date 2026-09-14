import SubscriptionPageClient from "./page-client";

export const metadata = {
  title: "Subscription | HSDEEP CORE AI",
  description: "Choose and manage your subscription plan",
};

/**
 * SUBSCRIPTION — plan selection + current subscription status.
 *
 * Enhanced with animated usage bars, interactive plan comparison,
 * billing history, payment method management, and live trial countdown.
 */
export default function SubscriptionPage() {
  return <SubscriptionPageClient />;
}
