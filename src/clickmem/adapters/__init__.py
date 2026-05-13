"""Per-agent adapter registry.

Each module under :mod:`clickmem.adapters` exposes a flat module-level
interface (``name``, ``label``, ``experimental``, ``detect``,
``iter_raw_sessions``, ``iter_doc_paths``, ``install_hooks``,
``uninstall_hooks``, ``export_blob``). This wrapper turns those modules into
:class:`clickmem.adapters.base.AgentAdapter`-compatible handles and exposes a
``registry`` list + ``get_registry()`` / ``get(name)`` accessors.

The list order matches the README's adapter table. ``agents.py`` and the
``hooks_install`` command iterate over ``registry`` directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import ModuleType
from typing import List, Optional

from clickmem.adapters import (
    aider,
    base,
    claude_code,
    cline,
    codex,
    continue_dev,
    cursor,
    generic,
    jetbrains,
    windsurf,
    zed,
)

_log = logging.getLogger(__name__)


@dataclass
class AdapterHandle:
    """Module-backed :class:`AgentAdapter`.

    A thin shim around the underlying adapter module so the registry can
    expose a uniform attribute surface without forcing each module to
    implement a class.
    """

    module: ModuleType

    @property
    def name(self) -> str:
        return getattr(self.module, "name", "")

    @property
    def label(self) -> str:
        return getattr(self.module, "label", self.name)

    @property
    def experimental(self) -> bool:
        return bool(getattr(self.module, "experimental", False))

    def detect(self) -> bool:
        try:
            return bool(self.module.detect())
        except Exception as e:  # noqa: BLE001
            _log.debug("adapter.detect failed for %s: %s", self.name, e)
            return False

    def iter_raw_sessions(self, since=None):
        try:
            yield from self.module.iter_raw_sessions(since=since)
        except Exception as e:  # noqa: BLE001
            _log.warning("adapter.iter_raw_sessions failed for %s: %s", self.name, e)
            return

    def iter_doc_paths(self):
        try:
            return list(self.module.iter_doc_paths())
        except Exception as e:  # noqa: BLE001
            _log.warning("adapter.iter_doc_paths failed for %s: %s", self.name, e)
            return []

    def install_hooks(self, server_url: str = "") -> dict:
        try:
            return self.module.install_hooks(server_url)
        except NotImplementedError as e:
            return {
                "ok": False,
                "installed": False,
                "agent": self.name,
                "experimental": self.experimental,
                "error": "doc-only adapter",
                "detail": str(e),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "installed": False, "agent": self.name, "error": str(e)}

    def uninstall_hooks(self) -> dict:
        try:
            return self.module.uninstall_hooks()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "agent": self.name, "error": str(e)}

    def export_blob(self, dst_path) -> dict:
        try:
            return self.module.export_blob(dst_path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "agent": self.name, "error": str(e)}


_modules: List[ModuleType] = [
    claude_code,
    cursor,
    codex,
    aider,
    continue_dev,
    cline,
    windsurf,
    zed,
    jetbrains,
    generic,
]

registry: List[AdapterHandle] = [AdapterHandle(m) for m in _modules]


def get_registry() -> dict[str, AdapterHandle]:
    """Return adapters keyed by name (used by ``clickmem.agents``)."""
    return {h.name: h for h in registry}


def get(name: str) -> Optional[AdapterHandle]:
    for h in registry:
        if h.name == name:
            return h
    return None


__all__ = [
    "AdapterHandle",
    "base",
    "get",
    "get_registry",
    "registry",
]
