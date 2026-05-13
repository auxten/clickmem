/**
 * Small formatting helpers used throughout the dashboard. Centralised here so
 * the same number / date / relative-time behaviour is applied everywhere.
 */

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (Math.abs(n) >= 10_000) return (n / 1_000).toFixed(1) + "k";
  return Math.round(n).toLocaleString();
}

export function formatPercent(n: number, digits = 0): string {
  if (Number.isNaN(n) || !Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function formatDelta(delta: number): { text: string; tone: "good" | "bad" | "flat" } {
  if (!Number.isFinite(delta)) return { text: "—", tone: "flat" };
  if (delta > 0) return { text: `+${delta.toFixed(0)}%`, tone: "good" };
  if (delta < 0) return { text: `${delta.toFixed(0)}%`, tone: "bad" };
  return { text: "0%", tone: "flat" };
}

export function parseServerDate(s: string | undefined | null): Date | null {
  if (!s) return null;
  const normalised = s.includes("T") ? s : s.replace(" ", "T");
  const withZ = /[zZ]|[+\-]\d{2}:?\d{2}$/.test(normalised) ? normalised : normalised + "Z";
  const d = new Date(withZ);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fromNow(s: string | undefined | null): string {
  const d = parseServerDate(s);
  if (!d) return "—";
  const ms = Date.now() - d.getTime();
  if (ms < 0) return "in the future";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  const mon = Math.floor(day / 30);
  if (mon < 12) return `${mon}mo ago`;
  const yr = Math.floor(mon / 12);
  return `${yr}y ago`;
}

export function shortDate(s: string | undefined | null): string {
  const d = parseServerDate(s);
  if (!d) return "—";
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function preview(text: string, n = 140): string {
  const t = (text || "").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n).trim() + "…" : t;
}

export function classNames(...xs: Array<string | false | null | undefined>): string {
  return xs.filter(Boolean).join(" ");
}
