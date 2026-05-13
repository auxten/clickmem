import { useEffect, useRef, useState } from "react";
import { api, EventRow } from "../api";

/**
 * Polls `/v1/events` every `intervalMs` (default 5s) and keeps a rolling tail.
 *
 * The server stamps each event with `created_at`; we re-issue the next request
 * with `since=<latest created_at>` to keep the response small. New events are
 * concatenated on top of the existing list and capped at `cap` rows.
 */
export interface UseEventsOptions {
  intervalMs?: number;
  cap?: number;
  kind?: string;
  agent?: string;
}

export interface UseEventsResult {
  events: EventRow[];
  error: Error | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useEvents(opts: UseEventsOptions = {}): UseEventsResult {
  const { intervalMs = 5000, cap = 100, kind, agent } = opts;
  const [events, setEvents] = useState<EventRow[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const sinceRef = useRef<string | undefined>(undefined);
  const aliveRef = useRef(true);

  const fetchOnce = async () => {
    try {
      const rows = await api.events({
        since: sinceRef.current,
        kind,
        agent,
        limit: 50,
      });
      if (!aliveRef.current) return;
      if (rows.length > 0) {
        // Server returns DESC by created_at; the first row is the newest.
        sinceRef.current = rows[0].created_at;
        setEvents((prev) => {
          const seen = new Set(prev.map((e) => e.id));
          const fresh = rows.filter((r) => !seen.has(r.id));
          const merged = [...fresh, ...prev];
          return merged.slice(0, cap);
        });
      }
      setError(null);
    } catch (e) {
      if (aliveRef.current) setError(e as Error);
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  };

  useEffect(() => {
    aliveRef.current = true;
    sinceRef.current = undefined;
    setEvents([]);
    setLoading(true);
    fetchOnce();
    const t = setInterval(fetchOnce, intervalMs);
    return () => {
      aliveRef.current = false;
      clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, cap, kind, agent]);

  return { events, error, loading, refresh: fetchOnce };
}
