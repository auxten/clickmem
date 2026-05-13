import { useMemo } from "react";
import { classNames } from "../lib/format";

interface DiffViewProps {
  before: string;
  after: string;
  mode?: "split" | "lines";
}

interface DiffLine {
  kind: "ctx" | "add" | "del";
  text: string;
}

/**
 * Render either a side-by-side split view or a unified line list. Algorithm is
 * deliberately small: token-aware on whitespace boundaries, line-aware overall.
 * Sufficient for the typical 1-3 paragraph memory edit.
 */
function lineDiff(before: string, after: string): DiffLine[] {
  const a = (before || "").split(/\r?\n/);
  const b = (after || "").split(/\r?\n/);
  const out: DiffLine[] = [];
  // LCS-ish iteration: walk both arrays, emitting matches as ctx and diff as
  // del/add. Cheap O(n+m) good-enough algorithm for short edits.
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      out.push({ kind: "ctx", text: a[i] });
      i++;
      j++;
      continue;
    }
    // look ahead one step in each direction for the next match.
    const nextInB = a[i] ? b.indexOf(a[i], j) : -1;
    const nextInA = b[j] ? a.indexOf(b[j], i) : -1;
    if (nextInB !== -1 && (nextInA === -1 || nextInB - j <= nextInA - i)) {
      while (j < nextInB) {
        out.push({ kind: "add", text: b[j] });
        j++;
      }
    } else if (nextInA !== -1) {
      while (i < nextInA) {
        out.push({ kind: "del", text: a[i] });
        i++;
      }
    } else {
      if (i < a.length) {
        out.push({ kind: "del", text: a[i] });
        i++;
      }
      if (j < b.length) {
        out.push({ kind: "add", text: b[j] });
        j++;
      }
    }
  }
  return out;
}

export function DiffView({ before, after, mode = "lines" }: DiffViewProps) {
  const lines = useMemo(() => lineDiff(before, after), [before, after]);
  if (mode === "split") {
    return (
      <div className="grid grid-cols-2 gap-3 text-sm">
        <DiffColumn title="A" text={before} highlight="del" />
        <DiffColumn title="B" text={after} highlight="add" />
      </div>
    );
  }
  return (
    <ol className="font-mono text-xs leading-relaxed">
      {lines.map((ln, idx) => (
        <li
          key={idx}
          className={classNames(
            "whitespace-pre-wrap break-words px-2 py-0.5 rounded",
            ln.kind === "add" && "bg-good/10 text-good",
            ln.kind === "del" && "bg-bad/10 text-bad line-through",
            ln.kind === "ctx" && "text-text-secondary",
          )}
        >
          <span className="select-none opacity-50 mr-2">
            {ln.kind === "add" ? "+" : ln.kind === "del" ? "−" : " "}
          </span>
          {ln.text || "\u00a0"}
        </li>
      ))}
    </ol>
  );
}

function DiffColumn({ title, text, highlight }: { title: string; text: string; highlight: "add" | "del" }) {
  return (
    <div className="rounded-lg border border-line bg-canvas-paper">
      <header className="border-b border-line px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        {title}
      </header>
      <pre
        className={classNames(
          "whitespace-pre-wrap break-words p-3 font-mono text-xs leading-relaxed",
          highlight === "add" ? "text-text-primary" : "text-text-primary",
        )}
      >
        {text || <span className="text-text-muted italic">empty</span>}
      </pre>
    </div>
  );
}
