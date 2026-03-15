"""Conversation filter — prioritise user input, condense agent output."""

from __future__ import annotations

import re

_MAX_AGENT_TURN_CHARS = 500
_MAX_TOTAL_CHARS = 4000
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_FILLER_RE = re.compile(
    r"^(Sure,?\s+I('ll|'d| will| can).*?[.!]|"
    r"Let me .*?[.!]|"
    r"I'll .*?[.!]|"
    r"Of course.*?[.!]|"
    r"Absolutely.*?[.!])",
    re.IGNORECASE | re.MULTILINE,
)
_ROLE_LINE_RE = re.compile(r"^(user|assistant|human|ai):\s*", re.IGNORECASE | re.MULTILINE)


def filter_conversation(
    messages: list[dict],
    agent_source: str = "",
) -> str:
    """Filter conversation messages: keep user fully, condense agent.

    Args:
        messages: list of {"role": "user"|"assistant", "content": str}
        agent_source: "claude_code" | "cursor" | "openclaw" (for future heuristics)

    Returns:
        Filtered text suitable for extraction (max ~4000 chars).
    """
    parts: list[str] = []
    total = 0

    for msg in messages:
        role = msg.get("role", "user").lower()
        content = msg.get("content", "")
        if not content.strip():
            continue

        if role in ("user", "human"):
            chunk = f"[User]: {content}"
        else:
            chunk = f"[Agent]: {_condense_agent(content)}"

        if total + len(chunk) > _MAX_TOTAL_CHARS:
            remaining = _MAX_TOTAL_CHARS - total
            if remaining > 50:
                parts.append(chunk[:remaining])
            break

        parts.append(chunk)
        total += len(chunk)

    return "\n\n".join(parts)


def _condense_agent(text: str) -> str:
    """Condense an agent message: strip code blocks, filler, limit length."""
    # Replace long code blocks with summary
    def _replace_code(m: re.Match) -> str:
        block = m.group(0)
        lines = block.split("\n")
        if len(lines) > 10:
            # Extract filename hint from first line if present
            first_line = lines[0].replace("```", "").strip()
            hint = f" ({first_line})" if first_line else ""
            return f"[code block{hint}: {len(lines)} lines]"
        return block

    result = _CODE_BLOCK_RE.sub(_replace_code, text)

    # Strip filler phrases
    result = _FILLER_RE.sub("", result)

    # Collapse whitespace
    result = re.sub(r"\n{3,}", "\n\n", result).strip()

    # Truncate
    if len(result) > _MAX_AGENT_TURN_CHARS:
        result = result[:_MAX_AGENT_TURN_CHARS] + "..."

    return result


def extract_user_messages(raw_text: str) -> str:
    """Extract just user messages from a raw 'user: ...' / 'assistant: ...' dump.

    Handles the format produced by Claude Code hooks.
    """
    lines = raw_text.split("\n")
    user_parts: list[str] = []
    current_role = ""
    current_content: list[str] = []

    for line in lines:
        role_match = _ROLE_LINE_RE.match(line)
        if role_match:
            # Flush previous
            if current_role in ("user", "human") and current_content:
                user_parts.append("\n".join(current_content))
            current_role = role_match.group(1).lower()
            remainder = line[role_match.end():]
            current_content = [remainder] if remainder.strip() else []
        else:
            current_content.append(line)

    # Flush last block
    if current_role in ("user", "human") and current_content:
        user_parts.append("\n".join(current_content))

    return "\n\n".join(user_parts)
