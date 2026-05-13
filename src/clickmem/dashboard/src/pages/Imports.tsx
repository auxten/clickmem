import { useMemo, useState } from "react";
import { Import, RefreshCw } from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { Pill } from "../components/Pill";
import { SparkLine } from "../components/SparkLine";
import { TrafficLight } from "../components/TrafficLight";
import { useToast } from "../components/Toast";
import { formatNumber, fromNow } from "../lib/format";

interface Props {
  refreshTick: number;
}

/**
 * Per-adapter import view. The server's REST surface today exposes adapter
 * activity (`/v1/agents/.../activity`) and lifecycle hooks; a dedicated
 * `/v1/imports/run` endpoint is on the Phase 6 backlog, so the "Run import"
 * button currently re-uses the test endpoint and shows the latest raw landings
 * for each adapter. See gap notes in the Phase 7 summary.
 */
export default function ImportsPage({ refreshTick }: Props) {
  const toast = useToast();
  const agentsQ = useApi(() => api.listAgents(), { deps: [refreshTick] });
  const rawQ = useApi(() => api.getRaw({ last: 200 }), { deps: [refreshTick] });

  const perAgent = useMemo(() => {
    const buckets: Record<string, { count: number; latest: string }> = {};
    for (const r of rawQ.data || []) {
      const a = r.agent || "(unknown)";
      buckets[a] = buckets[a] || { count: 0, latest: "" };
      buckets[a].count += 1;
      if (!buckets[a].latest || r.created_at > buckets[a].latest) {
        buckets[a].latest = r.created_at;
      }
    }
    return buckets;
  }, [rawQ.data]);

  const [busy, setBusy] = useState("");

  const runImport = async (name: string) => {
    setBusy(name);
    try {
      const r = await api.testAgent(name);
      toast.push(
        "info",
        `Import sweep for ${name}`,
        r.message || "adapter import API not wired yet (see Phase 6)",
      );
    } catch (e) {
      toast.push("error", `Import for ${name} failed`, (e as Error).message);
    } finally {
      setBusy("");
    }
  };

  if (agentsQ.loading && !agentsQ.data) {
    return (
      <Card>
        <LoadingShimmer lines={6} />
      </Card>
    );
  }
  if (!agentsQ.data || agentsQ.data.length === 0) {
    return (
      <Card>
        <Empty title="No adapters" description="The adapter registry is empty." />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card padded={false}>
        <ul className="divide-y divide-line/70">
          {agentsQ.data.map((a) => {
            const data = perAgent[a.name] || { count: 0, latest: "" };
            return (
              <li key={a.name} className="flex flex-wrap items-center gap-4 px-5 py-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-text-primary">{a.label}</h3>
                    {a.experimental && <Pill tone="warn">experimental</Pill>}
                    <TrafficLight
                      status={
                        a.installed ? "online" : a.discovered ? "warn" : "unknown"
                      }
                      label={a.installed ? "installed" : a.discovered ? "discovered" : "n/a"}
                    />
                  </div>
                  <p className="mt-1 text-xs text-text-muted">
                    <span className="font-mono">{a.name}</span>
                    <span className="mx-1.5">·</span>
                    raw landings:{" "}
                    <span className="tabular-nums text-text-primary">
                      {formatNumber(data.count)}
                    </span>
                    {data.latest && <span className="ml-2">latest {fromNow(data.latest)}</span>}
                  </p>
                </div>
                <div className="ml-auto text-accent-project">
                  <SparkLine
                    values={a.session_count_24h > 0 ? [0, a.session_count_24h] : []}
                    width={140}
                    height={36}
                    ariaLabel="24h activity"
                  />
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  icon={busy === a.name ? <RefreshCw size={12} /> : <Import size={12} />}
                  loading={busy === a.name}
                  onClick={() => runImport(a.name)}
                >
                  Run import
                </Button>
              </li>
            );
          })}
        </ul>
      </Card>
      <p className="text-xs text-text-muted">
        Imports currently invoke each adapter's <code>test()</code> probe — the
        real <code>POST /v1/imports/{"{name}"}/run</code> endpoint ships with the
        Phase 6 adapter framework.
      </p>
    </div>
  );
}
