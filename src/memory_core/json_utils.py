"""Robust JSON extraction from LLM responses.

Small language models often wrap JSON in markdown fences, add preamble text,
or produce slightly malformed output.  The helpers here handle the common
failure modes so callers don't need to.
"""

from __future__ import annotations

import json


def extract_json(text: str, expect: str = "auto"):
    """Extract a JSON object or array from potentially messy LLM output.

    Args:
        text: Raw LLM response.
        expect: ``"object"``, ``"array"``, or ``"auto"`` (accept either).

    Returns:
        Parsed ``dict`` / ``list``, or ``None`` on failure.
    """
    text = text.strip()

    # 1. Direct parse
    result = _try_parse(text)
    if result is not None and _type_ok(result, expect):
        return result

    # 2. Strip markdown code fences
    clean = _strip_fences(text)
    if clean != text:
        result = _try_parse(clean)
        if result is not None and _type_ok(result, expect):
            return result
    else:
        clean = text

    # 3. Scan for first valid JSON value using raw_decode
    targets = _open_chars(expect)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(clean):
        if ch in targets:
            try:
                result, _ = decoder.raw_decode(clean, i)
                if _type_ok(result, expect):
                    return result
            except (json.JSONDecodeError, ValueError):
                continue

    return None


def extract_json_or(text: str, default, expect: str = "auto"):
    """Like :func:`extract_json` but returns *default* instead of ``None``."""
    result = extract_json(text, expect=expect)
    return result if result is not None else default


# ------------------------------------------------------------------

def _try_parse(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _strip_fences(text: str) -> str:
    lines = text.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _open_chars(expect: str) -> str:
    if expect == "object":
        return "{"
    if expect == "array":
        return "["
    return "{["


def _type_ok(parsed, expect: str) -> bool:
    if expect == "auto":
        return isinstance(parsed, (dict, list))
    if expect == "object":
        return isinstance(parsed, dict)
    if expect == "array":
        return isinstance(parsed, list)
    return True
