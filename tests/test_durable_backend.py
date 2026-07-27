"""Smoke test for the durable backend — clickmem memory on a chdb.durable object.

Requires `chdb.durable` importable (chdb >= the release that ships it).

    PYTHONPATH=src python tests/test_durable_backend.py
    # S3/MinIO:
    CHDB_DURABLE_S3_ENDPOINT=http://127.0.0.1:9000 \
      AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_REGION=us-east-1 \
      DAO_TEST_URL=s3://clickmem/memories PYTHONPATH=src python tests/test_durable_backend.py
"""
import os
import shutil
import tempfile

from clickmem.backend.durable import DurableBackend

URL = os.getenv("DAO_TEST_URL") or ("local:" + tempfile.mkdtemp(prefix="clickmem-dao-"))
OID = "user-1"


def _open():
    return DurableBackend(URL, OID, embed_dim=4, db="clickmem")


def _n(rows):
    return int(rows[0]["c"])


def crash_replay_and_lease():
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


def lazy_audit_writes():
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


def main():
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


if __name__ == "__main__":
    main()
    crash_replay_and_lease()
    lazy_audit_writes()
