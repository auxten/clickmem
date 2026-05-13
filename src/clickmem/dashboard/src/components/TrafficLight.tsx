import { classNames } from "../lib/format";

export type Status = "online" | "warn" | "offline" | "unknown";

const COLORS: Record<Status, string> = {
  online: "bg-good",
  warn: "bg-warn",
  offline: "bg-bad",
  unknown: "bg-line-strong",
};

const RINGS: Record<Status, string> = {
  online: "ring-good/30",
  warn: "ring-warn/30",
  offline: "ring-bad/30",
  unknown: "ring-line/40",
};

interface TrafficLightProps {
  status: Status;
  label?: string;
  pulse?: boolean;
  className?: string;
}

export function TrafficLight({ status, label, pulse, className }: TrafficLightProps) {
  return (
    <span
      className={classNames(
        "inline-flex items-center gap-1.5 text-xs text-text-secondary",
        className,
      )}
      title={label || status}
    >
      <span
        className={classNames(
          "inline-block w-2 h-2 rounded-full ring-2",
          COLORS[status],
          RINGS[status],
          pulse && status === "online" && "animate-pulse",
        )}
      />
      {label && <span className="truncate">{label}</span>}
    </span>
  );
}
