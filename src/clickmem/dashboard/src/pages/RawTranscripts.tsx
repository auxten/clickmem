import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, ChevronDown, ChevronRight, FileText, Search } from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { fromNow, preview, shortDate } from "../lib/format";

interface Props {
  refreshTick: number;
}

export default function RawTranscriptsPage({ refreshTick }: Props) {
  const navigate = useNavigate();
  const [sessionFilter, setSessionFilter] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [limit, setLimit] = useState(100);

  const rawQ = useApi(
    () =>
      api.getRaw({
        session_id: sessionFilter || undefined,
        agent: agentFilter || undefined,
        last: limit,
      }),
    { deps: [refreshTick, sessionFilter, agentFilter, limit] },
  );

  const grouped = useMemo(() => {
    const items = rawQ.data || [];
    const sessions = new Map<string, typeof items>();
    for (const row of items) {
      const sid = row.session_id || "(no-session)";
      if (!sessions.has(sid)) sessions.set(sid, []);
      sessions.get(sid)!.push(row);
    }
    return Array.from(sessions.entries()).map(([sid, rows]) => ({
      session_id: sid,
      rows,
      agent: rows[0]?.agent || "",
      latest: rows[0]?.created_at || "",
      count: rows.length,
    }));
  }, [rawQ.data]);

  const promote = (text: string, sessionId: string, agent: string) => {
    navigate(
      `/memories?new=1&prefill=${encodeURIComponent(text)}&ref=${encodeURIComponent(
        `raw:${sessionId}`,
      )}&source=${encodeURIComponent(agent || "raw")}`,
    );
  };

  return (
    <div className="space-y-5">
      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-3 border-b border-line/60 px-5 py-3 text-xs">
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              value={sessionFilter}
              onChange={(e) => setSessionFilter(e.target.value)}
              placeholder="session id"
              className="rounded-md border border-line bg-canvas-paper py-1 pl-7 pr-2 font-mono"
            />
          </div>
          <input
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            placeholder="agent"
            className="rounded-md border border-line bg-canvas-paper px-2 py-1 font-mono"
          />
          <label className="ml-auto inline-flex items-center gap-1.5 text-text-secondary">
            <span>Show last</span>
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="rounded-md border border-line bg-canvas-paper px-2 py-1"
            >
              {[50, 100, 200, 500].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        </div>

        {rawQ.loading && !rawQ.data ? (
          <div className="p-5">
            <LoadingShimmer lines={6} />
          </div>
        ) : grouped.length === 0 ? (
          <div className="p-6">
            <Empty
              title="No raw transcripts"
              description="Cold storage stays empty until a hook calls POST /v1/raw."
              icon={<FileText size={16} />}
            />
          </div>
        ) : (
          <ul className="divide-y divide-line/70">
            {grouped.map((s) => (
              <SessionGroup key={s.session_id} group={s} onPromote={promote} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function SessionGroup({
  group,
  onPromote,
}: {
  group: {
    session_id: string;
    rows: Array<{
      id: string;
      session_id: string;
      agent: string;
      project_id: string;
      role: string;
      text: string;
      created_at: string;
    }>;
    agent: string;
    latest: string;
    count: number;
  };
  onPromote: (text: string, sessionId: string, agent: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-canvas-subtle/60"
      >
        {open ? (
          <ChevronDown size={14} className="text-text-muted" />
        ) : (
          <ChevronRight size={14} className="text-text-muted" />
        )}
        <span className="font-mono text-xs text-text-primary">
          {group.session_id.length > 24
            ? `${group.session_id.slice(0, 12)}…${group.session_id.slice(-8)}`
            : group.session_id}
        </span>
        {group.agent && <Pill tone="info">{group.agent}</Pill>}
        <span className="text-xs text-text-muted tabular-nums">{group.count} rows</span>
        <span className="ml-auto text-xs text-text-muted">
          latest {fromNow(group.latest)}
        </span>
      </button>
      {open && (
        <ul className="space-y-2 border-t border-line/40 bg-canvas-subtle/30 px-5 py-3">
          {group.rows.map((r) => (
            <li
              key={r.id}
              className="rounded-lg border border-line bg-canvas-paper p-3"
            >
              <div className="mb-1 flex items-center gap-2 text-xs">
                {r.role && <Pill tone="neutral">{r.role}</Pill>}
                {r.project_id && (
                  <span className="font-mono text-text-muted">{r.project_id}</span>
                )}
                <span className="ml-auto text-text-muted">{shortDate(r.created_at)}</span>
              </div>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-text-primary">
                {preview(r.text, 600)}
              </p>
              <div className="mt-2 flex justify-end">
                <Button
                  size="sm"
                  variant="outline"
                  icon={<ArrowRight size={12} />}
                  onClick={() => onPromote(r.text, r.session_id, r.agent)}
                >
                  Promote to memory
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
