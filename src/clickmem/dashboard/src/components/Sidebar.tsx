import { NavLink } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  Boxes,
  Cog,
  Database,
  FileText,
  Import,
  LayoutDashboard,
  Library,
  Pin,
  ShieldX,
  Sparkles,
} from "lucide-react";
import { classNames } from "../lib/format";
import { TrafficLight } from "./TrafficLight";

interface SidebarProps {
  health: { ok: boolean; backend?: string; version?: string } | null;
  pinnedRecentCount: number;
  agentsOnline: number;
  agentsTotal: number;
  identity?: string;
}

const ICON_SIZE = 16;

interface NavItem {
  to: string;
  icon: JSX.Element;
  label: string;
  end?: boolean;
  badge?: number;
}

export function Sidebar({
  health,
  pinnedRecentCount,
  agentsOnline,
  agentsTotal,
  identity,
}: SidebarProps) {
  const main: NavItem[] = [
    { to: "/", icon: <LayoutDashboard size={ICON_SIZE} />, label: "Overview", end: true },
    { to: "/memories", icon: <Library size={ICON_SIZE} />, label: "Memories" },
    { to: "/conflicts", icon: <AlertTriangle size={ICON_SIZE} />, label: "Conflicts" },
    { to: "/recall", icon: <Sparkles size={ICON_SIZE} />, label: "Recall Lab" },
    { to: "/raw", icon: <FileText size={ICON_SIZE} />, label: "Raw transcripts" },
  ];
  const manage: NavItem[] = [
    { to: "/pinned", icon: <Pin size={ICON_SIZE} />, label: "Pinned", badge: pinnedRecentCount },
    { to: "/imports", icon: <Import size={ICON_SIZE} />, label: "Imports" },
    { to: "/agents", icon: <Activity size={ICON_SIZE} />, label: "Agents" },
    { to: "/blacklist", icon: <ShieldX size={ICON_SIZE} />, label: "Blacklist" },
  ];
  const settings: NavItem[] = [
    { to: "/preferences", icon: <Cog size={ICON_SIZE} />, label: "Preferences" },
  ];

  return (
    <aside className="hidden md:flex md:w-60 lg:w-64 shrink-0 flex-col bg-ink-900 text-text-inverse shadow-sidebar">
      <div className="px-5 pt-5 pb-4">
        <div className="flex items-center gap-2.5">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-text-inverse/10 ring-1 ring-text-inverse/15">
            <Database size={18} className="text-text-inverse" />
          </span>
          <div className="leading-tight">
            <p className="text-base font-semibold tracking-tight">ClickMem</p>
            <p className="text-[11px] text-text-inverse/55">local memory</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto clickmem-scroll px-3 pb-4">
        <Section title="Main">
          {main.map((it) => (
            <Item key={it.to} item={it} />
          ))}
        </Section>
        <Section title="Manage">
          {manage.map((it) => (
            <Item key={it.to} item={it} />
          ))}
        </Section>
        <Section title="Settings">
          {settings.map((it) => (
            <Item key={it.to} item={it} />
          ))}
        </Section>
      </nav>

      <footer className="border-t border-text-inverse/10 px-4 py-3 text-xs text-text-inverse/75">
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Boxes size={12} className="text-text-inverse/55" />
            <span className="capitalize">
              {(health?.backend || "—") + " backend"}
            </span>
            <TrafficLight
              status={health?.ok ? "online" : "offline"}
              label={health?.ok ? "connected" : "offline"}
              pulse
              className="ml-auto text-text-inverse/80"
            />
          </div>
          <div className="flex items-center gap-1.5">
            <Activity size={12} className="text-text-inverse/55" />
            <span>
              agents{" "}
              <span className="tabular-nums">
                {agentsOnline}/{agentsTotal}
              </span>
            </span>
            <TrafficLight
              status={
                agentsOnline > 0
                  ? "online"
                  : agentsTotal > 0
                    ? "warn"
                    : "unknown"
              }
              className="ml-auto text-text-inverse/80"
            />
          </div>
          <div className="mt-2 truncate text-[11px] text-text-inverse/50">
            {identity ? identity : "API key not set"}
          </div>
        </div>
      </footer>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <p className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-text-inverse/45">
        {title}
      </p>
      <ul className="space-y-0.5">{children}</ul>
    </div>
  );
}

function Item({ item }: { item: NavItem }) {
  return (
    <li>
      <NavLink
        to={item.to}
        end={item.end}
        className={({ isActive }) =>
          classNames(
            "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors",
            isActive
              ? "bg-text-inverse/10 text-text-inverse"
              : "text-text-inverse/75 hover:bg-text-inverse/5 hover:text-text-inverse",
          )
        }
      >
        <span className="opacity-80 group-hover:opacity-100">{item.icon}</span>
        <span className="flex-1 truncate">{item.label}</span>
        {item.badge ? (
          <span className="ml-auto inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-text-inverse/15 px-1.5 text-[10px] tabular-nums">
            {item.badge}
          </span>
        ) : null}
      </NavLink>
    </li>
  );
}
