import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Download, FlaskConical, PowerOff, Wrench } from "lucide-react";
import { api, AgentRow } from "../api";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
import { SparkLine } from "../components/SparkLine";
import { TrafficLight, Status } from "../components/TrafficLight";
import { useToast } from "../components/Toast";
import { formatNumber, fromNow } from "../lib/format";

interface Props {
  refreshTick: number;
}

export default function AgentsPage({ refreshTick }: Props) {
  const [params] = useSearchParams();
  const focus = params.get("focus") || "";
  const listQ = useApi(() => api.listAgents(), { deps: [refreshTick] });

  return (
    <div className="space-y-5">
      {listQ.loading && !listQ.data ? (
        <Card>
          <LoadingShimmer lines={6} />
        </Card>
      ) : !listQ.data || listQ.data.length === 0 ? (
        <Card>
          <Empty
            title="No adapters discovered"
            description="ClickMem's built-in adapters appear here as the registry lights up."
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {listQ.data.map((agent) => (
            <AgentCard
              key={agent.name}
              agent={agent}
              focused={focus === agent.name}
              onRefresh={() => listQ.refresh()}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentCard({
  agent,
  focused,
  onRefresh,
}: {
  agent: AgentRow;
  focused: boolean;
  onRefresh: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<"" | "install" | "uninstall" | "test" | "reinstall">("");

  const activityQ = useApi(() => api.agentActivity(agent.name, 24), {
    deps: [agent.name],
  });

  const status: Status = agent.installed
    ? "online"
    : agent.discovered
      ? "warn"
      : "unknown";

  const total24h = useMemo(
    () => (activityQ.data || []).reduce((s, b) => s + b.count, 0),
    [activityQ.data],
  );

  const run = async (
    op: "install" | "uninstall" | "test" | "reinstall",
  ) => {
    setBusy(op);
    try {
      if (op === "reinstall") {
        await api.uninstallAgent(agent.name);
        const r = await api.installAgent(agent.name);
        toast.push("info", `${agent.label} reinstalled`, r.message);
      } else if (op === "install") {
        const r = await api.installAgent(agent.name);
        toast.push("info", `${agent.label} install`, r.message);
      } else if (op === "uninstall") {
        const r = await api.uninstallAgent(agent.name);
        toast.push("info", `${agent.label} uninstall`, r.message);
      } else if (op === "test") {
        const r = await api.testAgent(agent.name);
        toast.push(r.ok ? "success" : "info", `${agent.label} test`, r.message);
      }
      onRefresh();
    } catch (e) {
      toast.push("error", `${agent.label} ${op} failed`, (e as Error).message);
    } finally {
      setBusy("");
    }
  };

  return (
    <Card
      className={focused ? "ring-2 ring-accent-project/40" : ""}
      title={
        <span className="inline-flex items-center gap-2">
          {agent.label}
          {agent.experimental && (
            <Pill tone="warn">experimental</Pill>
          )}
        </span>
      }
      subtitle={`adapter · ${agent.name}`}
      action={<TrafficLight status={status} pulse label={agent.installed ? "installed" : agent.discovered ? "discovered" : "n/a"} />}
    >
      <div className="grid grid-cols-3 gap-3 text-sm">
        <Stat label="Sessions · 24h" value={formatNumber(agent.session_count_24h)} />
        <Stat label="Events · 24h" value={formatNumber(total24h)} />
        <Stat
          label="Last event"
          value={agent.last_event ? fromNow(agent.last_event) : "—"}
        />
      </div>

      <div className="mt-4 text-accent-project">
        <SparkLine
          values={
            activityQ.data && activityQ.data.length > 0
              ? activityQ.data.map((b) => b.count)
              : []
          }
          width={420}
          height={48}
          ariaLabel={`${agent.label} 24h activity`}
        />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="primary"
          icon={<Download size={12} />}
          loading={busy === "install"}
          disabled={agent.installed}
          onClick={() => run("install")}
        >
          Install
        </Button>
        <Button
          size="sm"
          variant="outline"
          icon={<Wrench size={12} />}
          loading={busy === "reinstall"}
          onClick={() => run("reinstall")}
        >
          Reinstall
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon={<PowerOff size={12} />}
          loading={busy === "uninstall"}
          onClick={() => run("uninstall")}
        >
          Uninstall
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon={<FlaskConical size={12} />}
          loading={busy === "test"}
          onClick={() => run("test")}
          className="ml-auto"
        >
          Test
        </Button>
      </div>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-canvas-paper px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-text-muted">{label}</p>
      <p className="mt-1 text-sm font-semibold tabular-nums text-text-primary">{value}</p>
    </div>
  );
}
