import { FormEvent, useState } from "react";
import { Plus, ShieldX, Trash2 } from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { useToast } from "../components/Toast";
import { fromNow } from "../lib/format";

interface Props {
  refreshTick: number;
}

export default function BlacklistPage({ refreshTick }: Props) {
  const toast = useToast();
  const listQ = useApi(() => api.listBlacklist(), { deps: [refreshTick] });
  const [pattern, setPattern] = useState("");
  const [scope, setScope] = useState("global");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!pattern.trim()) return;
    setBusy(true);
    try {
      await api.addBlacklist({ pattern: pattern.trim(), scope: scope || "global", reason });
      setPattern("");
      setReason("");
      toast.push("success", "Pattern added");
      listQ.refresh();
    } catch (err) {
      toast.push("error", "Add failed", (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await api.removeBlacklist(id);
      toast.push("success", "Pattern removed");
      listQ.refresh();
    } catch (err) {
      toast.push("error", "Remove failed", (err as Error).message);
    }
  };

  return (
    <div className="space-y-5">
      <Card title="Add pattern" subtitle="Case-insensitive substring · or id:<uuid> for exact memory bans">
        <form onSubmit={submit} className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_140px_1fr_auto]">
          <input
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            placeholder="pattern e.g. 'pager bot' or id:abc123…"
            className="rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm font-mono"
          />
          <input
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            placeholder="scope"
            className="rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm font-mono"
          />
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="reason (optional)"
            className="rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm"
          />
          <Button type="submit" variant="primary" icon={<Plus size={12} />} loading={busy}>
            Add
          </Button>
        </form>
      </Card>

      <Card title="Active patterns" padded={false}>
        {listQ.loading && !listQ.data ? (
          <div className="p-5">
            <LoadingShimmer lines={4} />
          </div>
        ) : !listQ.data || listQ.data.length === 0 ? (
          <div className="p-6">
            <Empty
              title="No blacklist patterns yet"
              description="Patterns block matching memories from being inserted, and filter them out of recall."
              icon={<ShieldX size={16} />}
            />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line/70 text-[11px] uppercase tracking-wide text-text-muted">
                <th className="px-5 py-2.5 text-left font-medium">Pattern</th>
                <th className="px-2 py-2.5 text-left font-medium">Scope</th>
                <th className="px-2 py-2.5 text-left font-medium">Reason</th>
                <th className="px-2 py-2.5 text-right font-medium">Hits</th>
                <th className="px-2 py-2.5 text-right font-medium">Added</th>
                <th className="px-5 py-2.5 text-right font-medium">&nbsp;</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/60">
              {listQ.data.map((b) => (
                <tr key={b.id} className="hover:bg-canvas-subtle/60">
                  <td className="px-5 py-3 font-mono text-xs text-text-primary">{b.pattern}</td>
                  <td className="px-2 py-3"><Pill tone="info">{b.scope || "global"}</Pill></td>
                  <td className="px-2 py-3 text-xs text-text-secondary">{b.reason || "—"}</td>
                  <td className="px-2 py-3 text-right tabular-nums">{b.hit_count}</td>
                  <td className="px-2 py-3 text-right text-xs text-text-muted">{fromNow(b.created_at)}</td>
                  <td className="px-5 py-3 text-right">
                    <Button
                      size="sm"
                      variant="danger"
                      icon={<Trash2 size={12} />}
                      onClick={() => remove(b.id)}
                    >
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
