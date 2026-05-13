import { ReactNode } from "react";
import { classNames } from "../lib/format";

interface CardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  padded?: boolean;
}

export function Card({
  title,
  subtitle,
  action,
  children,
  className,
  padded = true,
}: CardProps) {
  return (
    <section
      className={classNames(
        "bg-canvas-paper border border-line rounded-2xl shadow-card transition-shadow hover:shadow-cardHover",
        className,
      )}
    >
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 px-5 pt-4 pb-3 border-b border-line/60">
          <div className="min-w-0">
            {title && (
              <h2 className="text-sm font-semibold text-text-primary truncate">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="text-xs text-text-muted mt-0.5 truncate">{subtitle}</p>
            )}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}
      <div className={padded ? "p-5" : ""}>{children}</div>
    </section>
  );
}
