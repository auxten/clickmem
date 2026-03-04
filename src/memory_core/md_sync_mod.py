"""Markdown sync — export memories to human-readable .md files."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_core.db import MemoryDB

# Defaults for bootstrap injection (keep context window reasonable)
DEFAULT_MAX_ITEMS = 50
DEFAULT_MAX_CHARS = 8000


class md_sync:
    """Markdown export for memory layers."""

    @staticmethod
    def format_memory_md(
        db: "MemoryDB",
        max_items: int = DEFAULT_MAX_ITEMS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> str:
        """Format L2 semantic memories as markdown string.

        Memories are sorted by recency (newest first) and truncated
        to max_items entries or max_chars total length.
        """
        memories = db.list_by_layer("semantic", limit=max_items)
        # list_by_layer already returns ORDER BY created_at DESC

        by_category: dict[str, list] = defaultdict(list)
        for m in memories:
            by_category[m.category].append(m)

        lines = ["# Semantic Memory\n"]
        if not by_category:
            lines.append("_No semantic memories yet._\n")
        else:
            total_chars = len(lines[0])
            item_count = 0
            for cat in sorted(by_category.keys()):
                header = f"\n## [{cat}]\n"
                lines.append(header)
                total_chars += len(header)
                # Sort by recency within category (newest first), then content for stability
                sorted_mems = sorted(by_category[cat], key=lambda x: x.content)
                for i, m in enumerate(sorted_mems):
                    entry = f"- {m.content}\n"
                    if i < len(sorted_mems) - 1:
                        entry += "---\n"
                    if total_chars + len(entry) > max_chars:
                        lines.append(f"\n_... truncated at {max_chars} chars_\n")
                        return "".join(lines)
                    lines.append(entry)
                    total_chars += len(entry)
                    item_count += 1

        return "".join(lines)

    @staticmethod
    def export_memory_md(db: "MemoryDB", workspace_path: str, **kwargs) -> str:
        """Export L2 semantic memories to MEMORY.md."""
        content = md_sync.format_memory_md(db, **kwargs)
        filepath = os.path.join(workspace_path, "MEMORY.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    @staticmethod
    def format_daily_md(
        db: "MemoryDB",
        date: str = "",
        max_items: int = DEFAULT_MAX_ITEMS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> str:
        """Format L1 episodic memories for a specific day as markdown string."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        month = date[:7]
        all_monthly = db.get_episodic_by_month(month)

        day_memories = []
        for m in all_monthly:
            if m.created_at:
                mem_date = m.created_at.strftime("%Y-%m-%d")
                if mem_date == date:
                    day_memories.append(m)

        if not day_memories:
            all_episodic = db.list_by_layer("episodic", limit=1000)
            for m in all_episodic:
                if m.created_at:
                    mem_date = m.created_at.strftime("%Y-%m-%d")
                    if mem_date == date:
                        day_memories.append(m)

        # Sort by time, newest first; limit count
        day_memories.sort(
            key=lambda m: m.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        day_memories = day_memories[:max_items]

        lines = [f"# Episodic Memory — {date}\n"]

        if not day_memories:
            lines.append("\n_No events recorded for this day._\n")
        else:
            total_chars = len(lines[0])
            for m in day_memories:
                time_str = ""
                if m.created_at:
                    time_str = m.created_at.strftime("%H:%M")
                entry = f"\n## {time_str} - {m.content}\n"
                if m.tags:
                    entry += f"Tags: {', '.join(m.tags)}\n"
                if m.category:
                    entry += f"Category: {m.category}\n"
                entry += "---\n"

                if total_chars + len(entry) > max_chars:
                    lines.append(f"\n_... truncated at {max_chars} chars_\n")
                    break
                lines.append(entry)
                total_chars += len(entry)

        return "".join(lines)

    @staticmethod
    def export_daily_md(db: "MemoryDB", workspace_path: str, date: str = "", **kwargs) -> str:
        """Export L1 episodic memories for a specific day."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        memory_dir = os.path.join(workspace_path, "memory")
        os.makedirs(memory_dir, exist_ok=True)

        content = md_sync.format_daily_md(db, date, **kwargs)
        filepath = os.path.join(memory_dir, f"{date}.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath
