"""Orchestrate per-adapter ``install_hooks(server_url)`` calls.

Used by ``clickmem hooks install [--agent NAME]``. When ``--agent`` is set
we drive that single adapter; otherwise we walk every discovered adapter and
call ``install_hooks`` on each one, capturing per-adapter results.

In addition to writing v1 hooks, the installer detects and (by default)
cleans up pre-v1 install residue surfaced by each adapter's
``detect_v0_residue`` hook — stale ``enabledPlugins.clickmem@local`` keys,
``UserPromptSubmit`` / ``PostToolUse`` curl hooks pointing at the legacy
``/hooks/claude-code`` endpoint, the v0 ``~/.clickmem/claude-plugin/`` tree,
and the ``~/.cursor/plugins/clickmem/`` legacy install path. The cleanup is
surgical (JSON edits + targeted ``rm``, never wholesale file replacement)
and every modified file is backed up to a sibling
``.bak.<UTC-YYYYMMDDHHMMSS>`` before being rewritten. The behaviour can be
disabled with ``clean_v0_residue=False`` — detection still runs and findings
are surfaced under ``v0_residue.detected`` for the dashboard / a follow-up
``--keep-v0`` CLI flag.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from clickmem.adapters import registry
from clickmem.config import get_config
from clickmem.local_or_remote import event_write
from clickmem.skill_install import install_clickmem_skill


_log = logging.getLogger(__name__)


def _server_url(override: str | None = None) -> str:
    if override:
        return override.rstrip("/")
    cfg = get_config(refresh=True)
    return (cfg.remote_url or cfg.server_url()).rstrip("/")


# TODO(post-merge): expose --keep-v0 in cli.hooks_install. The other worker
# is editing cli.py for the async-embedding refactor; once that lands, wire
# `typer.Option(False, "--keep-v0", ...)` into hooks_install() and forward
# it as `clean_v0_residue=not keep_v0`.
def install_hooks_for_all(
    agent: Optional[str] = None,
    server_url: str | None = None,
    *,
    clean_v0_residue: bool = True,
) -> dict[str, Any]:
    """Install v1 hooks for every detected adapter, cleaning v0 residue first.

    Returns a payload of the shape::

        {
            "ok": bool,
            "server_url": "...",
            "results": [<per-adapter install_hooks payload>],
            "v0_residue": {
                "detected": [<V0ResidueItem.to_dict()>, ...],
                "cleaned":  [<per-action log entry>, ...],
                "skipped_reason": "" | "user requested --keep-v0",
            },
        }

    The ``v0_residue`` block is always present (even when nothing was found
    or cleanup was skipped) so the dashboard's Imports / Agents page can
    render a stable schema.
    """
    url = _server_url(server_url)
    results: List[dict[str, Any]] = []
    target = registry if not agent else [h for h in registry if h.name == agent]
    if agent and not target:
        return {
            "ok": False,
            "error": f"unknown adapter: {agent}",
            "server_url": url,
            "v0_residue": {"detected": [], "cleaned": [], "skipped_reason": ""},
        }

    detected_all: list[dict[str, Any]] = []
    cleaned_all: list[dict[str, Any]] = []
    skipped_reason = "user requested --keep-v0" if not clean_v0_residue else ""

    for h in target:
        findings = h.detect_v0_residue()
        if findings:
            detected_all.extend(item.to_dict() for item in findings)
            if clean_v0_residue:
                cleaned_all.extend(h.clean_v0_residue(findings))
            for item in findings:
                _log.warning(
                    "v0 residue [%s] %s — %s (%s)",
                    h.name, item.path, item.issue, item.action,
                )

    for h in target:
        if not agent and not h.detect():
            results.append({"agent": h.name, "skipped": "not discovered"})
            continue
        result = h.install_hooks(url)
        skill_result = install_clickmem_skill(h.name)
        if skill_result.get("installed") or skill_result.get("skipped"):
            result["skill"] = skill_result
        result.setdefault("agent", h.name)
        results.append(result)
        event_write(
            "agent.install",
            agent=h.name,
            message=result.get("error") or result.get("message", "hooks installed"),
            payload={"ok": bool(result.get("ok"))},
        )

    if detected_all:
        event_write(
            "agent.v0_residue",
            agent="hooks_install",
            message=(
                f"detected {len(detected_all)} v0 install residue items; "
                + (f"cleaned {len(cleaned_all)}" if clean_v0_residue else "skipped (--keep-v0)")
            ),
            payload={
                "detected": detected_all,
                "cleaned": cleaned_all,
                "skipped_reason": skipped_reason,
            },
        )

    return {
        "ok": all(r.get("ok", False) or r.get("skipped") for r in results),
        "server_url": url,
        "results": results,
        "v0_residue": {
            "detected": detected_all,
            "cleaned": cleaned_all,
            "skipped_reason": skipped_reason,
        },
    }


def install(
    agent: Optional[str] = None,
    server_url: str | None = None,
    *,
    clean_v0_residue: bool = True,
) -> dict[str, Any]:
    """Backward-compatible wrapper for :func:`install_hooks_for_all`.

    The current ``cli.py`` (owned by another worker) calls this as
    ``install(agent=..., server_url=...)``; once the CLI is updated to
    surface ``--keep-v0``, callers should prefer ``install_hooks_for_all``
    directly. Both names route through the same implementation.
    """
    return install_hooks_for_all(
        agent=agent,
        server_url=server_url,
        clean_v0_residue=clean_v0_residue,
    )


def uninstall(agent: Optional[str] = None) -> dict[str, Any]:
    results: List[dict[str, Any]] = []
    target = registry if not agent else [h for h in registry if h.name == agent]
    if agent and not target:
        return {"ok": False, "error": f"unknown adapter: {agent}"}

    for h in target:
        result = h.uninstall_hooks()
        result.setdefault("agent", h.name)
        results.append(result)
        event_write(
            "agent.uninstall",
            agent=h.name,
            message=result.get("error") or "hooks removed",
            payload={"ok": bool(result.get("ok"))},
        )

    return {"ok": all(r.get("ok", False) for r in results), "results": results}


__all__ = ["install", "install_hooks_for_all", "uninstall"]
