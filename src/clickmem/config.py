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


@dataclass
class Config:
    server_host: str = field(default_factory=lambda: os.environ.get("CLICKMEM_SERVER_HOST", "127.0.0.1"))
    server_port: int = field(default_factory=lambda: _env_int("CLICKMEM_SERVER_PORT", 9527))
    remote_url: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_REMOTE") or None)
    api_key: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_API_KEY") or None)

    backend: str = field(default_factory=lambda: os.environ.get("CLICKMEM_BACKEND", "local"))
    db_path: Path = field(
        default_factory=lambda: _env_path("CLICKMEM_DB_PATH", Path.home() / ".clickmem" / "data")
    )

    ch_url: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_CH_URL") or None)
    ch_user: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_CH_USER") or None)
    ch_password: str | None = field(default_factory=lambda: os.environ.get("CLICKMEM_CH_PASSWORD") or None)
    ch_database: str = field(default_factory=lambda: os.environ.get("CLICKMEM_CH_DATABASE", "clickmem"))

    embedding_model: str = field(
        default_factory=lambda: os.environ.get("CLICKMEM_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    )
    embedding_dim: int = field(default_factory=lambda: _env_int("CLICKMEM_EMBEDDING_DIM", 256))

    conflict_threshold: float = field(
        default_factory=lambda: _env_float("CLICKMEM_CONFLICT_THRESHOLD", 0.92)
    )
    log_level: str = field(default_factory=lambda: os.environ.get("CLICKMEM_LOG_LEVEL", "WARNING"))

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
