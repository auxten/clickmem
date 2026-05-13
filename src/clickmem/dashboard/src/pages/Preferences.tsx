import { useState } from "react";
import { Eye, EyeOff, Save } from "lucide-react";
import { HealthInfo, getApiKey, setApiKey } from "../api";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { useToast } from "../components/Toast";
import { Pill } from "../components/Pill";

interface Props {
  health: HealthInfo | null;
}

export default function PreferencesPage({ health }: Props) {
  const toast = useToast();
  const [showKey, setShowKey] = useState(false);
  const [keyDraft, setKeyDraft] = useState(getApiKey());

  const saveKey = () => {
    setApiKey(keyDraft.trim());
    toast.push("success", "API key saved");
  };

  return (
    <div className="space-y-5">
      <Card title="Runtime" subtitle="Reflects what the server reports from /v1/health">
        <dl className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Row label="Server version">
            <code>{health?.version || "—"}</code>
          </Row>
          <Row label="Backend">
            <Pill tone={health?.backend === "local" ? "good" : "info"}>
              {health?.backend || "—"}
            </Pill>
          </Row>
          <Row label="Embedding model">
            <code className="text-xs">{health?.embedding_model || "—"}</code>
          </Row>
          <Row label="Embedding dim">
            <code className="text-xs">{health?.embedding_dim ?? "—"}</code>
          </Row>
        </dl>
      </Card>

      <Card title="Bearer token" subtitle="Sent as Authorization: Bearer …">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[240px]">
            <input
              type={showKey ? "text" : "password"}
              value={keyDraft}
              onChange={(e) => setKeyDraft(e.target.value)}
              placeholder="leave empty for loopback-open setups"
              className="w-full rounded-lg border border-line bg-canvas-paper py-2 pl-3 pr-10 text-sm font-mono"
            />
            <button
              type="button"
              onClick={() => setShowKey((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-text-muted hover:bg-canvas-subtle hover:text-text-primary"
              aria-label="toggle key visibility"
            >
              {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
          <Button variant="primary" icon={<Save size={12} />} onClick={saveKey}>
            Save
          </Button>
        </div>
        <p className="mt-2 text-xs text-text-muted">
          Stored in <code>localStorage.CLICKMEM_API_KEY</code> on this browser only.
          Set <code>CLICKMEM_API_KEY</code> on the server to require it.
        </p>
      </Card>

      <Card title="Configuration reference" subtitle="All settings are env-var driven; the server is the source of truth">
        <dl className="grid grid-cols-1 gap-4 text-sm md:grid-cols-2">
          <EnvRow name="CLICKMEM_BACKEND" def="local" help="`local` (chDB) or `clickhouse`" />
          <EnvRow name="CLICKMEM_DB_PATH" def="~/.clickmem/data" help="chDB data dir for the local backend" />
          <EnvRow name="CLICKMEM_CH_URL" def="—" help="ClickHouse Cloud / self-hosted URL" />
          <EnvRow name="CLICKMEM_CH_USER" def="—" help="ClickHouse user" />
          <EnvRow name="CLICKMEM_CH_PASSWORD" def="—" help="ClickHouse password (do not commit)" />
          <EnvRow name="CLICKMEM_CH_DATABASE" def="clickmem" help="ClickHouse database name" />
          <EnvRow name="CLICKMEM_EMBEDDING_MODEL" def="Qwen/Qwen3-Embedding-0.6B" help="Override the embedding model" />
          <EnvRow name="CLICKMEM_CONFLICT_THRESHOLD" def="0.92" help="Cosine threshold for conflict surfacing" />
          <EnvRow name="CLICKMEM_SERVER_HOST" def="127.0.0.1" help="Bind address" />
          <EnvRow name="CLICKMEM_SERVER_PORT" def="9527" help="HTTP port (REST + MCP SSE + dashboard)" />
          <EnvRow name="CLICKMEM_API_KEY" def="—" help="Bearer token required on non-loopback binds" />
          <EnvRow name="CLICKMEM_REMOTE" def="—" help="Point CLI/MCP at a LAN server" />
          <EnvRow name="CLICKMEM_LOG_LEVEL" def="WARNING" help="DEBUG / INFO / WARNING / ERROR" />
        </dl>
      </Card>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-text-muted">{label}</dt>
      <dd className="mt-1 text-sm text-text-primary">{children}</dd>
    </div>
  );
}

function EnvRow({ name, def, help }: { name: string; def: string; help: string }) {
  return (
    <div className="rounded-lg border border-line bg-canvas-paper px-3 py-2.5">
      <div className="flex items-center justify-between gap-2 text-xs">
        <code className="font-mono text-text-primary">{name}</code>
        <span className="font-mono text-text-muted">default: {def}</span>
      </div>
      <p className="mt-1 text-xs text-text-secondary">{help}</p>
    </div>
  );
}
