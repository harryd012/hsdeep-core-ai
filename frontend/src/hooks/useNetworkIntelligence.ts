"use client";

import { useCallback, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  evaluateNetworkFlow,
  getNetworkScenario,
  listNetworkScenarios,
  type NetworkEvaluateResult,
} from "@/lib/api";
import {
  describeApiProblem,
  INVALID_RESPONSE,
  isValidEvaluation,
  isValidScenario,
  type ApiProblem,
} from "@/components/network-intelligence/status";

/**
 * useNetworkIntelligence — SHARED state for the Network Intelligence surfaces.
 *
 * Reuses the existing apiFetch client (lib/api.ts) and the existing React Query
 * cache. It intentionally does NOT poll: an evaluation only runs when an
 * operator explicitly asks for one, because the evaluation is advisory and must
 * never masquerade as continuous live telemetry.
 *
 * The last REAL evaluation is exposed through a tiny external store backed by
 * localStorage, so the compact /dashboard widget reports genuine values from the
 * most recent evaluation instead of fabricating any. With nothing stored it
 * honestly reports "READY — no evaluation executed yet".
 */

const STORAGE_KEY = "hsdeep_network_intelligence_last_evaluation";
const EVALUATION_EVENT = "network-intelligence:updated";

/** Cached snapshot so useSyncExternalStore sees a stable reference. */
let snapshotRaw: string | null = null;
let snapshotValue: NetworkEvaluateResult | null = null;

function parse(raw: string | null): NetworkEvaluateResult | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as NetworkEvaluateResult;
  } catch {
    return null;
  }
}

function getSnapshot(): NetworkEvaluateResult | null {
  const raw =
    typeof window === "undefined"
      ? null
      : window.localStorage.getItem(STORAGE_KEY);
  if (raw !== snapshotRaw) {
    snapshotRaw = raw;
    snapshotValue = parse(raw);
  }
  return snapshotValue;
}

function getServerSnapshot(): NetworkEvaluateResult | null {
  return null;
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  // Cross-tab (storage) and same-tab (custom event) notifications.
  function onStorage(e: StorageEvent) {
    if (e.key === STORAGE_KEY || e.key === null) onChange();
  }
  window.addEventListener("storage", onStorage);
  window.addEventListener(EVALUATION_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(EVALUATION_EVENT, onChange);
  };
}

function publish(result: NetworkEvaluateResult | null): void {
  if (typeof window === "undefined") return;
  try {
    if (result) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(result));
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    /* storage unavailable (private mode) — the UI degrades to READY. */
  }
  snapshotRaw = "___dirty___";
  window.dispatchEvent(new Event(EVALUATION_EVENT));
}

/** Reads the persisted evaluation WITHOUT ever triggering one. */
export function useStoredNetworkEvaluation(): NetworkEvaluateResult | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export const NETWORK_SCENARIO_FALLBACK = [
  "healthy",
  "wan-degraded",
  "security-violation",
  "combined",
] as const;

export function useNetworkIntelligence() {
  const scenariosQuery = useQuery({
    queryKey: ["network-intelligence", "scenarios"],
    queryFn: () => listNetworkScenarios(),
    refetchInterval: false,
    staleTime: 5 * 60_000,
  });

  const stored = useStoredNetworkEvaluation();
  const [scenario, setScenario] = useState<string>("combined");
  const [freshResult, setFreshResult] = useState<NetworkEvaluateResult | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<ApiProblem | null>(null);

  // Prefer the result of this session's evaluation; otherwise fall back to the
  // last persisted evaluation so a reload keeps the operator's context.
  const result = freshResult ?? stored;

  const scenarios = scenariosQuery.data?.scenarios?.length
    ? scenariosQuery.data.scenarios
    : [...NETWORK_SCENARIO_FALLBACK];

  const evaluate = useCallback(
    async (name?: string) => {
      const target = name ?? scenario;
      setEvaluating(true);
      setError(null);
      try {
        const input = await getNetworkScenario(target);
        if (!isValidScenario(input)) {
          // Never send a malformed scenario to the evaluator, and never render
          // a decision derived from one.
          setFreshResult(null);
          setError(INVALID_RESPONSE);
          return null;
        }
        const evaluated = await evaluateNetworkFlow({
          flow: input.flow,
          paths: input.paths,
        });
        if (!isValidEvaluation(evaluated)) {
          setFreshResult(null);
          setError(INVALID_RESPONSE);
          return null;
        }
        setFreshResult(evaluated);
        publish(evaluated);
        return evaluated;
      } catch (err) {
        setError(describeApiProblem(err));
        return null;
      } finally {
        setEvaluating(false);
      }
    },
    [scenario],
  );

  const clear = useCallback(() => {
    setFreshResult(null);
    publish(null);
  }, []);

  const clearError = useCallback(() => setError(null), []);

  return {
    scenarios,
    scenario,
    setScenario,
    result,
    evaluating,
    error,
    evaluate,
    clear,
    clearError,
    scenariosError: scenariosQuery.error,
  };
}
