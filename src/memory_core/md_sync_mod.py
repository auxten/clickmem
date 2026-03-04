"""Markdown sync — export memories to human-readable .md files."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_core.db import MemoryDB


class md_sync:
    """Markdown export for memory layers."""

    @staticmethod
    def export_memory_md(db: "MemoryDB", workspace_path: str) -> str:
        """Export L2 semantic memories to MEMORY.md."""
        memories = db.list_by_layer("semantic")

        # Group by category
        by_category: dict[str, list] = defaultdict(list)
        for m in memories:
            by_category[m.category].append(m)

        lines = ["# Semantic Memory\n"]
        if not by_category:
            lines.append("_No semantic memories yet._\n")
        else:
            for cat in sorted(by_category.keys()):
                lines.append(f"\n## [{cat}]\n")
                for m in sorted(by_category[cat], key=lambda x: x.content):
                    lines.append(f"- {m.content}\n")

        filepath = os.path.join(workspace_path, "MEMORY.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return filepath

    @staticmethod
    def export_daily_md(db: "MemoryDB", workspace_path: str, date: str = "") -> str:
        """Export L1 episodic memories for a specific day."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Ensure memory/ subdirectory exists
        memory_dir = os.path.join(workspace_path, "memory")
        os.makedirs(memory_dir, exist_ok=True)

        # Get episodic memories for this date
        # We query by month then filter by date
        month = date[:7]  # "2026-03"
        all_monthly = db.get_episodic_by_month(month)

        day_memories = []
        for m in all_monthly:
            if m.created_at:
                mem_date = m.created_at.strftime("%Y-%m-%d")
                if mem_date == date:
                    day_memories.append(m)

        # If no memories found via month query, try listing all episodic
        if not day_memories:
            all_episodic = db.list_by_layer("episodic", limit=1000)
            for m in all_episodic:
                if m.created_at:
                    mem_date = m.created_at.strftime("%Y-%m-%d")
                    if mem_date == date:
                        day_memories.append(m)

        lines = [f"# Episodic Memory — {date}\n"]

        if not day_memories:
            lines.append("\n_No events recorded for this day._\n")
        else:
            for m in day_memories:
                time_str = ""
                if m.created_at:
                    time_str = m.created_at.strftime("%H:%M")
                lines.append(f"\n## {time_str} - {m.content}\n")
                if m.tags:
                    lines.append(f"Tags: {', '.join(m.tags)}\n")
                if m.category:
                    lines.append(f"Category: {m.category}\n")

        filepath = os.path.join(memory_dir, f"{date}.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return filepath
