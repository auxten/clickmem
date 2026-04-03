"""Session Replay — recover missed turns from Claude Code session logs.

When hooks are interrupted (e.g., during plugin reinstall), conversation
turns are lost. This module reads Claude Code's JSONL session files and
re-ingests any turns that weren't captured.

Triggered on SessionStart to catch up on the previous session's missing data.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_core.transport import LocalTransport

_log = logging.getLogger("clickmem.session_replay")

# Claude Code stores sessions here
_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

_MAX_TURN_CHARS = 8000  # must match server.py _CC_MAX_TURN_CHARS


def _encode_project_path(cwd: str) -> str:
    """Encode a CWD path to Claude Code's directory name format.

    /Users/auxten/Codes/foo → -Users-auxten-Codes-foo
    """
    return cwd.replace("/", "-").lstrip("-")


def _find_recent_sessions(cwd: str, limit: int = 3) -> list[Path]:
    """Find recent session JSONL files for a project directory."""
    project_hash = _encode_project_path(cwd)
    project_dir = _CLAUDE_PROJECTS_DIR / project_hash

    if not project_dir.is_dir():
        return []

    jsonl_files = list(project_dir.glob("*.jsonl"))
    # Sort by modification time, most recent first
    jsonl_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return jsonl_files[:limit]


def _extract_turns(session_path: Path) -> list[dict]:
    """Extract user→assistant turn pairs from a Claude Code session JSONL.

    Returns list of dicts with keys: user_content, assistant_content, timestamp, session_id.
    Skips tool results, progress messages, subagent sidechains, etc.
    """
    messages: list[dict] = []

    try:
        with open(session_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")
                if msg_type not in ("user", "assistant"):
                    continue

                # Skip subagent messages
                if msg.get("isSidechain"):
                    continue

                role = msg.get("message", {}).get("role", "")
                content = msg.get("message", {}).get("content", "")

                # Handle structured content (array with text blocks and tool results)
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_result":
                                pass  # skip tool results
                            elif block.get("type") == "tool_use":
                                pass  # skip tool use blocks
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = "\n".join(text_parts)

                if not content or not content.strip():
                    continue

                messages.append({
                    "role": role,
                    "content": content.strip(),
                    "timestamp": msg.get("timestamp", ""),
                    "session_id": msg.get("sessionId", ""),
                })
    except Exception as exc:
        _log.warning("Failed to read session file %s: %s", session_path.name, exc)
        return []

    # Pair user→assistant turns
    turns: list[dict] = []
    i = 0
    while i < len(messages):
        if messages[i]["role"] == "user":
            user_msg = messages[i]
            # Look for next assistant response
            if i + 1 < len(messages) and messages[i + 1]["role"] == "assistant":
                asst_msg = messages[i + 1]
                turn_text = f"user: {user_msg['content']}\nassistant: {asst_msg['content']}"
                if len(turn_text) > _MAX_TURN_CHARS:
                    turn_text = turn_text[:_MAX_TURN_CHARS]
                if len(turn_text) >= 40:
                    turns.append({
                        "text": turn_text,
                        "timestamp": user_msg["timestamp"],
                        "session_id": user_msg["session_id"],
                    })
                i += 2
                continue
        i += 1

    return turns


def replay_missing_turns(
    transport: LocalTransport,
    cwd: str,
    current_session_id: str = "",
) -> dict:
    """Find and re-ingest turns that were missed by hooks.

    Reads recent Claude Code session files, checks which turns are
    already in raw_transcripts, and ingests the missing ones.

    Returns summary dict with counts.
    """
    db = transport._get_db()

    # Find recent session files (check last 2 sessions, not current)
    session_files = _find_recent_sessions(cwd, limit=5)
    if not session_files:
        return {"sessions_checked": 0, "turns_replayed": 0}

    total_replayed = 0
    sessions_checked = 0

    for session_path in session_files:
        session_id = session_path.stem  # UUID from filename

        # Skip the current active session (it's still in progress)
        if session_id == current_session_id:
            continue

        sessions_checked += 1
        turns = _extract_turns(session_path)
        if not turns:
            continue

        # Check which turns are already in raw_transcripts
        existing = db.query(
            f"SELECT content, created_at FROM raw_transcripts FINAL "
            f"WHERE session_id = '{db._escape(session_id)}' "
            f"AND source = 'claude_code'"
        )
        existing_snippets = set()
        for row in existing:
            # Use first 100 chars of content as a fingerprint
            snippet = row.get("content", "")[:100]
            if snippet:
                existing_snippets.add(snippet)

        # Ingest missing turns
        for turn in turns:
            snippet = turn["text"][:100]
            if snippet in existing_snippets:
                continue  # already ingested

            try:
                transport.ingest(
                    text=turn["text"],
                    session_id=session_id,
                    source="claude_code",
                    cwd=cwd,
                )
                total_replayed += 1
                _log.info(
                    "Replayed turn from session %s (%d chars)",
                    session_id[:8], len(turn["text"]),
                )
            except Exception as exc:
                _log.warning("Failed to replay turn: %s", exc)

        if total_replayed > 0:
            _log.info(
                "Session replay: %d turns recovered from %s",
                total_replayed, session_id[:8],
            )

    return {
        "sessions_checked": sessions_checked,
        "turns_replayed": total_replayed,
    }
