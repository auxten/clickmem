import { useMemo } from "react";
import { formatNumber } from "../lib/format";

export interface StackedRow {
  label: string;
  parts: Array<{ key: string; value: number; color: string }>;
}

interface StackedBarProps {
  rows: StackedRow[];
  legend?: Array<{ key: string; label: string; color: string }>;
}

/**
 * Horizontal stacked-bar chart used by Privacy × Project mix on Overview. The
 * vertical axis is the row label and each segment width is proportional to the
 * part value over the row's total.
 */
export function StackedBar({ rows, legend }: StackedBarProps) {
  const maxTotal = useMemo(
    () => Math.max(1, ...rows.map((r) => r.parts.reduce((a, p) => a + p.value, 0))),
    [rows],
  );

  return (
    <div className="space-y-3">
      {legend && legend.length > 0 && (
        <ul className="flex flex-wrap gap-3 text-xs text-text-secondary">
          {legend.map((l) => (
            <li key={l.key} className="inline-flex items-center gap-1.5">
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: l.color }}
              />
              <span className="capitalize">{l.label}</span>
            </li>
          ))}
        </ul>
      )}
      <ul className="space-y-2">
        {rows.map((row) => {
          const total = row.parts.reduce((a, p) => a + p.value, 0);
          return (
            <li key={row.label} className="flex items-center gap-3">
              <span className="w-28 shrink-0 truncate text-xs text-text-secondary" title={row.label}>
                {row.label || "global"}
              </span>
              <div className="flex h-3 flex-1 overflow-hidden rounded-full bg-canvas-subtle">
                {row.parts.map((p) =>
                  p.value > 0 ? (
                    <div
                      key={p.key}
                      title={`${p.key}: ${formatNumber(p.value)}`}
                      style={{
                        width: `${(p.value / maxTotal) * 100}%`,
                        backgroundColor: p.color,
                      }}
                    />
                  ) : null,
                )}
              </div>
              <span className="w-12 text-right text-xs tabular-nums text-text-secondary">
                {formatNumber(total)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
