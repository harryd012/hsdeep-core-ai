# Requirements Document

## Introduction

HSDEEP CORE AI is an enterprise multi-tenant NOC/SOC SaaS platform with an existing billing module (FastAPI + SQLAlchemy backend, Next.js frontend, Stripe Billing integration). The current module supports plan listing, subscription lifecycle, trial creation, entitlement computation, usage metering, and a partial Stripe Checkout / Customer Portal integration. However it lacks invoice display, payment method management, billing profiles, usage visualization, add-on purchasing, multi-currency support, and a complete six-tier plan catalog; the frontend subscription page also reports real API failures ("Unable to load subscription information", "Unable to load usage limits", "Unable to load plan usage", "Unable to load billing history", "Unable to load current plan").

This feature upgrades the existing billing module into a complete Enterprise Subscription & Billing Management Center. It extends the existing `Billing_API`, `Billing_Service`, `Plan_Model`, and `Subscription_Model` rather than duplicating them, and introduces new models for invoices, billing profiles, add-ons, and payment method references. All prices, limits, and entitlements remain database-driven and server-authoritative. Card data is never stored in the application database; all card flows are hosted by Stripe.

The scope covers eight billing center sections (Subscription Overview, Plans & Pricing, Usage & Limits, Add-ons & Capacity, Payment Methods, Billing History, Billing Profile, Subscription Management) plus cross-cutting concerns (Stripe integration, entitlement enforcement, subscription lifecycle, multi-currency support).

## Glossary

- **Billing_API**: The FastAPI route layer under `app/api/routes/billing.py` and related routes, exposing endpoints under the `/api/billing/*` prefix.
- **Billing_Service**: The application service layer (`BillingService`) that owns billing business rules including plan changes, trial creation, subscription state transitions, and entitlement computation.
- **Billing_Frontend**: The Next.js billing UI components and pages under `frontend/src/app/(dashboard)/subscription/` and supporting components.
- **Subscription_Store**: The Zustand store (`subscription-store.ts`) that is the single source of truth for subscription state across the frontend.
- **Entitlements_Guard**: The FastAPI dependencies (`require_active_subscription`, `require_entitlement`, `require_feature`) and supporting functions in `app/core/entitlements.py` that enforce plan limits and feature gating server-side.
- **Webhook_Handler**: The Stripe webhook endpoint (`app/api/routes/stripe_webhook.py`) that processes payment-provider events and syncs subscription state.
- **Billing_Scheduler**: The AsyncIOScheduler billing jobs (`app/scheduler/billing_jobs.py`) including trial expiry and reconciliation jobs.
- **Audit_Service**: The audit logging service that records all billing and entitlement actions.
- **Stripe_Checkout**: The Stripe-hosted checkout session flow for collecting payment and creating subscriptions.
- **Stripe_Customer_Portal**: The Stripe-hosted self-service billing portal for managing subscriptions, payment methods, and invoices.
- **Plan_Catalog**: The database-backed collection of subscription plan tiers (`Plan` rows) that is the single source of truth for pricing and entitlements.
- **Subscription_Model**: The database model (`Subscription`) linking a tenant to a plan and tracking the billing lifecycle.
- **Plan_Model**: The database model (`Plan`) defining a subscription tier's pricing, limits, and features.
- **Invoice_Model**: The database model representing a billing invoice synced from Stripe, storing invoice number, dates, amounts, currency, tax, and payment status (no card data).
- **Billing_Profile_Model**: The database model representing tenant billing profile data (company legal name, billing address, tax ID, billing email, finance contact, PO reference).
- **Add_On_Model**: The database model representing a purchasable capacity add-on (device packs, sensor packs, seats, API capacity, extended retention, advanced AI/RCA, premium support) that augments a plan's base limits.
- **Payment_Method_Model**: The database model storing a reference to a Stripe payment method (brand, last four digits, expiry, Stripe payment method ID) with no raw card data.
- **Event_Ledger**: The database table (`processed_stripe_events`) recording successfully processed webhook event IDs for idempotent processing.
- **Usage_Counter**: The database model (`UsageCounter`) storing metered usage events per tenant per billing period.
- **Tenant**: A multi-tenant boundary; all subscription, billing, and usage data is tenant-scoped.
- **Platform_Admin**: A superuser administrator with access to platform-level billing management endpoints (gated behind `require_admin`).
- **Owner**: The primary tenant role with `billing.write` permission for subscription management.
- **Plan_Tier**: A machine-readable plan key (free_trial, starter, professional, business, enterprise, custom).

## Requirements

### Requirement 1: Plan Catalog and Pricing

**User Story:** As a tenant administrator, I want to view a complete plan catalog with pricing and feature comparison, so that I can choose the right subscription tier for my organization.

#### Acceptance Criteria

1. THE Plan_Catalog SHALL include the tiers `free_trial`, `starter`, `professional`, `business`, `enterprise`, and `custom` with their respective pricing, resource limits, and feature entitlements.
2. WHEN a tenant administrator requests the plan catalog, THE Billing_API SHALL return all public plans with limits, features, pricing for both monthly and annual intervals, and Stripe price IDs resolved from the database.
3. WHERE a billing interval toggle is selected, THE Billing_API SHALL return plan pricing for the selected interval (`month` or `year`) without requiring a separate request per interval.
4. WHEN the Billing_API returns plan data, THE Billing_API SHALL resolve all prices and entitlement limits from the Plan_Catalog, not from client-supplied values.
5. THE Billing_Service SHALL seed all six plan tiers idempotently at startup, syncing catalog fields for existing tiers without modifying Stripe price IDs or subscriptions referencing the plan.
6. WHEN the `custom` plan tier is requested, THE Billing_API SHALL return a `contact_sales` response and SHALL NOT activate the plan through self-service checkout.

### Requirement 2: Subscription Overview

**User Story:** As a tenant administrator, I want to view my current subscription status, billing interval, next billing date, trial expiration, amount due, and payment status, so that I understand my account's billing state at a glance.

#### Acceptance Criteria

1. WHEN a tenant administrator requests the subscription endpoint, THE Billing_API SHALL return the current subscription with plan name, plan tier, status, billing interval, current period start, current period end, trial end date, and cancellation date.
2. WHILE a subscription is in the `trialing` status, THE Billing_API SHALL include the trial end date and the number of days remaining in the trial.
3. WHEN the Billing_API returns subscription data, THE Billing_API SHALL include the payment status derived from the subscription status (`active`, `past_due`, `suspended`, `canceled`).
4. IF a subscription has no Stripe customer linked, THEN THE Billing_API SHALL return the subscription with `stripe_customer_id` set to null without raising an error.
5. WHEN the Billing_Frontend loads the subscription overview, THE Subscription_Store SHALL fetch the subscription from the Billing_API and display the real server-returned data, not hardcoded sample data.
6. IF the Billing_API returns a 404 for the subscription endpoint, THEN THE Billing_Frontend SHALL display a friendly "no active subscription" state without showing an error banner.

### Requirement 3: Usage and Limits Visualization

**User Story:** As a tenant administrator, I want to see live consumption with progress indicators for seats, devices, sensors, monitoring sources, API requests, alerts, and data retention, so that I can monitor my resource usage against plan limits.

#### Acceptance Criteria

1. WHEN a tenant administrator requests the usage endpoint, THE Billing_API SHALL return current usage for seats, devices, sensors, monitoring sources, API requests, alerts, and collector runs against the current plan's limits.
2. THE Billing_API SHALL compute each resource's usage percentage as `used / limit * 100` and include an `over_limit` flag when usage equals or exceeds the limit.
3. WHEN the Billing_API returns usage data, THE Billing_API SHALL include configurable warning thresholds (50%, 75%, 90%, 100%) per tenant, defaulting to those values when not explicitly configured.
4. THE Billing_Frontend SHALL render usage progress bars that reflect the server-returned usage percentage and `over_limit` flag without computing percentages client-side.
5. IF the Billing_API fails to return usage data, THEN THE Billing_Frontend SHALL display the error message returned by the API and SHALL NOT substitute hardcoded usage values.
6. WHEN the Billing_Service computes entitlements, THE Billing_Service SHALL count live inventory (devices, sensors, monitoring sources, active users) from the database and metered usage (API calls, alerts, collector runs) from the Usage_Counter for the current billing period.

### Requirement 4: Add-ons and Capacity Purchasing

**User Story:** As a tenant administrator, I want to purchase additional capacity packs (devices, sensors, seats, API capacity, extended retention, advanced AI/RCA, premium support), so that I can scale resources beyond my plan's base limits.

#### Acceptance Criteria

1. WHEN a tenant administrator requests the add-on catalog, THE Billing_API SHALL return all available add-ons with their names, descriptions, pricing, and the resource they augment.
2. WHEN a tenant administrator purchases an add-on, THE Billing_API SHALL create a Stripe Checkout session for the add-on and SHALL return a `checkout_required` response with the checkout URL.
3. WHEN the Webhook_Handler receives a `checkout.session.completed` event for an add-on purchase, THE Webhook_Handler SHALL record the add-on against the tenant's subscription and SHALL apply the capacity increase to the tenant's effective limits.
4. THE Add_On_Model SHALL store the add-on name, tier, description, price, the resource it augments, the capacity increment, and the Stripe price ID, all resolved from the database.
5. WHEN the Billing_Service computes entitlements for a tenant, THE Billing_Service SHALL add the capacity from purchased add-ons to the base plan limits before evaluating usage against limits.
6. IF an add-on purchase webhook fails to process, THEN THE Webhook_Handler SHALL return a 500 status so Stripe retries the event, and SHALL NOT record the event as processed in the Event_Ledger.

### Requirement 5: Payment Methods Management

**User Story:** As a tenant administrator, I want to add, update, and remove payment methods with card brand, last four digits, and payment failure status, so that I can manage how my organization pays for the subscription.

#### Acceptance Criteria

1. WHEN a tenant administrator requests the payment methods endpoint, THE Billing_API SHALL return all payment methods linked to the tenant's Stripe customer, including card brand, last four digits, expiry month and year, and whether the method is the default.
2. WHEN a tenant administrator adds a payment method, THE Billing_API SHALL create a Stripe SetupIntent and SHALL return the client secret for the Stripe-hosted card collection flow.
3. THE Payment_Method_Model SHALL store only a reference to the Stripe payment method (brand, last four digits, expiry, Stripe payment method ID) and SHALL NOT store card numbers, CVC codes, or full PANs.
4. WHEN the Webhook_Handler receives a `payment_method.attached` event, THE Webhook_Handler SHALL record the payment method reference in the Payment_Method_Model.
5. WHEN the Webhook_Handler receives a `payment_method.detached` event, THE Webhook_Handler SHALL remove the corresponding payment method reference from the Payment_Method_Model.
6. IF a payment method is the default and the tenant attempts to remove it, THEN THE Billing_API SHALL reject the removal with a 409 status and a message instructing the tenant to set another method as default first.
7. WHEN a payment failure occurs, THE Billing_API SHALL expose the failure status on the subscription and payment method so the Billing_Frontend can display a payment failure warning.

### Requirement 6: Billing History and Invoices

**User Story:** As a tenant administrator, I want to view my billing history with invoice number, date, amount, currency, tax, payment status, and download invoice PDFs, so that I can track and reconcile my payments.

#### Acceptance Criteria

1. WHEN a tenant administrator requests the billing history endpoint, THE Billing_API SHALL return a paginated list of invoices for the tenant, each with invoice number, date, amount, currency, tax, payment status, and a download URL.
2. WHEN the Webhook_Handler receives an `invoice.paid` event, THE Webhook_Handler SHALL create or update an Invoice_Model row with the invoice number, dates, amounts, currency, tax, and `paid` status.
3. WHEN the Webhook_Handler receives an `invoice.payment_failed` event, THE Webhook_Handler SHALL record the invoice with `payment_failed` status and SHALL flip the subscription to `past_due`.
4. WHEN the Webhook_Handler receives an `invoice.created` or `invoice.finalized` event, THE Webhook_Handler SHALL create or update the Invoice_Model row with the draft or finalized status respectively.
5. WHEN a tenant administrator requests to download an invoice PDF, THE Billing_API SHALL return a URL to the Stripe-hosted invoice PDF and SHALL NOT generate or store the PDF in the application database.
6. WHEN the Billing_API returns billing history, THE Billing_API SHALL include credit notes associated with each invoice where applicable.
7. IF the Billing_API cannot retrieve invoices for a tenant (no Stripe customer linked), THEN THE Billing_API SHALL return an empty list with a 200 status rather than an error.

### Requirement 7: Billing Profile Management

**User Story:** As a tenant administrator, I want to manage my billing profile (company legal name, billing address, country, currency, tax ID, billing email, finance contact, PO reference), so that my invoices are addressed correctly.

#### Acceptance Criteria

1. WHEN a tenant administrator requests the billing profile endpoint, THE Billing_API SHALL return the tenant's billing profile with company legal name, billing address, country, currency, tax ID, billing email, finance contact, and PO reference.
2. WHEN a tenant administrator updates the billing profile, THE Billing_API SHALL persist the changes to the Billing_Profile_Model and SHALL sync the company name and address to the Stripe customer.
3. THE Billing_Profile_Model SHALL store one billing profile per tenant, linked by `tenant_id`.
4. IF a tenant has no billing profile, THEN THE Billing_API SHALL return an empty profile with default values and a 200 status, not an error.
5. WHEN the Webhook_Handler receives a `customer.updated` event, THE Webhook_Handler SHALL sync changes made in the Stripe Customer Portal back to the Billing_Profile_Model.
6. WHEN the Billing_Service updates a billing profile, THE Billing_Service SHALL record the change via the Audit_Service with old and new values.

### Requirement 8: Subscription Management Actions

**User Story:** As a tenant administrator, I want to upgrade, downgrade, renew, cancel, schedule cancellation, reactivate, and change my billing cycle, so that I can manage my subscription lifecycle as my needs change.

#### Acceptance Criteria

1. WHEN a tenant administrator upgrades to a higher-tier plan, THE Billing_API SHALL create a Stripe Checkout session and SHALL apply the new plan only after the Webhook_Handler confirms payment.
2. WHEN a tenant administrator downgrades to a lower-tier plan, THE Billing_Service SHALL check whether the tenant exceeds the new plan's limits and SHALL set `over_limit_blocked` to block new resource creation if so.
3. WHEN a tenant administrator cancels a subscription, THE Billing_API SHALL transition the subscription through the state machine to `canceled` and SHALL NOT delete the tenant's data.
4. WHEN a tenant administrator schedules a cancellation, THE Billing_API SHALL set the cancellation to take effect at the end of the current billing period and SHALL keep the subscription active until that date.
5. WHEN a tenant administrator requests reactivation of a canceled subscription, THE Billing_API SHALL transition the subscription from `canceled` to `active` only if the `canceled` status allows reactivation within a configurable window.
6. WHEN a tenant administrator changes the billing cycle (monthly to annual or vice versa), THE Billing_API SHALL create a Stripe Checkout session for the new interval and SHALL update the subscription's billing interval after webhook confirmation.
7. WHEN any subscription management action is performed, THE Billing_Service SHALL record the action via the Audit_Service with the actor, old state, and new state.
8. IF a requested subscription transition is not allowed by the state machine, THEN THE Billing_API SHALL reject the request with a 409 status and a descriptive message.

### Requirement 9: Stripe Integration and Webhook Processing

**User Story:** As a platform operator, I want robust Stripe integration with idempotent webhook processing, signature verification, out-of-order event handling, and an event ledger, so that subscription state stays consistent with the payment provider.

#### Acceptance Criteria

1. WHEN the Webhook_Handler receives a Stripe event, THE Webhook_Handler SHALL verify the webhook signature using the configured secret before processing any event data.
2. IF the webhook signature verification fails, THEN THE Webhook_Handler SHALL reject the request with a 400 status and SHALL record the rejection via the Audit_Service.
3. WHEN the Webhook_Handler successfully processes an event, THE Webhook_Handler SHALL record the event ID in the Event_Ledger only after the handler succeeds.
4. WHEN the Webhook_Handler receives a duplicate event ID already present in the Event_Ledger, THE Webhook_Handler SHALL return a 200 status without reprocessing the event.
5. WHEN the Webhook_Handler receives an event type it does not handle, THE Webhook_Handler SHALL return a 200 status and SHALL NOT return an error that would trigger Stripe retries.
6. WHEN a webhook handler raises an exception during processing, THE Webhook_Handler SHALL roll back the transaction and return a 500 status so Stripe retries the event.
7. THE Webhook_Handler SHALL handle the event types `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.created`, `invoice.finalized`, `invoice.paid`, `invoice.payment_failed`, `customer.updated`, `payment_method.attached`, and `payment_method.detached`.
8. WHEN events arrive out of order (for example, `invoice.paid` before `checkout.session.completed`), THE Webhook_Handler SHALL process each event independently and SHALL NOT corrupt subscription state.
9. WHEN the Webhook_Handler receives a `customer.subscription.created` event, THE Webhook_Handler SHALL link the Stripe subscription to the tenant and SHALL set the subscription status based on the Stripe status mapping.

### Requirement 10: Server-Side Entitlement Enforcement

**User Story:** As a platform operator, I want all entitlement and limit checks enforced server-side, so that no client can bypass plan limits or access features it has not paid for.

#### Acceptance Criteria

1. WHEN a tenant attempts to create a resource that would exceed a plan limit, THE Entitlements_Guard SHALL reject the request with a 402 status and an upgrade call-to-action payload containing the resource, limit, used count, and plan name.
2. WHEN a subscription is flagged `over_limit_blocked` after a downgrade, THE Entitlements_Guard SHALL block new resource creation with a 402 status and SHALL NOT delete or disable existing resources.
3. WHEN a tenant's subscription is `suspended` or `canceled`, THE Entitlements_Guard SHALL block write operations with a 402 status and SHALL allow read operations so the tenant can view its data and reach the billing pages.
4. WHILE a subscription is `past_due`, THE Entitlements_Guard SHALL allow write operations during the configurable grace period so the tenant can resolve payment.
5. WHEN the Entitlements_Guard denies an operation, THE Entitlements_Guard SHALL record the denial via the Audit_Service with the tenant ID, resource, and error code.
6. WHEN a plan change occurs (upgrade or via Stripe Customer Portal), THE Billing_Service SHALL sync the tenant's entitlements to the new plan's limits and features.
7. THE Entitlements_Guard SHALL never trust client-supplied limit values and SHALL always re-read the server-side subscription and plan state for every enforcement decision.
8. WHERE a plan feature is checked, THE Entitlements_Guard SHALL evaluate the feature against the plan's `features_json` and SHALL deny access when the feature is not present or explicitly disabled.

### Requirement 11: Subscription Lifecycle Management

**User Story:** As a platform operator, I want a complete subscription lifecycle with configurable trial duration, grace periods, payment retry policy, cancellation policy, renewal reminders, and failed payment notifications, so that the billing system handles every state transition predictably.

#### Acceptance Criteria

1. THE Subscription_Model SHALL support the statuses `trialing`, `active`, `past_due`, `unpaid`, `paused`, `canceled`, `incomplete`, and `expired`.
2. WHEN a trial subscription reaches its trial end date, THE Billing_Scheduler SHALL transition the subscription to `past_due` and SHALL allow a configurable grace period before further action.
3. WHEN a `past_due` subscription exceeds the configurable grace period without payment, THE Billing_Scheduler SHALL transition the subscription to `suspended`, which blocks write operations via the Entitlements_Guard.
4. WHEN a subscription is canceled, THE Billing_Service SHALL set `canceled_at` to the cancellation timestamp and SHALL NOT delete the tenant's data, subscriptions, or resources.
5. WHEN a subscription is `paused`, THE Entitlements_Guard SHALL block write operations and SHALL allow read operations.
6. WHEN a subscription is `expired`, THE Billing_Scheduler SHALL transition it from `incomplete` to `expired` if checkout never completes within the configurable timeout.
7. THE Billing_Scheduler SHALL send renewal reminder notifications at configurable intervals before the billing period end date.
8. THE Billing_Scheduler SHALL send failed payment notifications when a subscription transitions to `past_due`.
9. WHEN a subscription downgrades and the downgrade is scheduled for the next billing period, THE Billing_Service SHALL apply the downgrade at the period end date and SHALL keep the current plan active until then.
10. WHEN the Billing_Scheduler transitions a subscription status, the Billing_Scheduler SHALL validate the transition against the state machine and SHALL skip invalid transitions with a log entry rather than corrupting state.

### Requirement 12: Multi-Currency Support

**User Story:** As a tenant administrator, I want to view and pay in my local currency (USD, INR, EUR, GBP, AUD, CAD), so that I can understand pricing and be billed in a familiar currency.

#### Acceptance Criteria

1. THE Plan_Model SHALL support Stripe price IDs for each supported currency, with separate price IDs per currency per billing interval.
2. WHEN a tenant selects a billing currency, THE Billing_API SHALL return plan prices in the selected currency using the corresponding Stripe price IDs from the database.
3. WHEN the Billing_API creates a Stripe Checkout session, THE Billing_API SHALL use the price ID corresponding to the tenant's selected currency and billing interval.
4. WHEN the Billing_API returns invoices, THE Billing_API SHALL include the currency code for each invoice as synced from Stripe.
5. WHEN a tenant changes currency, THE Billing_API SHALL update the Stripe customer's preferred currency and SHALL use the new currency for subsequent checkout sessions.
6. THE Billing_API SHALL support the currencies USD, INR, EUR, GBP, AUD, and CAD, each backed by Stripe price IDs configured in the Plan_Catalog.

### Requirement 13: Promo Codes and Coupons

**User Story:** As a tenant administrator, I want to apply promo codes and coupons during checkout, so that I can receive discounts on my subscription.

#### Acceptance Criteria

1. WHEN a tenant administrator provides a promo code during plan selection, THE Billing_API SHALL validate the promo code against Stripe coupons and SHALL include the coupon in the Stripe Checkout session if valid.
2. IF a promo code is invalid or expired, THEN THE Billing_API SHALL reject the promo code with a 422 status and a descriptive message.
3. WHEN the Webhook_Handler processes a checkout with an applied coupon, THE Webhook_Handler SHALL record the discount on the Invoice_Model row for the resulting invoice.
4. WHEN the Billing_API returns the plan catalog, THE Billing_API SHALL not expose internal coupon codes but SHALL allow the tenant to enter a code at checkout.

### Requirement 14: Frontend Billing Center Integration

**User Story:** As a tenant administrator, I want a unified billing center in the frontend that displays all eight billing sections with real data, so that I can manage my subscription without encountering broken integrations or fake data.

#### Acceptance Criteria

1. WHEN the Billing_Frontend loads any billing section, THE Subscription_Store and billing API client SHALL fetch real data from the Billing_API and SHALL NOT substitute hardcoded sample data for failed requests.
2. WHEN the Billing_API returns an error for a billing endpoint, THE Billing_Frontend SHALL display the error message returned by the API and SHALL offer a retry action.
3. WHEN a tenant administrator performs a subscription action (select, change, cancel, reactivate), THE Subscription_Store SHALL update with the server-returned subscription state and SHALL notify all subscribed components synchronously.
4. WHEN the Billing_Frontend renders plan pricing, usage limits, or entitlements, THE Billing_Frontend SHALL use values returned by the Billing_API and SHALL NOT hardcode plan prices or limits in the frontend code.
5. WHEN the Billing_Frontend creates a Stripe Checkout or Customer Portal session, THE Billing_Frontend SHALL redirect to the URL returned by the Billing_API and SHALL NOT construct Stripe URLs client-side.
6. IF a billing endpoint returns 501 (Stripe not configured), THEN THE Billing_Frontend SHALL display a message indicating that billing requires configuration and SHALL NOT show fake checkout flows.
7. WHEN the Billing_Frontend displays the Stripe Customer Portal link, THE Billing_Frontend SHALL obtain the portal URL from the Billing_API and SHALL NOT embed Stripe secrets or customer IDs in the frontend.
