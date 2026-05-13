import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { classNames } from "../lib/format";

type ToastKind = "success" | "error" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  detail?: string;
}

interface ToastContextValue {
  push: (kind: ToastKind, message: string, detail?: string) => void;
}

const Ctx = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(1);

  const push = useCallback((kind: ToastKind, message: string, detail?: string) => {
    const id = idRef.current++;
    setItems((prev) => [...prev, { id, kind, message, detail }]);
    setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="fixed top-4 right-4 z-[60] flex flex-col gap-2 max-w-sm">
        {items.map((t) => (
          <ToastNode key={t.id} item={t} onDismiss={() => setItems((p) => p.filter((x) => x.id !== t.id))} />
        ))}
      </div>
    </Ctx.Provider>
  );
}

function ToastNode({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  useEffect(() => {
    // entrance animation handled via Tailwind transition utilities
  }, []);
  const tone =
    item.kind === "success"
      ? "border-good/30 bg-good/5 text-text-primary"
      : item.kind === "error"
        ? "border-bad/30 bg-bad/5 text-text-primary"
        : "border-line bg-canvas-paper text-text-primary";
  const icon =
    item.kind === "success" ? (
      <CheckCircle2 className="h-4 w-4 text-good" />
    ) : item.kind === "error" ? (
      <AlertTriangle className="h-4 w-4 text-bad" />
    ) : (
      <Info className="h-4 w-4 text-ink-500" />
    );
  return (
    <div
      className={classNames(
        "pointer-events-auto flex items-start gap-2 rounded-xl border px-3 py-2.5 shadow-card",
        tone,
      )}
      role="status"
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-5">{item.message}</p>
        {item.detail && (
          <p className="mt-0.5 text-xs text-text-muted break-words">{item.detail}</p>
        )}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="text-text-muted hover:text-text-primary"
        aria-label="Dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
