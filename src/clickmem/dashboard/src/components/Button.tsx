import { ButtonHTMLAttributes, ReactNode, forwardRef } from "react";
import { classNames } from "../lib/format";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  loading?: boolean;
}

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-ink-900 text-text-inverse hover:bg-ink-800 focus-visible:ring-ink-700 border border-ink-900",
  secondary:
    "bg-canvas-paper text-text-primary hover:bg-canvas-subtle border border-line focus-visible:ring-line-strong",
  outline:
    "bg-transparent text-text-primary hover:bg-canvas-subtle border border-line focus-visible:ring-line-strong",
  ghost:
    "bg-transparent text-text-secondary hover:bg-canvas-subtle hover:text-text-primary border border-transparent focus-visible:ring-line-strong",
  danger:
    "bg-bad/10 text-bad hover:bg-bad/15 border border-bad/20 focus-visible:ring-bad/30",
};

const SIZES: Record<Size, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-3.5 text-sm",
  lg: "h-10 px-4 text-sm",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    icon,
    loading,
    className,
    children,
    disabled,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={classNames(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas disabled:opacity-50 disabled:cursor-not-allowed",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading ? (
        <span className="inline-block w-3.5 h-3.5 rounded-full border-2 border-current border-r-transparent animate-spin" />
      ) : (
        icon && <span className="shrink-0 inline-flex items-center">{icon}</span>
      )}
      {children}
    </button>
  );
});
