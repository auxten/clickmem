import { useMemo, useState } from "react";
import { AlertTriangle, ArrowLeftRight, CheckCircle2, Trash2 } from "lucide-react";
import { api, ConflictRow, Memory } from "../api";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { DiffView } from "../components/DiffView";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { Pill } from "../components/Pill";
import { useToast } from "../components/Toast";
import { fromNow, preview } from "../lib/format";

interface ConflictsProps {
  refreshTick: number;
}

interface Pair {
  a: ConflictRow;
  b?: ConflictRow;
}

export default function ConflictsPage({ refreshTick }: ConflictsProps) {
  const [projectFilter, setProjectFilter] = useState("");
  const listQ = useApi(
    () => api.listConflicts(projectFilter || undefined, 200),
    { deps: [refreshTick, projectFilter] },
  );

  const grouped = useMemo<Pair[]>(() => {
    const items = listQ.data || [];
    const byId = new Map(items.map((it) => [it.id, it]));
    const seen = new Set<string>();
    const pairs: Pair[] = [];
    for (const item of items) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      const peers = item.conflict_with || [];
      const peer = peers.find((p) => byId.has(p) && !seen.has(p));
      if (peer && byId.has(peer)) {
        seen.add(peer);
        pairs.push({ a: item, b: byId.get(peer)! });
      } else {
        pairs.push({ a: item });
      }
    }
    return pairs;
  }, [listQ.data]);

  return (
    <div className="space-y-5">
      <Card title="Conflicts queue" subtitle="Pairs surfaced by /v1/conflicts" padded={false}>
        <div className="flex items-center gap-3 px-5 py-3 border-b border-line/60 text-xs">
          <label className="text-text-muted">Project</label>
          <input
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            placeholder="any"
            className="rounded-md border border-line bg-canvas-paper px-2 py-1 text-xs font-mono"
          />
          <span className="ml-auto text-text-muted tabular-nums">
            {grouped.length} pair{grouped.length === 1 ? "" : "s"}
          </span>
        </div>

        {listQ.loading && !listQ.data ? (
          <div className="p-5">
            <LoadingShimmer lines={6} />
          </div>
        ) : grouped.length === 0 ? (
          <div className="p-6">
            <Empty
              title="No unresolved conflicts"
              description="When two memories are semantically close but materially different, they'll show up here."
              icon={<CheckCircle2 className="h-4 w-4" />}
            />
          </div>
        ) : (
          <ul className="divide-y divide-line/70">
            {grouped.map((pair, idx) => (
              <ConflictPair
                key={pair.a.id + (pair.b?.id ?? idx)}
                pair={pair}
                onResolved={() => listQ.refresh()}
              />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function ConflictPair({
  pair,
  onResolved,
}: {
  pair: Pair;
  onResolved: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const resolve = async (op: string, peerId?: string) => {
    setBusy(true);
    try {
      await api.resolveConflict(pair.a.id, op, peerId);
      toast.push("success", `Resolved · ${op}`);
      onResolved();
    } catch (e) {
      toast.push("error", "Resolve failed", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reviseAFromB = async () => {
    if (!pair.b) return;
    setBusy(true);
    try {
      await api.updateMemory(pair.a.id, { content: pair.b.content, agent: "dashboard" });
      await api.resolveConflict(pair.a.id, "revise", pair.b.id);
      toast.push("success", "Revised A from B");
      onResolved();
    } catch (e) {
      toast.push("error", "Revise failed", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="p-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Pill tone="warn" icon={<AlertTriangle size={10} />}>
          conflict
        </Pill>
        <span className="font-mono text-xs text-text-muted">
          {pair.a.project_id || "global"}
        </span>
        <Pill tone="kind">{pair.a.kind}</Pill>
        <span className="text-xs text-text-muted ml-auto">
          updated {fromNow(pair.a.updated_at)}
        </span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Side title="A" id={pair.a.id} content={pair.a.content} />
        {pair.b ? (
          <Side title="B" id={pair.b.id} content={pair.b.content} />
        ) : (
          <Empty
            title="Peer unavailable"
            description="The other side of this conflict has been removed."
          />
        )}
      </div>

      {pair.b && (
        <div className="mt-4 rounded-xl border border-line bg-canvas-subtle/40 p-3">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
            Inline diff (A → B)
          </p>
          <DiffView before={pair.a.content} after={pair.b.content} mode="lines" />
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
        <Button
          variant="secondary"
          size="sm"
          loading={busy}
          icon={<CheckCircle2 size={12} />}
          onClick={() => resolve("contract", pair.b?.id)}
          disabled={!pair.b}
        >
          Keep A · contract B
        </Button>
        <Button
          variant="secondary"
          size="sm"
          loading={busy}
          icon={<CheckCircle2 size={12} />}
          onClick={async () => {
            if (!pair.b) return;
            try {
              await api.forgetMemory(pair.a.id, "resolved conflict — keep B", "dashboard");
              toast.push("success", "Kept B · contracted A");
              onResolved();
            } catch (e) {
              toast.push("error", "Keep B failed", (e as Error).message);
            }
          }}
          disabled={!pair.b}
        >
          Keep B · contract A
        </Button>
        <Button
          variant="outline"
          size="sm"
          loading={busy}
          icon={<ArrowLeftRight size={12} />}
          onClick={reviseAFromB}
          disabled={!pair.b}
        >
          Revise A from B
        </Button>
        <Button
          variant="ghost"
          size="sm"
          loading={busy}
          onClick={() => resolve("allow", pair.b?.id)}
        >
          Allow divergence
        </Button>
      </div>
    </li>
  );
}

function Side({ title, id, content }: { title: string; id: string; content: string }) {
  return (
    <div className="rounded-xl border border-line bg-canvas-paper p-3">
      <div className="mb-2 flex items-center gap-2">
        <Pill tone="info">{title}</Pill>
        <span className="font-mono text-[11px] text-text-muted">{id.slice(0, 12)}…</span>
      </div>
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-text-primary">{content}</p>
    </div>
  );
}
