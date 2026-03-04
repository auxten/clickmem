"""Import OpenClaw memory history into clickmem."""

from __future__ import annotations

import glob
import os
import re
import sqlite3
from typing import TYPE_CHECKING

from memory_core.models import Memory

if TYPE_CHECKING:
    from memory_core.db import MemoryDB


def import_workspace_memories(db: "MemoryDB", emb, openclaw_dir: str) -> dict:
    """Scan ~/.openclaw/workspace-*/memory/*.md and import as L1 episodic memories.

    Returns {"imported": N, "skipped": N}.
    """
    imported = 0
    skipped = 0

    pattern = os.path.join(openclaw_dir, "workspace-*", "memory", "*.md")
    for filepath in sorted(glob.glob(pattern)):
        basename = os.path.basename(filepath)
        # Try to extract date from filename like 2026-03-01.md
        date_match = re.match(r"(\d{4}-\d{2}-\d{2})", basename)

        content = _read_file(filepath)
        if not content:
            skipped += 1
            continue

        # Check for duplicate content
        if _content_exists(db, content):
            skipped += 1
            continue

        tags = ["openclaw_import"]
        if date_match:
            tags.append(date_match.group(1))

        m = Memory(
            content=content,
            layer="episodic",
            category="event",
            tags=tags,
            embedding=emb.encode_document(content),
            source="openclaw_import",
        )
        db.insert(m)
        imported += 1

    return {"imported": imported, "skipped": skipped}


def import_sqlite_chunks(db: "MemoryDB", emb, openclaw_dir: str) -> dict:
    """Scan ~/.openclaw/memory/*.sqlite and import chunks as L2 semantic memories.

    Returns {"imported": N, "skipped": N}.
    """
    imported = 0
    skipped = 0

    pattern = os.path.join(openclaw_dir, "memory", "*.sqlite")
    for sqlite_path in sorted(glob.glob(pattern)):
        try:
            chunks = _read_sqlite_chunks(sqlite_path)
        except Exception:
            skipped += 1
            continue

        for text in chunks:
            text = text.strip()
            if not text:
                skipped += 1
                continue

            if _content_exists(db, text):
                skipped += 1
                continue

            m = Memory(
                content=text,
                layer="semantic",
                category="knowledge",
                tags=["openclaw_import"],
                embedding=emb.encode_document(text),
                source="openclaw_import",
            )
            db.insert(m)
            imported += 1

    return {"imported": imported, "skipped": skipped}


def _read_file(filepath: str) -> str:
    """Read a file and return stripped content, or empty string on failure."""
    try:
        with open(filepath, encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _read_sqlite_chunks(sqlite_path: str) -> list[str]:
    """Read text chunks from an OpenClaw memory SQLite database."""
    conn = sqlite3.connect(sqlite_path)
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        texts = []
        # Try common table/column names
        for table in tables:
            try:
                cursor = conn.execute(f"PRAGMA table_info({table})")
                columns = [row[1] for row in cursor.fetchall()]

                text_col = None
                for candidate in ("text", "content", "chunk", "value"):
                    if candidate in columns:
                        text_col = candidate
                        break

                if text_col:
                    cursor = conn.execute(f"SELECT {text_col} FROM {table}")
                    for row in cursor.fetchall():
                        if row[0] and isinstance(row[0], str):
                            texts.append(row[0])
            except sqlite3.OperationalError:
                continue

        return texts
    finally:
        conn.close()


def _content_exists(db: "MemoryDB", content: str) -> bool:
    """Check if a memory with the same content already exists."""
    escaped = db._escape(content)
    rows = db.query(
        f"SELECT count() as cnt FROM memories "
        f"WHERE content = '{escaped}' AND is_active = 1"
    )
    return bool(rows and int(rows[0]["cnt"]) > 0)
