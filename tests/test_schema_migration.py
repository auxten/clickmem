"""Schema migration regression tests."""

from __future__ import annotations

from pathlib import Path


def test_existing_memories_table_rekeys_to_id(tmp_path):
    """Old ``ORDER BY (project_id, kind, id)`` tables must migrate to id-only."""
    from chdb import session as chsession

    from clickmem.backend.local_chdb import LocalBackend
    from clickmem.schema import MEMORIES_COLUMNS, memories_ddl

    db_path = tmp_path / "old-key-db"
    db_path.mkdir()
    old_ddl = memories_ddl(table="memories").replace(
        "ORDER BY id",
        "ORDER BY (project_id, kind, id)",
    )
    sess = chsession.Session(str(db_path))
    try:
        sess.query(old_ddl)
        sess.query(
            "INSERT INTO memories "
            f"({MEMORIES_COLUMNS}) VALUES "
            "('same-id', 'old project', 'fact', 'test', '', 'p1', 'private', ['test'], [], "
            "'active', 0, '', '', [], 'h1', 0, 0, 0, "
            "toDateTime64('2026-01-01 00:00:00', 3, 'UTC'), "
            "toDateTime64('2026-01-01 00:00:00', 3, 'UTC')), "
            "('same-id', 'new project', 'decision', 'test', '', 'p2', 'private', ['test'], [], "
            "'active', 0, '', '', [], 'h2', 0, 0, 0, "
            "toDateTime64('2026-01-01 00:00:00', 3, 'UTC'), "
            "toDateTime64('2026-01-02 00:00:00', 3, 'UTC'))"
        )
    finally:
        sess.close()

    backend = LocalBackend(Path(db_path))
    try:
        meta = backend.query("SELECT sorting_key FROM system.tables WHERE name = 'memories'")
        assert meta[0]["sorting_key"] == "id"
        rows = backend.query("SELECT id, content, project_id, kind FROM memories FINAL WHERE id = 'same-id'")
        assert rows == [
            {
                "id": "same-id",
                "content": "new project",
                "project_id": "p2",
                "kind": "decision",
            }
        ]
    finally:
        backend.close()
