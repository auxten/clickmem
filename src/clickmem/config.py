"""Centralised environment-variable resolution for ClickMem.

All config is env-driven with sensible defaults.  Nothing else in the codebase
should read os.environ directly for ClickMem-related keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    items = [item.strip() for item in raw.split(",")]
    return [item for item in items if item]


def _stored_active(backend_choice: str) -> str:
    """The store id last selected via `store use` / `store new` for this
    backend, from the per-user record (~/.clickmem/stores.json). Read inline
    (not via clickmem.stores) to keep config import-cycle free. Env always
    wins over the record."""
    try:
        import json

        raw = (Path.home() / ".clickmem" / "stores.json").read_text()
        value = json.loads(raw).get("active", {}).get(backend_choice, "")
        return value if isinstance(value, str) else ""
    except (OSError, ValueError):
        return ""


def _local_store_path() -> Path:
    env = os.environ.get("CLICKMEM_DB_PATH")
    if env:
        return Path(env).expanduser()
    active = _stored_active("local")
    if active and active != "default":
        return Path.home() / ".clickmem" / "stores" / active
    return Path.home() / ".clickmem" / "data"


@dataclass
class Config:
    server_host: str = field(default_factory=lambda: os.environ.get("CLICKMEM_SERVER_HOST", "127.0.0.1"))
    server_port: int = field(default_factory=lambda: _env_int("CLICKMEM_SERVER_PORT", 9527))
    remote_url: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_REMOTE") or None)
    api_key: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_API_KEY") or None)

    backend: str = field(default_factory=lambda: os.environ.get("CLICKMEM_BACKEND", "local"))
    db_path: Path = field(default_factory=_local_store_path)

    ch_url: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_CH_URL") or None)
    ch_user: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_CH_USER") or None)
    ch_password: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_CH_PASSWORD") or None)
    ch_database: str = field(
        default_factory=lambda: os.environ.get("CLICKMEM_CH_DATABASE")
        or _stored_active("clickhouse") or "clickmem"
    )
    # durable backend (chdb.durable): object store + this memory's store id
    durable_url: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_DURABLE_URL") or None)
    durable_id: str = field(
        default_factory=lambda: os.environ.get("CLICKMEM_DURABLE_ID")
        or _stored_active("durable") or "default"
    )

    # Optional shared project-memory store.  The personal backend above stays
    # open in the main ClickMem process; project operations run in a short-lived
    # helper process so chDB's process-local session does not contend with the
    # personal memory actor.  One project store can contain many project_ids.
    project_durable_url: str | None = field(
        default_factory=lambda: os.environ.get("CLICKMEM_PROJECT_DURABLE_URL") or None
    )
    project_durable_id: str = field(
        default_factory=lambda: os.environ.get("CLICKMEM_PROJECT_DURABLE_ID") or "team"
    )
    project_write_timeout_seconds: float = field(
        default_factory=lambda: _env_float("CLICKMEM_PROJECT_WRITE_TIMEOUT_SECONDS", 3.0)
    )

    # Daily Pickup runs outside the server process.  It reads local agent
    # transcripts, invokes the user's existing non-interactive agent CLI, then
    # submits only raw evidence + proposals to the personal ClickMem actor.
    pickup_agents: list[str] = field(
        default_factory=lambda: _env_list("CLICKMEM_PICKUP_AGENTS", ["codex", "claude_code"])
    )
    pickup_summarizer: str = field(
        default_factory=lambda: os.environ.get("CLICKMEM_PICKUP_SUMMARIZER", "auto")
    )
    pickup_command: str | None = field(
        default_factory=lambda: os.environ.get("CLICKMEM_PICKUP_COMMAND") or None
    )
    pickup_lookback_hours: int = field(
        default_factory=lambda: _env_int("CLICKMEM_PICKUP_LOOKBACK_HOURS", 26)
    )
    pickup_max_session_chars: int = field(
        default_factory=lambda: _env_int("CLICKMEM_PICKUP_MAX_SESSION_CHARS", 60_000)
    )
    pickup_hour: int = field(default_factory=lambda: _env_int("CLICKMEM_PICKUP_HOUR", 18))
    pickup_minute: int = field(default_factory=lambda: _env_int("CLICKMEM_PICKUP_MINUTE", 0))
    pickup_notify: bool = field(
        default_factory=lambda: _env_bool("CLICKMEM_PICKUP_NOTIFY", True)
    )
    pickup_spool_dir: Path = field(
        default_factory=lambda: _env_path(
            "CLICKMEM_PICKUP_SPOOL_DIR", Path.home() / ".clickmem" / "pickup" / "spool"
        )
    )

    embedding_model: str = field(
        default_factory=lambda: os.environ.get("CLICKMEM_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    )
    embedding_dim: int = field(default_factory=lambda: _env_int("CLICKMEM_EMBEDDING_DIM", 256))

    conflict_threshold: float = field(
        default_factory=lambda: _env_float("CLICKMEM_CONFLICT_THRESHOLD", 0.92)
    )
    log_level: str = field(default_factory=lambda: os.environ.get("CLICKMEM_LOG_LEVEL", "WARNING"))

    # MCP SSE transport security (DNS-rebinding protection in mcp.server.fastmcp).
    # Default to "*" so LAN/Tailscale hosts work out of the box; tighten via env
    # in hardened deployments.
    mcp_allowed_hosts: list[str] = field(
        default_factory=lambda: _env_list("CLICKMEM_MCP_ALLOWED_HOSTS", ["*"])
    )
    mcp_allowed_origins: list[str] = field(
        default_factory=lambda: _env_list("CLICKMEM_MCP_ALLOWED_ORIGINS", ["*"])
    )

    def server_url(self) -> str:
        return f"http://{self.server_host}:{self.server_port}"


_cached: Config | None = None


def get_config(refresh: bool = False) -> Config:
    """Return the process-wide config singleton.

    Call with refresh=True in tests where env vars change mid-process.
    """
    global _cached
    if refresh or _cached is None:
        _cached = Config()
    return _cached
