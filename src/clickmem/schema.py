"""All ClickMem DDL lives here.

Functions return SQL strings; they do **not** execute anything. The backend
layer calls ``bootstrap_statements(dim)`` on first connect and runs each
statement in order.

Tables:
    memories          - first-class entity (`Memory`); ReplacingMergeTree(updated_at)
    memory_history    - immutable per-version log; MergeTree
    projects          - project metadata; ReplacingMergeTree(updated_at)
    blacklist         - refused patterns; ReplacingMergeTree(updated_at)
    raw_transcripts   - cold raw landing; MergeTree
    events            - mutation/integration event log; MergeTree TTL 30d
"""

from __future__ import annotations

from typing import List


DEFAULT_EMBED_DIM = 256


MEMORIES_COLUMNS = (
    "id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
    "status, pinned, contract_reason, revises_id, conflict_with, content_hash, "
    "recall_hits, pending_embedding, embed_attempts, created_at, updated_at"
)


def memories_ddl(embed_dim: int = DEFAULT_EMBED_DIM, table: str = "memories") -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {table} (
        id              String,
        content         String,
        kind            LowCardinality(String),
        source          LowCardinality(String),
        source_ref      String,
        project_id      String,
        privacy         LowCardinality(String) DEFAULT 'private',
        tags            Array(String),
        embedding       Array(Float32),

        status          LowCardinality(String) DEFAULT 'active',
        pinned          UInt8 DEFAULT 0,
        contract_reason String DEFAULT '',
        revises_id      String DEFAULT '',
        conflict_with   Array(String),

        content_hash    String DEFAULT '',
        recall_hits     UInt64 DEFAULT 0,

        pending_embedding UInt8 DEFAULT 0,
        embed_attempts    UInt8 DEFAULT 0,

        created_at      DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
        updated_at      DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY id
    """.strip()


def memories_alter_for_async_embed() -> List[str]:
    """Backward-compat ALTERs for databases created before async embedding.

    These are issued on every bootstrap as best-effort statements: failures
    are swallowed by the backend wrapper so the server stays up.
    """
    return [
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS pending_embedding UInt8 DEFAULT 0",
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS embed_attempts UInt8 DEFAULT 0",
    ]


def memories_rekey_to_id_statements(embed_dim: int = DEFAULT_EMBED_DIM) -> List[str]:
    """Rebuild old memories tables whose replacing key included mutable fields.

    Earlier builds used ``ORDER BY (project_id, kind, id)``. Editing
    ``project_id`` or ``kind`` then produced two live rows with the same id,
    because ReplacingMergeTree only deduplicates within the sorting key. The
    replacement table keeps ``id`` as the only replacing key, while copying
    ``FINAL`` rows from the old table so pre-existing duplicate ids collapse
    under the new key.
    """
    tmp = "memories__id_order_tmp"
    backup = "memories__project_kind_id_backup"
    return [
        f"DROP TABLE IF EXISTS {tmp}",
        memories_ddl(embed_dim, table=tmp),
        f"INSERT INTO {tmp} ({MEMORIES_COLUMNS}) SELECT {MEMORIES_COLUMNS} FROM memories FINAL",
        f"DROP TABLE IF EXISTS {backup}",
        f"RENAME TABLE memories TO {backup}, {tmp} TO memories",
    ]


def memory_history_ddl() -> str:
    return """
    CREATE TABLE IF NOT EXISTS memory_history (
        memory_id   String,
        version     UInt32,
        op          LowCardinality(String),
        content     String,
        edited_by   String,
        edited_at   DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
        prev_id     String DEFAULT '',
        note        String DEFAULT ''
    )
    ENGINE = MergeTree
    ORDER BY (memory_id, version, edited_at)
    """.strip()


def projects_ddl(embed_dim: int = DEFAULT_EMBED_DIM) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS projects (
        id                  String,
        name                String,
        repo_url            String DEFAULT '',
        kind                LowCardinality(String) DEFAULT 'work',
        allowed_cross_refs  Array(String),
        embedding           Array(Float32),
        created_at          DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
        updated_at          DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY id
    """.strip()


def blacklist_ddl() -> str:
    return """
    CREATE TABLE IF NOT EXISTS blacklist (
        id          String,
        pattern     String,
        scope       LowCardinality(String) DEFAULT 'global',
        reason      String DEFAULT '',
        hit_count   UInt64 DEFAULT 0,
        created_at  DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
        updated_at  DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY id
    """.strip()


def raw_transcripts_ddl() -> str:
    return """
    CREATE TABLE IF NOT EXISTS raw_transcripts (
        id           String,
        session_id   String,
        agent        LowCardinality(String) DEFAULT '',
        project_id   String DEFAULT '',
        role         LowCardinality(String) DEFAULT '',
        text         String,
        text_hash    String,
        meta_json    String DEFAULT '',
        created_at   DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
    )
    ENGINE = MergeTree
    ORDER BY (session_id, created_at, id)
    """.strip()


def events_ddl() -> str:
    return """
    CREATE TABLE IF NOT EXISTS events (
        id           String,
        kind         LowCardinality(String),
        agent        LowCardinality(String) DEFAULT '',
        project_id   String DEFAULT '',
        memory_id    String DEFAULT '',
        message      String DEFAULT '',
        payload_json String DEFAULT '',
        created_at   DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
    )
    ENGINE = MergeTree
    PARTITION BY toYYYYMMDD(created_at)
    ORDER BY (created_at, kind, id)
    TTL toDateTime(created_at) + INTERVAL 30 DAY
    """.strip()


def bootstrap_statements(embed_dim: int = DEFAULT_EMBED_DIM) -> List[str]:
    """Ordered list of DDL statements to run on a fresh backend."""
    return [
        memories_ddl(embed_dim),
        *memories_alter_for_async_embed(),
        memory_history_ddl(),
        projects_ddl(embed_dim),
        blacklist_ddl(),
        raw_transcripts_ddl(),
        events_ddl(),
    ]


def ann_index_statements() -> List[str]:
    """Optional ANN index statements. Best-effort: try, ignore failure."""
    return [
        "ALTER TABLE memories ADD INDEX IF NOT EXISTS embedding_idx embedding TYPE annoy('cosineDistance') GRANULARITY 1024",
    ]
