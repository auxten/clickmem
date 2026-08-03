import { useMemo, useState } from "react";
import { Clock3, Inbox, Pencil, User, Users, X } from "lucide-react";
import { api, PickupCandidate } from "../api";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { Pill } from "../components/Pill";
import { useToast } from "../components/Toast";
import { useApi } from "../hooks/useApi";
import { fromNow } from "../lib/format";

interface Props {
  refreshTick: number;
}

export default function PickupPage({ refreshTick }: Props) {
  const [status, setStatus] = useState("pending");
  const candidatesQ = useApi(
    () => api.listPickupCandidates({ status, limit: 500 }),
    { deps: [refreshTick, status] },
  );

  const groups = useMemo(() => {
    const result = new Map<string, Map<string, PickupCandidate[]>>();
    for (const candidate of candidatesQ.data?.items || []) {
      const project = candidate.project_id || "Personal / unknown project";
      if (!result.has(project)) result.set(project, new Map());
      const sessions = result.get(project)!;
      const session = `${candidate.source_agent}:${candidate.session_id}`;
      if (!sessions.has(session)) sessions.set(session, []);
      sessions.get(session)!.push(candidate);
    }
    return Array.from(result.entries());
  }, [candidatesQ.data]);

  const pendingCount = status === "pending" ? candidatesQ.data?.total || 0 : 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700/15 bg-ink-700/5 px-4 py-3">
        <Inbox size={18} className="text-ink-700" />
        <div>
          <p className="text-sm font-semibold text-text-primary">
            {pendingCount} candidates, 0 memories added automatically
          </p>
          <p className="text-xs text-text-muted">
            AI prepared these proposals. Only your Personal or Project choice makes one recallable.
          </p>
        </div>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          className="ml-auto rounded-md border border-line bg-canvas-paper px-2 py-1.5 text-xs"
        >
          <option value="pending">Needs review</option>
          <option value="accepted_personal">Accepted personally</option>
          <option value="accepted_project">Shared with project</option>
          <option value="dismissed">Dismissed</option>
          <option value="snoozed">Snoozed</option>
          <option value="superseded">Superseded</option>
        </select>
      </div>

      {candidatesQ.loading && !candidatesQ.data ? (
        <Card><LoadingShimmer lines={8} /></Card>
      ) : groups.length === 0 ? (
        <Card>
          <Empty
            icon={<Inbox size={18} />}
            title={status === "pending" ? "Inbox zero" : "No candidates in this view"}
            description={
              status === "pending"
                ? "Run `clickmem pickup now` or wait for the daily timer. Nothing becomes memory automatically."
                : "Choose a different candidate status."
            }
          />
        </Card>
      ) : (
        groups.map(([project, sessions]) => (
          <section key={project} className="space-y-3">
            <div className="flex items-center gap-2 px-1">
              <h2 className="font-mono text-sm font-semibold text-text-primary">{project}</h2>
              <Pill tone="project">
                {Array.from(sessions.values()).reduce((sum, rows) => sum + rows.length, 0)}
              </Pill>
            </div>
            {Array.from(sessions.entries()).map(([session, rows]) => (
              <Card
                key={session}
                title={session}
                subtitle={`${rows.length} atomic proposal${rows.length === 1 ? "" : "s"}`}
                padded={false}
              >
                <ul className="divide-y divide-line/70">
                  {rows.map((candidate) => (
                    <CandidateRow
                      key={candidate.id}
                      candidate={candidate}
                      onChanged={candidatesQ.refresh}
                    />
                  ))}
                </ul>
              </Card>
            ))}
          </section>
        ))
      )}
    </div>
  );
}

function CandidateRow({
  candidate,
  onChanged,
}: {
  candidate: PickupCandidate;
  onChanged: () => Promise<void>;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(candidate.content);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const actionable = candidate.status === "pending";

  const accept = async (scope: "personal" | "project") => {
    setBusy(scope);
    try {
      const result = await api.acceptPickupCandidate(candidate.id, {
        version: candidate.version,
        scope,
        content: content.trim() === candidate.content ? undefined : content.trim(),
      });
      if (!result.accepted) {
        toast.push("error", "Candidate was not accepted", result.memory.message);
      } else {
        toast.push("success", scope === "project" ? "Shared with project memory" : "Remembered personally");
      }
      await onChanged();
    } catch (error) {
      toast.push("error", "Pickup failed", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("");
    }
  };

  const dismiss = async () => {
    setBusy("dismiss");
    try {
      await api.dismissPickupCandidate(candidate.id, candidate.version);
      toast.push("info", "Marked as temporary");
      await onChanged();
    } catch (error) {
      toast.push("error", "Dismiss failed", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("");
    }
  };

  const snooze = async () => {
    setBusy("snooze");
    try {
      await api.snoozePickupCandidate(candidate.id, candidate.version);
      toast.push("info", "Snoozed for later review");
      await onChanged();
    } catch (error) {
      toast.push("error", "Snooze failed", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("");
    }
  };

  return (
    <li className="px-5 py-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Pill tone="kind">{candidate.kind}</Pill>
        <Pill tone={candidate.suggested_scope === "project" ? "project" : "privacy"}>
          suggested: {candidate.suggested_scope}
        </Pill>
        {candidate.tags.map((tag) => <Pill key={tag}>{tag}</Pill>)}
        <span className="ml-auto text-xs text-text-muted">{fromNow(candidate.created_at)}</span>
      </div>

      {editing ? (
        <textarea
          value={content}
          onChange={(event) => setContent(event.target.value)}
          rows={4}
          className="w-full rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm leading-relaxed"
        />
      ) : (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-text-primary">{candidate.content}</p>
      )}

      {candidate.reason && (
        <p className="mt-2 text-xs text-text-secondary">
          <span className="font-semibold">Why keep:</span> {candidate.reason}
        </p>
      )}
      {candidate.evidence && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setEvidenceOpen((value) => !value)}
            className="text-xs font-medium text-text-secondary hover:text-text-primary"
          >
            {evidenceOpen ? "Hide" : "Show"} sanitized evidence
          </button>
          {evidenceOpen && (
            <p className="mt-1 rounded-lg bg-canvas-subtle px-3 py-2 text-xs leading-relaxed text-text-muted">
              {candidate.evidence}
            </p>
          )}
        </div>
      )}

      {actionable ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="primary"
            icon={<User size={12} />}
            loading={busy === "personal"}
            onClick={() => accept("personal")}
          >
            Personal
          </Button>
          <Button
            size="sm"
            variant="outline"
            icon={<Users size={12} />}
            loading={busy === "project"}
            disabled={!candidate.project_id}
            title={candidate.project_id ? "Share with project memory" : "No project detected"}
            onClick={() => accept("project")}
          >
            Project
          </Button>
          <Button size="sm" variant="ghost" icon={<Pencil size={12} />} onClick={() => setEditing((value) => !value)}>
            {editing ? "Done editing" : "Edit"}
          </Button>
          <Button size="sm" variant="ghost" icon={<Clock3 size={12} />} loading={busy === "snooze"} onClick={snooze}>
            Later
          </Button>
          <Button className="ml-auto" size="sm" variant="danger" icon={<X size={12} />} loading={busy === "dismiss"} onClick={dismiss}>
            Temporary
          </Button>
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-2">
          <Pill tone={candidate.status.startsWith("accepted") ? "good" : "neutral"}>{candidate.status}</Pill>
          {candidate.memory_ref && <span className="font-mono text-xs text-text-muted">{candidate.memory_ref}</span>}
          {candidate.decision_reason && <span className="text-xs text-text-muted">{candidate.decision_reason}</span>}
        </div>
      )}
    </li>
  );
}
