"""Install the bundled ClickMem startup skill for agents that support skills."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from clickmem.adapters import claude_code, codex, cursor


_SUPPORTED = {"cursor", "claude_code", "codex"}


def _repo_skill_path() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "skills" / "clickmem" / "SKILL.md"
        if candidate.is_file():
            return candidate
    return None


def _copy_bundled_skill(target: Path) -> Path:
    repo_path = _repo_skill_path()
    if repo_path is not None:
        if repo_path.resolve() != target.resolve():
            shutil.copyfile(repo_path, target)
        return repo_path
    candidate = resources.files("clickmem").joinpath("skills", "clickmem", "SKILL.md")
    with resources.as_file(candidate) as path:
        if path.resolve() != target.resolve():
            shutil.copyfile(path, target)
        return path


def _target_for(agent: str) -> Path | None:
    if agent == "cursor":
        return cursor._HOOK_DST.parents[1] / "skills" / "clickmem" / "SKILL.md"
    if agent == "claude_code":
        return claude_code._SETTINGS.parent / "skills" / "clickmem" / "SKILL.md"
    if agent == "codex":
        return codex._BASE / "skills" / "clickmem" / "SKILL.md"
    return None


def install_clickmem_skill(agent: str) -> dict[str, Any]:
    """Copy the startup skill for a supported agent.

    The operation is idempotent and intentionally separate from doc import:
    installing a skill changes agent behavior, but it does not promote any
    existing user documents into memories.
    """
    if agent not in _SUPPORTED:
        return {"installed": False, "skipped": "agent does not support skill install"}
    target = _target_for(agent)
    if target is None:
        return {"installed": False, "skipped": "no skill target for agent"}
    target.parent.mkdir(parents=True, exist_ok=True)
    source = _copy_bundled_skill(target)
    return {"installed": True, "path": str(target), "source": str(source)}


__all__ = ["install_clickmem_skill"]
