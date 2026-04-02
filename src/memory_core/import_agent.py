"""Agent history import — read transcripts and knowledge docs from Claude Code, Cursor, OpenClaw.

Discovers installed agents, reads their conversation history (JSONL) and curated knowledge
files (CLAUDE.md, AGENTS.md, memory/*.md), and ingests them into ClickMem via the transport
layer (local or remote).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

logger = logging.getLogger("clickmem.import")

_CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
_CURSOR_PROJECTS_DIR = os.path.expanduser("~/.cursor/projects")
_CODEX_DIR = os.path.expanduser("~/.codex")
_OPENCLAW_DIR = os.path.expanduser("~/.openclaw")

_HOSTNAME = socket.gethostname()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    session_id: str
    text: str
    source: str  # "claude_code" | "cursor" | "openclaw"
    cwd: str
    timestamp: str
    project_name: str = ""
    git_remote: str = ""
    github_url: str = ""
    git_branch: str = ""
    hostname: str = ""
    agent_version: str = ""
    slug: str = ""


@dataclass
class DocInfo:
    path: str
    content: str
    doc_type: str  # "CLAUDE.md" | "AGENTS.md" | "claude_memory" | "cursor_rule"
    project_name: str = ""
    cwd: str = ""
    git_remote: str = ""
    github_url: str = ""


@dataclass
class AgentInfo:
    name: str
    history_dir: str
    session_count: int = 0
    doc_count: int = 0
    hook_installed: bool = False


# ---------------------------------------------------------------------------
# Git info extraction (cached per cwd)
# ---------------------------------------------------------------------------

_git_cache: dict[str, tuple[str, str]] = {}


def _extract_git_info(cwd: str) -> tuple[str, str]:
    """Return (git_remote, github_url) for a project path. Cached."""
    if cwd in _git_cache:
        return _git_cache[cwd]

    git_remote = ""
    github_url = ""

    if cwd and os.path.isdir(cwd):
        try:
            r = subprocess.run(
                ["git", "-C", cwd, "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                git_remote = r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if git_remote:
        github_url = _normalize_github_url(git_remote)
    elif cwd:
        m = re.search(r"github\.com/([^/]+/[^/]+)", cwd.replace(os.sep, "/"))
        if m:
            github_url = f"https://github.com/{m.group(1)}"

    _git_cache[cwd] = (git_remote, github_url)
    return git_remote, github_url


def _normalize_github_url(remote: str) -> str:
    """Normalize git@github.com:a/b.git -> https://github.com/a/b"""
    remote = remote.strip()
    m = re.match(r"git@github\.com:(.+?)(?:\.git)?$", remote)
    if m:
        return f"https://github.com/{m.group(1)}"
    m = re.match(r"https?://github\.com/(.+?)(?:\.git)?$", remote)
    if m:
        return f"https://github.com/{m.group(1)}"
    return ""


# ---------------------------------------------------------------------------
# Agent discovery
# ---------------------------------------------------------------------------

def discover_agents() -> list[AgentInfo]:
    """Scan standard paths and return info about installed agents."""
    agents = []

    # Claude Code
    cc_dir = _CLAUDE_PROJECTS_DIR
    cc_sessions = 0
    cc_docs = 0
    if os.path.isdir(cc_dir):
        for project_dir in glob.glob(os.path.join(cc_dir, "*")):
            if not os.path.isdir(project_dir):
                continue
            for f in glob.glob(os.path.join(project_dir, "*.jsonl")):
                cc_sessions += 1
            for f in glob.glob(os.path.join(project_dir, "memory", "*.md")):
                cc_docs += 1
    cc_hook = _check_claude_hooks()
    agents.append(AgentInfo("claude-code", cc_dir, cc_sessions, cc_docs, cc_hook))

    # Cursor
    cr_dir = _CURSOR_PROJECTS_DIR
    cr_sessions = 0
    if os.path.isdir(cr_dir):
        for f in glob.glob(os.path.join(cr_dir, "*/agent-transcripts/*/*.jsonl")):
            if "/subagents/" not in f:
                cr_sessions += 1
    cr_hook = _check_cursor_hooks()
    agents.append(AgentInfo("cursor", cr_dir, cr_sessions, 0, cr_hook))

    # Codex
    cx_dir = _CODEX_DIR
    cx_sessions = 0
    cx_docs = 0
    if os.path.isdir(cx_dir):
        sessions_dir = os.path.join(cx_dir, "sessions")
        if os.path.isdir(sessions_dir):
            for root, _dirs, files in os.walk(sessions_dir):
                cx_sessions += sum(1 for f in files if f.startswith("rollout-") and f.endswith(".jsonl"))
        agents_md = os.path.join(cx_dir, "AGENTS.md")
        if os.path.isfile(agents_md):
            cx_docs += 1
        mem_dir = os.path.join(cx_dir, "memories")
        if os.path.isdir(mem_dir):
            cx_docs += len(glob.glob(os.path.join(mem_dir, "*.md")))
    cx_hook = _check_codex_hooks()
    agents.append(AgentInfo("codex", cx_dir, cx_sessions, cx_docs, cx_hook))

    # OpenClaw
    oc_dir = _OPENCLAW_DIR
    oc_count = 0
    if os.path.isdir(oc_dir):
        oc_count = len(glob.glob(os.path.join(oc_dir, "workspace-*", "memory", "*.md")))
        oc_count += len(glob.glob(os.path.join(oc_dir, "memory", "*.sqlite")))
    oc_hook = os.path.exists(os.path.join(oc_dir, "openclaw.json"))
    agents.append(AgentInfo("openclaw", oc_dir, oc_count, 0, oc_hook))

    return agents


def _check_claude_hooks() -> bool:
    settings = os.path.expanduser("~/.claude/settings.json")
    if not os.path.exists(settings):
        return False
    try:
        with open(settings) as f:
            data = json.load(f)
        hooks = data.get("hooks", {})
        for event_hooks in hooks.values():
            if isinstance(event_hooks, list):
                for h in event_hooks:
                    hooks_list = h.get("hooks", []) if isinstance(h, dict) else []
                    for hook in hooks_list:
                        url = hook.get("url", "") if isinstance(hook, dict) else ""
                        if "clickmem" in url or "9527" in url:
                            return True
    except (json.JSONDecodeError, OSError):
        pass
    return False


def _check_cursor_hooks() -> bool:
    plugin_dir = os.path.expanduser("~/.cursor/hooks/clickmem")
    return os.path.exists(plugin_dir)


def _check_codex_hooks() -> bool:
    """Check if Codex has clickmem hooks configured."""
    # Check global ~/.codex/hooks.json
    hooks_path = os.path.join(_CODEX_DIR, "hooks.json")
    if os.path.isfile(hooks_path):
        try:
            with open(hooks_path) as f:
                data = json.load(f)
            raw = json.dumps(data)
            if "clickmem" in raw or "9527" in raw:
                return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


# ---------------------------------------------------------------------------
# Claude Code reader
# ---------------------------------------------------------------------------

_CC_SKIP_TYPES = frozenset({
    "queue-operation", "file-history-snapshot", "progress",
    "last-prompt", "summary",
})


class ClaudeCodeReader:
    """Read conversation sessions from Claude Code history."""

    def __init__(self, base_dir: str | None = None):
        self._base = base_dir or _CLAUDE_PROJECTS_DIR

    def iter_sessions(self, since: float | None = None) -> Iterator[SessionInfo]:
        """Yield one SessionInfo per JSONL session file, newest first."""
        if not os.path.isdir(self._base):
            return

        all_jsonl: list[str] = []
        for project_dir in glob.glob(os.path.join(self._base, "*")):
            if not os.path.isdir(project_dir):
                continue
            all_jsonl.extend(glob.glob(os.path.join(project_dir, "*.jsonl")))

        all_jsonl.sort(key=lambda f: os.path.getmtime(f), reverse=True)

        for jsonl_path in all_jsonl:
            if since and os.path.getmtime(jsonl_path) < since:
                continue
            info = self._parse_session(jsonl_path)
            if info and len(info.text) >= 50:
                yield info

    def _parse_session(self, path: str) -> SessionInfo | None:
        session_id = Path(path).stem
        messages: list[str] = []
        cwd = ""
        git_branch = ""
        slug = ""
        timestamp = ""
        version = ""

        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    obj_type = obj.get("type", "")
                    if obj_type in _CC_SKIP_TYPES:
                        continue

                    if not cwd:
                        cwd = obj.get("cwd", "")
                    if not git_branch:
                        git_branch = obj.get("gitBranch", "")
                    if not slug:
                        slug = obj.get("slug", "")
                    if not version:
                        version = obj.get("version", "")
                    if not timestamp:
                        timestamp = obj.get("timestamp", "")

                    msg = obj.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    role = msg.get("role", "")
                    content = msg.get("content", "")

                    text = self._extract_text(content)
                    if not text:
                        continue

                    if role == "user":
                        messages.append(f"user: {text}")
                    elif role == "assistant":
                        messages.append(f"assistant: {text}")

        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None

        if not messages:
            return None

        combined = "\n".join(messages)
        project_name = os.path.basename(cwd) if cwd else ""
        git_remote, github_url = _extract_git_info(cwd) if cwd else ("", "")

        return SessionInfo(
            session_id=session_id,
            text=combined,
            source="claude_code",
            cwd=cwd,
            timestamp=timestamp,
            project_name=project_name,
            git_remote=git_remote,
            github_url=github_url,
            git_branch=git_branch,
            hostname=_HOSTNAME,
            agent_version=version,
            slug=slug,
        )

    @staticmethod
    def _extract_text(content) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        inner = block.get("content", "")
                        if isinstance(inner, str):
                            parts.append(inner)
            return "\n".join(p for p in parts if p).strip()
        return ""


# ---------------------------------------------------------------------------
# Cursor reader
# ---------------------------------------------------------------------------

class CursorReader:
    """Read conversation sessions from Cursor agent-transcripts."""

    def __init__(self, base_dir: str | None = None):
        self._base = base_dir or _CURSOR_PROJECTS_DIR

    def iter_sessions(self, since: float | None = None) -> Iterator[SessionInfo]:
        """Yield one SessionInfo per Cursor transcript, newest first."""
        if not os.path.isdir(self._base):
            return

        pattern = os.path.join(self._base, "*/agent-transcripts/*/*.jsonl")
        all_jsonl = [f for f in glob.glob(pattern) if "/subagents/" not in f]
        all_jsonl.sort(key=lambda f: os.path.getmtime(f), reverse=True)

        for jsonl_path in all_jsonl:
            if since and os.path.getmtime(jsonl_path) < since:
                continue
            info = self._parse_session(jsonl_path)
            if info and len(info.text) >= 50:
                yield info

    def _parse_session(self, path: str) -> SessionInfo | None:
        session_id = Path(path).stem
        messages: list[str] = []
        mtime = os.path.getmtime(path)
        timestamp = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

        # Derive project cwd from directory structure
        # e.g., .cursor/projects/Users-tong-Projects-ainote-AiNote/agent-transcripts/...
        parts = path.split(os.sep)
        project_slug = ""
        for i, p in enumerate(parts):
            if p == "agent-transcripts" and i > 0:
                project_slug = parts[i - 1]
                break
        cwd = self._decode_project_path(project_slug)

        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    role = obj.get("role", "")
                    msg = obj.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    content = msg.get("content", "")
                    text = self._extract_text(content)
                    if not text:
                        continue

                    if role == "user":
                        messages.append(f"user: {text}")
                    elif role == "assistant":
                        messages.append(f"assistant: {text}")

        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None

        if not messages:
            return None

        combined = "\n".join(messages)
        project_name = os.path.basename(cwd) if cwd else ""
        git_remote, github_url = _extract_git_info(cwd) if cwd else ("", "")

        return SessionInfo(
            session_id=session_id,
            text=combined,
            source="cursor",
            cwd=cwd,
            timestamp=timestamp,
            project_name=project_name,
            git_remote=git_remote,
            github_url=github_url,
            hostname=_HOSTNAME,
        )

    @staticmethod
    def _decode_project_path(slug: str) -> str:
        """Best-effort decode Cursor's dash-encoded project path.

        E.g., 'Users-tong-Projects-ainote' -> '/Users/tong/Projects/ainote'
        The encoding is ambiguous (dashes in dir names also become dashes),
        but we try to reconstruct a valid path.
        """
        if not slug:
            return ""
        candidate = "/" + slug.replace("-", "/")
        # Try progressively merging segments to find a path that exists
        if os.path.isdir(candidate):
            return candidate
        # Common pattern: Users/{user}/{rest}
        parts = slug.split("-")
        if len(parts) >= 2 and parts[0] == "Users":
            user_path = f"/Users/{parts[1]}"
            rest = parts[2:]
            # Greedily try to find the longest existing prefix
            path = user_path
            for p in rest:
                trial_dash = path + "-" + p
                trial_slash = path + "/" + p
                if os.path.isdir(trial_slash):
                    path = trial_slash
                elif os.path.isdir(trial_dash):
                    path = trial_dash
                else:
                    path = trial_slash  # default to slash
            return path
        return candidate

    @staticmethod
    def _extract_text(content) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(p for p in parts if p).strip()
        return ""


# ---------------------------------------------------------------------------
# Codex reader
# ---------------------------------------------------------------------------

class CodexReader:
    """Read conversation sessions from OpenAI Codex CLI rollout files.

    Codex stores sessions as JSONL in ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``.
    Each line has ``{timestamp, type, payload}``.  We extract user and assistant
    messages from ``response_item`` entries with ``payload.type == "message"``.
    """

    def __init__(self, base_dir: str | None = None):
        self._base = base_dir or _CODEX_DIR

    def iter_sessions(self, since: float | None = None) -> Iterator[SessionInfo]:
        sessions_dir = os.path.join(self._base, "sessions")
        if not os.path.isdir(sessions_dir):
            return

        all_jsonl: list[str] = []
        for root, _dirs, files in os.walk(sessions_dir):
            for f in files:
                if f.endswith(".jsonl") and f.startswith("rollout-"):
                    all_jsonl.append(os.path.join(root, f))

        all_jsonl.sort(key=lambda f: os.path.getmtime(f), reverse=True)

        for jsonl_path in all_jsonl:
            if since and os.path.getmtime(jsonl_path) < since:
                continue
            info = self._parse_session(jsonl_path)
            if info and len(info.text) >= 50:
                yield info

    def _parse_session(self, path: str) -> SessionInfo | None:
        session_id = Path(path).stem  # rollout-<ts>-<uuid>
        messages: list[str] = []
        cwd = ""
        git_remote = ""
        git_branch = ""
        timestamp = ""
        version = ""
        model = ""

        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    rec_type = obj.get("type", "")
                    payload = obj.get("payload", {})
                    if not isinstance(payload, dict):
                        continue

                    # Extract metadata from session_meta
                    if rec_type == "session_meta":
                        if not cwd:
                            cwd = payload.get("cwd", "")
                        if not timestamp:
                            timestamp = payload.get("timestamp", "") or obj.get("timestamp", "")
                        if not version:
                            version = payload.get("cli_version", "")
                        git_info = payload.get("git", {})
                        if isinstance(git_info, dict):
                            if not git_remote:
                                git_remote = git_info.get("repository_url", "")
                            if not git_branch:
                                git_branch = git_info.get("branch", "")
                        continue

                    # Extract cwd/model from turn_context
                    if rec_type == "turn_context":
                        if not cwd:
                            cwd = payload.get("cwd", "")
                        if not model:
                            model = payload.get("model", "")
                        continue

                    # Extract user/assistant messages from response_item
                    if rec_type == "response_item" and payload.get("type") == "message":
                        role = payload.get("role", "")
                        if role not in ("user", "assistant"):
                            continue  # skip developer/system messages
                        content = payload.get("content", [])
                        text = self._extract_text(content)
                        if text:
                            messages.append(f"{role}: {text}")

                    # Also capture user_message from event_msg
                    if rec_type == "event_msg" and payload.get("type") == "user_message":
                        user_msg = payload.get("message", "")
                        if user_msg and len(user_msg) > 5:
                            messages.append(f"user: {user_msg}")

        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Failed to read Codex session %s: %s", path, e)
            return None

        if not messages:
            return None

        combined = "\n".join(messages)
        project_name = os.path.basename(cwd) if cwd else ""
        github_url = _normalize_github_url(git_remote) if git_remote else ""
        if not github_url and cwd:
            _, github_url = _extract_git_info(cwd)

        return SessionInfo(
            session_id=session_id,
            text=combined,
            source="codex",
            cwd=cwd,
            timestamp=timestamp,
            project_name=project_name,
            git_remote=git_remote,
            github_url=github_url,
            git_branch=git_branch,
            hostname=_HOSTNAME,
            agent_version=version,
        )

    @staticmethod
    def _extract_text(content) -> str:
        """Extract text from Codex content array: [{type: "input_text"|"output_text", text: "..."}]."""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        # Skip developer permission/context blocks
                        if text.startswith("<permissions ") or text.startswith("<app-context>"):
                            continue
                        if text.startswith("<collaboration_mode>") or text.startswith("<skills_instructions>"):
                            continue
                        if text.startswith("<environment_context>"):
                            continue
                        parts.append(text)
            return "\n".join(p for p in parts if p).strip()
        return ""


# ---------------------------------------------------------------------------
# Knowledge doc discovery
# ---------------------------------------------------------------------------

def discover_knowledge_docs(cwd_set: set[str]) -> list[DocInfo]:
    """Find CLAUDE.md, AGENTS.md, memory/*.md across known project paths."""
    docs: list[DocInfo] = []
    seen_paths: set[str] = set()

    # 1. Claude Code memory MDs
    if os.path.isdir(_CLAUDE_PROJECTS_DIR):
        for md_path in glob.glob(os.path.join(_CLAUDE_PROJECTS_DIR, "*/memory/*.md")):
            if md_path in seen_paths:
                continue
            seen_paths.add(md_path)
            # Derive cwd from parent project dir
            project_dir = os.path.dirname(os.path.dirname(md_path))
            project_slug = os.path.basename(project_dir)
            # Try to find matching cwd
            matched_cwd = ""
            for c in cwd_set:
                encoded = c.replace("/", "-").lstrip("-")
                if project_slug == encoded or project_slug.endswith(encoded):
                    matched_cwd = c
                    break

            content = _read_file(md_path)
            if content:
                project_name = os.path.basename(matched_cwd) if matched_cwd else project_slug
                git_remote, github_url = _extract_git_info(matched_cwd) if matched_cwd else ("", "")
                docs.append(DocInfo(
                    path=md_path, content=content, doc_type="claude_memory",
                    project_name=project_name, cwd=matched_cwd,
                    git_remote=git_remote, github_url=github_url,
                ))

    # 2. CLAUDE.md and AGENTS.md in project directories
    for cwd in cwd_set:
        if not os.path.isdir(cwd):
            continue
        git_remote, github_url = _extract_git_info(cwd)
        project_name = os.path.basename(cwd)

        for doc_name in ("CLAUDE.md", "AGENTS.md"):
            doc_path = os.path.join(cwd, doc_name)
            if doc_path in seen_paths:
                continue
            if os.path.isfile(doc_path):
                seen_paths.add(doc_path)
                content = _read_file(doc_path)
                if content:
                    docs.append(DocInfo(
                        path=doc_path, content=content, doc_type=doc_name,
                        project_name=project_name, cwd=cwd,
                        git_remote=git_remote, github_url=github_url,
                    ))

        # Cursor rules inside project (.md and .mdc)
        cursor_rules_dir = os.path.join(cwd, ".cursor", "rules")
        for rule_path in (glob.glob(os.path.join(cursor_rules_dir, "*.md"))
                          + glob.glob(os.path.join(cursor_rules_dir, "*.mdc"))):
            if rule_path in seen_paths:
                continue
            seen_paths.add(rule_path)
            content = _read_file(rule_path)
            if content:
                docs.append(DocInfo(
                    path=rule_path, content=content, doc_type="cursor_rule",
                    project_name=project_name, cwd=cwd,
                    git_remote=git_remote, github_url=github_url,
                ))

    # 3. Global Cursor rules (~/.cursor/rules/)
    _global_cursor_rules = os.path.expanduser("~/.cursor/rules")
    if os.path.isdir(_global_cursor_rules):
        for rule_path in (glob.glob(os.path.join(_global_cursor_rules, "*.md"))
                          + glob.glob(os.path.join(_global_cursor_rules, "*.mdc"))):
            if rule_path in seen_paths:
                continue
            seen_paths.add(rule_path)
            content = _read_file(rule_path)
            if content:
                docs.append(DocInfo(
                    path=rule_path, content=content, doc_type="cursor_rule",
                    project_name="global", cwd="",
                ))

    # 4. Codex AGENTS.md (global and per-project)
    codex_global_agents = os.path.join(_CODEX_DIR, "AGENTS.md")
    if os.path.isfile(codex_global_agents) and codex_global_agents not in seen_paths:
        content = _read_file(codex_global_agents)
        if content:
            seen_paths.add(codex_global_agents)
            docs.append(DocInfo(
                path=codex_global_agents, content=content, doc_type="AGENTS.md",
                project_name="global", cwd="",
            ))

    # Per-project .codex/AGENTS.md
    for cwd in cwd_set:
        codex_proj_agents = os.path.join(cwd, ".codex", "AGENTS.md")
        if os.path.isfile(codex_proj_agents) and codex_proj_agents not in seen_paths:
            content = _read_file(codex_proj_agents)
            if content:
                seen_paths.add(codex_proj_agents)
                git_remote, github_url = _extract_git_info(cwd)
                docs.append(DocInfo(
                    path=codex_proj_agents, content=content, doc_type="AGENTS.md",
                    project_name=os.path.basename(cwd), cwd=cwd,
                    git_remote=git_remote, github_url=github_url,
                ))

    # 5. Codex memories (~/.codex/memories/)
    codex_memories = os.path.join(_CODEX_DIR, "memories")
    if os.path.isdir(codex_memories):
        for md_path in glob.glob(os.path.join(codex_memories, "*.md")):
            if md_path in seen_paths:
                continue
            seen_paths.add(md_path)
            content = _read_file(md_path)
            if content:
                docs.append(DocInfo(
                    path=md_path, content=content, doc_type="claude_memory",
                    project_name="global", cwd="",
                ))

    return docs


def scan_path(path: str) -> list[DocInfo]:
    """Scan a user-specified directory for knowledge docs."""
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        logger.warning("scan_path: %s is not a directory", path)
        return []

    docs: list[DocInfo] = []
    project_name = os.path.basename(path)
    git_remote, github_url = _extract_git_info(path)

    for doc_name in ("CLAUDE.md", "AGENTS.md"):
        doc_path = os.path.join(path, doc_name)
        if os.path.isfile(doc_path):
            content = _read_file(doc_path)
            if content:
                docs.append(DocInfo(
                    path=doc_path, content=content, doc_type=doc_name,
                    project_name=project_name, cwd=path,
                    git_remote=git_remote, github_url=github_url,
                ))

    cursor_rules = os.path.join(path, ".cursor", "rules")
    if os.path.isdir(cursor_rules):
        for rule_path in (glob.glob(os.path.join(cursor_rules, "*.md"))
                          + glob.glob(os.path.join(cursor_rules, "*.mdc"))):
            content = _read_file(rule_path)
            if content:
                docs.append(DocInfo(
                    path=rule_path, content=content, doc_type="cursor_rule",
                    project_name=project_name, cwd=path,
                    git_remote=git_remote, github_url=github_url,
                ))

    return docs


def _infer_domain(text: str) -> str:
    """Infer a CEO-brain domain from free-text content."""
    lower = text.lower()
    if any(w in lower for w in ("deploy", "ci", "release", "pip", "git tag", "service", "launchd")):
        return "ops"
    if any(w in lower for w in ("architect", "interface", "api", "asyncio", "chdb", "hook", "schema", "engine")):
        return "tech"
    if any(w in lower for w in ("product", "user-focused", "doc")):
        return "product"
    if any(w in lower for w in ("test", "mock", "pytest", "fixture")):
        return "ops"
    return "management"


def parse_agents_md(content: str) -> list[dict]:
    """Parse AGENTS.md bullet points into structured principle dicts.

    Section-aware: "Learned User Preferences" → scope="global",
    "Learned Workspace Facts" → scope="project".

    Returns list of {"content": str, "domain": str, "scope": str}.
    """
    results = []
    current_section = ""

    for line in content.split("\n"):
        line = line.rstrip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            continue
        if line.startswith("**") and line.endswith("**"):
            continue
        if line.startswith("- ") and len(line) > 25:
            bullet = line[2:].strip()

            # Determine scope from section
            section_lower = current_section.lower()
            if "preference" in section_lower:
                scope = "global"
            elif "fact" in section_lower:
                scope = "project"
            else:
                scope = "global"

            results.append({"content": bullet, "domain": _infer_domain(bullet), "scope": scope})

    return results


def parse_claude_memory_file(content: str) -> dict | None:
    """Parse a Claude Code auto-memory file with YAML frontmatter.

    Expected format::

        ---
        name: ...
        description: ...
        type: feedback | user | project | reference
        ---
        Body text ...

    Returns dict with keys ``name``, ``description``, ``type``, ``body``,
    or *None* if the file lacks a ``type`` field.
    """
    content = content.strip()
    if not content.startswith("---"):
        return None

    # Split on second '---'
    rest = content[3:]
    idx = rest.find("\n---")
    if idx < 0:
        return None

    frontmatter_text = rest[:idx]
    body = rest[idx + 4:].strip()  # skip "\n---"

    # Parse simple key: value lines from frontmatter
    meta: dict[str, str] = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    mem_type = meta.get("type", "")
    if not mem_type:
        return None

    return {
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "type": mem_type,
        "body": body,
    }


def ingest_agents_md(
    ceo_db,
    emb,
    doc: DocInfo,
    project_id: str = "",
) -> dict:
    """Directly parse AGENTS.md bullets into principles without LLM extraction.

    Scope-aware: "Learned User Preferences" bullets are stored as global (project_id=""),
    "Learned Workspace Facts" bullets are stored with the project_id.

    Returns {"imported": N, "skipped": N}.
    """
    from memory_core.models import Principle
    from memory_core.ceo_dedup import dedup_principle

    bullets = parse_agents_md(doc.content)
    imported = 0
    skipped = 0

    for item in bullets:
        pid = "" if item["scope"] == "global" else project_id
        p = Principle(
            project_id=pid,
            content=item["content"],
            domain=item["domain"],
            confidence=1.0,
            evidence_count=1,
        )
        if emb:
            p.embedding = emb.encode_document(item["content"])

        result = dedup_principle(ceo_db, emb, p)
        if result.action in ("NOOP", "CONFLICT"):
            skipped += 1
            continue

        ceo_db.insert_principle(p)
        imported += 1

    return {"imported": imported, "skipped": skipped}


def ingest_claude_memory(
    ceo_db,
    emb,
    doc: DocInfo,
    project_id: str = "",
) -> dict:
    """Directly parse a Claude Code auto-memory file into CEO entities.

    Type mapping:
      feedback, user → Principle  (user is global, feedback is project-scoped)
      project, reference → Fact   (category=reference)

    Returns {"imported": N, "skipped": N, "entity_type": str}.
    """
    from memory_core.ceo_dedup import dedup_fact, dedup_principle
    from memory_core.models import Fact, Principle

    # Skip MEMORY.md index file
    if os.path.basename(doc.path) == "MEMORY.md":
        return {"imported": 0, "skipped": 1, "entity_type": "index"}

    parsed = parse_claude_memory_file(doc.content)
    if parsed is None:
        return {"imported": 0, "skipped": 1, "entity_type": "unknown"}

    mem_type = parsed["type"]
    # Use body as content; fall back to description if body is empty
    content = parsed["body"] or parsed.get("description", "")
    if not content or len(content) < 10:
        return {"imported": 0, "skipped": 1, "entity_type": mem_type}

    # Prepend name for richer context
    name = parsed.get("name", "")
    if name:
        content = f"{name}: {content}"

    domain = _infer_domain(content)
    embedding = emb.encode_document(content) if emb else None

    if mem_type in ("feedback", "user"):
        # user → global principle; feedback → project-scoped
        pid = "" if mem_type == "user" else project_id
        p = Principle(
            project_id=pid,
            content=content,
            domain=domain,
            confidence=1.0,
            evidence_count=1,
            embedding=embedding,
        )
        result = dedup_principle(ceo_db, emb, p)
        if result.action in ("NOOP", "CONFLICT"):
            return {"imported": 0, "skipped": 1, "entity_type": "principle"}
        ceo_db.insert_principle(p)
        return {"imported": 1, "skipped": 0, "entity_type": "principle"}

    elif mem_type in ("project", "reference"):
        f = Fact(
            project_id=project_id,
            content=content,
            category="reference",
            domain=domain,
            embedding=embedding,
        )
        result = dedup_fact(ceo_db, emb, f)
        if result.action == "NOOP":
            return {"imported": 0, "skipped": 1, "entity_type": "fact"}
        ceo_db.insert_fact(f)
        return {"imported": 1, "skipped": 0, "entity_type": "fact"}

    # Unknown type — skip
    return {"imported": 0, "skipped": 1, "entity_type": mem_type}


# ---------------------------------------------------------------------------
# Real-time sync — called from hooks
# ---------------------------------------------------------------------------

def _cwd_to_claude_memory_dir(cwd: str) -> str | None:
    """Derive the Claude Code auto-memory directory for a given cwd.

    Returns the path if it exists, or None.
    """
    if not cwd:
        return None
    # Claude Code encodes cwd as: replace "/" with "-", keep leading dash
    encoded = cwd.replace("/", "-")
    candidates = [
        os.path.join(_CLAUDE_PROJECTS_DIR, encoded, "memory"),
        os.path.join(_CLAUDE_PROJECTS_DIR, encoded.lstrip("-"), "memory"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return None


def sync_project_memories(
    ceo_db,
    emb,
    cwd: str,
    state: "ImportState | None" = None,
) -> dict:
    """Scan the Claude auto-memory directory for a project and sync changed files.

    Called from the Stop hook to capture auto-memory updates written during the
    session.  Uses mtime-based incremental to skip unchanged files.

    Returns {"synced": N, "skipped": N}.
    """
    from memory_core.project_detect import detect_project

    memory_dir = _cwd_to_claude_memory_dir(cwd)
    if not memory_dir:
        return {"synced": 0, "skipped": 0}

    if state is None:
        state = ImportState()

    project_id = detect_project(ceo_db, cwd=cwd, emb=emb) or ""
    git_remote, github_url = _extract_git_info(cwd)

    synced = 0
    skipped = 0

    for md_path in glob.glob(os.path.join(memory_dir, "*.md")):
        if os.path.basename(md_path) == "MEMORY.md":
            continue
        if state.is_doc_current(md_path):
            skipped += 1
            continue

        content = _read_file(md_path)
        if not content:
            continue

        doc = DocInfo(
            path=md_path, content=content, doc_type="claude_memory",
            project_name=os.path.basename(cwd) if cwd else "",
            cwd=cwd, git_remote=git_remote, github_url=github_url,
        )
        result = ingest_claude_memory(ceo_db, emb, doc, project_id=project_id)
        state.mark_doc(md_path, project_id)
        synced += result.get("imported", 0)
        skipped += result.get("skipped", 0)

    state.save()
    return {"synced": synced, "skipped": skipped}


def sync_single_memory_file(
    ceo_db,
    emb,
    file_path: str,
    cwd: str,
    state: "ImportState | None" = None,
) -> dict:
    """Sync a single Claude auto-memory file into CEO Brain.

    Called from PostToolUse hook when we know the exact file that was written.

    Returns {"synced": 1, "skipped": 0} or {"synced": 0, "skipped": 1}.
    """
    from memory_core.project_detect import detect_project

    if os.path.basename(file_path) == "MEMORY.md":
        return {"synced": 0, "skipped": 1}

    if state is None:
        state = ImportState()

    if state.is_doc_current(file_path):
        return {"synced": 0, "skipped": 1}

    content = _read_file(file_path)
    if not content:
        return {"synced": 0, "skipped": 1}

    project_id = detect_project(ceo_db, cwd=cwd, emb=emb) or ""
    git_remote, github_url = _extract_git_info(cwd)

    doc = DocInfo(
        path=file_path, content=content, doc_type="claude_memory",
        project_name=os.path.basename(cwd) if cwd else "",
        cwd=cwd, git_remote=git_remote, github_url=github_url,
    )
    result = ingest_claude_memory(ceo_db, emb, doc, project_id=project_id)
    state.mark_doc(file_path, project_id)
    state.save()

    return {"synced": result.get("imported", 0), "skipped": result.get("skipped", 0)}


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""


# ---------------------------------------------------------------------------
# Import state management
# ---------------------------------------------------------------------------

_STATE_DIR = os.path.expanduser("~/.clickmem")
_STATE_PATH = os.path.join(_STATE_DIR, "import-state.json")


@dataclass
class ImportJob:
    job_id: str = ""
    pid: int = 0
    started_at: str = ""
    agent: str = ""
    progress: int = 0
    total: int = 0
    status: str = ""  # "running" | "completed" | "failed"
    error: str = ""
    sessions_imported: int = 0
    docs_imported: int = 0


class ImportState:
    """Persistent state for dedup and progress tracking."""

    def __init__(self, path: str | None = None):
        self._path = path or _STATE_PATH
        self._data: dict = {"version": 1, "sessions": {}, "docs": {}, "job": {}}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        if "sessions" not in self._data:
            self._data["sessions"] = {}
        if "docs" not in self._data:
            self._data["docs"] = {}
        if "job" not in self._data:
            self._data["job"] = {}

    def save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")

    def is_session_imported(self, session_id: str) -> bool:
        return session_id in self._data["sessions"]

    def mark_session(self, session_id: str, source: str, raw_id: str = ""):
        self._data["sessions"][session_id] = {
            "source": source,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "raw_id": raw_id,
        }

    def is_doc_current(self, path: str) -> bool:
        entry = self._data["docs"].get(path)
        if not entry:
            return False
        try:
            current_mtime = int(os.path.getmtime(path) * 1000)
            return current_mtime <= entry.get("mtimeMs", 0)
        except OSError:
            return False

    def mark_doc(self, path: str, project_id: str = ""):
        try:
            mtime = int(os.path.getmtime(path) * 1000)
        except OSError:
            mtime = 0
        self._data["docs"][path] = {
            "mtimeMs": mtime,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
        }

    def get_job(self) -> ImportJob:
        j = self._data.get("job", {})
        return ImportJob(**{k: v for k, v in j.items() if k in ImportJob.__dataclass_fields__})

    def set_job(self, job: ImportJob):
        from dataclasses import asdict
        self._data["job"] = asdict(job)
        self.save()

    @property
    def session_count(self) -> int:
        return len(self._data.get("sessions", {}))

    @property
    def doc_count(self) -> int:
        return len(self._data.get("docs", {}))


# ---------------------------------------------------------------------------
# Text assembly with metadata header
# ---------------------------------------------------------------------------

def build_text_with_header(
    text: str,
    *,
    project_name: str = "",
    cwd: str = "",
    github_url: str = "",
    git_branch: str = "",
    hostname: str = "",
    doc_type: str = "",
) -> str:
    """Prepend a metadata header so the LLM extractor sees project context."""
    parts = []
    if doc_type:
        parts.append(f"doc: {doc_type}")
    if project_name:
        parts.append(f"project: {project_name}")
    if cwd:
        parts.append(f"path: {cwd}")
    if github_url:
        parts.append(f"git: {github_url}")
    if git_branch:
        parts.append(f"branch: {git_branch}")
    if hostname:
        parts.append(f"host: {hostname}")

    if parts:
        header = "[" + " | ".join(parts) + "]"
        return f"{header}\n{text}"
    return text


# ---------------------------------------------------------------------------
# Import execution
# ---------------------------------------------------------------------------

def run_import(
    transport,
    agents: list[str],
    state: ImportState,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Run the full import pipeline.

    Phase 1: ingest conversation transcripts
    Phase 2: ingest knowledge docs (CLAUDE.md, AGENTS.md, memory/*.md)
    """
    stats = {
        "sessions_imported": 0, "sessions_skipped": 0,
        "docs_imported": 0, "docs_skipped": 0,
        "errors": 0, "projects_created": 0,
    }
    cwd_set: set[str] = set()

    # Phase 1: Transcripts
    readers: list[tuple[str, Iterator[SessionInfo]]] = []
    if "claude-code" in agents or "all" in agents:
        readers.append(("claude-code", ClaudeCodeReader().iter_sessions()))
    if "cursor" in agents or "all" in agents:
        readers.append(("cursor", CursorReader().iter_sessions()))
    if "codex" in agents or "all" in agents:
        readers.append(("codex", CodexReader().iter_sessions()))

    total_sessions = 0
    for agent_name, session_iter in readers:
        for info in session_iter:
            total_sessions += 1

            if state.is_session_imported(info.session_id):
                stats["sessions_skipped"] += 1
                if on_progress:
                    on_progress(total_sessions, 0, f"skip {info.source}/{info.session_id[:8]}")
                continue

            if info.cwd:
                cwd_set.add(info.cwd)

            text = build_text_with_header(
                info.text,
                project_name=info.project_name,
                cwd=info.cwd,
                github_url=info.github_url,
                git_branch=info.git_branch,
                hostname=info.hostname,
            )

            try:
                result = transport.ingest(
                    text=text,
                    session_id=info.session_id,
                    source=info.source,
                    cwd=info.cwd,
                )
                raw_id = result.get("raw_id", "")
                state.mark_session(info.session_id, info.source, raw_id)
                stats["sessions_imported"] += 1

                if on_progress:
                    ep = len(result.get("episodes", []))
                    dec = len(result.get("decisions", []))
                    on_progress(
                        total_sessions, 0,
                        f"{info.source}/{info.project_name}: {ep}ep {dec}dec",
                    )
            except Exception as e:
                logger.warning("Failed to ingest session %s: %s", info.session_id[:8], e)
                stats["errors"] += 1
                if on_progress:
                    on_progress(total_sessions, 0, f"ERROR: {e}")

            # Periodic save
            if total_sessions % 10 == 0:
                state.save()

    # Phase 2: Knowledge docs
    docs = discover_knowledge_docs(cwd_set)

    # OpenClaw import (reuse existing logic)
    if ("openclaw" in agents or "all" in agents) and os.path.isdir(_OPENCLAW_DIR):
        _import_openclaw_wrapper(transport, state, stats)

    for doc in docs:
        if state.is_doc_current(doc.path):
            stats["docs_skipped"] += 1
            continue

        try:
            if doc.doc_type == "AGENTS.md":
                # AGENTS.md is already structured — parse bullets directly as principles
                from memory_core.transport import LocalTransport
                if isinstance(transport, LocalTransport):
                    ceo_db = transport._get_ceo_db()
                    emb_engine = transport._get_emb()
                    from memory_core.project_detect import detect_project
                    pid = detect_project(ceo_db, cwd=doc.cwd, emb=emb_engine) or ""
                    r = ingest_agents_md(ceo_db, emb_engine, doc, project_id=pid)
                    state.mark_doc(doc.path)
                    stats["docs_imported"] += 1
                    if on_progress:
                        on_progress(
                            total_sessions, stats["docs_imported"],
                            f"doc: {doc.doc_type}/{doc.project_name} ({r['imported']} principles)",
                        )
                else:
                    # Remote: fall through to ingest API
                    _ingest_doc_via_api(transport, doc, state, stats, total_sessions, on_progress)
            elif doc.doc_type == "claude_memory":
                # Auto-memory: direct parse, no LLM needed
                from memory_core.transport import LocalTransport as _LT2
                if isinstance(transport, _LT2):
                    ceo_db = transport._get_ceo_db()
                    emb_engine = transport._get_emb()
                    from memory_core.project_detect import detect_project
                    pid = detect_project(ceo_db, cwd=doc.cwd, emb=emb_engine) or ""
                    r = ingest_claude_memory(ceo_db, emb_engine, doc, project_id=pid)
                    state.mark_doc(doc.path, pid)
                    stats["docs_imported"] += 1
                    if on_progress:
                        on_progress(
                            total_sessions, stats["docs_imported"],
                            f"doc: auto-memory/{doc.project_name} ({r.get('entity_type', '?')})",
                        )
                else:
                    _ingest_doc_via_api(transport, doc, state, stats, total_sessions, on_progress)
            else:
                # CLAUDE.md, cursor_rule — send to LLM extraction
                _ingest_doc_via_api(transport, doc, state, stats, total_sessions, on_progress)

        except Exception as e:
            logger.warning("Failed to ingest doc %s: %s", doc.path, e)
            stats["errors"] += 1

    state.save()
    return stats


def _ingest_doc_via_api(transport, doc: DocInfo, state: ImportState, stats: dict,
                        total_sessions: int, on_progress) -> None:
    """Ingest a knowledge doc via the standard transport.ingest() path (LLM extraction)."""
    text = build_text_with_header(
        doc.content,
        doc_type=doc.doc_type,
        project_name=doc.project_name,
        cwd=doc.cwd,
        github_url=doc.github_url,
    )
    transport.ingest(
        text=text,
        session_id=f"doc-{os.path.basename(doc.path)}-{hash(doc.path) & 0xFFFF:04x}",
        source="import",
        cwd=doc.cwd,
    )
    state.mark_doc(doc.path)
    stats["docs_imported"] += 1
    if on_progress:
        on_progress(
            total_sessions, stats["docs_imported"],
            f"doc: {doc.doc_type}/{doc.project_name}",
        )


def _import_openclaw_wrapper(transport, state: ImportState, stats: dict):
    """Wrap the existing OpenClaw import into the new state-tracked system."""
    oc_session_id = "openclaw-bulk-import"
    if state.is_session_imported(oc_session_id):
        stats["sessions_skipped"] += 1
        return

    try:
        from memory_core.import_openclaw import import_workspace_memories, import_sqlite_chunks
        from memory_core.transport import LocalTransport

        if not isinstance(transport, LocalTransport):
            logger.info("OpenClaw import requires LocalTransport; skipping")
            return

        db = transport._get_db()
        emb = transport._get_emb()
        r1 = import_workspace_memories(db, emb, _OPENCLAW_DIR)
        r2 = import_sqlite_chunks(db, emb, _OPENCLAW_DIR)
        total = r1.get("imported", 0) + r2.get("imported", 0)
        state.mark_session(oc_session_id, "openclaw")
        stats["sessions_imported"] += total
        logger.info("OpenClaw import: %d workspace + %d sqlite", r1.get("imported", 0), r2.get("imported", 0))
    except Exception as e:
        logger.warning("OpenClaw import failed: %s", e)
        stats["errors"] += 1
