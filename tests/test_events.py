"""Events log: a row is written on every mutation; TTL DDL present."""

from __future__ import annotations

from clickmem import memories
from clickmem.events import activity_counts, list_events, write


def _kinds(rows):
    return {r.get("kind") for r in rows}


def test_expand_writes_event(backend):
    memories.add("event-row-1", project_id="p1", tags=["test"])
    rows = list_events(limit=50)
    assert "memory.expand" in _kinds(rows)


def test_contract_and_pin_write_events(backend):
    res = memories.add("event-row-2", project_id="p1", tags=["test"])
    memories.pin(res["id"])
    memories.forget(res["id"], reason="done")
    rows = list_events(limit=50)
    kinds = _kinds(rows)
    assert {"memory.pin", "memory.contract"}.issubset(kinds)


def test_write_with_payload(backend):
    write(
        "custom.kind",
        agent="tester",
        project_id="p1",
        message="hi",
        payload={"foo": "bar"},
    )
    rows = list_events(kind="custom.kind", limit=10)
    assert rows and rows[0]["payload"]["foo"] == "bar"


def test_activity_counts_groups(backend):
    memories.add("a", project_id="p1", privacy="public", tags=["test"])
    memories.add("b", project_id="p1", privacy="public", tags=["test"])
    rows = activity_counts(hours=24, bucket_minutes=60)
    assert isinstance(rows, list)
    # at least one bucket with >= 2 events
    assert any(int(r["count"]) >= 2 for r in rows)


def test_events_table_has_ttl_clause(backend):
    """Sanity-check: TTL DDL is part of the schema (we don't assert expiry)."""
    rows = backend.query("SHOW CREATE TABLE events")
    text = " ".join(str(v) for r in rows for v in r.values()).upper()
    assert "TTL" in text
