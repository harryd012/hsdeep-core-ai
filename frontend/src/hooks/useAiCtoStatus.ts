'use client';

import { useEffect, useState } from 'react';
import { getAiCtoStatus, AiCtoStatus } from '@/lib/api';

const POLL_MS = 60_000; // 1 minute

/**
 * Hook that fetches the AI CTO + AI inference status from the backend.
 *
 * Returns the real-time status from `/api/health/ai-cto`.  The hook polls
 * the backend every minute so the dashboard always reflects the latest
 * dependency state without relying on manual refreshes.
 */
export function useAiCtoStatus() {
  const [data, setData] = useState<AiCtoStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetch() {
      setLoading(true);
      setError(null);
      try {
        const d = await getAiCtoStatus();
        if (!cancelled) setData(d);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetch();
    const id = setInterval(fetch, POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return { data, loading, error };
}
