"""Generic adapter — always-on placeholder for REST/MCP callers.

There is no hook to install, no doc tree to walk; the generic adapter exists
so the registry has a stable name to report for ``agent="generic"`` events
landed via ``POST /v1/raw`` or MCP calls from custom integrations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import RawSession

name = "generic"
label = "Generic (REST/MCP)"
experimental = False


def detect() -> bool:
    # Always available — anyone who can hit the API counts as "discovered".
    return True


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    if False:  # pragma: no cover
        yield None  # type: ignore[misc]
    return


def iter_doc_paths() -> List[Path]:
    return []


def install_hooks(server_url: str = "") -> dict[str, Any]:
    return {
        "ok": True,
        "installed": True,
        "agent": name,
        "message": "generic adapter requires no install; call /v1/raw or MCP tools directly",
        "server_url": server_url,
    }


def uninstall_hooks() -> dict[str, Any]:
    return {"ok": True, "installed": False, "agent": name, "message": "nothing to remove"}


def export_blob(dst_path: Path) -> dict[str, Any]:
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": []}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": 0, "path": str(dst_path)}
