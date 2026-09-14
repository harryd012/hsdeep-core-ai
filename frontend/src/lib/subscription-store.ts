/**
 * Shared subscription store — the single source of truth for billing state.
 *
 * Every surface that displays plan info (dashboard Subscription stat card,
 * /usage and /subscription panels) must consume THIS store rather than
 * firing independent getSubscription() calls. Guarantees:
 *  - one in-flight request for all subscribers (dedup);
 *  - every notify is synchronous — consumers can never be left in a stale
 *    or permanently-"loading" state because another component's fetch failed;
 *  - plan mutations (select/cancel/change) push the fresh subscription back
 *    into the store so the dashboard card updates instantly, no refetch.
 */

import { ApiError } from "./api";
import { getSubscription, type SubscriptionWithPlan } from "./usage-api";

export type SubState = {
  subscription: SubscriptionWithPlan | null; // null = confirmed "no plan" (404)
  error: string | null;
};

let state: SubState = { subscription: null, error: null };
/** True until the FIRST attempt settles — drives "loading…" UIs. */
let pendingFirstLoad = true;

const listeners = new Set<(s: SubState) => void>();
let inflight: Promise<void> | null = null;

function notify() {
  const snapshot: SubState = { ...state };
  for (const fn of listeners) fn(snapshot);
}

async function runLoad(): Promise<void> {
  try {
    // A clean 404 means the tenant simply has no plan yet -> keep null but
    // clear the error so UIs show the friendly empty state.
    const sub = await getSubscription();
    state = { subscription: sub, error: null };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      state = { subscription: null, error: null };
    } else {
      const msg =
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : "Failed to load subscription";
      state = { subscription: null, error: msg };
    }
  } finally {
    pendingFirstLoad = false;
  }
  notify();
}

/** Load once per tick; concurrent callers share the same promise. */
export function refreshSubscription(): Promise<void> {
  if (!inflight) {
    inflight = runLoad().finally(() => { inflight = null; });
  }
  return inflight;
}

/** Pull-to-refresh style reload; bypasses dedup against a completed load. */
export function forceRefreshSubscription(): Promise<void> {
  if (!inflight) return refreshSubscription();
  return inflight;
}

/** Push an externally-confirmed subscription into the store (after select/cancel/change). */
export function setSubscription(sub: SubscriptionWithPlan | null): void {
  state = { subscription: sub, error: null };
  notify();
}

/** Subscribe to store updates. Returns the unsubscribe function. */
export function subscribeToSubscription(fn: (s: SubState) => void): () => void {
  listeners.add(fn);
  // Kick a load if this is the first subscriber ever AND nothing has ever
  // settled yet. Listeners always receive the snapshot synchronously too,
  // so there is never a window where a consumer waits on nothing.
  if (pendingFirstLoad && !inflight) void refreshSubscription();
  fn({ ...state });
  return () => { listeners.delete(fn); };
}
