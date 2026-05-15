import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { FlaskConical, PowerOff, Wrench } from "lucide-react";
import { api, AgentRow } from "../api";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Pill } from "../components/Pill";
import { Empty } from "../components/Empty";
import { LoadingShimmer } from "../components/LoadingShimmer";
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
            title="No installed agents discovered"
            description="Install an agent on this host and it will appear here."
          />
        </Card>
      ) : (
        <Card padded={false}>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line/60 text-sm">
              <thead className="bg-canvas-subtle/60 text-left text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                <tr>
                  <th className="px-5 py-3">Agent</th>
                  <th className="px-4 py-3">Host</th>
                  <th className="px-4 py-3">Events · 24h</th>
                  <th className="px-4 py-3">Last event</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-5 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60 bg-canvas-paper">
                {listQ.data.map((agent) => (
                  <AgentTableRow
                    key={agent.name}
                    agent={agent}
                    focused={focus === agent.name}
                    onRefresh={() => listQ.refresh()}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function AgentTableRow({
  agent,
  focused,
  onRefresh,
}: {
  agent: AgentRow;
  focused: boolean;
  onRefresh: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<"" | "uninstall" | "test" | "reinstall">("");

  const status: Status = agent.installed
    ? "online"
    : agent.discovered
      ? "warn"
      : "unknown";

  const run = async (
    op: "uninstall" | "test" | "reinstall",
  ) => {
    setBusy(op);
    try {
      if (op === "reinstall") {
        await api.uninstallAgent(agent.name);
        const r = await api.installAgent(agent.name);
        toast.push("info", `${agent.label} reinstalled`, r.message);
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
    <tr className={focused ? "bg-accent-project/5 ring-1 ring-inset ring-accent-project/30" : ""}>
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-2">
          <span className="font-medium text-text-primary">{agent.label}</span>
          {agent.experimental && <Pill tone="warn">experimental</Pill>}
        </div>
        <p className="mt-0.5 font-mono text-xs text-text-muted">{agent.name}</p>
      </td>
      <td className="px-4 py-3.5 font-mono text-xs text-text-secondary">
        {agent.host || window.location.hostname || "localhost"}
      </td>
      <td className="px-4 py-3.5 tabular-nums text-text-primary">
        {formatNumber(agent.session_count_24h)}
      </td>
      <td className="px-4 py-3.5 text-text-secondary">
        {agent.last_event ? fromNow(agent.last_event) : "—"}
      </td>
      <td className="px-4 py-3.5">
        <TrafficLight
          status={status}
          pulse
          label={agent.installed ? "installed" : agent.discovered ? "discovered" : "n/a"}
        />
      </td>
      <td className="px-5 py-3.5">
        <div className="flex justify-end gap-2">
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
      </td>
    </tr>
  );
}
