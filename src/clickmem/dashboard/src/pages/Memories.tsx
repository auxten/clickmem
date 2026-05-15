import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Filter,
  Pin,
  PinOff,
  Plus,
  Search,
  ShieldX,
  Tag,
  Trash2,
  X,
} from "lucide-react";
import {
  api,
  Memory,
  MemoryKind,
  MemoryPrivacy,
  MemoryStatus,
} from "../api";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { Drawer } from "../components/Drawer";
import { DiffView } from "../components/DiffView";
import { SparkLine } from "../components/SparkLine";
import { useToast } from "../components/Toast";
import { classNames, formatNumber, fromNow, preview, shortDate } from "../lib/format";
import {
  MEMORY_KIND_OPTIONS,
  MEMORY_STATUS_OPTIONS,
  memoryKindLabel,
  memoryStatusLabel,
} from "../lib/labels";

interface Props {
  refreshTick: number;
  forcePinned?: boolean;
}

const KIND_TONES: Record<string, "kind" | "project" | "source" | "privacy" | "neutral"> = {
  principle: "kind",
  decision: "project",
  fact: "source",
  doc: "privacy",
  free: "neutral",
};

const PRIVACY_TONES: Record<string, "good" | "info" | "bad"> = {
  public: "good",
  private: "info",
  confidential: "bad",
};

const STATUS_TONES: Record<string, "good" | "warn" | "neutral"> = {
  active: "good",
  conflicted: "warn",
  contracted: "neutral",
};

const PAGE_SIZE = 25;

export default function MemoriesPage({ refreshTick, forcePinned }: Props) {
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const [filters, setFilters] = useState({
    search: "",
    kind: "" as "" | MemoryKind,
    privacy: "" as "" | MemoryPrivacy,
    status: "active" as "" | MemoryStatus,
    project_id: "",
    pinned: forcePinned ? true : (undefined as boolean | undefined),
  });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [drawerId, setDrawerId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const shouldCreate = params.get("new") === "1";
    const id = params.get("id");
    if (shouldCreate) {
      setCreating(true);
    }
    if (id) {
      setDrawerId(id);
    }
    if (shouldCreate || id) {
      const next = new URLSearchParams(params);
      next.delete("new");
      next.delete("id");
      setParams(next, { replace: true });
    }
  }, [params, setParams]);

  const listQ = useApi(
    () =>
      api.listMemories({
        search: filters.search || undefined,
        kind: filters.kind || undefined,
        privacy: filters.privacy || undefined,
        status: filters.status || undefined,
        project_id: filters.project_id || undefined,
        pinned: filters.pinned,
        offset,
        limit: PAGE_SIZE,
      }),
    {
      deps: [
        refreshTick,
        filters.search,
        filters.kind,
        filters.privacy,
        filters.status,
        filters.project_id,
        filters.pinned,
        offset,
      ],
    },
  );

  const total = listQ.data?.total ?? 0;
  const items = listQ.data?.items ?? [];

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (items.every((m) => selected.has(m.id))) {
      setSelected(new Set());
    } else {
      setSelected(new Set(items.map((m) => m.id)));
    }
  };

  const runBulk = useCallback(
    async (op: string, payload: Record<string, unknown> = {}) => {
      const ids = Array.from(selected);
      if (ids.length === 0) return;
      try {
        const res = await api.bulkMemories({ ids, op, payload, agent: "dashboard" });
        toast.push("success", `Bulk ${op} ran on ${res.count} memorie(s)`);
        setSelected(new Set());
        listQ.refresh();
      } catch (e) {
        toast.push("error", `Bulk ${op} failed`, (e as Error).message);
      }
    },
    [selected, toast, listQ],
  );

  return (
    <div className="space-y-5">
      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-3 px-5 py-4 border-b border-line/60">
          <div className="relative flex-1 min-w-[220px]">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
            />
            <input
              value={filters.search}
              onChange={(e) => {
                setOffset(0);
                setFilters({ ...filters, search: e.target.value });
              }}
              placeholder="Search memory content…"
              className="w-full rounded-lg border border-line bg-canvas-paper py-2 pl-9 pr-3 text-sm focus:border-ink-500 focus:outline-none focus:ring-2 focus:ring-ink-500/20"
            />
          </div>
          <FilterSelect
            label="Type"
            value={filters.kind}
            onChange={(v) => {
              setOffset(0);
              setFilters({ ...filters, kind: v as MemoryKind | "" });
            }}
            options={[
              { value: "", label: "All types" },
              ...MEMORY_KIND_OPTIONS.map((kind) => ({
                value: kind,
                label: memoryKindLabel(kind),
              })),
            ]}
          />
          <FilterSelect
            label="Privacy"
            value={filters.privacy}
            onChange={(v) => {
              setOffset(0);
              setFilters({ ...filters, privacy: v as MemoryPrivacy | "" });
            }}
            options={["", "public", "private", "confidential"]}
          />
          <FilterSelect
            label="State"
            value={filters.status}
            onChange={(v) => {
              setOffset(0);
              setFilters({ ...filters, status: v as MemoryStatus | "" });
            }}
            options={[
              { value: "", label: "All records" },
              ...MEMORY_STATUS_OPTIONS.map((status) => ({
                value: status,
                label: memoryStatusLabel(status),
              })),
            ]}
          />
          <input
            value={filters.project_id}
            onChange={(e) => {
              setOffset(0);
              setFilters({ ...filters, project_id: e.target.value });
            }}
            placeholder="project id…"
            className="w-32 rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm font-mono"
          />
          {!forcePinned && (
            <label className="inline-flex items-center gap-1.5 text-xs text-text-secondary">
              <input
                type="checkbox"
                checked={filters.pinned === true}
                onChange={(e) =>
                  setFilters({ ...filters, pinned: e.target.checked || undefined })
                }
              />
              pinned only
            </label>
          )}
          <Button
            variant="primary"
            size="sm"
            icon={<Plus size={14} />}
            onClick={() => setCreating(true)}
          >
            Add Memory
          </Button>
        </div>

        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-line/60 bg-canvas-subtle/60 px-5 py-2.5 text-xs">
            <span className="font-medium text-text-primary">{selected.size} selected</span>
            <span className="text-text-muted">·</span>
            <Button size="sm" variant="ghost" icon={<Pin size={12} />} onClick={() => runBulk("pin")}>
              Pin
            </Button>
            <Button size="sm" variant="ghost" icon={<PinOff size={12} />} onClick={() => runBulk("unpin")}>
              Unpin
            </Button>
            <BulkPrivacyButtons run={runBulk} />
            <BulkProjectButton run={runBulk} />
            <Button
              size="sm"
              variant="ghost"
              icon={<ShieldX size={12} />}
              onClick={() => runBulk("blacklist", { reason: "bulk blacklist from dashboard" })}
            >
              Blacklist
            </Button>
            <Button
              size="sm"
              variant="danger"
              icon={<Trash2 size={12} />}
              onClick={() =>
                runBulk("forget", { reason: "bulk forget from dashboard" })
              }
            >
              Archive
            </Button>
            <button
              type="button"
              className="ml-auto text-xs text-text-muted hover:text-text-primary"
              onClick={() => setSelected(new Set())}
            >
              clear
            </button>
          </div>
        )}

        <div className="overflow-x-auto clickmem-scroll">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line/70 text-[11px] uppercase tracking-wide text-text-muted">
                <th className="w-10 pl-5 pr-2 py-2.5">
                  <input
                    type="checkbox"
                    checked={items.length > 0 && items.every((m) => selected.has(m.id))}
                    onChange={toggleAll}
                  />
                </th>
                <th className="text-left font-medium py-2.5">Content</th>
                <th className="text-left font-medium py-2.5 hidden md:table-cell">Type</th>
                <th className="text-left font-medium py-2.5 hidden md:table-cell">Privacy</th>
                <th className="text-left font-medium py-2.5 hidden lg:table-cell">Project</th>
                <th className="text-left font-medium py-2.5 hidden lg:table-cell">State</th>
                <th className="text-right font-medium pr-5 py-2.5">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/60">
              {listQ.loading && items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-5">
                    <LoadingShimmer lines={6} />
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-5">
                    <Empty
                      title={
                        forcePinned ? "No pinned memories" : "No memories match your filters"
                      }
                      description={
                        forcePinned
                          ? "Pin a memory from the row drawer to make it immune to revision."
                          : "Try clearing some filters, or click Add Memory."
                      }
                      action={
                        <Button
                          variant="secondary"
                          onClick={() =>
                            setFilters({
                              search: "",
                              kind: "",
                              privacy: "",
                              status: "active",
                              project_id: "",
                              pinned: forcePinned ? true : undefined,
                            })
                          }
                        >
                          Reset filters
                        </Button>
                      }
                    />
                  </td>
                </tr>
              ) : (
                items.map((m) => (
                  <tr
                    key={m.id}
                    className="cursor-pointer hover:bg-canvas-subtle/60"
                    onClick={() => setDrawerId(m.id)}
                  >
                    <td className="pl-5 pr-2 py-3" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selected.has(m.id)}
                        onChange={() => toggle(m.id)}
                      />
                    </td>
                    <td className="py-3 max-w-[420px]">
                      <div className="flex items-center gap-2 min-w-0">
                        {m.pinned && (
                          <Pin
                            size={12}
                            className="text-accent-privacy shrink-0"
                            aria-label="pinned"
                          />
                        )}
                        <span className="truncate text-text-primary" title={m.content}>
                          {preview(m.content, 140)}
                        </span>
                      </div>
                      {m.tags.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {m.tags.slice(0, 3).map((t) => (
                            <Pill key={t} tone="neutral" icon={<Tag size={10} />}>
                              {t}
                            </Pill>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="py-3 hidden md:table-cell">
                      <Pill tone={KIND_TONES[m.kind] || "neutral"}>
                        {memoryKindLabel(m.kind)}
                      </Pill>
                    </td>
                    <td className="py-3 hidden md:table-cell">
                      <Pill tone={PRIVACY_TONES[m.privacy] || "info"}>{m.privacy}</Pill>
                    </td>
                    <td className="py-3 hidden lg:table-cell">
                      <span className="font-mono text-[11px] text-text-secondary">
                        {m.project_id || "global"}
                      </span>
                    </td>
                    <td className="py-3 hidden lg:table-cell">
                      <Pill tone={STATUS_TONES[m.status] || "neutral"}>
                        {memoryStatusLabel(m.status)}
                      </Pill>
                    </td>
                    <td className="py-3 pr-5 text-right text-xs text-text-muted whitespace-nowrap">
                      {fromNow(m.updated_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between border-t border-line/60 px-5 py-3 text-xs text-text-muted">
          <span>
            Showing{" "}
            <span className="tabular-nums text-text-primary">
              {items.length === 0 ? 0 : offset + 1}–{offset + items.length}
            </span>{" "}
            of <span className="tabular-nums text-text-primary">{formatNumber(total)}</span>
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              icon={<ChevronLeft size={14} />}
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Prev
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={<ChevronRight size={14} />}
              disabled={offset + items.length >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>

      <MemoryDrawer
        id={drawerId}
        open={drawerId !== null}
        onClose={() => setDrawerId(null)}
        onSaved={() => {
          listQ.refresh();
        }}
      />
      <CreateMemoryDrawer
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          listQ.refresh();
        }}
      />
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Array<string | { value: string; label: string }>;
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs">
      <Filter size={12} className="text-text-muted" />
      <span className="text-text-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-line bg-canvas-paper px-2 py-1 text-xs focus:border-ink-500 focus:outline-none"
      >
        {options.map((option) => {
          const value = typeof option === "string" ? option : option.value;
          const optionLabel = typeof option === "string" ? option || "any" : option.label;
          return (
            <option key={value} value={value}>
              {optionLabel}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function BulkPrivacyButtons({ run }: { run: (op: string, p?: Record<string, unknown>) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative">
      <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>
        Change privacy
      </Button>
      {open && (
        <div className="absolute z-20 mt-1 flex flex-col rounded-lg border border-line bg-canvas-paper shadow-card text-xs">
          {(["public", "private", "confidential"] as const).map((p) => (
            <button
              key={p}
              type="button"
              className="px-3 py-1.5 text-left hover:bg-canvas-subtle"
              onClick={() => {
                setOpen(false);
                run("set_privacy", { privacy: p });
              }}
            >
              → {p}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

function BulkProjectButton({ run }: { run: (op: string, p?: Record<string, unknown>) => void }) {
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => {
        const v = window.prompt("Reassign to project id (empty = global):");
        if (v === null) return;
        run("set_project", { project_id: v });
      }}
    >
      Reassign project
    </Button>
  );
}

function MemoryDrawer({
  id,
  open,
  onClose,
  onSaved,
}: {
  id: string | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const memQ = useApi(() => (id ? api.getMemory(id) : Promise.resolve(null)), {
    enabled: !!id,
    deps: [id],
  });
  const historyQ = useApi(() => (id ? api.memoryHistory(id) : Promise.resolve([])), {
    enabled: !!id,
    deps: [id],
  });
  const neighborsQ = useApi(
    () => (id ? api.memoryNeighbors(id, 8) : Promise.resolve([])),
    { enabled: !!id, deps: [id] },
  );

  const [draft, setDraft] = useState<Memory | null>(null);
  useEffect(() => {
    setDraft(memQ.data ? { ...memQ.data } : null);
  }, [memQ.data]);

  const recallSpark = useMemo(() => {
    if (!historyQ.data) return [];
    return historyQ.data.map((_h, i) => Math.max(1, i + 1));
  }, [historyQ.data]);

  if (!id) return null;

  const save = async () => {
    if (!draft) return;
    try {
      await api.updateMemory(draft.id, {
        content: draft.content,
        kind: draft.kind,
        privacy: draft.privacy,
        project_id: draft.project_id,
        tags: draft.tags,
        pinned: draft.pinned,
        agent: "dashboard",
      });
      toast.push("success", "Memory updated");
      onSaved();
    } catch (e) {
      toast.push("error", "Update failed", (e as Error).message);
    }
  };

  const togglePin = async () => {
    if (!draft) return;
    const next = !draft.pinned;
    try {
      await api.updateMemory(draft.id, { pinned: next, agent: "dashboard" });
      setDraft({ ...draft, pinned: next });
      toast.push("success", next ? "Pinned" : "Unpinned");
      onSaved();
    } catch (e) {
      toast.push("error", "Pin toggle failed", (e as Error).message);
    }
  };

  const forget = async () => {
    if (!draft) return;
    const reason = window.prompt("Why archive this memory?", "") || "";
    try {
      await api.forgetMemory(draft.id, reason, "dashboard");
      toast.push("success", "Memory archived");
      onSaved();
      onClose();
    } catch (e) {
      toast.push("error", "Archive failed", (e as Error).message);
    }
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="lg"
      title={draft ? `Memory ${draft.id.slice(0, 10)}…` : "Memory"}
      subtitle={
        draft
          ? `${memoryKindLabel(draft.kind)} · ${draft.privacy} · ${draft.project_id || "global"}`
          : "Loading…"
      }
      footer={
        draft && (
          <div className="flex items-center justify-between">
            <Button
              variant="danger"
              size="sm"
              icon={<Trash2 size={12} />}
              onClick={forget}
            >
              Archive
            </Button>
            <div className="flex gap-2">
              <Button
                variant={draft.pinned ? "secondary" : "outline"}
                size="sm"
                icon={draft.pinned ? <PinOff size={12} /> : <Pin size={12} />}
                onClick={togglePin}
              >
                {draft.pinned ? "Unpin" : "Pin"}
              </Button>
              <Button variant="primary" size="sm" icon={<Check size={12} />} onClick={save}>
                Save
              </Button>
            </div>
          </div>
        )
      }
    >
      {memQ.loading && !draft ? (
        <LoadingShimmer lines={8} />
      ) : !draft ? (
        <Empty title="Not found" description="The memory id may have been archived or removed." />
      ) : (
        <div className="space-y-6">
          <section>
            <label className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
              Content
            </label>
            <textarea
              value={draft.content}
              onChange={(e) => setDraft({ ...draft, content: e.target.value })}
              rows={6}
              className="mt-2 w-full rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm leading-6 focus:border-ink-500 focus:outline-none focus:ring-2 focus:ring-ink-500/15"
            />
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field label="Type">
                <select
                  value={draft.kind}
                  onChange={(e) =>
                    setDraft({ ...draft, kind: e.target.value as MemoryKind })
                  }
                  className="w-full rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-sm"
                >
                  {MEMORY_KIND_OPTIONS.map((k) => (
                    <option key={k} value={k}>
                      {memoryKindLabel(k)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Privacy">
                <select
                  value={draft.privacy}
                  onChange={(e) =>
                    setDraft({ ...draft, privacy: e.target.value as MemoryPrivacy })
                  }
                  className="w-full rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-sm"
                >
                  {["public", "private", "confidential"].map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Project">
                <input
                  value={draft.project_id}
                  onChange={(e) => setDraft({ ...draft, project_id: e.target.value })}
                  className="w-full rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-sm font-mono"
                />
              </Field>
              <Field label="Tags">
                <input
                  value={draft.tags.join(", ")}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      tags: e.target.value
                        .split(",")
                        .map((t) => t.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="comma-separated"
                  className="w-full rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-sm"
                />
              </Field>
            </div>
          </section>

          <section>
            <div className="flex items-center justify-between">
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                Recall trajectory
              </h3>
              <span className="text-xs text-text-muted tabular-nums">
                {draft.recall_hits} hits
              </span>
            </div>
            <div className="mt-2 text-accent-project">
              <SparkLine
                values={
                  recallSpark.length > 0
                    ? recallSpark
                    : [0, draft.recall_hits || 0]
                }
                width={420}
                height={36}
              />
            </div>
          </section>

          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
              Neighbors
            </h3>
            {neighborsQ.loading && !neighborsQ.data ? (
              <LoadingShimmer lines={3} />
            ) : !neighborsQ.data || neighborsQ.data.length === 0 ? (
              <p className="text-xs text-text-muted">No close neighbors found.</p>
            ) : (
              <ul className="space-y-2">
                {neighborsQ.data.map((n) => (
                  <li
                    key={n.id}
                    className="rounded-lg border border-line bg-canvas px-3 py-2 text-sm"
                  >
                    <p className="line-clamp-2 text-text-primary">{preview(n.content, 160)}</p>
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-text-muted">
                      <Pill tone={KIND_TONES[n.kind] || "neutral"}>
                        {memoryKindLabel(n.kind)}
                      </Pill>
                      <span className="font-mono">{n.project_id || "global"}</span>
                      {typeof n.cosine_sim === "number" && (
                        <span className="ml-auto tabular-nums">
                          cos {n.cosine_sim.toFixed(3)}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
              Edit log
            </h3>
            {historyQ.loading && !historyQ.data ? (
              <LoadingShimmer lines={4} />
            ) : !historyQ.data || historyQ.data.length === 0 ? (
              <p className="text-xs text-text-muted">No history yet.</p>
            ) : (
              <ol className="space-y-3">
                {historyQ.data.map((h) => (
                  <li
                    key={h.version}
                    className="rounded-lg border border-line bg-canvas px-3 py-2"
                  >
                    <div className="flex items-center gap-2 text-xs">
                      <Pill tone="neutral">v{h.version}</Pill>
                      <Pill tone="info">{h.op}</Pill>
                      <span className="text-text-muted">{h.edited_by || "—"}</span>
                      <span className="ml-auto text-text-muted">{shortDate(h.edited_at)}</span>
                    </div>
                    {h.diff && h.diff.length > 0 ? (
                      <div className="mt-2">
                        <DiffView
                          before={(h.diff.find((l) => l.startsWith("---")) ? "" : "")}
                          after={h.content}
                        />
                      </div>
                    ) : (
                      <p className="mt-2 text-sm text-text-secondary line-clamp-3 whitespace-pre-wrap">
                        {preview(h.content, 300)}
                      </p>
                    )}
                    {h.note && <p className="mt-1 text-xs italic text-text-muted">{h.note}</p>}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
    </Drawer>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        {label}
      </span>
      <span className="mt-1.5 block">{children}</span>
    </label>
  );
}

function CreateMemoryDrawer({
  open,
  onClose,
  onCreated,
  initialContent,
  initialRef,
  initialSource,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
  initialContent?: string;
  initialRef?: string;
  initialSource?: string;
}) {
  const toast = useToast();
  const [content, setContent] = useState("");
  const [kind, setKind] = useState<MemoryKind>("free");
  const [privacy, setPrivacy] = useState<MemoryPrivacy>("private");
  const [project, setProject] = useState("");
  const [tags, setTags] = useState("");
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setContent(initialContent ?? "");
      setKind("free");
      setPrivacy("private");
      setProject("");
      setTags("");
      setPinned(false);
    }
  }, [open, initialContent]);

  const submit = async () => {
    if (!content.trim()) return;
    setBusy(true);
    try {
      const res = await api.createMemory({
        content: content.trim(),
        kind,
        privacy,
        project_id: project,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        pinned,
        source: initialSource || "dashboard",
        source_ref: initialRef || "",
        agent: "dashboard",
      });
      if (res.status === "conflicted") {
        toast.push("info", "Conflict surfaced", `Peers: ${res.peer_ids?.join(", ") || "—"}`);
      } else if (res.status === "merged") {
        toast.push("info", "Merged into existing memory");
      } else if (res.status === "refused") {
        toast.push("error", "Refused by blacklist", res.message || "");
      } else {
        toast.push("success", "Memory added");
      }
      onCreated(res.id);
    } catch (e) {
      toast.push("error", "Create failed", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="New memory"
      subtitle="Commit something the agent should remember"
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={onClose} icon={<X size={12} />}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} loading={busy} icon={<Check size={12} />}>
            Commit memory
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <Field label="Content">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={6}
            placeholder="Write the principle, decision, or fact you want recall to surface…"
            className="w-full rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm leading-6 focus:border-ink-500 focus:outline-none focus:ring-2 focus:ring-ink-500/15"
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Type">
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as MemoryKind)}
              className="w-full rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-sm"
            >
              {MEMORY_KIND_OPTIONS.map((k) => (
                <option key={k} value={k}>
                  {memoryKindLabel(k)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Privacy">
            <select
              value={privacy}
              onChange={(e) => setPrivacy(e.target.value as MemoryPrivacy)}
              className="w-full rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-sm"
            >
              {["public", "private", "confidential"].map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Project">
            <input
              value={project}
              onChange={(e) => setProject(e.target.value)}
              className="w-full rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-sm font-mono"
              placeholder="leave blank for global"
            />
          </Field>
          <Field label="Tags">
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="w-full rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-sm"
              placeholder="comma-separated"
            />
          </Field>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={pinned}
            onChange={(e) => setPinned(e.target.checked)}
          />
          <span className="text-text-secondary">Pin (authoritative, immune to revision suggestions)</span>
        </label>
      </div>
    </Drawer>
  );
}

export { CreateMemoryDrawer };
