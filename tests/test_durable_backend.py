"""Durable backend tests — clickmem memory on a chdb.durable object.

Collected by pytest and skipped when `chdb.durable` is not importable (it is
merged into chDB but not yet in a released wheel). Also runnable as a plain
script, including against MinIO/S3:

    PYTHONPATH=src python tests/test_durable_backend.py
    CHDB_DURABLE_S3_ENDPOINT=http://127.0.0.1:9000 \
      AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_REGION=us-east-1 \
      DAO_TEST_URL=s3://clickmem/memories PYTHONPATH=src python tests/test_durable_backend.py
"""
import importlib.util
import os
import shutil
import tempfile

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("chdb.durable") is None,
    reason="chdb.durable not in this chdb release yet",
)

from clickmem.backend.durable import DurableBackend  # noqa: E402

URL = os.getenv("DAO_TEST_URL") or ("local:" + tempfile.mkdtemp(prefix="clickmem-dao-"))
OID = "user-1"


def _open():
    return DurableBackend(URL, OID, embed_dim=4, db="clickmem")


def _n(rows):
    return int(rows[0]["c"])


def test_crash_replay_and_lease():
    """Crash (no close) → lease blocks reopen with a clear hint → force takeover
    replays the WAL and timestamps survive byte-identical (utc_now_sql is a
    Python-evaluated literal, so replayed statements are deterministic)."""
    from clickmem.sqlutil import utc_now_sql

    url = "local:" + tempfile.mkdtemp(prefix="clickmem-dao-crash-")
    b = DurableBackend(url, "crash-1", embed_dim=4, db="clickmem")
    ts = utc_now_sql()
    assert ts.startswith("toDateTime64('"), ts  # literal, not server-side now64
    b.execute("CREATE TABLE r (id String, updated_at DateTime64(3, 'UTC')) "
              "ENGINE=ReplacingMergeTree(updated_at) ORDER BY id")
    b.execute(f"INSERT INTO r VALUES ('x', {ts})")
    before = b.query("SELECT toString(updated_at) AS t FROM r")[0]["t"]

    b._obj.session.close()  # simulate a crash: no checkpoint, lease not released

    try:
        DurableBackend(url, "crash-1", embed_dim=4, db="clickmem")
        raise AssertionError("expected LeaseError on reopen after crash")
    except Exception as e:
        assert "force=True" in str(e), e  # guidance present in the message

    b2 = DurableBackend(url, "crash-1", embed_dim=4, db="clickmem", force=True)
    after = b2.query("SELECT toString(updated_at) AS t FROM r")[0]["t"]
    assert after == before, (before, after)  # WAL replay reproduced the timestamp
    b2.close()
    shutil.rmtree(url.split(":", 1)[1], ignore_errors=True)
    print("crash replay + lease hint: deterministic timestamps PASS")


def test_lazy_audit_writes():
    """execute_lazy applies locally without cutting a WAL segment; the next
    belief write's flush carries it; a crash loses only the audit tail."""
    root = tempfile.mkdtemp(prefix="clickmem-dao-lazy-")
    b = DurableBackend("local:" + root, "lazy-1", embed_dim=4, db="clickmem")
    wal_dir = os.path.join(root, "lazy-1", "wal")

    def segs():
        return len(os.listdir(wal_dir)) if os.path.isdir(wal_dir) else 0

    b.execute("CREATE TABLE ev (id String) ENGINE=MergeTree ORDER BY id")
    n0 = segs()
    b.execute_lazy("INSERT INTO ev VALUES ('audit-1')")
    assert segs() == n0, "lazy write must not upload a WAL segment"
    assert _n(b.query("SELECT count() AS c FROM ev")) == 1  # visible locally
    b.execute("INSERT INTO ev VALUES ('belief-1')")  # flush carries audit-1 too
    n1 = segs()
    assert n1 > n0

    b.execute_lazy("INSERT INTO ev VALUES ('audit-2')")  # buffered, never flushed
    b._obj.session.close()  # crash

    b2 = DurableBackend("local:" + root, "lazy-1", embed_dim=4, db="clickmem", force=True)
    rows = {r["id"] for r in b2.query("SELECT id FROM ev")}
    assert rows == {"audit-1", "belief-1"}, rows  # audit tail lost, beliefs intact
    b2.close()
    shutil.rmtree(root, ignore_errors=True)
    print("lazy audit writes: no per-write upload, piggyback flush, bounded loss PASS")


def test_growth_control():
    """Segment-count trigger folds the WAL; the lazy deadline ships an idle
    audit tail; close only pays a full-base upload when replay would be long."""
    import time as _t

    root = tempfile.mkdtemp(prefix="clickmem-dao-growth-")
    b = DurableBackend("local:" + root, "grow-1", embed_dim=4, db="clickmem")
    ckpt_dir = os.path.join(root, "grow-1", "checkpoints")

    b.execute("CREATE TABLE g (id String) ENGINE=MergeTree ORDER BY id")
    b._CHECKPOINT_SEGMENTS = 3  # lower the fold threshold for the test
    for i in range(3):
        b.execute(f"INSERT INTO g VALUES ('s{i}')")
    # fold fires when the WAL hits 3 segments (create + 2 inserts); the third
    # insert then starts a fresh 1-segment WAL on top of the new base
    assert len(b._obj.wal) == 1, b._obj.wal
    assert os.path.isdir(ckpt_dir) and os.listdir(ckpt_dir)

    b._LAZY_FLUSH_SECONDS = 0.05  # lower the deadline for the test
    n_wal = len(b._obj.wal)
    b.execute_lazy("INSERT INTO g VALUES ('audit')")
    _t.sleep(0.1)
    b.maybe_flush()  # what the server's background worker calls periodically
    assert b._lazy_bytes == 0 and len(b._obj.wal) == n_wal + 1, "idle audit tail should ship"

    n_ckpt = len(os.listdir(ckpt_dir))
    b.close()  # 1 segment < close threshold: flush only, no new base
    assert len(os.listdir(ckpt_dir)) == n_ckpt, "short WAL must not mint a base on close"

    b2 = DurableBackend("local:" + root, "grow-1", embed_dim=4, db="clickmem")
    assert _n(b2.query("SELECT count() AS c FROM g")) == 4  # base + replayed segment
    b2.close()
    shutil.rmtree(root, ignore_errors=True)
    print("growth control: segment fold, idle-tail deadline, graded close PASS")


def test_bootstrap_persist_restore_wal_vector():
    b = _open()
    # bootstrap ran (clickmem's memories table exists); use a small table for the check
    b.execute("CREATE TABLE t (id String, content String, embedding Array(Float32)) "
              "ENGINE=ReplacingMergeTree ORDER BY id")
    b.execute("INSERT INTO t VALUES ('a','apple',[1,0,0,0]),('b','banana',[0,1,0,0])")
    assert _n(b.query("SELECT count() AS c FROM t")) == 2
    hits = b.vector_search("t", [1, 0, 0, 0], where="", limit=1, select="id, content")
    assert hits and hits[0]["id"] == "a" and float(hits[0]["cosine_sim"]) > 0.9, hits
    assert _n(b.query("SELECT count() AS c FROM memories")) >= 0  # bootstrap table present
    b.close()  # checkpoint + release

    b2 = _open()  # reopen: durable state must survive
    assert _n(b2.query("SELECT count() AS c FROM t")) == 2
    b2.execute("INSERT INTO t VALUES ('c','cherry',[0,0,1,0])")  # WAL append, no checkpoint
    b2.close()

    b3 = _open()  # WAL replayed on restart
    assert _n(b3.query("SELECT count() AS c FROM t")) == 3
    assert b3.vector_search("t", [0, 0, 1, 0], where="", limit=1, select="id")[0]["id"] == "c"
    b3.close()

    if URL.startswith("local:"):
        shutil.rmtree(URL.split(":", 1)[1], ignore_errors=True)
    print("durable backend: bootstrap + persist + restore + WAL replay + vector_search PASS")


def test_initial_schema_checkpoint_precedes_first_memory_wal():
    """A short first session must reopen even though bootstrap DDL is not WAL-logged.

    Regression: the first few memory/history/event INSERTs were persisted as
    WAL, but close deliberately skipped a full checkpoint below eight segments.
    Reopen then replayed those INSERTs before the constructor could bootstrap
    the tables and failed with UNKNOWN_TABLE.
    """
    from clickmem import memories

    root = tempfile.mkdtemp(prefix="clickmem-dao-first-base-")
    url = "local:" + root
    b = DurableBackend(url, "first-1", embed_dim=4, db="clickmem")
    result = memories.add(
        "first durable memory",
        project_id="chdb-io/chdb",
        tags=["regression"],
        backend=b,
    )
    assert result["status"] == "added"
    assert len(b._obj.wal) < b._CLOSE_CHECKPOINT_SEGMENTS
    b.close()

    reopened = DurableBackend(url, "first-1", embed_dim=4, db="clickmem")
    assert memories.get(result["id"], backend=reopened) is not None
    reopened.close()
    shutil.rmtree(root, ignore_errors=True)


def test_lazy_byte_threshold_flushes():
    """execute_lazy ships immediately once buffered bytes cross the cap."""
    root = tempfile.mkdtemp(prefix="clickmem-dao-lazyb-")
    b = DurableBackend("local:" + root, "lb-1", embed_dim=4, db="clickmem")
    wal = os.path.join(root, "lb-1", "wal")

    def segs():
        return len(os.listdir(wal)) if os.path.isdir(wal) else 0

    b.execute("CREATE TABLE ev (id String) ENGINE=MergeTree ORDER BY id")
    b._LAZY_FLUSH_BYTES = 1  # any lazy write now exceeds the cap
    n0 = segs()
    b.execute_lazy("INSERT INTO ev VALUES ('x')")
    assert segs() == n0 + 1, "over-cap lazy write must flush immediately"
    b.close()
    shutil.rmtree(root, ignore_errors=True)


def test_close_checkpoints_when_wal_long():
    """close folds the WAL into a base once it passes the close threshold."""
    root = tempfile.mkdtemp(prefix="clickmem-dao-closeck-")
    b = DurableBackend("local:" + root, "cc-1", embed_dim=4, db="clickmem")
    ckpt = os.path.join(root, "cc-1", "checkpoints")
    b.execute("CREATE TABLE ev (id String) ENGINE=MergeTree ORDER BY id")
    b.execute("INSERT INTO ev VALUES ('x')")  # >= 1 WAL segment
    b._CLOSE_CHECKPOINT_SEGMENTS = 1
    n0 = len(os.listdir(ckpt)) if os.path.isdir(ckpt) else 0
    b.close()
    assert (len(os.listdir(ckpt)) if os.path.isdir(ckpt) else 0) > n0, "close should fold"
    shutil.rmtree(root, ignore_errors=True)


def test_catalog_update_failure_is_swallowed(monkeypatch):
    """A directory-update error must never propagate out of open/close —
    the catalog is a navigation hint, not the source of truth."""
    import clickmem.stores as st

    root = tempfile.mkdtemp(prefix="clickmem-dao-catfail-")
    monkeypatch.setattr(
        st, "catalog_upsert",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    b = DurableBackend("local:" + root, "cf-1", embed_dim=4, db="clickmem")  # open path
    b.execute("CREATE TABLE ev (id String) ENGINE=MergeTree ORDER BY id")
    b.close()  # close path — must not raise despite the failing upsert
    shutil.rmtree(root, ignore_errors=True)


def test_bootstrap_tolerates_bad_statement(monkeypatch):
    """A bootstrap statement that errors (e.g. an unsupported ANN index on a
    given build) is skipped, not fatal to opening the store."""
    from clickmem.schema import bootstrap_statements as real

    monkeypatch.setattr(
        "clickmem.backend.durable.bootstrap_statements",
        lambda dim: list(real(dim)) + ["THIS IS NOT VALID SQL"],
    )
    root = tempfile.mkdtemp(prefix="clickmem-dao-badboot-")
    b = DurableBackend("local:" + root, "bb-1", embed_dim=4, db="clickmem")  # must open
    assert _n(b.query("SELECT count() AS c FROM memories")) >= 0  # real tables still built
    b.close()
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_bootstrap_persist_restore_wal_vector()
    test_crash_replay_and_lease()
    test_lazy_audit_writes()
    test_growth_control()
    test_lazy_byte_threshold_flushes()
    test_close_checkpoints_when_wal_long()
