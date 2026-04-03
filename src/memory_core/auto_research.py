"""Auto-research: generate recall probes from real conversations, test, diagnose."""

from __future__ import annotations

import json, logging, os, random, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("clickmem.research")
REVIEW_DIR = Path(os.path.expanduser("~/.clickmem/reviews"))


# ---------------------------------------------------------------------------
# Phase A: sample conversations and generate probes
# ---------------------------------------------------------------------------

def sample_conversations(n: int = 10, days: int = 7, sources: list[str] | None = None) -> list[dict]:
    from memory_core.import_agent import ClaudeCodeReader, CursorReader, CodexReader

    readers: list[tuple[str, object]] = [
        ("claude_code", ClaudeCodeReader()),
        ("cursor", CursorReader()),
        ("codex", CodexReader()),
    ]
    if sources:
        readers = [(name, r) for name, r in readers if name in sources]

    since = time.time() - days * 86400
    sessions: list[dict] = []

    for source_name, reader in readers:
        try:
            for sess in reader.iter_sessions(since=since):
                sessions.append({
                    "source": source_name,
                    "cwd": sess.cwd,
                    "project": sess.project_name or Path(sess.cwd).name if sess.cwd else "",
                    "text_snippet": _truncate_text(sess.text),
                    "timestamp": sess.timestamp,
                    "session_id": sess.session_id,
                })
        except Exception as exc:
            logger.warning("Failed reading %s sessions: %s", source_name, exc)

    if len(sessions) > n:
        sessions = random.sample(sessions, n)

    return sessions


def _truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]


def build_probe_prompt(conversations: list[dict]) -> str:
    instruction = (
        "Examine these real AI coding conversations with a critical eye. "
        "Identify key information that was conveyed, corrected, or decided.\n\n"
        "For each significant piece of information, design a recall probe:\n"
        '- "query": a natural question a user would ask to retrieve this info\n'
        '- "probe_words": 2-4 keywords that MUST appear in correct recall results\n'
        '- "rationale": one sentence on why this matters\n\n'
        "Focus on: factual details (IPs, ports, paths, configs), decisions with reasoning, "
        "corrections of prior beliefs, cross-project patterns. Skip generic code discussion.\n\n"
        "Output ONLY a JSON array. Target 3-5 probes per conversation."
    )
    parts = [instruction, ""]
    for conv in conversations:
        date = conv.get("timestamp", "")[:10]
        header = f"--- [{conv['source']} | {conv.get('project', '?')} | {date}]"
        parts.append(header)
        parts.append(conv["text_snippet"])
        parts.append("---\n")
    return "\n".join(parts)


def parse_probes(llm_output: str) -> list[dict]:
    from memory_core.json_utils import extract_json

    parsed = extract_json(llm_output, expect="array")
    if not isinstance(parsed, list):
        logger.warning("Could not extract probe array from LLM output")
        return []

    valid: list[dict] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        query = entry.get("query", "")
        probe_words = entry.get("probe_words", [])
        if isinstance(query, str) and query.strip() and isinstance(probe_words, list) and probe_words:
            valid.append(entry)
        else:
            logger.warning("Skipping invalid probe: %s", json.dumps(entry)[:120])
    return valid


# ---------------------------------------------------------------------------
# Phase B: run probes and evaluate
# ---------------------------------------------------------------------------

def run_probes(probes: list[dict], transport, top_k: int = 5) -> list[dict]:
    from memory_core.models import RetrievalConfig

    results: list[dict] = []
    cfg = RetrievalConfig(top_k=top_k)

    for probe in probes:
        query = probe["query"]
        probe_words = probe.get("probe_words", [])

        try:
            recall_results = transport.recall(query=query, cfg=cfg)
        except Exception as exc:
            logger.warning("Recall failed for query %r: %s", query, exc)
            recall_results = []

        # Concatenate all recalled content
        combined = " ".join(r.get("content", "") for r in recall_results).lower()

        found: list[str] = []
        missing: list[str] = []
        for word in probe_words:
            if word.lower() in combined:
                found.append(word)
            else:
                missing.append(word)

        if not missing:
            status = "pass"
        elif found:
            status = "partial"
        else:
            status = "fail"

        results.append({
            **probe,
            "status": status,
            "found_words": found,
            "missing_words": missing,
            "top_results": [
                {"content": r.get("content", "")[:200], "score": r.get("final_score", 0)}
                for r in recall_results[:3]
            ],
        })

    return results


def build_eval_prompt(failed_probes: list[dict]) -> str:
    instruction = (
        "Analyze these failed/partial recall probes and attribute each failure to systemic categories:\n"
        "- embedding: semantic gap between query and stored content\n"
        "- keyword: literal match failure in keyword search\n"
        "- entity_ranking: correct data exists but ranked too low\n"
        "- data_gap: information was never stored\n"
        "- scope: wrong project context surfaced\n\n"
        "Output JSON: {\"failure_analysis\": [{\"query\": ..., \"category\": ..., \"explanation\": ...}], "
        "\"systemic_issues\": [\"...\"], \"parameter_suggestions\": [\"...\"]}"
    )
    parts = [instruction, ""]
    for p in failed_probes:
        parts.append(f"Query: {p['query']}")
        parts.append(f"Status: {p['status']}")
        parts.append(f"Missing: {p.get('missing_words', [])}")
        if p.get("top_results"):
            parts.append(f"Top result: {p['top_results'][0].get('content', '')[:150]}")
        parts.append("")
    return "\n".join(parts)


def generate_report(probe_results: list[dict], evaluation: dict | None = None, date: str = "") -> str:
    if not date:
        date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    total = len(probe_results)
    passed = [p for p in probe_results if p["status"] == "pass"]
    partial = [p for p in probe_results if p["status"] == "partial"]
    failed = [p for p in probe_results if p["status"] == "fail"]

    lines = [
        f"# ClickMem Recall Research Report — {date}",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total probes | {total} |",
        f"| Pass | {len(passed)} |",
        f"| Partial | {len(partial)} |",
        f"| Fail | {len(failed)} |",
        f"| Pass rate | {len(passed) / total * 100:.0f}% |" if total else "| Pass rate | N/A |",
        "",
    ]

    if passed:
        lines.append("## Passed Probes")
        lines.append("")
        for p in passed:
            lines.append(f"- **{p['query']}** — words: {', '.join(p.get('probe_words', []))}")
        lines.append("")

    if partial:
        lines.append("## Partial Matches")
        lines.append("")
        for p in partial:
            lines.append(f"- **{p['query']}**")
            lines.append(f"  - Found: {', '.join(p.get('found_words', []))}")
            lines.append(f"  - Missing: {', '.join(p.get('missing_words', []))}")
        lines.append("")

    if failed:
        lines.append("## Failed Probes")
        lines.append("")
        for p in failed:
            lines.append(f"- **{p['query']}**")
            lines.append(f"  - Expected: {', '.join(p.get('probe_words', []))}")
            if p.get("top_results"):
                top = p["top_results"][0]
                lines.append(f"  - Top result ({top.get('score', 0):.3f}): {top.get('content', '')[:100]}...")
        lines.append("")

    if evaluation:
        if evaluation.get("failure_analysis"):
            lines.append("## Failure Analysis")
            lines.append("")
            for fa in evaluation["failure_analysis"]:
                lines.append(f"- **{fa.get('category', '?')}**: {fa.get('query', '')} — {fa.get('explanation', '')}")
            lines.append("")

        if evaluation.get("systemic_issues"):
            lines.append("## Systemic Issues")
            lines.append("")
            for issue in evaluation["systemic_issues"]:
                lines.append(f"- {issue}")
            lines.append("")

        if evaluation.get("parameter_suggestions"):
            lines.append("## Parameter Suggestions")
            lines.append("")
            for sug in evaluation["parameter_suggestions"]:
                lines.append(f"- {sug}")
            lines.append("")

    return "\n".join(lines)
