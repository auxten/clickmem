import { ReactNode, useEffect } from "react";
import { X } from "lucide-react";
import { classNames } from "../lib/format";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  subtitle?: ReactNode;
  width?: "sm" | "md" | "lg";
  children?: ReactNode;
  footer?: ReactNode;
}

const WIDTHS: Record<NonNullable<DrawerProps["width"]>, string> = {
  sm: "max-w-md",
  md: "max-w-xl",
  lg: "max-w-2xl",
};

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  width = "md",
  children,
  footer,
}: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <div
      className={classNames(
        "fixed inset-0 z-50 transition-opacity",
        open ? "pointer-events-auto" : "pointer-events-none opacity-0",
      )}
      aria-hidden={!open}
    >
      <div
        className={classNames(
          "absolute inset-0 bg-ink-900/40 backdrop-blur-[2px] transition-opacity",
          open ? "opacity-100" : "opacity-0",
        )}
        onClick={onClose}
      />
      <aside
        className={classNames(
          "absolute right-0 top-0 h-full w-full bg-canvas-paper shadow-2xl border-l border-line flex flex-col transition-transform duration-200",
          WIDTHS[width],
          open ? "translate-x-0" : "translate-x-full",
        )}
        role="dialog"
        aria-modal="true"
      >
        <header className="flex items-start gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1">
            {title && (
              <h2 className="text-sm font-semibold text-text-primary truncate">{title}</h2>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-text-muted">{subtitle}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-text-muted hover:bg-canvas-subtle hover:text-text-primary"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="clickmem-scroll flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <footer className="border-t border-line bg-canvas px-5 py-3">{footer}</footer>
        )}
      </aside>
    </div>
  );
}
