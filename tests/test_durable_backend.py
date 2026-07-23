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
