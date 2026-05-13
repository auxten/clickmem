import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api";

interface UseApiOptions {
  pollMs?: number;
  enabled?: boolean;
  deps?: ReadonlyArray<unknown>;
}

interface UseApiResult<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  refresh: () => Promise<void>;
  setData: (next: T | null) => void;
}

/**
 * Tiny data-fetching hook with optional polling. We intentionally avoid
 * pulling react-query just for the dashboard — every page only needs the
 * three things in `UseApiResult`.
 */
export function useApi<T>(
  fn: () => Promise<T>,
  opts: UseApiOptions = {},
): UseApiResult<T> {
  const { pollMs, enabled = true, deps = [] } = opts;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(false);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const run = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fn();
      if (aliveRef.current) {
        setData(res);
        setError(null);
      }
    } catch (e) {
      if (aliveRef.current) setError(e as Error);
    } finally {
      if (aliveRef.current) setLoading(false);
    }
    // fn is intentionally not in deps; pages opt-in via `deps` so they can
    // depend on serialisable inputs rather than function identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!enabled) return;
    run();
    if (!pollMs || pollMs <= 0) return;
    const t = setInterval(run, pollMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, pollMs, run]);

  return { data, error, loading, refresh: run, setData };
}
