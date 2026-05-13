import { useMemo } from "react";

interface SparkLineProps {
  values: number[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
  showArea?: boolean;
  ariaLabel?: string;
}

/**
 * Lightweight inline SVG sparkline — used inside table cells / KPI heros where
 * Recharts' overhead would be wasteful. Auto-scales to the data extent.
 */
export function SparkLine({
  values,
  width = 96,
  height = 28,
  stroke = "currentColor",
  fill,
  showArea = true,
  ariaLabel,
}: SparkLineProps) {
  const { path, area } = useMemo(() => {
    if (!values || values.length === 0) return { path: "", area: "" };
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = values.length > 1 ? width / (values.length - 1) : width;
    const points = values.map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / span) * height;
      return [x, y] as const;
    });
    const path = points
      .map(([x, y], i) => (i === 0 ? `M${x.toFixed(1)} ${y.toFixed(1)}` : `L${x.toFixed(1)} ${y.toFixed(1)}`))
      .join(" ");
    const area = `${path} L${width} ${height} L0 ${height} Z`;
    return { path, area };
  }, [values, width, height]);

  if (!values || values.length === 0) {
    return (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel || "sparkline"}
        className="text-line"
      >
        <line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke="currentColor" strokeDasharray="2 3" />
      </svg>
    );
  }

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label={ariaLabel || "sparkline"}
      preserveAspectRatio="none"
    >
      {showArea && (
        <path d={area} fill={fill || "currentColor"} fillOpacity={0.1} />
      )}
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
