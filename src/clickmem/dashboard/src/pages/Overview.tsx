import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BookOpen,
  Brain,
  CheckCircle2,
  Heart,
  Pin,
  Sparkles,
} from "lucide-react";
import { api, AgentRow, EventRow } from "../api";
import { useApi } from "../hooks/useApi";
import { useEvents } from "../hooks/useEvents";
import { Card } from "../components/Card";
import { KPI } from "../components/KPI";
import { SparkLine } from "../components/SparkLine";
import { Donut } from "../components/Donut";
import { StackedBar, StackedRow } from "../components/StackedBar";
import { Pill } from "../components/Pill";
import { TrafficLight, Status } from "../components/TrafficLight";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { Button } from "../components/Button";
import { classNames, fromNow, formatNumber, preview } from "../lib/format";

interface OverviewProps {
  refreshTick: number;
  agents: AgentRow[];
}

type Range = "1W" | "1M" | "3M" | "1Y" | "All";

const RANGE_DAYS: Record<Range, number> = {
  "1W": 7,
  "1M": 30,
  "3M": 90,
  "1Y": 365,
  All: 36500,
};

const KIND_PALETTE: Record<string, string> = {
  principle: "#8b5cf6",
  decision: "#3b82f6",
  fact: "#10b981",
  doc: "#f59e0b",
  free: "#94a3b8",
};

const PRIVACY_PALETTE: Record<string, string> = {
  public: "#10b981",
  private: "#3b82f6",
  confidential: "#dc2626",
};

export default function OverviewPage({ refreshTick, agents }: OverviewProps) {
  const navigate = useNavigate();
  const [range, setRange] = useState<Range>("1M");

  const statsQ = useApi(() => api.statsOverview(), { deps: [refreshTick] });
  const kindsQ = useApi(() => api.statsKinds(), { deps: [refreshTick] });
  const projectsQ = useApi(() => api.statsProjects(), { deps: [refreshTick] });
  const privacyQ = useApi(() => api.statsPrivacyMix(), { deps: [refreshTick] });
  const pinnedQ = useApi(
    () =>
      api.listMemories({ pinned: true, status: "active", limit: 6 }).then((r) => r.items),
    { deps: [refreshTick] },
  );
  const recentEvents = useEvents({ intervalMs: 5000, cap: 50 });
  const agentActivityQ = useApi(() => fetchHeroActivity(range), {
    deps: [refreshTick, range],
  });

  async function fetchHeroActivity(currentRange: Range): Promise<number[]> {
    // Hero sparkline = event volume from /v1/events binned by hour over the
    // selected window. We sum across agents/kinds for a single trend line.
    const days = RANGE_DAYS[currentRange];
    const buckets = days <= 7 ? 24 : days <= 30 ? 30 : days <= 90 ? 30 : 24;
    const windowMs = Math.min(days, 30) * 24 * 60 * 60 * 1000;
    const since = new Date(Date.now() - windowMs).toISOString().replace("T", " ").slice(0, 19);
    try {
      const events = await api.events({ since, limit: 2000 });
      if (events.length === 0) return [];
      const now = Date.now();
      const bucketMs = windowMs / buckets;
      const counts = new Array(buckets).fill(0);
      for (const e of events) {
        const t = Date.parse(e.created_at.replace(" ", "T") + "Z");
        if (!Number.isFinite(t)) continue;
        const idx = Math.min(
          buckets - 1,
          Math.max(0, Math.floor((t - (now - windowMs)) / bucketMs)),
        );
        counts[idx] += 1;
      }
      return counts;
    } catch {
      return [];
    }
  }

  const total = statsQ.data?.total ?? 0;
  const last7 = statsQ.data?.last7 ?? 0;
  const prev7 = statsQ.data?.prev7 ?? 0;
  const delta7 = prev7 === 0 ? (last7 > 0 ? 100 : 0) : ((last7 - prev7) / prev7) * 100;

  const kindData = useMemo(() => {
    const rows = (kindsQ.data || []).map((r) => ({
      label: r.kind || "free",
      value: r.c,
      color: KIND_PALETTE[r.kind] || "#94a3b8",
    }));
    return rows;
  }, [kindsQ.data]);

  const privacyRows: StackedRow[] = useMemo(() => {
    const byProject: Record<string, Record<string, number>> = {};
    for (const r of privacyQ.data || []) {
      const key = r.project_id || "global";
      byProject[key] = byProject[key] || { public: 0, private: 0, confidential: 0 };
      byProject[key][r.privacy] = r.c;
    }
    return Object.entries(byProject)
      .map(([label, vals]) => ({
        label: label === "" ? "global" : label.slice(0, 12),
        parts: [
          { key: "public", value: vals.public || 0, color: PRIVACY_PALETTE.public },
          { key: "private", value: vals.private || 0, color: PRIVACY_PALETTE.private },
          { key: "confidential", value: vals.confidential || 0, color: PRIVACY_PALETTE.confidential },
        ],
      }))
      .sort(
        (a, b) =>
          b.parts.reduce((s, p) => s + p.value, 0) - a.parts.reduce((s, p) => s + p.value, 0),
      )
      .slice(0, 8);
  }, [privacyQ.data]);

  return (
    <div className="space-y-6">
      {/* Hero KPI ----------------------------------------------------- */}
      <Card className="relative overflow-hidden" padded={false}>
        <div className="absolute -right-10 -top-10 h-48 w-48 rounded-full bg-accent-project/10 blur-3xl" aria-hidden />
        <div className="absolute right-20 top-20 h-32 w-32 rounded-full bg-accent-kind/10 blur-3xl" aria-hidden />
        <div className="relative grid grid-cols-1 gap-6 px-6 py-6 lg:grid-cols-[1.4fr_1fr]">
          <div>
            <p className="text-xs uppercase tracking-wide text-text-muted">
              Total Memories Value
            </p>
            <div className="mt-2 flex items-end gap-3">
              {statsQ.loading && !statsQ.data ? (
                <LoadingShimmer lines={1} height="h-12" className="w-48" />
              ) : (
                <>
                  <h2 className="text-4xl md:text-5xl font-semibold tabular-nums text-text-primary">
                    {formatNumber(total)}
                  </h2>
                  <Pill tone={delta7 >= 0 ? "good" : "bad"} className="mb-1">
                    {delta7 >= 0 ? "+" : ""}
                    {delta7.toFixed(0)}% · 7d
                  </Pill>
                </>
              )}
            </div>
            <p className="mt-2 max-w-md text-sm text-text-secondary">
              Every memory here was explicitly committed by an agent or by you.
              Garbage and hallucination both rejected at the door.
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-1.5">
              {(["1W", "1M", "3M", "1Y", "All"] as Range[]).map((r) => (
                <button
                  key={r}
                  onClick={() => setRange(r)}
                  className={classNames(
                    "rounded-full px-3 py-1 text-xs font-medium transition-colors",
                    r === range
                      ? "bg-ink-900 text-text-inverse"
                      : "text-text-secondary hover:bg-canvas-subtle",
                  )}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col justify-end gap-3">
            <div className="text-accent-project">
              <SparkLine
                values={agentActivityQ.data && agentActivityQ.data.length > 0 ? agentActivityQ.data : [0, 1, 2, 1, 3, 4, 2, 5, 3, 6]}
                width={460}
                height={120}
              />
            </div>
            <div className="grid grid-cols-3 gap-3 text-sm">
              <Mini label="Active" value={statsQ.data?.active ?? 0} />
              <Mini label="Pinned" value={statsQ.data?.pinned ?? 0} />
              <Mini label="Conflicts" value={statsQ.data?.conflicted ?? 0} tone="warn" />
            </div>
          </div>
        </div>
      </Card>

      {/* Mid row ------------------------------------------------------ */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card
          title="Top Projects"
          subtitle="Where your memory is concentrated"
          action={
            <Button
              size="sm"
              variant="ghost"
              icon={<ArrowUpRight size={12} />}
              onClick={() => navigate("/memories")}
            >
              Browse
            </Button>
          }
        >
          {projectsQ.loading && !projectsQ.data ? (
            <LoadingShimmer lines={5} />
          ) : (projectsQ.data || []).length === 0 ? (
            <Empty title="No projects yet" description="Detected at ingest from cwd → git remote." />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-text-muted">
                  <th className="text-left font-medium pb-2">Project</th>
                  <th className="text-right font-medium pb-2">Memories</th>
                  <th className="text-right font-medium pb-2 hidden sm:table-cell">Pinned</th>
                  <th className="text-right font-medium pb-2 hidden sm:table-cell">Conflicts</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60">
                {(projectsQ.data || []).slice(0, 6).map((p) => (
                  <tr key={p.project_id || "global"}>
                    <td className="py-2 font-medium text-text-primary">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="inline-block h-2 w-2 rounded-full bg-accent-project" />
                        <span className="truncate font-mono text-xs">
                          {p.project_id || "global"}
                        </span>
                      </div>
                    </td>
                    <td className="py-2 text-right tabular-nums">{formatNumber(p.memories)}</td>
                    <td className="py-2 text-right tabular-nums text-text-secondary hidden sm:table-cell">
                      {formatNumber(p.pinned)}
                    </td>
                    <td className="py-2 text-right tabular-nums hidden sm:table-cell">
                      {p.conflicts > 0 ? (
                        <span className="text-warn">{p.conflicts}</span>
                      ) : (
                        <span className="text-text-muted">0</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Memories by Kind" subtitle="Principle / decision / fact / doc / free">
          {kindsQ.loading && !kindsQ.data ? (
            <LoadingShimmer lines={5} />
          ) : kindData.length === 0 ? (
            <Empty title="No memories yet" description="Use clickmem remember or the + Add Memory button." />
          ) : (
            <Donut data={kindData} height={180} centerLabel="memories" />
          )}
        </Card>

        <Card title="Privacy × Project mix" subtitle="Distribution across projects">
          {privacyQ.loading && !privacyQ.data ? (
            <LoadingShimmer lines={5} />
          ) : privacyRows.length === 0 ? (
            <Empty title="No memories yet" />
          ) : (
            <StackedBar
              rows={privacyRows}
              legend={[
                { key: "public", label: "public", color: PRIVACY_PALETTE.public },
                { key: "private", label: "private", color: PRIVACY_PALETTE.private },
                { key: "confidential", label: "confidential", color: PRIVACY_PALETTE.confidential },
              ]}
            />
          )}
        </Card>
      </div>

      {/* Bottom row --------------------------------------------------- */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card
          title="Pinned Memories"
          subtitle="Immune to revision suggestions"
          action={
            <Button size="sm" variant="ghost" onClick={() => navigate("/pinned")}>
              View all
            </Button>
          }
        >
          {pinnedQ.loading && !pinnedQ.data ? (
            <LoadingShimmer lines={4} />
          ) : !pinnedQ.data || pinnedQ.data.length === 0 ? (
            <Empty
              title="No pinned memories"
              description="Pin authoritative memories to keep them immune to revision."
              icon={<Pin className="h-4 w-4" />}
            />
          ) : (
            <ul className="space-y-2.5">
              {pinnedQ.data.slice(0, 6).map((m) => (
                <li key={m.id} className="flex items-start gap-2.5">
                  <Pin size={14} className="text-accent-privacy mt-0.5 shrink-0" />
                  <button
                    onClick={() => navigate(`/memories?id=${m.id}`)}
                    className="text-left text-sm text-text-primary hover:text-accent-project"
                  >
                    {preview(m.content, 100)}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Recent Memories"
          subtitle="Live feed from /v1/events"
          action={<TrafficLight status="online" pulse label="live" />}
        >
          {recentEvents.loading && recentEvents.events.length === 0 ? (
            <LoadingShimmer lines={4} />
          ) : recentEvents.events.length === 0 ? (
            <Empty title="No events yet" description="Recent memory mutations land here every 5s." />
          ) : (
            <ul className="space-y-3 max-h-72 overflow-y-auto clickmem-scroll pr-1">
              {recentEvents.events.slice(0, 12).map((e) => (
                <EventLine key={e.id} ev={e} />
              ))}
            </ul>
          )}
        </Card>

        <Card title="Brain Health" subtitle="Key metrics at a glance">
          {statsQ.loading && !statsQ.data ? (
            <LoadingShimmer lines={5} />
          ) : (
            <ul className="space-y-3 text-sm">
              <HealthRow
                icon={<Brain size={14} className="text-accent-project" />}
                label="Active memories"
                value={formatNumber(statsQ.data?.active ?? 0)}
              />
              <HealthRow
                icon={<Pin size={14} className="text-accent-privacy" />}
                label="Pinned"
                value={formatNumber(statsQ.data?.pinned ?? 0)}
              />
              <HealthRow
                icon={<AlertTriangle size={14} className="text-warn" />}
                label="Unresolved conflicts"
                value={formatNumber(statsQ.data?.conflicted ?? 0)}
                tone={(statsQ.data?.conflicted ?? 0) > 0 ? "warn" : "ok"}
              />
              <HealthRow
                icon={<CheckCircle2 size={14} className="text-good" />}
                label="Contracted"
                value={formatNumber(statsQ.data?.contracted ?? 0)}
              />
              <HealthRow
                icon={<BookOpen size={14} className="text-ink-500" />}
                label="Raw transcripts"
                value={formatNumber(statsQ.data?.raw_transcripts ?? 0)}
              />
              <HealthRow
                icon={<Sparkles size={14} className="text-accent-kind" />}
                label="Events (24h)"
                value={formatNumber(statsQ.data?.events_24h ?? 0)}
              />
            </ul>
          )}
        </Card>
      </div>

      {/* Integrations health bar -------------------------------------- */}
      <Card title="Integrations health" subtitle="Adapters · click to manage">
        {agents.length === 0 ? (
          <Empty title="No adapters discovered" description="They appear here as you install hooks." />
        ) : (
          <div className="flex flex-wrap gap-2.5">
            {agents.map((a) => {
              const status: Status = a.installed
                ? "online"
                : a.discovered
                  ? "warn"
                  : "unknown";
              return (
                <button
                  key={a.name}
                  type="button"
                  onClick={() => navigate(`/agents?focus=${a.name}`)}
                  className="group inline-flex items-center gap-2 rounded-full border border-line bg-canvas-paper px-3 py-1.5 text-xs hover:bg-canvas-subtle"
                >
                  <TrafficLight status={status} />
                  <span className="text-text-primary font-medium">{a.label}</span>
                  <span className="text-text-muted tabular-nums">
                    {a.session_count_24h}/24h
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

function Mini({ label, value, tone }: { label: string; value: number; tone?: "warn" }) {
  return (
    <div className="rounded-xl border border-line bg-canvas-paper px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-text-muted">{label}</p>
      <p
        className={classNames(
          "mt-1 text-lg font-semibold tabular-nums",
          tone === "warn" && (value > 0 ? "text-warn" : "text-text-primary"),
        )}
      >
        {formatNumber(value)}
      </p>
    </div>
  );
}

function HealthRow({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone?: "ok" | "warn";
}) {
  return (
    <li className="flex items-center gap-2.5">
      <span>{icon}</span>
      <span className="text-text-secondary">{label}</span>
      <span
        className={classNames(
          "ml-auto font-semibold tabular-nums",
          tone === "warn" ? "text-warn" : "text-text-primary",
        )}
      >
        {value}
      </span>
    </li>
  );
}

function EventLine({ ev }: { ev: EventRow }) {
  const icon = iconFor(ev.kind);
  return (
    <li className="flex items-start gap-2.5">
      <span className="mt-0.5">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-text-primary">
          <span className="font-medium">{ev.kind}</span>
          {ev.agent && (
            <span className="ml-1 text-xs text-text-muted">· {ev.agent}</span>
          )}
        </p>
        <p className="truncate text-xs text-text-muted">{ev.message || ev.memory_id || ev.project_id || "—"}</p>
      </div>
      <span className="shrink-0 text-[11px] tabular-nums text-text-muted">
        {fromNow(ev.created_at)}
      </span>
    </li>
  );
}

function iconFor(kind: string) {
  if (kind.startsWith("memory.expand")) return <Sparkles size={14} className="text-good" />;
  if (kind.startsWith("memory.revise")) return <Brain size={14} className="text-accent-project" />;
  if (kind.startsWith("memory.contract")) return <CheckCircle2 size={14} className="text-text-muted" />;
  if (kind.startsWith("memory.pin") || kind.startsWith("memory.unpin"))
    return <Pin size={14} className="text-accent-privacy" />;
  if (kind.startsWith("recall")) return <Sparkles size={14} className="text-accent-kind" />;
  if (kind.startsWith("blacklist")) return <AlertTriangle size={14} className="text-bad" />;
  if (kind.startsWith("raw")) return <BookOpen size={14} className="text-ink-500" />;
  return <Heart size={14} className="text-text-muted" />;
}
