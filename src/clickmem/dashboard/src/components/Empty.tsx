import { ReactNode } from "react";
import { Inbox } from "lucide-react";

interface EmptyProps {
  title?: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
}

export function Empty({ title = "Nothing here yet", description, icon, action }: EmptyProps) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-center text-text-muted">
      <span className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-full bg-canvas-subtle text-text-secondary">
        {icon ?? <Inbox className="h-5 w-5" aria-hidden />}
      </span>
      <p className="text-sm font-medium text-text-primary">{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-xs leading-5 text-text-muted">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
