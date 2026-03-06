"""Maintenance functions for the memory system.

Provides cleanup_stale, purge_deleted, compress_episodic,
promote_to_semantic, review_semantic, and run_all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from memory_core.json_utils import extract_json_or
from memory_core.models import Memory

if TYPE_CHECKING:
    from memory_core.db import MemoryDB

# ---------------------------------------------------------------------------
# Prompt templates — kept short and explicit for small-model compatibility.
# Every prompt includes a concrete JSON example so 2B models know the format.
# ---------------------------------------------------------------------------

_COMPRESS_PROMPT = """\
Summarize the following episodic memories from {month} into one concise paragraph.

Memories:
{memories}

Return ONLY a JSON object like this example:
{{"summary": "In January the team migrated to PostgreSQL and adopted gRPC.", "category": "event", "tags": ["migration", "grpc"], "entities": ["PostgreSQL"]}}
"""

_PROMOTE_PROMPT = """\
The tag "{tag}" appeared {count} times in recent memories.

Samples:
{samples}

Should this be saved as a long-term fact? If yes, write a single sentence capturing the pattern.

Return ONLY a JSON object:
{{"should_promote": true, "content": "The team regularly uses microservices.", "category": "knowledge", "tags": ["microservices"]}}

If not worth promoting:
{{"should_promote": false, "content": "", "category": "", "tags": []}}
"""

_REVIEW_PROMPT = """\
Review these long-term memories. Identify any that are outdated or wrong.

Memories:
{memories}

Return ONLY a JSON object:
{{"stale_ids": ["id1"], "updates": [{{"id": "id2", "new_content": "corrected text"}}]}}

If everything looks fine:
{{"stale_ids": [], "updates": []}}
"""

_REVIEW_BATCH_SIZE = 10


class maintenance:
    """Memory maintenance operations."""

    @staticmethod
    def run_all(db: "MemoryDB", llm_complete=None, emb=None) -> dict:
        stale = maintenance.cleanup_stale(db)
        purged = maintenance.purge_deleted(db)
        compressed = 0
        promoted = 0
        reviewed = 0

        if llm_complete and emb:
            promoted = maintenance.promote_to_semantic(db, llm_complete, emb)

        if llm_complete:
            reviewed = maintenance.review_semantic(db, llm_complete)

        return {
            "stale_cleaned": stale,
            "deleted_purged": purged,
            "compressed": compressed,
            "promoted": promoted,
            "reviewed": reviewed,
        }

    @staticmethod
    def cleanup_stale(db: "MemoryDB", decay_days: int = 120) -> int:
        stale = db.find_stale_episodic(decay_days=decay_days)
        count = 0
        for m in stale:
            db.deactivate(m.id)
            count += 1
        return count

    @staticmethod
    def purge_deleted(db: "MemoryDB", days: int = 7) -> int:
        deleted = db.find_deleted(days=days)
        count = 0
        for m in deleted:
            db.delete(m.id)
            count += 1
        return count

    @staticmethod
    def compress_episodic(db: "MemoryDB", llm_complete, emb, month: str = "") -> int:
        if not month:
            from datetime import datetime, timezone
            month = datetime.now(timezone.utc).strftime("%Y-%m")

        entries = db.get_episodic_by_month(month)
        if len(entries) < 1:
            return 0

        memories_text = "\n".join(
            f"- [{m.category}] {m.content} (tags: {', '.join(m.tags)})"
            for m in entries
        )
        prompt = _COMPRESS_PROMPT.format(month=month, memories=memories_text)
        raw = llm_complete(prompt)

        data = extract_json_or(raw, {}, expect="object")
        summary_content = data.get("summary", "")
        if not summary_content:
            return 0

        summary = Memory(
            content=summary_content,
            layer="episodic",
            category=data.get("category", "event"),
            tags=data.get("tags", []),
            entities=data.get("entities", []),
            embedding=emb.encode_document(summary_content),
            source="maintenance",
        )
        db.insert(summary)

        for m in entries:
            db.deactivate(m.id)

        return 1

    @staticmethod
    def promote_to_semantic(db: "MemoryDB", llm_complete, emb) -> int:
        tag_freqs = db.get_tag_frequencies(layer="episodic", min_count=3)
        if not tag_freqs:
            return 0

        promoted_count = 0
        for tag, count in tag_freqs.items():
            samples = db.find_by_tags([tag])
            episodic_samples = [m for m in samples if m.layer == "episodic"][:5]
            if not episodic_samples:
                continue

            samples_text = "\n".join(
                f"- {m.content} (tags: {', '.join(m.tags)})"
                for m in episodic_samples
            )
            prompt = _PROMOTE_PROMPT.format(
                tag=tag, count=count, samples=samples_text
            )
            raw = llm_complete(prompt)

            data = extract_json_or(raw, {}, expect="object")
            if not data.get("should_promote", False):
                continue

            new_content = data.get("content", "")
            if not new_content:
                continue

            new_memory = Memory(
                content=new_content,
                layer="semantic",
                category=data.get("category", "knowledge"),
                tags=data.get("tags", [tag]),
                entities=data.get("entities", []),
                embedding=emb.encode_document(new_content),
                source="maintenance",
            )
            db.insert(new_memory)
            promoted_count += 1

        return promoted_count

    @staticmethod
    def review_semantic(db: "MemoryDB", llm_complete) -> int:
        semantic = db.list_by_layer("semantic")
        if not semantic:
            return 0

        total_reviewed = 0
        for start in range(0, len(semantic), _REVIEW_BATCH_SIZE):
            batch = semantic[start : start + _REVIEW_BATCH_SIZE]
            memories_text = "\n".join(
                f"- [id={m.id}] [{m.category}] {m.content}" for m in batch
            )
            prompt = _REVIEW_PROMPT.format(memories=memories_text)
            raw = llm_complete(prompt)

            data = extract_json_or(raw, {"stale_ids": [], "updates": []}, expect="object")

            for stale_id in data.get("stale_ids", []):
                db.deactivate(stale_id)

            for update in data.get("updates", []):
                uid = update.get("id", "")
                new_content = update.get("new_content", "")
                if uid and new_content:
                    try:
                        db.update_content(uid, new_content)
                    except ValueError:
                        pass

            total_reviewed += len(batch)

        return total_reviewed
