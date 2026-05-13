import { FormEvent, useState } from "react";
import { KeyRound } from "lucide-react";
import { Button } from "./Button";
import { setApiKey } from "../api";

interface AuthModalProps {
  open: boolean;
  onSaved: () => void;
}

/**
 * Shown on first launch only when `GET /v1/health` returns 401. The server is
 * loopback-open by default so most local installs never see this; remote /
 * LAN installs land here once and never again.
 */
export function AuthModal({ open, onSaved }: AuthModalProps) {
  const [value, setValue] = useState("");
  if (!open) return null;
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setApiKey(value.trim());
    onSaved();
  };
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-ink-900/60 backdrop-blur-sm px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md rounded-2xl bg-canvas-paper border border-line shadow-2xl p-6"
      >
        <div className="flex items-center gap-2 mb-4">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-canvas-subtle text-ink-700">
            <KeyRound size={18} />
          </span>
          <div className="leading-tight">
            <h2 className="text-base font-semibold text-text-primary">
              ClickMem API key required
            </h2>
            <p className="text-xs text-text-muted">
              Your server requested a bearer token. Set <code>CLICKMEM_API_KEY</code> on the
              server, then paste the same value here.
            </p>
          </div>
        </div>
        <input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="paste API key"
          className="w-full rounded-lg border border-line bg-canvas-paper px-3 py-2 text-sm font-mono focus:border-ink-500 focus:outline-none focus:ring-2 focus:ring-ink-500/20"
        />
        <div className="mt-4 flex items-center justify-end gap-2">
          <Button variant="ghost" type="button" onClick={onSaved}>
            Skip
          </Button>
          <Button variant="primary" type="submit">
            Save &amp; reload
          </Button>
        </div>
      </form>
    </div>
  );
}
