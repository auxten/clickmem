import { useMemo } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { formatNumber } from "../lib/format";

interface DonutDatum {
  label: string;
  value: number;
  color: string;
}

interface DonutProps {
  data: DonutDatum[];
  height?: number;
  centerLabel?: string;
  centerValue?: string | number;
}

export function Donut({ data, height = 200, centerLabel, centerValue }: DonutProps) {
  const total = useMemo(() => data.reduce((acc, d) => acc + d.value, 0), [data]);

  return (
    <div className="flex flex-col md:flex-row items-center gap-5">
      <div className="relative" style={{ width: height, height }}>
        <ResponsiveContainer width={height} height={height}>
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="label"
              innerRadius={Math.floor(height * 0.32)}
              outerRadius={Math.floor(height * 0.46)}
              startAngle={90}
              endAngle={-270}
              paddingAngle={2}
              stroke="none"
            >
              {data.map((d) => (
                <Cell key={d.label} fill={d.color} />
              ))}
            </Pie>
            <Tooltip
              cursor={false}
              contentStyle={{
                borderRadius: 12,
                border: "1px solid #e6e8ef",
                fontSize: 12,
                padding: "6px 10px",
              }}
              formatter={(value: number, name: string) => [formatNumber(value), name]}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-semibold text-text-primary tabular-nums">
            {centerValue ?? formatNumber(total)}
          </span>
          {centerLabel && (
            <span className="text-[11px] uppercase tracking-wide text-text-muted">
              {centerLabel}
            </span>
          )}
        </div>
      </div>

      <ul className="flex-1 space-y-2 w-full md:w-auto">
        {data.map((d) => {
          const pct = total > 0 ? (d.value / total) * 100 : 0;
          return (
            <li key={d.label} className="flex items-center gap-2 text-sm">
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: d.color }}
              />
              <span className="flex-1 text-text-primary capitalize truncate">{d.label}</span>
              <span className="text-text-secondary tabular-nums">{formatNumber(d.value)}</span>
              <span className="text-text-muted text-xs tabular-nums w-10 text-right">
                {pct.toFixed(0)}%
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
