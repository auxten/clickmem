import { classNames } from "../lib/format";

interface LoadingShimmerProps {
  lines?: number;
  className?: string;
  height?: string;
}

export function LoadingShimmer({ lines = 3, className, height = "h-4" }: LoadingShimmerProps) {
  return (
    <div className={classNames("space-y-2.5", className)} role="status" aria-label="loading">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className={classNames(
            "shimmer rounded-md",
            height,
            i === lines - 1 ? "w-1/2" : "w-full",
          )}
        />
      ))}
    </div>
  );
}
