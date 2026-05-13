import { ReactNode } from "react";
import { classNames, formatDelta, formatNumber } from "../lib/format";
import { SparkLine } from "./SparkLine";

interface KPIProps {
  label: string;
  value: number | string;
  delta?: number;
  hint?: ReactNode;
  spark?: number[];
  accent?: "ink" | "project" | "kind" | "privacy" | "source";
  size?: "sm" | "md" | "lg";
}

const ACCENTS: Record<NonNullable<KPIProps["accent"]>, string> = {
  ink: "text-ink-900",
  project: "text-accent-project",
  kind: "text-accent-kind",
  privacy: "text-accent-privacy",
  source: "text-accent-source",
};

export function KPI({
  label,
  value,
  delta,
  hint,
  spark,
  accent = "ink",
  size = "md",
}: KPIProps) {
  const d = delta !== undefined ? formatDelta(delta) : null;
  const valueText = typeof value === "number" ? formatNumber(value) : value;
  return (
    <div className="flex flex-col">
      <p className="text-xs uppercase tracking-wide text-text-muted">{label}</p>
      <div className="mt-1 flex items-end gap-2.5">
        <span
          className={classNames(
            "font-semibold tabular-nums leading-tight",
            ACCENTS[accent],
            size === "lg"
              ? "text-4xl md:text-5xl"
              : size === "sm"
                ? "text-xl"
                : "text-2xl",
          )}
        >
          {valueText}
        </span>
        {d && (
          <span
            className={classNames(
              "text-xs font-medium mb-1",
              d.tone === "good" && "text-good",
              d.tone === "bad" && "text-bad",
              d.tone === "flat" && "text-text-muted",
            )}
          >
            {d.text}
          </span>
        )}
        {spark && spark.length > 0 && (
          <div className={classNames("ml-auto mb-1", ACCENTS[accent])}>
            <SparkLine values={spark} width={120} height={32} />
          </div>
        )}
      </div>
      {hint && <p className="mt-1 text-xs text-text-muted">{hint}</p>}
    </div>
  );
}
