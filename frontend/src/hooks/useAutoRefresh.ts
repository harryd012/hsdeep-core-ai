"use client";

import { useEffect, useRef, useState } from "react";

interface UseAutoRefreshOptions {
  intervalMs?: number;
  enabled?: boolean;
}

export function useAutoRefresh<T extends () => Promise<void>>(
  fetch: T,
  { intervalMs = 60_000, enabled = true }: UseAutoRefreshOptions = {}
) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const activeRef = useRef(true);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    let cancelled = false;
    activeRef.current = true;
    let currentTimer: NodeJS.Timeout | null = null;

    async function load() {
      try {
        setError(null);
        await fetch();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load data");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    if (enabled) {
      currentTimer = setInterval(() => {
        if (activeRef.current) {
          fetch().catch(() => {});
        }
      }, intervalMs);
      timerRef.current = currentTimer;
    }
    return () => {
      cancelled = true;
      activeRef.current = false;
      if (currentTimer) clearInterval(currentTimer);
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [enabled, intervalMs]);

  const retry = () => {
    setLoading(true);
    setError(null);
    fetch().catch((err) => {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }).finally(() => setLoading(false));
  };

  return { loading, error, retry };
}