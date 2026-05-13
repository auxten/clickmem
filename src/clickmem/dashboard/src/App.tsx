import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Plus, RefreshCw } from "lucide-react";
import { api, ApiError, getApiKey, HealthInfo } from "./api";
import { useApi } from "./hooks/useApi";
import { Sidebar } from "./components/Sidebar";
import { Button } from "./components/Button";
import { AuthModal } from "./components/AuthModal";
import { ToastProvider, useToast } from "./components/Toast";
import OverviewPage from "./pages/Overview";
import MemoriesPage from "./pages/Memories";
import ConflictsPage from "./pages/Conflicts";
import RecallLabPage from "./pages/RecallLab";
import RawTranscriptsPage from "./pages/RawTranscripts";
import AgentsPage from "./pages/Agents";
import ImportsPage from "./pages/Imports";
import BlacklistPage from "./pages/Blacklist";
import PreferencesPage from "./pages/Preferences";

const PAGE_TITLES: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Overview", subtitle: "Health of the explicit memory you've committed" },
  "/memories": { title: "Memories", subtitle: "Every Expand / Revise / Contract you've recorded" },
  "/conflicts": { title: "Conflicts", subtitle: "Pairs the system flagged as semantically clashing" },
  "/recall": { title: "Recall Lab", subtitle: "Inspect scoring step by step" },
  "/raw": { title: "Raw transcripts", subtitle: "Cold storage — never read by recall" },
  "/pinned": { title: "Pinned", subtitle: "User-curated memories immune to revision" },
  "/imports": { title: "Imports", subtitle: "Per-adapter ingestion view" },
  "/agents": { title: "Agents", subtitle: "Adapter health and hook install state" },
  "/blacklist": { title: "Blacklist", subtitle: "Patterns refused on insert and on recall" },
  "/preferences": { title: "Preferences", subtitle: "Backend, embedding model, auth" },
};

export default function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}

function Shell() {
  const [authOpen, setAuthOpen] = useState(false);
  const [authBypass, setAuthBypass] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [hasInitialAuthCheck, setHasInitialAuthCheck] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();

  const healthQ = useApi<HealthInfo>(() => api.health(), {
    pollMs: 20_000,
    deps: [refreshTick],
  });

  useEffect(() => {
    if (hasInitialAuthCheck) return;
    if (healthQ.error && (healthQ.error as ApiError).status === 401 && !getApiKey() && !authBypass) {
      setAuthOpen(true);
      setHasInitialAuthCheck(true);
    } else if (healthQ.data || healthQ.error) {
      setHasInitialAuthCheck(true);
    }
  }, [healthQ.data, healthQ.error, hasInitialAuthCheck, authBypass]);

  const agentsQ = useApi(() => api.listAgents(), {
    pollMs: 30_000,
    deps: [refreshTick],
  });

  const pinnedRecentQ = useApi(
    () =>
      api
        .listMemories({
          pinned: true,
          status: "active",
          limit: 50,
        })
        .then((r) => r.items),
    { pollMs: 60_000, deps: [refreshTick] },
  );

  const pinnedRecentCount = useMemo(() => {
    const items = pinnedRecentQ.data || [];
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    return items.filter((m) => {
      const d = new Date(m.updated_at.replace(" ", "T") + "Z").getTime();
      return Number.isFinite(d) && d >= cutoff;
    }).length;
  }, [pinnedRecentQ.data]);

  const agentsOnline = useMemo(
    () => (agentsQ.data || []).filter((a) => a.session_count_24h > 0).length,
    [agentsQ.data],
  );
  const agentsTotal = (agentsQ.data || []).length;

  const identity = useMemo(() => {
    const key = getApiKey();
    if (!key) return "loopback (no auth)";
    return `key …${key.slice(-4)}`;
  }, [authOpen]);

  const onRefresh = useCallback(() => {
    setRefreshTick((n) => n + 1);
    toast.push("info", "Refreshed");
  }, [toast]);

  const onAddMemory = useCallback(() => {
    navigate("/memories?new=1");
  }, [navigate]);

  const pathKey = location.pathname.startsWith("/memories")
    ? "/memories"
    : location.pathname.startsWith("/recall")
      ? "/recall"
      : location.pathname.startsWith("/raw")
        ? "/raw"
        : location.pathname.startsWith("/conflicts")
          ? "/conflicts"
          : location.pathname.startsWith("/pinned")
            ? "/pinned"
            : location.pathname.startsWith("/imports")
              ? "/imports"
              : location.pathname.startsWith("/agents")
                ? "/agents"
                : location.pathname.startsWith("/blacklist")
                  ? "/blacklist"
                  : location.pathname.startsWith("/preferences")
                    ? "/preferences"
                    : "/";
  const title = PAGE_TITLES[pathKey] ?? { title: pathKey, subtitle: "" };

  return (
    <div className="flex h-full min-h-screen bg-canvas text-text-primary">
      <Sidebar
        health={healthQ.data}
        pinnedRecentCount={pinnedRecentCount}
        agentsOnline={agentsOnline}
        agentsTotal={agentsTotal}
        identity={identity}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          title={title.title}
          subtitle={title.subtitle}
          lastUpdated={
            healthQ.data ? new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : null
          }
          onRefresh={onRefresh}
          onAddMemory={onAddMemory}
        />
        <main className="flex-1 overflow-y-auto clickmem-scroll px-6 py-6">
          <Routes>
            <Route path="/" element={<OverviewPage refreshTick={refreshTick} agents={agentsQ.data || []} />} />
            <Route path="/memories" element={<MemoriesPage refreshTick={refreshTick} />} />
            <Route path="/pinned" element={<MemoriesPage refreshTick={refreshTick} forcePinned />} />
            <Route path="/conflicts" element={<ConflictsPage refreshTick={refreshTick} />} />
            <Route path="/recall" element={<RecallLabPage />} />
            <Route path="/raw" element={<RawTranscriptsPage refreshTick={refreshTick} />} />
            <Route path="/agents" element={<AgentsPage refreshTick={refreshTick} />} />
            <Route path="/imports" element={<ImportsPage refreshTick={refreshTick} />} />
            <Route path="/blacklist" element={<BlacklistPage refreshTick={refreshTick} />} />
            <Route path="/preferences" element={<PreferencesPage health={healthQ.data} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
      <AuthModal
        open={authOpen}
        onSaved={() => {
          setAuthOpen(false);
          setAuthBypass(true);
          setRefreshTick((n) => n + 1);
        }}
      />
    </div>
  );
}

function TopBar({
  title,
  subtitle,
  lastUpdated,
  onRefresh,
  onAddMemory,
}: {
  title: string;
  subtitle: string;
  lastUpdated: string | null;
  onRefresh: () => void;
  onAddMemory: () => void;
}) {
  return (
    <header className="flex items-center gap-4 border-b border-line bg-canvas-paper/80 backdrop-blur px-6 py-3.5">
      <div className="min-w-0">
        <h1 className="text-base font-semibold text-text-primary leading-tight">{title}</h1>
        <p className="text-xs text-text-muted truncate">{subtitle}</p>
      </div>
      <div className="ml-auto flex items-center gap-2">
        {lastUpdated && (
          <span className="hidden sm:inline text-xs text-text-muted">
            Last updated <span className="tabular-nums">{lastUpdated}</span>
          </span>
        )}
        <Button variant="ghost" icon={<RefreshCw size={14} />} onClick={onRefresh}>
          Refresh
        </Button>
        <Button variant="primary" icon={<Plus size={14} />} onClick={onAddMemory}>
          Add Memory
        </Button>
      </div>
    </header>
  );
}
