import { ReactNode } from "react";
import { classNames } from "../lib/format";

type Tone =
  | "neutral"
  | "good"
  | "warn"
  | "bad"
  | "project"
  | "privacy"
  | "kind"
  | "source"
  | "info";

interface PillProps {
  tone?: Tone;
  children: ReactNode;
  icon?: ReactNode;
  className?: string;
  title?: string;
  onClick?: () => void;
}

const TONES: Record<Tone, string> = {
  neutral: "bg-canvas-subtle text-text-secondary border border-line",
  good: "bg-good/10 text-good border border-good/20",
  warn: "bg-warn/10 text-warn border border-warn/25",
  bad: "bg-bad/10 text-bad border border-bad/20",
  project: "bg-accent-project/10 text-accent-project border border-accent-project/20",
  privacy: "bg-accent-privacy/10 text-accent-privacy border border-accent-privacy/25",
  kind: "bg-accent-kind/10 text-accent-kind border border-accent-kind/20",
  source: "bg-accent-source/10 text-accent-source border border-accent-source/25",
  info: "bg-ink-700/10 text-ink-700 border border-ink-700/20",
};

export function Pill({ tone = "neutral", children, icon, className, title, onClick }: PillProps) {
  const Comp = onClick ? "button" : "span";
  return (
    <Comp
      title={title}
      onClick={onClick}
      className={classNames(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium leading-4",
        TONES[tone],
        onClick && "hover:brightness-105 cursor-pointer",
        className,
      )}
    >
      {icon && <span className="inline-flex w-3 h-3 items-center">{icon}</span>}
      {children}
    </Comp>
  );
}
