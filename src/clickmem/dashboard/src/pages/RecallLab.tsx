import { FormEvent, useState } from "react";
import { Pin, PinOff, Play, ShieldX, Sparkles, SquareArrowOutUpRight } from "lucide-react";
import { api, RecallTrace } from "../api";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { useToast } from "../components/Toast";
import { classNames, preview } from "../lib/format";
import { MEMORY_KIND_OPTIONS, memoryKindLabel } from "../lib/labels";

interface PaneState {
  query: string;
  projectId: string;
  kind: string;
  includeConfidential: boolean;
  crossProject: boolean;
  limit: number;
  loading: boolean;
  result: RecallTrace | null;
  error: string;
}

const empty = (): PaneState => ({
  query: "",
  projectId: "",
  kind: "",
  includeConfidential: false,
  crossProject: false,
  limit: 10,
  loading: false,
  result: null,
  error: "",
});

export default function RecallLabPage() {
  const [compare, setCompare] = useState(false);
  const [a, setA] = useState<PaneState>(empty());
  const [b, setB] = useState<PaneState>(empty());

  return (
    <div className="space-y-5">
      <Card padded={false}>
        <div className="flex items-center justify-between border-b border-line/60 px-5 py-3">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Recall Lab</h2>
            <p className="text-xs text-text-muted">
              Inspect the cosine × project × privacy breakdown that drives ranking.
            </p>
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-text-secondary">
            <input
              type="checkbox"
              checked={compare}
              onChange={(e) => setCompare(e.target.checked)}
            />
            Compare two queries
          </label>
        </div>
        <div className={classNames("grid gap-5 p-5", compare && "lg:grid-cols-2")}>
          <RecallPane state={a} setState={setA} label="A" />
          {compare && <RecallPane state={b} setState={setB} label="B" />}
        </div>
      </Card>
    </div>
  );
}

function RecallPane({
  state,
  setState,
  label,
}: {
  state: PaneState;
  setState: (s: PaneState) => void;
  label: string;
}) {
  const toast = useToast();

  const run = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!state.query.trim()) return;
    setState({ ...state, loading: true, error: "" });
    try {
      const res = await api.recallTrace({
        query: state.query.trim(),
        project_id: state.projectId || undefined,
        kind: state.kind || null,
        include_confidential: state.includeConfidential,
        cross_project: state.crossProject,
        limit: state.limit,
      });
      setState({ ...state, loading: false, result: res, error: "" });
    } catch (err) {
      setState({
        ...state,
        loading: false,
        error: (err as Error).message,
      });
    }
  };

  const pin = async (id: string, next: boolean) => {
    try {
      await api.updateMemory(id, { pinned: next, agent: "dashboard" });
      toast.push("success", next ? "Pinned" : "Unpinned");
    } catch (e) {
      toast.push("error", "Pin failed", (e as Error).message);
    }
  };

  const blacklist = async (id: string) => {
    try {
      await api.addBlacklist({ pattern: `id:${id}`, reason: "recall lab" });
      toast.push("success", "Memory blacklisted");
    } catch (e) {
      toast.push("error", "Blacklist failed", (e as Error).message);
    }
  };

  return (
    <div>
      <form onSubmit={run} className="space-y-3 rounded-xl border border-line bg-canvas-subtle/50 p-4">
        <div className="flex items-center gap-2">
          <Pill tone="info">{label}</Pill>
          <textarea
            value={state.query}
            onChange={(e) => setState({ ...state, query: e.target.value })}
            placeholder="What should ClickMem surface?"
            rows={2}
            className="flex-1 rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm focus:border-ink-500 focus:outline-none focus:ring-2 focus:ring-ink-500/15"
          />
          <Button type="submit" variant="primary" icon={<Play size={12} />} loading={state.loading}>
            Run
          </Button>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
          <label className="block">
            <span className="text-text-muted">Project</span>
            <input
              value={state.projectId}
              onChange={(e) => setState({ ...state, projectId: e.target.value })}
              className="mt-1 w-full rounded-md border border-line bg-canvas-paper px-2 py-1 font-mono"
            />
          </label>
          <label className="block">
            <span className="text-text-muted">Type</span>
            <select
              value={state.kind}
              onChange={(e) => setState({ ...state, kind: e.target.value })}
              className="mt-1 w-full rounded-md border border-line bg-canvas-paper px-2 py-1"
            >
              {["", ...MEMORY_KIND_OPTIONS].map((k) => (
                <option key={k} value={k}>
                  {k ? memoryKindLabel(k) : "All types"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 mt-5">
            <input
              type="checkbox"
              checked={state.crossProject}
              onChange={(e) => setState({ ...state, crossProject: e.target.checked })}
            />
            <span className="text-text-secondary">cross-project</span>
          </label>
          <label className="flex items-center gap-1.5 mt-5">
            <input
              type="checkbox"
              checked={state.includeConfidential}
              onChange={(e) => setState({ ...state, includeConfidential: e.target.checked })}
            />
            <span className="text-text-secondary">include confidential</span>
          </label>
        </div>
      </form>

      {state.loading && !state.result && (
        <div className="mt-4">
          <LoadingShimmer lines={5} />
        </div>
      )}

      {state.error && (
        <div className="mt-4 rounded-lg border border-bad/30 bg-bad/5 p-3 text-sm text-bad">
          {state.error}
        </div>
      )}

      {state.result && (
        <div className="mt-4 space-y-4">
          <div className="rounded-lg border border-line bg-canvas-paper p-3 text-xs text-text-secondary">
            <p className="font-medium text-text-primary mb-1">
              {state.result.hits.length} hit{state.result.hits.length === 1 ? "" : "s"} ·{" "}
              {state.result.candidates.length} candidate{state.result.candidates.length === 1 ? "" : "s"} scanned
            </p>
            <p>
              filters:{" "}
              {state.result.filters.project_id || "all projects"} ·{" "}
              {state.result.filters.kind
                ? memoryKindLabel(state.result.filters.kind)
                : "all types"} ·{" "}
              {state.result.filters.cross_project ? "cross-project" : "same-project"} ·{" "}
              {state.result.filters.include_confidential ? "incl. confidential" : "no confidential"}
            </p>
          </div>

          {state.result.hits.length === 0 ? (
            <Empty
              title="No hits"
              description="Either the threshold filtered everything, or there is no memory near this query yet."
              icon={<Sparkles size={16} />}
            />
          ) : (
            <ol className="space-y-3">
              {state.result.hits.map((h, idx) => (
                <li key={h.id} className="rounded-xl border border-line bg-canvas-paper p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs">
                    <span className="font-semibold tabular-nums text-text-muted">#{idx + 1}</span>
                    <Pill tone="kind">{memoryKindLabel(h.kind)}</Pill>
                    <Pill tone="info">{h.privacy}</Pill>
                    <span className="font-mono text-[11px] text-text-muted">
                      {h.project_id || "global"}
                    </span>
                    {h.pinned && <Pill tone="privacy" icon={<Pin size={10} />}>pinned</Pill>}
                    <span className="ml-auto inline-flex items-center gap-3 text-text-secondary">
                      <span>score <b className="tabular-nums">{h.score.toFixed(3)}</b></span>
                      <span>cos <b className="tabular-nums">{h.cosine_sim.toFixed(3)}</b></span>
                      <span>×{h.project_boost.toFixed(1)}</span>
                    </span>
                  </div>
                  <p className="text-sm leading-relaxed text-text-primary whitespace-pre-wrap">
                    {h.content}
                  </p>
                  <div className="mt-3 flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={h.pinned ? <PinOff size={12} /> : <Pin size={12} />}
                      onClick={() => pin(h.id, !h.pinned)}
                    >
                      {h.pinned ? "Unpin" : "Pin"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<ShieldX size={12} />}
                      onClick={() => blacklist(h.id)}
                    >
                      Blacklist
                    </Button>
                    <a
                      href={`/dashboard/memories?id=${h.id}`}
                      className="ml-auto inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary"
                    >
                      Open <SquareArrowOutUpRight size={11} />
                    </a>
                  </div>
                </li>
              ))}
            </ol>
          )}

          {state.result.candidates.length > 0 && (
            <details className="rounded-xl border border-line bg-canvas-paper">
              <summary className="cursor-pointer px-4 py-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
                Full candidate trace ({state.result.candidates.length})
              </summary>
              <ul className="divide-y divide-line/70">
                {state.result.candidates.map((c) => (
                  <li
                    key={c.id}
                    className={classNames(
                      "px-4 py-2 text-xs",
                      !c.kept && "bg-canvas/60 text-text-muted",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono">{c.id.slice(0, 10)}</span>
                      <Pill tone={c.kept ? "good" : "neutral"}>
                        {c.kept ? "kept" : c.blacklisted ? "blacklisted" : c.privacy_blocked ? "privacy-blocked" : "filtered"}
                      </Pill>
                      <span className="font-mono">{c.project_id || "global"}</span>
                      <span className="ml-auto inline-flex items-center gap-2">
                        cos {c.cosine_sim.toFixed(3)} · ×{c.project_boost.toFixed(1)} · score{" "}
                        <b>{c.score.toFixed(3)}</b>
                      </span>
                    </div>
                    <p className="mt-1">{preview(c.content_preview, 200)}</p>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
